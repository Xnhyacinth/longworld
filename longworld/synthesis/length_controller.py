"""P74 T5: capacity-driven length controller with band accounting.

Implements charter .hl/design/p74_real_shared_worlds.md §7 (长度控制):

- Two modes. **Cap mode** (training default): a view is feasible iff its
  natural length is at most the cap; the real length is recorded as-is, no
  edge-chasing. **Band mode** (curriculum / controlled experiments): feasible
  iff |natural - target| <= band_tolerance * target with band_tolerance = 0.05
  (the ±5% engineering delta, δ).
- No illegal shortening: a world longer than its target is recorded
  infeasible-overlength — never "quietly reduce K/H", never pad with
  meaningless strings. A world with no capacity to reach a band stays at its
  natural length (verdict infeasible-with-capacity).
- Three legal lengthening paths, labeled per §7: dense-integration expansion
  (more related facts), distance-distractor expansion (more distractor
  documents — a valid retrieval-training augmentation that must not be passed
  off as new deep reasoning), and state-graph expansion (timeline depth; only
  offered when the world actually has a state timeline).
- Window budget arithmetic: L_system + L_context + L_query + answer_reserve +
  template <= L_model. "256K of input text" is not "a 256K model window": the
  non-context components consume part of the window, so a 256K window hosts at
  most window - overhead context tokens. The controller budgets and verifies
  256K capability (the 262144 band) but does not require any world to reach it
  in the first wave; worlds without capacity stop at their natural length.

Capacity estimation (semantic capacity, not just length_records): a calibrated
per-section model of the SemanticWorld render. The render is deterministic
given structure, so each section is modeled physically:

    header   : constant
    scope    : const + per_entry + per_family + per_relation + per_doc
    entities : rate * (skeleton_chars * n + payload_chars)
    facts    : rate * (skeleton_chars * n + payload_chars)
    documents: per_doc_count * n + per_char * chars

Constants were least-squares fitted on the calibration set (the demo world
plus the scaled worlds below; see CALIBRATION_PARAMS) with the pinned
tokenizer, and are frozen here. The fitted coefficients are all positive
(counts and payload both add length), so the model extrapolates safely.
Declared in-family tolerance: CALIBRATION_TOLERANCE = 8% (measured max
end-to-end error is 5.4% on the smallest world, <= 1% above ~44K tokens).
Out-of-family limit, reported honestly: the document-section rate is
calibrated on simulated ledger prose; real Wikipedia prose tokenizes ~15%
denser, so document-heavy real-source worlds are under-predicted — use
measure() for exact numbers there.

Deterministic: no RNG, no network; the tokenizer is pinned by (model,
revision) and cached. Standard library + transformers (already a project
dependency) only.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Sequence

from longworld.synthesis import shared_semantic_world as ssw

# --- pinned tokenizer (same recipe as the training/export side) ---

TOKENIZER_MODEL = "Qwen/Qwen3.5-4B"
TOKENIZER_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"

# --- bands (tokens) and mode constants ---

STANDARD_BANDS = (8192, 32768, 65536, 131072, 262144)
NONSTANDARD_BANDS = (49152, 98304)
DEFAULT_BANDS = tuple(sorted(STANDARD_BANDS + NONSTANDARD_BANDS))
BAND_TOLERANCE = 0.05  # charter §7: δ = ±5%, engineering configuration

# --- window budget defaults (tokens); "256K input != 256K model window" ---

DEFAULT_MODEL_WINDOW = 262144
DEFAULT_SYSTEM_TOKENS = 512
DEFAULT_QUERY_TOKENS = 256
DEFAULT_ANSWER_RESERVE = 1024
DEFAULT_TEMPLATE_TOKENS = 128

# --- calibrated capacity constants (see module docstring) ---

HEADER_TOKENS = 18
SCOPE_CONST = -0.404
SCOPE_PER_ENTRY = 21.5886
SCOPE_PER_FAMILY = 2.1965
SCOPE_PER_RELATION = 3.6788
SCOPE_PER_DOC = 2.7378
ENTITY_SKELETON_CHARS = 44
ENTITY_RATE = 0.36747
FACT_SKELETON_CHARS = 134
FACT_RATE = 0.40924
DOC_PER_COUNT = 6.964
DOC_PER_CHAR = 0.321545

# counts-only defaults (average payload per item over the calibration set)
DEFAULT_ENTITY_PAYLOAD_CHARS = 25.8
DEFAULT_FACT_PAYLOAD_CHARS = 84.7
DEFAULT_DOC_SECTION_CHARS = 5190.0

CALIBRATION_TOLERANCE = 0.08

# marginal cost of each legal lengthening path (tokens per added item)
TOKENS_PER_FACT = round(
    FACT_RATE * (FACT_SKELETON_CHARS + DEFAULT_FACT_PAYLOAD_CHARS), 1
)  # ~89.5
TOKENS_PER_STATE_ENTRY = TOKENS_PER_FACT  # state history entries are facts
TOKENS_PER_DOC = round(
    DOC_PER_COUNT + DOC_PER_CHAR * DEFAULT_DOC_SECTION_CHARS, 1
)  # ~1675.7

PATH_DENSE_INTEGRATION = "dense-integration"
PATH_DISTANCE_DISTRACTOR = "distance-distractor"
PATH_STATE_GRAPH = "state-graph"

_PATH_DESCRIPTIONS = {
    PATH_DENSE_INTEGRATION: (
        "dense-integration expansion (charter §7 密集整合扩展): add more "
        "related facts on existing subjects/relations; increases reasoning "
        "density, not just retrieval distance"
    ),
    PATH_DISTANCE_DISTRACTOR: (
        "distance-distractor expansion (charter §7 距离干扰扩展): add more "
        "distractor documents; a valid retrieval-training augmentation, but it "
        "does not by itself add new deep reasoning and must not be counted as such"
    ),
    PATH_STATE_GRAPH: (
        "state-graph expansion (charter §7 状态图扩展): extend the state "
        "timeline (more versions, revisions or revocations); only offered when "
        "the world already has a state timeline"
    ),
}

NO_SHRINK_NOTE = (
    "shrinking is not a legal operation here: no K/H reduction, no padding "
    "strings; the world stays at its natural length and the cell is recorded "
    "infeasible"
)


@lru_cache(maxsize=2)
def _pinned_tokenizer(model: str, revision: str):
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:  # pragma: no cover - venv always has transformers
        raise RuntimeError(
            "transformers is required for exact token measurement "
            "(pinned tokenizer recipe)"
        ) from exc
    return AutoTokenizer.from_pretrained(
        model, revision=revision, local_files_only=True, trust_remote_code=False
    )


def get_tokenizer():
    """The pinned tokenizer used for every exact measurement here."""
    return _pinned_tokenizer(TOKENIZER_MODEL, TOKENIZER_REVISION)


# --- measurement ---


def natural_scope(world: ssw.SemanticWorld) -> ssw.ScopeEntry:
    """The full-scope entry: every family, relation and document visible."""
    families = tuple(
        sorted(
            {e.entity_type for e in world.entities}, key=lambda v: (v is None, v or "")
        )
    )
    relations = tuple(sorted({f.relation for f in world.facts}))
    documents = tuple(d.doc_id for d in world.documents)
    return ssw.ScopeEntry(
        task_id="natural",
        object_families=families,
        relations=relations,
        documents=documents,
    )


def measure(source: "str | ssw.SemanticWorld") -> int:
    """Exact token count of a text, or of a world's full natural render."""
    if isinstance(source, ssw.SemanticWorld):
        text = source.render((natural_scope(source),))
    elif isinstance(source, str):
        text = source
    else:
        raise TypeError(f"measure accepts str or SemanticWorld, got {type(source)!r}")
    tokenizer = get_tokenizer()
    return len(tokenizer(text).input_ids)


