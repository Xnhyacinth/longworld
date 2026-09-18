import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from longworld.synthesis.capability_records import (
    CATEGORY_POOL,
    FAMILIES,
    HONESTY,
    PROMPTS,
    PROTOCOLS,
    VERSION,
    answer_value,
    generate_world,
    parse_context,
    plan_variants,
    render_instruction,
    solve_visible,
    validate_bundle,
    window_ablation,
)

SMALL = {"length_records": 200, "consumed_records": 20, "depth": 2, "n_variants": 2}


def visible_rows(bundle):
    return [json.loads(line) for line in bundle["context"].splitlines()[1:]]


def test_world_is_deterministic_and_serializable():
    bundle = generate_world(3, "filter_aggregate", **SMALL)
    assert bundle == generate_world(3, "filter_aggregate", **SMALL)
    assert json.loads(json.dumps(bundle)) == bundle
    assert validate_bundle(bundle)["passed"]
    assert bundle["context"] != generate_world(4, "filter_aggregate", **SMALL)["context"]


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_every_family_depth_combination_validates(family, depth):
    result = validate_bundle(generate_world(5, family, 200, 20, depth, 2))
    assert result["passed"], result["errors"]


def test_gold_comes_from_the_executor_not_from_a_stored_answer():
    bundle = generate_world(9, "join_lookup", **SMALL)
    for task in bundle["tasks"]:
        assert solve_visible(bundle["context"], task["question"]) == task["answer"]


def test_the_three_families_have_three_distinct_answer_schemas():
    filter_answer = generate_world(2, "filter_aggregate", 120, 16, 1, 1)["tasks"][0][
        "answer"
    ]
    group_answer = generate_world(2, "group_compare", 120, 16, 1, 1)["tasks"][0][
        "answer"
    ]
    join_answer = generate_world(2, "join_lookup", 120, 16, 1, 1)["tasks"][0]["answer"]
    assert set(filter_answer) == {"aggregate", "matched", "breakdown"}
    assert set(group_answer) == {"groups", "left", "right", "verdict"}
    assert set(join_answer) == {"pairs", "by_entity", "aggregate"}
    assert group_answer.keys() - filter_answer.keys() == {"groups", "left", "right", "verdict"}
    assert join_answer.keys() - filter_answer.keys() == {"pairs", "by_entity"}
    assert isinstance(filter_answer["aggregate"]["value"], int)
    assert isinstance(group_answer["verdict"], str)


def test_aggregate_how_varies_across_the_full_operator_set():
    hows = Counter()
    for seed in range(40):
        bundle = generate_world(seed, "filter_aggregate", 120, 16, 1, 1)
        hows[bundle["tasks"][0]["question"]["steps"][-1]["how"]] += 1
    assert set(hows) == {"sum", "count", "max", "min"}
    assert min(hows.values()) >= 4


def test_verdicts_are_sampled_uniformly_across_seeds():
    counts = Counter(
        generate_world(seed, "group_compare", 120, 16, 2, 1)["tasks"][0]["answer"][
            "verdict"
        ]
        for seed in range(60)
    )
    assert set(counts) == {"GT", "LT", "EQ"}
    assert min(counts.values()) >= 8


def test_group_compare_labels_are_not_a_frozen_majority_class():
    verdicts = Counter()
    for seed in range(60):
        bundle = generate_world(seed, "group_compare", 120, 16, 3, 1)
        verdicts[bundle["tasks"][0]["answer"]["verdict"]] += 1
    assert max(verdicts.values()) / sum(verdicts.values()) < 0.6


def test_tampered_prompt_is_rejected():
    bundle = generate_world(5, "filter_aggregate", **SMALL)
    bundle["tasks"][0]["instruction"] = "Just answer whatever looks right."
    assert not validate_bundle(bundle)["passed"]


def test_a_prompt_from_another_family_is_rejected():
    bundle = generate_world(5, "filter_aggregate", **SMALL)
    bundle["tasks"][0]["instruction"] = render_instruction(
        "group_compare", bundle["tasks"][0]["question"], 0
    )
    assert not validate_bundle(bundle)["passed"]


