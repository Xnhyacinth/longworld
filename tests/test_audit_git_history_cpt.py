from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_git_history_cpt as audit_module
from audit_git_history_cpt import (
    SourceRecordBinding,
    _canonical_license_policy,
    _cross_release_references,
    _load_reference_closure,
    _validate_license_binding_receipt,
    _validate_retained_count_contract,
    _validate_source_manifest_schema,
    _validate_source_record_binding,
    _validate_source_summary_binding,
    _validate_source_truncation_contract,
    _validate_source_window_binding,
)


def _license_bound_source() -> dict[str, object]:
    policy = {
        "schema_version": "longworld.repo-license-binding-policy.v1",
        "approved_path": "LICENSE.txt",
        "approved_blobs": [
            {"git_blob_sha": "b" * 40, "sha256": "c" * 64, "size": 12},
            {"git_blob_sha": "d" * 40, "sha256": "e" * 64, "size": 13},
        ],
    }
    bindings = [
        {"revision": "1" * 40, "git_blob_sha": "b" * 40},
        {"revision": "2" * 40, "git_blob_sha": "d" * 40},
    ]
    canonical = lambda value: json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "schema_version": "longworld.git-history-source-manifest.v1",
        "revision": "2" * 40,
        "repository_url": "https://github.com/example/repo",
        "license": "MIT",
        "parser": {"revision": "v5"},
        "public_policy": {
            "record_id": "PUBLIC",
            "sha256": "a" * 64,
            "repository_policy_sha256": "8" * 64,
            "license_binding_policy_sha256": hashlib.sha256(
                canonical(policy)
            ).hexdigest(),
        },
        "observed_commit_count": 2,
        "record_index": [
            {"source_event_id": "1" * 40},
            {"source_event_id": "2" * 40},
        ],
        "remote_identity": {
            "repository_response_sha256": "f" * 64,
            "commit_response_sha256": "0" * 64,
            "license_response_sha256": "9" * 64,
            "head_revision": "2" * 40,
            "repository_url": "https://github.com/example/repo",
            "license": "MIT",
            "repository_license_spdx_id": "MIT",
            "license_file_classifier_spdx_id": "NOASSERTION",
            "license_file_revision": "2" * 40,
            "license_file_path": "LICENSE.txt",
            "license_file_git_blob_sha": "d" * 40,
            "license_file_size": 13,
            "license_file_sha256": "e" * 64,
            "license_file_html_url": (
                "https://github.com/example/repo/blob/" + "2" * 40 + "/LICENSE.txt"
            ),
            "license_file_download_url": (
                "https://raw.githubusercontent.com/example/repo/"
                + "2" * 40
                + "/LICENSE.txt"
            ),
        },
        "license_binding": {
            "schema_version": "longworld.git-license-binding-receipt.v2",
            "policy": policy,
            "policy_sha256": hashlib.sha256(canonical(policy)).hexdigest(),
            "selected_commit_count": 2,
            "selected_first_revision": "1" * 40,
            "selected_last_revision": "2" * 40,
            "commit_bindings": bindings,
            "commit_bindings_sha256": hashlib.sha256(canonical(bindings)).hexdigest(),
            "observed_license_blobs": policy["approved_blobs"],
            "transition_count": 1,
        },
    }


def _validate_bound_fixture(source: dict[str, object]) -> str:
    return _validate_license_binding_receipt(
        source,
        canonical_policy=source["license_binding"]["policy"],
        canonical_repository_policy_sha256=source["public_policy"][
            "repository_policy_sha256"
        ],
    )


def test_capacity_scan_accepts_natural_partial_retention() -> None:
    _validate_retained_count_contract(
        {"16k": 73, "256k": 11},
        target={"16k": 1000, "256k": 1000},
        require_full_target=False,
    )


