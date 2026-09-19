"""Research/experiments domain adapter: the spine contract and the domain's own rules."""

import copy
import json
from collections import Counter

import pytest

from longworld.synthesis.capability_research import (
    FAMILIES,
    FIELDS,
    HONESTY,
    PROMPTS,
    PROTOCOLS,
    RETURNS,
    VERSION,
    generate_world,
    parse_context,
    plan_variants,
    render_instruction,
    solve_visible,
    validate_bundle,
)

SMALL = {"length_records": 200, "consumed_records": 20, "depth": 1, "n_variants": 2}
SEEDS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("family", FAMILIES)
def test_generation_and_validation_are_green_over_ten_seeds(seed, family):
    bundle = generate_world(seed, family, **SMALL)
    result = validate_bundle(bundle)
    assert result["passed"], (family, seed, result["errors"])


@pytest.mark.parametrize("family", FAMILIES)
def test_generation_and_validation_hold_at_depth_two(family):
    bundle = generate_world(
        11, family, length_records=200, consumed_records=20, depth=2, n_variants=2
    )
    result = validate_bundle(bundle)
    assert result["passed"], (family, result["errors"])


def test_world_is_deterministic_and_serializable():
    first = generate_world(3, "research_run", **SMALL)
    second = generate_world(3, "research_run", **SMALL)
    assert first == second
    assert json.loads(json.dumps(first)) == first
    assert first["context"] != generate_world(4, "research_run", **SMALL)["context"]
    assert validate_bundle(first)["checks"]["deterministic_reproduction"] is True


def test_a_relabelled_world_fails_deterministic_reproduction():
    bundle = generate_world(20, "research_run", **SMALL)
    bundle["length_records"] = 400
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert result["checks"]["deterministic_reproduction"] is False


def test_validation_does_not_mutate_the_bundle():
    bundle = generate_world(21, "research_join", **SMALL)
    before = copy.deepcopy(bundle)
    validate_bundle(bundle)
    assert bundle == before


# --------------------------------------------------------------------------
# The spine contract: what the runner consumes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
def test_the_spine_round_trip_from_plan_to_rows(family):
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
        bundle = generate_world(seed, family, length, 20, 1, 2)
        result = validate_bundle(bundle)
        assert result["passed"], result["errors"]
        for task in bundle["tasks"]:
            assert task["instruction"] == render_instruction(
                family, task["question"], task["phrasing_index"]
            )


@pytest.mark.parametrize("family", FAMILIES)
def test_the_bundle_shape_is_the_runners(family):
    bundle = generate_world(6, family, **SMALL)
    assert bundle["world_id"].startswith(VERSION + "-")
    assert bundle["family"] == family
    assert bundle["depth"] == 1
    assert bundle["n_variants"] == 2
    assert bundle["schema_version"] == VERSION
    accounting = bundle["length_accounting"]
    assert accounting["record_rows"] == bundle["length_records"] == 200
    assert accounting["consumed_rows_per_task"] == [
        task["consumed_count"] for task in bundle["tasks"]
    ]
    assert accounting["padding_rows"] == 200 - accounting["consumed_primary_rows"]
    assert accounting["padding_is_exposure_not_semantic_scale"] is True
    assert set(accounting) == {
        "record_rows",
        "reference_rows",
        "rendered_rows",
        "consumed_rows_per_task",
        "consumed_primary_rows",
        "padding_rows",
        "decoy_padding_rows",
        "padding_is_exposure_not_semantic_scale",
    }
    for task in bundle["tasks"]:
        assert task["task_id"].startswith("q")
        assert task["question"]["family"] == family
        assert task["capability"] == family
        assert task["consumed"] == sorted(task["consumed"])
        assert task["instruction"].startswith("Task: ")


