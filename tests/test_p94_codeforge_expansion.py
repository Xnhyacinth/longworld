"""Novelty checks for multi-view repository task expansion."""

import pytest

from scripts.audit_p94_codeforge_expansion import _check_novelty


def _new(
    *,
    kind="real_code_workflow",
    group="repo:new",
    task="task-1",
    split="train",
    answer="a",
):
    return {
        "source_kind": kind,
        "source_group": group,
        "semantic_task_id": task,
        "task_key": f"{kind}:{group}:{task}",
        "split": split,
        "answer_sha256": answer,
    }


def _base(row):
    return {"candidate": row}


def test_multiple_length_views_count_one_semantic_task():
    row = _new()
    result = _check_novelty([], [row, {**row, "sample_id": "another-view"}])
    assert result["new_semantic_tasks"] == 1
    assert result["new_groups"] == 1


def test_same_id_in_different_kind_does_not_collide():
    result = _check_novelty([_base(_new(kind="real_wiki", group="wiki:old"))], [_new()])
    assert result["new_semantic_tasks"] == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"split": "eval"},
        {"answer_sha256": "different-answer"},
        {"source_group": "repo:another"},
    ],
)
def test_same_semantic_task_cannot_change_group_split_or_answer(changed):
    row = _new()
    with pytest.raises(ValueError):
        _check_novelty([], [row, {**row, **changed}])


def test_existing_repository_or_semantic_task_is_rejected():
    with pytest.raises(ValueError):
        _check_novelty([_base(_new())], [_new(task="task-2")])
    with pytest.raises(ValueError):
        _check_novelty([_base(_new())], [_new(group="repo:another")])
