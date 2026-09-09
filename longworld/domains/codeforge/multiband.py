from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from itertools import pairwise
from typing import TYPE_CHECKING

from longworld.core.world import Event, SimulatedWorld

if TYPE_CHECKING:
    from longworld.domains.company.queries import QuerySpec

_BAND_ORDER = {"16k": 0, "32k": 1, "64k": 2, "128k": 3}
_RELEASE_HISTORY_QUERY_TYPES = {
    "version_selection",
    "release_supersession_trace",
}
_RELEASE_BAND_CYCLE_COUNT = {"16k": 1, "32k": 2, "64k": 3}
_PATCH_REVIEW_TEST_BAND_CYCLE_COUNT = {
    "16k": 1,
    "32k": 2,
    "64k": 4,
    "128k": 8,
}
_FAILURE_RECOVERY_BAND_CYCLE_COUNT = {
    "16k": 1,
    "32k": 2,
    "64k": 4,
    "128k": 8,
}
_REAL_SOURCE_ORIGINS = {"real_public", "real_private_export", "real_derived"}
_SOURCE_RELATION_KINDS = {"derived_from", "source_context"}


def _release_cycle_keys(query: QuerySpec) -> tuple[str, ...]:
    keys: tuple[str, ...]
    if query.query_type == "version_selection":
        prefix = "real:release:"
        if not query.answer_key.startswith(prefix):
            raise ValueError("release-history version answer has no release key")
        keys = (query.answer_key.removeprefix(prefix),)
    else:
        keys = tuple(query.answer_key.split("|"))
    if not all(keys) or len(keys) != len(set(keys)):
        raise ValueError("release-history answer has invalid release cycles")
    return keys


def _trace_program_identity(query: QuerySpec) -> str:
    def without_scale(value: object) -> object:
        if isinstance(value, dict):
            return {
                str(key): without_scale(item)
                for key, item in value.items()
                if key != "cycle_count"
            }
        if isinstance(value, list):
            return [without_scale(item) for item in value]
        return value

    return json.dumps(
        without_scale(query.program_ops), sort_keys=True, separators=(",", ":")
    )


def _release_cycle_closure(world: SimulatedWorld, release_key: str) -> set[str]:
    events = {event.id: event for event in world.events}
    releases = [
        event
        for event in world.events
        if event.type == "repo_record"
        and event.params.get("record_kind") == "release"
        and event.params.get("record_key") == release_key
    ]
    if len(releases) != 1:
        raise ValueError("release-history answer does not identify one release event")
    selected: set[str] = set()

    def visit(event_id: str) -> None:
        if event_id in selected:
            return
        event = events.get(event_id)
        if event is None:
            raise ValueError("release-history release cycle has a missing input")
        for parent_id in event.required_inputs:
            if event.relation_kinds.get(parent_id) != "supersedes":
                visit(parent_id)
        selected.add(event_id)

    visit(releases[0].id)
    return selected


def _authentic_relation_edges(
    world: SimulatedWorld, event_ids: Iterable[str]
) -> set[tuple[str, str, str]]:
    selected = set(event_ids)
    events = {
        event.id: event
        for event in world.events
        if event.id in selected and event.type == "repo_record"
    }
    edges: set[tuple[str, str, str]] = set()
    for event in events.values():
        synthetic_inputs = set(event.params.get("synthetic_relation_inputs") or [])
        edges.update(
            (
                parent_id,
                event.id,
                str(event.relation_kinds.get(parent_id) or "causal_input"),
            )
            for parent_id in event.causal_inputs
            if parent_id in events
            and parent_id not in synthetic_inputs
            and _has_authentic_source_relation(events[parent_id], event, parent_id)
        )
    return edges


def _valid_source_record_binding(event: Event) -> bool:
    params = event.params
    if str(params.get("source_origin") or "") not in _REAL_SOURCE_ORIGINS:
        return False
    identity = {
        "workflow_id": str(params.get("workflow_id") or ""),
        "record_key": str(params.get("record_key") or ""),
        "record_id": str(params.get("record_id") or ""),
        "provenance_id": str(params.get("provenance_id") or ""),
        "grounded_source_id": str(params.get("grounded_source_id") or ""),
        "source_body_sha256": str(params.get("source_body_sha256") or ""),
    }
    if not str(params.get("source_url") or "") or not all(identity.values()):
        return False
    binding = params.get("source_record_binding")
    if not isinstance(binding, dict) or any(
        str(binding.get(key) or "") != value for key, value in identity.items()
    ):
        return False
    binding_sha256 = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return binding_sha256 == params.get("source_record_binding_sha256")