@pytest.mark.parametrize("family", FAMILIES)
def test_honesty_labels_fail_closed(family):
    bundle = generate_world(14, family, **SMALL)
    assert bundle["honesty"] == HONESTY
    assert bundle["honesty"]["strict_long_dependency_verified"] is False
    assert bundle["honesty"]["model_utility_measured"] is False
    assert bundle["honesty"]["production_eligible"] is False
    assert bundle["honesty"]["source_kind"] == "simulated_domain"
    bundle["honesty"]["production_eligible"] = True
    assert not validate_bundle(bundle)["passed"]


@pytest.mark.parametrize("family", FAMILIES)
def test_prompt_pool_is_deep_enough_and_rotated(family):
    assert len(PROMPTS[family]) >= 24
    assert len(set(PROMPTS[family])) == len(PROMPTS[family])
    phrasings = {
        task["phrasing_index"]
        for seed in range(300)
        for task in generate_world(seed, family, 120, 16, 1, 1)["tasks"]
    }
    assert len(phrasings) >= 20


@pytest.mark.parametrize("family", FAMILIES)
def test_instruction_contract_is_the_module_instruction(family):
    bundle = generate_world(5, family, **SMALL)
    for task in bundle["tasks"]:
        assert task["instruction"].startswith(
            f"Task: {PROMPTS[family][task['phrasing_index']]}. "
        )
        assert f"Fields: {FIELDS[family]}" in task["instruction"]
        assert task["instruction"].endswith(RETURNS[family])


@pytest.mark.parametrize("family", FAMILIES)
def test_world_identity_never_appears_in_the_visible_text(family):
    bundle = generate_world(15, family, **SMALL)
    assert VERSION in bundle["context"]
    assert bundle["world_id"] not in bundle["context"]
    for task in bundle["tasks"]:
        assert bundle["world_id"] not in task["instruction"]


@pytest.mark.parametrize("family", FAMILIES)
def test_l_is_exact_and_k_is_one_variant_per_task(family):
    bundle = generate_world(7, family, 317, 20, 1, 3)
    types = [json.loads(line)["type"] for line in bundle["context"].splitlines()[1:]]
    assert types.count("measurement") == 317
    assert len(bundle["tasks"]) == 3


# --------------------------------------------------------------------------
# The domain contract: research lifecycle rows, not ledger rows
# --------------------------------------------------------------------------


def test_row_types_are_the_research_lifecycle():
    bundle = generate_world(30, "research_run", 300, 20, 2, 2)
    rows = [json.loads(line) for line in bundle["context"].splitlines()[1:]]
    types = Counter(row["type"] for row in rows)
    assert set(types) == {"config", "batch", "measurement", "retraction", "erratum"}
    for row in rows:
        if row["type"] == "config":
            assert set(row) == {"id", "type", "method", "instrument", "unit", "memo"}
        elif row["type"] == "batch":
            assert set(row) == {"id", "type", "config", "site", "memo"}
        elif row["type"] == "measurement":
            assert set(row) == {
                "id",
                "type",
                "batch",
                "unit",
                "value",
                "date",
                "memo",
            }
        elif row["type"] == "retraction":
            assert set(row) == {"id", "type", "batch", "reason", "disclosed", "memo"}
        elif row["type"] == "erratum":
            assert set(row) == {
                "id",
                "type",
                "method",
                "cutoff",
                "disclosed",
                "memo",
            }


def test_the_join_world_carries_no_errata():
    bundle = generate_world(31, "research_join", 300, 20, 1, 2)
    types = Counter(
        json.loads(line)["type"] for line in bundle["context"].splitlines()[1:]
    )
    assert set(types) == {"config", "batch", "measurement", "retraction"}
    assert "erratum" not in types


def test_unit_is_consistent_along_every_chain():
    """A measurement carries the unit its config's instrument reports."""
    bundle = generate_world(32, "research_join", 300, 20, 1, 2)
    _, rows = parse_context(bundle["context"])
    by_id = {row.id: row for row in rows}
    instruments = {row.instrument: row.unit for row in rows if row.type == "config"}
    for row in rows:
        if row.type == "measurement":
            batch = by_id[row.batch]
            config = by_id[batch.config]
            assert instruments[config.instrument] == row.unit == config.unit


