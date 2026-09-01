from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import scripts.promote_candidates as promotion_cli
from longworld.core import financehistory
from longworld.core import taskpromotion as taskpromotion_module
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ATTESTATION_ROLES,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.domainhistory import (
    KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
    HistoryBand,
    build_kev_catalog_history_candidates,
    build_kev_pipeline_candidate,
)
from longworld.core.financehistory import (
    FinancialFact,
    FinancialFiling,
    FinancialSourceRow,
    build_finance_pipeline_candidate,
    build_financial_history_candidates,
)
from longworld.core.macrovintage import build_macro_vintage_pipeline_candidates
from longworld.core.pack import SEP
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_RANKING_PURPOSE,
    RELEASE_SELECTION_SCHEMA,
    PromotionError,
    _candidate_has_source_bound_proof,
    _candidate_has_verified_real_source,
    _selection_audit_matches_candidate,
    candidate_sha256,
    serialized_row_sha256,
    validate_task_candidate_content_uniqueness,
)
from longworld.core.taskpromotion import (
    create_task_dense_audit,
    promote_task_candidate,
)
from longworld.core.taskreplaysidecar import (
    CYBER_KEV_TASK_REPLAY_ADAPTER,
    FINANCE_TASK_REPLAY_ADAPTER,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    LoadedTaskReplaySidecar,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from longworld.core.verify import Verification
from tests.test_macro_vintage_pipeline import _workflow_manifest

KEYS = {
    role: f"task-promotion-{role}-key-material-2026-v1".encode()
    for role in ATTESTATION_ROLES
}
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
TOKENIZER_MODEL_ID = "Qwen/Qwen3.5-4B"
TOKENIZER_REVISION = "c" * 40
TOKENIZER_ASSET_SHA256 = "d" * 64
_TASK_SIDECAR_TOKEN_COUNTER = taskpromotion_module.task_sidecar_token_counter


def _test_token_count(text: str) -> int:
    return max(1, len(text) // 4)


class _TestOffsetTokenizer:
    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        return_offsets_mapping: bool,
    ) -> dict[str, list[Any]]:
        assert add_special_tokens is False
        assert return_offsets_mapping is True
        count = _test_token_count(text)
        offsets = [
            (index * 4, min(len(text), (index + 1) * 4)) for index in range(count)
        ]
        return {"input_ids": list(range(count)), "offset_mapping": offsets}


_test_token_count.offset_tokenizer = _TestOffsetTokenizer()  # type: ignore[attr-defined]


class _MacroOffsetTokenizer:
    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        return_offsets_mapping: bool,
    ) -> dict[str, list[Any]]:
        assert add_special_tokens is False
        assert return_offsets_mapping is True
        offsets = [
            (index, min(len(text), index + 4)) for index in range(0, len(text), 4)
        ]
        return {"input_ids": list(range(len(offsets))), "offset_mapping": offsets}


def _macro_token_count(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


_macro_token_count.offset_tokenizer = _MacroOffsetTokenizer()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _role_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    _test_token_count.offset_tokenizer = _TestOffsetTokenizer()  # type: ignore[attr-defined]
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    for role, key in KEYS.items():
        monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"probe-task-promotion-{role}-v1")
    monkeypatch.setattr(
        taskpromotion_module, "task_sidecar_token_counter", lambda _: _test_token_count
    )


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def test_task_tokenizer_rejects_preload_digest_mismatch_without_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _candidate_row, sidecar = _cyber_candidate(tmp_path)
    loaded = 0
    monkeypatch.setattr(
        taskpromotion_module,
        "_APPROVED_EXACT_TOKENIZERS",
        {(TOKENIZER_MODEL_ID, TOKENIZER_REVISION)},
    )

    def load(_model_id: str, _revision: str) -> object:
        nonlocal loaded
        loaded += 1
        return object()

    monkeypatch.setattr(
        taskpromotion_module, "_resolved_local_tokenizer_revision", lambda *_: "c" * 40
    )
    monkeypatch.setattr(
        taskpromotion_module,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda *_: "f" * 64,
    )
    monkeypatch.setattr(taskpromotion_module, "_load_replay_tokenizer_uncached", load)

    with pytest.raises(PromotionError, match="assets do not match"):
        _TASK_SIDECAR_TOKEN_COUNTER(sidecar)
    assert loaded == 0


