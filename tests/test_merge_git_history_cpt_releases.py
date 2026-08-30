from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from merge_git_history_cpt_releases import (
    _select_disjoint_rows,
    _validate_release_row_bindings,
)


def _row(bucket: str, record_id: str) -> dict:
    return {
        "length_bucket": bucket,
        "tokenizer_context_tokens": 64_100 if bucket == "64k" else 128_100,
        "base_workflow_id": f"workflow:{record_id}",
        "source_export_digest": f"source:{record_id}",
        "workflow_records": [{"record_id": record_id}],
    }


def test_merge_selects_only_missing_band_quota_without_event_reuse() -> None:
    rows = [_row("64k", "r1"), _row("64k", "r2"), _row("128k", "r3")]
    selected, stats = _select_disjoint_rows(
        rows,
        target={"64k": 1, "128k": 1},
        source_event_by_record={"r1": "e1", "r2": "e2", "r3": "e3"},
        source_text_by_record={"r1": "t1", "r2": "t2", "r3": "t3"},
    )

    assert [row["workflow_records"][0]["record_id"] for row in selected] == [
        "r1",
        "r3",
    ]
    assert stats["retained_rows"] == {"64k": 1, "128k": 1}
    assert stats["retained_context_tokens"] == {"64k": 64_100, "128k": 128_100}
    assert stats["unique_source_events"] == 2


def test_merge_selects_registered_multiband_target() -> None:
    rows = [_row("16k", "r1"), _row("256k", "r2")]
    rows[0]["tokenizer_context_tokens"] = 16_100
    rows[1]["tokenizer_context_tokens"] = 256_100

    selected, stats = _select_disjoint_rows(
        rows,
        target={"16k": 1, "256k": 1},
        source_event_by_record={"r1": "e1", "r2": "e2"},
        source_text_by_record={"r1": "t1", "r2": "t2"},
    )

    assert len(selected) == 2
    assert stats["retained_rows"] == {"16k": 1, "256k": 1}


def test_merge_rejects_cross_shard_source_event_reuse() -> None:
    with pytest.raises(ValueError, match="source event is reused"):
        _select_disjoint_rows(
            [_row("64k", "r1"), _row("128k", "r2")],
            target={"64k": 1, "128k": 1},
            source_event_by_record={"r1": "same", "r2": "same"},
            source_text_by_record={"r1": "t1", "r2": "t2"},
        )


def test_merge_rejects_unknown_source_records() -> None:
    with pytest.raises(ValueError, match="unknown source record"):
        _select_disjoint_rows(
            [_row("64k", "r1")],
            target={"64k": 1, "128k": 1},
            source_event_by_record={},
            source_text_by_record={},
        )


def test_merge_skips_cross_shard_source_body_duplicates_and_refills() -> None:
    selected, stats = _select_disjoint_rows(
        [_row("64k", "r1"), _row("128k", "r2"), _row("128k", "r3")],
        target={"64k": 1, "128k": 1},
        source_event_by_record={"r1": "e1", "r2": "e2", "r3": "e3"},
        source_text_by_record={"r1": "same", "r2": "same", "r3": "different"},
    )

    assert [row["workflow_records"][0]["record_id"] for row in selected] == [
        "r1",
        "r3",
    ]
    assert stats["duplicate_source_body_rows_skipped"] == 1


def test_release_rows_must_match_signed_source_bindings() -> None:
    row = _row("64k", "r1")
    row["workflow_records"][0]["sha256"] = "signed-body"

    _validate_release_row_bindings(
        [row],
        source_event_by_record={"r1": "e1"},
        source_binding_by_record={"r1": ("signed-body", "source:r1")},
    )

    row["workflow_records"][0]["sha256"] = "forged-body"
    with pytest.raises(ValueError, match="source binding"):
        _validate_release_row_bindings(
            [row],
            source_event_by_record={"r1": "e1"},
            source_binding_by_record={"r1": ("signed-body", "source:r1")},
        )
