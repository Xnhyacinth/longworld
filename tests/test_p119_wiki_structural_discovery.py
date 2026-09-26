"""P119 category continuation and frozen-discovery integrity checks."""

import pytest

from scripts.p119_wiki_structural_discovery import (
    _parse_raw,
    category_page,
    validate,
)


class FakeFetcher:
    def __init__(self):
        self.requests = []

    def get_json(self, params):
        self.requests.append(params)
        start = 0 if "cmcontinue" not in params else 50
        count = min(50, 63 - start)
        value = {
            "query": {
                "categorymembers": [
                    {"title": f"List of institution {index}"}
                    for index in range(start, start + count)
                ]
            }
        }
        if start == 0:
            value["continue"] = {"cmcontinue": "next"}
        return value


def test_category_continuation_replays_exact_raw_response() -> None:
    fetcher = FakeFetcher()
    titles, raw = category_page(fetcher, "Category:Lists of institutions", "page", 60)
    assert len(titles) == 60
    assert len(raw) == len(fetcher.requests) == 2
    assert fetcher.requests[1]["cmcontinue"] == "next"
    assert _parse_raw(raw, "Category:Lists of institutions", "page", 60) == titles
    raw[1]["request"]["cmtitle"] = "Category:Other"
    with pytest.raises(ValueError, match="request changed"):
        _parse_raw(raw, "Category:Lists of institutions", "page", 60)


def test_config_rejects_repeated_root_and_unbounded_workers() -> None:
    config = {
        "schema": "longworld.p119-wiki-structural-discovery.v1.config",
        "roots": [
            {
                "category": "Category:Lists of hospitals",
                "domain": "healthcare",
                "split": "train",
            }
        ],
        "workers": 5,
        "subcategories_per_root": 1,
        "pages_per_category": 1,
        "max_pages_per_root": 1,
        "max_options_per_table": 1,
        "prior_pools": [{"path": "missing", "sha256": "bad"}],
    }
    with pytest.raises(ValueError, match="workers"):
        validate(config)
    config["workers"] = 1
    config["roots"].append(config["roots"][0])
    with pytest.raises(ValueError, match="repeats"):
        validate(config)


def test_live_intake_replays_when_frozen() -> None:
    import json
    from collections import Counter

    from scripts.p119_wiki_structural_discovery import ROOT, run

    output = ROOT / "data/candidates/p119_wiki_structural_intake_v2"
    if not (output / "manifest.json").exists():
        pytest.skip("P119 source campaign not yet frozen")
    result = run(
        ROOT / "configs/p119_wiki_structural_discovery_v1.json",
        output,
        verify_only=True,
    )
    assert result["gross_frozen_pages"] == result["new_frozen_groups"]
    assert result["selected_novel_titles"] >= result["new_frozen_groups"]
    freeze = [
        json.loads(line)
        for line in (output / "freeze_ledger.jsonl").read_text().splitlines()
    ]
    assert len(freeze) == result["selected_novel_titles"] == 160
    assert Counter(row["status"] for row in freeze) == {
        "frozen": 98,
        "freeze_failed": 62,
    }
    assert all(
        "HTTP Error 429" in row["reason"]
        for row in freeze
        if row["status"] == "freeze_failed"
    )
