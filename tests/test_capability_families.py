import copy
import json
import re
from collections import Counter

import pytest

from longworld.synthesis.capability_families import (
    CAPABILITY_LEVELS,
    FAMILIES,
    HONESTY,
    PROMPTS,
    PROTOCOLS,
    RULE_FAMILIES,
    RULE_STRUCTURES,
    VERSION,
    answer_value,
    generate_world,
    holdout_plan,
    plan_variants,
    render_instruction,
    solve_visible,
    split_for_rule_family,
    validate_bundle,
    window_ablation,
)

SMALL = {"length_records": 200, "consumed_records": 20, "depth": 2, "n_variants": 2}
# set_complete is an H=1 family: the spine schedules depth 1 for it and the
# executor rejects depth 2, so its shared-contract runs pin depth 1.
SET_SMALL = {**SMALL, "depth": 1}
# dense_aggregate is H=1 AND band-gated: its region is the passed K shared by
# every task, so a shared-contract cell must carry K/L inside [0.35, 0.85].
# The shared cells below keep their (L, K) for the other families and are
# rescaled for dense by cells_for(), so one parametrized suite covers all.
DENSE_SMALL = {**SMALL, "depth": 1, "consumed_records": 100}
SEEDS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
H1_FAMILIES = ("set_complete", "dense_aggregate")


def family_depth(family):
    """set_complete and dense_aggregate are H=1 families; the shared-contract
    tests run their depth."""
    return 1 if family in H1_FAMILIES else 2


def small_for(family):
    if family == "set_complete":
        return SET_SMALL
    if family == "dense_aggregate":
        return DENSE_SMALL
    return SMALL


def cells_for(family, length_records, consumed_records):
    """A shared (L, K) cell, rescaled in-band when the family is band-gated.

    dense_aggregate's K is the region, so a K/L of 0.1 (the shared cells'
    ratio) is out of its band; the rescale keeps the same L and lifts K to
    the band's midpoint, which is the family's own operating point.
    """
    if family == "dense_aggregate":
        return length_records, max(4, round(length_records * 0.6))
    return length_records, consumed_records


def visible_rows(bundle):
    return [json.loads(line) for line in bundle["context"].splitlines()[1:]]


def data_rows(bundle):
    """The parsed rows, a header line being the one entry that has no `type`."""
    return [row for row in visible_rows(bundle) if "type" in row]


def shape_of(answer):
    """The structural shape: quoted strings and numbers masked."""
    text = re.sub(r'"[^"]*"', '"S"', json.dumps(answer, sort_keys=True))
    return re.sub(r"\b\d+(?:\.\d+)?\b", "N", text)


def drop_row(bundle, row_id):
    """The context with one rendered row line removed."""
    lines = bundle["context"].splitlines()
    kept = [line for line in lines if json.loads(line).get("id") != row_id]
    assert len(kept) == len(lines) - 1
    return "\n".join(kept)


def solved(context, question):
    """The answer, or a fail-closed refusal as its own outcome.

    A family executor refuses rather than guesses -- a single-match query that
    no longer locates exactly one row, an alias whose declaration is gone, an
    empty-match declaration over a world where rows matched -- so a raised
    ValueError counts as the answer having changed, exactly as the module's own
    sensitivity measurement treats it.
    """
    try:
        return solve_visible(context, question)
    except ValueError:
        return "<refused>"


def answer_after_drop(bundle, task, row_id):
    return solved(drop_row(bundle, row_id), task["question"])


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_generation_and_validation_are_green_over_ten_seeds(family, seed):
    depths = (1,) if family in H1_FAMILIES else (1, 2)
    for depth in depths:
        bundle = generate_world(seed, family, **{**small_for(family), "depth": depth})
        result = validate_bundle(bundle)
        assert result["passed"], (family, seed, depth, result["errors"])


@pytest.mark.parametrize("family", FAMILIES)
def test_world_is_deterministic_and_serializable(family):
    first = generate_world(3, family, **small_for(family))
    second = generate_world(3, family, **small_for(family))
    assert first == second
    assert json.loads(json.dumps(first)) == first
    assert first["context"] != generate_world(4, family, **small_for(family))["context"]
    assert validate_bundle(first)["checks"]["deterministic_reproduction"] is True


@pytest.mark.parametrize("family", FAMILIES)
def test_a_relabelled_world_fails_deterministic_reproduction(family):
    bundle = generate_world(20, family, **small_for(family))
    bundle["length_records"] = 400
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert result["checks"]["deterministic_reproduction"] is False


@pytest.mark.parametrize("family", FAMILIES)
def test_validation_does_not_mutate_the_bundle(family):
    bundle = generate_world(21, family, **small_for(family))
    before = copy.deepcopy(bundle)
    validate_bundle(bundle)
    assert bundle == before


@pytest.mark.parametrize("family", FAMILIES)
def test_prompt_pool_is_deep_enough_and_rotated(family):
    assert len(PROMPTS[family]) >= 20
    assert len(set(PROMPTS[family])) == len(PROMPTS[family])
    phrasings = {
        task["phrasing_index"]
        for seed in range(200)
        for task in generate_world(seed, family, *cells_for(family, 120, 16), 1, 1)[
            "tasks"
        ]
    }
    assert len(phrasings) >= 20


@pytest.mark.parametrize("family", FAMILIES)
def test_instruction_is_reproducible_from_program_and_phrasing(family):
    bundle = generate_world(6, family, **small_for(family))
    assert set(PROTOCOLS) == set(FAMILIES)
    for task in bundle["tasks"]:
        assert task["instruction"] == render_instruction(
            family, task["question"], task["phrasing_index"]
        )
        assert task["instruction"].startswith("Task: ")
        assert task["capability"] == family
        assert task["capability_level"] == CAPABILITY_LEVELS[family]


