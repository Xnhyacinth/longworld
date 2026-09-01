from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENV,
    ATTESTATION_ENVIRONMENT_ENV,
    LOCAL_PROBE_COMBINED_ROLES_ENV,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
    attestation_key_from_env,
)
from longworld.core.record_contract import (
    STRICT_REPLAY_REVISION,
    exact_token_metadata_valid,
)
from longworld.core.release_profile import release_profile, release_profile_sha256
from longworld.core.training_manifest import (
    LLAMAFACTORY_SHAREGPT_SYSTEM_V4,
    LLAMAFACTORY_SHAREGPT_TRANSFORM_V4,
    create_training_manifest,
    file_sha256,
    resolve_training_manifest_path,
    validate_deterministic_training_transform,
    validate_training_manifest,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import quality_gate
from export_llamafactory import validate_release_transform
from quality_gate import (
    load_and_create_release_gate_receipt,
    validate_gate_receipt_output,
)

KEY = b"longworld-training-manifest-test-key-32-bytes"


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, KEY.decode())


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _source_digests(source: Path) -> dict[str, str]:
    return {
        name: file_sha256(source / name)
        for name in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }


def test_inrepo_single_gpu_trainer_is_explicitly_unsigned_diagnostic_only() -> None:
    source = (SCRIPTS / "train_sft.py").read_text(encoding="utf-8")

    assert 'ap.add_argument("--diagnostic-only", action="store_true")' in source
    assert "--diagnostic-only is required" in source
    assert "create_training_manifest" not in source
    assert "validate_training_manifest" not in source


def test_loaded_release_metrics_feed_the_gate_receipt_world_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
            {
                "data_stage": "train_ready",
                "release_profile_id": "p3-probe-12-v1",
                "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
                "promoted_row_set_sha256": "a" * 64,
                "promoted_split_row_set_sha256": {
                    "train": "b" * 64,
                    "eval": "c" * 64,
                },
            }
        )
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {
            "ok": True,
            "errors": [],
            "n_rows": 2,
            "n_worlds_observed": 12,
        },
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", KEY.decode())
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", "probe-gate-v1")

    product, receipt = load_and_create_release_gate_receipt(
        tmp_path,
        "p3-probe-12-v1",
        attestation_key=KEY,
    )

    assert receipt["n_worlds"] == 12
    assert receipt["ok"] is True
    assert receipt["errors"] == []
    assert receipt["source_file_sha256"] == product.source_file_sha256
    assert receipt["promoted_row_set_sha256"] == "a" * 64
    assert receipt["promoted_split_row_set_sha256"] == {
        "train": "b" * 64,
        "eval": "c" * 64,
    }
    assert receipt["tokenizer_model_id"] == "Qwen/Qwen3.5-4B"
    assert len(receipt["tokenizer_revision"]) == 40
    assert receipt["tokenizer_asset_manifest_sha256"] is None


def test_combined_probe_gate_receipt_explicitly_disables_production_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
            {
                "data_stage": "train_ready",
                "release_profile_id": "p3-probe-12-v1",
                "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            }
        )
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {
            "ok": True,
            "errors": [],
            "n_rows": 2,
            "n_worlds_observed": 12,
        },
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", KEY.decode())
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", "probe-gate-v1")
    monkeypatch.setenv(
        LOCAL_PROBE_COMBINED_ROLES_ENV, LOCAL_PROBE_TRUST_ISOLATION_VALUE
    )

    _, receipt = load_and_create_release_gate_receipt(
        tmp_path,
        "p3-probe-12-v1",
        attestation_key=KEY,
    )

    assert receipt["trust_scope"] == "local_probe"
    assert receipt["diagnostic_only"] is True
    assert receipt["trust_valid_for_production"] is False
    assert receipt["production_eligible"] is False


