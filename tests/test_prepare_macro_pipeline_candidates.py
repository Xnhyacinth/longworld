from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from longworld.core import macrovintage as macrovintage_module
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    LOCAL_PROBE_COMBINED_ROLES_ENV,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    verify_attestation,
)
from longworld.core.macrovintage import build_macro_vintage_pipeline_candidates
from longworld.core.macrovintageworkflow import (
    MACRO_PACKING_PLAN_PURPOSE,
    MACRO_REFERENCE_INDEX_PURPOSE,
    MACRO_REMOTE_SOURCE_RECEIPT_PURPOSE,
    sign_macro_remote_source_receipt,
    verify_macro_remote_source_receipt,
)
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE
from longworld.core.taskreplaysidecar import (
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
)
from scripts import materialize_macro_vintage_histories as materialize_module
from scripts import prepare_macro_pipeline_candidates as prepare_module
from scripts.materialize_macro_vintage_histories import (
    _build_source_sidecar,
    materialize,
    materialize_macro_execution_caches,
)
from scripts.prepare_macro_pipeline_candidates import prepare
from tests.test_macro_vintage_pipeline import _token_count, _workflow_manifest
from tests.test_taskpromotion import KEYS


@pytest.fixture(autouse=True)
def _role_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(
        LOCAL_PROBE_COMBINED_ROLES_ENV, LOCAL_PROBE_TRUST_ISOLATION_VALUE
    )
    for role, key in KEYS.items():
        monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"probe-macro-prepare-{role}-v1")


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def test_combined_macro_paths_are_probe_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    monkeypatch.delenv(LOCAL_PROBE_COMBINED_ROLES_ENV)

    with pytest.raises(ValueError, match="local-probe diagnostic only"):
        materialize(tmp_path / "missing-config.json", tmp_path / "materialized")
    with pytest.raises(ValueError, match="local-probe diagnostic only"):
        prepare(tmp_path / "missing-candidates.jsonl", tmp_path / "prepared")


