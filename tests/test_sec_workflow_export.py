from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from longworld.core.attestation import attach_attestation
from longworld.core.filingworkflow import (
    build_sec_filing_manifest,
    load_sec_filing_manifest,
)
from longworld.core.provenance import ProvenanceError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from export_sec_workflow import export_sec_workflow

TEST_KEY = b"sec-export-test-attestation-key-32-bytes"


def _source(tmp_path: Path, name: str = "filing.txt") -> tuple[Path, str]:
    text = (
        "ACME filed its annual report. Contact filings@example.com.\n"
        "Revenue for fiscal 2025 was USD 42 million after the audit adjustment.\n"
        "The material weakness was remediated on 2026-02-03.\n"
    )
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path, text


def _input(tmp_path: Path) -> dict:
    source, text = _source(tmp_path)
    quote = "Revenue for fiscal 2025 was USD 42 million"
    return {
        "schema_version": "longworld.sec-filing-input.v1",
        "source_status": "test_fixture",
        "authorization": {
            "record_id": "TEST-SEC-FIXTURE-001",
            "scope": "read-only fixture export",
            "basis": "test fixture only; not downloaded from SEC",
            "reviewed_at": "2026-08-24T10:00:00Z",
        },
        "filings": [
            {
                "accession": "0000000001-26-000001",
                "cik": "0000000001",
                "form": "10-K",
                "filing_date": "2026-02-20",
                "source_url": ("https://www.sec.gov/Archives/edgar/data/1/fixture.txt"),
                "source_file": source.name,
                "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "retrieved_at": "2026-08-24T09:00:00Z",
                "access_policy": "SEC public filing; fixture content is test-only",
                "parser": {"name": "sec_fixture_text", "version": "1"},
                "derived_facts": [
                    {
                        "fact_id": "fiscal_revenue",
                        "field": "revenue",
                        "value": "USD 42 million",
                        "evidence_quote": quote,
                        "evidence_char_start": text.index(quote),
                    }
                ],
            }
        ],
    }


def test_fixture_export_binds_sec_identity_source_hash_and_grounded_facts(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "manifest.json"
    input_path.write_text(json.dumps(_input(tmp_path)), encoding="utf-8")

    export_sec_workflow(
        input_path,
        output_path,
        attestation_key=TEST_KEY,
        generated_at="2026-08-24T11:00:00Z",
    )
    loaded = load_sec_filing_manifest(output_path, attestation_key=TEST_KEY)

    assert loaded["source_status"] == "test_fixture"
    assert loaded["data_stage"] == "source_inventory"
    assert loaded["hybrid_train_ready"] is False
    assert loaded["production_eligible"] is False
    filing = loaded["filings"][0]
    assert filing["accession"] == "0000000001-26-000001"
    assert filing["cik"] == "0000000001"
    assert filing["form"] == "10-K"
    assert filing["filing_date"] == "2026-02-20"
    assert filing["source_sha256"] == _input(tmp_path)["filings"][0]["source_sha256"]
    assert filing["provenance_id"] == f"sha256:{filing['source_sha256']}"
    assert filing["parser"] == "sec_fixture_text@1"
    assert filing["derived_facts"][0]["value"] == "USD 42 million"
    assert filing["derived_facts"][0]["source_sha256"] == filing["source_sha256"]
    assert "filings@example.com" not in filing["text"]
    assert "[redacted-email]" in filing["text"]


@pytest.mark.parametrize(
    "missing",
    (
        "source_url",
        "source_file",
        "source_sha256",
        "retrieved_at",
        "access_policy",
        "parser",
    ),
)
def test_export_rejects_missing_source_binding(tmp_path: Path, missing: str) -> None:
    payload = _input(tmp_path)
    payload["filings"][0].pop(missing)

    with pytest.raises(ProvenanceError, match="source"):
        build_sec_filing_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


@pytest.mark.parametrize("duplicate", ("accession", "source_sha256"))
def test_export_rejects_duplicate_filing_records(
    tmp_path: Path, duplicate: str
) -> None:
    payload = _input(tmp_path)
    second_source, second_text = _source(tmp_path, "second.txt")
    second = {
        **payload["filings"][0],
        "accession": "0000000001-26-000002",
        "source_file": second_source.name,
        "source_sha256": hashlib.sha256(second_text.encode()).hexdigest(),
    }
    if duplicate == "source_sha256":
        second["accession"] = "0000000001-26-000002"
    else:
        second["accession"] = payload["filings"][0]["accession"]
        second_source.write_text(second_text + "distinct", encoding="utf-8")
        second["source_sha256"] = hashlib.sha256(second_source.read_bytes()).hexdigest()
    payload["filings"].append(second)

    with pytest.raises(ProvenanceError, match=f"duplicate {duplicate}"):
        build_sec_filing_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_export_rejects_fact_not_grounded_at_declared_source_span(
    tmp_path: Path,
) -> None:
    payload = _input(tmp_path)
    payload["filings"][0]["derived_facts"][0]["value"] = "USD 99 million"

    with pytest.raises(ProvenanceError, match="derived fact value"):
        build_sec_filing_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_export_accepts_hash_bound_json_source(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    text = json.dumps(
        {"entity": "ACME", "revenue": "USD 42 million"}, ensure_ascii=False
    )
    source = tmp_path / "filing.json"
    source.write_text(text, encoding="utf-8")
    quote = '"revenue": "USD 42 million"'
    filing = payload["filings"][0]
    filing["source_file"] = source.name
    filing["source_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    filing["parser"] = {"name": "sec_fixture_json", "version": "1"}
    filing["derived_facts"][0]["evidence_quote"] = quote
    filing["derived_facts"][0]["evidence_char_start"] = text.index(quote)

    manifest = build_sec_filing_manifest(
        payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
    )

    assert manifest["filings"][0]["text"] == text
    assert manifest["filings"][0]["parser"] == "sec_fixture_json@1"


def test_export_rejects_source_hash_mismatch(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    source = tmp_path / payload["filings"][0]["source_file"]
    source.write_text(source.read_text(encoding="utf-8") + "changed", encoding="utf-8")

    with pytest.raises(ProvenanceError, match="source hash mismatch"):
        build_sec_filing_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_export_rejects_credential_shaped_source_text(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    source = tmp_path / payload["filings"][0]["source_file"]
    text = source.read_text(encoding="utf-8") + (
        "token ghp_abcdefghijklmnopqrstuvwxyz123456"
    )
    source.write_text(text, encoding="utf-8")
    payload["filings"][0]["source_sha256"] = hashlib.sha256(text.encode()).hexdigest()

    with pytest.raises(ProvenanceError, match="credential-shaped"):
        build_sec_filing_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_manifest_attestation_detects_post_export_tampering(tmp_path: Path) -> None:
    payload = build_sec_filing_manifest(
        _input(tmp_path), tmp_path, generated_at="2026-08-24T11:00:00Z"
    )
    signed = attach_attestation(payload, TEST_KEY, purpose="source_manifest")
    signed["filings"][0]["derived_facts"][0]["value"] = "USD 99 million"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(signed), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="attestation"):
        load_sec_filing_manifest(path, attestation_key=TEST_KEY)
