from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    verify_attestation,
)
from longworld.core.domainhistory import (
    HistoryBand,
    audit_cross_cve_pipeline_candidate,
    build_cross_cve_pipeline_candidate,
    build_cross_cve_remediation_history_candidates,
    replay_cross_cve_pipeline_candidate,
)
from longworld.core.pack import SEP
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE
from longworld.core.taskpromotion import _cross_cve_chronology
from longworld.core.taskproof import _projection_chronology
from longworld.core.taskreplaysidecar import (
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)

CANDIDATE_KEY = b"cyber-cross-cve-candidate-test-key-32b"
SOURCE_KEY = b"cyber-cross-cve-source-test-key-32byt"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_record(cve_id: str, kind: str, payload: dict[str, str]) -> dict[str, Any]:
    text = _canonical(payload)
    record_id = f"nvd:{cve_id}" if kind == "nvd_cve" else f"cisa-kev:{cve_id}"
    return {
        "record_id": record_id,
        "kind": kind,
        "cve_id": cve_id,
        "source_url": f"https://example.test/{record_id}",
        "source_sha256": "a" * 64,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
    }


def _documents(cve_id: str, date_added: str, due_date: str) -> list[dict[str, Any]]:
    nvd = _source_record(
        cve_id,
        "nvd_cve",
        {
            "id": cve_id,
            "published": "2020-01-01T00:00:00.000",
            "lastModified": "2026-01-01T00:00:00.000",
            "vulnStatus": "Analyzed",
        },
    )
    kev = _source_record(
        cve_id,
        "cisa_kev_entry",
        {
            "cveID": cve_id,
            "dateAdded": date_added,
            "dueDate": due_date,
            "knownRansomwareCampaignUse": "Known",
            "product": "Gateway",
            "requiredAction": f"Apply the vendor update for {cve_id}.",
            "vendorProject": "Example Vendor",
        },
    )
    relation = {
        "relation_id": f"cyber:listed-in-kev:{cve_id}",
        "kind": "listed_in_kev",
        "source_record_id": kev["record_id"],
        "target_record_id": nvd["record_id"],
    }
    return [nvd, kev, relation]


def _history_row() -> dict[str, Any]:
    documents = [
        _documents(
            f"CVE-2020-{1000 + index}",
            f"202{index}-01-01",
            f"202{index}-02-01",
        )
        for index in range(6)
    ]
    manifest = {
        "records": [record for group in documents for record in group[:2]],
        "relations": [group[2] for group in documents],
    }
    [row, *_rest] = build_cross_cve_remediation_history_candidates(
        manifest,
        world_id="cross-cve-pipeline-test",
        source_binding={
            "source_manifest_sha256": "a" * 64,
            "fetch_inventory_sha256": "b" * 64,
            "authorization_record_id": "public-cross-cve-test",
            "observed_at": "2026-09-01T00:00:00Z",
        },
        bands=(HistoryBand("16k", 1, 1_000_000),),
        token_counter=len,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
    )
    return row


def _candidate() -> dict[str, Any]:
    return build_cross_cve_pipeline_candidate(
        _history_row(),
        token_counter=len,
        tokenizer_asset_manifest_sha256="d" * 64,
        candidate_attestation_key=CANDIDATE_KEY,
    )


def test_pipeline_candidate_shards_each_source_line_and_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["candidate"], CANDIDATE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["candidate"], "probe-cross-cve-candidate-v1")
    candidate = _candidate()

    assert verify_attestation(
        candidate, CANDIDATE_KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    documents = candidate["document_context"].split(SEP)
    classifications = candidate["artifact_classification"]
    assert len(documents) == len(classifications) >= 4
    assert candidate["answer_program_id"] == (
        "cyber.cross_cve_remediation_reconstruction.v1"
    )
    assert (
        candidate["cross_cve_replay_contract"]["adapter_id"]
        == (CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[0])
    )
    assert (
        replay_cross_cve_pipeline_candidate(candidate)["answer"] == candidate["answer"]
    )
    assert (
        replay_cross_cve_pipeline_candidate(candidate, counterfactual=True)["answer"]
        == candidate["cf_answer"]
    )
    audit = audit_cross_cve_pipeline_candidate(candidate, token_counter=len)
    assert all(audit.values()), audit
    unknown = replay_cross_cve_pipeline_candidate(
        candidate,
        evidence_artifact_ids=candidate["essential_artifact_ids"][:3],
    )
    assert unknown["answer"] != candidate["answer"]


def test_sidecar_commitments_survive_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-cross-cve-source-v1")
    monkeypatch.setenv(ROLE_KEY_ENVS["candidate"], CANDIDATE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["candidate"], "probe-cross-cve-candidate-v1")
    history = _history_row()
    provisional = build_cross_cve_pipeline_candidate(
        history,
        token_counter=len,
        tokenizer_asset_manifest_sha256="d" * 64,
        candidate_attestation_key=CANDIDATE_KEY,
    )
    commitment = task_candidate_content_commitment(provisional)
    sidecar = build_task_replay_sidecar(
        adapter_id=CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[0],
        adapter_revision=CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[1],
        replay_payload={
            "source_manifest_sha256": "a" * 64,
            "fetch_inventory_sha256": "b" * 64,
            "authorization_record_id": "public-cross-cve-test",
            "replay_revision": CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[1],
            "tokenizer_model_id": history["tokenizer_model_id"],
            "tokenizer_revision": history["tokenizer_revision"],
            "tokenizer_asset_manifest_sha256": "d" * 64,
            "candidate_content_commitments": [commitment],
        },
        source_attestation_key=SOURCE_KEY,
    )
    sidecar_bytes = (
        json.dumps(sidecar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    binding = task_replay_sidecar_binding(
        sidecar_bytes, source_attestation_key=SOURCE_KEY
    )
    rebound = build_cross_cve_pipeline_candidate(
        history,
        token_counter=len,
        tokenizer_asset_manifest_sha256="d" * 64,
        candidate_attestation_key=CANDIDATE_KEY,
        task_replay_sidecar_binding=binding,
    )
    assert task_candidate_content_commitment(rebound) == commitment
    assert rebound["task_replay_sidecar"] == binding
    assert rebound["generation_integration"] == "task_replay_sidecar_bound"


def test_ordered_view_chronology_matches_cross_cve_adapter() -> None:
    candidate = _candidate()
    documents = candidate["document_context"].split(SEP)
    classifications = candidate["artifact_classification"]
    proof = _projection_chronology(candidate, classifications, documents)
    promotion = _cross_cve_chronology(list(zip(classifications, documents)))
    assert [item["order_key"] for item in proof] == [item[0] for item in promotion]
    assert [item["artifact_id"] for item in proof] == [
        item[1]["artifact_id"] for item in promotion
    ]
