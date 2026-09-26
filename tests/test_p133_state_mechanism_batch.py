"""Executable state transitions, source-text interventions and policy choices."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p133_state_mechanism_batch as batch


@pytest.mark.parametrize("mechanism", batch.MECHANISMS)
def test_new_world_has_two_replayed_scopes_and_operations(mechanism: str) -> None:
    world = batch.build_world(1330000, mechanism, 400, 20, 2, 2)
    assert len(world["tasks"]) == 4
    assert {task["question"]["operation"] for task in world["tasks"]} == {
        "net_sum",
        "residual_entries",
    }
    for task in world["tasks"]:
        assert (
            batch.solve_visible(world["reader_context"], task["question"])
            == task["answer"]
        )
    assert world["world_id"].startswith(f"p133-{mechanism}-")


def test_partial_reversal_changes_amount_without_deleting_record() -> None:
    world = batch.build_world(1330000, "partial_reversal", 400, 20, 2, 2)
    task = next(
        item for item in world["tasks"] if item["task_id"] == "q0:residual_entries"
    )
    baseline = {entry["id"]: entry["residual"] for entry in task["answer"]["entries"]}
    _, _, events = batch._parse(world["reader_context"])
    for event in events:
        if event["type"] != "partial_reversal" or event["record_id"] not in baseline:
            continue
        reduced, removed = batch._drop(world["reader_context"], event["id"])
        changed = {
            entry["id"]: entry["residual"]
            for entry in batch.solve_visible(reduced, task["question"])["entries"]
        }
        if changed != baseline:
            assert removed == [event["id"]]
            assert set(changed) == set(baseline)
            assert (
                changed[event["record_id"]] - baseline[event["record_id"]]
                == event["units"]
            )
            return
    pytest.fail("no effective partial reversal changed a visible residual")


def test_controller_grant_restores_held_record_but_viewer_does_not() -> None:
    world = batch.build_world(1330000, "authorization_hold", 400, 20, 2, 2)
    task = next(item for item in world["tasks"] if item["task_id"] == "q0:net_sum")
    _, rows, events = batch._parse(world["reader_context"])
    scoped = set(batch._scope(rows, task["question"]["filters"]))
    by_role = {
        item["role"]: item
        for item in events
        if item["type"] == "grant" and item["record_id"] in scoped
    }
    baseline = task["answer"]["sum"]
    removed_controller, _ = batch._drop(
        world["reader_context"], by_role["controller"]["id"]
    )
    removed_viewer, _ = batch._drop(world["reader_context"], by_role["viewer"]["id"])
    assert batch.solve_visible(removed_controller, task["question"])["sum"] < baseline
    assert batch.solve_visible(removed_viewer, task["question"])["sum"] == baseline


@pytest.mark.parametrize("mechanism", batch.MECHANISMS)
def test_two_action_outcomes_and_reader_deletion_flip(mechanism: str) -> None:
    world = batch.build_world(1330000, mechanism, 400, 20, 2, 2)
    task = next(item for item in world["tasks"] if item["task_id"] == "q0:net_sum")
    balance = task["answer"]["sum"]
    for sign in (1, -1):
        target = balance + sign * 100
        action, outcomes = batch.policy.decision(balance, target, 500)
        assert (
            outcomes["ADD_500"]["next_balance"]
            != outcomes["REMOVE_500"]["next_balance"]
        )
        flips = batch._state_witnesses(world["reader_context"], task, target, sign)
        assert flips
        assert batch.policy.decision(flips[0][1], target, 500)[0] != action


def test_verify_rejects_different_compiler_sha(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"compiler_sha256": "stale"}))
    with pytest.raises(ValueError, match="compiler SHA differs"):
        batch.compile_batch(
            Path("configs/p133_state_mechanism_batch_v1.json"),
            tmp_path,
            verify_only=True,
        )


@pytest.mark.parametrize("mechanism", batch.MECHANISMS)
def test_effective_and_disclosure_boundaries_change_executed_state(
    mechanism: str,
) -> None:
    world = batch.build_world(1330000, mechanism, 400, 20, 2, 2)
    task = next(item for item in world["tasks"] if item["task_id"] == "q0:net_sum")
    original = task["answer"]["sum"]
    from datetime import date, timedelta

    for field in ("asof", "known_at"):
        changed = {**task["question"]}
        changed[field] = (
            date.fromisoformat(changed[field]) + timedelta(days=1)
        ).isoformat()
        assert batch.solve_visible(world["reader_context"], changed)["sum"] < original


def test_prior_source_pin_detects_seed_overlap() -> None:
    config = batch._config(Path("configs/p133_state_mechanism_batch_v2.json"))
    seeds, _, _, pins = batch._prior_worlds(config)
    assert 1330000 in seeds
    assert 1123000 in seeds
    assert len(pins) == 2
    assert not seeds & {job[0] for job in batch._jobs(config)}


@pytest.mark.parametrize("mechanism", batch.MECHANISMS)
def test_qa_witnesses_are_transition_events(mechanism: str) -> None:
    world = batch.build_world(1330000, mechanism, 400, 20, 2, 2)
    for task in world["tasks"]:
        witnesses = batch._qa_witnesses(world["reader_context"], task)
        assert witnesses
        assert all(fact_id.startswith("e") for fact_id, _, _ in witnesses)


@pytest.mark.parametrize("mechanism", batch.MECHANISMS)
def test_both_policy_labels_have_mechanism_event_action_flip(mechanism: str) -> None:
    world = batch.build_world(1330000, mechanism, 400, 20, 2, 2)
    task = next(item for item in world["tasks"] if item["task_id"] == "q0:net_sum")
    balance = task["answer"]["sum"]
    for sign in (1, -1):
        target = balance + sign * 100
        expected = batch.policy.decision(balance, target, 500)[0]
        witnesses = batch._policy_mechanism_witnesses(
            world["reader_context"], task, target
        )
        assert witnesses
        assert all(witness["fact_id"].startswith("e") for witness in witnesses)
        assert all(witness["changed_action"] != expected for witness in witnesses)
        if sign < 0:
            assert any(witness["kind"] == "edit" for witness in witnesses)


def test_future_hold_boundary_edit_recovers_short_grant_witness() -> None:
    world = batch.build_world(1331039, "authorization_hold", 400, 20, 2, 2)
    for task in world["tasks"]:
        if task["question"]["operation"] != "net_sum":
            continue
        balance = task["answer"]["sum"]
        witnesses = batch._policy_mechanism_witnesses(
            world["reader_context"], task, balance - 100
        )
        assert any(
            witness["kind"] == "edit"
            and witness["field"] == "effective"
            and witness["changed_action"] == "ADD_500"
            for witness in witnesses
        )


def test_prior_world_scan_reserves_rejected_gross_world(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(batch, "ROOT", tmp_path)
    root = tmp_path / "source"
    pins = {}
    for name, seed in (("accepted", 1), ("rejected", 2)):
        path = root / "worlds" / name / "world.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "world_id": name,
                    "seed": seed,
                    "base_record_context_sha256": str(seed) * 64,
                }
            )
        )
        pins[str(path.relative_to(root))] = batch.file_sha(path)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": batch.OUTPUT_SCHEMA,
                "source_worlds": 1,
                "gross_source_worlds": 2,
                "world_files_sha256": pins,
            }
        )
    )
    config = {
        "prior_world_sets": [
            {
                "path": "source/manifest.json",
                "sha256": batch.file_sha(manifest_path),
            }
        ]
    }
    seeds, ids, hashes, _ = batch._prior_worlds(config)
    assert seeds == {1, 2}
    assert ids == {"accepted", "rejected"}
    assert len(hashes) == 2
