from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from longworld.core.attestation import attach_attestation, verify_attestation
from longworld.core.cyberworkflow import (
    CYBER_KEV_REMEDIATION_TASK_SCHEMA,
    CYBER_WORKFLOW_MANIFEST_SCHEMA,
    audit_cyber_kev_remediation_task,
    audit_cyber_workflow_manifest,
    build_cyber_kev_remediation_task,
    build_cyber_workflow_from_fetch_inventory,
    load_cyber_workflow_manifest,
    replay_cyber_kev_remediation_task,
)
from longworld.core.provenance import ProvenanceError
from scripts.export_cyber_task import export_cyber_task_candidate
from scripts.export_cyber_workflow import export_cyber_workflow
from scripts.fetch_cyber_workflow import HttpResponse, fetch_cyber_workflow
from tests.test_fetch_cyber_workflow import _kev_body, _nvd_body, _request


def test_core_cyberworkflow_import_does_not_require_repo_scripts_package(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-c", "import longworld.core.cyberworkflow"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _inventory(tmp_path: Path) -> tuple[dict, Path]:
    calls = 0

    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        nonlocal calls
        calls += 1
        return HttpResponse(
            _nvd_body() if calls == 1 else _kev_body(),
            200,
            url,
            "application/json",
        )

    observed = iter(
        [
            "2026-08-29T01:00:01Z",
            "2026-08-29T01:00:02Z",
            "2026-08-29T01:00:03Z",
        ]
    )
    path = fetch_cyber_workflow(
        _request(tmp_path),
        tmp_path / "inventory",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-29T01:00:00Z",
        clock=observed.__next__,
    )
    return json.loads(path.read_text()), path


def _build(tmp_path: Path) -> dict:
    inventory, inventory_path = _inventory(tmp_path)
    return build_cyber_workflow_from_fetch_inventory(
        inventory,
        inventory_path.parent,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
    )


def test_builds_disabled_current_snapshot_with_exact_cve_kev_join(
    tmp_path: Path,
) -> None:
    manifest = _build(tmp_path)
    assert manifest["schema_version"] == CYBER_WORKFLOW_MANIFEST_SCHEMA
    assert manifest["generation_integration"] == "disabled"
    assert manifest["semantic_facts_train_ready"] is False
    assert manifest["production_eligible"] is False
    assert manifest["historical_event_claims"] is False
    assert manifest["snapshot_semantics"] == "current_state_observed_at_fetch"
    assert manifest["n"] == 2
    assert manifest["n_relations"] == 1
    assert {record["kind"] for record in manifest["records"]} == {
        "cisa_kev_entry",
        "nvd_cve",
    }
    assert all(
        record["temporal_semantics"] == "current_snapshot_observation"
        and "occurred_at" not in record
        for record in manifest["records"]
    )


def test_manifest_audit_rebinds_every_record_to_fetch_receipt(tmp_path: Path) -> None:
    manifest = _build(tmp_path)
    forged = deepcopy(manifest)
    record = next(item for item in forged["records"] if item["kind"] == "nvd_cve")
    record.update(
        source_url="https://evil.example/fabricated.json",
        retrieval_url="https://evil.example/fabricated.json",
        source_sha256="f" * 64,
        provenance_id=f"sha256:{'f' * 64}",
        observed_at="2026-08-29T01:00:09Z",
    )
    relation = forged["relations"][0]
    evidence = next(
        item
        for item in relation["evidence"]
        if item["record_id"] == record["record_id"]
    )
    evidence["source_sha256"] = record["source_sha256"]

    with pytest.raises(ProvenanceError, match="fetch receipt lineage"):
        audit_cyber_workflow_manifest(forged)

    forged = deepcopy(manifest)
    forged["records"][0]["source_record_sha256"] = "e" * 64
    with pytest.raises(ProvenanceError, match="body or provenance"):
        audit_cyber_workflow_manifest(forged)
    relation = manifest["relations"][0]
    assert relation["kind"] == "listed_in_kev"
    assert relation["source_record_id"] == "cisa-kev:CVE-2021-44228"
    assert relation["target_record_id"] == "nvd:CVE-2021-44228"
    assert {item["record_id"] for item in relation["evidence"]} == {
        relation["source_record_id"],
        relation["target_record_id"],
    }
    assert all(
        record["license"] and record["attribution"] and record["terms_url"]
        for record in manifest["records"]
    )
    nvd = next(record for record in manifest["records"] if record["kind"] == "nvd_cve")
    assert "security@apache.org" not in nvd["text"]
    assert nvd["privacy_review"]["email_redaction_count"] == 1


def test_rejects_source_corruption_even_when_attacker_updates_hash(
    tmp_path: Path,
) -> None:
    inventory, inventory_path = _inventory(tmp_path)
    nvd = next(
        item
        for item in inventory["fetch_receipt"]["retrievals"]
        if item["kind"] == "nvd_cve"
    )
    raw_path = inventory_path.parent / nvd["retrieval_file"]
    raw = json.loads(raw_path.read_text())
    raw["vulnerabilities"][0]["cve"]["id"] = "CVE-2021-0001"
    corrupted = json.dumps(raw).encode()
    raw_path.write_bytes(corrupted)
    nvd["sha256"] = hashlib.sha256(corrupted).hexdigest()
    with pytest.raises(ProvenanceError, match="identity"):
        build_cyber_workflow_from_fetch_inventory(
            inventory,
            inventory_path.parent,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_rejects_missing_or_duplicate_semantic_join(tmp_path: Path) -> None:
    inventory, inventory_path = _inventory(tmp_path)
    kev = next(
        item
        for item in inventory["fetch_receipt"]["retrievals"]
        if item["kind"] == "cisa_kev"
    )
    raw_path = inventory_path.parent / kev["retrieval_file"]
    raw = json.loads(raw_path.read_text())
    raw["vulnerabilities"].append(dict(raw["vulnerabilities"][0]))
    raw["count"] = len(raw["vulnerabilities"])
    corrupted = json.dumps(raw).encode()
    raw_path.write_bytes(corrupted)
    kev["sha256"] = hashlib.sha256(corrupted).hexdigest()
    with pytest.raises(ProvenanceError, match="unique"):
        build_cyber_workflow_from_fetch_inventory(
            inventory,
            inventory_path.parent,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_rejects_credential_shaped_source_text(tmp_path: Path) -> None:
    inventory, inventory_path = _inventory(tmp_path)
    nvd = next(
        item
        for item in inventory["fetch_receipt"]["retrievals"]
        if item["kind"] == "nvd_cve"
    )
    raw_path = inventory_path.parent / nvd["retrieval_file"]
    raw = json.loads(raw_path.read_text())
    raw["vulnerabilities"][0]["cve"]["descriptions"][0]["value"] += (
        " ghp_abcdefghijklmnopqrstuvwxyz"
    )
    corrupted = json.dumps(raw).encode()
    raw_path.write_bytes(corrupted)
    nvd["sha256"] = hashlib.sha256(corrupted).hexdigest()
    with pytest.raises(ProvenanceError, match="public scanner"):
        build_cyber_workflow_from_fetch_inventory(
            inventory,
            inventory_path.parent,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_signed_manifest_loader_fails_closed_on_resigned_field_corruption(
    tmp_path: Path,
) -> None:
    manifest = _build(tmp_path)
    key = b"k" * 32
    nvd = next(record for record in manifest["records"] if record["kind"] == "nvd_cve")
    nvd["cve_id"] = "CVE-2021-0001"
    path = tmp_path / "corrupt.signed.json"
    path.write_text(
        json.dumps(attach_attestation(manifest, key, purpose="source_manifest"))
    )
    with pytest.raises(ProvenanceError, match="identity"):
        load_cyber_workflow_manifest(path, attestation_key=key)


def test_signed_manifest_loader_rejects_resigned_body_corruption(
    tmp_path: Path,
) -> None:
    manifest = _build(tmp_path)
    key = b"b" * 32
    nvd = next(record for record in manifest["records"] if record["kind"] == "nvd_cve")
    nvd["text"] = nvd["text"].replace("CVE-2021-44228", "CVE-2021-0001", 1)
    nvd["text_sha256"] = hashlib.sha256(nvd["text"].encode()).hexdigest()
    path = tmp_path / "body-corrupt.signed.json"
    path.write_text(
        json.dumps(attach_attestation(manifest, key, purpose="source_manifest"))
    )
    with pytest.raises(ProvenanceError, match="identity"):
        load_cyber_workflow_manifest(path, attestation_key=key)


def test_export_attests_only_valid_disabled_source_manifest(tmp_path: Path) -> None:
    _inventory_payload, inventory_path = _inventory(tmp_path)
    output = tmp_path / "cyber.signed.json"
    key = b"s" * 32
    export_cyber_workflow(
        inventory_path,
        output,
        attestation_key=key,
        generated_at="2026-08-29T02:00:00Z",
    )
    signed = json.loads(output.read_text())
    assert verify_attestation(signed, key, purpose="source_manifest")
    loaded = load_cyber_workflow_manifest(output, attestation_key=key)
    assert loaded["production_eligible"] is False
    assert loaded["generation_integration"] == "disabled"


def test_kev_task_replays_real_state_counterfactual_and_negative_gates(
    tmp_path: Path,
) -> None:
    task = build_cyber_kev_remediation_task(_build(tmp_path))

    assert task["schema_version"] == CYBER_KEV_REMEDIATION_TASK_SCHEMA
    assert task["data_stage"] == "candidate_task"
    assert task["train_ready"] is False
    assert task["production_eligible"] is False
    assert task["promotion_eligible"] is False
    assert task["complete_world"] is False
    assert task["promoted"] is False
    assert task["generation_integration"] == "disabled"
    assert task["source_attestation_verified"] is False
    assert task["answer"] == (
        "CVE-2021-44228 | NVD status: Analyzed | KEV added: 2021-12-10 | "
        "due: 2021-12-24 | required action: Apply mitigations per vendor "
        "instructions."
    )
    assert replay_cyber_kev_remediation_task(task) == task["answer"]
    assert (
        replay_cyber_kev_remediation_task(task, counterfactual=True)
        == task["cf_answer"]
    )
    assert task["cf_answer"] != task["answer"]

    essentials = task["essential_evidence_ids"]
    assert len(essentials) == 5
    assert all(
        replay_cyber_kev_remediation_task(task, evidence_ids=[evidence_id]) == "unknown"
        for evidence_id in essentials
    )
    assert all(
        replay_cyber_kev_remediation_task(
            task,
            evidence_ids=[item for item in essentials if item != removed],
        )
        == "unknown"
        for removed in essentials
    )
    audit = audit_cyber_kev_remediation_task(task)
    assert audit["source_attestation_verified"] is False
    assert all(
        value for name, value in audit.items() if name != "source_attestation_verified"
    )


def test_kev_task_rejects_body_corruption_even_with_updated_task_hashes(
    tmp_path: Path,
) -> None:
    task = build_cyber_kev_remediation_task(_build(tmp_path))
    corrupted = deepcopy(task)
    nvd = next(
        record for record in corrupted["source_records"] if record["kind"] == "nvd_cve"
    )
    nvd["text"] = nvd["text"].replace("Analyzed", "Modified", 1)
    digest = hashlib.sha256(nvd["text"].encode()).hexdigest()
    nvd["text_sha256"] = digest
    nvd["source_record_sha256"] = digest
    corrupted["source_bindings"][nvd["record_id"]]["text_sha256"] = digest
    corrupted["source_bindings"][nvd["record_id"]]["source_record_sha256"] = digest

    with pytest.raises(ProvenanceError, match="evidence|state|binding"):
        replay_cyber_kev_remediation_task(corrupted)


def test_kev_task_rejects_coordinated_receipt_and_record_endpoint_tamper(
    tmp_path: Path,
) -> None:
    task = build_cyber_kev_remediation_task(_build(tmp_path))
    forged = deepcopy(task)
    nvd = next(
        record for record in forged["source_records"] if record["kind"] == "nvd_cve"
    )
    receipt = next(
        item
        for item in forged["source_receipt"]["retrievals"]
        if item["kind"] == "nvd_cve"
    )
    forged_url = "https://evil.example/CVE-2021-44228.json"
    nvd["source_url"] = forged_url
    nvd["retrieval_url"] = forged_url
    receipt["requested_url"] = forged_url
    receipt["final_url"] = forged_url
    binding = forged["source_bindings"][nvd["record_id"]]
    binding["source_url"] = forged_url
    binding["retrieval_url"] = forged_url

    with pytest.raises(ProvenanceError, match="source binding|receipt binding"):
        replay_cyber_kev_remediation_task(forged)


def test_kev_task_rejects_declared_or_extra_byte_counterfactual(
    tmp_path: Path,
) -> None:
    task = build_cyber_kev_remediation_task(_build(tmp_path))
    forged = deepcopy(task)
    twin = forged["counterfactual_twin"]
    twin["text"] = "X" + twin["text"]
    twin["text_sha256"] = hashlib.sha256(twin["text"].encode()).hexdigest()
    twin["char_start"] += 1
    twin["char_end"] += 1

    with pytest.raises(ProvenanceError, match="counterfactual replacement"):
        replay_cyber_kev_remediation_task(forged, counterfactual=True)


def test_kev_task_builder_rejects_coordinated_manifest_source_tamper(
    tmp_path: Path,
) -> None:
    manifest = _build(tmp_path)
    forged = deepcopy(manifest)
    nvd = next(record for record in forged["records"] if record["kind"] == "nvd_cve")
    receipt = next(
        item
        for item in forged["fetch_receipt"]["retrievals"]
        if item["kind"] == "nvd_cve"
    )
    forged_url = "https://evil.example/CVE-2021-44228.json"
    nvd["source_url"] = forged_url
    nvd["retrieval_url"] = forged_url
    receipt["requested_url"] = forged_url
    receipt["final_url"] = forged_url

    with pytest.raises(ProvenanceError, match="fetch receipt lineage"):
        build_cyber_kev_remediation_task(forged)


def test_kev_task_rejects_non_string_evidence_identifiers_fail_closed(
    tmp_path: Path,
) -> None:
    task = build_cyber_kev_remediation_task(_build(tmp_path))
    task["essential_evidence_ids"][0] = ["not", "hashable"]

    with pytest.raises(ProvenanceError, match="essential evidence contract"):
        replay_cyber_kev_remediation_task(task)


def test_exports_ignored_cyber_candidate_with_executable_audit(
    tmp_path: Path,
) -> None:
    key = b"cyber-task-source-test-key-32-bytes"
    manifest_path = tmp_path / "cyber-source.signed.json"
    manifest_path.write_text(
        json.dumps(
            attach_attestation(_build(tmp_path), key, purpose="source_manifest")
        ),
        encoding="utf-8",
    )
    candidate_path = tmp_path / "cyber-task-candidate.jsonl"
    audit_path = tmp_path / "cyber-task-audit.json"

    export_cyber_task_candidate(
        manifest_path,
        candidate_path,
        audit_path,
        attestation_key=key,
    )

    task = json.loads(candidate_path.read_text())
    receipt = json.loads(audit_path.read_text())
    assert task["complete_world"] is False
    assert task["promotion_eligible"] is False
    assert task["promoted"] is False
    assert task["source_attestation_verified"] is False
    assert "signed_manifest" not in task["source_manifest_binding"]
    assert task["source_manifest_binding"]["attestation"]
    assert task["source_manifest_binding"]["manifest_metadata"]
    assert receipt["data_stage"] == "candidate_task_audit"
    assert receipt["promotion_status"] == "ignored_non_world_candidate"
    assert receipt["source_attestation_verified"] is True
    assert receipt["promoted"] is False
    assert all(receipt["audit"].values())
    assert audit_cyber_kev_remediation_task(task, source_attestation_key=key)[
        "source_attestation_verified"
    ]
    assert not audit_cyber_kev_remediation_task(task, source_attestation_key=b"w" * 32)[
        "source_attestation_verified"
    ]


def test_source_attestation_rejects_coordinated_unused_fact_tamper(
    tmp_path: Path,
) -> None:
    key = b"cyber-task-source-test-key-32-bytes"
    signed = attach_attestation(_build(tmp_path), key, purpose="source_manifest")
    task = build_cyber_kev_remediation_task(signed)
    forged = deepcopy(task)
    nvd = next(
        record for record in forged["source_records"] if record["kind"] == "nvd_cve"
    )
    published = next(fact for fact in nvd["facts"] if fact["fact_id"] == "published")
    prior = str(published["value"])
    replacement = prior.replace("2021-12-10", "2021-12-09")
    assert replacement != prior and len(replacement) == len(prior)
    nvd["text"] = nvd["text"].replace(prior, replacement, 1)
    published["value"] = replacement
    published["evidence_quote"] = published["evidence_quote"].replace(
        prior, replacement, 1
    )
    digest = hashlib.sha256(nvd["text"].encode()).hexdigest()
    nvd["text_sha256"] = digest
    nvd["source_record_sha256"] = digest
    binding = forged["source_bindings"][nvd["record_id"]]
    binding["text_sha256"] = digest
    binding["source_record_sha256"] = digest

    assert replay_cyber_kev_remediation_task(forged) == forged["answer"]
    assert not audit_cyber_kev_remediation_task(forged, source_attestation_key=key)[
        "source_attestation_verified"
    ]


def test_source_attestation_v2_revalidates_from_explicit_key_without_producer_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from longworld.core.attestation import (
        ATTESTATION_ENVIRONMENT_ENV,
        ROLE_KEY_ENVS,
        ROLE_KEY_ID_ENVS,
    )

    key = b"cyber-v2-source-key-material-32-bytes"
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], key.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-cyber-source-v2")
    signed = attach_attestation(_build(tmp_path), key, purpose="source_manifest")
    task = build_cyber_kev_remediation_task(signed)
    monkeypatch.delenv(ATTESTATION_ENVIRONMENT_ENV)
    monkeypatch.delenv(ROLE_KEY_ENVS["source"])
    monkeypatch.delenv(ROLE_KEY_ID_ENVS["source"])

    assert audit_cyber_kev_remediation_task(task, source_attestation_key=key)[
        "source_attestation_verified"
    ]


def test_kev_task_rejects_surface_with_all_answer_inputs(tmp_path: Path) -> None:
    inventory, inventory_path = _inventory(tmp_path)
    kev = next(
        item
        for item in inventory["fetch_receipt"]["retrievals"]
        if item["kind"] == "cisa_kev"
    )
    raw_path = inventory_path.parent / kev["retrieval_file"]
    raw = json.loads(raw_path.read_text())
    raw["vulnerabilities"][0]["requiredAction"] = (
        "CVE-2021-44228 Analyzed 2021-12-10 2021-12-24"
    )
    changed = json.dumps(raw).encode()
    raw_path.write_bytes(changed)
    kev["sha256"] = hashlib.sha256(changed).hexdigest()
    manifest = build_cyber_workflow_from_fetch_inventory(
        inventory,
        inventory_path.parent,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(ProvenanceError, match="executable audit"):
        build_cyber_kev_remediation_task(manifest)
