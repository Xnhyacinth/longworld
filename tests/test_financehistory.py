from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.materialize_finance_histories as finance_materializer
import scripts.prepare_finance_pipeline_candidates as finance_preparer
from longworld.core import financehistory
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)
from longworld.core.domainhistory import HistoryBand, audit_cumulative_history
from longworld.core.financehistory import (
    FinancialFact,
    FinancialFiling,
    FinancialSourceRow,
    _task_view_wrap_headroom,
    audit_finance_dense_ranking,
    audit_finance_pipeline_candidate,
    audit_financial_history_candidate,
    build_finance_pipeline_candidate,
    build_financial_history_candidates,
    extract_sec_financial_filings,
    replay_finance_pipeline_selection,
    replay_financial_history,
    _candidate_rows,
)
from longworld.core.pack import SEP
from longworld.core.promotion import candidate_sha256
from longworld.core.provenance import ProvenanceError
from longworld.core.taskreplaysidecar import (
    FINANCE_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
)

SOURCE_KEY = b"finance-source-test-key-material-at-least-32-bytes"
CANDIDATE_KEY = b"finance-candidate-test-key-material-at-least-32-bytes"


def _filings() -> tuple[FinancialFiling, ...]:
    filings = []
    roles = (
        "revenue",
        "operating_income",
        "assets",
        "liabilities_and_equity",
        "cash_from_operations",
        "cash_from_investing",
        "cash_from_financing",
        "cash_fx_effect",
        "cash_period_change",
    )
    for year_index, year in enumerate(range(2021, 2025)):
        rows = []
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


def _bands() -> tuple[HistoryBand, ...]:
    return (
        HistoryBand("16k", 20_000, 22_000),
        HistoryBand("32k", 40_000, 43_000),
        HistoryBand("64k", 70_000, 74_000),
    )


def _build() -> list[dict]:
    return build_financial_history_candidates(
        _filings(),
        world_id="finance-amazon-four-year-test",
        issuer_name="Example Issuer",
        cik="0000000001",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_ir_rendered_xbrl",
            "authorization_record_id": "AUTH-1",
        },
        bands=_bands(),
        token_counter=len,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="b" * 40,
    )


def test_builds_nested_multi_filing_financial_histories() -> None:
    rows = _build()

    assert [row["length_bucket"] for row in rows] == ["16k", "32k", "64k"]
    assert [row["selected_filing_count"] for row in rows] == [2, 3, 4]
    assert all(all(audit_financial_history_candidate(row).values()) for row in rows)
    assert audit_cumulative_history(rows) == []
    for before, after in pairwise(rows):
        assert after["context"].startswith(before["context"] + "\n")
        assert set(before["source_record_ids"]) < set(after["source_record_ids"])
        assert set(before["source_relation_ids"]) < set(after["source_relation_ids"])
        assert set(before["essential_evidence_ids"]) < set(
            after["essential_evidence_ids"]
        )
        assert len(before["authentic_source_relation_edges"]) < len(
            after["authentic_source_relation_edges"]
        )
        assert len(before["verified_derived_relation_edges"]) < len(
            after["verified_derived_relation_edges"]
        )
    for band, row in zip(_bands(), rows, strict=True):
        assert (
            band.lower_tokens
            <= row["tokenizer_context_tokens"]
            <= band.upper_tokens - _task_view_wrap_headroom(band.name)
        )
        assert row["train_ready"] is False
        assert row["complete_world"] is False
        assert row["real_source_verified"] is False
        assert row["source_verified_at_materialization"] is False
        assert "fact_ledger" not in json.loads(row["answer"])
        assert row["graph"]["proof_depth"] <= 10
        assert row["real_source_token_ratio"] == pytest.approx(
            row["semantic_tokens"]["event_bearing"] / row["semantic_tokens"]["internal"]
        )


