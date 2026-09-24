"""P74 first deliverable in miniature: a simulated shared semantic world.

Builds one observatory-project SourceSnapshot -> SemanticWorld, runs the
charter §5 dependency chain (spec version -> rule -> instruments ->
observations with a revocation -> group/compare), prints the answer, the
proof's span lineage, and the intervention results (upstream mutation flips
the verdict; an unrelated observation does not).

This is simulated data — the real Wiki/repo snapshot wiring is the next wave
(T4 adapter). The world builder here is also the test fixture: the behavioral
tests import build_demo_world from this module, so the demo and the tests
exercise one world, not two.

Run: .venv/bin/python scripts/demo_p74_world.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import dependency_ops as ops
from longworld.synthesis import shared_semantic_world as ssw

AS_OF = "2026-09-01"


def _render_doc_with_spans(
    doc_id: str, title: str, lines: list[str], needle_order: list[tuple[str, str]]
) -> tuple[ssw.Document, dict[str, ssw.SpanRef]]:
    """Render one document whose facts' spans are valid by construction.

    `needle_order` lists (fact_id, needle) in the order the needles appear in
    the text; each is located by scanning forward from the previous match, so
    repeated values ("active", "boreal") yield distinct, correct spans.
    """
    text = "\n".join(lines)
    spans: dict[str, ssw.SpanRef] = {}
    cursor = 0
    for fact_id, needle in needle_order:
        start = text.find(needle, cursor)
        if start < 0:
            raise ValueError(f"{fact_id} needle {needle!r} not found in {doc_id}")
        spans[fact_id] = ssw.SpanRef(doc_id, start, start + len(needle))
        cursor = start + len(needle)
    return ssw.Document(doc_id, title, text), spans


def build_demo_world() -> ssw.SemanticWorld:
    """The observatory-project world: 14 objects, 39 facts, 3 documents."""
    E = ssw.Entity
    entities = [
        E("P-HALE", "Hale Atlas Survey", entity_type="project", doc_id="D1"),
        E("P-KIRK", "Kirkwood Near-Sky Survey", entity_type="project", doc_id="D1"),
        E("V-SPEC2", "spec v2", entity_type="method_version", doc_id="D2"),
        E("V-SPEC3", "spec v3", entity_type="method_version", doc_id="D2"),
        E("I-M1", "Mapper One", entity_type="instrument", doc_id="D2"),
        E("I-M2", "Mapper Two", entity_type="instrument", doc_id="D2"),
        E("I-M3", "Mapper Three", entity_type="instrument", doc_id="D2"),
        E("O-101", "obs-101", entity_type="observation", doc_id="D3"),
        E("O-102", "obs-102", entity_type="observation", doc_id="D3"),
        E("O-103", "obs-103", entity_type="observation", doc_id="D3"),
        E("O-104", "obs-104", entity_type="observation", doc_id="D3"),
        E("O-105", "obs-105", entity_type="observation", doc_id="D3"),
        E("O-106", "obs-106", entity_type="observation", doc_id="D3"),
        E("O-107", "obs-107", entity_type="observation", doc_id="D3"),
    ]
    labels = {e.entity_id: e.label for e in entities}
    # (fact_id, subject, relation, value, value_type, time, version, quals, doc)
    raw: list[tuple[str, str, str, Any, str, str | None, str | None, dict, str]] = [
        (
            "F-01",
            "P-HALE",
            "adopts_method",
            "V-SPEC2",
            "entity",
            "2026-03-01",
            None,
            {},
            "D2",
        ),
        (
            "F-02",
            "P-HALE",
            "adopts_method",
            "V-SPEC3",
            "entity",
            "2026-07-01",
            None,
            {},
            "D2",
        ),
        (
            "F-03",
            "V-SPEC2",
            "validity_rule",
            "noise_level <= 4.5",
            "rule",
            "2026-02-01",
            "v2",
            {},
            "D2",
        ),
        (
            "F-04",
            "V-SPEC3",
            "validity_rule",
            "noise_level <= 2.5",
            "rule",
            "2026-06-01",
            "v3",
            {},
            "D2",
        ),
        ("F-05", "I-M1", "noise_level", 4.2, "number", "2026-05-01", None, {}, "D2"),
        ("F-06", "I-M2", "noise_level", 6.5, "number", "2026-05-01", None, {}, "D2"),
        ("F-07", "I-M3", "noise_level", 2.1, "number", "2026-05-01", None, {}, "D2"),
        (
            "F-08",
            "P-HALE",
            "assigns_instrument",
            "I-M1",
            "entity",
            "2026-04-01",
            None,
            {},
            "D3",
        ),
        (
            "F-09",
            "P-HALE",
            "assigns_instrument",
            "I-M2",
            "entity",
            "2026-04-01",
            None,
            {},
            "D3",
        ),
        (
            "F-10",
            "P-HALE",
            "assigns_instrument",
            "I-M3",
            "entity",
            "2026-04-01",
            None,
            {},
            "D3",
        ),
        (
            "F-11",
            "P-KIRK",
            "assigns_instrument",
            "I-M2",
            "entity",
            "2026-04-01",
            None,
            {},
            "D3",
        ),
        ("F-12", "I-M1", "produces", "O-101", "entity", "2026-05-10", None, {}, "D3"),
        ("F-13", "I-M1", "produces", "O-102", "entity", "2026-05-11", None, {}, "D3"),
        ("F-14", "I-M2", "produces", "O-103", "entity", "2026-05-12", None, {}, "D3"),
        ("F-15", "I-M2", "produces", "O-104", "entity", "2026-05-12", None, {}, "D3"),
        ("F-16", "I-M3", "produces", "O-105", "entity", "2026-05-13", None, {}, "D3"),
        ("F-17", "I-M3", "produces", "O-106", "entity", "2026-05-13", None, {}, "D3"),
        ("F-18", "I-M1", "produces", "O-107", "entity", "2026-05-14", None, {}, "D3"),
        ("F-19", "O-101", "exposure_count", 30, "number", "2026-05-10", None, {}, "D3"),
        ("F-20", "O-102", "exposure_count", 4, "number", "2026-05-11", None, {}, "D3"),
        ("F-21", "O-103", "exposure_count", 35, "number", "2026-05-12", None, {}, "D3"),
        ("F-22", "O-104", "exposure_count", 12, "number", "2026-05-12", None, {}, "D3"),
        ("F-23", "O-105", "exposure_count", 2, "number", "2026-05-13", None, {}, "D3"),
        ("F-24", "O-106", "exposure_count", 25, "number", "2026-05-13", None, {}, "D3"),
        ("F-25", "O-107", "exposure_count", 50, "number", "2026-05-14", None, {}, "D3"),
        (
            "F-26",
            "O-101",
            "calibration_status",
            "active",
            "string",
            "2026-05-10",
            None,
            {},
            "D3",
        ),
        (
            "F-27",
            "O-102",
            "calibration_status",
            "active",
            "string",
            "2026-05-11",
            None,
            {},
            "D3",
        ),
        (
            "F-28",
            "O-103",
            "calibration_status",
            "active",
            "string",
            "2026-05-12",
            None,
            {},
            "D3",
        ),
        (
            "F-29",
            "O-104",
            "calibration_status",
            "active",
            "string",
            "2026-05-12",
            None,
            {},
            "D3",
        ),
        (
            "F-30",
            "O-105",
            "calibration_status",
            "active",
            "string",
            "2026-05-13",
            None,
            {},
            "D3",
        ),
        (
            "F-31",
            "O-106",
            "calibration_status",
            "active",
            "string",
            "2026-05-13",
            None,
            {},
            "D3",
        ),
        # the one revocation: O-107's calibration was withdrawn 2026-06-01
        (
            "F-32",
            "O-107",
            "calibration_status",
            "active",
            "string",
            "2026-05-14",
            None,
            {"revoked_at": "2026-06-01"},
            "D3",
        ),
        ("F-33", "O-101", "band", "aurora", "string", "2026-05-10", None, {}, "D3"),
        ("F-34", "O-102", "band", "boreal", "string", "2026-05-11", None, {}, "D3"),
        ("F-35", "O-103", "band", "aurora", "string", "2026-05-12", None, {}, "D3"),
        ("F-36", "O-104", "band", "boreal", "string", "2026-05-12", None, {}, "D3"),
        ("F-37", "O-105", "band", "aurora", "string", "2026-05-13", None, {}, "D3"),
        ("F-38", "O-106", "band", "boreal", "string", "2026-05-13", None, {}, "D3"),
        ("F-39", "O-107", "band", "boreal", "string", "2026-05-14", None, {}, "D3"),
    ]
    d1_lines = [
        "Two surveys share this observatory in 2026.",
        "The Hale Atlas Survey runs the wide-field program; the Kirkwood Near-Sky Survey runs the northern cadence program.",
    ]
    d2_lines = [
        "Method versions and instrument noise, frozen 2026-09.",
        "The Hale Atlas Survey adopted spec v2 on 2026-03-01 and spec v3 on 2026-07-01.",
        "spec v2 sets validity_rule noise_level <= 4.5; spec v3 sets validity_rule noise_level <= 2.5.",
        "Mapper One measured noise_level 4.2; Mapper Two measured noise_level 6.5; Mapper Three measured noise_level 2.1.",
    ]
    d3_lines = [
        "Observation ledger for 2026-05; calibration tracked as of 2026-09-01.",
        "The Hale Atlas Survey assigns_instrument Mapper One, Mapper Two and Mapper Three.",
        "The Kirkwood Near-Sky Survey assigns_instrument Mapper Two.",
        "Mapper One produces obs-101, obs-102 and obs-107; Mapper Two produces obs-103 and obs-104; Mapper Three produces obs-105 and obs-106.",
        "obs-101 records exposure_count 30 in band aurora with calibration_status active.",
        "obs-102 records exposure_count 4 in band boreal with calibration_status active.",
        "obs-103 records exposure_count 35 in band aurora with calibration_status active.",
        "obs-104 records exposure_count 12 in band boreal with calibration_status active.",
        "obs-105 records exposure_count 2 in band aurora with calibration_status active.",
        "obs-106 records exposure_count 25 in band boreal with calibration_status active.",
        "obs-107 records exposure_count 50 in band boreal with calibration_status active, revoked 2026-06-01.",
    ]
    doc_order = {
        "D1": [],
        "D2": [
            ("F-01", "spec v2"),
            ("F-02", "spec v3"),
            ("F-03", "noise_level <= 4.5"),
            ("F-04", "noise_level <= 2.5"),
            ("F-05", "4.2"),
            ("F-06", "6.5"),
            ("F-07", "2.1"),
        ],
        "D3": [
            ("F-08", "Mapper One"),
            ("F-09", "Mapper Two"),
            ("F-10", "Mapper Three"),
            ("F-11", "Mapper Two"),
            ("F-12", "obs-101"),
            ("F-13", "obs-102"),
            ("F-18", "obs-107"),
            ("F-14", "obs-103"),
            ("F-15", "obs-104"),
            ("F-16", "obs-105"),
            ("F-17", "obs-106"),
            ("F-19", "30"),
            ("F-33", "aurora"),
            ("F-26", "active"),
            ("F-20", "4"),
            ("F-34", "boreal"),
            ("F-27", "active"),
            ("F-21", "35"),
            ("F-35", "aurora"),
            ("F-28", "active"),
            ("F-22", "12"),
            ("F-36", "boreal"),
            ("F-29", "active"),
            ("F-23", "2"),
            ("F-37", "aurora"),
            ("F-30", "active"),
            ("F-24", "25"),
            ("F-38", "boreal"),
            ("F-31", "active"),
            ("F-25", "50"),
            ("F-39", "boreal"),
            ("F-32", "active"),
        ],
    }
    documents = []
    spans: dict[str, ssw.SpanRef] = {}
    needle_of = {
        fid: (labels[value] if vtype == "entity" else str(value))
        for (fid, _s, _r, value, vtype, _t, _v, _q, _d) in raw
    }
    for doc_id, title in (
        ("D1", "Observatory Programs"),
        ("D2", "Method and Instrument Registry"),
        ("D3", "Observation Ledger"),
    ):
        lines = {"D1": d1_lines, "D2": d2_lines, "D3": d3_lines}[doc_id]
        order = [(fact_id, needle_of[fact_id]) for fact_id, _ in doc_order[doc_id]]
        doc, doc_spans = _render_doc_with_spans(doc_id, title, lines, order)
        documents.append(doc)
        spans.update(doc_spans)
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


def demo_snapshot(world: ssw.SemanticWorld) -> ssw.SourceSnapshot:
    """Wrap the demo world's data in the §14 v1 snapshot contract."""
    return ssw.SourceSnapshot(
        snapshot_id="p74-demo-observatory-v1",
        frozen_at="2026-09-23",
        source={
            "kind": "simulated",
            "license": "none",
            "revisions": {},
            "fetched_via": "in-process",
        },
        documents=world.documents,
        entities=world.entities,
        facts=world.facts,
        relations=(),
    )


