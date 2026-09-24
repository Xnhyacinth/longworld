"""Behavioral checks for the offline typed-row JOIN source planner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis import wiki_adapter
from scripts import index_frozen_wiki_joins as indexer


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(path: Path, title: str, text: str, pageid: int) -> dict:
    snapshot = {
        "schema_version": wiki_adapter.SOURCE_SNAPSHOT_SCHEMA,
        "snapshot_id": f"snapshot_{pageid}",
        "frozen_at": "2026-09-24T00:00:00Z",
        "source": {
            "license": {"text": "Creative Commons Attribution-ShareAlike"},
            "revisions": {title: pageid},
        },
        "documents": [
            {
                "doc_id": f"doc-{pageid}",
                "title": title,
                "text": text,
                "sections": [{"start": 0, "end": len(text)}],
            }
        ],
        "entities": [],
        "facts": [],
        "relations": [],
        "ungrounded_quarantine": [],
    }
    wiki_adapter.validate_snapshot(snapshot)
    _write(path, snapshot)
    return snapshot


def _config(tmp_path: Path, *, prior_ids: list[str] | None = None) -> Path:
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _snapshot(
        left,
        "List of test trees A",
        "# List of test trees A\nName | Selector\nOak | S1\nPine | S2\nElm | S3\n",
        101,
    )
    _snapshot(
        right,
        "List of test trees B",
        "# List of test trees B\nName | Target\nOak | Red\nPine | Blue\nElm | Green\n",
        102,
    )
    prior_manifest = tmp_path / "prior.jsonl"
    prior_manifest.write_text("")
    prior_index = tmp_path / "prior_index.jsonl"
    prior_index.write_text(
        "".join(
            json.dumps({"semantic_task_id": task_id}) + "\n"
            for task_id in prior_ids or []
        )
    )
    pool = tmp_path / "pool.json"
    pool_sha = _write(
        pool,
        {
            "schema": "longworld.source-batch-pool.v2",
            "prior_source_manifest": {
                "path": str(prior_manifest),
                "sha256": hashlib.sha256(prior_manifest.read_bytes()).hexdigest(),
            },
            "requested_recipes": [],
            "max_tasks_by_recipe": {},
            "sources": [
                {
                    "name": name,
                    "domain": "biology",
                    "topic": "trees_lists",
                    "split": "train",
                    "snapshot": {
                        "path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    },
                }
                for name, path in (("left", left), ("right", right))
            ],
        },
    )
    config_path = tmp_path / "config.json"
    _write(
        config_path,
        {
            "schema": indexer.SCHEMA,
            "source_pools": [{"path": str(pool), "sha256": pool_sha}],
            "prior_task_indexes": [
                {
                    "path": str(prior_index),
                    "sha256": hashlib.sha256(prior_index.read_bytes()).hexdigest(),
                }
            ],
            "max_pairs": 10,
            "max_tasks_per_pair": 100,
        },
    )
    return config_path


def test_index_compiles_source_supported_pair_and_excludes_prior_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indexer, "ROOT", tmp_path)
    config = _config(tmp_path)
    output = tmp_path / "first"
    result = indexer.run(config, output)
    assert result["candidate_pairs"] == 1
    assert result["accepted_pairs"] == 1
    assert result["accepted_tasks_by_split"]["train"] >= 3
    source = json.loads((output / "source_pool.json").read_text())["sources"][0]
    snapshot = json.loads(indexer._path(source["snapshot"]["path"]).read_text())
    wiki_adapter.validate_snapshot(snapshot)
    assert snapshot["facts"] == []
    assert len(snapshot["source"]["composition"]) == 2
    audit = json.loads((output / "pair_audit.jsonl").read_text())
    config = _config(tmp_path, prior_ids=audit["task_ids"])
    second = indexer.run(config, tmp_path / "second")
    assert second["accepted_pairs"] == 0
    assert second["pair_statuses"] == {"prior_task_duplicate": 1}


def test_output_pin_keeps_workspace_symlink_path_without_escape() -> None:
    relative = Path("data/capability_records/example/snapshot.json")
    assert indexer._output_pin(relative) == str(relative)
    assert indexer._output_pin(indexer.ROOT / relative) == str(relative)
    with pytest.raises(ValueError):
        indexer._output_pin(Path("data/../outside.json"))
    with pytest.raises(ValueError):
        indexer._output_pin(Path("/tmp/outside.json"))


def test_wrong_source_pin_fails_before_output(tmp_path: Path) -> None:
    config = _config(tmp_path)
    payload = json.loads(config.read_text())
    payload["source_pools"][0]["sha256"] = "0" * 64
    _write(config, payload)
    with pytest.raises(ValueError, match="source pool pin mismatch"):
        indexer.run(config, tmp_path / "output")
    assert not (tmp_path / "output").exists()
