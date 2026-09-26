"""Behavioral checks for the review-comment/final-head join."""

from __future__ import annotations

import pytest

from scripts.p132_review_diff_join import (
    add_excluded_path,
    balanced_pairs,
    intervene,
    review_path,
    scoped_join,
)


def _visible() -> dict:
    records = []
    scope = []
    for number, path, stale in ((11, "src/Foo.py", "old.py"), (12, "lib/bar.py", None)):
        prefix = str(number)
        names = {
            name: prefix + name for name in ("pr", "merge", "head", "review", "stale")
        }
        selected = [names[name] for name in ("pr", "merge", "head", "review")]
        records.extend(
            [
                {
                    "id": names["pr"],
                    "kind": "pull_request",
                    "text": "PR",
                    "attributes": {"number": number},
                    "links": [],
                    "occurred_at": prefix,
                },
                {
                    "id": names["merge"],
                    "kind": "merge",
                    "text": "merged",
                    "attributes": {"commit": prefix},
                    "links": [names["pr"], names["head"]],
                    "occurred_at": prefix,
                },
                {
                    "id": names["head"],
                    "kind": "commit",
                    "text": "diff -- " + path + "\n+content",
                    "attributes": {"sha": prefix},
                    "links": [],
                    "occurred_at": prefix,
                },
                {
                    "id": names["review"],
                    "kind": "review",
                    "text": "review_comment_id=1 on " + path + ":\nPlease revise",
                    "attributes": {},
                    "links": [names["pr"]],
                    "occurred_at": prefix,
                },
            ]
        )
        if stale:
            records.append(
                {
                    "id": names["stale"],
                    "kind": "review",
                    "text": "review_comment_id=2 on " + stale + ":\nOlder revision",
                    "attributes": {},
                    "links": [names["pr"]],
                    "occurred_at": prefix,
                }
            )
            selected.append(names["stale"])
        scope.append({"pull_request": number, "records": selected})
    return {"scope": scope, "records": records}


def test_complete_join_and_both_text_interventions() -> None:
    visible = _visible()
    answer, rows = scoped_join(visible)
    assert answer == [
        {"pull_request": 11, "path": "src/Foo.py"},
        {"pull_request": 12, "path": "lib/bar.py"},
    ]
    assert rows[0]["excluded"] == ["old.py"]
    for side in ("comment", "target"):
        changed = intervene(visible, rows[0], "src/Foo.py", side)
        altered, _ = scoped_join(changed)
        assert altered == [{"pull_request": 12, "path": "lib/bar.py"}]
        selected = (
            [
                record
                for record in changed["records"]
                if record["id"] == rows[0]["head_id"]
            ]
            if side == "target"
            else [
                record
                for record in changed["records"]
                if record["id"] in rows[0]["comment_records"]["src/Foo.py"]
            ]
        )
        assert all("src/Foo.py" not in record["text"] for record in selected)
    inserted, _ = scoped_join(add_excluded_path(visible, rows[0], "old.py"))
    assert inserted == [
        {"pull_request": 11, "path": "old.py"},
        {"pull_request": 11, "path": "src/Foo.py"},
        {"pull_request": 12, "path": "lib/bar.py"},
    ]


def test_exact_case_and_ambiguous_diff_rejected() -> None:
    visible = _visible()
    visible["records"][3]["text"] = "review_comment_id=1 on src/foo.py:\nWrong case"
    answer, _ = scoped_join(visible)
    assert answer == [{"pull_request": 12, "path": "lib/bar.py"}]
    visible["records"][2]["text"] += "\nrename from prior.py\nrename to src/Foo.py"
    with pytest.raises(ValueError, match="rename/deletion"):
        scoped_join(visible)


def test_comment_path_only_in_review_comment_text() -> None:
    assert (
        review_path({"kind": "review", "text": "review_comment_id=3 on a/b:\nbody"})
        == "a/b"
    )
    assert review_path({"kind": "review", "text": "review_id=3 on a/b:\nbody"}) is None


def test_scope_schedule_prefers_distinct_pr_episodes() -> None:
    episodes = [{"episode_id": str(i)} for i in range(4)]
    pairs = [
        (episodes[0], episodes[1]),
        (episodes[0], episodes[2]),
        (episodes[2], episodes[3]),
        (episodes[1], episodes[3]),
    ]
    ordered = balanced_pairs(pairs)
    assert [episode["episode_id"] for episode in ordered[0]] == ["0", "1"]
    assert [episode["episode_id"] for episode in ordered[1]] == ["2", "3"]