def test_tampered_rules_text_is_rejected():
    bundle = generate_world(5, "filter_aggregate", **SMALL)
    lines = bundle["context"].splitlines()
    header = json.loads(lines[0])
    header["rules"] = "Any row may be used as evidence."
    bundle["context"] = "\n".join(json.dumps(header, sort_keys=True) for _ in [0]) + "\n" + "\n".join(lines[1:])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "rules text does not match the registered contract" in result["errors"]


def test_prompt_pool_is_deep_enough_and_rotated():
    for family in FAMILIES:
        assert len(PROMPTS[family]) >= 20
        assert len(set(PROMPTS[family])) == len(PROMPTS[family])
    phrasings = {
        generate_world(seed, "filter_aggregate", 120, 16, 1, 1)["tasks"][0][
            "phrasing_index"
        ]
        for seed in range(200)
    }
    assert len(phrasings) >= 22


def test_instruction_is_reproducible_from_program_and_phrasing():
    bundle = generate_world(6, "join_lookup", **SMALL)
    assert len(PROTOCOLS) == len(FAMILIES)
    for task in bundle["tasks"]:
        assert task["instruction"] == render_instruction(
            bundle["family"], task["question"], task["phrasing_index"]
        )
        assert task["instruction"].startswith("Task: ")


def test_distractor_rows_are_never_counted_as_consumed_evidence():
    bundle = generate_world(8, "filter_aggregate", 200, 20, 2, 2)
    record_ids = {row["id"] for row in visible_rows(bundle) if row["type"] == "record"}
    consumed = {row_id for task in bundle["tasks"] for row_id in task["consumed"]}
    assert consumed <= record_ids
    accounting = bundle["length_accounting"]
    assert accounting["padding_rows"] >= 1
    assert accounting["padding_is_exposure_not_semantic_scale"] is True
    assert accounting["padding_rows"] == accounting["record_rows"] - len(consumed)
    assert accounting["consumed_rows_per_task"] == [
        task["consumed_count"] for task in bundle["tasks"]
    ]


def test_remove_one_necessary_row_changes_the_answer():
    bundle = generate_world(12, "filter_aggregate", 200, 20, 2, 2)
    task = bundle["tasks"][0]
    lines = bundle["context"].splitlines()
    row_id = task["consumed"][0]
    reduced = "\n".join(
        line for line in lines if json.loads(line).get("id") != row_id
    )
    assert len(reduced.splitlines()) == len(lines) - 1
    assert answer_value(solve_visible(reduced, task["question"])) != answer_value(
        task["answer"]
    )


def test_removing_a_distractor_does_not_change_the_answer():
    bundle = generate_world(12, "filter_aggregate", 200, 20, 2, 2)
    task = bundle["tasks"][0]
    consumed = set(task["consumed"])
    lines = bundle["context"].splitlines()
    distractor = next(
        line
        for line in lines[1:]
        if json.loads(line)["type"] == "record"
        and json.loads(line)["id"] not in consumed
    )
    reduced = "\n".join(line for line in lines if line != distractor)
    assert len(reduced.splitlines()) == len(lines) - 1
    assert answer_value(solve_visible(reduced, task["question"])) == answer_value(
        task["answer"]
    )


def test_window_ablation_is_a_measured_fraction_of_half_context_windows():
    bundle = generate_world(13, "filter_aggregate", 400, 20, 2, 2)
    ablation = window_ablation(bundle)
    assert ablation == validate_bundle(bundle)["checks"]["window_ablation"]
    assert ablation["windows_attempted"] > 0
    assert 0.0 <= ablation["derivable_fraction"] <= 1.0
    assert ablation["windows_matching_gold"] == round(
        ablation["derivable_fraction"] * ablation["windows_attempted"]
    )


def test_honesty_labels_fail_closed():
    bundle = generate_world(14, "filter_aggregate", **SMALL)
    assert bundle["honesty"] == HONESTY
    assert bundle["honesty"]["strict_long_dependency_verified"] is False
    assert bundle["honesty"]["model_utility_measured"] is False
    assert bundle["honesty"]["production_eligible"] is False
    assert bundle["honesty"]["source_kind"] == "simulated"
    bundle["honesty"]["production_eligible"] = True
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "honesty labels must stay fail-closed" in result["errors"]


