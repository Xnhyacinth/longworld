"""P96 paper native-to-unified trust boundary regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.p96_paper_to_unified import _normalize_pair, convert

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "data/candidates/p96_paper_reference_qa_v4"
CONFIG = ROOT / "configs/p96_paper_reference_qa_v1.json"


def _first(name: str) -> dict:
    return json.loads((NATIVE / name).read_text().splitlines()[0])


def test_normalizer_rejects_changed_gold_or_hidden_reader_metadata() -> None:
    if not (NATIVE / "manifest.json").is_file():
        pytest.skip("frozen P96 paper candidate artifact is not mounted")
    index, reader, proof = (
        _first("sample_index.jsonl"),
        _first("train.jsonl"),
        _first("audit.jsonl"),
    )
    changed = {
        **reader,
        "messages": [reader["messages"][0], {"role": "assistant", "content": "wrong"}],
    }
    with pytest.raises(ValueError, match="evidence identity"):
        _normalize_pair(index, changed, proof, NATIVE / "manifest.json")
    with pytest.raises(ValueError, match="hidden audit metadata"):
        _normalize_pair(
            index, {**reader, "proof": proof}, proof, NATIVE / "manifest.json"
        )


def test_p96_native_batch_normalizes_to_candidate_only_shard(tmp_path: Path) -> None:
    if not (NATIVE / "mask_audit.json").is_file():
        pytest.skip("frozen P96 paper candidate artifact is not mounted")
    output = tmp_path / "merged"
    manifest = convert(CONFIG, NATIVE, output)
    assert manifest["candidate_views"] == manifest["independent_semantic_tasks"] == 8
    assert manifest["views_by_lane"] == {"p96_paper_reference": 8}
    assert manifest["splits"] == {"train": 8}
    assert manifest["train_ready"] is False
    readers = [
        json.loads(line)
        for line in (output / "candidate_train.jsonl").read_text().splitlines()
    ]
    indices = [
        json.loads(line)
        for line in (output / "sample_index.jsonl").read_text().splitlines()
    ]
    assert len(readers) == len(indices) == 8
    assert all(set(row) == {"sample_id", "messages"} for row in readers)
    assert all(row["source_kind"] == "real_paper_source" for row in indices)
    assert all(
        row["dependency_status"]
        == "bounded_final_reader_reference_and_target_deletions"
        for row in indices
    )
    assert convert(CONFIG, NATIVE, output, verify_only=True) == manifest
