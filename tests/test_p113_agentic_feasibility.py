"""L5 feasibility requires action-conditioned, replayable alternatives."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p113_agentic_feasibility as feasibility


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_decision_requires_observation_two_actions_transition_feedback() -> None:
    point = {
        "observation": "test failed",
        "available_actions": ["fix", "revert"],
        "chosen_action": "fix",
        "action_outcomes": {
            "fix": {"next_observation": "test passed", "feedback": 1},
            "revert": {"next_observation": "test skipped", "feedback": 0},
        },
    }
    assert feasibility.qualify_decision(point) == (True, [])
    point["action_outcomes"].pop("revert")
    qualified, reasons = feasibility.qualify_decision(point)
    assert not qualified
    assert "missing_counterfactual_alternative" in reasons
    assert "missing_action_conditioned_transition" in reasons


def test_historical_ci_is_feedback_observation_not_policy_label() -> None:
    payload = {
        "records": [
            {"kind": "commit"},
            {"kind": "ci_run", "attributes": {"conclusion": "failure"}},
        ]
    }
    row = feasibility._source_row(payload, "pr-1", "historical_pr_ci")
    assert row["qualified_decision_points"] == 0
    assert "missing_action_menu" in row["rejection_reasons"]
    assert "missing_counterfactual_alternative" in row["rejection_reasons"]


def test_preflight_replays_pinned_sources_and_rejects_drift(tmp_path: Path) -> None:
    code_dir = tmp_path / "code"
    bundle = code_dir / "bundles" / "repo.json"
    export = code_dir / "exports" / "pr.json"
    _write(
        export,
        {
            "records": [
                {
                    "kind": "ci_run",
                    "occurred_at": "2020",
                    "attributes": {"conclusion": "failure"},
                }
            ]
        },
    )
    _write(
        bundle,
        {
            "path_base": "repository_root",
            "episodes": [
                {"path": "exports/pr.json", "sha256": feasibility.sha(export)}
            ],
        },
    )
    code_manifest = code_dir / "manifest.json"
    _write(
        code_manifest,
        {
            "train_ready": False,
            "frozen_usable_episodes": 1,
            "by_repository": [
                {
                    "repository": "test/repo",
                    "bundle_status": "signed_source_bundle",
                    "bundle_path": str(bundle),
                    "bundle": {"bundle_sha256": feasibility.sha(bundle)},
                    "usable_episodes": 1,
                }
            ],
        },
    )
    controlled = tmp_path / "controlled"
    world = controlled / "shards" / "one" / "world.json"
    receipt = world.parent / "receipt.json"
    _write(world, {"world_id": "one", "tasks": [{}]})
    _write(receipt, {"world_id": "one", "world_sha256": feasibility.sha(world)})
    _write(
        controlled / "manifest.json",
        {"train_ready": False, "accepted_jobs": 1, "semantic_tasks": 1},
    )
    report = feasibility.inspect(code_manifest, controlled)
    assert (
        report["historical_pr_episodes"],
        report["controlled_base_worlds"],
        report["qualified_l5_decision_points"],
    ) == (1, 1, 0)
    export.write_text(export.read_text() + " ")
    with pytest.raises(ValueError, match="source export changed"):
        feasibility.inspect(code_manifest, controlled)
