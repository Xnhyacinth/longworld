from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from longworld.core.attestation import verify_attestation
from longworld.core.provenance import ProvenanceError
from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    IETF_WORKFLOW_MANIFEST_SCHEMA,
    _clean,
    _validate_rfc_target_closure,
    audit_ietf_normative_change_task,
    build_ietf_normative_change_task,
    build_ietf_workflow_from_fetch_inventory,
    replay_ietf_normative_change_task,
)
from scripts.export_ietf_workflow import export_ietf_workflow


def _write(path: Path, body: bytes) -> dict[str, object]:
    path.write_bytes(body)
    return {
        "retrieval_file": path.name,
        "sha256": hashlib.sha256(body).hexdigest(),
        "status": 200,
        "content_type": "application/json" if path.suffix == ".json" else "text/plain",
        "requested_url": "",
        "final_url": "",
        "redirect_chain": [],
    }


def _inventory(tmp_path: Path) -> tuple[dict[str, object], Path]:
    private_key_block = (
        b"-----BEGIN PRIVATE KEY-----\npublic RFC test vector\n"
        b"-----END PRIVATE KEY-----"
    )
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-test-1",
            "scope": "public IETF standards",
            "basis": "public standards research",
            "reviewed_at": "2026-08-29T00:00:00Z",
            "allowed_actions": [
                "fetch_datatracker_document",
                "fetch_datatracker_relation",
                "fetch_draft_revision",
                "fetch_rfc",
            ],
        },
        "drafts": [{"name": "draft-ietf-demo", "revisions": ["00", "01"]}],
        "rfc_numbers": [7777, 8888, 9999],
        "approved_public_test_vector_sha256": [
            hashlib.sha256(private_key_block).hexdigest()
        ],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    bodies = {
        "datatracker": json.dumps(
            {
                "name": "draft-ietf-demo",
                "rev": "01",
                "rfc": "RFC 9999",
                "time": "2024-02-03T12:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker_relation": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-demo/",
                        "target": "/api/v1/doc/document/rfc9999/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft00": (
            b"Internet-Draft draft-ietf-demo-00\n1 January 2024\n"
            b"Endpoints SHOULD reject stale tokens.\nContact: editor@example.org\n"
        ),
        "draft01": (
            b"Internet-Draft draft-ietf-demo-01\n2 February 2024\n"
            b"Endpoints MUST reject stale tokens.\n" + private_key_block + b"\n"
        ),
        "rfc7777": b"RFC 7777\nHistoric baseline.\nJanuary 2020\n",
        "rfc8888": b"RFC 8888\nEarlier standard.\nJanuary 2021\n",
        "rfc9999": (
            b"Request for Comments: 9999                    Example Publisher\n"
            b"Internet-Draft draft-ietf-demo-01\n"
            b"References draft-old-demo-12 and draft-old-demo-12.\n"
            b"  Updates: RFC 8888,\n"
            b"           RFC 7777\n"
            b"Obsoletes: RFC 7777\nMarch 2025\n"
        ),
    }
    retrievals = []
    specifications = [
        (
            "datatracker_document",
            "datatracker.json",
            bodies["datatracker"],
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-demo/",
        ),
        (
            "datatracker_relation",
            "datatracker-relation.json",
            bodies["datatracker_relation"],
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-demo&limit=100",
        ),
        (
            "draft_revision",
            "draft-00.txt",
            bodies["draft00"],
            "https://www.ietf.org/archive/id/draft-ietf-demo-00.txt",
        ),
        (
            "draft_revision",
            "draft-01.txt",
            bodies["draft01"],
            "https://www.ietf.org/archive/id/draft-ietf-demo-01.txt",
        ),
        (
            "rfc",
            "rfc7777.txt",
            bodies["rfc7777"],
            "https://www.rfc-editor.org/rfc/rfc7777.txt",
        ),
        (
            "rfc",
            "rfc8888.txt",
            bodies["rfc8888"],
            "https://www.rfc-editor.org/rfc/rfc8888.txt",
        ),
        (
            "rfc",
            "rfc9999.txt",
            bodies["rfc9999"],
            "https://www.rfc-editor.org/rfc/rfc9999.txt",
        ),
    ]
    for kind, filename, body, url in specifications:
        item = _write(tmp_path / filename, body)
        item.update(
            {
                "kind": kind,
                "requested_url": url,
                "final_url": url,
                "observed_at": (f"2026-08-29T01:00:0{len(retrievals) + 1}Z"),
            }
        )
        retrievals.append(item)
    inventory = {
        "schema_version": IETF_FETCH_INVENTORY_SCHEMA,
        "source_status": "public_api_export",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "semantic_facts_train_ready": False,
        "generated_at": "2026-08-29T01:00:00Z",
        "request_file": request_path.name,
        "request_sha256": hashlib.sha256(request_raw).hexdigest(),
        "authorization": request["authorization"],
        "fetch_receipt": {
            "policy_url": "https://www.ietf.org/about/open-records/",
            "started_at": "2026-08-29T01:00:00Z",
            "completed_at": "2026-08-29T01:00:08Z",
            "requests_per_second": 1.0,
            "max_retries": 2,
            "allowed_actions": request["authorization"]["allowed_actions"],
            "request_file": request_path.name,
            "request_sha256": hashlib.sha256(request_raw).hexdigest(),
            "user_agent_sha256": hashlib.sha256(
                request["user_agent"].encode()
            ).hexdigest(),
            "retrievals": retrievals,
        },
        "n_retrievals": len(retrievals),
    }
    inventory_path = tmp_path / "ietf_fetch_inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return inventory, inventory_path


def test_builds_disabled_exact_source_manifest_with_grounded_relations(
    tmp_path: Path,
) -> None:
    inventory, _path = _inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    assert manifest["schema_version"] == IETF_WORKFLOW_MANIFEST_SCHEMA
    assert manifest["generation_integration"] == "disabled"
    assert manifest["production_eligible"] is False
    assert {item["kind"] for item in manifest["relations"]} == {
        "revision_of",
        "published_as",
        "updates",
        "obsoletes",
    }
    assert sum(item["kind"] == "updates" for item in manifest["relations"]) == 2
    published = next(
        item for item in manifest["relations"] if item["kind"] == "published_as"
    )
    assert published["source_record_id"] == "ietf:draft:draft-ietf-demo-01"
    assert published["target_record_id"] == "ietf:rfc:9999"
    assert {item["record_id"] for item in published["evidence"]} == {
        published["source_record_id"],
        published["target_record_id"],
        "ietf:datatracker-relation:draft-ietf-demo:rfc9999",
    }
    for relation in manifest["relations"]:
        assert {
            relation["source_record_id"],
            relation["target_record_id"],
        }.issubset({item["record_id"] for item in relation["evidence"]})
    assert {item["kind"] for item in manifest["records"]} == {
        "draft_revision",
        "rfc",
    }
    assert {item["kind"] for item in manifest["supporting_records"]} == {
        "datatracker_document",
        "datatracker_relation",
    }
    relation_record = next(
        item
        for item in manifest["supporting_records"]
        if item["kind"] == "datatracker_relation"
    )
    assert relation_record["occurred_at"] == "2026-08-29T01:00:02Z"
    assert relation_record["temporal_semantics"] == "retrieval_observation_only"
    final_rfc = next(
        item for item in manifest["records"] if item["record_id"] == "ietf:rfc:9999"
    )
    assert "draft-old-demo-12" not in final_rfc["draft_references"]
    assert "LongWorld/0.2 standards@example.org" not in json.dumps(manifest)
    draft = next(
        item
        for item in manifest["records"]
        if item["record_id"] == "ietf:draft:draft-ietf-demo-00"
    )
    assert "editor@example.org" not in draft["text"]
    assert draft["privacy_review"]["email_redaction_count"] == 1
    assert all(fact["evidence_quote"] in draft["text"] for fact in draft["facts"])
    latest = next(
        item
        for item in manifest["records"]
        if item["record_id"] == "ietf:draft:draft-ietf-demo-01"
    )
    assert "BEGIN PRIVATE KEY" not in latest["text"]
    assert latest["privacy_review"]["private_key_redaction_count"] == 1
    assert latest["privacy_review"]["secrets"] == "redacted_then_scanned"


def test_rejects_nonconsecutive_revision_history_and_unsanitized_manifest(
    tmp_path: Path,
) -> None:
    inventory, _path = _inventory(tmp_path)
    request_path = tmp_path / str(inventory["request_file"])
    request = json.loads(request_path.read_text())
    request["drafts"][0]["revisions"] = ["00", "02"]
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    request_sha256 = hashlib.sha256(request_raw).hexdigest()
    inventory["request_sha256"] = request_sha256
    inventory["fetch_receipt"]["request_sha256"] = request_sha256
    with pytest.raises(ProvenanceError, match="draft identity"):
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            tmp_path,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )

    scanner_path = tmp_path / "scanner"
    scanner_path.mkdir()
    inventory, _path = _inventory(scanner_path)
    request_path = scanner_path / str(inventory["request_file"])
    request = json.loads(request_path.read_text())
    request["authorization"]["basis"] = "reviewer@example.org"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    request_sha256 = hashlib.sha256(request_raw).hexdigest()
    inventory["request_sha256"] = request_sha256
    inventory["authorization"] = request["authorization"]
    inventory["fetch_receipt"]["request_sha256"] = request_sha256
    with pytest.raises(ProvenanceError, match="manifest failed the public scanner"):
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            scanner_path,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_rejects_unclosed_private_key_block() -> None:
    with pytest.raises(ProvenanceError, match="private-key"):
        _clean(
            b"-----BEGIN PRIVATE KEY-----\nnot a complete public test vector\n",
            approved_private_key_digests=frozenset(),
        )


@pytest.mark.parametrize(
    "label",
    [
        "PRIVATE KEY",
        "ENCRYPTED PRIVATE KEY",
        "DSA PRIVATE KEY",
        "PGP PRIVATE KEY BLOCK",
    ],
)
def test_rejects_unapproved_complete_private_key_block(label: str) -> None:
    with pytest.raises(ProvenanceError, match="not digest-approved"):
        _clean(
            f"-----BEGIN {label}-----\nunknown\n-----END {label}-----\n".encode(),
            approved_private_key_digests=frozenset(),
        )


def test_rfc_target_closure_allows_published_target_overlap() -> None:
    _validate_rfc_target_closure(
        requested={1000, 2000},
        published={1000, 2000},
        referenced={2000},
    )


def test_rejects_nonmonotonic_retrieval_observations(tmp_path: Path) -> None:
    inventory, _path = _inventory(tmp_path)
    retrievals = inventory["fetch_receipt"]["retrievals"]
    retrievals[1]["observed_at"] = retrievals[0]["observed_at"]

    with pytest.raises(ProvenanceError, match="observation timeline"):
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            tmp_path,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_rejects_hash_tamper_and_filename_does_not_supply_identity(
    tmp_path: Path,
) -> None:
    inventory, _path = _inventory(tmp_path)
    retrieval = inventory["fetch_receipt"]["retrievals"][2]
    (tmp_path / retrieval["retrieval_file"]).write_text("unrelated body")
    with pytest.raises(ProvenanceError, match="hash mismatch"):
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            tmp_path,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="b" * 64,
        )

    body = b"1 January 2024\nThis filename is not source evidence.\n"
    (tmp_path / retrieval["retrieval_file"]).write_bytes(body)
    retrieval["sha256"] = hashlib.sha256(body).hexdigest()
    with pytest.raises(ProvenanceError, match="body does not bind"):
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            tmp_path,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="b" * 64,
        )


