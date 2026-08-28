from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from longworld.core.consist import artifact_text_issues, is_real_workflow_body
from longworld.core.engine import (
    answer_from_artifacts,
    answer_from_events,
    semantic_answer_from_artifacts,
)
from longworld.core.render import Artifact, semantic_attestation_valid
from longworld.core.retrieve import (
    bm25_top1_insufficient,
    bm25_topk_insufficient,
    contiguous_windows_insufficient,
    embedding_topk_insufficient,
    lexical_tfidf_topk_insufficient,
    raw_token_fact_windows_insufficient,
)
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
    production_mode: bool = True
    candidate_mode: bool = False
    full_sufficient: bool = False
    minimal_sufficient: bool = False
    semantic_sufficient: bool = False
    strict_executable_sufficient: bool = False
    remove_one_fails: bool = False
    counterfactual_changes_answer: bool = False
    counterfactual_replay_sufficient: bool = False
    local_window_insufficient: bool = False
    contiguous_windows_insufficient: bool = True
    closed_book_unsolved: bool = False
    distractor_invariance_gold: bool = False
    surface_match: bool = False
    schema_ok: bool = False
    no_shortcut: bool = False
    min_complexity: bool = False
    bm25_top1_insufficient: bool = True
    bm25_topk_insufficient: bool = True
    lexical_tfidf_topk_insufficient: bool = True
    embedding_topk_insufficient: bool = False
    question_only_unsolved: bool = False
    essential_single_doc_insufficient: bool = False
    essential_surface_gold_free: bool = False
    essential_text_grounded: bool = False

    def all_green(self) -> bool:
        checks = [
            self.full_sufficient,
            self.minimal_sufficient,
            self.remove_one_fails,
            self.counterfactual_changes_answer,
            self.local_window_insufficient,
            self.closed_book_unsolved,
            self.distractor_invariance_gold,
            self.schema_ok,
            self.no_shortcut,
            self.min_complexity,
        ]
        if self.production_mode or self.candidate_mode:
            checks.extend(
                [
                    self.semantic_sufficient,
                    self.strict_executable_sufficient,
                    self.counterfactual_replay_sufficient,
                    self.contiguous_windows_insufficient,
                    self.bm25_top1_insufficient,
                    self.bm25_topk_insufficient,
                    self.lexical_tfidf_topk_insufficient,
                    self.essential_single_doc_insufficient,
                    self.essential_surface_gold_free,
                    self.essential_text_grounded,
                ]
            )
        if self.production_mode:
            checks.extend([self.surface_match, self.embedding_topk_insufficient])
        return all(checks)


class ViewVerification(BaseModel):
    expected_answer: str
    strict_replay_answer: str
    essential_present: bool = False
    semantic_text_grounded: bool = False
    classification_ok: bool = False
    global_proof_green: bool = False
    production_eligible: bool = False


