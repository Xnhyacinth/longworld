"""Tests for the P74 T5 capacity-driven length controller (length_controller).

Charter coverage: .hl/design/p74_real_shared_worlds.md §7 — cap vs band mode
(±5%), semantic-capacity knobs, the three labeled lengthening paths, no K/H
reduction and no padding, window arithmetic (L_sys+ctx+query+answer+template
<= L_model; "256K input != 256K window"), band accounting with visible empty
cells, and honest estimator calibration.

The pinned tokenizer makes these tests slow to start (one ~35s cold load,
cached afterwards), so the suite is module-scoped where possible.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import length_controller as lc
from longworld.synthesis import shared_semantic_world as ssw
from scripts.demo_p74_world import build_demo_world

_FILLER_PATTERNS = (
    r"\bpad\b",
    r"\bpadding\b",
    r"\bpadded\b",
    r"\bfiller\b",
    r"\blorem\b",
    r"\bipsum\b",
    r"\bplaceholder\b",
    r"\bdummy\b",
    r"\bxxxxx+\b",
    r"\baaaaa+\b",
    r"\bdeadbeef\b",
)


def _assert_no_filler(value, path="plan") -> None:
    """No padding/filler strings anywhere in a rendered plan.

    The module's own no-shrink declaration ("... no padding strings") is legal
    language, not filler content, so those exact occurrences are stripped
    before scanning.
    """
    text = json.dumps(value, default=str).lower()
    text = text.replace(lc.NO_SHRINK_NOTE.lower(), " ")
    for pattern in _FILLER_PATTERNS:
        found = re.search(pattern, text)
        assert found is None, (
            f"filler pattern {pattern!r} found in {path}: {found.group(0)!r}"
        )


@pytest.fixture(scope="module")
def world():
    return build_demo_world()


@pytest.fixture(scope="module")
def demo_tokens(world):
    return lc.measure(world)


# --- measure(): pinned tokenizer, str and SemanticWorld ---


def test_measure_string_and_world(world):
    assert lc.measure("hello world") == 2
    tokens = lc.measure(world)
    assert tokens > 0
    # a world's natural render is exactly the tokenizer's count of the text
    assert tokens == lc.measure(world.render((lc.natural_scope(world),)))


def test_measure_rejects_other_types():
    with pytest.raises(TypeError):
        lc.measure(123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        lc.measure(None)  # type: ignore[arg-type]


def test_tokenizer_is_pinned():
    tokenizer = lc.get_tokenizer()
    assert tokenizer.name_or_path == lc.TOKENIZER_MODEL
    assert lc.TOKENIZER_MODEL == "Qwen/Qwen3.5-4B"
    assert lc.TOKENIZER_REVISION == "a7b0d22b993d71000cf2eadfb37222a67cee521e"


# --- estimate_capacity(): structure -> predicted tokens ---


def test_estimate_capacity_world_and_dict(world, demo_tokens):
    predicted = lc.estimate_capacity(world)
    # in-family: within the declared tolerance on the calibration set
    assert abs(predicted - demo_tokens) / demo_tokens <= lc.CALIBRATION_TOLERANCE
    # a full structure dict (counts + payload chars) matches the world estimate
    feats = lc.structure_of(world)
    assert lc.estimate_capacity(feats) == predicted
    # counts-only defaults are forward-planning constants (average payloads
    # over the calibration set): positive, and conservative (over, not under)
    counts_only = lc.estimate_capacity(
        {"entity_count": 14, "fact_count": 39, "doc_count": 3}
    )
    assert counts_only > 0
    assert counts_only >= predicted


def test_structure_of_monotone_in_knobs(world):
    base = lc.structure_of(world)
    more_facts = dict(base)
    more_facts["fact_count"] = base["fact_count"] + 10
    more_facts["fact_payload_chars"] = base["fact_payload_chars"] + 10 * 60
    assert lc.estimate_capacity(more_facts) > lc.estimate_capacity(base)
    more_docs = dict(base)
    more_docs["doc_count"] = base["doc_count"] + 2
    more_docs["doc_chars"] = base["doc_chars"] + 2 * 5000
    assert lc.estimate_capacity(more_docs) > lc.estimate_capacity(base)
    # zero-structure world degenerates to the non-negative header/scope floor
    assert lc.estimate_capacity({}) >= 0


# --- cap mode: feasible iff natural <= cap; actual reported ---


def test_cap_mode_acceptance(world, demo_tokens):
    ok = lc.plan(world, "cap", demo_tokens)
    assert ok.feasible and ok.verdict == "within-cap"
    assert ok.natural_tokens == demo_tokens  # actual length recorded as-is
    # no edge chasing: a shorter cap under a huge world stays feasible
    assert lc.plan(world, "cap", demo_tokens + 1).feasible
    # window limits apply even in cap mode: the window must hold the context
    # PLUS the system/query/answer/template overheads
    overhead = (
        lc.DEFAULT_SYSTEM_TOKENS
        + lc.DEFAULT_QUERY_TOKENS
        + lc.DEFAULT_ANSWER_RESERVE
        + lc.DEFAULT_TEMPLATE_TOKENS
    )
    assert lc.plan(
        demo_tokens, "cap", demo_tokens, model_window=demo_tokens + overhead
    ).feasible
    assert (
        lc.plan(
            demo_tokens, "cap", demo_tokens, model_window=demo_tokens + overhead - 1
        ).verdict
        == "infeasible-window"
    )


def test_cap_mode_over_cap_records_infeasible(world, demo_tokens):
    over = lc.plan(world, "cap", demo_tokens - 1)
    assert not over.feasible and over.verdict == "over-cap"
    assert over.natural_tokens == demo_tokens
    _assert_no_filler(over.to_dict())
    # the plan never suggests shrinking the world to fit the cap
    assert not any(
        "reduce" in note.lower() or "shrink" in note.lower() for note in over.notes
    ) or all("not" in note.lower() for note in over.notes if "shrink" in note.lower())


# --- band mode: ±5% ---


def test_band_mode_acceptance_boundaries(world, demo_tokens):
    # exactly at center and at both ±5% edges is in-band
    for length in (
        demo_tokens,
        int(demo_tokens * 1.04),
        int(demo_tokens * 0.96),
    ):
        verdict = lc.plan(length, "band", demo_tokens)
        assert verdict.feasible and verdict.verdict == "in-band", length
    # outside the ±5% window on either side is not feasible
    assert not lc.plan(int(demo_tokens * 1.06), "band", demo_tokens).feasible
    assert not lc.plan(int(demo_tokens * 0.94), "band", demo_tokens).feasible


def test_band_mode_tolerance_is_five_percent():
    assert lc.BAND_TOLERANCE == 0.05


# --- infeasible verdicts never resolve via padding ---


def test_short_world_never_padded(world, demo_tokens):
    short = lc.plan(world, "band", demo_tokens * 8)
    assert not short.feasible
    assert short.verdict == "needs-expansion"
    payload = short.to_dict()
    _assert_no_filler(payload)
    # the plan proposes labeled paths, never padding
    for path in short.recommended_paths:
        assert path.path in (
            lc.PATH_DENSE_INTEGRATION,
            lc.PATH_DISTANCE_DISTRACTOR,
            lc.PATH_STATE_GRAPH,
        )
        assert path.per_item_tokens > 0
        assert path.items_to_band >= 0
    # every note mentions either the gap, the honest limits, or the no-shrink rule
    assert any("natural length" in note for note in short.notes)


def test_three_paths_labeled_per_charter(world, demo_tokens):
    short = lc.plan(world, "band", demo_tokens * 8)
    names = {p.path for p in short.recommended_paths}
    # demo world has versions + a revocation: state-graph path must be offered
    assert names == {
        lc.PATH_DENSE_INTEGRATION,
        lc.PATH_DISTANCE_DISTRACTOR,
        lc.PATH_STATE_GRAPH,
    }
    dense = next(
        p for p in short.recommended_paths if p.path == lc.PATH_DENSE_INTEGRATION
    )
    distractor = next(
        p for p in short.recommended_paths if p.path == lc.PATH_DISTANCE_DISTRACTOR
    )
    # the labels are charter §7's, and distractor expansion is explicitly not
    # passed off as new deep reasoning
    assert "dense-integration" in dense.description
    assert "distance-distractor" in distractor.description
    assert "retrieval" in distractor.description
    # arithmetic sanity: adding the priced items must actually reach the band floor
    lo = demo_tokens * 8 * 0.95
    for path in short.recommended_paths:
        reached = demo_tokens + path.items_to_band * path.per_item_tokens
        assert reached >= lo
        assert reached <= demo_tokens * 8 * 1.05 + path.per_item_tokens


def test_state_graph_only_offered_with_timeline():
    # a bare token count has no timeline, so the state-graph path disappears
    plan = lc.plan(4000, "band", 32768)
    names = {p.path for p in plan.recommended_paths}
    assert lc.PATH_STATE_GRAPH not in names
    assert names == {lc.PATH_DENSE_INTEGRATION, lc.PATH_DISTANCE_DISTRACTOR}


def test_infeasible_with_capacity_verdict(world, demo_tokens):
    # the gap to the 64K band cannot fit inside a small model window: no
    # legal path closes it, so the world stays at its natural length
    far = lc.plan(world, "band", 65536, model_window=8192)
    assert far.window.fits  # the natural length itself fits the small window
    assert far.verdict == "infeasible-with-capacity"
    assert not far.feasible
    assert far.gap_tokens > far.window.slack  # the gap exceeds all legal slack
    assert all(not p.closes_gap for p in far.recommended_paths)
    _assert_no_filler(far.to_dict())
    assert any("stays at its natural length" in note for note in far.notes)


# --- band selection: visible empty cells, per-band counts ---


def test_select_views_reports_empty_bands(world):
    selection = lc.select_views(
        [("demo", world)], bands=(8192, 32768, 65536, 131072, 262144)
    )
    assert selection.bands == (8192, 32768, 65536, 131072, 262144)
    by_band = {cell.band: cell for cell in selection.cells}
    # the demo world lands in the 8K band as needs-expansion, not as a success
    assert by_band[8192].assigned == ("demo",)
    assert by_band[8192].pending_expansion == 1
    # empty bands are visible, not dropped
    for band in (32768, 65536, 131072, 262144):
        assert by_band[band].empty
        assert (
            by_band[band].success
            + by_band[band].pending_expansion
            + by_band[band].failure
            + by_band[band].infeasible
            == 0
        )
    # totals add up: exactly one verdict per world
    assert (
        sum(
            cell.success + cell.pending_expansion + cell.failure + cell.infeasible
            for cell in selection.cells
        )
        == 1
    )


def test_select_views_accepts_counts_and_counts_bands():
    selection = lc.select_views(
        [8000, 31000, 33000, 130000, 250000], bands=lc.DEFAULT_BANDS
    )
    rows = {row["name"]: row for row in selection.world_rows}
    assert [r["band"] for r in selection.world_rows] == [
        8192,
        32768,
        32768,
        131072,
        262144,
    ]
    assert rows["length-001"]["verdict"] == "in-band"
    assert rows["length-002"]["verdict"] == "needs-expansion"
    by_band = {cell.band: cell for cell in selection.cells}
    assert by_band[8192].success == 1
    assert by_band[32768].pending_expansion == 1
    assert by_band[65536].empty


def test_nearest_band_deterministic():
    bands = (8192, 32768, 49152, 65536, 98304, 131072, 262144)
    assert lc._nearest_band(8192, bands) == 8192
    assert lc._nearest_band(9000, bands) == 8192
    assert lc._nearest_band(64000, bands) == 65536
    assert lc._nearest_band(150000, bands) == 131072
    # geometric-mid ties break to the smaller band
    assert lc._nearest_band(int((32768 * 49152) ** 0.5), bands) == 32768
    with pytest.raises(ValueError):
        lc._nearest_band(0, bands)
    with pytest.raises(ValueError):
        lc._nearest_band(100, ())


# --- window budget arithmetic ---


def test_window_budget_arithmetic():
    budget = lc.window_budget(250000)
    assert budget.total == 512 + 250000 + 256 + 1024 + 128
    assert budget.total <= budget.model_window and budget.fits
    assert budget.slack == 262144 - budget.total
    over = lc.window_budget(261000)
    assert not over.fits and over.slack < 0
    with pytest.raises(ValueError):
        lc.window_budget(-1)


def test_256k_input_is_not_256k_window():
    """The charter's '256K 输入 ≠ 256K 模型窗口' statement, as a check."""
    window = 262144
    overhead = (
        lc.DEFAULT_SYSTEM_TOKENS
        + lc.DEFAULT_QUERY_TOKENS
        + lc.DEFAULT_ANSWER_RESERVE
        + lc.DEFAULT_TEMPLATE_TOKENS
    )
    max_context = window - overhead
    assert max_context < 262144
    assert lc.window_budget(max_context).fits
    assert not lc.window_budget(max_context + 1).fits