def test_v2_license_binding_replays_policy_history_and_remote_tip() -> None:
    source = _license_bound_source()

    assert _validate_bound_fixture(source) == "git-license-binding-v2"

    source["license_binding"]["commit_bindings_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="commit binding digest"):
        _validate_bound_fixture(source)


def test_v2_license_binding_rejects_policy_or_remote_tampering() -> None:
    source = _license_bound_source()
    source["public_policy"]["license_binding_policy_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="policy binding"):
        _validate_bound_fixture(source)

    source = _license_bound_source()
    source["remote_identity"]["license_file_git_blob_sha"] = "b" * 40
    with pytest.raises(ValueError, match="remote license tip"):
        _validate_bound_fixture(source)


def test_v2_license_binding_rejects_policy_not_pinned_by_canonical_allowlist() -> None:
    source = _license_bound_source()
    canonical_policy = json.loads(json.dumps(source["license_binding"]["policy"]))
    canonical_policy["approved_blobs"][0]["sha256"] = "7" * 64

    with pytest.raises(ValueError, match="canonical allowlist"):
        _validate_license_binding_receipt(
            source,
            canonical_policy=canonical_policy,
            canonical_repository_policy_sha256=source["public_policy"][
                "repository_policy_sha256"
            ],
        )


def test_v2_license_binding_accepts_historical_approved_allowlist_digest(
    monkeypatch,
) -> None:
    source = _license_bound_source()
    source["public_policy"]["sha256"] = "7" * 64
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_PUBLIC_POLICY_SHA256", "7" * 64 + "," + "a" * 64)

    assert _validate_bound_fixture(source) == "git-license-binding-v2"

    source["public_policy"]["sha256"] = "6" * 64
    with pytest.raises(ValueError, match="independently approved"):
        _validate_bound_fixture(source)


def test_canonical_license_policy_requires_current_approval_pin(monkeypatch) -> None:
    raw = audit_module.CANONICAL_ALLOWLIST.read_bytes()
    allowlist = yaml.safe_load(raw)
    policy = allowlist["repositories"]["godotengine/godot"]
    authorization = dict(policy["authorization"])
    reviewed_at = authorization["reviewed_at"]
    if isinstance(reviewed_at, datetime):
        authorization["reviewed_at"] = reviewed_at.isoformat().replace("+00:00", "Z")
    source = {
        "repository_url": "https://github.com/godotengine/godot",
        "license": "MIT",
        "authorization": authorization,
    }
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_PUBLIC_POLICY_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="current canonical allowlist"):
        _canonical_license_policy(source)

    monkeypatch.setenv(
        "LONGWORLD_PUBLIC_POLICY_SHA256", hashlib.sha256(raw).hexdigest()
    )
    binding, digest = _canonical_license_policy(source)
    assert binding == policy["license_binding"]
    assert len(digest) == 64


def test_git_history_source_manifest_schema_is_closed() -> None:
    _validate_source_manifest_schema(
        {"schema_version": "longworld.git-history-source-manifest.v1"}
    )
    with pytest.raises(ValueError, match="source manifest schema"):
        _validate_source_manifest_schema({"schema_version": "future.v2"})


def test_legacy_license_branch_is_explicit_and_never_accepts_noassertion(
    monkeypatch,
) -> None:
    source = _license_bound_source()
    source.pop("license_binding")
    source["parser"]["revision"] = "v4"
    source["remote_identity"] = {
        "repository_response_sha256": "f" * 64,
        "commit_response_sha256": "0" * 64,
        "license_response_sha256": "9" * 64,
        "head_revision": "2" * 40,
        "repository_url": "https://github.com/example/repo",
        "license": "MIT",
    }

    assert _validate_license_binding_receipt(source) == "legacy-remote-head-v1"

    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="production audit requires"):
        _validate_license_binding_receipt(source)
    monkeypatch.delenv("LONGWORLD_ATTESTATION_ENVIRONMENT")

    source["remote_identity"]["license_file_classifier_spdx_id"] = "NOASSERTION"
    with pytest.raises(ValueError, match="NOASSERTION"):
        _validate_license_binding_receipt(source)

    source["remote_identity"].pop("license_file_classifier_spdx_id")
    source["parser"]["revision"] = "v6"
    with pytest.raises(ValueError, match="legacy parser"):
        _validate_license_binding_receipt(source)

    source["parser"]["revision"] = "v5"
    with pytest.raises(ValueError, match="v2 receipt"):
        _validate_license_binding_receipt(source)