# --- capacity estimation ---


def entity_payload_chars(entity: ssw.Entity) -> int:
    return len(entity.entity_id) + len(entity.label) + len(entity.entity_type or "")


def fact_payload_chars(fact: ssw.Fact) -> int:
    qualifiers = json.dumps(fact.qualifiers, sort_keys=True, separators=(",", ":"))
    spans = sum(
        len(span.doc_id) + len(str(span.start)) + len(str(span.end)) + 22
        for span in fact.supporting_spans
    )
    return (
        len(fact.fact_id)
        + len(fact.subject)
        + len(fact.relation)
        + len(str(fact.value))
        + len(fact.value_type)
        + len(fact.unit or "")
        + len(fact.time or "")
        + len(fact.version or "")
        + len(qualifiers)
        + spans
    )


def doc_section_chars(doc: ssw.Document) -> int:
    return len(doc.doc_id) + len(doc.title) + len(doc.text)


def structure_of(source: "ssw.SemanticWorld | dict[str, Any]") -> dict[str, float]:
    """The semantic-capacity knobs (charter §7) as one feature dict.

    Accepts a SemanticWorld (payload chars summed exactly, no tokenizer) or a
    plain counts dict; missing optional keys take calibrated defaults.
    """
    if isinstance(source, ssw.SemanticWorld):
        world = source
        return {
            "entity_count": float(len(world.entities)),
            "entity_payload_chars": float(
                sum(entity_payload_chars(e) for e in world.entities)
            ),
            "fact_count": float(len(world.facts)),
            "fact_payload_chars": float(
                sum(fact_payload_chars(f) for f in world.facts)
            ),
            "relation_count": float(len({f.relation for f in world.facts})),
            "family_count": float(len({e.entity_type for e in world.entities})),
            "doc_count": float(len(world.documents)),
            "doc_chars": float(sum(doc_section_chars(d) for d in world.documents)),
            "scope_entries": 1.0,
        }
    if isinstance(source, dict):
        structure = dict(source)
        n_ent = float(structure.get("entity_count", 0.0))
        n_fact = float(structure.get("fact_count", 0.0))
        n_doc = float(structure.get("doc_count", 0.0))
        return {
            "entity_count": n_ent,
            "entity_payload_chars": float(
                structure.get(
                    "entity_payload_chars", n_ent * DEFAULT_ENTITY_PAYLOAD_CHARS
                )
            ),
            "fact_count": n_fact,
            "fact_payload_chars": float(
                structure.get("fact_payload_chars", n_fact * DEFAULT_FACT_PAYLOAD_CHARS)
            ),
            "relation_count": float(structure.get("relation_count", n_fact)),
            "family_count": float(structure.get("family_count", 1.0)),
            "doc_count": n_doc,
            "doc_chars": float(
                structure.get("doc_chars", n_doc * DEFAULT_DOC_SECTION_CHARS)
            ),
            "scope_entries": float(structure.get("scope_entries", 1.0)),
        }
    raise TypeError(f"structure_of accepts SemanticWorld or dict, got {type(source)!r}")


