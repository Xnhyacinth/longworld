from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from longworld.core.asof import find_event, world_as_of
from longworld.core.groundedspan import normalize_fact_value
from longworld.core.scholarly import (
    format_revision_added_delta,
    format_revision_funding_delta,
)
from longworld.core.wikiparse import WIKI_ENTITY_VIEW_PREFIX, WIKI_SECTION_VIEW_PREFIX
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.company.queries import (
    QuerySpec,
    _merge_overrides,
    instance_topology,
)
from longworld.domains.researchlab.events import apply_event, check_preconditions


def _sim_for(world: SimulatedWorld) -> WorldSimulator:
    return WorldSimulator(
        spec=world.spec,
        init_values=world.init_values,
        check_preconditions=check_preconditions,
        apply_event=apply_event,
    )


def eval_answer(
    world: SimulatedWorld, spec: QuerySpec, state_values: dict[str, Any]
) -> str:
    val = state_values.get(spec.answer_key)
    if spec.query_type == "fork_join":
        cache = state_values.get("cause_cache")
        split = state_values.get("cause_split")
        if not cache or not split:
            return "unknown"
        return f"{cache}+{split}"
    if spec.query_type == "delayed_effect":
        c = state_values.get("tokenizer_commit")
        if not c or state_values.get("rerun_score") is None:
            return "unknown"
        return str(c)
    if spec.query_type == "counterfactual":
        if not state_values.get("release_published"):
            return "unknown"
        val = state_values.get("authoritative_score")
        if val is None:
            return "unknown"
        return str(val)
    if spec.query_type == "contradiction":
        body = state_values.get("body_score")
        auth = state_values.get("authoritative_score")
        if body is None or auth is None:
            return "unknown"
        return f"{body} -> {auth}"
    if spec.query_type == "hidden_bridge":
        lic = state_values.get("ship_license")
        if not lic or not state_values.get("release_published"):
            return "unknown"
        return str(lic)
    if spec.query_type == "cross_stream":
        lic = state_values.get("score_license_at_withdraw")
        if lic is None or state_values.get("withdrawn_score") is None:
            return "unknown"
        return str(lic)
    if spec.query_type == "revision_reproduction_resolution":
        revision = state_values.get("accepted_revision")
        benchmark = state_values.get("accepted_benchmark")
        reproduction = state_values.get("accepted_reproduction")
        response = state_values.get("accepted_review_response")
        if not all((revision, benchmark, reproduction, response)):
            return "unknown"
        return f"{revision}@{benchmark}@{reproduction} via {response}"
    if spec.query_type == "review_response_trace":
        requirement = state_values.get("resolved_review_requirement")
        response = state_values.get("resolved_review_response")
        revision = state_values.get("resolved_revision")
        if not all((requirement, response, revision)):
            return "unknown"
        return f"{requirement} > {response} > {revision}"
    if spec.query_type == "benchmark_revision_conflict":
        candidate = state_values.get("benchmark_candidate")
        accepted = state_values.get("accepted_benchmark")
        revision = state_values.get("accepted_revision")
        if not candidate or not accepted or not revision:
            return "unknown"
        return f"{candidate} -> {accepted} @ {revision}"
    if spec.query_type == "experiment_matrix_resolution":
        matrix = state_values.get("experiment_matrix")
        if not matrix:
            return "unknown"
        return str(matrix)
    if spec.query_type == "real_revision_added_text":
        value = state_values.get("real_revision_added_text")
        return str(value) if value else "unknown"
    if spec.query_type == "wiki_claim_reconstruction":
        value = state_values.get(spec.answer_key)
        return str(value) if value else "unknown"
    if val is None or val is False:
        return "unknown"
    return str(val)


def gold_from_full(world: SimulatedWorld, spec: QuerySpec) -> str:
    from longworld.core.domain import eval_answer as deval

    st = _sim_for(world).replay_events(
        world.events, up_to=spec.as_of, param_overrides=_merge_overrides(spec)
    )
    return deval(world, spec, st.values)


def cf_from_full(world: SimulatedWorld, spec: QuerySpec) -> str:
    from longworld.core.domain import eval_answer as deval

    extra = {spec.cf_event_id: spec.cf_param_updates}
    st = _sim_for(world).replay_events(
        world.events,
        up_to=spec.as_of,
        param_overrides=_merge_overrides(spec, extra),
    )
    return deval(world, spec, st.values)


def _event(world: SimulatedWorld, suffix: str) -> Event:
    prefix = world.spec["prefix"]
    eid = f"{prefix}.{suffix}"
    for e in world.events:
        if e.id == eid:
            return e
    raise KeyError(eid)


def _aid(world: SimulatedWorld, suffix: str) -> str:
    return f"{world.spec['world_id']}.{world.spec['prefix']}.{suffix}"


def _event_artifact_id(world: SimulatedWorld, event: Event) -> str:
    return f"{world.spec['world_id']}.{event.visibility[0]}"


def _counterfactual_revision_text(text: str, value: str) -> tuple[str, str]:
    digits = list(re.finditer(r"\d", value))
    if not digits:
        raise ValueError("real revision answer must contain a counterfactual digit")
    digit = digits[-1]
    index = digit.start()
    replacement = "9" if value[index] != "9" else "8"
    changed_value = value[:index] + replacement + value[index + 1 :]
    if text.count(value) != 1:
        raise ValueError("real revision answer must occur once in source text")
    return text.replace(value, changed_value), changed_value


