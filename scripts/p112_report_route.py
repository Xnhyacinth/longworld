"""Route source-visible annual-report cells to dependent period-delta tasks.

This is a candidate lane over P96's frozen, audited original-report readers.
It does not infer unsupported prose relations or prove that comparative columns
cannot supply an alternative proof elsewhere in a filing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import physical_length_bin
from scripts.audit_p95_report_finance_shared import _visible_amount, _visible_year
from scripts.audit_p95_report_selector_interventions import _replacement_quotes
from scripts.p95_report_finance_shared import canonical, digest
from scripts.p96_finance_factorial import METRIC_LABELS
from scripts.run_p95_report_finance_shared import SEPARATOR, _token_span, sha
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p112-report-route.v2"
KINDS = ("max_value", "min_value")
METRICS = tuple(METRIC_LABELS)
ENDPOINTS = ("earliest", "latest")
CODE_FILES = (
    "scripts/p112_report_route.py",
    "scripts/audit_p95_report_finance_shared.py",
    "scripts/p96_finance_factorial.py",
    "scripts/train_sft.py",
)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _write(path: Path, value: object) -> None:
    path.write_text(canonical(value) + "\n", encoding="utf-8")


def _write_rows(path: Path, values: list[dict]) -> None:
    path.write_text("".join(canonical(v) + "\n" for v in values), encoding="utf-8")


def execute_visible(
    context: str, cells: dict[tuple[str, str], dict], program: dict
) -> tuple[dict, list[tuple[str, str]], str]:
    """Independently read final-visible values, select a period, then subtract."""
    if set(program) != {
        "selector_kind",
        "selector_metric",
        "target_metric",
        "baseline_endpoint",
    }:
        raise ValueError("invalid program fields")
    kind, selector, target = (
        program[k] for k in ("selector_kind", "selector_metric", "target_metric")
    )
    if (
        kind not in KINDS
        or selector not in METRICS
        or target not in METRICS
        or selector == target
        or program["baseline_endpoint"] not in ENDPOINTS
    ):
        raise ValueError("invalid source-operation pairing")
    records = sorted(
        {record for record, _ in cells}, key=lambda r: (_visible_year(context, r), r)
    )
    if len(records) != 4 or len({_visible_year(context, r) for r in records}) != 4:
        raise ValueError("four distinct annual source records required")
    selectors = [
        _visible_amount(context, *cells[r, selector]["context_span"]) for r in records
    ]
    best = max(selectors) if kind == "max_value" else min(selectors)
    if selectors.count(best) != 1:
        raise ValueError("selector_tie")
    selected = selectors.index(best)
    baseline_index = 0 if program["baseline_endpoint"] == "earliest" else 3
    if selected == baseline_index:
        raise ValueError("selected_reference_endpoint")
    current, previous = records[selected], records[baseline_index]
    current_value = _visible_amount(context, *cells[current, target]["context_span"])
    previous_value = _visible_amount(context, *cells[previous, target]["context_span"])
    if current_value == previous_value:
        raise ValueError("zero_target_delta")
    answer = {
        "selected_fiscal_year": _visible_year(context, current),
        "target_metric": target,
        "reference_fiscal_year": _visible_year(context, previous),
        "change_from_reference_year_usd_millions": current_value - previous_value,
    }
    lineage = [(r, selector) for r in records] + [(previous, target), (current, target)]
    return answer, lineage, current


@lru_cache(maxsize=32)
def _report_texts(context: str) -> dict[str, str]:
    """Group final-visible chunks by their original annual-filing heading."""
    headings = list(
        re.finditer(r"(?m)^=== Annual filing: (.*?) ===\nReport date:", context)
    )
    if not headings:
        raise ValueError("final reader has no source report headings")
    chunks: dict[str, list[str]] = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(context)
        chunks.setdefault(heading.group(1), []).append(context[heading.start() : end])
    return {record: "\n".join(parts) for record, parts in chunks.items()}


def _number_visible(text: str, value: int) -> bool:
    magnitude = abs(value)
    return any(
        re.search(rf"(?<!\d){re.escape(display)}(?!\d)", text)
        for display in {str(magnitude), f"{magnitude:,}"}
    )


def shortcut_reason(
    context: str, cells: dict, lineage: list, answer: dict
) -> str | None:
    """Conservatively reject direct numbers in one report or the answer itself.

    Numeric-only matching over-rejects; surviving cases cannot have the exact
    same Arabic-numeral evidence in a single visible report. Paraphrases remain
    outside this bounded search.
    """
    first, selected = lineage[-2:]
    baseline = _visible_amount(context, *cells[first]["context_span"])
    current = _visible_amount(context, *cells[selected]["context_span"])
    reports = _report_texts(context)
    if any(
        _number_visible(text, baseline) and _number_visible(text, current)
        for text in reports.values()
    ):
        return "one_report_contains_both_target_numbers"
    difference = answer["change_from_reference_year_usd_millions"]
    if any(_number_visible(text, difference) for text in reports.values()):
        return "answer_number_visible_in_report"
    return None


def _selector_flip(
    context: str, cells: dict, program: dict, baseline: dict, selected: str
) -> dict | None:
    """Bounded same-width visible edit that changes both period and delta."""
    for key, item in sorted(cells.items()):
        if key[1] != program["selector_metric"]:
            continue
        start, end = item["context_span"]
        for replacement in _replacement_quotes(
            item["quote"], allow_signed=key[1] != "revenue"
        ):
            if len(replacement) != end - start:
                continue
            changed = context[:start] + replacement + context[end:]
            changed_cells = {**cells, key: {**item, "quote": replacement}}
            try:
                answer, _, next_selected = execute_visible(
                    changed, changed_cells, program
                )
            except ValueError:
                continue
            if (
                next_selected != selected
                and answer["change_from_reference_year_usd_millions"]
                != baseline["change_from_reference_year_usd_millions"]
            ):
                return {
                    "record_id": key[0],
                    "role": key[1],
                    "span": [start, end],
                    "replacement": replacement,
                    "answer": answer,
                }
    return None


def _target_flip(
    context: str,
    cells: dict,
    program: dict,
    baseline: dict,
    lineage: list[tuple[str, str]],
) -> dict | None:
    for key in lineage[-2:]:
        item = cells[key]
        start, end = item["context_span"]
        for replacement in _replacement_quotes(
            item["quote"], allow_signed=key[1] != "revenue"
        ):
            if len(replacement) != end - start:
                continue
            changed = context[:start] + replacement + context[end:]
            try:
                answer, _, _ = execute_visible(
                    changed, {**cells, key: {**item, "quote": replacement}}, program
                )
            except ValueError:
                continue
            if (
                answer["change_from_reference_year_usd_millions"]
                != baseline["change_from_reference_year_usd_millions"]
            ):
                return {
                    "record_id": key[0],
                    "role": key[1],
                    "span": [start, end],
                    "replacement": replacement,
                    "answer": answer,
                }
    return None


def _source_views(native: Path, manifest: dict) -> dict[str, tuple[str, dict]]:
    for name, expected in manifest["file_sha256"].items():
        if sha(native / name) != expected:
            raise ValueError(f"P96 source file changed: {native / name}")
    grouped = defaultdict(list)
    for reader, index, proof in zip(
        _rows(native / "reader.jsonl"),
        _rows(native / "sample_index.jsonl"),
        _rows(native / "audit.jsonl"),
        strict=True,
    ):
        if (
            reader["sample_id"] != index["sample_id"]
            or proof["sample_id"] != index["sample_id"]
        ):
            raise ValueError("P96 source reader/audit identity mismatch")
        grouped[index["variant"]].append((reader, index, proof))
    output = {}
    for variant, rows in grouped.items():
        contexts = {
            r[0]["messages"][0]["content"].rsplit(SEPARATOR, 1)[0] for r in rows
        }
        if len(contexts) != 1:
            raise ValueError("P96 issuer variant has multiple contexts")
        context = next(iter(contexts))
        cells = {}
        for _, _, proof in rows:
            for item in proof["evidence"]:
                key = item["record_id"], item["role"]
                if key in cells and cells[key] != item:
                    raise ValueError("inconsistent P96 visible cell")
                if context[slice(*item["context_span"])] != item["quote"]:
                    raise ValueError("P96 evidence quote absent from final context")
                cells[key] = item
        if len(cells) != 20 or {role for _, role in cells} != set(METRICS):
            raise ValueError("source has incomplete five-metric/four-year shape")
        output[variant] = context, cells
    if set(output) != {"complete_statements", "analyst_packet"}:
        raise ValueError("P96 reader-view pair incomplete")
    return output


def _render(
    context: str,
    cells: dict,
    program: dict,
    answer: dict,
    lineage: list,
    variant: str,
    issuer: str,
    source_group: str,
    split: str,
) -> tuple[dict, dict, dict]:
    years = sorted({_visible_year(context, r) for r, _ in cells})
    direction = "highest" if program["selector_kind"] == "max_value" else "lowest"
    question = (
        f"Across the original annual filings for {', '.join(years)}, identify the fiscal year with the {direction} "
        f"{METRIC_LABELS[program['selector_metric']]}. Relative to the {program['baseline_endpoint']} fiscal year in this set, "
        f"how much did {METRIC_LABELS[program['target_metric']]} change? Use the values in each year's own filing. "
        "Give the signed change in USD millions."
    )
    user = context + SEPARATOR + question
    answer_text = canonical(answer)
    messages = [
        {"role": "user", "content": user},
        {"role": "assistant", "content": answer_text},
    ]
    tokenizer = get_tokenizer()
    encoded = tokenize_assistant_only(tokenizer, messages, 262144)
    labels = encoded["labels"]
    input_tokens = sum(value == -100 for value in labels)
    supervised = len(labels) - input_tokens
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    user_start = prompt.find(user)
    if user_start < 0 or prompt.count(user) != 1 or supervised <= 0:
        raise ValueError("final reader chat boundary or mask invalid")
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    evidence = []
    for key in dict.fromkeys(lineage):
        item = cells[key]
        start, end = item["context_span"]
        token_span = _token_span(offsets, user_start + start, user_start + end)
        if token_span[1] > input_tokens:
            raise ValueError("visible evidence overlaps assistant tokens")
        evidence.append({**item, "prompt_token_span": token_span})
    query = _token_span(
        offsets, user_start + len(context) + len(SEPARATOR), user_start + len(user)
    )[0]
    semantic = "task:" + digest([SCHEMA, source_group, program])
    sample = "p112:" + digest(
        [semantic, variant, hashlib.sha256(context.encode()).hexdigest()]
    )
    operation = f"{program['selector_kind']}:{program['selector_metric']}->{program['baseline_endpoint']}_delta:{program['target_metric']}"
    reader = {"sample_id": sample, "messages": messages}
    index = {
        "sample_id": sample,
        "semantic_task_id": semantic,
        "operation": operation,
        "variant": variant,
        "source_kind": "real_finance",
        "source_group": source_group,
        "domain": "finance",
        "topic": issuer,
        "split": split,
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "answer_sha256": hashlib.sha256(answer_text.encode()).hexdigest(),
        "full_chat_tokens": len(labels),
        "input_tokens": input_tokens,
        "supervised_tokens": supervised,
        "length_bin": physical_length_bin(len(labels)),
        "source_document_count": 4,
        "dependency_status": "visible_selector_and_target_edits; alternative_comparative_support_unsearched",
    }
    proof = {
        "sample_id": sample,
        "operation": operation,
        "program": program,
        "evidence": evidence,
        "query_token_start": query,
        "evidence_extent_tokens": max(x["prompt_token_span"][1] for x in evidence)
        - min(x["prompt_token_span"][0] for x in evidence),
        "last_evidence_to_query_tokens": query
        - max(x["prompt_token_span"][1] for x in evidence),
        "mask_checked": True,
    }
    return reader, index, proof


def _issuer(job: dict, source_dir: str, destination: str, max_tasks: int) -> dict:
    source = Path(source_dir) / job["issuer"]
    source_manifest = json.loads((source / "manifest.json").read_text())
    if (
        source_manifest["source_group"] != job["source_group"]
        or source_manifest["split"] != job["split"]
    ):
        raise ValueError("issuer source group or split drift")
    views = _source_views(source, source_manifest)
    matrix, viable = [], []
    for selector, target, kind, endpoint in product(METRICS, METRICS, KINDS, ENDPOINTS):
        if selector == target:
            continue
        program = {
            "selector_kind": kind,
            "selector_metric": selector,
            "target_metric": target,
            "baseline_endpoint": endpoint,
        }
        cell = {"program": program}
        outcomes = []
        for variant in ("complete_statements", "analyst_packet"):
            context, cells = views[variant]
            try:
                answer, lineage, selected = execute_visible(context, cells, program)
                shortcut = shortcut_reason(context, cells, lineage, answer)
                if shortcut is not None:
                    cell.update(
                        status="single_report_or_answer_shortcut", reason=shortcut
                    )
                    break
                selector_edit = _selector_flip(
                    context, cells, program, answer, selected
                )
                target_edit = _target_flip(context, cells, program, answer, lineage)
            except ValueError as error:
                cell.update(status="unsupported_source_shape", reason=str(error))
                break
            if selector_edit is None or target_edit is None:
                cell.update(
                    status="bounded_intervention_failed",
                    reason="selector" if selector_edit is None else "target",
                )
                break
            outcomes.append((variant, answer, lineage, selector_edit, target_edit))
        else:
            if outcomes[0][1] != outcomes[1][1]:
                cell.update(status="reader_view_answer_disagreement", reason="")
            else:
                viable.append((program, cell, outcomes))
                cell.update(
                    status="legal_candidate",
                    reason="",
                    selected_year=outcomes[0][1]["selected_fiscal_year"],
                )
        matrix.append(cell)
    selected, family_count, target_count, year_count, endpoint_count = (
        [],
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    while viable:
        viable.sort(
            key=lambda entry: (
                family_count[entry[0]["selector_kind"]],
                target_count[entry[0]["target_metric"]],
                year_count[entry[2][0][1]["selected_fiscal_year"]],
                endpoint_count[entry[0]["baseline_endpoint"]],
                digest([job["source_group"], entry[0]]),
            )
        )
        program, cell, outcomes = viable.pop(0)
        if len(selected) >= max_tasks:
            cell.update(status="not_selected_budget", reason="source task cap")
            continue
        family, target, year, endpoint = (
            program["selector_kind"],
            program["target_metric"],
            outcomes[0][1]["selected_fiscal_year"],
            program["baseline_endpoint"],
        )
        if (
            family_count[family] >= (max_tasks + 1) // 2
            or target_count[target] >= 3
            or year_count[year] >= (max_tasks + 1) // 2
            or endpoint_count[endpoint] >= (max_tasks + 1) // 2
        ):
            cell.update(
                status="rejected_balance_cap", reason="family/target/year/endpoint cap"
            )
            continue
        family_count[family] += 1
        target_count[target] += 1
        year_count[year] += 1
        endpoint_count[endpoint] += 1
        cell.update(status="selected", reason="")
        selected.append((program, outcomes))
    readers, indices, audits, edits = [], [], [], []
    for program, outcomes in selected:
        for variant, answer, lineage, selector_edit, target_edit in outcomes:
            context, cells = views[variant]
            reader, index, proof = _render(
                context,
                cells,
                program,
                answer,
                lineage,
                variant,
                job["issuer"],
                job["source_group"],
                job["split"],
            )
            readers.append(reader)
            indices.append(index)
            audits.append(proof)
            edits.append(
                {
                    "sample_id": index["sample_id"],
                    "selector": selector_edit,
                    "target": target_edit,
                }
            )
    output = Path(destination) / job["issuer"]
    output.mkdir(parents=True)
    _write_rows(output / "reader.jsonl", readers)
    _write_rows(output / "sample_index.jsonl", indices)
    _write_rows(output / "audit.jsonl", audits)
    _write_rows(output / "support_matrix.jsonl", matrix)
    _write_rows(output / "interventions.jsonl", edits)
    receipt = {
        "schema": SCHEMA + ".issuer",
        "source_manifest_sha256": sha(source / "manifest.json"),
        "source_group": job["source_group"],
        "split": job["split"],
        "issuer": job["issuer"],
        "matrix_cells": len(matrix),
        "cell_statuses": dict(sorted(Counter(x["status"] for x in matrix).items())),
        "semantic_tasks": len(selected),
        "views": len(readers),
        "mask_checked": len(audits),
        "length_bins": dict(sorted(Counter(x["length_bin"] for x in indices).items())),
        "file_sha256": {
            name: sha(output / name)
            for name in (
                "reader.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
                "support_matrix.jsonl",
                "interventions.jsonl",
            )
        },
        "train_ready": False,
    }
    _write(output / "manifest.json", receipt)
    return {
        "issuer": job["issuer"],
        "manifest_sha256": sha(output / "manifest.json"),
        "semantic_tasks": len(selected),
        "views": len(readers),
        "matrix_cells": len(matrix),
        "cell_statuses": receipt["cell_statuses"],
    }


def build(config_path: Path, output: Path, workers: int) -> dict:
    if output.exists() or not 1 <= workers <= 8:
        raise ValueError("existing output or invalid worker count")
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or not 1 <= config["max_tasks_per_world"] <= 24
    ):
        raise ValueError("invalid report route config")
    for key in ("source_batch_manifest", "source_final_audit"):
        pin = config[key]
        if sha(ROOT / pin["path"]) != pin["sha256"]:
            raise ValueError(f"{key} pin drift")
    batch = json.loads((ROOT / config["source_batch_manifest"]["path"]).read_text())
    if batch["source_groups"] != len(batch["jobs"]):
        raise ValueError("P96 source batch incomplete")
    jobs = []
    for row in batch["jobs"]:
        path = ROOT / config["source_batch_dir"] / row["issuer"] / "manifest.json"
        if row["status"] != "verified_candidate" or sha(path) != row["manifest_sha256"]:
            raise ValueError("P96 issuer receipt drift")
        source = json.loads(path.read_text())
        jobs.append(
            {
                "issuer": row["issuer"],
                "source_group": source["source_group"],
                "split": source["split"],
            }
        )
    output.mkdir(parents=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                _issuer,
                jobs,
                [str(ROOT / config["source_batch_dir"])] * len(jobs),
                [str(output)] * len(jobs),
                [config["max_tasks_per_world"]] * len(jobs),
            )
        )
    receipt = {
        "schema": SCHEMA + ".batch",
        "config_sha256": sha(config_path),
        "code_sha256": {name: sha(ROOT / name) for name in CODE_FILES},
        "source_batch_manifest_sha256": config["source_batch_manifest"]["sha256"],
        "source_final_audit_sha256": config["source_final_audit"]["sha256"],
        "jobs": results,
        "source_groups": len(results),
        "semantic_tasks": sum(x["semantic_tasks"] for x in results),
        "views": sum(x["views"] for x in results),
        "matrix_cells": sum(x["matrix_cells"] for x in results),
        "cell_statuses": dict(
            sorted(
                Counter(
                    {
                        k: sum(x["cell_statuses"].get(k, 0) for x in results)
                        for k in set().union(*(x["cell_statuses"] for x in results))
                    }
                ).items()
            )
        ),
        "train_ready": False,
    }
    _write(output / "batch_manifest.json", receipt)
    return receipt


def verify(config: Path, output: Path, workers: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="p112_report_verify_") as temp:
        reproduced = build(config, Path(temp) / "replay", workers)
        for path in (Path(temp) / "replay").rglob("*"):
            if path.is_file():
                relative = path.relative_to(Path(temp) / "replay")
                if (
                    not (output / relative).is_file()
                    or path.read_bytes() != (output / relative).read_bytes()
                ):
                    raise ValueError(f"P112 replay mismatch: {relative}")
        if {p.relative_to(output) for p in output.rglob("*") if p.is_file()} - {
            Path("final_audit.json")
        } != {
            p.relative_to(Path(temp) / "replay")
            for p in (Path(temp) / "replay").rglob("*")
            if p.is_file()
        }:
            raise ValueError("P112 output contains unverified files")
        return reproduced


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        verify(args.config, args.output, args.workers)
        if args.verify_only
        else build(args.config, args.output, args.workers)
    )
    print(canonical({key: value for key, value in result.items() if key != "jobs"}))


if __name__ == "__main__":
    main()
