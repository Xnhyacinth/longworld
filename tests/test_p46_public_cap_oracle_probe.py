"""Behavioral checks for the source-bound public-cap rejection probe."""

import copy
import gzip
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from reports.p46_public_cap_oracle_probe import (
    cap_oracle,
    ntsb_projection,
    replay_timeline,
    span,
    verify_span,
)


def summary_text():
    raw = gzip.decompress(
        Path("sources/p46_public_cap_probe_20260906/july_summary.pdf.gz").read_bytes()
    )
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)


def test_real_public_tables_use_consistent_consumption_basis():
    text = summary_text()
    old, revised = cap_oracle(text, False), cap_oracle(text)
    assert [r["delta_gbp"] for r in old["rows"]] == ["221", "233", "215", "53"]
    assert [r["delta_gbp"] for r in revised["rows"]] == ["186", "197", "181", "46"]
    for row in revised["rows"]:
        assert "£" in verify_span(text, row["source_span"])


def test_missing_operand_cannot_produce_answer():
    text = summary_text().replace("£1,477", "MISSING")
    with pytest.raises(ValueError, match="four public cap rows"):
        cap_oracle(text)


def test_source_and_offset_tampering_rejected():
    bound = span("abc source xyz", "source")
    with pytest.raises(ValueError, match="source text changed"):
        verify_span("abd source xyz", bound)
    altered = bound | {"start": bound["start"] + 1}
    with pytest.raises(ValueError, match="source span changed"):
        verify_span("abc source xyz", altered)


def test_recipient_status_does_not_override_ntsb():
    record = {
        "Addressees": [
            {
                "Correspondence": [
                    {
                        "IsFromNtsb": True,
                        "CorrespondenceDate": "2020-01-01T00:00:00Z",
                        "ResponseSummary": "OPEN--ACCEPTABLE RESPONSE",
                    },
                    {
                        "IsFromNtsb": False,
                        "CorrespondenceDate": "2021-01-01T00:00:00Z",
                        "ResponseSummary": "CLOSED--ACCEPTABLE ACTION",
                    },
                ]
            }
        ]
    }
    events = ntsb_projection(record)
    assert len(events) == 1
    assert replay_timeline(events)[0]["current"] == "OPEN ACCEPTABLE RESPONSE"


def test_real_timeline_remove_one_changes_history_but_not_final_state():
    report = json.loads(Path("reports/p46_public_cap_oracle_probe_v1.json").read_text())
    events = copy.deepcopy(report["ntsb_fallback"][0]["classification_spans"])
    full = replay_timeline(events)
    reduced = replay_timeline(events[1:])
    assert full != reduced
    assert full[-1]["current"] == reduced[-1]["current"]
    assert full[-1]["current"] == replay_timeline(events[-1:])[-1]["current"]
