from __future__ import annotations

from copy import deepcopy

from longworld.core.p66_macro_vintage_taskbank import execute
from scripts.materialize_p66_macro_vintage_taskbank import rejection_counts


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


def test_rejection_counter_reads_both_reject_row_schemas() -> None:
    # Whole-world rejections carry "rejection" (_world), band/program
    # rejections carry "reason" (build). Reading only "reason" raised KeyError
    # on the first rejected world and lost the entire report.
    rows: list[dict[str, object]] = [
        {"series_id": "s", "period": "2020", "rejection": "fewer_than_four_vintages"},
        {
            "series_id": "s",
            "period": "2020",
            "band": "64k",
            "reason": "authentic_unique_records_cannot_fill_band",
        },
        {
            "series_id": "s",
            "period": "2020",
            "band": "128k",
            "program": "full_revision_path",
            "reason": "complete_hf_chat_overflow",
            "tokens": 262145,
        },
        {"series_id": "s", "period": "2021", "rejection": "fewer_than_four_vintages"},
        {"series_id": "s", "period": "2022"},
    ]
    assert rejection_counts(rows) == {
        "fewer_than_four_vintages": 2,
        "authentic_unique_records_cannot_fill_band": 1,
        "complete_hf_chat_overflow": 1,
        "unknown": 1,
    }
    assert rejection_counts([]) == {}
