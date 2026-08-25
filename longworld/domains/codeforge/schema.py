"""CodeForge world: repository workflow with independent proof motifs.

Motifs (not clones of company v2→v3 or lab leaderboard supersession):
  supersession     — release tag adopts HEAD; changelog is not controlling
  fork_join        — CI flake token AND issue fail token jointly name the fault
  delayed_effect   — broken commit hash only matters after CI ran
  contradiction    — changelog quotes the broken hash; tag adopts hotfix HEAD
  hidden_bridge    — early SPDX becomes the shipping license only at tag time
  counterfactual   — if the issue is never filed, HEAD stays the broken commit
"""

from __future__ import annotations

import hashlib
import random
import re
from datetime import date, timedelta
from typing import Any

from longworld.core.grounded import pick_anchors
from longworld.core.process import assign_register, attach_roles, sample_process
from longworld.core.realworkflow import RealWorkflow, WorkflowRecord
from longworld.domains.company.names import FIRST, LAST, STEMS

SCHEMA_VERSION = "p1.1"


_FACT_PATTERNS = {
    "commit": re.compile(r"\bcommit\s+([0-9a-f]{7,40})\b", re.IGNORECASE),
    "package": re.compile(
        r"\b(?:package|dependency|proposes|carries|packages)\s+"
        r"([a-z][a-z0-9_.-]+)\s+version\s+v?\d+\.\d+(?:\.\d+)?\b",
        re.IGNORECASE,
    ),
    "version": re.compile(
        r"\b(?:package|dependency|proposes|carries|packages)\s+"
        r"[a-z][a-z0-9_.-]+\s+version\s+(v?\d+\.\d+(?:\.\d+)?)\b",
        re.IGNORECASE,
    ),
    "run": re.compile(r"\bCI\s+run\s+([a-z0-9_.-]+)", re.IGNORECASE),
    "test": re.compile(r"\b(test_[a-z0-9_]+)\b", re.IGNORECASE),
    "tag": re.compile(r"\brelease(?:\s+tag)?\s+(v?\d+\.\d+(?:\.\d+)?)", re.IGNORECASE),
    "license": re.compile(
        r"\b(Apache-2\.0|BSD-3-Clause|BSD-2-Clause|MIT|MPL-2\.0|GPL-3\.0-only|"
        r"LGPL-3\.0-only|AGPL-3\.0-only)\b",
        re.IGNORECASE,
    ),
}


def _body_string_fact(
    record: WorkflowRecord,
    key: str,
    *attribute_keys: str,
) -> str:
    """Return a source value only when the record body itself supports it."""
    text = record.text
    lowered = text.lower()
    for attribute_key in attribute_keys or (key,):
        value = str(record.attributes.get(attribute_key) or "").strip()
        if value and value.lower() in lowered:
            return value
    pattern = _FACT_PATTERNS.get(key)
    match = pattern.search(text) if pattern is not None else None
    return match.group(1) if match is not None else ""


def _body_result(record: WorkflowRecord) -> str:
    value = _body_string_fact(
        record, "result", "result", "status", "decision", "conclusion", "state"
    )
    if value:
        lowered = value.lower()
    else:
        lowered = record.text.lower()
    if any(token in lowered for token in ("failed", "failure", "red")):
        return "failed"
    if any(token in lowered for token in ("passed", "success", "green")):
        return "passed"
    if "approved" in lowered:
        return "approved"
    if "rejected" in lowered:
        return "rejected"
    return ""


def _body_compatibility(record: WorkflowRecord) -> bool | None:
    lowered = record.text.lower()
    if "incompatible" in lowered or "not compatible" in lowered:
        return False
    if "compatible" in lowered:
        return True
    return None


def _body_dependency_compatibility(record: WorkflowRecord) -> tuple[str, bool] | None:
    """Extract only an explicit dependency-license compatibility conclusion."""
    for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", record.text):
        if not re.search(
            r"\bdependenc(?:y|ies)\b|dependency graph|dependency tree",
            sentence,
            re.IGNORECASE,
        ):
            continue
        relation = re.search(
            r"\b(?P<decision>incompatible|not\s+compatible|(?:fully\s+)?compatible)"
            r"\s+with\s+(?:a\s+clean\s+)?(?P<license>Apache-2\.0|BSD-3-Clause|"
            r"BSD-2-Clause|MIT|MPL-2\.0|GPL-3\.0-only|LGPL-3\.0-only|AGPL-3\.0-only)\b",
            sentence,
            re.IGNORECASE,
        )
        if relation is None:
            continue
        decision = relation.group("decision").lower()
        return relation.group("license"), not (
            "incompatible" in decision or "not compatible" in decision
        )
    return None


