from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

from longworld.core.cascade import cascade_events
from longworld.core.grounded import grounded_events
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.codeforge.events import (
    apply_event,
    check_preconditions,
    init_values,
)


def _date(start: date, months: int, extra_days: int = 0) -> date:
    return start + timedelta(days=30 * months + extra_days)


def events_for_repo(project: dict[str, Any], prefix: str) -> list[Event]:
    start = date.fromisoformat(project["start"])
    repo = project["repo"].lower()
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
            id=eid("broken_commit"),
            type="broken_commit",
            time=_date(start, 0, 8),
            params={"commit": project["broken_hash"]},
            visibility=[f"{prefix}.broken_commit"],
            causal_inputs=[eid("license_clause")],
            relation_kinds={eid("license_clause"): "enables"},
        ),
        Event(
            id=eid("ci_fail"),
            type="ci_fail",
            time=_date(start, 1, 3),
            params={"flake_token": project["flake_token"]},
            visibility=[f"{prefix}.ci_log"],
            causal_inputs=[eid("broken_commit")],
            relation_kinds={eid("broken_commit"): "derived_from"},
        ),
        Event(
            id=eid("issue_bug"),
            type="issue_bug",
            time=_date(start, 2, 2),
            params={"filed": True, "fail_token": project["fail_token"]},
            visibility=[f"{prefix}.github_issue"],
            causal_inputs=[eid("broken_commit")],
            relation_kinds={eid("broken_commit"): "contradicts"},
        ),
        Event(
            id=eid("hotfix"),
            type="hotfix",
            time=_date(start, 3, 5),
            params={"commit": project["hotfix_hash"]},
            visibility=[f"{prefix}.hotfix_commit"],
            preconditions=["issue_filed"],
            causal_inputs=[eid("issue_bug"), eid("ci_fail")],
            relation_kinds={
                eid("issue_bug"): "enables",
                eid("ci_fail"): "derived_from",
            },
        ),
        Event(
            id=eid("changelog_stale"),
            type="changelog_stale",
            time=_date(start, 4, 2),
            params={"quoted_hash": project["broken_hash"]},
            visibility=[f"{prefix}.changelog"],
            causal_inputs=[eid("broken_commit"), eid("hotfix")],
            relation_kinds={
                eid("broken_commit"): "contradicts",
                eid("hotfix"): "supersedes",
            },
        ),
        Event(
            id=eid("tag_release"),
            type="tag_release",
            time=_date(start, 5, 4),
            params={"adopt_head": adopt},
            visibility=[f"{prefix}.tag_release"],
            causal_inputs=[eid("hotfix"), eid("license_clause")],
            relation_kinds={
                eid("hotfix"): "derived_from",
                eid("license_clause"): "enables",
            },
        ),
    ]
    if project.get("process", {}).get("rollback"):
        evs.append(
            Event(
                id=eid("rollback_hotfix"),
                type="rollback_hotfix",
                time=_date(start, 6, 2),
                params={},
                visibility=[f"{prefix}.rollback_note"],
                causal_inputs=[eid("broken_commit"), eid("hotfix")],
                relation_kinds={
                    eid("hotfix"): "supersedes",
                    eid("broken_commit"): "derived_from",
                },
            )
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
            ack_off=100,
            reopen_off=240,
            ratify_off=265,
        )
    )
    repo_records = list(project.get("repo_episode") or [])
    event_id_by_record = {
        str(record["record_key"]): eid(f"real_{index:05d}")
        for index, record in enumerate(repo_records)
    }
    body_sha_by_record = {
        str(record["record_key"]): hashlib.sha256(
            str(record["body_text"]).encode()
        ).hexdigest()
        for record in repo_records
    }
    real_record_events: list[Event] = []
    real_release_events: list[Event] = []
    for index, record in enumerate(repo_records):
        record_key = str(record["record_key"])
        links = [str(link) for link in record.get("links") or []]
        linked_event_ids = [
            event_id_by_record[link] for link in links if link in event_id_by_record
        ]
        required_event_ids: list[str] = []
        required_body_hashes: set[str] = set()
        for link in links:
            linked_event_id = event_id_by_record.get(link)
            body_sha256 = body_sha_by_record.get(link)
            if (
                linked_event_id is not None
                and body_sha256 is not None
                and body_sha256 not in required_body_hashes
            ):
                required_event_ids.append(linked_event_id)
                required_body_hashes.add(body_sha256)
        facts = dict(record.get("body_facts") or {})
        record_event = Event(
            id=event_id_by_record[record_key],
            type="repo_record",
            time=date.fromisoformat(str(record["occurred_at"])[:10]),
            params={
                "record_key": record_key,
                "record_id": str(record["record_id"]),
                "single_workflow": len(project.get("real_workflow_ids") or []) == 1,
                "record_kind": str(record["kind"]),
                "body_text": str(record["body_text"]),
                "body_sha256": hashlib.sha256(
                    str(record["body_text"]).encode()
                ).hexdigest(),
                "source_body_facts": facts,
                "links": links,
                "source_links": list(record.get("source_links") or []),
                "source_pointer": str(record.get("source_pointer") or ""),
                "workflow_id": str(record["workflow_id"]),
                "source_origin": str(record["source_origin"]),
                "provenance_id": str(record["provenance_id"]),
                "source_url": str(record["source_url"]),
                "release_cycle": int(record.get("release_cycle") or 0),
                "source_order": index,
                **facts,
            },
            visibility=[f"{prefix}.real_record_{index:05d}"],
            causal_inputs=list(linked_event_ids),
            required_inputs=required_event_ids,
            relation_kinds={item: "derived_from" for item in linked_event_ids},
        )
        evs.append(record_event)
        real_record_events.append(record_event)
        if record_event.params["record_kind"] == "release":
            real_release_events.append(record_event)
    _link_release_cycles(real_release_events)
    if real_release_events:
        latest_release = max(real_release_events, key=lambda item: (item.time, item.id))
        cross_repo_merges = [
            event
            for event in real_record_events
            if event.params["record_kind"] == "merge"
            and event.params["source_url"] != latest_release.params["source_url"]
        ]
        if cross_repo_merges:
            latest_merge = max(cross_repo_merges, key=lambda item: (item.time, item.id))
            policy_id = hashlib.sha256(
                f"{latest_release.id}|{latest_merge.id}".encode()
            ).hexdigest()[:12]
            evs.append(
                Event(
                    id=eid("real_cross_repo_integration"),
                    type="cross_repo_integration",
                    time=max(latest_release.time, latest_merge.time)
                    + timedelta(days=1),
                    params={
                        "policy_id": f"XDEP-{policy_id}",
                        "release_record_key": latest_release.params["record_key"],
                        "merge_record_key": latest_merge.params["record_key"],
                        "release_source_url": latest_release.params["source_url"],
                        "merge_source_url": latest_merge.params["source_url"],
                        "active": True,
                    },
                    visibility=[f"{prefix}.real_cross_repo_integration"],
                    causal_inputs=[latest_release.id, latest_merge.id],
                    required_inputs=[latest_release.id, latest_merge.id],
                    relation_kinds={
                        latest_release.id: "requires_release",
                        latest_merge.id: "requires_merge",
                    },
                )
            )
    workflow_merges: list[str] = []
    for i, workstream in enumerate(project.get("workstreams") or []):
        stream = str(workstream["id"])
        base_day = 285 + i * 16
        request_id = eid(f"workflow_{i}_request")
        review_id = eid(f"workflow_{i}_review")
        ci_id = eid(f"workflow_{i}_ci")
        license_id = eid(f"workflow_{i}_license")
        merge_id = eid(f"workflow_{i}_merge")
        common = {
            "stream": stream,
            "package": workstream["package"],
        }
        evs.extend(
            [
                Event(
                    id=request_id,
                    type="dependency_request",
                    time=start + timedelta(days=base_day),
                    params={
                        **common,
                        "version": workstream["version"],
                        "license": workstream["license"],
                        "advisory": workstream["advisory"],
                        "docket": project["docket_token"],
                    },
                    visibility=[f"{prefix}.workflow_{i}_request"],
                    causal_inputs=[eid("tag_release")],
                    relation_kinds={eid("tag_release"): "enables"},
                ),
                Event(
                    id=review_id,
                    type="maintainer_review",
                    time=start + timedelta(days=base_day + 3),
                    params={
                        **common,
                        "decision": "approved",
                        "review_token": workstream["review_token"],
                    },
                    visibility=[f"{prefix}.workflow_{i}_review"],
                    causal_inputs=[request_id],
                    required_inputs=[request_id],
                    relation_kinds={request_id: "derived_from"},
                ),
                Event(
                    id=ci_id,
                    type="ci_validation",
                    time=start + timedelta(days=base_day + 7),
                    params={
                        **common,
                        "passed": True,
                        "ci_token": workstream["ci_token"],
                    },
                    visibility=[f"{prefix}.workflow_{i}_ci"],
                    causal_inputs=[review_id],
                    required_inputs=[review_id],
                    relation_kinds={review_id: "enables"},
                ),
                Event(
                    id=license_id,
                    type="license_clearance",
                    time=start + timedelta(days=base_day + 9),
                    params={
                        **common,
                        "compatible": True,
                        "clearance_token": workstream["clearance_token"],
                    },
                    visibility=[f"{prefix}.workflow_{i}_license"],
                    causal_inputs=[request_id],
                    required_inputs=[request_id],
                    relation_kinds={request_id: "derived_from"},
                ),
                Event(
                    id=merge_id,
                    type="integration_merge",
                    time=start + timedelta(days=base_day + 12),
                    params={**common, "merge_token": workstream["merge_token"]},
                    visibility=[f"{prefix}.workflow_{i}_merge"],
                    causal_inputs=[ci_id, license_id],
                    required_inputs=[ci_id, license_id],
                    relation_kinds={ci_id: "enables", license_id: "enables"},
                ),
            ]
        )
        workflow_merges.append(merge_id)
    if workflow_merges:
        release_id = eid("workflow_release_decision")
        evs.append(
            Event(
                id=release_id,
                type="release_decision",
                time=start + timedelta(days=290 + len(workflow_merges) * 16),
                params={
                    "policy": "require_all",
                    "streams": [str(item["id"]) for item in project["workstreams"]],
                },
                visibility=[f"{prefix}.workflow_release_decision"],
                causal_inputs=list(workflow_merges),
                required_inputs=list(workflow_merges),
                relation_kinds={item: "enables" for item in workflow_merges},
            )
        )
    blockers = (
        "runner quota",
        "reviewer latency",
        "wheel mirror",
        "license check",
        "docs rebuild",
    )
    n_pulses = int(project.get("n_pulses", 10))
    for i in range(n_pulses):
        evs.append(
            Event(
                id=eid(f"status_pulse_{i}"),
                type="status_pulse",
                time=_date(start, 0, 11 + i * 11),
                params={
                    "week_index": i + 1,
                    "blocker": blockers[i % len(blockers)],
                    "ticket": f"CF-{repo[:4].upper()}-{300 + i}",
                },
                visibility=[f"{prefix}.status_pulse_{i}"],
                causal_inputs=[eid("broken_commit")],
                relation_kinds={eid("broken_commit"): "observed_in"},
            )
        )
    evs.sort(key=lambda e: (e.time, e.id))
    return evs


