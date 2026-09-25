"""Behavioral and source-boundary tests for P109 numeric prose rules."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import p109_prose_support as support
from scripts.p109_prose_audit import _blind
from scripts.p109_prose_compile import SOURCE_MARKER, _answer, _records, _remove_spans


def test_same_opaque_ids_get_different_answers_from_visible_values():
    first = _records("rfc9000", "active_connection_id_limit", 2, 11, 16)
    second = _records("rfc9000", "active_connection_id_limit", 2, 29, 16)
    assert [row["id"] for row in first] == [row["id"] for row in second]
    assert _answer(first, 2) != _answer(second, 2)


def test_blind_rule_application_and_group_deletion():
    source = (
        "The padding_length MUST be at least 16.\n\n"
        "A sender MUST use padding of at least 16 bytes.\n\n"
    )
    rows = _records("rfc6520", "padding_length", 16, 11, 16)
    observations = "".join(
        f"Observation {row['id']}: padding_length={row['value']}.\n" for row in rows
    )
    context = source + SOURCE_MARKER + observations
    limit, visible, answer = _blind(context, "padding_length")
    assert limit == 16
    assert answer == _answer(rows, 16)
    assert [row["id"] for row in visible] == [row["id"] for row in rows]
    paragraphs = [
        [0, source.index("\n\n")],
        [source.index("\n\n") + 2, len(source) - 2],
    ]
    without = _remove_spans(source, paragraphs) + SOURCE_MARKER + observations
    assert _blind(without, "padding_length") is None


def test_support_probe_rejects_qualified_anaphoric_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(support, "ROOT", tmp_path)
    text = (
        "When an Initial packet is sent, the client chooses an ID. "
        "This Destination Connection ID MUST be at least 8 bytes.\n\n"
        "The active_connection_id_limit parameter MUST be at least 2.\n\n"
    )
    source = tmp_path / "rfc9000.txt"
    source.write_text(text)
    rows = support._scan_source(
        {
            "rfc_id": "rfc9000",
            "source_path": source.name,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    assert [(row["subject"], row["status"]) for row in rows] == [
        ("This Destination Connection ID", "field_or_scope_not_typed"),
        ("The active_connection_id_limit parameter", "supported_numeric_minimum"),
    ]


def test_official_inventory_must_match_frozen_rfc_url_and_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(support, "ROOT", tmp_path)
    source = tmp_path / "rfc9000.txt"
    source.write_text("Official RFC text\n")
    inventory = tmp_path / "ietf_fetch_inventory.json"
    record = {
        "retrieval_file": "rfc9000.txt",
        "kind": "rfc",
        "status": 200,
        "final_url": "https://www.rfc-editor.org/rfc/rfc9000.txt",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    inventory.write_text(json.dumps({"fetch_receipt": {"retrievals": [record]}}))
    pin = {
        "path": inventory.name,
        "sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
    }
    assert support._catalog([pin])["rfc9000"]["source_sha256"] == record["sha256"]
    record["final_url"] = "https://example.org/rfc9000.txt"
    inventory.write_text(json.dumps({"fetch_receipt": {"retrievals": [record]}}))
    pin["sha256"] = hashlib.sha256(inventory.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="official RFC source differs"):
        support._catalog([pin])