def test_task_tokenizer_failed_postload_digest_does_not_poison_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _candidate_row, sidecar = _cyber_candidate(tmp_path)
    digests = iter((TOKENIZER_ASSET_SHA256, "f" * 64))
    loaded: list[object] = []
    monkeypatch.setattr(
        taskpromotion_module,
        "_APPROVED_EXACT_TOKENIZERS",
        {(TOKENIZER_MODEL_ID, TOKENIZER_REVISION)},
    )

    def load(_model_id: str, _revision: str) -> object:
        tokenizer = object()
        loaded.append(tokenizer)
        return tokenizer

    monkeypatch.setattr(
        taskpromotion_module,
        "_resolved_local_tokenizer_revision",
        lambda *_: TOKENIZER_REVISION,
    )
    monkeypatch.setattr(
        taskpromotion_module,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda *_: next(digests),
    )
    monkeypatch.setattr(taskpromotion_module, "_load_replay_tokenizer_uncached", load)
    monkeypatch.setattr(
        taskpromotion_module, "_token_counter_for", lambda _tokenizer: _test_token_count
    )

    with pytest.raises(PromotionError, match="assets do not match"):
        _TASK_SIDECAR_TOKEN_COUNTER(sidecar)

    digests = iter((TOKENIZER_ASSET_SHA256, TOKENIZER_ASSET_SHA256))
    counter = _TASK_SIDECAR_TOKEN_COUNTER(sidecar)

    assert counter("abcd") == 1
    assert len(loaded) == 2


def _loaded_sidecar(
    tmp_path: Path,
    adapter: tuple[str, str, str],
    replay_payload: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[LoadedTaskReplaySidecar, dict[str, str]]:
    replay_payload = {
        **replay_payload,
        "candidate_content_commitments": sorted(
            (task_candidate_content_commitment(row) for row in candidates),
            key=lambda item: (item["world_id"], item["length_bucket"]),
        ),
    }
    raw = _canonical_bytes(
        build_task_replay_sidecar(
            adapter_id=adapter[0],
            adapter_revision=adapter[1],
            replay_payload=replay_payload,
            source_attestation_key=KEYS["source"],
        )
    )
    relative = f"{adapter[0]}.json"
    (tmp_path / relative).write_bytes(raw)
    binding = task_replay_sidecar_binding(raw, source_attestation_key=KEYS["source"])
    return (
        load_task_replay_sidecar(
            tmp_path,
            relative,
            binding,
            source_attestation_key=KEYS["source"],
        ),
        binding,
    )


def _kev_catalog() -> dict[str, Any]:
    vulnerabilities = []
    for index in range(24):
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
        "count": len(vulnerabilities),
        "vulnerabilities": vulnerabilities,
    }


def _cyber_candidate(
    tmp_path: Path,
    *,
    world_id: str = "cyber-kev-task-promotion-test",
) -> tuple[dict[str, Any], LoadedTaskReplaySidecar]:
    catalog = _kev_catalog()
    for vulnerability in catalog["vulnerabilities"]:
        vulnerability["shortDescription"] = "Observed exploited vulnerability " + (
            "distinct remediation chronology detail " * 400
        )
    [history] = build_kev_catalog_history_candidates(
        catalog,
        world_id=world_id,
        source_binding={
            "source_url": "https://www.cisa.gov/kev.json",
            "observed_at": "2026-08-30T00:00:00Z",
            "retrieval_sha256": "a" * 64,
            "signed_manifest_sha256": "b" * 64,
        },
        bands=(HistoryBand("16k", 16_000, 16_384),),
        token_counter=_test_token_count,
        tokenizer_model_id=TOKENIZER_MODEL_ID,
        tokenizer_revision=TOKENIZER_REVISION,
    )
    replay_binding = {
        "schema_version": KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
        "sha256": "e" * 64,
        "source_manifest_sha256": "b" * 64,
        "source_response_sha256": "a" * 64,
        "replay_revision": history["strict_replay_revision"],
    }
    provisional = build_kev_pipeline_candidate(
        history,
        token_counter=_test_token_count,
        tokenizer_asset_manifest_sha256=TOKENIZER_ASSET_SHA256,
        replay_manifest_binding=replay_binding,
        candidate_attestation_key=KEYS["candidate"],
        document_shards=5,
    )
    loaded, binding = _loaded_sidecar(
        tmp_path,
        CYBER_KEV_TASK_REPLAY_ADAPTER,
        {
            "source_manifest_sha256": "b" * 64,
            "source_response_sha256": "a" * 64,
            "replay_manifest_sha256": "e" * 64,
            "replay_revision": history["strict_replay_revision"],
            "tokenizer_model_id": TOKENIZER_MODEL_ID,
            "tokenizer_revision": TOKENIZER_REVISION,
            "tokenizer_asset_manifest_sha256": TOKENIZER_ASSET_SHA256,
        },
        [provisional],
    )
    candidate = build_kev_pipeline_candidate(
        history,
        token_counter=_test_token_count,
        tokenizer_asset_manifest_sha256=TOKENIZER_ASSET_SHA256,
        replay_manifest_binding=replay_binding,
        candidate_attestation_key=KEYS["candidate"],
        task_replay_sidecar_binding=binding,
        document_shards=5,
    )
    return candidate, loaded


