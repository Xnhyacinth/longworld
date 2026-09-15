from __future__ import annotations

from copy import deepcopy

from longworld.core.p66_macro_vintage_taskbank import execute


def _rows() -> list[dict[str, object]]:
    return [
        {"observation_id": "a", "vintage_date": "2020-01-01", "value": "10"},
        {"observation_id": "b", "vintage_date": "2020-02-01", "value": "13"},
        {"observation_id": "c", "vintage_date": "2020-03-01", "value": "12"},
        {"observation_id": "d", "vintage_date": "2020-04-01", "value": "9"},
    ]


def test_programs_reconstruct_ordered_path_and_tied_largest_changes() -> None:
    rows = list(reversed(_rows()))
    assert [
        item["observation_id"]
        for item in execute(rows, "full_revision_path")["observations"]
    ] == ["a", "b", "c", "d"]
    assert execute(rows, "revision_change_summary") == {
        "first_vintage": "2020-01-01",
        "last_vintage": "2020-04-01",
        "first_value": "10",
        "last_value": "9",
        "net_change": "-1",
        "changed_transitions": 3,
        "unchanged_transitions": 0,
        "total_transitions": 3,
    }
    largest = execute(rows, "largest_absolute_revision")
    assert largest["maximum_absolute_delta"] == "3"
    assert len(largest["transitions"]) == 2


def test_remove_one_changes_full_path() -> None:
    gold = execute(_rows(), "full_revision_path")
    for index in range(4):
        reduced = deepcopy(_rows())
        del reduced[index]
        assert execute(reduced, "full_revision_path") != gold


def test_fewer_than_two_observations_is_explicitly_insufficient() -> None:
    assert execute(_rows()[:1], "revision_change_summary") == {
        "status": "insufficient_observations"
    }
