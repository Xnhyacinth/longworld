"""Frozen Wiki composition preserves source offsets and rejects unsafe unions."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from longworld.synthesis import wiki_adapter, wiki_snapshot_concat, wiki_world_bridge
from scripts import compose_wiki_snapshot_pool


def _snapshot(pageid: int, title: str, name: str) -> dict:
    page = wiki_adapter.PageRecord(
        pageid=pageid,
        title=title,
        revid=pageid + 100,
        timestamp="2026-09-24T00:00:00Z",
        wikitext=(
            f'{{| class="wikitable"\n! Name !! Region !! Type\n'
            f"|-\n| {name} || North || Protected\n|}}\n"
        ),
    )
    return wiki_adapter.build_snapshot(
        members=[wiki_adapter.Member(pageid, title)],
        pages={pageid: page},
        link_meta=[],
        rights={
            "text": "Creative Commons Attribution-Share Alike 4.0",
            "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
            "page_url_prefix": "https://en.wikipedia.org/wiki/",
        },
        category_title="test composition",
        collection_kind="title_bundle",
        frozen_at="2026-09-24T00:00:00Z",
    )


def _parts(a: dict, b: dict):
    return [(a, "a" * 64, "train"), (b, "b" * 64, "train")]


def test_composition_preserves_every_document_fact_and_offset() -> None:
    a = _snapshot(101, "List of sample parks", "Alpha Park")
    b = _snapshot(202, "List of sample peaks", "Beta Peak")
    result = wiki_snapshot_concat.compose_snapshots(_parts(a, b), split="train")
    wiki_adapter.validate_snapshot(result)
    assert result["source"]["composition_schema"] == wiki_snapshot_concat.SCHEMA
    assert len(result["source"]["components"]) == 2
    for part in (a, b):
        for name, key in (
            ("documents", "doc_id"),
            ("entities", "entity_id"),
            ("facts", "fact_id"),
            ("relations", "relation_id"),
        ):
            actual = {record[key]: record for record in result[name]}
            for record in part[name]:
                assert actual[record[key]] == record
    world = wiki_world_bridge.snapshot_to_world(result)
    assert len(world.documents) == 2
    assert len(result["source"]["revisions"]) == 2


def test_duplicate_identity_and_split_are_rejected() -> None:
    a = _snapshot(101, "List of sample parks", "Alpha Park")
    b = _snapshot(202, "List of sample peaks", "Beta Peak")
    with pytest.raises(ValueError, match="split mismatch"):
        wiki_snapshot_concat.compose_snapshots(
            [(a, "a" * 64, "train"), (b, "b" * 64, "eval")], split="train"
        )
    conflicting = copy.deepcopy(b)
    conflicting["entities"][0]["entity_id"] = a["entities"][0]["entity_id"]
    with pytest.raises(ValueError, match="conflicting entity_id"):
        wiki_snapshot_concat.compose_snapshots(_parts(a, conflicting), split="train")
    with pytest.raises(ValueError, match="duplicate page title/revision"):
        wiki_snapshot_concat.compose_snapshots(
            _parts(a, _snapshot(303, "List of sample parks", "Gamma Park")),
            split="train",
        )


def test_pinned_reader_rejects_changed_bytes(tmp_path) -> None:
    snapshot = _snapshot(101, "List of sample parks", "Alpha Park")
    path = tmp_path / "snapshot.json"
    raw = (json.dumps(snapshot) + "\n").encode()
    path.write_bytes(raw)
    loaded, digest = wiki_snapshot_concat.load_pinned(
        path, hashlib.sha256(raw).hexdigest()
    )
    assert loaded == snapshot
    assert digest == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="hash mismatch"):
        wiki_snapshot_concat.load_pinned(path, "0" * 64)


def test_assignment_preflight_rejects_prior_opposite_split(tmp_path) -> None:
    a = _snapshot(101, "List of sample parks", "Alpha Park")
    b = _snapshot(202, "List of sample peaks", "Beta Peak")
    prior = tmp_path / "prior.jsonl"
    prior_bytes = (
        json.dumps({"split": "eval", "revisions": a["source"]["revisions"]}) + "\n"
    ).encode()
    prior.write_bytes(prior_bytes)
    catalog = tmp_path / "catalog.json"
    catalog_bytes = json.dumps(
        {
            "schema": "longworld.source-batch-pool.v2",
            "prior_source_manifest": {
                "path": str(prior),
                "sha256": hashlib.sha256(prior_bytes).hexdigest(),
            },
            "sources": [
                {"snapshot": {"sha256": digest}, "split": "train"}
                for digest in ("a" * 64, "b" * 64)
            ],
        }
    ).encode()
    catalog.write_bytes(catalog_bytes)
    config = {
        "source_pool_catalog": {
            "path": str(catalog),
            "sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        }
    }
    with pytest.raises(ValueError, match="crosses prior split"):
        compose_wiki_snapshot_pool._validate_split_assignments(config, _parts(a, b))
