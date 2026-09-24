"""Connected Wiki intake routes sources without weakening split or join gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import discover_connected_wiki_pairs as intake

ROOT = Path(__file__).resolve().parents[1]
POOL = (
    ROOT / "data/capability_records/p78_wiki_topic_discovery_broad_v1/source_pool.json"
)


def _local_pool() -> dict:
    if not POOL.is_file():
        pytest.skip("frozen P78 source pool is not mounted")
    return json.loads(POOL.read_text())


def test_preview_needs_a_shared_row_with_a_typed_target_and_alternatives():
    text = "Name | Established\nAlpha Observatory | 1984\nBeta Observatory | 1991\n"
    assert intake._preview_score(text, {"alpha observatory"}) == (1, 2)
    assert intake._preview_score(text, {"gamma observatory"}) == (0, 2)
    assert intake._preview_score(
        "Name | Notes\nAlpha Observatory | 1984\n", {"alpha observatory"}
    ) == (0, 0)


def test_prior_manifest_title_split_conflict_is_recorded_for_exclusion():
    pool = _local_pool()
    titles = intake._existing_titles(pool)
    assert titles["list of volcanoes in iceland"] == {"train", "eval"}
    assert titles["list of astronomical observatories"] == {"train"}


def test_spread_handles_one_query_and_reaches_endpoints():
    names = [f"Name {index}" for index in range(20)]
    assert intake._spread(names, 1) == ["Name 10"]
    assert intake._spread(names, 3) == ["Name 0", "Name 9", "Name 19"]


def test_auto_sources_choose_table_rich_anchors_from_distinct_domains():
    pool = _local_pool()
    config = json.loads((ROOT / "configs/p81_connected_wiki_auto_v1.json").read_text())
    intake._validate(config, pool)
    seeds = intake._auto_seeds(config, pool)
    assert len(seeds) == 6
    source_by_name = {source["name"]: source for source in pool["sources"]}
    assert {source_by_name[seed["anchor_source"]]["domain"] for seed in seeds} == set(
        config["auto_sources"]["domains"]
    )
    assert all(seed["max_queries"] == 5 for seed in seeds)


def test_generic_preview_accepts_typed_target_beyond_established_year():
    text = "Name | Height\nAlpha Bridge | 35 m\nBeta Bridge | 42 m\n"
    assert intake._preview_score(text, {"alpha bridge"}) == (1, 2)
    assert intake._query_specs("bridges_lists", ["Alpha Bridge", "Beta Bridge"], 2) == [
        ("topic", 'intitle:"List of" bridges'),
        ("Beta Bridge", 'intitle:"List of" "Beta Bridge"'),
    ]


def test_wrong_frozen_source_pool_pin_fails_before_acquisition(tmp_path: Path):
    _local_pool()
    config = json.loads((ROOT / "configs/p81_connected_wiki_auto_v1.json").read_text())
    config["base_pool_sha256"] = "0" * 64
    path = tmp_path / "wrong-pin.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="base source pool pin mismatch"):
        intake.run(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_auto_anchor_routing_uses_row_capacity_without_local_data(
    monkeypatch: pytest.MonkeyPatch,
):
    pool = {
        "sources": [
            {"name": "nature_small", "domain": "nature", "snapshot": {"name": "small"}},
            {"name": "nature_large", "domain": "nature", "snapshot": {"name": "large"}},
            {"name": "culture", "domain": "culture", "snapshot": {"name": "culture"}},
        ]
    }
    monkeypatch.setattr(intake, "_snapshot", lambda root, pin: pin)
    monkeypatch.setattr(
        intake,
        "_anchor_rows",
        lambda snapshot: (
            {"table": ["a", "b", "c"]}
            if snapshot["name"] == "large"
            else {"table": ["a"]}
        ),
    )
    config = {
        "auto_sources": {
            "domains": ["nature", "culture"],
            "max_sources": 2,
            "max_queries": 5,
            "results_per_query": 10,
            "max_previews": 10,
            "max_freezes": 2,
        }
    }
    seeds = intake._auto_seeds(config, pool)
    assert [seed["anchor_source"] for seed in seeds] == ["nature_large", "culture"]


def test_auto_anchor_routing_round_robins_all_domains_and_multiple_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = {
        "sources": [
            {"name": "nature_small", "domain": "nature", "snapshot": {"name": "n1"}},
            {"name": "culture_small", "domain": "culture", "snapshot": {"name": "c1"}},
            {"name": "nature_large", "domain": "nature", "snapshot": {"name": "n2"}},
            {"name": "culture_large", "domain": "culture", "snapshot": {"name": "c2"}},
            {"name": "empty", "domain": "zoology", "snapshot": {"name": "z"}},
        ]
    }
    monkeypatch.setattr(intake, "_snapshot", lambda _root, pin: pin)
    monkeypatch.setattr(
        intake,
        "_anchor_rows",
        lambda snapshot: {
            "table": list(
                range({"n2": 4, "c2": 3, "n1": 2, "c1": 1}.get(snapshot["name"], 0))
            )
        },
    )
    config = {
        "auto_sources": {
            "domains": "*",
            "max_sources": 4,
            "max_per_domain": 2,
            "max_queries": 2,
            "results_per_query": 2,
            "max_previews": 2,
            "max_freezes": 1,
        }
    }
    seeds = intake._auto_seeds(config, pool)
    assert [seed["anchor_source"] for seed in seeds] == [
        "culture_large",
        "nature_large",
        "culture_small",
        "nature_small",
    ]
    assert len({seed["name"] for seed in seeds}) == 4
    assert (
        intake._auto_seeds(config, {"sources": list(reversed(pool["sources"]))})
        == seeds
    )


def test_auto_source_limits_reject_invalid_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = {
        "schema": "longworld.source-batch-pool.v2",
        "sources": [{"name": "nature", "domain": "nature", "snapshot": {}}],
    }
    monkeypatch.setattr(intake, "_snapshot", lambda _root, pin: pin)
    monkeypatch.setattr(intake, "_anchor_rows", lambda _snapshot: {"table": ["A"]})
    base = {
        "schema": intake.SCHEMA,
        "auto_sources": {
            "domains": "*",
            "max_sources": 1,
            "max_per_domain": 1,
            "max_queries": 1,
            "results_per_query": 1,
            "max_previews": 1,
            "max_freezes": 1,
        },
    }
    intake._validate(base, pool)
    invalid = json.loads(json.dumps(base))
    invalid["auto_sources"]["max_per_domain"] = 0
    with pytest.raises(ValueError, match="max_per_domain"):
        intake._validate(invalid, pool)
    invalid = json.loads(json.dumps(base))
    invalid["auto_sources"]["domains"] = ["missing"]
    with pytest.raises(ValueError, match="unknown domain"):
        intake._validate(invalid, pool)


def test_bootstrap_topics_covers_domains_before_second_topic() -> None:
    pool = {
        "sources": [
            {"name": "a2", "domain": "alpha", "topic": "two", "split": "train"},
            {"name": "a1", "domain": "alpha", "topic": "one", "split": "train"},
            {"name": "b1", "domain": "beta", "topic": "one", "split": "eval"},
            {"name": "a1_dup", "domain": "alpha", "topic": "one", "split": "train"},
        ]
    }
    assert [row["name"] for row in intake._bootstrap_topics(pool, 3)] == [
        "a1",
        "b1",
        "a2",
    ]


def test_bootstrap_pin_failure_precedes_output_and_network(tmp_path: Path) -> None:
    _local_pool()
    config = json.loads(
        (ROOT / "configs/p82_wiki_anchor_bootstrap_v1.json").read_text()
    )
    config["base_pool_sha256"] = "0" * 64
    path = tmp_path / "bootstrap_bad_pin.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="base source pool pin mismatch"):
        intake.bootstrap_anchors(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_preview_bootstrap_topic_gate_rejects_unrelated_search_hits() -> None:
    assert intake._topic_matches_title(
        "stadiums", "List of football stadiums in England"
    )
    assert intake._topic_matches_title(
        "botanical_gardens", "List of botanical gardens and arboretums in New York"
    )
    assert not intake._topic_matches_title(
        "hospitals", "List of Murray State University alumni"
    )
    assert not intake._topic_matches_title(
        "museums_lists", "List of University of the Arts alumni"
    )


def test_direct_bootstrap_rejects_unrelated_title_before_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = {
        "schema": "longworld.source-batch-pool.v2",
        "sources": [
            {
                "name": "museum_source",
                "domain": "culture",
                "topic": "museums_lists",
                "split": "train",
            }
        ],
        "prior_source_manifest": {},
        "requested_recipes": [],
        "max_tasks_by_recipe": {},
    }
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(json.dumps(pool))
    config = {
        "schema": intake.BOOTSTRAP_SCHEMA,
        "base_pool": str(pool_path),
        "base_pool_sha256": hashlib.sha256(pool_path.read_bytes()).hexdigest(),
        "prior_unified_batch": {"path": "unused", "manifest_sha256": "0" * 64},
        "max_topics": 1,
        "results_per_topic": 1,
        "max_anchors": 1,
        "min_rows": 2,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(intake, "_existing_titles", lambda _pool, _prior: {})
    monkeypatch.setattr(
        intake,
        "resolve_titles",
        lambda *_args: pytest.fail("unrelated title was fetched"),
    )

    class SearchOnlyFetcher:
        def get_json(self, _params):
            return {
                "query": {
                    "search": [{"title": "List of University of the Arts alumni"}]
                }
            }

    result = intake.bootstrap_anchors(
        config_path, tmp_path / "output", fetcher=SearchOnlyFetcher()
    )
    assert result["frozen_anchors"] == 0
    assert result["reports"][0]["rejections"] == [
        {
            "title": "List of University of the Arts alumni",
            "reason": "topic_title_mismatch",
        }
    ]


def test_preview_bootstrap_manifest_pin_failure_precedes_output(tmp_path: Path) -> None:
    _local_pool()
    config = json.loads(
        (ROOT / "configs/p82_wiki_preview_bootstrap_v1.json").read_text()
    )
    config["preview_manifest"]["sha256"] = "0" * 64
    path = tmp_path / "preview_bad_pin.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="preview manifest pin mismatch"):
        intake.bootstrap_from_preview(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_bootstrap_relative_snapshot_path_is_root_relative() -> None:
    path = Path("data/capability_records/example/snapshots/page.json")
    assert intake._relative_to_root(path) == str(path)
    assert intake._relative_to_root(ROOT / path) == str(path)


def test_prior_unified_wiki_lane_adds_opposite_split_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intake, "ROOT", tmp_path)
    monkeypatch.setattr(
        intake,
        "_snapshot",
        lambda _root, _pin: {"documents": [{"title": "Shared Page"}]},
    )
    source = tmp_path / "wiki_pool.json"
    source.write_text(
        json.dumps({"sources": [{"split": "eval", "snapshot": {"path": "x"}}]})
    )
    batch = tmp_path / "batch"
    batch.mkdir()
    plan = {
        "sources": [
            {
                "name": "wiki_other_lane",
                "kind": "wiki_source_pool",
                "config": "wiki_pool.json",
                "config_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ]
    }
    (batch / "plan.json").write_text(json.dumps(plan))
    (batch / "manifest.json").write_text(
        json.dumps(
            {
                "plan_sha256": hashlib.sha256(
                    (batch / "plan.json").read_bytes()
                ).hexdigest()
            }
        )
    )
    pin = {
        "path": "batch",
        "manifest_sha256": hashlib.sha256(
            (batch / "manifest.json").read_bytes()
        ).hexdigest(),
    }
    assert intake._prior_unified_titles(pin) == {"shared page": {"eval"}}
    source.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="Wiki lane config changed"):
        intake._prior_unified_titles(pin)
