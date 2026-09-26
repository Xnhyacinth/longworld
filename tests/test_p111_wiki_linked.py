"""Behavioral checks for visible Wiki link binding and frozen P111 replay."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.p111_wiki_linked_batch import _solve
from scripts.p111_wiki_linked_freeze import plan
from scripts.p111_wiki_linked_freeze import run as replay_freeze
from scripts.p111_wiki_linked_targets import _url_key

ROOT = Path(__file__).resolve().parents[1]


def test_final_reader_needs_visible_list_link_and_target_field() -> None:
    context = (
        "=== DOCUMENT: List of stations (revision 123) ===\n"
        "# List of stations\nName | Code\n"
        "[Alpha](<https://en.wikipedia.org/wiki/Alpha_station>) | A1\n"
        "[Beta](<https://en.wikipedia.org/wiki/Beta_station>) | B2"
        "\n\n=== DOCUMENT: Alpha station (revision 120) ===\n"
        "# Alpha station\nplatforms: two"
        "\n\n=== DOCUMENT: Beta station (revision 119) ===\n"
        "# Beta station\nplatforms: three"
    )
    assert _solve(context, "Code", "A1", "platforms") == "two"
    assert _solve(context, "Code", "B2", "platforms") == "three"
    assert _solve(context.replace(" | A1", " | C1"), "Code", "A1", "platforms") is None
    assert (
        _solve(context.replace("platforms: two", ""), "Code", "A1", "platforms") is None
    )
    assert (
        _solve(
            context.replace(
                "[Alpha](<https://en.wikipedia.org/wiki/Alpha_station>)", "Alpha"
            ),
            "Code",
            "A1",
            "platforms",
        )
        is None
    )
    assert (
        _solve(
            context
            + "\n\n=== DOCUMENT: Related list (revision 110) ===\n# Related list",
            "Code",
            "A1",
            "platforms",
        )
        == "two"
    )


def test_target_url_gate_normalizes_percent_encoding_and_underscores() -> None:
    assert _url_key("https://en.wikipedia.org/wiki/Alpha%5Fstation") == _url_key(
        "https://en.wikipedia.org/wiki/Alpha_station"
    )


def test_pinned_full_source_plan_and_completed_replay() -> None:
    config_path = ROOT / "configs/p111_wiki_linked_freeze_v1.json"
    jobs, prior = plan(json.loads(config_path.read_text()))
    assert len(jobs) == 171
    assert len(prior) == 14
    manifest = replay_freeze(
        config_path, ROOT / "data/candidates/p111_wiki_linked_raw_v1", verify_only=True
    )
    assert manifest["frozen_pages"] == 171
    assert manifest["new_exact_revision_pages"] == 157
