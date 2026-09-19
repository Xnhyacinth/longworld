"""join_unanswerable spine adapter: the six-family surface, on the runner contract."""

import copy
import json
from collections import Counter

import pytest

from longworld.synthesis import capability_records as records
from longworld.synthesis.capability_unanswerable_adapter import (
    FAMILIES,
    HONESTY,
    PROMPTS,
    PROTOCOLS,
    RETURNS,
    UNKNOWN,
    VERSION,
    generate_world,
    plan_variants,
    render_instruction,
    solve_visible,
    validate_bundle,
)

SMALL = {"length_records": 200, "consumed_records": 20, "depth": 1, "n_variants": 2}
SEEDS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)


@pytest.mark.parametrize("seed", SEEDS)
def test_generation_and_validation_are_green_over_ten_seeds(seed):
    bundle = generate_world(seed, "join_unanswerable", **SMALL)
    result = validate_bundle(bundle)
    assert result["passed"], (seed, result["errors"])


def test_world_is_deterministic_and_serializable():
    first = generate_world(3, "join_unanswerable", **SMALL)
    second = generate_world(3, "join_unanswerable", **SMALL)
    assert first == second
    assert json.loads(json.dumps(first)) == first
    assert (
        first["context"] != generate_world(4, "join_unanswerable", **SMALL)["context"]
    )
    assert validate_bundle(first)["checks"]["deterministic_reproduction"] is True


def test_a_relabelled_world_fails_deterministic_reproduction():
    bundle = generate_world(20, "join_unanswerable", **SMALL)
    bundle["length_records"] = 400
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert result["checks"]["deterministic_reproduction"] is False


def test_validation_does_not_mutate_the_bundle():
    bundle = generate_world(21, "join_unanswerable", **SMALL)
    before = copy.deepcopy(bundle)
    validate_bundle(bundle)
    assert bundle == before


# --------------------------------------------------------------------------
# The spine contract: what the runner consumes
# --------------------------------------------------------------------------


def test_the_spine_round_trip_from_plan_to_rows():
    """plan_variants -> generate_world -> validate_bundle -> render_instruction.

    The same sequence scripts/run_capability_records.py drives per family:
    the floor search over plan_variants, generate_world(seed, family, L, K,
    H, n) with positional (L, K, H) arguments, validate_bundle on the fitted
    bundle, and the instruction re-render build_messages compares against.
    """
    for seed in (5, 6):
        length = 32
        while plan_variants(length, 20, 2) != 2:
            length += 1
        bundle = generate_world(seed, "join_unanswerable", length, 20, 1, 2)
        result = validate_bundle(bundle)
        assert result["passed"], result["errors"]
        for task in bundle["tasks"]:
            assert task["instruction"] == render_instruction(
                "join_unanswerable", task["question"], task["phrasing_index"]
            )


def test_the_bundle_shape_is_the_runners():
    bundle = generate_world(6, "join_unanswerable", **SMALL)
    assert bundle["world_id"] == VERSION + "-" + bundle["world_id"].split("-v1-")[1]
    assert bundle["world_id"].startswith(VERSION + "-")
    assert bundle["family"] == "join_unanswerable"
    assert bundle["depth"] == 1
    assert bundle["n_variants"] == 2
    accounting = bundle["length_accounting"]
    assert accounting["record_rows"] == bundle["length_records"] == 200
    assert accounting["consumed_rows_per_task"] == [
        task["consumed_count"] for task in bundle["tasks"]
    ]
    assert accounting["padding_rows"] == 200 - sum(accounting["consumed_rows_per_task"])
    assert accounting["padding_is_exposure_not_semantic_scale"] is True
    assert set(accounting) == {
        "record_rows",
        "reference_rows",
        "rendered_rows",
        "consumed_rows_per_task",
        "padding_rows",
        "decoy_padding_rows",
        "padding_is_exposure_not_semantic_scale",
    }
    for task in bundle["tasks"]:
        assert task["task_id"].startswith("q")
        assert task["question"]["family"] == "join_unanswerable"
        assert task["answer"] == UNKNOWN
        assert task["consumed"] == sorted(task["consumed"])
        assert task["consumed_count"] == 20
        assert task["instruction"].startswith("Task: ")


