"""Independent P105 reader joins and answer-changing edits."""

import pytest

from scripts.p105_wiki_grid_audit import _assert_hit_control, _by_id


def test_mixed_split_records_join_by_sample_id() -> None:
    readers = [
        {"sample_id": "train-a", "split": "train"},
        {"sample_id": "eval-b", "split": "eval"},
    ]
    index = [
        {"sample_id": "eval-b", "split": "eval"},
        {"sample_id": "train-a", "split": "train"},
    ]
    by_reader = _by_id(readers, "reader")
    by_index = _by_id(index, "index")
    assert by_reader["eval-b"]["split"] == by_index["eval-b"]["split"]
    assert by_reader["train-a"]["split"] == by_index["train-a"]["split"]
    with pytest.raises(ValueError, match="duplicate"):
        _by_id(readers + readers[:1], "reader")


def test_hit_must_add_exactly_the_edited_row() -> None:
    baseline = {"count": 2, "entries": ["A", "B"]}
    _assert_hit_control(
        baseline, {"count": 3, "entries": ["A", "B", "C"]}, baseline, "C"
    )
    with pytest.raises(ValueError, match="exactly one"):
        _assert_hit_control(
            baseline, {"count": 3, "entries": ["A", "B", "D"]}, baseline, "C"
        )
    with pytest.raises(ValueError, match="exactly one"):
        _assert_hit_control(
            baseline,
            {"count": 3, "entries": ["A", "B", "C"]},
            {"count": 1, "entries": ["A"]},
            "C",
        )
