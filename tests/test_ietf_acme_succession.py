from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError
from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    audit_ietf_acme_issuance_succession_task,
    build_ietf_acme_issuance_succession_task,
    build_ietf_workflow_from_fetch_inventory,
    materialize_ietf_acme_issuance_counterfactual,
    replay_ietf_acme_issuance_succession_task,
)
from tests.test_standardsworkflow import _write

_OFFICIAL_INVENTORY = Path(
    "/workspace/wynckeliao/longworld/data/source_inventory/"
    "p57_ietf_acme_family_v1/ietf_fetch_inventory.json"
)
_OFFICIAL_DIR = _OFFICIAL_INVENTORY.parent
_LEFTOVER_RFCS = (8737, 8738, 8823, 9444, 9773)


def _acme_inventory(tmp_path: Path) -> dict[str, object]:
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-acme-test-1",
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
        "drafts": [{"name": "draft-ietf-acme-acme", "revisions": ["18"]}],
        "rfc_numbers": [8555, *_LEFTOVER_RFCS],
        "rfc_datatracker_sources": [8555],
        "approved_public_test_vector_sha256": [],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    leftover_targets = ", ".join(f"rfc{number}" for number in _LEFTOVER_RFCS)
    bodies = {
        "datatracker-draft-ietf-acme-acme.json": json.dumps(
            {
                "name": "draft-ietf-acme-acme",
                "rev": "18",
                "time": "2019-03-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-draft-ietf-acme-acme-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-acme-acme/",
                        "target": "/api/v1/doc/document/rfc8555/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft-ietf-acme-acme-18.txt": (
            b"Internet-Draft draft-ietf-acme-acme-18\n1 March 2019\nACME draft.\n"
        ),
        "rfc8555.txt": (
            b"Request for Comments: 8555\n"
            b"Updates: 8737, 8738, 8823, 9444, 9773                  March 2019\n"
            b"Automatic Certificate Management Environment (ACME)\n"
            b"This document describes a protocol\n"
            b"that a CA and an applicant can use to automate the process of\n"
            b"verification and certificate issuance.\n"
            b"The protocol also provides\n"
            b"facilities for other certificate management functions, such as\n"
            b"certificate revocation.\n"
        ),
        "datatracker-rfc8555.json": json.dumps(
            {
                "name": "rfc8555",
                "rfc": "8555",
                "rev": "",
                "time": "2019-03-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-rfc8555-relations.json": json.dumps(
            {
                "meta": {"total_count": len(_LEFTOVER_RFCS)},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc8555/",
                        "target": f"/api/v1/doc/document/{name}/",
                    }
                    for name in leftover_targets.split(", ")
                ],
            },
            sort_keys=True,
        ).encode(),
    }
    for number in _LEFTOVER_RFCS:
        bodies[f"rfc{number}.txt"] = (
            f"Request for Comments: {number}\nMarch 2020\n"
            f"ACME leftover RFC {number} whole span.\n"
        ).encode()
    specifications = [
        (
            "datatracker_document",
            "datatracker-draft-ietf-acme-acme.json",
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-acme-acme/",
        ),
        (
            "datatracker_relation",
            "datatracker-draft-ietf-acme-acme-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-acme-acme&limit=100",
        ),
        (
            "draft_revision",
            "draft-ietf-acme-acme-18.txt",
            "https://www.ietf.org/archive/id/draft-ietf-acme-acme-18.txt",
        ),
        ("rfc", "rfc8555.txt", "https://www.rfc-editor.org/rfc/rfc8555.txt"),
        *(
            ("rfc", f"rfc{number}.txt", f"https://www.rfc-editor.org/rfc/rfc{number}.txt")
            for number in _LEFTOVER_RFCS
        ),
        (
            "datatracker_document",
            "datatracker-rfc8555.json",
            "https://datatracker.ietf.org/api/v1/doc/document/rfc8555/",
        ),
        (
            "datatracker_relation",
            "datatracker-rfc8555-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=rfc8555&limit=100",
        ),
    ]
    retrievals = []
    for kind, filename, url in specifications:
        item = _write(tmp_path / filename, bodies[filename])
        item.update(
            {
                "kind": kind,
                "requested_url": url,
                "final_url": url,
                "observed_at": f"2026-08-29T01:00:{len(retrievals) + 1:02d}Z",
            }
        )
        retrievals.append(item)
    return {
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
            "completed_at": "2026-08-29T01:00:17Z",
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


def test_acme_issuance_succession_task_replays_and_fails_closed_on_remove_one(
    tmp_path: Path,
) -> None:
    inventory = _acme_inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_acme_issuance_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.acme_issuance_succession.v1"
    assert task["query_type"] == "protocol_succession_resolution"
    assert task["answer"] == {
        "current_protocol": "ACME_RFC8555",
        "certificate_management": "ISSUANCE_AND_REVOCATION",
    }
    assert all(item["record_id"] == "ietf:rfc:8555" for item in task["evidence_items"])
    assert replay_ietf_acme_issuance_succession_task(task) == task["answer"]
    removed = replay_ietf_acme_issuance_succession_task(
        task,
        evidence_ids=[
            item
            for item in task["essential_evidence_ids"]
            if item != "certificate_management"
        ],
    )
    assert removed["certificate_management"] == "UNKNOWN"
    assert removed["current_protocol"] == "ACME_RFC8555"
    audit = audit_ietf_acme_issuance_succession_task(task)
    assert audit["strict_replay"] is True
    assert audit["remove_one_evidence_fails"] is True
    materialized = materialize_ietf_acme_issuance_counterfactual(task)
    assert materialized["counterfactual_twin"]["evidence_id"] == "current_protocol"
    assert materialized["answer"]["current_protocol"] == "UNKNOWN"
    assert materialized["answer"]["certificate_management"] == "ISSUANCE_AND_REVOCATION"


def test_acme_official_family_cannot_compile_signed_graph() -> None:
    if not _OFFICIAL_INVENTORY.is_file():
        return
    inventory = json.loads(_OFFICIAL_INVENTORY.read_text())
    inventory_sha256 = hashlib.sha256(_OFFICIAL_INVENTORY.read_bytes()).hexdigest()
    with pytest.raises(ProvenanceError, match="not grounded"):
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            _OFFICIAL_DIR,
            generated_at="2026-09-08T00:00:00Z",
            fetch_inventory_sha256=inventory_sha256,
        )


def test_acme_packing_pins_last_leftover_chunk_without_isolation() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    quote = "This document describes a protocol that a CA and an applicant can use."
    leftover_a = "authentic leftover block a " + ("a" * 40)
    leftover_b = "authentic leftover block b " + ("b" * 40)
    leftover_c = "authentic leftover block c " + ("c" * 40)
    text = "\n\n".join([quote, leftover_a, leftover_b, leftover_c])
    task = {
        "question": "Resolve ACME issuance succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:8555",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc8555.txt",
                    "occurred_at": "2019-03-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [
            {
                "evidence_id": "current_protocol",
                "record_id": "ietf:rfc:8555",
                "char_start": 0,
                "char_end": len(quote),
            }
        ],
    }
    kwargs = {
        "chunked_record_ids": frozenset({"ietf:rfc:8555"}),
        "chunk_max_tokens": 80,
        "support_priority_record_ids": ("ietf:rfc:8555",),
    }
    unpinned, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0, **kwargs)
    pinned, _ = _artifacts_for_bucket(
        task,
        len,
        "64k",
        1,
        100_000,
        0,
        pin_last_leftover_record_ids=frozenset({"ietf:rfc:8555"}),
        **kwargs,
    )
    assert leftover_c not in "".join(item["text"] for item in unpinned)
    assert leftover_c in "".join(item["text"] for item in pinned)
    assert quote in "".join(item["text"] for item in pinned)
    last = max(pinned, key=lambda item: item["char_start"])
    assert last["essential"] is False
    assert leftover_c in last["text"]


def test_acme_packing_keeps_leftover_only_record_as_one_span() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    first = "Request for Comments: 8737 leftover start."
    middle = "Authentic ACME leftover paragraph."
    last = "Authentic ACME leftover end."
    text = "\n\n".join([first, middle, last])
    task = {
        "question": "Resolve ACME issuance succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:8737",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc8737.txt",
                    "occurred_at": "2020-03-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [],
    }
    artifacts, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0)
    leftover = [item for item in artifacts if item["record_id"] == "ietf:rfc:8737"]
    assert len(leftover) == 1
    assert leftover[0]["essential"] is False
    assert leftover[0]["char_start"] == 0
    assert leftover[0]["char_end"] == len(text)
    assert first in leftover[0]["text"]
    assert last in leftover[0]["text"]


def test_acme_generate_refuses_isolate_evidence_and_128k(tmp_path: Path) -> None:
    from reports.p57_ietf_acme_succession_generate import build

    config_path = tmp_path / "bad.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.ietf-acme-generation-config.v1",
                "length_buckets": {"128k": [128000, 131072]},
                "packing": {"isolate_evidence_ids": ["current_protocol"]},
            }
        )
    )
    with pytest.raises(ValueError, match="must not isolate"):
        build(config_path)
