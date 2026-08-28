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
import json
import random
import re
from collections.abc import Mapping
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from longworld.core.grounded import pick_anchors
from longworld.core.groundedspan import (
    GroundedFact,
    GroundedRelation,
    GroundedSource,
    GroundedSpanError,
    normalize_fact_value,
    sha256_text,
    validate_grounded_source,
    validate_grounded_sources,
)
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


def _exact_value_matches(text: str, value: str) -> list[re.Match[str]]:
    """Locate one complete structured value, never a substring of another token."""
    return list(
        re.finditer(
            rf"(?<![A-Za-z0-9_.-]){re.escape(value)}"
            rf"(?![A-Za-z0-9_])(?![.-][A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
    )


def _body_string_fact(
    record: WorkflowRecord,
    key: str,
    *attribute_keys: str,
) -> str:
    """Return a source value only when the record body itself supports it."""
    text = record.text
    for attribute_key in attribute_keys or (key,):
        value = str(record.attributes.get(attribute_key) or "").strip()
        if value:
            matches = _exact_value_matches(text, value)
            if matches:
                return matches[0].group(0)
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
    if re.search(r"\b(?:failed|failure|red)\b", lowered):
        return "failed"
    if re.search(r"\b(?:passed|success|green)\b", lowered):
        return "passed"
    if re.search(r"\bapproved\b", lowered):
        return "approved"
    if re.search(r"\brejected\b", lowered):
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


def _fact_quote_span(text: str, key: str, value: Any) -> tuple[int, int, str]:
    if key == "result":
        patterns = {
            "failed": r"\b(?:failed|failure|red)\b",
            "passed": r"\b(?:passed|success|green)\b",
            "approved": r"\bapproved\b",
            "rejected": r"\brejected\b",
        }
        matches = list(re.finditer(patterns[str(value)], text, re.IGNORECASE))
        match = matches[-1] if matches else None
    elif key == "compatible":
        pattern = (
            r"\b(?:incompatible|not\s+compatible)\b"
            if value is False
            else r"\bcompatible\b"
        )
        match = re.search(pattern, text, re.IGNORECASE)
    elif key == "license" and str(value) == "Apache-2.0":
        match = re.search(
            r"Apache-2\.0|Apache License,?\s+Version 2\.0", text, re.IGNORECASE
        )
    elif key == "version":
        matches = _exact_value_matches(text, str(value))
        match = matches[-1] if matches else None
    else:
        matches = _exact_value_matches(text, str(value))
        match = matches[0] if matches else None
    if match is None:
        raise GroundedSpanError(f"body fact {key!r} has no exact source quote")
    return match.start(), match.end(), match.group(0)


def _grounded_fact_spans(
    *, source_id: str, body_text: str, body_facts: Mapping[str, Any]
) -> list[dict[str, Any]]:
    text_sha256 = sha256_text(body_text)
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for key, value in body_facts.items():
        start, end, quote = _fact_quote_span(body_text, key, value)
        span = grouped.setdefault(
            (start, end),
            {
                "fact_id": f"{source_id}:span:{start}:{end}",
                "source_id": source_id,
                "text_sha256": text_sha256,
                "quote": quote,
                "char_start": start,
                "char_end": end,
                "normalized_quote": normalize_fact_value(quote),
                "values": {},
            },
        )
        span["values"][key] = value
    spans = [grouped[key] for key in sorted(grouped)]
    if any(
        int(left["char_end"]) > int(right["char_start"])
        for left, right in pairwise(spans)
    ):
        raise GroundedSpanError("body fact spans overlap")
    return spans


def _grounded_fact(span: Mapping[str, Any]) -> GroundedFact:
    return GroundedFact(
        fact_id=str(span["fact_id"]),
        source_id=str(span["source_id"]),
        text_sha256=str(span["text_sha256"]),
        quote=str(span["quote"]),
        char_start=int(span["char_start"]),
        char_end=int(span["char_end"]),
        normalized_quote=str(span["normalized_quote"]),
    )


def _grounded_relation(relation: Mapping[str, Any]) -> GroundedRelation:
    return GroundedRelation(
        relation_id=str(relation["relation_id"]),
        relation_type=str(relation["relation_type"]),
        source_id=str(relation["source_id"]),
        target_id=str(relation["target_id"]),
        claimed_provenance_class=str(relation["claimed_provenance_class"]),  # type: ignore[arg-type]
        evidence_fact_ids=tuple(str(item) for item in relation["evidence_fact_ids"]),
        proof_mode="structural_closure_only",
    )


def _grounded_source(record: Mapping[str, Any]) -> GroundedSource:
    return GroundedSource(
        source_id=str(record["grounded_source_id"]),
        visible_text=str(record["body_text"]),
        text_sha256=str(record["source_body_sha256"]),
        facts=tuple(
            _grounded_fact(span) for span in record["source_grounded_fact_spans"]
        ),
        relations=tuple(
            _grounded_relation(relation)
            for relation in record.get("grounded_relations") or []
        ),
    )


def _canonical_binding_sha256(binding: Mapping[str, Any]) -> str:
    return sha256_text(json.dumps(binding, sort_keys=True, separators=(",", ":")))


def _validate_record_binding(params: Mapping[str, Any]) -> list[str]:
    binding = params.get("source_record_binding")
    if not isinstance(binding, dict):
        raise GroundedSpanError("repository source record binding is missing")
    if _canonical_binding_sha256(binding) != params.get("source_record_binding_sha256"):
        raise GroundedSpanError("repository source record binding hash mismatch")
    identity = {
        "workflow_id": str(params.get("workflow_id") or ""),
        "record_key": str(params.get("record_key") or ""),
        "record_id": str(params.get("record_id") or ""),
        "provenance_id": str(params.get("provenance_id") or ""),
        "grounded_source_id": str(params.get("grounded_source_id") or ""),
        "source_body_sha256": str(params.get("source_body_sha256") or ""),
    }
    if any(binding.get(key) != value for key, value in identity.items()):
        raise GroundedSpanError("repository source record binding identity mismatch")
    raw_links = binding.get("links")
    if not isinstance(raw_links, list) or any(
        not isinstance(item, dict) for item in raw_links
    ):
        raise GroundedSpanError("repository source record link binding is malformed")
    links = [str(item.get("record_key") or "") for item in raw_links]
    source_links = [str(item.get("record_id") or "") for item in raw_links]
    if not all(links) or links != list(params.get("links") or []):
        raise GroundedSpanError("repository links differ from canonical record binding")
    if source_links != list(params.get("source_links") or []):
        raise GroundedSpanError("repository source links differ from record binding")
    if binding.get("grounded_relations") != list(
        params.get("grounded_relations") or []
    ):
        raise GroundedSpanError(
            "repository grounded relations differ from canonical record binding"
        )
    return links


def _canonical_grounded_value(key: str, quote: str) -> Any:
    lowered = quote.lower()
    if key == "result":
        if any(token in lowered for token in ("failed", "failure", "red")):
            return "failed"
        if any(token in lowered for token in ("passed", "success", "green")):
            return "passed"
        if "approved" in lowered:
            return "approved"
        if "rejected" in lowered:
            return "rejected"
        raise GroundedSpanError("result quote cannot be replayed")
    if key == "compatible":
        if "incompatible" in lowered or "not compatible" in lowered:
            return False
        if "compatible" in lowered:
            return True
        raise GroundedSpanError("compatibility quote cannot be replayed")
    if key in {"license", "compatibility_license"}:
        match = _FACT_PATTERNS["license"].search(quote)
        if match is not None:
            return match.group(1)
        if re.search(r"Apache License,?\s+Version 2\.0", quote, re.IGNORECASE):
            return "Apache-2.0"
        raise GroundedSpanError("license quote cannot be replayed")
    return quote


def _replacement_quote(key: str, value: Any) -> str:
    if key == "compatible":
        return "compatible" if value is True else "incompatible"
    return str(value)


def materialize_grounded_repo_record(params: dict[str, Any]) -> dict[str, Any]:
    """Replay one source body, including any explicit counterfactual updates."""
    replayed_links = _validate_record_binding(params)
    source_text = str(params.get("source_body_text") or "")
    incoming_body_text = str(params.get("body_text") or "")
    source_hash = str(params.get("source_body_sha256") or "")
    source_spans = list(params.get("source_grounded_fact_spans") or [])
    if sha256_text(source_text) != source_hash:
        raise GroundedSpanError("repository source visible text does not match sha256")
    if source_spans:
        validate_grounded_source(
            GroundedSource(
                source_id=str(params["grounded_source_id"]),
                visible_text=source_text,
                text_sha256=source_hash,
                facts=tuple(_grounded_fact(span) for span in source_spans),
            )
        )

    cursor = 0
    output_length = 0
    parts: list[str] = []
    current_spans: list[dict[str, Any]] = []
    replayed_facts: dict[str, Any] = {}
    for source_span in sorted(source_spans, key=lambda item: int(item["char_start"])):
        start = int(source_span["char_start"])
        end = int(source_span["char_end"])
        source_values = dict(source_span["values"])
        replacements = {
            _replacement_quote(key, params.get(key, source_value))
            for key, source_value in source_values.items()
            if params.get(key, source_value) != source_value
        }
        if len(replacements) > 1:
            raise GroundedSpanError("counterfactual span has conflicting replacements")
        quote = replacements.pop() if replacements else source_text[start:end]
        prefix = source_text[cursor:start]
        parts.append(prefix)
        output_length += len(prefix)
        current_start = output_length
        parts.append(quote)
        output_length += len(quote)
        current_end = current_start + len(quote)
        current_values: dict[str, Any] = {}
        for key, source_value in source_values.items():
            expected = params.get(key, source_value)
            value = _canonical_grounded_value(key, quote)
            if isinstance(value, str) and isinstance(expected, str):
                matches = value.casefold() == expected.casefold()
            else:
                matches = value == expected
            if not matches:
                raise GroundedSpanError(
                    f"grounded quote does not replay the requested {key!r} value"
                )
            current_values[key] = value
            replayed_facts[key] = value
        current_spans.append(
            {
                **dict(source_span),
                "quote": quote,
                "char_start": current_start,
                "char_end": current_end,
                "normalized_quote": normalize_fact_value(quote),
                "values": current_values,
            }
        )
        cursor = end
    parts.append(source_text[cursor:])
    visible_text = "".join(parts)
    if incoming_body_text not in {source_text, visible_text}:
        raise GroundedSpanError(
            "repository body_text does not match grounded source visible text"
        )
    current_hash = sha256_text(visible_text)
    for span in current_spans:
        span["text_sha256"] = current_hash
    if current_spans:
        validate_grounded_source(
            GroundedSource(
                source_id=str(params["grounded_source_id"]),
                visible_text=visible_text,
                text_sha256=current_hash,
                facts=tuple(_grounded_fact(span) for span in current_spans),
            )
        )
    return {
        "body_text": visible_text,
        "body_sha256": current_hash,
        "grounded_fact_spans": current_spans,
        "replayed_body_facts": replayed_facts,
        "replayed_links": replayed_links,
    }


def _normalized_workflow_records(
    workflow: RealWorkflow, workflow_index: int
) -> list[dict[str, Any]]:
    release_cycle = 0
    records: list[dict[str, Any]] = []
    for index, record in enumerate(workflow.records):
        if record.kind == "release":
            release_cycle += 1
        body_facts = _record_facts(record)
        source_id = (
            "git-record:"
            + hashlib.sha256(
                (
                    f"{workflow.lineage.provenance_id}|{workflow.workflow_id}|"
                    f"{record.record_id}"
                ).encode()
            ).hexdigest()[:24]
        )
        body_sha256 = sha256_text(record.text)
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
                "source_body_text": record.text,
                "source_body_sha256": body_sha256,
                "grounded_source_id": source_id,
                "source_grounded_fact_spans": _grounded_fact_spans(
                    source_id=source_id,
                    body_text=record.text,
                    body_facts=body_facts,
                ),
                "grounded_relations": [],
                "links": [
                    f"{workflow.workflow_id}:{record_id}" for record_id in record.links
                ],
                "source_links": list(record.links),
                "body_facts": body_facts,
                "source_pointer": record.source_pointer,
                "release_cycle": release_cycle,
                "source_origin": workflow.source_origin.value,
                "provenance_id": workflow.lineage.provenance_id,
                "source_url": workflow.lineage.url,
                "source_kind": workflow.source_kind,
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
    records = [dict(record) for record in records]
    records_by_key = {str(record["record_key"]): record for record in records}
    grounded_records = {
        key: record
        for key, record in records_by_key.items()
        if record["source_grounded_fact_spans"]
    }
    relation_count = 0
    for record_key, record in grounded_records.items():
        relations: list[dict[str, Any]] = []
        for link in record["links"]:
            linked = grounded_records.get(str(link))
            if linked is None:
                continue
            relation_digest = hashlib.sha256(
                f"{record_key}|{link}".encode()
            ).hexdigest()
            relations.append(
                {
                    "relation_id": f"git-link:{relation_digest[:24]}",
                    "relation_type": "explicit_record_link",
                    "source_id": record["grounded_source_id"],
                    "target_id": linked["grounded_source_id"],
                    "claimed_provenance_class": "verified_derived",
                    "evidence_fact_ids": [
                        record["source_grounded_fact_spans"][0]["fact_id"],
                        linked["source_grounded_fact_spans"][0]["fact_id"],
                    ],
                    "proof_mode": "structural_closure_only",
                }
            )
        record["grounded_relations"] = relations
        relation_count += len(relations)
    for record in records:
        link_bindings: list[dict[str, Any]] = []
        source_links = list(record["source_links"])
        if len(source_links) != len(record["links"]):
            raise GroundedSpanError("repository source link binding length mismatch")
        for link, source_record_id in zip(record["links"], source_links, strict=True):
            target = records_by_key.get(str(link))
            if target is None:
                raise GroundedSpanError("repository link references a foreign workflow")
            relation = next(
                (
                    item
                    for item in record["grounded_relations"]
                    if item["target_id"] == target["grounded_source_id"]
                ),
                None,
            )
            link_bindings.append(
                {
                    "record_key": str(link),
                    "record_id": str(source_record_id),
                    "target_workflow_id": str(target["workflow_id"]),
                    "target_grounded_source_id": str(target["grounded_source_id"]),
                    "grounded_relation_id": (
                        str(relation["relation_id"]) if relation is not None else ""
                    ),
                }
            )
        binding = {
            "workflow_id": str(record["workflow_id"]),
            "record_key": str(record["record_key"]),
            "record_id": str(record["record_id"]),
            "provenance_id": str(record["provenance_id"]),
            "grounded_source_id": str(record["grounded_source_id"]),
            "source_body_sha256": str(record["source_body_sha256"]),
            "links": link_bindings,
            "grounded_relations": list(record["grounded_relations"]),
        }
        record["source_record_binding"] = binding
        record["source_record_binding_sha256"] = _canonical_binding_sha256(binding)
    if grounded_records:
        workflow_grounded_records: dict[str, list[dict[str, Any]]] = {}
        for record in grounded_records.values():
            workflow_grounded_records.setdefault(str(record["workflow_id"]), []).append(
                record
            )
        for workflow_records in workflow_grounded_records.values():
            validate_grounded_sources(
                tuple(_grounded_source(record) for record in workflow_records),
                allow_duplicate_text_hashes=True,
            )
    workflow_ids = [workflow.workflow_id for workflow in workflows]
    provenance_ids = [workflow.lineage.provenance_id for workflow in workflows]
    project["repo_episode"] = records
    project["real_record_aliases"] = {}
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
    project["real_grounded_fact_count"] = sum(
        len(record["source_grounded_fact_spans"]) for record in records
    )
    project["real_grounded_relation_count"] = relation_count
    project["real_structural_relation_count"] = relation_count
    project["real_semantic_relation_count"] = 0


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