@pytest.mark.parametrize("family", FAMILIES)
def test_tampered_prompt_is_rejected(family):
    bundle = generate_world(5, family, **small_for(family))
    bundle["tasks"][0]["instruction"] = "Just answer whatever looks right."
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "instruction does not match the registered contract" in result["errors"]


@pytest.mark.parametrize("family", FAMILIES)
def test_a_prompt_from_another_family_is_rejected(family):
    other = next(name for name in FAMILIES if name != family)
    bundle = generate_world(5, family, **small_for(family))
    bundle["tasks"][0]["instruction"] = render_instruction(
        other, generate_world(5, other, **small_for(other))["tasks"][0]["question"], 0
    )
    assert not validate_bundle(bundle)["passed"]


@pytest.mark.parametrize("family", FAMILIES)
def test_tampered_protocol_is_rejected(family):
    bundle = generate_world(5, family, **small_for(family))
    lines = bundle["context"].splitlines()
    header = json.loads(lines[0])
    header["rules"] = "Any row may be used as evidence."
    bundle["context"] = "\n".join([json.dumps(header, sort_keys=True), *lines[1:]])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "rules text does not match the registered contract" in result["errors"]


@pytest.mark.parametrize("family", FAMILIES)
def test_honesty_labels_fail_closed(family):
    bundle = generate_world(14, family, **small_for(family))
    assert bundle["honesty"] == HONESTY
    assert bundle["honesty"]["strict_long_dependency_verified"] is False
    assert bundle["honesty"]["model_utility_measured"] is False
    assert bundle["honesty"]["production_eligible"] is False
    assert bundle["honesty"]["source_kind"] == "simulated"
    bundle["honesty"]["production_eligible"] = True
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "honesty labels must stay fail-closed" in result["errors"]


@pytest.mark.parametrize("family", FAMILIES)
def test_world_identity_never_appears_in_the_visible_text(family):
    bundle = generate_world(15, family, **small_for(family))
    assert VERSION in bundle["context"]  # the contract, not the identity
    assert bundle["world_id"] not in bundle["context"]
    for task in bundle["tasks"]:
        assert bundle["world_id"] not in task["instruction"]
        assert f"seed {bundle['seed']}" not in task["instruction"]
        assert f"seed={bundle['seed']}" not in task["instruction"]
        assert "world" not in task["instruction"].lower()


@pytest.mark.parametrize("family", FAMILIES)
def test_executor_fails_closed_on_missing_contract(family):
    bundle = generate_world(17, family, **small_for(family))
    question = bundle["tasks"][0]["question"]
    with pytest.raises(ValueError):
        solve_visible("", question)
    lines = bundle["context"].splitlines()
    header = json.loads(lines[0])
    header["schema"] = "not-this-schema"
    with pytest.raises(ValueError):
        solve_visible(
            "\n".join([json.dumps(header, sort_keys=True), *lines[1:]]), question
        )
    header["schema"] = VERSION
    header["family"] = "some_other_family"
    with pytest.raises(ValueError):
        solve_visible(
            "\n".join([json.dumps(header, sort_keys=True), *lines[1:]]), question
        )


@pytest.mark.parametrize("family", FAMILIES)
def test_executor_rejects_a_program_from_another_family(family):
    other = next(name for name in FAMILIES if name != family)
    context = generate_world(17, family, **small_for(family))["context"]
    foreign = generate_world(17, other, **small_for(other))["tasks"][0]["question"]
    with pytest.raises(ValueError):
        solve_visible(context, foreign)


@pytest.mark.parametrize("family", FAMILIES)
def test_distractor_rows_are_never_counted_as_consumed_evidence(family):
    length, consumed = cells_for(family, 300, 25)
    bundle = generate_world(8, family, length, consumed, family_depth(family), 2)
    primary = {
        row["id"] for row in data_rows(bundle) if row["type"] in ("record", "event")
    }
    consumed = {row_id for task in bundle["tasks"] for row_id in task["consumed"]}
    assert consumed & primary
    accounting = bundle["length_accounting"]
    assert accounting["padding_rows"] >= 1
    assert accounting["padding_is_exposure_not_semantic_scale"] is True
    assert (
        accounting["consumed_primary_rows"] + accounting["padding_rows"]
        == accounting["record_rows"]
    )
    assert accounting["consumed_rows_per_task"] == [
        task["consumed_count"] for task in bundle["tasks"]
    ]


@pytest.mark.parametrize("family", FAMILIES)
def test_deleting_a_necessary_row_changes_the_answer(family):
    length, consumed = cells_for(family, 300, 25)
    bundle = generate_world(12, family, length, consumed, family_depth(family), 2)
    for task in bundle["tasks"]:
        for row_id in task["necessary"]:
            assert answer_value(
                answer_after_drop(bundle, task, row_id)
            ) != answer_value(task["answer"])


@pytest.mark.parametrize("family", FAMILIES)
def test_deleting_a_distractor_does_not_change_the_answer(family):
    length, consumed = cells_for(family, 300, 25)
    bundle = generate_world(12, family, length, consumed, family_depth(family), 2)
    for task in bundle["tasks"]:
        consumed = set(task["consumed"])
        distractor = next(
            row["id"]
            for row in data_rows(bundle)
            if row["id"] not in consumed and row["type"] in ("record", "event")
        )
        assert answer_value(
            answer_after_drop(bundle, task, distractor)
        ) == answer_value(task["answer"])