def test_gate_receipt_rejects_non_green_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text("{}")
    _write_jsonl(tmp_path / "train.jsonl", [{"split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {
            "errors": ["semantic growth failed"],
            "n_rows": 2,
            "n_worlds_observed": 12,
        },
    )

    with pytest.raises(ValueError, match="failed quality gate"):
        load_and_create_release_gate_receipt(
            tmp_path,
            "p3-probe-12-v1",
            attestation_key=KEY,
        )


def test_gate_receipt_rejects_an_incomplete_auditor_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", KEY.decode())
    monkeypatch.delenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", raising=False)
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
            {
                "data_stage": "train_ready",
                "release_profile_id": "p3-probe-12-v1",
                "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            }
        )
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {
            "errors": [],
            "n_rows": 2,
            "n_worlds_observed": 12,
        },
    )

    with pytest.raises(ValueError, match="complete auditor identity"):
        load_and_create_release_gate_receipt(
            tmp_path,
            "p3-probe-12-v1",
            attestation_key=KEY,
        )


@pytest.mark.parametrize(
    "source_name", ("quality_report.json", "train.jsonl", "eval.jsonl")
)
def test_gate_receipt_cannot_overwrite_a_bound_release_source(
    tmp_path: Path, source_name: str
) -> None:
    with pytest.raises(ValueError, match="cannot overwrite"):
        validate_gate_receipt_output(tmp_path, tmp_path / source_name)


def test_release_loader_always_rechecks_the_complete_train_eval_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = {"release_profile_id": "p3-probe-12-v1"}
    (tmp_path / "quality_report.json").write_text(json.dumps(report))
    train = [{"query_id": "train-1", "split": "train"}]
    evaluation = [{"query_id": "eval-1", "split": "eval"}]
    _write_jsonl(tmp_path / "train.jsonl", train)
    _write_jsonl(tmp_path / "eval.jsonl", evaluation)
    observed: dict[str, object] = {}

    def fake_evaluate(candidate_report, rows, **kwargs):
        observed.update(report=candidate_report, rows=rows, kwargs=kwargs)
        return {"ok": True, "errors": []}

    monkeypatch.setattr(quality_gate, "evaluate_quality", fake_evaluate)

    loaded = quality_gate.load_release_product(tmp_path, "p3-probe-12-v1")

    assert loaded.train_rows == train
    assert loaded.eval_rows == evaluation
    assert observed["rows"] == train + evaluation
    assert observed["kwargs"]["release_profile_id"] == "p3-probe-12-v1"


def test_release_loader_fails_closed_on_any_recomputed_gate_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text(
        json.dumps({"release_profile_id": "p3-probe-12-v1"})
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"query_id": "train-1", "split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"query_id": "eval-1", "split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {"ok": False, "errors": ["tampered"]},
    )

    with pytest.raises(ValueError, match="tampered"):
        quality_gate.load_release_product(tmp_path, "p3-probe-12-v1")


def test_release_loader_rejects_rows_stored_in_the_wrong_split_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text(
        json.dumps({"release_profile_id": "p3-probe-12-v1"})
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"query_id": "eval-1", "split": "eval"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"query_id": "train-1", "split": "train"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {"ok": True, "errors": []},
    )

    with pytest.raises(ValueError, match="split file mismatch"):
        quality_gate.load_release_product(tmp_path, "p3-probe-12-v1")


def test_training_manifest_binds_source_report_and_every_output(tmp_path: Path) -> None:
    source = tmp_path / "release"
    output = tmp_path / "export"
    source.mkdir()
    output.mkdir()
    (source / "quality_report.json").write_text('{"signed":"report"}\n')
    (source / "train.jsonl").write_text('{"split":"train"}\n')
    (source / "eval.jsonl").write_text('{"split":"eval"}\n')
    shard = output / "B5.json"
    shard.write_text('[{"conversations":[]}]\n')
    dataset_info = output / "dataset_info.json"
    dataset_info.write_text('{"causaltwin_b5":{}}\n')
    manifest_path = output / "training_export_manifest.json"

    manifest = create_training_manifest(
        manifest_path,
        source_data_dir=source,
        release_profile_id="p3-probe-12-v1",
        transform_revision="sharegpt-v-test",
        output_paths=[shard, dataset_info],
        source_file_sha256=_source_digests(source),
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )

    validated = validate_training_manifest(
        manifest_path,
        expected_release_profile_id="p3-probe-12-v1",
        expected_transform_revision="sharegpt-v-test",
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )
    assert validated == manifest
    assert manifest["release_profile_sha256"] == release_profile_sha256(
        "p3-probe-12-v1"
    )

    shard.write_text('[{"conversations":[{"tampered":true}]}]\n')
    with pytest.raises(ValueError, match="output digest"):
        validate_training_manifest(
            manifest_path,
            expected_release_profile_id="p3-probe-12-v1",
            expected_transform_revision="sharegpt-v-test",
            attestation_key=attestation_key_from_env("training_export_manifest"),
        )