def test_rejects_relation_without_both_endpoint_evidence(tmp_path: Path) -> None:
    inventory, _path = _inventory(tmp_path)
    rfc = inventory["fetch_receipt"]["retrievals"][-1]
    body = b"RFC 9999\nInternet-Draft draft-ietf-demo-01\nMarch 2025\n"
    (tmp_path / rfc["retrieval_file"]).write_bytes(body)
    rfc["sha256"] = hashlib.sha256(body).hexdigest()
    with pytest.raises(ProvenanceError, match="requested RFC relation target"):
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            tmp_path,
            generated_at="2026-08-29T02:00:00Z",
            fetch_inventory_sha256="c" * 64,
        )


def test_normative_change_task_replays_source_relations_cf_and_negative_gates(
    tmp_path: Path,
) -> None:
    inventory, _path = _inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_normative_change_task(manifest)

    assert task["query_type"] == "normative_change_introducer"
    assert task["answer_program_id"] == "ietf.normative_change_introducer.v1"
    assert replay_ietf_normative_change_task(task) == task["answer"]
    assert task["answer"] == (
        "draft-ietf-demo-01 | RFC 9999 | SHOULD -> MUST | "
        "Endpoints MUST reject stale tokens."
    )
    essentials = task["essential_evidence_ids"]
    assert len(essentials) == 4
    assert all(
        replay_ietf_normative_change_task(task, evidence_ids=[evidence_id]) == "unknown"
        for evidence_id in essentials
    )
    assert all(
        replay_ietf_normative_change_task(
            task,
            evidence_ids=[item for item in essentials if item != removed],
        )
        == "unknown"
        for removed in essentials
    )
    assert all(
        task["answer"] not in item["surface_text"]
        for item in task["evidence_items"]
        if item["evidence_id"] in essentials
    )

    assert (
        replay_ietf_normative_change_task(task, counterfactual=True)
        == task["cf_answer"]
    )
    assert task["cf_answer"] != task["answer"]
    assert audit_ietf_normative_change_task(task) == {
        "strict_replay_sufficient": True,
        "counterfactual_replay_sufficient": True,
        "counterfactual_changes_answer": True,
        "remove_one_fails": True,
        "essential_single_doc_insufficient": True,
        "essential_surface_gold_free": True,
        "essential_text_grounded": True,
    }

    corrupted = deepcopy(task)
    latest = next(
        item
        for item in corrupted["source_records"]
        if item["record_id"] == "ietf:draft:draft-ietf-demo-01"
    )
    latest["text"] = latest["text"].replace("MUST", "MAY", 1)
    latest["text_sha256"] = hashlib.sha256(latest["text"].encode()).hexdigest()
    corrupted["source_bindings"][latest["record_id"]] = latest["text_sha256"]
    with pytest.raises(ProvenanceError, match="byte binding"):
        replay_ietf_normative_change_task(corrupted)

    keyword_tamper = deepcopy(task)
    normative_after = next(
        item
        for item in keyword_tamper["evidence_items"]
        if item["kind"] == "normative_after"
    )
    normative_after["keyword"] = "MAY"
    with pytest.raises(ProvenanceError, match="normative keyword"):
        replay_ietf_normative_change_task(keyword_tamper)

    prefixed_cf = deepcopy(task)
    twin = prefixed_cf["counterfactual_twin"]
    twin["text"] = "X" + twin["text"]
    twin["text_sha256"] = hashlib.sha256(twin["text"].encode()).hexdigest()
    twin["char_start"] += 1
    twin["char_end"] += 1
    twin["keyword_char_start"] += 1
    twin["keyword_char_end"] += 1
    with pytest.raises(ProvenanceError, match="counterfactual replacement"):
        replay_ietf_normative_change_task(prefixed_cf, counterfactual=True)