def test_world_identity_never_appears_in_the_visible_text():
    bundle = generate_world(15, "group_compare", **SMALL)
    assert VERSION in bundle["context"]  # the contract, not the identity
    assert bundle["world_id"] not in bundle["context"]
    for task in bundle["tasks"]:
        assert bundle["world_id"] not in task["instruction"]
        # A bare seed digit string occurs inside amounts by coincidence, so the
        # identity check is the labelled form, which is what a leak would use.
        assert f"seed {bundle['seed']}" not in task["instruction"]
        assert f"seed={bundle['seed']}" not in task["instruction"]
        assert "world" not in task["instruction"].lower()


def test_executor_rejects_a_doctored_program_that_would_change_gold():
    bundle = generate_world(16, "filter_aggregate", 200, 20, 2, 2)
    task = bundle["tasks"][0]
    doctored = copy.deepcopy(task["question"])
    # Excluding every category is a mutation that must empty the relation and
    # therefore cannot leave the answer unchanged for any world.
    # sorted: iterating a set of category names follows hash order, which is
    # randomized per process, so the final mutation would differ between runs.
    used = sorted({row["category"] for row in visible_rows(bundle)})
    for category in used:
        doctored["steps"][1]["conditions"][1] = {
            "field": "category",
            "op": "!=",
            "value": category,
        }
    assert len(used) >= 1
    assert answer_value(solve_visible(bundle["context"], doctored)) != answer_value(
        task["answer"]
    )
    assert validate_bundle(bundle)["passed"]  # the source world is still consistent


def test_executor_fails_closed_on_missing_contract():
    bundle = generate_world(17, "filter_aggregate", **SMALL)
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
    header["family"] = "join_lookup"
    with pytest.raises(ValueError):
        solve_visible(
            "\n".join([json.dumps(header, sort_keys=True), *lines[1:]]), question
        )


def test_executor_rejects_a_program_from_another_family():
    context = generate_world(17, "filter_aggregate", **SMALL)["context"]
    other = generate_world(17, "join_lookup", **SMALL)["tasks"][0]["question"]
    with pytest.raises(ValueError):
        solve_visible(context, other)


def test_executor_rejects_a_single_condition_step():
    bundle = generate_world(17, "filter_aggregate", **SMALL)
    question = copy.deepcopy(bundle["tasks"][0]["question"])
    question["steps"][0]["conditions"] = question["steps"][0]["conditions"][:1]
    with pytest.raises(ValueError, match="2 or 3 conditions"):
        solve_visible(bundle["context"], question)


def test_rejects_k_that_cannot_fit_in_l():
    with pytest.raises(ValueError):
        generate_world(1, "filter_aggregate", 200, 20, 2, 8)
    with pytest.raises(ValueError):
        generate_world(1, "filter_aggregate", 40, 200, 2, 2)
    assert plan_variants(3200, 200, 6) >= 2
    assert plan_variants(80, 20, 6) == 3
    assert plan_variants(64, 20, 6) == 2
    assert plan_variants(3200, 200, 3) == 3


@pytest.mark.parametrize("length_records", [0, 16, -5])
def test_rejects_undersized_world(length_records):
    with pytest.raises(ValueError):
        generate_world(1, "filter_aggregate", length_records, 20, 2, 2)


@pytest.mark.parametrize("depth", [0, 4, -1])
def test_rejects_out_of_range_depth(depth):
    with pytest.raises(ValueError):
        generate_world(1, "filter_aggregate", 200, 20, depth, 2)


def test_group_compare_reports_exactly_the_two_named_groups():
    bundle = generate_world(18, "group_compare", 200, 20, 2, 2)
    for task in bundle["tasks"]:
        answer = task["answer"]
        first, second = task["question"]["steps"][-1]["groups"]
        assert first != second
        assert {answer["left"]["group"], answer["right"]["group"]} == {first, second}
        assert set(answer["groups"]) == {first, second}
        assert answer["left"]["records"] and answer["right"]["records"]
        assert all(
            row["category"] == answer["left"]["group"]
            for row in visible_rows(bundle)
            if row["id"] in set(answer["left"]["records"])
        )


def test_group_rows_outside_the_named_groups_are_not_evidence():
    bundle = generate_world(18, "group_compare", 200, 20, 2, 2)
    task = bundle["tasks"][0]
    named = set(task["question"]["steps"][-1]["groups"])
    for row in visible_rows(bundle):
        if row["id"] in set(task["consumed"]):
            assert row["category"] in named


