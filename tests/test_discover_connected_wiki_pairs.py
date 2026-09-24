"""Connected Wiki intake routes sources without weakening split or join gates."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import discover_connected_wiki_pairs as intake

ROOT = Path(__file__).resolve().parents[1]


def test_preview_needs_a_shared_row_with_a_typed_target_and_alternatives():
    text = "Name | Established\nAlpha Observatory | 1984\nBeta Observatory | 1991\n"
    assert intake._preview_score(text, {"alpha observatory"}) == (1, 2)
    assert intake._preview_score(text, {"gamma observatory"}) == (0, 2)
    assert intake._preview_score(
        "Name | Notes\nAlpha Observatory | 1984\n", {"alpha observatory"}
    ) == (0, 0)


def test_prior_manifest_title_split_conflict_is_recorded_for_exclusion():
    pool = json.loads(
        (
            ROOT
            / "data/capability_records/p78_wiki_topic_discovery_broad_v1/source_pool.json"
        ).read_text()
    )
    titles = intake._existing_titles(pool)
    assert titles["list of volcanoes in iceland"] == {"train", "eval"}
    assert titles["list of astronomical observatories"] == {"train"}


def test_spread_handles_one_query_and_reaches_endpoints():
    names = [f"Name {index}" for index in range(20)]
    assert intake._spread(names, 1) == ["Name 10"]
    assert intake._spread(names, 3) == ["Name 0", "Name 9", "Name 19"]
