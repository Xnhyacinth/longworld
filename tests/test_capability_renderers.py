"""Multi-format rendering: one typed world, three expressions, one gold.

The three records families and the three P70 F families (alias_locate,
asof_state, rule_holdout) render through the same dispatch, template banks and
pinned contract, so the same round-trip, equivalence and tamper guarantees hold
over all six.

ROUGE-1 precision of question-against-evidence (renderers.lexical_overlap), the
distribution the task asks to be measured and reported -- seeds 0-5 x 3 families
x 2 variants, 36 tasks per format:

    jsonl 0.0699 mean, 0.1173 max   (matches NoLiMa's published 0.069 anchor)
    prose 0.0241 mean, 0.0412 max
    table 0.0096 mean, 0.0238 max

All three sit far below the published NIAH 0.905 contrast and under the 0.3
target: no format lets a solver answer by matching the question's own words.
On the F families the same measure stays under the target at bank scale (the
live v2 shards); tiny synthetic worlds can exceed it only through a
one-row evidence denominator, which the F-family overlap test documents.
"""

import glob
import json
import re
import statistics

import pytest

from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records
from longworld.synthesis import capability_renderers as renderers

# The immutable v2 bank: the live corpus whose F-family shards the smoke tests read.
DATA = "/volume/pt-dev/qjiu/longworld/data/capability_records"

SEEDS = (0, 3, 8, 14, 26)
SMALL = {"length_records": 200, "consumed_records": 20, "depth": 2, "n_variants": 2}
OVERLAP_TARGET = 0.3
OVERLAP_OBSERVED_MAX = 0.15
# Lines from the top of a rendering to its first row, per format: the pinned
# contract (version, rules, extra) plus each format's own preamble.
OFFSET = {"jsonl": 1, "prose": 4, "table": 5}
# (seed, family) pairs for the tests that need no format axis.
WORLDS = [(seed, family) for family in records.FAMILIES for seed in SEEDS]
F_WORLDS = [(seed, family) for family in families.FAMILIES[:3] for seed in SEEDS]
MODULES = {
    **{f: records for f in records.FAMILIES},
    **{f: families for f in families.FAMILIES[:3]},
}


def generate(seed, family, **overrides):
    return MODULES[family].generate_world(seed, family, **{**SMALL, **overrides})


def corpus():
    """Five seeds x three families: the corpus the renderer is checked against."""
    return [(seed, records.generate_world(seed, f, **SMALL)) for seed, f in WORLDS]


def f_corpus():
    """The same corpus over the three F families."""
    return [(seed, generate(seed, f)) for seed, f in F_WORLDS]


def without_row(rendered, fmt, row_id, rows, family=None):
    """The rendering with one row's own text line deleted, re-parsed.

    The line is located by parsing, never by searching the text, so another row's
    id quoted inside a sentence cannot mislead the probe.
    """
    position = next(
        i
        for i, item in enumerate(renderers.parse_world(rendered, fmt))
        if item.id == row_id
    )
    lines = rendered.splitlines()
    del lines[position + OFFSET[fmt]]
    return renderers.parse_world("\n".join(lines), fmt), [
        row for row in rows if row.id != row_id
    ]


def answer_without(bundle, fmt, row_id, question):
    """Re-execute one question against the world with one row's line removed."""
    family = bundle["family"]
    _, rows = records.parse_context(bundle["context"])
    remaining, expected = without_row(
        renderers.render_world(bundle["context"], family, fmt), fmt, row_id, rows
    )
    assert remaining == expected
    return records.answer_value(
        records.solve_visible(records.render_context(remaining, family), question)
    )