def _release_version(event: Event) -> tuple[int, ...]:
    tag = str(event.params.get("tag") or "").lstrip("vV")
    try:
        return tuple(int(part) for part in tag.split("."))
    except ValueError:
        return ()


def _link_release_cycles(releases: list[Event]) -> None:
    """Create body-derived supersession edges between distinct release tags."""
    previous_by_repo: dict[str, Event] = {}
    cycles_by_repo: dict[str, dict[tuple[int, ...], int]] = {}
    for event in sorted(releases, key=lambda item: (item.time, item.id)):
        source_url = str(event.params.get("source_url") or "")
        version = _release_version(event)
        version_cycles = cycles_by_repo.setdefault(source_url, {})
        if version not in version_cycles:
            version_cycles[version] = len(version_cycles) + 1
        event.params["release_cycle"] = version_cycles[version]
        previous = previous_by_repo.get(source_url)
        if previous is not None:
            previous_version = _release_version(previous)
            if version and previous_version and version > previous_version:
                event.causal_inputs.append(previous.id)
                event.required_inputs.append(previous.id)
                event.relation_kinds[previous.id] = "supersedes"
        if previous is None or version > _release_version(previous):
            previous_by_repo[source_url] = event


def simulate_code(spec: dict[str, Any]) -> dict[str, SimulatedWorld]:
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
                "domain": "codeforge",
            },
            init_values=init_values(project),
            check_preconditions=check_preconditions,
            apply_event=apply_event,
        )
        evs = events_for_repo(project, prefix)
        worlds[prefix] = sim.run(evs)
        worlds[prefix].spec["project"] = project
        worlds[prefix].spec["prefix"] = prefix
        worlds[prefix].spec["domain"] = "codeforge"
    return worlds
