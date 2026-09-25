"""Metadata selection must preserve work-level splits and source uncertainty."""

from __future__ import annotations

import hashlib
import json

import pytest

from scripts import p105_paper_catalog as catalog


def _entry(work: str, query: str, version: int) -> dict:
    return {
        "work_id": work,
        "category_query": query,
        "latest_version": version,
        "license_status": "not_reported_by_atom",
        "license_uri": [],
    }


def test_multidomain_selection_deduplicates_work_and_keeps_last_two_versions():
    config = {
        "queries": ["cat:cs.CL", "cat:stat.ML"],
        "per_query_work_limit": 2,
        "work_limit": 4,
        "minimum_latest_version": 2,
    }
    pages = [
        (
            {"query": "cat:cs.CL"},
            [
                _entry("2608.31046", "cat:cs.CL", 2),
                _entry("2604.07709", "cat:cs.CL", 5),
                _entry("2606.27349", "cat:cs.CL", 1),
            ],
        ),
        (
            {"query": "cat:stat.ML"},
            [
                _entry("2608.31046", "cat:stat.ML", 2),
                _entry("2606.27349", "cat:stat.ML", 3),
                _entry("2609.18366", "cat:stat.ML", 3),
            ],
        ),
    ]
    selected, coverage = catalog._select(config, pages, {"2604.07709"})
    assert coverage["distinct_metadata_works"] == 4
    assert [row["work_id"] for row in selected] == [
        "2608.31046",
        "2606.27349",
        "2609.18366",
    ]
    assert selected[1]["versions"] == ["v2", "v3"]
    assert selected[1]["license_status"] == "not_reported_by_atom"
    assert len({row["work_id"] for row in selected}) == len(selected)


def test_cached_atom_page_rejects_byte_tamper(tmp_path):
    url = "https://export.arxiv.org/api/query?search_query=cat%3Acs.CL"
    query = "cat:cs.CL"
    directory = tmp_path / "metadata/query_000"
    directory.mkdir(parents=True)
    raw = b"<feed />"
    (directory / "response.atom.xml").write_bytes(raw)
    (directory / "receipt.json").write_text(
        json.dumps(
            {
                "query": query,
                "url": url,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "entries": 0,
            }
        )
    )
    (directory / "response.atom.xml").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        catalog._read_page(tmp_path, 0, query, url)


@pytest.mark.parametrize("raw", ["../outside", "/absolute/outside"])
def test_catalog_pin_paths_stay_inside_workspace(raw, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="workspace-relative"):
        catalog._path(raw)
