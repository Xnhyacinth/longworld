from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import scripts.materialize_domain_histories as materializer
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    verify_attestation,
)
from longworld.core.domainhistory import (
    KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
    HistoryBand,
    audit_kev_pipeline_candidate,
    build_kev_catalog_history_candidates,
    build_kev_pipeline_candidate,
    build_kev_pipeline_replay_manifest,
    kev_pipeline_replay_manifest_binding,
    replay_kev_pipeline_candidate,
    verify_kev_pipeline_replay_manifest_bytes,
)
from longworld.core.pack import SEP
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_RANKING_PURPOSE,
    PromotionError,
    _reconstruct_candidate,
    candidate_sha256,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from rank_candidates_dense import rank_candidates

CANDIDATE_KEY = b"cyber-kev-candidate-test-key-32-bytes"
RANKER_KEY = b"cyber-kev-ranker-test-key-at-least-32-bytes"
SOURCE_KEY = b"cyber-kev-source-test-key-at-least-32-bytes"
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


def _catalog(n: int = 24) -> dict[str, Any]:
    vulnerabilities = []
    for index in range(n):
        year = 2021 + index // 8
        day = index % 8 + 1
        vulnerabilities.append(
            {
                "cveID": f"CVE-{year}-{1000 + index}",
                "vendorProject": f"Vendor {index}",
                "product": f"Product {index}",
                "vulnerabilityName": f"Issue {index}",
                "dateAdded": f"{year}-01-{day:02d}",
                "shortDescription": "Observed exploited vulnerability "
                + ("detail " * 8),
                "requiredAction": f"Apply vendor remediation {index} immediately.",
                "dueDate": f"{year}-02-{day:02d}",
                "knownRansomwareCampaignUse": (
                    "Known" if index % 3 == 0 else "Unknown"
                ),
                "notes": f"https://example.test/{index}",
                "cwes": [f"CWE-{100 + index}"],
            }
        )
    return {
        "title": "CISA Known Exploited Vulnerabilities Catalog",
        "catalogVersion": "test-1",
        "dateReleased": "2026.08.30",
        "count": n,
        "vulnerabilities": vulnerabilities,
    }


def _history_row() -> dict[str, Any]:
    [row] = build_kev_catalog_history_candidates(
        _catalog(),
        world_id="cyber-kev-pipeline-test",
        source_binding={
            "source_url": "https://www.cisa.gov/kev.json",
            "observed_at": "2026-08-30T00:00:00Z",
            "retrieval_sha256": "a" * 64,
            "signed_manifest_sha256": "b" * 64,
        },
        bands=(HistoryBand("16k", 2_000, 3_000),),
        token_counter=len,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
    )
    return row


def _candidate() -> dict[str, Any]:
    history = _history_row()
    return build_kev_pipeline_candidate(
        history,
        token_counter=len,
        tokenizer_asset_manifest_sha256="d" * 64,
        replay_manifest_binding={
            "schema_version": KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
            "sha256": "e" * 64,
            "source_manifest_sha256": "b" * 64,
            "source_response_sha256": "a" * 64,
            "replay_revision": history["strict_replay_revision"],
        },
        candidate_attestation_key=CANDIDATE_KEY,
        document_shards=4,
    )


class _Tokenizer:
    def __call__(self, text: str, **_kwargs: Any) -> dict[str, list[int]]:
        return {"input_ids": [ord(character) for character in text]}

    def decode(self, token_ids: list[int], **_kwargs: Any) -> str:
        return "".join(chr(token_id) for token_id in token_ids)


