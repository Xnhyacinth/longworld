"""P74 proof certificates: minimal evidence, alternative proofs, D_min.

The T3 work package of the P74 charter (.hl/design/p74_real_shared_worlds.md
§6, §16 T3 row). Three concepts the charter keeps apart are implemented apart:

- MINIMAL sufficient evidence (E*): greedy deletion search over the executed
  proof's consumed facts — drop a fact, re-execute, keep the removal when the
  answer survives; iterate to a fixpoint. The result is minimal with respect
  to the deletion order actually tried (recorded in the search log): after the
  final pass no single fact of E* can be removed while the answer is
  preserved. It is NOT claimed to be MINIMUM-cost: minimum needs a separate
  cost definition (fact count, document count or op count) and exhaustive
  search over deletion orders.
- In-context necessity: irreplaceable within E* does not mean irreplaceable
  in the full context C — another document, summary or table may carry an
  equivalent disclosure. The alternative-proof search checks exactly that,
  bounded: (a) duplicate disclosures — a fact whose supporting spans live
  in more than one document (the charter's 同一事实多处出现; the world
  model forbids two rows with the same (subject, relation), so a duplicate
  IS a second span), re-executed with each single document's disclosure
  kept alone; (b) alternative_spans on minimal facts; (c) re-execution
  after removing each single supporting span of a minimal fact. OR
  behavior becomes visible: deleting one disclosure of a duplicated fact
  need not break the answer.
- D_min: the most compact evidence window among CHECKED (verified) proofs —
  min over proofs of (max span end - min span start), reported per proof. A
  proof whose spans cross documents gets a global extent that mixes
  per-document coordinate systems; the flag and the per-document windows are
  reported alongside so the number is never mistaken for one physical
  continuous window.

Deletion proves UNDERDETERMINATION, not a solver error (charter §6): with the
minimal evidence removed (C-minus), two worlds W1, W2 both consistent with
C-minus are constructed by giving one deleted fact two candidate values
(W1 keeps the original); Q(W1) != Q(W2) yields the "underdetermined" verdict.
If no candidate pair differs, the verdict is honestly "deletion_insufficient".

Four certificates per (task, world), never one overloaded True:
surface_answer_supported / evidence_sufficiency_checked /
alternative_proof_scope_checked / long_span_requirement_verified_within_scope.

Bounded first version: at most `budget` re-executions per task (default 64)
through the task-level entry points `analyze`/`certificates`, which share one
budget across phases; the standalone phase functions bound their own phase by
their `budget` argument. No AND-OR proof graph, no combinatorial substitution
search, no claim of exhaustive proof search — what was searched is recorded,
and `proof_search_complete_within_supported_language` is false the moment the
budget cuts the search short. Deterministic order everywhere (sorted fact ids,
stored span order); standard library only; no RNG.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from longworld.synthesis.dependency_ops import (
    ExecutionResult,
    execute,
    mutate_fact,
    mutate_relation,
)
from longworld.synthesis.shared_semantic_world import (
    Fact,
    SemanticWorld,
    SpanRef,
)

DEFAULT_BUDGET = 64

# The three bounded mechanisms of the alternative-proof search, in the order
# they run. This tuple IS the "supported language" of the first version: the
# next wave's AND-OR proof graph (OR nodes = interchangeable sources, deletion
# of a necessary fact handled through its equivalent support) is not in it.
MECHANISMS = ("duplicate_disclosures", "alternative_spans", "single_span_removal")


# --- shared re-execution budget ---


class _Budget:
    """One re-execution budget shared across the phases of one task."""

    def __init__(self, total: int) -> None:
        if not isinstance(total, int) or total < 0:
            raise ValueError("budget must be a non-negative integer")
        self.total = total
        self.used = 0

    def can_spend(self, units: int = 1) -> bool:
        return self.used + units <= self.total

    def spend(self, units: int = 1) -> None:
        # callers must have checked can_spend first
        self.used += units

    @property
    def exhausted(self) -> bool:
        return self.used >= self.total


def _first_line(error: BaseException) -> str:
    return str(error).splitlines()[0][:120] if str(error) else "unknown error"


def _try_execute(
    world: SemanticWorld, program: dict[str, Any], require_fold_gate: bool
) -> tuple[bool, ExecutionResult | None, str | None]:
    """Re-execute; a failed execution is a search outcome, never a crash."""
    try:
        result = execute(world, program, require_fold_gate=require_fold_gate)
    except ValueError as error:
        return False, None, _first_line(error)
    return True, result, None


def _reduced_world(
    world: SemanticWorld,
    fact_ids: set[str],
    replacements: dict[str, Fact] | None = None,
) -> tuple[SemanticWorld | None, str | None]:
    """The world restricted to `fact_ids`, with per-fact span-level edits.

    Iterates the ORIGINAL fact order (deterministic, and follow-relation
    tuple orders stay closest to the world being verified). Replacement rows
    are included even when their id is not in `fact_ids`. A rebuild that
    fails the world's own fact->span integrity is reported, not raised.
    """
    reps = replacements or {}
    facts = []
    for fact in world.facts:
        if fact.fact_id in reps:
            facts.append(reps[fact.fact_id])
        elif fact.fact_id in fact_ids:
            facts.append(fact)
    try:
        return (
            SemanticWorld(world.documents, world.entities, tuple(facts)),
            None,
        )
    except ValueError as error:
        return None, _first_line(error)


def _world_without_facts(world: SemanticWorld, drop: set[str]) -> SemanticWorld:
    """The world minus whole fact rows (removal never breaks validation)."""
    facts = tuple(fact for fact in world.facts if fact.fact_id not in drop)
    return SemanticWorld(world.documents, world.entities, facts)


def _spans_from_result(
    result: ExecutionResult,
) -> tuple[tuple[str, tuple[SpanRef, ...]], ...]:
    """(fact_id, spans) in first-citation order, one entry per fact."""
    ordered: dict[str, tuple[SpanRef, ...]] = {}
    for item in result.proof:
        if item.kind == "fact" and item.ref_id not in ordered:
            ordered[item.ref_id] = item.spans
    return tuple(ordered.items())


# --- 1. minimal sufficient evidence: greedy deletion search ---


@dataclass(frozen=True)
class RemovalTrial:
    """One deletion attempt: which fact, in which pass, with what outcome."""

    pass_index: int
    fact_id: str
    outcome: str  # "kept" | "rejected"
    reason: str  # "answer_preserved" | "answer_changed" | "execution_failed: ..."
    reexecutions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.pass_index,
            "fact_id": self.fact_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "reexecutions": self.reexecutions,
        }


@dataclass(frozen=True)
class MinimalEvidenceResult:
    """E* plus the full search log; minimal, and only minimal, is claimed."""

    fact_ids: tuple[str, ...]
    consumed_fact_ids: tuple[str, ...]
    search_log: tuple[RemovalTrial, ...]
    passes: int
    reexecutions_used: int  # re-executions THIS phase performed
    budget_total: int
    budget_used_total: int  # pool usage incl. earlier phases sharing it
    exhausted: bool  # the budget cut the search before the fixpoint
    complete: bool  # a full pass tried every remaining fact and removed none
    baseline_answer: Any
    standalone_answer: Any | None
    standalone_verified: bool
    standalone_sufficient: bool
    # None, or "budget_exhausted" / "world_invalid: ..." / "execution_failed: ..."
    standalone_error: str | None
    # (fact_id, spans) cited by the standalone E*-only re-execution
    standalone_spans_by_fact: tuple[tuple[str, tuple[SpanRef, ...]], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_ids": list(self.fact_ids),
            "consumed_fact_ids": list(self.consumed_fact_ids),
            "search_log": [trial.to_dict() for trial in self.search_log],
            "passes": self.passes,
            "reexecutions_used": self.reexecutions_used,
            "budget_total": self.budget_total,
            "budget_used_total": self.budget_used_total,
            "exhausted": self.exhausted,
            "complete": self.complete,
            "standalone_answer": self.standalone_answer,
            "standalone_verified": self.standalone_verified,
            "standalone_sufficient": self.standalone_sufficient,
            "standalone_error": self.standalone_error,
        }


def minimal_evidence(
    world: SemanticWorld,
    program: dict[str, Any],
    execution: ExecutionResult | None = None,
    *,
    budget: int | _Budget = DEFAULT_BUDGET,
    require_fold_gate: bool = True,
) -> MinimalEvidenceResult:
    """E*: the deletion-search minimal sufficient set, with its search log.

    Greedy: each pass tries to delete each still-present consumed fact from
    the cumulative reduction; a deletion is kept when the re-executed answer
    equals the baseline. The search ends at the fixpoint (a full pass with no
    removal) or when the budget runs out (complete=False, honestly). After a
    complete search E* is re-executed ALONE — evidence sufficiency must hold
    on the reduced world, not just on the full one.
    """
    tracker = budget if isinstance(budget, _Budget) else _Budget(budget)
    if execution is None:
        execution = execute(world, program, require_fold_gate=require_fold_gate)
    baseline = execution.answer
    phase_start = tracker.used
    remaining = list(execution.consumed_fact_ids)
    removed: list[str] = []
    trials: list[RemovalTrial] = []
    passes = 0
    cut_short = False
    while remaining:
        passes += 1
        kept_this_pass = 0
        for fact_id in list(remaining):
            if not tracker.can_spend():
                cut_short = True
                break
            reduced = _world_without_facts(world, set(removed) | {fact_id})
            ok, result, error = _try_execute(reduced, program, require_fold_gate)
            tracker.spend()
            if ok and result.answer == baseline:
                removed.append(fact_id)
                remaining.remove(fact_id)
                trials.append(
                    RemovalTrial(passes, fact_id, "kept", "answer_preserved", 1)
                )
                kept_this_pass += 1
            else:
                reason = "answer_changed" if ok else f"execution_failed: {error}"
                trials.append(RemovalTrial(passes, fact_id, "rejected", reason, 1))
        if cut_short or kept_this_pass == 0:
            break
    e_star = tuple(sorted(remaining))
    standalone_answer = None
    standalone_verified = False
    standalone_sufficient = False
    standalone_spans: tuple[tuple[str, tuple[SpanRef, ...]], ...] = ()
    # The standalone re-execution of E* ALONE: sufficiency must hold on the
    # reduced world, not just on the full one. A build failure is recorded in
    # dedicated fields, not as a fake removal trial.
    standalone_error = None
    if tracker.can_spend():
        reduced, build_error = _reduced_world(world, set(e_star))
        if reduced is None:
            standalone_error = f"world_invalid: {build_error}"
        else:
            ok, result, error = _try_execute(reduced, program, require_fold_gate)
            tracker.spend()
            standalone_verified = ok
            if ok:
                standalone_answer = result.answer
                standalone_sufficient = result.answer == baseline
                standalone_spans = _spans_from_result(result)
            else:
                standalone_error = f"execution_failed: {error}"
    else:
        standalone_error = "budget_exhausted"
    return MinimalEvidenceResult(
        fact_ids=e_star,
        consumed_fact_ids=tuple(execution.consumed_fact_ids),
        search_log=tuple(trials),
        passes=passes,
        reexecutions_used=tracker.used - phase_start,
        budget_total=tracker.total,
        budget_used_total=tracker.used,
        exhausted=cut_short,
        # complete: the deletion search reached its fixpoint AND the
        # standalone re-execution of E* was performed
        complete=(not cut_short) and standalone_error != "budget_exhausted",
        baseline_answer=baseline,
        standalone_error=standalone_error,
        standalone_answer=standalone_answer,
        standalone_verified=standalone_verified,
        standalone_sufficient=standalone_sufficient,
        standalone_spans_by_fact=standalone_spans,
    )


# --- 2. bounded alternative-proof search ---


@dataclass(frozen=True)
class AlternativeProof:
    """One checked evidence set, and how it was reached.

    A checked proof is a set of fact ids whose reduced world re-executes to
    the baseline answer. `verified` records whether it did. Per-fact spans
    are kept so D_min consumers need one pass.
    """

    mechanism: str  # one of MECHANISMS, or "reference"
    description: str
    fact_ids: tuple[str, ...]  # sorted
    spans_by_fact: tuple[tuple[str, tuple[SpanRef, ...]], ...]
    verified: bool
    answer: Any
    drop_fact_id: str | None
    drop_span: SpanRef | None

    def spans(self) -> tuple[SpanRef, ...]:
        return tuple(
            span for _fid, fact_spans in self.spans_by_fact for span in fact_spans
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "description": self.description,
            "fact_ids": list(self.fact_ids),
            "spans": [span.to_dict() for span in self.spans()],
            "verified": self.verified,
            "answer": self.answer,
            "drop_fact_id": self.drop_fact_id,
            "drop_span": self.drop_span.to_dict() if self.drop_span else None,
        }


@dataclass(frozen=True)
class AlternativeProofsResult:
    mechanisms: tuple[str, ...]  # the mechanisms this first version searches
    checked: tuple[AlternativeProof, ...]
    # fact ids whose supporting spans appear in >1 document (OR nodes)
    duplicate_disclosures: tuple[tuple[str, ...], ...]
    branches_enumerated: int
    branches_executed: int
    reexecutions_used: int  # re-executions THIS phase performed
    budget_total: int  # size of the (possibly shared) pool
    budget_used_total: int  # pool usage incl. earlier phases sharing it
    exhausted: bool
    # true iff every branch the supported mechanisms enumerate was re-executed
    # (duplicate rows + alternative_spans + single-span removals) within budget
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanisms": list(self.mechanisms),
            "checked": [proof.to_dict() for proof in self.checked],
            "duplicate_disclosures": [
                list(group) for group in self.duplicate_disclosures
            ],
            "branches_enumerated": self.branches_enumerated,
            "branches_executed": self.branches_executed,
            "reexecutions_used": self.reexecutions_used,
            "budget_total": self.budget_total,
            "budget_used_total": self.budget_used_total,
            "exhausted": self.exhausted,
            "complete": self.complete,
        }


def duplicate_disclosure_groups(
    world: SemanticWorld,
) -> tuple[tuple[str, ...], ...]:
    """Facts whose supporting spans appear in more than one document.

    In a SemanticWorld a duplicate is a second SPAN on the same fact row
    (lookup_fact enforces (subject, relation) uniqueness, so two rows with
    the same key cannot exist): the charter's duplicate disclosure (the
    same fact appearing in multiple places). Each such fact is an OR node —
    one disclosure among several. Sorted for determinism.
    """
    groups: dict[str, set[str]] = {}
    for fact in world.facts:
        docs_of = {span.doc_id for span in fact.supporting_spans}
        if len(docs_of) > 1:
            groups[fact.fact_id] = docs_of
    return tuple((fact_id,) for fact_id in sorted(groups))

def alternative_proofs(
    world: SemanticWorld,
    program: dict[str, Any],
    execution: ExecutionResult | None = None,
    *,
    minimal: MinimalEvidenceResult | None = None,
    budget: int | _Budget = DEFAULT_BUDGET,
    require_fold_gate: bool = True,
) -> AlternativeProofsResult:
    """Bounded search for alternate evidence paths (charter §6 first pass).

    Branches, in fixed order, each spending re-executions from one budget:

    (a) duplicate_disclosures — for each E* fact that carries
        supporting spans in MORE THAN ONE document (the charter's 同一事实
        多处出现), the E*-reduced world is re-built with that fact kept by
        ONE document's disclosure alone (each document in turn): if the
        answer survives, deleting the other document's disclosure does not
        break the answer — the OR behavior the charter wants visible.
        Duplicate fact ROWS (same subject/relation/value twice) cannot
        exist in a SemanticWorld: lookup_fact requires uniqueness on
        (subject, relation), so the world model itself rules that reading
        out; a duplicate is a second SPAN on the same row.
    (b) alternative_spans — each E* fact's alternative_spans replace its
        supporting spans in the E*-reduced world (a same-fact relocation).
    (c) single_span_removal — each single supporting span of each E* fact
        (when it has several) is dropped from that fact's span list
        (redaction within one document).

    A branch skipped for budget counts in branches_enumerated but not in
    branches_executed, and forces complete=False. What was searched is
    always recorded, in deterministic order.
    """
    tracker = budget if isinstance(budget, _Budget) else _Budget(budget)
    if execution is None:
        execution = execute(world, program, require_fold_gate=require_fold_gate)
    baseline = execution.answer
    phase_start = tracker.used
    if minimal is None:
        minimal = minimal_evidence(
            world,
            program,
            execution,
            budget=tracker,
            require_fold_gate=require_fold_gate,
        )
    e_star = set(minimal.fact_ids)
    groups = duplicate_disclosure_groups(world)
    checked: list[AlternativeProof] = []
    branches_enumerated = 0
    branches_executed = 0
    skipped_for_budget = 0

    def record(
        mechanism: str,
        description: str,
        fact_ids: tuple[str, ...],
        spans_by_fact: tuple[tuple[str, tuple[SpanRef, ...]], ...],
        verified: bool,
        answer: Any,
        drop_fact_id: str | None,
        drop_span: SpanRef | None,
    ) -> None:
        nonlocal branches_executed
        branches_executed += 1
        checked.append(
            AlternativeProof(
                mechanism,
                description,
                tuple(sorted(fact_ids)),
                spans_by_fact,
                verified,
                answer,
                drop_fact_id,
                drop_span,
            )
        )

    # (a) duplicate disclosures: one E* fact, spans in >1 document — keep it
    # alive by EACH single document's disclosure in turn (the others removed).
    for fact_id in sorted(e_star):
        fact = world.facts_by_id[fact_id]
        docs_of = sorted({span.doc_id for span in fact.supporting_spans})
        if len(docs_of) < 2:
            continue
        for keep_doc in docs_of:
            branches_enumerated += 1
            if not tracker.can_spend():
                skipped_for_budget += 1
                break
            kept_spans = tuple(
                span for span in fact.supporting_spans if span.doc_id == keep_doc
            )
            partial = replace(fact, supporting_spans=kept_spans)
            reduced, build_error = _reduced_world(world, e_star, {fact_id: partial})
            if reduced is None:
                record(
                    "duplicate_disclosures",
                    f"keeping only {keep_doc}'s disclosure of {fact_id} "
                    f"fails world validation: {build_error}",
                    tuple(sorted(e_star)),
                    (),
                    False,
                    None,
                    fact_id,
                    kept_spans[0] if kept_spans else None,
                )
                continue
            ok, result, _ = _try_execute(reduced, program, require_fold_gate)
            tracker.spend()
            dropped = [
                span for span in fact.supporting_spans if span.doc_id != keep_doc
            ]
            record(
                "duplicate_disclosures",
                f"E* with {fact_id} disclosed by {keep_doc} only "
                f"(dropped {[s.doc_id for s in dropped]})",
                tuple(sorted(e_star)),
                _spans_from_result(result) if ok else (),
                ok and result.answer == baseline,
                result.answer if ok else None,
                fact_id,
                dropped[0] if dropped else None,
            )

    # (b) alternative_spans on E* facts (same fact, relocated evidence).

    # (b) alternative_spans on E* facts (same fact, relocated evidence).
    for fact_id in sorted(e_star):
        fact = world.facts_by_id[fact_id]
        if not fact.alternative_spans:
            continue
        branches_enumerated += 1
        if not tracker.can_spend():
            skipped_for_budget += 1
            break
        alt = replace(fact, supporting_spans=fact.alternative_spans)
        reduced, build_error = _reduced_world(world, e_star, {fact_id: alt})
        if reduced is None:
            record(
                "alternative_spans",
                f"alternative_spans for {fact_id} fail world validation: {build_error}",
                tuple(sorted(e_star)),
                (),
                False,
                None,
                fact_id,
                fact.alternative_spans[0],
            )
            continue
        ok, result, _ = _try_execute(reduced, program, require_fold_gate)
        tracker.spend()
        record(
            "alternative_spans",
            f"E* with {fact_id} carried by alternative_spans",
            tuple(sorted(e_star)),
            _spans_from_result(result) if ok else (),
            ok and result.answer == baseline,
            result.answer if ok else None,
            fact_id,
            fact.alternative_spans[0],
        )

    # (c) one supporting span at a time removed from one E* fact.
    for fact_id in sorted(e_star):
        fact = world.facts_by_id[fact_id]
        if len(fact.supporting_spans) < 2:
            continue
        for drop in fact.supporting_spans:
            branches_enumerated += 1
            if not tracker.can_spend():
                skipped_for_budget += 1
                break
            kept = tuple(span for span in fact.supporting_spans if span != drop)
            alt = replace(fact, supporting_spans=kept)
            reduced, build_error = _reduced_world(world, e_star, {fact_id: alt})
            if reduced is None:
                record(
                    "single_span_removal",
                    f"removing {drop.doc_id}[{drop.start}:{drop.end}] from "
                    f"{fact_id} fails world validation: {build_error}",
                    tuple(sorted(e_star)),
                    (),
                    False,
                    None,
                    fact_id,
                    drop,
                )
                continue
            ok, result, _ = _try_execute(reduced, program, require_fold_gate)
            tracker.spend()
            record(
                "single_span_removal",
                f"E* with span {drop.doc_id}[{drop.start}:{drop.end}] removed "
                f"from {fact_id}",
                tuple(sorted(e_star)),
                _spans_from_result(result) if ok else (),
                ok and result.answer == baseline,
                result.answer if ok else None,
                fact_id,
                drop,
            )

    complete = branches_executed == branches_enumerated
    # A budget-cut minimal-evidence search means E* itself may not be the
    # fixpoint set: the alternative-proof search then runs over an
    # unverified reference set, so its scope claim must not read complete.
    if not minimal.complete:
        complete = False
    return AlternativeProofsResult(
        mechanisms=MECHANISMS,
        checked=tuple(checked),
        duplicate_disclosures=groups,
        branches_enumerated=branches_enumerated,
        branches_executed=branches_executed,
        reexecutions_used=tracker.used - phase_start,
        budget_total=tracker.total,
        budget_used_total=tracker.used,
        exhausted=tracker.exhausted,
        complete=complete,
    )


# --- 3. D_min: the compact evidence window over CHECKED proofs ---


@dataclass(frozen=True)
class WindowReport:
    """The evidence-window arithmetic for one proof.

    `global_extent` is max(span.end) - min(span.start) over all spans of the
    proof, treating every document's coordinate system as one axis. When the
    proof's spans span more than one document, this number mixes coordinate
    systems and is NOT a physical continuous window; per-document windows
    are reported alongside, and `crosses_documents` flags the case.
    """

    proof_label: str
    span_count: int
    global_extent: int | None  # None: a proof with no spans
    crosses_documents: bool
    documents: tuple[str, ...]
    per_document: tuple[tuple[str, int], ...]
    first_span: SpanRef | None
    last_span: SpanRef | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof": self.proof_label,
            "span_count": self.span_count,
            "global_extent": self.global_extent,
            "crosses_documents": self.crosses_documents,
            "documents": list(self.documents),
            "per_document": [
                {"doc_id": doc, "window": window} for doc, window in self.per_document
            ],
            "first_span": self.first_span.to_dict() if self.first_span else None,
            "last_span": self.last_span.to_dict() if self.last_span else None,
        }


@dataclass(frozen=True)
class DMinResult:
    """D_min over the CHECKED proofs, with per-proof windows.

    `minimum_span_within_checked_proofs` is the charter's own field name
    (§6: 只发现部分证明时标注 minimum_span_within_checked_proofs): the flag
    marks that this D_min is scoped to what the bounded search verified.
    """

    proofs_considered: tuple[str, ...]
    verified_proofs_considered: tuple[str, ...]
    d_min: int | None  # None: no checked proof had spans
    d_min_proof: str | None
    # None when d_min is None; True when the winning window spans documents
    d_min_crosses_documents: bool | None
    window_reports: tuple[WindowReport, ...]
    note: str
    # True when D_min is computed only over proofs the bounded search checked
    minimum_span_within_checked_proofs: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "proofs_considered": list(self.proofs_considered),
            "verified_proofs_considered": list(self.verified_proofs_considered),
            "d_min": self.d_min,
            "d_min_proof": self.d_min_proof,
            "d_min_crosses_documents": self.d_min_crosses_documents,
            "window_reports": [report.to_dict() for report in self.window_reports],
            "note": self.note,
            "minimum_span_within_checked_proofs": (
                self.minimum_span_within_checked_proofs
            ),
        }


def _window_report(proof_label: str, spans: tuple[SpanRef, ...]) -> WindowReport:
    if not spans:
        return WindowReport(
            proof_label=proof_label,
            span_count=0,
            global_extent=None,
            crosses_documents=False,
            documents=(),
            per_document=(),
            first_span=None,
            last_span=None,
        )
    starts = [span.start for span in spans]
    ends = [span.end for span in spans]
    documents = tuple(sorted({span.doc_id for span in spans}))
    per_doc: dict[str, tuple[int, int]] = {}
    for span in spans:
        lo, hi = per_doc.get(span.doc_id, (span.start, span.end))
        per_doc[span.doc_id] = (min(lo, span.start), max(hi, span.end))
    per_document = tuple((doc, hi - lo) for doc, (lo, hi) in sorted(per_doc.items()))
    first = min(spans, key=lambda s: (s.doc_id, s.start))
    last = max(spans, key=lambda s: (s.doc_id, s.end))
    return WindowReport(
        proof_label=proof_label,
        span_count=len(spans),
        global_extent=max(ends) - min(starts),
        crosses_documents=len(documents) > 1,
        documents=documents,
        per_document=per_document,
        first_span=first,
        last_span=last,
    )


def minimum_span(
    minimal: MinimalEvidenceResult,
    alt: AlternativeProofsResult,
    reference: ExecutionResult | None = None,
) -> DMinResult:
    """D_min: min over CHECKED proofs of the compact-window extent.

    Checked proofs are: the E* execution (if verified standalone), each
    VERIFIED alternative proof, and the reference execution (if supplied) —
    a proof that failed verification never enters the window competition.
    D_min is the minimum over these of (max span end - min span start).
    Only proofs that were checked count: D_min says nothing about the
    compactness of proofs this bounded search never found.
    """
    reports: list[WindowReport] = []
    verified_labels: list[str] = []
    if minimal.standalone_verified and minimal.standalone_spans_by_fact:
        spans = tuple(
            span
            for _fid, fact_spans in minimal.standalone_spans_by_fact
            for span in fact_spans
        )
        reports.append(_window_report("E*", spans))
        verified_labels.append("E*")
    for proof in alt.checked:
        if proof.verified and proof.spans():
            reports.append(_window_report(proof.description, proof.spans()))
            verified_labels.append(proof.description)
    if reference is not None:
        ref_spans = tuple(
            span
            for _fid, fact_spans in _spans_from_result(reference)
            for span in fact_spans
        )
        if ref_spans:
            reports.append(_window_report("reference", ref_spans))
            verified_labels.append("reference")
    d_min: int | None = None
    d_min_proof: str | None = None
    crosses: bool | None = None
    for report in reports:
        if report.global_extent is None:
            continue
        if d_min is None or report.global_extent < d_min:
            d_min = report.global_extent
            d_min_proof = report.proof_label
            crosses = report.crosses_documents
    labels = tuple(report.proof_label for report in reports)
    note = (
        "D_min is the minimum over CHECKED proofs only (charter §6: "
        "只发现部分证明时,在已认证证明范围内标注). Bounded search does "
        "not enumerate all proofs; an unchecked shorter window may exist. "
        "A window crossing documents mixes coordinate systems — see "
        "per_document windows."
        if reports
        else "no checked proof carried spans; D_min is undefined"
    )
    return DMinResult(
        proofs_considered=labels,
        verified_proofs_considered=tuple(verified_labels),
        d_min=d_min,
        d_min_proof=d_min_proof,
        d_min_crosses_documents=crosses,
        window_reports=tuple(reports),
        note=note,
    )


# --- 4. deletion -> non-uniqueness: the W1 != W2 construction ---


@dataclass(frozen=True)
class CandidateWorlds:
    """Two worlds consistent with C-minus, built from one deleted fact."""

    c_minus: SemanticWorld
    fact_id: str
    fact_subject: str
    fact_relation: str
    original_value: Any
    mutated_value: Any
    w1: SemanticWorld
    w2: SemanticWorld

    def to_dict(self) -> dict[str, Any]:
        return {
            "c_minus_fact_count": len(self.c_minus.facts),
            "fact_id": self.fact_id,
            "subject": self.fact_subject,
            "relation": self.fact_relation,
            "original_value": self.original_value,
            "mutated_value": self.mutated_value,
        }


@dataclass(frozen=True)
class NonUniquenessResult:
    """Deletion verdict: underdetermined (W1 != W2), or honest failure.

    C-minus is the world with the minimal evidence rows removed. W1 gives
    one deleted fact its original value back (a C-minus-consistent world);
    W2 gives it a different candidate value. Q is the program. When
    Q(W1) != Q(W2) the deletion proves the answer is UNDERDETERMINED —
    the report is a verdict, never a solver error. When no pair differs the
    verdict is "deletion_insufficient": this single deletion does not make
    the answer non-unique, and no stronger claim is made.
    """

    verdict: str  # "underdetermined" | "deletion_insufficient" | "budget_exhausted"
    deleted_fact_ids: tuple[str, ...]
    world_pair: CandidateWorlds | None
    q_w1: Any | None
    q_w2: Any | None
    answers_differ: bool | None  # None when the search never ran
    tried_fact_ids: tuple[str, ...]
    reexecutions_used: int  # re-executions THIS phase performed
    budget_total: int
    budget_used_total: int  # pool usage incl. earlier phases sharing it
    # which re-executions produced values: "w1" / "w2" of which fact
    execution_log: tuple[tuple[str, str, Any], ...]  # (fact_id, world, answer)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "deleted_fact_ids": list(self.deleted_fact_ids),
            "world_pair": self.world_pair.to_dict() if self.world_pair else None,
            "q_w1": self.q_w1,
            "q_w2": self.q_w2,
            "answers_differ": self.answers_differ,
            "tried_fact_ids": list(self.tried_fact_ids),
            "reexecutions_used": self.reexecutions_used,
            "budget_total": self.budget_total,
            "budget_used_total": self.budget_used_total,
            "execution_log": [
                {"fact_id": fid, "world": which, "answer": answer}
                for fid, which, answer in self.execution_log
            ],
        }


def _candidate_value(fact: Fact, world: SemanticWorld) -> Any | None:
    """A deterministic, different candidate value for one fact.

    Scalar facts step to the next distinct number. String facts try a
    suffixed variant; when that string is already used by a sibling fact of
    the same (subject, relation) the suffix keeps incrementing until it is
    unused. Rule facts flip the comparison's threshold. Entity facts find
    an unused object of the same family (an entity the subject does not
    already relate along that relation). A return of None means "no honest
    candidate found": the caller reports it, it does not guess.
    """
    if fact.value_type in ("number",):
        candidates = [fact.value + 1, fact.value - 1, fact.value * 2]
        for value in candidates:
            if value != fact.value:
                return value
        return None
    if fact.value_type == "string":
        base = f"{fact.value}_alt"
        used = {
            other.value
            for other in world.facts
            if other.subject == fact.subject and other.relation == fact.relation
        }
        suffix = 1
        while f"{base}{suffix}" in used:
            suffix += 1
        return f"{base}{suffix}"
    if fact.value_type == "rule":
        field, op, threshold = str(fact.value).split()
        if op in ("==", "!="):
            return None
        # flip direction and keep the threshold: a genuinely different rule
        flipped = {"<": ">=", "<=": ">", ">": "<=", ">=": "<"}
        return f"{field} {flipped[op]} {threshold}"
    # entity-valued: an object of the same family not already related
    family = world.objects[fact.value].entity_type
    related = {
        other.value
        for other in world.facts
        if other.subject == fact.subject and other.relation == fact.relation
    }
    for entity_id in sorted(world.objects_by_family.get(family, ())):
        if entity_id != fact.value and entity_id not in related:
            return entity_id
    return None


def deletion_non_uniqueness(
    world: SemanticWorld,
    program: dict[str, Any],
    minimal: MinimalEvidenceResult,
    *,
    budget: int | _Budget = DEFAULT_BUDGET,
    require_fold_gate: bool = True,
) -> NonUniquenessResult:
    """The §6 W1 != W2 construction on the minimal evidence E*.

    C-minus = world minus the E* rows. W1 = C-minus + E* re-added verbatim
    (the verified sufficient completion, so Q(W1) is the baseline answer by
    construction). W2 = C-minus + E* with ONE minimal fact's value changed
    (the simulated counterfactual: text edits with span re-offsetting via
    mutate_fact/mutate_relation). Both are consistent with C-minus: each
    restores exactly what the deletion left unconstrained.

    If Q(W1) != Q(W2) the deletion proves the answer UNDERDETERMINED — the
    report is a verdict, never a solver error. If no candidate value makes
    the answers differ, the verdict is "deletion_insufficient": THIS
    deletion (of all E* facts at once, with these single-value candidates)
    does not make the answer non-unique, and no stronger claim is made.
    Facts are tried in sorted order; the first differing pair wins and is
    reported; the budget stops the search with the facts tried so far.
    """
    tracker = budget if isinstance(budget, _Budget) else _Budget(budget)
    phase_start = tracker.used
    deleted = set(minimal.fact_ids)
    c_minus = _world_without_facts(world, deleted)
    # W1: every minimal row re-added verbatim. Q(W1) is the baseline answer
    # by the standalone sufficiency check, but it is re-executed (and
    # budgeted) so the pair in this report is always measured, not assumed.
    w1 = _readd_to(c_minus, tuple(world.facts_by_id[f] for f in minimal.fact_ids))
    tried: list[str] = []
    log: list[tuple[str, str, Any]] = []
    if not tracker.can_spend():
        return NonUniquenessResult(
            verdict="budget_exhausted",
            deleted_fact_ids=tuple(sorted(deleted)),
            world_pair=None,
            q_w1=None,
            q_w2=None,
            answers_differ=None,
            tried_fact_ids=(),
            reexecutions_used=tracker.used - phase_start,
            budget_used_total=tracker.used,
            budget_total=tracker.total,
            execution_log=(("W1", "budget_exhausted", None),),
        )
    ok1, r1, _ = _try_execute(w1, program, require_fold_gate)
    tracker.spend()
    log.append(("W1", "w1", r1.answer if ok1 else None))
    if not ok1:
        return NonUniquenessResult(
            verdict="deletion_insufficient",
            deleted_fact_ids=tuple(sorted(deleted)),
            world_pair=None,
            q_w1=None,
            q_w2=None,
            answers_differ=False,
            tried_fact_ids=(),
            reexecutions_used=tracker.used - phase_start,
            budget_used_total=tracker.used,
            budget_total=tracker.total,
            execution_log=tuple(log),
        )
    q_w1 = r1.answer

    for fact_id in minimal.fact_ids:
        fact = world.facts_by_id[fact_id]
        candidate = _candidate_value(fact, world)
        if candidate is None:
            log.append((fact_id, "no_candidate", None))
            continue
        if not tracker.can_spend():
            log.append((fact_id, "budget_exhausted", None))
            break
        w2 = _mutated_e_star(c_minus, world, minimal.fact_ids, fact_id, candidate)
        if w2 is None:
            log.append((fact_id, "w2_unbuildable", None))
            continue
        tried.append(fact_id)
        ok2, r2, _ = _try_execute(w2, program, require_fold_gate)
        tracker.spend()
        log.append((fact_id, "w2", r2.answer if ok2 else None))
        if ok2 and q_w1 != r2.answer:
            pair = CandidateWorlds(
                c_minus=c_minus,
                fact_id=fact_id,
                fact_subject=fact.subject,
                fact_relation=fact.relation,
                original_value=fact.value,
                mutated_value=candidate,
                w1=w1,
                w2=w2,
            )
            return NonUniquenessResult(
                verdict="underdetermined",
                deleted_fact_ids=tuple(sorted(deleted)),
                world_pair=pair,
                q_w1=q_w1,
                q_w2=r2.answer,
                answers_differ=True,
                tried_fact_ids=tuple(tried),
                reexecutions_used=tracker.used - phase_start,
                budget_used_total=tracker.used,
                budget_total=tracker.total,
                execution_log=tuple(log),
            )
    return NonUniquenessResult(
        verdict="deletion_insufficient",
        deleted_fact_ids=tuple(sorted(deleted)),
        world_pair=None,
        q_w1=q_w1,
        q_w2=None,
        answers_differ=False,
        tried_fact_ids=tuple(tried),
        reexecutions_used=tracker.used - phase_start,
        budget_used_total=tracker.used,
        budget_total=tracker.total,
        execution_log=tuple(log),
    )


def _readd_to(base: SemanticWorld, facts: tuple[Fact, ...]) -> SemanticWorld:
    """C-minus plus original rows (W1); row re-add, no text edits."""
    merged = tuple(base.facts) + tuple(facts)
    return SemanticWorld(base.documents, base.entities, merged)


def _mutated_e_star(
    c_minus: SemanticWorld,
    world: SemanticWorld,
    e_star: tuple[str, ...],
    fact_id: str,
    new_value: Any,
) -> SemanticWorld | None:
    """W2: C-minus + E* with ONE fact's value changed, text-edit machinery.

    The mutate functions operate on a world that CONTAINS the fact being
    edited, so E* is re-added first, then the one row is value-changed with
    span re-offsetting. A mutation failure returns None (reported, never
    raised through); the candidate generator already avoids values that
    would break the world's own validation.
    """
    base = _readd_to(c_minus, tuple(world.facts_by_id[f] for f in e_star))
    fact = world.facts_by_id[fact_id]
    try:
        if fact.value_type == "entity":
            return mutate_relation(base, fact_id, new_value)
        return mutate_fact(base, fact_id, new_value)
    except ValueError:
        return None


# --- 5. the four certificates ---


CERTIFICATE_KEYS = (
    "surface_answer_supported",
    "evidence_sufficiency_checked",
    "alternative_proof_scope_checked",
    "long_span_requirement_verified_within_scope",
)


def certificates(
    execution: ExecutionResult,
    world: SemanticWorld,
    minimal: MinimalEvidenceResult,
    alt: AlternativeProofsResult,
    dmin: DMinResult,
    nonuniq: NonUniquenessResult,
) -> dict[str, Any]:
    """The four charter §6 certificates, each a scoped record, never one True.

    - surface_answer_supported: the execution succeeded and every proof span
      points at verbatim document text (re-checked here against the world).
    - evidence_sufficiency_checked: E* was FOUND and its standalone
      re-execution reproduced the answer; the search log is carried.
    - alternative_proof_scope_checked: the bounded search RAN and its scope
      (mechanisms, branches, budget, completeness) is recorded.
    - long_span_requirement_verified_within_scope: D_min over the CHECKED
      proofs (the with-claims scoped version: verified window, the with-
      claims label, and the D_min value with its crossing flag).
    """
    docs = {doc.doc_id: doc for doc in world.documents}
    spans_ok = True
    span_count = 0
    bad_spans: list[str] = []
    for item in execution.proof:
        for span, text in zip(item.spans, item.span_texts):
            span_count += 1
            if span.doc_id not in docs:
                spans_ok = False
                bad_spans.append(f"{item.ref_id}:{span.doc_id} unknown doc")
                continue
            actual = docs[span.doc_id].text[span.start : span.end]
            if actual != text:
                spans_ok = False
                bad_spans.append(
                    f"{item.ref_id}:{span.doc_id}[{span.start}:{span.end}] drifted"
                )
    e_star_found = bool(minimal.fact_ids) or bool(minimal.consumed_fact_ids)
    sufficiency_ok = minimal.standalone_verified and minimal.standalone_sufficient
    alt_ran = alt.branches_executed > 0 or alt.branches_enumerated == 0
    d_min_value = dmin.d_min
    long_span_ok = d_min_value is not None and d_min_value > 0
    return {
        "surface_answer_supported": {
            "value": spans_ok,
            "spans_checked": span_count,
            "answer": execution.answer,
            "bad_spans": bad_spans,
        },
        "evidence_sufficiency_checked": {
            "value": sufficiency_ok,
            "e_star": list(minimal.fact_ids),
            "search_log_entries": len(minimal.search_log),
            "passes": minimal.passes,
            "complete": minimal.complete,
        },
        "alternative_proof_scope_checked": {
            "value": alt_ran,
            "mechanisms": list(alt.mechanisms),
            "branches_enumerated": alt.branches_enumerated,
            "branches_executed": alt.branches_executed,
            "reexecutions_used": alt.reexecutions_used,
            "budget_total": alt.budget_total,
            "proof_search_complete_within_supported_language": alt.complete,
            "alternative_proofs_checked": len(alt.checked),
        },
        "long_span_requirement_verified_within_scope": {
            "value": long_span_ok,
            "d_min": d_min_value,
            "d_min_proof": dmin.d_min_proof,
            "d_min_crosses_documents": dmin.d_min_crosses_documents,
            "verified_proofs_considered": list(dmin.verified_proofs_considered),
        },
    }


# --- 6. one-call analysis: all phases under one shared budget ---


@dataclass(frozen=True)
class TaskAnalysis:
    """Everything T3 computes for one (task, world) pair."""

    execution: ExecutionResult
    minimal: MinimalEvidenceResult
    alt: AlternativeProofsResult
    dmin: DMinResult
    non_uniqueness: NonUniquenessResult
    certificates: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.execution.answer,
            "minimal_evidence": self.minimal.to_dict(),
            "alternative_proofs": self.alt.to_dict(),
            "d_min": self.dmin.to_dict(),
            "non_uniqueness": self.non_uniqueness.to_dict(),
            "certificates": self.certificates,
        }


def analyze(
    world: SemanticWorld,
    program: dict[str, Any],
    *,
    budget: int = DEFAULT_BUDGET,
    require_fold_gate: bool = True,
) -> TaskAnalysis:
    """Run all four T3 phases for one (world, program) under one budget.

    The budget is shared: minimal-evidence search, alternative-proof search,
    and the W1/W2 construction all draw re-executions from the same pool, so
    the ≤64-per-task bound is a real bound. Phases run in charter order;
    each phase's result is complete independently of the others.
    """
    tracker = _Budget(budget)
    execution = execute(world, program, require_fold_gate=require_fold_gate)
    minimal = minimal_evidence(
        world,
        program,
        execution,
        budget=tracker,
        require_fold_gate=require_fold_gate,
    )
    alt = alternative_proofs(
        world,
        program,
        execution,
        minimal=minimal,
        budget=tracker,
        require_fold_gate=require_fold_gate,
    )
    dmin = minimum_span(minimal, alt, reference=execution)
    nonuniq = deletion_non_uniqueness(
        world,
        program,
        minimal,
        budget=tracker,
        require_fold_gate=require_fold_gate,
    )
    cert = certificates(execution, world, minimal, alt, dmin, nonuniq)
    return TaskAnalysis(
        execution=execution,
        minimal=minimal,
        alt=alt,
        dmin=dmin,
        non_uniqueness=nonuniq,
        certificates=cert,
    )