def _has_authentic_source_relation(parent: Event, child: Event, parent_id: str) -> bool:
    relation_kind = str(child.relation_kinds.get(parent_id) or "")
    if relation_kind not in _SOURCE_RELATION_KINDS:
        return False
    if not _valid_source_record_binding(parent) or not _valid_source_record_binding(
        child
    ):
        return False
    if (
        parent.params.get("workflow_id") != child.params.get("workflow_id")
        or parent.params.get("provenance_id") != child.params.get("provenance_id")
        or parent.params.get("source_url") != child.params.get("source_url")
    ):
        return False
    child_binding = child.params["source_record_binding"]
    links = child_binding.get("links")
    if not isinstance(links, list):
        return False
    expected = {
        "record_key": str(parent.params["record_key"]),
        "record_id": str(parent.params["record_id"]),
        "target_workflow_id": str(parent.params["workflow_id"]),
        "target_grounded_source_id": str(parent.params["grounded_source_id"]),
    }
    return any(
        isinstance(link, dict)
        and all(str(link.get(key) or "") == value for key, value in expected.items())
        for link in links
    )


def bind_cumulative_release_history(
    world: SimulatedWorld, queries: list[QuerySpec]
) -> None:
    """Bind and validate nested real release-history tasks across length bands."""
    groups: dict[str, list[QuerySpec]] = {}
    for query in queries:
        if query.query_type not in _RELEASE_HISTORY_QUERY_TYPES:
            continue
        if (
            len(query.preferred_length_buckets) != 1
            or query.preferred_length_buckets[0] not in _BAND_ORDER
        ):
            raise ValueError("release-history query must bind one exact length band")
        if not query.semantic_growth_group:
            raise ValueError("release-history query has no semantic growth group")
        groups.setdefault(query.semantic_growth_group, []).append(query)

    for group_id, group in groups.items():
        group.sort(key=lambda item: _BAND_ORDER[item.preferred_length_buckets[0]])
        bands = [item.preferred_length_buckets[0] for item in group]
        if len(bands) != len(set(bands)):
            raise ValueError("release-history group repeats a length band")
        expected = ["16k", "32k", "64k"][: len(bands)]
        if bands != expected:
            raise ValueError("release-history group has a non-cumulative band sequence")

        cycle_keys = [_release_cycle_keys(query) for query in group]
        cycle_support = [
            set().union(*(_release_cycle_closure(world, key) for key in keys))
            for keys in cycle_keys
        ]
        for query, band, keys in zip(group, bands, cycle_keys, strict=True):
            expected_query_type = (
                "version_selection" if band == "16k" else "release_supersession_trace"
            )
            if query.query_type != expected_query_type:
                raise ValueError("release-history band uses the wrong answer operator")
            if len(keys) != _RELEASE_BAND_CYCLE_COUNT[band]:
                raise ValueError("release-history band has the wrong cycle count")
            declared_counts = [
                op["cycle_count"] for op in query.program_ops if "cycle_count" in op
            ]
            if query.query_type == "release_supersession_trace" and (
                not declared_counts
                or any(
                    type(count) is not int or count != len(keys)
                    for count in declared_counts
                )
            ):
                raise ValueError(
                    "release-history declared cycle count disagrees with answer"
                )

        longest = cycle_keys[-1]
        orientations = {"prefix", "suffix"}
        for keys in cycle_keys[:-1]:
            matching: set[str] = set()
            if keys == longest[: len(keys)]:
                matching.add("prefix")
            if keys == longest[-len(keys) :]:
                matching.add("suffix")
            orientations &= matching
        if not orientations:
            raise ValueError(
                "release-history cycles are not an ordered cumulative history"
            )

        trace_identities = {
            _trace_program_identity(query)
            for query in group
            if query.query_type == "release_supersession_trace"
        }
        if len(trace_identities) > 1:
            raise ValueError(
                "release-history trace program identity changes across bands"
            )
        execution_work = [
            len(query.program_ops) * len(keys)
            for query, keys in zip(group, cycle_keys, strict=True)
        ]
        if any(before >= after for before, after in pairwise(execution_work)):
            raise ValueError("release-history executable answer program does not grow")

        for before, after in pairwise(group):
            if not set(before.essential_event_ids) < set(after.essential_event_ids):
                raise ValueError("release-history necessary evidence does not grow")
            if not set(before.sufficient_event_ids) < set(after.sufficient_event_ids):
                raise ValueError("release-history strict support does not grow")
            if before.proof_depth >= after.proof_depth:
                raise ValueError("release-history proof depth does not grow")
            if not _authentic_relation_edges(
                world, before.sufficient_event_ids
            ) < _authentic_relation_edges(world, after.sufficient_event_ids):
                raise ValueError(
                    "release-history authentic source relations do not grow"
                )
        for query, expected_support in zip(group, cycle_support, strict=True):
            if set(query.sufficient_event_ids) != expected_support:
                raise ValueError(
                    "release-history strict support is not the exact cycle closure"
                )

        base_task_group = f"codeforge.release_history|{group_id}"
        for query in group:
            query.base_task_group = base_task_group


