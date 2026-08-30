from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from filter_git_history_cpt_release import (
    _select_disjoint_from_reference,
    _validate_selected_identities,
)


def _row(record_id: str, body_sha256: str, text: str) -> dict:
    return {
        "document_context": text,
        "workflow_records": [{"record_id": record_id, "sha256": body_sha256}],
    }


def test_reference_filter_removes_body_event_and_context_overlap() -> None:
    rows = [
        _row("body", "same-body", "body row"),
        _row("event", "new-body-1", "event row"),
        _row("context", "new-body-2", "same context"),
        _row("kept", "new-body-3", "kept context"),
    ]

    selected, rejects = _select_disjoint_from_reference(
        rows,
        source_event_by_record={
            "body": "new-event-1",
            "event": "same-event",
            "context": "new-event-2",
            "kept": "new-event-3",
        },
        reference_source_bodies={"same-body"},
        reference_source_events={"same-event"},
        reference_contexts={hashlib.sha256(b"same context").hexdigest()},
    )

    assert selected == [rows[-1]]
    assert rejects == {
        "reference_source_body_overlap": 1,
        "reference_source_event_overlap": 1,
        "reference_context_overlap": 1,
    }


def test_selected_identity_validation_allows_chunks_of_one_event_per_row() -> None:
    rows = [
        {
            "document_context": "first",
            "workflow_records": [
                {"record_id": "r1", "sha256": "b1"},
                {"record_id": "r2", "sha256": "b2"},
            ],
        },
        {
            "document_context": "second",
            "workflow_records": [{"record_id": "r3", "sha256": "b3"}],
        },
    ]

    records, events = _validate_selected_identities(
        rows, {"r1": "e1", "r2": "e1", "r3": "e2"}
    )

    assert records == {"r1", "r2", "r3"}
    assert events == {"e1", "e2"}

    with pytest.raises(ValueError, match="reused"):
        _validate_selected_identities(rows, {"r1": "e1", "r2": "e1", "r3": "e1"})
