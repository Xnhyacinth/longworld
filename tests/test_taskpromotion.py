from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import sysconfig
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import scripts.project_task_candidate_views as task_view_cli
import scripts.promote_candidates as promotion_cli
from longworld.core import financehistory
from longworld.core import taskpromotion as taskpromotion_module
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ATTESTATION_ROLES,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
    verify_attestation,
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
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_RANKING_PURPOSE,
    RELEASE_SELECTION_SCHEMA,
    PromotionError,
    _candidate_has_source_bound_proof,
    _candidate_has_verified_real_source,
    _selection_audit_matches_candidate,
    _task_sidecar_matches_candidate,
    candidate_sha256,
    candidate_structural_preflight,
    select_release_worlds,
    serialized_row_sha256,
    task_semantic_commitment_sha256_from_audit,
    validate_task_candidate_content_uniqueness,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.taskpromotion import (
    build_task_candidate_view_projections,
    create_task_dense_audit,
    promote_task_candidate,
)
from longworld.core.taskproof import TaskProofError, audit_task_view_projection
from longworld.core.taskreplaysidecar import (
    CYBER_KEV_TASK_REPLAY_ADAPTER,
    FINANCE_TASK_REPLAY_ADAPTER,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    TASK_REPLAY_SIDECAR_PURPOSE,
    TASK_REPLAY_SIDECAR_SCHEMA_V2,
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
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


def test_cross_cve_v3_sidecar_matches_registered_cyber_candidate() -> None:
    candidate = {
        "domain": "cyber",
        "view": "full",
        "composition_method": "same_case_dossier",
        "strict_replay_revision": "longworld.cross-cve-remediation-replay.v1",
        "dossier_id": "cross-cve-dossier",
        "task_view_projection": {
            "schema_version": "longworld.task-view-projection.v1",
            "derivation_revision": "longworld.task-view-derivation.v4",
            "view": "full",
            "dossier_id": "cross-cve-dossier",
        },
    }
    binding = {
        "adapter_id": "cyber.cross_cve_remediation.v1",
        "adapter_revision": "longworld.cross-cve-remediation-replay.v1",
        "sidecar_schema_version": TASK_REPLAY_SIDECAR_SCHEMA_V3,
    }

    assert _task_sidecar_matches_candidate(candidate, binding)


def test_task_view_projection_cli_bootstraps_repo_without_editable_install(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys(
            (
                sysconfig.get_path("purelib"),
                sysconfig.get_path("platlib"),
            )
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(
                Path(__file__).resolve().parents[1]
                / "scripts"
                / "project_task_candidate_views.py"
            ),
            "--help",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


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
            "workflow_manifest_sha256": source_binding["workflow_manifest_sha256"],
            "raw_source_sha256": source_binding["raw_source_sha256"],
            "fetch_inventory_sha256": source_binding["fetch_inventory_sha256"],
            "fetch_receipt": kwargs["manifest"]["fetch_receipt"],
            "source_families": source_binding["source_families"],
            "authorization_record_id": source_binding["authorization_record_id"],
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
    assert (
        quality_metadata["motif"]
        == {
            "cyber": "chronology+annual_aggregation+remediation_window",
            "finance": (
                "multi_filing_trajectory+certification+cross_statement_reconciliation"
            ),
        }[candidate["domain"]]
    )
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


def test_executable_proof_identity_ignores_instance_labels_but_binds_topology(
    tmp_path: Path,
) -> None:
    candidate, sidecar = _finance_candidate(tmp_path)
    identifiers = taskpromotion_module._canonical_task_identifiers(
        candidate, sidecar.registry_key
    )

    renamed = deepcopy(candidate)
    renamed["essential_artifact_ids"] = [
        f"renamed-essential-{index}"
        for index, _value in enumerate(candidate["essential_artifact_ids"])
    ]
    node_names: dict[str, str] = {}
    for field in (
        "authentic_source_relation_edges",
        "verified_derived_relation_edges",
    ):
        renamed_relations: list[list[str]] = []
        for relation in candidate[field]:
            renamed_relation = deepcopy(relation)
            for index in (0, 1):
                value = str(relation[index])
                node_names.setdefault(value, f"renamed-node-{len(node_names)}")
                renamed_relation[index] = node_names[value]
            renamed_relations.append(renamed_relation)
        renamed[field] = renamed_relations

    changed_topology = deepcopy(candidate)
    changed_topology["authentic_source_relation_edges"][0][2] = (
        "independently_changed_relation"
    )

    assert (
        taskpromotion_module._canonical_task_identifiers(renamed, sidecar.registry_key)[
            "executable_proof_id"
        ]
        == identifiers["executable_proof_id"]
    )
    assert (
        taskpromotion_module._canonical_task_identifiers(
            changed_topology, sidecar.registry_key
        )["executable_proof_id"]
        != identifiers["executable_proof_id"]
    )


def test_finance_semantic_identity_binds_supported_answer_program() -> None:
    [history] = build_financial_history_candidates(
        _financial_filings(),
        world_id="finance-asset-trajectory-identity-test",
        issuer_name="Microsoft Corporation",
        cik="0000789019",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_owned_sec_ixbrl",
            "authorization_record_id": "AUTH-MICROSOFT",
        },
        bands=(HistoryBand("16k", 1_000, 20_000),),
        token_counter=_test_token_count,
        tokenizer_model_id=TOKENIZER_MODEL_ID,
        tokenizer_revision=TOKENIZER_REVISION,
        answer_program_id="finance.multi_filing_asset_trajectory.v1",
    )
    candidate = build_finance_pipeline_candidate(history)

    identifiers = taskpromotion_module._canonical_task_identifiers(
        candidate, FINANCE_TASK_REPLAY_ADAPTER
    )

    assert identifiers["answer_program_id"] == candidate["answer_program_id"]
    assert identifiers["motif"] == (
        "multi_filing_asset_and_operating_cash_trajectory+balance_sheet_certification"
    )

    forged = deepcopy(candidate)
    forged["finance_task"]["answer_program_id"] = "finance.unsupported.v1"
    with pytest.raises(PromotionError, match="finance answer program is unsupported"):
        taskpromotion_module._canonical_task_identifiers(
            forged, FINANCE_TASK_REPLAY_ADAPTER
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


def _candidate_task_views(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    token_counter: Any = _test_token_count,
) -> list[dict[str, Any]]:
    return build_task_candidate_view_projections(
        candidate,
        adapter_key=sidecar.registry_key,
        token_counter=token_counter,
        candidate_attestation_key=KEYS["candidate"],
    )


def _resign_v3_projection(
    output_dir: Path,
    sidecar: LoadedTaskReplaySidecar,
    original: dict[str, Any],
    forged: dict[str, Any],
) -> tuple[dict[str, Any], LoadedTaskReplaySidecar]:
    old_commitment = task_candidate_content_commitment(
        original, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3
    )
    new_commitment = task_candidate_content_commitment(
        forged, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3
    )
    payload = deepcopy(sidecar.replay_payload)
    payload["candidate_content_commitments"] = [
        new_commitment if item == old_commitment else item
        for item in payload["candidate_content_commitments"]
    ]
    payload["candidate_content_commitments"].sort(
        key=lambda item: (item["world_id"], item["length_bucket"], item["view"])
    )
    for receipt in payload["projection_derivation_receipts"]:
        if receipt["projection_content_commitment"] == old_commitment:
            receipt["projection_content_commitment"] = new_commitment
            receipt["projection_receipt"] = deepcopy(forged["task_view_projection"])
            receipt["projection_receipt_sha256"] = hashlib.sha256(
                json.dumps(
                    forged["task_view_projection"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
    payload["projection_derivation_receipts"].sort(
        key=lambda item: (
            item["projection_content_commitment"]["world_id"],
            item["projection_content_commitment"]["length_bucket"],
            item["projection_content_commitment"]["view"],
        )
    )
    signed_sidecar = build_task_replay_sidecar(
        adapter_id=sidecar.adapter_id,
        adapter_revision=sidecar.adapter_revision,
        replay_payload=payload,
        source_attestation_key=KEYS["source"],
        sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3,
    )
    raw = _canonical_bytes(signed_sidecar)
    path = output_dir / "FORGED_SOURCE_MEASUREMENT_SIDECAR_V3.json"
    path.write_bytes(raw)
    binding = task_replay_sidecar_binding(raw, source_attestation_key=KEYS["source"])
    rebound = deepcopy(forged)
    rebound.pop("attestation", None)
    rebound["task_replay_sidecar"] = binding
    rebound = attach_attestation(
        rebound, KEYS["candidate"], purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    return rebound, load_task_replay_sidecar(
        output_dir,
        path.name,
        binding,
        source_attestation_key=KEYS["source"],
    )


def _assert_standard_training_views(
    candidate: dict[str, Any], views: list[dict[str, Any]]
) -> None:

    by_view = {row["view"]: row for row in views}
    assert set(by_view) == {"full", "cf", "ordered_artifact_view"}
    assert by_view["full"]["dossier_id"] == by_view["cf"]["dossier_id"]
    assert by_view["full"]["document_context"] != by_view["cf"]["document_context"]
    full_ids = [
        value["artifact_id"] for value in by_view["full"]["artifact_classification"]
    ]
    cf_ids = [
        value["artifact_id"] for value in by_view["cf"]["artifact_classification"]
    ]
    ordered_ids = [
        value["artifact_id"]
        for value in by_view["ordered_artifact_view"]["artifact_classification"]
    ]
    assert full_ids == cf_ids
    assert full_ids != ordered_ids
    assert (
        by_view["full"]["document_context"]
        != by_view["ordered_artifact_view"]["document_context"]
    )
    assert len({row["context"] for row in views}) == 3
    assert by_view["full"]["answer"] == candidate["answer"]
    assert by_view["cf"]["answer"] == candidate["cf_answer"]
    assert by_view["cf"]["cf_answer"] == candidate["answer"]
    changed = [
        item
        for item in by_view["cf"]["artifact_classification"]
        if item["source_origin"] == "synthetic_counterfactual"
    ]
    assert len(changed) == 1
    assert changed[0]["counterfactual_parent_source_origin"] in {
        "real_public",
        "real_private_export",
        "real_derived",
    }
    assert changed[0]["counterfactual_parent_provenance_id"]
    assert changed[0]["counterfactual_parent_text_sha256"]
    assert changed[0]["counterfactual_parent_source_binding_sha256"]
    assert _candidate_has_source_bound_proof(by_view["cf"])
    only_changed = deepcopy(by_view["cf"])
    only_changed["artifact_classification"] = changed
    assert not _candidate_has_source_bound_proof(only_changed)
    assert all(row["train_ready"] is False for row in views)
    assert all(row["production_eligible"] is False for row in views)
    assert all(
        row["promotion_blocker_code"] == "task_view_source_commitment_pending"
        for row in views
    )
    assert len({candidate_sha256(row) for row in views}) == 3
    assert (
        len({task_candidate_content_commitment(row)["content_sha256"] for row in views})
        == 3
    )
    assert all(
        verify_attestation(
            row, KEYS["candidate"], purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        for row in views
    )
    assert (
        by_view["cf"]["task_view_projection"]["materialized_replay_answer_sha256"]
        == hashlib.sha256(candidate["cf_answer"].encode()).hexdigest()
    )
    chronology = by_view["ordered_artifact_view"]["task_view_projection"]["chronology"]
    assert chronology == sorted(chronology, key=lambda item: item["order_key"])
    forged_order = deepcopy(by_view["ordered_artifact_view"])
    forged_order["task_view_projection"]["chronology"] = [
        {"artifact_id": item["artifact_id"], "order_key": f"{index:08d}"}
        for index, item in enumerate(chronology)
    ]
    with pytest.raises(TaskProofError, match="projection_chronology_valid"):
        audit_task_view_projection(forged_order)

    reshuffled = deepcopy(by_view["ordered_artifact_view"])
    reshuffled_documents = list(reversed(reshuffled["document_context"].split(SEP)))
    reshuffled["document_context"] = SEP.join(reshuffled_documents)
    reshuffled["artifact_classification"] = list(
        reversed(reshuffled["artifact_classification"])
    )
    reshuffled["task_view_projection"]["document_context_sha256"] = hashlib.sha256(
        reshuffled["document_context"].encode()
    ).hexdigest()
    reshuffled["task_view_projection"]["artifact_bindings"] = [
        {
            "artifact_id": classification["artifact_id"],
            "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
        }
        for classification, document in zip(
            reshuffled["artifact_classification"],
            reshuffled_documents,
            strict=True,
        )
    ]
    reshuffled["task_view_projection"]["chronology"] = [
        {"artifact_id": item["artifact_id"], "order_key": f"{index:08d}"}
        for index, item in enumerate(reshuffled["artifact_classification"])
    ]
    with pytest.raises(TaskProofError, match="projection_chronology_valid"):
        audit_task_view_projection(reshuffled)

    forged_parent = deepcopy(by_view["cf"])
    forged_parent["artifact_classification"] = deepcopy(
        by_view["cf"]["artifact_classification"]
    )
    forged_changed = next(
        item
        for item in forged_parent["artifact_classification"]
        if item["source_origin"] == "synthetic_counterfactual"
    )
    forged_changed["counterfactual_parent_provenance_id"] = "sha256:" + "0" * 64
    with pytest.raises(TaskProofError, match="counterfactual_parent_binding_valid"):
        audit_task_view_projection(forged_parent)


@pytest.mark.parametrize("factory", (_finance_candidate, _cyber_candidate))
def test_task_promotion_materializes_standard_training_views(
    tmp_path: Path,
    factory: Any,
) -> None:
    candidate, sidecar = factory(tmp_path)

    _assert_standard_training_views(
        candidate, _candidate_task_views(candidate, sidecar)
    )


def test_task_view_difficulty_uses_replayed_view_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, sidecar = _finance_candidate(tmp_path)
    parent_depth = candidate["graph"]["proof_depth"]
    view_depth = 2 if parent_depth >= 3 else 3
    original_replay = taskpromotion_module._task_view_replay

    def replay_with_distinct_view_depth(
        candidate_row: dict[str, Any],
        adapter_key: tuple[str, str, str],
        artifact_ids: Any,
        *,
        counterfactual: bool = False,
    ) -> dict[str, Any]:
        replay = original_replay(
            candidate_row,
            adapter_key,
            artifact_ids,
            counterfactual=counterfactual,
        )
        if candidate_row.get("view") == "ordered_artifact_view" and not counterfactual:
            replay = deepcopy(replay)
            replay["proof_depth"] = view_depth
        return replay

    monkeypatch.setattr(
        taskpromotion_module, "_task_view_replay", replay_with_distinct_view_depth
    )

    ordered = next(
        row
        for row in _candidate_task_views(candidate, sidecar)
        if row["view"] == "ordered_artifact_view"
    )
    expected_dependency_class = (
        "deep_dependency"
        if view_depth >= 3
        else "long_range_retrieval"
        if ordered["difficulty"]["max_evidence_distance"] >= 8_000
        else "local_or_mixed"
    )

    assert ordered["graph"]["proof_depth"] == view_depth
    assert ordered["difficulty"]["proof_depth"] == view_depth
    assert ordered["dependency_class"] == expected_dependency_class


def test_macro_task_promotion_materializes_standard_training_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        taskpromotion_module,
        "task_sidecar_token_counter",
        lambda _sidecar: _macro_token_count,
    )
    candidate, sidecar = _macro_candidate(tmp_path)

    _assert_standard_training_views(
        candidate,
        _candidate_task_views(candidate, sidecar, token_counter=_macro_token_count),
    )


def test_task_view_projection_cli_is_deterministic_and_stays_candidate_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, sidecar = _finance_candidate(tmp_path)
    input_path = tmp_path / "candidates.jsonl"
    input_path.write_bytes(_canonical_bytes(candidate))
    output_dir = tmp_path / "projected"
    monkeypatch.setattr(
        task_view_cli,
        "task_sidecar_token_counter",
        lambda _sidecar: _test_token_count,
    )

    first = task_view_cli.project(
        input_path, tmp_path / sidecar.relative_path, output_dir
    )
    first_bytes = {
        path.name: path.read_bytes() for path in sorted(output_dir.iterdir())
    }
    second = task_view_cli.project(
        input_path, tmp_path / sidecar.relative_path, output_dir
    )

    assert first == second
    assert first["projection_candidate_count"] == 3
    assert first["train_ready"] is False
    assert first["requires_source_sidecar_rebuild"] is False
    assert first["dense_audit_complete"] is False
    assert first_bytes == {
        path.name: path.read_bytes() for path in sorted(output_dir.iterdir())
    }
    commitment_input = json.loads(
        (output_dir / "SOURCE_COMMITMENT_INPUT.json").read_text()
    )
    assert commitment_input["sidecar_schema_version"] == (
        "longworld.task-replay-sidecar.v3"
    )
    assert commitment_input["required_commitment_identity"] == [
        "world_id",
        "length_bucket",
        "view",
    ]

    projected = [
        json.loads(line)
        for line in (output_dir / "candidates.jsonl").read_text().splitlines()
    ]
    v3_raw = (output_dir / "TASK_REPLAY_SIDECAR_V3.json").read_bytes()
    v3_binding = task_replay_sidecar_binding(
        v3_raw, source_attestation_key=KEYS["source"]
    )
    v3_sidecar = load_task_replay_sidecar(
        output_dir,
        "TASK_REPLAY_SIDECAR_V3.json",
        v3_binding,
        source_attestation_key=KEYS["source"],
    )
    legacy_sidecar = deepcopy(v3_sidecar.signed_sidecar)
    legacy_sidecar.pop("attestation")
    legacy_payload = legacy_sidecar["replay_payload"]
    legacy_payload["projection_derivation_revision"] = (
        "longworld.task-view-derivation.v3"
    )
    for receipt in legacy_payload["projection_derivation_receipts"]:
        receipt["projection_receipt"]["derivation_revision"] = (
            "longworld.task-view-derivation.v3"
        )
        receipt["projection_receipt_sha256"] = hashlib.sha256(
            _canonical_bytes(receipt["projection_receipt"]).rstrip(b"\n")
        ).hexdigest()
    legacy_sidecar = attach_attestation(
        legacy_sidecar,
        KEYS["source"],
        purpose=TASK_REPLAY_SIDECAR_PURPOSE,
    )
    with pytest.raises(ProvenanceError, match="parent candidate binding"):
        task_replay_sidecar_binding(
            _canonical_bytes(legacy_sidecar),
            source_attestation_key=KEYS["source"],
        )
    ordered = next(row for row in projected if row["view"] == "ordered_artifact_view")
    reordered = deepcopy(ordered)
    reordered.pop("attestation")
    reordered_documents = list(reversed(reordered["document_context"].split(SEP)))
    reordered["document_context"] = SEP.join(reordered_documents)
    reordered["artifact_classification"] = list(
        reversed(reordered["artifact_classification"])
    )
    reordered["context"] = wrap_prompt(
        reordered["question"], reordered["document_context"], "first"
    )
    projection = reordered["task_view_projection"]
    projection["document_context_sha256"] = hashlib.sha256(
        reordered["document_context"].encode()
    ).hexdigest()
    projection["artifact_bindings"] = [
        {
            "artifact_id": classification["artifact_id"],
            "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
        }
        for classification, document in zip(
            reordered["artifact_classification"], reordered_documents, strict=True
        )
    ]
    projection["parent_artifact_bindings"] = list(
        reversed(projection["parent_artifact_bindings"])
    )
    replay_candidate = deepcopy(reordered)
    replay_candidate["context"] = "\n".join(
        [projection["adapter_context_header"], *reordered_documents]
    )
    artifact_ids = [
        classification["artifact_id"]
        for classification in reordered["artifact_classification"]
    ]
    replay = taskpromotion_module._task_view_replay(
        replay_candidate, FINANCE_TASK_REPLAY_ADAPTER, artifact_ids
    )
    for field in (
        "source_record_ids",
        "source_relation_ids",
        "authentic_source_relation_edges",
        "verified_derived_relation_edges",
        "event_count",
        "strict_support_event_count",
    ):
        reordered[field] = deepcopy(replay[field])
    reordered["graph"] = {
        **reordered["graph"],
        "proof_depth": replay["proof_depth"],
        "hop_count": replay["hop_count"],
    }
    reordered, reordered_sidecar = _resign_v3_projection(
        output_dir, v3_sidecar, ordered, reordered
    )
    with pytest.raises(PromotionError, match="adapter audit"):
        create_task_dense_audit(
            reordered,
            _ranking(reordered),
            reordered_sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )
    audits = [
        create_task_dense_audit(
            row,
            _ranking(row),
            v3_sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )
        for row in projected
    ]
    assert len(audits) == 3
    assert all(audit["full_pool_strict_replay_sufficient"] is True for audit in audits)
    cf_audit = next(
        audit
        for audit in audits
        if next(
            row
            for row in projected
            if candidate_sha256(row) == audit["candidate_sha256"]
        )["view"]
        == "cf"
    )
    assert 0.0 < cf_audit["task_quality_metadata"]["real_source_token_ratio"] < 1.0
    cf = next(row for row in projected if row["view"] == "cf")
    forged_measurement = deepcopy(cf)
    forged_measurement.pop("attestation")
    measurement = forged_measurement["task_view_projection"][
        "source_token_measurement_receipt"
    ]
    changed = next(
        item
        for item in measurement["parent_artifact_token_contributions"]
        if item["retained_as_real"] is False
    )
    retained = next(
        item
        for item in measurement["parent_artifact_token_contributions"]
        if item["retained_as_real"] is True
    )
    assert changed["token_contribution"] > 1
    shifted_tokens = changed["token_contribution"] // 2
    changed["token_contribution"] -= shifted_tokens
    retained["token_contribution"] += shifted_tokens
    measurement["retained_parent_document_tokens"] += shifted_tokens
    forged_measurement, forged_measurement_sidecar = _resign_v3_projection(
        output_dir, v3_sidecar, cf, forged_measurement
    )
    with pytest.raises(
        PromotionError, match="source-token measurement receipt is invalid"
    ):
        create_task_dense_audit(
            forged_measurement,
            _ranking(forged_measurement),
            forged_measurement_sidecar,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )
    forged_parent = deepcopy(v3_sidecar.parent_candidates[0])
    forged_parent["answer_program_id"] = "renamed.before.source.signing"
    with pytest.raises(PromotionError, match="parent candidate is not verified"):
        create_task_dense_audit(
            projected[0],
            _ranking(projected[0]),
            replace(v3_sidecar, parent_candidates=(forged_parent,)),
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )
    _accepted, _rejects = candidate_structural_preflight(
        projected,
        "p3-probe-12-v1",
        candidate_attestation_key=KEYS["candidate"],
    )
    legacy_derivation = deepcopy(
        next(row for row in projected if row["view"] == "full")
    )
    legacy_derivation.pop("attestation")
    legacy_derivation["task_view_projection"]["derivation_revision"] = (
        "longworld.task-view-derivation.v3"
    )
    legacy_derivation = attach_attestation(
        legacy_derivation,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    with pytest.raises(
        PromotionError, match="structural preflight task replay sidecar is invalid"
    ):
        candidate_structural_preflight(
            [legacy_derivation],
            "p13-authentic-six-domain-probe-12-v1",
            candidate_attestation_key=KEYS["candidate"],
        )
    rankings_path = tmp_path / "rankings.jsonl"
    rankings_path.write_bytes(
        b"".join(_canonical_bytes(_ranking(row)) for row in projected)
    )
    audit_manifest = task_view_cli.audit_projections(output_dir, rankings_path)
    audit_bytes = (output_dir / "audits.jsonl").read_bytes()
    assert audit_manifest["dense_audit_complete"] is True
    assert audit_manifest["audited_projection_count"] == 3
    assert task_view_cli.audit_projections(output_dir, rankings_path) == audit_manifest
    assert (output_dir / "audits.jsonl").read_bytes() == audit_bytes
    full = next(row for row in projected if row["view"] == "full")
    full_audit = next(
        audit for audit in audits if audit["candidate_sha256"] == candidate_sha256(full)
    )
    legacy_receipt_audit = deepcopy(full_audit)
    legacy_receipt_audit["task_proof"]["task_proof_receipt"]["schema_version"] = (
        "longworld.task-proof-receipt.v4"
    )
    with pytest.raises(PromotionError, match="task proof receipt schema"):
        task_semantic_commitment_sha256_from_audit(legacy_receipt_audit)
    full_digest = candidate_sha256(full)
    selection = attach_attestation(
        {
            "schema_version": RELEASE_SELECTION_SCHEMA,
            "selected_candidate_sha256": [full_digest],
            "split_by_world": {full["world_id"]: "train"},
            "audit_sha256_by_candidate": {
                full_digest: serialized_row_sha256(full_audit)
            },
            "task_semantic_commitment_sha256_by_candidate": {
                full_digest: full_audit["task_semantic_commitment_sha256"]
            },
            "tokenizer_asset_manifest_sha256": TOKENIZER_ASSET_SHA256,
        },
        KEYS["auditor"],
        purpose="release_world_selection",
    )
    promoted = promote_task_candidate(
        full,
        full_audit,
        v3_sidecar,
        candidate_attestation_key=KEYS["candidate"],
        audit_attestation_key=KEYS["auditor"],
        promotion_attestation_key=KEYS["promotion"],
        source_attestation_key=KEYS["source"],
        expected_split="train",
        release_selection_receipt=selection,
    )
    assert promoted["promotion"]["candidate_sha256"] == full_digest
    assert promoted["promotion"]["task_candidate_content_commitment"]["view"] == (
        "full"
    )
    assert promoted["hop_count"] == promoted["graph"]["hop_count"]
    renamed = deepcopy(full)
    renamed.pop("attestation")
    for field in (
        "motif",
        "answer_program_id",
        "executable_proof_id",
        "semantic_base_task_id",
    ):
        renamed[field] = "same-semantics-new-name"
    renamed = attach_attestation(
        renamed, KEYS["candidate"], purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    forged_payload = deepcopy(v3_sidecar.replay_payload)
    old_commitment = task_candidate_content_commitment(
        full, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3
    )
    renamed_commitment = task_candidate_content_commitment(
        renamed, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3
    )
    forged_payload["candidate_content_commitments"] = [
        renamed_commitment if item == old_commitment else item
        for item in forged_payload["candidate_content_commitments"]
    ]
    for receipt in forged_payload["projection_derivation_receipts"]:
        if receipt["projection_content_commitment"] == old_commitment:
            receipt["projection_content_commitment"] = renamed_commitment
    forged_sidecar_row = build_task_replay_sidecar(
        adapter_id=v3_sidecar.adapter_id,
        adapter_revision=v3_sidecar.adapter_revision,
        replay_payload=forged_payload,
        source_attestation_key=KEYS["source"],
        sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3,
    )
    forged_sidecar_path = output_dir / "FORGED_TASK_REPLAY_SIDECAR_V3.json"
    forged_sidecar_path.write_bytes(_canonical_bytes(forged_sidecar_row))
    forged_binding = task_replay_sidecar_binding(
        forged_sidecar_path.read_bytes(), source_attestation_key=KEYS["source"]
    )
    renamed = deepcopy(renamed)
    renamed.pop("attestation")
    renamed["task_replay_sidecar"] = forged_binding
    renamed = attach_attestation(
        renamed, KEYS["candidate"], purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    forged_loaded = load_task_replay_sidecar(
        output_dir,
        forged_sidecar_path.name,
        forged_binding,
        source_attestation_key=KEYS["source"],
    )
    with pytest.raises(PromotionError, match="semantic identifiers are not canonical"):
        create_task_dense_audit(
            renamed,
            _ranking(renamed),
            forged_loaded,
            candidate_attestation_key=KEYS["candidate"],
            ranking_attestation_key=KEYS["ranker"],
            audit_attestation_key=KEYS["auditor"],
            source_attestation_key=KEYS["source"],
        )
    substituted = deepcopy(next(row for row in projected if row["view"] == "full"))
    substituted.pop("attestation")
    substituted["view"] = "cf"
    substituted["composition_method"] = "counterfactual_twin"
    substituted = attach_attestation(
        substituted,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    with pytest.raises(PromotionError, match="does not bind candidate content"):
        taskpromotion_module._validate_candidate_identity(
            substituted,
            v3_sidecar,
            candidate_attestation_key=KEYS["candidate"],
            source_attestation_key=KEYS["source"],
        )
    with pytest.raises(
        PromotionError, match="structural preflight task replay sidecar is invalid"
    ):
        candidate_structural_preflight(
            [substituted],
            "p3-probe-12-v1",
            candidate_attestation_key=KEYS["candidate"],
        )

    downgraded = deepcopy(next(row for row in projected if row["view"] == "full"))
    downgraded.pop("attestation")
    downgraded["task_replay_sidecar"]["sidecar_schema_version"] = (
        sidecar.sidecar_schema_version
    )
    downgraded = attach_attestation(
        downgraded,
        KEYS["candidate"],
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    with pytest.raises(
        PromotionError, match="structural preflight task replay sidecar is invalid"
    ):
        candidate_structural_preflight(
            [downgraded],
            "p3-probe-12-v1",
            candidate_attestation_key=KEYS["candidate"],
        )

    v1_payload = {
        **{
            key: value
            for key, value in sidecar.replay_payload.items()
            if key != "candidate_content_commitments"
        },
        "candidate_content_commitments": [
            task_candidate_content_commitment(
                row, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V2
            )
            for row in projected
        ],
    }
    v1_payload["candidate_content_commitments"].sort(
        key=lambda item: (item["world_id"], item["length_bucket"], item["view"])
    )
    with pytest.raises(ProvenanceError, match="content commitments"):
        build_task_replay_sidecar(
            adapter_id=sidecar.adapter_id,
            adapter_revision=sidecar.adapter_revision,
            replay_payload=v1_payload,
            source_attestation_key=KEYS["source"],
        )


@pytest.mark.parametrize(
    ("factory", "token_counter"),
    (
        (_finance_candidate, _test_token_count),
        (_cyber_candidate, _test_token_count),
        (_macro_candidate, _macro_token_count),
    ),
)
def test_all_domain_standard_views_pass_independent_dense_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Any,
    token_counter: Any,
) -> None:
    monkeypatch.setattr(
        taskpromotion_module, "task_sidecar_token_counter", lambda _: token_counter
    )
    monkeypatch.setattr(
        task_view_cli, "task_sidecar_token_counter", lambda _: token_counter
    )
    candidate, sidecar = factory(tmp_path)
    input_path = tmp_path / "candidates.jsonl"
    input_path.write_bytes(_canonical_bytes(candidate))
    output_dir = tmp_path / "projected"
    task_view_cli.project(input_path, tmp_path / sidecar.relative_path, output_dir)
    projected = [
        json.loads(line)
        for line in (output_dir / "candidates.jsonl").read_text().splitlines()
    ]
    rankings_path = tmp_path / "rankings.jsonl"
    rankings_path.write_bytes(
        b"".join(_canonical_bytes(_ranking(row)) for row in projected)
    )

    manifest = task_view_cli.audit_projections(output_dir, rankings_path)

    assert manifest["audited_projection_count"] == 3
    assert manifest["dense_audit_complete"] is True
    audits = [
        json.loads(line)
        for line in (output_dir / "audits.jsonl").read_text().splitlines()
    ]
    assert {audit["candidate_sha256"] for audit in audits} == {
        candidate_sha256(row) for row in projected
    }
    audit_by_digest = {audit["candidate_sha256"]: audit for audit in audits}
    parent_ratio = candidate["real_source_token_ratio"]
    for row in projected:
        receipt = row["task_view_projection"]["source_token_measurement_receipt"]
        assert receipt["schema_version"] == (
            "longworld.source-token-measurement-receipt.v2"
        )
        assert receipt["measurement_basis"] == ("final_prompt_real_source_marginal")
        assert receipt["parent_real_source_token_ratio"] == parent_ratio
        assert (
            sum(
                item["token_contribution"]
                for item in receipt["parent_artifact_token_contributions"]
            )
            == receipt["parent_document_context_tokens"]
        )
        documents = row["document_context"].split(SEP)
        without_real_context = SEP.join(
            document
            for classification, document in zip(
                row["artifact_classification"], documents, strict=True
            )
            if classification["source_origin"]
            not in {"real_public", "real_private_export", "real_derived"}
        )
        final_prompt_tokens = token_counter(row["context"])
        without_real_prompt_tokens = token_counter(
            wrap_prompt(row["question"], without_real_context, row["query_timing"])
        )
        expected_source_tokens = final_prompt_tokens - without_real_prompt_tokens
        assert receipt["final_prompt_tokens"] == final_prompt_tokens
        assert receipt["without_real_prompt_tokens"] == without_real_prompt_tokens
        assert receipt["real_source_marginal_tokens"] == expected_source_tokens
        assert receipt["real_source_token_ratio"] == (
            expected_source_tokens / final_prompt_tokens
        )
        assert row["real_source_token_ratio"] == receipt["real_source_token_ratio"]
        assert (
            audit_by_digest[candidate_sha256(row)]["task_quality_metadata"][
                "real_source_token_ratio"
            ]
            == row["real_source_token_ratio"]
        )
        assert 0.0 < row["real_source_token_ratio"] <= 1.0
    ratio_by_view = {row["view"]: row["real_source_token_ratio"] for row in projected}
    assert ratio_by_view["cf"] < ratio_by_view["full"]


def test_v2_standard_views_are_historical_and_cannot_enter_p13_or_promotion(
    tmp_path: Path,
) -> None:
    parent, parent_sidecar = _finance_candidate(tmp_path)
    projections = _candidate_task_views(parent, parent_sidecar)
    commitments = sorted(
        (
            task_candidate_content_commitment(
                row, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V2
            )
            for row in projections
        ),
        key=lambda item: (item["world_id"], item["length_bucket"], item["view"]),
    )
    replay_payload = {
        **{
            key: value
            for key, value in parent_sidecar.replay_payload.items()
            if key != "candidate_content_commitments"
        },
        "candidate_content_commitments": commitments,
    }
    signed_sidecar = build_task_replay_sidecar(
        adapter_id=parent_sidecar.adapter_id,
        adapter_revision=parent_sidecar.adapter_revision,
        replay_payload=replay_payload,
        source_attestation_key=KEYS["source"],
        sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V2,
    )
    raw = _canonical_bytes(signed_sidecar)
    path = tmp_path / "TASK_REPLAY_SIDECAR_V2.json"
    path.write_bytes(raw)
    binding = task_replay_sidecar_binding(raw, source_attestation_key=KEYS["source"])
    loaded = load_task_replay_sidecar(
        tmp_path,
        path.name,
        binding,
        source_attestation_key=KEYS["source"],
    )
    rebound = []
    for projection in projections:
        projection = deepcopy(projection)
        projection.pop("attestation")
        projection["task_replay_sidecar"] = binding
        rebound.append(
            attach_attestation(
                projection,
                KEYS["candidate"],
                purpose=CANDIDATE_ATTESTATION_PURPOSE,
            )
        )

    with pytest.raises(
        PromotionError, match="structural preflight task replay sidecar is invalid"
    ):
        candidate_structural_preflight(
            rebound,
            "p3-probe-12-v1",
            candidate_attestation_key=KEYS["candidate"],
        )
    with pytest.raises(PromotionError, match="task replay sidecar binding is invalid"):
        select_release_worlds(
            rebound,
            [],
            "p3-probe-12-v1",
            candidate_attestation_key=KEYS["candidate"],
            audit_attestation_key=KEYS["auditor"],
        )
    with pytest.raises(PromotionError, match="production promotion requires v3"):
        promote_task_candidate(
            rebound[0],
            {},
            loaded,
            candidate_attestation_key=KEYS["candidate"],
            audit_attestation_key=KEYS["auditor"],
            promotion_attestation_key=KEYS["promotion"],
            source_attestation_key=KEYS["source"],
        )


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
    assert promoted["hop_count"] == promoted["graph"]["hop_count"]
    assert promoted["trust_scope"] == "local_probe"
    assert Verification.model_validate(promoted["verification"]).all_green()
