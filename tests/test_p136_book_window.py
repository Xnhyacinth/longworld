"""The second campaign must derive IDs from the pinned P121 catalog plan."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.p124_book_primary_acquire import _inputs, acquire
from scripts.p136_book_window import freeze_window, prepare

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/p136_book_window_v1.json"
P121_CONFIG = ROOT / "configs/p121_book_primary_author_v3.json"
P121_PLAN = ROOT / "data/sources/p121_book_primary_plan_v3/plan.json"


def test_second_window_is_derived_and_tamper_rejected(tmp_path: Path) -> None:
    output = tmp_path / "window_plan.json"
    window = prepare(CONFIG, output)
    parent = json.loads(P121_PLAN.read_text())
    assert window["start_offset"] == 180
    assert window["window_size"] == 180
    assert window["candidates"] == parent["candidates"][180:360]
    assert {item["ebook_id"] for item in window["candidates"]}.isdisjoint(
        {item["ebook_id"] for item in parent["candidates"][:180]}
    )
    assert prepare(CONFIG, output, verify_only=True) == window
    window["candidates"][0] = parent["candidates"][0]
    output.write_text(json.dumps(window))
    with pytest.raises(ValueError, match="window differs"):
        _inputs(P121_CONFIG, output)


def test_first_window_receipt_replays_without_byte_change() -> None:
    attempts = ROOT / "data/sources/p124_book_attempts_v1"
    receipt = attempts / "download_manifest.json"
    before = hashlib.sha256(receipt.read_bytes()).hexdigest()
    assert (
        acquire(P121_CONFIG, P121_PLAN, attempts, verify_only=True)["attempted"] == 180
    )
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == before


def test_freeze_rejects_first_window_attempts(tmp_path: Path) -> None:
    output = tmp_path / "window_plan.json"
    prepare(CONFIG, output)
    with pytest.raises(ValueError, match="window|order|receipt"):
        freeze_window(
            CONFIG,
            output,
            ROOT / "data/sources/p124_book_attempts_v1",
            tmp_path / "source",
        )