def _record_facts(record: WorkflowRecord) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    fields: list[tuple[str, tuple[str, ...]]] = [
        ("project", ("project", "repository")),
        ("dependency_project", ("dependency_project", "upstream")),
    ]
    if record.kind in {"commit", "pull_request"}:
        fields.extend(
            [
                ("commit", ("commit", "hash", "revision", "sha", "head_sha")),
                ("package", ("package", "component", "dependency")),
                ("version", ("version", "candidate_version")),
            ]
        )
    elif record.kind == "ci_run":
        fields.extend(
            [
                ("commit", ("commit", "hash", "revision", "sha", "head_sha")),
                ("run", ("run", "run_id", "ci_run")),
                ("test", ("test", "test_name", "name")),
            ]
        )
    elif record.kind == "merge":
        fields.append(("commit", ("commit", "hash", "revision", "sha", "head_sha")))
    elif record.kind == "release":
        fields.append(("tag", ("tag", "release_tag")))
    elif record.kind == "license":
        fields.append(("license", ("license", "spdx", "spdx_id")))
    for key, attribute_keys in fields:
        value = _body_string_fact(record, key, *attribute_keys)
        if value:
            facts[key] = value
    if (
        record.kind == "license"
        and "license" not in facts
        and re.search(r"Apache License,?\s+Version 2\.0", record.text, re.IGNORECASE)
    ):
        facts["license"] = "Apache-2.0"
    if record.kind in {"pull_request", "review", "merge"}:
        decision = _body_dependency_compatibility(record)
        if decision is not None:
            facts["compatibility_license"], facts["compatible"] = decision
    if record.kind in {"ci_run", "review"}:
        result = _body_result(record)
        if result:
            facts["result"] = result
    if record.kind == "license":
        compatible = _body_compatibility(record)
        if compatible is not None:
            facts["compatible"] = compatible
    return facts


def _normalized_workflow_records(
    workflow: RealWorkflow, workflow_index: int
) -> list[dict[str, Any]]:
    release_cycle = 0
    records: list[dict[str, Any]] = []
    for index, record in enumerate(workflow.records):
        if record.kind == "release":
            release_cycle += 1
        records.append(
            {
                "index": index,
                "workflow_index": workflow_index,
                "workflow_id": workflow.workflow_id,
                "record_key": f"{workflow.workflow_id}:{record.record_id}",
                "record_id": record.record_id,
                "kind": record.kind,
                "occurred_at": record.occurred_at,
                "body_text": record.text,
                "links": [
                    f"{workflow.workflow_id}:{record_id}" for record_id in record.links
                ],
                "source_links": list(record.links),
                "body_facts": _record_facts(record),
                "source_pointer": record.source_pointer,
                "release_cycle": release_cycle,
                "source_origin": workflow.source_origin.value,
                "provenance_id": workflow.lineage.provenance_id,
                "source_url": workflow.lineage.url,
            }
        )
    return records


def bind_real_workflows(project: dict[str, Any], workflows: list[RealWorkflow]) -> None:
    """Attach validated episodes in chronology while preserving source identities."""
    records = [
        record
        for workflow_index, workflow in enumerate(workflows)
        for record in _normalized_workflow_records(workflow, workflow_index)
    ]
    records.sort(
        key=lambda record: (
            str(record["occurred_at"]),
            int(record["workflow_index"]),
            int(record["index"]),
        )
    )
    canonical_snapshots: dict[tuple[str, str, str], str] = {}
    aliases: dict[str, str] = {}
    canonical_records: list[dict[str, Any]] = []
    for record in records:
        record = dict(record)
        record["links"] = [aliases.get(link, link) for link in record["links"]]
        record_key = str(record["record_key"])
        if record["kind"] == "license":
            identity = (
                str(record["source_url"]),
                str(record["kind"]),
                hashlib.sha256(str(record["body_text"]).encode()).hexdigest(),
            )
            canonical = canonical_snapshots.get(identity)
            if canonical is not None:
                aliases[record_key] = canonical
                continue
            canonical_snapshots[identity] = record_key
        aliases[record_key] = record_key
        canonical_records.append(record)
    records = canonical_records
    workflow_ids = [workflow.workflow_id for workflow in workflows]
    provenance_ids = [workflow.lineage.provenance_id for workflow in workflows]
    project["repo_episode"] = records
    project["real_record_aliases"] = {
        record_key: canonical
        for record_key, canonical in aliases.items()
        if record_key != canonical
    }
    project["real_workflow_ids"] = workflow_ids
    project["real_provenance_ids"] = provenance_ids
    project["real_workflow_id"] = workflow_ids[0] if len(workflow_ids) == 1 else ""
    project["real_provenance_id"] = (
        provenance_ids[0] if len(provenance_ids) == 1 else ""
    )
    project["real_release_cycles"] = len(
        {
            (
                str(record["source_url"]),
                str((record.get("body_facts") or {}).get("tag") or record["record_id"]),
            )
            for record in records
            if record["kind"] == "release"
        }
    )
    project["real_event_chars"] = sum(len(record["body_text"]) for record in records)


