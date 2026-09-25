"""Behavioral checks for paginated discovery and source-level split hygiene."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis import p93_wiki_structural_intake as intake


def family(**changes):
    value = {
        "name": "river_lists",
        "domain": "geography",
        "mode": "search",
        "query_template": 'intitle:"List of {term}"',
        "terms": ["rivers", "lakes"],
        "max_results_per_term": 4,
        "bundle_size": 2,
        "max_bundles_per_term": 1,
    }
    value.update(changes)
    return value


class FakeFetcher:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = 0
        self.params = []

    def get_json(self, params):
        self.params.append(params)
        self.requests += 1
        return self.responses.pop(0)


def test_paginated_search_filters_titles_and_bounds_requests():
    fetcher = FakeFetcher(
        [
            {
                "query": {
                    "search": [
                        {"title": "List of rivers in Peru"},
                        {"title": "Rivers of Peru"},
                    ]
                },
                "continue": {"sroffset": 2},
            },
            {
                "query": {
                    "search": [
                        {"title": "List of rivers in Peru"},
                        {"title": "List of rivers in Chile"},
                    ]
                }
            },
        ]
    )
    titles, pages = intake.discover(fetcher, family(), "rivers")
    assert titles == ["List of rivers in Peru", "List of rivers in Chile"]
    assert len(pages) == 2
    assert fetcher.params[1]["sroffset"] == 2


def test_category_pagination_and_split_stable_by_family():
    fetcher = FakeFetcher(
        [
            {
                "query": {"categorymembers": [{"title": "List of lakes in Peru"}]},
                "continue": {"cmcontinue": "next"},
            },
            {"query": {"categorymembers": [{"title": "List of lakes in Chile"}]}},
        ]
    )
    fam = family(mode="category", query_template="Category:Lists of {term}")
    titles, _ = intake.discover(fetcher, fam, "lakes")
    assert len(titles) == 2
    assert fetcher.params[1]["cmcontinue"] == "next"
    config = {"eval_families": ["river_lists"]}
    assert intake.planned_split(config, fam["name"]) == intake.planned_split(
        config, {**fam, "terms": ["other"]}["name"]
    )


def test_run_freezes_only_novel_titles_and_keeps_native_probe_separate(
    tmp_path, monkeypatch
):
    root = tmp_path
    old = root / "old.json"
    old.write_text(json.dumps({"documents": [{"title": "List of rivers in Peru"}]}))
    base = root / "base.json"
    base.write_text(
        json.dumps(
            {
                "schema": intake.POOL_SCHEMA,
                "sources": [
                    {
                        "name": "old",
                        "snapshot": {
                            "path": "old.json",
                            "sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
                        },
                    }
                ],
                "requested_recipes": ["wiki_table_lookup"],
                "max_tasks_by_recipe": {"wiki_table_lookup": 2},
                "prior_source_manifest": {"path": "prior", "sha256": "unused"},
            }
        )
    )
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": intake.SCHEMA,
                "base_pool": {
                    "path": "base.json",
                    "sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
                },
                "eval_families": ["river_lists"],
                "families": [family(terms=["rivers"])],
            }
        )
    )
    fetcher = FakeFetcher(
        [
            {
                "query": {
                    "search": [
                        {"title": "List of rivers in Peru"},
                        {"title": "List of rivers in Chile"},
                        {"title": "List of rivers in Bolivia"},
                    ]
                }
            }
        ]
    )

    def fake_freeze(_fetcher, titles, name, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"documents": [{"title": title} for title in titles]})
        )
        return {
            "snapshot_id": "snap",
            "revisions": {title: 12 for title in titles},
            "license": {"text": "CC BY-SA"},
            "pages": len(titles),
            "facts": 4,
        }

    monkeypatch.setattr("scripts.freeze_wiki_title_bundle.freeze_titles", fake_freeze)
    monkeypatch.setattr(
        intake,
        "probe",
        lambda *_: {
            "supported_recipes": ["wiki_table_lookup"],
            "potential_jobs": 1,
        },
    )
    output = root / "out"
    result = intake.run(config, output, root, fetcher=fetcher)
    assert result["frozen_pages"] == 2
    assert result["source_groups"] == 1
    assert result["probe_supported_groups"] == 1
    assert result["discovery"][0]["existing_titles"] == 1
    assert result["frozen"][0]["titles"] == [
        "List of rivers in Chile",
        "List of rivers in Bolivia",
    ]
    assert len(json.loads((output / "source_pool.json").read_text())["sources"]) == 1
    assert (
        intake.verify(output, root)["source_pool_sha256"]
        == result["source_pool_sha256"]
    )
    repartitioned = intake.repartition(config, output, root / "resplit", root)
    assert repartitioned["offline_repartition_from"]["sha256"] == intake.sha(
        output / "manifest.json"
    )
    selected = json.loads((root / "resplit/source_pool.json").read_text())["sources"]
    assert selected[0]["split"] == "eval"
    assert (
        selected[0]["snapshot"]
        == json.loads((output / "source_pool.json").read_text())["sources"][0][
            "snapshot"
        ]
    )
    drift = json.loads(config.read_text())
    drift["families"][0]["terms"] = ["mountains"]
    config.write_text(json.dumps(drift))
    with pytest.raises(ValueError, match="queries differ"):
        intake.repartition(config, output, root / "bad_resplit", root)
    with pytest.raises(ValueError, match="must be new"):
        intake.run(config, output, root, fetcher=fetcher)


def test_invalid_config_rejected_before_writing(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": intake.SCHEMA,
                "base_pool": {},
                "eval_families": ["river_lists"],
                "families": [family(query_template="no substitution")],
            }
        )
    )
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="query_template"):
        intake.run(config, output, tmp_path)
    assert not output.exists()


def test_relative_output_is_resolved_against_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path
    old = root / "old.json"
    old.write_text(json.dumps({"documents": []}))
    base = root / "base.json"
    base.write_text(
        json.dumps(
            {
                "schema": intake.POOL_SCHEMA,
                "sources": [
                    {
                        "name": "old",
                        "snapshot": {
                            "path": "old.json",
                            "sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
                        },
                    }
                ],
            }
        )
    )
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": intake.SCHEMA,
                "base_pool": {
                    "path": "base.json",
                    "sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
                },
                "eval_families": ["river_lists"],
                "families": [family(terms=["rivers"])],
            }
        )
    )
    fetcher = FakeFetcher([{"query": {"search": []}}])
    result = intake.run(config, Path("out"), root, fetcher=fetcher)
    assert result["source_groups"] == 0
    assert (root / "out/discovery/river_lists_01.json").is_file()


def test_symlink_output_cannot_escape_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "leak").symlink_to(outside, target_is_directory=True)
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": intake.SCHEMA,
                "base_pool": {},
                "eval_families": ["river_lists"],
                "families": [family(terms=["rivers"])],
            }
        )
    )
    with pytest.raises(ValueError, match="escapes workspace"):
        intake.run(config, Path("leak/new"), root)
    assert not (outside / "new").exists()
