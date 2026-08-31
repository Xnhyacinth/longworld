from __future__ import annotations

import json
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
    replay_kev_pipeline_raw_slice,
)
from longworld.core.financehistory import (
    build_finance_pipeline_candidate,
    build_financial_history_candidates,
    replay_finance_pipeline_raw_slice,
)
from longworld.core.pack import SEP
from longworld.core.taskproof import (
    TaskProofError,
    _contiguous_window_proof,
    _raw_token_window_proof,
    _semantic_window_starts,
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


class _CharacterOffsetTokenizer:
    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        return_offsets_mapping: bool,
    ) -> dict[str, list[Any]]:
        assert add_special_tokens is False
        assert return_offsets_mapping is True
        return {
            "input_ids": list(range(len(text))),
            "offset_mapping": [(index, index + 1) for index in range(len(text))],
        }


_CHARACTER_TOKENIZER = _CharacterOffsetTokenizer()


def _proof(candidate: dict[str, Any]) -> dict[str, Any]:
    return compute_task_proof(
        candidate,
        token_counter=len,
        offset_tokenizer=_CHARACTER_TOKENIZER,
    )


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
        document_shards=6,
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


def test_computes_closed_real_task_proof_without_mutating_candidate() -> None:
    candidate = _long_finance_candidate()
    before = deepcopy(candidate)

    proof = _proof(candidate)

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
    assert receipt["window_scope"] == (
        "exact_raw_slice_replay_with_separate_intersection_upper_bound"
    )
    assert set(receipt["artifact_aligned_windows"]) == {"4k", "8k", "16k"}
    assert all(
        item["status"] == "insufficient"
        for item in receipt["artifact_aligned_windows"].values()
    )
    assert set(receipt["raw_token_offset_windows"]) == {"4k", "8k", "16k"}
    assert receipt["retrieval"]["bm25"]["all_prefixes_insufficient"] is True
    assert receipt["retrieval"]["lexical_tfidf"]["all_prefixes_insufficient"] is True
    assert all(receipt["checks"].values())


def test_clustered_cyber_candidate_fails_raw_16k_replay() -> None:
    with pytest.raises(TaskProofError, match="raw token window 16k"):
        _proof(_long_cyber_candidate())


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
        _proof(candidate)


def test_rejects_source_body_corruption() -> None:
    candidate = _long_cyber_candidate()
    candidate["document_context"] = candidate["document_context"].replace(
        "Apply vendor remediation 0 immediately.", "Ignore remediation 0.", 1
    )

    with pytest.raises(TaskProofError, match="body|adapter audit"):
        _proof(candidate)


def test_rejects_source_relation_edge_content_corruption() -> None:
    candidate = _long_cyber_candidate()
    corrupted = list(candidate["authentic_source_relation_edges"][0])
    corrupted[1] = "cisa-kev:forged"
    candidate["authentic_source_relation_edges"][0] = corrupted

    with pytest.raises(TaskProofError, match="source relation"):
        _proof(candidate)


def test_rejects_answer_exposed_on_question_surface() -> None:
    candidate = _long_finance_candidate()
    candidate["question"] += " " + candidate["answer"]

    with pytest.raises(TaskProofError, match="surface"):
        _proof(candidate)


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


def test_raw_token_window_proof_is_conservative_for_oversized_artifacts() -> None:
    oversized = json.dumps(
        {"payload": "x" * 5000, "record_type": "test"},
        sort_keys=True,
        separators=(",", ":"),
    )
    control = '{"record_type":"control"}'
    with pytest.raises(TaskProofError, match="raw token window 4k"):
        _raw_token_window_proof(
            artifact_ids=["oversized", "control"],
            documents=[oversized, control],
            offset_tokenizer=_CHARACTER_TOKENIZER,
            replay_raw_answer=lambda _text, _left, _right: "unknown",
            replay_artifact_answer=lambda selected: (
                "gold" if "oversized" in selected else "unknown"
            ),
            expected_answer="gold",
            expected_total_tokens=len(oversized + SEP + control),
        )


def test_raw_token_window_replays_the_exact_original_character_slice() -> None:
    document = json.dumps(
        {"payload": "x" * 5000, "record_type": "test"},
        sort_keys=True,
        separators=(",", ":"),
    )
    observed: list[tuple[str, bool, bool]] = []

    windows, strict, _spans = _raw_token_window_proof(
        artifact_ids=["oversized"],
        documents=[document],
        offset_tokenizer=_CHARACTER_TOKENIZER,
        replay_raw_answer=lambda text, left, right: (
            observed.append((text, left, right)) or "unknown"
        ),
        replay_artifact_answer=lambda _selected: "unknown",
        expected_answer="gold",
        expected_total_tokens=len(document),
    )

    assert strict is True
    assert observed == [(document[:4096], True, False)]
    assert windows["4k"]["exact_raw_executable"]["status"] == "insufficient"
    assert (
        windows["4k"]["conservative_intersection_upper_bound"]["can_establish_pass"]
        is False
    )


