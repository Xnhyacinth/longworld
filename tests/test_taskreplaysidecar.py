from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.record_contract import replay_bundle_binding_valid
from longworld.core.taskreplaysidecar import (
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER_V2,
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER_V3,
    CYBER_KEV_TASK_REPLAY_ADAPTER,
    CYBER_KEV_TASK_REPLAY_ADAPTER_V2,
    CYBER_KEV_TASK_REPLAY_ADAPTER_V3,
    ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER,
    EURLEX_PMS_TASK_REPLAY_ADAPTER,
    EURLEX_PMS_TASK_REPLAY_ADAPTER_V3,
    FINANCE_TASK_REPLAY_ADAPTER,
    FINANCE_TASK_REPLAY_ADAPTER_V2,
    FINANCE_TASK_REPLAY_ADAPTER_V3,
    GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER,
    GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER_V3,
    IETF_HTTP3_QUIC_TASK_REPLAY_ADAPTER,
    IETF_HTTP3_QUIC_TASK_REPLAY_ADAPTER_V3,
    IETF_OAUTH_TASK_REPLAY_ADAPTER,
    IETF_OAUTH_TASK_REPLAY_ADAPTER_V3,
    IETF_TLS13_HANDSHAKE_TASK_REPLAY_ADAPTER,
    IETF_TLS13_HANDSHAKE_TASK_REPLAY_ADAPTER_V3,
    IETF_HTTP_SEMANTICS_TASK_REPLAY_ADAPTER,
    IETF_HTTP_SEMANTICS_TASK_REPLAY_ADAPTER_V3,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER_V2,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER_V3,
    TASK_REPLAY_ADAPTER_REGISTRY,
    TASK_REPLAY_SIDECAR_SCHEMA,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)

SOURCE_KEY = b"task-replay-sidecar-source-key-material-v1"
WRONG_KEY = b"task-replay-sidecar-wrong-key-material-v1"


@pytest.fixture(autouse=True)
def _source_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-task-replay-source-v1")


def _signed_sidecar(
    adapter: tuple[str, str, str] = CYBER_KEV_TASK_REPLAY_ADAPTER,
    *,
    key: bytes = SOURCE_KEY,
) -> dict:
    adapter_id, adapter_revision, sidecar_schema_version = adapter
    assert sidecar_schema_version == TASK_REPLAY_SIDECAR_SCHEMA
    if adapter == CYBER_KEV_TASK_REPLAY_ADAPTER:
        replay_payload = {
            "source_manifest_sha256": "a" * 64,
            "source_response_sha256": "b" * 64,
            "replay_manifest_sha256": "c" * 64,
            "replay_revision": adapter_revision,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": "d" * 40,
            "tokenizer_asset_manifest_sha256": "e" * 64,
            "candidate_content_commitments": [
                {
                    "world_id": "test-world",
                    "length_bucket": "16k",
                    "content_sha256": "f" * 64,
                }
            ],
        }
    elif adapter == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER:
        replay_payload = {
            "source_manifest_sha256": "a" * 64,
            "fetch_inventory_sha256": "b" * 64,
            "authorization_record_id": "public-cross-cve-test",
            "replay_revision": adapter_revision,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": "d" * 40,
            "tokenizer_asset_manifest_sha256": "e" * 64,
            "candidate_content_commitments": [
                {
                    "world_id": "test-world",
                    "length_bucket": "16k",
                    "content_sha256": "f" * 64,
                }
            ],
        }
    elif adapter == FINANCE_TASK_REPLAY_ADAPTER:
        replay_payload = {
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_owned_sec_filing",
            "authorization_record_id": "amazon-ir-public-v1",
            "replay_revision": adapter_revision,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": "d" * 40,
            "tokenizer_asset_manifest_sha256": "e" * 64,
            "candidate_content_commitments": [
                {
                    "world_id": "test-world",
                    "length_bucket": "16k",
                    "content_sha256": "f" * 64,
                }
            ],
        }
    else:
        assert adapter == MACRO_VINTAGE_TASK_REPLAY_ADAPTER
        replay_payload = {
            "workflow_manifest_sha256": "a" * 64,
            "raw_source_sha256": "b" * 64,
            "fetch_inventory_sha256": "c" * 64,
            "fetch_receipt": {
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:00:01Z",
                "retrieval": {
                    "requested_url": "https://apps.bea.gov/source.xlsx",
                    "final_url": "https://apps.bea.gov/source.xlsx",
                    "status": 200,
                    "raw_bytes": 1_024,
                    "sha256": "b" * 64,
                },
            },
            "source_families": ["bea_nipa_fixed_xlsx"],
            "authorization_record_id": "bea-public-download-v1",
            "replay_revision": adapter_revision,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": "d" * 40,
            "tokenizer_asset_manifest_sha256": "e" * 64,
            "candidate_content_commitments": [
                {
                    "world_id": "test-world",
                    "length_bucket": "16k",
                    "content_sha256": "f" * 64,
                }
            ],
        }
    return build_task_replay_sidecar(
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        replay_payload=replay_payload,
        source_attestation_key=key,
    )


