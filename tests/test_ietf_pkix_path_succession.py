from __future__ import annotations

import hashlib
import json
from pathlib import Path

from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    audit_ietf_pkix_path_succession_task,
    build_ietf_pkix_path_succession_task,
    build_ietf_workflow_from_fetch_inventory,
    materialize_ietf_pkix_path_counterfactual,
    replay_ietf_pkix_path_succession_task,
)
from tests.test_standardsworkflow import _write

ROOT = Path(__file__).resolve().parents[1]


def _pkix_path_inventory(tmp_path: Path) -> dict[str, object]:
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-pkix-path-test-1",
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
        "drafts": [{"name": "draft-ietf-pkix-rfc3280bis", "revisions": ["11"]}],
        "rfc_numbers": [3280, 4325, 4630, 5280],
        "rfc_datatracker_sources": [5280],
        "approved_public_test_vector_sha256": [],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    bodies = {
        "datatracker-draft-ietf-pkix-rfc3280bis.json": json.dumps(
            {
                "name": "draft-ietf-pkix-rfc3280bis",
                "rev": "11",
                "time": "2008-04-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-draft-ietf-pkix-rfc3280bis-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-pkix-rfc3280bis/",
                        "target": "/api/v1/doc/document/rfc5280/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft-ietf-pkix-rfc3280bis-11.txt": (
            b"Internet-Draft draft-ietf-pkix-rfc3280bis-11\n1 April 2008\n"
            b"PKIX path validation draft leftover.\n"
        ),
        "rfc3280.txt": (
            b"Request for Comments: 3280\nApril 2002\n"
            b"Internet X.509 Public Key Infrastructure leftover profile.\n"
        ),
        "rfc4325.txt": (
            b"Request for Comments: 4325\n"
            b"Updates: 3280                                                 R. Housley\n"
            b"December 2005\n"
            b"Authority Information Access CRL extension leftover whole span.\n"
        ),
        "rfc4630.txt": (
            b"Request for Comments: 4630\n"
            b"Updates: 3280                                               S. Santesson\n"
            b"August 2006\n"
            b"DirectoryString encoding leftover whole span.\n"
        ),
        "rfc5280.txt": (
            b"Request for Comments: 5280\n"
            b"Obsoletes: 3280, 4325, 4630                                 S. Santesson\n"
            b"May 2008\n"
            b"This memo profiles the X.509 v3 certificate and X.509 v2 certificate\n"
            b"   revocation list (CRL) for use in the Internet.\n"
            b"This specification obsoletes [RFC3280].  Differences from RFC 3280\n"
            b"   are summarized below:\n"
            b"      * Sections 4.1.2.4 and 4.1.2.6 incorporate the conditions for\n"
            b"        continued use of legacy text encoding schemes that were\n"
            b"        specified in [RFC4630].  Where in use by an established PKI,\n"
            b"      * The Authority Information Access (AIA) CRL extension, as\n"
            b"        specified in [RFC4325], was added as Section 5.2.7.\n"
            b"An algorithm for X.509 certification path validation is described.\n"
        ),
        "datatracker-rfc5280.json": json.dumps(
            {
                "name": "rfc5280",
                "rfc": "5280",
                "rev": "",
                "time": "2008-05-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-rfc5280-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc5280/",
                        "target": "/api/v1/doc/document/rfc3280/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
    }
    specifications = [
        (
            "datatracker_document",
            "datatracker-draft-ietf-pkix-rfc3280bis.json",
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-pkix-rfc3280bis/",
        ),
        (
            "datatracker_relation",
            "datatracker-draft-ietf-pkix-rfc3280bis-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-pkix-rfc3280bis&limit=100",
        ),
        (
            "draft_revision",
            "draft-ietf-pkix-rfc3280bis-11.txt",
            "https://www.ietf.org/archive/id/draft-ietf-pkix-rfc3280bis-11.txt",
        ),
        ("rfc", "rfc3280.txt", "https://www.rfc-editor.org/rfc/rfc3280.txt"),
        ("rfc", "rfc4325.txt", "https://www.rfc-editor.org/rfc/rfc4325.txt"),
        ("rfc", "rfc4630.txt", "https://www.rfc-editor.org/rfc/rfc4630.txt"),
        ("rfc", "rfc5280.txt", "https://www.rfc-editor.org/rfc/rfc5280.txt"),
        (
            "datatracker_document",
            "datatracker-rfc5280.json",
            "https://datatracker.ietf.org/api/v1/doc/document/rfc5280/",
        ),
        (
            "datatracker_relation",
            "datatracker-rfc5280-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=rfc5280&limit=100",
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
            "completed_at": "2026-08-29T01:00:12Z",
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


def test_pkix_path_succession_task_replays_and_fails_closed_on_remove_one(
    tmp_path: Path,
) -> None:
    inventory = _pkix_path_inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_pkix_path_succession_task(manifest)
    assert task["schema_version"] == "longworld.ietf-pkix-path-succession-task.v1"
    assert task["answer_program_id"] == "ietf.pkix_path_succession.v1"
    assert task["query_type"] == "protocol_succession_resolution"
    assert task["answer"] == {
        "current_protocol": "PKIX_PATH_RFC5280",
        "obsoletes_certificate_crl_profile": "RFC3280",
        "obsoletes_aia_crl_extension": "RFC4325",
        "obsoletes_directorystring": "RFC4630",
        "path_validation_algorithm": "X509_PATH_VALIDATION_ALGORITHM",
    }
    assert all(
        item["record_id"] == "ietf:rfc:5280" for item in task["evidence_items"]
    )
    assert replay_ietf_pkix_path_succession_task(task) == task["answer"]
    removed = replay_ietf_pkix_path_succession_task(
        task,
        evidence_ids=[
            item
            for item in task["essential_evidence_ids"]
            if item != "obsoletes_certificate_crl_profile"
        ],
    )
    assert removed["obsoletes_certificate_crl_profile"] == "UNKNOWN"
    assert removed["current_protocol"] == "PKIX_PATH_RFC5280"
    audit = audit_ietf_pkix_path_succession_task(task)
    assert audit == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }
    materialized = materialize_ietf_pkix_path_counterfactual(task)
    assert materialized["counterfactual_twin"]["evidence_id"] == "current_protocol"
    assert materialized["answer"]["current_protocol"] == "UNKNOWN"
    assert materialized["answer"]["obsoletes_certificate_crl_profile"] == "RFC3280"


def test_pkix_path_succession_task_binds_official_bytes_when_present() -> None:
    signed = Path(
        f"{ROOT}/data/source_inventory/"
        "p57_ietf_pkix_path_graph_v1/"
        "ietf_workflow_manifest.p57.pkix-path.v1.signed.json"
    )
    if not signed.is_file():
        return
    manifest = json.loads(signed.read_text())
    task = build_ietf_pkix_path_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.pkix_path_succession.v1"
    assert all(
        item["record_id"] == "ietf:rfc:5280" for item in task["evidence_items"]
    )
    quotes = [item["evidence_quote"] for item in task["evidence_items"]]
    assert len(quotes) == len(set(quotes))
    assert audit_ietf_pkix_path_succession_task(task) == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }


def test_pkix_path_packing_pins_last_leftover_chunk_without_isolation() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    quote = "This memo profiles the X.509 v3 certificate and X.509 v2 certificate."
    leftover_a = "authentic leftover block a " + ("a" * 40)
    leftover_b = "authentic leftover block b " + ("b" * 40)
    leftover_c = "authentic leftover block c " + ("c" * 40)
    text = "\n\n".join([quote, leftover_a, leftover_b, leftover_c])
    task = {
        "question": "Resolve PKIX path succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:5280",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc5280.txt",
                    "occurred_at": "2008-05-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [
            {
                "evidence_id": "current_protocol",
                "record_id": "ietf:rfc:5280",
                "char_start": 0,
                "char_end": len(quote),
            }
        ],
    }
    kwargs = {
        "chunked_record_ids": frozenset({"ietf:rfc:5280"}),
        "chunk_max_tokens": 80,
        "support_priority_record_ids": ("ietf:rfc:5280",),
    }
    unpinned, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0, **kwargs)
    pinned, _ = _artifacts_for_bucket(
        task,
        len,
        "64k",
        1,
        100_000,
        0,
        pin_last_leftover_record_ids=frozenset({"ietf:rfc:5280"}),
        **kwargs,
    )
    assert leftover_c not in "".join(item["text"] for item in unpinned)
    assert leftover_c in "".join(item["text"] for item in pinned)
    assert quote in "".join(item["text"] for item in pinned)
    last = max(pinned, key=lambda item: item["char_start"])
    assert last["essential"] is False
    assert leftover_c in last["text"]


def test_pkix_path_packing_keeps_leftover_only_record_as_one_span() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    first = "Request for Comments: 4325 leftover start."
    middle = "Authentic AIA leftover paragraph."
    last = "Authentic AIA leftover end."
    text = "\n\n".join([first, middle, last])
    task = {
        "question": "Resolve PKIX path succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:4325",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc4325.txt",
                    "occurred_at": "2005-12-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [],
    }
    artifacts, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0)
    leftover = [item for item in artifacts if item["record_id"] == "ietf:rfc:4325"]
    assert len(leftover) == 1
    assert leftover[0]["essential"] is False
    assert leftover[0]["char_start"] == 0
    assert leftover[0]["char_end"] == len(text)
    assert first in leftover[0]["text"]
    assert last in leftover[0]["text"]
