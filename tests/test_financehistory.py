from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from itertools import pairwise

import pytest

from longworld.core import financehistory
from longworld.core.domainhistory import HistoryBand, audit_cumulative_history
from longworld.core.financehistory import (
    FinancialFact,
    FinancialFiling,
    FinancialSourceRow,
    audit_financial_history_candidate,
    build_financial_history_candidates,
    replay_financial_history,
)
from longworld.core.provenance import ProvenanceError


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
        assert band.lower_tokens <= row["tokenizer_context_tokens"] <= band.upper_tokens
        assert row["train_ready"] is False
        assert row["complete_world"] is False
        assert row["real_source_verified"] is False
        assert row["source_verified_at_materialization"] is False
        assert "fact_ledger" not in json.loads(row["answer"])
        assert row["graph"]["proof_depth"] <= 10
        assert row["real_source_token_ratio"] == pytest.approx(
            row["semantic_tokens"]["event_bearing"] / row["semantic_tokens"]["internal"]
        )


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
