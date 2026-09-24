"""Relation evidence checks on frozen Wiki documents and a misleading claim."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from longworld.synthesis.shared_semantic_world import (
    Document,
    Entity,
    Fact,
    SemanticWorld,
    SpanRef,
)
from longworld.synthesis.wiki_evidence import check_locate_fact
from longworld.synthesis.wiki_world_bridge import (
    snapshot_to_world,
    structurally_typed_world,
)

SNAPSHOTS = (
    Path(__file__).resolve().parents[1] / "data/capability_records/p74_wiki_snapshot_v1"
)


def _world(name: str):
    snapshot = json.loads((SNAPSHOTS / f"{name}_snapshot.json").read_text())
    world, _ = structurally_typed_world(snapshot_to_world(snapshot))
    return world


def test_table_cell_requires_subject_header_and_value_alignment():
    world = _world("astronomical_observatories")
    fact = next(
        f
        for f in world.facts
        if world.label_of(f.subject) == "Abu Reyhan-e Birooni Observatory"
        and f.relation == "established in"
    )
    check = check_locate_fact(world, fact)
    assert check.supported
    assert "Name | Established | Location" == check.header
    assert "Abu Reyhan-e Birooni Observatory | 1976 |" in check.row
    assert not check_locate_fact(
        world,
        replace(
            fact,
            subject=next(
                e.entity_id
                for e in world.entities
                if e.label == "Algonquin Radio Observatory"
            ),
        ),
    ).supported
    assert not check_locate_fact(
        world, replace(fact, qualifiers={"table_column": "Location"})
    ).supported


def test_table_fact_cannot_borrow_an_older_header_after_column_changes():
    text = (
        "# List of test hospitals\n"
        "Name (other name) | Location | Established\n"
        "Alpha Hospital | Athens | 1900\n"
        "Name (other name) | Location | Established/New building\n"
        "Beta Hospital | Athens | 2000\n"
    )
    start = text.rindex("2000")
    fact = Fact(
        "f1",
        "e1",
        "established in",
        2000,
        "year",
        qualifiers={"table_column": "Established"},
        supporting_spans=(SpanRef("d1", start, start + 4),),
    )
    world = SemanticWorld(
        (Document("d1", "List of test hospitals", text),),
        (Entity("e1", "Beta Hospital"),),
        (fact,),
    )
    check = check_locate_fact(world, fact)
    assert not check.supported
    assert "nearest table header" in check.reason


def test_value_mention_alone_does_not_support_false_subject_claim():
    text = "Desert ecology is a field. Deserts are located in Antarctica."
    start = text.index("Antarctica")
    fact = Fact(
        "f1",
        "e1",
        "located in",
        "Antarctica",
        "string",
        supporting_spans=(SpanRef("d1", start, start + len("Antarctica")),),
    )
    world = SemanticWorld(
        (Document("d1", "Desert ecology", text),),
        (Entity("e1", "Desert ecology"),),
        (fact,),
    )
    check = check_locate_fact(world, fact)
    assert not check.supported
    assert "subject, relation and value" in check.reason


def test_prose_rejects_negation_and_accepts_subject_at_start():
    def checked(sentence: str):
        start = sentence.index("Safford")
        fact = Fact(
            "f1",
            "e1",
            "located in",
            "Safford",
            "string",
            supporting_spans=(SpanRef("d1", start, start + len("Safford")),),
        )
        world = SemanticWorld(
            (Document("d1", "Aker Observatory", sentence),),
            (Entity("e1", "Aker Observatory"),),
            (fact,),
        )
        return check_locate_fact(world, fact)

    assert checked("Aker Observatory is located in Safford.").supported
    assert not checked("Aker Observatory is not located in Safford.").supported
    assert not checked("Aker Observatory was never located in Safford.").supported


def test_infobox_key_supports_relation_but_markup_does_not():
    world = _world("cantilever_bridges")
    fact = next(
        f
        for f in world.facts
        if world.label_of(f.subject) == "Kömürhan Bridge" and f.relation == "closed in"
    )
    assert check_locate_fact(world, fact).supported
    markup = next(f for f in world.facts if "citation needed" in str(f.value).lower())
    assert not check_locate_fact(world, markup).supported