def _financial_filings() -> tuple[FinancialFiling, ...]:
    filings = []
    roles = tuple(financehistory._SEMANTIC_ROLE_MARKERS)
    for year_index, year in enumerate((2021, 2022)):
        values = {
            "revenue": 1000 + year_index * 100,
            "operating_income": 100 + year_index * 10,
            "assets": 2000 + year_index * 200,
            "liabilities_and_equity": 2000 + year_index * 200,
            "cash_from_operations": 300 + year_index * 10,
            "cash_from_investing": -100,
            "cash_from_financing": -50,
            "cash_fx_effect": 0,
            "cash_period_change": 150 + year_index * 10,
        }
        rows = []
        for row_index, role in enumerate(roles):
            value = values[role]
            quote = f"({abs(value):,})" if value < 0 else f"{value:,}"
            markers = financehistory._SEMANTIC_ROLE_MARKERS[role]
            prefix = f"{markers[0]} | {markers[1]} | fiscal {year} | "
            text = prefix + quote + " | " + (f"source detail {year} {row_index} " * 8)
            rows.append(
                FinancialSourceRow(
                    record_id=f"filing-{year}:row:{row_index}",
                    filing_record_id=f"filing-{year}",
                    report_date=f"{year}-12-31",
                    source_url=f"https://issuer.example/{year}",
                    source_sha256=f"{year_index + 1}" * 64,
                    section=f"statement-{row_index // 4}",
                    source_char_start=row_index * 1000,
                    source_char_end=row_index * 1000 + len(text),
                    source_text=text,
                    facts=(
                        FinancialFact(
                            role=role,
                            evidence_quote=quote,
                            relative_start=len(prefix),
                        ),
                    ),
                )
            )
        for support_index in range(24):
            text = f"supporting note {year} {support_index} | " + (
                f"distinct disclosed term {year}-{support_index} " * 7
            )
            rows.append(
                FinancialSourceRow(
                    record_id=f"filing-{year}:support:{support_index}",
                    filing_record_id=f"filing-{year}",
                    report_date=f"{year}-12-31",
                    source_url=f"https://issuer.example/{year}",
                    source_sha256=f"{year_index + 1}" * 64,
                    section=f"note-{support_index // 4}",
                    source_char_start=20_000 + support_index * 1000,
                    source_char_end=20_000 + support_index * 1000 + len(text),
                    source_text=text,
                    facts=(),
                )
            )
        filings.append(
            FinancialFiling(
                record_id=f"filing-{year}",
                filing_date=f"{year + 1}-02-01",
                report_date=f"{year}-12-31",
                source_url=f"https://issuer.example/{year}",
                source_sha256=f"{year_index + 1}" * 64,
                rows=tuple(rows),
            )
        )
    return tuple(filings)


def _finance_candidate(
    tmp_path: Path,
) -> tuple[dict[str, Any], LoadedTaskReplaySidecar]:
    filings = []
    for filing in _financial_filings():
        rows = []
        source_cursor = 0
        for row in filing.rows:
            padding = (
                f" distinct long source evidence {row.record_id} " * 50
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
        world_id="finance-task-promotion-test",
        issuer_name="Example Issuer",
        cik="0000000001",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_ir_rendered_xbrl",
            "authorization_record_id": "AUTH-1",
        },
        bands=(HistoryBand("16k", 16_000, 16_384),),
        token_counter=_test_token_count,
        tokenizer_model_id=TOKENIZER_MODEL_ID,
        tokenizer_revision=TOKENIZER_REVISION,
    )
    history["tokenizer_asset_manifest_sha256"] = TOKENIZER_ASSET_SHA256
    provisional = build_finance_pipeline_candidate(history)
    loaded, binding = _loaded_sidecar(
        tmp_path,
        FINANCE_TASK_REPLAY_ADAPTER,
        {
            **history["source_binding"],
            "replay_revision": history["strict_replay_revision"],
            "tokenizer_model_id": TOKENIZER_MODEL_ID,
            "tokenizer_revision": TOKENIZER_REVISION,
            "tokenizer_asset_manifest_sha256": TOKENIZER_ASSET_SHA256,
        },
        [provisional],
    )
    candidate = build_finance_pipeline_candidate(
        history, task_replay_sidecar_binding=binding
    )
    return (
        attach_attestation(
            candidate,
            KEYS["candidate"],
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
        ),
        loaded,
    )


