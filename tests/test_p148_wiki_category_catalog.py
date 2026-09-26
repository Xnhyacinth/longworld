"""Automatic official-category routing and immutable response checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p148_wiki_category_catalog as catalog


def test_schedule_uses_discovered_roots_and_balances_parent_branches() -> None:
    found = {
        "Category:Lists of hospitals": ("Category:Health-related lists", 40),
        "Category:Lists of clinics": ("Category:Health-related lists", 20),
        "Category:Lists of airports": ("Category:Geography-related lists", 30),
        "Category:Lists of bridges": ("Category:Geography-related lists", 15),
        "Category:Lists of books": ("Category:Cultural lists", 25),
        "Category:Lists of museums": ("Category:Cultural lists", 18),
        "Category:Lists of species lists": ("Category:Science-related lists", 100),
    }
    roots, exclusions = catalog.schedule(found, {"category:lists of hospitals"}, 4)
    assert len({row["category"] for row in roots}) == 4
    assert all(row["category"] in found for row in roots)
    assert all(row["domain"] == "wiki_list_index" for row in roots)
    assert {row["split"] for row in roots} == {"train", "eval"}
    assert exclusions == {"prior_root": 1, "not_direct_list_root": 1}
    assert {row["parent"] for row in roots} == {
        "Category:Health-related lists",
        "Category:Geography-related lists",
        "Category:Cultural lists",
    }


def test_category_info_rejects_mismatched_frozen_request(tmp_path: Path) -> None:
    path = tmp_path / "response.json"
    path.write_text(json.dumps({"request": {"titles": "Other"}, "response": {}}))
    with pytest.raises(ValueError, match="request changed"):
        catalog._raw_info(path, None, ["Category:Lists of bridges"])


def test_config_rejects_non_official_or_unbounded_source_route(tmp_path: Path) -> None:
    config = json.loads(Path("configs/p148_wiki_category_catalog_v1.json").read_text())
    config["scheduled_roots"] = 100
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="invalid P148"):
        catalog._config(path)


def test_prior_source_pins_and_official_api() -> None:
    config = catalog._config(Path("configs/p148_wiki_category_catalog_v1.json"))
    assert config["api"] == "https://en.wikipedia.org/w/api.php"
    assert len(config["prior_pools"]) == 4
