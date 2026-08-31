from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)
from longworld.core.domainhistory import (
    KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
    HistoryBand,
    build_kev_catalog_history_candidates,
    build_kev_pipeline_candidate,
)
from longworld.core.financehistory import (
    build_finance_pipeline_candidate,
    build_financial_history_candidates,
)
from longworld.core.taskproof import (
    TaskProofError,
    _contiguous_window_proof,
    compute_task_proof,
)
from longworld.core.taskreplaysidecar import (
    CYBER_KEV_TASK_REPLAY_ADAPTER,
    FINANCE_TASK_REPLAY_ADAPTER,
)
from longworld.core.verify import Verification
from tests.test_taskpromotion import (
    KEYS,
    TOKENIZER_ASSET_SHA256,
    TOKENIZER_MODEL_ID,
    TOKENIZER_REVISION,
    _financial_filings,
    _kev_catalog,
)


@pytest.fixture(autouse=True)
def _development_attestation_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    for role, key in KEYS.items():
        monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"probe-task-proof-{role}-v1")


def _binding(adapter: tuple[str, str, str]) -> dict[str, str]:
    return {
        "adapter_id": adapter[0],
        "adapter_revision": adapter[1],
        "sidecar_schema_version": adapter[2],
        "sha256": "f" * 64,
    }


def _long_cyber_candidate() -> dict[str, Any]:
    catalog = _kev_catalog()
    for vulnerability in catalog["vulnerabilities"]:
        vulnerability["shortDescription"] = "Observed exploited vulnerability " + (
            "distinct remediation chronology detail " * 85
        )
    [history] = build_kev_catalog_history_candidates(
        catalog,
        world_id="cyber-kev-task-proof-long",
        source_binding={
            "source_url": "https://www.cisa.gov/kev.json",
            "observed_at": "2026-08-30T00:00:00Z",
            "retrieval_sha256": "a" * 64,
            "signed_manifest_sha256": "b" * 64,
        },
        bands=(HistoryBand("16k", 17_000, 25_000),),
        token_counter=len,
        tokenizer_model_id=TOKENIZER_MODEL_ID,
        tokenizer_revision=TOKENIZER_REVISION,
    )
    return build_kev_pipeline_candidate(
        history,
        token_counter=len,
        tokenizer_asset_manifest_sha256=TOKENIZER_ASSET_SHA256,
        replay_manifest_binding={
            "schema_version": KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
            "sha256": "e" * 64,
            "source_manifest_sha256": "b" * 64,
            "source_response_sha256": "a" * 64,
            "replay_revision": history["strict_replay_revision"],
        },
        candidate_attestation_key=KEYS["candidate"],
        task_replay_sidecar_binding=_binding(CYBER_KEV_TASK_REPLAY_ADAPTER),
        document_shards=4,
    )


def _long_finance_candidate() -> dict[str, Any]:
    filings = []
    for filing in _financial_filings():
        rows = []
        source_cursor = 0
        for row in filing.rows:
            padding = (
                f" distinct long-range source evidence {row.record_id} " * 20
                if row.facts
                else ""
            )
            source_text = row.source_text + padding
            rows.append(
                replace(
                    row,
                    source_char_start=source_cursor,
                    source_char_end=source_cursor + len(source_text),
                    source_text=source_text,
                )
            )
            source_cursor += len(source_text) + 100
        filings.append(replace(filing, rows=tuple(rows)))
    [history] = build_financial_history_candidates(
        tuple(filings),
        world_id="finance-task-proof-long",
        issuer_name="Example Issuer",
        cik="0000000001",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_ir_rendered_xbrl",
            "authorization_record_id": "AUTH-1",
        },
        bands=(HistoryBand("16k", 30_000, 40_000),),
        token_counter=len,
        tokenizer_model_id=TOKENIZER_MODEL_ID,
        tokenizer_revision=TOKENIZER_REVISION,
    )
    history["tokenizer_asset_manifest_sha256"] = TOKENIZER_ASSET_SHA256
    return build_finance_pipeline_candidate(
        history,
        task_replay_sidecar_binding=_binding(FINANCE_TASK_REPLAY_ADAPTER),
    )


