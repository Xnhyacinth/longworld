from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from longworld.core.attestation import attach_attestation, verify_attestation
from longworld.core.clinicalworkflow import (
    CLINICAL_WORKFLOW_MANIFEST_SCHEMA,
    _clinical_surface_is_gold_free,
    audit_clinical_trial_approval_task,
    build_clinical_trial_approval_task,
    build_clinical_workflow_from_fetch_inventory,
    load_clinical_workflow_manifest,
    replay_clinical_trial_approval_task,
)
from longworld.core.provenance import ProvenanceError
from scripts.export_clinical_task import export_clinical_task_candidate
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


def test_builds_executable_source_bound_trial_approval_task(tmp_path: Path) -> None:
    task = build_clinical_trial_approval_task(_build(tmp_path))
    assert task["data_stage"] == "candidate_task"
    assert task["train_ready"] is False
    assert task["production_eligible"] is False
    assert task["promotion_eligible"] is False
    assert task["complete_world"] is False
    assert task["promoted"] is False
    assert task["generation_integration"] == "disabled"
    assert task["source_attestation_verified"] is False
    assert task["answer_program_id"] == "clinical.trial_approval_trace.v1"
    assert task["answer"] == (
        "NCT04280705 | COMPLETED | Time to recovery | NDA214787 | "
        "original approval 2020-10-22 | label effective 2025-08-25"
    )
    essentials = task["essential_evidence_ids"]
    assert len(essentials) == 5
    assert all(
        replay_clinical_trial_approval_task(task, evidence_ids=[evidence_id])
        == "unknown"
        for evidence_id in essentials
    )
    assert all(
        replay_clinical_trial_approval_task(
            task,
            evidence_ids=[item for item in essentials if item != removed],
        )
        == "unknown"
        for removed in essentials
    )
    assert all(
        task["answer"] not in item["surface_text"] for item in task["evidence_items"]
    )
    assert (
        replay_clinical_trial_approval_task(task, counterfactual=True)
        == task["cf_answer"]
    )
    assert task["cf_answer"] != task["answer"]
    assert audit_clinical_trial_approval_task(task) == {
        "strict_replay_sufficient": True,
        "counterfactual_replay_sufficient": True,
        "counterfactual_changes_answer": True,
        "remove_one_fails": True,
        "essential_single_doc_insufficient": True,
        "essential_surface_gold_free": True,
        "essential_text_grounded": True,
        "receipt_binding_valid": True,
        "source_attestation_verified": False,
    }


def test_clinical_surface_gate_rejects_format_transformed_full_answer() -> None:
    answer = (
        "NCT04280705 | COMPLETED | Time to recovery | NDA214787 | "
        "original approval 2020-10-22 | label effective 2025-08-25"
    )
    transformed = (
        "Trial NCT04280705 was COMPLETED; its primary outcome was Time to "
        "recovery. NDA214787 was originally approved 2020-10-22 and the label "
        "became effective 2025-08-25."
    )

    assert not _clinical_surface_is_gold_free(transformed, answer)