def test_quota_mode_rejects_partial_retention() -> None:
    with pytest.raises(ValueError, match="row counts"):
        _validate_retained_count_contract(
            {"16k": 73}, target={"16k": 1000}, require_full_target=True
        )


def test_capacity_scan_never_accepts_rows_above_its_safety_cap() -> None:
    with pytest.raises(ValueError, match="row counts"):
        _validate_retained_count_contract(
            {"16k": 1001}, target={"16k": 1000}, require_full_target=False
        )


def test_cross_release_references_accept_legacy_and_list_contracts() -> None:
    receipt = {
        "path": "data/releases/reference",
        "release_manifest_sha256": "a" * 64,
    }

    assert _cross_release_references({"cross_release_reference": receipt}) == [receipt]
    assert _cross_release_references({"cross_release_references": [receipt]}) == [
        receipt
    ]

    with pytest.raises(ValueError, match="contract"):
        _cross_release_references(
            {
                "cross_release_reference": receipt,
                "cross_release_references": [receipt],
            }
        )


def test_reference_closure_verifies_pins_and_expands_transitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_by_path = {name: name.encode() for name in ("current", "prior", "oldest")}
    digest = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in raw_by_path.items()
    }
    releases = {
        "current": {
            "cross_release_references": [
                {"path": "prior", "release_manifest_sha256": digest["prior"]}
            ]
        },
        "prior": {
            "cross_release_reference": {
                "path": "oldest",
                "release_manifest_sha256": digest["oldest"],
            }
        },
        "oldest": {},
    }

    def fake_load(path: Path):
        name = str(path)
        return releases[name], raw_by_path[name], [], {}, {}, {}

    monkeypatch.setattr(audit_module, "_release_path", Path)
    monkeypatch.setattr(audit_module, "_load_release", fake_load)

    closure = _load_reference_closure(
        [{"path": "current", "release_manifest_sha256": digest["current"]}]
    )

    assert [item[0]["release_manifest_sha256"] for item in closure] == sorted(
        digest.values()
    )

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        _load_reference_closure(
            [{"path": "current", "release_manifest_sha256": "0" * 64}]
        )


def test_reference_closure_rejects_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_by_path = {name: name.encode() for name in ("left", "right")}
    digest = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in raw_by_path.items()
    }
    releases = {
        "left": {
            "cross_release_reference": {
                "path": "right",
                "release_manifest_sha256": digest["right"],
            }
        },
        "right": {
            "cross_release_reference": {
                "path": "left",
                "release_manifest_sha256": digest["left"],
            }
        },
    }

    def fake_load(path: Path):
        name = str(path)
        return releases[name], raw_by_path[name], [], {}, {}, {}

    monkeypatch.setattr(audit_module, "_release_path", Path)
    monkeypatch.setattr(audit_module, "_load_release", fake_load)

    with pytest.raises(ValueError, match="cycle"):
        _load_reference_closure(
            [{"path": "left", "release_manifest_sha256": digest["left"]}]
        )


def test_source_truncation_contract_replays_record_level_omissions() -> None:
    source = {
        "observed_commit_count": 2,
        "reject_reasons": {
            "commits_truncated": 1,
            "commit_chunks_truncated": 3,
        },
        "truncation_quality": {
            "revision": "git-observed-prefix-truncation-v1",
            "observed_commit_count": 2,
            "truncated_commit_count": 1,
            "omitted_chunk_count": 3,
            "truncated_commit_ratio_ppm": 500_000,
            "maximum_truncated_commit_ratio_ppm": 500_000,
            "tier": "within_configured_limit",
        },
        "truncated_commit_index": [
            {
                "source_event_id": "a" * 40,
                "commit_chunk_count_total": 4,
                "commit_chunk_count_emitted": 1,
            }
        ],
        "record_index": [
            {
                "source_event_id": "a" * 40,
                "chunk_index": 0,
                "commit_chunk_count_total": 4,
                "commit_chunk_count_emitted": 1,
                "commit_was_truncated": True,
            },
            {
                "source_event_id": "b" * 40,
                "chunk_index": 0,
                "commit_chunk_count_total": 1,
                "commit_chunk_count_emitted": 1,
                "commit_was_truncated": False,
            },
        ],
    }

    _validate_source_truncation_contract(source, 500_000)
    source["record_index"][0]["commit_chunk_count_total"] = 3
    with pytest.raises(ValueError, match="truncation"):
        _validate_source_truncation_contract(source, 500_000)

    source["record_index"][0]["commit_chunk_count_total"] = 4
    source["record_index"][0]["commit_was_truncated"] = False
    with pytest.raises(ValueError, match="truncation"):
        _validate_source_truncation_contract(source, 500_000)


