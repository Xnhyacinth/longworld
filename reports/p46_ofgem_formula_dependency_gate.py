"""Fail-closed P46 answer-dependency gate over persisted preflight evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs/p46_ofgem_formula_dependency_gate_v1.json"
DEFAULT_OUTPUT = ROOT / "reports/p46_ofgem_formula_dependency_gate_v1.json"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_pinned(path: Path, expected_sha256: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if _sha256(raw) != expected_sha256:
        raise ValueError(f"P46 predecessor hash changed: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"P46 predecessor is not an object: {path}")
    return value


def evaluate(config: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    """Separate template capacity from executable answer-dependency capacity."""
    expected_bands = {
        "32k": [32_000, 32_768],
        "64k": [64_000, 65_536],
        "128k": [128_000, 131_072],
    }
    if config.get("capacity_bands") != expected_bands:
        raise ValueError("P46 exact bands changed")
    if preflight.get("config_sha256") != config["predecessor"]["config_sha256"]:
        raise ValueError("P46 preflight does not bind the pinned request")
    workbooks = preflight.get("workbooks")
    if not isinstance(workbooks, list) or not workbooks:
        raise ValueError("P46 workbook evidence is missing")

    formula_count = sum(int(item["formula_count"]) for item in workbooks)
    template_instances = sum(
        int(item["template_deduplicated_unit_count"]) for item in workbooks
    )
    unique_templates = int(
        preflight["capacity"]["cross_version_template_deduplicated_unit_count"]
    )
    branch_rows = [
        (str(item["period"]), branch)
        for item in workbooks
        for branch in item["dag_branch_preflight"]
    ]
    terminal_sheets = {str(branch["output_sheet"]) for _, branch in branch_rows}
    calculator_targets = {
        str(item["period"]): len(
            {str(branch["calculator_sheet"]) for branch in item["dag_branch_preflight"]}
        )
        for item in workbooks
    }
    materialized_sources = {
        str(item["period"]): sorted(
            {
                str(sheet)
                for branch in item["dag_branch_preflight"]
                for sheet in branch["source_reference_formula_counts"]
            }
        )
        for item in workbooks
    }

    predecessor_config_path = ROOT / config["predecessor"]["config_path"]
    predecessor_config = _load_pinned(
        predecessor_config_path, config["predecessor"]["config_sha256"]
    )
    prohibited = set(predecessor_config["authorization"]["prohibited_actions"])
    oracle = preflight["oracle_preflight"]
    internal_only_counts = {
        str(item["period"]): int(item["archive"]["internal_only_marker_part_count"])
        for item in workbooks
    }
    broken_name_counts = {
        str(item["period"]): int(item["broken_defined_name_count"])
        for item in workbooks
    }
    external_target_counts = {
        str(item["period"]): int(
            item["external_links"]["external_relationship_target_count"]
        )
        for item in workbooks
    }

    blockers = []
    if "generate_candidates" in prohibited:
        blockers.append("authorization_prohibits_candidate_generation")
    if any(internal_only_counts.values()):
        blockers.append("internal_only_marker_rights_clearance_missing")
    if any("cell_level_dependency_closures" not in item for item in workbooks):
        blockers.append("cell_level_dependency_closures_not_persisted")
    if not (
        oracle.get("openpyxl_available_in_project_environment")
        and oracle.get("libreoffice_available_in_environment")
    ):
        blockers.append("cross_engine_oracle_unavailable")
    if any(broken_name_counts.values()) or any(external_target_counts.values()):
        blockers.append("stale_external_state_exclusion_not_replayed")
    blockers.extend(
        [
            "output_cell_coordinates_and_cached_values_not_persisted",
            "cross_workbook_answer_change_not_demonstrated",
        ]
    )

    raw_capacity = dict(preflight["capacity"])
    answer_band_capacity = {band: False for band in expected_bands}
    report = {
        "schema_version": "longworld.p46-ofgem-formula-dependency-gate-report.v1",
        "data_product": config["data_product"],
        "predecessor": config["predecessor"],
        "evaluation_mode": config["mode"],
        "external_retrieval_performed": False,
        "topology": {
            "workbook_count": len(workbooks),
            "formula_cell_count": formula_count,
            "cross_workbook_template_instance_count": template_instances,
            "cross_workbook_unique_template_count": unique_templates,
            "cross_workbook_template_reuse_ratio": round(
                1.0 - unique_templates / template_instances, 6
            ),
            "nominal_branch_count": len(branch_rows),
            "unique_terminal_output_sheet_count": len(terminal_sheets),
            "terminal_output_sheets": sorted(terminal_sheets),
            "unique_calculator_targets_per_workbook": calculator_targets,
            "materialized_source_sheets_per_workbook": materialized_sources,
            "cell_level_formula_closure_count": 0,
            "replayable_cross_sheet_closure_count": 0,
            "replayable_cross_workbook_relation_count": 0,
            "interpretation": "sheet-level reference counts show convergence into two calculators and one output sheet, but do not bind any output cell to a complete dependency closure",
        },
        "raw_template_capacity": raw_capacity,
        "answer_dependency_capacity": {
            "replayable_output_count": 0,
            "authenticated_dependency_edge_count": 0,
            "unique_qwen_tokens": 0,
            "exact_band_capacity": answer_band_capacity,
            "reason": "no persisted cell-level closure, output receipt, or cross-engine replay exists",
        },
        "near_duplicate_audit": {
            "formula_instance_reduction_ratio_by_workbook": {
                str(item["period"]): float(item["formula_template_reduction_ratio"])
                for item in workbooks
            },
            "cross_workbook_template_reuse_ratio": round(
                1.0 - unique_templates / template_instances, 6
            ),
            "candidate_near_duplicate_gate_run": False,
            "reason": "candidate count is zero",
        },
        "shortcut_audit": {
            "raw_window_tokens": config["unchanged_gates"]["raw_window_tokens"],
            "audited_output_count": 0,
            "status": "not_auditable_fail_closed",
            "reason": "the aggregate preflight retains neither output-cell coordinates nor cached output values; no raw-window answer comparison is possible",
        },
        "oracle": {
            "openpyxl_available": bool(
                oracle.get("openpyxl_available_in_project_environment")
            ),
            "libreoffice_available": bool(
                oracle.get("libreoffice_available_in_environment")
            ),
            "executable_parent_compiler_replay_built": False,
            "reason": "building an executable parent from aggregate counts would invent missing formulas and source bindings",
        },
        "source_boundary": {
            "internal_only_marker_part_count_by_workbook": internal_only_counts,
            "broken_defined_name_count_by_workbook": broken_name_counts,
            "external_relationship_target_count_by_workbook": external_target_counts,
            "raw_workbooks_persisted": False,
        },
        "next_entity": {
            "proposal": "Ofgem quarterly default-tariff-cap public level tables and decision-publication chain",
            "selection_contract": "use only an official Ofgem HTML, CSV, or accessible annex with explicit public reuse status, dated revisions, stable row keys, and directly stated final cap values; exclude any attachment carrying an unresolved Internal Only marker",
            "task_shape": "quarter-to-quarter retained/changed/new region-payment-method cap rows with deterministic value-delta and publication-date oracle",
        },
        "candidate_count": 0,
        "generated_rows": 0,
        "accepted_rows": 0,
        "rejected_rows": 0,
        "train_ready": False,
        "production_eligible": False,
        "inventory_delta": 0,
        "blockers": sorted(set(blockers)),
        "verdict": "FAIL_CLOSED_ZERO_CANDIDATES",
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    config = json.loads(args.config.read_bytes())
    predecessor = config["predecessor"]
    preflight = _load_pinned(
        ROOT / predecessor["report_path"], predecessor["report_sha256"]
    )
    report = evaluate(config, preflight)
    args.output.write_bytes(
        json.dumps(report, indent=2, sort_keys=True).encode() + b"\n"
    )
    print(json.dumps({"output": str(args.output), "verdict": report["verdict"]}))


if __name__ == "__main__":
    main()