# --- calibration honesty ---


def test_calibration_report_within_declared_tolerance():
    demo = build_demo_world()
    report = lc.calibration_report(lc.calibration_worlds(demo))
    assert len(report["rows"]) == 9
    assert report["max_abs_error_pct"] <= report["declared_tolerance_pct"]
    assert report["within_tolerance"]
    # the table carries predicted AND measured for every calibration world
    for row in report["rows"]:
        assert row["measured_tokens"] > 0
        assert row["predicted_tokens"] > 0
        assert abs(row["error_pct"]) < 100


def test_scaled_worlds_are_valid_semantic_worlds():
    # calibration worlds must themselves pass the strict world validation
    for name, params in lc.CALIBRATION_PARAMS:
        world = lc.build_scaled_world(*params)
        assert len(world.facts) > 0
        # spans are valid by construction
        docs = {doc.doc_id: doc for doc in world.documents}
        for fact in world.facts:
            span = fact.supporting_spans[0]
            text = docs[span.doc_id].text
            assert 0 <= span.start < span.end <= len(text)


# --- plan() guards ---


def test_plan_rejects_bad_mode_and_target(world):
    with pytest.raises(ValueError, match="mode"):
        lc.plan(world, "filler", 8192)
    with pytest.raises(ValueError, match="target"):
        lc.plan(world, "band", 0)
    with pytest.raises(ValueError, match="target"):
        lc.plan(world, "cap", -5)
