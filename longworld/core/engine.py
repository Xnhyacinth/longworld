from __future__ import annotations

import hashlib
from typing import Any

from longworld.core.domain import eval_answer, handlers
from longworld.core.issuerfilingworkflow import ISSUER_IR_SECTIONS_64K
from longworld.core.world import SimulatedWorld, WorldSimulator
from longworld.domains.codeforge.schema import materialize_grounded_repo_record
from longworld.domains.codeforge.simulate import canonical_repo_record_envelopes
from longworld.domains.company.queries import QuerySpec, _merge_overrides
from longworld.domains.company.simulate import (
    canonical_issuer_ir_source_section_envelopes,
    canonical_sec_source_section_envelope,
)
from longworld.domains.researchlab.simulate import (
    canonical_researchlab_source_event_envelope,
    canonical_researchlab_source_visible_text,
)


def simulator(world: SimulatedWorld) -> WorldSimulator:
    check_preconditions, apply_event = handlers(world)
    return WorldSimulator(
        spec=world.spec,
        init_values=world.init_values,
        check_preconditions=check_preconditions,
        apply_event=apply_event,
    )


def revealed_event_ids(
    artifacts: list[Any], world: SimulatedWorld | None = None
) -> list[str]:
    """Collect event ids revealed by artifacts that belong to ``world``.

    Extra-world filler reuses local names like ``focal.change_roadmap``.
    Replaying those ids on the query world would fake a shorter proof.
    """
    wid = ""
    if world is not None:
        wid = str(world.spec.get("world_id") or "")
    ids: list[str] = []
    seen: set[str] = set()
    for art in artifacts:
        if wid and not str(getattr(art, "artifact_id", "")).startswith(wid):
            continue
        for eid in art.reveals_events:
            if eid not in seen:
                seen.add(eid)
                ids.append(eid)
    return ids


def answer_from_events(
    world: SimulatedWorld,
    spec: QuerySpec,
    event_ids: list[str] | set[str],
    extra_overrides: dict[str, dict[str, Any]] | None = None,
    skip_ids: set[str] | None = None,
    enforce_preconditions: bool = False,
) -> str:
    allowed = set(event_ids)
    cached = getattr(world, "_longworld_event_index", None)
    if cached is None or cached[0] is not world.events:
        cached = (world.events, {event.id: event for event in world.events})
        world._longworld_event_index = cached
    event_index = cached[1]
    events = [event_index[event_id] for event_id in allowed if event_id in event_index]
    st = simulator(world).replay_events(
        events,
        up_to=spec.as_of,
        skip_ids=skip_ids,
        param_overrides=_merge_overrides(spec, extra_overrides),
        enforce_preconditions=enforce_preconditions,
    )
    return eval_answer(world, spec, st.values)


def answer_from_artifacts(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Any],
    extra_overrides: dict[str, dict[str, Any]] | None = None,
    skip_ids: set[str] | None = None,
    enforce_preconditions: bool = False,
) -> str:
    return answer_from_events(
        world,
        spec,
        revealed_event_ids(artifacts, world),
        extra_overrides,
        skip_ids,
        enforce_preconditions,
    )


def _sec_raw_lineage_valid(world: SimulatedWorld, event: Any) -> bool:
    """Regenerate one SEC section event from the trusted source workflow."""

    params = getattr(event, "params", None)
    if (
        not isinstance(params, dict)
        or getattr(event, "type", "") != "sec_source_section"
    ):
        return False
    workflow_id = str(params.get("workflow_id") or "")
    record_id = str(params.get("record_id") or "")
    project = world.spec.get("project")
    if not isinstance(project, dict):
        return False
    source_workflows = project.get("source_workflows", ())
    matches = [
        (workflow_index, record_index, workflow, record)
        for workflow_index, workflow in enumerate(source_workflows)
        if getattr(workflow, "workflow_id", "") == workflow_id
        for record_index, record in enumerate(getattr(workflow, "records", ()))
        if getattr(record, "record_id", "") == record_id
    ]
    if len(matches) != 1:
        return False
    workflow_index, record_index, workflow, record = matches[0]
    section_id = str(params.get("section_id") or "")
    prefix = str(world.spec.get("prefix") or "")
    expected_event_id = (
        f"{prefix}.sec_section_{section_id}_{workflow_index}_{record_index}"
    )
    if event.id != expected_event_id or list(event.visibility) != [expected_event_id]:
        return False
    canonical = canonical_sec_source_section_envelope(
        workflow=workflow,
        record=record,
        section_id=section_id,
    )
    return canonical is not None and canonical == {
        "type": event.type,
        "time": event.time,
        "params": event.params,
        "preconditions": list(event.preconditions),
        "causal_inputs": list(event.causal_inputs),
        "required_inputs": list(event.required_inputs),
        "relation_kinds": dict(event.relation_kinds),
        "skipped": event.skipped,
        "skip_reason": event.skip_reason,
    }