@pytest.mark.parametrize(
    "mutate",
    [
        "dangling_batch",
        "dangling_config",
        "wrong_unit",
        "dangling_retraction",
        "early_retraction",
        "erratum_before_cutoff",
        "erratum_in_join",
    ],
)
def test_domain_illegal_worlds_are_rejected_by_the_executor(mutate):
    """The same JOIN over config->batch->measurement needs domain legality, not type matching."""
    bundle = generate_world(33, "research_run", 200, 20, 1, 2)
    lines = bundle["context"].splitlines()
    rows = [json.loads(line) for line in lines[1:]]
    by_id = {row["id"]: row for row in rows}
    task = bundle["tasks"][0]
    question = task["question"]
    if mutate == "dangling_batch":
        measurement = next(row for row in rows if row["type"] == "measurement")
        measurement["batch"] = "b-does-not-exist"
    elif mutate == "dangling_config":
        batch = next(row for row in rows if row["type"] == "batch")
        batch["config"] = "c-does-not-exist"
    elif mutate == "wrong_unit":
        measurement = next(row for row in rows if row["type"] == "measurement")
        measurement["unit"] = "bogus_unit"
    elif mutate == "dangling_retraction":
        retraction = next(row for row in rows if row["type"] == "retraction")
        retraction["batch"] = "b-does-not-exist"
    elif mutate == "early_retraction":
        retraction = next(row for row in rows if row["type"] == "retraction")
        early = min(
            (
                row
                for row in rows
                if row["type"] == "measurement"
                and by_id[row["batch"]]["id"] == retraction["batch"]
            ),
            key=lambda row: row["date"],
        )
        retraction["disclosed"] = "2019-01-01"
        assert retraction["disclosed"] < early["date"]
    elif mutate == "erratum_before_cutoff":
        erratum = next(row for row in rows if row["type"] == "erratum")
        erratum["disclosed"] = "2019-01-01"
        assert erratum["disclosed"] < erratum["cutoff"]
    else:
        bundle = generate_world(33, "research_join", 200, 20, 1, 2)
        lines = bundle["context"].splitlines()
        rows = [json.loads(line) for line in lines[1:]]
        question = bundle["tasks"][0]["question"]
        rows.append(
            {
                "id": "e-intruder",
                "type": "erratum",
                "memo": "note-intruder",
                "method": question["steps"][0]["method"],
                "cutoff": "2020-01-01",
                "disclosed": "2020-02-01",
            }
        )
    tampered = "\n".join([lines[0], *[json.dumps(row, sort_keys=True) for row in rows]])
    with pytest.raises(ValueError):
        solve_visible(tampered, question)


def test_validator_rejects_an_edited_illegal_world():
    """validate_bundle re-checks domain legality, not just reproduction."""
    bundle = generate_world(34, "research_run", 200, 20, 1, 2)
    tampered = copy.deepcopy(bundle)
    rows = [json.loads(line) for line in tampered["context"].splitlines()[1:]]
    # Dangling batch reference inside the stored context.
    for row in rows:
        if row["type"] == "batch":
            row["config"] = "c-does-not-exist"
            break
    lines = tampered["context"].splitlines()
    tampered["context"] = "\n".join(
        [lines[0], *[json.dumps(row, sort_keys=True) for row in rows]]
    )
    result = validate_bundle(tampered)
    assert not result["passed"]
    assert any("config" in error or "batch" in error for error in result["errors"])


# --------------------------------------------------------------------------
# Interventions: the as-of flip and the two-sided join removal
# --------------------------------------------------------------------------


def test_reveal_flip_intervention_is_recorded_and_flips():
    bundle = generate_world(35, "research_run", 200, 20, 1, 2)
    for task in bundle["tasks"]:
        intervention = task["intervention"]
        assert intervention["kind"] == "reveal_flip"
        assert intervention["before"] > task["question"]["steps"][-1]["as_of"]
        assert intervention["after"] < task["question"]["steps"][-1]["as_of"]
    checks = validate_bundle(bundle)["checks"]
    assert checks["intervention:research_run"] is True