def test_validator_rejects_a_valid_manifest_for_a_different_output_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "release"
    signed_output = tmp_path / "signed"
    actual_output = tmp_path / "actual"
    source.mkdir()
    signed_output.mkdir()
    actual_output.mkdir()
    (source / "quality_report.json").write_text('{"signed":"report"}\n')
    (source / "train.jsonl").write_text('{"split":"train"}\n')
    (source / "eval.jsonl").write_text('{"split":"eval"}\n')
    signed_shard = signed_output / "B5.json"
    signed_shard.write_text("[]\n")
    manifest_path = signed_output / "training_export_manifest.json"
    create_training_manifest(
        manifest_path,
        source_data_dir=source,
        release_profile_id="p3-probe-12-v1",
        transform_revision="sharegpt-v-test",
        output_paths=[signed_shard],
        source_file_sha256=_source_digests(source),
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )

    manifest = validate_training_manifest(
        manifest_path,
        expected_release_profile_id="p3-probe-12-v1",
        expected_transform_revision="sharegpt-v-test",
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )
    bound = resolve_training_manifest_path(
        manifest_path, manifest, manifest["outputs"][0]["path"]
    )

    assert bound != (actual_output / "B5.json").resolve()


def test_training_manifest_validates_after_release_directory_relocation(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release-v1"
    source = release_root / "04_promoted"
    output = release_root / "05_training"
    source.mkdir(parents=True)
    output.mkdir()
    (source / "quality_report.json").write_text('{"signed":"report"}\n')
    (source / "train.jsonl").write_text('{"split":"train"}\n')
    (source / "eval.jsonl").write_text('{"split":"eval"}\n')
    shard = output / "B5.json"
    shard.write_text("[]\n")
    manifest_path = output / "training_export_manifest.json"

    manifest = create_training_manifest(
        manifest_path,
        release_root=release_root,
        source_data_dir=source,
        release_profile_id="p3-probe-12-v1",
        transform_revision="sharegpt-v-test",
        output_paths=[shard],
        source_file_sha256=_source_digests(source),
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )

    assert manifest["manifest_path"] == "05_training/training_export_manifest.json"
    assert manifest["source_data_dir"] == "04_promoted"
    assert manifest["outputs"][0]["path"] == "05_training/B5.json"
    assert not Path(manifest["source_data_dir"]).is_absolute()

    relocated = tmp_path / "relocated"
    shutil.move(release_root, relocated)
    validate_training_manifest(
        relocated / "05_training" / "training_export_manifest.json",
        expected_release_profile_id="p3-probe-12-v1",
        expected_transform_revision="sharegpt-v-test",
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )


def test_training_export_rows_are_exact_deterministic_promoted_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from longworld.core import training_manifest

    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    monkeypatch.setenv(ROLE_KEY_ENVS["promotion"], KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["promotion"], "kms-promotion-v1")
    monkeypatch.setenv(ROLE_KEY_ENVS["report"], KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["report"], "kms-report-v1")
    monkeypatch.setattr(
        training_manifest,
        "release_profile",
        lambda _profile_id: type(
            "Profile",
            (),
            {
                "training_conditions": ("B1",),
                "training_length_buckets": ("32k",),
                "training_export_seed": 0,
            },
        )(),
    )
    root = tmp_path / "release"
    source = root / "04_promoted"
    output = root / "05_training"
    source.mkdir(parents=True)
    output.mkdir()
    promoted = {
        "data_stage": "train_ready",
        "split": "train",
        "world_id": "world-1",
        "query_id": "query-1",
        "dossier_id": "dossier-1",
        "view": "full",
        "query_type": "version_selection",
        "query_timing": "first",
        "length_bucket": "32k",
        "context": "release history and CI evidence",
        "answer": "v2.1.0",
        "difficulty": {"context_tokens": 32_000, "max_evidence_distance": 24_000},
        "dependency_class": "long_range",
        "base_task_id": "task-1",
        "executable_proof_id": "proof-1",
        "source_relation_id": "relation-1",
        "answer_program_id": "program-1",
        "source_origins": ["real_public"],
        "workflow_kinds": ["hybrid_causal"],
        "workflow_ids": ["workflow-1"],
        "evidence_roles": ["essential"],
        "composition_method": "same_case_dossier",
        "training_objective": "sft",
        "verification": {
            "production_mode": True,
            "semantic_sufficient": True,
            "strict_executable_sufficient": True,
            "embedding_topk_insufficient": True,
        },
        "view_verification": {
            "production_eligible": True,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": "v2.1.0",
            "strict_replay_answer": "v2.1.0",
        },
        "promotion": {
            "schema_version": "train-ready-promotion-v2",
            "candidate_sha256": "a" * 64,
            "dense_audit_sha256": "b" * 64,
            "dense_ranking_sha256": "c" * 64,
            "dense_model_provider": "huggingface",
            "dense_model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "dense_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
            "dense_model_backend": "sentence-transformers-6.0.0",
            "dense_score_metric": "dot_product",
            "dense_chunking": {
                "strategy": "tokenizer_token_windows",
                "max_tokens": 192,
                "overlap_tokens": 32,
                "aggregation": "max_similarity",
            },
            "dense_top_k": 3,
            "strict_replay_revision": STRICT_REPLAY_REVISION,
            "strict_replay_answer": "v2.1.0",
        },
        "artifact_classification": [
            {
                "artifact_id": "artifact-1",
                "workflow_id": "workflow-1",
                "workflow_kind": "hybrid_causal",
                "evidence_role": "causal_gold",
                "source_origin": "real_public",
                "provenance_id": "source-sha256:" + "d" * 64,
            }
        ],
    }
    (source / "quality_report.json").write_text("{}\n")
    second = {
        **promoted,
        "query_id": "query-2",
        "dossier_id": "dossier-2",
        "context": "a second source-bound release history",
        "base_task_id": "task-2",
        "executable_proof_id": "proof-2",
    }
    promoted = attach_attestation(promoted, KEY, purpose="sft_row")
    second = attach_attestation(second, KEY, purpose="sft_row")
    _write_jsonl(source / "train.jsonl", [promoted, second])
    (source / "eval.jsonl").write_text("")
    transformed = {
        "conversations": [
            {"from": "system", "value": LLAMAFACTORY_SHAREGPT_SYSTEM_V4},
            {"from": "human", "value": promoted["context"]},
            {"from": "gpt", "value": promoted["answer"]},
        ],
        "world_id": "world-1",
        "query_id": "query-1",
        "dossier_id": "dossier-1",
        "view": "full",
        "query_type": "version_selection",
        "query_timing": "first",
        "length_bucket": "32k",
        "evidence_distance": 24_000,
        "dependency_class": "long_range",
        "sample_weight": 1,
        "base_task_id": "task-1",
        "executable_proof_id": "proof-1",
        "source_relation_id": "relation-1",
        "answer_program_id": "program-1",
        "source_origins": ["real_public"],
        "workflow_kinds": ["hybrid_causal"],
        "workflow_ids": ["workflow-1"],
        "evidence_roles": ["essential"],
        "composition_method": "same_case_dossier",
        "training_objective": "sft",
    }
    transformed_second = json.loads(json.dumps(transformed))
    transformed_second.update(
        {
            "query_id": second["query_id"],
            "dossier_id": second["dossier_id"],
            "base_task_id": second["base_task_id"],
            "executable_proof_id": second["executable_proof_id"],
        }
    )
    transformed_second["conversations"][1]["value"] = second["context"]
    transformed_second["conversations"][2]["value"] = second["answer"]
    shard = output / "B1.json"
    expected_rows = [transformed, transformed_second]
    shard.write_text(json.dumps(expected_rows) + "\n")
    manifest_path = output / "training_export_manifest.json"

    def build_manifest() -> dict:
        return create_training_manifest(
            manifest_path,
            release_root=root,
            source_data_dir=source,
            release_profile_id="p3-probe-12-v1",
            transform_revision=LLAMAFACTORY_SHAREGPT_TRANSFORM_V4,
            output_paths=[shard],
            source_file_sha256=_source_digests(source),
            attestation_key=attestation_key_from_env("training_export_manifest"),
        )

    manifest = build_manifest()

    assert validate_deterministic_training_transform(manifest_path, manifest) == 2

    assert not {
        "trust_scope",
        "diagnostic_only",
        "content_gate_eligible",
        "trust_valid_for_production",
        "production_eligible",
    }.intersection(promoted)
    assert (
        validate_deterministic_training_transform(
            manifest_path, manifest, require_production_trust=True
        )
        == 2
    )

    invalid = {
        **{key: value for key, value in promoted.items() if key != "attestation"},
        "view_verification": {
            **promoted["view_verification"],
            "production_eligible": False,
        },
    }
    invalid = attach_attestation(invalid, KEY, purpose="sft_row")
    _write_jsonl(source / "train.jsonl", [invalid, second])
    manifest = build_manifest()
    with pytest.raises(ValueError, match="view_not_production_eligible"):
        validate_deterministic_training_transform(
            manifest_path, manifest, require_production_trust=True
        )

    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["promotion"], "probe-promotion-v1")
    probe = {
        **{key: value for key, value in promoted.items() if key != "attestation"},
        "trust_scope": "local_probe",
        "diagnostic_only": True,
        "content_gate_eligible": True,
        "trust_valid_for_production": False,
        "production_eligible": False,
        "view_verification": {
            **promoted["view_verification"],
            "content_gate_eligible": True,
            "production_eligible": False,
        },
    }
    _write_jsonl(
        source / "train.jsonl", [attach_attestation(probe, KEY, purpose="sft_row")]
    )
    manifest = build_manifest()
    with pytest.raises(ValueError, match="production trust"):
        validate_deterministic_training_transform(
            manifest_path, manifest, require_production_trust=True
        )

    shard.write_text(json.dumps([]) + "\n")
    manifest = build_manifest()
    with pytest.raises(ValueError, match="deterministic transform"):
        validate_deterministic_training_transform(manifest_path, manifest)


