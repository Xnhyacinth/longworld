"""Recompute P96 final reader masks and declared programs from visible cells."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import physical_length_bin
from scripts.audit_p95_report_finance_shared import _visible_amount, _visible_year
from scripts.p95_report_finance_shared import canonical
from scripts.p96_finance_factorial import SCHEMA, TRANSITION_KINDS
from scripts.run_p95_report_finance_shared import SEPARATOR, _token_span, sha
from scripts.run_p96_finance_factorial import CODE_FILES
from scripts.train_sft import _render_chat, tokenize_assistant_only

AUDIT_SCHEMA = SCHEMA + ".final-audit"


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def visible_replay(
    context: str, evidence: list[dict], program: dict
) -> tuple[dict, str]:
    """Independent visible-value evaluation; no hidden fact values or trace."""
    values, years = {}, {}
    for item in evidence:
        key = item["record_id"], item["role"]
        if key in values:
            raise ValueError("duplicate visible source cell")
        values[key] = _visible_amount(context, *item["context_span"])
        years[item["record_id"]] = _visible_year(context, item["record_id"])
    records = sorted(years, key=lambda identity: (years[identity], identity))
    if len(records) != 4 or len(set(years.values())) != 4:
        raise ValueError("four visible annual report headers required")
    selector = program["selector"]
    kind, metrics, target = (
        selector["kind"],
        selector["metrics"],
        program["target_metric"],
    )

    def series(metric: str) -> list[int]:
        try:
            return [values[identity, metric] for identity in records]
        except KeyError as error:
            raise ValueError("selector metric missing from visible evidence") from error

    if len(metrics) == 1:
        base = [Fraction(value) for value in series(metrics[0])]
    elif len(metrics) == 2:
        numerators, denominators = series(metrics[0]), series(metrics[1])
        if any(value <= 0 for value in denominators):
            raise ValueError("visible ratio denominator nonpositive")
        base = [Fraction(a, b) for a, b in zip(numerators, denominators, strict=True)]
    else:
        raise ValueError("visible selector metric arity invalid")
    if kind == "max_growth":
        if any(value <= 0 for value in base):
            raise ValueError("visible growth denominator nonpositive")
        scores = [(base[i] - base[i - 1]) / base[i - 1] for i in range(1, 4)]
    elif kind in {"max_abs_delta", "max_abs_delta_ratio"}:
        scores = [abs(base[i] - base[i - 1]) for i in range(1, 4)]
    else:
        scores = base
    if kind == "median_ratio":
        if len(set(scores)) != 4:
            raise ValueError("visible selector tied")
        selected = sorted(range(4), key=lambda i: scores[i])[1]
    else:
        winning = min(scores) if kind in {"min_value", "min_ratio"} else max(scores)
        if scores.count(winning) != 1:
            raise ValueError("visible selector tied")
        selected = scores.index(winning) + (kind in TRANSITION_KINDS)
    try:
        value = values[records[selected], target]
    except KeyError as error:
        raise ValueError("selected target missing from visible evidence") from error
    answer = (
        {
            "transition": f"{years[records[selected - 1]]}–{years[records[selected]]}",
            "target_metric": target,
            "later_year_value_usd_millions": value,
        }
        if kind in TRANSITION_KINDS
        else {
            "fiscal_year": years[records[selected]],
            "target_metric": target,
            "value_usd_millions": value,
        }
    )
    return answer, records[selected]


def audit(
    config_path: Path, batch_dir: Path, output: Path, *, verify_only: bool = False
) -> dict:
    config = json.loads(config_path.read_text())
    catalog_path = ROOT / config["catalog"]["path"]
    batch_path = batch_dir / "batch_manifest.json"
    batch = json.loads(batch_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or sha(catalog_path) != config["catalog"]["sha256"]
        or batch.get("schema") != SCHEMA + ".batch"
        or batch["config_sha256"] != sha(config_path)
        or batch["catalog_sha256"] != sha(catalog_path)
        or batch["source_groups"] != len(batch["jobs"])
    ):
        raise ValueError("P96 config/catalog/batch pin changed")
    catalog = json.loads(catalog_path.read_text())
    receipts = {row["issuer"]: row for row in batch["jobs"]}
    if len(receipts) != len(catalog["jobs"]):
        raise ValueError("issuer receipt cardinality changed")
    tokenizer = get_tokenizer()
    samples, semantic, groups = set(), set(), set()
    masks, lengths, operations, families = Counter(), Counter(), Counter(), Counter()
    extents, distances, per_source = [], [], []
    for job in catalog["jobs"]:
        issuer = job["issuer"]
        native = batch_dir / issuer
        manifest_path = native / "manifest.json"
        receipt = json.loads(manifest_path.read_text())
        if (
            receipts[issuer]["status"] != "verified_candidate"
            or sha(manifest_path) != receipts[issuer]["manifest_sha256"]
            or receipt["schema"] != SCHEMA + ".issuer"
            or receipt["source_manifest"]
            != {"path": job["source_manifest"], "sha256": job["source_manifest_sha256"]}
            or sha(ROOT / job["source_manifest"]) != job["source_manifest_sha256"]
            or receipt["base_config"]["sha256"] != job["config_sha256"]
            or sha(ROOT / job["config"]) != job["config_sha256"]
            or receipt["code_sha256"] != {name: sha(ROOT / name) for name in CODE_FILES}
            or receipt["source_group"] in groups
            or receipt["train_ready"] is not False
        ):
            raise ValueError(f"P96 source, code or issuer receipt changed: {issuer}")
        groups.add(receipt["source_group"])
        for name, expected in receipt["file_sha256"].items():
            if sha(native / name) != expected:
                raise ValueError("P96 issuer artifact hash changed")
        counts = Counter()
        task_views, task_answers = defaultdict(set), defaultdict(set)
        for reader, index, proof in zip(
            _rows(native / "reader.jsonl"),
            _rows(native / "sample_index.jsonl"),
            _rows(native / "audit.jsonl"),
            strict=True,
        ):
            sample = index["sample_id"]
            if (
                sample in samples
                or reader["sample_id"] != sample
                or proof["sample_id"] != sample
                or index["source_group"] != receipt["source_group"]
                or index["split"] != receipt["split"]
                or proof["operation"] != index["operation"]
                or proof["mask_checked"] is not True
                or index["dependency_status"]
                != "formal_program_lineage_only; visible_alternatives_unsearched"
            ):
                raise ValueError("P96 final reader identity or status changed")
            samples.add(sample)
            messages = reader["messages"]
            if [entry["role"] for entry in messages] != ["user", "assistant"]:
                raise ValueError("P96 reader roles changed")
            user = messages[0]["content"]
            if SEPARATOR not in user:
                raise ValueError("P96 question boundary absent")
            context, question = user.rsplit(SEPARATOR, 1)
            answer = messages[1]["content"]
            if (
                not context
                or not question
                or hashlib.sha256(context.encode()).hexdigest()
                != index["context_sha256"]
                or hashlib.sha256(answer.encode()).hexdigest() != index["answer_sha256"]
            ):
                raise ValueError("P96 reader context/answer hash changed")
            encoded = tokenize_assistant_only(tokenizer, messages, 262144)
            labels = encoded["labels"]
            input_tokens = sum(value == -100 for value in labels)
            supervised = len(labels) - input_tokens
            if (
                (input_tokens, supervised, len(labels))
                != (
                    index["input_tokens"],
                    index["supervised_tokens"],
                    index["full_chat_tokens"],
                )
                or index["length_bin"] != physical_length_bin(len(labels))
                or supervised <= 0
            ):
                raise ValueError("P96 final assistant-only mask changed")
            prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
            user_start = prompt.find(user)
            if user_start < 0 or prompt.count(user) != 1:
                raise ValueError("P96 user text not unique in chat template")
            offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
                "offset_mapping"
            ]
            query = _token_span(
                offsets,
                user_start + len(context) + len(SEPARATOR),
                user_start + len(user),
            )[0]
            if proof["query_token_start"] != query:
                raise ValueError("P96 query token position changed")
            spans = []
            for item in proof["evidence"]:
                start, end = item["context_span"]
                if context[start:end] != item["quote"]:
                    raise ValueError("P96 visible source quote changed")
                span = _token_span(offsets, user_start + start, user_start + end)
                if span != item["prompt_token_span"] or span[1] > input_tokens:
                    raise ValueError("P96 source evidence token position changed")
                spans.append(span)
            replayed, selected_record = visible_replay(
                context, proof["evidence"], proof["program"]
            )
            if (
                replayed != json.loads(answer)
                or selected_record != proof["trace"]["selected_record_id"]
            ):
                raise ValueError("P96 visible source execution disagrees with answer")
            extent = max(end for _, end in spans) - min(start for start, _ in spans)
            distance = query - max(end for _, end in spans)
            if (
                extent != proof["evidence_extent_tokens"]
                or distance != proof["last_evidence_to_query_tokens"]
            ):
                raise ValueError("P96 evidence position summary changed")
            extents.append(extent)
            distances.append(distance)
            masks["views"] += 1
            masks["visible_answer_replays"] += 1
            masks["input_tokens"] += input_tokens
            masks["supervised_tokens"] += supervised
            lengths[index["length_bin"]] += 1
            operations[index["operation"]] += 1
            families[proof["program"]["selector"]["kind"]] += 1
            task_views[index["semantic_task_id"]].add(index["variant"])
            task_answers[index["semantic_task_id"]].add(answer)
            semantic.add(index["semantic_task_id"])
            counts["views"] += 1
        if (
            counts["views"] != receipt["views"]
            or len(task_views) != receipt["semantic_tasks"]
            or any(
                views != {"complete_statements", "analyst_packet"}
                for views in task_views.values()
            )
            or any(len(answers) != 1 for answers in task_answers.values())
        ):
            raise ValueError("P96 issuer tasks/views differ")
        per_source.append(
            {
                "issuer": issuer,
                "source_group": receipt["source_group"],
                "split": receipt["split"],
                "semantic_tasks": len(task_views),
                "views": counts["views"],
                "planning": receipt["planning"],
            }
        )
    if (
        masks["views"] != batch["candidate_views"]
        or len(semantic) != batch["semantic_tasks"]
    ):
        raise ValueError("P96 batch count differs from final readers")
    result = {
        "schema": AUDIT_SCHEMA,
        "config_sha256": sha(config_path),
        "batch_manifest_sha256": sha(batch_path),
        "sources": per_source,
        "source_groups": len(groups),
        "semantic_tasks": len(semantic),
        "mask": dict(masks),
        "length_bins": dict(sorted(lengths.items())),
        "operations": dict(sorted(operations.items())),
        "selector_families": dict(sorted(families.items())),
        "evidence_extent_tokens": {"min": min(extents), "max": max(extents)},
        "last_evidence_to_query_tokens": {"min": min(distances), "max": max(distances)},
        "scope": "final-visible cell replay and masks; alternative comparative proofs not exhausted",
        "train_ready": False,
    }
    payload = canonical(result) + "\n"
    if verify_only:
        if not output.is_file() or output.read_text() != payload:
            raise ValueError("P96 final audit drift")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = audit(
        args.config, args.batch_dir, args.output, verify_only=args.verify_only
    )
    print(canonical({key: value for key, value in result.items() if key != "sources"}))


if __name__ == "__main__":
    main()
