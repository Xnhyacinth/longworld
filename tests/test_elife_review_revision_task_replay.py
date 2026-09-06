from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)
from longworld.core.documentworkflow import (
    build_elife_review_revision_inventory,
    replay_elife_review_revision_task,
)
from longworld.core.taskreplaysidecar import (
    ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_replay_sidecar_binding,
)
from tests.test_elife_review_revision_workflow import _request, _xml_versions

SOURCE_KEY = b"elife-review-replay-source-key-v1"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def test_elife_sidecar_binds_privacy_safe_source_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-elife-sidecar-v1")
    v1, v2, review, response = _xml_versions()
    v1 = v1.replace(
        b"</article-meta>",
        b"<email>first.v1@example.org</email><email>second.v1@example.org</email>"
        b"<email>third.v1@example.org</email></article-meta>",
    )
    v2 = v2.replace(
        b"</article-meta>",
        b"<email>first.v2@example.org</email><email>second.v2@example.org</email>"
        b"<email>third.v2@example.org</email></article-meta>",
    )
    inventory = build_elife_review_revision_inventory(
        _request(v1, v2, review, response),
        {1: v1, 2: v2},
        generated_at="2026-09-03T03:00:00Z",
    )
    inventory_raw = (
        json.dumps(
            inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )
    source_inventory_sha256 = hashlib.sha256(inventory_raw).hexdigest()
    source_record_bindings = [
        {
            "record_id": record["record_id"],
            "raw_source_sha256": record["source_sha256"],
            "redacted_text_sha256": record["text_sha256"],
            "email_redaction_count": record["privacy_review"]["email_redaction_count"],
        }
        for record in inventory["records"]
    ]
    adapter_id, adapter_revision, schema_version = (
        ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER
    )
    signed = build_task_replay_sidecar(
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        sidecar_schema_version=schema_version,
        replay_payload={
            "source_inventory_sha256": source_inventory_sha256,
            "authorization_record_id": inventory["authorization"]["record_id"],
            "source_record_bindings": source_record_bindings,
            "email_redaction_receipt": {
                "replacement": "[redacted-email]",
                "total": sum(
                    item["email_redaction_count"] for item in source_record_bindings
                ),
            },
            "relation_kinds": sorted(
                relation["kind"] for relation in inventory["relations"]
            ),
            "elife_review_revision_task": inventory["task"],
            "task_sha256": _canonical_sha256(inventory["task"]),
            "replay_revision": adapter_revision,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": "d" * 40,
            "tokenizer_asset_manifest_sha256": "e" * 64,
            "candidate_content_commitments": [
                {
                    "world_id": "elife-94586-review-revision-world",
                    "length_bucket": "64k",
                    "content_sha256": "f" * 64,
                }
            ],
        },
        source_attestation_key=SOURCE_KEY,
    )
    raw = (
        json.dumps(
            signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )
    path = tmp_path / "elife-review-replay.json"
    path.write_bytes(raw)
    binding = task_replay_sidecar_binding(raw, source_attestation_key=SOURCE_KEY)

    loaded = load_task_replay_sidecar(
        tmp_path,
        path.name,
        binding,
        source_attestation_key=SOURCE_KEY,
    )

    assert loaded.registry_key == ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER
    assert loaded.replay_payload["source_inventory_sha256"] == source_inventory_sha256
    assert loaded.replay_payload["source_record_bindings"] == source_record_bindings
    assert loaded.replay_payload["email_redaction_receipt"]["total"] == 6
    assert loaded.replay_payload["relation_kinds"] == [
        "implements_revision_delta",
        "requests_revision",
        "responds_to_review",
        "revision_of",
    ]
    assert loaded.replay_payload["elife_review_revision_task"] == inventory["task"]
    assert loaded.replay_payload["task_sha256"] == _canonical_sha256(inventory["task"])
    assert loaded.replay_payload["tokenizer_model_id"] == "Qwen/Qwen3.5-4B"
    assert loaded.replay_payload["tokenizer_revision"] == "d" * 40
    assert loaded.replay_payload["tokenizer_asset_manifest_sha256"] == "e" * 64
    assert loaded.replay_payload["candidate_content_commitments"] == [
        {
            "world_id": "elife-94586-review-revision-world",
            "length_bucket": "64k",
            "content_sha256": "f" * 64,
        }
    ]
    assert (
        replay_elife_review_revision_task(
            {"task": loaded.replay_payload["elife_review_revision_task"]}
        )
        == inventory["task"]["answer"]
    )