@pytest.mark.parametrize("seed,family", WORLDS)
@pytest.mark.parametrize("fmt", renderers.FORMATS)
def test_round_trip_parse_reproduces_the_rows(seed, family, fmt):
    """Five seeds x three families x three formats, parsed by a real regex parser."""
    bundle = records.generate_world(seed, family, **SMALL)
    _, rows = records.parse_context(bundle["context"])
    text = renderers.render_world(bundle["context"], family, fmt)
    assert renderers.parse_world(text, fmt) == rows
    assert renderers.parse_world(renderers.render_world(rows, family, fmt), fmt) == rows
    # Deterministic, and the same rows however the caller supplies them.
    assert text == renderers.render_world(rows, family, fmt)
    if fmt == "jsonl":
        assert text == bundle["context"]  # the compiler's own bytes, not a fork


@pytest.mark.parametrize("seed,family", WORLDS)
def test_the_same_program_yields_the_same_gold_in_all_three_formats(seed, family):
    bundle = records.generate_world(seed, family, **SMALL)
    for task in bundle["tasks"]:
        report = renderers.verify_format_equivalence(bundle["context"], task)
        assert report["equivalent"], report["errors"]
        assert set(report["gold_by_format"]) == set(renderers.FORMATS)
        assert report["gold"] == task["answer"]
    # Sampled questions across the whole corpus, well past the required twenty.
    pairs = [(b, task) for _, b in corpus() for task in b["tasks"]]
    assert len(pairs) >= 20
    assert all(
        renderers.verify_format_equivalence(b["context"], task)["equivalent"]
        for b, task in pairs
    )


def test_the_prose_bank_varies_sentences_and_round_trips_every_template():
    """The bank is the parser's inverse: each template must compile and match."""
    bundle = records.generate_world(6, "join_lookup", 400, 30, 2, 2)
    _, rows = records.parse_context(bundle["context"])
    prose = renderers.render_world(bundle["context"], None, "prose")
    lines = prose.splitlines()[OFFSET["prose"] :]
    shapes = {re.sub(r"[A-Za-z0-9._-]+", "", line) for line in lines}
    assert len(lines) >= 300 and len(shapes) > 1
    # Re-rendering the parse reproduces the text byte for byte: every sentence is
    # recovered by a template and re-emitted identically.
    assert renderers.render_world(rows, "join_lookup", "prose") == prose
    for row in rows[:1] + rows[-1:]:
        text = renderers.render_world([row], "join_lookup", "prose")
        assert renderers.parse_world(text, "prose") == [row]
        assert str(row.amount) in text.splitlines()[OFFSET["prose"]]
    banks = (renderers.RECORD_TEMPLATES, renderers.REFERENCE_TEMPLATES)
    assert len(banks[0]) >= 12
    for bank in banks:
        assert len({re.sub(r"[A-Za-z0-9._-]+", "", t) for t in bank}) == len(bank)


@pytest.mark.parametrize("fmt", renderers.FORMATS)
def test_question_vocabulary_adapts_while_the_program_description_is_fixed(fmt):
    bundle = records.generate_world(2, "filter_aggregate", **SMALL)
    program = bundle["tasks"][0]["question"]
    text = renderers.render_question(program, fmt, 0)
    assert program == bundle["tasks"][0]["question"]
    assert renderers.render_question(program, "jsonl", 0) == records.render_instruction(
        "filter_aggregate", program, 0
    )
    assert "Program: " in text and text.endswith("breakdown (category to count).")
    fields = text.split("Fields: ")[1].split(" Program: ")[0]
    if fmt != "jsonl":
        assert (
            fields
            != (
                renderers.render_question(program, "jsonl", 0)
                .split("Fields: ")[1]
                .split(" Program: ")[0]
            )
        )