@pytest.mark.parametrize(
    ("offsets", "record_spans", "limit"),
    [
        ([(index, index + 1) for index in range(12)], [(0, 2), (4, 7)], 4),
        ([(0, 2), (1, 3), (4, 5), (6, 8), (9, 10)], [(1, 3), (6, 8)], 2),
        ([(0, 1), (3, 4), (7, 8), (8, 9)], [(3, 4), (7, 9)], 2),
        ([(0, 1), (1, 2), (2, 3), (3, 4)], [(0, 2), (2, 4)], 2),
    ],
)
def test_semantic_window_starts_match_all_start_oracle(
    offsets: list[tuple[int, int]],
    record_spans: list[tuple[int, int]],
    limit: int,
) -> None:
    max_start = len(offsets) - limit

    def state(start: int) -> tuple[int, ...]:
        char_start = offsets[start][0]
        char_end = offsets[start + limit - 1][1]
        return tuple(
            index
            for index, (record_start, record_end) in enumerate(record_spans)
            if char_start <= record_start and record_end <= char_end
        )

    oracle = {state(start) for start in range(max_start + 1)}
    representatives = _semantic_window_starts(offsets, record_spans, limit)

    assert {state(start) for start in representatives} == oracle


def test_domain_raw_slice_replay_ignores_partial_framing() -> None:
    candidate = _long_cyber_candidate()
    lines = candidate["document_context"].splitlines()
    header = lines[0]
    entry = next(line for line in lines[1:] if line.startswith('{"record_type"'))
    raw = "partial-prefix\n" + header + "\n" + entry + "\npartial-suffix"

    replay = replay_kev_pipeline_raw_slice(
        candidate,
        raw,
        left_framed=False,
        right_framed=False,
    )

    assert replay["answer"] != "unknown"
    assert replay["raw_slice_record_count"] == 2


def test_domain_raw_slice_replay_rejects_corrupted_or_surface_only_text() -> None:
    candidate = _long_cyber_candidate()
    line = next(
        value
        for value in candidate["document_context"].splitlines()
        if value.startswith('{"record_type"')
    )
    corrupted = line.replace('"record_type"', '"forged_type"', 1)

    assert (
        replay_kev_pipeline_raw_slice(
            candidate,
            corrupted,
            left_framed=True,
            right_framed=True,
        )["answer"]
        == "unknown"
    )
    assert (
        replay_kev_pipeline_raw_slice(
            candidate,
            str(candidate["answer"]),
            left_framed=False,
            right_framed=False,
        )["answer"]
        == "unknown"
    )


def test_finance_raw_slice_replay_revalidates_complete_records() -> None:
    candidate = _long_finance_candidate()
    documents = candidate["document_context"].split(SEP)
    essential = set(candidate["essential_artifact_ids"])
    selected = [
        document
        for document, classification in zip(
            documents, candidate["artifact_classification"], strict=True
        )
        if classification["artifact_id"] in essential
    ]
    raw = SEP.join(selected)

    replay = replay_finance_pipeline_raw_slice(
        candidate,
        raw,
        left_framed=True,
        right_framed=True,
    )

    assert replay["answer"] == candidate["answer"]
    assert replay["raw_slice_record_count"] == len(selected)


def test_task_proof_distinguishes_raw_executable_from_semantic_shortcut_proof() -> None:
    proof = _proof(_long_finance_candidate())
    verification = Verification.model_validate(proof["verification"])

    receipt = proof["task_proof_receipt"]
    assert receipt["window_scope"] == (
        "exact_raw_slice_replay_with_separate_intersection_upper_bound"
    )
    assert all(
        band["exact_raw_executable"]["status"] == "insufficient"
        for band in receipt["raw_token_offset_windows"].values()
    )
    assert all(
        band["conservative_intersection_upper_bound"]["can_establish_pass"] is False
        for band in receipt["raw_token_offset_windows"].values()
    )
    assert receipt["checks"]["raw_token_executable_windows_insufficient"] is True
    assert "raw_token_offset_windows_insufficient" not in receipt["checks"]
    assert verification.artifact_aligned_windows_insufficient is True
    assert verification.contiguous_windows_insufficient is False
    assert verification.local_window_insufficient is False
    assert verification.no_shortcut is False
