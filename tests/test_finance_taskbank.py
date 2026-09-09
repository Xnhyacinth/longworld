"""Semantic taskbank tests use small signed fixtures and the real execution contract."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from longworld.core import finance_taskbank as bank
from longworld.core.attestation import attach_attestation, verify_attestation
from longworld.core.issuerfilingworkflow import (
    IssuerIrMetricFact,
    IssuerIrRenderedProgram,
)
from longworld.core.provenance import ProvenanceError

KEY = b"finance-taskbank-test-source-key-at-least-32-bytes"


@pytest.fixture
def world(tmp_path, monkeypatch):
    programs = {}
    records = []
    for index, year in enumerate(range(2021, 2025)):
        values = {
            "revenue": [100, 130, 120, 160][index],
            "operating_income": [20, -5, 12, 32][index],
            "assets": [500, 600, 580, 700][index],
            "liabilities_and_equity": [500, 600, 580, 700][index],
            "cash_from_operations": [30, 40, -5, 80][index],
            "cash_from_investing": [-10, 5, -8, -12][index],
            "cash_from_financing": [-6, -7, 4, -10][index],
            "cash_fx_effect": [-1, 2, -1, 3][index],
        }
        values["cash_period_change"] = sum(
            values[r]
            for r in (
                "cash_from_operations",
                "cash_from_investing",
                "cash_from_financing",
                "cash_fx_effect",
            )
        )
        text = "<table><strong>Annual statements - USD ($) $ in Millions</strong><tr><th>12 Months Ended</th></tr>"
        facts = []
        for role, value in values.items():
            prefix = f"<tr><td>{role}</td><td>"
            quote = str(value)
            start = len(text) + len(prefix)
            text += prefix + quote + "</td></tr>"
            facts.append(
                IssuerIrMetricFact(
                    role=role,
                    numeric_value=value,
                    evidence_quote=quote,
                    char_start=start,
                    char_end=start + len(quote),
                    section="Annual statements",
                )
            )
        text += "</table>"
        digest = hashlib.sha256(text.encode()).hexdigest()
        programs[digest] = IssuerIrRenderedProgram(
            report_date=f"{year}-12-31",
            report_date_display=f"{year}-12-31",
            report_date_char_start=0,
            report_date_char_end=0,
            facts=tuple(facts),
            section_ranges=(("Annual statements", 0, len(text)),),
            source_sha256=digest,
        )
        records.append(
            {
                "record_id": f"doc-{year}",
                "report_date": f"{year}-12-31",
                "filing_date": f"{year + 1}-02-01",
                "source_url": f"https://issuer.test/{year}.html",
                "text": text,
                "source_sha256": digest,
            }
        )
    manifest = {
        "schema_version": "longworld.issuer-ir-filing-manifest.v1",
        "source_family": "issuer_ir_rendered_xbrl",
        "issuer": {"cik": "0000000001", "name": "Fixture Issuer"},
        "records": records,
    }
    path = tmp_path / "signed.json"
    path.write_text(
        json.dumps(attach_attestation(manifest, KEY, purpose="source_manifest"))
    )

    def verify(raw):
        parsed = json.loads(raw)
        if not verify_attestation(parsed, KEY, purpose="source_manifest"):
            raise ProvenanceError("invalid source signature")
        return parsed

    monkeypatch.setattr(bank, "load_issuer_ir_filing_manifest_bytes", verify)
    monkeypatch.setattr(
        bank,
        "parse_issuer_ir_rendered_metrics",
        lambda text, **kw: programs[hashlib.sha256(text.encode()).hexdigest()],
    )
    return bank.load_finance_world(path)


def test_many_deterministic_non_equivalent_tasks_with_evidence(world):
    result = bank.compile_finance_taskbank(world)
    assert len(result["tasks"]) >= 80
    assert len(result["metrics"]["by_family"]) >= 5
    assert result["metrics"]["within_filing_tasks"] >= 40
    ids = [t["semantic_task_id"] for t in result["tasks"]]
    assert len(ids) == len(set(ids))
    assert result == bank.compile_finance_taskbank(world)
    for task in result["tasks"]:
        assert bank.validate_finance_task(world, task)["ok"]
        assert bank.execute_finance_task(world, task) == task["answer"]
        assert all("text" not in document for document in task["evidence_documents"])
        assert task["split_group_id"] == world["issuer"]["cik"]
    assert json.loads(json.dumps(result))["metrics"]["accepted_tasks"] == len(ids)


def test_unverified_and_mutated_worlds_fail_closed(world):
    with pytest.raises(bank.FinanceTaskBankError):
        bank.compile_finance_taskbank(json.loads(json.dumps(world)))
    world["facts"][0]["value"] += 1
    with pytest.raises(bank.FinanceTaskBankError):
        bank.compile_finance_taskbank(world)


def test_program_scope_question_answer_and_evidence_are_bound(world):
    task = next(
        t
        for t in bank.compile_finance_taskbank(world)["tasks"]
        if t["family"] == "delta"
    )
    for field, value in [
        ("answer", {"value": 999, "unit": "USD_millions"}),
        ("question", task["question"].replace("later", "earlier")),
        ("consumed_fact_ids", []),
        ("semantic_task_id", "x"),
    ]:
        altered = deepcopy(task)
        altered[field] = value
        with pytest.raises(bank.FinanceTaskBankError):
            bank.validate_finance_task(world, altered)
    altered = deepcopy(task)
    altered["evidence_spans"][0]["start"] += 1
    with pytest.raises(bank.FinanceTaskBankError):
        bank.validate_finance_task(world, altered)


def test_same_fact_cannot_be_counted_twice(world):
    task = next(
        t
        for t in bank.compile_finance_taskbank(world)["tasks"]
        if t["family"] == "aggregate"
    )
    altered = deepcopy(task)
    altered["program"]["input"]["items"].append(
        deepcopy(altered["program"]["input"]["items"][0])
    )
    with pytest.raises(bank.FinanceTaskBankError, match="same_fact"):
        bank.execute_finance_task(world, altered)


def test_filters_and_ratios_consume_real_numeric_outputs(world):
    tasks = bank.compile_finance_taskbank(world)["tasks"]
    ratio = next(
        t
        for t in tasks
        if t["family"] == "ratio"
        and t["scope"]["filings"][0]["report_date"] == "2022-12-31"
        and "operating_income" in t["scope"]["metrics"]
    )
    assert ratio["answer"]["value"] == -385
    assert ratio["answer"]["numerator"] == -5 and ratio["answer"]["denominator"] == 130
    filtered = next(t for t in tasks if t["family"] == "filtered_aggregate")
    assert (
        len(filtered["consumed_fact_ids"]) > len(filtered["answer"]["period_ends"]) >= 2
    )
    assert (
        "causal" not in filtered["scope"]
        and filtered["scope"]["reporting_basis"] == "as_reported_in_filing"
    )


def test_stock_sums_and_constant_filters_are_rejected(world):
    result = bank.compile_finance_taskbank(world)
    assert result["metrics"]["by_rejection"].get("nonadditive_metric", 0) > 0
    assert result["metrics"]["by_rejection"].get("vacuous_filter", 0) > 0
    assert all(
        not (t["family"] == "aggregate" and t["scope"]["metrics"] == ["assets"])
        for t in result["tasks"]
    )


def test_source_authentication_is_rechecked_on_load(world):
    path = Path(world["source_manifest"]["path"])
    payload = json.loads(path.read_text())
    payload["records"][0]["text"] += "tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(ProvenanceError, match="signature"):
        bank.load_finance_world(path)


def test_same_source_cell_alias_does_not_create_more_tasks(world, monkeypatch):
    from dataclasses import replace

    original = bank.parse_issuer_ir_rendered_metrics

    def with_alias(text, **kwargs):
        program = original(text, **kwargs)
        asset = next(f for f in program.facts if f.role == "assets")
        return replace(
            program, facts=(*program.facts, replace(asset, role="stockholders_equity"))
        )

    baseline = bank.compile_finance_taskbank(world)
    monkeypatch.setattr(bank, "parse_issuer_ir_rendered_metrics", with_alias)
    aliased = bank.load_finance_world(world["source_manifest"]["path"])
    assert len(aliased["facts"]) == len(world["facts"])
    assert (
        sum(
            r["reason"] == "same_source_fact_alias"
            for r in aliased["normalization_rejections"]
        )
        == 4
    )
    assert [
        t["semantic_task_id"] for t in bank.compile_finance_taskbank(aliased)["tasks"]
    ] == [t["semantic_task_id"] for t in baseline["tasks"]]


def test_canonical_sum_is_order_independent_and_task_metadata_does_not_mutate_world(
    world,
):
    task = next(
        t
        for t in bank.compile_finance_taskbank(world)["tasks"]
        if t["family"] == "aggregate"
    )
    reverse = deepcopy(task["program"])
    reverse["input"]["items"].reverse()
    assert (
        bank._build_task(world, reverse)["semantic_task_id"] == task["semantic_task_id"]
    )
    task["scope"]["issuer"]["name"] = "changed task"
    task["evidence_spans"][0]["period"]["end"] = "1900-01-01"
    assert bank.compile_finance_taskbank(world)["metrics"]["accepted_tasks"] > 80
    with pytest.raises(bank.FinanceTaskBankError):
        bank.validate_finance_task(world, task)


def test_funnel_counts_and_complete_source_sections(world):
    result = bank.compile_finance_taskbank(world)
    assert result["metrics"]["draft_candidates"] == len(result["tasks"]) + len(
        result["rejections"]
    )
    for document in world["documents"]:
        for section in document["sections"]:
            assert document["text"][section["start"] : section["end"]].startswith(
                "<table>"
            )
    assert world["world_instance_id"] == world["world_id"]
    assert world["source_collection_id"]


def test_applicability_and_execution_funnels_have_separate_denominators(
    world, monkeypatch
):
    from dataclasses import replace

    original = bank.parse_issuer_ir_rendered_metrics

    def restricted_profile(text, **kwargs):
        program = original(text, **kwargs)
        return replace(
            program,
            facts=tuple(
                f
                for f in program.facts
                if f.role
                in {
                    "revenue",
                    "assets",
                    "liabilities_and_equity",
                    "cash_from_operations",
                }
            ),
        )

    monkeypatch.setattr(bank, "parse_issuer_ir_rendered_metrics", restricted_profile)
    limited = bank.load_finance_world(world["source_manifest"]["path"])
    result = bank.compile_finance_taskbank(limited)
    metrics = result["metrics"]
    assert (
        metrics["applicability_rejections"]
        == len(result["applicability_rejections"])
        > 0
    )
    assert (
        metrics["attempted_programs"]
        == metrics["accepted_tasks"] + metrics["rejected_tasks"]
    )
    assert (
        metrics["draft_candidates"]
        == metrics["attempted_programs"] + metrics["applicability_rejections"]
    )
    assert all(
        r["stage"] == "applicability" for r in result["applicability_rejections"]
    )
    assert all(
        r["stage"] in {"program_validation", "semantic_dedup"}
        for r in result["rejections"]
    )
    for stage in metrics["stages"].values():
        assert stage["input"] == stage["passed"] + stage["rejected"]


def test_numerically_equal_type_changes_do_not_bypass_verified_world(world):
    world["facts"][0]["value"] = float(world["facts"][0]["value"])
    with pytest.raises(bank.FinanceTaskBankError):
        bank.compile_finance_taskbank(world)


def test_answer_numeric_type_is_part_of_task_contract(world):
    task = next(
        t
        for t in bank.compile_finance_taskbank(world)["tasks"]
        if t["family"] == "lookup"
    )
    task["answer"]["value"] = float(task["answer"]["value"])
    with pytest.raises(bank.FinanceTaskBankError):
        bank.validate_finance_task(world, task)
