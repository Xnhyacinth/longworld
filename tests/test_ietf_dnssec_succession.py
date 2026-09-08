from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError
from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    build_ietf_workflow_from_fetch_inventory,
)
from tests.test_standardsworkflow import _write


DNSSEC_FAMILY_INVENTORY = Path(
    "/workspace/wynckeliao/longworld/data/source_inventory/"
    "p57_ietf_dnssec_family_v1/ietf_fetch_inventory.json"
)
DNSSEC_SIGNED_MANIFEST = Path(
    "/workspace/wynckeliao/longworld/data/source_inventory/"
    "p57_ietf_dnssec_family_v1/"
    "ietf_workflow_manifest.p57.dnssec.v1.signed.json"
)


def _dnssec_inventory(tmp_path: Path) -> dict[str, object]:
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-dnssec-test-1",
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
        "drafts": [{"name": "draft-ietf-dnsext-dnssec-protocol", "revisions": ["09"]}],
        "rfc_numbers": [1034, 1035, 4033, 4034, 4035],
        "rfc_datatracker_sources": [4035],
        "approved_public_test_vector_sha256": [],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    bodies = {
        "datatracker-draft-ietf-dnsext-dnssec-protocol.json": json.dumps(
            {
                "name": "draft-ietf-dnsext-dnssec-protocol",
                "rev": "09",
                "time": "2005-03-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-draft-ietf-dnsext-dnssec-protocol-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-dnsext-dnssec-protocol/",
                        "target": "/api/v1/doc/document/rfc4035/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft-ietf-dnsext-dnssec-protocol-09.txt": (
            b"Internet-Draft draft-ietf-dnsext-dnssec-protocol-09\n"
            b"1 March 2005\nDNSSEC protocol draft.\n"
        ),
        "rfc1034.txt": (
            b"Request for Comments: 1034\nNovember 1987\n"
            b"Domain names concepts leftover whole span.\n"
        ),
        "rfc1035.txt": (
            b"Request for Comments: 1035\nNovember 1987\n"
            b"Domain names implementation leftover whole span.\n"
        ),
        "rfc4033.txt": (
            b"Request for Comments: 4033\nMarch 2005\n"
            b"DNSSEC introduction leftover whole span.\n"
        ),
        "rfc4034.txt": (
            b"Request for Comments: 4034\nMarch 2005\n"
            b"DNSSEC resource records leftover whole span.\n"
        ),
        "rfc4035.txt": (
            b"Request for Comments: 4035\n"
            b"Updates: 1034, 1035                                      March 2005\n"
            b"Protocol Modifications for the DNS Security Extensions\n"
            b"This document defines the DNSSEC protocol operations.\n"
            b"The reader is also assumed to be familiar with the basic DNS concepts\n"
            b"   described in [RFC1034], [RFC1035], and the subsequent documents that\n"
            b"   update them.\n"
        ),
        "datatracker-rfc4035.json": json.dumps(
            {
                "name": "rfc4035",
                "rfc": "4035",
                "rev": "",
                "time": "2005-03-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-rfc4035-relations.json": json.dumps(
            {
                "meta": {"total_count": 2},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refnorm/",
                        "source": "/api/v1/doc/document/rfc4035/",
                        "target": "/api/v1/doc/document/rfc4033/",
                    },
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refnorm/",
                        "source": "/api/v1/doc/document/rfc4035/",
                        "target": "/api/v1/doc/document/rfc4034/",
                    },
                ],
            },
            sort_keys=True,
        ).encode(),
    }
    specifications = [
        (
            "datatracker_document",
            "datatracker-draft-ietf-dnsext-dnssec-protocol.json",
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-dnsext-dnssec-protocol/",
        ),
        (
            "datatracker_relation",
            "datatracker-draft-ietf-dnsext-dnssec-protocol-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-dnsext-dnssec-protocol&limit=100",
        ),
        (
            "draft_revision",
            "draft-ietf-dnsext-dnssec-protocol-09.txt",
            "https://www.ietf.org/archive/id/draft-ietf-dnsext-dnssec-protocol-09.txt",
        ),
        ("rfc", "rfc1034.txt", "https://www.rfc-editor.org/rfc/rfc1034.txt"),
        ("rfc", "rfc1035.txt", "https://www.rfc-editor.org/rfc/rfc1035.txt"),
        ("rfc", "rfc4033.txt", "https://www.rfc-editor.org/rfc/rfc4033.txt"),
        ("rfc", "rfc4034.txt", "https://www.rfc-editor.org/rfc/rfc4034.txt"),
        ("rfc", "rfc4035.txt", "https://www.rfc-editor.org/rfc/rfc4035.txt"),
        (
            "datatracker_document",
            "datatracker-rfc4035.json",
            "https://datatracker.ietf.org/api/v1/doc/document/rfc4035/",
        ),
        (
            "datatracker_relation",
            "datatracker-rfc4035-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=rfc4035&limit=100",
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


def test_dnssec_family_inventory_cannot_ground_published_as() -> None:
    inventory = json.loads(DNSSEC_FAMILY_INVENTORY.read_text())
    with pytest.raises(
        ProvenanceError, match="published_as endpoints are not both grounded"
    ):
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            DNSSEC_FAMILY_INVENTORY.parent,
            generated_at="2026-09-08T05:30:00Z",
            fetch_inventory_sha256="a" * 64,
        )


def test_dnssec_succession_task_replays_and_fails_closed_on_remove_one(
    tmp_path: Path,
) -> None:
    from reports.p57_ietf_dnssec_succession_generate import (
        audit_ietf_dnssec_succession_task,
        build_ietf_dnssec_succession_task,
        materialize_ietf_dnssec_counterfactual,
        replay_ietf_dnssec_succession_task,
    )

    inventory = _dnssec_inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_dnssec_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.dnssec_succession.v1"
    assert task["query_type"] == "protocol_succession_resolution"
    assert task["answer"] == {
        "current_protocol": "DNSSEC_PROTOCOL_RFC4035",
        "updates_dns_concepts": "RFC1034",
        "updates_dns_implementation": "RFC1035",
    }
    assert all(item["record_id"] == "ietf:rfc:4035" for item in task["evidence_items"])
    assert replay_ietf_dnssec_succession_task(task) == task["answer"]
    removed = replay_ietf_dnssec_succession_task(
        task,
        evidence_ids=[
            item
            for item in task["essential_evidence_ids"]
            if item != "updates_dns_concepts"
        ],
    )
    assert removed["updates_dns_concepts"] == "UNKNOWN"
    assert removed["current_protocol"] == "DNSSEC_PROTOCOL_RFC4035"
    audit = audit_ietf_dnssec_succession_task(task)
    assert audit == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }
    materialized = materialize_ietf_dnssec_counterfactual(task)
    assert materialized["counterfactual_twin"]["evidence_id"] == "current_protocol"
    assert materialized["answer"]["current_protocol"] == "UNKNOWN"
    assert materialized["answer"]["updates_dns_concepts"] == "RFC1034"


def test_dnssec_succession_task_binds_official_bytes_when_present() -> None:
    from reports.p57_ietf_dnssec_succession_generate import (
        audit_ietf_dnssec_succession_task,
        build_ietf_dnssec_succession_task,
    )

    if not DNSSEC_SIGNED_MANIFEST.is_file():
        return
    manifest = json.loads(DNSSEC_SIGNED_MANIFEST.read_text())
    task = build_ietf_dnssec_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.dnssec_succession.v1"
    assert all(item["record_id"] == "ietf:rfc:4035" for item in task["evidence_items"])
    assert audit_ietf_dnssec_succession_task(task) == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }


