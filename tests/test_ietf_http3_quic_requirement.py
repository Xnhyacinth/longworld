from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    audit_ietf_http3_quic_requirement_task,
    build_ietf_http3_quic_requirement_task,
    build_ietf_workflow_from_fetch_inventory,
    materialize_ietf_http3_quic_counterfactual,
    replay_ietf_http3_quic_requirement_task,
)
from tests.test_standardsworkflow import _write


def _http3_inventory(tmp_path: Path) -> dict[str, object]:
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-http3-test-1",
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
        "drafts": [{"name": "draft-ietf-quic-http", "revisions": ["34"]}],
        "rfc_numbers": [7301, 8446, 9000, 9114, 9204],
        "rfc_datatracker_sources": [9114],
        "approved_public_test_vector_sha256": [],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    bodies = {
        "datatracker-draft-ietf-quic-http.json": json.dumps(
            {
                "name": "draft-ietf-quic-http",
                "rev": "34",
                "time": "2022-06-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-draft-ietf-quic-http-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-quic-http/",
                        "target": "/api/v1/doc/document/rfc9114/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft-ietf-quic-http-34.txt": (
            b"Internet-Draft draft-ietf-quic-http-34\n1 June 2022\nHTTP/3 mapping.\n"
        ),
        "rfc7301.txt": (
            b"Request for Comments: 7301\nJuly 2014\n"
            b'except that the "ProtocolNameList" MUST contain\n'
            b'   exactly one "ProtocolName".\n'
        ),
        "rfc8446.txt": (
            b"Request for Comments: 8446\nAugust 2018\n"
            b"This document specifies version 1.3 of the Transport Layer Security\n"
            b"   (TLS) protocol.\n"
        ),
        "rfc9000.txt": (
            b"Request for Comments: 9000\nMay 2021\n"
            b"This document defines the core of the QUIC transport protocol.\n"
        ),
        "rfc9114.txt": (
            b"Request for Comments: 9114\nJune 2022\n"
            b"HTTP/3 replaces HPACK with QPACK ([QPACK]).\n"
            b"HTTP/3 relies on QUIC version 1 as the underlying transport.\n"
            b"QUIC version 1 uses TLS version 1.3 or greater as its handshake\n"
            b"   protocol.\n"
            b'selecting the ALPN token "h3" in the TLS handshake.\n'
            b"After the QUIC connection is\n"
            b"   established, a SETTINGS frame MUST be sent by each endpoint as the\n"
            b"   initial frame of their respective HTTP control stream.\n"
        ),
        "rfc9204.txt": (
            b"Request for Comments: 9204\nJune 2022\n"
            b"This specification defines QPACK: a compression format for\n"
            b"   efficiently representing HTTP fields that is to be used in HTTP/3.\n"
        ),
        "datatracker-rfc9114.json": json.dumps(
            {
                "name": "rfc9114",
                "rfc": "9114",
                "rev": "",
                "time": "2022-06-06T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-rfc9114-relations.json": json.dumps(
            {
                "meta": {"total_count": 4},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refnorm/",
                        "source": "/api/v1/doc/document/rfc9114/",
                        "target": "/api/v1/doc/document/rfc7301/",
                    },
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refnorm/",
                        "source": "/api/v1/doc/document/rfc9114/",
                        "target": "/api/v1/doc/document/rfc9000/",
                    },
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refnorm/",
                        "source": "/api/v1/doc/document/rfc9114/",
                        "target": "/api/v1/doc/document/rfc9204/",
                    },
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc9114/",
                        "target": "/api/v1/doc/document/rfc8446/",
                    },
                ],
            },
            sort_keys=True,
        ).encode(),
    }
    specifications = [
        (
            "datatracker_document",
            "datatracker-draft-ietf-quic-http.json",
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-quic-http/",
        ),
        (
            "datatracker_relation",
            "datatracker-draft-ietf-quic-http-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-quic-http&limit=100",
        ),
        (
            "draft_revision",
            "draft-ietf-quic-http-34.txt",
            "https://www.ietf.org/archive/id/draft-ietf-quic-http-34.txt",
        ),
        ("rfc", "rfc7301.txt", "https://www.rfc-editor.org/rfc/rfc7301.txt"),
        ("rfc", "rfc8446.txt", "https://www.rfc-editor.org/rfc/rfc8446.txt"),
        ("rfc", "rfc9000.txt", "https://www.rfc-editor.org/rfc/rfc9000.txt"),
        ("rfc", "rfc9114.txt", "https://www.rfc-editor.org/rfc/rfc9114.txt"),
        ("rfc", "rfc9204.txt", "https://www.rfc-editor.org/rfc/rfc9204.txt"),
        (
            "datatracker_document",
            "datatracker-rfc9114.json",
            "https://datatracker.ietf.org/api/v1/doc/document/rfc9114/",
        ),
        (
            "datatracker_relation",
            "datatracker-rfc9114-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=rfc9114&limit=100",
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
            "completed_at": "2026-08-29T01:00:11Z",
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


def test_http3_requirement_task_replays_and_fails_closed_on_remove_one(
    tmp_path: Path,
) -> None:
    inventory = _http3_inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_http3_quic_requirement_task(manifest)
    assert task["answer_program_id"] == "ietf.http3_quic_effective_requirement.v1"
    assert task["answer"] == {
        "transport": "PASS_QUIC_V1",
        "tls_version": "PASS_TLS_1_3",
        "alpn": "PASS_H3",
        "header_compression": "PASS_QPACK",
        "settings": "PASS_SETTINGS_FIRST",
    }
    assert replay_ietf_http3_quic_requirement_task(task) == task["answer"]
    removed_transport = replay_ietf_http3_quic_requirement_task(
        task,
        evidence_ids=[
            item
            for item in task["essential_evidence_ids"]
            if item != "transport_current"
        ],
    )
    assert removed_transport["transport"] == "UNKNOWN"
    assert removed_transport["settings"] == "PASS_SETTINGS_FIRST"
    audit = audit_ietf_http3_quic_requirement_task(task)
    assert audit == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }
    materialized = materialize_ietf_http3_quic_counterfactual(task)
    assert materialized["counterfactual_twin"]["evidence_id"] == "transport_current"
    assert materialized["answer"]["transport"] == "UNKNOWN"
    assert materialized["answer"]["settings"] == "PASS_SETTINGS_FIRST"


def test_http3_requirement_task_binds_official_bytes_when_present() -> None:
    signed = Path(
        "/workspace/wynckeliao/longworld/data/source_inventory/"
        "p57_ietf_http3_quic_requirement_graph_v1/"
        "ietf_workflow_manifest.p57.http3-quic-requirement.v1.signed.json"
    )
    if not signed.is_file():
        return
    manifest = json.loads(signed.read_text())
    task = build_ietf_http3_quic_requirement_task(manifest)
    assert task["answer_program_id"] == "ietf.http3_quic_effective_requirement.v1"
    assert audit_ietf_http3_quic_requirement_task(task) == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }


