from __future__ import annotations

import hashlib
import json
from pathlib import Path

from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    audit_ietf_http2_succession_task,
    build_ietf_http2_succession_task,
    build_ietf_workflow_from_fetch_inventory,
    materialize_ietf_http2_counterfactual,
    replay_ietf_http2_succession_task,
)
from tests.test_standardsworkflow import _write


def _http2_inventory(tmp_path: Path) -> dict[str, object]:
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-http2-test-1",
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
        "drafts": [{"name": "draft-ietf-httpbis-http2bis", "revisions": ["07"]}],
        "rfc_numbers": [7540, 8740, 9113],
        "rfc_datatracker_sources": [9113],
        "approved_public_test_vector_sha256": [],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    bodies = {
        "datatracker-draft-ietf-httpbis-http2bis.json": json.dumps(
            {
                "name": "draft-ietf-httpbis-http2bis",
                "rev": "07",
                "time": "2022-01-13T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-draft-ietf-httpbis-http2bis-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-httpbis-http2bis/",
                        "target": "/api/v1/doc/document/rfc9113/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft-ietf-httpbis-http2bis-07.txt": (
            b"Internet-Draft draft-ietf-httpbis-http2bis-07\n13 January 2022\n"
            b"HTTP/2 draft.\n"
        ),
        "rfc7540.txt": (
            b"Request for Comments: 7540\nMay 2015\n"
            b"Hypertext Transfer Protocol Version 2 leftover whole span.\n"
        ),
        "rfc8740.txt": (
            b"Request for Comments: 8740\n"
            b"Updates: 7540                                              February 2020\n"
            b"Using TLS 1.3 with HTTP/2 leftover whole span.\n"
        ),
        "rfc9113.txt": (
            b"Request for Comments: 9113\n"
            b"Obsoletes: 7540, 8740                                   June 2022\n"
            b"HTTP/2\n"
            b"This specification describes an optimized expression of the semantics\n"
            b"   of the Hypertext Transfer Protocol (HTTP), referred to as HTTP\n"
            b"   version 2 (HTTP/2).\n"
            b"This document obsoletes RFCs 7540 and 8740.\n"
            b"Use of TLS 1.3 was defined based on [RFC8740], which this document\n"
            b"      obsoletes.\n"
            b"The priority scheme defined in RFC 7540 is deprecated.\n"
            b"HTTP/2 provides an optimized transport for HTTP semantics.\n"
            b'HTTP/2 over TLS uses the "h2" protocol identifier.\n'
        ),
        "datatracker-rfc9113.json": json.dumps(
            {
                "name": "rfc9113",
                "rfc": "9113",
                "rev": "",
                "time": "2022-06-06T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-rfc9113-relations.json": json.dumps(
            {
                "meta": {"total_count": 2},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc9113/",
                        "target": "/api/v1/doc/document/rfc7540/",
                    },
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc9113/",
                        "target": "/api/v1/doc/document/rfc8740/",
                    },
                ],
            },
            sort_keys=True,
        ).encode(),
    }
    specifications = [
        (
            "datatracker_document",
            "datatracker-draft-ietf-httpbis-http2bis.json",
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-httpbis-http2bis/",
        ),
        (
            "datatracker_relation",
            "datatracker-draft-ietf-httpbis-http2bis-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-httpbis-http2bis&limit=100",
        ),
        (
            "draft_revision",
            "draft-ietf-httpbis-http2bis-07.txt",
            "https://www.ietf.org/archive/id/draft-ietf-httpbis-http2bis-07.txt",
        ),
        ("rfc", "rfc7540.txt", "https://www.rfc-editor.org/rfc/rfc7540.txt"),
        ("rfc", "rfc8740.txt", "https://www.rfc-editor.org/rfc/rfc8740.txt"),
        ("rfc", "rfc9113.txt", "https://www.rfc-editor.org/rfc/rfc9113.txt"),
        (
            "datatracker_document",
            "datatracker-rfc9113.json",
            "https://datatracker.ietf.org/api/v1/doc/document/rfc9113/",
        ),
        (
            "datatracker_relation",
            "datatracker-rfc9113-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=rfc9113&limit=100",
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


def test_http2_succession_task_replays_and_fails_closed_on_remove_one(
    tmp_path: Path,
) -> None:
    inventory = _http2_inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_http2_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.http2_succession.v1"
    assert task["schema_version"] == "longworld.ietf-http2-succession-task.v1"
    assert task["query_type"] == "protocol_succession_resolution"
    assert task["answer"] == {
        "current_protocol": "HTTP_2_RFC9113",
        "obsoletes_http2": "RFC7540",
        "obsoletes_tls13_http2": "RFC8740",
        "optimized_transport": "HTTP_2_OPTIMIZED_TRANSPORT",
        "tls_alpn_id": "HTTP_2_OVER_TLS_H2",
    }
    assert all(item["record_id"] == "ietf:rfc:9113" for item in task["evidence_items"])
    assert replay_ietf_http2_succession_task(task) == task["answer"]
    removed = replay_ietf_http2_succession_task(
        task,
        evidence_ids=[
            item
            for item in task["essential_evidence_ids"]
            if item != "obsoletes_http2"
        ],
    )
    assert removed["obsoletes_http2"] == "UNKNOWN"
    assert removed["current_protocol"] == "HTTP_2_RFC9113"
    assert removed["obsoletes_tls13_http2"] == "RFC8740"
    audit = audit_ietf_http2_succession_task(task)
    assert audit == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }
    materialized = materialize_ietf_http2_counterfactual(task)
    assert materialized["counterfactual_twin"]["evidence_id"] == "current_protocol"
    assert materialized["answer"]["current_protocol"] == "UNKNOWN"
    assert materialized["answer"]["obsoletes_http2"] == "RFC7540"


def test_http2_succession_task_binds_official_bytes_when_present() -> None:
    signed = Path(
        "/workspace/wynckeliao/longworld/data/source_inventory/"
        "p57_ietf_http2_graph_v1/"
        "ietf_workflow_manifest.p57.http2.v1.signed.json"
    )
    if not signed.is_file():
        return
    manifest = json.loads(signed.read_text())
    task = build_ietf_http2_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.http2_succession.v1"
    assert task["schema_version"] == "longworld.ietf-http2-succession-task.v1"
    assert all(item["record_id"] == "ietf:rfc:9113" for item in task["evidence_items"])
    assert audit_ietf_http2_succession_task(task) == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }


def test_http2_packing_pins_last_leftover_chunk_without_isolation() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    quote = "This specification describes HTTP version 2 (HTTP/2)."
    leftover_a = "authentic leftover block a " + ("a" * 40)
    leftover_b = "authentic leftover block b " + ("b" * 40)
    leftover_c = "authentic leftover block c " + ("c" * 40)
    text = "\n\n".join([quote, leftover_a, leftover_b, leftover_c])
    task = {
        "question": "Resolve HTTP/2 succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:9113",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc9113.txt",
                    "occurred_at": "2022-06-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [
            {
                "evidence_id": "current_protocol",
                "record_id": "ietf:rfc:9113",
                "char_start": 0,
                "char_end": len(quote),
            }
        ],
    }
    kwargs = {
        "chunked_record_ids": frozenset({"ietf:rfc:9113"}),
        "chunk_max_tokens": 80,
        "support_priority_record_ids": ("ietf:rfc:9113",),
    }
    unpinned, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0, **kwargs)
    pinned, _ = _artifacts_for_bucket(
        task,
        len,
        "64k",
        1,
        100_000,
        0,
        pin_last_leftover_record_ids=frozenset({"ietf:rfc:9113"}),
        **kwargs,
    )
    assert leftover_c not in "".join(item["text"] for item in unpinned)
    assert leftover_c in "".join(item["text"] for item in pinned)
    assert quote in "".join(item["text"] for item in pinned)
    last = max(pinned, key=lambda item: item["char_start"])
    assert last["essential"] is False
    assert leftover_c in last["text"]


