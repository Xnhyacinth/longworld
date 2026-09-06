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
from longworld.core.standardsworkflow import (
    build_ietf_cross_spec_requirement_task,
    replay_ietf_cross_spec_requirement_task,
)
from longworld.core.taskreplaysidecar import (
    IETF_OAUTH_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_replay_sidecar_binding,
)
from tests.test_ietf_cross_spec_requirement import _oauth_manifest

SOURCE_KEY = b"ietf-cross-spec-sidecar-source-key-v1"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def test_serializes_loads_and_replays_source_bound_ietf_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-ietf-sidecar-v1")
    task = build_ietf_cross_spec_requirement_task(_oauth_manifest(tmp_path))
    adapter_id, adapter_revision, schema_version = IETF_OAUTH_TASK_REPLAY_ADAPTER
    signed = build_task_replay_sidecar(
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        sidecar_schema_version=schema_version,
        replay_payload={
            "source_manifest_sha256": task["source_manifest_sha256"],
            "fetch_inventory_sha256": task["source_manifest"]["fetch_inventory_sha256"],
            "authorization_record_id": task["source_manifest"]["authorization"][
                "record_id"
            ],
            "ietf_requirement_task": task,
            "task_sha256": _canonical_sha256(task),
            "replay_revision": adapter_revision,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": "d" * 40,
            "tokenizer_asset_manifest_sha256": "e" * 64,
            "candidate_content_commitments": [
                {
                    "world_id": "ietf-oauth-world",
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
    path = tmp_path / "ietf-replay-sidecar.json"
    path.write_bytes(raw)
    binding = task_replay_sidecar_binding(raw, source_attestation_key=SOURCE_KEY)

    loaded = load_task_replay_sidecar(
        tmp_path,
        path.name,
        binding,
        source_attestation_key=SOURCE_KEY,
    )

    assert loaded.registry_key == IETF_OAUTH_TASK_REPLAY_ADAPTER
    assert (
        replay_ietf_cross_spec_requirement_task(
            loaded.replay_payload["ietf_requirement_task"]
        )
        == task["answer"]
    )
