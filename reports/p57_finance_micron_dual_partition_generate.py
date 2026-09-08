"""Compile Micron dual-partition identity from the signed FY2022–FY2025 graph.

This is an extra semantic task, not a clone of asset-trajectory and not an
NVIDIA market_* program. Adapter/promotion wiring is intentionally out of
scope: the compiler emits a task JSON and, when asked, one packed parent at
the natural primary length.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.financehistory import _ROW, _context, _row_record
from longworld.core.financehistory import FinancialFact, FinancialSourceRow
from longworld.core.issuerfilingworkflow import (
    MICRON_CIK,
    MICRON_GEO_SECTIONS,
    MICRON_TECHNOLOGY_SECTIONS,
    parse_issuer_ir_rendered_metrics,
)
from longworld.core.provenance import ProvenanceError

ANSWER_PROGRAM_ID = "micron.dual_partition_identity.v1"
SCHEMA_VERSION = "longworld.micron-dual-partition-identity.v1"
WORLD_ID = "finance_micron_dual_partition_identity_fy2022_2025_v1"
TECH_ROLES = ("category_dram", "category_nand", "category_other")
OPS_SECTION = "CONSOLIDATED STATEMENTS OF OPERATIONS"
PRIMARY_BAND = "32k"
PRIMARY_BAND_BOUNDS = (32000, 32768)
_NUMBER = re.compile(r"^\(?\$?[ \t]*[0-9][0-9,]*\)?$")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _parse_number(value: str) -> int:
    if _NUMBER.fullmatch(value) is None:
        raise ProvenanceError("Micron mix quote is not a USD-millions number")
    return int(re.sub(r"[^0-9]", "", value))


def _replacement_quote(value: str) -> str:
    numeric = _parse_number(value)
    replacement = f"{numeric + 1:,}"
    if len(replacement) != len(value):
        replacement = value[:-1] + str((int(value[-1]) + 1) % 10)
    if len(replacement) != len(value) or replacement == value:
        raise ProvenanceError("cannot construct Micron mix counterfactual")
    return replacement


def _is_mix_role(role: str) -> bool:
    return role == "revenue" or role.startswith("category_") or role.startswith("geo_")


def _mix_sections() -> frozenset[str]:
    return frozenset((OPS_SECTION, *MICRON_TECHNOLOGY_SECTIONS, *MICRON_GEO_SECTIONS))


def compile_micron_dual_partition_task(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Emit one dual-partition identity task from signed issuer-IR records."""
    if len(records) != 4:
        raise ProvenanceError("Micron dual-partition task requires four annual filings")
    ordered = sorted(records, key=lambda item: str(item.get("report_date") or ""))
    annual: list[dict[str, Any]] = []
    gold_quotes: list[dict[str, Any]] = []
    dram_cf: dict[str, Any] | None = None
    for record in ordered:
        report_date = str(record.get("report_date") or "")
        text = record.get("text")
        source_sha256 = str(record.get("source_sha256") or "")
        if not isinstance(text, str) or not text or _sha256_text(text) != source_sha256:
            raise ProvenanceError("Micron signed graph text digest mismatch")
        if str(record.get("cik") or "") != MICRON_CIK:
            raise ProvenanceError("Micron dual-partition task is CIK-bound")
        program = parse_issuer_ir_rendered_metrics(
            text,
            report_date=report_date,
            issuer_cik=MICRON_CIK,
        )
        values = {fact.role: fact.numeric_value for fact in program.facts}
        revenue = int(values["revenue"])
        technology = {
            "dram": int(values["category_dram"]),
            "nand": int(values["category_nand"]),
            "other": int(values["category_other"]),
        }
        geography = {
            role.removeprefix("geo_"): int(value)
            for role, value in sorted(values.items())
            if role.startswith("geo_")
        }
        if sum(technology.values()) != revenue:
            raise ProvenanceError("Micron technology revenue identity fails")
        if sum(geography.values()) != revenue:
            raise ProvenanceError("Micron geography revenue identity fails")
        europe_present = "europe" in geography
        if report_date < "2023-08-01" and europe_present:
            raise ProvenanceError("FY2022 Micron geography must not claim Europe")
        if report_date >= "2023-08-01" and not europe_present:
            raise ProvenanceError("post-FY2022 Micron geography must include Europe")
        annual.append(
            {
                "report_date": report_date,
                "revenue": revenue,
                "technology": technology,
                "technology_identity": True,
                "geography": geography,
                "geography_identity": True,
                "europe_present": europe_present,
            }
        )
        for fact in program.facts:
            if not _is_mix_role(fact.role):
                continue
            if text[fact.char_start : fact.char_end] != fact.evidence_quote:
                raise ProvenanceError("Micron mix gold quote does not replay")
            gold_quotes.append(
                {
                    "report_date": report_date,
                    "role": fact.role,
                    "numeric_value": fact.numeric_value,
                    "evidence_quote": fact.evidence_quote,
                    "char_start": fact.char_start,
                    "char_end": fact.char_end,
                    "quote_sha256": _sha256_text(fact.evidence_quote),
                    "source_sha256": source_sha256,
                    "source_url": str(record.get("source_url") or ""),
                    "filing_id": str(record.get("filing_id") or ""),
                    "section": fact.section,
                }
            )
            if report_date == "2025-08-28" and fact.role == "category_dram":
                dram_cf = {
                    "report_date": report_date,
                    "role": fact.role,
                    "source_origin": "synthetic_counterfactual",
                    "provenance_operation": "replace_exact_span",
                    "parent_value": fact.evidence_quote,
                    "value": _replacement_quote(fact.evidence_quote),
                    "source_sha256": source_sha256,
                    "char_start": fact.char_start,
                    "char_end": fact.char_end,
                }
    if dram_cf is None:
        raise ProvenanceError("Micron dual-partition counterfactual has no DRAM span")
    answer = {
        "annual_partitions": annual,
        "dual_partition_identity": True,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "data_stage": "compiled_task",
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "disabled",
        "world_id": WORLD_ID,
        "domain": "finance",
        "cik": MICRON_CIK,
        "issuer_name": "Micron Technology, Inc.",
        "query_type": "micron_dual_partition_identity",
        "answer_program_id": ANSWER_PROGRAM_ID,
        "answer_program_operations": [
            "source_span_parse",
            "technology_revenue_identity",
            "geography_revenue_identity",
            "europe_presence",
        ],
        "question": (
            "Using the exact annual technology and customer-headquarters geography "
            "rows, certify that DRAM+NAND+Other and the geography partition each "
            "equal stated revenue for every FY2022–FY2025 filing, and report "
            "whether Europe is present in the geography table."
        ),
        "gold_quotes": gold_quotes,
        "answer": _canonical_json(answer),
        "cf_answer": "unknown",
        "counterfactual_twin": dram_cf,
        "selected_filing_count": 4,
        "source_family": "issuer_owned_ir_rendered_xbrl",
        "length_bucket": None,
    }


