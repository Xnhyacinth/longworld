from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, Field

from longworld.core.engine import answer_from_artifacts, answer_from_events
from longworld.core.render import Artifact
from longworld.core.world import SimulatedWorld
from longworld.domains.company.queries import QuerySpec, gold_from_full

REFUSAL = "unanswerable"


class ProofGraph(BaseModel):
    necessary_nodes: list[str]
    sufficient_set: list[str]
    answer_expression: str
    cf_event_id: str
    cf_op: str


class Difficulty(BaseModel):
    context_tokens: int = 0
    max_evidence_distance: int = 0
    proof_depth: int = 1
    state_updates: int = 0
    query_delay: int = 0
    distractor_similarity: float = 0.0
    visibility_gap: int = 0


class Verification(BaseModel):
    full_sufficient: bool = False
    minimal_sufficient: bool = False
    remove_one_fails: bool = False
    counterfactual_changes_answer: bool = False
    local_window_insufficient: bool = False
    closed_book_unsolved: bool = False
    distractor_invariance_gold: bool = False
    surface_match: bool = False
    schema_ok: bool = False
    no_shortcut: bool = False
    min_complexity: bool = False

    def all_green(self) -> bool:
        return all(
            [
                self.full_sufficient,
                self.minimal_sufficient,
                self.remove_one_fails,
                self.counterfactual_changes_answer,
                self.local_window_insufficient,
                self.closed_book_unsolved,
                self.distractor_invariance_gold,
                self.surface_match,
                self.schema_ok,
                self.no_shortcut,
                self.min_complexity,
            ]
        )


class SampleRecord(BaseModel):
    world_id: str
    seed: int
    schema_version: str
    query_id: str
    query_type: str
    query_timing: str
    question: str
    answer: str
    cf_answer: str
    view: str
    context: str
    proof_graph: ProofGraph
    difficulty: Difficulty
    verification: Verification
    essential_artifact_ids: list[str]
    window_artifact_ids: list[str] = Field(default_factory=list)
    position_bucket: str = "middle"
    length_bucket: str = "raw"
    split: str = "train"
    cf_op: str = "version"
    reject_reason: str | None = None


def _by_id(artifacts: list[Artifact]) -> dict[str, Artifact]:
    return {a.artifact_id: a for a in artifacts}


def closed_book_rule(answer: str) -> bool:
    """Pass if the gold answer is world-private (rare token / non-round number)."""
    if answer in {"unknown", "", REFUSAL, "None", "v1", "v2", "v3", "v4"}:
        return False
    if answer.lstrip("-").isdigit():
        n = abs(int(answer))
        return n > 1000 and n % 1000 != 0
    return any(ch.isdigit() for ch in answer) or "-" in answer or "_" in answer


KV_LEAK = re.compile(r"^[\-\*\s]*[a-z][a-z0-9_]{2,}=.+$", re.MULTILINE)
FACT_HEADER = re.compile(
    r"(recorded facts|line items \(authoritative\)|recorded decisions \(authoritative\))",
    re.IGNORECASE,
)


def shortcut_free(
    world: SimulatedWorld,
    artifacts: list[Artifact],
    spec: QuerySpec,
) -> tuple[bool, str]:
    gold = spec.answer
    ess = set(spec.essential_artifact_ids)
    for a in artifacts:
        if FACT_HEADER.search(a.text) or KV_LEAK.search(a.text):
            return False, f"kv_or_header:{a.artifact_id}"
        if a.artifact_id in ess:
            continue
        if gold and answer_from_artifacts(world, spec, [a]) == gold:
            return False, f"single_doc:{a.artifact_id}"
    if gold and gold in spec.question and spec.query_type != "counterfactual":
        return False, "gold_in_question"
    return True, "ok"


def token_overlap(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def verify_question(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    cf_artifacts: list[Artifact] | None = None,
    window_ids: list[str] | None = None,
    surface_min: float = 0.82,
) -> tuple[Verification, dict[str, Any]]:
    v = Verification()
    index = _by_id(artifacts)
    missing = [i for i in spec.essential_artifact_ids if i not in index]
    notes: dict[str, Any] = {"missing_essential": missing}

    gold = spec.answer or gold_from_full(world, spec)
    v.schema_ok = bool(gold) and gold != "unknown" and not missing

    full_ans = answer_from_artifacts(world, spec, artifacts)
    v.full_sufficient = full_ans == gold
    notes["full_ans"] = full_ans

    min_arts = [index[i] for i in spec.essential_artifact_ids if i in index]
    min_ans = answer_from_artifacts(world, spec, min_arts)
    v.minimal_sufficient = min_ans == gold
    notes["min_ans"] = min_ans

    remove_ok = True
    remove_notes = []
    if len(spec.essential_artifact_ids) == 0:
        remove_ok = False
    for aid in spec.essential_artifact_ids:
        remaining = [a for a in artifacts if a.artifact_id != aid]
        ans = answer_from_artifacts(world, spec, remaining)
        remove_notes.append({"drop": aid, "ans": ans})
        if ans == gold:
            remove_ok = False
    v.remove_one_fails = remove_ok and len(spec.essential_artifact_ids) > 0
    notes["remove_one"] = remove_notes

    cf_ans = spec.cf_answer
    v.counterfactual_changes_answer = (
        bool(cf_ans) and cf_ans != gold and cf_ans != "unknown"
    )
    notes["cf_ans"] = cf_ans

    if window_ids is None:
        # Default: latest non-essential focal artifacts (query-adjacent distractors).
        non_ess = [
            a
            for a in artifacts
            if a.is_focal and a.artifact_id not in set(spec.essential_artifact_ids)
        ]
        non_ess.sort(key=lambda a: a.time)
        window_ids = [a.artifact_id for a in non_ess[-2:]]
    win_arts = [index[i] for i in window_ids if i in index]
    win_ans = answer_from_artifacts(world, spec, win_arts)
    v.local_window_insufficient = win_ans != gold
    notes["window_ans"] = win_ans
    notes["window_ids"] = window_ids

    closed_ans = answer_from_events(world, spec, [])
    v.closed_book_unsolved = closed_ans != gold and closed_book_rule(gold)
    notes["closed_ans"] = closed_ans

    if spec.invariance_event_id:
        inv = answer_from_events(
            world,
            spec,
            [e.id for e in world.events],
            extra_overrides={spec.invariance_event_id: spec.invariance_param_updates},
        )
        v.distractor_invariance_gold = inv == gold
        notes["invariance_ans"] = inv
    else:
        v.distractor_invariance_gold = True

    if cf_artifacts is None:
        v.surface_match = True
    else:
        full_text = "\n".join(a.text for a in artifacts if a.is_focal)
        cf_text = "\n".join(a.text for a in cf_artifacts if a.is_focal)
        ratio = token_overlap(full_text, cf_text)
        v.surface_match = ratio >= surface_min
        notes["surface_ratio"] = ratio

    ok_sc, sc_why = shortcut_free(world, artifacts, spec)
    v.no_shortcut = ok_sc
    notes["shortcut"] = sc_why
    vis_gap = len(
        {a.doc_type for a in artifacts if a.artifact_id in spec.essential_artifact_ids}
    )
    v.min_complexity = (
        spec.proof_depth >= 2 and len(spec.essential_artifact_ids) >= 2
    ) or spec.query_type == "counterfactual"
    notes["visibility_gap"] = vis_gap

    return v, notes
