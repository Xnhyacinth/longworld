import json

import pytest

from longworld.synthesis.capability_world import (
    generate_bundle,
    solve_visible,
    validate_bundle,
)


def test_deterministic_world_and_three_capabilities():
    bundle = generate_bundle(17, n_records=40)
    assert bundle == generate_bundle(17, n_records=40)
    assert validate_bundle(bundle)["passed"]
    assert {t["capability"] for t in bundle["tasks"]} == {
        "recall_binding",
        "dense_aggregation",
        "state_transitions",
    }
    assert bundle["context"] != generate_bundle(18, n_records=40)["context"]


def test_counterfactual_reruns_conditional_descendants():
    bundle = generate_bundle(8)
    original = bundle["tasks"][2]["answer"]
    changed = bundle["counterfactual"]["tasks"][2]["answer"]
    assert changed != original
    assert bundle["counterfactual"]["checks"]["descendant_changed"]
    assert all(
        a["answer"] != b["answer"]
        for a, b in zip(bundle["tasks"], bundle["counterfactual"]["tasks"])
    )


def test_visible_solver_does_not_accept_answer_metadata_or_incomplete_input():
    bundle = generate_bundle(9)
    task = bundle["tasks"][0]
    question = {**task["question"], "answer": "forged"}
    assert solve_visible(bundle["context"], question) == task["answer"]
    with pytest.raises(ValueError):
        solve_visible("", question)
    lines = bundle["context"].splitlines()
    with pytest.raises(ValueError, match="count"):
        solve_visible("\n".join(lines[:-1]), task["question"])


def test_tampered_gold_and_observation_rejected():
    bundle = generate_bundle(5)
    bundle["tasks"][1]["answer"] += 1
    assert not validate_bundle(bundle)["passed"]
    bundle = generate_bundle(5)
    records = [json.loads(line) for line in bundle["context"].splitlines()]
    records[1]["params"]["entity"] = "forged"
    bundle["context"] = "\n".join(json.dumps(row) for row in records)
    assert not validate_bundle(bundle)["passed"]


def test_record_workload_scales_without_duplicate_world_claim():
    small = generate_bundle(1, n_records=24)
    large = generate_bundle(1, n_records=200)
    assert len(large["context"]) > len(small["context"])
    assert validate_bundle(large)["passed"]
    assert large["lineage"]["topology_family"] == small["lineage"]["topology_family"]
    assert not large["admission"]["strict_long_dependency"]


@pytest.mark.parametrize("n_records", [0, 5, -1])
def test_rejects_undersized_world(n_records):
    with pytest.raises(ValueError):
        generate_bundle(1, n_records=n_records)


def test_aggregation_excludes_rejected_records_but_recall_keeps_them():
    bundle = generate_bundle(42, n_records=30)
    rows = [json.loads(line) for line in bundle["context"].splitlines()]
    entity = next(
        row["params"]["entity"]
        for row in rows[1:]
        if row["type"] == "bind"
        and row["params"]["alias"] == bundle["tasks"][1]["question"]["alias"]
    )
    relevant = [
        row["params"]
        for row in rows[1:]
        if row["type"] == "record" and row["params"]["entity"] == entity
    ]
    assert any(not record["approved"] for record in relevant)
    expected = sum(record["amount"] for record in relevant if record["approved"])
    assert bundle["tasks"][1]["answer"] == expected
    rejected = next(
        index for index, record in enumerate(relevant) if not record["approved"]
    )
    question = {**bundle["tasks"][0]["question"], "ordinal": rejected + 1}
    assert solve_visible(bundle["context"], question) == relevant[rejected]["memo"]


def test_duplicate_event_and_unknown_dependency_fail_closed():
    bundle = generate_bundle(2)
    rows = [json.loads(line) for line in bundle["context"].splitlines()]
    rows[-1]["required_inputs"] = ["nonexistent"]
    with pytest.raises(ValueError, match="dependency"):
        solve_visible(
            "\n".join(json.dumps(row) for row in rows), bundle["tasks"][2]["question"]
        )
    rows = [json.loads(line) for line in bundle["context"].splitlines()]
    rows[-1]["id"] = rows[-2]["id"]
    with pytest.raises(ValueError, match="duplicate"):
        solve_visible(
            "\n".join(json.dumps(row) for row in rows), bundle["tasks"][2]["question"]
        )


def test_late_spend_costs_do_not_encode_aggregate_and_state_is_not_aggregate():
    bundles = [generate_bundle(seed, n_records=60) for seed in (11, 12)]
    late_parameters = []
    for bundle in bundles:
        rows = [json.loads(line) for line in bundle["context"].splitlines()]
        late_parameters.append(
            [
                (row["params"]["cost"], row["params"]["minimum_balance"])
                for row in rows[-2:]
            ]
        )
        assert bundle["tasks"][2]["answer"] != bundle["tasks"][1]["answer"]
        assert bundle["tasks"][0]["question"]["ordinal"] > 1
    assert late_parameters[0] == late_parameters[1]
    assert bundles[0]["tasks"][1]["answer"] != bundles[1]["tasks"][1]["answer"]


def test_nonspend_dependencies_rejected_instead_of_ignored():
    bundle = generate_bundle(2)
    rows = [json.loads(line) for line in bundle["context"].splitlines()]
    rows[4]["required_inputs"] = [rows[1]["id"]]
    with pytest.raises(ValueError, match="only spend"):
        solve_visible(
            "\n".join(json.dumps(row) for row in rows), bundle["tasks"][0]["question"]
        )


def test_counterfactual_uses_native_ranges_and_no_recall_marker():
    bundle = generate_bundle(5, n_records=400)
    factual = [json.loads(line) for line in bundle["context"].splitlines()][1:]
    changed = [
        json.loads(line) for line in bundle["counterfactual"]["context"].splitlines()
    ][1:]
    edited = [(left, right) for left, right in zip(factual, changed) if left != right]
    assert len(edited) == 2
    memo_event = next(
        left["id"]
        for left, right in edited
        if left["params"].get("memo") != right["params"].get("memo")
    )
    for row in changed:
        if row["type"] == "record":
            assert 10 <= row["params"]["amount"] <= 50
            assert row["params"]["memo"].startswith("authorization-")
        assert memo_event not in row["required_inputs"]
    assert validate_bundle(bundle)["passed"]


@pytest.mark.parametrize("seed", range(10))
def test_bounded_intervention_seed_sweep(seed):
    assert validate_bundle(generate_bundle(seed, n_records=24))["passed"]