def test_subtree_removal_intervention_removes_both_sides():
    """Deleting one side of a join key would dangle the other; both go."""
    bundle = generate_world(36, "research_join", 200, 20, 1, 2)
    _, rows = parse_context(bundle["context"])
    by_id = {row.id: row for row in rows}
    from longworld.synthesis.capability_research import _domain_legality

    for task in bundle["tasks"]:
        intervention = task["intervention"]
        assert intervention["kind"] == "subtree_removal"
        removed = intervention["removed"]
        assert intervention["root"] in removed
        # The removed set is referentially closed downwards: every removed
        # measurement's batch is removed, and every removed retraction's
        # batch is removed, so no remaining row dangles.
        for row_id in removed:
            row = by_id[row_id]
            if row.type in ("measurement", "retraction"):
                assert row.batch in removed
        # The remaining world is domain-legal: the removal leaves no
        # dangling reference and keeps every surviving chain consistent.
        remaining = [row for row in rows if row.id not in set(removed)]
        _domain_legality(remaining, "research_join")
        # The intervention's root is one of this task's own valid chains:
        # the removal takes out that chain's batch and its runs, leaving the
        # config behind for the other batches that still reference it.
        assert intervention["chain"][1] == intervention["root"]
        assert intervention["chain"][0] in removed
        assert intervention["chain"][2] not in removed
    checks = validate_bundle(bundle)["checks"]
    assert checks["intervention:research_join"] is True


def test_tampered_intervention_is_rejected():
    bundle = generate_world(37, "research_run", 200, 20, 1, 2)
    tampered = copy.deepcopy(bundle)
    tampered["tasks"][0]["intervention"]["after"] = tampered["tasks"][0][
        "intervention"
    ]["before"]
    result = validate_bundle(tampered)
    assert not result["passed"]
    assert "the declared intervention did not change the answer" in result["errors"]


def test_tampered_provenance_is_rejected():
    bundle = generate_world(38, "research_join", 200, 20, 1, 2)
    task = bundle["tasks"][0]
    rows = [json.loads(line) for line in bundle["context"].splitlines()[1:]]
    distractor = next(
        row["id"]
        for row in rows
        if row["type"] == "measurement" and row["id"] not in set(task["consumed"])
    )
    task["consumed"] = sorted([*task["consumed"], distractor])
    task["consumed_count"] = len(task["consumed"])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert any("consumed" in error for error in result["errors"])


def test_wrong_answer_is_rejected():
    bundle = generate_world(39, "research_run", 200, 20, 1, 2)
    bundle["tasks"][0]["answer"] = {"as_of": "2020-01-01", "valid": 42}
    assert not validate_bundle(bundle)["passed"]


def test_tampered_rules_text_is_rejected():
    bundle = generate_world(40, "research_run", 200, 20, 1, 2)
    lines = bundle["context"].splitlines()
    header = json.loads(lines[0])
    header["rules"] = "Any row may be used as evidence."
    bundle["context"] = "\n".join([json.dumps(header, sort_keys=True), *lines[1:]])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "rules text does not match the registered contract" in result["errors"]


def test_a_world_header_of_the_wrong_family_is_rejected():
    bundle = generate_world(41, "research_run", 200, 20, 1, 2)
    lines = bundle["context"].splitlines()
    header = json.loads(lines[0])
    header["family"] = "research_join"
    header["rules"] = PROTOCOLS["research_join"]
    bundle["context"] = "\n".join([json.dumps(header, sort_keys=True), *lines[1:]])
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "header family mismatch" in result["errors"]


def test_tampered_instruction_is_rejected():
    bundle = generate_world(42, "research_run", 200, 20, 1, 2)
    bundle["tasks"][0]["instruction"] = "Task: just return 0."
    result = validate_bundle(bundle)
    assert not result["passed"]
    assert "instruction does not match the registered contract" in result["errors"]


