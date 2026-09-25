"""P107 keeps source-pair novelty and parallel pair replay exact."""

from __future__ import annotations

import pytest

from scripts import p107_code_curate as curate
from scripts import p107_code_parallel_replay as replay


def test_prior_exposure_requires_both_semantic_and_pr_pair_agreement():
    pair = ("https://github.com/oxc-project/oxc", (10, 20))
    assert curate._prior_status("new", pair, set(), set()) == "accepted_new_pr_pair"
    assert (
        curate._prior_status("old", pair, {"old"}, {pair}) == "rejected_prior_pr_pair"
    )
    with pytest.raises(ValueError, match="exposure disagree"):
        curate._prior_status("old", pair, {"old"}, set())
    with pytest.raises(ValueError, match="exposure disagree"):
        curate._prior_status("new", pair, set(), {pair})


def test_two_distinct_added_line_witnesses_and_content_intervention_required():
    audit = {
        "source_group": "https://github.com/oxc-project/oxc",
        "source_prs": [10, 20],
        "evidence": [{"pull_request": 10}, {"pull_request": 20}],
        "filename_preserving_added_code_removal_changes_answer": True,
        "all_selected_identifiers_absent_after_added_code_removal": True,
        "evidence_token_span": 17000,
        "answer": [{"pull_request": 10}, {"pull_request": 20}],
    }
    assert curate._pair(audit) == (
        "https://github.com/oxc-project/oxc",
        (10, 20),
    )
    curate._evidence(audit, {"observed_witness_span_tokens": 17000})
    with pytest.raises(ValueError, match="certificate differs"):
        curate._evidence(
            {
                **audit,
                "all_selected_identifiers_absent_after_added_code_removal": False,
            },
            {"observed_witness_span_tokens": 17000},
        )
    with pytest.raises(ValueError, match="certificate differs"):
        curate._evidence(audit, {"observed_witness_span_tokens": 16999})
    with pytest.raises(ValueError, match="two distinct PRs"):
        curate._pair({**audit, "source_prs": [10, 10]})


def test_parallel_worker_reuses_same_world_and_pair_scope(monkeypatch):
    world = {
        "episodes": [{"episode_id": "a"}, {"episode_id": "b"}],
        "world_id": "frozen",
    }
    reader = {"messages": [{"role": "user", "content": "context"}]}
    index = {"semantic_task_id": "task"}
    audit = {"episode_ids": ["a", "b"]}
    monkeypatch.setattr(replay, "_load_bank", lambda _entry: world)
    monkeypatch.setattr(replay, "_tokenizer", lambda: object())

    def compile_pair(scoped, _tokenizer, **limits):
        assert scoped["world_id"] == "frozen"
        assert [item["episode_id"] for item in scoped["episodes"]] == ["a", "b"]
        assert limits["min_span"] == 16384
        return [(reader, index, audit)], []

    monkeypatch.setattr(replay, "compile_bank", compile_pair)
    result = replay._worker(
        ({}, [0], {"min_evidence_span_tokens": 16384, "max_chat_tokens": 262144})
    )
    assert result["pair_count"] == 1
    assert result["outcomes"] == [
        {
            "scope": ("a", "b"),
            "status": "accepted",
            "reader_sha256": replay._digest(reader),
            "index_sha256": replay._digest(index),
            "audit_sha256": replay._digest(audit),
        }
    ]


def test_parallel_worker_rejects_missing_pair_outcome(monkeypatch):
    monkeypatch.setattr(
        replay,
        "_load_bank",
        lambda _entry: {"episodes": [{"episode_id": "a"}, {"episode_id": "b"}]},
    )
    monkeypatch.setattr(replay, "_tokenizer", lambda: object())
    monkeypatch.setattr(replay, "compile_bank", lambda *_a, **_k: ([], []))
    with pytest.raises(ValueError, match="one pair outcome"):
        replay._worker(
            ({}, [0], {"min_evidence_span_tokens": 16384, "max_chat_tokens": 262144})
        )