def replay_micron_dual_partition_task(
    task: dict[str, Any],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Recompute dual-partition gold from bound quotes; CF identity failure is unknown."""
    if task.get("answer_program_id") != ANSWER_PROGRAM_ID:
        return {"answer": "unknown"}
    quotes = deepcopy(task.get("gold_quotes") or [])
    if counterfactual:
        twin = task.get("counterfactual_twin") or {}
        matched = False
        for item in quotes:
            if (
                item.get("report_date") == twin.get("report_date")
                and item.get("role") == twin.get("role")
            ):
                item["evidence_quote"] = twin["value"]
                item["numeric_value"] = _parse_number(str(twin["value"]))
                matched = True
        if not matched:
            return {"answer": "unknown"}
    by_year: dict[str, dict[str, int]] = {}
    for item in quotes:
        if not _is_mix_role(str(item.get("role") or "")):
            continue
        by_year.setdefault(str(item["report_date"]), {})[str(item["role"])] = int(
            item["numeric_value"]
        )
    annual: list[dict[str, Any]] = []
    for report_date, values in sorted(by_year.items()):
        try:
            revenue = int(values["revenue"])
            technology = {
                "dram": int(values["category_dram"]),
                "nand": int(values["category_nand"]),
                "other": int(values["category_other"]),
            }
            geography = {
                role.removeprefix("geo_"): int(value)
                for role, value in sorted(values.items())
                if role.startswith("geo_")
            }
        except KeyError:
            return {"answer": "unknown"}
        tech_ok = sum(technology.values()) == revenue
        geo_ok = bool(geography) and sum(geography.values()) == revenue
        europe_present = "europe" in geography
        if report_date < "2023-08-01" and europe_present:
            return {"answer": "unknown"}
        if report_date >= "2023-08-01" and not europe_present:
            return {"answer": "unknown"}
        if not tech_ok or not geo_ok:
            return {"answer": "unknown"}
        annual.append(
            {
                "report_date": report_date,
                "revenue": revenue,
                "technology": technology,
                "technology_identity": True,
                "geography": geography,
                "geography_identity": True,
                "europe_present": europe_present,
            }
        )
    if len(annual) != 4:
        return {"answer": "unknown"}
    return {
        "answer": _canonical_json(
            {"annual_partitions": annual, "dual_partition_identity": True}
        )
    }


def _mix_source_rows(records: list[dict[str, Any]]) -> list[FinancialSourceRow]:
    rows: list[FinancialSourceRow] = []
    allowed = _mix_sections()
    seen: set[str] = set()
    for record in sorted(records, key=lambda item: str(item.get("report_date") or "")):
        text = str(record["text"])
        report_date = str(record["report_date"])
        program = parse_issuer_ir_rendered_metrics(
            text,
            report_date=report_date,
            issuer_cik=MICRON_CIK,
        )
        facts_by_span = sorted(program.facts, key=lambda fact: fact.char_start)
        filing_id = str(record["record_id"])
        for section, start, end in sorted(program.section_ranges, key=lambda item: item[1]):
            if section not in allowed:
                continue
            for row_index, match in enumerate(_ROW.finditer(text, start, end)):
                raw = match.group()
                digest = _sha256_text(raw)
                if digest in seen:
                    continue
                seen.add(digest)
                row_start, row_end = match.span()
                row_facts = tuple(
                    FinancialFact(
                        role=fact.role,
                        evidence_quote=fact.evidence_quote,
                        relative_start=fact.char_start - row_start,
                    )
                    for fact in facts_by_span
                    if _is_mix_role(fact.role)
                    and row_start <= fact.char_start < fact.char_end <= row_end
                )
                rows.append(
                    FinancialSourceRow(
                        record_id=f"{filing_id}:mix:{hashlib.sha256(section.encode()).hexdigest()[:12]}:row:{row_index}",
                        filing_record_id=filing_id,
                        report_date=report_date,
                        source_url=str(record["source_url"]),
                        source_sha256=str(record["source_sha256"]),
                        section=section,
                        source_char_start=row_start,
                        source_char_end=row_end,
                        source_text=raw,
                        facts=row_facts,
                    )
                )
    return rows


def pack_micron_dual_partition_parent(
    task: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    token_counter,
    lower_tokens: int = PRIMARY_BAND_BOUNDS[0],
    upper_tokens: int = PRIMARY_BAND_BOUNDS[1],
) -> dict[str, Any]:
    """Pack mix/ops/geo rows only. Do not pad with unrelated statements or 128k fill."""
    rows = sorted(
        _mix_source_rows(records),
        key=lambda row: (row.report_date, row.source_char_start, row.record_id),
    )
    essentials = [row for row in rows if row.facts]
    leftover = [row for row in rows if not row.facts]
    if not essentials:
        raise ProvenanceError("Micron dual-partition pack has no mix facts")

    def _tokens_for(chosen: list[FinancialSourceRow]) -> tuple[str, int]:
        ordered = sorted(
            chosen,
            key=lambda row: (row.report_date, row.source_char_start, row.record_id),
        )
        context = _context([_row_record(row) for row in ordered])
        return context, token_counter(context)

    essential_context, essential_tokens = _tokens_for(essentials)
    if essential_tokens > upper_tokens:
        raise ProvenanceError(
            f"Micron dual-partition essentials overflow {upper_tokens}: {essential_tokens}"
        )
    lo = 0
    hi = len(leftover)
    best_n = 0
    best_context = essential_context
    best_tokens = essential_tokens
    best_rows = list(essentials)
    while lo <= hi:
        mid = (lo + hi) // 2
        chosen = essentials + leftover[:mid]
        context, tokens = _tokens_for(chosen)
        if tokens <= upper_tokens:
            best_n = mid
            best_context = context
            best_tokens = tokens
            best_rows = sorted(
                chosen,
                key=lambda row: (row.report_date, row.source_char_start, row.record_id),
            )
            lo = mid + 1
        else:
            hi = mid - 1
    packed = [_row_record(row) for row in best_rows]
    context = best_context
    tokens = best_tokens
    if tokens > upper_tokens:
        raise ProvenanceError(
            f"Micron dual-partition pack overflow {upper_tokens}: {tokens}"
        )
    if tokens < lower_tokens:
        raise ProvenanceError(
            f"Micron dual-partition natural length {tokens} is below {lower_tokens}; "
            "refusing unrelated padding"
        )
    parent = deepcopy(task)
    parent["length_bucket"] = PRIMARY_BAND
    parent["band_lower_tokens"] = lower_tokens
    parent["band_upper_tokens"] = upper_tokens
    parent["context"] = context
    parent["context_sha256"] = _sha256_text(context)
    parent["tokenizer_context_tokens"] = tokens
    parent["actual_context_tokens"] = tokens
    parent["source_record_count"] = len(packed)
    parent["essential_evidence_count"] = len(essentials)
    parent["production_eligible"] = False
    parent["train_ready"] = False
    return parent


def load_signed_graph_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("issuer", {}).get("cik") != MICRON_CIK:
        raise ProvenanceError("signed Micron graph CIK mismatch")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != 4:
        raise ProvenanceError("signed Micron graph does not have four filings")
    return sorted(records, key=lambda item: str(item.get("report_date") or ""))


def build(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != "longworld.micron-dual-partition-generation.v1":
        raise ValueError("unsupported Micron dual-partition generation config")
    if config.get("answer_program_id") != ANSWER_PROGRAM_ID:
        raise ValueError("generation config answer program mismatch")
    if list(config.get("length_buckets") or {}) != [PRIMARY_BAND]:
        raise ValueError("Micron dual-partition packing is one primary length")
    records = load_signed_graph_records(Path(config["signed_issuer_manifest"]))
    task = compile_micron_dual_partition_task(records)
    replayed = replay_micron_dual_partition_task(task)
    if replayed["answer"] != task["answer"]:
        raise ProvenanceError("compiled Micron dual-partition gold does not replay")
    cf = replay_micron_dual_partition_task(task, counterfactual=True)
    if cf["answer"] != "unknown":
        raise ProvenanceError("Micron dual-partition counterfactual is not executable")
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    task_path = output_dir / "task.json"
    task_bytes = (_canonical_json(task) + "\n").encode()
    task_path.write_bytes(task_bytes)
    receipt: dict[str, Any] = {
        "schema_version": "longworld.micron-dual-partition-generation-receipt.v1",
        "answer_program_id": ANSWER_PROGRAM_ID,
        "world_id": WORLD_ID,
        "task_sha256": hashlib.sha256(task_bytes).hexdigest(),
        "gold_quote_count": len(task["gold_quotes"]),
        "production_eligible": False,
        "train_ready": False,
        "packed_parent": False,
        "adapter_wired": False,
    }
    pack = config.get("packing") or {}
    if pack.get("pack_parent"):
        tokenizer_config = config["tokenizer"]
        from longworld.core.attestation import sanitized_attestation_environment
        from transformers import AutoTokenizer

        with sanitized_attestation_environment():
            tokenizer = AutoTokenizer.from_pretrained(
                tokenizer_config["model_id"],
                revision=tokenizer_config["revision"],
                trust_remote_code=False,
                local_files_only=True,
            )

        def token_counter(text: str) -> int:
            with sanitized_attestation_environment():
                return len(tokenizer.encode(text, add_special_tokens=False))

        parent = pack_micron_dual_partition_parent(
            task,
            records,
            token_counter=token_counter,
            lower_tokens=int(config["length_buckets"][PRIMARY_BAND][0]),
            upper_tokens=int(config["length_buckets"][PRIMARY_BAND][1]),
        )
        parent_path = output_dir / "parents.jsonl"
        parent_bytes = (_canonical_json(parent) + "\n").encode()
        parent_path.write_bytes(parent_bytes)
        receipt.update(
            {
                "packed_parent": True,
                "parent_tokens": parent["actual_context_tokens"],
                "parent_sha256": hashlib.sha256(parent_bytes).hexdigest(),
                "source_record_count": parent["source_record_count"],
                "essential_evidence_count": parent["essential_evidence_count"],
                "length_bucket": PRIMARY_BAND,
            }
        )
    (output_dir / "GENERATION_RECEIPT.json").write_text(
        _canonical_json(receipt) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    receipt = build(args.config)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
