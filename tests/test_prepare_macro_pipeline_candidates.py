from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    verify_attestation,
)
from longworld.core.macrovintage import build_macro_vintage_pipeline_candidates
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE
from longworld.core.taskreplaysidecar import (
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
)
from scripts import prepare_macro_pipeline_candidates as prepare_module
from scripts.materialize_macro_vintage_histories import _build_source_sidecar
from scripts.prepare_macro_pipeline_candidates import prepare
from tests.test_macro_vintage_pipeline import _token_count, _workflow_manifest
from tests.test_taskpromotion import KEYS


@pytest.fixture(autouse=True)
def _role_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    for role, key in KEYS.items():
        monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"probe-macro-prepare-{role}-v1")


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


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
    assert report["train_ready"] is False
