"""Read-only L5 action-feedback feasibility check on frozen P108/P112 sources.

Historical commits, CI, state events and QA answers remain useful reader
material, but do not become policy labels without a replayable decision point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "longworld.p113-agentic-feasibility.v1"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def qualify_decision(point: dict[str, Any]) -> tuple[bool, list[str]]:
    """Require two executable alternatives with observed next state and feedback."""
    reasons = []
    if not point.get("observation"):
        reasons.append("missing_pre_action_observation")
    actions = point.get("available_actions")
    if (
        not isinstance(actions, list)
        or len(actions) < 2
        or any(not isinstance(action, str) or not action for action in actions)
        or len(actions) != len(set(actions))
    ):
        reasons.append("missing_action_menu")
        actions = []
    if point.get("chosen_action") not in actions:
        reasons.append("missing_chosen_action")
    outcomes = point.get("action_outcomes")
    if (
        not isinstance(outcomes, dict)
        or any(
            not isinstance(outcomes.get(action), dict)
            or not outcomes[action].get("next_observation")
            for action in actions
        )
        or not actions
    ):
        reasons.append("missing_action_conditioned_transition")
    if (
        not isinstance(outcomes, dict)
        or any(
            not isinstance(outcomes.get(action), dict)
            or outcomes[action].get("feedback") is None
            for action in actions
        )
        or not actions
    ):
        reasons.append("missing_feedback_by_action")
    if (
        not isinstance(outcomes, dict)
        or len(actions) < 2
        or not all(action in outcomes for action in actions)
    ):
        reasons.append("missing_counterfactual_alternative")
    return not reasons, reasons


def _decision_points(payload: dict[str, Any]) -> list[dict[str, Any]]:
    points = payload.get("decision_points")
    return (
        points
        if isinstance(points, list) and all(isinstance(p, dict) for p in points)
        else []
    )


def _source_row(payload: dict[str, Any], source_id: str, lane: str) -> dict[str, Any]:
    points = _decision_points(payload)
    decisions = [qualify_decision(point) for point in points]
    if not points:
        # Absence of a decision-point record is not evidence for any action.
        reasons = [
            "missing_action_menu",
            "missing_chosen_action",
            "missing_action_conditioned_transition",
            "missing_feedback_by_action",
            "missing_counterfactual_alternative",
        ]
    else:
        reasons = sorted({reason for _, errors in decisions for reason in errors})
    return {
        "source_id": source_id,
        "lane": lane,
        "decision_points": len(points),
        "qualified_decision_points": sum(ok for ok, _ in decisions),
        "rejection_reasons": reasons,
    }


def inspect(code_manifest_path: Path, controlled_dir: Path) -> dict[str, Any]:
    code = _json(code_manifest_path)
    controlled_manifest_path = controlled_dir / "manifest.json"
    controlled = _json(controlled_manifest_path)
    if (
        code.get("train_ready") is not False
        or controlled.get("train_ready") is not False
    ):
        raise ValueError("feasibility scan accepts candidate-only source manifests")
    bundle_rows = []
    episode_rows = []
    for repository in code["by_repository"]:
        if repository["bundle_status"] != "signed_source_bundle":
            raise ValueError("P108 source bundle is not signed")
        bundle_path = Path(repository["bundle_path"])
        if sha(bundle_path) != repository["bundle"]["bundle_sha256"]:
            raise ValueError("P108 bundle changed")
        bundle = _json(bundle_path)
        if bundle.get("path_base") != "repository_root":
            raise ValueError("unexpected P108 bundle path base")
        if len(bundle["episodes"]) != repository["usable_episodes"]:
            raise ValueError("P108 bundle episode count differs")
        bundle_rows.append(
            {
                "repository": repository["repository"],
                "episodes": len(bundle["episodes"]),
            }
        )
        for entry in bundle["episodes"]:
            export_path = bundle_path.parent.parent / entry["path"]
            if sha(export_path) != entry["sha256"]:
                raise ValueError("P108 source export changed")
            exported = _json(export_path)
            records = exported["records"]
            ci = [record for record in records if record["kind"] == "ci_run"]
            failed = [
                record
                for record in ci
                if record["attributes"].get("conclusion")
                in {
                    "failure",
                    "cancelled",
                    "timed_out",
                    "action_required",
                    "stale",
                    "startup_failure",
                }
            ]
            later_commit = any(
                record["kind"] == "commit"
                and any(
                    record["occurred_at"] > check["occurred_at"] for check in failed
                )
                for record in records
            )
            row = _source_row(exported, entry["path"], "historical_pr_ci")
            row.update(
                {
                    "observed_ci": bool(ci),
                    "failed_ci": bool(failed),
                    "later_commit_after_failed_ci": later_commit,
                }
            )
            episode_rows.append(row)
    if len(episode_rows) != code["frozen_usable_episodes"]:
        raise ValueError("P108 usable episode count differs")

    controlled_rows = []
    for world_path in sorted((controlled_dir / "shards").glob("*/world.json")):
        receipt_path = world_path.parent / "receipt.json"
        receipt = _json(receipt_path)
        if sha(world_path) != receipt["world_sha256"]:
            raise ValueError("controlled world changed")
        world = _json(world_path)
        if world["world_id"] != receipt["world_id"]:
            raise ValueError("controlled world identity differs")
        row = _source_row(world, world["world_id"], "controlled_state_qa")
        row["qa_tasks"] = len(world["tasks"])
        controlled_rows.append(row)
    if (
        len(controlled_rows) != controlled["accepted_jobs"]
        or sum(row["qa_tasks"] for row in controlled_rows)
        != controlled["semantic_tasks"]
    ):
        raise ValueError("controlled world/task count differs")
    reasons = Counter(
        reason
        for row in episode_rows + controlled_rows
        for reason in row["rejection_reasons"]
    )
    return {
        "schema_version": SCHEMA,
        "source_manifest_sha256": sha(code_manifest_path),
        "controlled_manifest_sha256": sha(controlled_manifest_path),
        "unit_contract": "27 PR source episodes plus 24 controlled base worlds; these are not 51 L5 tasks",
        "repositories": bundle_rows,
        "historical_pr_episodes": len(episode_rows),
        "pr_episodes_with_ci": sum(row["observed_ci"] for row in episode_rows),
        "pr_episodes_with_failed_ci": sum(row["failed_ci"] for row in episode_rows),
        "pr_episodes_with_failed_ci_then_later_commit": sum(
            row["later_commit_after_failed_ci"] for row in episode_rows
        ),
        "controlled_base_worlds": len(controlled_rows),
        "controlled_qa_tasks": sum(row["qa_tasks"] for row in controlled_rows),
        "qualified_l5_decision_points": sum(
            row["qualified_decision_points"] for row in episode_rows + controlled_rows
        ),
        "rejection_reason_source_counts": dict(sorted(reasons.items())),
        "source_rows": episode_rows + controlled_rows,
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-manifest", type=Path, required=True)
    parser.add_argument("--controlled-base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    report = inspect(args.code_manifest, args.controlled_base)
    if args.verify_only:
        if _json(args.output) != report:
            raise ValueError("agentic feasibility report does not replay")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    print(
        json.dumps(
            {
                "pr_episodes": report["historical_pr_episodes"],
                "controlled_worlds": report["controlled_base_worlds"],
                "qualified_l5": report["qualified_l5_decision_points"],
            }
        )
    )


if __name__ == "__main__":
    main()