def verify_rendered_view(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    *,
    view_name: str,
    expected_answer: str,
    base_verification: Verification,
    classification_ok: bool,
) -> ViewVerification:
    """Verify the exact artifacts and answer serialized for one training view."""
    index = _by_id(artifacts)
    essential = [index.get(artifact_id) for artifact_id in spec.essential_artifact_ids]
    essential_present = bool(essential) and all(essential)
    essential_artifacts = [artifact for artifact in essential if artifact is not None]
    text_grounded = (
        essential_present
        and not artifact_text_issues(
            world,
            essential_artifacts,
            require_attestation=(
                base_verification.production_mode or base_verification.candidate_mode
            ),
        )
        and not _sec_essential_evidence_issues(world, spec, essential_artifacts)
    )
    overrides = {spec.cf_event_id: spec.cf_param_updates} if view_name == "cf" else None
    strict_answer = answer_from_artifacts(
        world,
        spec,
        artifacts,
        extra_overrides=overrides,
        enforce_preconditions=True,
    )
    global_green = base_verification.all_green()
    eligible = bool(
        expected_answer
        and expected_answer != REFUSAL
        and strict_answer == expected_answer
        and essential_present
        and text_grounded
        and classification_ok
        and global_green
    )
    return ViewVerification(
        expected_answer=expected_answer,
        strict_replay_answer=strict_answer,
        essential_present=essential_present,
        semantic_text_grounded=text_grounded,
        classification_ok=classification_ok,
        global_proof_green=global_green,
        production_eligible=eligible,
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


def _sec_essential_evidence_issues(
    world: SimulatedWorld, spec: QuerySpec, artifacts: list[Artifact]
) -> list[str]:
    """Reject SEC source nodes that are only structural, not answer-bearing."""
    if spec.query_type != "sec_financial_reconstruction":
        return []
    events = {event.id: event for event in world.events}
    required_roles = {
        str(role)
        for event_id in spec.sufficient_event_ids
        if event_id in events and events[event_id].type == "sec_financial_answer"
        for role in events[event_id].params.get("required_roles") or []
    }
    issues: list[str] = []
    for artifact in artifacts:
        if artifact.artifact_id not in spec.essential_artifact_ids:
            continue
        source_events = [
            events[event_id]
            for event_id in artifact.reveals_events
            if event_id in events and events[event_id].type == "sec_source_section"
        ]
        for event in source_events:
            relevant = any(
                isinstance(span, dict)
                and (
                    span.get("kind") == "certification"
                    or str(span.get("role") or "") in required_roles
                )
                for span in event.params.get("fact_spans") or []
            )
            if not relevant:
                issues.append(artifact.artifact_id)
    return issues


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
        if (
            a.doc_type != "source_pack"
            and not is_real_workflow_body(a)
            and (FACT_HEADER.search(a.text) or KV_LEAK.search(a.text))
        ):
            return False, f"kv_or_header:{a.artifact_id}"
        if a.artifact_id in ess:
            continue
        if (
            gold
            and answer_from_artifacts(world, spec, [a], enforce_preconditions=True)
            == gold
        ):
            return False, f"single_doc:{a.artifact_id}"
    if gold and gold in spec.question and spec.query_type != "counterfactual":
        return False, "gold_in_question"
    bm25_ok, bm25_notes = bm25_top1_insufficient(world, spec, artifacts)
    if not bm25_ok:
        return False, f"bm25_top1:{bm25_notes.get('bm25_top1_id')}"
    return True, "ok"


@lru_cache(maxsize=8)
def token_overlap(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def artifact_surface_ratio(
    factual_artifacts: list[Artifact], counterfactual_artifacts: list[Artifact]
) -> float:
    factual = {artifact.artifact_id: artifact.text for artifact in factual_artifacts}
    counterfactual = {
        artifact.artifact_id: artifact.text for artifact in counterfactual_artifacts
    }
    total = max(sum(map(len, factual.values())), sum(map(len, counterfactual.values())))
    if total == 0:
        return 1.0
    matched = 0.0
    for artifact_id in sorted(factual.keys() & counterfactual.keys()):
        left = factual[artifact_id]
        right = counterfactual[artifact_id]
        if left == right:
            matched += len(left)
        else:
            matched += token_overlap(left, right) * max(len(left), len(right))
    return matched / total


def _surface_contains_answer(text: str, answer: str) -> bool:
    answer = (answer or "").strip()
    if not answer:
        return False
    return (
        re.search(rf"(?<!\w){re.escape(answer)}(?!\w)", text, re.IGNORECASE) is not None
    )


def query_adjacent_window_artifact_ids(
    artifacts: list[Artifact], query_timing: str, essential_ids: set[str]
) -> list[str]:
    """Select the exact non-proof records nearest the serialized query boundary."""
    nonessential = [
        artifact
        for artifact in artifacts
        if artifact.is_focal and artifact.artifact_id not in essential_ids
    ]
    selected = nonessential[-2:] if query_timing == "late" else nonessential[:2]
    return [artifact.artifact_id for artifact in selected]


def counterfactual_shortcuts_insufficient(
    world: SimulatedWorld,
    spec: QuerySpec,
    counterfactual: list[Artifact],
    *,
    retrieval_top_k: int = 3,
    contiguous_window_sizes: tuple[int, ...] = (4000, 8000, 16000),
    raw_window_tokenizer: Any | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Replay all cheap shortcut gates against the actual counterfactual dossier."""
    overrides = {spec.cf_event_id: spec.cf_param_updates}
    cf_index = _by_id(counterfactual)
    essential = [
        cf_index[artifact_id]
        for artifact_id in spec.essential_artifact_ids
        if artifact_id in cf_index
    ]
    single_answers = [
        answer_from_artifacts(
            world,
            spec,
            [artifact],
            extra_overrides=overrides,
            enforce_preconditions=True,
        )
        for artifact in essential
    ]
    remove_answers = [
        answer_from_artifacts(
            world,
            spec,
            [
                artifact
                for artifact in essential
                if artifact.artifact_id != dropped.artifact_id
            ],
            extra_overrides=overrides,
            enforce_preconditions=True,
        )
        for dropped in essential
    ]
    bm25_top1, bm25_top1_notes = bm25_top1_insufficient(
        world,
        spec,
        counterfactual,
        expected_answer=spec.cf_answer,
        extra_overrides=overrides,
    )
    bm25_topk, bm25_topk_notes = bm25_topk_insufficient(
        world,
        spec,
        counterfactual,
        k=retrieval_top_k,
        expected_answer=spec.cf_answer,
        extra_overrides=overrides,
    )
    tfidf_topk, tfidf_topk_notes = lexical_tfidf_topk_insufficient(
        world,
        spec,
        counterfactual,
        k=retrieval_top_k,
        expected_answer=spec.cf_answer,
        extra_overrides=overrides,
    )
    windows, window_notes = contiguous_windows_insufficient(
        world,
        spec,
        counterfactual,
        window_sizes=contiguous_window_sizes,
        necessary_artifact_ids=set(spec.essential_artifact_ids),
        necessary_set_proven=(
            bool(essential)
            and len(essential) == len(spec.essential_artifact_ids)
            and all(answer != spec.cf_answer for answer in remove_answers)
        ),
        expected_answer=spec.cf_answer,
        extra_overrides=overrides,
    )
    raw_windows = True
    raw_window_notes: dict[str, Any] = {
        "applicable": False,
        "proof_mode": "tokenizer_not_supplied",
    }
    if raw_window_tokenizer is not None:
        raw_windows, raw_window_notes = raw_token_fact_windows_insufficient(
            world,
            spec,
            counterfactual,
            raw_window_tokenizer,
            contiguous_window_sizes,
            expected_answer=spec.cf_answer,
            extra_overrides=overrides,
        )
    all_green = all(
        (
            bool(essential),
            len(essential) == len(spec.essential_artifact_ids),
            all(answer != spec.cf_answer for answer in single_answers),
            all(answer != spec.cf_answer for answer in remove_answers),
            bm25_top1,
            bm25_topk,
            tfidf_topk,
            windows,
            raw_windows,
        )
    )
    return all_green, {
        "essential_single_answers": single_answers,
        "remove_one_answers": remove_answers,
        "bm25_top1": bm25_top1_notes,
        "bm25_topk": bm25_topk_notes,
        "lexical_tfidf_topk": tfidf_topk_notes,
        "contiguous_windows": window_notes,
        "raw_token_fact_windows": raw_window_notes,
        "all_green": all_green,
    }


def verify_question(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    cf_artifacts: list[Artifact] | None = None,
    window_ids: list[str] | None = None,
    surface_min: float = 0.82,
    surface_artifacts: list[Artifact] | None = None,
    retrieval_top_k: int = 3,
    embedding_ranked_ids: list[str] | None = None,
    embedding_model_id: str | None = None,
    contiguous_window_sizes: tuple[int, ...] = (4000, 8000, 16000),
    verification_mode: str = "legacy",
    raw_window_tokenizer: Any | None = None,
    require_raw_token_windows: bool | None = None,
) -> tuple[Verification, dict[str, Any]]:
    if verification_mode not in {"production", "candidate", "legacy", "diagnostic"}:
        raise ValueError(f"unknown verification_mode: {verification_mode}")
    v = Verification(
        production_mode=verification_mode == "production",
        candidate_mode=verification_mode == "candidate",
    )
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
    semantic_min_artifacts = (
        [artifact for artifact in min_arts if semantic_attestation_valid(artifact)]
        if verification_mode in {"production", "candidate"}
        else min_arts
    )
    semantic_min_ans = semantic_answer_from_artifacts(
        world, spec, semantic_min_artifacts
    )
    notes["semantic_min_ans"] = semantic_min_ans
    notes["semantic_proof_scope"] = "attested_artifact_bytes_and_params"

    sufficient_event_ids = set(spec.sufficient_event_ids)
    strict_proof_artifacts = [
        artifact
        for artifact in artifacts
        if sufficient_event_ids.intersection(artifact.reveals_events)
    ]
    strict_min_ans = answer_from_artifacts(
        world, spec, strict_proof_artifacts, enforce_preconditions=True
    )
    v.strict_executable_sufficient = strict_min_ans == gold
    notes["strict_min_ans"] = strict_min_ans
    notes["strict_proof_scope"] = "declared_sufficient_event_set"
    notes["strict_proof_artifact_ids"] = [
        artifact.artifact_id for artifact in strict_proof_artifacts
    ]
    strict_full_ans = answer_from_artifacts(
        world, spec, artifacts, enforce_preconditions=True
    )
    notes["strict_full_ans"] = strict_full_ans

    essential_checks = []
    single_doc_ok = bool(min_arts)
    surface_gold_free = True
    for artifact in min_arts:
        replay_answer = answer_from_artifacts(
            world, spec, [artifact], enforce_preconditions=True
        )
        surface_gold = _surface_contains_answer(artifact.text, gold)
        essential_checks.append(
            {
                "artifact_id": artifact.artifact_id,
                "replay_answer": replay_answer,
                "surface_gold_present": surface_gold,
            }
        )
        if replay_answer == gold:
            single_doc_ok = False
        if surface_gold:
            surface_gold_free = False
    v.essential_single_doc_insufficient = single_doc_ok
    v.essential_surface_gold_free = surface_gold_free and bool(min_arts)
    notes["essential_single_docs"] = essential_checks

    text_issues = artifact_text_issues(
        world,
        min_arts,
        require_attestation=verification_mode in {"production", "candidate"},
    )
    sec_evidence_issues = _sec_essential_evidence_issues(world, spec, min_arts)
    v.essential_text_grounded = (
        bool(min_arts) and not text_issues and not sec_evidence_issues
    )
    v.semantic_sufficient = semantic_min_ans == gold and v.essential_text_grounded
    notes["essential_text_issues"] = [
        {
            "artifact_id": issue.artifact_id,
            "kind": issue.kind,
            "detail": issue.detail,
        }
        for issue in text_issues
    ]
    notes["sec_essential_evidence_issues"] = sec_evidence_issues

    remove_ok = True
    remove_notes = []
    if len(spec.essential_artifact_ids) == 0:
        remove_ok = False
    for aid in spec.essential_artifact_ids:
        remaining = [a for a in min_arts if a.artifact_id != aid]
        semantic_ans = semantic_answer_from_artifacts(world, spec, remaining)
        strict_ans = answer_from_artifacts(
            world, spec, remaining, enforce_preconditions=True
        )
        remove_notes.append(
            {"drop": aid, "semantic_ans": semantic_ans, "strict_ans": strict_ans}
        )
        if semantic_ans == gold:
            remove_ok = False
    v.remove_one_fails = remove_ok and len(spec.essential_artifact_ids) > 0
    notes["remove_one"] = remove_notes
    notes["remove_one_scope"] = "semantic_essential_proof_set"

    cf_ans = spec.cf_answer
    cf_label_changes = bool(cf_ans) and cf_ans != gold and cf_ans != "unknown"
    cf_replay_ans = None
    cf_text_issues = []
    cf_missing_essential = list(spec.essential_artifact_ids)
    if cf_artifacts is not None:
        cf_index = _by_id(cf_artifacts)
        cf_missing_essential = [
            artifact_id
            for artifact_id in spec.essential_artifact_ids
            if artifact_id not in cf_index
        ]
        cf_min_artifacts = [
            cf_index[artifact_id]
            for artifact_id in spec.essential_artifact_ids
            if artifact_id in cf_index
        ]
        cf_text_issues = artifact_text_issues(
            world,
            cf_min_artifacts,
            require_attestation=verification_mode in {"production", "candidate"},
        )
        cf_replay_ans = semantic_answer_from_artifacts(
            world,
            spec,
            cf_artifacts,
            extra_overrides={spec.cf_event_id: spec.cf_param_updates},
            enforce_preconditions=True,
        )
    v.counterfactual_replay_sufficient = (
        cf_replay_ans == cf_ans
        and bool(cf_ans)
        and not cf_missing_essential
        and not cf_text_issues
    )
    v.counterfactual_changes_answer = cf_label_changes and (
        v.counterfactual_replay_sufficient
        or verification_mode not in {"production", "candidate"}
    )
    notes["cf_ans"] = cf_ans
    notes["cf_replay_ans"] = cf_replay_ans
    notes["cf_missing_essential"] = cf_missing_essential
    notes["cf_text_issues"] = [
        {
            "artifact_id": issue.artifact_id,
            "kind": issue.kind,
            "detail": issue.detail,
        }
        for issue in cf_text_issues
    ]

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
    win_ans = answer_from_artifacts(world, spec, win_arts, enforce_preconditions=True)
    v.local_window_insufficient = win_ans != gold
    notes["window_ans"] = win_ans
    notes["window_ids"] = window_ids

    contiguous_ok, contiguous_notes = contiguous_windows_insufficient(
        world,
        spec,
        artifacts,
        window_sizes=contiguous_window_sizes,
        necessary_artifact_ids=set(spec.essential_artifact_ids),
        necessary_set_proven=(v.remove_one_fails and v.strict_executable_sufficient),
    )
    if raw_window_tokenizer is not None:
        raw_windows_ok, raw_window_notes = raw_token_fact_windows_insufficient(
            world,
            spec,
            artifacts,
            raw_window_tokenizer,
            contiguous_window_sizes,
        )
        contiguous_ok = contiguous_ok and raw_windows_ok
        notes["raw_token_fact_windows"] = raw_window_notes
    elif (
        (
            verification_mode in {"candidate", "production"}
            and spec.query_type
            in {"sec_financial_reconstruction", "wiki_claim_reconstruction"}
        )
        if require_raw_token_windows is None
        else require_raw_token_windows
    ):
        contiguous_ok = False
        notes["raw_token_fact_windows"] = {
            "applicable": True,
            "error": "pinned_tokenizer_required",
        }
    v.contiguous_windows_insufficient = contiguous_ok
    notes["contiguous_windows"] = contiguous_notes

    closed_ans = answer_from_events(world, spec, [])
    v.closed_book_unsolved = closed_ans != gold and closed_book_rule(gold)
    v.question_only_unsolved = v.closed_book_unsolved
    notes["closed_ans"] = closed_ans
    bm25_ok, bm25_notes = bm25_top1_insufficient(world, spec, artifacts)
    v.bm25_top1_insufficient = bm25_ok
    notes.update(bm25_notes)
    bm25_topk_ok, bm25_topk_notes = bm25_topk_insufficient(
        world, spec, artifacts, k=retrieval_top_k
    )
    v.bm25_topk_insufficient = bm25_topk_ok
    notes["bm25_topk"] = bm25_topk_notes
    lexical_ok, lexical_notes = lexical_tfidf_topk_insufficient(
        world, spec, artifacts, k=retrieval_top_k
    )
    v.lexical_tfidf_topk_insufficient = lexical_ok
    notes["lexical_tfidf_topk"] = lexical_notes
    embedding_ok, embedding_notes = embedding_topk_insufficient(
        world,
        spec,
        artifacts,
        ranked_artifact_ids=embedding_ranked_ids,
        model_id=embedding_model_id,
        k=retrieval_top_k,
    )
    v.embedding_topk_insufficient = embedding_ok
    notes["embedding_topk"] = embedding_notes

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
        surface_src = surface_artifacts if surface_artifacts is not None else artifacts
        ratio = artifact_surface_ratio(
            [artifact for artifact in surface_src if artifact.is_focal],
            [artifact for artifact in cf_artifacts if artifact.is_focal],
        )
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


def verify_packed_question(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    *,
    base_verification: Verification,
    base_notes: dict[str, Any],
    cf_artifacts: list[Artifact] | None = None,
    window_ids: list[str] | None = None,
    retrieval_top_k: int = 3,
    embedding_ranked_ids: list[str] | None = None,
    embedding_model_id: str | None = None,
    contiguous_window_sizes: tuple[int, ...] = (4000, 8000, 16000),
    verification_mode: str = "legacy",
    raw_window_tokenizer: Any | None = None,
) -> tuple[Verification, dict[str, Any]]:
    """Reuse a global proof while recomputing gates that depend on the packed view."""
    if verification_mode not in {"production", "candidate", "legacy", "diagnostic"}:
        raise ValueError(f"unknown verification_mode: {verification_mode}")
    expected_production = verification_mode == "production"
    expected_candidate = verification_mode == "candidate"
    if (
        base_verification.production_mode != expected_production
        or base_verification.candidate_mode != expected_candidate
    ):
        raise ValueError("base proof verification mode does not match packed view")

    del base_notes
    aligned_cf_artifacts = None
    if cf_artifacts is not None:
        cf_index = _by_id(cf_artifacts)
        aligned_cf_artifacts = [
            cf_index.get(artifact.artifact_id.split("#", 1)[0], artifact)
            for artifact in artifacts
        ]
    return verify_question(
        world,
        spec,
        artifacts,
        cf_artifacts=aligned_cf_artifacts,
        window_ids=window_ids,
        retrieval_top_k=retrieval_top_k,
        embedding_ranked_ids=embedding_ranked_ids,
        embedding_model_id=embedding_model_id,
        contiguous_window_sizes=contiguous_window_sizes,
        verification_mode=verification_mode,
        raw_window_tokenizer=raw_window_tokenizer,
    )