def estimate_capacity(structure: "ssw.SemanticWorld | dict[str, Any]") -> int:
    """Predicted token count of the full natural render, from structure."""
    feats = structure_of(structure)
    scope = SCOPE_CONST + (
        SCOPE_PER_ENTRY * feats["scope_entries"]
        + SCOPE_PER_FAMILY * feats["family_count"]
        + SCOPE_PER_RELATION * feats["relation_count"]
        + SCOPE_PER_DOC * feats["doc_count"]
    )
    entities = ENTITY_RATE * (
        ENTITY_SKELETON_CHARS * feats["entity_count"] + feats["entity_payload_chars"]
    )
    facts = FACT_RATE * (
        FACT_SKELETON_CHARS * feats["fact_count"] + feats["fact_payload_chars"]
    )
    docs = DOC_PER_COUNT * feats["doc_count"] + DOC_PER_CHAR * feats["doc_chars"]
    return int(round(max(0.0, HEADER_TOKENS + scope) + entities + facts + docs))


def calibration_error(
    structure: "ssw.SemanticWorld | dict[str, Any]", measured_tokens: int
) -> float:
    """Signed relative error of the estimator against one measurement."""
    predicted = estimate_capacity(structure)
    if measured_tokens <= 0:
        raise ValueError("measured_tokens must be positive")
    return (predicted - measured_tokens) / measured_tokens


# --- window budget arithmetic (§7: L_sys+ctx+query+answer+template <= L_model) ---


@dataclass(frozen=True)
class WindowBudget:
    system: int
    context: int
    query: int
    answer_reserve: int
    template: int
    model_window: int

    @property
    def total(self) -> int:
        return (
            self.system
            + self.context
            + self.query
            + self.answer_reserve
            + self.template
        )

    @property
    def fits(self) -> bool:
        return self.total <= self.model_window

    @property
    def slack(self) -> int:
        return self.model_window - self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "context": self.context,
            "query": self.query,
            "answer_reserve": self.answer_reserve,
            "template": self.template,
            "total": self.total,
            "model_window": self.model_window,
            "fits": self.fits,
            "slack": self.slack,
        }


def window_budget(
    context_tokens: int,
    *,
    system_tokens: int = DEFAULT_SYSTEM_TOKENS,
    query_tokens: int = DEFAULT_QUERY_TOKENS,
    answer_reserve: int = DEFAULT_ANSWER_RESERVE,
    template_tokens: int = DEFAULT_TEMPLATE_TOKENS,
    model_window: int = DEFAULT_MODEL_WINDOW,
) -> WindowBudget:
    """Check the §7 window arithmetic.

    "256K input" != "256K model window": the system prompt, query, answer
    reserve and template overhead all live inside the same window, so the
    context a 256K window can host is window - (all overheads).
    """
    for name, value in (
        ("context_tokens", context_tokens),
        ("system_tokens", system_tokens),
        ("query_tokens", query_tokens),
        ("answer_reserve", answer_reserve),
        ("template_tokens", template_tokens),
        ("model_window", model_window),
    ):
        if value < 0:
            raise ValueError(f"{name} must be non-negative, got {value}")
    return WindowBudget(
        system=system_tokens,
        context=context_tokens,
        query=query_tokens,
        answer_reserve=answer_reserve,
        template=template_tokens,
        model_window=model_window,
    )


# --- planning ---


@dataclass(frozen=True)
class PathRecommendation:
    """One legal lengthening path (§7 labels), priced per item."""

    path: str
    description: str
    per_item_tokens: float
    items_to_band: int
    items_to_target: int
    closes_gap: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "description": self.description,
            "per_item_tokens": self.per_item_tokens,
            "items_to_band": self.items_to_band,
            "items_to_target": self.items_to_target,
            "closes_gap": self.closes_gap,
        }