@pytest.mark.parametrize("factory", (_long_cyber_candidate, _long_finance_candidate))
def test_computes_closed_real_task_proof_without_mutating_candidate(
    factory: Any,
) -> None:
    candidate = factory()
    before = deepcopy(candidate)

    proof = compute_task_proof(candidate, token_counter=len)

    assert candidate == before
    verification = Verification.model_validate(proof["verification"])
    assert verification.candidate_mode is True
    assert verification.production_mode is False
    assert verification.embedding_topk_insufficient is False
    assert not verification.all_green()
    assert verification.artifact_aligned_windows_insufficient is True
    assert verification.contiguous_windows_insufficient is False
    assert verification.local_window_insufficient is False
    assert verification.no_shortcut is False
    assert proof["view_verification"]["global_proof_green"] is False
    receipt = proof["task_proof_receipt"]
    assert receipt["window_scope"] == "artifact_aligned"
    assert set(receipt["artifact_aligned_windows"]) == {"4k", "8k", "16k"}
    assert all(
        item["status"] == "insufficient"
        for item in receipt["artifact_aligned_windows"].values()
    )
    assert receipt["retrieval"]["bm25"]["all_prefixes_insufficient"] is True
    assert receipt["retrieval"]["lexical_tfidf"]["all_prefixes_insufficient"] is True
    assert all(receipt["checks"].values())


def test_rejects_candidate_solved_by_a_strict_16k_contiguous_window() -> None:
    [history] = build_financial_history_candidates(
        _financial_filings(),
        world_id="finance-task-proof-short-window",
        issuer_name="Example Issuer",
        cik="0000000001",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_ir_rendered_xbrl",
            "authorization_record_id": "AUTH-1",
        },
        bands=(HistoryBand("16k", 20_000, 22_000),),
        token_counter=len,
        tokenizer_model_id=TOKENIZER_MODEL_ID,
        tokenizer_revision=TOKENIZER_REVISION,
    )
    history["tokenizer_asset_manifest_sha256"] = TOKENIZER_ASSET_SHA256
    candidate = build_finance_pipeline_candidate(
        history,
        task_replay_sidecar_binding=_binding(FINANCE_TASK_REPLAY_ADAPTER),
    )

    with pytest.raises(TaskProofError, match="contiguous window.*16k"):
        compute_task_proof(candidate, token_counter=len)


def test_rejects_source_body_corruption() -> None:
    candidate = _long_cyber_candidate()
    candidate["document_context"] = candidate["document_context"].replace(
        "Apply vendor remediation 0 immediately.", "Ignore remediation 0.", 1
    )

    with pytest.raises(TaskProofError, match="body|adapter audit"):
        compute_task_proof(candidate, token_counter=len)


def test_rejects_source_relation_edge_content_corruption() -> None:
    candidate = _long_cyber_candidate()
    corrupted = list(candidate["authentic_source_relation_edges"][0])
    corrupted[1] = "cisa-kev:forged"
    candidate["authentic_source_relation_edges"][0] = corrupted

    with pytest.raises(TaskProofError, match="source relation"):
        compute_task_proof(candidate, token_counter=len)


def test_contiguous_windows_count_each_bpe_substring_exactly() -> None:
    documents = ["left", "right"]
    joined = "\n\n--- ARTIFACT BOUNDARY ---\n\n".join(documents)

    def non_additive_counter(text: str) -> int:
        return {"left": 900, "right": 4095, joined: 5000}[text]

    with pytest.raises(TaskProofError, match="contiguous window 4k"):
        _contiguous_window_proof(
            artifact_ids=["left", "right"],
            documents=documents,
            spans=[
                {"start_token": 0, "end_token": 900},
                {"start_token": 900, "end_token": 5000},
            ],
            full_tokens=5000,
            token_counter=non_additive_counter,
            replay_answer=lambda selected: (
                "gold" if selected == ["right"] else "unknown"
            ),
            expected_answer="gold",
        )


def test_artifact_aligned_window_does_not_claim_token_offset_exhaustive() -> None:
    proof = compute_task_proof(_long_finance_candidate(), token_counter=len)
    verification = Verification.model_validate(proof["verification"])

    assert proof["task_proof_receipt"]["window_scope"] == "artifact_aligned"
    assert verification.artifact_aligned_windows_insufficient is True
    assert verification.contiguous_windows_insufficient is False
    assert verification.local_window_insufficient is False
    assert verification.no_shortcut is False
