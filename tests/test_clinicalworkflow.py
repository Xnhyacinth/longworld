from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.attestation import attach_attestation, verify_attestation
from longworld.core.clinicalworkflow import (
    CLINICAL_WORKFLOW_MANIFEST_SCHEMA,
    build_clinical_workflow_from_fetch_inventory,
    load_clinical_workflow_manifest,
)
from longworld.core.provenance import ProvenanceError
from scripts.export_clinical_workflow import export_clinical_workflow
from scripts.fetch_clinical_workflow import HttpResponse, fetch_clinical_workflow
from tests.test_fetch_clinical_workflow import (
    APPLICATION_NUMBER,
    NCT_ID,
    _application_body,
    _label_body,
    _request,
    _trial_body,
)


def _inventory(tmp_path: Path, *, label_body: bytes | None = None) -> tuple[dict, Path]:
    calls = 0

    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        nonlocal calls
        calls += 1
        bodies = (_trial_body(), _application_body(), label_body or _label_body())
        return HttpResponse(bodies[calls - 1], 200, url, "application/json")

    observed = iter(
        [
            "2026-08-29T01:00:01Z",
            "2026-08-29T01:00:02Z",
            "2026-08-29T01:00:03Z",
            "2026-08-29T01:00:04Z",
        ]
    )
    path = fetch_clinical_workflow(
        _request(tmp_path),
        tmp_path / "inventory",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-29T01:00:00Z",
        clock=observed.__next__,
    )
    return json.loads(path.read_text()), path


def _build(tmp_path: Path) -> dict:
    inventory, path = _inventory(tmp_path)
    return build_clinical_workflow_from_fetch_inventory(
        inventory,
        path.parent,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def test_builds_disabled_current_snapshot_with_only_explicit_identifier_joins(
    tmp_path: Path,
) -> None:
    manifest = _build(tmp_path)
    assert manifest["schema_version"] == CLINICAL_WORKFLOW_MANIFEST_SCHEMA
    assert manifest["data_stage"] == "source_inventory"
    assert manifest["generation_integration"] == "disabled"
    assert manifest["semantic_facts_train_ready"] is False
    assert manifest["hybrid_train_ready"] is False
    assert manifest["production_eligible"] is False
    assert manifest["historical_snapshot_claims"] is False
    assert manifest["snapshot_semantics"] == "current_state_observed_at_fetch"
    assert manifest["n"] == 3
    assert manifest["n_relations"] == 2
    assert {record["kind"] for record in manifest["records"]} == {
        "clinical_trial",
        "fda_application",
        "fda_label",
    }
    relations = {relation["kind"]: relation for relation in manifest["relations"]}
    trial_join = relations["label_explicitly_references_trial"]
    assert trial_join["join_key"] == {"kind": "nct_id", "value": NCT_ID}
    assert trial_join["source_record_id"] == f"openfda-label:{APPLICATION_NUMBER}"
    assert trial_join["target_record_id"] == f"clinicaltrials:{NCT_ID}"
    application_join = relations["label_matches_application"]
    assert application_join["join_key"] == {
        "kind": "fda_application_number",
        "value": APPLICATION_NUMBER,
    }
    assert all(len(relation["evidence"]) == 2 for relation in relations.values())
    assert all(
        record["temporal_semantics"] == "current_snapshot_observation"
        and "occurred_at" not in record
        and record["license"]
        and record["attribution"]
        and record["terms_url"]
        for record in manifest["records"]
    )
    application = next(
        record for record in manifest["records"] if record["kind"] == "fda_application"
    )
    assert any(
        fact["fact_id"] == "original_approval_date" and fact["value"] == "2020-10-22"
        for fact in application["facts"]
    )


def test_rejects_fuzzy_drug_name_join_without_explicit_nct_id(tmp_path: Path) -> None:
    inventory, path = _inventory(
        tmp_path,
        label_body=_label_body(
            clinical_studies="A trial evaluated remdesivir in hospitalized adults."
        ),
    )
    with pytest.raises(ProvenanceError, match="explicit NCT"):
        build_clinical_workflow_from_fetch_inventory(
            inventory,
            path.parent,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )


def test_rejects_projection_corruption_even_if_projection_hash_is_updated(
    tmp_path: Path,
) -> None:
    inventory, path = _inventory(tmp_path)
    trial = next(
        item
        for item in inventory["fetch_receipt"]["retrievals"]
        if item["kind"] == "clinical_trial"
    )
    projection_path = path.parent / trial["retrieval_file"]
    projection = json.loads(projection_path.read_text())
    projection["nct_id"] = "NCT00000001"
    corrupted = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
    projection_path.write_bytes(corrupted)
    trial["projection_sha256"] = hashlib.sha256(corrupted).hexdigest()
    with pytest.raises(ProvenanceError, match="identity"):
        build_clinical_workflow_from_fetch_inventory(
            inventory,
            path.parent,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_signed_loader_fails_closed_on_resigned_body_corruption(tmp_path: Path) -> None:
    manifest = _build(tmp_path)
    key = b"k" * 32
    label = next(
        record for record in manifest["records"] if record["kind"] == "fda_label"
    )
    label["text"] = label["text"].replace(NCT_ID, "NCT00000001", 1)
    label["text_sha256"] = hashlib.sha256(label["text"].encode()).hexdigest()
    path = tmp_path / "corrupt.signed.json"
    path.write_text(
        json.dumps(attach_attestation(manifest, key, purpose="source_manifest"))
    )
    with pytest.raises(ProvenanceError, match="evidence|identity|NCT|text hash"):
        load_clinical_workflow_manifest(path, attestation_key=key)


def test_signed_loader_rebinds_response_hash_to_fetch_receipt(tmp_path: Path) -> None:
    manifest = _build(tmp_path)
    key = b"r" * 32
    manifest["records"][0]["source_response_sha256"] = "f" * 64
    path = tmp_path / "response-lineage-corrupt.signed.json"
    path.write_text(
        json.dumps(attach_attestation(manifest, key, purpose="source_manifest"))
    )

    with pytest.raises(ProvenanceError, match="receipt lineage"):
        load_clinical_workflow_manifest(path, attestation_key=key)


def test_export_attests_only_valid_disabled_source_manifest(tmp_path: Path) -> None:
    _inventory_payload, inventory_path = _inventory(tmp_path)
    output = tmp_path / "clinical.signed.json"
    key = b"s" * 32
    export_clinical_workflow(
        inventory_path,
        output,
        attestation_key=key,
        generated_at="2026-08-29T02:00:00Z",
    )
    signed = json.loads(output.read_text())
    assert verify_attestation(signed, key, purpose="source_manifest")
    loaded = load_clinical_workflow_manifest(output, attestation_key=key)
    assert loaded["production_eligible"] is False
    assert loaded["generation_integration"] == "disabled"