@pytest.mark.parametrize("fmt", renderers.FORMATS)
def test_a_reworded_instruction_is_rejected(fmt):
    """The PROMPTS pinning pattern: only a byte-identical re-render passes.

    Each tamper below denotes the same program, so byte-pinning is what rejects it.
    """
    program = records.generate_world(2, "group_compare", **SMALL)["tasks"][0][
        "question"
    ]
    honest = renderers.render_question(program, fmt, 1)
    assert renderers.validate_question(honest, program, fmt, 1)
    for tampered in (
        honest.replace("Task: ", "Task: please ", 1),
        honest.replace("Program: ", "Note: ", 1),
        honest.replace("Fields: ", "Fields: note ", 1),
        honest.replace("keep rows where", "retain rows where", 1),
        honest.replace("Task:", "task:", 1),
        honest + " ",
        honest[:-1],
    ):
        assert tampered != honest
        assert not renderers.validate_question(tampered, program, fmt, 1), tampered
    assert not renderers.validate_question(honest, program, fmt, 2)
    assert not renderers.validate_question(
        honest, {**program, "steps": program["steps"][:-1]}, fmt, 1
    )


def test_a_tampered_container_or_unknown_input_is_rejected():
    bundle = records.generate_world(5, "filter_aggregate", **SMALL)
    prose = renderers.render_world(bundle["context"], None, "prose")
    table = renderers.render_world(bundle["context"], None, "table")
    for fmt, tampered in (
        ("prose", prose.replace(records.PROTOCOLS["filter_aggregate"], "Solve it.", 1)),
        ("prose", prose.replace("# one sentence per row, in file order", "# x", 1)),
        ("prose", prose.replace("capability-records-v1", "capability-records-v9", 1)),
        ("prose", prose + "\nOn 2019-01-01, nobody logged a thing."),
        ("table", table.replace("|".join(renderers.TABLE_COLUMNS), "id|amount", 1)),
        ("table", table + "\nshort|row"),
    ):
        with pytest.raises(ValueError):
            renderers.parse_world(tampered, fmt)
    for call in (
        lambda: renderers.render_world(bundle["context"], "join_lookup", "prose"),
        lambda: renderers.render_world(bundle["context"], None, "yaml"),
        lambda: renderers.parse_world(bundle["context"], "yaml"),
        lambda: renderers.render_world([], "filter_aggregate", "prose"),
        lambda: renderers.lexical_overlap(bundle["context"], {}, "prose"),
    ):
        with pytest.raises(ValueError):
            call()


