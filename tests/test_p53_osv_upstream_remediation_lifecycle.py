from __future__ import annotations

from typing import Any

from reports import p53_osv_upstream_remediation_lifecycle_build as p53


def _artifact(artifact_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return p53._artifact(
        artifact_id=artifact_id,
        kind=kind,
        payload=payload,
        source_url="https://example.invalid/source",
        source_sha256="a" * 64,
        source_record_id=artifact_id,
        license_id="MIT",
        license_source_url="https://example.invalid/license",
        occurred_at="2024-01-01T00:00:00Z",
    )


def _case() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    withdrawn = _artifact(
        "withdrawn",
        "withdrawn_advisory",
        {
            "withdrawn": "2024-01-01T00:00:00Z",
            "details": "Withdrawn as duplicate of ACTIVE-1",
            "aliases": ["CVE-2024-0001"],
        },
    )
    active = _artifact(
        "active",
        "active_advisory",
        {
            "aliases": ["CVE-2024-0001"],
            "affected": [
                {
                    "package": {"name": "package"},
                    "ranges": [
                        {"type": "GIT", "events": [{"fixed": "b" * 40}]},
                        {"type": "ECOSYSTEM", "events": [{"fixed": "2.0"}]},
                    ],
                }
            ],
            "affected_versions": [{"package": "package", "versions": ["1.0", "1.5"]}],
            "references": [
                {
                    "type": "FIX",
                    "url": f"https://example.invalid/commit/{'b' * 40}",
                }
            ],
        },
    )
    patch = _artifact(
        "patch",
        "upstream_patch",
        {"commit": "b" * 40, "patch": "authentic patch"},
    )
    release = _artifact(
        "release",
        "pypi_release_receipt",
        {"package": "package", "version": "2.0"},
    )
    license_receipt = _artifact(
        "license",
        "upstream_license_receipt",
        {"spdx": "MIT", "license_text": "MIT License"},
    )
    essential = ["withdrawn", "active", "patch", "release", "license"]
    task = {
        "chains": [
            {
                "withdrawn_id": "WITHDRAWN-1",
                "active_id": "ACTIVE-1",
                "package": "package",
                "shared_alias": "CVE-2024-0001",
                "fixed_commit": "b" * 40,
                "fixed_release": "2.0",
                "affected_probe": "1.0",
                "unlisted_probe": "9999.0",
                "license_spdx": "MIT",
                "patch_url": f"https://example.invalid/commit/{'b' * 40}.patch",
                "withdrawn_artifact_id": "withdrawn",
                "active_artifact_id": "active",
                "patch_artifact_id": "patch",
                "release_artifact_id": "release",
                "license_artifact_id": "license",
                "essential_artifact_ids": essential,
            }
        ]
    }
    return [withdrawn, active, patch, release, license_receipt], task


def test_p53_replay_keeps_unlisted_versions_unknown() -> None:
    artifacts, task = _case()

    result = p53.replay(artifacts, task)

    assert "1.0=AFFECTED" in result["answer"]
    assert "2.0=FIXED_BOUNDARY" in result["answer"]
    assert "9999.0=UNKNOWN" in result["answer"]


def test_p53_replay_fails_closed_on_missing_or_conflicting_evidence() -> None:
    artifacts, task = _case()
    missing = p53.replay(artifacts[:-1], task)
    counterfactual = p53._replace_fixed_release(artifacts[1], "2.0")
    cf_task = {
        "chains": [
            {
                **task["chains"][0],
                "essential_artifact_ids": [
                    "active:cf" if item == "active" else item
                    for item in task["chains"][0]["essential_artifact_ids"]
                ],
            }
        ]
    }
    conflict = p53.replay([artifacts[0], counterfactual, *artifacts[2:]], cf_task)

    assert missing == {"answer": "unknown", "proof_depth": 0}
    assert conflict["answer"] == "CONFLICT-package"
    assert conflict["conflict_count"] == 1
