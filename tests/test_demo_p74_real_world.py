"""Smoke + seam tests for scripts/demo_p74_real_world.py (P74 INT).

Charter coverage: .hl/design/p74_real_shared_worlds.md §16 INT row — the
real-snapshot end-to-end run (bridge -> typed view -> task bank ->
execution + certificates -> length entry -> §5 dep-chain probe -> showcase)
must complete on the smallest real frozen snapshot and exit 0 in
allow-milestone-fail mode (the honest FAIL verdict IS a deliverable).

The module is imported the same way the sibling test files import
scripts.demo_p74_world: a spec-based load with the repo root on sys.path.
The full 7-snapshot run is exercised by the script itself, not here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "demo_p74_real_world", REPO / "scripts" / "demo_p74_real_world.py"
)
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

SMALLEST = "cantilever_bridges"
SNAPSHOT_PATH = demo.SNAPSHOT_DIR / f"{SMALLEST}_snapshot.json"


@pytest.fixture(scope="module")
def snapshot_world():
    if not SNAPSHOT_PATH.exists():
        pytest.skip("real frozen snapshots not on this volume")
    import json

    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return snapshot, demo.wwb.snapshot_to_world(snapshot)


# --- the typing adapter ------------------------------------------------------


def test_typed_view_types_subjects_and_object_roles(snapshot_world):
    _snapshot, world = snapshot_world
    typed, stats = demo.typed_view(world)
    # every fact subject got a subject family (s: prefix)
    subjects = {fact.subject for fact in world.facts}
    subject_families = {
        typed.objects[entity_id].entity_type
        for entity_id in subjects
        if typed.objects[entity_id].entity_type
    }
    assert subject_families
    assert all(name.startswith("s:") for name in subject_families)
    # object-only entities get o: role families, never a subject family
    objects = {fact.value for fact in world.facts if fact.value_type == "entity"}
    role_typed = [
        typed.objects[entity_id].entity_type
        for entity_id in objects - subjects
        if typed.objects[entity_id].entity_type
    ]
    assert all(name.startswith("o:") for name in role_typed)
    # facts, documents and spans are carried over untouched
    assert typed.facts == world.facts
    assert typed.documents == world.documents
    # stats account for every entity exactly once
    total = (
        stats["rule_subject_family"]
        + stats["rule_object_role_family"]
        + stats["unlinked_untyped"]
    )
    assert total == len(world.entities)


def test_structural_families_is_deterministic(snapshot_world):
    _snapshot, world = snapshot_world
    assert demo.structural_families(world) == demo.structural_families(world)


# --- the task bank runs on the typed view ------------------------------------


def test_bank_produces_locate_tasks_on_real_snapshot(snapshot_world):
    _snapshot, world = snapshot_world
    typed, _stats = demo.typed_view(world)
    bank = demo.wtb.build_task_bank(typed, demo.BANK_BUDGET)
    assert bank.counts()["locate"] >= 1
    # every task executes with a non-empty answer and fact consumption
    for task in bank.tasks:
        assert task.answer not in (None, "", ())
        assert task.consumed_fact_ids


# --- the §5 dep-chain probe ---------------------------------------------------


def test_dep_chain_probe_passes_fold_gate(snapshot_world):
    _snapshot, world = snapshot_world
    typed, _stats = demo.typed_view(world)
    probe = demo.run_dep_chain_probe(typed)
    assert probe["passed"] >= 1
    for chain in probe["chains"]:
        assert chain["metrics"]["non_foldable"]
        assert chain["metrics"]["state_transition_depth"] >= 1
        # every proof span's verbatim text matches the frozen document text
        docs = {doc.doc_id: doc for doc in typed.documents}
        for span in chain["proof"]:
            for ref, text in zip(span["spans"], span["span_texts"]):
                actual = docs[ref["doc_id"]].text[ref["start"] : ref["end"]]
                assert actual == text


# --- the end-to-end smoke run --------------------------------------------------


def test_main_smoke_run_exits_zero(tmp_path):
    if not SNAPSHOT_PATH.exists():
        pytest.skip("real frozen snapshots not on this volume")
    report, exit_code = demo.main(
        [
            "--snapshots",
            SMALLEST,
            "--json",
            str(tmp_path / "demo.json"),
            "--quiet",
            "--allow-milestone-fail",
        ]
    )
    assert exit_code == 0
    row = report["snapshots"][0]
    assert row["name"] == SMALLEST
    assert row["executed"] >= 1
    assert row["certificates"]["counts"]["surface_answer_supported"]["true"] >= 1
    assert row["length"]["natural_tokens"] > 0
    assert row["dep_chains"]["passed"] >= 1
    # the showcase ran (a chain exists on this snapshot) and its spans verify
    showcase = report["showcase"]
    assert showcase is not None
    for span in showcase["chain"]["proof_spans"]:
        assert span["matches_document_text"]
    written = tmp_path / "demo.json"
    assert written.exists()
    import json

    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["schema_version"] == demo.SCHEMA
    assert (
        payload["milestone_1_summary"]["failing"]
        or payload["milestone_1_summary"]["passing"]
    )


def test_main_reports_milestone_failure_without_dying(tmp_path):
    if not SNAPSHOT_PATH.exists():
        pytest.skip("real frozen snapshots not on this volume")
    report, exit_code = demo.main(
        [
            "--snapshots",
            SMALLEST,
            "--json",
            str(tmp_path / "demo.json"),
            "--quiet",
        ]
    )
    # the real-snapshot milestone verdict is FAIL (aggregate/multi_hop/
    # as_of_state are structurally absent) and that is REPORTED, not raised
    assert exit_code == 1
    row = report["snapshots"][0]
    assert not row["milestone_1"]["pass"]
    assert row["milestone_1"]["reasons"]
