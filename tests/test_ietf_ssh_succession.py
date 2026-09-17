from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.standardsworkflow import (
    IETF_FETCH_INVENTORY_SCHEMA,
    audit_ietf_ssh_architecture_succession_task,
    build_ietf_ssh_architecture_succession_task,
    build_ietf_workflow_from_fetch_inventory,
    materialize_ietf_ssh_architecture_counterfactual,
    replay_ietf_ssh_architecture_succession_task,
)
from tests.test_standardsworkflow import _write

ROOT = Path(__file__).resolve().parents[1]

_OFFICIAL_SIGNED = Path(
    f"{ROOT}/data/source_inventory/"
    "p57_ietf_ssh_family_v1/ietf_workflow_manifest.p57.ssh.v1.signed.json"
)
_LEFTOVER_RFCS = (4252, 4253, 4254)


def _ssh_inventory(tmp_path: Path) -> dict[str, object]:
    request = {
        "schema_version": "longworld.ietf-fetch-request.v1",
        "user_agent": "LongWorld/0.2 standards@example.org",
        "authorization": {
            "record_id": "ietf-ssh-test-1",
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
        "drafts": [{"name": "draft-ietf-secsh-architecture", "revisions": ["22"]}],
        "rfc_numbers": [4251, *_LEFTOVER_RFCS],
        "rfc_datatracker_sources": [4251],
        "approved_public_test_vector_sha256": [],
        "requests_per_second": 1.0,
        "max_retries": 2,
    }
    request_path = tmp_path / "ietf_fetch_request.json"
    request_raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    request_path.write_bytes(request_raw)
    leftover_targets = ", ".join(f"rfc{number}" for number in _LEFTOVER_RFCS)
    bodies = {
        "datatracker-draft-ietf-secsh-architecture.json": json.dumps(
            {
                "name": "draft-ietf-secsh-architecture",
                "rev": "22",
                "time": "2006-01-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-draft-ietf-secsh-architecture-relations.json": json.dumps(
            {
                "meta": {"total_count": 1},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/became_rfc/",
                        "source": "/api/v1/doc/document/draft-ietf-secsh-architecture/",
                        "target": "/api/v1/doc/document/rfc4251/",
                    }
                ],
            },
            sort_keys=True,
        ).encode(),
        "draft-ietf-secsh-architecture-22.txt": (
            b"Internet-Draft draft-ietf-secsh-architecture-22\n1 January 2006\n"
            b"SSH architecture draft.\n"
        ),
        "rfc4251.txt": (
            b"Request for Comments: 4251\n"
            b"January 2006\n"
            b"The Secure Shell (SSH) Protocol Architecture\n"
            b"This\n"
            b"document describes the architecture of the SSH protocol, as well as\n"
            b"the notation and terminology used in SSH protocol documents.\n"
            b"The\n"
            b"Transport Layer Protocol provides server authentication,\n"
            b"confidentiality, and integrity with perfect forward secrecy.\n"
            b"The\n"
            b"User Authentication Protocol authenticates the client to the server.\n"
            b"The Connection Protocol multiplexes the encrypted tunnel into several\n"
            b"logical channels.\n"
            b"End point security is assumed by the connection protocol.\n"
            b"Each server host SHOULD have a host key.\n"
        ),
        "datatracker-rfc4251.json": json.dumps(
            {
                "name": "rfc4251",
                "rfc": "4251",
                "rev": "",
                "time": "2006-01-01T00:00:00Z",
            },
            sort_keys=True,
        ).encode(),
        "datatracker-rfc4251-relations.json": json.dumps(
            {
                "meta": {"total_count": len(_LEFTOVER_RFCS)},
                "objects": [
                    {
                        "relationship": "/api/v1/name/docrelationshipname/refinfo/",
                        "source": "/api/v1/doc/document/rfc4251/",
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
            {
                4252: (
                    f"Request for Comments: {number}\nJanuary 2006\n"
                    "This document describes the SSH authentication protocol "
                    "framework and public key, password, and host-based client "
                    "authentication methods.\n"
                ),
                4254: (
                    f"Request for Comments: {number}\nJanuary 2006\n"
                    "This document describes the SSH Connection Protocol. It "
                    "provides interactive login sessions, remote execution of "
                    "commands, forwarded TCP/IP connections, and forwarded X11 "
                    "connections.\n"
                ),
            }.get(
                number,
                (
                    f"Request for Comments: {number}\nJanuary 2006\n"
                    f"SSH leftover RFC {number} whole span.\n"
                ),
            )
        ).encode()
    specifications = [
        (
            "datatracker_document",
            "datatracker-draft-ietf-secsh-architecture.json",
            "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-secsh-architecture/",
        ),
        (
            "datatracker_relation",
            "datatracker-draft-ietf-secsh-architecture-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-secsh-architecture&limit=100",
        ),
        (
            "draft_revision",
            "draft-ietf-secsh-architecture-22.txt",
            "https://www.ietf.org/archive/id/draft-ietf-secsh-architecture-22.txt",
        ),
        ("rfc", "rfc4251.txt", "https://www.rfc-editor.org/rfc/rfc4251.txt"),
        *(
            ("rfc", f"rfc{number}.txt", f"https://www.rfc-editor.org/rfc/rfc{number}.txt")
            for number in _LEFTOVER_RFCS
        ),
        (
            "datatracker_document",
            "datatracker-rfc4251.json",
            "https://datatracker.ietf.org/api/v1/doc/document/rfc4251/",
        ),
        (
            "datatracker_relation",
            "datatracker-rfc4251-relations.json",
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=rfc4251&limit=100",
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


def test_ssh_architecture_succession_task_replays_and_fails_closed_on_remove_one(
    tmp_path: Path,
) -> None:
    inventory = _ssh_inventory(tmp_path)
    manifest = build_ietf_workflow_from_fetch_inventory(
        inventory,
        tmp_path,
        generated_at="2026-08-29T02:00:00Z",
        fetch_inventory_sha256="a" * 64,
    )
    task = build_ietf_ssh_architecture_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.ssh_architecture_succession.v1"
    assert task["query_type"] == "protocol_succession_resolution"
    assert task["answer"] == {
        "current_protocol": "SSH_ARCHITECTURE_RFC4251",
        "transport_layer": "SSH_TRANS",
        "user_authentication": "SSH_USERAUTH",
        "connection": "SSH_CONNECT",
        "host_key": "SSH_HOST_KEY",
    }
    records = {item["record_id"] for item in task["evidence_items"]}
    assert records == {"ietf:rfc:4251", "ietf:rfc:4252", "ietf:rfc:4254"}
    by_id = {item["evidence_id"]: item["record_id"] for item in task["evidence_items"]}
    assert by_id["current_protocol"] == "ietf:rfc:4251"
    assert by_id["user_authentication"] == "ietf:rfc:4252"
    assert by_id["connection"] == "ietf:rfc:4254"
    assert replay_ietf_ssh_architecture_succession_task(task) == task["answer"]
    removed = replay_ietf_ssh_architecture_succession_task(
        task,
        evidence_ids=[
            item for item in task["essential_evidence_ids"] if item != "connection"
        ],
    )
    assert removed["connection"] == "UNKNOWN"
    assert removed["current_protocol"] == "SSH_ARCHITECTURE_RFC4251"
    audit = audit_ietf_ssh_architecture_succession_task(task)
    assert audit["strict_replay"] is True
    assert audit["remove_one_evidence_fails"] is True
    materialized = materialize_ietf_ssh_architecture_counterfactual(task)
    assert materialized["counterfactual_twin"]["evidence_id"] == "current_protocol"
    assert materialized["answer"]["current_protocol"] == "UNKNOWN"
    assert materialized["answer"]["transport_layer"] == "SSH_TRANS"


def test_ssh_architecture_succession_task_binds_official_bytes_when_present() -> None:
    if not _OFFICIAL_SIGNED.is_file():
        return
    manifest = json.loads(_OFFICIAL_SIGNED.read_text())
    task = build_ietf_ssh_architecture_succession_task(manifest)
    assert task["answer_program_id"] == "ietf.ssh_architecture_succession.v1"
    records = {item["record_id"] for item in task["evidence_items"]}
    assert records == {"ietf:rfc:4251", "ietf:rfc:4252", "ietf:rfc:4254"}
    audit = audit_ietf_ssh_architecture_succession_task(task)
    assert audit["strict_replay"] is True
    assert audit["remove_one_evidence_fails"] is True


def test_ssh_packing_pins_last_leftover_chunk_without_isolation() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    quote = "This document describes the architecture of the SSH protocol."
    leftover_a = "authentic leftover block a " + ("a" * 40)
    leftover_b = "authentic leftover block b " + ("b" * 40)
    leftover_c = "authentic leftover block c " + ("c" * 40)
    text = "\n\n".join([quote, leftover_a, leftover_b, leftover_c])
    task = {
        "question": "Resolve SSH architecture succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:4251",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc4251.txt",
                    "occurred_at": "2006-01-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [
            {
                "evidence_id": "current_protocol",
                "record_id": "ietf:rfc:4251",
                "char_start": 0,
                "char_end": len(quote),
            }
        ],
    }
    kwargs = {
        "chunked_record_ids": frozenset({"ietf:rfc:4251"}),
        "chunk_max_tokens": 80,
        "support_priority_record_ids": ("ietf:rfc:4251",),
    }
    unpinned, _ = _artifacts_for_bucket(task, len, "32k", 1, 100_000, 0, **kwargs)
    pinned, _ = _artifacts_for_bucket(
        task,
        len,
        "32k",
        1,
        100_000,
        0,
        pin_last_leftover_record_ids=frozenset({"ietf:rfc:4251"}),
        **kwargs,
    )
    assert leftover_c not in "".join(item["text"] for item in unpinned)
    assert leftover_c in "".join(item["text"] for item in pinned)
    assert quote in "".join(item["text"] for item in pinned)
    last = max(pinned, key=lambda item: item["char_start"])
    assert last["essential"] is False
    assert leftover_c in last["text"]


def test_ssh_packing_keeps_leftover_only_record_as_one_span() -> None:
    from reports.p57_ietf_http3_quic_requirement_generate import _artifacts_for_bucket

    first = "Request for Comments: 4252 leftover start."
    middle = "Authentic SSH leftover paragraph."
    last = "Authentic SSH leftover end."
    text = "\n\n".join([first, middle, last])
    task = {
        "question": "Resolve SSH architecture succession.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:4252",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc4252.txt",
                    "occurred_at": "2006-01-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": [],
    }
    artifacts, _ = _artifacts_for_bucket(task, len, "32k", 1, 100_000, 0)
    leftover = [item for item in artifacts if item["record_id"] == "ietf:rfc:4252"]
    assert len(leftover) == 1
    assert leftover[0]["essential"] is False
    assert leftover[0]["char_start"] == 0
    assert leftover[0]["char_end"] == len(text)
    assert first in leftover[0]["text"]
    assert last in leftover[0]["text"]


def test_ssh_generate_refuses_exploded_4251_chunks(tmp_path: Path) -> None:
    from reports.p57_ietf_ssh_succession_generate import build

    config_path = tmp_path / "exploded.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.ietf-ssh-generation-config.v1",
                "length_buckets": {"32k": [32000, 32768]},
                "packing": {
                    "support_priority_record_ids": ["ietf:rfc:4251"],
                    "chunked_record_ids": [
                        "ietf:rfc:4251",
                        "ietf:rfc:4252",
                        "ietf:rfc:4254",
                    ],
                    "chunk_max_tokens": 4096,
                },
            }
        )
    )
    with pytest.raises(ValueError, match="must not explode RFC 4251/4252"):
        build(config_path)


def test_ssh_generate_refuses_isolate_evidence_and_64k(tmp_path: Path) -> None:
    from reports.p57_ietf_ssh_succession_generate import build

    config_path = tmp_path / "bad.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.ietf-ssh-generation-config.v1",
                "length_buckets": {"64k": [64000, 65536]},
                "packing": {"isolate_evidence_ids": ["current_protocol"]},
            }
        )
    )
    with pytest.raises(ValueError, match="must not isolate"):
        build(config_path)