def test_macro_candidate_reader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    path.write_text('{"world_id":"first","world_id":"second"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate key"):
        prepare_module._read_jsonl(path)


def test_macro_remote_receipt_is_source_signed_and_fetch_bound() -> None:
    workflow = _workflow_manifest()
    [candidate] = build_macro_vintage_pipeline_candidates(
        workflow,
        workflow_manifest_sha256="a" * 64,
        world_id="macro-cache-source-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )

    receipt = sign_macro_remote_source_receipt(
        source_binding=candidate["source_binding"],
        fetch_receipt=workflow["fetch_receipt"],
        exported_at=workflow["generated_at"],
        key=KEYS["source"],
    )

    verified = verify_macro_remote_source_receipt(
        receipt,
        expected_source_binding=candidate["source_binding"],
        expected_fetch_receipt=workflow["fetch_receipt"],
        key=KEYS["source"],
    )
    assert receipt["attestation"]["purpose"] == MACRO_REMOTE_SOURCE_RECEIPT_PURPOSE
    assert receipt["attestation"]["role"] == "source"
    assert verified["source_identity"]["raw_source_sha256"] == "b" * 64
    assert "audit_passed" not in receipt
    assert "train_ready" not in receipt

    drifted = json.loads(json.dumps(workflow["fetch_receipt"]))
    drifted["retrieval"]["raw_bytes"] += 1
    with pytest.raises(ValueError, match="identity"):
        verify_macro_remote_source_receipt(
            receipt,
            expected_source_binding=candidate["source_binding"],
            expected_fetch_receipt=drifted,
            key=KEYS["source"],
        )


@pytest.mark.parametrize(
    "exported_at",
    ["2025-12-31T23:59:59Z", "2026-01-02T00:00:01Z"],
)
def test_macro_remote_receipt_rejects_backdated_or_stale_export(
    exported_at: str,
) -> None:
    workflow = _workflow_manifest()
    [candidate] = build_macro_vintage_pipeline_candidates(
        workflow,
        workflow_manifest_sha256="a" * 64,
        world_id="macro-cache-source-time-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )

    with pytest.raises(ValueError, match="stale"):
        sign_macro_remote_source_receipt(
            source_binding=candidate["source_binding"],
            fetch_receipt=workflow["fetch_receipt"],
            exported_at=exported_at,
            key=KEYS["source"],
        )


def test_materializer_writes_role_separated_nonfinal_macro_caches(
    tmp_path: Path,
) -> None:
    workflow = _workflow_manifest()
    [candidate] = build_macro_vintage_pipeline_candidates(
        workflow,
        workflow_manifest_sha256="a" * 64,
        world_id="macro-cache-materialize-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    candidate["source_verified_at_materialization"] = True
    candidate["source_attestation_verified"] = True
    candidate["real_source_verified"] = True
    _sidecar_bytes, sidecar_binding, _registry = _build_source_sidecar(
        [candidate], workflow, KEYS["source"]
    )
    candidate["task_replay_sidecar"] = sidecar_binding

    bindings = materialize_macro_execution_caches(
        [candidate], workflow=workflow, output_dir=tmp_path
    )

    assert set(bindings) == {
        "remote_source_receipt_sha256",
        "packing_plan_sha256",
        "reference_index_sha256",
    }
    expected = {
        "MACRO_REMOTE_SOURCE_RECEIPT.json": (
            MACRO_REMOTE_SOURCE_RECEIPT_PURPOSE,
            "source",
        ),
        "MACRO_PACKING_PLAN.json": (MACRO_PACKING_PLAN_PURPOSE, "promotion"),
        "MACRO_REFERENCE_INDEX.json": (MACRO_REFERENCE_INDEX_PURPOSE, "report"),
    }
    for filename, (purpose, role) in expected.items():
        payload = json.loads((tmp_path / filename).read_text())
        assert payload["attestation"]["purpose"] == purpose
        assert payload["attestation"]["role"] == role
        assert "audit_passed" not in payload
        assert "train_ready" not in payload
        assert "promoted" not in payload


def test_materializer_reuses_verified_plan_but_reaudits_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow_manifest()
    workflow_path = tmp_path / "workflow.json"
    source_path = tmp_path / "source.xlsx"
    config_path = tmp_path / "config.json"
    workflow_path.write_bytes(_canonical_bytes(workflow))
    source_path.write_bytes(b"test source bytes")
    config_path.write_bytes(
        _canonical_bytes(
            {
                "schema_version": "longworld.macro-vintage-materialization.v1",
                "workflow_manifest": str(workflow_path),
                "raw_source": str(source_path),
                "world_id": "macro-cache-hit-test",
                "target_series_id": "BEA_GDP_CURRENT_DOLLARS",
                "target_period": "2020Q1",
                "bands": [
                    {
                        "name": "16k",
                        "lower_tokens": 16_000,
                        "upper_tokens": 16_384,
                    }
                ],
                "tokenizer": {
                    "model_id": "Qwen/Qwen3.5-4B",
                    "revision": "c" * 40,
                },
            }
        )
    )

    class Tokenizer:
        @staticmethod
        def encode(text: str, *, add_special_tokens: bool) -> list[int]:
            assert add_special_tokens is False
            return [0] * _token_count(text)

    monkeypatch.setattr(
        materialize_module, "audit_macro_vintage_workflow_manifest", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(materialize_module, "_load_tokenizer", lambda *_args: Tokenizer())
    monkeypatch.setattr(
        materialize_module,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda *_args: "d" * 64,
    )
    output_dir = tmp_path / "materialized"
    first_report = materialize(config_path, output_dir)
    first_candidates = (output_dir / "candidates.jsonl").read_bytes()

    audit_calls = 0
    original_audit = materialize_module.audit_macro_vintage_pipeline_candidate

    def counting_audit(row: dict[str, object]) -> dict[str, bool]:
        nonlocal audit_calls
        audit_calls += 1
        return original_audit(row)

    monkeypatch.setattr(
        materialize_module, "audit_macro_vintage_pipeline_candidate", counting_audit
    )
    monkeypatch.setattr(
        macrovintage_module,
        "bisect_right",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("packing DP executed on a verified cache hit")
        ),
    )

    second_report = materialize(config_path, output_dir)

    assert first_report["packing_cache_hit"] is False
    assert second_report["packing_cache_hit"] is True
    assert (output_dir / "candidates.jsonl").read_bytes() == first_candidates
    assert audit_calls == second_report["accepted_candidates"]


def test_prepare_rejects_missing_macro_cache_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        prepare_module,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model_id, _revision: "d" * 64,
    )
    workflow = _workflow_manifest()
    [candidate] = build_macro_vintage_pipeline_candidates(
        workflow,
        workflow_manifest_sha256="a" * 64,
        world_id="macro-cache-required-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    candidate["source_verified_at_materialization"] = True
    candidate["source_attestation_verified"] = True
    candidate["real_source_verified"] = True
    sidecar_bytes, _sidecar_binding, _registry = _build_source_sidecar(
        [candidate], workflow, KEYS["source"]
    )
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "candidates.jsonl").write_bytes(_canonical_bytes(candidate))
    (source_dir / "TASK_REPLAY_SIDECAR.json").write_bytes(sidecar_bytes)
    with pytest.raises(ValueError, match="remote source receipt"):
        prepare(source_dir / "candidates.jsonl", tmp_path / "prepared")


def test_prepares_source_bound_macro_candidate_for_dense_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        prepare_module,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model_id, _revision: "d" * 64,
    )
    workflow = _workflow_manifest()
    [candidate] = build_macro_vintage_pipeline_candidates(
        workflow,
        workflow_manifest_sha256="a" * 64,
        world_id="macro-prepare-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    candidate["source_verified_at_materialization"] = True
    candidate["source_attestation_verified"] = True
    candidate["real_source_verified"] = True
    sidecar_bytes, sidecar_binding, _registry_bytes = _build_source_sidecar(
        [candidate], workflow, KEYS["source"]
    )
    assert sidecar_binding["adapter_id"] == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[0]
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "candidates.jsonl").write_bytes(_canonical_bytes(candidate))
    (source_dir / "TASK_REPLAY_SIDECAR.json").write_bytes(sidecar_bytes)
    cache_bindings = materialize_macro_execution_caches(
        [candidate], workflow=workflow, output_dir=source_dir
    )

    report = prepare(source_dir / "candidates.jsonl", tmp_path / "prepared")

    [prepared] = [
        json.loads(line)
        for line in (tmp_path / "prepared" / "candidates.jsonl")
        .read_text()
        .splitlines()
        if line
    ]
    assert verify_attestation(
        prepared,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    assert prepared["task_replay_sidecar"] == report["task_replay_sidecar"]
    assert report["pipeline_stage"] == "dense_ranking_ready"
    assert report["macro_strict_replay_green"] is True
    assert report["cache_bindings"] == cache_bindings
    assert report["cache_restored_final_gate"] is False
    assert report["candidate_audit_recomputed_after_cache_load"] is True
    assert report["train_ready"] is False
    assert {
        "MACRO_REMOTE_SOURCE_RECEIPT.json",
        "MACRO_PACKING_PLAN.json",
        "MACRO_REFERENCE_INDEX.json",
    } <= {path.name for path in (tmp_path / "prepared").iterdir()}


def test_valid_macro_caches_cannot_restore_a_failed_candidate_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        prepare_module,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model_id, _revision: "d" * 64,
    )
    workflow = _workflow_manifest()
    [candidate] = build_macro_vintage_pipeline_candidates(
        workflow,
        workflow_manifest_sha256="a" * 64,
        world_id="macro-cache-no-final-restore-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    candidate["source_verified_at_materialization"] = True
    candidate["source_attestation_verified"] = True
    candidate["real_source_verified"] = True
    sidecar_bytes, _sidecar_binding, _registry = _build_source_sidecar(
        [candidate], workflow, KEYS["source"]
    )
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "candidates.jsonl").write_bytes(_canonical_bytes(candidate))
    (source_dir / "TASK_REPLAY_SIDECAR.json").write_bytes(sidecar_bytes)
    materialize_macro_execution_caches(
        [candidate], workflow=workflow, output_dir=source_dir
    )
    monkeypatch.setattr(
        prepare_module,
        "audit_macro_vintage_pipeline_candidate",
        lambda _row: {"strict_replay": False},
    )

    with pytest.raises(ValueError, match="candidate audit failed"):
        prepare(source_dir / "candidates.jsonl", tmp_path / "prepared")


def test_prepare_rejects_final_claim_smuggled_into_signed_cache_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        prepare_module,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model_id, _revision: "d" * 64,
    )
    workflow = _workflow_manifest()
    [candidate] = build_macro_vintage_pipeline_candidates(
        workflow,
        workflow_manifest_sha256="a" * 64,
        world_id="macro-cache-final-claim-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    candidate["source_verified_at_materialization"] = True
    candidate["source_attestation_verified"] = True
    candidate["real_source_verified"] = True
    sidecar_bytes, _sidecar_binding, _registry = _build_source_sidecar(
        [candidate], workflow, KEYS["source"]
    )
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "candidates.jsonl").write_bytes(_canonical_bytes(candidate))
    (source_dir / "TASK_REPLAY_SIDECAR.json").write_bytes(sidecar_bytes)
    materialize_macro_execution_caches(
        [candidate], workflow=workflow, output_dir=source_dir
    )
    plan_path = source_dir / "MACRO_PACKING_PLAN.json"
    plan = deepcopy(json.loads(plan_path.read_text()))
    plan["audit_passed"] = True
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(ValueError, match="packing plan"):
        prepare(source_dir / "candidates.jsonl", tmp_path / "prepared")