# --------------------------------------------------------------------------
# Depth and feasibility rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
def test_depth_deeper_than_two_is_rejected_loudly(family):
    with pytest.raises(ValueError, match="depth must be 1 or 2"):
        generate_world(1, family, 200, 20, 3, 2)


@pytest.mark.parametrize("family", FAMILIES)
def test_rejects_k_that_cannot_fit_in_l(family):
    with pytest.raises(ValueError):
        generate_world(1, family, 200, 20, 1, 8)
    with pytest.raises(ValueError):
        generate_world(1, family, 40, 200, 1, 2)


@pytest.mark.parametrize("length_records", [0, 16, -5])
def test_rejects_undersized_world(length_records):
    with pytest.raises(ValueError):
        generate_world(1, "research_run", length_records, 20, 1, 2)


def test_rejects_an_unknown_family():
    with pytest.raises(ValueError):
        generate_world(1, "not_a_family", **SMALL)


@pytest.mark.parametrize("family", FAMILIES)
def test_padding_volume_grows_with_l_and_not_with_k(family):
    small = generate_world(19, family, 200, 20, 1, 2)
    large = generate_world(19, family, 800, 20, 1, 2)
    assert (
        large["length_accounting"]["padding_rows"]
        > small["length_accounting"]["padding_rows"]
    )
    assert (
        large["length_accounting"]["consumed_rows_per_task"]
        == small["length_accounting"]["consumed_rows_per_task"]
    )


def test_the_family_dispatch_shape_registers_two_families():
    assert FAMILIES == ("research_run", "research_join")
    assert set(PROTOCOLS) == set(FAMILIES)
    assert set(PROMPTS) == set(FAMILIES)
    assert set(RETURNS) == set(FAMILIES)
    assert set(FIELDS) == set(FAMILIES)


# --------------------------------------------------------------------------
# Not a finance reskin: the domain's own semantics are load-bearing
# --------------------------------------------------------------------------


def test_status_precedence_is_retracted_over_invalidated():
    """A run covered by both a retraction and an erratum reports retracted."""
    bundle = generate_world(43, "research_run", 200, 20, 1, 2)
    checks = validate_bundle(bundle)["checks"]
    assert checks["precedence_probes"] >= 1


def test_the_asof_answer_counts_hidden_disclosures_as_valid():
    """A post-query retraction leaves its runs valid at the query date."""
    bundle = generate_world(44, "research_run", 200, 20, 1, 2)
    _, rows = parse_context(bundle["context"])
    found = False
    for task in bundle["tasks"]:
        as_of = task["question"]["steps"][-1]["as_of"]
        hidden = [
            row for row in rows if row.type == "retraction" and row.disclosed > as_of
        ]
        for retraction in hidden:
            runs = [
                row
                for row in rows
                if row.type == "measurement" and row.batch == retraction.batch
            ]
            if runs and any(run.id in set(task["consumed"]) for run in runs):
                found = True
    assert found, "no task reads a post-query retraction's runs"


def test_the_domain_vocabulary_is_research_not_finance():
    """No ledger term survives into the visible contract; the research terms do."""
    for family in FAMILIES:
        text = PROTOCOLS[family] + FIELDS[family] + RETURNS[family]
        for term in ("balance", "credit", "debit", "account", "ledger"):
            assert term not in text.lower()
        for term in ("batch", "measurement", "retraction", "config"):
            assert term in text.lower()
    # The lifecycle family alone carries errata and their cutoffs.
    run_text = (
        PROTOCOLS["research_run"] + FIELDS["research_run"] + RETURNS["research_run"]
    ).lower()
    for term in ("erratum", "cutoff", "disclosed"):
        assert term in run_text
    bundle = generate_world(45, "research_run", 200, 20, 1, 2)
    visible = "\n".join(
        [
            PROTOCOLS["research_run"],
            bundle["context"].splitlines()[0],
            FIELDS["research_run"],
        ]
    ).lower()
    for term in ("instrument", "unit", "method", "disclosed", "cutoff"):
        assert term in visible
