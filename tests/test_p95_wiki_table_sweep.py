"""Behavioral checks for a bank-aware frozen table sweep."""

from __future__ import annotations

import json

import pytest

from longworld.synthesis.p92_generic_table_scan import parse_tables
from scripts.run_p95_wiki_table_sweep import _load_sources, _plan, _task_id


def _scan_fixture(split: str = "train"):
    body = (
        "## Catalogue\nName | Established | Region\n"
        "Alpha Park | 1960 | North\nBeta Park | 1970 | South\n"
        "Gamma Park | 1980 | East\nDelta Park | 1990 | West\n"
        "Epsilon Park | 2000 | North\nZeta Park | 2010 | South\n"
        "Eta Park | 2020 | East\nTheta Park | 2030 | West\n"
    )
    doc = {"title": "List of parks", "doc_id": "doc-parks", "text": body}
    table = parse_tables(body)[0][0]
    source = {"name": "parks", "split": split}
    snapshot = {"snapshot_id": "snapshot-parks"}
    pages = [{"title": doc["title"], "doc_id": doc["doc_id"], "rejected_tables": []}]
    return source, snapshot, [(doc, table)], pages


def test_global_task_id_is_removed_before_reader_materialization() -> None:
    scan = _scan_fixture()
    _s, _snapshot, candidates, _pages = scan
    doc, table = candidates[0]
    all_work, _, _ = _plan([scan], set(), {}, 8)
    assert len(all_work) == 1 and all_work[0][-1]
    low, high = all_work[0][-1][0]
    existing = _task_id(doc, table, low, high)
    work, rejected, counts = _plan([scan], {existing}, {}, 8)
    assert counts["intervals_planned"] == len(all_work[0][-1])
    assert len(work[0][-1]) == len(all_work[0][-1]) - 1
    assert rejected[0]["reason"] == "duplicate_existing"


def test_duplicate_table_and_cross_split_title_do_not_generate_duplicate_tasks() -> (
    None
):
    scan = _scan_fixture()
    work, rejected, counts = _plan([scan, scan], set(), {}, 4)
    assert len(work) == 1
    assert counts["eligible_tables"] == 2
    assert any(row["reason"] == "duplicate_source_table" for row in rejected)
    with pytest.raises(ValueError, match="crosses train/eval"):
        _plan([scan, _scan_fixture("eval")], set(), {}, 4)


@pytest.mark.parametrize("malformed_second", [False, True])
def test_repeated_visible_table_key_is_rejected_even_if_second_table_is_bad(
    malformed_second: bool,
) -> None:
    source, snapshot, _candidates, pages = _scan_fixture()
    first = _scan_fixture()[2][0][0]["text"]
    second = "## Catalogue\nName | Established | Region\n" + (
        "Broken Park | circa 1970 | North\n"
        if malformed_second
        else first.split("\n", 2)[2]
    )
    doc = {"title": "List of parks", "doc_id": "doc-parks", "text": first + second}
    candidates = [(doc, table) for table in parse_tables(doc["text"])[0]]
    assert candidates
    work, rejected, _ = _plan([(source, snapshot, candidates, pages)], set(), {}, 4)
    assert not work
    assert any(row["reason"] == "ambiguous_visible_table_key" for row in rejected)


def test_pinned_frozen_pools_have_unique_snapshots() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "configs/p95_wiki_table_sweep_v1.json").read_text())
    sources, gross = _load_sources(config)
    assert gross["pool_records"] == len(sources) == 57
    assert len({source["snapshot"]["sha256"] for source in sources}) == len(sources)


def test_duplicate_snapshot_in_two_splits_is_rejected(tmp_path, monkeypatch) -> None:
    import scripts.run_p95_wiki_table_sweep as sweep

    source = {
        "name": "one",
        "split": "train",
        "snapshot": {"path": "unused", "sha256": "a" * 64},
    }
    for name, split in (("first", "train"), ("second", "eval")):
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "schema": "longworld.source-batch-pool.v2",
                    "sources": [{**source, "split": split}],
                }
            )
        )
    monkeypatch.setattr(sweep, "_pinned_path", lambda pin: tmp_path / pin["path"])
    with pytest.raises(ValueError, match="both splits"):
        _load_sources({"source_pools": [{"path": "first"}, {"path": "second"}]})