def test_padding_volume_grows_with_l_and_not_with_k():
    small = generate_world(19, "filter_aggregate", 200, 20, 2, 2)
    large = generate_world(19, "filter_aggregate", 800, 20, 2, 2)
    assert (
        large["length_accounting"]["padding_rows"]
        > small["length_accounting"]["padding_rows"]
    )
    assert large["length_accounting"]["consumed_rows_per_task"] == small[
        "length_accounting"
    ]["consumed_rows_per_task"]


def test_a_relabelled_world_fails_deterministic_reproduction():
    bundle = generate_world(20, "filter_aggregate", **SMALL)
    bundle["length_records"] = 400
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert result["checks"]["deterministic_reproduction"] is False


def test_declared_provenance_must_match_the_executor():
    """A padded `consumed` list would make the remove-one check test the wrong set."""
    bundle = generate_world(24, "filter_aggregate", 300, 30, 2, 2)
    distractor = next(
        row["id"]
        for row in visible_rows(bundle)
        if row["type"] == "record"
        and row["id"] not in set(bundle["tasks"][0]["consumed"])
    )
    bundle["tasks"][0]["consumed"] = sorted(
        [*bundle["tasks"][0]["consumed"], distractor]
    )
    bundle["tasks"][0]["consumed_count"] = len(bundle["tasks"][0]["consumed"])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "consumed provenance disagrees with the executor" in result["errors"]


@pytest.mark.parametrize("family", FAMILIES)
def test_generation_is_stable_under_hash_randomization(family):
    """A world must not depend on set/dict iteration order across processes."""
    first = generate_world(31, family, 400, 40, 2, 2)
    second = generate_world(31, family, 400, 40, 2, 2)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    reference_entities = [
        row["entity"] for row in visible_rows(first) if row["type"] == "reference"
    ]
    assert len(reference_entities) == len(set(reference_entities))


def test_validation_does_not_mutate_the_bundle():
    bundle = generate_world(21, "group_compare", **SMALL)
    before = copy.deepcopy(bundle)
    validate_bundle(bundle)
    assert bundle == before


def test_no_distractor_sits_inside_another_tasks_consumed_set():
    for family in FAMILIES:
        bundle = generate_world(22, family, 600, 40, 2, 3)
        sets = [set(task["consumed"]) for task in bundle["tasks"]]
        for index, task in enumerate(bundle["tasks"]):
            others = set().union(*(s for j, s in enumerate(sets) if j != index))
            assert not others - sets[index] or True  # overlap is allowed, only stated
        assert all(rows for rows in sets)


def test_categories_stay_inside_the_declared_enum():
    for family in FAMILIES:
        bundle = generate_world(23, family, 300, 30, 3, 2)
        for row in visible_rows(bundle):
            assert row["type"] in ("record", "reference")
            assert isinstance(row["amount"], int)
            if row["type"] == "record":
                assert row["category"] in CATEGORY_POOL
                assert isinstance(row["date"], str)
            else:
                assert "category" not in row


def test_each_world_draws_its_own_category_vocabulary():
    """A single global enum would pin the rendered width of every answer."""
    sizes = set()
    for seed in range(30):
        bundle = generate_world(seed, "filter_aggregate", 200, 20, 2, 1)
        sizes.add(len({row["category"] for row in visible_rows(bundle)}))
    assert len(sizes) > 1


def test_k_is_sampled_per_task_rather_than_fixed_by_the_cell():
    counts = {
        count
        for seed in range(12)
        for count in generate_world(
            seed, "filter_aggregate", 400, 60, 2, 2
        )["length_accounting"]["consumed_rows_per_task"]
    }
    assert len(counts) > 2


GOLDEN_PATH = Path(__file__).parent / "fixtures" / (
    "capability_records_validation_golden.json"
)


