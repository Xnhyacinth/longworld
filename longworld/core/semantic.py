"""Semantic quality: boilerplate detection, roles, near-duplicate scans.

These are engineering gates, not literature-standard thresholds.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from longworld.core.prose import SENTENCE_BANK
from longworld.core.render import Artifact
from longworld.core.taxonomy import SourceOrigin, WorkflowKind, artifact_classification

_PULSE_ID = re.compile(r"status_pulse", re.IGNORECASE)
_WEEKLY = re.compile(r"weekly\s+(pulse|review|\d+)", re.IGNORECASE)
_TICKET_BLOCK = re.compile(
    r"(ticket\s+\S+\s+blocked on|no scores, no\s+commits)", re.IGNORECASE
)


def is_boilerplate(artifact: Artifact) -> bool:
    if artifact.doc_type in {"status_pulse", "pulse"}:
        return True
    if _PULSE_ID.search(artifact.artifact_id or ""):
        return True
    text = artifact.text or ""
    low = text.lower()
    if "weekly pulse" in low:
        return True
    if _WEEKLY.search(text) and _TICKET_BLOCK.search(text):
        return True
    if "this paragraph exists to occupy timeline space" in low:
        return True
    return "unique intervening documents between legal and finance" in low


def boilerplate_char_fraction(text: str) -> float:
    """Fraction of characters that are copies of the frozen 20-sentence bank."""
    if not text:
        return 0.0
    hits = 0
    for s in SENTENCE_BANK:
        if not s:
            continue
        hits += text.count(s) * len(s)
    return min(1.0, hits / max(1, len(text)))


def pulse_doc_ratio(artifacts: list[Artifact]) -> float:
    if not artifacts:
        return 0.0
    n = sum(
        1 for a in artifacts if is_boilerplate(a) or _PULSE_ID.search(a.artifact_id)
    )
    return n / len(artifacts)


def _schema_family(aid: str) -> str:
    if ".anchor." in aid:
        return "anchor"
    head = aid.split(":")[0] if ":" in aid else aid.split(".")[0]
    if head.startswith("lab"):
        return "lab"
    if head.startswith("code"):
        return "code"
    return "company"


def artifact_role(
    artifact: Artifact,
    ess_ids: set[str],
    hard_ids: set[str],
    gold_stem: str = "",
    support_ids: set[str] | None = None,
) -> str:
    if artifact.artifact_id in ess_ids:
        return "causal_gold"
    if artifact.artifact_id in (support_ids or set()):
        return "causal_supporting"
    if is_boilerplate(artifact):
        return "boilerplate"
    if (artifact.slots or {}).get("anchor"):
        return "natural_background"
    if artifact.doc_type == "source_pack" and not artifact.reveals_events:
        return "natural_background"
    if artifact.artifact_id in hard_ids:
        return "structural_hard_negative"
    if gold_stem and not artifact.artifact_id.startswith(gold_stem):
        if _schema_family(artifact.artifact_id) == _schema_family(gold_stem):
            return "structural_hard_negative"
        return "natural_background"
    return "natural_background"


def gold_artifact_stem(ess_ids: set[str]) -> str:
    if not ess_ids:
        return ""
    aid = next(iter(ess_ids))
    return aid.split(".", 1)[0] if "." in aid else aid


def _valid_arxiv_file_spans(
    text: str, spans: object, declared_basenames: object
) -> bool:
    if not isinstance(spans, list) or not isinstance(declared_basenames, list):
        return False
    basenames: list[str] = []
    previous_end = 0
    for span in spans:
        if not isinstance(span, dict) or set(span) != {
            "path",
            "basename",
            "char_start",
            "char_end",
            "text_sha256",
        }:
            return False
        path = str(span["path"])
        basename = str(span["basename"])
        start = span["char_start"]
        end = span["char_end"]
        if (
            not path
            or path.rsplit("/", 1)[-1] != basename
            or basename in basenames
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not previous_end <= start < end <= len(text)
            or hashlib.sha256(text[start:end].encode()).hexdigest()
            != span["text_sha256"]
        ):
            return False
        basenames.append(basename)
        previous_end = end
    return sorted(basenames) == declared_basenames


def _attested_arxiv_revision(artifact: Artifact) -> tuple[str, str] | None:
    slots = artifact.slots or {}
    params = slots.get("params")
    if not isinstance(params, dict):
        return None
    classification = artifact_classification(artifact)
    workflow_id = str(slots.get("source_workflow_id") or "")
    record_id = str(slots.get("source_record_id") or "")
    parent_provenance_id = str(slots.get("parent_provenance_id") or "")
    provenance_id = classification.provenance_id
    text_sha256 = hashlib.sha256(artifact.text.encode()).hexdigest()
    operation = str(params.get("provenance_operation") or "")
    if operation in {
        "arxiv_semantic_latex_body_v1",
        "arxiv_semantic_latex_body_v2",
    }:
        excluded_paths = params.get("excluded_paths")
        if (
            not parent_provenance_id.startswith("sha256:")
            or not isinstance(excluded_paths, list)
            or any(not isinstance(path, str) for path in excluded_paths)
            or excluded_paths != sorted(set(excluded_paths))
        ):
            return None
        provenance_payload = {
            "operation": operation,
            "parent_provenance_id": parent_provenance_id,
            "excluded_paths": excluded_paths,
            "text_sha256": text_sha256,
        }
        if operation == "arxiv_semantic_latex_body_v2":
            source_file_spans = params.get("source_file_spans")
            source_view_basenames = params.get("source_view_basenames")
            if not _valid_arxiv_file_spans(
                artifact.text, source_file_spans, source_view_basenames
            ):
                return None
            provenance_payload.update(
                {
                    "source_file_spans": source_file_spans,
                    "source_view_basenames": source_view_basenames,
                }
            )
    elif operation == "counterfactual_revision_text":
        if not parent_provenance_id.startswith("derived-sha256:"):
            return None
        provenance_payload = {
            "operation": operation,
            "parent_provenance_id": parent_provenance_id,
            "text_sha256": text_sha256,
        }
        if params.get("source_file_spans") is not None:
            source_file_spans = params.get("source_file_spans")
            source_view_basenames = params.get("source_view_basenames")
            if not _valid_arxiv_file_spans(
                artifact.text, source_file_spans, source_view_basenames
            ):
                return None
            provenance_payload.update(
                {
                    "source_file_spans": source_file_spans,
                    "source_view_basenames": source_view_basenames,
                }
            )
    else:
        return None
    expected_provenance = (
        "derived-sha256:"
        + hashlib.sha256(
            json.dumps(
                provenance_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    if (
        slots.get("event_type") != "arxiv_revision"
        or slots.get("real_workflow_record") is not True
        or classification.source_origin is not SourceOrigin.REAL_DERIVED
        or classification.workflow_kind is not WorkflowKind.REAL_SOURCE_DERIVED
        or not workflow_id
        or not record_id
        or params.get("workflow_id") != workflow_id
        or params.get("record_id") != record_id
        or params.get("text") != artifact.text
        or params.get("text_sha256") != text_sha256
        or params.get("provenance_id") != expected_provenance
        or provenance_id != expected_provenance
        or params.get("source_family") != "arxiv_record"
        or not str(params.get("source_url") or "").startswith("https://arxiv.org/abs/")
    ):
        return None
    return workflow_id, record_id


def _attested_revision_relations(
    artifacts: list[Artifact],
) -> set[tuple[str, str, str]]:
    relations: set[tuple[str, str, str]] = set()
    visible_revisions = {
        revision
        for artifact in artifacts
        if (revision := _attested_arxiv_revision(artifact)) is not None
    }
    for artifact in artifacts:
        slots = artifact.slots or {}
        params = slots.get("params")
        classification = artifact_classification(artifact)
        if not isinstance(params, dict):
            continue
        workflow_id = str(slots.get("source_workflow_id") or "")
        source_record_id = str(params.get("source_record_id") or "")
        target_record_id = str(params.get("target_record_id") or "")
        source_revision_id = str(params.get("source_revision_id") or "")
        target_revision_id = str(params.get("target_revision_id") or "")
        source_revision = re.fullmatch(r"v([1-9][0-9]*)", source_revision_id)
        target_revision = re.fullmatch(r"v([1-9][0-9]*)", target_revision_id)
        source_record_revision = re.search(r"v([1-9][0-9]*)$", source_record_id)
        target_record_revision = re.search(r"v([1-9][0-9]*)$", target_record_id)
        canonical_text = (
            json.dumps(
                {
                    "kind": "arxiv_revision_relation",
                    "relation": params.get("relation_kind"),
                    "source_revision": source_revision_id,
                    "target_revision": target_revision_id,
                    "evidence": params.get("evidence_quote"),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        expected_provenance = (
            "derived-sha256:" + hashlib.sha256(artifact.text.encode()).hexdigest()
        )
        if (
            slots.get("event_type") == "arxiv_revision_relation"
            and classification.source_origin is SourceOrigin.REAL_DERIVED
            and classification.workflow_kind is WorkflowKind.HYBRID_CAUSAL
            and params.get("workflow_id") == workflow_id
            and params.get("relation_kind") == "revision_of"
            and isinstance(params.get("evidence_quote"), str)
            and params["evidence_quote"]
            and artifact.text == canonical_text
            and source_record_id
            and target_record_id
            and source_record_id != target_record_id
            and source_revision is not None
            and target_revision is not None
            and source_record_revision is not None
            and target_record_revision is not None
            and source_record_revision.group(1) == source_revision.group(1)
            and target_record_revision.group(1) == target_revision.group(1)
            and int(source_revision.group(1)) > int(target_revision.group(1))
            and (workflow_id, source_record_id) in visible_revisions
            and (workflow_id, target_record_id) in visible_revisions
            and classification.provenance_id == expected_provenance
        ):
            relations.add((workflow_id, source_record_id, target_record_id))
    return relations


def _authorized_revision_overlap(
    current: tuple[str, str] | None,
    previous: tuple[str, str] | None,
    relations: set[tuple[str, str, str]],
) -> bool:
    if not current or not previous or current[0] != previous[0]:
        return False
    workflow_id = current[0]

    def has_path(source_record_id: str, target_record_id: str) -> bool:
        frontier = [source_record_id]
        visited: set[str] = set()
        while frontier:
            record_id = frontier.pop()
            if record_id in visited:
                continue
            visited.add(record_id)
            for relation_workflow, source_id, target_id in relations:
                if relation_workflow != workflow_id or source_id != record_id:
                    continue
                if target_id == target_record_id:
                    return True
                frontier.append(target_id)
        return False

    return has_path(current[1], previous[1]) or has_path(previous[1], current[1])


def sentence_near_dup_ratio(artifacts: list[Artifact]) -> float:
    """Character share of unattributed exact sentence duplication.

    Exact overlap between two separately attested arXiv revisions is versioned
    source evidence, not synthetic copying. Repetition inside either revision,
    or overlap with any unverified artifact, remains duplicate content.
    """
    relations = _attested_revision_relations(artifacts)
    seen: dict[str, list[tuple[int, tuple[str, str] | None]]] = {}
    dup_chars = 0
    total = 0
    for artifact_index, a in enumerate(artifacts):
        revision = _attested_arxiv_revision(a)
        for raw in re.split(r"(?<=[.!?])\s+", a.text or ""):
            s = " ".join(raw.lower().split())
            if len(s) < 40:
                continue
            total += len(s)
            previous = seen.get(s, [])
            repeated_in_artifact = any(
                previous_index == artifact_index
                for previous_index, _previous_revision in previous
            )
            authorized_overlap = bool(previous) and all(
                _authorized_revision_overlap(revision, previous_revision, relations)
                for _previous_index, previous_revision in previous
            )
            if previous and (repeated_in_artifact or not authorized_overlap):
                dup_chars += len(s)
            seen.setdefault(s, []).append((artifact_index, revision))
    if total == 0:
        return 0.0
    return dup_chars / total


def type_share(artifacts: list[Artifact]) -> dict[str, float]:
    n = max(1, len(artifacts))
    c = Counter(a.doc_type for a in artifacts)
    return {k: round(v / n, 4) for k, v in c.items()}


def max_type_share(artifacts: list[Artifact]) -> float:
    shares = type_share(artifacts)
    return max(shares.values()) if shares else 0.0
