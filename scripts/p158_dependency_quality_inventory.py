"""Inventory bounded dependency evidence in a frozen reader selection.

Grades describe the *recorded intervention*, not unrestricted necessity or
model behavior. Token distances are reported only with pinned native proof or
explicit candidate fields. A long reader by itself contributes no distance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from scripts.p113_coverage_matrix import build_report as verify_selection
from scripts.p138_capability_coverage import _length_bin, classify

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p158-dependency-quality-inventory.v1"

# These are evidence-method categories, not a total order of semantic proof.
# Every status in the frozen selection must be assigned explicitly.
SINGLE_EDIT = {
    "selected_reader_visible_transition_event_deletion_changes_answer;bounded_event_gap",
    "scoped_named_table_cell_removed",
    "scoped_table_insertion_replay",
    "bounded_visible_cell_hit_and_control_replay",
    "raw_to_final_reader_cells_plus_bounded_hit_control",
    "bounded_full_reader_row_scan_and_hit_control_edit",
    "scoped_table_parser_year_cells_removed",
    "bounded_numeric_hit_and_near_miss_replay",
    "bounded_full_table_row_scan_hit_removal_and_near_miss_edits",
    "bounded_full_table_row_scan_and_numeric_cell_edits",
    "filename_content_backed_scoped_certificate",
}
SCOPED_SCREEN = {"linked_article_primary_label_shortcut_screened"}
MULTI_EDIT = {
    "source_swap_and_both_support_deletions_passed",
    "all_shared_support_deletion_and_two_negative_insertions_passed",
    "visible_selector_and_target_edits; alternative_comparative_support_unsearched",
    "two_cell_interventions_passed",
    "content_backed_two_source_scoped_certificate",
    "bounded_final_reader_reference_and_target_deletions",
    "scoped_review_to_final_diff_text_intervention",
    "bounded_reference_and_target_text_deletions_passed",
    "four_own_report_cells; all_exact_numeral_support_removed_per_value; paraphrase_unsearched",
    "reader_visible_remote_selector_and_target_hit_control",
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def grade(status: str | None) -> str:
    if status is None or status == "unmeasured":
        return "unmeasured"
    if status in SINGLE_EDIT:
        return "bounded_single_or_set_edit"
    if status in MULTI_EDIT:
        return "bounded_multiple_support_edits"
    if status in SCOPED_SCREEN:
        return "scoped_shortcut_screen_only"
    raise ValueError(f"unclassified dependency status: {status}")


def _add_metric(metrics: dict[str, int], name: str, value: Any, limit: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= limit:
        raise ValueError(f"invalid final-token {name}: {value}")
    if name in metrics and metrics[name] != value:
        raise ValueError(f"conflicting final-token {name}")
    metrics[name] = value


def evidence_for(row: dict[str, Any], proof: dict[str, Any] | None) -> tuple[dict[str, int], set[str]]:
    """Extract only audited final-token quantities and native witness IDs."""
    metrics: dict[str, int] = {}
    witnesses: set[str] = set()
    limit = row["input_tokens"]
    for field, metric in (
        ("observed_witness_gap_tokens", "support_separation"),
        ("bounded_support_gap_tokens", "support_separation"),
        ("observed_witness_span_tokens", "evidence_extent"),
        ("evidence_extent_tokens", "evidence_extent"),
        ("last_evidence_to_query_tokens", "last_support_to_query"),
    ):
        if field in row:
            _add_metric(metrics, metric, row[field], limit)
    if proof is None:
        return metrics, witnesses
    name = row["source_name"]
    if name == "p133_state_reader_QA":
        if proof.get("world_id") != row["source_group"]:
            raise ValueError("P133 proof world differs")
        if proof.get("task_id") != row.get("source_task_id") or proof.get("source_world_sha256") != row.get("source_world_sha256"):
            raise ValueError("P133 proof task or frozen world differs")
        span = proof["decisive_fact_token_span"]
        query = proof["query_start_token"]
        if not 0 <= span[0] < span[1] <= query <= limit:
            raise ValueError("P133 proof token order differs")
        _add_metric(metrics, "decisive_support_to_query", query - span[1], limit)
        if metrics["decisive_support_to_query"] != proof["decisive_to_query_tokens"]:
            raise ValueError("P133 proof gap differs")
        witnesses.add(f"{row['source_group']}:{proof['decisive_fact_id']}")
    elif name == "p154_typed_grid_grammar":
        position = proof["final_chat_positions"]
        first = position["first_evidence_token"]
        last = position["last_evidence_token"]
        query = position["question_start_token"]
        if not 0 <= first < last < query <= limit:
            raise ValueError("P154 proof token order differs")
        _add_metric(metrics, "evidence_extent", last - first, limit)
        _add_metric(metrics, "last_support_to_query", query - last, limit)
        for item in proof["candidate_rows"]:
            start, end = item["source_html_span"]
            witnesses.add(f"{proof['source_html_sha256']}:{start}:{end}")
    elif name == "p146_real_paper_source_reference":
        spans = proof["prompt_token_spans"]
        positions = [spans["reference"], spans["target"]]
        query = proof["query_token_start"]
        if any(not 0 <= start < end < query <= limit for start, end in positions):
            raise ValueError("P146 proof token order differs")
        _add_metric(metrics, "evidence_extent", max(s[1] for s in positions) - min(s[0] for s in positions), limit)
        _add_metric(metrics, "support_separation", proof["bounded_support_gap_tokens"], limit)
        _add_metric(metrics, "last_support_to_query", query - max(s[1] for s in positions), limit)
        archive = proof["source_archive"]["sha256"]
        witnesses.update({
            f"{archive}:{proof['source_path']}:{proof['reference_label']}",
            f"{archive}:{proof['target_path']}:{proof['reference_label']}",
        })
    return metrics, witnesses


def _proofs(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    proof_by_id: dict[str, dict[str, Any]] = {}
    for item in config["native_proofs"]:
        for proof in rows(ROOT / item["path"]):
            sample_id = proof["sample_id"]
            if sample_id in proof_by_id:
                raise ValueError(f"duplicate native proof: {sample_id}")
            proof_by_id[sample_id] = proof
    return proof_by_id


def summarize(entries: list[dict[str, Any]], routes: list[dict[str, Any]], proofs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cells: dict[tuple[str, str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"views": 0, "tasks": set(), "groups": set(), "full_chat_tokens": 0,
                 "metrics": defaultdict(list)}
    )
    statuses = Counter()
    grades = Counter()
    measured = Counter()
    evidence_by_group: dict[tuple[str, str], list[tuple[str, str, set[str]]]] = defaultdict(list)
    groups_capabilities: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entry in entries:
        row = entry["candidate"]
        status = row.get("dependency_status")
        evidence_grade = grade(status)
        capability, _, route_state = classify(row, routes)
        if route_state != "declared":
            raise ValueError(f"unclassified capability: {row['sample_id']}")
        full = row["full_chat_tokens"]
        if row["input_tokens"] + row["supervised_tokens"] != full:
            raise ValueError("final-chat token accounting differs")
        length = _length_bin(full)
        if row["length_bin"] != length:
            raise ValueError("physical length label differs")
        proof = proofs.get(row["sample_id"])
        if row["source_name"] in {"p133_state_reader_QA", "p154_typed_grid_grammar", "p146_real_paper_source_reference"} and proof is None:
            raise ValueError(f"missing pinned native proof: {row['sample_id']}")
        metrics, witnesses = evidence_for(row, proof)
        group = (row["source_kind"], row["source_group"])
        task = (row["source_kind"], row["semantic_task_id"])
        key = (row["source_kind"], capability, length, evidence_grade, str(status))
        slot = cells[key]
        slot["views"] += 1
        slot["tasks"].add(task)
        slot["groups"].add(group)
        slot["full_chat_tokens"] += full
        for metric, value in metrics.items():
            slot["metrics"][metric].append(value)
            measured[metric] += 1
        statuses[str(status)] += 1
        grades[evidence_grade] += 1
        evidence_by_group[group].append((row["sample_id"], capability, witnesses))
        if capability != "composite_reuse":
            groups_capabilities[group].add(capability)
    overlap = Counter()
    overlap_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []
    for group, members in sorted(evidence_by_group.items()):
        if len(groups_capabilities[group]) < 2:
            continue
        for left, right in combinations(members, 2):
            if left[1] == right[1]:
                continue
            overlap["cross_capability_pairs"] += 1
            overlap_by_source[group[0]]["cross_capability_pairs"] += 1
            if not left[2] or not right[2]:
                overlap["unmeasurable_pairs"] += 1
                overlap_by_source[group[0]]["unmeasurable_pairs"] += 1
                continue
            overlap["native_witness_measurable_pairs"] += 1
            overlap_by_source[group[0]]["native_witness_measurable_pairs"] += 1
            shared = left[2] & right[2]
            if shared:
                overlap["pairs_with_shared_native_witness"] += 1
                overlap_by_source[group[0]]["pairs_with_shared_native_witness"] += 1
                if len(examples) < 10:
                    examples.append({"group": list(group), "left": left[0], "right": right[0], "shared_witness_count": len(shared)})
    if overlap["cross_capability_pairs"] != overlap["unmeasurable_pairs"] + overlap["native_witness_measurable_pairs"]:
        raise ValueError("overlap accounting differs")
    return {
        "views": len(entries),
        "independent_tasks": len({(e["candidate"]["source_kind"], e["candidate"]["semantic_task_id"]) for e in entries}),
        "source_kinds": dict(sorted(Counter(e["candidate"]["source_kind"] for e in entries).items())),
        "grades": dict(sorted(grades.items())),
        "statuses": dict(sorted(statuses.items())),
        "final_token_metric_available_views": dict(sorted(measured.items())),
        "cells": [
            {"source_kind": key[0], "capability": key[1], "length_bin": key[2],
             "grade": key[3], "dependency_status": key[4], "views": cell["views"],
             "tasks": len(cell["tasks"]), "typed_source_groups": len(cell["groups"]),
             "full_chat_tokens": cell["full_chat_tokens"],
             "metrics": {name: {"views": len(values), "min": min(values), "max": max(values),
                                 "ge8192": sum(value >= 8192 for value in values),
                                 "ge16384": sum(value >= 16384 for value in values)}
                         for name, values in sorted(cell["metrics"].items())}}
            for key, cell in sorted(cells.items())
        ],
        "cross_capability_native_witness_overlap": {
            **{key: overlap[key] for key in ("cross_capability_pairs", "native_witness_measurable_pairs", "pairs_with_shared_native_witness", "unmeasurable_pairs")},
            "by_source_kind": {
                kind: {key: counts[key] for key in ("cross_capability_pairs", "native_witness_measurable_pairs", "pairs_with_shared_native_witness", "unmeasurable_pairs")}
                for kind, counts in sorted(overlap_by_source.items())
            },
            "examples": examples,
            "scope": "Only pinned P133 decisive fact IDs, P154 examined source HTML cells, and P146 reference/target labels. A shared native witness is not proof that both whole tasks require it; unmeasurable pairs are not treated as disjoint.",
        },
    }


def build_report(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != SCHEMA:
        raise ValueError("invalid P158 config")
    for item in config["pins"] + config["native_proofs"]:
        if sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError(f"pinned input changed: {item['path']}")
    paths = {item["role"]: ROOT / item["path"] for item in config["pins"]}
    verified = verify_selection(paths["index_manifest"].parent, paths["selection_manifest"].parent)
    selected = rows(paths["selection_refs"])
    if len(selected) != verified["selected"]["views"]:
        raise ValueError("selection verification count differs")
    routes = json.loads(paths["taxonomy"].read_text(encoding="utf-8"))["routes"]
    baseline = rows(paths["baseline_refs"])
    summary = summarize(selected, routes, _proofs(config))
    old_ids = {entry["candidate"]["sample_id"] for entry in baseline}
    new_ids = {entry["candidate"]["sample_id"] for entry in selected}
    return {
        "schema": SCHEMA,
        "config_sha256": sha(config_path),
        "input_sha256": {item["path"]: item["sha256"] for item in config["pins"] + config["native_proofs"]},
        "baseline_p152": {"views": len(baseline), "retained_sample_ids": len(old_ids & new_ids),
                           "only_baseline": len(old_ids - new_ids), "only_p156": len(new_ids - old_ids)},
        "selected_p156_v3": summary,
        "interpretation": "Classes record bounded intervention shapes. Their presence, physical length and measured token gap do not prove unrestricted reader-text necessity, all-proof minimum distance, or model gains.",
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/p158_dependency_quality_v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/candidates/p158_dependency_quality_v1/report.json")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    report = build_report(args.config)
    if args.verify_only:
        if json.loads(args.output.read_text(encoding="utf-8")) != report:
            raise ValueError("P158 report does not replay")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    print(json.dumps({"selected": report["selected_p156_v3"]["views"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
