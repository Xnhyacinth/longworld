"""Bounded reader-text deletion audit for P112 annual-report candidates.

Numeric surfaces are candidates for alternative support, not semantic facts.
This audit does not prove that a blind reader cannot derive the answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_p95_report_finance_shared import _visible_amount
from scripts.p95_report_finance_shared import canonical
from scripts.p112_report_route import SCHEMA, _rows
from scripts.run_p95_report_finance_shared import SEPARATOR, sha

AUDIT_SCHEMA = "longworld.p113-report-reader-text-audit.v1"
HEADINGS = re.compile(r"(?m)^=== Annual filing: (.*?) ===\nReport date:")


def numeric_spans(context: str, value: int) -> list[tuple[int, int]]:
    """Find exact Arabic numeral magnitudes, grouped or ungrouped.

    Commas are included in the boundary to avoid matching 234 inside 1,234.
    Signs, units, metric roles and prose paraphrases are intentionally ignored.
    """
    magnitude = abs(value)
    forms = {str(magnitude), f"{magnitude:,}"}
    pattern = re.compile(
        r"(?<![\d,])(?:" + "|".join(re.escape(x) for x in sorted(forms)) + r")(?![\d,])"
    )
    return [match.span() for match in pattern.finditer(context)]


def redact(context: str, spans: list[tuple[int, int]]) -> str:
    """Replace visible digits in-place so source offsets remain stable."""
    chars = list(context)
    changed = 0
    for start, end in spans:
        if start < 0 or end > len(chars) or start >= end:
            raise ValueError("invalid reader redaction span")
        for offset in range(start, end):
            if chars[offset].isdigit():
                chars[offset] = "X"
                changed += 1
    if not changed:
        raise ValueError("reader redaction did not change any digits")
    return "".join(chars)


def _report_chunks(context: str) -> list[tuple[str, int, int]]:
    headings = list(HEADINGS.finditer(context))
    if not headings:
        raise ValueError("missing annual-filing headings")
    return [
        (
            match.group(1),
            match.start(),
            headings[i + 1].start() if i + 1 < len(headings) else len(context),
        )
        for i, match in enumerate(headings)
    ]


def _report_for(position: int, chunks: list[tuple[str, int, int]]) -> str | None:
    return next(
        (record for record, start, end in chunks if start <= position < end), None
    )


def inspect_target(context: str, item: dict) -> tuple[dict, str]:
    start, end = item["context_span"]
    if context[start:end] != item["quote"]:
        raise ValueError("proof quote is absent from reader context")
    value = _visible_amount(context, start, end)
    occurrences = numeric_spans(context, value)
    proof_occurrences = [x for x in occurrences if start <= x[0] and x[1] <= end]
    if len(proof_occurrences) != 1:
        raise ValueError("proof target must contain exactly one numeral occurrence")
    chunks = _report_chunks(context)
    proof_report = _report_for(start, chunks)
    if proof_report != item["record_id"]:
        raise ValueError("proof target report heading mismatch")
    other = [x for x in occurrences if x != proof_occurrences[0]]
    proof_only = redact(context, [proof_occurrences[0]])
    residual = numeric_spans(proof_only, value)
    if residual != other:
        raise ValueError("proof-only redaction changed unexpected numeric surfaces")
    context_minus = redact(context, occurrences)
    if numeric_spans(context_minus, value):
        raise ValueError("all-surface reader redaction left target numeral visible")
    report = {
        "record_id": item["record_id"],
        "role": item["role"],
        "proof_context_span": [start, end],
        "numeric_surface_occurrences": len(occurrences),
        "surviving_after_proof_only": len(residual),
        "same_report_numeric_candidates": sum(
            _report_for(position, chunks) == proof_report for position, _ in other
        ),
        "other_report_numeric_candidates": sum(
            _report_for(position, chunks) not in {None, proof_report}
            for position, _ in other
        ),
        "unscoped_numeric_candidates": sum(
            _report_for(position, chunks) is None for position, _ in other
        ),
        "redacted_surface_spans": [list(x) for x in occurrences],
        "reader_context_minus_sha256": hashlib.sha256(
            context_minus.encode()
        ).hexdigest(),
        "bounded_target_numeral_absent": True,
    }
    return report, context_minus


def audit(native: Path, output: Path, *, verify_only: bool = False) -> dict:
    batch_path = native / "batch_manifest.json"
    batch = json.loads(batch_path.read_text())
    if batch["schema"] != SCHEMA + ".batch" or batch["train_ready"] is not False:
        raise ValueError("unexpected P112 report batch")
    if not verify_only and output.exists():
        raise ValueError("P113 audit output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p113_report_text_", dir=output.parent
    ) as temp:
        stage = Path(temp)
        counters = Counter()
        sample_ids = set()
        semantic_ids = set()
        tasks_with_numeric_alternatives = set()
        with (
            (stage / "audit_index.jsonl").open("x", encoding="utf-8") as audit_stream,
            (stage / "reader_context_minus.jsonl").open(
                "x", encoding="utf-8"
            ) as reader_stream,
        ):
            for job in batch["jobs"]:
                folder = native / job["issuer"]
                manifest_path = folder / "manifest.json"
                if sha(manifest_path) != job["manifest_sha256"]:
                    raise ValueError("P112 issuer manifest drift")
                manifest = json.loads(manifest_path.read_text())
                if manifest["schema"] != SCHEMA + ".issuer":
                    raise ValueError("P112 issuer schema drift")
                for name, expected in manifest["file_sha256"].items():
                    if sha(folder / name) != expected:
                        raise ValueError("P112 native file hash drift")
                rows = zip(
                    _rows(folder / "reader.jsonl"),
                    _rows(folder / "sample_index.jsonl"),
                    _rows(folder / "audit.jsonl"),
                    strict=True,
                )
                for reader, index, proof in rows:
                    sample = reader["sample_id"]
                    if (
                        sample in sample_ids
                        or sample != index["sample_id"]
                        or sample != proof["sample_id"]
                        or proof["mask_checked"] is not True
                    ):
                        raise ValueError("P112 reader/index/proof mismatch")
                    sample_ids.add(sample)
                    semantic_ids.add(index["semantic_task_id"])
                    messages = reader["messages"]
                    if [row["role"] for row in messages] != ["user", "assistant"]:
                        raise ValueError("unexpected P112 reader messages")
                    user = messages[0]["content"]
                    if user.count(SEPARATOR) != 1:
                        raise ValueError("ambiguous P112 reader question boundary")
                    context, question = user.split(SEPARATOR)
                    if (
                        not question
                        or hashlib.sha256(context.encode()).hexdigest()
                        != index["context_sha256"]
                    ):
                        raise ValueError("P112 reader context hash drift")
                    target_role = proof["program"]["target_metric"]
                    targets = [x for x in proof["evidence"] if x["role"] == target_role]
                    if (
                        len(targets) != 2
                        or targets[0]["record_id"] == targets[1]["record_id"]
                    ):
                        raise ValueError(
                            "P112 task lacks two distinct target observations"
                        )
                    case = {
                        "sample_id": sample,
                        "semantic_task_id": index["semantic_task_id"],
                        "targets": [],
                    }
                    for target in targets:
                        finding, context_minus = inspect_target(context, target)
                        case["targets"].append(finding)
                        reader_stream.write(
                            canonical(
                                {
                                    "sample_id": sample,
                                    "removed_record_id": target["record_id"],
                                    "messages": [
                                        {
                                            "role": "user",
                                            "content": context_minus
                                            + SEPARATOR
                                            + question,
                                        }
                                    ],
                                }
                            )
                            + "\n"
                        )
                        counters["target_observations"] += 1
                        counters["numeric_occurrences"] += finding[
                            "numeric_surface_occurrences"
                        ]
                        counters["alternate_numeric_candidates"] += finding[
                            "surviving_after_proof_only"
                        ]
                        if finding["surviving_after_proof_only"]:
                            counters["targets_with_numeric_alternatives"] += 1
                            tasks_with_numeric_alternatives.add(
                                index["semantic_task_id"]
                            )
                        if finding["same_report_numeric_candidates"]:
                            counters["targets_with_same_report_numeric_candidates"] += 1
                        if finding["other_report_numeric_candidates"]:
                            counters[
                                "targets_with_other_report_numeric_candidates"
                            ] += 1
                    audit_stream.write(canonical(case) + "\n")
                    counters["views"] += 1
        if counters["views"] != batch["views"] or len(sample_ids) != batch["views"]:
            raise ValueError("P112 batch reader count drift")
        if len(semantic_ids) != batch["semantic_tasks"]:
            raise ValueError("P112 batch semantic task count drift")
        counters["semantic_tasks"] = len(semantic_ids)
        counters["tasks_with_numeric_alternatives"] = len(
            tasks_with_numeric_alternatives
        )
        manifest = {
            "schema": AUDIT_SCHEMA,
            "native_batch_manifest_sha256": sha(batch_path),
            "counts": dict(sorted(counters.items())),
            "files_sha256": {
                name: sha(stage / name)
                for name in ("audit_index.jsonl", "reader_context_minus.jsonl")
            },
            "scope": (
                "actual reader-context redaction of exact Arabic numeral surfaces for each "
                "target magnitude; repeated numeric occurrences are candidates, not verified "
                "equivalent semantic supports; no gold-blind reader or exhaustive proof search"
            ),
            "train_ready": False,
        }
        (stage / "manifest.json").write_text(
            canonical(manifest) + "\n", encoding="utf-8"
        )
        if verify_only:
            for name in (*manifest["files_sha256"], "manifest.json"):
                if (
                    not (output / name).is_file()
                    or (stage / name).read_bytes() != (output / name).read_bytes()
                ):
                    raise ValueError(f"P113 reader-text audit replay mismatch: {name}")
        else:
            stage.rename(output)
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(canonical(audit(args.native, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