def test_p12_sec_profile_deterministically_exports_legal_128k_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from longworld.core import training_manifest

    monkeypatch.setattr(training_manifest, "sft_row_errors", lambda _row: [])
    asset_digest = "bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb"
    bucket_tokens = {
        "16k": 16_000,
        "32k": 32_000,
        "64k": 64_000,
        "128k": 128_000,
    }
    rows = []
    for bucket, tokens in bucket_tokens.items():
        for view in ("full", "cf", "ordered_artifact_view"):
            row = {
                "data_stage": "train_ready",
                "split": "train",
                "world_id": "sec-world",
                "query_id": f"sec-{bucket}-{view}",
                "dossier_id": (
                    f"sec-{bucket}-twin"
                    if view in {"full", "cf"}
                    else f"sec-{bucket}-ordered"
                ),
                "view": view,
                "query_type": "sec_financial_reconstruction",
                "query_timing": "first",
                "length_bucket": bucket,
                "context": f"source-bound SEC reconstruction {bucket} {view}",
                "answer": f"answer-{bucket}-{view}",
                "difficulty": {
                    "context_tokens": tokens,
                    "max_evidence_distance": tokens - 1_000,
                },
                "dependency_class": "deep_dependency",
                "base_task_id": f"task-{bucket}",
                "executable_proof_id": f"proof-{bucket}",
                "source_relation_id": f"relation-{bucket}",
                "answer_program_id": "sec-reconstruction",
                "source_origins": ["real_public"],
                "workflow_kinds": ["hybrid_causal"],
                "workflow_ids": ["sec-workflow"],
                "evidence_roles": ["causal_gold"],
                "composition_method": (
                    "causal_timeline"
                    if view == "ordered_artifact_view"
                    else "same_case_dossier"
                ),
                "training_objective": "sft",
            }
            if bucket == "128k":
                row.update(
                    tokenizer_context_tokens=tokens,
                    tokenizer_model_id="Qwen/Qwen3.5-4B",
                    tokenizer_revision=("a7b0d22b993d71000cf2eadfb37222a67cee521e"),
                    tokenizer_asset_manifest_sha256=asset_digest,
                    promotion={"tokenizer_asset_manifest_sha256": asset_digest},
                )
                assert exact_token_metadata_valid(row, require_asset_manifest=True)
                assert (
                    row["promotion"]["tokenizer_asset_manifest_sha256"]
                    == (row["tokenizer_asset_manifest_sha256"])
                )
            rows.append(row)

    historical_outputs = training_manifest._expected_sharegpt_outputs(
        rows, {"release_profile_id": "p7-sec-source-slice-1-v1"}
    )
    assert release_profile("p7-sec-source-slice-1-v1").training_length_buckets == (
        "16k",
        "32k",
        "64k",
    )
    assert not any(
        row["length_bucket"] == "128k"
        for output in historical_outputs.values()
        for row in output
    )

    profile_id = "p12-sec-source-slice-1-v1"
    validate_release_transform(
        profile_id,
        conditions=release_profile(profile_id).training_conditions,
        train_buckets=set(bucket_tokens),
        seed=0,
        token_budget=None,
    )
    with pytest.raises(ValueError, match="immutable profile"):
        validate_release_transform(
            "p7-sec-source-slice-1-v1",
            conditions=release_profile("p7-sec-source-slice-1-v1").training_conditions,
            train_buckets=set(bucket_tokens),
            seed=0,
            token_budget=None,
        )
    expected_outputs = training_manifest._expected_sharegpt_outputs(
        rows, {"release_profile_id": profile_id}
    )
    assert any(
        row["length_bucket"] == "128k"
        for output in expected_outputs.values()
        for row in output
    )

    root = tmp_path / "release"
    source = root / "04_promoted"
    output = root / "05_training"
    source.mkdir(parents=True)
    output.mkdir()
    (source / "quality_report.json").write_text("{}\n")
    _write_jsonl(source / "train.jsonl", rows)
    (source / "eval.jsonl").write_text("")
    output_paths = []
    for name, expected_rows in expected_outputs.items():
        path = output / name
        path.write_text(json.dumps(expected_rows) + "\n")
        output_paths.append(path)
    manifest_path = output / "training_export_manifest.json"
    manifest = create_training_manifest(
        manifest_path,
        release_root=root,
        source_data_dir=source,
        release_profile_id=profile_id,
        transform_revision=LLAMAFACTORY_SHAREGPT_TRANSFORM_V4,
        output_paths=output_paths,
        source_file_sha256=_source_digests(source),
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )

    assert validate_deterministic_training_transform(manifest_path, manifest) == sum(
        len(output_rows) for output_rows in expected_outputs.values()
    )