def bind_cumulative_patch_review_test_history(
    world: SimulatedWorld, queries: list[QuerySpec]
) -> None:
    """Validate nested patch/review/test/ancestry programs across exact bands."""
    families = {
        "patch_review_test_ancestry": (
            "JOIN_PATCH_REVIEW_TEST_ANCESTRY",
            "codeforge.patch_review_test",
        ),
        "patch_files_review_test_ancestry": (
            "JOIN_PATCH_FILES_REVIEW_TEST_ANCESTRY",
            "codeforge.patch_files_review_test",
        ),
        "patch_files_review_test_release_v2": (
            "JOIN_PATCH_FILES_REVIEW_TEST_RELEASE_V2",
            "codeforge.patch_files_review_test_release_v2",
        ),
    }
    groups: dict[tuple[str, str], list[QuerySpec]] = {}
    for query in queries:
        if query.query_type not in families:
            continue
        if (
            len(query.preferred_length_buckets) != 1
            or query.preferred_length_buckets[0] not in _BAND_ORDER
            or not query.semantic_growth_group
        ):
            raise ValueError("patch-review-test query has no exact growth binding")
        groups.setdefault((query.query_type, query.semantic_growth_group), []).append(
            query
        )

    events_by_record = {
        str(event.params.get("record_key")): event
        for event in world.events
        if event.type == "repo_record"
    }
    for group in groups.values():
        op_name, base_task_prefix = families[group[0].query_type]
        group.sort(key=lambda item: _BAND_ORDER[item.preferred_length_buckets[0]])
        bands = [query.preferred_length_buckets[0] for query in group]
        if bands != ["16k", "32k", "64k", "128k"][: len(group)]:
            raise ValueError(
                "patch-review-test group has a non-cumulative band sequence"
            )
        programs: list[list[dict[str, object]]] = []
        for query, band in zip(group, bands, strict=True):
            ops = [op for op in query.program_ops if op.get("op") == op_name]
            if len(ops) != _PATCH_REVIEW_TEST_BAND_CYCLE_COUNT[band]:
                raise ValueError("patch-review-test band has the wrong cycle count")
            programs.append(ops)
            selected = set(query.sufficient_event_ids)
            for op in ops:
                patch = events_by_record.get(str(op.get("patch_record_key") or ""))
                review = events_by_record.get(str(op.get("review_record_key") or ""))
                ci = events_by_record.get(str(op.get("ci_record_key") or ""))
                merge = events_by_record.get(str(op.get("merge_record_key") or ""))
                release = events_by_record.get(str(op.get("release_record_key") or ""))
                cycle = (patch, review, ci, merge, release)
                if any(event is None or event.id not in selected for event in cycle):
                    raise ValueError("patch-review-test program is missing an episode")
                assert patch is not None and review is not None and ci is not None
                assert merge is not None and release is not None
                required_edges = (
                    (patch, merge),
                    (review, merge),
                    (patch, ci),
                    (merge, release),
                    (ci, release),
                )
                if any(
                    parent.id not in child.causal_inputs
                    or not _has_authentic_source_relation(parent, child, parent.id)
                    for parent, child in required_edges
                ):
                    raise ValueError(
                        "patch-review-test program lacks an authentic source link"
                    )
        longest = programs[-1]
        if any(program != longest[-len(program) :] for program in programs[:-1]):
            raise ValueError("patch-review-test programs are not an ordered history")
        for before, after in pairwise(group):
            if not set(before.essential_event_ids) < set(after.essential_event_ids):
                raise ValueError("patch-review-test necessary evidence does not grow")
            if not set(before.sufficient_event_ids) < set(after.sufficient_event_ids):
                raise ValueError("patch-review-test sufficient evidence does not grow")
            if not _authentic_relation_edges(
                world, before.sufficient_event_ids
            ) < _authentic_relation_edges(world, after.sufficient_event_ids):
                raise ValueError(
                    "patch-review-test authentic source relations do not grow"
                )
        base_task_group = f"{base_task_prefix}|{group[0].semantic_growth_group}"
        for query in group:
            query.base_task_group = base_task_group