def test_task_replay_rejects_body_and_receipt_binding_corruption(
    tmp_path: Path,
) -> None:
    task = build_clinical_trial_approval_task(_build(tmp_path))
    corrupted = deepcopy(task)
    trial = next(
        record
        for record in corrupted["source_records"]
        if record["kind"] == "clinical_trial"
    )
    trial["text"] = trial["text"].replace("Time to recovery", "Time to response")
    trial["text_sha256"] = hashlib.sha256(trial["text"].encode()).hexdigest()
    corrupted["source_bindings"][trial["record_id"]]["text_sha256"] = trial[
        "text_sha256"
    ]
    with pytest.raises(ProvenanceError, match="state|evidence|byte binding|manifest"):
        replay_clinical_trial_approval_task(corrupted)

    receipt_tamper = deepcopy(task)
    receipt_tamper["source_receipt"]["retrievals"][0]["requested_url"] += "?unbound=1"
    receipt_tamper["source_inventory_binding"]["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            receipt_tamper["source_receipt"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with pytest.raises(ProvenanceError, match="receipt binding|retrieval URL"):
        replay_clinical_trial_approval_task(receipt_tamper)

    policy_tamper = deepcopy(task)
    policy_tamper["source_receipt"]["source_policies"]["openfda"]["terms_url"] = (
        "https://evil.example/terms"
    )
    policy_tamper["source_inventory_binding"]["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            policy_tamper["source_receipt"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    with pytest.raises(ProvenanceError, match="receipt binding"):
        replay_clinical_trial_approval_task(policy_tamper)


def test_counterfactual_twin_is_executed_not_declared(tmp_path: Path) -> None:
    task = build_clinical_trial_approval_task(_build(tmp_path))
    twin_tamper = deepcopy(task)
    twin = twin_tamper["counterfactual_twin"]
    twin["text"] = "X" + twin["text"]
    twin["text_sha256"] = hashlib.sha256(twin["text"].encode()).hexdigest()
    twin["char_start"] += 1
    twin["char_end"] += 1
    with pytest.raises(ProvenanceError, match="counterfactual replacement"):
        replay_clinical_trial_approval_task(twin_tamper, counterfactual=True)


def test_source_attestation_rejects_coordinated_clinical_semantic_rewrite(
    tmp_path: Path,
) -> None:
    key = b"clinical-source-attestation-key-32"
    signed = attach_attestation(_build(tmp_path), key, purpose="source_manifest")
    task = build_clinical_trial_approval_task(signed)
    assert (
        audit_clinical_trial_approval_task(task, source_attestation_key=key)[
            "source_attestation_verified"
        ]
        is True
    )

    forged = deepcopy(signed)
    trial = next(
        record for record in forged["records"] if record["kind"] == "clinical_trial"
    )
    trial["state"]["overall_status"] = "SUSPENDED"
    trial["text"] = json.dumps(
        trial["state"], ensure_ascii=False, indent=2, sort_keys=True
    )
    trial["text_sha256"] = hashlib.sha256(trial["text"].encode()).hexdigest()
    trial["source_projection_sha256"] = hashlib.sha256(
        json.dumps(
            trial["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    trial["provenance_id"] = f"sha256:{trial['source_projection_sha256']}"
    status_fact = next(
        fact for fact in trial["facts"] if fact["fact_id"] == "overall_status"
    )
    status_fact["value"] = "SUSPENDED"
    status_fact["evidence_quote"] = status_fact["evidence_quote"].replace(
        "COMPLETED", "SUSPENDED"
    )
    retrieval = next(
        item
        for item in forged["fetch_receipt"]["retrievals"]
        if item["kind"] == "clinical_trial"
    )
    original_response_sha256 = retrieval["response_sha256"]
    retrieval["projection_sha256"] = trial["source_projection_sha256"]
    retrieval["projection_bytes"] = len(
        json.dumps(
            trial["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    for relation in forged["relations"]:
        for evidence in relation["evidence"]:
            if evidence["record_id"] == trial["record_id"]:
                evidence["source_projection_sha256"] = trial["source_projection_sha256"]

    forged_task = build_clinical_trial_approval_task(forged)
    assert "SUSPENDED" in replay_clinical_trial_approval_task(forged_task)
    assert retrieval["response_sha256"] == original_response_sha256
    forged_audit = audit_clinical_trial_approval_task(
        forged_task, source_attestation_key=key
    )
    assert forged_audit["strict_replay_sufficient"] is True
    assert forged_audit["receipt_binding_valid"] is True
    assert forged_audit["source_attestation_verified"] is False


def test_v2_clinical_source_attestation_replays_without_producer_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from longworld.core.attestation import (
        ATTESTATION_ENVIRONMENT_ENV,
        ROLE_KEY_ENVS,
        ROLE_KEY_ID_ENVS,
    )

    key = b"clinical-offline-source-key-material"
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], key.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-clinical-offline-v1")
    task = build_clinical_trial_approval_task(
        attach_attestation(_build(tmp_path), key, purpose="source_manifest")
    )
    monkeypatch.delenv(ATTESTATION_ENVIRONMENT_ENV)
    monkeypatch.delenv(ROLE_KEY_ENVS["source"])
    monkeypatch.delenv(ROLE_KEY_ID_ENVS["source"])

    assert audit_clinical_trial_approval_task(task, source_attestation_key=key)[
        "source_attestation_verified"
    ]


def test_unhashable_clinical_essential_fails_with_provenance_error(
    tmp_path: Path,
) -> None:
    task = build_clinical_trial_approval_task(_build(tmp_path))
    task["essential_evidence_ids"][0] = ["not-hashable"]
    with pytest.raises(ProvenanceError, match="essential evidence"):
        replay_clinical_trial_approval_task(task)


def test_exports_local_candidate_and_audit_without_promoting(tmp_path: Path) -> None:
    manifest = _build(tmp_path)
    key = b"candidate-source-key-material-32b"
    manifest_path = tmp_path / "clinical-source.signed.json"
    manifest_path.write_text(
        json.dumps(attach_attestation(manifest, key, purpose="source_manifest")),
        encoding="utf-8",
    )
    candidate_path = tmp_path / "clinical-task-candidate.jsonl"
    audit_path = tmp_path / "clinical-task-audit.json"
    export_clinical_task_candidate(
        manifest_path,
        candidate_path,
        audit_path,
        attestation_key=key,
    )
    rows = candidate_path.read_text().splitlines()
    assert len(rows) == 1
    task = json.loads(rows[0])
    receipt = json.loads(audit_path.read_text())
    candidate_bytes = candidate_path.read_bytes()
    assert task["train_ready"] is False
    assert task["production_eligible"] is False
    assert task["promotion_eligible"] is False
    assert task["complete_world"] is False
    assert task["promoted"] is False
    assert task["generation_integration"] == "disabled"
    assert task["source_attestation_verified"] is False
    assert verify_attestation(task["source_manifest"], key, purpose="source_manifest")
    assert receipt["data_stage"] == "candidate_task_audit"
    assert receipt["source_attestation_verified"] is True
    assert receipt["n"] == 1
    assert receipt["promoted"] is False
    assert receipt["complete_world"] is False
    assert receipt["candidate_sha256"] == hashlib.sha256(candidate_bytes).hexdigest()
    assert all(receipt["audit"].values())
