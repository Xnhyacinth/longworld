"""Tests for the P74 T7 support matrix and generation funnel report.

Charter coverage: .hl/design/p74_real_shared_worlds.md §16 T7 row — the
topic x capability x length support matrix computed from the real wave-2
artifacts, and the §11 milestone-2 six-stage funnel; §11/§16: infeasible and
empty cells never disappear ("不可行格不消失"); stages without data are
"not yet measured", never invented.

The pure core (family_cell / world_row / funnel arithmetic / rendering) is
tested on tiny fixture matrices built here; one smoke test then runs the
real gathering pipeline over the demo world to prove the CLI contract
end-to-end without the wiki snapshot dir dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.p74_support_matrix import (
    CAPABILITY_FAMILIES,
    band_cells,
    build_funnel,
    build_funnel_from_details,
    build_matrix,
    coverage_summary,
    family_cell,
    funnel_stage,
    not_measured_stage,
    render_band_table,
    render_funnel_table,
    render_matrix_table,
    render_report,
    validate_funnel,
)


# --- fixture: a tiny two-world matrix covering every cell status ---


def _fixture_details():
    """Two raw world details (as the gathering layer emits) covering every
    cell status: demo fully supported; wiki with 3 skips + 1 infeasible."""
    demo_cells = {family: family_cell(6) for family in CAPABILITY_FAMILIES}
    wiki_cells = {
        "locate": family_cell(0, skip_reason="no bindable object"),
        "aggregate": family_cell(
            0, infeasible_reason="versioned relations exist but 0 typed subjects"
        ),
        "multi_hop": family_cell(0, skip_reason="no versioned timeline"),
        "as_of_state": family_cell(0, skip_reason="no versioned facts"),
    }
    return [
        {
            "name": "demo-world",
            "kind": "demo",
            "topic": "synthetic demo",
            "pages": 3,
            "entities": 14,
            "typed_entities": 14,
            "facts": 39,
            "candidates_in": 99,
            "candidates_out": 94,
            "rejected": 5,
            "distinct_programs": 94,
            "tasks_exported": 24,
            "text_answerable": 24,
            "fold_total": 6,
            "fold_pass": 6,
            "family_cells": demo_cells,
        },
        {
            "name": "topic_a_snapshot",
            "kind": "wiki",
            "topic": "Category:A",
            "pages": 20,
            "entities": 792,
            "typed_entities": 0,
            "facts": 195,
            "candidates_in": 0,
            "candidates_out": 0,
            "rejected": 0,
            "distinct_programs": 0,
            "tasks_exported": 0,
            "text_answerable": 0,
            "fold_total": 0,
            "fold_pass": 0,
            "family_cells": wiki_cells,
        },
    ]


def _fixture_matrix():
    return build_matrix(_fixture_details(), LENGTH_REPORT_FIXTURE, include_demo=True)


def _fixture_rows():
    return _fixture_matrix()["rows"]


LENGTH_REPORT_FIXTURE = {
    "bands": [8192, 32768, 49152],
    "band_cells": [
        {
            "band": 8192,
            "assigned": ["demo-world"],
            "success": 0,
            "pending_expansion": 1,
            "failure": 0,
            "infeasible": 0,
            "empty": False,
        },
        {
            "band": 32768,
            "assigned": [],
            "success": 0,
            "pending_expansion": 0,
            "failure": 0,
            "infeasible": 0,
            "empty": True,
        },
        {
            "band": 49152,
            "assigned": ["topic_a_snapshot"],
            "success": 0,
            "pending_expansion": 0,
            "failure": 0,
            "infeasible": 1,
            "empty": False,
        },
    ],
    "band_assignment": [
        {
            "name": "demo-world",
            "band": 8192,
            "verdict": "needs-expansion",
            "feasible": False,
            "natural_tokens": 4057,
        },
        {
            "name": "topic_a_snapshot",
            "band": 49152,
            "verdict": "infeasible-overlength",
            "feasible": False,
            "natural_tokens": 52332,
        },
    ],
}


def test_rendering_shows_counts_and_codes():
    text = render_matrix_table(_fixture_rows())
    header = text.splitlines()[0]
    for family in CAPABILITY_FAMILIES:
        assert family in header
    assert "6" in text  # supported cell renders its task count
    assert "S" in text and "I" in text  # non-supported cells render codes


def test_infeasible_cells_stay_visible():
    # the aggregate infeasible cell must survive in the matrix and the legend
    matrix = _fixture_matrix()
    cell = matrix["rows"][1]["families"]["aggregate"]
    assert cell["status"] == "infeasible"
    assert cell["tasks"] == 0
    assert "typed" in cell["reason"]
    rendered = render_report(matrix, build_funnel(_fixture_stages()))
    assert "infeasible" in rendered
    codes = [c for c in matrix["reason_legend"] if c.startswith("I")]
    assert codes, "infeasible cells must carry legend codes"
    # unclassified zeros (neither skipped nor classified) also stay visible
    zero_detail = {
        "name": "mystery_world",
        "kind": "wiki",
        "topic": "Category:Mystery",
        "family_cells": {
            **{
                family: family_cell(0, skip_reason="x")
                for family in CAPABILITY_FAMILIES
            },
            "locate": family_cell(0),  # no reason at all
        },
    }
    matrix2 = build_matrix([zero_detail], LENGTH_REPORT_FIXTURE, include_demo=True)
    assert matrix2["rows"][0]["families"]["locate"]["status"] == "infeasible"
    assert "unclassified zero" in matrix2["rows"][0]["families"]["locate"]["reason"]


def test_reason_legend_maps_every_code_to_a_reason():
    matrix = _fixture_matrix()
    legend = matrix["reason_legend"]
    assert legend, "non-supported cells must be explained"
    for entry in legend.values():
        assert entry["reason"]
        assert entry["status"] in ("skipped", "infeasible")
    # every non-supported cell in the rows has a legend entry and vice versa
    non_supported = [
        (row["name"], family)
        for row in matrix["rows"]
        for family in CAPABILITY_FAMILIES
        if row["families"][family]["status"] != "supported"
    ]
    legend_pairs = {(e["world"], e["family"]) for e in legend.values()}
    assert legend_pairs == set(non_supported)


def test_coverage_summary_counts_every_cell():
    summary = coverage_summary(_fixture_rows())
    assert summary["worlds"] == 2
    assert summary["cells"] == 8
    assert summary["by_status"] == {"supported": 4, "skipped": 3, "infeasible": 1}
    assert summary["total_tasks"] == 24
    per_family = summary["per_family"]
    for family in CAPABILITY_FAMILIES:
        assert per_family[family]["supported"] == 1
    assert per_family["aggregate"]["infeasible"] == 1


def test_band_table_keeps_empty_bands():
    cells = band_cells(LENGTH_REPORT_FIXTURE)
    assert [c["band"] for c in cells] == [8192, 32768, 49152]
    text = render_band_table(cells)
    assert "EMPTY" in text
    assert "32768" in text  # the empty band itself is rendered, not dropped


# --- funnel arithmetic ---


def _fixture_stages():
    return [
        funnel_stage("source_availability", "topics", 2, 2),
        funnel_stage("fact_parse", "facts", 100, 90),
        funnel_stage("program_instantiability", "candidates", 90, 0),
        funnel_stage("semantic_dedup_retention", "programs", 0, 0),
        funnel_stage("text_answerability", "tasks", 0, 0),
        funnel_stage("dependency_profile_pass", "multi_hop tasks", 0, 0),
        not_measured_stage(
            "witness_row_distinction",
            "witness rows",
            "no witness has run over P74 tasks yet",
        ),
    ]


def test_funnel_in_ge_out_and_zero_rate_is_honest():
    stages = _fixture_stages()
    funnel = build_funnel(stages)
    for stage in funnel["stages"]:
        if stage["status"] == "not-yet-measured":
            continue
        assert stage["in"] >= stage["out"]
    # the earliest zeroing stage is the bottleneck, not the last zero
    assert funnel["bottleneck"]["stage"] == "program_instantiability"
    # in == 0 with out == 0 measures rate 0.0, not None, not 1.0
    tail = [s for s in funnel["stages"] if s["stage"] == "semantic_dedup_retention"][0]
    assert tail["in"] == 0 and tail["rate"] == 0.0


def test_not_yet_measured_stages_carry_no_numbers():
    funnel = build_funnel(_fixture_stages())
    witness = [s for s in funnel["stages"] if s["stage"] == "witness_row_distinction"][
        0
    ]
    assert witness["status"] == "not-yet-measured"
    assert witness["in"] is None and witness["out"] is None and witness["rate"] is None
    rendered = render_funnel_table(funnel)
    assert "not yet measured" in rendered


@pytest.mark.parametrize(
    "stage",
    [
        {"stage": "x", "status": "measured", "unit": "u", "in": 10, "out": 11},
        {"stage": "x", "status": "measured", "unit": "u", "in": -1, "out": -1},
        {
            "stage": "x",
            "status": "measured",
            "unit": "u",
            "in": 10,
            "out": 10,
            "rate": 1.5,
        },
    ],
)
def test_funnel_validation_rejects_bad_arithmetic(stage):
    with pytest.raises(ValueError):
        validate_funnel([stage])


def test_funnel_validation_rejects_not_measured_with_numbers():
    stage = not_measured_stage("witness", "rows", "no artifact")
    stage["in"] = 5  # numbers on a not-measured stage are a contradiction
    with pytest.raises(ValueError):
        validate_funnel([stage])


def test_real_topic_bottleneck_localizes_demo_masked_gap():
    stages = _fixture_stages()
    for stage in stages:
        if stage["status"] == "measured":
            stage["real_topic_in"] = stage["in"]
            stage["real_topic_out"] = (
                0 if stage["stage"] == "fact_parse" else stage["out"]
            )
    funnel = build_funnel(stages)
    assert funnel["real_topic_bottleneck"]["stage"] == "fact_parse"
    rendered = render_funnel_table(funnel)
    assert "real-topic bottleneck" in rendered


# --- the full assembly from world details (funnel over the same fixture) ---


def test_build_funnel_from_details_matches_matrix_cells():
    details = _fixture_details()
    manifest = {
        "snapshots": [{"name": "topic_a_snapshot", "category": "Category:A"}],
        "totals": {"facts": 195, "snapshots": 1},
    }
    funnel = build_funnel_from_details(details, manifest)
    by_stage = {s["stage"]: s for s in funnel["stages"]}
    # real-topic columns exclude the demo world entirely
    assert by_stage["program_instantiability"]["real_topic_in"] == 0
    assert by_stage["text_answerability"]["real_topic_in"] == 0
    assert funnel["real_topic_bottleneck"]["stage"] == "program_instantiability"
    # aggregate columns include the demo world's mass
    assert by_stage["program_instantiability"]["in"] == 99
    assert by_stage["text_answerability"]["out"] == 24
    assert by_stage["witness_row_distinction"]["status"] == "not-yet-measured"


# --- end-to-end smoke over the real demo world (no wiki dir dependency) ---


def test_matrix_renders_over_real_demo_world():
    from scripts.p74_support_matrix import _gather_demo

    detail = _gather_demo()
    assert detail["kind"] == "demo"
    matrix = build_matrix([detail], LENGTH_REPORT_FIXTURE, include_demo=True)
    rendered = render_report(matrix, build_funnel(_fixture_stages()))
    assert "demo-world" in rendered
    assert "locate" in rendered
    # all four families supported on the demo world at default budget
    for family in CAPABILITY_FAMILIES:
        cell = matrix["rows"][0]["families"][family]
        assert cell["status"] == "supported"
        assert cell["tasks"] == 6


def test_include_demo_false_drops_demo_row_only():
    matrix = build_matrix(_fixture_details(), LENGTH_REPORT_FIXTURE, include_demo=False)
    assert [row["name"] for row in matrix["rows"]] == ["topic_a_snapshot"]
    summary = matrix["coverage_summary"]
    assert summary["by_status"]["supported"] == 0
    assert summary["total_tasks"] == 0