def _macro_candidate(
    tmp_path: Path,
) -> tuple[dict[str, Any], LoadedTaskReplaySidecar]:
    kwargs = {
        "manifest": _workflow_manifest(),
        "workflow_manifest_sha256": "a" * 64,
        "world_id": "macro-task-promotion-test",
        "target_series_id": "BEA_GDP_CURRENT_DOLLARS",
        "target_period": "2020Q1",
        "bands": (("16k", 16_000, 16_384),),
        "token_counter": _macro_token_count,
        "tokenizer_model_id": TOKENIZER_MODEL_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "tokenizer_asset_manifest_sha256": TOKENIZER_ASSET_SHA256,
    }
    [provisional] = build_macro_vintage_pipeline_candidates(**kwargs)
    provisional["source_verified_at_materialization"] = True
    provisional["source_attestation_verified"] = True
    provisional["real_source_verified"] = True
    source_binding = provisional["source_binding"]
    loaded, binding = _loaded_sidecar(
        tmp_path,
        MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
        {
            "workflow_manifest_sha256": source_binding[
                "workflow_manifest_sha256"
            ],
            "raw_source_sha256": source_binding["raw_source_sha256"],
            "fetch_inventory_sha256": source_binding[
                "fetch_inventory_sha256"
            ],
            "fetch_receipt": kwargs["manifest"]["fetch_receipt"],
            "source_families": source_binding["source_families"],
            "authorization_record_id": source_binding[
                "authorization_record_id"
            ],
            "replay_revision": provisional["strict_replay_revision"],
            "tokenizer_model_id": TOKENIZER_MODEL_ID,
            "tokenizer_revision": TOKENIZER_REVISION,
            "tokenizer_asset_manifest_sha256": TOKENIZER_ASSET_SHA256,
        },
        [provisional],
    )
    [candidate] = build_macro_vintage_pipeline_candidates(
        **kwargs,
        task_replay_sidecar_binding=binding,
    )
    candidate["source_verified_at_materialization"] = True
    candidate["source_attestation_verified"] = True
    candidate["real_source_verified"] = True
    return (
        attach_attestation(
            candidate,
            KEYS["candidate"],
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
        ),
        loaded,
    )


def _ranking(candidate: dict[str, Any]) -> dict[str, Any]:
    documents = str(candidate["document_context"]).split(SEP)
    classifications = candidate["artifact_classification"]
    return attach_attestation(
        {
            "schema_version": "dense-ranking-v2",
            "ranker_type": "dense_embedding",
            "query_id": candidate["query_id"],
            "candidate_sha256": candidate_sha256(candidate),
            "query_sha256": hashlib.sha256(candidate["question"].encode()).hexdigest(),
            "model": {
                "provider": "huggingface",
                "model_id": "sentence-transformers/all-MiniLM-L6-v2",
                "revision": MODEL_REVISION,
                "backend": "sentence-transformers-6.0.0",
                "score_metric": "dot_product",
                "chunking": {
                    "strategy": "tokenizer_token_windows",
                    "max_tokens": 192,
                    "overlap_tokens": 32,
                    "aggregation": "max_similarity",
                },
            },
            "artifacts": [
                {
                    "rank": rank,
                    "artifact_id": classification["artifact_id"],
                    "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                    "score": 1.0 - rank / 1000.0,
                    "chunk_count": 1,
                }
                for rank, (classification, document) in enumerate(
                    zip(classifications, documents, strict=True), start=1
                )
            ],
        },
        KEYS["ranker"],
        purpose=DENSE_RANKING_PURPOSE,
    )


def _with_forged_candidate_proof(candidate: dict[str, Any]) -> dict[str, Any]:
    unsigned = deepcopy(candidate)
    unsigned.pop("attestation", None)
    unsigned["verification"] = Verification(
        production_mode=False,
        candidate_mode=True,
        full_sufficient=True,
        minimal_sufficient=True,
        semantic_sufficient=True,
        strict_executable_sufficient=True,
        remove_one_fails=True,
        counterfactual_changes_answer=True,
        counterfactual_replay_sufficient=True,
        local_window_insufficient=True,
        closed_book_unsolved=True,
        distractor_invariance_gold=True,
        surface_match=True,
        schema_ok=True,
        no_shortcut=True,
        min_complexity=True,
        embedding_topk_insufficient=True,
        question_only_unsolved=True,
        essential_single_doc_insufficient=True,
        essential_surface_gold_free=True,
        essential_text_grounded=True,
    ).model_dump()
    unsigned["view_verification"] = {
        "expected_answer": unsigned["answer"],
        "strict_replay_answer": unsigned["answer"],
        "essential_present": True,
        "semantic_text_grounded": True,
        "classification_ok": True,
        "global_proof_green": True,
        "production_eligible": True,
    }
    return attach_attestation(
        unsigned,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )


