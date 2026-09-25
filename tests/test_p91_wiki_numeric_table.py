"""Closed numeric table admission and reader-text intervention behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.synthesis.p91_wiki_numeric_table import (
    interval_answer,
    intervention,
    parse_table,
)

ROOT = Path(__file__).resolve().parents[1]


def _finland() -> str:
    snapshot = json.loads(
        (
            ROOT
            / "data/capability_records/p89_wiki_typed_lists_v1/snapshots/wiki_skyscrapers_p89_01_snapshot.json"
        ).read_text()
    )
    return next(
        doc["text"]
        for doc in snapshot["documents"]
        if doc["title"] == "List of tallest buildings in Finland"
    )


def test_frozen_table_scans_every_visible_height_and_replays_boundary_change() -> None:
    text = _finland()
    table = parse_table(text)
    assert len(table.rows) == 36
    assert sum(row.plain_name for row in table.rows) == 35
    unsafe = [row for row in table.rows if not row.plain_name]
    assert len(unsafe) == 1
    assert unsafe[0].name.startswith("Sitadelli 2Cite web |title=")
    assert unsafe[0].meters == 60.0
    assert all(text[row.value_start : row.value_end] for row in table.rows)
    answer = interval_answer(table, 80, 100)
    assert answer["count"] == 8
    receipt = intervention(table, text, 80, 100)
    assert receipt["changed_row"] == "Clarion Hotel Helsinki"
    assert receipt["hit_answer"]["count"] == 9


def test_malformed_row_or_scope_change_rejects_whole_table() -> None:
    text = _finland()
    with pytest.raises(ValueError, match="unnormalized height row"):
        parse_table(text.replace("convert|98|m|ft|abbr=on", "unknown", 1))
    with pytest.raises(ValueError, match="closing heading"):
        parse_table(text.replace("## Under construction", "## Future list", 1))
    with pytest.raises(ValueError, match="selected table row has no clean name"):
        interval_answer(parse_table(text), 60, 63)


def test_final_candidate_receipt_if_present() -> None:
    folder = ROOT / "data/candidates/p91_wiki_numeric_table_v5"
    if not folder.exists():
        pytest.skip("P91 native candidate has not been generated")
    manifest = json.loads((folder / "manifest.json").read_text())
    page_audit = [
        json.loads(line)
        for line in (folder / "page_audit.jsonl").read_text().splitlines()
    ]
    index = [
        json.loads(line)
        for line in (folder / "sample_index.jsonl").read_text().splitlines()
    ]
    assert manifest["candidate_views"] == manifest["independent_tasks"] == len(index)
    assert manifest["mask_audited"] == len(index)
    assert manifest["page_rejections"] == 3
    assert len(page_audit) == 4
    assert all(row["candidate_rows"] == 36 for row in index)
    assert {row["source_group"] for row in index} == {"snapshot_965e480e1f65185dc900"}
    assert all(row["task_type"] == row["operation"] for row in index)
    assert {row["dependency_status"] for row in index} == {
        "bounded_numeric_hit_and_near_miss_replay"
    }
    assert all(row["evidence_token_extent"] > 0 for row in index)
    assert all(row["full_chat_tokens"] < 65536 for row in index)
    assert manifest["train_ready"] is False