def test_honesty_labels_fail_closed():
    bundle = generate_world(14, "join_unanswerable", **SMALL)
    assert bundle["honesty"] == HONESTY
    assert bundle["honesty"]["strict_long_dependency_verified"] is False
    assert bundle["honesty"]["model_utility_measured"] is False
    assert bundle["honesty"]["production_eligible"] is False
    assert bundle["honesty"]["source_kind"] == "simulated"
    bundle["honesty"]["production_eligible"] = True
    assert not validate_bundle(bundle)["passed"]


def test_prompt_pool_is_deep_enough_and_rotated():
    assert len(PROMPTS["join_unanswerable"]) >= 20
    assert len(set(PROMPTS["join_unanswerable"])) == len(PROMPTS["join_unanswerable"])
    phrasings = {
        task["phrasing_index"]
        for seed in range(200)
        for task in generate_world(seed, "join_unanswerable", 120, 16, 1, 1)["tasks"]
    }
    assert len(phrasings) >= 20


def test_instruction_contract_is_the_module_instruction():
    """The adapter's render_instruction reproduces the P0-1 byte contract."""
    bundle = generate_world(5, "join_unanswerable", **SMALL)
    for task in bundle["tasks"]:
        assert task["instruction"] == (
            f"Task: {PROMPTS['join_unanswerable'][task['phrasing_index']]} "
            f"Fields: {PROTOCOLS and records.FIELDS['join_lookup']} "
            f"Program: {records.describe_program({'family': 'join_lookup', 'steps': task['question']['steps']})}. "
            f"{RETURNS['join_unanswerable']}"
        )
        assert task["instruction"] != records.render_instruction(
            "join_lookup", {**task["question"], "family": "join_lookup"}, 0
        )


def test_world_identity_never_appears_in_the_visible_text():
    bundle = generate_world(15, "join_unanswerable", **SMALL)
    assert records.VERSION in bundle["context"]  # the contract, not the identity
    assert bundle["world_id"] not in bundle["context"]
    for task in bundle["tasks"]:
        assert bundle["world_id"] not in task["instruction"]
        assert "world" not in task["instruction"].lower()


def test_l_is_exact_and_k_is_one_variant_per_task():
    bundle = generate_world(7, "join_unanswerable", 317, 20, 1, 3)
    types = [json.loads(line)["type"] for line in bundle["context"].splitlines()[1:]]
    assert types.count("record") == 317
    assert len(bundle["tasks"]) == 3


# --------------------------------------------------------------------------
# The constraints: one UNKNOWN task, depth 1, the module's own PROMPTS bank
# --------------------------------------------------------------------------


def test_depth_deeper_than_one_is_rejected_loudly():
    with pytest.raises(ValueError, match="depth must be 1"):
        generate_world(1, "join_unanswerable", 200, 20, 2, 2)


def test_rejects_k_that_cannot_fit_in_l():
    with pytest.raises(ValueError):
        generate_world(1, "join_unanswerable", 200, 20, 1, 8)
    with pytest.raises(ValueError):
        generate_world(1, "join_unanswerable", 40, 200, 1, 2)


@pytest.mark.parametrize("length_records", [0, 16, -5])
def test_rejects_undersized_world(length_records):
    with pytest.raises(ValueError):
        generate_world(1, "join_unanswerable", length_records, 20, 1, 2)


def test_rejects_an_unknown_family():
    with pytest.raises(ValueError):
        generate_world(1, "not_a_family", **SMALL)


def test_the_executor_translates_the_family_and_refuses_foreign_programs():
    bundle = generate_world(17, "join_unanswerable", **SMALL)
    question = bundle["tasks"][0]["question"]
    # The stored question, translated to join_lookup, raises the undetermined
    # join refusal -- the executor-visible signature of UNKNOWN.
    with pytest.raises(ValueError, match="join consumed no rows"):
        solve_visible(bundle["context"], question)
    # A stored question the executor cannot translate is refused loudly.
    foreign = {**question, "family": "filter_aggregate"}
    with pytest.raises(ValueError):
        solve_visible(bundle["context"], foreign)