def test_leftover_fill_skips_cover_page_identity_rows() -> None:
    shifted: list[FinancialFiling] = []
    for filing in _filings():
        cover = FinancialSourceRow(
            record_id=f"{filing.record_id}:cover:0",
            filing_record_id=filing.record_id,
            report_date=filing.report_date,
            source_url=filing.source_url,
            source_sha256=filing.source_sha256,
            section="Cover Page",
            source_char_start=0,
            source_char_end=len(f"cover identity {filing.report_date} unique dek"),
            source_text=f"cover identity {filing.report_date} unique dek",
            facts=(),
        )
        moved = []
        for row in filing.rows:
            delta = 1_000
            moved.append(
                FinancialSourceRow(
                    record_id=row.record_id,
                    filing_record_id=row.filing_record_id,
                    report_date=row.report_date,
                    source_url=row.source_url,
                    source_sha256=row.source_sha256,
                    section=row.section,
                    source_char_start=row.source_char_start + delta,
                    source_char_end=row.source_char_end + delta,
                    source_text=row.source_text,
                    facts=row.facts,
                )
            )
        shifted.append(
            FinancialFiling(
                record_id=filing.record_id,
                filing_date=filing.filing_date,
                report_date=filing.report_date,
                source_url=filing.source_url,
                source_sha256=filing.source_sha256,
                rows=(cover, *moved),
            )
        )
    rows = build_financial_history_candidates(
        tuple(shifted),
        world_id="finance-cover-page-leftover-test",
        issuer_name="Example Issuer",
        cik="0000000001",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_ir_rendered_xbrl",
            "authorization_record_id": "AUTH-1",
        },
        bands=_bands()[:1],
        token_counter=len,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="b" * 40,
        answer_program_id="finance.multi_filing_asset_trajectory.v1",
    )
    sections = {
        json.loads(line).get("section")
        for line in rows[0]["context"].splitlines()
        if json.loads(line).get("record_type") == "financial_source_row"
    }
    assert "Cover Page" not in sections
    assert rows[0]["tokenizer_context_tokens"] <= (
        _bands()[0].upper_tokens - _task_view_wrap_headroom(_bands()[0].name)
    )


def test_leftover_fill_skips_rows_before_first_fact() -> None:
    shifted: list[FinancialFiling] = []
    for filing in _filings():
        preamble = FinancialSourceRow(
            record_id=f"{filing.record_id}:preamble:0",
            filing_record_id=filing.record_id,
            report_date=filing.report_date,
            source_url=filing.source_url,
            source_sha256=filing.source_sha256,
            section="statement-preamble",
            source_char_start=0,
            source_char_end=len(f"preamble {filing.report_date} unique dek"),
            source_text=f"preamble {filing.report_date} unique dek",
            facts=(),
        )
        moved = []
        for row in filing.rows:
            delta = 1_000
            moved.append(
                FinancialSourceRow(
                    record_id=row.record_id,
                    filing_record_id=row.filing_record_id,
                    report_date=row.report_date,
                    source_url=row.source_url,
                    source_sha256=row.source_sha256,
                    section=row.section,
                    source_char_start=row.source_char_start + delta,
                    source_char_end=row.source_char_end + delta,
                    source_text=row.source_text,
                    facts=row.facts,
                )
            )
        shifted.append(
            FinancialFiling(
                record_id=filing.record_id,
                filing_date=filing.filing_date,
                report_date=filing.report_date,
                source_url=filing.source_url,
                source_sha256=filing.source_sha256,
                rows=(preamble, *moved),
            )
        )
    packed = build_financial_history_candidates(
        tuple(shifted),
        world_id="finance-preamble-leftover-test",
        issuer_name="Example Issuer",
        cik="0000000001",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_ir_rendered_xbrl",
            "authorization_record_id": "AUTH-1",
        },
        bands=_bands()[:1],
        token_counter=len,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="b" * 40,
        answer_program_id="finance.multi_filing_asset_trajectory.v1",
    )
    sections = {
        json.loads(line).get("section")
        for line in packed[0]["context"].splitlines()
        if json.loads(line).get("record_type") == "financial_source_row"
    }
    assert "statement-preamble" not in sections


def test_leftover_candidates_prefer_latest_filing_tail() -> None:
    filings = _filings()
    used = {
        row.record_id
        for filing in filings[:2]
        for row in filing.rows
        if row.facts
    }
    selected = {filings[0].record_id, filings[1].record_id}
    ordered = _candidate_rows(
        filings[:2],
        selected,
        used,
        {"revenue", "assets"},
        newest_first=True,
    )
    assert ordered
    assert ordered[0].report_date >= ordered[-1].report_date
    latest = [row for row in ordered if row.report_date == ordered[0].report_date]
    assert latest
    assert latest == sorted(
        latest,
        key=lambda row: (row.source_char_start, row.record_id),
        reverse=True,
    )
    earliest_first = _candidate_rows(
        filings[:2], selected, used, {"revenue", "assets"}
    )
    assert earliest_first
    assert earliest_first[0].report_date <= earliest_first[-1].report_date


