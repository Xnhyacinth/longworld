from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    LOCAL_PROBE_COMBINED_ROLES_ENV,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    verify_attestation,
)
from longworld.core.gitcache import (
    MAX_REMOTE_IDENTITY_RECEIPT_BYTES,
    PACKING_PLAN_ATTESTATION_PURPOSE,
    REFERENCE_INDEX_ATTESTATION_PURPOSE,
    REMOTE_IDENTITY_ATTESTATION_PURPOSE,
    REMOTE_IDENTITY_VALIDITY_SECONDS,
    GitCacheError,
    packing_plan_cache_key,
    read_packing_plan,
    read_reference_index,
    read_remote_identity_receipt,
    reference_audited_identity,
    reference_index_cache_key,
    sign_packing_plan,
    sign_reference_index,
    sign_remote_identity_receipt,
    verify_packing_plan,
    verify_reference_index,
    verify_remote_identity_receipt,
)


def _activate_role(monkeypatch: pytest.MonkeyPatch, role: str, *, key_id: str) -> bytes:
    key = f"{role}-role-git-cache-test-key-00000001".encode()
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], key_id)
    return key


def _remote_request() -> dict[str, Any]:
    repository = "example/repo"
    head = "a" * 40
    return {
        "repository": repository,
        "head_revision": head,
        "license": "MIT",
        "license_binding_policy_sha256": "b" * 64,
        "source_client": {"path": "/usr/bin/gh", "sha256": "c" * 64},
        "request_operations": [
            {
                "method": "GET",
                "hostname": "github.com",
                "endpoint": f"repos/{repository}",
            },
            {
                "method": "GET",
                "hostname": "github.com",
                "endpoint": f"repos/{repository}/commits/{head}",
            },
            {
                "method": "GET",
                "hostname": "github.com",
                "endpoint": f"repos/{repository}/license?ref={head}",
            },
        ],
    }


def _remote_identity() -> dict[str, Any]:
    head = "a" * 40
    repository_url = "https://github.com/example/repo"
    return {
        "repository_response_sha256": "d" * 64,
        "commit_response_sha256": "e" * 64,
        "license_response_sha256": "f" * 64,
        "head_revision": head,
        "repository_url": repository_url,
        "license": "MIT",
        "repository_license_spdx_id": "MIT",
        "license_file_classifier_spdx_id": "NOASSERTION",
        "license_file_revision": head,
        "license_file_path": "LICENSE.txt",
        "license_file_git_blob_sha": "1" * 40,
        "license_file_size": 12,
        "license_file_sha256": "2" * 64,
        "license_file_html_url": f"{repository_url}/blob/{head}/LICENSE.txt",
        "license_file_download_url": (
            f"https://raw.githubusercontent.com/example/repo/{head}/LICENSE.txt"
        ),
    }


def test_remote_identity_receipt_binds_source_role_identity_and_fixed_validity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _activate_role(monkeypatch, "source", key_id="probe-source-cache-v1")
    checked_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    request = _remote_request()

    receipt = sign_remote_identity_receipt(
        request_identity=request,
        remote_identity=_remote_identity(),
        checked_at=checked_at,
        key=key,
    )

    verified = verify_remote_identity_receipt(
        receipt,
        expected_request_identity=_remote_request(),
        exported_at="2026-09-01T12:00:00Z",
        key=key,
    )
    assert verified["remote_identity"] == _remote_identity()
    assert verified["request_identity"] == _remote_request()
    assert len(verified["receipt_sha256"]) == 64
    assert receipt["checked_at"] == "2026-09-01T00:00:00Z"
    assert receipt["expires_at"] == "2026-09-02T00:00:00Z"
    assert REMOTE_IDENTITY_VALIDITY_SECONDS == 86_400
    assert receipt["attestation"]["role"] == "source"
    assert verify_attestation(receipt, key, purpose=REMOTE_IDENTITY_ATTESTATION_PURPOSE)
    assert not verify_attestation(receipt, key, purpose="artifact_semantics")
    request["source_client"]["sha256"] = "9" * 64
    verified["request_identity"]["source_client"]["sha256"] = "8" * 64
    assert receipt["request_identity"]["source_client"]["sha256"] == "c" * 64