@pytest.mark.parametrize("fmt", renderers.FORMATS)
def test_lexical_overlap_is_below_the_shortcut_target(fmt):
    """A question naming the fields it filters is unavoidable; values are not."""
    values = [
        renderers.lexical_overlap(bundle["context"], task, fmt)
        for _, bundle in corpus()
        for task in bundle["tasks"]
    ]
    assert values and max(values) < OVERLAP_OBSERVED_MAX < OVERLAP_TARGET
    bundle = records.generate_world(3, "filter_aggregate", **SMALL)
    task = bundle["tasks"][0]
    _, rows = records.parse_context(bundle["context"])
    text = renderers.render_question(task["question"], fmt, 0)
    shared = set(re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", text.lower()))
    assert {"amount", "date", "category"} <= shared
    # No value-bearing token: no memo, no consumed row id, in the question text.
    assert not {row.memo for row in rows} & shared
    assert not set(task["consumed"]) & shared
    assert {"the", "of", "and"} <= renderers.STOPWORDS


@pytest.mark.parametrize("fmt", renderers.FORMATS)
@pytest.mark.parametrize("family", records.FAMILIES)
def test_a_distractor_row_never_changes_the_gold(fmt, family):
    """Removing a distractor's line leaves the answer untouched.

    The consumed row below is the other half: without it, "nothing moves the gold"
    would also pass on a corpus whose rendered lines are never read.
    """
    for seed in SEEDS[:3]:
        bundle = records.generate_world(seed, family, **SMALL)
        _, rows = records.parse_context(bundle["context"])
        for task in bundle["tasks"]:
            gold = records.answer_value(task["answer"])
            distractors = [
                row
                for row in rows
                if row.type == "record" and row.id not in set(task["consumed"])
            ][:4]
            assert len(distractors) == 4
            for row in distractors:
                assert answer_without(bundle, fmt, row.id, task["question"]) == gold
            if task["question"]["steps"][-1].get("how") in ("sum", "count"):
                # Sum and count are per-row; min and max are the compiler's
                # documented `unique_extremum` exception and are left alone.
                assert (
                    answer_without(bundle, fmt, task["consumed"][0], task["question"])
                    != gold
                )


def test_verify_format_equivalence_reports_a_broken_format_rather_than_passing():
    bundle = records.generate_world(17, "join_lookup", **SMALL)
    task = bundle["tasks"][0]
    report = renderers.verify_format_equivalence(bundle["context"], task["question"])
    assert report["equivalent"] and report["gold"] == task["answer"]
    assert all(report["round_trip"].values())
    assert report["rows"] == len(records.parse_context(bundle["context"])[1])
    bad = renderers.verify_format_equivalence(bundle["context"], {"steps": []})
    assert not bad["equivalent"] and bad["errors"]


def test_the_format_banks_are_registered_distinct_and_wide_enough():
    for fmt, bank in (
        ("prose", renderers.PROSE_PROMPTS),
        ("table", renderers.TABLE_PROMPTS),
    ):
        assert len(bank) >= 8 and len(bank) == len(set(bank))
        # Every renderable family gets the same pinned bank for the format.
        assert set(renderers.FORMAT_PROMPTS[fmt]) == set(renderers.RENDERABLE)
        assert list(renderers.FORMAT_PROMPTS[fmt].values()) == [bank] * len(
            renderers.FORMAT_PROMPTS[fmt]
        )
    # jsonl reuses each owner's bank under the owner's families, byte-identical
    # to that owner's render_instruction.
    assert renderers.FORMAT_PROMPTS["jsonl"] == {
        **records.PROMPTS,
        **families.PROMPTS,
    }


# --------------------------------------------------------------------------
# The three P70 F families render through the same dispatch, banks and contract.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed,family", F_WORLDS)
@pytest.mark.parametrize("fmt", renderers.FORMATS)
def test_f_family_round_trip_parse_reproduces_the_rows(seed, family, fmt):
    """The F rows render and re-parse in all three formats, in file order."""
    bundle = generate(seed, family)
    _, rows = families.parse_context(bundle["context"])
    text = renderers.render_world(bundle["context"], family, fmt)
    assert renderers.parse_world(text, fmt) == rows
    # Deterministic, and the same world however the caller supplies it.
    assert text == renderers.render_world(bundle, family, fmt)
    if fmt == "jsonl":
        assert text == bundle["context"]  # the owner's own bytes, not a fork
    if family == "rule_holdout":
        # Bare rows cannot carry the rule declaration, so rendering them would
        # drop executable state: the renderer refuses instead of emitting an
        # unparseable world.
        with pytest.raises(ValueError):
            renderers.render_world(rows, family, fmt)
    else:
        assert (
            renderers.parse_world(renderers.render_world(rows, family, fmt), fmt)
            == rows
        )


@pytest.mark.parametrize("seed,family", F_WORLDS)
def test_f_family_the_same_program_yields_the_same_gold_in_all_three_formats(
    seed, family
):
    """Gold is byte-identical across formats, header extras carried and re-checked."""
    bundle = generate(seed, family)
    for task in bundle["tasks"]:
        report = renderers.verify_format_equivalence(bundle["context"], task)
        assert report["equivalent"], report["errors"]
        assert set(report["gold_by_format"]) == set(renderers.FORMATS)
        assert report["gold"] == task["answer"]
    pairs = [(b, task) for _, b in f_corpus() for task in b["tasks"]]
    assert len(pairs) >= 20
    assert all(
        renderers.verify_format_equivalence(b["context"], task)["equivalent"]
        for b, task in pairs
    )


@pytest.mark.parametrize("fmt", renderers.FORMATS)
@pytest.mark.parametrize("family", families.FAMILIES[:3])
def test_f_family_header_extras_round_trip(fmt, family):
    """The third pinned line carries the owning family's header extras.

    rule_holdout's rule declaration (family, structure text, modulus, labels) is
    part of the executable contract: a re-parsed world that cannot be
    re-executed is not a rendering, so a tampered extra line is refused.
    """
    bundle = generate(3, family)
    text = renderers.render_world(bundle["context"], family, fmt)
    if fmt == "jsonl":
        header = json.loads(text.splitlines()[0])
        carried = {
            key: value
            for key, value in header.items()
            if key not in ("schema", "family", "rules")
        }
    else:
        line = text.splitlines()[2]
        assert line.startswith("# extra: ")
        carried = json.loads(line[len("# extra: ") :])
    if family == "rule_holdout":
        header = json.loads(bundle["context"].splitlines()[0])
        expected = {
            key: value
            for key, value in header.items()
            if key not in ("schema", "family", "rules")
        }
        assert carried == expected
        for fmt2 in ("prose", "table"):
            rendered = renderers.render_world(bundle["context"], family, fmt2)
            lines = rendered.splitlines()
            extras = json.loads(lines[2][len("# extra: ") :])
            # Tampering the declared structure must fail the re-parse.
            tampered = lines.copy()
            if extras["rule_family"] == "threshold_class":
                other = "parity_vote"
            else:
                other = "threshold_class"
            tampered[2] = "# extra: " + json.dumps(
                {**extras, "rule_family": other}, sort_keys=True, separators=(",", ":")
            )
            with pytest.raises(ValueError):
                renderers.parse_world("\n".join(tampered), fmt2)
            # Dropping the line entirely is also a foreign contract.
            with pytest.raises(ValueError):
                renderers.parse_world("\n".join(lines[:2] + lines[3:]), fmt2)
    else:
        assert carried == {}


@pytest.mark.parametrize("fmt", renderers.FORMATS)
def test_f_family_lexical_overlap_measures_real_evidence(fmt):
    """Evidence fallback reads the owner's evidence-capable row types.

    The SMALL worlds carry locate_empty tasks whose whole evidence is the single
    alias declaration row, so their denominators are a handful of tokens and the
    handle the program binds necessarily reappears in it; the corpus the target
    applies to is the bank scale, measured here on real v2 shards.
    """
    values = [
        renderers.lexical_overlap(bundle["context"], task, fmt)
        for _, bundle in f_corpus()
        for task in bundle["tasks"]
    ]
    assert values and 0.0 < statistics.mean(values) < OVERLAP_TARGET
    for family in families.FAMILIES[:3]:
        shard = min(glob.glob(f"{DATA}/p70_superset_v2/shards/{family}-*/world.json"))
        with open(shard) as stream:
            world = json.load(stream)
        bank = [
            renderers.lexical_overlap(world["context"], task, fmt)
            for task in world["tasks"]
        ]
        assert bank and max(bank) < OVERLAP_TARGET
    # asof fallback with no consumed list is every event, not every "record".
    bundle = generate(0, "asof_state")
    rows = families.parse_context(bundle["context"])[1]
    assert all(row.type == "event" for row in rows)
    fallback = renderers.lexical_overlap(
        bundle["context"], {"question": bundle["tasks"][0]["question"]}, fmt
    )
    assert 0.0 < fallback < OVERLAP_TARGET


@pytest.mark.parametrize("family", families.FAMILIES[:3])
def test_f_family_banks_are_wide_enough_and_shape_distinct(family):
    """Every F row type carries a bank the records bank's size or a matched 4."""
    templates = renderers.FAMILY_TEMPLATES[family]
    for row_type, bank in templates.items():
        records_bank = (
            renderers.RECORD_TEMPLATES
            if row_type == "record"
            else renderers.REFERENCE_TEMPLATES
        )
        assert len(bank) >= len(records_bank) or len(bank) >= 4
        shapes = {re.sub(r"[A-Za-z0-9._-]+", "", t) for t in bank}
        assert len(shapes) == len(bank)


@pytest.mark.parametrize("fmt", renderers.FORMATS)
@pytest.mark.parametrize("family", families.FAMILIES[:3])
def test_f_family_a_distractor_row_never_changes_the_gold(fmt, family):
    """Removing a distractor's line leaves the answer; a consumed row changes it."""
    for seed in SEEDS[:2]:
        bundle = generate(seed, family)
        _, rows = families.parse_context(bundle["context"])
        owner = MODULES[family]
        for task in bundle["tasks"]:
            gold = owner.answer_value(task["answer"])
            types = renderers._EVIDENCE_TYPES[owner]
            distractors = [
                row
                for row in rows
                if row.type in types and row.id not in set(task["consumed"])
            ][:4]
            assert len(distractors) == 4
            for row in distractors:
                assert f_answer_without(bundle, fmt, row.id, task["question"]) == gold
            # The bundle's own measured necessity partition names the rows that
            # move the answer; a consumed row outside it (padding inside the
            # fold, a jointly-necessary demonstration) is not individually
            # decisive and its removal may well leave the gold intact. A
            # necessary row's removal changes the answer or stops it being
            # computable at all (a dropped demonstration can make the rule
            # ambiguous: the executor fails closed, which is also a change).
            by_id = {row.id: row for row in rows}
            necessary = [
                row_id for row_id in task["necessary"] if by_id[row_id].type in types
            ]
            assert necessary
            try:
                assert (
                    f_answer_without(bundle, fmt, necessary[0], task["question"])
                    != gold
                )
            except ValueError:
                pass  # the reduced world no longer computes an answer: a change


def f_answer_without(bundle, fmt, row_id, question):
    """Re-execute one F-family question with one row's line removed."""
    family = bundle["family"]
    _, rows = families.parse_context(bundle["context"])
    remaining, expected = without_row(
        renderers.render_world(bundle["context"], family, fmt), fmt, row_id, rows
    )
    assert remaining == expected
    # Re-render the surviving rows through the owner's own jsonl contract.
    header = json.loads(bundle["context"].splitlines()[0])
    extras = {
        key: value
        for key, value in header.items()
        if key not in ("schema", "family", "rules")
    }
    context = families.render_context(remaining, family, extras or None)
    return MODULES[family].answer_value(
        MODULES[family].solve_visible(context, question)
    )


def test_f_family_render_question_matches_the_owner_jsonl_instruction():
    """jsonl questions are byte-identical to the family's render_instruction."""
    for family in families.FAMILIES[:3]:
        bundle = generate(1, family)
        task = bundle["tasks"][0]
        assert renderers.render_question(
            task["question"], "jsonl", task["phrasing_index"]
        ) == families.render_instruction(
            family, task["question"], task["phrasing_index"]
        )
        for fmt in ("prose", "table"):
            text = renderers.render_question(task["question"], fmt, 1)
            assert renderers.validate_question(text, task["question"], fmt, 1)
            assert not renderers.validate_question(text, task["question"], fmt, 2)


def test_the_p70_superset_v2_shards_round_trip_and_agree():
    """One live shard per F family: three formats, gold byte-identical.

    The v2 bank is the real corpus this rendering exists for: its worlds carry
    header extras and multi-point demonstrations the synthetic SMALL worlds may
    not exercise.
    """
    for family in families.FAMILIES[:3]:
        shards = sorted(
            glob.glob(f"{DATA}/p70_superset_v2/shards/{family}-*/world.json")
        )
        assert shards, family
        with open(shards[0]) as stream:
            world = json.load(stream)
        _, rows = families.parse_context(world["context"])
        for fmt in renderers.FORMATS:
            text = renderers.render_world(world["context"], family, fmt)
            assert renderers.parse_world(text, fmt) == rows
        for task in world["tasks"]:
            report = renderers.verify_format_equivalence(world["context"], task)
            assert report["equivalent"], report["errors"]
            assert report["gold"] == task["answer"]
