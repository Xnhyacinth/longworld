"""Micron dual-partition identity is an independent extra task, not asset-trajectory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from longworld.core.issuerfilingworkflow import MICRON_CIK, parse_issuer_ir_rendered_metrics
from reports.p57_finance_micron_dual_partition_generate import (
    ANSWER_PROGRAM_ID,
    compile_micron_dual_partition_task,
    replay_micron_dual_partition_task,
)

SIGNED_GRAPH = Path(
    "/workspace/wynckeliao/longworld/data/source_inventory/"
    "p57_finance_micron_ir_fy2022_2025_v1/issuer_ir_manifest.signed.json"
)

FY2025_REVENUE_QUOTE_SHA256 = (
    "8c89115684c6e41ad387beff1b7e45abff407a1385ac4e52dd39bdf3654ba79f"
)
FY2025_DRAM_QUOTE_SHA256 = (
    "c17735901dfa937dfa53ccd27992d53ec1fd558979843eea60a3c95513ad4a4b"
)
FY2025_NAND_QUOTE_SHA256 = (
    "a4c6b36b0b2006891b3405e788d5637b2563d5cf1b3c335259e6e98beba5fab7"
)
FY2025_OTHER_QUOTE_SHA256 = (
    "c59eccd2ae254026623689f9aea4dc237869b1d4b9c77186289273853fd07e40"
)
FY2025_GEO_EUROPE_QUOTE_SHA256 = (
    "ad723f42c7aba316d944f19f340ce47d8e0c6fb354d212736ec4782314a6824a"
)
FY2022_REVENUE_QUOTE_SHA256 = (
    "62788ab98b17f25c7ae1e44e2afe6002b6500865764634cfa9fa37ee6f92d0d1"
)


def _graph_records() -> list[dict]:
    payload = json.loads(SIGNED_GRAPH.read_text(encoding="utf-8"))
    assert payload["issuer"]["cik"] == MICRON_CIK
    return sorted(payload["records"], key=lambda item: item["report_date"])


def _task():
    return compile_micron_dual_partition_task(_graph_records())


def test_program_is_independent_of_asset_trajectory():
    task = _task()
    assert task["answer_program_id"] == ANSWER_PROGRAM_ID
    assert task["answer_program_id"] == "micron.dual_partition_identity.v1"
    assert task["answer_program_id"] != "finance.multi_filing_asset_trajectory.v1"
    assert "market_" not in json.dumps(task)
    gold = json.loads(task["answer"])
    blob = json.dumps(gold)
    assert "assets" not in blob
    assert "cash_from_operations" not in blob
    assert "cross_filing_asset_trajectory" not in blob
    assert "annual_observations" not in blob


def test_gold_quotes_replay_signed_10k_spans():
    records = _graph_records()
    task = compile_micron_dual_partition_task(records)
    quotes = {(item["report_date"], item["role"]): item for item in task["gold_quotes"]}
    mix_quotes = [
        item
        for item in task["gold_quotes"]
        if item["role"] in {"revenue", "category_dram", "category_nand", "category_other"}
        or item["role"].startswith("geo_")
    ]
    assert len(mix_quotes) == 47
    by_year = {record["report_date"]: record for record in records}
    for item in mix_quotes:
        text = by_year[item["report_date"]]["text"]
        start = item["char_start"]
        end = item["char_end"]
        assert text[start:end] == item["evidence_quote"]
        assert hashlib.sha256(item["evidence_quote"].encode()).hexdigest() == item[
            "quote_sha256"
        ]
        assert item["source_sha256"] == by_year[item["report_date"]]["source_sha256"]
    fy2025 = quotes[("2025-08-28", "revenue")]
    assert fy2025["evidence_quote"] == "$ 37,378"
    assert fy2025["quote_sha256"] == FY2025_REVENUE_QUOTE_SHA256
    assert quotes[("2025-08-28", "category_dram")]["quote_sha256"] == FY2025_DRAM_QUOTE_SHA256
    assert quotes[("2025-08-28", "category_nand")]["quote_sha256"] == FY2025_NAND_QUOTE_SHA256
    assert quotes[("2025-08-28", "category_other")]["quote_sha256"] == FY2025_OTHER_QUOTE_SHA256
    assert quotes[("2025-08-28", "geo_europe")]["quote_sha256"] == FY2025_GEO_EUROPE_QUOTE_SHA256
    assert quotes[("2022-09-01", "revenue")]["quote_sha256"] == FY2022_REVENUE_QUOTE_SHA256
    assert ("2022-09-01", "geo_europe") not in quotes


def test_both_partitions_sum_to_stated_revenue_each_fy():
    task = _task()
    gold = json.loads(task["answer"])
    years = {item["report_date"]: item for item in gold["annual_partitions"]}
    assert set(years) == {"2022-09-01", "2023-08-31", "2024-08-29", "2025-08-28"}
    expected = {
        "2022-09-01": (30758, 22386, 7811, 561, False),
        "2023-08-31": (15540, 10978, 4206, 356, True),
        "2024-08-29": (25111, 17603, 7227, 281, True),
        "2025-08-28": (37378, 28578, 8503, 297, True),
    }
    for report_date, (revenue, dram, nand, other, europe) in expected.items():
        row = years[report_date]
        tech = row["technology"]
        geo = row["geography"]
        assert row["revenue"] == revenue
        assert tech["dram"] + tech["nand"] + tech["other"] == revenue
        assert tech == {"dram": dram, "nand": nand, "other": other}
        assert sum(geo.values()) == revenue
        assert row["technology_identity"] is True
        assert row["geography_identity"] is True
        assert row["europe_present"] is europe
        if europe:
            assert "europe" in geo
        else:
            assert "europe" not in geo
    assert gold["dual_partition_identity"] is True
    replayed = replay_micron_dual_partition_task(task)
    assert replayed["answer"] == task["answer"]


def test_counterfactual_mix_edit_breaks_identity():
    task = _task()
    replayed = replay_micron_dual_partition_task(task, counterfactual=True)
    assert replayed["answer"] == "unknown"
    assert replayed["answer"] != task["answer"]
    twin = task["counterfactual_twin"]
    assert twin["role"] == "category_dram"
    assert twin["report_date"] == "2025-08-28"
    assert twin["parent_value"] == "28,578"
    assert twin["value"] != twin["parent_value"]
    assert task["cf_answer"] == "unknown"


def test_parser_on_signed_bytes_matches_gold_and_not_disk_html_offsets():
    records = _graph_records()
    task = compile_micron_dual_partition_task(records)
    fy2025 = next(item for item in records if item["report_date"] == "2025-08-28")
    program = parse_issuer_ir_rendered_metrics(
        fy2025["text"],
        report_date="2025-08-28",
        issuer_cik=MICRON_CIK,
    )
    revenue = next(fact for fact in program.facts if fact.role == "revenue")
    gold = next(
        item
        for item in task["gold_quotes"]
        if item["report_date"] == "2025-08-28" and item["role"] == "revenue"
    )
    assert revenue.numeric_value == 37378
    assert revenue.evidence_quote == gold["evidence_quote"] == "$ 37,378"
    assert revenue.char_start == gold["char_start"]
    disk = Path(
        "/workspace/wynckeliao/longworld/data/source_inventory/"
        "p57_finance_micron_ir_fy2022_2025_v1/micron-fy2025-annual.rendered_xbrl_html.html"
    ).read_text(encoding="utf-8")
    assert hashlib.sha256(disk.encode()).hexdigest() != fy2025["source_sha256"]
    assert revenue.char_start != 89915


def test_single_primary_length_and_probe_flags():
    task = _task()
    assert task["production_eligible"] is False
    assert task["train_ready"] is False
    assert task.get("length_buckets") in (None, ["32k"], [])
    assert task.get("length_bucket") in (None, "32k")
    assert "16k" not in str(task.get("bands") or "")
    assert task["cik"] == MICRON_CIK
    assert task["world_id"] != "finance_micron_asset_trajectory_fy2022_2025_v1"
