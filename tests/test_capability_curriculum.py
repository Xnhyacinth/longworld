import copy
import json

import pytest

from longworld.synthesis.capability_curriculum import (
    generate_bundle,
    solve_visible,
    validate_bundle,
)


@pytest.mark.parametrize("family", ["ledger", "reservation"])
@pytest.mark.parametrize("seed", [1, 7, 123])
def test_independent_multiqa_and_causal_replay(family, seed):
    bundle = generate_bundle(seed, family)
    result = validate_bundle(bundle)
    assert result["passed"], result
    assert len(bundle["tasks"]) == 16
    assert (
        len({json.dumps(t["question"], sort_keys=True) for t in bundle["tasks"]}) == 16
    )
    changes = sum(
        a["answer"] != b["answer"]
        for a, b in zip(bundle["tasks"], bundle["counterfactual"]["tasks"])
    )
    assert 0 < changes < 16
    assert bundle["counterfactual"]["checks"]["descendant_changed"]
    for task in bundle["tasks"]:
        with pytest.raises(ValueError):
            solve_visible("", task["question"])
        assert (
            solve_visible(task["controls"]["oracle_compact_context"], task["question"])
            == task["answer"]
        )


@pytest.mark.parametrize("family", ["ledger", "reservation"])
def test_tampered_answer_and_missing_evidence_rejected(family):
    bundle = generate_bundle(5, family)
    changed = copy.deepcopy(bundle)
    changed["tasks"][0]["answer"] = "wrong"
    assert not validate_bundle(changed)["passed"]
    rows = bundle["context"].splitlines()
    with pytest.raises(ValueError):
        solve_visible("\n".join(rows[:-1]), bundle["tasks"][0]["question"])


def test_family_transitions_are_distinct_and_topic_not_hidden_truth():
    a = generate_bundle(1, "ledger", topic={"domain": "science"})
    b = generate_bundle(1, "reservation", topic={"domain": "science"})
    assert a["lineage"]["topology_family"] != b["lineage"]["topology_family"]
    assert a["context"] != b["context"]
    assert (
        generate_bundle(1, "ledger", topic={"domain": "arts"})["context"]
        == a["context"]
    )


@pytest.mark.parametrize(
    "kwargs", [{"n_records": 15}, {"n_questions": 17}, {"family": "fake"}]
)
def test_reject_unsupported_workloads(kwargs):
    with pytest.raises(ValueError):
        generate_bundle(1, **kwargs)


def test_release_is_idempotent_and_failed_reserve_does_not_create_capacity():
    from longworld.synthesis.capability_curriculum import RULES, VERSION

    events = [
        ("bind", {"entity": "a", "alias": "x"}, []),
        ("supply", {"entity": "a", "amount": 10, "memo": "m"}, []),
        ("reserve", {"entity": "a", "amount": 11}, []),
        ("reserve", {"entity": "a", "amount": 7}, []),
        ("release", {"entity": "a", "reservation": "e2"}, []),
        ("release", {"entity": "a", "reservation": "e3"}, []),
        ("release", {"entity": "a", "reservation": "e3"}, []),
        ("reserve", {"entity": "a", "amount": 1}, ["e2"]),
    ]
    rows = [
        {
            "schema": VERSION,
            "family": "reservation",
            "rules": RULES["reservation"],
            "event_count": len(events),
        }
    ]
    rows.extend(
        {
            "id": f"e{i}",
            "time": f"2000-01-{i + 1:02d}",
            "type": kind,
            "params": params,
            "required_inputs": parents,
        }
        for i, (kind, params, parents) in enumerate(events)
    )
    context = "\n".join(json.dumps(row) for row in rows)
    q = {"alias": "x", "asof": "e7", "operation": "state"}
    assert solve_visible(context, q) == 10
    assert solve_visible(context, {**q, "operation": "active"}) == []
    assert solve_visible(context, {**q, "operation": "state", "asof": "e3"}) == 3


@pytest.mark.parametrize("family", ["ledger", "reservation"])
def test_long_chain_and_question_only_witness(family):
    bundle = generate_bundle(42, family, n_records=4000)
    assert validate_bundle(bundle)["passed"]
    original, alternate = bundle["tasks"][0], bundle["counterfactual"]["tasks"][0]
    assert original["question"] == alternate["question"]
    assert original["answer"] != alternate["answer"]
    changed = bundle["counterfactual"]["checks"]["changed_application_events"]
    # The intervention position is now randomized (T1 fix), so the span of
    # recomputed descendants varies with it. The old assertion
    # (span > 3900) encoded the degenerate always-index-4 intervention:
    # every descendant across the full context flipped every time. Keep the
    # substantive requirements instead: the recomputation exists, it is
    # ordered after the intervention, and the context is fully rendered.
    assert len(changed) >= 1
    assert int(changed[0][1:]) > int(
        next(iter(bundle["counterfactual"]["intervention"]["param_overrides"]))[1:]
    )
    assert len(bundle["context"].splitlines()) == 4005


def test_topic_view_does_not_inflate_semantic_world_identity():
    first = generate_bundle(6, topic={"domain": "science"})
    second = generate_bundle(6, topic={"domain": "arts"})
    assert first["world_id"] == second["world_id"]
    assert first["context"] == second["context"]
    assert first["lineage"]["topic_view_id"] != second["lineage"]["topic_view_id"]


@pytest.mark.parametrize("family", ["ledger", "reservation"])
def test_nonaction_dependency_is_rejected(family):
    bundle = generate_bundle(10, family)
    rows = [json.loads(line) for line in bundle["context"].splitlines()]
    rows[5]["required_inputs"] = [rows[1]["id"]]
    with pytest.raises(ValueError, match="only debit/reserve"):
        solve_visible(
            "\n".join(json.dumps(row) for row in rows), bundle["tasks"][0]["question"]
        )