def _event_envelope(event: Any) -> dict[str, Any]:
    return {
        "type": event.type,
        "time": event.time,
        "params": event.params,
        "preconditions": list(event.preconditions),
        "causal_inputs": list(event.causal_inputs),
        "required_inputs": list(event.required_inputs),
        "relation_kinds": dict(event.relation_kinds),
        "skipped": event.skipped,
        "skip_reason": event.skip_reason,
    }


def _repo_record_lineage_valid(world: SimulatedWorld, event: Any) -> bool:
    params = getattr(event, "params", None)
    if not isinstance(params, dict) or getattr(event, "type", "") != "repo_record":
        return False
    cached = getattr(world, "_longworld_canonical_repo_records", None)
    if cached is None:
        cached = canonical_repo_record_envelopes(world)
        world._longworld_canonical_repo_records = cached
    canonical = cached.get(str(params.get("record_key") or ""))
    return canonical is not None and _event_envelope(canonical) == _event_envelope(
        event
    )


def _issuer_ir_raw_lineage_valid(world: SimulatedWorld, event: Any) -> bool:
    params = getattr(event, "params", None)
    if (
        not isinstance(params, dict)
        or getattr(event, "type", "") != "issuer_ir_source_section"
    ):
        return False
    workflow_id = str(params.get("workflow_id") or "")
    record_id = str(params.get("record_id") or "")
    project = world.spec.get("project")
    if not isinstance(project, dict):
        return False
    matches = [
        (workflow_index, record_index, workflow, record)
        for workflow_index, workflow in enumerate(project.get("source_workflows", ()))
        if getattr(workflow, "workflow_id", "") == workflow_id
        for record_index, record in enumerate(getattr(workflow, "records", ()))
        if getattr(record, "record_id", "") == record_id
    ]
    if len(matches) != 1:
        return False
    workflow_index, record_index, workflow, record = matches[0]
    section_name = str(params.get("section_name") or "")
    try:
        section_index = ISSUER_IR_SECTIONS_64K.index(section_name)
    except ValueError:
        return False
    prefix = str(world.spec.get("prefix") or "")
    expected_event_id = (
        f"{prefix}.issuer_ir_section_{section_index}_{workflow_index}_{record_index}"
    )
    if event.id != expected_event_id or list(event.visibility) != [expected_event_id]:
        return False
    cache_key = (
        str(getattr(workflow, "component_digest", "")),
        record_id,
        str(getattr(record, "source_sha256", "")),
        str(getattr(record, "text_sha256", "")),
    )
    cache = getattr(world, "_longworld_canonical_issuer_sections", None)
    if cache is None:
        cache = {}
        world._longworld_canonical_issuer_sections = cache
    if cache_key not in cache:
        cache[cache_key] = canonical_issuer_ir_source_section_envelopes(
            workflow=workflow, record=record
        )
    canonical = cache[cache_key].get(section_name)
    return canonical is not None and canonical == _event_envelope(event)


def _research_source_envelope(
    world: SimulatedWorld, event: Any
) -> dict[str, Any] | None:
    params = getattr(event, "params", None)
    if not isinstance(params, dict):
        return None
    canonical = canonical_researchlab_source_event_envelope(
        world.spec,
        event_id=str(getattr(event, "id", "") or ""),
        event_type=str(getattr(event, "type", "") or ""),
        record_id=str(params.get("record_id") or ""),
        relation_id=str(params.get("relation_id") or ""),
        section_id=str(params.get("section_id") or ""),
    )
    if canonical is None:
        return None
    observed = {
        "id": event.id,
        **_event_envelope(event),
        "visibility": list(event.visibility),
    }
    return canonical if canonical == observed else None