def _counterfactual_grounded_source(
    payload: object, *, original_quote: str, changed_quote: str, changed_text: str
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("counterfactual source binding is missing")
    facts = payload.get("facts")
    if not isinstance(facts, list):
        raise TypeError("counterfactual source facts are missing")
    changed = 0
    original_text = payload.get("visible_text")
    if not isinstance(original_text, str):
        raise TypeError("counterfactual visible source is missing")
    if any(not isinstance(fact, dict) for fact in facts):
        raise TypeError("counterfactual source fact is invalid")
    text_sha256 = hashlib.sha256(changed_text.encode()).hexdigest()
    updated_facts = []
    matching = [fact for fact in facts if fact.get("quote") == original_quote]
    if len(matching) != 1:
        raise ValueError("counterfactual source fact is not unique")
    target_start = matching[0].get("char_start")
    target_end = matching[0].get("char_end")
    if (
        isinstance(target_start, bool)
        or isinstance(target_end, bool)
        or not isinstance(target_start, int)
        or not isinstance(target_end, int)
        or original_text[target_start:target_end] != original_quote
        or changed_text
        != original_text[:target_start] + changed_quote + original_text[target_end:]
    ):
        raise ValueError("counterfactual source replacement is invalid")
    shift = len(changed_quote) - len(original_quote)
    for value in facts:
        if not isinstance(value, dict):
            raise TypeError("counterfactual source fact is invalid")
        fact = dict(value)
        fact["text_sha256"] = text_sha256
        if fact.get("quote") == original_quote:
            fact.update(
                {
                    "quote": changed_quote,
                    "char_end": target_start + len(changed_quote),
                    "normalized_quote": normalize_fact_value(changed_quote),
                }
            )
            changed += 1
        else:
            fact_start = fact.get("char_start")
            fact_end = fact.get("char_end")
            if (
                isinstance(fact_start, bool)
                or isinstance(fact_end, bool)
                or not isinstance(fact_start, int)
                or not isinstance(fact_end, int)
            ):
                raise ValueError("counterfactual source fact span is invalid")
            if fact_start >= target_end:
                fact["char_start"] = fact_start + shift
                fact["char_end"] = fact_end + shift
            elif fact_end > target_start:
                raise ValueError("counterfactual source facts overlap")
        updated_facts.append(fact)
    if changed != 1:
        raise ValueError("counterfactual source fact is not unique")
    return {
        **payload,
        "visible_text": changed_text,
        "text_sha256": text_sha256,
        "facts": updated_facts,
    }


def _wiki_program_ops(control_tier: str) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = [{"op": "READ_WIKI_FACT", "role": "born"}]
    if control_tier in {"32k", "64k"}:
        ops.extend(
            [
                {"op": "READ_WIKI_FACT", "role": "commemoration"},
                {"op": "READ_WIKI_FACT", "role": "entity"},
            ]
        )
    if control_tier == "64k":
        ops.append({"op": "READ_WIKI_FACT", "role": "popular_culture"})
    return ops


def _wiki_cf_updates(section: Event) -> dict[str, Any]:
    original = str(section.params.get("text") or "")
    born = next(
        (
            span
            for span in section.params.get("fact_spans") or []
            if isinstance(span, dict) and span.get("role") == "born"
        ),
        None,
    )
    if not isinstance(born, dict):
        return {"text": original}
    start = born.get("char_start")
    end = born.get("char_end")
    quote = str(born.get("evidence_quote") or "")
    value = str(born.get("value") or "")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or original[start:end] != quote
        or len(value) < 10
    ):
        return {"text": original}
    year = value[:4]
    if not year.isdigit() or quote.count(year) != 1:
        return {"text": original}
    mutated_year = str(int(year) + 1).zfill(4)
    mutated_quote = quote.replace(year, mutated_year, 1)
    mutated_value = mutated_year + value[4:]
    mutated = original[:start] + mutated_quote + original[end:]
    spans = []
    for span in section.params.get("fact_spans") or []:
        if not isinstance(span, dict):
            continue
        updated = dict(span)
        if span is born or (
            span.get("role") == "born" and span.get("evidence_quote") == quote
        ):
            updated["evidence_quote"] = mutated_quote
            updated["value"] = mutated_value
            updated["char_end"] = start + len(mutated_quote)
        else:
            span_start = span.get("char_start")
            span_end = span.get("char_end")
            if not isinstance(span_start, int) or not isinstance(span_end, int):
                raise ValueError("counterfactual wiki fact span is invalid")
            shift = len(mutated_quote) - len(quote)
            if span_start >= end:
                updated["char_start"] = span_start + shift
                updated["char_end"] = span_end + shift
            elif span_end > start:
                raise ValueError("counterfactual wiki fact spans overlap")
        spans.append(updated)
    grounded_source = _counterfactual_grounded_source(
        section.params.get("grounded_source"),
        original_quote=quote,
        changed_quote=mutated_quote,
        changed_text=mutated,
    )
    prefix = (
        WIKI_ENTITY_VIEW_PREFIX
        if section.params.get("section_id") == "wikidata_entity"
        else WIKI_SECTION_VIEW_PREFIX
    )
    section_sha256 = hashlib.sha256(mutated[len(prefix) :].encode()).hexdigest()
    parent_provenance_id = str(section.params.get("provenance_id") or "")
    provenance_payload = {
        "operation": "counterfactual_wiki_section_v1",
        "parent_provenance_id": parent_provenance_id,
        "parent_source_sha256": str(section.params.get("parent_source_sha256") or ""),
        "text_sha256": hashlib.sha256(mutated.encode()).hexdigest(),
    }
    provenance = hashlib.sha256(
        json.dumps(provenance_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "text": mutated,
        "text_sha256": hashlib.sha256(mutated.encode()).hexdigest(),
        "section_sha256": section_sha256,
        "fact_spans": spans,
        "grounded_source": grounded_source,
        "source_binding_provenance": "synthetic_executable",
        "provenance_operation": "counterfactual_wiki_section_v1",
        "parent_provenance_id": parent_provenance_id,
        "provenance_id": f"derived-sha256:{provenance}",
        "source_origin": "synthetic_world",
        "parent_source_origin": "real_derived",
        "parent_source_text_sha256": str(section.params.get("text_sha256") or ""),
    }


def build_lab_queries(world: SimulatedWorld) -> list[QuerySpec]:
    if world.spec.get("prefix") != "focal":
        return []
    p = world.spec["project"]
    paper, model, bench = p["paper"], p["model"], p["benchmark"]
    qid = world.spec["world_id"].split(":")[0]
    as_of_now = max(e.time for e in world.events)
    v1 = _event(world, "report_v1")
    cache = _event(world, "log_stale_cache")
    issue = _event(world, "issue_testset")
    rerun = _event(world, "fix_rerun")
    cam = _event(world, "camera_ready")
    rel = _event(world, "release_note")
    tok = _event(world, "commit_tokenizer")
    lic = _event(world, "license_clause")
    rev2 = _event(world, "submit_revision_2")
    review1 = _event(world, "review_round_1")
    response1 = _event(world, "respond_round_1")
    repro_fail = _event(world, "reproduction_failure")
    patch = _event(world, "benchmark_patch")
    review2 = _event(world, "review_round_2")
    response2 = _event(world, "respond_round_2")
    repro_recovery = _event(world, "reproduction_recovery")
    rev3 = _event(world, "submit_revision_3")
    resolve = _event(world, "resolve_review")
    meta = _event(world, "meta_decision")
    queries: list[QuerySpec] = []

    for relation in [
        event for event in world.events if event.type == "arxiv_revision_relation"
    ]:
        decision = next(
            event
            for event in world.events
            if event.type == "arxiv_revision_decision"
            and event.params.get("relation_event_id") == relation.id
        )
        source = next(
            event
            for event in world.events
            if event.id == relation.params["source_record_event_id"]
        )
        target = next(
            event
            for event in world.events
            if event.id == relation.params["target_record_event_id"]
        )
        start = int(relation.params["fact_char_start"])
        end = int(relation.params["fact_char_end"])
        offset = int(relation.params["fact_value_offset"])
        length = int(relation.params["fact_value_length"])
        quote = str(source.params["text"])[start:end]
        value = quote[offset : offset + length]
        if not format_revision_added_delta(value):
            continue
        funding = format_revision_funding_delta(value)
        cf_text, cf_value = _counterfactual_revision_text(
            str(source.params["text"]), value
        )
        cf_text_sha256 = hashlib.sha256(cf_text.encode()).hexdigest()
        cf_grounded_source = _counterfactual_grounded_source(
            source.params.get("grounded_source"),
            original_quote=value,
            changed_quote=cf_value,
            changed_text=cf_text,
        )
        cf_provenance = hashlib.sha256(
            json.dumps(
                {
                    "operation": "counterfactual_revision_text",
                    "parent_provenance_id": source.params["provenance_id"],
                    "text_sha256": cf_text_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        relation_key = hashlib.sha256(
            str(relation.params["relation_id"]).encode()
        ).hexdigest()[:12]
        if funding:
            question = (
                "Compare the two authentic arXiv manuscript bodies connected by "
                f"the verified revision relation for {relation.params['work_id']}. "
                "Report the original submission date and the funding disclosure "
                "newly added by the later body and selected by the subsequent "
                "revision-resolution workflow. Reply exactly as YYYY-MM-DD | "
                "<first_funder> | <agency_acronym> | NSF <grant_id>."
            )
            gold_expression = (
                "READ_DATE(earlier) AND READ_SOURCE_SPAN(later) AND "
                "ABSENT_FROM(earlier) THEN "
                "PARSE_FUNDING_DISCLOSURE AND FOLLOW(revision_of, resolution)"
            )
            parse_op = "PARSE_FUNDING_DISCLOSURE"
        else:
            question = (
                "Compare the two authentic arXiv manuscript bodies connected by "
                f"the verified revision relation for {relation.params['work_id']}. "
                "Report the original submission date and the unique semantic "
                "sentence newly added by the later body and selected by the "
                "subsequent revision-resolution workflow. Reply exactly as "
                "YYYY-MM-DD | <added_sentence>."
            )
            gold_expression = (
                "READ_DATE(earlier) AND READ_SOURCE_SPAN(later) AND "
                "ABSENT_FROM(earlier) THEN "
                "SELECT_UNIQUE_SEMANTIC_DELTA AND FOLLOW(revision_of, resolution)"
            )
            parse_op = "SELECT_UNIQUE_SEMANTIC_DELTA"
        queries.append(
            QuerySpec(
                query_id=f"{qid}:real_revision_added_text:{relation_key}",
                query_type="real_revision_added_text",
                question=question,
                answer="",
                as_of=as_of_now,
                answer_key="real_revision_added_text",
                essential_event_ids=[
                    target.id,
                    source.id,
                    relation.id,
                    decision.id,
                ],
                essential_artifact_ids=[
                    _event_artifact_id(world, target),
                    _event_artifact_id(world, source),
                    _event_artifact_id(world, relation),
                    _event_artifact_id(world, decision),
                ],
                sufficient_event_ids=[
                    target.id,
                    source.id,
                    relation.id,
                    decision.id,
                ],
                cf_event_id=source.id,
                cf_param_updates={
                    "text": cf_text,
                    "text_sha256": cf_text_sha256,
                    "parent_provenance_id": source.params["provenance_id"],
                    "provenance_id": f"derived-sha256:{cf_provenance}",
                    "provenance_operation": "counterfactual_revision_text",
                    "source_binding_provenance": "synthetic_executable",
                    "source_origin": "synthetic_world",
                    "parent_source_origin": "real_derived",
                    "parent_source_envelope_sha256": source.params[
                        "canonical_source_envelope_sha256"
                    ],
                    "parent_source_text_sha256": source.params["text_sha256"],
                    "excluded_paths": [],
                    "ground_values": [cf_value],
                    "grounded_source": cf_grounded_source,
                },
                cf_answer="",
                invariance_event_id=tok.id,
                invariance_param_updates={"commit": "ab00ab"},
                gold_expression=gold_expression,
                proof_depth=4,
                cf_op="revision_text",
                motif="real_revision_semantic_delta",
                topology_id=instance_topology(
                    "lab.real_revision_semantic_delta",
                    relation.params["relation_kind"],
                    relation.params["fact_id"],
                ),
                domain="researchlab",
                truth_regime="real_source_derived",
                program_ops=[
                    {"op": "READ_SOURCE_SPAN"},
                    {"op": "READ_PRIOR_SUBMISSION_DATE"},
                    {"op": "VERIFY_ABSENT_FROM_PRIOR_REVISION"},
                    {"op": "FOLLOW_REVISION_OF"},
                    {"op": parse_op},
                    {"op": "APPLY_REVISION_RESOLUTION"},
                ],
                preferred_length_buckets=["64k"],
                semantic_growth_group="researchlab_real_revision_delta",
            )
        )

    computes: dict[str, dict[str, Event]] = {}
    copies_16k: dict[str, list[Event]] = {}
    wiki_sections: dict[str, dict[str, Event]] = {}
    wiki_relations: dict[str, list[Event]] = {}
    for event in world.events:
        record_id = str(event.params.get("record_id") or "")
        if not record_id:
            continue
        if event.type == "wiki_claim_answer":
            tier = str(event.params.get("control_tier") or "")
            if str(event.params.get("compose") or "compute") == "copy":
                if tier == "16k":
                    copies_16k.setdefault(record_id, []).append(event)
            elif tier:
                computes.setdefault(record_id, {})[tier] = event
        elif event.type == "wiki_source_section":
            section_id = str(event.params.get("section_id") or "")
            if section_id:
                wiki_sections.setdefault(record_id, {})[section_id] = event
        elif event.type == "wiki_source_relation":
            wiki_relations.setdefault(record_id, []).append(event)
    for record_id, by_tier in computes.items():
        if any(tier not in by_tier for tier in ("16k", "32k", "64k")):
            continue
        copies = sorted(copies_16k.get(record_id) or [], key=lambda event: event.time)
        if len(copies) < 2:
            continue
        publish = copies[-1]
        by_section = wiki_sections.get(record_id) or {}
        early = by_section.get("early_work")
        if early is None:
            continue
        source_key = record_id.replace(":", "_")
        for control_tier, proof_depth, needed_names, extra_answers, extra_copies in (
            ("16k", 4, ("early_work",), ("16k",), copies),
            (
                "32k",
                5,
                ("early_work", "commemoration", "wikidata_entity"),
                ("16k",),
                (),
            ),
            (
                "64k",
                6,
                (
                    "early_work",
                    "commemoration",
                    "popular_culture",
                    "wikidata_entity",
                ),
                ("16k", "32k"),
                (),
            ),
        ):
            answer_event = publish if control_tier == "16k" else by_tier[control_tier]
            needed_sections = [by_section.get(name) for name in needed_names]
            needed_answers = [by_tier.get(name) for name in extra_answers]
            if any(item is None for item in (*needed_sections, *needed_answers)):
                continue
            essential_events = [
                *needed_sections,
                *needed_answers,
                *extra_copies,
            ]
            if control_tier in {"32k", "64k"}:
                essential_events.extend(
                    sorted(
                        wiki_relations.get(record_id) or [],
                        key=lambda event: (event.time, event.id),
                    )
                )
            if control_tier != "16k":
                essential_events.append(answer_event)
            essential_ids = [event.id for event in essential_events]
            queries.append(
                QuerySpec(
                    query_id=(
                        f"{qid}:wiki_claim_reconstruction:{source_key}:{control_tier}"
                    ),
                    query_type="wiki_claim_reconstruction",
                    question=(
                        "Reconstruct the tagged Wikipedia/Wikidata claim program "
                        f"through the {control_tier} control stage using only the "
                        "cited revision sections and entity record in context. "
                        "Do not use identity-header fields as substitutes for "
                        "body claims."
                    ),
                    answer="",
                    as_of=answer_event.time,
                    answer_key=str(answer_event.params["answer_key"]),
                    essential_event_ids=essential_ids,
                    essential_artifact_ids=[
                        f"{world.spec['world_id']}.{event.visibility[0]}"
                        for event in essential_events
                    ],
                    sufficient_event_ids=essential_ids,
                    cf_event_id=early.id,
                    cf_param_updates=_wiki_cf_updates(early),
                    cf_answer="",
                    invariance_event_id=early.id,
                    invariance_param_updates={
                        "retrieval_url": "https://en.wikipedia.org/w/index.php?oldid=0"
                    },
                    gold_expression="tagged BORN/COMM/ENTITY/POP reconstruction",
                    proof_depth=proof_depth,
                    cf_op="numeric",
                    motif="source-wiki-claim-program",
                    topology_id=instance_topology(
                        "lab.wiki_claim_reconstruction",
                        record_id,
                        control_tier,
                    ),
                    domain="researchlab",
                    truth_regime="real_source_derived",
                    program_ops=_wiki_program_ops(control_tier),
                    preferred_length_buckets=[control_tier],
                    semantic_growth_group="researchlab_real_wiki_claim_reconstruction",
                    base_task_group=f"wiki_claim_reconstruction:{source_key}",
                )
            )

    q_cur = QuerySpec(
        query_id=f"{qid}:current_state",
        query_type="current_state",
        question=(
            f"As of {as_of_now.isoformat()}, what is the authoritative score of "
            f"{model} on {bench} for {paper}? Camera-ready prose is not controlling "
            f"if a later release note exists. Reply with the numeric score only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_score",
        essential_event_ids=[rerun.id, rel.id],
        essential_artifact_ids=[_aid(world, "rerun_json"), _aid(world, "release_note")],
        sufficient_event_ids=[rerun.id, rel.id],
        cf_event_id=rerun.id,
        cf_param_updates={"score": round(float(p["final_score"]) + 1.13, 2)},
        cf_answer="",
        invariance_event_id=cam.id,
        invariance_param_updates={"body_score": 11.11},
        gold_expression="authoritative_score adopts rerun",
        proof_depth=3,
        cf_op="score",
        motif="supersession",
        topology_id=instance_topology(
            "lab.release_adopts_rerun", paper, p["final_score"]
        ),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_cur)

    q_fj = QuerySpec(
        query_id=f"{qid}:fork_join",
        query_type="fork_join",
        question=(
            f"For {paper}, which two independent evaluation faults jointly explain "
            f"why the January number exceeded the corrected run? Reply with the "
            f"lab-private cause token only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="inflation_cause",
        essential_event_ids=[cache.id, issue.id],
        essential_artifact_ids=[_aid(world, "eval_log"), _aid(world, "github_issue")],
        sufficient_event_ids=[cache.id, issue.id],
        cf_event_id=issue.id,
        cf_param_updates={
            "cause_split": f"SPLIT{int(p['final_score'] * 10) % 90 + 10}"
        },
        cf_answer="",
        invariance_event_id=tok.id,
        invariance_param_updates={"commit": "dead00"},
        gold_expression="cache token + split token",
        proof_depth=2,
        cf_op="score",
        motif="fork_join",
        topology_id=instance_topology("lab.cache_and_split", paper, p["cause_token"]),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_fj)

    q_del = QuerySpec(
        query_id=f"{qid}:delayed_effect",
        query_type="delayed_effect",
        question=(
            f"Which git commit of the {paper} tokenizer change is the delayed cause "
            f"that only becomes decision-relevant after the May rerun? Reply with "
            f"the six-hex commit token only. Ignore later release notes."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="tokenizer_commit",
        essential_event_ids=[tok.id, rerun.id],
        essential_artifact_ids=[_aid(world, "git_commit"), _aid(world, "rerun_json")],
        sufficient_event_ids=[tok.id, rerun.id],
        cf_event_id=tok.id,
        cf_param_updates={"commit": f"{p['commit_hash'][:3]}aaa"},
        cf_answer="",
        invariance_event_id=cam.id,
        invariance_param_updates={"body_score": 0.01},
        gold_expression="tokenizer_commit after delayed rerun",
        proof_depth=2,
        cf_op="score",
        motif="delayed_effect",
        topology_id=instance_topology(
            "lab.tokenizer_then_rerun", paper, p["commit_hash"]
        ),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_del)

    q_con = QuerySpec(
        query_id=f"{qid}:contradiction",
        query_type="contradiction",
        question=(
            f"For {paper}, the camera-ready body still quotes an old number while "
            f"the authoritative channel quotes a later number. Reply exactly as "
            f"<body_score> -> <authoritative_score>."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_score",
        essential_event_ids=[cam.id, rel.id, rerun.id],
        essential_artifact_ids=[
            _aid(world, "camera_ready"),
            _aid(world, "release_note"),
            _aid(world, "rerun_json"),
        ],
        sufficient_event_ids=[cam.id, rel.id, rerun.id],
        cf_event_id=cam.id,
        cf_param_updates={"body_score": round(float(p["v1_score"]) - 5.13, 2)},
        cf_answer="",
        invariance_event_id=tok.id,
        invariance_param_updates={"commit": "beef00"},
        gold_expression="body_score -> authoritative_score",
        proof_depth=2,
        cf_op="score",
        motif="contradiction",
        topology_id=instance_topology(
            "lab.body_vs_release", p["v1_score"], p["final_score"]
        ),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_con)

    q_cf = QuerySpec(
        query_id=f"{qid}:counterfactual",
        query_type="counterfactual",
        question=(
            f"Suppose the {bench} test-set Issue for {paper} had never been filed. "
            f"What would the authoritative score be as of {as_of_now.isoformat()}? "
            f"Reply with the numeric score only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_score",
        essential_event_ids=[v1.id, rel.id],
        essential_artifact_ids=[_aid(world, "arxiv_v1"), _aid(world, "release_note")],
        sufficient_event_ids=[v1.id, rel.id],
        cf_event_id=v1.id,
        cf_param_updates={"score": round(float(p["v1_score"]) + 3.07, 2)},
        cf_answer="",
        invariance_event_id=tok.id,
        invariance_param_updates={"commit": "ffff00"},
        gold_expression="cf: issue never filed keeps v1",
        proof_depth=3,
        cf_op="score",
        motif="counterfactual_supersession",
        topology_id=instance_topology("lab.issue_not_filed", paper, p["v1_score"]),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
        question_overrides={
            issue.id: {"filed": False},
            rerun.id: {"aborted": True},
        },
    )
    queries.append(q_cf)

    q_hid = QuerySpec(
        query_id=f"{qid}:hidden_bridge",
        query_type="hidden_bridge",
        question=(
            f"Which SPDX identifier from the early {paper} LICENSE file becomes "
            f"the shipping license only when the July repository release is cut? "
            f"Reply with the SPDX token only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="ship_license",
        essential_event_ids=[lic.id, rel.id],
        essential_artifact_ids=[
            _aid(world, "license_note"),
            _aid(world, "release_note"),
        ],
        sufficient_event_ids=[lic.id, rel.id],
        cf_event_id=lic.id,
        cf_param_updates={"spdx": f"BSD-3-L{p['spdx'][-4:]}"},
        cf_answer="",
        invariance_event_id=tok.id,
        invariance_param_updates={"commit": "aa00aa"},
        gold_expression="ship_license copied at release from pending license",
        proof_depth=2,
        cf_op="version",
        motif="hidden_bridge",
        topology_id=instance_topology("lab.license_then_release", paper, p["spdx"]),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_hid)

    lineage_events = [
        v1,
        rev2,
        review1,
        response1,
        repro_fail,
        patch,
        review2,
        response2,
        repro_recovery,
        rev3,
        resolve,
    ]
    q_revision = QuerySpec(
        query_id=f"{qid}:revision_reproduction_resolution",
        query_type="revision_reproduction_resolution",
        question=(
            f"For {paper}, reconstruct the accepted scientific lineage after two "
            f"review rounds, a failed reproduction, a benchmark patch, and recovery. "
            f"Reply exactly as <revision>@<frozen_benchmark>@<successful_run> via "
            f"<controlling_response>. The meta decision and review resolution do not "
            f"reprint those identifiers."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="accepted_revision",
        essential_event_ids=[
            patch.id,
            response2.id,
            repro_recovery.id,
            rev3.id,
            resolve.id,
            meta.id,
        ],
        essential_artifact_ids=[
            _aid(world, "benchmark_patch"),
            _aid(world, "response_2"),
            _aid(world, "reproduction_recovery"),
            _aid(world, "revision_3"),
            _aid(world, "review_resolution"),
            _aid(world, "meta_decision"),
        ],
        sufficient_event_ids=[event.id for event in [*lineage_events, meta]],
        cf_event_id=rev3.id,
        cf_param_updates={"revision": f"R3-CF-{p['revision_v3'][-4:]}"},
        cf_answer="",
        invariance_event_id=repro_fail.id,
        invariance_param_updates={"run": f"RFAIL-ALT-{p['reproduction_failure'][-4:]}"},
        gold_expression=(
            "accepted_revision@accepted_benchmark@accepted_reproduction via "
            "accepted_review_response"
        ),
        proof_depth=12,
        cf_op="version",
        motif="revision_reproduction_resolution",
        topology_id=instance_topology(
            "lab.review_reproduction_acceptance",
            paper,
            p["revision_v3"],
            p["benchmark_v3"],
        ),
        domain="researchlab",
        truth_regime="synthetic_executable",
        program_ops=[
            {"op": "FOLLOW_REVIEW_SUPERSESSION"},
            {"op": "REQUIRE_REPRODUCTION_RECOVERY"},
            {"op": "JOIN_REVISION_BENCHMARK_RUN_RESPONSE"},
        ],
        preferred_length_buckets=["32k", "64k"],
        semantic_growth_group="researchlab_revision_reproduction",
    )
    queries.append(q_revision)

    q_response = QuerySpec(
        query_id=f"{qid}:review_response_trace",
        query_type="review_response_trace",
        question=(
            f"For {paper}, which second-round review requirement was resolved by "
            f"which author response and carried into which final revision? Follow "
            f"the failed reproduction and benchmark recovery between the two review "
            f"rounds. Reply exactly as <requirement> > <response> > <revision>. "
            f"The resolution memo does not reprint any identifier."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="resolved_revision",
        essential_event_ids=[review2.id, response2.id, rev3.id, resolve.id],
        essential_artifact_ids=[
            _aid(world, "review_2"),
            _aid(world, "response_2"),
            _aid(world, "revision_3"),
            _aid(world, "review_resolution"),
        ],
        sufficient_event_ids=[event.id for event in lineage_events],
        cf_event_id=response2.id,
        cf_param_updates={"response": f"RESP2-CF-{p['response_round2'][-4:]}"},
        cf_answer="",
        invariance_event_id=repro_fail.id,
        invariance_param_updates={"run": f"RFAIL-ALT-{p['reproduction_failure'][-4:]}"},
        gold_expression=(
            "resolved_review_requirement > resolved_review_response > resolved_revision"
        ),
        proof_depth=11,
        cf_op="version",
        motif="review_response_trace",
        topology_id=instance_topology(
            "lab.review_response_recovery",
            paper,
            p["review_round2"],
            p["revision_v3"],
        ),
        domain="researchlab",
        truth_regime="synthetic_executable",
        program_ops=[
            {"op": "FOLLOW_REVIEW_SUPERSESSION"},
            {"op": "FOLLOW_FAILED_THEN_RECOVERED_REPRODUCTION"},
            {"op": "JOIN_REQUIREMENT_RESPONSE_REVISION"},
        ],
        preferred_length_buckets=["32k", "64k"],
        semantic_growth_group="researchlab_review_response",
    )
    queries.append(q_response)

    q_benchmark_conflict = QuerySpec(
        query_id=f"{qid}:benchmark_revision_conflict",
        query_type="benchmark_revision_conflict",
        question=(
            f"For {paper}, trace the benchmark named by revision 2 to the frozen "
            "benchmark accepted after reproduction recovery, and identify the "
            "accepted manuscript revision. Reply exactly as "
            "<revision_2_benchmark> -> <accepted_benchmark> @ <accepted_revision>. "
            "The meta decision does not repeat these identifiers."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="accepted_benchmark",
        essential_event_ids=[
            rev2.id,
            patch.id,
            rev3.id,
            resolve.id,
            meta.id,
        ],
        essential_artifact_ids=[
            _aid(world, "revision_2"),
            _aid(world, "benchmark_patch"),
            _aid(world, "revision_3"),
            _aid(world, "review_resolution"),
            _aid(world, "meta_decision"),
        ],
        sufficient_event_ids=[event.id for event in [*lineage_events, meta]],
        cf_event_id=patch.id,
        cf_param_updates={"benchmark": f"BENCH-CF-{p['benchmark_v3'][-4:]}"},
        cf_answer="",
        invariance_event_id=repro_fail.id,
        invariance_param_updates={"run": f"RFAIL-ALT-{p['reproduction_failure'][-4:]}"},
        gold_expression=(
            "RESOLVE(benchmark_candidate, benchmark_frozen) then "
            "JOIN(accepted_revision)"
        ),
        proof_depth=12,
        cf_op="version",
        motif="benchmark_revision_conflict",
        topology_id=instance_topology(
            "lab.benchmark_revision_conflict",
            paper,
            p["benchmark_v2"],
            p["benchmark_v3"],
        ),
        domain="researchlab",
        truth_regime="synthetic_executable",
        program_ops=[
            {"op": "READ_REVISION_BENCHMARK"},
            {"op": "FOLLOW_BENCHMARK_SUPERSESSION"},
            {"op": "REQUIRE_REPRODUCTION_ACCEPTANCE"},
            {"op": "FORMAT_BENCHMARK_CONFLICT_REVISION"},
        ],
        preferred_length_buckets=["32k", "64k"],
        semantic_growth_group="researchlab_benchmark_revision_conflict",
    )
    queries.append(q_benchmark_conflict)

    experiment_workstreams = list(p.get("experiment_workstreams") or [])
    if experiment_workstreams:
        experiment_events = [
            _event(world, f"experiment_{workstream['id']}_{stage}")
            for workstream in experiment_workstreams
            for stage in ("revision", "review", "benchmark", "failure", "recovery")
        ]
        recovery_events = [
            _event(world, f"experiment_{workstream['id']}_recovery")
            for workstream in experiment_workstreams
        ]
        matrix_decision = _event(world, "experiment_matrix_decision")
        first_workstream = experiment_workstreams[0]
        first_recovery = recovery_events[0]
        first_failure = _event(world, f"experiment_{first_workstream['id']}_failure")
        lane_count = min(6, len(experiment_workstreams))
        max_cycles = (len(experiment_workstreams) + lane_count - 1) // lane_count
        q_matrix = QuerySpec(
            query_id=f"{qid}:experiment_matrix_resolution",
            query_type="experiment_matrix_resolution",
            question=(
                f"For {paper}, reconstruct the complete experiment matrix after every "
                f"declared workstream passes revision, review, benchmark freeze, an "
                f"observed failure, and a recovery. Preserve declared workstream order "
                f"across parallel lanes and reply as recovery run identifiers separated "
                f"by ` | `. The final matrix decision does not reprint any run identifier."
            ),
            answer="",
            as_of=as_of_now,
            answer_key="experiment_matrix",
            essential_event_ids=[
                *[event.id for event in recovery_events],
                matrix_decision.id,
            ],
            essential_artifact_ids=[
                *[
                    _aid(world, f"experiment_{workstream['id']}_recovery")
                    for workstream in experiment_workstreams
                ],
                _aid(world, "experiment_matrix_decision"),
            ],
            sufficient_event_ids=[
                *[event.id for event in experiment_events],
                matrix_decision.id,
            ],
            cf_event_id=first_recovery.id,
            cf_param_updates={"run": f"XP-CF-{str(first_workstream['recovery'])[-4:]}"},
            cf_answer="",
            invariance_event_id=first_failure.id,
            invariance_param_updates={
                "failure": f"XF-ALT-{str(first_workstream['failure'])[-4:]}"
            },
            gold_expression=(
                "ordered join of every experiment:<workstream>:recovery after "
                "experiment_matrix_decision"
            ),
            proof_depth=5 * max_cycles + 1,
            cf_op="version",
            motif="parallel_experiment_matrix",
            topology_id=instance_topology(
                "lab.parallel_experiment_matrix",
                paper,
                len(experiment_workstreams),
                lane_count,
            ),
            domain="researchlab",
            truth_regime="synthetic_executable",
            program_ops=[
                {
                    "op": "FOLLOW_PARALLEL_FAILURE_RECOVERY_CHAINS",
                    "workstreams": len(experiment_workstreams),
                    "lanes": lane_count,
                },
                {
                    "op": "REQUIRE_ALL_EXPERIMENT_RECOVERIES",
                    "workstreams": len(experiment_workstreams),
                },
                {"op": "ORDER_BY_DECLARED_WORKSTREAM"},
                {"op": "JOIN_RECOVERY_RUNS"},
            ],
            preferred_length_buckets=["16k", "32k", "64k"],
            semantic_growth_group="researchlab_parallel_experiment_matrix",
        )
        queries.append(q_matrix)

    inv = find_event(world, "invalidate_run")
    if inv is not None:
        dset = str(p.get("dataset_v2") or "DSET-v2")
        q_ex = QuerySpec(
            query_id=f"{qid}:exclusion",
            query_type="exclusion",
            question=(
                f"After dataset card {dset} for {paper}, which previously reported "
                f"{bench} score must be withdrawn? The card does not reprint the "
                f"numeral. Reply with the numeric score only."
            ),
            answer="",
            as_of=world_as_of(world),
            answer_key="withdrawn_score",
            essential_event_ids=[v1.id, inv.id],
            essential_artifact_ids=[
                _aid(world, "arxiv_v1"),
                _aid(world, "dataset_card"),
            ],
            sufficient_event_ids=[v1.id, inv.id],
            cf_event_id=v1.id,
            cf_param_updates={"score": round(float(p["v1_score"]) + 4.09, 2)},
            cf_answer="",
            invariance_event_id=tok.id,
            invariance_param_updates={"commit": "cc00cc"},
            gold_expression="withdrawn_score copies v1 after dataset v2",
            proof_depth=2,
            cf_op="score",
            motif="exclusion",
            topology_id=instance_topology("lab.withdraw_v1_after_v2", paper, dset),
            domain="researchlab",
            truth_regime="real_schema_synthetic_instance",
        )
        queries.append(q_ex)

        q_xs = QuerySpec(
            query_id=f"{qid}:cross_stream",
            query_type="cross_stream",
            question=(
                f"After dataset v2 withdrawal for {paper}, which SPDX identifier "
                f"was in force? Reconstruct from LICENSE plus the dataset card. "
                f"Reply SPDX only."
            ),
            answer="",
            as_of=world_as_of(world),
            answer_key="score_license_at_withdraw",
            essential_event_ids=[lic.id, inv.id],
            essential_artifact_ids=[
                _aid(world, "license_note"),
                _aid(world, "dataset_card"),
            ],
            sufficient_event_ids=[lic.id, inv.id],
            cf_event_id=lic.id,
            cf_param_updates={"spdx": f"BSD-3-L{p['spdx'][-4:]}"},
            cf_answer="",
            invariance_event_id=tok.id,
            invariance_param_updates={"commit": "dd00dd"},
            gold_expression="score_license_at_withdraw copied at invalidate from pending license",
            proof_depth=2,
            cf_op="version",
            motif="cross_workstream",
            topology_id=instance_topology(
                "lab.withdraw_under_license", paper, p["spdx"]
            ),
            domain="researchlab",
            truth_regime="real_schema_synthetic_instance",
        )
        queries.append(q_xs)

    from longworld.core.cascade import (
        build_docket_control_query,
        build_ratification_query,
        build_revisitation_query,
    )
    from longworld.core.grounded import (
        build_content_grounded_query,
        build_source_choice_query,
        build_source_grounded_query,
    )

    q_g = build_source_grounded_query(
        world,
        tok.id,
        {"commit": "ee00ee"},
    )
    if q_g is not None:
        queries.append(q_g)
    q_content = build_content_grounded_query(
        world,
        tok.id,
        {"commit": "ee00ee"},
    )
    if q_content is not None:
        queries.append(q_content)
    q_choice = build_source_choice_query(world, tok.id, {"commit": "ee00ee"})
    if q_choice is not None:
        queries.append(q_choice)

    q_rev = build_revisitation_query(world, tok.id, {"commit": "ee00ee"})
    if q_rev is not None:
        queries.append(q_rev)
    q_rat = build_ratification_query(world, tok.id, {"commit": "ee00ee"})
    if q_rat is not None:
        queries.append(q_rat)
    q_dock = build_docket_control_query(world, tok.id, {"commit": "ee00ee"})
    if q_dock is not None:
        queries.append(q_dock)

    out: list[QuerySpec] = []
    for q in queries:
        if q.truth_regime == "real_schema_synthetic_instance":
            q.truth_regime = "synthetic_executable"
        q.answer = gold_from_full(world, q)
        q.cf_answer = cf_from_full(world, q)
        out.append(q)
    return out
