"""Behavioral checks for bounded dependency grades and native token proofs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p158_dependency_quality_inventory as audit


def candidate(sample: str, operation: str = "net_sum") -> dict:
    return {
        "sample_id": sample,
        "semantic_task_id": sample,
        "source_kind": "controlled_simulation",
        "source_name": "p133_state_reader_QA",
        "source_group": "p133-partial_reversal-fixture",
        "source_task_id": f"q0:{operation}",
        "source_world_sha256": "worldhash",
        "topic": "partial_reversal",
        "evidence_profile": "controlled_state_transition",
        "operation": operation,
        "dependency_status": "selected_reader_visible_transition_event_deletion_changes_answer;bounded_event_gap",
        "length_bin": "32k",
        "input_tokens": 39995,
        "supervised_tokens": 5,
        "full_chat_tokens": 40000,
    }


def proof(sample: str, fact: str) -> dict:
    return {
        "sample_id": sample,
        "world_id": "p133-partial_reversal-fixture",
        "task_id": f"q0:{sample.split(':')[-1]}",
        "source_world_sha256": "worldhash",
        "decisive_fact_id": fact,
        "decisive_fact_token_span": [100, 120],
        "query_start_token": 30000,
        "decisive_to_query_tokens": 29880,
    }


def test_p133_gap_uses_final_token_offsets_and_native_id() -> None:
    row = candidate("w:q0:net_sum")
    native = proof(row["sample_id"], "e1")
    metrics, witnesses = audit.evidence_for(row, native)
    assert metrics == {"decisive_support_to_query": 29880}
    assert witnesses == {"p133-partial_reversal-fixture:e1"}
    with pytest.raises(ValueError, match="proof gap differs"):
        audit.evidence_for(row, {**native, "decisive_to_query_tokens": 100})
    with pytest.raises(ValueError, match="task or frozen world"):
        audit.evidence_for(row, {**native, "source_world_sha256": "other"})


def test_unknown_status_and_physical_length_mismatch_fail_closed() -> None:
    assert audit.grade(None) == "unmeasured"
    assert audit.grade("linked_article_primary_label_shortcut_screened") == "scoped_shortcut_screen_only"
    with pytest.raises(ValueError, match="unclassified dependency"):
        audit.grade("new_unreviewed_status")
    row = candidate("w:q0:net_sum")
    row["length_bin"] = "64k"
    routes = [{"source_kind": "controlled_simulation", "operation": ["net_sum"], "capability": "state_aggregate"}]
    with pytest.raises(ValueError, match="physical length"):
        audit.summarize([{"candidate": row}], routes, {row["sample_id"]: proof(row["sample_id"], "e1")})


def test_cross_capability_overlap_has_measurable_denominator() -> None:
    a = candidate("w:q0:net_sum")
    b = candidate("w:q0:residual_entries", "residual_entries")
    c = candidate("w:q1:residual_entries", "residual_entries")
    c["source_task_id"] = "q1:residual_entries"
    p = {a["sample_id"]: proof(a["sample_id"], "e1"), b["sample_id"]: proof(b["sample_id"], "e1")}
    # The third row has no native proof and is therefore unmeasurable, not
    # evidence-disjoint. This fixture uses a non-P133 name for that row.
    c["source_name"] = "future_scoped_source"
    routes = [
        {"source_kind": "controlled_simulation", "operation": ["net_sum"], "capability": "state_aggregate"},
        {"source_kind": "controlled_simulation", "operation": ["residual_entries"], "capability": "state_complete_set"},
    ]
    result = audit.summarize([{"candidate": x} for x in (a, b, c)], routes, p)
    overlap = result["cross_capability_native_witness_overlap"]
    assert overlap["cross_capability_pairs"] == 2
    assert overlap["native_witness_measurable_pairs"] == 1
    assert overlap["pairs_with_shared_native_witness"] == 1
    assert overlap["unmeasurable_pairs"] == 1


def test_changed_input_pin_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = tmp_path / "selected_refs.jsonl"
    pinned.write_text("original\n", encoding="utf-8")
    config = {"schema": audit.SCHEMA, "pins": [{"role": "selection_refs", "path": pinned.name, "sha256": audit.sha(pinned)}], "native_proofs": []}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    pinned.write_text("tampered\n", encoding="utf-8")
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="pinned input changed"):
        audit.build_report(config_path)
