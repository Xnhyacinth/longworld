from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from longworld.core.attestation import attach_attestation, verify_attestation
from longworld.core.provenance import ProvenanceError
from longworld.core.regulationworkflow import (
    REGULATION_WORKFLOW_MANIFEST_SCHEMA,
    audit_regulation_workflow_manifest,
    build_regulation_workflow_from_fetch_inventory,
    load_regulation_workflow_manifest,
)
from scripts.export_regulation_workflow import export_regulation_workflow
from scripts.fetch_regulation_workflow import HttpResponse, fetch_regulation_workflow
from tests.test_fetch_regulation_workflow import (
    DOCUMENT_NUMBERS,
    _docket_body,
    _fr_body,
    _request,
)


def _inventory(tmp_path: Path) -> tuple[dict, Path]:
    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        body = (
            _fr_body(url.rsplit("/", 1)[-1].removesuffix(".json"))
            if "federalregister.gov" in url
            else _docket_body()
        )
        return HttpResponse(
            body,
            200,
            url,
            "application/vnd.api+json"
            if "api.regulations.gov" in url
            else "application/json",
        )

    observed = iter(
        [
            "2026-08-29T01:00:01Z",
            "2026-08-29T01:00:02Z",
            "2026-08-29T01:00:03Z",
            "2026-08-29T01:00:04Z",
            "2026-08-29T01:00:05Z",
        ]
    )
    path = fetch_regulation_workflow(
        _request(tmp_path),
        tmp_path / "inventory",
        api_key="test-key",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-29T01:00:00Z",
        clock=observed.__next__,
    )
    return json.loads(path.read_text()), path