def bind_real_workflow(project: dict[str, Any], workflow: RealWorkflow) -> None:
    """Backward-compatible single-episode binding."""
    bind_real_workflows(project, [workflow])


def _person(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name not in used:
            used.add(name)
            return name


def _hex(rng: random.Random, n: int = 7) -> str:
    return f"{rng.randint(0x1000000, 0xEFFFFFF):07x}"[:n]


def sample_code_spec(
    seed: int,
    n_parallel: int = 2,
    n_pulses: int = 0,
    n_workstreams: int = 0,
    real_workflow: RealWorkflow | None = None,
    real_workflows: list[RealWorkflow] | None = None,
) -> dict[str, Any]:
    if real_workflow is not None and real_workflows is not None:
        raise ValueError("pass real_workflow or real_workflows, not both")
    workflows = list(
        real_workflows or ([] if real_workflow is None else [real_workflow])
    )
    rng = random.Random(seed)
    used: set[str] = set()
    start = date(2026, 1, 8) + timedelta(days=rng.randrange(0, 10))

    def one(kind: str, is_focal: bool) -> dict[str, Any]:
        stem = rng.choice(STEMS)
        tag = rng.randint(10, 99)
        repo = f"{stem}kit-{tag}"
        broken = _hex(rng)
        hotfix = _hex(rng)
        while hotfix == broken:
            hotfix = _hex(rng)
        people = {
            "owner": _person(rng, used),
            "reviewer": _person(rng, used),
        }
        project = {
            "kind": kind,
            "is_focal": is_focal,
            "repo": repo,
            "package": f"{stem[:3].lower()}lib",
            "test_name": f"test_{stem.lower()}_q{rng.randint(3, 19)}",
            "broken_hash": broken,
            "hotfix_hash": hotfix,
            "fail_token": f"FAIL{rng.randint(10, 99)}",
            "flake_token": f"FLAKE{rng.randint(10, 99)}",
            "spdx": f"Apache-2.0-X{rng.randint(1000, 9999)}",
            "owner": people["owner"],
            "reviewer": people["reviewer"],
            "people": people,
            "org": attach_roles(people, {}),
            "process": sample_process(rng, "codeforge"),
            "ci": f"{stem}-ci",
            "n_pulses": n_pulses,
            "start": start.isoformat(),
            "latent_token": f"LT-{stem[:3].upper()}{rng.randint(1000, 9999)}",
            "decoy_latent_token": f"LD-{stem[:3].upper()}{rng.randint(1000, 9999)}",
            **pick_anchors(rng),
            "docket_token": f"DK-{stem[:3].upper()}{rng.randint(1000, 9999)}",
        }
        kinds = (
            "runtime",
            "tokenizer",
            "packaging",
            "security",
            "storage",
            "api",
        )
        project["workstreams"] = [
            {
                "id": f"{kinds[i % len(kinds)]}-{i + 1}",
                "package": f"{kinds[i % len(kinds)]}-{stem[:3].lower()}",
                "version": f"{1 + i}.{rng.randint(1, 9)}.{rng.randint(10, 99)}",
                "license": ("Apache-2.0" if i % 2 == 0 else "BSD-3-Clause"),
                "advisory": f"ADV-{rng.randint(1000, 9999)}",
                "review_token": f"RVW-{_hex(rng, 6).upper()}",
                "ci_token": f"CI-{_hex(rng, 6).upper()}",
                "clearance_token": f"LIC-{rng.randint(1000, 9999)}",
                "merge_token": f"MRG-{_hex(rng, 6).upper()}",
            }
            for i in range(n_workstreams if is_focal else 0)
        ]
        return project

    focal = one("focal", True)
    if workflows:
        bind_real_workflows(focal, workflows)
    parallels = [one("parallel", False) for _ in range(n_parallel)]
    assign_register(seed, focal, parallels)
    world_id = f"code{seed:06d}-{focal['repo'].lower()}"
    spec = {
        "world_id": world_id,
        "seed": seed,
        "schema_version": SCHEMA_VERSION,
        "domain": "codeforge",
        "truth_regime": "real_schema_synthetic_instance",
        "n_pulses": n_pulses,
        "start": start.isoformat(),
        "focal": focal,
        "parallels": parallels,
        "n_parallel": n_parallel,
        "project": focal,
    }
    if workflows:
        spec.update(
            {
                "real_workflow_ids": focal["real_workflow_ids"],
                "real_provenance_ids": focal["real_provenance_ids"],
                "real_workflow_id": focal["real_workflow_id"],
                "real_provenance_id": focal["real_provenance_id"],
                "real_release_cycles": focal["real_release_cycles"],
                "real_event_chars": focal["real_event_chars"],
            }
        )
    return spec
