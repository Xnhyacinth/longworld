"""Tests for the P74 W2-B structure-driven task bank (world_task_bank).

Charter coverage: .hl/design/p74_real_shared_worlds.md §16 W2-B row —
structure-driven generation over ANY SemanticWorld, no per-topic code; §2/§3
milestone-1 acceptance (new same-kind source yields scoped, correct,
text-supported tasks with different operations); §3.1 fact-reuse check.

Two worlds exercise the same code path:
- the demo observatory world (scripts.demo_p74_world.build_demo_world);
- a differently shaped second world (different families and relation kinds,
  no versioned facts) built here from raw structures, which must degrade
  honestly: families without structure are skipped with recorded reasons.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import dependency_ops as ops
from longworld.synthesis import shared_semantic_world as ssw
from longworld.synthesis.world_task_bank import (
    CAPABILITY_FAMILIES,
    TEMPLATES,
    build_task_bank,
    export_rows,
    generate_tasks,
)
from scripts.demo_p74_world import build_demo_world


@pytest.fixture(scope="module")
def bank():
    return build_task_bank(build_demo_world())


# --- §16 W2-B: every family produces >=5 tasks on the demo world ---


def test_every_family_has_at_least_five_tasks(bank):
    counts = bank.counts()
    for family in CAPABILITY_FAMILIES:
        assert counts[family] >= 5, f"{family} produced only {counts[family]}"


def test_task_ids_are_unique_and_family_tagged(bank):
    ids = [task.task_id for task in bank.tasks]
    assert len(ids) == len(set(ids))
    for task in bank.tasks:
        assert task.family in task.task_id


# --- every task executes and its answer is reproducible ---


def test_all_tasks_execute_with_answers(bank):
    world = build_demo_world()
    for task in bank.tasks:
        result = ops.execute(world, task.program, require_fold_gate=False)
        assert result.answer == task.answer, f"{task.task_id} answer drifted"


def test_multi_hop_tasks_pass_the_fold_gate(bank):
    world = build_demo_world()
    multi = bank.by_family("multi_hop")
    assert multi
    for task in multi:
        # require_fold_gate=True must ACCEPT (not raise) every emitted
        # multi-hop task: the gate is the family's admission contract
        result = ops.execute(world, task.program, require_fold_gate=True)
        assert result.metrics["non_foldable"] is True
        assert result.metrics["key_bindings"], task.task_id
        assert result.metrics["dependency_depth"] >= 5


def test_locate_and_aggregate_are_constant_programs(bank):
    # locate/aggregate legitimately compile as constant programs (the gate
    # is opt-in for them, charter §5); their depth is still real
    for task in bank.by_family("locate"):
        assert task.metrics["program_length"] == 2
    for task in bank.by_family("aggregate"):
        assert task.metrics["program_length"] == 4


# --- non-degeneracy ---


def test_aggregate_and_multi_hop_have_two_groups(bank):
    for task in bank.by_family("aggregate") + bank.by_family("multi_hop"):
        assert isinstance(task.answer, dict) and "groups" in task.answer
        assert len(task.answer["groups"]) >= 2, task.task_id


def test_locate_answers_are_not_empty(bank):
    for task in bank.by_family("locate"):
        assert task.answer is not None and task.answer != ""
        if isinstance(task.answer, tuple):
            assert task.answer


def test_multi_hop_consumes_more_than_a_locate(bank):
    locate_min = min(len(task.consumed_fact_ids) for task in bank.by_family("locate"))
    for task in bank.by_family("multi_hop"):
        assert len(task.consumed_fact_ids) > locate_min


# --- no per-topic code: questions reference only world structure ---


def _world_vocabulary(world: ssw.SemanticWorld) -> set[str]:
    vocab = set()
    for entity in world.entities:
        vocab.add(entity.label)
        vocab.update(entity.aliases)
    for fact in world.facts:
        vocab.add(fact.relation)
        vocab.add(str(fact.value))
        if fact.time:
            vocab.add(fact.time)
    for entity in world.entities:
        if entity.entity_type:
            vocab.add(entity.entity_type)
    return vocab


def _normalize(text: str) -> str:
    return (
        text.replace(",", " ")
        .replace("'", " ")
        .replace("(", " ")
        .replace(")", " ")
        .replace(";", " ")
        .replace(".", " ")
        .replace("!", " ")
    )


def _template_filler() -> set[str]:
    """All words the generic templates contribute (placeholders removed)."""
    filler: set[str] = set()
    for template in TEMPLATES.values():
        fields = _template_fields(template)
        rendered = template.format(**{k: "\x00" for k in fields})
        filler.update(_normalize(rendered.replace("\x00", " ")).split())
    return filler


def _describe_filler(bank, vocab_words: set[str]) -> set[str]:
    """Tokens in multi_hop questions that are not world tokens: the
    describe_program skeleton (LET, RETURN, reachable, members...)."""
    filler: set[str] = set()
    for task in bank.tasks:
        if task.family != "multi_hop":
            continue
        for word in _normalize(task.question).split():
            if word not in vocab_words:
                filler.add(word)
    return filler


def _template_fields(template: str) -> dict[str, str]:
    import string

    return {
        name: ""
        for _, name, _, _ in string.Formatter().parse(template)
        if name is not None
    }


def test_questions_use_only_world_vocabulary(bank):
    world = build_demo_world()
    # every non-template token must be a word of a world label, alias,
    # relation, value, date or family — no bank-invented vocabulary
    vocab_words: set[str] = set()
    for token in _world_vocabulary(world):
        vocab_words.update(_normalize(token).split())
    operation_words = {
        # operation names the {how} placeholder can carry
        "sum",
        "count",
        "max",
        "min",
        "high",
        "low",
        "medium",
    }
    filler = _template_filler() | _describe_filler(bank, vocab_words)
    filler |= operation_words
    for task in bank.tasks:
        for word in _normalize(task.question).split():
            if word in filler:
                continue
            assert word in vocab_words, (
                f"{task.task_id} question uses non-world token {word!r}"
            )


def test_no_topic_constant_appears_in_programs(bank):
    # the programs may only carry labels/relations/families that exist in
    # the world — no topic string from the bank's own code
    world = build_demo_world()
    labels = {entity.label for entity in world.entities}
    relations = {fact.relation for fact in world.facts}
    families = {entity.entity_type for entity in world.entities}
    dates = {fact.time for fact in world.facts if fact.time}
    for task in bank.tasks:
        for step in task.program["steps"]:
            for key in (
                "family",
                "label",
                "relation",
                "status_relation",
                "key",
                "field",
            ):
                value = step.get(key)
                if value is None:
                    continue
                if key == "family":
                    assert value in families
                elif key == "label":
                    assert value in labels
                elif key in ("relation", "status_relation", "key", "field"):
                    assert value in relations, f"{task.task_id}: {value!r}"
                else:
                    assert value in dates


# --- scope correctness: restricted world re-execution reproduces answers ---


def test_answers_reproduce_on_scope_restricted_worlds(bank):
    world = build_demo_world()
    for task in list(bank.tasks)[:8]:
        restricted = world.restricted_to(task.scope)
        result = ops.execute(restricted, task.program, require_fold_gate=False)
        assert result.answer == task.answer, (
            f"{task.task_id} answer changed under its own scope"
        )


# --- §3.1 fact reuse: tasks really share facts ---


def test_fact_reuse_is_non_zero(bank):
    assert bank.reuse_summary["facts_reused_ge2"] > 0
    assert bank.reuse  # fact_id -> >=2 consuming tasks
    for fact_id, count in bank.reuse.items():
        assert count >= 2
        assert fact_id in {fact.fact_id for fact in build_demo_world().facts}


def test_reuse_counts_match_proofs(bank):
    from collections import Counter

    counts = Counter()
    for task in bank.tasks:
        for fact_id in task.consumed_fact_ids:
            counts[fact_id] += 1
    assert bank.reuse == {k: v for k, v in counts.items() if v >= 2}


def test_proof_spans_point_at_world_text(bank):
    world = build_demo_world()
    docs = {doc.doc_id: doc for doc in world.documents}
    for task in bank.tasks:
        assert task.proof
        for item in task.proof:
            if item.kind != "fact":
                continue
            for span in item.spans:
                text = docs[span.doc_id].text[span.start : span.end]
                assert text  # non-empty, in-range span


# --- determinism: two runs produce identical banks ---


def test_generation_is_deterministic():
    world = build_demo_world()
    first = build_task_bank(world)
    second = build_task_bank(world)
    assert first.to_dict() == second.to_dict()


def test_budget_is_respected():
    world = build_demo_world()
    small = build_task_bank(
        world, {"locate": 2, "aggregate": 2, "multi_hop": 1, "as_of_state": 2}
    )
    assert len(small.by_family("locate")) <= 2
    assert len(small.by_family("aggregate")) <= 2
    assert len(small.by_family("multi_hop")) <= 1
    assert len(small.by_family("as_of_state")) <= 2


def test_generate_tasks_returns_list_of_specs(bank):
    world = build_demo_world()
    tasks = generate_tasks(world, {"locate": 3})
    assert isinstance(tasks, list)
    assert all(task.family == "locate" for task in tasks)
    assert len(tasks) <= 3


def test_export_rows_round_trips_json(bank):
    import json

    rows = export_rows(bank)
    payload = json.dumps(rows, ensure_ascii=False)
    loaded = json.loads(payload)
    assert len(loaded) == len(bank.tasks)
    for row, task in zip(loaded, bank.tasks):
        assert row["task_id"] == task.task_id
        assert row["family"] == task.family


# --- the second, differently shaped world: honest degradation ---


def _build_second_world() -> ssw.SemanticWorld:
    """A team world: different families, different relation kinds, NO
    versioned facts (no supersession, no revocation) and no rule chain.

    Structure: members ->(works_on)-> tasks; tasks ->(blocks)-> tasks;
    members carry skill (string) and hours_per_week (number); tasks carry
    priority (string) and estimate_days (number). The multi_hop and
    as_of_state families must be skipped with recorded reasons.
    """
    E = ssw.Entity
    entities = [
        E("M-1", "Dana", entity_type="member", doc_id="T1"),
        E("M-2", "Emil", entity_type="member", doc_id="T1"),
        E("M-3", "Frida", entity_type="member", doc_id="T1"),
        E("M-4", "Gopal", entity_type="member", doc_id="T1"),
        E("T-A", "auth-service", entity_type="task", doc_id="T2"),
        E("T-B", "billing-sync", entity_type="task", doc_id="T2"),
        E("T-C", "notif-router", entity_type="task", doc_id="T2"),
        E("T-D", "export-api", entity_type="task", doc_id="T2"),
    ]
    labels = {e.entity_id: e.label for e in entities}
    # (fact_id, subject, relation, value, value_type, time, version, quals, doc)
    raw = [
        ("G-01", "M-1", "works_on", "T-A", "entity", "2026-01-05", None, {}, "T1"),
        ("G-02", "M-2", "works_on", "T-A", "entity", "2026-01-05", None, {}, "T1"),
        ("G-03", "M-2", "works_on", "T-B", "entity", "2026-01-05", None, {}, "T1"),
        ("G-04", "M-3", "works_on", "T-B", "entity", "2026-01-05", None, {}, "T1"),
        ("G-05", "M-3", "works_on", "T-C", "entity", "2026-01-05", None, {}, "T1"),
        ("G-06", "M-4", "works_on", "T-D", "entity", "2026-01-05", None, {}, "T1"),
        ("G-07", "T-B", "blocks", "T-A", "entity", "2026-01-06", None, {}, "T2"),
        ("G-08", "T-C", "blocks", "T-B", "entity", "2026-01-06", None, {}, "T2"),
        ("G-09", "T-D", "blocks", "T-B", "entity", "2026-01-06", None, {}, "T2"),
        ("G-10", "M-1", "skill", "rust", "string", "2026-01-01", None, {}, "T1"),
        ("G-11", "M-2", "skill", "sql", "string", "2026-01-01", None, {}, "T1"),
        ("G-12", "M-3", "skill", "rust", "string", "2026-01-01", None, {}, "T1"),
        ("G-13", "M-4", "skill", "sql", "string", "2026-01-01", None, {}, "T1"),
        ("G-14", "M-1", "hours_per_week", 20, "number", "2026-01-01", None, {}, "T1"),
        ("G-15", "M-2", "hours_per_week", 30, "number", "2026-01-01", None, {}, "T1"),
        ("G-16", "M-3", "hours_per_week", 25, "number", "2026-01-01", None, {}, "T1"),
        ("G-17", "M-4", "hours_per_week", 10, "number", "2026-01-01", None, {}, "T1"),
        ("G-18", "T-A", "priority", "high", "string", "2026-01-02", None, {}, "T2"),
        ("G-19", "T-B", "priority", "high", "string", "2026-01-02", None, {}, "T2"),
        ("G-20", "T-C", "priority", "low", "string", "2026-01-02", None, {}, "T2"),
        ("G-21", "T-D", "priority", "medium", "string", "2026-01-02", None, {}, "T2"),
        ("G-22", "T-A", "estimate_days", 8, "number", "2026-01-02", None, {}, "T2"),
        ("G-23", "T-B", "estimate_days", 3, "number", "2026-01-02", None, {}, "T2"),
        ("G-24", "T-C", "estimate_days", 13, "number", "2026-01-02", None, {}, "T2"),
        ("G-25", "T-D", "estimate_days", 5, "number", "2026-01-02", None, {}, "T2"),
    ]
    t1_lines = [
        "Team rota, frozen January 2026.",
        "Dana works on auth-service; Emil works on auth-service and billing-sync.",
        "Frida works on billing-sync and notif-router; Gopal works on export-api.",
        "Dana's skill is rust and hours_per_week 20; Emil's skill is sql and hours_per_week 30.",
        "Frida's skill is rust and hours_per_week 25; Gopal's skill is sql and hours_per_week 10.",
    ]
    # one line per fact, in doc_order sequence, so the forward needle walk
    # cannot fail: G-07 (blocks auth-service), G-08 (blocks billing-sync),
    # G-09 (blocks billing-sync), then T-A/T-B/T-C/T-D attributes
    t2_lines = [
        "Task board, frozen January 2026.",
        "billing-sync blocks auth-service.",
        "notif-router blocks billing-sync.",
        "export-api blocks billing-sync.",
        "auth-service has priority high and estimate_days 8.",
        "billing-sync has priority high and estimate_days 3.",
        "notif-router has priority low and estimate_days 13.",
        "export-api has priority medium and estimate_days 5.",
    ]
    # needle per fact = the object's label (entity facts) or the value; the
    # order matches the values' appearance in each document's text
    doc_order = {
        "T1": [
            "G-01",
            "G-02",
            "G-03",
            "G-04",
            "G-05",
            "G-06",
            "G-10",
            "G-14",
            "G-11",
            "G-15",
            "G-12",
            "G-16",
            "G-13",
            "G-17",
        ],
        "T2": [
            "G-07",
            "G-08",
            "G-09",
            "G-18",
            "G-22",
            "G-19",
            "G-23",
            "G-20",
            "G-24",
            "G-21",
            "G-25",
        ],
    }
    needle_of = {
        fid: (labels[value] if vtype == "entity" else str(value))
        for (fid, _s, _r, value, vtype, _t, _v, _q, _d) in raw
    }
    documents = []
    spans: dict[str, ssw.SpanRef] = {}
    for doc_id, title, lines in (
        ("T1", "Team Rota", t1_lines),
        ("T2", "Task Board", t2_lines),
    ):
        text = "\n".join(lines)
        cursor = 0
        for fact_id in doc_order[doc_id]:
            needle = needle_of[fact_id]
            start = text.find(needle, cursor)
            assert start >= 0, f"{fact_id} needle {needle!r} missing in {doc_id}"
            spans[fact_id] = ssw.SpanRef(doc_id, start, start + len(needle))
            cursor = start + len(needle)
        documents.append(ssw.Document(doc_id, title, text))
    facts = tuple(
        ssw.Fact(
            fact_id=fid,
            subject=subject,
            relation=relation,
            value=value,
            value_type=vtype,
            time=time,
            version=version,
            qualifiers=quals,
            supporting_spans=(spans[fid],),
        )
        for (fid, subject, relation, value, vtype, time, version, quals, _doc) in raw
    )
    return ssw.SemanticWorld(tuple(documents), tuple(entities), facts)


@pytest.fixture(scope="module")
def second_world():
    return _build_second_world()


def test_second_world_produces_locate_and_aggregate(second_world):
    bank = build_task_bank(second_world)
    counts = bank.counts()
    assert counts["locate"] >= 5
    assert counts["aggregate"] >= 5


def test_second_world_skips_state_families_with_reasons(second_world):
    bank = build_task_bank(second_world)
    counts = bank.counts()
    assert counts["multi_hop"] == 0
    assert counts["as_of_state"] == 0
    skipped = {entry.family: entry.reason for entry in bank.skipped}
    assert "multi_hop" in skipped, "missing multi_hop skip reason"
    assert "as_of_state" in skipped, "missing as_of_state skip reason"
    # the reasons must name structure, not topics
    for reason in skipped.values():
        assert any(
            word in reason
            for word in (
                "versioned",
                "timeline",
                "resolve",
                "supersession",
                "revocation",
            )
        )


def test_second_world_questions_use_only_its_vocabulary(second_world):
    bank = build_task_bank(second_world)
    vocab_words: set[str] = set()
    for token in _world_vocabulary(second_world):
        vocab_words.update(_normalize(token).split())
    filler = _template_filler() | {
        "sum",
        "count",
        "max",
        "min",
    }
    for task in bank.tasks:
        for word in _normalize(task.question).split():
            if word in filler:
                continue
            assert word in vocab_words, (
                f"{task.task_id} uses non-world token {word!r} on second world"
            )


def test_second_world_tasks_execute(second_world):
    bank = build_task_bank(second_world)
    assert bank.tasks
    for task in bank.tasks:
        result = ops.execute(second_world, task.program, require_fold_gate=False)
        assert result.answer == task.answer


def test_second_world_answers_are_hand_checkable(second_world):
    """Hand-derived: M-2 works on {auth-service, billing-sync}; grouped by
    estimate_days those are {8: auth-service, 3: billing-sync}.

    Group-by-value on a numeric key splits singletons; grouped by priority
    both are high -> one group (degenerate, filtered). The count variant
    must report exactly one member per estimate_days group.
    """
    bank = build_task_bank(second_world)
    aggregate = bank.by_family("aggregate")
    assert aggregate
    seen = set()
    for task in aggregate:
        if task.structure_signals.get("entity") == "M-2" and task.structure_signals.get(
            "group_key"
        ) == "estimate_days":
            seen.add(task.structure_signals["how"])
            if task.structure_signals["how"] == "count":
                assert task.answer["groups"] == {"8": 1, "3": 1}
            elif task.structure_signals["how"] == "max":
                assert task.answer["groups"] == {"8": 8, "3": 3}
    # at least one estimate_days-keyed aggregate for M-2 was selected
    assert seen, "no estimate_days-keyed aggregate emitted for M-2"
    # locate: Dana's skill is rust, auth-service priority is high
    for task in bank.by_family("locate"):
        if task.structure_signals.get("relation") == "skill" and task.structure_signals.get("entity") == "M-1":
            assert task.answer == "rust"
        if task.structure_signals.get("relation") == "priority" and task.structure_signals.get("entity") == "T-A":
            assert task.answer == "high"


def test_second_world_no_state_families_with_zero_budget(second_world):
    # a zero multi_hop/as_of_state budget must skip those families but not
    # fabricate tasks: counts stay 0 and the skip reasons are structural
    bank = build_task_bank(
        second_world, {"locate": 3, "aggregate": 3, "multi_hop": 0, "as_of_state": 0}
    )
    assert bank.counts() == {"locate": 3, "aggregate": 3, "multi_hop": 0, "as_of_state": 0}