@dataclass(frozen=True)
class LengthPlan:
    mode: str  # "cap" | "band"
    target: int
    natural_tokens: int
    estimated_tokens: int | None
    estimate_error: float | None
    feasible: bool
    verdict: str
    gap_tokens: int = 0
    recommended_paths: tuple[PathRecommendation, ...] = ()
    window: WindowBudget | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target": self.target,
            "natural_tokens": self.natural_tokens,
            "estimated_tokens": self.estimated_tokens,
            "estimate_error": self.estimate_error,
            "feasible": self.feasible,
            "verdict": self.verdict,
            "gap_tokens": self.gap_tokens,
            "recommended_paths": [p.to_dict() for p in self.recommended_paths],
            "window": self.window.to_dict() if self.window else None,
            "notes": list(self.notes),
        }


def _timeline_exists(world: ssw.SemanticWorld) -> bool:
    """State-graph capacity: multi-entry timelines or version/revocation."""
    multi = any(len(entries) >= 2 for entries in world.timeline.values())
    versioned = any(f.version is not None for f in world.facts)
    revoked = any("revoked_at" in f.qualifiers for f in world.facts)
    return multi or versioned or revoked


def _path_recommendations(
    natural: int, target: int, window: WindowBudget
) -> tuple[PathRecommendation, ...]:
    """Price each legal path for closing the (positive) gap to a band."""
    lo = target * (1.0 - BAND_TOLERANCE)
    gap_lo = max(0, lo - natural)
    gap_target = max(0, target - natural)
    budget = window.slack  # how many tokens may still be added legally

    def recommend(path: str, per_item: float) -> PathRecommendation:
        to_band = math.ceil(gap_lo / per_item) if gap_lo > 0 else 0
        to_target = math.ceil(gap_target / per_item) if gap_target > 0 else 0
        closes = to_band * per_item <= budget if to_band else True
        return PathRecommendation(
            path=path,
            description=_PATH_DESCRIPTIONS[path],
            per_item_tokens=per_item,
            items_to_band=to_band,
            items_to_target=to_target,
            closes_gap=closes,
        )

    paths = [
        recommend(PATH_DENSE_INTEGRATION, TOKENS_PER_FACT),
        recommend(PATH_DISTANCE_DISTRACTOR, TOKENS_PER_DOC),
    ]
    return tuple(paths)


def _state_graph_recommendation(
    natural: int, target: int, window: WindowBudget
) -> PathRecommendation:
    lo = target * (1.0 - BAND_TOLERANCE)
    gap_lo = max(0, lo - natural)
    gap_target = max(0, target - natural)
    budget = window.slack
    to_band = math.ceil(gap_lo / TOKENS_PER_STATE_ENTRY) if gap_lo > 0 else 0
    to_target = math.ceil(gap_target / TOKENS_PER_STATE_ENTRY) if gap_target > 0 else 0
    closes = to_band * TOKENS_PER_STATE_ENTRY <= budget if to_band else True
    return PathRecommendation(
        path=PATH_STATE_GRAPH,
        description=_PATH_DESCRIPTIONS[PATH_STATE_GRAPH],
        per_item_tokens=TOKENS_PER_STATE_ENTRY,
        items_to_band=to_band,
        items_to_target=to_target,
        closes_gap=closes,
    )


