"""Independently recheck final P95 reader bytes, source pins and assistant masks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import physical_length_bin
from scripts.p95_report_finance_shared import canonical
from scripts.run_p95_report_finance_shared import (
    CODE_FILES,
    SCHEMA,
    SEPARATOR,
    _token_span,
    sha,
)
from scripts.train_sft import _render_chat, tokenize_assistant_only

AUDIT_SCHEMA = SCHEMA + ".final-audit"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _visible_amount(context: str, start: int, end: int) -> int:
    """Parse an observed table cell without using the hidden normalized fact."""
    raw = context[start:end].strip().replace("$", "").replace(",", "").replace(" ", "")
    parenthesized = raw.startswith("(") and raw.endswith(")")
    if parenthesized:
        raw = raw[1:-1]
    signed = raw.startswith(("-", "−"))
    if signed:
        raw = raw[1:]
    if not raw.isdigit():
        raise ValueError("reader evidence is not a plain numeric cell")
    before = re.split(r"[\t\r\n]", context[max(0, start - 24) : start])[-1]
    after = re.split(r"[\t\r\n]", context[end : end + 24])[0]
    negative = (
        parenthesized
        or signed
        or bool(re.search(r"[−-]\s*\$?\s*$", before))
        or bool(re.search(r"\(\s*\$?\s*$", before) and re.match(r"^\s*\)", after))
    )
    return -int(raw) if negative else int(raw)


def _visible_year(context: str, record_id: str) -> str:
    heading = re.escape(f"=== Annual filing: {record_id} ===\nReport date: ")
    years = set(re.findall(heading + r"(\d{4})-\d{2}-\d{2}", context))
    if len(years) != 1:
        raise ValueError("report year is not uniquely visible")
    return years.pop()


def replay_answer(context: str, evidence: list[dict], operation: str) -> dict:
    """Execute the operation from final visible cell quotes and report headers."""
    values = {}
    years = {}
    for item in evidence:
        record_id, role = item["record_id"], item["role"]
        key = record_id, role
        if key in values:
            raise ValueError("duplicate evidence role in report")
        values[key] = _visible_amount(context, *item["context_span"])
        years[record_id] = _visible_year(context, record_id)
    records = sorted(years, key=lambda identity: (years[identity], identity))
    if len(records) < 3 or len(set(years.values())) != len(records):
        raise ValueError("visible annual report sequence is ambiguous")

    def series(role: str) -> list[int]:
        try:
            return [values[identity, role] for identity in records]
        except KeyError as error:
            raise ValueError("visible selector metric is absent") from error

    def selected_value(index: int, role: str) -> int:
        try:
            return values[records[index], role]
        except KeyError as error:
            raise ValueError("selected target not in bounded evidence") from error

    if operation in {"middle_margin_then_cash", "largest_margin_swing_then_cash"}:
        revenue, income = series("revenue"), series("operating_income")
        if any(value <= 0 for value in revenue):
            raise ValueError("visible revenue denominator invalid")
        ratios = [Fraction(op, rev) for op, rev in zip(income, revenue, strict=True)]
        if operation == "middle_margin_then_cash":
            if len(set(ratios)) != len(ratios):
                raise ValueError("visible operating margins tied")
            selected = sorted(range(len(records)), key=lambda i: ratios[i])[
                (len(records) - 1) // 2
            ]
            return {
                "fiscal_year": years[records[selected]],
                "operating_cash_flow_usd_millions": selected_value(
                    selected, "cash_from_operations"
                ),
            }
        scores = [abs(ratios[i] - ratios[i - 1]) for i in range(1, len(ratios))]
        if scores.count(max(scores)) != 1:
            raise ValueError("visible margin swing tied")
        later = scores.index(max(scores)) + 1
        return {
            "transition": f"{years[records[later - 1]]}–{years[records[later]]}",
            "later_year_operating_cash_flow_usd_millions": selected_value(
                later, "cash_from_operations"
            ),
        }
    if operation == "largest_cash_jump_then_revenue":
        cash = series("cash_from_operations")
        scores = [abs(cash[i] - cash[i - 1]) for i in range(1, len(cash))]
        if scores.count(max(scores)) != 1:
            raise ValueError("visible cash jump tied")
        later = scores.index(max(scores)) + 1
        return {
            "transition": f"{years[records[later - 1]]}–{years[records[later]]}",
            "later_year_revenue_usd_millions": selected_value(later, "revenue"),
        }
    if operation == "max_revenue_growth_then_cash":
        revenues = series("revenue")
        if any(value <= 0 for value in revenues):
            raise ValueError("visible revenue denominator invalid")
        scores = [
            Fraction(revenues[i] - revenues[i - 1], revenues[i - 1])
            for i in range(1, len(revenues))
        ]
        if scores.count(max(scores)) != 1:
            raise ValueError("visible revenue growth tied")
        later = scores.index(max(scores)) + 1
        return {
            "transition": f"{years[records[later - 1]]}–{years[records[later]]}",
            "later_year_operating_cash_flow_usd_millions": selected_value(
                later, "cash_from_operations"
            ),
        }
    raise ValueError("unsupported visible replay operation")


def audit(
    catalog_path: Path, batch_dir: Path, output: Path, *, verify_only: bool = False
) -> dict:
    catalog = json.loads(catalog_path.read_text())
    batch = json.loads((batch_dir / "batch_manifest.json").read_text())
    if (
        catalog.get("schema_version") != "longworld.finance-taskbank-long-catalog.v1"
        or batch.get("schema") != SCHEMA + ".batch"
        or batch["catalog_sha256"] != sha(catalog_path)
        or batch["verified_sources"] != len(catalog["jobs"])
    ):
        raise ValueError("catalog or batch receipt changed")
    receipts = {row["issuer"]: row for row in batch["jobs"]}
    if len(receipts) != len(batch["jobs"]):
        raise ValueError("duplicate batch issuer")
    tokenizer = get_tokenizer()
    all_ids = set()
    source_groups = set()
    masks, lengths, operations = Counter(), Counter(), Counter()
    extents, distances = [], []
    per_source = []
    for job in catalog["jobs"]:
        issuer = job["issuer"]
        receipt = receipts[issuer]
        native = batch_dir / issuer
        manifest_path = native / "manifest.json"
        if (
            receipt["status"] != "verified_candidate"
            or sha(manifest_path) != receipt["manifest_sha256"]
        ):
            raise ValueError(f"issuer receipt changed: {issuer}")
        manifest = json.loads(manifest_path.read_text())
        if (
            manifest.get("schema") != SCHEMA
            or manifest.get("train_ready") is not False
            or manifest["source_manifest"]
            != {"path": job["source_manifest"], "sha256": job["source_manifest_sha256"]}
            or manifest["base_config"]["sha256"] != job["config_sha256"]
            or sha(ROOT / job["source_manifest"]) != job["source_manifest_sha256"]
            or sha(ROOT / job["config"]) != job["config_sha256"]
            or manifest["code_sha256"]
            != {name: sha(ROOT / name) for name in CODE_FILES}
            or manifest["source_group"] in source_groups
        ):
            raise ValueError(f"issuer source or code binding changed: {issuer}")
        source_groups.add(manifest["source_group"])
        for name, expected in manifest["file_sha256"].items():
            if sha(native / name) != expected:
                raise ValueError(f"native file changed: {issuer}/{name}")
        readers = _rows(native / "reader.jsonl")
        indices = _rows(native / "sample_index.jsonl")
        audits = _rows(native / "audit.jsonl")
        if not len(readers) == len(indices) == len(audits) == manifest["views"]:
            raise ValueError(f"issuer row count changed: {issuer}")
        semantic_views: dict[str, set[str]] = defaultdict(set)
        semantic_answers: dict[str, set[str]] = defaultdict(set)
        for reader, index, proof in zip(readers, indices, audits, strict=True):
            sample = index["sample_id"]
            if (
                reader["sample_id"] != sample
                or proof["sample_id"] != sample
                or sample in all_ids
                or index["split"] != manifest["split"]
                or index["source_group"] != manifest["source_group"]
                or index["source_kind"] != "real_finance"
                or proof["operation"] != index["operation"]
                or proof["mask_checked"] is not True
            ):
                raise ValueError("final reader/index/audit identity mismatch")
            all_ids.add(sample)
            messages = reader["messages"]
            if [item["role"] for item in messages] != ["user", "assistant"]:
                raise ValueError("reader message roles changed")
            user = messages[0]["content"]
            if SEPARATOR not in user:
                raise ValueError("reader question boundary missing")
            context, question = user.rsplit(SEPARATOR, 1)
            if not context or not question:
                raise ValueError("reader context or question missing")
            if hashlib.sha256(context.encode()).hexdigest() != index["context_sha256"]:
                raise ValueError("final reader context hash changed")
            answer = messages[1]["content"]
            if hashlib.sha256(answer.encode()).hexdigest() != index["answer_sha256"]:
                raise ValueError("final reader answer hash changed")
            encoded = tokenize_assistant_only(tokenizer, messages, 262144)
            labels = encoded["labels"]
            input_tokens = sum(value == -100 for value in labels)
            supervised = len(labels) - input_tokens
            if (
                index["input_tokens"] != input_tokens
                or index["supervised_tokens"] != supervised
                or index["full_chat_tokens"] != len(labels)
                or index["length_bin"] != physical_length_bin(len(labels))
                or supervised < 1
                or any(value != -100 for value in labels[:input_tokens])
            ):
                raise ValueError("final assistant-only mask differs")
            prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
            user_start = prompt.find(user)
            if user_start < 0 or prompt.count(user) != 1:
                raise ValueError("reader user text not unique in template")
            offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
                "offset_mapping"
            ]
            query = _token_span(
                offsets,
                user_start + len(context) + len(SEPARATOR),
                user_start + len(user),
            )[0]
            if query != proof["query_token_start"]:
                raise ValueError("question token boundary changed")
            spans = []
            for item in proof["evidence"]:
                start, end = item["context_span"]
                if context[start:end] != item["quote"] or not item["quote"]:
                    raise ValueError("reader evidence quote changed")
                span = _token_span(offsets, user_start + start, user_start + end)
                if span != item["prompt_token_span"] or span[1] > input_tokens:
                    raise ValueError("reader evidence token span changed")
                spans.append(span)
            if replay_answer(
                context, proof["evidence"], index["operation"]
            ) != json.loads(answer):
                raise ValueError(
                    "visible evidence replay disagrees with assistant answer"
                )
            extent = max(end for _, end in spans) - min(start for start, _ in spans)
            distance = query - max(end for _, end in spans)
            if (
                extent != proof["evidence_extent_tokens"]
                or distance != proof["last_evidence_to_query_tokens"]
            ):
                raise ValueError("evidence distance changed")
            extents.append(extent)
            distances.append(distance)
            masks["views"] += 1
            masks["visible_answer_replays"] += 1
            masks["input_tokens"] += input_tokens
            masks["supervised_tokens"] += supervised
            lengths[index["length_bin"]] += 1
            operations[index["operation"]] += 1
            semantic_views[index["semantic_task_id"]].add(index["variant"])
            semantic_answers[index["semantic_task_id"]].add(answer)
        if (
            len(semantic_views) != manifest["semantic_tasks"]
            or any(
                views != {"complete_statements", "analyst_packet"}
                for views in semantic_views.values()
            )
            or any(len(answers) != 1 for answers in semantic_answers.values())
        ):
            raise ValueError(f"issuer semantic task views diverged: {issuer}")
        per_source.append(
            {
                "issuer": issuer,
                "source_group": manifest["source_group"],
                "split": manifest["split"],
                "tasks": len(semantic_views),
                "views": len(readers),
            }
        )
    if (
        masks["views"] != batch["views"]
        or sum(row["tasks"] for row in per_source) != batch["semantic_tasks"]
    ):
        raise ValueError("batch totals differ from reader rows")
    result = {
        "schema": AUDIT_SCHEMA,
        "catalog_sha256": sha(catalog_path),
        "batch_manifest_sha256": sha(batch_dir / "batch_manifest.json"),
        "sources": per_source,
        "source_groups": len(source_groups),
        "semantic_tasks": batch["semantic_tasks"],
        "mask": dict(masks),
        "length_bins": dict(sorted(lengths.items())),
        "operations": dict(sorted(operations.items())),
        "evidence_extent_tokens": {"min": min(extents), "max": max(extents)},
        "last_evidence_to_query_tokens": {"min": min(distances), "max": max(distances)},
        "scope": "final reader bytes, source pins and visible-cell answer replay; alternative visible proofs not exhaustively searched",
        "train_ready": False,
    }
    payload = canonical(result) + "\n"
    if verify_only:
        if not output.is_file() or output.read_text() != payload:
            raise ValueError("P95 final audit drift")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        canonical(
            audit(
                args.catalog, args.batch_dir, args.output, verify_only=args.verify_only
            )
        )
    )


if __name__ == "__main__":
    main()