def test_deterministic_b5w_transform_uses_weights_without_copying_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from longworld.core import training_manifest

    monkeypatch.setattr(training_manifest, "sft_row_errors", lambda _row: [])
    monkeypatch.setattr(
        training_manifest,
        "release_profile",
        lambda _profile_id: type(
            "Profile",
            (),
            {
                "training_conditions": ("B5w",),
                "training_length_buckets": ("32k",),
                "training_export_seed": 0,
            },
        )(),
    )
    base = {
        "data_stage": "train_ready",
        "split": "train",
        "world_id": "world-1",
        "query_type": "version_selection",
        "query_timing": "first",
        "length_bucket": "32k",
        "answer": "v2.1.0",
        "difficulty": {"context_tokens": 32_000, "max_evidence_distance": 24_000},
        "dependency_class": "long_range",
    }
    rows = [
        {
            **base,
            "query_id": f"query-{view}",
            "dossier_id": "dossier-twin" if view in {"full", "cf"} else "dossier-2",
            "view": view,
            "context": f"source-bound {view} context",
        }
        for view in ("full", "cf", "ordered_artifact_view")
    ]

    expected = training_manifest._expected_sharegpt_outputs(
        rows,
        {"release_profile_id": "p3-probe-12-v1"},
    )

    assert set(expected) == {"B5w.json"}
    assert len(expected["B5w.json"]) == len(rows)
    assert {row["sample_weight"] for row in expected["B5w.json"]} == {2}
