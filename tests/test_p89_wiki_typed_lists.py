"""Frozen P89 source, split, reader and mask consistency checks."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path):
    return json.loads(path.read_text())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_p89_frozen_sources_and_reader_splits() -> None:
    source = ROOT / "data/capability_records/p89_wiki_typed_lists_v1"
    candidate = ROOT / "data/candidates/p89_wiki_typed_lists_v1"
    audit = ROOT / "data/candidates/p89_wiki_typed_lists_mask_audit_v1"
    if not (source / "acquisition_manifest.json").exists():
        pytest.skip("frozen P89 source trial is not mounted")
    manifest = _json(source / "acquisition_manifest.json")
    seen_titles = set()
    for group in manifest["frozen"]:
        snapshot_path = ROOT / group["snapshot"]["path"]
        assert _sha(snapshot_path) == group["snapshot"]["sha256"]
        snapshot = _json(snapshot_path)
        assert set(group["revisions"]) == {
            doc["title"] for doc in snapshot["documents"]
        }
        assert (
            group["license"]["text"] == "Creative Commons Attribution-Share Alike 4.0"
        )
        titles = {doc["title"].casefold() for doc in snapshot["documents"]}
        assert not seen_titles & titles
        seen_titles.update(titles)
    assert len(seen_titles) == 12
    result = _json(candidate / "result.json")
    assert result["candidate_views"] == result["independent_tasks"] == 23
    assert result["train_ready"] is False
    index = [
        json.loads(line)
        for line in (candidate / "merged/sample_index.jsonl").read_text().splitlines()
    ]
    assert Counter(row["split"] for row in index) == {"train": 22, "eval": 1}
    assert len({row["task_id"] for row in index}) == len(index)
    assert {row["task_type"] for row in index} == {"table_cell_lookup"}
    mask = _json(audit / "manifest.json")
    assert mask["checked_rows"] == len(index)
    assert mask["source_index_sha256"] == _sha(candidate / "merged/sample_index.jsonl")
    join = _json(
        ROOT
        / "data/capability_records/p89_frozen_wiki_row_index_v1/index_manifest.json"
    )
    assert join["accepted_pairs"] == 0