def test_contract_accepts_signed_local_probe_combined_role_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _activate_role(monkeypatch, "source", key_id="probe-source-cache-v1")
    monkeypatch.setenv(
        LOCAL_PROBE_COMBINED_ROLES_ENV, LOCAL_PROBE_TRUST_ISOLATION_VALUE
    )
    receipt = sign_remote_identity_receipt(
        request_identity=_remote_request(),
        remote_identity=_remote_identity(),
        checked_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        key=key,
    )

    assert (
        verify_remote_identity_receipt(
            receipt,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )["remote_identity"]["head_revision"]
        == "a" * 40
    )
    assert receipt["local_probe_trust_isolation"] == (LOCAL_PROBE_TRUST_ISOLATION_VALUE)


def test_remote_identity_reader_rejects_unsafe_or_noncanonical_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = _activate_role(monkeypatch, "source", key_id="probe-source-cache-v1")
    receipt = sign_remote_identity_receipt(
        request_identity=_remote_request(),
        remote_identity=_remote_identity(),
        checked_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        key=key,
    )
    path = tmp_path / "REMOTE.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    assert (
        read_remote_identity_receipt(
            path,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )["remote_identity"]
        == _remote_identity()
    )

    link = tmp_path / "REMOTE-link.json"
    link.symlink_to(path)
    with pytest.raises(GitCacheError, match="unreadable"):
        read_remote_identity_receipt(
            link,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )

    path.write_text('{"schema_version":"first","schema_version":"second"}')
    with pytest.raises(GitCacheError, match="unreadable"):
        read_remote_identity_receipt(
            path,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )

    path.write_text('{"schema_version":NaN}')
    with pytest.raises(GitCacheError, match="unreadable"):
        read_remote_identity_receipt(
            path,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )

    path.write_bytes(b" " * (MAX_REMOTE_IDENTITY_RECEIPT_BYTES + 1))
    with pytest.raises(GitCacheError, match="unreadable"):
        read_remote_identity_receipt(
            path,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )


def test_remote_identity_receipt_fails_closed_on_tamper_or_stale_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _activate_role(monkeypatch, "source", key_id="probe-source-cache-v1")
    receipt = sign_remote_identity_receipt(
        request_identity=_remote_request(),
        remote_identity=_remote_identity(),
        checked_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        key=key,
    )
    with pytest.raises(GitCacheError, match="stale"):
        verify_remote_identity_receipt(
            receipt,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-02T00:00:01Z",
            key=key,
        )
    with pytest.raises(GitCacheError, match="stale"):
        verify_remote_identity_receipt(
            receipt,
            expected_request_identity=_remote_request(),
            exported_at="2026-08-31T23:59:59Z",
            key=key,
        )

    tampered = deepcopy(receipt)
    tampered["remote_identity"]["license_file_size"] = 13
    with pytest.raises(GitCacheError, match="signature"):
        verify_remote_identity_receipt(
            tampered,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )

    extended_attestation = deepcopy(receipt)
    extended_attestation["attestation"]["unbound_claim"] = True
    with pytest.raises(GitCacheError, match="signature"):
        verify_remote_identity_receipt(
            extended_attestation,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )


