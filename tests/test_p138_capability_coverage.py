"""Behavioral tests for versioned capability attribution and frozen inputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p138_capability_coverage as audit

CONFIG = Path(__file__).resolve().parents[1] / "configs/p138_capability_taxonomy_v1.json"
ROUTES = json.loads(CONFIG.read_text(encoding="utf-8"))["routes"]


def _entry(
    sample: str,
    task: str,
    *,
    source_kind: str = "real_wiki",
    source_name: str = "wiki_real",
    source_group: str = "same-world",
    operation: str = "table_cell_lookup",
    topic: str = "airports",
    evidence_profile: str = "table_subject_header_value_structurally_checked",
    full: int = 40000,
) -> dict:
    return {
        "shard": "fixture",
        "candidate": {
            "sample_id": sample,
            "semantic_task_id": task,
            "source_kind": source_kind,
            "source_name": source_name,
            "source_group": source_group,
            "operation": operation,
            "topic": topic,
            "evidence_profile": evidence_profile,
            "domain": "fixture",
            "split": "train",
            "dependency_status": None,
            "length_bin": "32k",
            "input_tokens": full - 5,
            "supervised_tokens": 5,
            "full_chat_tokens": full,
        },
    }


def test_deduplicates_views_but_counts_token_exposure() -> None:
    entries = [_entry("a", "task"), _entry("b", "task")]
    report = audit.summarize(entries, ROUTES)
    assert report["views"] == 2
    assert report["independent_semantic_tasks"] == 1
    assert report["capabilities"]["locate"]["tasks"] == 1
    assert report["capabilities"]["locate"]["input_tokens"] == 79990
    assert report["null_dependency_status_views"] == 2
    assert report["actual_length_bin_mismatch_views"] == 0


def test_unknown_and_ambiguous_are_not_guessed() -> None:
    unknown = _entry("a", "a", operation="future_operation")
    assert audit.classify(unknown["candidate"], ROUTES) == (
        "unknown",
        None,
        "unmapped",
    )
    conflicting = [
        {"source_kind": "real_wiki", "operation": ["table_cell_lookup"], "capability": "locate"},
        {"source_kind": "real_wiki", "operation": ["table_cell_lookup"], "capability": "compare"},
    ]
    assert audit.classify(_entry("b", "b")["candidate"], conflicting) == (
        "unknown",
        None,
        "ambiguous",
    )


@pytest.mark.parametrize("mechanism", ["partial_reversal", "authorization_hold"])
def test_p133_requires_source_topic_and_evidence(mechanism: str) -> None:
    row = _entry(
        "a", "a", source_kind="controlled_simulation",
        source_name="p133_state_reader_QA", source_group=f"p133-{mechanism}-abc",
        operation="net_sum", topic=mechanism,
        evidence_profile="controlled_state_transition",
    )["candidate"]
    assert audit.classify(row, ROUTES) == ("state_aggregate", mechanism, "declared")
    assert audit.classify({**row, "evidence_profile": "native_solver_recheck"}, ROUTES)[0] == "unknown"
    assert audit.classify({**row, "source_name": "other"}, ROUTES)[0] == "unknown"
    assert audit.classify({**row, "source_group": "unrelated"}, ROUTES)[0] == "unknown"


def test_p125_reuse_is_not_new_capability_or_multi_capability_group() -> None:
    joint = _entry(
        "joint", "joint", source_name="p125_joint_multi_operation",
        operation="joint_multi_operation_answer",
        evidence_profile="two_audited_same_context_tasks",
    )
    locate = _entry("base", "base")
    report = audit.summarize([joint, locate], ROUTES)
    assert report["capabilities"]["composite_reuse"]["tasks"] == 1
    assert report["known_multi_capability_groups"] == 0


def test_same_p133_world_has_two_capabilities_but_one_native_mechanism() -> None:
    rows = [
        _entry(
            operation, operation, source_kind="controlled_simulation",
            source_name="p133_state_reader_QA",
            source_group="p133-partial_reversal-same",
            topic="partial_reversal", operation=operation,
            evidence_profile="controlled_state_transition",
        )
        for operation in ("net_sum", "residual_entries")
    ]
    report = audit.summarize(rows, ROUTES)
    assert report["known_multi_capability_groups"] == 1
    assert report["native_mechanisms"]["partial_reversal"]["tasks"] == 2
    assert list(report["native_mechanisms"]) == ["partial_reversal"]


def test_one_semantic_task_cannot_change_capability_across_length_views() -> None:
    rows = [
        _entry("a", "same"),
        _entry("b", "same", operation="closed_categorical_table_scan"),
    ]
    with pytest.raises(ValueError, match="inconsistent capability"):
        audit.summarize(rows, ROUTES)


def test_dynamic_finance_labels_normalize_without_claiming_new_mechanisms() -> None:
    row = _entry(
        "a", "a", source_kind="real_finance", source_name="p112_report_period_delta",
        operation="min_value:revenue->latest_delta:cash_from_operations",
    )["candidate"]
    assert audit.classify(row, ROUTES) == ("aggregate_compare", None, "declared")


def test_changed_pin_rejected_before_loading_bank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    index = tmp_path / config["input"]["index"]
    selection = tmp_path / config["input"]["selection"]
    index.mkdir(parents=True)
    selection.mkdir(parents=True)
    for directory, name in (
        (index, "manifest.json"), (index, "candidate_refs.jsonl"),
        (selection, "manifest.json"), (selection, "selected_refs.jsonl"),
    ):
        (directory / name).write_text("original\n", encoding="utf-8")
    for key, path in {
        "index_manifest_sha256": index / "manifest.json",
        "index_refs_sha256": index / "candidate_refs.jsonl",
        "selection_manifest_sha256": selection / "manifest.json",
        "selection_refs_sha256": selection / "selected_refs.jsonl",
    }.items():
        config["input"][key] = audit._sha(path)
    config_path = tmp_path / "taxonomy.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    (selection / "selected_refs.jsonl").write_text("tampered\n", encoding="utf-8")
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="pinned input changed: selection_refs_sha256"):
        audit.build_report(config_path)