def test_http3_cf_target_keeps_authentic_host_remnant() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import (
        _host_counterfactual_span,
    )

    evidence = [
        {"evidence_id": "transport_current", "char_start": 10, "char_end": 20},
        {"evidence_id": "tls_current", "char_start": 40, "char_end": 50},
    ]
    hosted = _host_counterfactual_span(
        [(0, 10), (10, 20), (20, 40), (40, 50)],
        evidence,
        "transport_current",
    )
    assert hosted == [(0, 20), (20, 40), (40, 50)]
    already_hosted = _host_counterfactual_span(
        hosted, evidence, "transport_current"
    )
    assert already_hosted == hosted


def test_http3_cf_target_refuses_quote_only_merge_of_two_quotes() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import (
        _host_counterfactual_span,
    )

    evidence = [
        {"evidence_id": "transport_current", "char_start": 0, "char_end": 10},
        {"evidence_id": "tls_current", "char_start": 10, "char_end": 20},
    ]
    with pytest.raises(ValueError, match="no authentic host remnant"):
        _host_counterfactual_span(
            [(0, 10), (10, 20)],
            evidence,
            "transport_current",
        )


def test_http3_coarsens_transport_with_tls_companion_on_same_record() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import (
        _artifacts_for_bucket,
    )

    units = [
        "HTTP/3 relies on QUIC version 1 as the underlying transport.",
        "intervening-authentic-source " + "i" * 24,
        "QUIC version 1 uses TLS version 1.3 or greater as its handshake protocol.",
        "trailing-authentic-source " + "t" * 28,
    ]
    text = "\n\n".join(units)
    evidence = []
    for evidence_id, quote in (
        ("transport_current", "HTTP/3 relies on QUIC version 1 as the underlying transport."),
        ("tls_current", "QUIC version 1 uses TLS version 1.3 or greater as its handshake protocol."),
    ):
        start = text.index(quote)
        evidence.append(
            {
                "evidence_id": evidence_id,
                "record_id": "ietf:rfc:9114",
                "char_start": start,
                "char_end": start + len(quote),
            }
        )
    task = {
        "question": "Resolve the requirements.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:9114",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc9114.txt",
                    "occurred_at": "2022-06-06T00:00:00Z",
                }
            ]
        },
        "evidence_items": evidence,
    }

    artifacts, _tokens = _artifacts_for_bucket(
        task,
        len,
        "fixture",
        1,
        100_000,
        0,
        chunked_record_ids=frozenset({"ietf:rfc:9114"}),
        chunk_max_tokens=200,
        isolate_evidence_ids=frozenset({"settings_current"}),
        counterfactual_companion_evidence_id="tls_current",
        counterfactual_evidence_id="transport_current",
    )

    containing = [
        artifact
        for artifact in artifacts
        if all(
            artifact["char_start"] <= item["char_start"]
            and item["char_end"] <= artifact["char_end"]
            for item in evidence
        )
    ]
    assert len(containing) == 1
    [artifact] = containing
    local = evidence[0]["char_start"] - artifact["char_start"]
    parent_value = text[evidence[0]["char_start"] : evidence[0]["char_end"]]
    remnant = (
        artifact["text"][:local]
        + " " * len(parent_value)
        + artifact["text"][local + len(parent_value) :]
    )
    assert remnant.strip()
    assert "TLS version 1.3" in remnant