class _Model:
    tokenizer = _Tokenizer()

    def encode(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
        return [[1.0, float(index + 1)] for index, _text in enumerate(texts)]


def test_pipeline_candidate_has_rankable_documents_and_independent_replay() -> None:
    candidate = _candidate()

    assert verify_attestation(
        candidate, CANDIDATE_KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    documents = candidate["document_context"].split(SEP)
    classifications = candidate["artifact_classification"]
    assert len(documents) == len(classifications) == 4
    assert candidate["essential_artifact_ids"] == [
        item["artifact_id"] for item in classifications
    ]
    assert "verification" not in candidate
    assert "view_verification" not in candidate
    assert candidate["pipeline_capabilities"] == {
        "dense_ranking": True,
        "cyber_strict_replay": True,
        "generic_strict_replay": False,
        "generic_promotion": False,
    }
    assert (
        candidate["semantic_tokens"]["internal"]
        == candidate["tokenizer_context_tokens"]
    )
    assert replay_kev_pipeline_candidate(candidate)["answer"] == candidate["answer"]
    unknown_selection = replay_kev_pipeline_candidate(
        candidate,
        evidence_artifact_ids=[classifications[0]["artifact_id"], "unknown-artifact"],
    )
    assert unknown_selection["answer"] == "unknown"
    assert unknown_selection["source_record_ids"] == []
    assert (
        replay_kev_pipeline_candidate(candidate, counterfactual=True)["answer"]
        == candidate["cf_answer"]
    )
    assert all(audit_kev_pipeline_candidate(candidate, token_counter=len).values())


def test_pipeline_candidate_fails_closed_after_source_text_corruption() -> None:
    candidate = _candidate()
    candidate.pop("attestation")
    candidate["document_context"] = candidate["document_context"].replace(
        "Apply vendor remediation 0 immediately.", "Ignore remediation 0.", 1
    )

    audit = audit_kev_pipeline_candidate(candidate, token_counter=len)

    assert not audit["strict_replay_sufficient"]
    assert not audit["artifact_text_bindings_valid"]


def test_source_attested_replay_manifest_binds_exact_source_and_response() -> None:
    source_binding = _history_row()["source_binding"]
    manifest = build_kev_pipeline_replay_manifest(
        source_binding=source_binding,
        source_manifest_name="cyber_workflow_manifest.signed.v4.json",
        source_response_name="cisa-known-exploited-vulnerabilities.json",
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
        source_attestation_key=SOURCE_KEY,
    )
    raw = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )

    assert verify_kev_pipeline_replay_manifest_bytes(raw, SOURCE_KEY) == manifest
    assert kev_pipeline_replay_manifest_binding(raw, SOURCE_KEY) == {
        "schema_version": KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
        "sha256": __import__("hashlib").sha256(raw).hexdigest(),
        "source_manifest_sha256": "b" * 64,
        "source_response_sha256": "a" * 64,
        "replay_revision": _history_row()["strict_replay_revision"],
    }

    tampered = raw.replace(b"cisa-known", b"forged-cisa", 1)
    with pytest.raises(Exception, match="attestation"):
        verify_kev_pipeline_replay_manifest_bytes(tampered, SOURCE_KEY)


def test_dense_ranker_consumes_candidate_but_shared_promotion_needs_adapter(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")

    assert (
        rank_candidates(
            candidates_path,
            rankings_path,
            model_id=MODEL_ID,
            revision=MODEL_REVISION,
            model=_Model(),
            attestation_key=RANKER_KEY,
        )
        == 1
    )
    ranking = json.loads(rankings_path.read_text(encoding="utf-8"))
    assert ranking["candidate_sha256"] == candidate_sha256(candidate)
    assert verify_attestation(ranking, RANKER_KEY, purpose=DENSE_RANKING_PURPOSE)

    with pytest.raises(
        PromotionError,
        match="requires episode_replay_bundle or source_workflow_bundle",
    ):
        _reconstruct_candidate(candidate)


def test_materializer_exports_ranker_ready_candidates_and_source_replay_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-cyber-source-v1")
    source_manifest_path = tmp_path / "cyber_workflow_manifest.signed.v4.json"
    source_manifest_path.write_text("{}", encoding="utf-8")
    source_response_path = tmp_path / "cisa-known-exploited-vulnerabilities.json"
    source_response_path.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "out"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.domain-history-materialization.v1",
                "tokenizer": {
                    "model_id": "Qwen/Qwen3.5-4B",
                    "revision": "c" * 40,
                },
                "bands": [
                    {"name": "16k", "lower_tokens": 2_000, "upper_tokens": 3_000}
                ],
                "cyber_kev_history": {
                    "world_id": "cyber-kev-pipeline-materialization-test",
                    "signed_manifest": str(source_manifest_path),
                    "catalog_response": str(source_response_path),
                },
                "pipeline_candidate_export": {
                    "enabled": True,
                    "document_shards": 4,
                },
                "capacity_checks": [],
            }
        ),
        encoding="utf-8",
    )
    retrieval = {
        "kind": "cisa_kev",
        "retrieval_file": source_response_path.name,
        "final_url": "https://www.cisa.gov/kev.json",
        "observed_at": "2026-08-30T00:00:00Z",
        "sha256": "a" * 64,
        "status": 200,
    }

    class _CharacterTokenizer:
        @staticmethod
        def encode(text: str, *, add_special_tokens: bool) -> range:
            assert add_special_tokens is False
            return range(len(text))

    monkeypatch.setattr(
        materializer,
        "attestation_key_from_env",
        lambda purpose: CANDIDATE_KEY if purpose == "candidate_row" else SOURCE_KEY,
    )
    monkeypatch.setattr(
        materializer,
        "_load_tokenizer",
        lambda model_id, revision: _CharacterTokenizer(),
    )
    monkeypatch.setattr(
        materializer,
        "load_cyber_workflow_manifest_bytes",
        lambda raw, attestation_key: {"fetch_receipt": {"retrievals": [retrieval]}},
    )
    monkeypatch.setattr(
        materializer,
        "verify_bound_json_retrieval",
        lambda path, receipt: _catalog(),
    )
    monkeypatch.setattr(
        materializer,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda model_id, revision: "d" * 64,
    )

    report = materializer.materialize(config_path, output_dir)
    pipeline_rows = [
        json.loads(line)
        for line in (output_dir / "pipeline_candidates.jsonl").read_text().splitlines()
    ]
    replay_raw = (output_dir / "KEV_REPLAY_MANIFEST.json").read_bytes()
    sidecar = json.loads(
        (output_dir / "TASK_REPLAY_SIDECAR.json").read_text(encoding="utf-8")
    )
    registry = json.loads(
        (output_dir / "REPLAY_PATH_REGISTRY.json").read_text(encoding="utf-8")
    )

    assert report["pipeline_export"]["status"] == (
        "task_dense_audit_ready_upstream_proof_pending"
    )
    assert report["pipeline_export"]["accepted_candidates"] == 1
    assert report["pipeline_export"]["promotion_adapter_available"] is True
    assert report["pipeline_export"]["promotion_blocker_code"] == (
        "missing_signed_upstream_proof_gates:cyber"
    )
    assert all(
        audit_kev_pipeline_candidate(row, token_counter=len).values()
        for row in pipeline_rows
    )
    assert verify_kev_pipeline_replay_manifest_bytes(replay_raw, SOURCE_KEY)
    assert sidecar["adapter_id"] == "cyber.kev_history.v1"
    assert registry["task_replay_sidecars"] == {
        report["pipeline_export"]["task_replay_sidecar_sha256"]: (
            "TASK_REPLAY_SIDECAR.json"
        )
    }