def test_source_record_timestamp_is_bound_before_span_replay() -> None:
    expected = SourceRecordBinding(
        text_sha256="a" * 64,
        provenance_id="sha256:" + "b" * 64,
        source_event_id="c" * 40,
        occurred_at="2026-01-01T00:00:00Z",
        source_pointer="https://github.com/example/repo/commit/" + "c" * 40,
        predecessor_ids=(),
        ordinal=0,
        manifest_record_count=1,
    )
    record = {
        "record_id": "r0",
        "sha256": "a" * 64,
        "occurred_at": "2026-01-01T00:00:00Z",
        "source_pointer": "https://github.com/example/repo/commit/" + "c" * 40,
        "predecessor_ids": [],
    }

    _validate_source_record_binding(expected, record, "sha256:" + "b" * 64)
    record["occurred_at"] = "2020-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="source record binding"):
        _validate_source_record_binding(expected, record, "sha256:" + "b" * 64)


def test_source_window_binding_rejects_reordered_noncontiguous_or_relinked_rows() -> (
    None
):
    provenance = "sha256:" + "d" * 64
    source_records = [
        SourceRecordBinding(
            text_sha256=str(index) * 64,
            provenance_id=provenance,
            source_event_id=str(index) * 40,
            occurred_at=f"2026-01-0{index + 1}T00:00:00Z",
            source_pointer=f"https://github.com/example/repo/commit/{str(index) * 40}",
            predecessor_ids=(() if index == 0 else (f"r{index - 1}",)),
            ordinal=index,
            manifest_record_count=3,
        )
        for index in range(3)
    ]
    rows = [
        {
            "record_id": f"r{index}",
            "sha256": str(index) * 64,
            "occurred_at": f"2026-01-0{index + 1}T00:00:00Z",
            "source_pointer": f"https://github.com/example/repo/commit/{str(index) * 40}",
            "predecessor_ids": ([] if index == 0 else [f"r{index - 1}"]),
        }
        for index in range(3)
    ]

    _validate_source_window_binding(
        source_records, rows, provenance, source_start_index=0, source_end_index=3
    )
    with pytest.raises(ValueError, match="source window"):
        _validate_source_window_binding(
            [source_records[0], source_records[2]],
            [rows[0], rows[2]],
            provenance,
            source_start_index=0,
            source_end_index=3,
        )
    changed = [dict(row) for row in rows]
    changed[1]["predecessor_ids"] = []
    with pytest.raises(ValueError, match="source record binding"):
        _validate_source_window_binding(
            source_records,
            changed,
            provenance,
            source_start_index=0,
            source_end_index=3,
        )


def test_source_summary_is_bound_to_signed_manifest() -> None:
    source = {
        "repository_url": "https://github.com/example/repo",
        "observed_commit_count": 2,
        "accepted_record_count": 3,
        "reject_reasons": {"commits_truncated": 1},
        "truncation_quality": {"tier": "within_configured_limit"},
        "parser": {"skip_commits": 10},
    }
    summary = {
        "repository": "example/repo",
        "commit_count": 2,
        "record_count": 3,
        "reject_reasons": {"commits_truncated": 1},
        "truncation_quality": {"tier": "within_configured_limit"},
        "skip_commits": 10,
    }

    _validate_source_summary_binding(summary, source)
    summary["truncation_quality"] = {"tier": "complete"}
    with pytest.raises(ValueError, match="summary"):
        _validate_source_summary_binding(summary, source)