# --------------------------------------------------------------------------
# The certificate: two legal completions, both preserved in the bundle
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", (3, 8, 11))
def test_the_two_world_property_still_holds(seed):
    """Completion A is undetermined; completion B is a concrete executor value."""
    bundle = generate_world(seed, "join_unanswerable", 120, 12, 1, 1)
    task = bundle["tasks"][0]
    header, _ = records.parse_context(bundle["context"])
    assert header["family"] == "join_lookup"
    # The stored question, translated, is what a records solver must refuse.
    with pytest.raises(ValueError, match="join consumed no rows"):
        records.solve_visible(
            bundle["context"], {**task["question"], "family": "join_lookup"}
        )
    # Completion B: add one reference row for a join-domain entity, and the
    # same executor returns exactly the certificate's value.
    certificate = task["certificate"]
    completion_b = certificate["completion_b"]
    by_id = {
        json.loads(line)["id"]: json.loads(line)
        for line in bundle["context"].splitlines()[1:]
    }
    # Completion B: add one reference row for an entity the join domain
    # contributes `multiplier` pairs of, and the same executor returns
    # exactly the certificate's value. The certificate pins (added amount,
    # multiplier, product); an entity whose consumed-row count is that
    # multiplier reproduces it.
    pairs_per_entity = Counter(by_id[row_id]["entity"] for row_id in task["consumed"])
    added = completion_b["added_reference_amount"]
    multiplier = completion_b["pairs_multiplier"]
    assert multiplier >= 1
    entity = next(
        name for name, count in pairs_per_entity.items() if count == multiplier
    )
    added_line = json.dumps(
        {
            "id": "k-completion-b",
            "type": "reference",
            "entity": entity,
            "amount": added,
            "memo": "note-completion-b",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    completed = bundle["context"] + "\n" + added_line
    answer = records.solve_visible(
        completed, {**task["question"], "family": "join_lookup"}
    )
    assert answer["aggregate"]["value"] == added * multiplier


def test_the_certificate_lives_in_the_task_and_the_bundle_mirror():
    bundle = generate_world(9, "join_unanswerable", 120, 12, 1, 2)
    mirror = bundle["unanswerable_certificate"]
    assert set(mirror) == {task["task_id"] for task in bundle["tasks"]}
    for task in bundle["tasks"]:
        certificate = task["certificate"]
        assert mirror[task["task_id"]] is certificate
        assert certificate["form"] == "two_world_completion"
        completion_b = certificate["completion_b"]
        assert (
            completion_b["executor_value"]
            == completion_b["added_reference_amount"] * completion_b["pairs_multiplier"]
        )
        assert certificate["differ"] is True


def test_tampered_instruction_is_rejected():
    bundle = generate_world(5, "join_unanswerable", 120, 12, 1, 1)
    bundle["tasks"][0]["instruction"] = "Task: just return 0."
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "instruction does not match the registered contract" in result["errors"]


def test_a_certificate_dropped_from_the_bundle_mirror_is_rejected():
    bundle = generate_world(8, "join_unanswerable", 120, 12, 1, 1)
    tampered = copy.deepcopy(bundle)
    del tampered["unanswerable_certificate"]
    result = validate_bundle(tampered)
    assert not result["passed"]
    assert (
        "the bundle-level certificate mirror does not match the tasks"
        in result["errors"]
    )


def test_a_certificate_edited_in_one_place_only_is_rejected():
    bundle = generate_world(8, "join_unanswerable", 120, 12, 1, 1)
    tampered = copy.deepcopy(bundle)
    # Replace (not mutate) the task's certificate so the bundle mirror keeps
    # the original: the validator must reject both the tampered field and
    # the mirror disagreement.
    tampered["tasks"][0]["certificate"] = {
        **tampered["tasks"][0]["certificate"],
        "differ": False,
    }
    result = validate_bundle(tampered)
    assert not result["passed"]
    assert "certificate must record that completions differ" in result["errors"]
    assert (
        "the bundle-level certificate mirror does not match the tasks"
        in result["errors"]
    )


def test_wrong_answer_is_rejected():
    bundle = generate_world(6, "join_unanswerable", 120, 12, 1, 1)
    bundle["tasks"][0]["answer"] = 42
    assert not validate_bundle(bundle)["passed"]


def test_a_world_where_the_join_is_determined_is_rejected():
    """A context that supplies the reference row is not an unanswerable world."""
    bundle = generate_world(6, "join_unanswerable", 120, 12, 1, 1)
    task = bundle["tasks"][0]
    by_id = {
        json.loads(line)["id"]: json.loads(line)
        for line in bundle["context"].splitlines()[1:]
    }
    entity = next(iter({by_id[row_id]["entity"] for row_id in task["consumed"]}))
    added_line = json.dumps(
        {
            "id": "k-completion-b",
            "type": "reference",
            "entity": entity,
            "amount": 7,
            "memo": "note-completion-b",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    tampered = copy.deepcopy(bundle)
    tampered["context"] = bundle["context"] + "\n" + added_line
    tampered["unanswerable_certificate"] = copy.deepcopy(
        bundle["unanswerable_certificate"]
    )
    result = validate_bundle(tampered)
    assert not result["passed"]


def test_tampered_provenance_is_rejected():
    bundle = generate_world(24, "join_unanswerable", 120, 12, 1, 2)
    task = bundle["tasks"][0]
    distractor = next(
        json.loads(line)["id"]
        for line in bundle["context"].splitlines()[1:]
        if json.loads(line)["type"] == "record"
        and json.loads(line)["id"] not in set(task["consumed"])
    )
    task["consumed"] = sorted([*task["consumed"], distractor])
    task["consumed_count"] = len(task["consumed"])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert any("consumed" in error for error in result["errors"])


def test_tampered_rules_text_is_rejected():
    bundle = generate_world(5, "join_unanswerable", 120, 12, 1, 1)
    lines = bundle["context"].splitlines()
    header = json.loads(lines[0])
    header["rules"] = "Any row may be used as evidence."
    bundle["context"] = "\n".join([json.dumps(header, sort_keys=True), *lines[1:]])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "rules text does not match the registered contract" in result["errors"]


def test_a_world_header_of_the_wrong_family_is_rejected():
    bundle = generate_world(5, "join_unanswerable", 120, 12, 1, 1)
    lines = bundle["context"].splitlines()
    header = json.loads(lines[0])
    header["family"] = "filter_aggregate"
    bundle["context"] = "\n".join([json.dumps(header, sort_keys=True), *lines[1:]])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "world header must pin the join_lookup contract" in result["errors"]


# --------------------------------------------------------------------------
# Distribution matching: same record/reference row shapes as answerable join rows
# --------------------------------------------------------------------------


def test_row_shapes_match_the_join_lookup_answerable_worlds():
    """The unanswerable world is a records join_lookup world, row for row."""
    unanswerable = generate_world(31, "join_unanswerable", 300, 20, 1, 2)
    answerable = records.generate_world(31, "join_lookup", 300, 20, 1, 2)
    keys = lambda bundle: {
        frozenset(json.loads(line).keys())
        for line in bundle["context"].splitlines()[1:]
    }
    assert keys(unanswerable) <= keys(answerable) | {
        frozenset({"id", "type", "entity", "amount", "memo", "category", "date"}),
        frozenset({"id", "type", "entity", "amount", "memo"}),
    }
    header = json.loads(unanswerable["context"].splitlines()[0])
    assert header["rules"] == records.PROTOCOLS["join_lookup"]
    assert header["schema"] == records.VERSION
    # Memo and id conventions are the records conventions.
    for line in unanswerable["context"].splitlines()[1:]:
        row = json.loads(line)
        assert row["memo"].startswith("note-")
        assert row["id"][0] in ("r", "k")


def test_reference_rows_exist_so_the_world_is_not_trivially_empty():
    """Reference rows ARE present (for other entities) -- the model must check
    which entity they belong to, not just notice the type is absent."""
    bundle = generate_world(11, "join_unanswerable", 120, 12, 1, 1)
    types = [json.loads(line)["type"] for line in bundle["context"].splitlines()[1:]]
    assert types.count("reference") >= 4
    assert types.count("record") >= 12


def test_padding_volume_grows_with_l_and_not_with_k():
    small = generate_world(19, "join_unanswerable", 200, 20, 1, 2)
    large = generate_world(19, "join_unanswerable", 800, 20, 1, 2)
    assert (
        large["length_accounting"]["padding_rows"]
        > small["length_accounting"]["padding_rows"]
    )
    assert (
        large["length_accounting"]["consumed_rows_per_task"]
        == small["length_accounting"]["consumed_rows_per_task"]
    )


def test_the_family_dispatch_shape_registers_one_family():
    assert FAMILIES == ("join_unanswerable",)
    assert set(PROTOCOLS) == set(FAMILIES)
    assert set(PROMPTS) == set(FAMILIES)
    assert set(RETURNS) == set(FAMILIES)