def test_direct_task_builder_audits_receipt_lineage(tmp_path: Path) -> None:
    inventory, _path = _inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )

    wrong_url = deepcopy(manifest)
    draft = next(
        item for item in wrong_url["records"] if item["kind"] == "draft_revision"
    )
    draft["retrieval_url"] = f"{draft['retrieval_url']}?unbound=1"
    with pytest.raises(ProvenanceError, match="receipt lineage"):
        build_ietf_normative_change_task(wrong_url)

    wrong_observation = deepcopy(manifest)
    relation_record = next(
        item
        for item in wrong_observation["supporting_records"]
        if item["kind"] == "datatracker_relation"
    )
    relation_record["occurred_at"] = "2026-08-29T00:30:00Z"
    with pytest.raises(ProvenanceError, match="receipt lineage"):
        build_ietf_normative_change_task(wrong_observation)

    coordinated_url_tamper = deepcopy(manifest)
    rfc = next(
        item for item in coordinated_url_tamper["records"] if item["kind"] == "rfc"
    )
    prior_url = rfc["source_url"]
    forged_url = "https://evil.example/fabricated.txt"
    rfc["source_url"] = forged_url
    rfc["retrieval_url"] = forged_url
    retrieval = next(
        item
        for item in coordinated_url_tamper["fetch_receipt"]["retrievals"]
        if item["kind"] == "rfc" and item["requested_url"] == prior_url
    )
    retrieval["requested_url"] = forged_url
    retrieval["final_url"] = forged_url
    with pytest.raises(ProvenanceError, match="retrieval URL or transport"):
        build_ietf_normative_change_task(coordinated_url_tamper)