def test_remote_identity_receipt_rejects_role_key_id_environment_and_endpoint_misuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _activate_role(monkeypatch, "source", key_id="probe-source-cache-v1")
    receipt = sign_remote_identity_receipt(
        request_identity=_remote_request(),
        remote_identity=_remote_identity(),
        checked_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        key=key,
    )

    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-source-cache-v2")
    with pytest.raises(GitCacheError, match="signature"):
        verify_remote_identity_receipt(
            receipt,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-source-cache-v1")
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    with pytest.raises(GitCacheError, match="signature"):
        verify_remote_identity_receipt(
            receipt,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    with pytest.raises(GitCacheError, match="signature"):
        verify_remote_identity_receipt(
            receipt,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=b"wrong-source-role-key-000000000000",
        )

    wrong_role = deepcopy(receipt)
    wrong_role["attestation"]["role"] = "report"
    with pytest.raises(GitCacheError, match="signature"):
        verify_remote_identity_receipt(
            wrong_role,
            expected_request_identity=_remote_request(),
            exported_at="2026-09-01T12:00:00Z",
            key=key,
        )

    wrong_endpoint = deepcopy(_remote_request())
    wrong_endpoint["request_operations"][2]["endpoint"] += "&stale=true"
    with pytest.raises(GitCacheError, match="request"):
        sign_remote_identity_receipt(
            request_identity=wrong_endpoint,
            remote_identity=_remote_identity(),
            checked_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            key=key,
        )


@pytest.mark.parametrize("license_path", [".", "..", "docs/../LICENSE.txt"])
def test_remote_identity_rejects_dot_path_components(
    monkeypatch: pytest.MonkeyPatch, license_path: str
) -> None:
    key = _activate_role(monkeypatch, "source", key_id="probe-source-cache-v1")
    identity = _remote_identity()
    head = str(identity["head_revision"])
    identity["license_file_path"] = license_path
    identity["license_file_html_url"] = (
        f"https://github.com/example/repo/blob/{head}/{license_path}"
    )
    identity["license_file_download_url"] = (
        f"https://raw.githubusercontent.com/example/repo/{head}/{license_path}"
    )
    with pytest.raises(GitCacheError, match="remote identity"):
        sign_remote_identity_receipt(
            request_identity=_remote_request(),
            remote_identity=identity,
            checked_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            key=key,
        )


def _packing_identity() -> dict[str, Any]:
    return {
        "repository": "example/repo",
        "root_revision": "a" * 40,
        "source_record_count": 40,
        "source_records_sha256": "b" * 64,
        "public_policy_sha256": "c" * 64,
        "history_client_sha256": "d" * 64,
        "remote_identity_receipt_sha256": "e" * 64,
        "requests_sha256": "f" * 64,
        "tokenizer_asset_manifest_sha256": "1" * 64,
        "packer_revision": "disjoint-contiguous-v1",
        "source_event_selector_revision": "git-commit-sha-v1",
    }


def test_packing_plan_is_content_addressed_and_promotion_role_signed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _activate_role(monkeypatch, "promotion", key_id="probe-packing-v1")
    identity = _packing_identity()
    windows = [
        {
            "band": "16k",
            "source_start_index": 2,
            "source_end_index": 28,
            "context_tokens": 16_100,
            "context_sha256": "2" * 64,
            "source_event_ids": ["3" * 40, "4" * 40],
        }
    ]

    plan = sign_packing_plan(
        identity=identity,
        windows=windows,
        next_record_index=30,
        reject_reasons={"short_tail_records": 2},
        key=key,
    )

    verified = verify_packing_plan(
        plan,
        expected_identity=identity,
        key=key,
    )
    assert verified["windows"] == windows
    assert verified["next_record_index"] == 30
    assert plan["identity_sha256"] == packing_plan_cache_key(identity)
    assert plan["attestation"]["role"] == "promotion"
    assert verify_attestation(plan, key, purpose=PACKING_PLAN_ATTESTATION_PURPOSE)
    assert not verify_attestation(plan, key, purpose="cpt_row")
    assert "audit_passed" not in plan


def test_packing_plan_rejects_final_claims_identity_drift_and_file_tamper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = _activate_role(monkeypatch, "promotion", key_id="probe-packing-v1")
    identity = _packing_identity()
    plan = sign_packing_plan(
        identity=identity,
        windows=[
            {
                "band": "16k",
                "source_start_index": 0,
                "source_end_index": 20,
                "context_tokens": 16_000,
                "context_sha256": "2" * 64,
                "source_event_ids": ["3" * 40],
            }
        ],
        next_record_index=20,
        reject_reasons={},
        key=key,
    )
    path = tmp_path / "PACKING.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    assert (
        read_packing_plan(path, expected_identity=identity, key=key)[
            "next_record_index"
        ]
        == 20
    )

    changed_identity = {**identity, "requests_sha256": "9" * 64}
    with pytest.raises(GitCacheError, match="identity does not match"):
        verify_packing_plan(plan, expected_identity=changed_identity, key=key)

    final_claim = deepcopy(plan)
    final_claim["audit_passed"] = True
    with pytest.raises(GitCacheError, match="unexpected fields"):
        verify_packing_plan(final_claim, expected_identity=identity, key=key)

    tampered = deepcopy(plan)
    tampered["windows"][0]["context_tokens"] = 16_001
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(GitCacheError, match="signature"):
        read_packing_plan(path, expected_identity=identity, key=key)


def test_reference_index_binds_release_closure_and_identity_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _activate_role(monkeypatch, "report", key_id="probe-reference-v1")
    closure = ["2" * 64, "1" * 64]
    bodies = ["4" * 64, "3" * 64]
    events = ["8" * 40, "7" * 40]
    contexts = ["6" * 64, "5" * 64]
    audited_identity = reference_audited_identity(
        release_manifest_sha256="0" * 64,
        closure_release_manifest_sha256=closure,
        cpt_rows_sha256="9" * 64,
        cpt_row_count=2,
        source_body_sha256=bodies,
        source_event_ids=events,
        context_sha256=contexts,
    )

    index = sign_reference_index(
        release_manifest_sha256="0" * 64,
        closure_release_manifest_sha256=closure,
        cpt_rows_sha256="9" * 64,
        cpt_row_count=2,
        source_body_sha256=bodies,
        source_event_ids=events,
        context_sha256=contexts,
        key=key,
    )

    verified = verify_reference_index(
        index,
        expected_audited_identity=audited_identity,
        key=key,
    )
    assert verified["source_body_sha256"] == sorted(bodies)
    assert verified["source_event_ids"] == sorted(events)
    assert verified["context_sha256"] == sorted(contexts)
    assert index["index_sha256"] == reference_index_cache_key(index)
    assert index["counts"] == {
        "closure_releases": 2,
        "cpt_rows": 2,
        "source_bodies": 2,
        "source_events": 2,
        "contexts": 2,
    }
    assert index["attestation"]["role"] == "report"
    assert verify_attestation(index, key, purpose=REFERENCE_INDEX_ATTESTATION_PURPOSE)
    assert not verify_attestation(index, key, purpose="quality_report")
    assert "audit_passed" not in index


def test_reference_index_reader_rejects_closure_drift_and_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = _activate_role(monkeypatch, "report", key_id="probe-reference-v1")
    index = sign_reference_index(
        release_manifest_sha256="0" * 64,
        closure_release_manifest_sha256=["1" * 64],
        cpt_rows_sha256="5" * 64,
        cpt_row_count=1,
        source_body_sha256=["2" * 64],
        source_event_ids=["3" * 40],
        context_sha256=["4" * 64],
        key=key,
    )
    path = tmp_path / "REFERENCE.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    audited_identity = reference_audited_identity(
        release_manifest_sha256="0" * 64,
        closure_release_manifest_sha256=["1" * 64],
        cpt_rows_sha256="5" * 64,
        cpt_row_count=1,
        source_body_sha256=["2" * 64],
        source_event_ids=["3" * 40],
        context_sha256=["4" * 64],
    )
    assert (
        read_reference_index(
            path,
            expected_audited_identity=audited_identity,
            key=key,
        )["counts"]["contexts"]
        == 1
    )

    with pytest.raises(GitCacheError, match="identity does not match"):
        changed_identity = {
            **audited_identity,
            "closure_release_manifest_sha256": ["6" * 64],
        }
        verify_reference_index(
            index,
            expected_audited_identity=changed_identity,
            key=key,
        )

    tampered = deepcopy(index)
    tampered["counts"]["contexts"] = 2
    with pytest.raises(GitCacheError, match="signature"):
        verify_reference_index(
            tampered,
            expected_audited_identity=audited_identity,
            key=key,
        )