@pytest.mark.parametrize("factory", (_cyber_candidate, _finance_candidate))
def test_task_dense_audit_replays_full_pool_but_rejects_dense_prefixes(
    tmp_path: Path,
    factory: Any,
) -> None:
    candidate, sidecar = factory(tmp_path)
    assert "token_counter" not in inspect.signature(create_task_dense_audit).parameters
    assert "token_counter" not in inspect.signature(promote_task_candidate).parameters

    audit = create_task_dense_audit(
        candidate,
        _ranking(candidate),
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        ranking_attestation_key=KEYS["ranker"],
        audit_attestation_key=KEYS["auditor"],
        source_attestation_key=KEYS["source"],
    )

    assert audit["embedding_topk_insufficient"] is True
    assert audit["full_pool_strict_replay_sufficient"] is True
    assert len(audit["task_semantic_commitment_sha256"]) == 64
    quality_metadata = audit["task_quality_metadata"]
    assert set(quality_metadata) == {
        "world_id",
        "domain",
        "length_bucket",
        "motif",
        "base_task_id",
        "executable_proof_id",
        "answer_program_id",
        "semantic_base_task_id",
        "real_source_verified",
        "real_source_family_ids",
        "real_source_workflow_ids",
        "real_source_token_ratio",
        "source_relation_edges",
        "source_relation_id",
        "authentic_source_relation_id",
        "hybrid_causal_edges",
        "context_source_relation_count",
    }
    assert quality_metadata["world_id"] == candidate["world_id"]
    assert quality_metadata["domain"] == candidate["domain"]
    assert quality_metadata["length_bucket"] == candidate["length_bucket"]
    assert quality_metadata["motif"] == candidate.get("motif")
    assert quality_metadata["answer_program_id"] == candidate.get("answer_program_id")
    assert quality_metadata["real_source_verified"] is True
    assert quality_metadata["real_source_family_ids"]
    assert quality_metadata["real_source_workflow_ids"]
    assert quality_metadata["authentic_source_relation_id"]
    assert audit["counterfactual_replay_answer"] == candidate["cf_answer"]
    assert candidate["answer"] not in audit["strict_replay_prefix_answers"]
    assert _selection_audit_matches_candidate(audit, candidate, 3)
    assert _candidate_has_source_bound_proof(candidate)
    assert _candidate_has_verified_real_source(candidate)
    receipt = audit["task_proof"]["task_proof_receipt"]
    essential_ids = receipt["essential_artifact_ids"]
    metrics = audit["strict_growth_metrics"]
    assert metrics["graph"]["n_essential_artifacts"] == len(essential_ids)
    assert metrics["graph"]["n_essential_events"] != candidate["event_count"]
    assert metrics["semantic_tokens"]["internal"] == _test_token_count(
        candidate["document_context"]
    )


def test_macro_task_dense_audit_uses_explicit_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        taskpromotion_module,
        "task_sidecar_token_counter",
        lambda _sidecar: _macro_token_count,
    )
    candidate, sidecar = _macro_candidate(tmp_path)

    audit = create_task_dense_audit(
        candidate,
        _ranking(candidate),
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        ranking_attestation_key=KEYS["ranker"],
        audit_attestation_key=KEYS["auditor"],
        source_attestation_key=KEYS["source"],
    )

    assert audit["adapter_audit"]["four_vintage_path"] is True
    assert audit["embedding_topk_insufficient"] is True
    assert audit["counterfactual_replay_answer"] == candidate["cf_answer"]
    assert audit["strict_growth_metrics"]["semantic_growth_group_id"].endswith(
        "|macro-vintage-history"
    )


def test_macro_candidate_passes_shared_content_identity_preflight(
    tmp_path: Path,
) -> None:
    candidate, _sidecar = _macro_candidate(tmp_path)

    validate_task_candidate_content_uniqueness(
        [candidate], label="Macro shared preflight"
    )