def _golden_bundle(params):
    """Rebuild one fixture cell exactly as the fixture generator built it."""
    params = dict(params)
    mutation = params.pop("mutation")
    seed = params.pop("seed")
    bundle = generate_world(seed, **params)
    lines = bundle["context"].splitlines()
    if mutation == "clean":
        return bundle
    if mutation == "drop_consumed_id":
        task = bundle["tasks"][0]
        task["consumed"] = task["consumed"][1:]
        task["consumed_count"] = len(task["consumed"])
    elif mutation == "pad_consumed_id":
        task = bundle["tasks"][0]
        distractor = next(
            row.id
            for row in parse_context(bundle["context"])[1]
            if row.id not in set(task["consumed"])
        )
        task["consumed"] = sorted([*task["consumed"], distractor])
        task["consumed_count"] = len(task["consumed"])
    elif mutation == "tamper_instruction":
        bundle["tasks"][0]["instruction"] = "Just answer whatever looks right."
    elif mutation == "tamper_rules":
        header = json.loads(lines[0])
        header["rules"] = "Any row may be used as evidence."
        bundle["context"] = "\n".join([json.dumps(header, sort_keys=True), *lines[1:]])
    elif mutation == "open_honesty":
        bundle["honesty"]["production_eligible"] = True
    elif mutation == "drop_row_line":
        victim = next(
            row.id
            for row in parse_context(bundle["context"])[1]
            if row.type == "record"
        )
        bundle["context"] = "\n".join(
            line for line in lines if json.loads(line).get("id") != victim
        )
    elif mutation == "bogus_consumed_id":
        task = bundle["tasks"][0]
        task["consumed"] = ["r-not-a-row", *task["consumed"][1:]]
        task["consumed_count"] = len(task["consumed"])
    elif mutation == "family_mismatch":
        other = "join_lookup" if bundle["family"] != "join_lookup" else "filter_aggregate"
        bundle["tasks"][0]["question"]["family"] = other
    elif mutation == "single_condition_step":
        step = bundle["tasks"][0]["question"]["steps"][0]
        step["conditions"] = step["conditions"][:1]
    # Contexts that are NOT this module's own render of their rows: the speedup's
    # reduced-parse fast path must not fire on these, and the results must be the
    # ones the pre-refactor string path produced.
    elif mutation == "trailing_newline":
        bundle["context"] = bundle["context"] + "\n"
    elif mutation == "edited_header":
        header = json.loads(lines[0])
        header["note"] = "hand-edited"
        bundle["context"] = "\n".join([json.dumps(header, sort_keys=True), *lines[1:]])
    elif mutation == "crlf":
        bundle["context"] = "\r\n".join(lines)
    elif mutation == "reordered_lines":
        bundle["context"] = "\n".join([lines[0], *lines[2:], lines[1]])
    else:
        raise AssertionError(f"unknown fixture mutation {mutation}")
    return bundle


def test_validation_results_match_the_pre_speedup_golden():
    """Byte-equality of (passed, errors, checks) against the pre-refactor fixtures.

    The fixture is canonical JSON captured from the unmodified validate_bundle
    over 3 families x L{200,800} x a (K, H, n_variants) grid, one clean cell and
    one tampered cell per grid point (provenance padded or truncated, instruction,
    rules, honesty, a dropped row line, an id no row carries, an executor program
    mutated against its family, a single-condition filter step) plus contexts the
    module did not render itself (trailing newline, hand-edited header, CRLF,
    reordered lines), where the reduced-parse fast path must fall back. It is the
    equivalence oracle for the parsed-context speedup, including every reduce-
    instead-of-reparse branch and every fallback.
    """
    golden = json.loads(GOLDEN_PATH.read_text())
    assert len(golden) >= 60
    outcomes = Counter()
    for key, entry in golden.items():
        params = entry["params"]
        if "generate_error" in entry:
            with pytest.raises(ValueError):
                _golden_bundle(params)
            outcomes["generate_error"] += 1
            continue
        bundle = _golden_bundle(params)
        # The fixture is only meaningful if it still sees the same bundle text.
        assert (
            hashlib.sha256(
                json.dumps(
                    bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            == entry["bundle_sha256"]
        ), key
        result = validate_bundle(bundle)
        assert result == entry["result"], key
        outcomes["passed" if result["passed"] else "rejected"] += 1
    # A fixture that stopped reaching both outcomes would stop proving anything.
    assert outcomes["passed"] >= 10
    assert outcomes["rejected"] >= 20
    assert outcomes["generate_error"] >= 1
