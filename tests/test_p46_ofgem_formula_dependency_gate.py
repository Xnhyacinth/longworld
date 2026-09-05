from __future__ import annotations

import json
from pathlib import Path

from reports import p46_ofgem_formula_dependency_gate as gate

ROOT = Path(__file__).resolve().parents[1]


def test_p46_fails_closed_without_cell_closures_or_oracle_engines() -> None:
    config = json.loads(
        (ROOT / "configs/p46_ofgem_formula_dependency_gate_v1.json").read_text()
    )
    preflight = json.loads(
        (ROOT / "reports/p46_ofgem_price_cap_workbook_preflight_v1.json").read_text()
    )

    report = gate.evaluate(config, preflight)

    assert report["candidate_count"] == 0
    assert report["train_ready"] is False
    assert report["answer_dependency_capacity"]["replayable_output_count"] == 0
    assert report["answer_dependency_capacity"]["exact_band_capacity"] == {
        "32k": False,
        "64k": False,
        "128k": False,
    }
    assert report["shortcut_audit"]["status"] == "not_auditable_fail_closed"
    assert "authorization_prohibits_candidate_generation" in report["blockers"]
    assert "cell_level_dependency_closures_not_persisted" in report["blockers"]
    assert "cross_engine_oracle_unavailable" in report["blockers"]


def test_p46_quantifies_template_reuse_without_treating_it_as_answer_capacity() -> None:
    config = json.loads(
        (ROOT / "configs/p46_ofgem_formula_dependency_gate_v1.json").read_text()
    )
    preflight = json.loads(
        (ROOT / "reports/p46_ofgem_price_cap_workbook_preflight_v1.json").read_text()
    )

    report = gate.evaluate(config, preflight)

    topology = report["topology"]
    assert topology["formula_cell_count"] == 481_336
    assert topology["cross_workbook_template_instance_count"] == 42_983
    assert topology["cross_workbook_unique_template_count"] == 18_224
    assert topology["cross_workbook_template_reuse_ratio"] > 0.57
    assert topology["nominal_branch_count"] == 12
    assert topology["unique_terminal_output_sheet_count"] == 1
    assert topology["unique_calculator_targets_per_workbook"] == {
        "2024Q4": 2,
        "2025Q4": 2,
        "2026Q3": 2,
    }
    assert report["raw_template_capacity"]["exact_band_capacity"]["128k"] is True
    assert report["answer_dependency_capacity"]["exact_band_capacity"]["128k"] is False
