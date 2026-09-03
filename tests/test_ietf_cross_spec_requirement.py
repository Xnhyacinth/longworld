from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError
from longworld.core.sourceworkflow import STANDARDS_SOURCE_KIND, adapt_source_manifest
from longworld.core.standardsworkflow import (
    audit_ietf_cross_spec_requirement_task,
    build_ietf_cross_spec_requirement_task,
    build_ietf_workflow_from_fetch_inventory,
    replay_ietf_cross_spec_requirement_task,
)
from tests.test_standardsworkflow import _inventory


def _oauth_manifest(tmp_path: Path) -> dict[str, object]:
    inventory, _path = _inventory(tmp_path)
    request_path = tmp_path / str(inventory["request_file"])
    request = json.loads(request_path.read_text())
    numbers = [6749, 6750, 6819, 7636, 8414, 9207, 9700]
    request["rfc_numbers"] = numbers
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    request_sha256 = hashlib.sha256(request_raw).hexdigest()
    inventory["request_sha256"] = request_sha256
    receipt = inventory["fetch_receipt"]
    receipt["request_sha256"] = request_sha256

    relations = {
        "meta": {"total_count": 7},
        "objects": [
            {
                "relationship": f"/api/v1/name/docrelationshipname/{kind}/",
                "source": "/api/v1/doc/document/draft-ietf-demo/",
                "target": f"/api/v1/doc/document/rfc{number}/",
            }
            for kind, number in [
                ("refnorm", 6749),
                ("refnorm", 6750),
                ("refnorm", 6819),
                ("refinfo", 7636),
                ("refnorm", 8414),
                ("refinfo", 9207),
                ("became_rfc", 9700),
            ]
        ],
    }
    relation_raw = json.dumps(relations, sort_keys=True).encode()
    relation_retrieval = next(
        item for item in receipt["retrievals"] if item["kind"] == "datatracker_relation"
    )
    (tmp_path / str(relation_retrieval["retrieval_file"])).write_bytes(relation_raw)
    relation_retrieval["sha256"] = hashlib.sha256(relation_raw).hexdigest()

    bodies = {
        6749: (
            "RFC 6749\nOctober 2012\n"
            "When a redirection URI is included in an authorization request, the\n"
            "   authorization server MUST compare and match the value received\n"
            "   against at least one of the registered redirection URIs (or URI\n"
            "   components) as defined in [RFC3986] Section 6, if any redirection\n"
            "   URIs were registered. If the client registration included the full\n"
            "   redirection URI, the authorization server MUST compare the two URIs\n"
            "   using simple string comparison as defined in [RFC3986] Section 6.2.1.\n"
        ),
        6750: (
            "Request for Comments: 6750 Example Publisher\nOctober 2012\n"
            "Because of the security weaknesses associated with the URI method,\n"
            "   including the high likelihood that the URL containing the access token\n"
            "   will be logged, it SHOULD NOT be used unless it is impossible to\n"
            "   transport the access token in the Authorization request header field or\n"
            "   the HTTP request entity-body. Resource servers MAY support this method.\n"
            "\f\nReferences\n      RFC 6750\n      RFC 6750\n"
        ),
        6819: (
            "RFC 6819\nJanuary 2013\n"
            "Refresh token rotation is intended to automatically detect and\n"
            "   prevent attempts to use the same refresh token in parallel from\n"
            "   different apps. If a compromise is detected, both revoked.\n"
        ),
        7636: (
            "RFC 7636\nSeptember 2015\n"
            'If the client is capable of using "S256", it MUST use "S256", as\n'
            '   "S256" is Mandatory To Implement (MTI) on the server.\n'
        ),
        8414: (
            "RFC 8414\nJune 2018\n"
            "the client MUST ensure that the\n"
            "   issuer identifier URL it is using as the prefix for the metadata\n"
            '   request exactly matches the value of the "issuer" metadata value in\n'
            "   the authorization server metadata document received by the client.\n"
        ),
        9207: (
            "RFC 9207\nMarch 2022\n"
            "If the value does not match the expected\n"
            "   issuer identifier, clients MUST reject the authorization response and\n"
            "   MUST NOT proceed with the authorization grant.\n"
        ),
        9700: (
            "RFC 9700\nUpdates: 6749, 6750, 6819\nJanuary 2025\n"
            "When comparing client redirection URIs against pre-registered URIs,\n"
            "   authorization servers MUST utilize exact string matching except for\n"
            "   port numbers in localhost redirection URIs of native apps (see\n"
            "   Section 4.1.3).\n"
            "Clients MUST NOT pass access tokens in a URI query parameter in\n"
            "   the way described in Section 2.3 of [RFC6750].\n"
            "Refresh tokens for public clients MUST be sender-constrained or use\n"
            "   refresh token rotation as described in Section 4.14.\n"
            "Public clients MUST use PKCE [RFC7636] to this end, as motivated\n"
            "   in Section 4.5.3.1.\n"
            "It is therefore RECOMMENDED that authorization servers publish OAuth\n"
            "   Authorization Server Metadata according to [RFC8414] and that clients\n"
            "   make use of this Authorization Server Metadata (when available) to\n"
            "   configure themselves.\n"
            "When an OAuth client can interact with more than one authorization\n"
            "   server, a defense against mix-up attacks (see Section 4.4) is\n"
            "   REQUIRED.\n"
        ),
    }
    retained = [item for item in receipt["retrievals"] if item["kind"] != "rfc"]
    for number, body in bodies.items():
        raw = body.encode()
        filename = f"rfc{number}.txt"
        (tmp_path / filename).write_bytes(raw)
        retained.append(
            {
                "kind": "rfc",
                "requested_url": f"https://www.rfc-editor.org/rfc/{filename}",
                "final_url": f"https://www.rfc-editor.org/rfc/{filename}",
                "status": 200,
                "content_type": "text/plain",
                "redirect_chain": [],
                "observed_at": f"2026-08-29T01:00:{len(retained) + 1:02d}Z",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "retrieval_file": filename,
            }
        )
    receipt["retrievals"] = retained
    receipt["completed_at"] = "2026-08-29T01:00:20Z"
    inventory["n_retrievals"] = len(retained)
    return build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )


def test_public_builder_records_unrequested_header_exclusion_but_keeps_requested(
    tmp_path: Path,
) -> None:
    inventory, _path = _inventory(tmp_path)
    retrieval = next(
        item
        for item in inventory["fetch_receipt"]["retrievals"]
        if item["retrieval_file"] == "rfc9999.txt"
    )
    body = (
        b"RFC 9999\nInternet-Draft draft-ietf-demo-01\n"
        b"Updates: RFC 8888, RFC 7777, RFC 5555\nMarch 2025\n"
    )
    (tmp_path / "rfc9999.txt").write_bytes(body)
    retrieval["sha256"] = hashlib.sha256(body).hexdigest()

    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )

    record = next(
        item for item in manifest["records"] if item["record_id"] == "ietf:rfc:9999"
    )
    assert record["excluded_rfc_relation_targets"] == [
        {"kind": "updates", "rfc_number": 5555, "reason": "not_requested"}
    ]
    assert any(
        relation["kind"] == "updates"
        and relation["source_record_id"] == "ietf:rfc:9999"
        and relation["target_record_id"] == "ietf:rfc:8888"
        for relation in manifest["relations"]
    )


def test_cross_spec_requirement_task_replays_six_fields_and_remove_one(
    tmp_path: Path,
) -> None:
    task = build_ietf_cross_spec_requirement_task(_oauth_manifest(tmp_path))

    assert task["query_type"] == "cross_spec_requirement_resolution"
    assert task["answer_program_id"] == "ietf.oauth_effective_requirement.v1"
    assert task["answer"] == {
        "redirect_match": "FAIL_EXACT_REQUIRED",
        "bearer_transport": "FAIL_URI_QUERY_PROHIBITED",
        "refresh_protection": "PASS_ROTATION",
        "pkce": "PASS_S256",
        "metadata_issuer": "PASS_EXACT_MATCH",
        "multi_as_issuer": "PASS_MATCHED",
    }
    assert replay_ietf_cross_spec_requirement_task(task) == task["answer"]
    wrong_scenario = deepcopy(task)
    wrong_scenario["scenario"]["pkce_method"] = "plain"
    with pytest.raises(ProvenanceError, match="task contract"):
        replay_ietf_cross_spec_requirement_task(wrong_scenario)
    audit = audit_ietf_cross_spec_requirement_task(task)
    assert audit == {
        "strict_replay": True,
        "remove_one_evidence_fails": True,
        "remove_one_relation_fails": True,
    }


@pytest.mark.parametrize("tamper", ["remove", "mismatch"])
def test_cross_spec_task_requires_exact_datatracker_dependency_fact(
    tmp_path: Path, tamper: str
) -> None:
    manifest = _oauth_manifest(tmp_path)
    relation = next(
        item
        for item in manifest["relations"]
        if item["kind"] == "normative_reference"
        and item["source_record_id"] == "ietf:rfc:9700"
        and item["target_record_id"] == "ietf:rfc:8414"
    )
    supporting = next(
        item
        for item in relation["evidence"]
        if item["record_id"].startswith("ietf:datatracker-relation:")
    )
    if tamper == "remove":
        relation["evidence"].remove(supporting)
    else:
        supporting["fact_ids"] = ["normative_reference:6749"]

    with pytest.raises(ProvenanceError, match="evidence binding"):
        build_ietf_cross_spec_requirement_task(manifest)


def test_standards_adapter_requires_exact_datatracker_dependency_fact(
    tmp_path: Path,
) -> None:
    manifest = _oauth_manifest(tmp_path)
    relation = next(
        item
        for item in manifest["relations"]
        if item["kind"] == "normative_reference"
        and item["source_record_id"] == "ietf:rfc:9700"
        and item["target_record_id"] == "ietf:rfc:8414"
    )
    supporting = next(
        item
        for item in relation["evidence"]
        if item["record_id"].startswith("ietf:datatracker-relation:")
    )
    supporting["fact_ids"] = ["normative_reference:6749"]

    with pytest.raises(ProvenanceError, match="evidence"):
        adapt_source_manifest(
            manifest,
            source_kind=STANDARDS_SOURCE_KIND,
            signed_bundle_authorized=True,
        )