def _write_sidecar(
    root: Path,
    *,
    adapter: tuple[str, str, str] = CYBER_KEV_TASK_REPLAY_ADAPTER,
    key: bytes = SOURCE_KEY,
    relative_path: str = "sidecars/replay.json",
) -> tuple[dict[str, str], bytes]:
    raw = (
        json.dumps(
            _signed_sidecar(adapter, key=key),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    path = root.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    adapter_id, adapter_revision, sidecar_schema_version = adapter
    return (
        {
            "adapter_id": adapter_id,
            "adapter_revision": adapter_revision,
            "sidecar_schema_version": sidecar_schema_version,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        raw,
    )


@pytest.mark.parametrize(
    "adapter",
    (
        CYBER_KEV_TASK_REPLAY_ADAPTER,
        CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
        FINANCE_TASK_REPLAY_ADAPTER,
        MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    ),
)
def test_loads_exact_source_attested_registered_sidecar(
    tmp_path: Path, adapter: tuple[str, str, str]
) -> None:
    binding, raw = _write_sidecar(tmp_path, adapter=adapter)

    loaded = load_task_replay_sidecar(
        tmp_path,
        "sidecars/replay.json",
        binding,
        source_attestation_key=SOURCE_KEY,
    )

    assert loaded.registry_key == adapter
    assert loaded.relative_path == "sidecars/replay.json"
    assert loaded.sidecar_sha256 == hashlib.sha256(raw).hexdigest()
    assert loaded.raw_bytes == raw
    assert loaded.replay_payload == _signed_sidecar(adapter)["replay_payload"]
    assert loaded.signed_sidecar["adapter_id"] == loaded.adapter_id
    assert (
        task_replay_sidecar_binding(raw, source_attestation_key=SOURCE_KEY) == binding
    )


def test_builder_requires_source_role_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ATTESTATION_ENVIRONMENT_ENV)
    monkeypatch.delenv(ROLE_KEY_ENVS["source"])
    monkeypatch.delenv(ROLE_KEY_ID_ENVS["source"])

    with pytest.raises(ProvenanceError, match="source-role identity"):
        _signed_sidecar()


def test_adapter_payload_schemas_are_closed_and_distinct() -> None:
    cyber = _signed_sidecar(CYBER_KEV_TASK_REPLAY_ADAPTER)
    cross_cve = _signed_sidecar(CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER)
    finance = _signed_sidecar(FINANCE_TASK_REPLAY_ADAPTER)
    macro = _signed_sidecar(MACRO_VINTAGE_TASK_REPLAY_ADAPTER)
    assert (
        len(
            {
                frozenset(cyber["replay_payload"]),
                frozenset(cross_cve["replay_payload"]),
                frozenset(finance["replay_payload"]),
                frozenset(macro["replay_payload"]),
            }
        )
        == 4
    )

    for adapter, wrong_payload in (
        (CYBER_KEV_TASK_REPLAY_ADAPTER, finance["replay_payload"]),
        (CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER, cyber["replay_payload"]),
        (FINANCE_TASK_REPLAY_ADAPTER, cyber["replay_payload"]),
        (MACRO_VINTAGE_TASK_REPLAY_ADAPTER, finance["replay_payload"]),
    ):
        with pytest.raises(ProvenanceError, match="payload"):
            build_task_replay_sidecar(
                adapter_id=adapter[0],
                adapter_revision=adapter[1],
                replay_payload=wrong_payload,
                source_attestation_key=SOURCE_KEY,
            )

    extra = dict(cyber["replay_payload"], unexpected="not-closed")
    with pytest.raises(ProvenanceError, match="payload"):
        build_task_replay_sidecar(
            adapter_id=CYBER_KEV_TASK_REPLAY_ADAPTER[0],
            adapter_revision=CYBER_KEV_TASK_REPLAY_ADAPTER[1],
            replay_payload=extra,
            source_attestation_key=SOURCE_KEY,
        )


def test_candidate_content_commitment_binds_body_answer_and_relations() -> None:
    candidate = {
        "world_id": "world-a",
        "length_bucket": "16k",
        "document_context": "real body",
        "answer": "derived answer",
        "source_relation_ids": ["relation-a"],
        "task_replay_sidecar": {"sha256": "a" * 64},
        "attestation": {"digest": "b" * 64},
    }
    baseline = task_candidate_content_commitment(candidate)

    for field, value in (
        ("document_context", "forged body"),
        ("answer", "forged answer"),
        ("source_relation_ids", ["relation-forged"]),
    ):
        changed = dict(candidate)
        changed[field] = value
        assert task_candidate_content_commitment(changed) != baseline

    trust_only = dict(candidate)
    trust_only["task_replay_sidecar"] = {"sha256": "c" * 64}
    trust_only["attestation"] = {"digest": "d" * 64}
    trust_only["local_probe_trust_isolation"] = "non_independent_local_diagnostic"
    assert task_candidate_content_commitment(trust_only) == baseline


def test_registry_is_closed_over_adapter_revision_and_schema(tmp_path: Path) -> None:
    assert set(TASK_REPLAY_ADAPTER_REGISTRY) == {
        CYBER_KEV_TASK_REPLAY_ADAPTER,
        CYBER_KEV_TASK_REPLAY_ADAPTER_V2,
        CYBER_KEV_TASK_REPLAY_ADAPTER_V3,
        CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
        CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER_V2,
        CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER_V3,
        FINANCE_TASK_REPLAY_ADAPTER,
        FINANCE_TASK_REPLAY_ADAPTER_V2,
        FINANCE_TASK_REPLAY_ADAPTER_V3,
        MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
        MACRO_VINTAGE_TASK_REPLAY_ADAPTER_V2,
        MACRO_VINTAGE_TASK_REPLAY_ADAPTER_V3,
        IETF_OAUTH_TASK_REPLAY_ADAPTER,
        IETF_OAUTH_TASK_REPLAY_ADAPTER_V3,
        IETF_HTTP3_QUIC_TASK_REPLAY_ADAPTER,
        IETF_HTTP3_QUIC_TASK_REPLAY_ADAPTER_V3,
        IETF_TLS13_HANDSHAKE_TASK_REPLAY_ADAPTER,
        IETF_TLS13_HANDSHAKE_TASK_REPLAY_ADAPTER_V3,
        IETF_HTTP_SEMANTICS_TASK_REPLAY_ADAPTER,
        IETF_HTTP_SEMANTICS_TASK_REPLAY_ADAPTER_V3,
        ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER,
        GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER,
        GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER_V3,
        EURLEX_PMS_TASK_REPLAY_ADAPTER,
        EURLEX_PMS_TASK_REPLAY_ADAPTER_V3,
    }
    binding, _ = _write_sidecar(tmp_path, adapter=FINANCE_TASK_REPLAY_ADAPTER)
    binding["adapter_revision"] = "longworld.financial-history-replay.v999"

    with pytest.raises(ProvenanceError, match="not registered"):
        load_task_replay_sidecar(
            tmp_path,
            "sidecars/replay.json",
            binding,
            source_attestation_key=SOURCE_KEY,
        )


def test_binding_uses_exact_serialized_bytes(tmp_path: Path) -> None:
    binding, raw = _write_sidecar(tmp_path)
    (tmp_path / "sidecars" / "replay.json").write_bytes(raw + b" ")

    with pytest.raises(ProvenanceError, match="digest"):
        load_task_replay_sidecar(
            tmp_path,
            "sidecars/replay.json",
            binding,
            source_attestation_key=SOURCE_KEY,
        )


def test_rejects_non_source_role_or_retired_source_identity(tmp_path: Path) -> None:
    with pytest.raises(ProvenanceError, match="source-role identity"):
        _write_sidecar(tmp_path, key=WRONG_KEY)

    binding, _ = _write_sidecar(tmp_path)
    with pytest.raises(ProvenanceError, match="source-role attestation"):
        load_task_replay_sidecar(
            tmp_path,
            "sidecars/replay.json",
            binding,
            source_attestation_key=WRONG_KEY,
        )

    binding, _ = _write_sidecar(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-task-replay-source-v2")
        with pytest.raises(ProvenanceError, match="source-role attestation"):
            load_task_replay_sidecar(
                tmp_path,
                "sidecars/replay.json",
                binding,
                source_attestation_key=SOURCE_KEY,
            )


@pytest.mark.parametrize(
    "relative_path",
    (
        "../replay.json",
        "/tmp/replay.json",
        "sidecars/../replay.json",
        "sidecars\\replay.json",
        "sidecars/*.json",
    ),
)
def test_rejects_unsafe_binding_paths(tmp_path: Path, relative_path: str) -> None:
    binding, _ = _write_sidecar(tmp_path)
    with pytest.raises(ProvenanceError, match="path is unsafe"):
        load_task_replay_sidecar(
            tmp_path,
            relative_path,
            binding,
            source_attestation_key=SOURCE_KEY,
        )


def test_rejects_symlinked_root_parent_or_file(tmp_path: Path) -> None:
    real_root = tmp_path / "real-root"
    binding, _ = _write_sidecar(real_root)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(ProvenanceError, match="cannot read"):
        load_task_replay_sidecar(
            linked_root,
            "sidecars/replay.json",
            binding,
            source_attestation_key=SOURCE_KEY,
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "replay.json").write_bytes(
        (real_root / "sidecars" / "replay.json").read_bytes()
    )
    alias_root = tmp_path / "alias-root"
    alias_root.mkdir()
    (alias_root / "sidecars").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProvenanceError, match="cannot read"):
        load_task_replay_sidecar(
            alias_root,
            "sidecars/replay.json",
            binding,
            source_attestation_key=SOURCE_KEY,
        )

    file_root = tmp_path / "file-root"
    file_binding, _ = _write_sidecar(file_root)
    target = file_root / "sidecars" / "target.json"
    (file_root / "sidecars" / "replay.json").replace(target)
    (file_root / "sidecars" / "replay.json").symlink_to(target)
    with pytest.raises(ProvenanceError, match="cannot read"):
        load_task_replay_sidecar(
            file_root,
            "sidecars/replay.json",
            file_binding,
            source_attestation_key=SOURCE_KEY,
        )


def test_sidecar_identity_must_match_the_registered_binding(tmp_path: Path) -> None:
    binding, raw = _write_sidecar(tmp_path, adapter=FINANCE_TASK_REPLAY_ADAPTER)
    binding.update(
        {
            "adapter_id": CYBER_KEV_TASK_REPLAY_ADAPTER[0],
            "adapter_revision": CYBER_KEV_TASK_REPLAY_ADAPTER[1],
            "sidecar_schema_version": CYBER_KEV_TASK_REPLAY_ADAPTER[2],
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    )

    with pytest.raises(ProvenanceError, match="identity"):
        load_task_replay_sidecar(
            tmp_path,
            "sidecars/replay.json",
            binding,
            source_attestation_key=SOURCE_KEY,
        )


def test_training_row_contract_accepts_exactly_one_task_sidecar_binding(
    tmp_path: Path,
) -> None:
    binding, _ = _write_sidecar(tmp_path)
    row = {
        "task_replay_sidecar": binding,
        "promotion": {"task_replay_sidecar": dict(binding)},
    }

    assert replay_bundle_binding_valid(row)

    row["episode_replay_bundle"] = {
        "schema_version": "longworld.episode-replay-bundle.v1",
        "sha256": "e" * 64,
        "composition": "chronological_causal_union",
    }
    assert not replay_bundle_binding_valid(row)

    row.pop("episode_replay_bundle")
    row["promotion"]["task_replay_sidecar"]["sha256"] = "f" * 64
    assert not replay_bundle_binding_valid(row)


def test_replay_registry_v2_routes_portable_task_sidecar_binding(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import (
        _candidate_replay_paths,
        _candidate_task_replay_path,
        _load_replay_registry,
    )

    binding, _ = _write_sidecar(tmp_path)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v2",
                "episode_replay_bundles": {},
                "source_workflow_bundles": {},
                "task_replay_sidecars": {binding["sha256"]: "sidecars/replay.json"},
            }
        ),
        encoding="utf-8",
    )
    registry = _load_replay_registry(registry_path)
    candidate = {"task_replay_sidecar": binding}

    assert _candidate_replay_paths(candidate, None, None, registry) == (None, None)
    assert (
        _candidate_task_replay_path(candidate, registry)
        == (tmp_path / "sidecars" / "replay.json").absolute()
    )

    candidate["source_workflow_bundle"] = {"sha256": "a" * 64}
    with pytest.raises(Exception, match="cannot bind two"):
        _candidate_replay_paths(candidate, None, None, registry)


def test_replay_registry_v2_canonicalizes_task_sidecar_parent_symlink(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import _load_replay_registry

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    sidecar_path = real_directory / "replay.json"
    sidecar_path.write_text("{}", encoding="utf-8")
    (tmp_path / "linked").symlink_to(real_directory, target_is_directory=True)
    digest = "a" * 64
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v2",
                "episode_replay_bundles": {},
                "source_workflow_bundles": {},
                "task_replay_sidecars": {digest: "linked/replay.json"},
            }
        ),
        encoding="utf-8",
    )

    registry = _load_replay_registry(registry_path)

    assert registry["task_replay_sidecars"][digest] == sidecar_path.resolve()
