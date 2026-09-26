"""Source-shape gate and bounded four-filing complete-set reader candidates.

The paper lane is a measured unsupported matrix. The report lane reads frozen P96
reader bytes; it does not use hidden normalized finance facts as answer truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from bisect import bisect_right
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    normalize_native_candidate,
    physical_length_bin,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_p95_report_finance_shared import _visible_amount, _visible_year
from scripts.audit_unified_reader_mask import audit_reader
from scripts.p95_report_finance_shared import canonical, digest
from scripts.p96_finance_factorial import METRIC_LABELS
from scripts.p112_report_route import _number_visible, _report_texts, _source_views
from scripts.run_p95_report_finance_shared import SEPARATOR, _token_span, sha
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p115-real-source-shape.v1"
ROLE = {
    "revenue": r"(?:revenues?|net sales)",
    "operating_income": r"(?:income.*from operations|operating income|operating profit)",
    "cash_from_operations": r"(?:net cash.*operating activities|cash.*operations)",
    "cash_from_investing": r"(?:net cash.*investing activities|cash.*investing)",
    "cash_from_financing": r"(?:net cash.*financing activities|cash.*financing)",
}


def rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def write_rows(path: Path, data: list[dict]) -> None:
    path.write_text("".join(canonical(row) + "\n" for row in data), encoding="utf-8")


def pin(entry: dict) -> Path:
    path = Path(entry["path"])
    if path.is_absolute() or ".." in path.parts or sha(ROOT / path) != entry["sha256"]:
        raise ValueError(f"pinned input drift: {path}")
    return ROOT / path


def patterns(value: int) -> list[re.Pattern]:
    magnitude = abs(value)
    return [
        re.compile(r"(?<!\d)" + re.escape(form) + r"(?!\d)")
        for form in {str(magnitude), f"{magnitude:,}"}
    ]


def redact_all_numeric_support(
    context: str, value: int, cap: int
) -> tuple[str, list[list[int]]]:
    """Mask every exact numeral spelling, including comparative repeats."""
    matches = sorted(
        {(m.start(), m.end()) for pat in patterns(value) for m in pat.finditer(context)}
    )
    if not 1 <= len(matches) <= cap:
        raise ValueError("exact_numeric_support_count_out_of_bounds")
    chars = list(context)
    for start, end in matches:
        chars[start:end] = [
            "#" if char.isdigit() else char for char in chars[start:end]
        ]
    masked = "".join(chars)
    if len(masked) != len(context) or _number_visible(masked, value):
        raise ValueError("exact_numeric_support_survived_redaction")
    return masked, [[start, end] for start, end in matches]


def alternative_support_scope(
    context: str, evidence: list[dict], offsets: list, user_start: int
) -> dict:
    """Lower bound by all exact-numeral occurrences, including comparative columns.

    Numeral matching may include unrelated numbers, so this errs toward rejection.
    It cannot rule out paraphrases, derived values, or all nonnumeric shortcuts.
    """
    headings = list(
        re.finditer(r"(?m)^=== Annual filing: (.*?) ===\nReport date:", context)
    )
    starts = [hit.start() for hit in headings]
    occurrences = []
    for fact, row in enumerate(evidence):
        for start, end in row["all_exact_numeric_occurrences"]:
            ordinal = bisect_right(starts, start) - 1
            if ordinal < 0:
                raise ValueError("numeric support precedes source heading")
            token_start, token_end = _token_span(
                offsets, user_start + start, user_start + end
            )
            occurrences.append(
                (token_start, token_end, fact, headings[ordinal].group(1))
            )
    occurrences.sort()
    min_span = None
    best_docs = None
    for left in range(len(occurrences)):
        seen = set()
        for right in range(left, len(occurrences)):
            seen.add(occurrences[right][2])
            if len(seen) == len(evidence):
                window = occurrences[left : right + 1]
                span = max(row[1] for row in window) - window[0][0]
                if min_span is None or span < min_span:
                    min_span = span
                    best_docs = sorted({row[3] for row in window})
                break
    support_docs = sorted({row[3] for row in occurrences})
    minimum_docs = None
    for count in range(1, len(support_docs) + 1):
        if any(
            all(
                any(o[2] == fact and o[3] in selected for o in occurrences)
                for fact in range(len(evidence))
            )
            for selected in combinations(support_docs, count)
        ):
            minimum_docs = count
            break
    if min_span is None or minimum_docs is None:
        raise ValueError("incomplete_exact_numeric_support")
    return {
        "minimum_exact_numeric_support_extent_tokens": min_span,
        "minimum_exact_numeric_support_reports": minimum_docs,
        "shortest_window_report_ids": best_docs,
        "scope": "all exact numeral spellings in final reader; conservative lower-bound proxy",
    }


def visible_four(context: str, cells: dict, metric: str) -> list[dict]:
    if metric not in ROLE or len(cells) != 20:
        raise ValueError("incomplete_metric_cell_shape")
    reports = _report_texts(context)
    records = sorted(
        {record for record, _ in cells}, key=lambda r: (_visible_year(context, r), r)
    )
    if (
        len(records) != 4
        or len({_visible_year(context, r) for r in records}) != 4
        or set(reports) != set(records)
    ):
        raise ValueError("four_distinct_own_report_headers_required")
    output = []
    heading = list(
        re.finditer(r"(?m)^=== Annual filing: (.*?) ===\nReport date:", context)
    )
    ranges = {}
    for i, hit in enumerate(heading):
        ranges.setdefault(hit.group(1), []).append(
            (
                hit.start(),
                heading[i + 1].start() if i + 1 < len(heading) else len(context),
            )
        )
    for record in records:
        item = cells[record, metric]
        start, end = item["context_span"]
        if context[start:end] != item["quote"] or not any(
            a <= start < end <= b for a, b in ranges[record]
        ):
            raise ValueError("cell_quote_or_own_report_scope_mismatch")
        nearby = [
            line.strip()
            for line in context[max(0, start - 220) : start].splitlines()
            if line.strip()
        ]
        row = nearby[-1] if nearby else ""
        if not re.search(ROLE[metric], row, re.IGNORECASE):
            raise ValueError("metric_row_role_absent")
        own_start = max(a for a, b in ranges[record] if a <= start < b)
        unit_hits = list(
            re.finditer(r"\$\s*in\s*millions", context[own_start:start], re.IGNORECASE)
        )
        if not unit_hits:
            raise ValueError("report_unit_absent")
        unit_start = own_start + unit_hits[-1].start()
        table_title = context[max(own_start, unit_start - 190) : unit_start]
        table_header = context[unit_start : min(start, unit_start + 450)]
        table_years = re.findall(
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+(\d{4})",
            table_header,
            re.IGNORECASE,
        )
        if (
            start - unit_start > 5000
            or not re.search(r"STATEMENTS? OF", table_title, re.IGNORECASE)
            or record.rsplit(":", 1)[-1][:4] not in table_years
            or table_years[0] != record.rsplit(":", 1)[-1][:4]
        ):
            raise ValueError("same_statement_unit_and_own_year_header_absent")
        row_start = context.rfind("\n", 0, start) + 1
        row_end = context.find("\n", end)
        row_text = context[row_start : row_end if row_end >= 0 else len(context)]
        numbers = list(
            re.finditer(r"(?<![\w])(?:\$\s*)?\(?\d[\d,]*(?:\.\d+)?\)?(?!\w)", row_text)
        )
        if not numbers or not (
            row_start + numbers[0].start() <= start < row_start + numbers[0].end()
            or start <= row_start + numbers[0].start() < end
        ):
            raise ValueError("target_not_first_own_year_numeric_column")
        output.append(
            {
                "record_id": record,
                "year": _visible_year(context, record),
                "value": _visible_amount(context, start, end),
                "evidence": item,
            }
        )
    return output


def render(
    context: str,
    records: list[dict],
    metric: str,
    threshold: float,
    variant: str,
    job: dict,
    cap: int,
) -> tuple[dict, dict, dict]:
    years = [r["year"] for r in records]
    answer = {"fiscal_years": [r["year"] for r in records if r["value"] > threshold]}
    threshold_text = str(int(threshold)) if threshold.is_integer() else str(threshold)
    question = (
        f"Across the original annual filings for {', '.join(years)}, list every fiscal year whose "
        f"{METRIC_LABELS[metric]} in that year's own filing exceeds {threshold_text} USD millions. "
        "Read the row under that filing's report year, treat parenthesized amounts as negative, and return "
        'a JSON object with the key "fiscal_years" and years in chronological order.'
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
        raise ValueError("final_chat_or_mask_boundary_invalid")
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    proofs = []
    for record in records:
        item = record["evidence"]
        start, end = item["context_span"]
        token_span = _token_span(offsets, user_start + start, user_start + end)
        if token_span[1] > input_tokens:
            raise ValueError("evidence_overlaps_supervision")
        masked, occurrences = redact_all_numeric_support(context, record["value"], cap)
        try:
            _visible_amount(masked, start, end)
        except ValueError:
            pass
        else:
            raise ValueError("target_cell_remains_readable_after_redaction")
        proofs.append(
            {
                "record_id": record["record_id"],
                "year": record["year"],
                "value": record["value"],
                "context_span": [start, end],
                "prompt_token_span": token_span,
                "all_exact_numeric_occurrences": occurrences,
                "reader_context_minus_sha256": hashlib.sha256(
                    masked.encode()
                ).hexdigest(),
                "intervention_scope": "all exact numeral spellings of this value in the reader context",
            }
        )
    semantic = "task:" + digest(
        [SCHEMA, job["source_group"], metric, threshold_text, years]
    )
    sample = "p115:" + digest(
        [semantic, variant, hashlib.sha256(context.encode()).hexdigest()]
    )
    index = {
        "sample_id": sample,
        "semantic_task_id": semantic,
        "source_kind": "real_finance",
        "source_group": job["source_group"],
        "domain": "finance",
        "topic": job["issuer"],
        "operation": "four_filing_complete_set_threshold",
        "variant": variant,
        "split": job["split"],
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "answer_sha256": hashlib.sha256(answer_text.encode()).hexdigest(),
        "full_chat_tokens": len(labels),
        "input_tokens": input_tokens,
        "supervised_tokens": supervised,
        "length_bin": physical_length_bin(len(labels)),
        "tokenizer_profile": "pinned-chat-template",
        "source_document_count": 4,
        "dependency_status": "four_own_report_cells; all_exact_numeral_support_removed_per_value; paraphrase_unsearched",
    }
    proof = {
        "sample_id": sample,
        "metric": metric,
        "threshold_usd_millions": threshold_text,
        "evidence": proofs,
        "query_token_start": _token_span(
            offsets, user_start + len(context) + len(SEPARATOR), user_start + len(user)
        )[0],
        "evidence_extent_tokens": max(x["prompt_token_span"][1] for x in proofs)
        - min(x["prompt_token_span"][0] for x in proofs),
        "last_evidence_to_query_tokens": _token_span(
            offsets, user_start + len(context) + len(SEPARATOR), user_start + len(user)
        )[0]
        - max(x["prompt_token_span"][1] for x in proofs),
        "mask_checked": True,
    }
    proof["alternative_support_scope"] = alternative_support_scope(
        context, proofs, offsets, user_start
    )
    reader = {"sample_id": sample, "messages": messages}
    audit_reader(reader, index, tokenizer, 262144)
    return reader, index, proof


def issuer(
    job: dict, source_dir: str, output_dir: str, cap: int, min_span_tokens: int
) -> dict:
    source = Path(source_dir) / job["issuer"]
    source_manifest = json.loads((source / "manifest.json").read_text())
    if (
        sha(source / "manifest.json") != job["manifest_sha256"]
        or source_manifest["source_group"] != job["source_group"]
        or source_manifest["split"] != job["split"]
    ):
        raise ValueError("source receipt/group/split drift")
    views = _source_views(source, source_manifest)
    matrix, accepted = [], []
    for metric in METRIC_LABELS:
        cell = {
            "source_kind": "real_finance",
            "source_group": job["source_group"],
            "issuer": job["issuer"],
            "split": job["split"],
            "metric": metric,
            "source_manifest_sha256": job["manifest_sha256"],
        }
        try:
            facts = [
                visible_four(views[variant][0], views[variant][1], metric)
                for variant in ("complete_statements", "analyst_packet")
            ]
            if [(r["year"], r["value"]) for r in facts[0]] != [
                (r["year"], r["value"]) for r in facts[1]
            ]:
                raise ValueError("reader_view_value_disagreement")
            values = [r["value"] for r in facts[0]]
            order = sorted(values)
            if len(set(values)) != 4:
                raise ValueError("tied_year_values")
            # One balanced threshold per source-metric cell; this is semantic, not a renderer multiplier.
            rank = 1 + int(digest([job["source_group"], metric])[:8], 16) % 3
            threshold = (order[rank - 1] + order[rank]) / 2
            if any(
                sum(_number_visible(chunk, value) for value in values) == 4
                for chunk in _report_texts(views["complete_statements"][0]).values()
            ):
                raise ValueError("single_report_contains_all_four_values")
            outputs = []
            for variant, fact_rows in zip(
                ("complete_statements", "analyst_packet"), facts, strict=True
            ):
                context = views[variant][0]
                if any(
                    sum(_number_visible(chunk, value) for value in values) == 4
                    for chunk in _report_texts(context).values()
                ):
                    raise ValueError("single_report_contains_all_four_values")
                outputs.append(
                    render(context, fact_rows, metric, threshold, variant, job, cap)
                )
            if (
                outputs[0][0]["messages"][1]["content"]
                != outputs[1][0]["messages"][1]["content"]
            ):
                raise ValueError("reader_view_answer_disagreement")
            scopes = [output[2]["alternative_support_scope"] for output in outputs]
            cell["alternative_support_scope"] = scopes
            if any(
                scope["minimum_exact_numeric_support_reports"] < 2 for scope in scopes
            ):
                raise ValueError("single_report_alternative_numeric_support")
            if any(
                scope["minimum_exact_numeric_support_extent_tokens"] < min_span_tokens
                for scope in scopes
            ):
                raise ValueError("short_alternative_numeric_support_window")
            cell.update(
                status="candidate_after_bounded_reader_check",
                reason="",
                threshold_usd_millions=threshold,
                answer=json.loads(outputs[0][0]["messages"][1]["content"]),
                semantic_task_id=outputs[0][1]["semantic_task_id"],
            )
            accepted.extend(outputs)
        except ValueError as error:
            cell.update(status="rejected", reason=str(error))
        matrix.append(cell)
    output = Path(output_dir) / job["issuer"]
    output.mkdir(parents=True)
    write_rows(output / "reader.jsonl", [x[0] for x in accepted])
    write_rows(output / "sample_index.jsonl", [x[1] for x in accepted])
    write_rows(output / "proof.jsonl", [x[2] for x in accepted])
    write_rows(output / "support_matrix.jsonl", matrix)
    manifest = {
        "schema": SCHEMA + ".issuer",
        "issuer": job["issuer"],
        "source_manifest_sha256": job["manifest_sha256"],
        "source_group": job["source_group"],
        "split": job["split"],
        "source_cells": len(matrix),
        "candidate_tasks": len(accepted) // 2,
        "candidate_views": len(accepted),
        "status_counts": dict(
            sorted(
                Counter(
                    row["reason"] if row["status"] == "rejected" else row["status"]
                    for row in matrix
                ).items()
            )
        ),
        "file_sha256": {
            name: sha(output / name)
            for name in (
                "reader.jsonl",
                "sample_index.jsonl",
                "proof.jsonl",
                "support_matrix.jsonl",
            )
        },
        "train_ready": False,
    }
    (output / "manifest.json").write_text(canonical(manifest) + "\n")
    return {
        "issuer": job["issuer"],
        "manifest_sha256": sha(output / "manifest.json"),
        "candidate_tasks": manifest["candidate_tasks"],
        "candidate_views": manifest["candidate_views"],
        "status_counts": manifest["status_counts"],
    }


def build(config_path: Path, output: Path, workers: int) -> dict:
    if output.exists() or not 1 <= workers <= 4:
        raise ValueError("output exists or invalid worker count")
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("min_exact_support_span_tokens", 0) < 1
    ):
        raise ValueError("invalid config schema")
    for key in ("finance_batch", "finance_final_audit"):
        pin(config[key])
    for cohort in ("prior_index", "current_index"):
        for key in ("manifest", "refs"):
            pin(config[cohort][key])
    paper = []
    for cohort in ("paper_hist", "paper_new"):
        pin(config[cohort]["manifest"])
        for row in rows(pin(config[cohort]["matrix"])):
            archive = row["source_archive"]
            if sha(ROOT / archive["path"]) != archive["sha256"]:
                raise ValueError("paper archive drift")
            paper.append(
                {
                    "source_kind": "real_paper",
                    "source_group": row["source_group"],
                    "split": row["split"],
                    "cohort": cohort,
                    "source_archive_sha256": archive["sha256"],
                    "source_status": row["source_status"],
                    "status": "unsupported",
                    "reason": "source_parser_rejected"
                    if row["source_status"] == "rejected_source_parser"
                    else "no_independently_verified_typed_table_or_definition_adapter",
                }
            )
    batch = json.loads(pin(config["finance_batch"]).read_text())
    jobs = batch["jobs"]
    if len(jobs) != 8 or any(j["status"] != "verified_candidate" for j in jobs):
        raise ValueError("frozen issuer cohort incomplete")
    output.mkdir(parents=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                issuer,
                jobs,
                [str(ROOT / config["finance_batch_dir"])] * len(jobs),
                [str(output)] * len(jobs),
                [config["max_report_value_occurrences"]] * len(jobs),
                [config["min_exact_support_span_tokens"]] * len(jobs),
            )
        )
    write_rows(output / "paper_unsupported.jsonl", paper)
    prior = {
        (
            r["candidate"]["source_kind"],
            r["candidate"]["source_group"],
            r["candidate"]["semantic_task_id"],
        ): r["candidate"]["answer_sha256"]
        for r in rows(pin(config["prior_index"]["refs"]))
    }
    current = {
        (
            r["candidate"]["source_kind"],
            r["candidate"]["source_group"],
            r["candidate"]["semantic_task_id"],
        ): r["candidate"]["answer_sha256"]
        for r in rows(pin(config["current_index"]["refs"]))
    }
    novelty = []
    ledger = CandidateLedger()
    all_indices = []
    for job in jobs:
        source_receipt = (
            ROOT / config["finance_batch_dir"] / job["issuer"] / "manifest.json"
        )
        binding = AdapterBinding(
            source_kind="real_finance",
            source_group=job["source_group"],
            domain="finance",
            topic=job["issuer"],
            operation="four_filing_complete_set_threshold",
            evidence_profile="same_statement_unit_year_first_column_and_bounded_numeric_redaction",
            tokenizer_profile="pinned-chat-template",
            receipt_path=source_receipt,
            receipt_sha256=job["manifest_sha256"],
        )
        indices = rows(output / job["issuer"] / "sample_index.jsonl")
        readers = rows(output / job["issuer"] / "reader.jsonl")
        if len(indices) != len(readers):
            raise ValueError("P115 reader/index count mismatch")
        for index, reader in zip(indices, readers, strict=True):
            context = reader["messages"][0]["content"].rsplit(SEPARATOR, 1)[0]
            ledger.add(
                normalize_native_candidate(index, reader, binding, context_text=context)
            )
            all_indices.append(index)
    seen_task = set()
    for index in all_indices:
        key = (index["source_kind"], index["source_group"], index["semantic_task_id"])
        if key in seen_task:
            continue
        seen_task.add(key)
        novelty.append(
            {
                "source_kind": key[0],
                "source_group": key[1],
                "semantic_task_id": key[2],
                "answer_sha256": index["answer_sha256"],
                "historical_p113_overlap": key in prior,
                "status": "current_exact_same_answer"
                if current.get(key) == index["answer_sha256"]
                else "current_task_id_answer_conflict"
                if key in current
                else "net_new",
            }
        )
    write_rows(output / "novelty.jsonl", novelty)
    receipt = {
        "schema": SCHEMA + ".batch",
        "config_sha256": sha(config_path),
        "paper_source_groups": len(paper),
        "paper_status_counts": dict(
            sorted(Counter(r["reason"] for r in paper).items())
        ),
        "finance_source_cells": sum(sum(r["status_counts"].values()) for r in results),
        "candidate_tasks_gross": sum(r["candidate_tasks"] for r in results),
        "candidate_views_gross": sum(r["candidate_views"] for r in results),
        "normalized_candidate_tasks": ledger.independent_tasks,
        "normalized_candidate_views": ledger.rows,
        "net_new_tasks": sum(r["status"] == "net_new" for r in novelty),
        "net_new_views": sum(2 for r in novelty if r["status"] == "net_new"),
        "finance_status_counts": dict(
            sorted(
                Counter(
                    {
                        k: sum(r["status_counts"].get(k, 0) for r in results)
                        for k in set().union(*(r["status_counts"] for r in results))
                    }
                ).items()
            )
        ),
        "jobs": results,
        "source_pins": {
            k: config[k]
            for k in (
                "finance_batch",
                "finance_final_audit",
                "prior_index",
                "current_index",
                "paper_hist",
                "paper_new",
            )
        },
        "file_sha256": {
            name: sha(output / name)
            for name in ("paper_unsupported.jsonl", "novelty.jsonl")
        },
        "verification_scope": "source-visible_numeric_rows_units_four_reports; all_exact_numeral_redaction_per_value; final_chat_mask; no_paraphrase_or_arithmetic_alternative_proof",
        "train_ready": False,
    }
    (output / "manifest.json").write_text(canonical(receipt) + "\n")
    return receipt


def verify(config: Path, output: Path, workers: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="p115_verify_") as temp:
        replay = Path(temp) / "replay"
        receipt = build(config, replay, workers)
        paths = {p.relative_to(replay) for p in replay.rglob("*") if p.is_file()}
        if paths != {p.relative_to(output) for p in output.rglob("*") if p.is_file()}:
            raise ValueError("P115 replay file inventory mismatch")
        for relative in paths:
            if (replay / relative).read_bytes() != (output / relative).read_bytes():
                raise ValueError(f"P115 replay mismatch: {relative}")
        return receipt


def shard(config_path: Path, native: Path, output: Path) -> dict:
    """Materialize only the net-new native rows as an isolated unified shard."""
    if output.exists():
        raise ValueError("P115 unified shard output exists")
    config = json.loads(config_path.read_text())
    manifest = json.loads((native / "manifest.json").read_text())
    if manifest.get("schema") != SCHEMA + ".batch" or manifest.get(
        "config_sha256"
    ) != sha(config_path):
        raise ValueError("P115 native receipt/config mismatch")
    if manifest["source_pins"].get("current_index") != config["current_index"]:
        raise ValueError("P115 current novelty index pin drift")
    for name, expected in manifest["file_sha256"].items():
        if sha(native / name) != expected:
            raise ValueError("P115 native batch file drift")
    novelty = rows(native / "novelty.jsonl")
    if len(novelty) != manifest["candidate_tasks_gross"] or any(
        row["status"] != "net_new" for row in novelty
    ):
        raise ValueError("P115 shard contains non-novel tasks")
    ledger = CandidateLedger()
    tokenizer = get_tokenizer()
    readers = {"train": [], "eval": []}
    index_rows, mask_rows = [], []
    for job in manifest["jobs"]:
        issuer_dir = native / job["issuer"]
        if sha(issuer_dir / "manifest.json") != job["manifest_sha256"]:
            raise ValueError("P115 native issuer receipt drift")
        issuer_manifest = json.loads((issuer_dir / "manifest.json").read_text())
        for name, expected in issuer_manifest["file_sha256"].items():
            if sha(issuer_dir / name) != expected:
                raise ValueError("P115 native issuer file drift")
        source_receipt = (
            ROOT / config["finance_batch_dir"] / job["issuer"] / "manifest.json"
        )
        binding = AdapterBinding(
            source_kind="real_finance",
            source_group=issuer_manifest["source_group"],
            domain="finance",
            topic=job["issuer"],
            operation="four_filing_complete_set_threshold",
            evidence_profile="same_statement_unit_year_first_column_and_bounded_numeric_redaction",
            tokenizer_profile="pinned-chat-template",
            receipt_path=source_receipt,
            receipt_sha256=issuer_manifest["source_manifest_sha256"],
        )
        triples = zip(
            rows(issuer_dir / "reader.jsonl"),
            rows(issuer_dir / "sample_index.jsonl"),
            rows(issuer_dir / "proof.jsonl"),
            strict=True,
        )
        for native_row, (reader, index, proof) in enumerate(triples):
            if proof["sample_id"] != index["sample_id"] or not proof["mask_checked"]:
                raise ValueError("P115 native proof/index mismatch")
            context = reader["messages"][0]["content"].rsplit(SEPARATOR, 1)[0]
            candidate = normalize_native_candidate(
                index, reader, binding, context_text=context
            )
            ledger.add(candidate)
            mask_rows.append(audit_reader(reader, index, tokenizer, 262144))
            split = candidate.split
            pointer = candidate.to_dict()
            pointer.update(
                source_name="p115_real_report_complete_set",
                native_row_ref=f"{issuer_dir / 'reader.jsonl'}:{native_row}",
                native_proof_ref=f"{issuer_dir / 'proof.jsonl'}:{native_row}",
                native_manifest_sha256=job["manifest_sha256"],
                output_file=f"candidate_{split}.jsonl",
                row_index=len(readers[split]),
            )
            readers[split].append(reader)
            index_rows.append(pointer)
    if (
        ledger.independent_tasks != manifest["net_new_tasks"]
        or ledger.rows != manifest["net_new_views"]
    ):
        raise ValueError("P115 unified task/view ledger mismatch")
    output.mkdir(parents=True)
    write_rows(output / "candidate_train.jsonl", readers["train"])
    write_rows(output / "candidate_eval.jsonl", readers["eval"])
    write_rows(output / "sample_index.jsonl", index_rows)
    write_rows(output / "mask_audit.jsonl", mask_rows)
    receipt = {
        "schema_version": "longworld.unified-candidates.v1",
        "quality_gate_schema": SCHEMA,
        "native_manifest_sha256": sha(native / "manifest.json"),
        "current_index_manifest_sha256": config["current_index"]["manifest"]["sha256"],
        "current_index_refs_sha256": config["current_index"]["refs"]["sha256"],
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "views_by_lane": {"p115_real_report_complete_set": ledger.rows},
        "splits": {split: len(values) for split, values in readers.items() if values},
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in index_rows).items())
        ),
        "mask_checked_views": len(mask_rows),
        "files_sha256": {
            name: sha(output / name)
            for name in (
                "candidate_train.jsonl",
                "candidate_eval.jsonl",
                "sample_index.jsonl",
                "mask_audit.jsonl",
            )
        },
        "dependency_scope": "minimum two-report exact-numeric support and at least 16384 final-chat tokens; no exhaustive paraphrase proof",
        "train_ready": False,
    }
    (output / "manifest.json").write_text(canonical(receipt) + "\n")
    verify_merge(output)
    return receipt


def verify_shard(config: Path, native: Path, output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="p115_shard_verify_") as temp:
        replay = Path(temp) / "shard"
        receipt = shard(config, native, replay)
        paths = {p.relative_to(replay) for p in replay.rglob("*") if p.is_file()}
        if paths != {p.relative_to(output) for p in output.rglob("*") if p.is_file()}:
            raise ValueError("P115 shard replay inventory mismatch")
        for relative in paths:
            if (replay / relative).read_bytes() != (output / relative).read_bytes():
                raise ValueError(f"P115 shard replay mismatch: {relative}")
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--shard-native", type=Path)
    parser.add_argument("--shard-verify-only", action="store_true")
    args = parser.parse_args()
    if args.shard_native:
        result = (
            verify_shard(args.config, args.shard_native, args.output)
            if args.shard_verify_only
            else shard(args.config, args.shard_native, args.output)
        )
    elif args.shard_verify_only:
        raise ValueError("--shard-verify-only requires --shard-native")
    else:
        result = (
            verify(args.config, args.output, args.workers)
            if args.verify_only
            else build(args.config, args.output, args.workers)
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