def test_ssh_generate_requires_rfc4251_leftover_fill_first(tmp_path: Path) -> None:
    from reports.p57_ietf_ssh_succession_generate import build

    config_path = tmp_path / "bad-priority.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.ietf-ssh-generation-config.v1",
                "length_buckets": {"32k": [32000, 32768]},
                "packing": {
                    "support_priority_record_ids": [
                        "ietf:rfc:4252",
                        "ietf:rfc:4251",
                    ]
                },
            }
        )
    )
    with pytest.raises(ValueError, match="RFC 4251 leftover"):
        build(config_path)


def test_ssh_packing_requires_successor_rfc_gold() -> None:
    from reports.p57_ietf_ssh_succession_generate import _require_ssh_cross_rfc_gold

    only_4251 = [
        {
            "artifact_id": "gold-early",
            "record_id": "ietf:rfc:4251",
            "essential": True,
        },
        {
            "artifact_id": "leftover-4252",
            "record_id": "ietf:rfc:4252",
            "essential": False,
        },
    ]
    with pytest.raises(ValueError, match="RFC 4252/4254 gold"):
        _require_ssh_cross_rfc_gold(only_4251)
    crossed = [
        *only_4251,
        {
            "artifact_id": "gold-auth",
            "record_id": "ietf:rfc:4252",
            "essential": True,
        },
    ]
    assert _require_ssh_cross_rfc_gold(crossed) is crossed