def chain_program() -> dict[str, Any]:
    """The charter §5 chain over the demo world."""
    return {
        "steps": [
            {
                "op": "bind",
                "out": "proj",
                "family": "project",
                "label": "Hale Atlas Survey",
            },
            {
                "op": "resolve_version",
                "out": "spec",
                "subject": "$proj",
                "relation": "adopts_method",
                "at": AS_OF,
            },
            {
                "op": "resolve_version",
                "out": "rule",
                "subject": "$spec",
                "relation": "validity_rule",
                "at": AS_OF,
            },
            {
                "op": "follow_relation",
                "out": "instr",
                "subject": "$proj",
                "relation": "assigns_instrument",
            },
            {"op": "apply_rule", "out": "kept", "rule": "$rule", "over": "$instr"},
            {
                "op": "join_on_bound_result",
                "out": "obs",
                "left": "$kept",
                "relation": "produces",
                "status_relation": "calibration_status",
                "at": AS_OF,
            },
            {"op": "group", "out": "byband", "items": "$obs", "key": "band"},
            {
                "op": "aggregate",
                "out": "res",
                "groups": "$byband",
                "how": "sum",
                "field": "exposure_count",
                "compare": True,
            },
        ],
        "return": "res",
    }


def locate_program() -> dict[str, Any]:
    """A plain locate task over the same world (shares fact F-05 with the chain)."""
    return {
        "steps": [
            {
                "op": "bind",
                "out": "inst",
                "family": "instrument",
                "label": "Mapper One",
            },
            {
                "op": "bind",
                "out": "noise",
                "subject": "$inst",
                "relation": "noise_level",
            },
        ],
        "return": "noise",
    }