def test_task_growth_uses_full_replay_strict_support_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, sidecar = _cyber_candidate(tmp_path)
    original_replay = taskpromotion_module._replay_selection
    full_artifact_ids = {
        item["artifact_id"] for item in candidate["artifact_classification"]
    }
    replayed_strict_support = candidate["strict_support_event_count"] + 7

    def replay_with_distinct_support_truth(
        candidate_row: dict[str, Any],
        loaded_sidecar: LoadedTaskReplaySidecar,
        artifact_ids: Any,
        *,
        counterfactual: bool = False,
    ) -> dict[str, Any]:
        replay = original_replay(
            candidate_row,
            loaded_sidecar,
            artifact_ids,
            counterfactual=counterfactual,
        )
        if not counterfactual and set(artifact_ids) == full_artifact_ids:
            replay = {
                **replay,
                "strict_support_event_count": replayed_strict_support,
            }
        return replay

    monkeypatch.setattr(
        taskpromotion_module, "_replay_selection", replay_with_distinct_support_truth
    )

    audit = create_task_dense_audit(
        candidate,
        _ranking(candidate),
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        ranking_attestation_key=KEYS["ranker"],
        audit_attestation_key=KEYS["auditor"],
        source_attestation_key=KEYS["source"],
    )

    assert (
        audit["strict_growth_metrics"]["strict_support_event_count"]
        == replayed_strict_support
    )


def test_task_growth_rejects_invalid_full_replay_strict_support_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, sidecar = _cyber_candidate(tmp_path)
    original_replay = taskpromotion_module._replay_selection
    full_artifact_ids = {
        item["artifact_id"] for item in candidate["artifact_classification"]
    }

    def replay_without_support_truth(
        candidate_row: dict[str, Any],
        loaded_sidecar: LoadedTaskReplaySidecar,
        artifact_ids: Any,
        *,
        counterfactual: bool = False,
    ) -> dict[str, Any]:
        replay = original_replay(
            candidate_row,
            loaded_sidecar,
            artifact_ids,
            counterfactual=counterfactual,
        )
        if not counterfactual and set(artifact_ids) == full_artifact_ids:
            replay = {**replay, "strict_support_event_count": True}
        return replay

    monkeypatch.setattr(
        taskpromotion_module, "_replay_selection", replay_without_support_truth
    )

    with pytest.raises(PromotionError, match="strict support truth is invalid"):
        create_task_dense_audit(
            candidate,
            _ranking(candidate),
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )


def test_task_growth_rejects_candidate_relabelled_causal_supporting_artifacts(
    tmp_path: Path,
) -> None:
    candidate, sidecar = _finance_candidate(tmp_path)
    unsigned = deepcopy(candidate)
    unsigned.pop("attestation")
    support_index = next(
        index
        for index, item in enumerate(unsigned["artifact_classification"])
        if item["evidence_role"] == "natural_background"
    )
    unsigned["artifact_classification"][support_index]["evidence_role"] = (
        "causal_supporting"
    )
    support_artifact_id = unsigned["artifact_classification"][support_index][
        "artifact_id"
    ]
    unsigned["source_record_ids_by_artifact"] = {
        support_artifact_id: [unsigned["authentic_source_relation_edges"][0][0]]
    }
    replay_payload = dict(sidecar.replay_payload)
    replay_payload.pop("candidate_content_commitments")
    sidecar, binding = _loaded_sidecar(
        tmp_path,
        FINANCE_TASK_REPLAY_ADAPTER,
        replay_payload,
        [unsigned],
    )
    unsigned["task_replay_sidecar"] = binding
    candidate = attach_attestation(
        unsigned,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )

    with pytest.raises(PromotionError, match="source mapping is not authoritative"):
        create_task_dense_audit(
            candidate,
            _ranking(candidate),
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )


def test_task_promotion_uses_signed_auditor_proof_when_candidate_has_none(
    tmp_path: Path,
) -> None:
    candidate, sidecar = _cyber_candidate(tmp_path)
    audit = create_task_dense_audit(
        candidate,
        _ranking(candidate),
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        ranking_attestation_key=KEYS["ranker"],
        audit_attestation_key=KEYS["auditor"],
        source_attestation_key=KEYS["source"],
    )

    assert "verification" not in candidate
    assert audit["task_proof"]["task_proof_receipt"]["window_scope"] == (
        "exact_raw_slice_replay_with_separate_intersection_upper_bound"
    )
    with pytest.raises(PromotionError, match="requires signed release selection"):
        promote_task_candidate(
            candidate,
            audit,
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            audit_attestation_key=KEYS["auditor"],
            promotion_attestation_key=KEYS["promotion"],
            source_attestation_key=KEYS["source"],
        )


