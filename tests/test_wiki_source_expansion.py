"""Behavioral checks for topic-driven Wiki source intake."""

from __future__ import annotations

import json

import pytest

from scripts import expand_wiki_source_pool as intake


class FakeFetcher:
    def __init__(self, rows: list[str]) -> None:
        self.rows = rows
        self.calls = []

    def get_json(self, params):
        self.calls.append(params)
        key = "search" if params["list"] == "search" else "categorymembers"
        return {"query": {key: [{"title": title} for title in self.rows]}}


def _seed(**changes):
    return {
        "name": "wiki_new_rivers",
        "domain": "geography",
        "topic": "rivers",
        "split": "eval",
        "mode": "search",
        "query": 'intitle:"List of" rivers',
        "title_contains": "river",
        "max_pages": 8,
        "bundle_size": 2,
        "max_bundles": 2,
        **changes,
    }


def test_discovery_filters_off_topic_results_and_preserves_raw_response() -> None:
    fetcher = FakeFetcher(
        ["List of rivers in Peru", "List of lakes in Peru", "List of rivers in Peru"]
    )
    titles, payload = intake.discover(fetcher, _seed())
    assert titles == ["List of rivers in Peru"]
    assert len(payload["query"]["search"]) == 3
    assert fetcher.calls[0]["srsearch"] == 'intitle:"List of" rivers'


def test_category_discovery_uses_bounded_page_members() -> None:
    fetcher = FakeFetcher(["List of rivers in Chile", "River Nile"])
    titles, _ = intake.discover(
        fetcher,
        _seed(mode="category", query="Category:Lists of rivers"),
    )
    assert titles == ["List of rivers in Chile"]
    assert fetcher.calls[0]["cmlimit"] == 8


def test_expansion_freezes_novel_bundles_and_separates_probe(
    tmp_path, monkeypatch
) -> None:
    base_path = tmp_path / "base.json"
    base = {
        "schema": "longworld.source-batch-pool.v2",
        "sources": [{"name": "old_source", "snapshot": {"path": "unused"}}],
        "requested_recipes": ["wiki_table_lookup"],
        "max_tasks_by_recipe": {"wiki_table_lookup": 3},
    }
    base_path.write_text(json.dumps(base))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {"schema": intake.SCHEMA, "base_pool": str(base_path), "seeds": [_seed()]}
        )
    )
    monkeypatch.setattr(
        intake, "_existing_titles", lambda _base: {"list of rivers in peru"}
    )

    def fake_freeze(_fetcher, titles, label, path):
        path.write_text(json.dumps({"label": label, "titles": titles}))
        return {
            "snapshot_id": "snap1",
            "pages": len(titles),
            "facts": 7,
            "revisions": {title: 42 for title in titles},
            "license": {"text": "CC BY-SA", "url": "https://license.example"},
        }

    monkeypatch.setattr(intake, "freeze_titles", fake_freeze)
    monkeypatch.setattr(
        intake,
        "expand",
        lambda config, _root, _reader: (
            [{"recipe": "wiki_table_lookup"}],
            [{"source": config["sources"][0]["name"], "reason": "pair unsupported"}],
        ),
    )
    fetcher = FakeFetcher(
        [
            "List of rivers in Peru",
            "List of rivers in Chile",
            "List of rivers in Bolivia",
            "List of mountains in Chile",
        ]
    )
    output = tmp_path / "out"
    result = intake.run(catalog, output, fetcher=fetcher)
    pool = json.loads((output / "source_pool.json").read_text())
    new_pool = json.loads((output / "new_source_pool.json").read_text())
    assert result["frozen_groups"] == 1
    assert result["productive_groups_at_probe"] == 1
    assert result["discovery"][0]["excluded_existing_titles"] == 1
    assert result["frozen"][0]["titles"] == [
        "List of rivers in Chile",
        "List of rivers in Bolivia",
    ]
    assert result["frozen"][0]["license"]["text"] == "CC BY-SA"
    assert result["frozen"][0]["revisions"]["List of rivers in Chile"] == 42
    assert pool["sources"][0] == base["sources"][0]
    assert pool["sources"][1]["snapshot"]["sha256"]
    assert len(new_pool["sources"]) == 1
    assert result["new_source_pool_sha256"]
    assert not result["train_ready"]
    with pytest.raises(ValueError, match="must be new"):
        intake.run(catalog, output, fetcher=fetcher)


def test_invalid_seed_rejected_before_writing(tmp_path) -> None:
    base_path = tmp_path / "base.json"
    base_path.write_text(
        json.dumps({"schema": "longworld.source-batch-pool.v2", "sources": []})
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema": intake.SCHEMA,
                "base_pool": str(base_path),
                "seeds": [_seed(max_pages=0)],
            }
        )
    )
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="max_pages"):
        intake.run(catalog, output, fetcher=FakeFetcher([]))
    assert not output.exists()