def test_reference_index_rejects_signed_partial_row_set_against_audited_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _activate_role(monkeypatch, "report", key_id="probe-reference-v1")
    full_bodies = ["1" * 64, "2" * 64]
    events = ["3" * 40, "4" * 40]
    contexts = ["5" * 64, "6" * 64]
    expected = reference_audited_identity(
        release_manifest_sha256="0" * 64,
        closure_release_manifest_sha256=[],
        cpt_rows_sha256="7" * 64,
        cpt_row_count=2,
        source_body_sha256=full_bodies,
        source_event_ids=events,
        context_sha256=contexts,
    )
    signed_partial = sign_reference_index(
        release_manifest_sha256="0" * 64,
        closure_release_manifest_sha256=[],
        cpt_rows_sha256="7" * 64,
        cpt_row_count=2,
        source_body_sha256=full_bodies[:1],
        source_event_ids=events,
        context_sha256=contexts,
        key=key,
    )

    with pytest.raises(GitCacheError, match="identity does not match"):
        verify_reference_index(
            signed_partial,
            expected_audited_identity=expected,
            key=key,
        )

    with pytest.raises(GitCacheError, match="row count"):
        sign_reference_index(
            release_manifest_sha256="0" * 64,
            closure_release_manifest_sha256=[],
            cpt_rows_sha256="7" * 64,
            cpt_row_count=3,
            source_body_sha256=full_bodies,
            source_event_ids=events,
            context_sha256=contexts,
            key=key,
        )
