from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any

from longworld.core.cascade import cascade_events
from longworld.core.grounded import grounded_events
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.researchlab.events import (
    apply_event,
    check_preconditions,
    init_values,
)


def _date(start: date, months: int, extra_days: int = 0) -> date:
    # Approximate month steps without dateutil.
    return start + timedelta(days=30 * months + extra_days)


def _semantic_arxiv_body(record: Any) -> tuple[str, str, list[str]]:
    """Preserve semantic LaTeX while excluding non-body preamble formatting."""
    try:
        payload = json.loads(record.text)
    except json.JSONDecodeError as error:
        raise ValueError("arXiv source record is not canonical JSON") from error
    raw_sources = payload.get("latex_sources") if isinstance(payload, dict) else None
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("arXiv source record has no LaTeX body")
    selected: list[str] = []
    paths: set[str] = set()
    for source in raw_sources:
        if not isinstance(source, dict) or set(source) not in (
            {"path", "text"},
            {"path", "text", "sha256"},
        ):
            raise ValueError("arXiv LaTeX source entry is invalid")
        path = str(source["path"])
        text = str(source["text"])
        if not path or path in paths or not text:
            raise ValueError("arXiv LaTeX source entry is invalid")
        paths.add(path)
        if path.rsplit("/", 1)[-1] == "preamble.tex":
            continue
        selected.append(text)
    if not selected:
        raise ValueError("arXiv semantic LaTeX body is empty")
    revision_id = record.attribute("revision_id")
    if not revision_id:
        raise ValueError("arXiv source record has no revision identity")
    body = (
        f"% arXiv manuscript revision {revision_id}\n"
        f"% arXiv submitted_at {record.occurred_at}\n" + "\n\n".join(selected)
    )
    text_sha256 = hashlib.sha256(body.encode()).hexdigest()
    excluded_paths = sorted(
        path for path in paths if path.rsplit("/", 1)[-1] == "preamble.tex"
    )
    provenance = hashlib.sha256(
        json.dumps(
            {
                "operation": "arxiv_semantic_latex_body_v1",
                "parent_provenance_id": record.provenance_id,
                "excluded_paths": excluded_paths,
                "text_sha256": text_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return body, f"derived-sha256:{provenance}", excluded_paths


def _source_workflow_events(project: dict[str, Any], prefix: str) -> list[Event]:
    events: list[Event] = []
    for workflow_index, workflow in enumerate(project.get("source_workflows") or []):
        record_event_ids: dict[str, str] = {}
        record_bodies: dict[str, str] = {}
        records = {record.record_id: record for record in workflow.records}
        for record_index, record in enumerate(workflow.records):
            body, provenance_id, excluded_paths = _semantic_arxiv_body(record)
            event_id = f"{prefix}.arxiv_revision_{workflow_index}_{record_index}"
            record_event_ids[record.record_id] = event_id
            record_bodies[record.record_id] = body
            events.append(
                Event(
                    id=event_id,
                    type="arxiv_revision",
                    time=date.fromisoformat(record.occurred_at[:10]),
                    params={
                        "workflow_id": workflow.workflow_id,
                        "record_id": record.record_id,
                        "revision_id": record.attribute("revision_id"),
                        "occurred_at": record.occurred_at,
                        "text": body,
                        "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
                        "source_sha256": record.source_sha256,
                        "parent_provenance_id": record.provenance_id,
                        "provenance_id": provenance_id,
                        "provenance_operation": "arxiv_semantic_latex_body_v1",
                        "excluded_paths": excluded_paths,
                        "source_origin": "real_derived",
                        "source_family": record.source_family,
                        "source_url": record.source_url,
                        "retrieval_url": record.retrieval_url,
                        "ground_values": [
                            record.occurred_at[:10],
                            *(
                                fact.value
                                for fact in record.facts
                                if fact.value in body
                            ),
                        ],
                    },
                    visibility=[
                        f"{prefix}.arxiv_revision_{workflow_index}_{record_index}"
                    ],
                )
            )
        for relation_index, relation in enumerate(workflow.relations):
            if relation.kind != "revision_of":
                continue
            source = records[relation.source_record_id]
            selected_facts = [
                fact for fact in source.facts if fact.field == "revision_added_text"
            ]
            if len(selected_facts) != 1 or len(relation.evidence) != 1:
                continue
            fact = selected_facts[0]
            source_body = record_bodies[relation.source_record_id]
            if source_body.count(fact.value) != 1:
                raise ValueError("revision-added fact is not unique in semantic body")
            fact_start = source_body.index(fact.value)
            evidence = relation.evidence[0]
            relation_event_id = (
                f"{prefix}.arxiv_revision_relation_{workflow_index}_{relation_index}"
            )
            decision_event_id = (
                f"{prefix}.arxiv_revision_decision_{workflow_index}_{relation_index}"
            )
            source_event_id = record_event_ids[relation.source_record_id]
            target_event_id = record_event_ids[relation.target_record_id]
            events.extend(
                [
                    Event(
                        id=relation_event_id,
                        type="arxiv_revision_relation",
                        time=date.fromisoformat(source.occurred_at[:10]),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "work_id": source.attribute("work_id"),
                            "relation_id": relation.relation_id,
                            "relation_kind": relation.kind,
                            "source_record_id": relation.source_record_id,
                            "target_record_id": relation.target_record_id,
                            "source_revision_id": source.attribute("revision_id"),
                            "target_revision_id": records[
                                relation.target_record_id
                            ].attribute("revision_id"),
                            "source_record_event_id": source_event_id,
                            "target_record_event_id": target_event_id,
                            "source_url": source.source_url,
                            "target_source_url": records[
                                relation.target_record_id
                            ].source_url,
                            "source_family": source.source_family,
                            "evidence_quote": evidence.evidence_quote,
                            "evidence_char_start": evidence.char_start,
                            "fact_id": fact.fact_id,
                            "fact_char_start": fact_start,
                            "fact_char_end": fact_start + len(fact.value),
                            "fact_value_offset": 0,
                            "fact_value_length": len(fact.value),
                        },
                        visibility=[
                            (
                                f"{prefix}.arxiv_revision_relation_"
                                f"{workflow_index}_{relation_index}"
                            )
                        ],
                        causal_inputs=[target_event_id, source_event_id],
                        required_inputs=[target_event_id, source_event_id],
                        relation_kinds={
                            target_event_id: "revision_of",
                            source_event_id: "derived_from",
                        },
                    ),
                    Event(
                        id=decision_event_id,
                        type="arxiv_revision_decision",
                        time=date.fromisoformat(source.occurred_at[:10])
                        + timedelta(days=1),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "relation_id": relation.relation_id,
                            "relation_event_id": relation_event_id,
                            "source_record_event_id": source_event_id,
                            "target_record_event_id": target_event_id,
                        },
                        visibility=[
                            (
                                f"{prefix}.arxiv_revision_decision_"
                                f"{workflow_index}_{relation_index}"
                            )
                        ],
                        causal_inputs=[relation_event_id],
                        required_inputs=[relation_event_id],
                        relation_kinds={relation_event_id: "enables"},
                    ),
                ]
            )
    return events


def _experiment_events(
    project: dict[str, Any], prefix: str, start: date
) -> list[Event]:
    workstreams = list(project.get("experiment_workstreams") or [])
    if not workstreams:
        return []

    def event_id(workstream_id: str, stage: str) -> str:
        return f"{prefix}.experiment_{workstream_id}_{stage}"

    events: list[Event] = []
    lane_count = min(6, len(workstreams))
    for workstream in workstreams:
        workstream_id = str(workstream["id"])
        lane = int(workstream["lane"])
        cycle = int(workstream["cycle"])
        base_month = 21 + (cycle - 1) * 6
        day_offset = 2 * (lane - 1)
        upstream_id = workstream.get("upstream_id")
        upstream_recovery = (
            event_id(str(upstream_id), "recovery") if upstream_id else None
        )
        revision_inputs = [upstream_recovery] if upstream_recovery else []
        revision_relations = (
            {upstream_recovery: "derived_from"} if upstream_recovery else {}
        )
        shared = {
            "workstream": workstream_id,
            "lane": lane,
            "cycle": cycle,
            "upstream_id": upstream_id,
            "objective": workstream["objective"],
            "dataset_slice": workstream["dataset_slice"],
            "metric": workstream["metric"],
            "environment": workstream["environment"],
            "failure_mode": workstream["failure_mode"],
            "recovery_action": workstream["recovery_action"],
        }
        revision_id = event_id(workstream_id, "revision")
        review_id = event_id(workstream_id, "review")
        benchmark_id = event_id(workstream_id, "benchmark")
        failure_id = event_id(workstream_id, "failure")
        recovery_id = event_id(workstream_id, "recovery")
        events.extend(
            [
                Event(
                    id=revision_id,
                    type="experiment_revision",
                    time=_date(start, base_month, day_offset),
                    params={**shared, "revision": workstream["revision"]},
                    visibility=[f"{prefix}.experiment_{workstream_id}_revision"],
                    causal_inputs=revision_inputs,
                    required_inputs=revision_inputs,
                    relation_kinds=revision_relations,
                ),
                Event(
                    id=review_id,
                    type="experiment_review",
                    time=_date(start, base_month + 1, day_offset),
                    params={
                        **shared,
                        "review": workstream["review"],
                        "revision": workstream["revision"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_review"],
                    causal_inputs=[revision_id],
                    required_inputs=[revision_id],
                    relation_kinds={revision_id: "derived_from"},
                ),
                Event(
                    id=benchmark_id,
                    type="experiment_benchmark",
                    time=_date(start, base_month + 2, day_offset),
                    params={
                        **shared,
                        "benchmark": workstream["benchmark"],
                        "review": workstream["review"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_benchmark"],
                    causal_inputs=[review_id],
                    required_inputs=[review_id],
                    relation_kinds={review_id: "enables"},
                ),
                Event(
                    id=failure_id,
                    type="experiment_failure",
                    time=_date(start, base_month + 3, day_offset),
                    params={
                        **shared,
                        "failure": workstream["failure"],
                        "benchmark": workstream["benchmark"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_failure"],
                    causal_inputs=[benchmark_id],
                    required_inputs=[benchmark_id],
                    relation_kinds={benchmark_id: "contradicts"},
                ),
                Event(
                    id=recovery_id,
                    type="experiment_recovery",
                    time=_date(start, base_month + 4, day_offset),
                    params={
                        **shared,
                        "run": workstream["recovery"],
                        "failure": workstream["failure"],
                        "benchmark": workstream["benchmark"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_recovery"],
                    causal_inputs=[failure_id, benchmark_id],
                    required_inputs=[failure_id],
                    relation_kinds={
                        failure_id: "supersedes",
                        benchmark_id: "derived_from",
                    },
                ),
            ]
        )

    recovery_ids = [
        event_id(str(workstream["id"]), "recovery") for workstream in workstreams
    ]
    last_cycle = max(int(workstream["cycle"]) for workstream in workstreams)
    events.append(
        Event(
            id=f"{prefix}.experiment_matrix_decision",
            type="experiment_matrix_decision",
            time=_date(start, 21 + (last_cycle - 1) * 6 + 5, 15),
            params={
                "workstream_ids": [str(workstream["id"]) for workstream in workstreams],
                "lane_count": lane_count,
            },
            visibility=[f"{prefix}.experiment_matrix_decision"],
            causal_inputs=recovery_ids,
            required_inputs=recovery_ids,
            relation_kinds={event_id_: "enables" for event_id_ in recovery_ids},
        )
    )
    return events


def events_for_lab(project: dict[str, Any], prefix: str) -> list[Event]:
    start = date.fromisoformat(project["start"])
    pid = project["paper"].lower()
    v1 = project["v1_score"]
    final = project["final_score"]
    cause = project["cause_token"]
    cache_tok, split_tok = cause.split("+", 1)
    adopt = bool(project.get("is_focal", False))

    def eid(kind: str) -> str:
        return f"{prefix}.{kind}"

    evs: list[Event] = [
        Event(
            id=eid("license_clause"),
            type="license_clause",
            time=_date(start, 0, 1),
            params={"spdx": project["spdx"]},
            visibility=[f"{prefix}.license_note"],
            causal_inputs=[],
        ),
        Event(
            id=eid("report_v1"),
            type="report_v1",
            time=_date(start, 0, 2),
            params={"score": v1},
            visibility=[f"{prefix}.arxiv_v1"],
            causal_inputs=[],
        ),
        Event(
            id=eid("commit_tokenizer"),
            type="commit_tokenizer",
            time=_date(start, 1, 4),
            params={"commit": project["commit_hash"]},
            visibility=[f"{prefix}.git_commit"],
            causal_inputs=[eid("report_v1")],
            relation_kinds={eid("report_v1"): "enables"},
        ),
        Event(
            id=eid("log_stale_cache"),
            type="log_stale_cache",
            time=_date(start, 2, 6),
            params={"cause_cache": cache_tok},
            visibility=[f"{prefix}.eval_log"],
            causal_inputs=[eid("commit_tokenizer")],
            relation_kinds={eid("commit_tokenizer"): "derived_from"},
        ),
        Event(
            id=eid("issue_testset"),
            type="issue_testset",
            time=_date(start, 3, 3),
            params={
                "filed": True,
                "cause_cache": cache_tok,
                "cause_split": split_tok,
            },
            visibility=[f"{prefix}.github_issue"],
            causal_inputs=[eid("report_v1")],
            relation_kinds={eid("report_v1"): "contradicts"},
        ),
        Event(
            id=eid("fix_rerun"),
            type="fix_rerun",
            time=_date(start, 4, 8),
            params={"score": final},
            visibility=[f"{prefix}.rerun_json"],
            preconditions=["issue_filed"],
            causal_inputs=[eid("issue_testset"), eid("log_stale_cache")],
            relation_kinds={
                eid("issue_testset"): "enables",
                eid("log_stale_cache"): "derived_from",
            },
        ),
        Event(
            id=eid("camera_ready"),
            type="camera_ready",
            time=_date(start, 5, 5),
            params={"body_score": round(float(v1) + 0.01, 2)},
            visibility=[f"{prefix}.camera_ready"],
            causal_inputs=[eid("fix_rerun"), eid("report_v1")],
            relation_kinds={
                eid("fix_rerun"): "supersedes",
                eid("report_v1"): "contradicts",
            },
        ),
        Event(
            id=eid("release_note"),
            type="release_note",
            time=_date(start, 6, 2),
            params={"adopt_rerun": adopt},
            visibility=[f"{prefix}.release_note"],
            causal_inputs=[
                eid("fix_rerun"),
                eid("camera_ready"),
                eid("license_clause"),
            ],
            relation_kinds={
                eid("fix_rerun"): "derived_from",
                eid("camera_ready"): "supersedes",
                eid("license_clause"): "enables",
            },
        ),
    ]
    if project.get("process", {}).get("invalidate"):
        evs.append(
            Event(
                id=eid("invalidate_run"),
                type="invalidate_run",
                time=_date(start, 7, 3),
                params={"dataset": project["dataset_v2"]},
                visibility=[f"{prefix}.dataset_card"],
                causal_inputs=[eid("report_v1")],
                relation_kinds={eid("report_v1"): "supersedes"},
            )
        )
    evs.extend(
        [
            Event(
                id=eid("submit_revision_2"),
                type="submit_revision_2",
                time=_date(start, 8, 5),
                params={
                    "revision": project["revision_v2"],
                    "benchmark": project["benchmark_v2"],
                },
                visibility=[f"{prefix}.revision_2"],
                causal_inputs=[eid("camera_ready"), eid("fix_rerun")],
                relation_kinds={
                    eid("camera_ready"): "supersedes",
                    eid("fix_rerun"): "derived_from",
                },
            ),
            Event(
                id=eid("review_round_1"),
                type="review_round_1",
                time=_date(start, 9, 8),
                params={"requirement": project["review_round1"]},
                visibility=[f"{prefix}.review_1"],
                causal_inputs=[eid("submit_revision_2")],
                required_inputs=[eid("submit_revision_2")],
                relation_kinds={eid("submit_revision_2"): "derived_from"},
            ),
            Event(
                id=eid("respond_round_1"),
                type="respond_round_1",
                time=_date(start, 10, 6),
                params={"response": project["response_round1"]},
                visibility=[f"{prefix}.response_1"],
                causal_inputs=[eid("review_round_1")],
                required_inputs=[eid("review_round_1")],
                relation_kinds={eid("review_round_1"): "derived_from"},
            ),
            Event(
                id=eid("reproduction_failure"),
                type="reproduction_failure",
                time=_date(start, 11, 9),
                params={
                    "run": project["reproduction_failure"],
                    "benchmark": project["benchmark_v2"],
                },
                visibility=[f"{prefix}.reproduction_failure"],
                causal_inputs=[eid("respond_round_1")],
                required_inputs=[eid("respond_round_1")],
                relation_kinds={eid("respond_round_1"): "contradicts"},
            ),
            Event(
                id=eid("benchmark_patch"),
                type="benchmark_patch",
                time=_date(start, 13, 4),
                params={
                    "benchmark": project["benchmark_v3"],
                    "commit": project["benchmark_patch_commit"],
                },
                visibility=[f"{prefix}.benchmark_patch"],
                causal_inputs=[eid("reproduction_failure")],
                required_inputs=[eid("reproduction_failure")],
                relation_kinds={eid("reproduction_failure"): "supersedes"},
            ),
            Event(
                id=eid("review_round_2"),
                type="review_round_2",
                time=_date(start, 14, 7),
                params={"requirement": project["review_round2"]},
                visibility=[f"{prefix}.review_2"],
                causal_inputs=[eid("review_round_1"), eid("benchmark_patch")],
                required_inputs=[eid("benchmark_patch")],
                relation_kinds={
                    eid("review_round_1"): "supersedes",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
            Event(
                id=eid("respond_round_2"),
                type="respond_round_2",
                time=_date(start, 15, 9),
                params={"response": project["response_round2"]},
                visibility=[f"{prefix}.response_2"],
                causal_inputs=[eid("review_round_2"), eid("respond_round_1")],
                required_inputs=[eid("review_round_2")],
                relation_kinds={
                    eid("review_round_2"): "derived_from",
                    eid("respond_round_1"): "supersedes",
                },
            ),
            Event(
                id=eid("reproduction_recovery"),
                type="reproduction_recovery",
                time=_date(start, 17, 3),
                params={
                    "run": project["reproduction_success"],
                    "benchmark": project["benchmark_v3"],
                },
                visibility=[f"{prefix}.reproduction_recovery"],
                causal_inputs=[eid("respond_round_2"), eid("benchmark_patch")],
                required_inputs=[eid("respond_round_2"), eid("benchmark_patch")],
                relation_kinds={
                    eid("respond_round_2"): "enables",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
            Event(
                id=eid("submit_revision_3"),
                type="submit_revision_3",
                time=_date(start, 18, 6),
                params={"revision": project["revision_v3"]},
                visibility=[f"{prefix}.revision_3"],
                causal_inputs=[
                    eid("respond_round_2"),
                    eid("reproduction_recovery"),
                ],
                required_inputs=[eid("reproduction_recovery")],
                relation_kinds={
                    eid("respond_round_2"): "derived_from",
                    eid("reproduction_recovery"): "enables",
                },
            ),
            Event(
                id=eid("resolve_review"),
                type="resolve_review",
                time=_date(start, 19, 5),
                params={},
                visibility=[f"{prefix}.review_resolution"],
                causal_inputs=[
                    eid("review_round_2"),
                    eid("respond_round_2"),
                    eid("submit_revision_3"),
                ],
                required_inputs=[eid("respond_round_2"), eid("submit_revision_3")],
                relation_kinds={
                    eid("review_round_2"): "derived_from",
                    eid("respond_round_2"): "derived_from",
                    eid("submit_revision_3"): "enables",
                },
            ),
            Event(
                id=eid("meta_decision"),
                type="meta_decision",
                time=_date(start, 20, 8),
                params={"accepted": True},
                visibility=[f"{prefix}.meta_decision"],
                causal_inputs=[
                    eid("resolve_review"),
                    eid("respond_round_2"),
                    eid("reproduction_recovery"),
                    eid("benchmark_patch"),
                ],
                required_inputs=[
                    eid("resolve_review"),
                    eid("reproduction_recovery"),
                ],
                relation_kinds={
                    eid("resolve_review"): "enables",
                    eid("respond_round_2"): "derived_from",
                    eid("reproduction_recovery"): "enables",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
        ]
    )
    evs.extend(
        grounded_events(
            project,
            prefix,
            start,
            ingest_off=4,
            alt_off=6,
            adopt_off=62,
        )
    )
    evs.extend(
        cascade_events(
            project,
            prefix,
            start,
            seed_off=12,
            ack_off=95,
            reopen_off=248,
            ratify_off=270,
        )
    )
    evs.extend(_experiment_events(project, prefix, start))
    evs.extend(_source_workflow_events(project, prefix))
    blockers = (
        "GPU quota",
        "reviewer latency",
        "dataset mirror",
        "license check",
        "plot regeneration",
    )
    n_pulses = int(project.get("n_pulses", 10))
    for i in range(n_pulses):
        evs.append(
            Event(
                id=eid(f"status_pulse_{i}"),
                type="status_pulse",
                time=_date(start, 0, 9 + i * 11),
                params={
                    "week_index": i + 1,
                    "blocker": blockers[i % len(blockers)],
                    "ticket": f"RL-{pid[:4].upper()}-{200 + i}",
                },
                visibility=[f"{prefix}.status_pulse_{i}"],
                causal_inputs=[eid("report_v1")],
                relation_kinds={eid("report_v1"): "observed_in"},
            )
        )
    evs.sort(key=lambda e: (e.time, e.id))
    _ = pid
    return evs


def simulate_lab(spec: dict[str, Any]) -> dict[str, SimulatedWorld]:
    worlds: dict[str, SimulatedWorld] = {}
    projects = [("focal", spec["focal"])] + [
        (f"par{i}", p) for i, p in enumerate(spec["parallels"])
    ]
    for prefix, project in projects:
        sim = WorldSimulator(
            spec={
                **spec,
                "world_id": f"{spec['world_id']}:{prefix}",
                "project": project,
                "domain": "researchlab",
            },
            init_values=init_values(project),
            check_preconditions=check_preconditions,
            apply_event=apply_event,
        )
        evs = events_for_lab(project, prefix)
        worlds[prefix] = sim.run(evs)
        worlds[prefix].spec["project"] = project
        worlds[prefix].spec["prefix"] = prefix
        worlds[prefix].spec["domain"] = "researchlab"
    return worlds