def _semantic_artifact_overrides(
    artifacts: list[Any],
    world: SimulatedWorld,
    authorized_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Bind source replay to immutable params or an explicit CF update."""
    world_id = str(world.spec.get("world_id") or "")
    event_index = {event.id: event for event in world.events}
    overrides: dict[str, dict[str, Any]] = {}
    invalid_event_ids: set[str] = set()
    for artifact in artifacts:
        if world_id and not str(getattr(artifact, "artifact_id", "")).startswith(
            world_id
        ):
            continue
        slots = getattr(artifact, "slots", {}) or {}
        event_ids = list(getattr(artifact, "reveals_events", ()) or ())
        params = slots.get("params")
        source_events = [
            event_index[event_id]
            for event_id in event_ids
            if event_id in event_index
            and event_index[event_id].type
            in {
                "arxiv_revision",
                "arxiv_revision_relation",
                "repo_record",
                "sec_filing",
                "sec_source_section",
                "issuer_ir_source_section",
                "wiki_source_relation",
                "wiki_source_section",
            }
        ]
        if not source_events:
            continue
        artifact_text = str(getattr(artifact, "text", "") or "")
        for event in source_events:
            event_id = event.id
            expected = {
                **event.params,
                **(authorized_overrides or {}).get(event_id, {}),
            }
            if event.type == "repo_record" and event_id in (authorized_overrides or {}):
                expected.update(materialize_grounded_repo_record(expected))
            research_envelope = (
                _research_source_envelope(world, event)
                if event.type
                in {
                    "arxiv_revision",
                    "arxiv_revision_relation",
                    "wiki_source_relation",
                    "wiki_source_section",
                }
                else None
            )
            if research_envelope is not None:
                expected_envelope = {**research_envelope, "params": expected}
                declared_text = canonical_researchlab_source_visible_text(
                    expected_envelope
                )
                visible_text = artifact_text
                expected_text = str(declared_text or "")
            else:
                text_field = "body_text" if event.type == "repo_record" else "text"
                declared_text = (
                    str(params.get(text_field) or "")
                    if isinstance(params, dict)
                    else ""
                )
                visible_text = (
                    artifact_text[
                        artifact_text.index(declared_text) : artifact_text.index(
                            declared_text
                        )
                        + len(declared_text)
                    ]
                    if declared_text and artifact_text.count(declared_text) == 1
                    else artifact_text
                )
                expected_text = str(expected.get(text_field) or "")
            lineage_valid = (
                len(event_ids) == 1
                and isinstance(params, dict)
                and slots.get("event_type") == event.type
                and params == expected
                and visible_text == expected_text
                and len(event.visibility) == 1
                and str(getattr(artifact, "artifact_id", ""))
                == f"{world_id}.{event.visibility[0]}"
                and getattr(artifact, "time", None) == event.time
            )
            if event.type == "sec_source_section":
                prefix = "SEC source section\n"
                raw_section = (
                    visible_text[len(prefix) :]
                    if visible_text.startswith(prefix)
                    else ""
                )
                lineage_valid = (
                    lineage_valid
                    and hashlib.sha256(raw_section.encode()).hexdigest()
                    == expected.get("section_sha256")
                    and _sec_raw_lineage_valid(world, event)
                )
            elif event.type == "issuer_ir_source_section":
                prefix = "Issuer IR rendered XBRL statement\n"
                raw_section = (
                    visible_text[len(prefix) :]
                    if visible_text.startswith(prefix)
                    else ""
                )
                lineage_valid = (
                    lineage_valid
                    and hashlib.sha256(raw_section.encode()).hexdigest()
                    == expected.get("section_sha256")
                    and _issuer_ir_raw_lineage_valid(world, event)
                )
            elif event.type == "repo_record":
                lineage_valid = lineage_valid and _repo_record_lineage_valid(
                    world, event
                )
            elif event.type in {
                "arxiv_revision",
                "arxiv_revision_relation",
                "wiki_source_relation",
                "wiki_source_section",
            }:
                lineage_valid = lineage_valid and research_envelope is not None
            if lineage_valid and event_id not in invalid_event_ids:
                overrides[event_id] = dict(params) if isinstance(params, dict) else {}
            elif not lineage_valid:
                invalid_event_ids.add(event_id)
                overrides[event_id] = {
                    **expected,
                    "text": visible_text,
                    "text_sha256": hashlib.sha256(visible_text.encode()).hexdigest(),
                    "fact_spans": [],
                }
    return overrides, invalid_event_ids


def semantic_answer_from_artifacts(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Any],
    extra_overrides: dict[str, dict[str, Any]] | None = None,
    skip_ids: set[str] | None = None,
    enforce_preconditions: bool = False,
) -> str:
    """Replay source-bearing events from visible bytes, not hidden world params."""
    merged = {
        event_id: dict(values) for event_id, values in (extra_overrides or {}).items()
    }
    semantic_overrides, invalid_event_ids = _semantic_artifact_overrides(
        artifacts, world, extra_overrides
    )
    for event_id, values in semantic_overrides.items():
        merged[event_id] = {**merged.get(event_id, {}), **values}
    blocked_ids = set(skip_ids or ()) | invalid_event_ids
    return answer_from_events(
        world,
        spec,
        revealed_event_ids(artifacts, world),
        merged,
        blocked_ids,
        enforce_preconditions,
    )