def test_attested_standards_manifest_bundle_materializes_executable_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from longworld.core.attestation import (
        ATTESTATION_ENVIRONMENT_ENV,
        ROLE_KEY_ENVS,
        ROLE_KEY_ID_ENVS,
        attach_attestation,
    )
    from longworld.core.sourceworkflow import STANDARDS_SOURCE_KIND
    from longworld.core.standardsworkflow import materialize_ietf_normative_change_task
    from scripts.build_source_workflow_bundle import build_source_workflow_bundle

    key = b"standards-source-bundle-test-key-material"
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], key.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-standards-source-v1")
    inventory, _path = _inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    manifest_path = tmp_path / "ietf-source-manifest.json"
    manifest_path.write_text(
        json.dumps(attach_attestation(manifest, key, purpose="source_manifest")),
        encoding="utf-8",
    )

    loaded = build_source_workflow_bundle(
        [(STANDARDS_SOURCE_KIND, manifest_path)],
        tmp_path / "source-workflow-bundle.json",
        attestation_key=key,
    )
    assert len(loaded.workflows) == 1
    workflow = loaded.workflows[0]
    assert workflow.source_kind == STANDARDS_SOURCE_KIND
    assert workflow.target_domain == "standards"
    task = materialize_ietf_normative_change_task(workflow)
    assert replay_ietf_normative_change_task(task) == task["answer"]
    assert audit_ietf_normative_change_task(task)["counterfactual_replay_sufficient"]


def test_core_sourceworkflow_import_does_not_require_repo_scripts_package(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import longworld.core.sourceworkflow; import longworld.core.promotion",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_export_signs_only_valid_disabled_manifest(tmp_path: Path) -> None:
    _inventory_payload, inventory_path = _inventory(tmp_path)
    output = tmp_path / "signed.json"
    key = b"k" * 32
    export_ietf_workflow(
        inventory_path,
        output,
        attestation_key=key,
        generated_at="2026-08-29T02:00:00Z",
    )
    signed = json.loads(output.read_text())
    assert signed["production_eligible"] is False
    assert verify_attestation(signed, key, purpose="source_manifest")