def _build(tmp_path: Path) -> dict:
    inventory, path = _inventory(tmp_path)
    return build_regulation_workflow_from_fetch_inventory(
        inventory,
        path.parent,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def test_builds_disabled_source_bound_rulemaking_sequence(tmp_path: Path) -> None:
    manifest = _build(tmp_path)
    assert manifest["schema_version"] == REGULATION_WORKFLOW_MANIFEST_SCHEMA
    assert manifest["generation_integration"] == "disabled"
    assert manifest["semantic_facts_train_ready"] is False
    assert manifest["production_eligible"] is False
    assert manifest["historical_snapshot_claims"] is False
    assert manifest["n"] == 4
    assert manifest["n_relations"] == 5
    assert {record["kind"] for record in manifest["records"]} == {
        "regulations_docket_current_metadata",
        "federal_register_proposed_rule",
        "federal_register_comment_extension",
        "federal_register_final_rule",
    }
    docket = next(
        record
        for record in manifest["records"]
        if record["kind"].startswith("regulations")
    )
    assert docket["temporal_semantics"] == "current_snapshot_observation"
    assert "occurred_at" not in docket
    federal = [
        record for record in manifest["records"] if record["kind"].startswith("federal")
    ]
    assert all(
        record["temporal_semantics"] == "source_event_time" for record in federal
    )
    assert [record["document_number"] for record in federal] == DOCUMENT_NUMBERS
    assert [relation["kind"] for relation in manifest["relations"]].count(
        "docket_has_document"
    ) == 3
    assert [relation["kind"] for relation in manifest["relations"]].count(
        "next_rulemaking_event"
    ) == 2
    assert all(relation["evidence"] for relation in manifest["relations"])
    assert all(
        record["license"] and record["attribution"] and record["terms_url"]
        for record in manifest["records"]
    )


def test_rejects_wrong_rin_join_even_if_hash_is_updated(tmp_path: Path) -> None:
    inventory, path = _inventory(tmp_path)
    retrieval = next(
        item
        for item in inventory["fetch_receipt"]["retrievals"]
        if item["kind"] == "federal_register_document"
    )
    source = path.parent / retrieval["retrieval_file"]
    payload = json.loads(source.read_text())
    payload["regulation_id_numbers"] = ["3084-ZZ99"]
    corrupted = json.dumps(payload).encode()
    source.write_bytes(corrupted)
    retrieval["sha256"] = hashlib.sha256(corrupted).hexdigest()
    with pytest.raises(ProvenanceError, match="RIN"):
        build_regulation_workflow_from_fetch_inventory(
            inventory,
            path.parent,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_rejects_non_monotonic_or_semantically_unclassified_sequence(
    tmp_path: Path,
) -> None:
    inventory, path = _inventory(tmp_path)
    retrieval = next(
        item
        for item in inventory["fetch_receipt"]["retrievals"]
        if item.get("document_number") == "2023-07036"
    )
    source = path.parent / retrieval["retrieval_file"]
    payload = json.loads(source.read_text())
    payload["publication_date"] = "2023-01-01"
    payload["title"] = "Unrelated notice"
    payload["abstract"] = "Unrelated notice"
    corrupted = json.dumps(payload).encode()
    source.write_bytes(corrupted)
    retrieval["sha256"] = hashlib.sha256(corrupted).hexdigest()
    with pytest.raises(ProvenanceError, match="sequence|classify"):
        build_regulation_workflow_from_fetch_inventory(
            inventory,
            path.parent,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_signed_loader_fails_closed_on_resigned_relation_corruption(
    tmp_path: Path,
) -> None:
    manifest = _build(tmp_path)
    key = b"r" * 32
    manifest["relations"][0]["target_record_id"] = "fr:2099-00000"
    path = tmp_path / "corrupt.signed.json"
    path.write_text(
        json.dumps(attach_attestation(manifest, key, purpose="source_manifest"))
    )
    with pytest.raises(ProvenanceError, match="relation"):
        load_regulation_workflow_manifest(path, attestation_key=key)


def test_manifest_audit_rebinds_records_to_official_fetch_receipt(
    tmp_path: Path,
) -> None:
    manifest = _build(tmp_path)
    forged = deepcopy(manifest)
    record = next(
        item for item in forged["records"] if item["kind"].startswith("federal")
    )
    prior_url = record["source_url"]
    forged_url = "https://evil.example/fabricated.json"
    record.update(
        source_url=forged_url,
        retrieval_url=forged_url,
        source_sha256="f" * 64,
        source_record_sha256="e" * 64,
        provenance_id=f"sha256:{'f' * 64}",
        observed_at="2026-08-29T01:00:09Z",
    )
    receipt = next(
        item
        for item in forged["fetch_receipt"]["retrievals"]
        if item["requested_url"] == prior_url
    )
    receipt.update(
        requested_url=forged_url,
        final_url=forged_url,
        sha256="f" * 64,
        observed_at=record["observed_at"],
    )
    for relation in forged["relations"]:
        for evidence in relation["evidence"]:
            if evidence["record_id"] == record["record_id"]:
                evidence["source_sha256"] = record["source_sha256"]

    with pytest.raises(ProvenanceError, match="retrieval URL or transport|lineage"):
        audit_regulation_workflow_manifest(forged)


def test_signed_loader_rejects_resigned_body_semantic_corruption(
    tmp_path: Path,
) -> None:
    manifest = _build(tmp_path)
    key = b"b" * 32
    extension = next(
        record
        for record in manifest["records"]
        if record["kind"] == "federal_register_comment_extension"
    )
    text = json.loads(extension["text"])
    text["title"] = "Unrelated notice"
    text["action"] = "Notice."
    text["abstract"] = "Unrelated notice."
    extension["text"] = json.dumps(text, ensure_ascii=False, indent=2, sort_keys=True)
    extension["text_sha256"] = hashlib.sha256(extension["text"].encode()).hexdigest()
    extension["source_record_sha256"] = extension["text_sha256"]
    for fact in extension["facts"]:
        fact["value"] = text[fact["field"]]
        fact["evidence_quote"] = (
            f'  "{fact["field"]}": {json.dumps(fact["value"])}{","}'
        )
        fact["char_start"] = extension["text"].index(fact["evidence_quote"])
        fact["char_end"] = fact["char_start"] + len(fact["evidence_quote"])
    facts = {fact["fact_id"]: fact for fact in extension["facts"]}
    for relation in manifest["relations"]:
        for evidence in relation["evidence"]:
            if evidence["record_id"] != extension["record_id"]:
                continue
            selected = [facts[fact_id] for fact_id in evidence["fact_ids"]]
            evidence["evidence_quotes"] = [fact["evidence_quote"] for fact in selected]
            evidence["char_spans"] = [
                [fact["char_start"], fact["char_end"]] for fact in selected
            ]
    path = tmp_path / "body-corrupt.signed.json"
    path.write_text(
        json.dumps(attach_attestation(manifest, key, purpose="source_manifest"))
    )
    with pytest.raises(ProvenanceError, match="classif"):
        load_regulation_workflow_manifest(path, attestation_key=key)


def test_export_attests_only_valid_disabled_source_manifest(tmp_path: Path) -> None:
    _payload, inventory_path = _inventory(tmp_path)
    output = tmp_path / "regulation.signed.json"
    key = b"s" * 32
    export_regulation_workflow(
        inventory_path,
        output,
        attestation_key=key,
        generated_at="2026-08-29T02:00:00Z",
    )
    signed = json.loads(output.read_text())
    assert verify_attestation(signed, key, purpose="source_manifest")
    loaded = load_regulation_workflow_manifest(output, attestation_key=key)
    assert loaded["production_eligible"] is False
    assert loaded["generation_integration"] == "disabled"