def test_http3_packing_skips_blank_leftover_units() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import (
        _artifacts_for_bucket,
        _source_units,
        _visible_source_span,
    )

    quote = "HTTP/3 relies on QUIC version 1 as the underlying transport."
    text = quote + "\n\n\n\nvisible leftover paragraph for packing.\n"
    blank_units = [
        (start, end)
        for start, end in _source_units(text)
        if not _visible_source_span(text, start, end)
    ]
    assert blank_units
    task = {
        "question": "Resolve the requirements.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:9114",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc9114.txt",
                    "occurred_at": "2022-06-06T00:00:00Z",
                }
            ]
        },
        "evidence_items": [
            {
                "evidence_id": "transport_current",
                "record_id": "ietf:rfc:9114",
                "char_start": 0,
                "char_end": len(quote),
            }
        ],
    }
    artifacts, _tokens = _artifacts_for_bucket(
        task,
        len,
        "16k",
        1,
        100_000,
        0,
        chunked_record_ids=frozenset({"ietf:rfc:9114"}),
        chunk_max_tokens=200,
        isolate_evidence_ids=frozenset(),
        support_priority_record_ids=("ietf:rfc:9114",),
    )
    assert artifacts
    assert all(item["text"].strip() for item in artifacts)
    packed_spans = {(item["char_start"], item["char_end"]) for item in artifacts}
    assert packed_spans.isdisjoint(blank_units)