def bind_cumulative_failure_recovery_history(
    world: SimulatedWorld, queries: list[QuerySpec]
) -> None:
    """Validate nested failed-CI/repair/review/pass/release programs across bands."""
    groups: dict[str, list[QuerySpec]] = {}
    for query in queries:
        if query.query_type != "failure_recovery_release_trace":
            continue
        if not any(
            op.get("op") == "JOIN_FAILURE_RECOVERY_RELEASE" for op in query.program_ops
        ):
            continue
        if (
            len(query.preferred_length_buckets) != 1
            or query.preferred_length_buckets[0] not in _BAND_ORDER
            or not query.semantic_growth_group
        ):
            raise ValueError("failure-recovery query has no exact growth binding")
        groups.setdefault(query.semantic_growth_group, []).append(query)

    events_by_record = {
        str(event.params.get("record_key")): event
        for event in world.events
        if event.type == "repo_record"
    }
    for group in groups.values():
        group.sort(key=lambda item: _BAND_ORDER[item.preferred_length_buckets[0]])
        bands = [query.preferred_length_buckets[0] for query in group]
        if bands != ["16k", "32k", "64k", "128k"][: len(group)]:
            raise ValueError(
                "failure-recovery group has a non-cumulative band sequence"
            )
        programs: list[list[dict[str, object]]] = []
        for query, band in zip(group, bands, strict=True):
            ops = [
                op
                for op in query.program_ops
                if op.get("op") == "JOIN_FAILURE_RECOVERY_RELEASE"
            ]
            if len(ops) != _FAILURE_RECOVERY_BAND_CYCLE_COUNT[band]:
                raise ValueError("failure-recovery band has the wrong cycle count")
            programs.append(ops)
            selected = set(query.sufficient_event_ids)
            for op in ops:
                broken = events_by_record.get(
                    str(op.get("broken_commit_record_key") or "")
                )
                fail_ci = events_by_record.get(str(op.get("fail_ci_record_key") or ""))
                repair = events_by_record.get(
                    str(op.get("repair_commit_record_key") or "")
                )
                review = events_by_record.get(str(op.get("review_record_key") or ""))
                pass_ci = events_by_record.get(str(op.get("pass_ci_record_key") or ""))
                merge = events_by_record.get(str(op.get("merge_record_key") or ""))
                release = events_by_record.get(str(op.get("release_record_key") or ""))
                cycle = (broken, fail_ci, repair, review, pass_ci, merge, release)
                if any(event is None or event.id not in selected for event in cycle):
                    raise ValueError("failure-recovery program is missing an episode")
                assert broken is not None and fail_ci is not None and repair is not None
                assert review is not None and pass_ci is not None
                assert merge is not None and release is not None
                required_edges = (
                    (broken, fail_ci),
                    (repair, pass_ci),
                    (repair, merge),
                    (review, merge),
                    (merge, release),
                    (pass_ci, release),
                )
                if any(
                    parent.id not in child.causal_inputs
                    or not _has_authentic_source_relation(parent, child, parent.id)
                    for parent, child in required_edges
                ):
                    raise ValueError(
                        "failure-recovery program lacks an authentic source link"
                    )
                if (
                    broken.params.get("commit") == repair.params.get("commit")
                    or fail_ci.params.get("test") != pass_ci.params.get("test")
                    or fail_ci.params.get("result") != "failed"
                    or pass_ci.params.get("result") != "passed"
                ):
                    raise ValueError(
                        "failure-recovery cycle is not a cross-commit repair"
                    )
        longest = programs[-1]
        if any(program != longest[-len(program) :] for program in programs[:-1]):
            raise ValueError("failure-recovery programs are not an ordered history")
        for before, after in pairwise(group):
            if not set(before.essential_event_ids) < set(after.essential_event_ids):
                raise ValueError("failure-recovery necessary evidence does not grow")
            if not set(before.sufficient_event_ids) < set(after.sufficient_event_ids):
                raise ValueError("failure-recovery sufficient evidence does not grow")
            if not _authentic_relation_edges(
                world, before.sufficient_event_ids
            ) < _authentic_relation_edges(world, after.sufficient_event_ids):
                raise ValueError(
                    "failure-recovery authentic source relations do not grow"
                )
        base_task_group = f"codeforge.failure_recovery|{group[0].semantic_growth_group}"
        for query in group:
            query.base_task_group = base_task_group
