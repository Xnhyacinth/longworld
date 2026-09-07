from __future__ import annotations

import hashlib
import json
from pathlib import Path

from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    audit_ietf_http_semantics_succession_task,
    build_ietf_http_semantics_succession_task,
    build_ietf_workflow_from_fetch_inventory,
    materialize_ietf_http_semantics_counterfactual,
    replay_ietf_http_semantics_succession_task,
)
from tests.test_standardsworkflow import _write


def _http_semantics_inventory(tmp_path: Path) -> dict[str, object]:
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-http-semantics-test-1",
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
        "drafts": [{"name": "draft-ietf-httpbis-semantics", "revisions": ["19"]}],
        "rfc_numbers": [
            2818,
            3864,
            7230,
            7231,
            7232,
            7233,
            7235,
            7538,
            7615,
            7694,
            9110,
        ],
        "rfc_datatracker_sources": [9110],
        "approved_public_test_vector_sha256": [],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    bodies = {
        "datatracker-draft-ietf-httpbis-semantics.json": json.dumps(
            {
                "name": "draft-ietf-httpbis-semantics",
                "rev": "19",
                "time": "2022-06-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-draft-ietf-httpbis-semantics-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-httpbis-semantics/",
                        "target": "/api/v1/doc/document/rfc9110/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft-ietf-httpbis-semantics-19.txt": (
            b"Internet-Draft draft-ietf-httpbis-semantics-19\n1 June 2022\n"
            b"HTTP Semantics draft.\n"
        ),
        "rfc2818.txt": (
            b"Request for Comments: 2818\nMay 2000\nHTTP Over TLS leftover.\n"
        ),
        "rfc3864.txt": (
            b"Request for Comments: 3864\nSeptember 2004\n"
            b"Registration Procedures for Message Header Fields leftover.\n"
        ),
        "rfc7230.txt": (
            b"Request for Comments: 7230\n"
            b"Updates: 2818                                              June 2014\n"
            b"HTTP/1.1 Message Syntax and Routing leftover.\n"
        ),
        "rfc7231.txt": (
            b"Request for Comments: 7231\nJune 2014\n"
            b"HTTP/1.1 Semantics and Content leftover.\n"
        ),
        "rfc7232.txt": (
            b"Request for Comments: 7232\nJune 2014\n"
            b"HTTP/1.1 Conditional Requests leftover.\n"
        ),
        "rfc7233.txt": (
            b"Request for Comments: 7233\nJune 2014\n"
            b"HTTP/1.1 Range Requests leftover.\n"
        ),
        "rfc7235.txt": (
            b"Request for Comments: 7235\nJune 2014\n"
            b"HTTP/1.1 Authentication leftover.\n"
        ),
        "rfc7538.txt": (
            b"Request for Comments: 7538\nApril 2015\n"
            b"HTTP Status Code 308 leftover whole span.\n"
        ),
        "rfc7615.txt": (
            b"Request for Comments: 7615\nSeptember 2015\n"
            b"HTTP Authentication-Info leftover whole span.\n"
        ),
        "rfc7694.txt": (
            b"Request for Comments: 7694\nNovember 2015\n"
            b"HTTP Client-Initiated Content-Encoding leftover whole span.\n"
        ),
        "rfc9110.txt": (
            b"Request for Comments: 9110\n"
            b"Obsoletes: 2818, 7230, 7231, 7232, 7233, 7235                  June 2022\n"
            b"Updates: 3864\n"
            b"HTTP Semantics\n"
            b"This document describes the overall architecture of HTTP,\n"
            b"   establishes common terminology, and defines aspects of the protocol\n"
            b"   that are shared by all versions.\n"
            b"| HTTP Over TLS                              | [RFC2818] | B.1 |\n"
            b"| HTTP/1.1 Message Syntax and Routing [*]    | [RFC7230] | B.2 |\n"
            b"| HTTP/1.1 Semantics and Content             | [RFC7231] | B.3 |\n"
            b"| HTTP/1.1 Conditional Requests              | [RFC7232] | B.4 |\n"
            b"| HTTP/1.1 Range Requests                    | [RFC7233] | B.5 |\n"
            b"| HTTP/1.1 Authentication                    | [RFC7235] | B.6 |\n"
            b"This specification updates the HTTP-related aspects of the existing\n"
            b"   registration procedures for message header fields defined in\n"
            b"   [RFC3864].\n"
        ),
        "datatracker-rfc9110.json": json.dumps(
            {
                "name": "rfc9110",
                "rfc": "9110",
                "rev": "",
                "time": "2022-06-06T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-rfc9110-relations.json": json.dumps(
            {
                "meta": {"total_count": 3},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc9110/",
                        "target": "/api/v1/doc/document/rfc7538/",
                    },
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc9110/",
                        "target": "/api/v1/doc/document/rfc7615/",
                    },
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc9110/",
                        "target": "/api/v1/doc/document/rfc7694/",
                    },
                ],
            },
            sort_keys=True,
        ).encode(),
    }
    specifications = [
        (
            "datatracker_document",
            "datatracker-draft-ietf-httpbis-semantics.json",
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-httpbis-semantics/",
        ),
        (
            "datatracker_relation",
            "datatracker-draft-ietf-httpbis-semantics-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-httpbis-semantics&limit=100",
        ),
        (
            "draft_revision",
            "draft-ietf-httpbis-semantics-19.txt",
            "https://www.ietf.org/archive/id/draft-ietf-httpbis-semantics-19.txt",
        ),
        ("rfc", "rfc2818.txt", "https://www.rfc-editor.org/rfc/rfc2818.txt"),
        ("rfc", "rfc3864.txt", "https://www.rfc-editor.org/rfc/rfc3864.txt"),
        ("rfc", "rfc7230.txt", "https://www.rfc-editor.org/rfc/rfc7230.txt"),
        ("rfc", "rfc7231.txt", "https://www.rfc-editor.org/rfc/rfc7231.txt"),
        ("rfc", "rfc7232.txt", "https://www.rfc-editor.org/rfc/rfc7232.txt"),
        ("rfc", "rfc7233.txt", "https://www.rfc-editor.org/rfc/rfc7233.txt"),
        ("rfc", "rfc7235.txt", "https://www.rfc-editor.org/rfc/rfc7235.txt"),
        ("rfc", "rfc7538.txt", "https://www.rfc-editor.org/rfc/rfc7538.txt"),
        ("rfc", "rfc7615.txt", "https://www.rfc-editor.org/rfc/rfc7615.txt"),
        ("rfc", "rfc7694.txt", "https://www.rfc-editor.org/rfc/rfc7694.txt"),
        ("rfc", "rfc9110.txt", "https://www.rfc-editor.org/rfc/rfc9110.txt"),
        (
            "datatracker_document",
            "datatracker-rfc9110.json",
            "https://datatracker.ietf.org/api/v1/doc/document/rfc9110/",
        ),
        (
            "datatracker_relation",
            "datatracker-rfc9110-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=rfc9110&limit=100",
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


def test_http_semantics_succession_task_replays_and_fails_closed_on_remove_one(
    tmp_path: Path,
) -> None:
    inventory = _http_semantics_inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_http_semantics_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.http_semantics_succession.v1"
    assert task["query_type"] == "protocol_succession_resolution"
    assert task["answer"] == {
        "current_protocol": "HTTP_SEMANTICS_RFC9110",
        "obsoletes_http_over_tls": "RFC2818",
        "obsoletes_http11_message": "RFC7230",
        "obsoletes_semantics_content": "RFC7231",
        "obsoletes_conditional": "RFC7232",
        "obsoletes_range": "RFC7233",
        "obsoletes_authentication": "RFC7235",
        "updates_header_registration": "RFC3864",
    }
    assert all(
        item["record_id"] == "ietf:rfc:9110" for item in task["evidence_items"]
    )
    assert replay_ietf_http_semantics_succession_task(task) == task["answer"]
    removed = replay_ietf_http_semantics_succession_task(
        task,
        evidence_ids=[
            item
            for item in task["essential_evidence_ids"]
            if item != "obsoletes_semantics_content"
        ],
    )
    assert removed["obsoletes_semantics_content"] == "UNKNOWN"
    assert removed["current_protocol"] == "HTTP_SEMANTICS_RFC9110"
    audit = audit_ietf_http_semantics_succession_task(task)
    assert audit == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }
    materialized = materialize_ietf_http_semantics_counterfactual(task)
    assert materialized["counterfactual_twin"]["evidence_id"] == "current_protocol"
    assert materialized["answer"]["current_protocol"] == "UNKNOWN"
    assert materialized["answer"]["obsoletes_http11_message"] == "RFC7230"


def test_http_semantics_succession_task_binds_official_bytes_when_present() -> None:
    signed = Path(
        "/workspace/wynckeliao/longworld/data/source_inventory/"
        "p57_ietf_http_semantics_graph_v1/"
        "ietf_workflow_manifest.p57.http-semantics.v1.signed.json"
    )
    if not signed.is_file():
        return
    manifest = json.loads(signed.read_text())
    task = build_ietf_http_semantics_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.http_semantics_succession.v1"
    assert all(
        item["record_id"] == "ietf:rfc:9110" for item in task["evidence_items"]
    )
    assert audit_ietf_http_semantics_succession_task(task) == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }


