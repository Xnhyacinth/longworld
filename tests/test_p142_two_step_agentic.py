"""Two-step policy branches must be executable and causally distinct."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p142_two_step_agentic as agentic


def test_first_action_changes_observation_and_second_choice() -> None:
    balances = {"KEEP_EVENT": 1000, "CANCEL_EVENT": 2200}
    keep = agentic._plan(balances, 1500)
    cancel = agentic._plan(balances, 1700)
    assert keep["first_action"] == "KEEP_EVENT"
    assert cancel["first_action"] == "CANCEL_EVENT"
    assert keep["branches"]["KEEP_EVENT"]["second_action"] == "ADD_500"
    assert keep["branches"]["CANCEL_EVENT"]["second_action"] == "REMOVE_500"
    assert cancel["branches"]["KEEP_EVENT"]["second_action"] == "ADD_500"
    assert cancel["branches"]["CANCEL_EVENT"]["second_action"] == "REMOVE_500"
    assert keep["branches"]["KEEP_EVENT"]["terminal_feedback"] == 0
    assert cancel["branches"]["CANCEL_EVENT"]["terminal_feedback"] == 0


def test_tied_or_identical_second_decisions_are_rejected() -> None:
    with pytest.raises(ValueError, match="tied terminal feedback"):
        agentic._plan({"KEEP_EVENT": 1000, "CANCEL_EVENT": 2000}, 1500)
    with pytest.raises(ValueError, match="does not change second decision"):
        agentic._plan({"KEEP_EVENT": 1000, "CANCEL_EVENT": 1200}, 2000)


def test_source_event_cancel_replays_different_balance() -> None:
    jobs = agentic._source_jobs(
        agentic._config(Path("configs/p142_two_step_agentic_pilot_v1.json"))
    )
    world = json.loads(Path(jobs[0][0]).read_text())
    task = next(
        item for item in world["tasks"] if item["question"]["operation"] == "net_sum"
    )
    event_id, balances = agentic._event(world["reader_context"], task["question"])
    assert event_id.startswith("e")
    assert balances["CANCEL_EVENT"] - balances["KEEP_EVENT"] > 500
    for action in agentic.FIRST_ACTIONS:
        assert (
            agentic._branch(world["reader_context"], task["question"], event_id, action)
            == balances[action]
        )


def test_source_manifest_pin_rejects_tamper(tmp_path: Path) -> None:
    original = json.loads(
        Path("configs/p142_two_step_agentic_pilot_v1.json").read_text()
    )
    original["source_manifest_sha256"] = "0" * 64
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="source manifest pin differs"):
        agentic._config(path)
