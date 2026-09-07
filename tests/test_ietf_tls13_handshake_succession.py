from __future__ import annotations

import hashlib
import json
from pathlib import Path

from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    audit_ietf_tls13_handshake_succession_task,
    build_ietf_tls13_handshake_succession_task,
    build_ietf_workflow_from_fetch_inventory,
    materialize_ietf_tls13_handshake_counterfactual,
    replay_ietf_tls13_handshake_succession_task,
)
from tests.test_standardsworkflow import _write


def _tls13_inventory(tmp_path: Path) -> dict[str, object]:
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-tls13-test-1",
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
        "drafts": [{"name": "draft-ietf-tls-tls13", "revisions": ["28"]}],
        "rfc_numbers": [5077, 5246, 5705, 6066, 6961, 8446],
        "rfc_datatracker_sources": [8446],
        "approved_public_test_vector_sha256": [],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    bodies = {
        "datatracker-draft-ietf-tls-tls13.json": json.dumps(
            {
                "name": "draft-ietf-tls-tls13",
                "rev": "28",
                "time": "2018-03-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-draft-ietf-tls-tls13-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-tls-tls13/",
                        "target": "/api/v1/doc/document/rfc8446/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft-ietf-tls-tls13-28.txt": (
            b"Internet-Draft draft-ietf-tls-tls13-28\n1 March 2018\nTLS 1.3 draft.\n"
        ),
        "rfc5077.txt": (
            b"Request for Comments: 5077\nJanuary 2008\n"
            b"The TLS server encapsulates the session state into a\n"
            b"   ticket and forwards it to the client.\n"
        ),
        "rfc5246.txt": (
            b"Request for Comments: 5246\nAugust 2008\n"
            b"This document specifies Version 1.2 of the Transport Layer Security\n"
            b"   (TLS) protocol.\n"
        ),
        "rfc5705.txt": (
            b"Request for Comments: 5705\nMarch 2010\n"
            b"A number of protocols wish to leverage Transport Layer Security (TLS)\n"
            b"   to perform key establishment but then use some of the keying material\n"
            b"   for their own purposes.\n"
        ),
        "rfc6066.txt": (
            b"Request for Comments: 6066\nJanuary 2011\n"
            b'It is a companion document for RFC 5246, "The Transport Layer\n'
            b'   Security (TLS) Protocol Version 1.2".\n'
        ),
        "rfc6961.txt": (
            b"Request for Comments: 6961\nJune 2013\n"
            b"This document defines the Transport Layer Security (TLS) Certificate\n"
            b"   Status Version 2 Extension to allow clients to specify and support\n"
            b"   several certificate status methods.\n"
        ),
        "rfc8446.txt": (
            b"Request for Comments: 8446\n"
            b"Obsoletes: 5077, 5246, 6961                                  August 2018\n"
            b"Updates: 5705, 6066\n"
            b"This document specifies version 1.3 of the Transport Layer Security\n"
            b"   (TLS) protocol.\n"
            b"This document supersedes and obsoletes previous versions of TLS,\n"
            b"   including version 1.2 [RFC5246].  It also obsoletes the TLS ticket\n"
            b"   mechanism defined in [RFC5077] and replaces it with the mechanism\n"
            b"   defined in Section 2.2.  Because TLS 1.3 changes the way keys are\n"
            b"   derived, it updates [RFC5705] as described in Section 7.5.  It also\n"
            b"   changes how Online Certificate Status Protocol (OCSP) messages are\n"
            b"   carried and therefore updates [RFC6066] and obsoletes [RFC6961] as\n"
            b"   described in Section 4.4.2.1.\n"
        ),
        "datatracker-rfc8446.json": json.dumps(
            {
                "name": "rfc8446",
                "rfc": "8446",
                "rev": "",
                "time": "2018-08-10T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-rfc8446-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc8446/",
                        "target": "/api/v1/doc/document/rfc5246/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
    }
    specifications = [
        (
            "datatracker_document",
            "datatracker-draft-ietf-tls-tls13.json",
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-tls-tls13/",
        ),
        (
            "datatracker_relation",
            "datatracker-draft-ietf-tls-tls13-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-tls-tls13&limit=100",
        ),
        (
            "draft_revision",
            "draft-ietf-tls-tls13-28.txt",
            "https://www.ietf.org/archive/id/draft-ietf-tls-tls13-28.txt",
        ),
        ("rfc", "rfc5077.txt", "https://www.rfc-editor.org/rfc/rfc5077.txt"),
        ("rfc", "rfc5246.txt", "https://www.rfc-editor.org/rfc/rfc5246.txt"),
        ("rfc", "rfc5705.txt", "https://www.rfc-editor.org/rfc/rfc5705.txt"),
        ("rfc", "rfc6066.txt", "https://www.rfc-editor.org/rfc/rfc6066.txt"),
        ("rfc", "rfc6961.txt", "https://www.rfc-editor.org/rfc/rfc6961.txt"),
        ("rfc", "rfc8446.txt", "https://www.rfc-editor.org/rfc/rfc8446.txt"),
        (
            "datatracker_document",
            "datatracker-rfc8446.json",
            "https://datatracker.ietf.org/api/v1/doc/document/rfc8446/",
        ),
        (
            "datatracker_relation",
            "datatracker-rfc8446-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=rfc8446&limit=100",
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


def test_tls13_succession_task_replays_and_fails_closed_on_remove_one(
    tmp_path: Path,
) -> None:
    inventory = _tls13_inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_tls13_handshake_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.tls13_handshake_succession.v1"
    assert task["query_type"] == "protocol_succession_resolution"
    assert task["answer"] == {
        "current_protocol": "TLS_1_3_RFC8446",
        "obsoletes_session_tickets": "RFC5077",
        "obsoletes_tls12": "RFC5246",
        "obsoletes_ocsp_stapling": "RFC6961",
        "updates_exporters": "RFC5705",
        "updates_extensions": "RFC6066",
    }
    assert replay_ietf_tls13_handshake_succession_task(task) == task["answer"]
    removed = replay_ietf_tls13_handshake_succession_task(
        task,
        evidence_ids=[
            item
            for item in task["essential_evidence_ids"]
            if item != "obsoletes_tls12"
        ],
    )
    assert removed["obsoletes_tls12"] == "UNKNOWN"
    assert removed["current_protocol"] == "TLS_1_3_RFC8446"
    audit = audit_ietf_tls13_handshake_succession_task(task)
    assert audit == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }
    materialized = materialize_ietf_tls13_handshake_counterfactual(task)
    assert materialized["counterfactual_twin"]["evidence_id"] == "current_protocol"
    assert materialized["answer"]["current_protocol"] == "UNKNOWN"
    assert materialized["answer"]["obsoletes_tls12"] == "RFC5246"


def test_tls13_succession_task_binds_official_bytes_when_present() -> None:
    signed = Path(
        "/workspace/wynckeliao/longworld/data/source_inventory/"
        "p57_ietf_tls13_handshake_graph_v1/"
        "ietf_workflow_manifest.p57.tls13-handshake.v1.signed.json"
    )
    if not signed.is_file():
        return
    manifest = json.loads(signed.read_text())
    task = build_ietf_tls13_handshake_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.tls13_handshake_succession.v1"
    assert audit_ietf_tls13_handshake_succession_task(task) == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }


def test_tls13_packing_pins_last_leftover_chunk_without_isolation() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    quote = "This document specifies version 1.3 of the Transport Layer Security protocol."
    leftover_a = "authentic leftover block a " + ("a" * 40)
    leftover_b = "authentic leftover block b " + ("b" * 40)
    leftover_c = "authentic leftover block c " + ("c" * 40)
    text = "\n\n".join([quote, leftover_a, leftover_b, leftover_c])
    task = {
        "question": "Resolve TLS 1.3 handshake succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:8446",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc8446.txt",
                    "occurred_at": "2018-08-10T00:00:00Z",
                }
            ]
        },
        "evidence_items": [
            {
                "evidence_id": "current_protocol",
                "record_id": "ietf:rfc:8446",
                "char_start": 0,
                "char_end": len(quote),
            }
        ],
    }
    kwargs = {
        "chunked_record_ids": frozenset({"ietf:rfc:8446"}),
        "chunk_max_tokens": 80,
        "support_priority_record_ids": ("ietf:rfc:8446",),
    }
    unpinned, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0, **kwargs)
    pinned, _ = _artifacts_for_bucket(
        task,
        len,
        "64k",
        1,
        100_000,
        0,
        pin_last_leftover_record_ids=frozenset({"ietf:rfc:8446"}),
        **kwargs,
    )
    assert leftover_c not in "".join(item["text"] for item in unpinned)
    assert leftover_c in "".join(item["text"] for item in pinned)
    assert quote in "".join(item["text"] for item in pinned)
    last = max(pinned, key=lambda item: item["char_start"])
    assert last["essential"] is False
    assert leftover_c in last["text"]


def test_tls13_packing_keeps_leftover_only_record_as_one_span() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    first = "Request for Comments: 5077 leftover start."
    middle = "Authentic ticket leftover paragraph."
    last = "Authentic ticket leftover end."
    text = "\n\n".join([first, middle, last])
    task = {
        "question": "Resolve TLS 1.3 handshake succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:5077",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc5077.txt",
                    "occurred_at": "2008-01-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [],
    }
    artifacts, _ = _artifacts_for_bucket(task, len, "64k", 1, 100_000, 0)
    leftover = [item for item in artifacts if item["record_id"] == "ietf:rfc:5077"]
    assert len(leftover) == 1
    assert leftover[0]["essential"] is False
    assert leftover[0]["char_start"] == 0
    assert leftover[0]["char_end"] == len(text)
    assert first in leftover[0]["text"]
    assert last in leftover[0]["text"]
