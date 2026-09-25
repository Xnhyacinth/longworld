import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis import p92_source_router as router


def _write(root: Path, relative: str, value: dict) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, *, second_split: str = "train") -> dict:
    digest = _write(tmp_path, "snapshots/one.json", {"snapshot_id": "snapshot_one"})
    source = {
        "name": "source_one",
        "domain": "geology",
        "topic": "volcanoes",
        "split": "train",
        "snapshot": {"path": "snapshots/one.json", "sha256": digest},
    }
    for index, split in enumerate(("train", second_split)):
        _write(
            tmp_path,
            f"pools/{index}/source_pool.json",
            {
                "schema": "longworld.source-batch-pool.v2",
                "sources": [{**source, "split": split}],
            },
        )
    _write(tmp_path, "receipts/paper.json", {"source_families": []})
    _write(tmp_path, "receipts/finance.json", {"jobs": []})
    _write(tmp_path, "receipts/code.json", {"rows": []})
    _write(
        tmp_path,
        "receipts/rfc.json",
        {"generator_revision": "p87-hybrid-v6", "operations": {}},
    )
    prior_sha = _write(tmp_path, "prior.json", {})
    return {
        "schema": router.SCHEMA,
        "wiki_source_pool_glob": "pools/*/source_pool.json",
        "prior_source_manifest": {"path": "prior.json", "sha256": prior_sha},
        "observed_receipts": {
            "paper": "receipts/paper.json",
            "finance": "receipts/finance.json",
            "code": "receipts/code.json",
            "rfc_hybrid": "receipts/rfc.json",
        },
    }


def _fake_probe(job: tuple[str, str, str, int]) -> tuple[str, dict]:
    return job[2], {
        "source_kind": "real_wiki",
        "genre": "encyclopedia_list",
        "source_identity": "snapshot_one",
        "world_group_id": "snapshot_one",
        "document_count": 2,
        "document_identities": [
            {"title": "List of volcanoes", "revision_url": "https://example.test/1"}
        ],
        "fact_count": 10,
        "native_structure": {
            "lookup_probe_tasks": 1,
            "cross_document_pair_probe_tasks": 0,
            "closed_table_documents": 0,
        },
        "license": {"url": "https://example.test/license"},
        "source_pins": [{"path": job[1], "sha256": job[2]}],
        "cells": [router._cell("wiki_table_lookup", "probe_supported")],
    }


def test_deduplicates_snapshot_and_reports_native_capacity(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    monkeypatch.setattr(router, "_probe_wiki", _fake_probe)
    result = router.route(config, tmp_path)
    assert result["wiki_pool_references"] == 2
    assert result["wiki_unique_snapshots"] == 1
    assert result["wiki_native_capacity"]["lookup_worlds"] == 1
    assert result["sources"][-1]["world_group_id"] == "snapshot_one"
    assert result["unsupported_genres"]["book"]
    pool = router.consolidated_wiki_pool(result, config["prior_source_manifest"])
    assert len(pool["sources"]) == 1
    assert set(pool["requested_recipes"]) == set(router.WIKI_OPS)


def test_split_conflict_blocks_cells(tmp_path, monkeypatch):
    config = _fixture(tmp_path, second_split="eval")
    monkeypatch.setattr(router, "_probe_wiki", _fake_probe)
    result = router.route(config, tmp_path)
    assert result["split_conflict_worlds"] == ["snapshot_one"]
    wiki = next(row for row in result["sources"] if row["source_kind"] == "real_wiki")
    assert wiki["cells"][0]["status"] == "blocked_split_conflict"
    assert not router.consolidated_wiki_pool(result, config["prior_source_manifest"])[
        "sources"
    ]


def test_rejects_changed_snapshot(tmp_path):
    config = _fixture(tmp_path)
    (tmp_path / "snapshots/one.json").write_text("{}")
    with pytest.raises(ValueError, match="snapshot pin mismatch"):
        router.route(config, tmp_path)


def test_prior_index_novelty_is_bound_to_real_candidate_rows(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    monkeypatch.setattr(router, "_probe_wiki", _fake_probe)
    refs = tmp_path / "prior/candidate_refs.jsonl"
    refs.parent.mkdir()
    refs.write_text(
        json.dumps(
            {
                "candidate": {
                    "source_kind": "real_wiki",
                    "source_group": "snapshot_one",
                    "operation": "table_cell_lookup",
                }
            }
        )
        + "\n"
    )
    digest = hashlib.sha256(refs.read_bytes()).hexdigest()
    _write(tmp_path, "prior/manifest.json", {"refs_sha256": digest})
    config["prior_candidate_index"] = {
        "path": "prior/candidate_refs.jsonl",
        "sha256": digest,
    }
    novelty = router.route(config, tmp_path)["prior_index_novelty"]
    assert novelty["wiki_worlds_already_indexed"] == 1
    assert novelty["new_supported_worlds"] == 0
    assert novelty["supported_operation_cells_absent_from_index"] == {}
    refs.write_text(refs.read_text() + "\n")
    with pytest.raises(ValueError, match="prior candidate index pin mismatch"):
        router.route(config, tmp_path)


def test_same_wiki_page_in_different_snapshots_blocks_both_splits():
    sources = [
        {
            "source_kind": "real_wiki",
            "world_group_id": f"snapshot_{split}",
            "split": split,
            "document_identities": [{"title": title}],
            "cells": [router._cell("wiki_table_lookup", "probe_supported")],
        }
        for split, title in (("train", "List of Parks"), ("eval", "list of parks"))
    ]
    assert router._block_page_split_conflicts(sources) == ["list of parks"]
    assert {source["split"] for source in sources} == {"conflict"}
    assert all(
        cell["status"] == "blocked_split_conflict"
        for source in sources
        for cell in source["cells"]
    )


def test_pinned_pool_catalog_ignores_new_glob_matches(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    monkeypatch.setattr(router, "_probe_wiki", _fake_probe)
    pools = sorted(tmp_path.glob("pools/*/source_pool.json"))
    config.pop("wiki_source_pool_glob")
    config["wiki_source_pools"] = [
        {
            "path": str(path.relative_to(tmp_path)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in pools
    ]
    first = router.route(config, tmp_path)
    _write(
        tmp_path,
        "pools/new/source_pool.json",
        {"schema": "longworld.source-batch-pool.v2", "sources": []},
    )
    assert router.route(config, tmp_path) == first
    config["wiki_source_pools"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Wiki source-pool pin changed"):
        router.route(config, tmp_path)