def plan(
    world: "ssw.SemanticWorld | int",
    mode: str,
    target: int,
    *,
    system_tokens: int = DEFAULT_SYSTEM_TOKENS,
    query_tokens: int = DEFAULT_QUERY_TOKENS,
    answer_reserve: int = DEFAULT_ANSWER_RESERVE,
    template_tokens: int = DEFAULT_TEMPLATE_TOKENS,
    model_window: int = DEFAULT_MODEL_WINDOW,
) -> LengthPlan:
    """Plan one world against a cap (mode="cap") or a band (mode="band").

    `world` is a SemanticWorld (measured exactly with the pinned tokenizer)
    or an already-measured natural token count. Cap mode: feasible iff
    natural <= cap, actual length reported as-is. Band mode: feasible iff
    |natural - target| <= 5% of target. Infeasible plans either get labeled
    legal lengthening paths that could close the gap, or the honest
    infeasible-with-capacity / infeasible-window verdict. Never K/H
    reduction, never padding.
    """
    if mode not in ("cap", "band"):
        raise ValueError(f"mode must be 'cap' or 'band', got {mode!r}")
    if target <= 0:
        raise ValueError(f"target must be positive, got {target}")

    if isinstance(world, ssw.SemanticWorld):
        natural = measure(world)
        estimated = estimate_capacity(world)
        estimate_error = calibration_error(world, natural)
    else:
        natural = int(world)
        estimated = None
        estimate_error = None
    window = window_budget(
        natural,
        system_tokens=system_tokens,
        query_tokens=query_tokens,
        answer_reserve=answer_reserve,
        template_tokens=template_tokens,
        model_window=model_window,
    )
    common = {
        "natural_tokens": natural,
        "estimated_tokens": estimated,
        "estimate_error": estimate_error,
        "window": window,
    }

    if not window.fits:
        return LengthPlan(
            mode=mode,
            target=target,
            feasible=False,
            verdict="infeasible-window",
            notes=(
                "natural render already exceeds the model window once system, "
                "query, answer reserve and template overheads are counted "
                "(256K input != 256K model window)",
                NO_SHRINK_NOTE,
            ),
            **common,
        )

    if mode == "cap":
        if natural <= target:
            return LengthPlan(
                mode=mode,
                target=target,
                feasible=True,
                verdict="within-cap",
                notes=(
                    f"natural length {natural} <= cap {target}; real length recorded as-is",
                ),
                **common,
            )
        return LengthPlan(
            mode=mode,
            target=target,
            feasible=False,
            verdict="over-cap",
            notes=(
                f"natural length {natural} exceeds cap {target}; switch world or "
                "raise the cap — the world is not shortened",
                NO_SHRINK_NOTE,
            ),
            **common,
        )

    # band mode
    lo = target * (1.0 - BAND_TOLERANCE)
    hi = target * (1.0 + BAND_TOLERANCE)
    if lo <= natural <= hi:
        return LengthPlan(
            mode=mode,
            target=target,
            feasible=True,
            verdict="in-band",
            notes=(
                f"natural length {natural} within [{int(lo)}, {int(hi)}] (±{int(BAND_TOLERANCE * 100)}%)",
            ),
            **common,
        )
    if natural > hi:
        return LengthPlan(
            mode=mode,
            target=target,
            feasible=False,
            verdict="infeasible-overlength",
            gap_tokens=int(natural - target),
            notes=(
                f"natural length {natural} above band top {int(hi)}; this world "
                "belongs in a larger band or another bucket",
                NO_SHRINK_NOTE,
            ),
            **common,
        )

    # short of the band: which legal path could close the gap?
    paths = list(_path_recommendations(natural, target, window))
    if isinstance(world, ssw.SemanticWorld) and _timeline_exists(world):
        paths.append(_state_graph_recommendation(natural, target, window))
    any_closes = any(p.closes_gap for p in paths)
    verdict = "needs-expansion" if any_closes else "infeasible-with-capacity"
    notes = [
        f"natural length {natural} below band floor {int(lo)}; gap {int(lo - natural)} tokens to feasibility"
    ]
    if not any_closes:
        notes.append(
            "no legal path closes the gap within the model window; the world "
            "stays at its natural length"
        )
    notes.append(NO_SHRINK_NOTE)
    return LengthPlan(
        mode=mode,
        target=target,
        feasible=False,
        verdict=verdict,
        gap_tokens=int(lo - natural),
        recommended_paths=tuple(paths),
        notes=tuple(notes),
        **common,
    )


# --- band selection with visible empty cells ---


def _nearest_band(length: int, bands: Sequence[int]) -> int:
    """The band whose center is nearest in ratio (multiplicative) distance.

    Ties break to the smaller band. Deterministic.
    """
    if length <= 0:
        raise ValueError(f"length must be positive, got {length}")
    ordered = sorted(set(int(b) for b in bands))
    if not ordered:
        raise ValueError("bands must be a non-empty sequence")
    return min(ordered, key=lambda b: (abs(math.log(length / b)), b))


@dataclass(frozen=True)
class BandCell:
    band: int
    assigned: tuple[str, ...]
    success: int
    pending_expansion: int
    failure: int
    infeasible: int

    @property
    def empty(self) -> bool:
        return not self.assigned

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "assigned": list(self.assigned),
            "success": self.success,
            "pending_expansion": self.pending_expansion,
            "failure": self.failure,
            "infeasible": self.infeasible,
            "empty": self.empty,
        }


@dataclass(frozen=True)
class ViewSelection:
    bands: tuple[int, ...]
    cells: tuple[BandCell, ...]
    world_rows: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bands": list(self.bands),
            "cells": [c.to_dict() for c in self.cells],
            "worlds": [dict(row) for row in self.world_rows],
        }