def test_builds_distinct_multi_filing_asset_trajectory_program() -> None:
    rows = build_financial_history_candidates(
        _filings(),
        world_id="finance-microsoft-asset-trajectory-test",
        issuer_name="Microsoft Corporation",
        cik="0000789019",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_owned_sec_ixbrl",
            "authorization_record_id": "AUTH-MICROSOFT",
        },
        bands=_bands(),
        token_counter=len,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="b" * 40,
        answer_program_id="finance.multi_filing_asset_trajectory.v1",
    )

    assert {row["query_type"] for row in rows} == {"multi_filing_asset_trajectory"}
    assert {row["answer_program_id"] for row in rows} == {
        "finance.multi_filing_asset_trajectory.v1"
    }
    for row in rows:
        answer = json.loads(row["answer"])
        assert len(answer["annual_observations"]) == row["selected_filing_count"]
        assert all(
            set(observation)
            == {
                "report_date",
                "revenue",
                "assets",
                "liabilities_and_equity",
                "cash_from_operations",
            }
            for observation in answer["annual_observations"]
        )
        assert answer["cross_filing_asset_trajectory"] is not None
        assert answer["cross_filing_operating_cash_trajectory"] is not None
        assert answer["cross_filing_revenue_trajectory"] is not None
        assert all(
            set(check) == {"report_date", "balance_sheet_certified"}
            for check in answer["annual_checks"]
        )
        assert all(audit_financial_history_candidate(row).values())