@pytest.mark.parametrize("factory", (_cyber_candidate, _finance_candidate))
def test_task_audit_preserves_replayed_relations_and_executable_graph(
    tmp_path: Path,
    factory: Any,
) -> None:
    candidate, sidecar = factory(tmp_path)
    audit = create_task_dense_audit(
        candidate,
        _ranking(candidate),
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        ranking_attestation_key=KEYS["ranker"],
        audit_attestation_key=KEYS["auditor"],
        source_attestation_key=KEYS["source"],
    )

    growth = audit["strict_growth_metrics"]
    assert (
        growth["authentic_source_relation_edges"]
        == candidate["authentic_source_relation_edges"]
    )
    assert growth["graph"]["proof_depth"] == candidate["graph"]["proof_depth"]
    assert growth["graph"]["hop_count"] == candidate["graph"]["hop_count"]
    if candidate["domain"] == "cyber":
        assert isinstance(growth["authentic_source_relation_edges"][0], list)


@pytest.mark.parametrize(
    "proof_field",
    (
        "adapter_audit",
        "adapter_audit_sha256",
        "task_proof",
        "task_proof_receipt",
        "task_proof_sha256",
        "task_quality_metadata",
        "task_semantic_commitment_sha256",
        "strict_replay_prefix_answers",
        "counterfactual_replay_answer",
        "full_pool_strict_replay_sufficient",
        "task_replay_payload_sha256",
        "source_binding_sha256",
        "strict_growth_metrics",
        "verification",
        "verification_replay_sha256",
        "view_verification",
        "promotion",
    ),
)
def test_task_candidate_rejects_declared_proof_fields(
    tmp_path: Path, proof_field: str
) -> None:
    candidate, sidecar = _cyber_candidate(tmp_path)
    unsigned = deepcopy(candidate)
    unsigned.pop("attestation")
    unsigned[proof_field] = None
    candidate = attach_attestation(
        unsigned, KEYS["candidate"], purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="must not declare proof fields"):
        create_task_dense_audit(
            candidate,
            _ranking(candidate),
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )


