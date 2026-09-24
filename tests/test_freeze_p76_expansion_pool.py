"""A frozen source expansion becomes a pinned input to the same batch planner."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import freeze_p76_expansion_pool as expansion


def test_expansion_appends_pinned_sources_without_changing_base(
    tmp_path: Path, monkeypatch
) -> None:
    base_path = tmp_path / "base.json"
    base = {
        "schema": "longworld.source-batch-pool.v2",
        "sources": [{"name": "old_source"}],
        "requested_recipes": ["wiki_table_lookup"],
    }
    base_path.write_text(json.dumps(base))
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema": expansion.SCHEMA,
                "base_pool": str(base_path),
                "groups": [
                    {
                        "name": "new_source",
                        "domain": "history",
                        "topic": "castles",
                        "split": "eval",
                        "titles": ["List of castles in Scotland"],
                    }
                ],
            }
        )
    )

    def fake_freeze(_fetcher, titles, label, out):
        assert titles == ["List of castles in Scotland"]
        out.write_text(json.dumps({"snapshot_id": "frozen", "label": label}))
        return {"pages": 1, "facts": 3, "snapshot_id": "frozen"}

    monkeypatch.setattr(expansion, "freeze_titles", fake_freeze)
    output = tmp_path / "frozen"
    result = expansion.run(catalog_path, output, workers=1)
    generated = json.loads((output / "source_pool.json").read_text())
    assert result["frozen_groups"] == 1 and not result["failed_groups"]
    assert generated["sources"][0] == base["sources"][0]
    assert generated["sources"][1]["snapshot"]["sha256"]
    assert json.loads(base_path.read_text()) == base