def test_http2_packing_keeps_leftover_only_record_as_one_span() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    first = "Request for Comments: 8740 leftover start."
    middle = "Authentic TLS 1.3 HTTP/2 leftover paragraph."
    last = "Authentic TLS 1.3 HTTP/2 leftover end."
    text = "\n\n".join([first, middle, last])
    task = {
        "question": "Resolve HTTP/2 succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:8740",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc8740.txt",
                    "occurred_at": "2020-02-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [],
    }
    artifacts, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0)
    leftover = [item for item in artifacts if item["record_id"] == "ietf:rfc:8740"]
    assert len(leftover) == 1
    assert leftover[0]["essential"] is False
    assert leftover[0]["char_start"] == 0
    assert leftover[0]["char_end"] == len(text)
    assert first in leftover[0]["text"]
    assert last in leftover[0]["text"]


def test_http2_generation_config_refuses_isolate_and_128k() -> None:
    from reports.p57_ietf_http2_succession_generate import (
        validate_http2_generation_config,
    )

    base = {
        "schema_version": "longworld.ietf-http2-generation-config.v1",
        "length_buckets": {"64k": [64000, 65536]},
        "packing": {"chunk_max_tokens": 8192, "chunk_max_tokens_by_bucket": {"64k": 8192}},
    }
    validate_http2_generation_config(base)
    isolated = {
        **base,
        "packing": {**base["packing"], "isolate_evidence_ids": ["current_protocol"]},
    }
    try:
        validate_http2_generation_config(isolated)
    except ValueError as error:
        assert "isolate" in str(error)
    else:
        raise AssertionError("isolate_evidence_ids must be refused")
    padded = {**base, "length_buckets": {"64k": [64000, 65536], "128k": [128000, 131072]}}
    try:
        validate_http2_generation_config(padded)
    except ValueError as error:
        assert "128k" in str(error)
    else:
        raise AssertionError("128k must be refused")
    thin = {
        **base,
        "packing": {"chunk_max_tokens": 1024, "chunk_max_tokens_by_bucket": {"64k": 1024}},
    }
    try:
        validate_http2_generation_config(thin)
    except ValueError as error:
        assert "8192" in str(error) or "thick" in str(error)
    else:
        raise AssertionError("thin 64k chunks must be refused")
