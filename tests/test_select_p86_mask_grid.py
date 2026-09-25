"""The mask audit grid chooses a real train reader in each occupied cell."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import select_p86_mask_grid as grid


def _row(sample: str, split: str, position: int, length: str) -> dict:
    return {
        "sample_id": sample,
        "task_key": sample,
        "semantic_task_id": sample,
        "source_kind": "real_wiki",
        "source_group": "source-one",
        "domain": "nature",
        "topic": "parks",
        "operation": "cross_document_table_join",
        "split": split,
        "length_bin": length,
        "full_chat_tokens": 35000,
        "answer_sha256": "a" * 64,
        "source_name": "fixture",
        "output_file": f"candidate_{split}.jsonl",
        "row_index": position,
    }


def test_selects_one_train_reader_per_operation_length_cell(
    tmp_path: Path, monkeypatch
) -> None:
    merged = tmp_path / "merged"
    merged.mkdir()
    (merged / "manifest.json").write_text("{}")
    rows = [
        _row("eval-32", "eval", 0, "32k"),
        _row("train-32-a", "train", 0, "32k"),
        _row("train-32-b", "train", 1, "32k"),
        _row("train-64", "train", 2, "64k"),
    ]
    (merged / "sample_index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    monkeypatch.setattr(
        grid,
        "verify_merge",
        lambda _: {"candidate_views": 4, "splits": {"train": 3, "eval": 1}},
    )
    output = tmp_path / "selection"
    result = grid.select(merged, output)
    chosen = [
        json.loads(line)
        for line in (output / "selection_index.jsonl").read_text().splitlines()
    ]
    assert result["selected_views"] == 2
    assert result["selected_by_length"] == {"32k": 1, "64k": 1}
    assert {row["split"] for row in chosen} == {"train"}
    assert {row["sample_id"] for row in chosen} & {"train-32-a", "train-32-b"}
    assert "eval-32" not in {row["sample_id"] for row in chosen}
    assert [row["selection_rank"] for row in chosen] == [0, 1]
    assert grid.verify_selection(merged, output) == result
    eval_output = tmp_path / "eval-selection"
    eval_result = grid.select(merged, eval_output, split_filter="eval")
    assert eval_result["selected_views"] == 1
    assert eval_result["selection_split"] == "eval"
    assert (
        grid.verify_selection(merged, eval_output, split_filter="eval") == eval_result
    )


def test_rejects_broken_reader_position(tmp_path: Path, monkeypatch) -> None:
    merged = tmp_path / "merged"
    merged.mkdir()
    (merged / "manifest.json").write_text("{}")
    (merged / "sample_index.jsonl").write_text(
        json.dumps(_row("bad", "train", 4, "32k")) + "\n"
    )
    monkeypatch.setattr(
        grid,
        "verify_merge",
        lambda _: {"candidate_views": 1, "splits": {"train": 1}},
    )
    try:
        grid.select(merged, tmp_path / "selection")
    except ValueError as error:
        assert "position" in str(error)
    else:
        raise AssertionError("invalid source pointer was accepted")
