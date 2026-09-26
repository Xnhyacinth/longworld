"""Recompute P112 answers, dependency edits and masks from final reader bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import physical_length_bin
from scripts.audit_p95_report_finance_shared import _visible_amount, _visible_year
from scripts.p95_report_finance_shared import canonical
from scripts.p112_report_route import (
    CODE_FILES,
    METRICS,
    SCHEMA,
    _rows,
    _source_views,
    shortcut_reason,
)
from scripts.run_p95_report_finance_shared import SEPARATOR, _token_span, sha
from scripts.train_sft import _render_chat, tokenize_assistant_only


def replay(
    context: str, cells: dict[tuple[str, str], dict], program: dict
) -> tuple[dict, str]:
    """Visible-only oracle with an independent period-selection implementation."""
    if set(program) != {
        "selector_kind",
        "selector_metric",
        "target_metric",
        "baseline_endpoint",
    }:
        raise ValueError("unknown program")
    selection, target = program["selector_metric"], program["target_metric"]
    if selection not in METRICS or target not in METRICS or selection == target:
        raise ValueError("invalid metric roles")
    records = sorted(
        {record for record, _ in cells},
        key=lambda record: _visible_year(context, record),
    )
    if len(records) != 4:
        raise ValueError("incomplete report world")
    values = [
        _visible_amount(context, *cells[record, selection]["context_span"])
        for record in records
    ]
    if program["selector_kind"] == "max_value":
        winner = max(values)
    elif program["selector_kind"] == "min_value":
        winner = min(values)
    else:
        raise ValueError("invalid selection operation")
    if values.count(winner) != 1:
        raise ValueError("selector tie")
    index = values.index(winner)
    endpoint = program["baseline_endpoint"]
    if endpoint not in {"earliest", "latest"}:
        raise ValueError("invalid reference endpoint")
    reference_index = 0 if endpoint == "earliest" else 3
    if index == reference_index:
        raise ValueError("selected reference year")
    later = _visible_amount(context, *cells[records[index], target]["context_span"])
    earlier = _visible_amount(
        context, *cells[records[reference_index], target]["context_span"]
    )
    if later == earlier:
        raise ValueError("zero delta")
    return {
        "selected_fiscal_year": _visible_year(context, records[index]),
        "target_metric": target,
        "reference_fiscal_year": _visible_year(context, records[reference_index]),
        "change_from_reference_year_usd_millions": later - earlier,
    }, records[index]


def _edited(context: str, cells: dict, edit: dict) -> tuple[str, dict]:
    key = edit["record_id"], edit["role"]
    item = cells[key]
    start, end = edit["span"]
    if item["context_span"] != [start, end] or len(edit["replacement"]) != end - start:
        raise ValueError("intervention target span drift")
    changed = context[:start] + edit["replacement"] + context[end:]
    return changed, {**cells, key: {**item, "quote": edit["replacement"]}}


def audit(
    config_path: Path, native: Path, output: Path, *, verify_only: bool = False
) -> dict:
    config = json.loads(config_path.read_text())
    batch = json.loads((native / "batch_manifest.json").read_text())
    if batch["schema"] != SCHEMA + ".batch" or batch["config_sha256"] != sha(
        config_path
    ):
        raise ValueError("P112 config/batch mismatch")
    if batch.get("code_sha256") != {name: sha(ROOT / name) for name in CODE_FILES}:
        raise ValueError("P112 producer code pin mismatch")
    if (
        sha(ROOT / config["source_batch_manifest"]["path"])
        != batch["source_batch_manifest_sha256"]
    ):
        raise ValueError("P96 source batch changed")
    if (
        sha(ROOT / config["source_final_audit"]["path"])
        != batch["source_final_audit_sha256"]
    ):
        raise ValueError("P96 source final audit changed")
    tokenizer = get_tokenizer()
    sample_ids, semantic_ids, groups = set(), set(), set()
    tokens, lengths, operations = Counter(), Counter(), Counter()
    spans, distances, per_source = [], [], []
    for job in batch["jobs"]:
        issuer = job["issuer"]
        folder = native / issuer
        manifest_path = folder / "manifest.json"
        if sha(manifest_path) != job["manifest_sha256"]:
            raise ValueError("P112 issuer receipt mismatch")
        manifest = json.loads(manifest_path.read_text())
        if (
            manifest["schema"] != SCHEMA + ".issuer"
            or manifest["train_ready"] is not False
        ):
            raise ValueError("P112 native stage drift")
        if manifest["source_group"] in groups:
            raise ValueError("duplicate source group")
        groups.add(manifest["source_group"])
        for name, expected in manifest["file_sha256"].items():
            if sha(folder / name) != expected:
                raise ValueError("P112 native file hash changed")
        source_folder = ROOT / config["source_batch_dir"] / issuer
        if sha(source_folder / "manifest.json") != manifest["source_manifest_sha256"]:
            raise ValueError("P96 issuer source changed")
        source_views = _source_views(
            source_folder, json.loads((source_folder / "manifest.json").read_text())
        )
        task_views = defaultdict(set)
        task_answers = defaultdict(set)
        rows = list(
            zip(
                _rows(folder / "reader.jsonl"),
                _rows(folder / "sample_index.jsonl"),
                _rows(folder / "audit.jsonl"),
                _rows(folder / "interventions.jsonl"),
                strict=True,
            )
        )
        if len(rows) != manifest["views"]:
            raise ValueError("P112 issuer cardinality mismatch")
        for reader, index, proof, intervention in rows:
            sample = index["sample_id"]
            if (
                sample in sample_ids
                or reader["sample_id"] != sample
                or proof["sample_id"] != sample
                or intervention["sample_id"] != sample
                or index["source_group"] != manifest["source_group"]
                or index["split"] != manifest["split"]
                or proof["mask_checked"] is not True
            ):
                raise ValueError("P112 reader identity/split mismatch")
            sample_ids.add(sample)
            messages = reader["messages"]
            if [row["role"] for row in messages] != ["user", "assistant"]:
                raise ValueError("P112 chat roles changed")
            user, response = (row["content"] for row in messages)
            if user.count(SEPARATOR) != 1:
                raise ValueError("P112 question separator ambiguous")
            context, question = user.split(SEPARATOR)
            if (
                not question
                or hashlib.sha256(context.encode()).hexdigest()
                != index["context_sha256"]
            ):
                raise ValueError("P112 reader context changed")
            if hashlib.sha256(response.encode()).hexdigest() != index["answer_sha256"]:
                raise ValueError("P112 answer hash changed")
            source_context, cells = source_views[index["variant"]]
            if context != source_context:
                raise ValueError(
                    "P112 reader does not equal audited P96 source context"
                )
            answer, selected = replay(context, cells, proof["program"])
            if canonical(answer) != response:
                raise ValueError("P112 final visible answer replay failed")
            records = sorted(
                {record for record, _ in cells}, key=lambda r: _visible_year(context, r)
            )
            endpoint = proof["program"]["baseline_endpoint"]
            reference_record = records[0 if endpoint == "earliest" else 3]
            lineage = [(r, proof["program"]["selector_metric"]) for r in records] + [
                (reference_record, proof["program"]["target_metric"]),
                (selected, proof["program"]["target_metric"]),
            ]
            if shortcut_reason(context, cells, lineage, answer) is not None:
                raise ValueError(
                    "P112 selected reader has one-report or direct-answer shortcut"
                )
            for name, edit in (
                ("selector", intervention["selector"]),
                ("target", intervention["target"]),
            ):
                if (
                    edit["role"]
                    != proof["program"][
                        "selector_metric" if name == "selector" else "target_metric"
                    ]
                ):
                    raise ValueError("P112 edit role mismatch")
                changed, changed_cells = _edited(context, cells, edit)
                changed_answer, changed_selected = replay(
                    changed, changed_cells, proof["program"]
                )
                if (
                    changed_answer != edit["answer"]
                    or changed_answer["change_from_reference_year_usd_millions"]
                    == answer["change_from_reference_year_usd_millions"]
                ):
                    raise ValueError("P112 edit did not change numeric answer")
                if name == "selector" and changed_selected == selected:
                    raise ValueError("P112 selector edit did not change selected year")
                if name == "target" and changed_selected != selected:
                    raise ValueError("P112 target edit changed selected year")
                tokens[name + "_edits"] += 1
            encoded = tokenize_assistant_only(tokenizer, messages, 262144)
            labels = encoded["labels"]
            input_tokens = sum(value == -100 for value in labels)
            supervised = len(labels) - input_tokens
            if (
                (len(labels), input_tokens, supervised)
                != (
                    index["full_chat_tokens"],
                    index["input_tokens"],
                    index["supervised_tokens"],
                )
                or index["length_bin"] != physical_length_bin(len(labels))
                or supervised <= 0
            ):
                raise ValueError("P112 final assistant-only mask changed")
            prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
            user_start = prompt.find(user)
            if user_start < 0 or prompt.count(user) != 1:
                raise ValueError("P112 final prompt boundary changed")
            offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
                "offset_mapping"
            ]
            query = _token_span(
                offsets,
                user_start + len(context) + len(SEPARATOR),
                user_start + len(user),
            )[0]
            if query != proof["query_token_start"] or query >= input_tokens:
                raise ValueError("P112 query token offset changed")
            spans_this = []
            proof_cells = {}
            for item in proof["evidence"]:
                key = item["record_id"], item["role"]
                start, end = item["context_span"]
                if context[start:end] != item["quote"] or key in proof_cells:
                    raise ValueError("P112 evidence span absent or repeated")
                span = _token_span(offsets, user_start + start, user_start + end)
                if span != item["prompt_token_span"] or span[1] > input_tokens:
                    raise ValueError("P112 evidence token span changed")
                spans_this.append(span)
                proof_cells[key] = item
            if len(proof_cells) != 6:
                raise ValueError(
                    "P112 lineage must contain four selectors and two distinct targets"
                )
            # Both target observations are required by the declared visible-cell program.
            for item in [
                x
                for x in proof["evidence"]
                if x["role"] == proof["program"]["target_metric"]
            ]:
                key = item["record_id"], item["role"]
                try:
                    replay(
                        context,
                        {k: v for k, v in proof_cells.items() if k != key},
                        proof["program"],
                    )
                except KeyError:
                    tokens["bounded_target_support_deletions"] += 1
                else:
                    raise ValueError(
                        "P112 target support deletion did not break program"
                    )
            extent = max(end for _, end in spans_this) - min(
                start for start, _ in spans_this
            )
            distance = query - max(end for _, end in spans_this)
            if (
                extent != proof["evidence_extent_tokens"]
                or distance != proof["last_evidence_to_query_tokens"]
            ):
                raise ValueError("P112 evidence position summary changed")
            spans.append(extent)
            distances.append(distance)
            tokens["views"] += 1
            tokens["input_tokens"] += input_tokens
            tokens["supervised_tokens"] += supervised
            lengths[index["length_bin"]] += 1
            operations[index["operation"]] += 1
            task_views[index["semantic_task_id"]].add(index["variant"])
            task_answers[index["semantic_task_id"]].add(response)
            semantic_ids.add(index["semantic_task_id"])
        if (
            len(task_views) != manifest["semantic_tasks"]
            or any(
                views != {"complete_statements", "analyst_packet"}
                for views in task_views.values()
            )
            or any(len(answers) != 1 for answers in task_answers.values())
        ):
            raise ValueError("P112 issuer view pair incomplete")
        per_source.append(
            {
                "issuer": issuer,
                "source_group": manifest["source_group"],
                "split": manifest["split"],
                "semantic_tasks": len(task_views),
                "views": len(rows),
            }
        )
    if (
        len(sample_ids) != batch["views"]
        or len(semantic_ids) != batch["semantic_tasks"]
    ):
        raise ValueError("P112 batch totals changed")
    result = {
        "schema": SCHEMA + ".final-audit",
        "batch_manifest_sha256": sha(native / "batch_manifest.json"),
        "config_sha256": sha(config_path),
        "auditor_code_sha256": {
            name: sha(ROOT / name)
            for name in (
                "scripts/p112_report_audit.py",
                "scripts/p112_report_route.py",
                "scripts/train_sft.py",
            )
        },
        "source_groups": len(groups),
        "semantic_tasks": len(semantic_ids),
        "mask": dict(sorted(tokens.items())),
        "length_bins": dict(sorted(lengths.items())),
        "operations": dict(sorted(operations.items())),
        "evidence_extent_tokens": {"min": min(spans), "max": max(spans)},
        "last_evidence_to_query_tokens": {"min": min(distances), "max": max(distances)},
        "sources": per_source,
        "scope": "final-visible four-selector/two-target cell replay, bounded edits and assistant mask; alternate comparative-text proofs not exhausted",
        "train_ready": False,
    }
    payload = canonical(result) + "\n"
    if verify_only:
        if output.read_text() != payload:
            raise ValueError("P112 final audit replay mismatch")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = audit(args.config, args.native, args.output, verify_only=args.verify_only)
    print(
        canonical(
            {
                key: value
                for key, value in result.items()
                if key not in {"sources", "operations"}
            }
        )
    )


if __name__ == "__main__":
    main()