def test_task_dense_audit_rejects_sidecar_source_binding_or_body_corruption(
    tmp_path: Path,
) -> None:
    candidate, sidecar = _cyber_candidate(tmp_path)
    mismatched = deepcopy(candidate)
    mismatched.pop("attestation")
    mismatched["source_binding"]["retrieval_sha256"] = "f" * 64
    mismatched = attach_attestation(
        mismatched,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    with pytest.raises(PromotionError, match="sidecar payload|replay manifest"):
        create_task_dense_audit(
            mismatched,
            _ranking(mismatched),
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )

    corrupted = deepcopy(candidate)
    corrupted.pop("attestation")
    corrupted["document_context"] = corrupted["document_context"].replace(
        "Apply vendor remediation 0 immediately.", "Ignore remediation 0.", 1
    )
    corrupted = attach_attestation(
        corrupted,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    with pytest.raises(PromotionError, match="adapter audit|bind candidate content"):
        create_task_dense_audit(
            corrupted,
            _ranking(corrupted),
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )

    wrong_tokens = deepcopy(candidate)
    wrong_tokens.pop("attestation")
    wrong_tokens["tokenizer_context_tokens"] += 1
    wrong_tokens = attach_attestation(
        wrong_tokens,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    with pytest.raises(
        PromotionError, match="exact token count|bind candidate content"
    ):
        create_task_dense_audit(
            wrong_tokens,
            _ranking(wrong_tokens),
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )


def test_task_dense_audit_rejects_self_consistent_world_reusing_source_sidecar(
    tmp_path: Path,
) -> None:
    _original, original_sidecar = _cyber_candidate(tmp_path, world_id="world-original")
    forged, _forged_sidecar = _cyber_candidate(tmp_path, world_id="world-forged")
    unsigned = deepcopy(forged)
    unsigned.pop("attestation")
    unsigned["task_replay_sidecar"] = {
        "adapter_id": original_sidecar.adapter_id,
        "adapter_revision": original_sidecar.adapter_revision,
        "sidecar_schema_version": original_sidecar.sidecar_schema_version,
        "sha256": original_sidecar.sidecar_sha256,
    }
    forged = attach_attestation(
        unsigned,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )

    with pytest.raises(PromotionError, match="does not bind candidate content"):
        create_task_dense_audit(
            forged,
            _ranking(forged),
            original_sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )


def test_task_promotion_attestation_chain_fails_closed(tmp_path: Path) -> None:
    raw_candidate, sidecar = _cyber_candidate(tmp_path)
    unsigned = deepcopy(raw_candidate)
    unsigned.pop("attestation")
    with pytest.raises(PromotionError, match="candidate attestation"):
        create_task_dense_audit(
            unsigned,
            _ranking(raw_candidate),
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )

    candidate = raw_candidate
    ranking = _ranking(candidate)
    ranking["query_sha256"] = "f" * 64
    with pytest.raises(PromotionError, match="ranking attestation"):
        create_task_dense_audit(
            candidate,
            ranking,
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )

    audit = create_task_dense_audit(
        candidate,
        _ranking(candidate),
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        ranking_attestation_key=KEYS["ranker"],
        audit_attestation_key=KEYS["auditor"],
        source_attestation_key=KEYS["source"],
    )
    audit["expected_answer"] = "forged"
    with pytest.raises(PromotionError, match="audit attestation"):
        promote_task_candidate(
            candidate,
            audit,
            sidecar,
            candidate_attestation_key=KEYS["candidate"],
            audit_attestation_key=KEYS["auditor"],
            promotion_attestation_key=KEYS["promotion"],
            source_attestation_key=KEYS["source"],
        )


def test_shared_cli_dispatch_consumes_task_sidecar_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, sidecar = _cyber_candidate(tmp_path)
    ranking = _ranking(candidate)
    registry = {
        "episode_replay_bundles": {},
        "source_workflow_bundles": {},
        "task_replay_sidecars": {
            sidecar.sidecar_sha256: tmp_path / sidecar.relative_path
        },
    }
    audits, accepted, rejects = promotion_cli._audit_world(
        [(candidate, ranking)],
        3,
        None,
        None,
        KEYS["candidate"],
        KEYS["ranker"],
        KEYS["auditor"],
        KEYS["source"],
        False,
        registry,
    )

    assert len(audits) == 1
    assert audits[0]["task_replay_sidecar"] == candidate["task_replay_sidecar"]
    assert accepted == []
    assert rejects == []


def test_task_promotion_honors_signed_world_selection(tmp_path: Path) -> None:
    candidate, sidecar = _cyber_candidate(tmp_path)
    audit = create_task_dense_audit(
        candidate,
        _ranking(candidate),
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        ranking_attestation_key=KEYS["ranker"],
        audit_attestation_key=KEYS["auditor"],
        source_attestation_key=KEYS["source"],
    )
    digest = candidate_sha256(candidate)
    receipt = attach_attestation(
        {
            "schema_version": RELEASE_SELECTION_SCHEMA,
            "selected_candidate_sha256": [digest],
            "split_by_world": {candidate["world_id"]: "train"},
            "audit_sha256_by_candidate": {digest: serialized_row_sha256(audit)},
            "task_semantic_commitment_sha256_by_candidate": {
                digest: audit["task_semantic_commitment_sha256"]
            },
            "tokenizer_asset_manifest_sha256": TOKENIZER_ASSET_SHA256,
        },
        KEYS["auditor"],
        purpose="release_world_selection",
    )

    promoted = promote_task_candidate(
        candidate,
        audit,
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        audit_attestation_key=KEYS["auditor"],
        promotion_attestation_key=KEYS["promotion"],
        source_attestation_key=KEYS["source"],
        expected_split="train",
        release_selection_receipt=receipt,
    )

    assert promoted["train_ready"] is True
    assert Verification.model_validate(promoted["verification"]).all_green()


def test_macro_task_promotion_honors_signed_world_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        taskpromotion_module,
        "task_sidecar_token_counter",
        lambda _sidecar: _macro_token_count,
    )
    candidate, sidecar = _macro_candidate(tmp_path)
    audit = create_task_dense_audit(
        candidate,
        _ranking(candidate),
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        ranking_attestation_key=KEYS["ranker"],
        audit_attestation_key=KEYS["auditor"],
        source_attestation_key=KEYS["source"],
    )
    digest = candidate_sha256(candidate)
    receipt = attach_attestation(
        {
            "schema_version": RELEASE_SELECTION_SCHEMA,
            "selected_candidate_sha256": [digest],
            "split_by_world": {candidate["world_id"]: "train"},
            "audit_sha256_by_candidate": {digest: serialized_row_sha256(audit)},
            "task_semantic_commitment_sha256_by_candidate": {
                digest: audit["task_semantic_commitment_sha256"]
            },
            "tokenizer_asset_manifest_sha256": TOKENIZER_ASSET_SHA256,
        },
        KEYS["auditor"],
        purpose="release_world_selection",
    )

    promoted = promote_task_candidate(
        candidate,
        audit,
        sidecar,
        candidate_attestation_key=KEYS["candidate"],
        audit_attestation_key=KEYS["auditor"],
        promotion_attestation_key=KEYS["promotion"],
        source_attestation_key=KEYS["source"],
        expected_split="train",
        release_selection_receipt=receipt,
    )

    assert promoted["domain"] == "macro_economics"
    assert promoted["view"] == "ordered_release_timeline"
    assert promoted["composition_method"] == "as_of_revision_workflow"
    assert promoted["train_ready"] is True
    assert promoted["trust_scope"] == "local_probe"
    assert Verification.model_validate(promoted["verification"]).all_green()