def test_http_semantics_packing_pins_last_leftover_chunk_without_isolation() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    quote = "This document describes the overall architecture of HTTP."
    leftover_a = "authentic leftover block a " + ("a" * 40)
    leftover_b = "authentic leftover block b " + ("b" * 40)
    leftover_c = "authentic leftover block c " + ("c" * 40)
    text = "\n\n".join([quote, leftover_a, leftover_b, leftover_c])
    task = {
        "question": "Resolve HTTP Semantics succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:9110",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc9110.txt",
                    "occurred_at": "2022-06-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [
            {
                "evidence_id": "current_protocol",
                "record_id": "ietf:rfc:9110",
                "char_start": 0,
                "char_end": len(quote),
            }
        ],
    }
    kwargs = {
        "chunked_record_ids": frozenset({"ietf:rfc:9110"}),
        "chunk_max_tokens": 80,
        "support_priority_record_ids": ("ietf:rfc:9110",),
    }
    unpinned, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0, **kwargs)
    pinned, _ = _artifacts_for_bucket(
        task,
        len,
        "64k",
        1,
        100_000,
        0,
        pin_last_leftover_record_ids=frozenset({"ietf:rfc:9110"}),
        **kwargs,
    )
    assert leftover_c not in "".join(item["text"] for item in unpinned)
    assert leftover_c in "".join(item["text"] for item in pinned)
    assert quote in "".join(item["text"] for item in pinned)
    last = max(pinned, key=lambda item: item["char_start"])
    assert last["essential"] is False
    assert leftover_c in last["text"]


def test_http_semantics_packing_keeps_leftover_only_record_as_one_span() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    first = "Request for Comments: 7538 leftover start."
    middle = "Authentic 308 leftover paragraph."
    last = "Authentic 308 leftover end."
    text = "\n\n".join([first, middle, last])
    task = {
        "question": "Resolve HTTP Semantics succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:7538",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc7538.txt",
                    "occurred_at": "2015-04-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [],
    }
    artifacts, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0)
    leftover = [item for item in artifacts if item["record_id"] == "ietf:rfc:7538"]
    assert len(leftover) == 1
    assert leftover[0]["essential"] is False
    assert leftover[0]["char_start"] == 0
    assert leftover[0]["char_end"] == len(text)
    assert first in leftover[0]["text"]
    assert last in leftover[0]["text"]
