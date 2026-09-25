"""P103 novelty audit rejects repeated source or task identities."""

from __future__ import annotations

import pytest

from scripts.audit_p103_categorical_delta import (
    _check_disjoint_pages,
    _support_rows,
    _task_ids,
)


def test_page_title_and_url_are_both_global_novelty_keys() -> None:
    prior = {("old title", "en.wikipedia.org/wiki/old")}
    _check_disjoint_pages(prior, {("new title", "en.wikipedia.org/wiki/new")})
    with pytest.raises(ValueError, match="page title or URL"):
        _check_disjoint_pages(prior, {("old title", "en.wikipedia.org/wiki/new")})
    with pytest.raises(ValueError, match="page title or URL"):
        _check_disjoint_pages(prior, {("new title", "en.wikipedia.org/wiki/old")})


def test_task_identity_and_support_matrix_reconcile() -> None:
    with pytest.raises(ValueError, match="task IDs repeat"):
        _task_ids([{"task_id": "a"}, {"task_id": "a"}])
    rows = _support_rows(
        "p95",
        {"source_one": {"split": "eval", "domain": "education", "topic": "schools"}},
        {"world_one": "source_one"},
        [{"world_id": "world_one"}, {"world_id": "world_one"}],
        [
            {
                "source_group": "source_one",
                "eligible_tables": 1,
                "rejected_tables": [
                    {"reason": "row_width_mismatch"},
                    {"reason": "too_few_rows"},
                ],
            }
        ],
    )
    assert rows == [
        {
            "pool": "p95",
            "source": "source_one",
            "split": "eval",
            "domain": "education",
            "topic": "schools",
            "pages": 1,
            "eligible_tables": 1,
            "accepted_tasks": 2,
            "row_width_mismatch": 1,
            "other_table_rejections": 1,
        }
    ]