def main() -> int:
    world = build_demo_world()
    print(
        f"world: {len(world.objects)} objects, {len(world.facts)} facts, {len(world.documents)} documents"
    )

    snapshot = demo_snapshot(world)
    print(
        f"snapshot: {snapshot.snapshot_id} roundtrip={'ok' if ssw.SourceSnapshot.from_dict(snapshot.to_dict()) == snapshot else 'FAIL'}"
    )

    program = chain_program()
    result = ops.execute(world, program)
    answer = result.answer
    print("\nchain program:")
    print("  " + ops.describe_program(program))
    print(
        f"answer: aurora={answer['groups']['aurora']} boreal={answer['groups']['boreal']} verdict={answer['verdict']}"
    )
    print(
        f"metrics: {result.metrics['program_length']} steps, depth {result.metrics['dependency_depth']}, "
        f"{result.metrics['constant_params']} constant params, {result.metrics['state_transition_depth']} state transitions"
    )
    print(f"consumed facts: {len(result.consumed_fact_ids)}")
    print("proof (first 6 lineage items):")
    for item in result.proof[:6]:
        where = ", ".join(f"{s.doc_id}[{s.start}:{s.end}]" for s in item.spans)
        what = (
            f"{item.subject}.{item.relation}={item.value!r}"
            if item.relation
            else f"object {item.subject} labeled {item.value!r}"
        )
        print(
            f"  step {item.step} {item.op}: {item.ref_id} ({what}) @ {where} -> {item.span_texts}"
        )

    upstream = ops.check_intervention(world, program, "F-02", "V-SPEC2", relation=True)
    print("\nintervention, upstream adoption F-02 -> spec v2:")
    print(
        f"  answer changed: {upstream['answer_changed']} (fact in proof: {upstream['fact_in_proof']})"
    )
    print(
        f"  base verdict={upstream['base_answer']['verdict']} mutated verdict={upstream['mutated_answer']['verdict']}"
    )

    unrelated = ops.check_intervention(world, program, "F-21", 99)
    print("intervention, unrelated observation F-21 (obs-103 exposure) -> 99:")
    print(
        f"  answer changed: {unrelated['answer_changed']} (fact in proof: {unrelated['fact_in_proof']})"
    )

    scope = ssw.ScopeEntry(
        task_id="T1",
        object_families=("project", "method_version", "instrument", "observation"),
        relations=(
            "adopts_method",
            "validity_rule",
            "assigns_instrument",
            "noise_level",
            "produces",
            "calibration_status",
            "band",
            "exposure_count",
        ),
        documents=("D2", "D3"),
    )
    rendered = world.render((scope,))
    recovered, scopes = ssw.from_rendered(rendered)
    reexec = ops.execute(recovered.restricted_to(scopes[0]), program)
    print(
        f"\nscope recovery: render -> parse -> restrict -> re-execute equals original: {reexec.answer == answer}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
