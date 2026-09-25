"""A source-pool mask replay must handle lookup and pair rows together."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_p94_wiki_native import audit


def test_source_pool_replays_mixed_lookup_and_pair_readers(tmp_path: Path) -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "data/candidates/p95_wiki_vocab_native_v2/merged"
    )
    if not (source / "manifest.json").is_file():
        pytest.skip("frozen P95 mixed Wiki source pool is not mounted")
    indexes = [
        json.loads(line)
        for line in (source / "sample_index.jsonl").read_text().splitlines()
    ]
    chosen = []
    for kind in ("table_cell_lookup", "table_pair_earlier_year"):
        chosen.append(next(row for row in indexes if row["task_type"] == kind))
    ids = {row["example_id"] for row in chosen}
    for name in ("sample_index.jsonl", "audit.jsonl", "train.jsonl", "eval.jsonl"):
        rows = [
            json.loads(line)
            for line in (source / name).read_text().splitlines()
            if line
        ]
        (tmp_path / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows if row["example_id"] in ids)
        )
    (tmp_path / "manifest.json").write_text(json.dumps({"candidate_views": 2}))
    result = audit(tmp_path, "source_pool")
    assert result["checked_rows"] == 2
    assert set(result["reader_sha256"]) == ids
    train_path, eval_path = tmp_path / "train.jsonl", tmp_path / "eval.jsonl"
    train_rows = [
        json.loads(line) for line in train_path.read_text().splitlines() if line
    ]
    eval_rows = [
        json.loads(line) for line in eval_path.read_text().splitlines() if line
    ]
    source_rows, destination_rows = (
        (train_rows, eval_rows) if train_rows else (eval_rows, train_rows)
    )
    destination_rows.append(source_rows.pop())
    train_path.write_text("".join(json.dumps(row) + "\n" for row in train_rows))
    eval_path.write_text("".join(json.dumps(row) + "\n" for row in eval_rows))
    with pytest.raises(ValueError, match="physical split"):
        audit(tmp_path, "source_pool")
    index_path = tmp_path / "sample_index.jsonl"
    index_path.write_text(
        index_path.read_text() + index_path.read_text().splitlines()[0] + "\n"
    )
    with pytest.raises(ValueError, match="index example IDs repeat"):
        audit(tmp_path, "source_pool")