def select_views(
    worlds: "Iterable[ssw.SemanticWorld | int | str | tuple[str, ssw.SemanticWorld | int | str]]",
    bands: Sequence[int] = DEFAULT_BANDS,
    **window_kwargs: Any,
) -> ViewSelection:
    """Assign each world its natural band; report every band, empty or not.

    Items are SemanticWorld objects (measured on the natural render), plain
    texts (measured verbatim — the text-concat path for worlds whose snapshot
    is not yet bridgeable), natural token counts, or (name, item) pairs. Each
    item goes to its nearest band; the per-band cell then classifies it with
    plan(world, "band", band):

    - success: inside the ±5% band window as-is
    - pending_expansion: short of the band but a labeled legal path could
      close the gap (needs-expansion)
    - failure: short of the band and no legal path fits the model window
      (infeasible-with-capacity)
    - infeasible: over the band, or over the model window (infeasible-
      overlength / infeasible-window); the world stays at its natural length

    Empty bands appear as empty cells (visible, never dropped).
    """
    ordered = tuple(sorted(set(int(b) for b in bands)))
    rows: list[dict[str, Any]] = []
    for item in worlds:
        if isinstance(item, tuple):
            name, payload = item
        else:
            payload = item
            name = None
        if isinstance(payload, ssw.SemanticWorld):
            natural = measure(payload)
            if name is None:
                name = f"world-{len(rows) + 1:03d}"
        elif isinstance(payload, str):
            natural = measure(payload)
            if name is None:
                name = f"text-{len(rows) + 1:03d}"
        else:
            natural = int(payload)
            if name is None:
                name = f"length-{len(rows) + 1:03d}"
        band = _nearest_band(natural, ordered)
        if isinstance(payload, ssw.SemanticWorld):
            result = plan(payload, "band", band, **window_kwargs)
        else:
            result = plan(natural, "band", band, **window_kwargs)
        rows.append(
            {
                "name": name,
                "natural_tokens": natural,
                "band": band,
                "verdict": result.verdict,
                "feasible": result.feasible,
                "gap_tokens": result.gap_tokens,
                "recommended_paths": [p.to_dict() for p in result.recommended_paths],
            }
        )
    cells = []
    for band in ordered:
        assigned = tuple(row["name"] for row in rows if row["band"] == band)
        verdicts = [row["verdict"] for row in rows if row["band"] == band]
        cells.append(
            BandCell(
                band=band,
                assigned=assigned,
                success=sum(1 for v in verdicts if v == "in-band"),
                pending_expansion=sum(1 for v in verdicts if v == "needs-expansion"),
                failure=sum(1 for v in verdicts if v == "infeasible-with-capacity"),
                infeasible=sum(
                    1
                    for v in verdicts
                    if v in ("infeasible-overlength", "infeasible-window")
                ),
            )
        )
    return ViewSelection(bands=ordered, cells=tuple(cells), world_rows=tuple(rows))


# --- calibration worlds (deterministic; the demo world is passed in) ---


_ONES = (
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)


def _words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    return str(n)


_PROJECT_NAMES = (
    "Hale Atlas Survey",
    "Kirkwood Near-Sky Survey",
    "Aurora Wide Field Monitoring Program",
    "Boreal Southern Cadence Survey",
    "Meridian Deep Sky Program",
    "Polar Transit Array Survey Initiative",
)

CALIBRATION_PARAMS: tuple[tuple[str, tuple[int, int, int, int]], ...] = (
    ("s1", (2, 3, 7, 1)),
    ("s2", (2, 6, 21, 1)),
    ("s3", (3, 12, 48, 1)),
    ("s4", (4, 16, 96, 2)),
    ("s5", (5, 24, 160, 2)),
    ("s6", (6, 32, 256, 3)),
    ("s7", (8, 40, 384, 4)),
    ("s8", (12, 56, 672, 6)),
)


def _project_label(i: int) -> str:
    if i < len(_PROJECT_NAMES):
        return _PROJECT_NAMES[i]
    return f"{_PROJECT_NAMES[i % len(_PROJECT_NAMES)]} Extension {i // len(_PROJECT_NAMES) + 1}"


def _render_doc_with_spans(
    doc_id: str, title: str, lines: list[str], needle_order: list[tuple[str, str]]
) -> tuple[ssw.Document, dict[str, ssw.SpanRef]]:
    """Same span-by-construction scheme as the demo world builder."""
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