def test_dnssec_packing_pins_last_leftover_chunk_without_isolation() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    quote = "This document defines the DNSSEC protocol operations."
    leftover_a = "authentic leftover block a " + ("a" * 40)
    leftover_b = "authentic leftover block b " + ("b" * 40)
    leftover_c = "authentic leftover block c " + ("c" * 40)
    text = "\n\n".join([quote, leftover_a, leftover_b, leftover_c])
    task = {
        "question": "Resolve DNSSEC protocol succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:4035",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc4035.txt",
                    "occurred_at": "2005-03-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [
            {
                "evidence_id": "current_protocol",
                "record_id": "ietf:rfc:4035",
                "char_start": 0,
                "char_end": len(quote),
            }
        ],
    }
    kwargs = {
        "chunked_record_ids": frozenset({"ietf:rfc:4035"}),
        "chunk_max_tokens": 80,
        "support_priority_record_ids": ("ietf:rfc:4035",),
    }
    unpinned, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0, **kwargs)
    pinned, _ = _artifacts_for_bucket(
        task,
        len,
        "64k",
        1,
        100_000,
        0,
        pin_last_leftover_record_ids=frozenset({"ietf:rfc:4035"}),
        **kwargs,
    )
    assert leftover_c not in "".join(item["text"] for item in unpinned)
    assert leftover_c in "".join(item["text"] for item in pinned)
    assert quote in "".join(item["text"] for item in pinned)
    last = max(pinned, key=lambda item: item["char_start"])
    assert last["essential"] is False
    assert leftover_c in last["text"]


def test_dnssec_packing_keeps_leftover_only_record_as_one_span() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    first = "Request for Comments: 4033 leftover start."
    middle = "Authentic DNSSEC introduction leftover paragraph."
    last = "Authentic DNSSEC introduction leftover end."
    text = "\n\n".join([first, middle, last])
    task = {
        "question": "Resolve DNSSEC protocol succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:4033",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc4033.txt",
                    "occurred_at": "2005-03-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [],
    }
    artifacts, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0)
    leftover = [item for item in artifacts if item["record_id"] == "ietf:rfc:4033"]
    assert len(leftover) == 1
    assert leftover[0]["essential"] is False
    assert leftover[0]["char_start"] == 0
    assert leftover[0]["char_end"] == len(text)
    assert first in leftover[0]["text"]
    assert last in leftover[0]["text"]