def _filings_with_128k_tables() -> tuple[FinancialFiling, ...]:
    filings: list[FinancialFiling] = []
    for filing in _filings():
        extra_rows = list(filing.rows)
        year = filing.report_date[:4]
        for extra_index, role in enumerate(("product_revenue", "service_revenue")):
            markers = financehistory._SEMANTIC_ROLE_MARKERS[role]
            quote = f"{400 + extra_index:,}"
            prefix = f"{markers[0]} | {markers[1]} | fiscal {year} | "
            text = prefix + quote + " | " + (f"mix table {year} {role} " * 12)
            start = 50_000 + extra_index * 2_000
            extra_rows.append(
                FinancialSourceRow(
                    record_id=f"{filing.record_id}:mix:{role}",
                    filing_record_id=filing.record_id,
                    report_date=filing.report_date,
                    source_url=filing.source_url,
                    source_sha256=filing.source_sha256,
                    section=f"mix-table-{role}",
                    source_char_start=start,
                    source_char_end=start + len(text),
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
        for support_index in range(36):
            text = f"extra unique table {year} {support_index} | " + (
                f"branch cell {year}-{support_index} " * 18
            )
            start = 60_000 + support_index * 1_500
            extra_rows.append(
                FinancialSourceRow(
                    record_id=f"{filing.record_id}:extra:{support_index}",
                    filing_record_id=filing.record_id,
                    report_date=filing.report_date,
                    source_url=filing.source_url,
                    source_sha256=filing.source_sha256,
                    section=f"extra-table-{support_index // 6}",
                    source_char_start=start,
                    source_char_end=start + len(text),
                    source_text=text,
                    facts=(),
                )
            )
        filings.append(
            FinancialFiling(
                record_id=filing.record_id,
                filing_date=filing.filing_date,
                report_date=filing.report_date,
                source_url=filing.source_url,
                source_sha256=filing.source_sha256,
                rows=tuple(extra_rows),
            )
        )
    return tuple(filings)


def test_builds_128k_from_extra_unique_tables_without_fifth_filing() -> None:
    bands = (
        *_bands(),
        HistoryBand("128k", 100_000, 120_000),
    )
    rows = build_financial_history_candidates(
        _filings_with_128k_tables(),
        world_id="finance-microsoft-asset-trajectory-128k-test",
        issuer_name="Microsoft Corporation",
        cik="0000789019",
        source_binding={
            "signed_manifest_sha256": "a" * 64,
            "source_family": "issuer_owned_sec_ixbrl",
            "authorization_record_id": "AUTH-MICROSOFT",
        },
        bands=bands,
        token_counter=len,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="b" * 40,
        answer_program_id="finance.multi_filing_asset_trajectory.v1",
    )

    assert [row["length_bucket"] for row in rows] == ["16k", "32k", "64k", "128k"]
    assert [row["selected_filing_count"] for row in rows] == [2, 3, 4, 4]
    assert audit_cumulative_history(rows) == []
    long_row = rows[-1]
    answer = json.loads(long_row["answer"])
    assert answer["table_topology"]["branches"]
    assert answer["table_topology"]["year_joins"]
    assert any("cashflow_reconciled" in check for check in answer["annual_checks"])
    assert any(
        "product_service_mix_reconciled" in check for check in answer["annual_checks"]
    )
    assert all(all(audit_financial_history_candidate(row).values()) for row in rows)
    for before, after in pairwise(rows):
        assert after["context"].startswith(before["context"] + "\n")
        assert set(before["source_record_ids"]) < set(after["source_record_ids"])
        assert set(before["source_relation_ids"]) < set(after["source_relation_ids"])
        assert set(before["essential_evidence_ids"]) < set(after["essential_evidence_ids"])
        assert after["graph"]["proof_depth"] > before["graph"]["proof_depth"]


def test_extracts_financial_rows_from_verified_sec_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def filing(year: int, value: int) -> dict:
        rows = [
            (
                "total_revenue",
                "revenue",
                "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                value,
            ),
            ("assets", "assets", "us-gaap:Assets", value * 2),
            (
                "liabilities_and_equity",
                "liabilities_and_equity",
                "us-gaap:LiabilitiesAndStockholdersEquity",
                value * 2,
            ),
            (
                "cfo",
                "cash_from_operations",
                "us-gaap:NetCashProvidedByUsedInOperatingActivities",
                value // 2,
            ),
        ]
        source = "".join(
            f'<tr><td><ix:nonFraction name="{concept}">{amount:,}'
            f"</ix:nonFraction></td></tr>"
            for _program_role, _role, concept, amount in rows
        )
        roles = {}
        cursor = 0
        for program_role, _role, concept, amount in rows:
            marker = f'name="{concept}">'
            quote = f"{amount:,}"
            start = source.index(marker, cursor) + len(marker)
            roles[program_role] = SimpleNamespace(
                concept=concept,
                evidence_quote=quote,
                char_start=start,
                char_end=start + len(quote),
            )
            cursor = start + len(quote)
        return {
            "record_id": f"sec:{year}",
            "filing_date": f"{year + 1}-02-01",
            "report_date": f"{year}-12-31",
            "source_url": f"https://issuer.example/{year}",
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "text_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "parser": "issuer_gcs_merged_html@2",
            "cik": "0000789019",
            "text": source,
            "_program": SimpleNamespace(
                sections=(
                    SimpleNamespace(
                        section_id="statements",
                        char_start=0,
                        char_end=len(source),
                    ),
                ),
                roles=roles,
            ),
        }

    filings = [filing(2022, 1000), filing(2023, 1100)]
    programs = {item["report_date"]: item.pop("_program") for item in filings}
    monkeypatch.setattr(
        financehistory,
        "parse_sec_financial_program",
        lambda _text, _digest, *, report_date, parser_revision: programs[report_date],
    )
    manifest = {
        "schema_version": "longworld.sec-filing-manifest.v1",
        "source_status": "issuer_owned_public_export",
        "filings": filings,
    }

    extracted = extract_sec_financial_filings(manifest, cik="0000789019")

    assert len(extracted) == 2
    assert [len(item.rows) for item in extracted] == [4, 4]
    assert [
        sorted(fact.role for row in item.rows for fact in row.facts)
        for item in extracted
    ] == [
        ["assets", "cash_from_operations", "liabilities_and_equity", "revenue"],
        ["assets", "cash_from_operations", "liabilities_and_equity", "revenue"],
    ]


def test_adapts_financial_history_for_dense_ranking_and_strict_selection() -> None:
    row = _build()[0]

    candidate = build_finance_pipeline_candidate(row)

    documents = candidate["document_context"].split(SEP)
    classifications = candidate["artifact_classification"]
    assert len(documents) == len(classifications)
    assert candidate["query_id"].endswith(":16k:full")
    assert candidate["pipeline_capabilities"] == {
        "dense_ranking": True,
        "finance_strict_replay": True,
        "generic_strict_replay": False,
        "generic_promotion": False,
    }
    assert candidate["source_family_ids"] == ["issuer_ir_rendered_xbrl"]
    assert all(url.startswith("https://") for url in candidate["source_urls"])
    assert (
        candidate["finance_state"]["filing_chain"]
        == json.loads(row["answer"])["filing_chain"]
    )
    assert all(audit_finance_pipeline_candidate(candidate).values())

    corrupted = deepcopy(candidate)
    corrupted["document_context"] = corrupted["document_context"].replace(
        "1,000", "9,000", 1
    )
    assert not audit_finance_pipeline_candidate(corrupted)["document_binding_valid"]

    essentials = candidate["essential_artifact_ids"]
    assert (
        replay_finance_pipeline_selection(candidate, essentials)["answer"]
        == row["answer"]
    )
    for removed in essentials:
        assert (
            replay_finance_pipeline_selection(
                candidate, [value for value in essentials if value != removed]
            )["answer"]
            != row["answer"]
        )


def test_dense_ranking_audit_replays_ranked_finance_documents() -> None:
    candidate = build_finance_pipeline_candidate(_build()[0])
    documents = candidate["document_context"].split(SEP)
    classifications = candidate["artifact_classification"]
    ranking = {
        "schema_version": "dense-ranking-v2",
        "ranker_type": "dense_embedding",
        "query_id": candidate["query_id"],
        "candidate_sha256": candidate_sha256(candidate),
        "query_sha256": hashlib.sha256(candidate["question"].encode()).hexdigest(),
        "artifacts": [
            {
                "rank": rank,
                "artifact_id": classification["artifact_id"],
                "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                "score": 1.0 - rank / 1000,
                "chunk_count": 1,
            }
            for rank, (classification, document) in enumerate(
                zip(classifications, documents, strict=True), start=1
            )
        ],
    }

    audit = audit_finance_dense_ranking(candidate, ranking, k=3)

    assert audit["embedding_topk_insufficient"] is True
    assert audit["full_pool_strict_replay_sufficient"] is True
    assert audit["generic_promotion_ready"] is False


def _write_finance_sidecar(path: Path, rows: list[dict]) -> None:
    source_binding = rows[0]["source_binding"]
    for row in rows:
        row["tokenizer_asset_manifest_sha256"] = "d" * 64
    commitments = sorted(
        (
            task_candidate_content_commitment(build_finance_pipeline_candidate(row))
            for row in rows
        ),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    sidecar = build_task_replay_sidecar(
        adapter_id=FINANCE_TASK_REPLAY_ADAPTER[0],
        adapter_revision=FINANCE_TASK_REPLAY_ADAPTER[1],
        replay_payload={
            "signed_manifest_sha256": source_binding["signed_manifest_sha256"],
            "source_family": source_binding["source_family"],
            "authorization_record_id": source_binding["authorization_record_id"],
            "replay_revision": FINANCE_TASK_REPLAY_ADAPTER[1],
            "tokenizer_model_id": rows[0]["tokenizer_model_id"],
            "tokenizer_revision": rows[0]["tokenizer_revision"],
            "tokenizer_asset_manifest_sha256": "d" * 64,
            "candidate_content_commitments": commitments,
        },
        source_attestation_key=SOURCE_KEY,
    )
    path.write_text(
        json.dumps(sidecar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def test_preparer_verifies_and_binds_materializer_finance_task_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-finance-source-v1")
    monkeypatch.setenv(ROLE_KEY_ENVS["candidate"], CANDIDATE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["candidate"], "probe-finance-candidate-v1")
    monkeypatch.setattr(
        finance_preparer,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda model_id, revision: "d" * 64,
    )
    input_path = tmp_path / "histories.jsonl"
    rows = _build()
    _write_finance_sidecar(tmp_path / "TASK_REPLAY_SIDECAR.json", rows)
    input_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    output_dir = tmp_path / "out"

    report = finance_preparer.prepare(input_path, output_dir)
    candidates = [
        json.loads(line)
        for line in (output_dir / "candidates.jsonl").read_text().splitlines()
    ]
    sidecar_path = output_dir / "TASK_REPLAY_SIDECAR.json"
    binding = candidates[0]["task_replay_sidecar"]
    loaded = load_task_replay_sidecar(
        output_dir, sidecar_path.name, binding, source_attestation_key=SOURCE_KEY
    )

    assert report["task_replay_sidecar_sha256"] == binding["sha256"]
    assert all(row["task_replay_sidecar"] == binding for row in candidates)
    assert loaded.replay_payload["signed_manifest_sha256"] == "a" * 64
    assert loaded.replay_payload["source_family"] == "issuer_ir_rendered_xbrl"
    registry = json.loads(
        (output_dir / "REPLAY_PATH_REGISTRY.json").read_text(encoding="utf-8")
    )
    assert registry["task_replay_sidecars"] == {
        binding["sha256"]: "TASK_REPLAY_SIDECAR.json"
    }


def test_preparer_refuses_to_mint_missing_source_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-finance-source-v1")
    monkeypatch.setenv(ROLE_KEY_ENVS["candidate"], CANDIDATE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["candidate"], "probe-finance-candidate-v1")
    monkeypatch.setattr(
        finance_preparer,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda model_id, revision: "d" * 64,
    )
    input_path = tmp_path / "histories.jsonl"
    input_path.write_text(
        "".join(json.dumps(row) + "\n" for row in _build()), encoding="utf-8"
    )

    with pytest.raises(ProvenanceError, match="cannot read task replay sidecar"):
        finance_preparer.prepare(input_path, tmp_path / "out")


def test_materializer_emits_source_sidecar_after_manifest_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-finance-source-v1")
    source_path = tmp_path / "issuer.json"
    source_path.write_text('{"signed":"source-bytes"}\n', encoding="utf-8")
    manifest = {
        "source_family": "issuer_ir_rendered_xbrl",
        "authorization": {"record_id": "AUTH-1"},
        "issuer": {"name": "Example Issuer", "cik": "0000000001"},
    }
    verified_bytes: list[bytes] = []

    def verified_loader(raw: bytes) -> dict:
        verified_bytes.append(raw)
        return manifest

    class CharacterTokenizer:
        def encode(self, text: str, *, add_special_tokens: bool) -> list[str]:
            assert add_special_tokens is False
            return list(text)

    monkeypatch.setattr(
        finance_materializer, "load_issuer_ir_filing_manifest_bytes", verified_loader
    )
    monkeypatch.setattr(
        finance_materializer, "extract_financial_filings", lambda _: _filings()
    )
    monkeypatch.setattr(
        finance_materializer, "_load_tokenizer", lambda *_: CharacterTokenizer()
    )
    monkeypatch.setattr(
        finance_materializer,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda model_id, revision: "d" * 64,
    )
    config = {
        "schema_version": "longworld.finance-history-materialization.v1",
        "world_id": "finance-test",
        "signed_issuer_manifest": str(source_path),
        "tokenizer": {
            "model_id": "Qwen/Qwen3.5-4B",
            "revision": "b" * 40,
        },
        "bands": [
            {
                "name": band.name,
                "lower_tokens": band.lower_tokens,
                "upper_tokens": band.upper_tokens,
            }
            for band in _bands()
        ],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_dir = tmp_path / "history"

    report = finance_materializer.materialize(config_path, output_dir)

    assert verified_bytes == [source_path.read_bytes()]
    materialized_rows = [
        json.loads(line)
        for line in (output_dir / "candidates.jsonl").read_text().splitlines()
    ]
    assert all(row["source_verified_at_materialization"] for row in materialized_rows)
    binding = report["task_replay_sidecar"]
    loaded = load_task_replay_sidecar(
        output_dir,
        "TASK_REPLAY_SIDECAR.json",
        binding,
        source_attestation_key=SOURCE_KEY,
    )
    assert (
        loaded.replay_payload["signed_manifest_sha256"]
        == hashlib.sha256(source_path.read_bytes()).hexdigest()
    )


def test_materializer_accepts_verified_issuer_sec_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-finance-source-v1")
    source_path = tmp_path / "sec.json"
    source_path.write_text('{"signed":"sec-source-bytes"}\n', encoding="utf-8")
    manifest = {
        "schema_version": "longworld.sec-filing-manifest.v1",
        "source_status": "issuer_owned_public_export",
        "authorization": {"record_id": "AUTH-MICROSOFT"},
    }
    loaded_paths: list[Path] = []

    def verified_loader(path: Path) -> dict:
        loaded_paths.append(path)
        return manifest

    class CharacterTokenizer:
        def encode(self, text: str, *, add_special_tokens: bool) -> list[str]:
            assert add_special_tokens is False
            return list(text)

    monkeypatch.setattr(
        finance_materializer, "load_sec_filing_manifest", verified_loader
    )
    monkeypatch.setattr(
        finance_materializer,
        "extract_sec_financial_filings",
        lambda value, *, cik: _filings(),
    )
    monkeypatch.setattr(
        finance_materializer, "_load_tokenizer", lambda *_: CharacterTokenizer()
    )
    monkeypatch.setattr(
        finance_materializer,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda model_id, revision: "d" * 64,
    )
    config = {
        "schema_version": "longworld.finance-history-materialization.v1",
        "world_id": "finance-microsoft-test",
        "signed_issuer_manifest": str(source_path),
        "source_manifest_kind": "issuer_sec",
        "issuer": {"name": "Microsoft Corporation", "cik": "0000789019"},
        "answer_program_id": "finance.multi_filing_asset_trajectory.v1",
        "tokenizer": {
            "model_id": "Qwen/Qwen3.5-4B",
            "revision": "b" * 40,
        },
        "bands": [
            {
                "name": band.name,
                "lower_tokens": band.lower_tokens,
                "upper_tokens": band.upper_tokens,
            }
            for band in _bands()
        ],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    report = finance_materializer.materialize(config_path, tmp_path / "history")
    rows = [
        json.loads(line)
        for line in (tmp_path / "history/candidates.jsonl").read_text().splitlines()
    ]

    assert loaded_paths == [source_path]
    assert (
        report["source_manifest_sha256"]
        == hashlib.sha256(source_path.read_bytes()).hexdigest()
    )
    assert {row["answer_program_id"] for row in rows} == {
        "finance.multi_filing_asset_trajectory.v1"
    }
    assert {row["source_binding"]["source_family"] for row in rows} == {
        "issuer_owned_sec_ixbrl"
    }


def test_replay_recomputes_answer_cf_remove_one_and_corruption() -> None:
    row = _build()[0]
    replay = replay_financial_history(row)
    cf_replay = replay_financial_history(row, counterfactual=True)

    assert replay["answer"] == row["answer"]
    assert cf_replay["answer"] == row["cf_answer"]
    assert replay["answer"] != cf_replay["answer"]
    for essential in row["essential_evidence_ids"]:
        remaining = [
            value for value in row["essential_evidence_ids"] if value != essential
        ]
        assert (
            replay_financial_history(row, evidence_ids=remaining)["answer"]
            != row["answer"]
        )
        assert (
            replay_financial_history(row, evidence_ids=[essential])["answer"]
            != row["answer"]
        )

    corrupted = deepcopy(row)
    corrupted["context"] = corrupted["context"].replace("1,000", "9,000", 1)
    assert replay_financial_history(corrupted)["answer"] != row["answer"]
    assert not audit_financial_history_candidate(corrupted)["strict_replay_sufficient"]


def test_semantic_corruption_gate_replays_a_digest_consistent_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _build()[0]
    original_replay = financehistory.replay_financial_history
    corrupted_answers: list[str] = []

    def observed_replay(task: dict, **kwargs: object) -> dict:
        result = original_replay(task, **kwargs)
        if task is not row and task.get("context") != row["context"]:
            corrupted_answers.append(result["answer"])
        return result

    monkeypatch.setattr(financehistory, "replay_financial_history", observed_replay)

    audit = financehistory.audit_financial_history_candidate(row)

    assert audit["semantic_corruption_fails"]
    assert corrupted_answers
    assert any(answer not in {"unknown", row["answer"]} for answer in corrupted_answers)


def test_rejects_unknown_counterfactual_and_body_role_corruption() -> None:
    row = _build()[0]
    invalid_cf = deepcopy(row)
    invalid_cf["counterfactual_twin"]["record_id"] = "missing-record"
    invalid_cf["cf_answer"] = "unknown"
    assert not audit_financial_history_candidate(invalid_cf)[
        "counterfactual_replay_sufficient"
    ]

    corrupted = deepcopy(row)
    records = [json.loads(line) for line in corrupted["context"].splitlines()]
    target = next(
        record
        for record in records
        if record.get("record_type") == "financial_source_row"
        and any(fact["role"] == "cash_from_operations" for fact in record["facts"])
    )
    target["source_text"] = target["source_text"].replace(
        "operating activities", "unrelated activities"
    )
    target["source_text_sha256"] = hashlib.sha256(
        target["source_text"].encode()
    ).hexdigest()
    corrupted["context"] = "\n".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) for record in records
    )
    corrupted["context_sha256"] = hashlib.sha256(
        corrupted["context"].encode()
    ).hexdigest()

    assert replay_financial_history(corrupted)["answer"] == "unknown"
    assert audit_financial_history_candidate(row)["semantic_corruption_fails"]


def test_answer_requires_the_complete_filing_relation_chain() -> None:
    row = _build()[1]
    records = [json.loads(line) for line in row["context"].splitlines()]
    relation_ids = [
        record["relation_id"]
        for record in records
        if record.get("record_type") == "filing_relation"
    ]
    assert relation_ids
    for relation_id in relation_ids:
        corrupted = deepcopy(row)
        remaining = [
            record for record in records if record.get("relation_id") != relation_id
        ]
        corrupted["context"] = "\n".join(
            json.dumps(record, sort_keys=True, separators=(",", ":"))
            for record in remaining
        )
        corrupted["context_sha256"] = hashlib.sha256(
            corrupted["context"].encode()
        ).hexdigest()
        assert replay_financial_history(corrupted)["answer"] == "unknown"

    malformed_id = deepcopy(row)
    malformed_records = [
        json.loads(line) for line in malformed_id["context"].splitlines()
    ]
    relation = next(
        record
        for record in malformed_records
        if record.get("record_type") == "filing_relation"
    )
    relation["relation_id"] = "arbitrary-relation-id"
    malformed_id["context"] = "\n".join(
        json.dumps(record, sort_keys=True, separators=(",", ":"))
        for record in malformed_records
    )
    malformed_id["context_sha256"] = hashlib.sha256(
        malformed_id["context"].encode()
    ).hexdigest()
    assert replay_financial_history(malformed_id)["answer"] == "unknown"

    audit = audit_financial_history_candidate(row)
    assert audit["remove_one_relation_fails"]
    assert audit["relation_corruption_fails"]


def test_rejects_duplicate_source_rows_and_insufficient_real_capacity() -> None:
    filings = list(_filings())
    duplicated = filings[0].rows[0]
    filings[0] = FinancialFiling(
        record_id=filings[0].record_id,
        filing_date=filings[0].filing_date,
        report_date=filings[0].report_date,
        source_url=filings[0].source_url,
        source_sha256=filings[0].source_sha256,
        rows=(*filings[0].rows, duplicated),
    )
    with pytest.raises(ProvenanceError, match="duplicate financial source row"):
        build_financial_history_candidates(
            filings,
            world_id="duplicate",
            issuer_name="Example",
            cik="0000000001",
            source_binding={
                "signed_manifest_sha256": "a" * 64,
                "source_family": "issuer_ir_rendered_xbrl",
                "authorization_record_id": "AUTH-1",
            },
            bands=_bands(),
            token_counter=len,
            tokenizer_model_id="Qwen/Qwen3.5-4B",
            tokenizer_revision="b" * 40,
        )

    too_large = (HistoryBand("16k", 500_000, 510_000),)
    with pytest.raises(ProvenanceError, match="cannot fill exact 16k"):
        build_financial_history_candidates(
            _filings(),
            world_id="insufficient",
            issuer_name="Example",
            cik="0000000001",
            source_binding={
                "signed_manifest_sha256": "a" * 64,
                "source_family": "issuer_ir_rendered_xbrl",
                "authorization_record_id": "AUTH-1",
            },
            bands=too_large,
            token_counter=len,
            tokenizer_model_id="Qwen/Qwen3.5-4B",
            tokenizer_revision="b" * 40,
        )
