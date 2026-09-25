"""Behavioral checks for the automatic arXiv taxonomy planner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p110_paper_autocatalog as catalog


def _taxonomy() -> bytes:
    groups = []
    for group in "abcdef":
        rows = "".join(
            f"<h4>{group}.A{chr(65 + index)} <span>(Topic {index})</span></h4>"
            for index in range(20)
        )
        groups.append(f'<h2 id="accordion-head-grp_{group}">{group}</h2>{rows}')
    return "".join(groups).encode()


def test_taxonomy_plan_balances_groups_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"queries": ["cat:a.AA"]}))
    config = {
        "schema": catalog.SCHEMA + ".config",
        "taxonomy_url": catalog.TAXONOMY_URL,
        "queries": 18,
        "max_per_group": 3,
        "shard_size": 6,
        "page_size": 50,
        "request_interval_seconds": 3.2,
        "content_use": "local_research_only_no_redistribution",
        "user_agent": "test@example.org",
        "frozen_inventory_root": "data/source_inventory",
        "prior_catalog_config": {"path": "prior.json", "sha256": "unused"},
        "prior_candidate_index": {"path": "prior.json", "sha256": "unused"},
        "prior_source_configs": [],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(catalog, "_pin", lambda _pin: prior)
    taxonomy = tmp_path / "taxonomy.html"
    taxonomy.write_bytes(_taxonomy())
    output = tmp_path / "plan"
    result = catalog.run(config_path, output, taxonomy, False)
    assert result["selected_queries"] == 18
    assert result["selected_groups"] == 6
    assert len(result["shards"]) == 3
    queries = [
        query
        for name in result["shards"]
        for query in json.loads((output / name).read_text())["queries"]
    ]
    assert len(queries) == len(set(queries)) == 18
    assert "cat:a.AA" not in queries
    assert catalog.run(config_path, output, None, True) == result
    (output / "category_ledger.jsonl").write_text("tampered\n")
    with pytest.raises(ValueError, match="replay drift"):
        catalog.run(config_path, output, None, True)


def test_taxonomy_rejects_incomplete_or_duplicate_snapshot() -> None:
    with pytest.raises(ValueError, match="distinct dotted categories"):
        catalog._categories(b"<h4>cs.AI</h4>")
    groups = _taxonomy().decode().replace("b.AA", "a.AA")
    with pytest.raises(ValueError, match="distinct dotted categories"):
        catalog._categories(groups.encode())