@pytest.mark.parametrize("family", FAMILIES)
def test_declared_necessity_partition_must_match_the_measurement(family):
    """A padded or hand-edited `necessary` list would make the check vacuous."""
    length, consumed = cells_for(family, 300, 25)
    bundle = generate_world(24, family, length, consumed, family_depth(family), 2)
    task = bundle["tasks"][0]
    task["necessary"] = []
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert (
        "the declared sensitivity partition does not match the measurement"
        in result["errors"]
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_declared_provenance_must_match_the_executor(family):
    length, consumed = cells_for(family, 300, 25)
    bundle = generate_world(24, family, length, consumed, family_depth(family), 2)
    task = bundle["tasks"][0]
    distractor = next(
        row["id"]
        for row in data_rows(bundle)
        if row["type"] in ("record", "event") and row["id"] not in set(task["consumed"])
    )
    task["consumed"] = sorted([*task["consumed"], distractor])
    task["consumed_count"] = len(task["consumed"])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "consumed provenance disagrees with the executor" in result["errors"]


@pytest.mark.parametrize("family", FAMILIES)
def test_window_ablation_is_a_measured_fraction_of_half_context_windows(family):
    length, consumed = cells_for(family, 400, 25)
    bundle = generate_world(13, family, length, consumed, family_depth(family), 2)
    ablation = window_ablation(bundle)
    assert ablation == validate_bundle(bundle)["checks"]["window_ablation"]
    assert ablation["windows_attempted"] > 0
    assert 0.0 <= ablation["derivable_fraction"] <= 1.0
    assert ablation["windows_matching_gold"] == round(
        ablation["derivable_fraction"] * ablation["windows_attempted"]
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_each_family_carries_at_least_eight_structural_answer_shapes(family):
    shapes = Counter(
        shape_of(task["answer"])
        for seed in range(1, 41)
        for task in generate_world(
            seed, family, *cells_for(family, 400, 40), family_depth(family), 2
        )["tasks"]
    )
    assert len(shapes) >= 8, shapes.most_common(3)


@pytest.mark.parametrize("family", FAMILIES)
def test_padding_volume_grows_with_l_and_not_with_k(family):
    small = generate_world(
        19, family, *cells_for(family, 200, 20), family_depth(family), 2
    )
    large = generate_world(
        19, family, *cells_for(family, 800, 20), family_depth(family), 2
    )
    assert (
        large["length_accounting"]["padding_rows"]
        > small["length_accounting"]["padding_rows"]
    )
    if family in ("set_complete", "dense_aggregate"):
        # A scope reads every rendered row (set_complete) or the region is a
        # band-gated share of L (dense_aggregate, whose shared cells rescale
        # K with L): the consumed count grows with L itself, so the
        # invariance the other families enjoy cannot hold here, and the
        # honest claim is only that each task's consumed set never shrinks.
        for small_task, large_task in zip(small["tasks"], large["tasks"]):
            assert large_task["consumed_count"] >= small_task["consumed_count"]
        return
    assert (
        large["length_accounting"]["consumed_rows_per_task"]
        == small["length_accounting"]["consumed_rows_per_task"]
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_rejects_k_that_cannot_fit_in_l(family):
    if family == "dense_aggregate":
        # The band gate, not the disjoint-budget gate, is what rejects a
        # dense cell: K below the floor (0.1) and K above the cap (5.0) both
        # fail loudly, and the shared plan gate is untouched for the rest.
        with pytest.raises(ValueError, match="K/L in"):
            generate_world(1, family, 200, 20, 1, 2)
        with pytest.raises(ValueError, match="K/L in"):
            generate_world(1, family, 40, 200, 1, 2)
        assert plan_variants(3200, 200, 6) >= 2
        # Below the floor (0.25) rejects; inside the band (0.6) the shared
        # region hosts every requested variant.
        assert plan_variants(3200, 800, 6, family) == 0
        assert plan_variants(3200, 1920, 6, family) == 6
        return
    with pytest.raises(ValueError):
        generate_world(1, family, 200, 20, 2, 8)
    with pytest.raises(ValueError):
        generate_world(1, family, 40, 200, family_depth(family), 2)
    assert plan_variants(3200, 200, 6) >= 2


@pytest.mark.parametrize("length_records", [0, 16, -5])
def test_rejects_undersized_world(length_records):
    with pytest.raises(ValueError):
        generate_world(1, "alias_locate", length_records, 20, 2, 2)


@pytest.mark.parametrize("depth", [0, 3, -1])
def test_rejects_out_of_range_depth(depth):
    with pytest.raises(ValueError):
        generate_world(1, "alias_locate", 200, 20, depth, 2)


def test_rejects_an_unknown_family():
    with pytest.raises(ValueError):
        generate_world(1, "not_a_family", **SMALL)


# --------------------------------------------------------------------------
# F1 alias_locate: the binding, not the category, is the discriminator
# --------------------------------------------------------------------------


def test_alias_swapping_two_declarations_flips_the_answer():
    for seed in (1, 4, 9):
        bundle = generate_world(seed, "alias_locate", 300, 25, 2, 2)
        lines = bundle["context"].splitlines()
        by_id = {json.loads(line)["id"]: json.loads(line) for line in lines[1:]}
        for task in bundle["tasks"]:
            first, second = task["intervention"]["rows"]
            assert task["intervention"]["kind"] == "alias_swap"
            swapped = []
            for line in lines[1:]:
                row = json.loads(line)
                if row["id"] == first:
                    row["entity"] = by_id[second]["entity"]
                elif row["id"] == second:
                    row["entity"] = by_id[first]["entity"]
                swapped.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
            context = "\n".join([lines[0], *swapped])
            assert solved(context, task["question"]) != task["answer"]


def test_alias_binding_is_drawn_per_world_and_not_a_fixed_map():
    bindings = []
    for seed in range(12):
        bundle = generate_world(seed, "alias_locate", 200, 20, 2, 1)
        held = {}
        for row in data_rows(bundle):
            if row["type"] == "alias":
                held.setdefault(row["alias"], set()).add(row["entity"])
        # No handle is bound to more than one entity inside a world.
        assert all(len(entities) == 1 for entities in held.values())
        bindings.append(
            frozenset(
                (handle, next(iter(entities))) for handle, entities in held.items()
            )
        )
    # The handle -> entity maps differ per world: a fixed map would let a reader
    # learn the binding instead of resolving it.
    assert len(set(bindings)) > 1
    assert all(bindings)


def test_every_alias_task_has_a_same_category_negative():
    bundle = generate_world(9, "alias_locate", 300, 25, 2, 2)
    for task in bundle["tasks"]:
        entity = None
        alias = task["question"]["steps"][0]["alias"]
        for row in data_rows(bundle):
            if row["type"] == "alias" and row["alias"] == alias:
                entity = row["entity"]
        assert entity is not None
        # At least one other entity carries rows the program's filter admits, so
        # dropping the binding step would not narrow the relation.
        category = task["queried_category"]
        others = [
            row
            for row in data_rows(bundle)
            if row["type"] == "record"
            and row["entity"] != entity
            and row["category"] == category
        ]
        assert others, task["task_id"]


def test_a_locating_task_can_answer_with_a_declared_empty_set():
    """An empty match is a declared state of the world, not a missing row."""
    empties = 0
    for seed in range(1, 40):
        bundle = generate_world(seed, "alias_locate", 300, 25, 2, 2)
        assert validate_bundle(bundle)["passed"]
        for task in bundle["tasks"]:
            if task["question"]["steps"][-1]["op"] != "locate_empty":
                continue
            empties += 1
            assert set(task["answer"]) == {"alias", "entity", "ids", "count", "empty"}
            assert task["answer"]["empty"] is True
            assert task["answer"]["ids"] == []
            assert task["answer"]["count"] == 0
            # The declaration is enforced: a world where rows matched would make
            # this query malformed rather than empty.
            assert task["consumed"] == [
                row_id for row_id in task["consumed"] if row_id in set(task["consumed"])
            ]
    assert empties >= 1


def test_a_declared_empty_query_rejects_a_world_where_rows_match():
    bundle = generate_world(3, "alias_locate", 300, 25, 2, 2)
    empty_task = next(
        task
        for task in bundle["tasks"]
        if task["question"]["steps"][-1]["op"] == "locate_empty"
    )
    # Point the query at the category the bound entity does carry.
    entity = next(
        row["entity"]
        for row in data_rows(bundle)
        if row["type"] == "alias"
        and row["alias"] == empty_task["question"]["steps"][0]["alias"]
    )
    carried = next(
        row["category"]
        for row in data_rows(bundle)
        if row["type"] == "record" and row["entity"] == entity
    )
    doctored = copy.deepcopy(empty_task["question"])
    doctored["steps"][1]["conditions"][0]["value"] = carried
    with pytest.raises(ValueError, match="empty match"):
        solve_visible(bundle["context"], doctored)


def test_alias_provenance_is_the_declaration_plus_the_matched_rows():
    bundle = generate_world(11, "alias_locate", 300, 25, 2, 2)
    by_id = {row["id"]: row for row in data_rows(bundle)}
    for task in bundle["tasks"]:
        declarations = [
            row_id for row_id in task["consumed"] if by_id[row_id]["type"] == "alias"
        ]
        assert len(declarations) == 1
        entity = by_id[declarations[0]]["entity"]
        matched = [row_id for row_id in task["consumed"] if row_id not in declarations]
        assert matched
        assert all(
            by_id[row_id]["type"] == "record" and by_id[row_id]["entity"] == entity
            for row_id in matched
        )


# --------------------------------------------------------------------------
# F3 asof_state: event time and reveal time are separate
# --------------------------------------------------------------------------


def test_changing_a_reveal_time_flips_the_as_of_answer():
    for seed in (2, 5, 8):
        bundle = generate_world(seed, "asof_state", 300, 25, 2, 2)
        lines = bundle["context"].splitlines()
        for task in bundle["tasks"]:
            assert task["intervention"]["kind"] == "reveal_flip"
            row_id = task["intervention"]["row"]
            moved = []
            for line in lines[1:]:
                row = json.loads(line)
                if row["id"] == row_id:
                    assert row["reveal"] == task["intervention"]["before"]
                    row["reveal"] = task["intervention"]["after"]
                moved.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
            context = "\n".join([lines[0], *moved])
            assert solved(context, task["question"]) != task["answer"]


def test_a_hold_row_is_decisive_for_every_task():
    bundle = generate_world(6, "asof_state", 300, 25, 2, 2)
    checks = validate_bundle(bundle)["checks"]
    assert checks["tasks_carrying_a_decisive_hold_row"] == len(bundle["tasks"])
    by_id = {row["id"]: row for row in data_rows(bundle)}
    for task in bundle["tasks"]:
        assert any(
            by_id[row_id]["kind"] in ("set_aside", "release")
            for row_id in task["necessary"]
        )


def test_reveal_order_and_event_order_disagree_about_the_state():
    """The cap on `release` is what makes the two timestamps load-bearing."""
    for seed in (1, 4, 9):
        bundle = generate_world(seed, "asof_state", 300, 25, 2, 2)
        checks = validate_bundle(bundle)["checks"]
        assert checks["reveal_order_state_disagreements"] > 0


def test_events_revealed_after_the_query_date_are_not_evidence():
    bundle = generate_world(3, "asof_state", 300, 25, 1, 2)
    by_id = {row["id"]: row for row in data_rows(bundle)}
    for task in bundle["tasks"]:
        cutoff = task["question"]["steps"][0]["reveal"]
        for row_id in task["consumed"]:
            assert by_id[row_id]["reveal"] <= cutoff


def test_as_of_answers_carry_their_query_date():
    for seed in range(6):
        bundle = generate_world(seed, "asof_state", 200, 20, 2, 1)
        for task in bundle["tasks"]:
            steps = task["question"]["steps"]
            assert (
                task["answer"]["as_of"] == steps[-2 if len(steps) == 3 else 0]["reveal"]
            )


# --------------------------------------------------------------------------
# F7 rule_holdout: structural holdout
# --------------------------------------------------------------------------


def test_the_two_rule_families_are_structural_not_coefficient_variants():
    seen = set()
    for seed in range(12):
        bundle = generate_world(seed, "rule_holdout", 200, 20, 2, 2)
        header = json.loads(bundle["context"].splitlines()[0])
        seen.add(header["rule_family"])
        assert header["rule_structure"] == RULE_STRUCTURES[header["rule_family"]]
        assert bundle["rule"]["rule_family"] == header["rule_family"]
        assert bundle["rule"]["hypotheses_enumerated"] >= 4
    assert seen == set(RULE_FAMILIES)


def test_holdout_plan_puts_one_rule_family_in_train_and_the_other_in_eval():
    for trained in RULE_FAMILIES:
        plan = holdout_plan(trained)
        assert plan["train_rule_families"] == [trained]
        assert set(plan["eval_rule_families"]) == set(RULE_FAMILIES) - {trained}
        assert plan["basis"] == "rule_structure"
        assert split_for_rule_family(trained, trained) == "train"
        held_out = next(name for name in RULE_FAMILIES if name != trained)
        assert split_for_rule_family(held_out, trained) == "eval"
    with pytest.raises(ValueError):
        holdout_plan("not_a_rule_family")


def test_an_eval_family_bundle_is_generated_by_its_own_family_and_is_seed_disjoint():
    """The untrained-family evaluation uses the held-out family's own contexts."""
    trained = RULE_FAMILIES[0]
    held_out = RULE_FAMILIES[1]
    assert split_for_rule_family(held_out, trained) == "eval"
    bundle = generate_world(3, "rule_holdout", 200, 20, 2, 1)
    header = json.loads(bundle["context"].splitlines()[0])
    if header["rule_family"] != held_out:
        bundle = generate_world(2, "rule_holdout", 200, 20, 2, 1)
        header = json.loads(bundle["context"].splitlines()[0])
    assert header["rule_family"] == held_out
    assert bundle["rule"]["holdout"]["trained_rule_family"] == held_out


def test_a_program_from_the_other_rule_family_is_rejected():
    """A context generated under one structure cannot answer the other."""
    by_family = {}
    for seed in range(8):
        bundle = generate_world(seed, "rule_holdout", 200, 20, 2, 1)
        family = json.loads(bundle["context"].splitlines()[0])["rule_family"]
        by_family.setdefault(family, bundle)
    assert set(by_family) == set(RULE_FAMILIES)
    first, second = (by_family[name] for name in RULE_FAMILIES)
    assert first["context"] != second["context"]
    with pytest.raises(ValueError):
        solve_visible(first["context"], second["tasks"][0]["question"])
    with pytest.raises(ValueError):
        solve_visible(second["context"], first["tasks"][0]["question"])


def test_demonstrations_do_not_include_the_query_entities_feature_points():
    for seed in range(1, 20):
        bundle = generate_world(seed, "rule_holdout", 300, 25, 2, 2)
        rows = data_rows(bundle)
        header = json.loads(bundle["context"].splitlines()[0])
        modular = header["modulus"]
        rule_family = header["rule_family"]
        queried = {
            entity
            for task in bundle["tasks"]
            for entity in (
                [task["question"]["steps"][-1].get("entity")]
                if task["question"]["steps"][-1]["op"] in ("label", "verify")
                else task["question"]["steps"][-1].get("entities") or []
            )
        }
        # The unseen-feature claim is about the aggregate the rule actually
        # reads, which differs per rule family: threshold_class reads the
        # entity's reduced coordinate point, parity_vote reads its (row count,
        # counted residue population) vote. No queried entity's signature of
        # either kind may be one the demonstrations publish. Individual record
        # rows may well repeat a demonstrated point.
        residue = (
            bundle["rule"]["parameters"][0] if rule_family == "parity_vote" else None
        )
        signatures = set()
        for row in rows:
            if row["type"] != "demo":
                continue
            points = [tuple(point) for point in row["points"]]
            signatures.add(
                (
                    sum(x for x, _ in points) % modular,
                    sum(y for _, y in points) % modular,
                )
                if residue is None
                else (len(points), sum((x + y) % modular == residue for x, y in points))
            )
        for entity in queried:
            entity_rows = [row for row in rows if row["entity"] == entity]
            assert entity_rows
            signature = (
                (
                    sum(row["x"] for row in entity_rows) % modular,
                    sum(row["y"] for row in entity_rows) % modular,
                )
                if residue is None
                else (
                    len(entity_rows),
                    sum(
                        (row["x"] + row["y"]) % modular == residue
                        for row in entity_rows
                    ),
                )
            )
            assert signature not in signatures


def test_rule_labels_are_not_a_frozen_majority_class():
    for seed in range(1, 20):
        bundle = generate_world(seed, "rule_holdout", 300, 25, 2, 2)
        for task in bundle["tasks"]:
            assert validate_bundle(bundle)["checks"]["labels:" + task["task_id"]] >= 2


def test_removing_every_demonstration_changes_the_answer():
    bundle = generate_world(7, "rule_holdout", 300, 25, 2, 2)
    lines = bundle["context"].splitlines()
    kept = [line for line in lines[1:] if json.loads(line)["type"] != "demo"]
    reduced = "\n".join([lines[0], *kept])
    assert len(kept) < len(lines) - 1
    for task in bundle["tasks"]:
        with pytest.raises(ValueError):
            solve_visible(reduced, task["question"])


def test_a_doctored_demonstration_is_rejected_by_re_inference():
    bundle = generate_world(5, "rule_holdout", 200, 20, 2, 2)
    lines = bundle["context"].splitlines()
    demo = next(
        index
        for index, line in enumerate(lines)
        if index and json.loads(line)["type"] == "demo"
    )
    row = json.loads(lines[demo])
    row["label"] = "label_not_declared"
    lines[demo] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    bundle["context"] = "\n".join(lines)
    assert not validate_bundle(bundle)["passed"]


def test_a_declared_rule_that_the_demonstrations_do_not_identify_is_rejected():
    bundle = generate_world(5, "rule_holdout", 200, 20, 2, 2)
    bundle["rule"]["parameters"] = [1, 1, 1, 1]
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert (
        "the visible demonstrations do not identify the declared rule"
        in result["errors"]
    )


def test_rule_worlds_carry_a_structural_holdout_plan():
    for seed in range(6):
        bundle = generate_world(seed, "rule_holdout", 200, 20, 2, 1)
        rule_family = bundle["rule"]["rule_family"]
        assert bundle["rule"]["holdout"] == holdout_plan(rule_family)
        assert bundle["rule"]["holdout"]["eval_rule_families"]


# --------------------------------------------------------------------------
# F2 set_complete: gold is the exhaustive membership
# --------------------------------------------------------------------------


def data_rows_as_rows(bundle):
    """The visible rows re-parsed into Row objects for the family's own helpers."""
    from longworld.synthesis.capability_families import Row

    return [
        Row(
            id=row["id"],
            type=row["type"],
            entity=row["entity"],
            memo=row["memo"],
            amount=row.get("amount"),
            category=row.get("category"),
            day=row.get("date"),
            alias=row.get("alias"),
            kind=row.get("kind"),
            reveal=row.get("reveal"),
            x=row.get("x"),
            y=row.get("y"),
            points=tuple(tuple(p) for p in row["points"])
            if row.get("points")
            else None,
            label=row.get("label"),
        )
        for row in data_rows(bundle)
    ]


def test_gold_is_the_executor_computed_full_set_over_the_visible_world():
    from longworld.synthesis.capability_families import _set_members, _set_steps

    for seed in range(1, 20):
        bundle = generate_world(seed, "set_complete", 300, 25, 1, 2)
        assert validate_bundle(bundle)["passed"]
        for task in bundle["tasks"]:
            members = _set_members(
                data_rows_as_rows(bundle), _set_steps(task["question"])
            )
            ids = sorted(row.id for row in members)
            terminal = task["question"]["steps"][-1]
            answer = task["answer"]
            if terminal["op"] == "set_list":
                shown = (
                    answer
                    if isinstance(answer, list)
                    else answer.get("ids")
                    or sorted(
                        one for group in answer["entities"].values() for one in group
                    )
                )
                assert sorted(shown) == ids
            elif terminal["op"] == "set_count":
                count = answer if isinstance(answer, int) else answer["count"]
                assert count == len(ids)
            elif terminal["op"] == "set_contains":
                verdict = answer if isinstance(answer, bool) else answer["contains"]
                assert verdict == (terminal["id"] in set(ids))
            else:
                missing = answer if isinstance(answer, list) else answer["missing"]
                assert sorted(missing) == sorted(
                    row_id for row_id in terminal["ids"] if row_id not in set(ids)
                )


def test_every_member_is_individually_decisive_miss_item_is_wrong():
    """Removing any member changes the answer: a missing item is a wrong answer."""
    for seed in range(1, 12):
        bundle = generate_world(seed, "set_complete", 300, 25, 1, 2)
        for task in bundle["tasks"]:
            terminal = task["question"]["steps"][-1]
            if terminal["op"] in ("set_contains", "set_missing"):
                continue
            # task["necessary"] is the measured decisive set; for set answers
            # it must cover the whole membership.
            assert set(task["necessary"]) == set(task["consumed"])
            for row_id in task["necessary"]:
                assert answer_value(
                    answer_after_drop(bundle, task, row_id)
                ) != answer_value(task["answer"])


def test_inserting_a_legal_hit_into_the_world_flips_the_answer():
    """The insert-hit intervention: a legal hit in an uncovered region must
    enter the answer once repaired into the scope."""
    for seed in range(1, 12):
        bundle = generate_world(seed, "set_complete", 300, 25, 1, 2)
        lines = bundle["context"].splitlines()
        by_id = {json.loads(line)["id"]: json.loads(line) for line in lines[1:]}
        for task in bundle["tasks"]:
            assert task["intervention"]["kind"] == "insert_hit"
            insert_id = task["intervention"]["row"]
            row = by_id[insert_id]
            # The row as rendered is outside the scope (a previously uncovered
            # region), and repairing it inside must change the answer.
            bounds = {
                (c["field"], c["op"]): c["value"]
                for c in task["intervention"]["conditions"]
            }
            moved = []
            for line in lines[1:]:
                item = json.loads(line)
                if item["id"] == insert_id:
                    day = item.get("date")
                    low = bounds.get(("date", ">="))
                    if low is not None and day < low:
                        day = low
                    high = bounds.get(("date", "<="))
                    if high is not None and day > high:
                        day = high
                    amount = item.get("amount")
                    floor = bounds.get(("amount", ">="))
                    if floor is not None and amount < floor:
                        amount = floor
                    ceil = bounds.get(("amount", "<="))
                    if ceil is not None and amount > ceil:
                        amount = ceil
                    category = item.get("category")
                    wanted = bounds.get(("category", "=="))
                    if wanted is not None and category != wanted:
                        category = wanted
                    item = {**item, "date": day, "amount": amount, "category": category}
                moved.append(json.dumps(item, sort_keys=True, separators=(",", ":")))
            context = "\n".join([lines[0], *moved])
            question = task["question"]
            assert solved(context, question) != task["answer"]


def test_sibling_distractor_removal_does_not_change_the_answer():
    for seed in range(1, 12):
        bundle = generate_world(seed, "set_complete", 300, 25, 1, 2)
        for task in bundle["tasks"]:
            consumed = set(task["consumed"])
            # A same-schema row the task does not consume: a sibling, the
            # insert row of another task, or padding. Removing it must not
            # move the membership answer.
            outside = [
                row["id"] for row in data_rows(bundle) if row["id"] not in consumed
            ]
            for row_id in outside[:8]:
                assert answer_value(
                    answer_after_drop(bundle, task, row_id)
                ) == answer_value(task["answer"]), (seed, task["task_id"], row_id)


def test_set_complete_rejects_depth_above_one():
    with pytest.raises(ValueError, match="H=1"):
        generate_world(1, "set_complete", 200, 20, 2, 2)


def test_answer_is_sorted_canonical_regardless_of_presentation():
    unordered_seen = ordered_seen = 0
    for seed in range(1, 60):
        bundle = generate_world(seed, "set_complete", 300, 25, 1, 2)
        for task in bundle["tasks"]:
            terminal = task["question"]["steps"][-1]
            if terminal["op"] != "set_list":
                continue
            answer = task["answer"]
            ids = answer if isinstance(answer, list) else answer.get("ids")
            if ids is None:
                ids = sorted(
                    one for group in answer["entities"].values() for one in group
                )
            assert ids == sorted(ids)
            if task["question"]["unordered"]:
                unordered_seen += 1
            else:
                ordered_seen += 1
    assert unordered_seen >= 10
    assert ordered_seen >= 10


def test_set_complete_prompt_pool_is_pinned():
    from longworld.synthesis.capability_families import PROMPTS as P

    assert len(P["set_complete"]) >= 24
    assert len(set(P["set_complete"])) == len(P["set_complete"])


def test_set_complete_instruction_tamper_is_rejected():
    bundle = generate_world(5, "set_complete", 200, 20, 1, 2)
    bundle["tasks"][0]["instruction"] = "Answer with the first rows you see."
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "instruction does not match the registered contract" in result["errors"]


def test_set_complete_declared_necessity_tamper_is_rejected():
    bundle = generate_world(24, "set_complete", 300, 25, 1, 2)
    bundle["tasks"][0]["necessary"] = []
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert (
        "the declared sensitivity partition does not match the measurement"
        in result["errors"]
    )


def test_set_complete_intervention_tamper_is_rejected():
    bundle = generate_world(24, "set_complete", 300, 25, 1, 2)
    # Point the declared insert at a member row: the repair cannot change
    # anything and the validator must reject the flip claim.
    task = bundle["tasks"][0]
    task["intervention"]["row"] = task["consumed"][0]
    result = validate_bundle(bundle)
    assert not result["passed"]


# --------------------------------------------------------------------------
# F8 dense_aggregate: per-fragment judgment, then global aggregation
# --------------------------------------------------------------------------


def dense_bundle(seed=1, length=300, consumed=180, variants=2):
    return generate_world(seed, "dense_aggregate", length, consumed, 1, variants)


def test_dense_region_is_the_shared_consumed_set_of_every_task():
    for seed in range(1, 8):
        bundle = dense_bundle(seed)
        regions = [set(task["consumed"]) for task in bundle["tasks"]]
        # One region, shared by every task: density is a world property.
        assert all(region == regions[0] for region in regions)
        assert regions[0]
        by_id = {row["id"]: row for row in data_rows(bundle)}
        region = regions[0]
        # The boundary is coarse: every region row is a same-schema record,
        # and the region is declared by dates and a category slice, never by
        # ids the program could name.
        assert all(by_id[row_id]["type"] == "record" for row_id in region)
        steps = bundle["tasks"][0]["question"]["steps"][0]
        fields = {condition["field"] for condition in steps["conditions"]}
        assert fields <= {"category", "date"}


def test_dense_measured_density_stays_inside_the_declared_band():
    from longworld.synthesis.capability_families import (
        DENSE_CAP_FRACTION,
        DENSE_FLOOR_FRACTION,
    )

    for seed in range(1, 20):
        bundle = dense_bundle(seed)
        for density in bundle["length_accounting"]["density_per_task"]:
            assert DENSE_FLOOR_FRACTION <= density <= DENSE_CAP_FRACTION, (
                seed,
                density,
            )
        for task in bundle["tasks"]:
            assert (
                DENSE_FLOOR_FRACTION
                <= task["consumed_count"] / bundle["length_records"]
                <= DENSE_CAP_FRACTION
            )


def test_every_in_region_row_is_individually_decisive():
    """Removing any region row changes the aggregate: no skippable row."""
    for seed in range(1, 10):
        bundle = dense_bundle(seed)
        for task in bundle["tasks"]:
            assert set(task["necessary"]) == set(task["consumed"])
            for row_id in task["necessary"]:
                assert answer_value(
                    answer_after_drop(bundle, task, row_id)
                ) != answer_value(task["answer"])


def test_flipping_one_region_rows_class_moves_the_aggregate():
    for seed in range(1, 12):
        bundle = dense_bundle(seed)
        lines = bundle["context"].splitlines()
        for task in bundle["tasks"]:
            assert task["intervention"]["kind"] == "class_flip"
            row_id = task["intervention"]["row"]
            moved = []
            for line in lines[1:]:
                row = json.loads(line)
                if row["id"] == row_id:
                    assert row["amount"] == task["intervention"]["amount_before"]
                    row["amount"] = task["intervention"]["amount_after"]
                moved.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
            context = "\n".join([lines[0], *moved])
            assert solved(context, task["question"]) != task["answer"]


def test_out_of_region_rows_are_stable_distractors():
    for seed in range(1, 12):
        bundle = dense_bundle(seed)
        for task in bundle["tasks"]:
            consumed = set(task["consumed"])
            outside = [
                row["id"] for row in data_rows(bundle) if row["id"] not in consumed
            ]
            assert outside, "the world carries no out-of-region exposure"
            for row_id in outside[:8]:
                assert answer_value(
                    answer_after_drop(bundle, task, row_id)
                ) == answer_value(task["answer"]), (seed, task["task_id"], row_id)


def test_both_classes_are_present_under_every_judgment():
    from longworld.synthesis.capability_families import (
        _dense_region,
        _dense_steps,
        _holds,
    )

    for seed in range(1, 12):
        bundle = dense_bundle(seed)
        rows = data_rows_as_rows(bundle)
        for task in bundle["tasks"]:
            judgment = task["question"]["steps"][0]["judgment"]
            region = _dense_region(rows, _dense_steps(task["question"]))
            passing = sum(
                all(_holds(row, condition) for condition in judgment) for row in region
            )
            assert 0 < passing < len(region), (seed, task["task_id"])


def test_dense_answers_are_sorted_and_carry_both_counts():
    from longworld.synthesis.capability_families import (
        _dense_region,
        _dense_steps,
        _holds,
    )

    for seed in range(1, 12):
        bundle = dense_bundle(seed)
        rows = data_rows_as_rows(bundle)
        for task in bundle["tasks"]:
            answer = task["answer"]
            region = _dense_region(rows, _dense_steps(task["question"]))
            assert isinstance(answer, dict), terminal_shape(task)
            if "failing" in answer:
                assert answer["failing"] == sorted(answer["failing"])
            if "passing" in answer:
                assert answer["passing"] == sorted(answer["passing"])
                assert len(answer["passing"]) + len(answer["failing"]) == len(region)
            if "total" in answer:
                assert answer["total"] == len(region)
            if "first" in answer:
                # The compound's first failing id is the smallest id the
                # judgment fails over the region.
                failing = sorted(
                    row.id
                    for row in region
                    if not all(
                        _holds(row, condition)
                        for condition in task["question"]["steps"][0]["judgment"]
                    )
                )
                assert answer["first"] == failing[0]
                assert answer["fail"] == len(failing)
            if "pass" in answer:
                assert answer["pass"] + answer["fail"] == len(region)


def terminal_shape(task):
    terminal = task["question"]["steps"][-1]
    return (terminal["op"], terminal.get("shape"))


def test_dense_aggregate_rejects_depth_above_one():
    with pytest.raises(ValueError, match="H=1"):
        generate_world(1, "dense_aggregate", 300, 180, 2, 2)


def test_dense_out_of_band_cells_are_rejected_loudly():
    with pytest.raises(ValueError, match="K/L in"):
        generate_world(1, "dense_aggregate", 200, 20, 1, 2)
    with pytest.raises(ValueError, match="K/L in"):
        generate_world(1, "dense_aggregate", 40, 200, 1, 2)


def test_dense_prompt_pool_is_pinned():
    from longworld.synthesis.capability_families import PROMPTS as P

    assert len(P["dense_aggregate"]) >= 24
    assert len(set(P["dense_aggregate"])) == len(P["dense_aggregate"])


def test_dense_instruction_tamper_is_rejected():
    bundle = dense_bundle(5, 200, 100)
    bundle["tasks"][0]["instruction"] = "Count the rows that look like failures."
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "instruction does not match the registered contract" in result["errors"]


def test_dense_declared_necessity_tamper_is_rejected():
    bundle = dense_bundle(24)
    bundle["tasks"][0]["necessary"] = []
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert (
        "the declared sensitivity partition does not match the measurement"
        in result["errors"]
    )


def test_dense_intervention_tamper_is_rejected():
    bundle = dense_bundle(24)
    # Point the declared flip at a row whose amount does not match the
    # declaration: the replay must refuse the flip claim.
    task = bundle["tasks"][0]
    task["intervention"]["row"] = next(
        row["id"]
        for row in data_rows(bundle)
        if row["id"] != task["intervention"]["row"]
    )
    result = validate_bundle(bundle)
    assert not result["passed"]


def test_dense_padding_rows_never_join_the_region():
    """The measured region equals the built region exactly: padding placement
    cannot drift the density by accidentally landing inside the boundary."""
    from longworld.synthesis.capability_families import (
        _dense_region,
        _dense_steps,
        _holds,
    )

    for seed in range(1, 12):
        bundle = dense_bundle(seed, 400, 240)
        rows = data_rows_as_rows(bundle)
        header = json.loads(bundle["context"].splitlines()[0])
        for task in bundle["tasks"]:
            region = _dense_region(rows, _dense_steps(task["question"]))
            assert len(region) == task["consumed_count"]
        # Every out-of-region row is excluded by the boundary, not by absence.
        steps = _dense_steps(bundle["tasks"][0]["question"])[0]
        for row in rows:
            if row.id in set(bundle["tasks"][0]["consumed"]):
                continue
            assert not all(_holds(row, condition) for condition in steps["conditions"])
        assert header["family"] == "dense_aggregate"