def build_scaled_world(
    n_projects: int = 2,
    n_instruments: int = 3,
    n_observations: int = 7,
    ledger_docs: int = 1,
) -> ssw.SemanticWorld:
    """A demo-family world at scale (same relations, doc layout, needle spans).

    Deterministic, no RNG: the world is a pure function of its four capacity
    knobs. Used only to calibrate/verify the estimator — the demo world
    itself (scripts/demo_p74_world.py) is never modified.
    """
    E = ssw.Entity
    n_ledger = max(1, ledger_docs)
    ledger_ids = ["D3"] if n_ledger == 1 else [f"D3-{k}" for k in range(n_ledger)]

    def obs_ledger(i: int) -> str:
        return ledger_ids[min(n_ledger - 1, (i * n_ledger) // max(1, n_observations))]

    projects = [
        E(f"P-{i:03d}", _project_label(i), entity_type="project", doc_id="D1")
        for i in range(n_projects)
    ]
    specs = [
        E("V-SPEC2", "spec v2", entity_type="method_version", doc_id="D2"),
        E("V-SPEC3", "spec v3", entity_type="method_version", doc_id="D2"),
    ]
    instruments = [
        E(
            f"I-{j:03d}",
            f"Mapper {_words(j + 1)}",
            entity_type="instrument",
            doc_id="D2",
        )
        for j in range(n_instruments)
    ]
    obs_ids = [1001 + i for i in range(n_observations)]
    observations = [
        E(f"O-{oid}", f"obs-{oid}", entity_type="observation", doc_id=obs_ledger(i))
        for i, oid in enumerate(obs_ids)
    ]

    # (fact_id, subject, relation, value, value_type, time, version, quals, doc)
    raw: list[tuple[str, str, str, Any, str, str | None, str | None, dict, str]] = []
    for i in range(n_projects):
        raw.append(
            (
                f"F-adopt-{i}a",
                f"P-{i:03d}",
                "adopts_method",
                "V-SPEC2",
                "entity",
                "2026-03-01",
                None,
                {},
                "D2",
            )
        )
        raw.append(
            (
                f"F-adopt-{i}b",
                f"P-{i:03d}",
                "adopts_method",
                "V-SPEC3",
                "entity",
                "2026-07-01",
                None,
                {},
                "D2",
            )
        )
    raw.append(
        (
            "F-rule-2",
            "V-SPEC2",
            "validity_rule",
            "noise_level <= 4.5",
            "rule",
            "2026-02-01",
            "v2",
            {},
            "D2",
        )
    )
    raw.append(
        (
            "F-rule-3",
            "V-SPEC3",
            "validity_rule",
            "noise_level <= 2.5",
            "rule",
            "2026-06-01",
            "v3",
            {},
            "D2",
        )
    )
    for j in range(n_instruments):
        noise = round(1.5 + 0.7 * j, 1)
        raw.append(
            (
                f"F-noise-{j}",
                f"I-{j:03d}",
                "noise_level",
                noise,
                "number",
                "2026-05-01",
                None,
                {},
                "D2",
            )
        )
        raw.append(
            (
                f"F-assign-{j}",
                f"P-{j % max(1, n_projects):03d}",
                "assigns_instrument",
                f"I-{j:03d}",
                "entity",
                "2026-04-01",
                None,
                {},
                ledger_ids[0],
            )
        )
    noise_of = {f"F-noise-{j}": round(1.5 + 0.7 * j, 1) for j in range(n_instruments)}
    for i, oid in enumerate(obs_ids):
        doc = obs_ledger(i)
        day = f"2026-05-{1 + i % 28:02d}"
        raw.append(
            (
                f"F-prod-{i}",
                f"I-{i % max(1, n_instruments):03d}",
                "produces",
                f"O-{oid}",
                "entity",
                day,
                None,
                {},
                doc,
            )
        )
        quals = {"revoked_at": "2026-06-01"} if i % 7 == 6 else {}
        raw.append(
            (
                f"F-exp-{i}",
                f"O-{oid}",
                "exposure_count",
                round(10.5 + (i * 7) % 90, 1),
                "number",
                day,
                None,
                quals,
                doc,
            )
        )
        raw.append(
            (
                f"F-status-{i}",
                f"O-{oid}",
                "calibration_status",
                "active",
                "string",
                day,
                None,
                quals,
                doc,
            )
        )
        raw.append(
            (
                f"F-band-{i}",
                f"O-{oid}",
                "band",
                "aurora" if i % 2 == 0 else "boreal",
                "string",
                day,
                None,
                {},
                doc,
            )
        )

    d1_lines = ["Observatory programs share this site in 2026."]
    for i in range(n_projects):
        d1_lines.append(
            f"The {_project_label(i)} runs program {i + 1} under the shared site agreement."
        )

    d2_lines = ["Method versions and instrument noise, frozen 2026-09."]
    d2_order: list[tuple[str, str]] = []
    for i in range(n_projects):
        d2_lines.append(
            f"The {_project_label(i)} adopted spec v2 on 2026-03-01 and spec v3 on 2026-07-01."
        )
        d2_order.append((f"F-adopt-{i}a", "spec v2"))
        d2_order.append((f"F-adopt-{i}b", "spec v3"))
    d2_lines.append(
        "spec v2 sets validity_rule noise_level <= 4.5; spec v3 sets validity_rule noise_level <= 2.5."
    )
    d2_order.append(("F-rule-2", "noise_level <= 4.5"))
    d2_order.append(("F-rule-3", "noise_level <= 2.5"))
    for chunk_start in range(0, n_instruments, 4):
        chunk = instruments[chunk_start : chunk_start + 4]
        parts = [
            f"{inst.label} measured noise_level {noise_of[f'F-noise-{chunk_start + k}']}"
            for k, inst in enumerate(chunk)
        ]
        d2_lines.append("; ".join(parts) + ".")
        for k, inst in enumerate(chunk):
            d2_order.append(
                (
                    f"F-noise-{chunk_start + k}",
                    str(noise_of[f"F-noise-{chunk_start + k}"]),
                )
            )

    ledger_lines: dict[str, list[str]] = {lid: [] for lid in ledger_ids}
    ledger_order: dict[str, list[tuple[str, str]]] = {lid: [] for lid in ledger_ids}
    first = ledger_ids[0]
    for p in range(n_projects):
        mine = [
            instruments[j] for j in range(n_instruments) if j % max(1, n_projects) == p
        ]
        if not mine:
            continue
        names = (
            ", ".join(inst.label for inst in mine[:-1]) + f" and {mine[-1].label}"
            if len(mine) > 1
            else mine[0].label
        )
        ledger_lines[first].append(
            f"The {_project_label(p)} assigns_instrument {names}."
        )
        for inst in mine:
            ledger_order[first].append(
                (f"F-assign-{int(inst.entity_id[2:])}", inst.label)
            )
    for k, lid in enumerate(ledger_ids):
        obs_in_doc = [i for i in range(n_observations) if obs_ledger(i) == lid]
        ledger_lines[lid].append(
            f"Observation ledger part {k + 1} for 2026-05; calibration tracked as of 2026-09-01."
        )
        for j in range(n_instruments):
            produced = [i for i in obs_in_doc if i % max(1, n_instruments) == j]
            if not produced:
                continue
            names = ", ".join(f"obs-{obs_ids[i]}" for i in produced)
            ledger_lines[lid].append(f"{instruments[j].label} produces {names}.")
            for i in produced:
                ledger_order[lid].append((f"F-prod-{i}", f"obs-{obs_ids[i]}"))
        for i in obs_in_doc:
            revoked = i % 7 == 6
            suffix = ", revoked 2026-06-01" if revoked else ""
            exposure = next(f[3] for f in raw if f[0] == f"F-exp-{i}")
            ledger_lines[lid].append(
                f"obs-{obs_ids[i]} records exposure_count {exposure} in band "
                f"{'aurora' if i % 2 == 0 else 'boreal'} with calibration_status active{suffix}."
            )
            ledger_order[lid].append((f"F-exp-{i}", str(exposure)))
            ledger_order[lid].append(
                (f"F-band-{i}", "aurora" if i % 2 == 0 else "boreal")
            )
            ledger_order[lid].append((f"F-status-{i}", "active"))

    documents: list[ssw.Document] = []
    spans: dict[str, ssw.SpanRef] = {}
    doc_specs: list[tuple[str, str, list[str], list[tuple[str, str]]]] = [
        ("D1", "Observatory Programs", d1_lines, [])
    ]
    doc_specs.append(("D2", "Method and Instrument Registry", d2_lines, d2_order))
    for k, lid in enumerate(ledger_ids):
        title = (
            "Observation Ledger"
            if n_ledger == 1
            else f"Observation Ledger Part {k + 1}"
        )
        doc_specs.append((lid, title, ledger_lines[lid], ledger_order[lid]))
    for doc_id, title, lines, order in doc_specs:
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
    entities = tuple(projects + specs + instruments + observations)
    return ssw.SemanticWorld(tuple(documents), entities, facts)


def calibration_worlds(
    demo_world: ssw.SemanticWorld | None = None,
) -> list[tuple[str, ssw.SemanticWorld]]:
    """The calibration set: the demo world (if given) plus s1..s8."""
    worlds: list[tuple[str, ssw.SemanticWorld]] = []
    if demo_world is not None:
        worlds.append(("demo", demo_world))
    worlds.extend(
        (name, build_scaled_world(*params)) for name, params in CALIBRATION_PARAMS
    )
    return worlds


def calibration_report(
    worlds: Sequence[tuple[str, ssw.SemanticWorld]],
) -> dict[str, Any]:
    """Predicted vs measured for each calibration world; honest error table."""
    rows = []
    max_abs_pct = 0.0
    for name, world in worlds:
        measured = measure(world)
        predicted = estimate_capacity(world)
        error = calibration_error(world, measured)
        abs_pct = abs(error) * 100
        max_abs_pct = max(max_abs_pct, abs_pct)
        rows.append(
            {
                "name": name,
                "structure": {
                    "entities": len(world.entities),
                    "facts": len(world.facts),
                    "documents": len(world.documents),
                },
                "measured_tokens": measured,
                "predicted_tokens": predicted,
                "error_pct": round(error * 100, 2),
                "abs_error_pct": round(abs_pct, 2),
            }
        )
    return {
        "rows": rows,
        "max_abs_error_pct": round(max_abs_pct, 2),
        "declared_tolerance_pct": CALIBRATION_TOLERANCE * 100,
        "within_tolerance": max_abs_pct <= CALIBRATION_TOLERANCE * 100,
    }