def _ssh_artifact(
    record_id: str,
    text: str,
    *,
    essential: bool,
    char_start: int = 0,
    occurred_at: str = "2006-01-01T00:00:00Z",
) -> dict:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {
        "artifact_id": (
            f"{record_id}:chars:{char_start:07d}-{char_start + len(text):07d}:{digest[:12]}"
        ),
        "record_id": record_id,
        "char_start": char_start,
        "char_end": char_start + len(text),
        "text": text,
        "text_sha256": digest,
        "essential": essential,
        "occurred_at": occurred_at,
    }


def test_ssh_explode_zipper_tail_splits_single_leftover() -> None:
    from reports.p57_ietf_ssh_succession_generate import (
        _dossier_spread,
        _explode_ssh_zipper_tail,
        _require_ssh_zipper_clears_16k,
        _ssh_chronology,
    )

    prefix = [
        _ssh_artifact(
            "ietf:draft:draft-ietf-secsh-architecture-22",
            "SSH draft identity paragraph.\n\n",
            essential=False,
            occurred_at="2005-11-01T00:00:00Z",
        ),
        _ssh_artifact("ietf:rfc:4251", "RFC 4251 header.\n\n", essential=False),
    ]
    gold_4251 = _ssh_artifact(
        "ietf:rfc:4251",
        "SSH architecture gold.\n\n",
        essential=True,
        char_start=20,
    )
    filler = [
        _ssh_artifact(
            "ietf:rfc:4251",
            f"RFC 4251 supporting block {index}.\n\n",
            essential=False,
            char_start=100 + index * 40,
        )
        for index in range(8)
    ]
    gold_4254 = _ssh_artifact(
        "ietf:rfc:4254",
        "SSH connection protocol gold.\n\n",
        essential=True,
    )
    leftover = "\n\n".join(
        f"leftover paragraph {index} " + ("x" * 48) for index in range(20)
    )
    tail = _ssh_artifact(
        "ietf:rfc:4254", leftover, essential=False, char_start=len(gold_4254["text"])
    )
    packed = _explode_ssh_zipper_tail(
        [*prefix, gold_4251, *filler, gold_4254, tail], span_id_width=7
    )
    assert 13 <= len(packed) <= 80
    golds = [item for item in packed if item["essential"]]
    assert len(golds) == 2
    tail_arts = [
        item
        for item in packed
        if item["record_id"] == "ietf:rfc:4254" and not item["essential"]
    ]
    assert len(tail_arts) >= 12
    assert "".join(item["text"] for item in tail_arts) == leftover
    spread = _dossier_spread(_ssh_chronology(packed))
    gold_positions = [
        index for index, (item, _document) in enumerate(spread) if item.get("essential")
    ]
    assert max(gold_positions) - min(gold_positions) >= 12
    with pytest.raises(ValueError, match="still retrieves gold inside 16k"):
        _require_ssh_zipper_clears_16k(
            [gold_4251, gold_4254, tail], lambda text: min(len(text), 100), "q"
        )


def test_ssh_generation_config_fills_rfc4251_leftover_first() -> None:
    config = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "configs"
            / "p57_ietf_ssh_succession_generation_v1.json"
        ).read_text()
    )
    packing = config["packing"]
    assert packing["support_priority_record_ids"][0] == "ietf:rfc:4251"
    assert packing["chunked_record_ids"] == ["ietf:rfc:4254"]
    assert packing["chunk_max_tokens"] >= 8192
    assert packing["pin_last_leftover_record_ids"] == ["ietf:rfc:4254"]
