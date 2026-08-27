"""Deterministic parsers for real documents and exported development workflows.

The connectors in this module preserve source order and explicit relationships.
They never sample, shuffle, or concatenate unrelated records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    attestation_key_from_env,
    canonical_attested_payload,
    verify_attestation,
)
from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    SourceLineage,
    _parse_timestamp,
    _read_regular_file,
)
from longworld.core.taxonomy import SourceOrigin

GIT_WORKFLOW_SCHEMA = "longworld.git-workflow.v1"
PUBLIC_SCANNER = "longworld-public-secret-patterns"
PUBLIC_SCANNER_REVISION = "v2"
PUBLIC_POLICY_SHA256_ENV = "LONGWORLD_PUBLIC_POLICY_SHA256"
GH_BINARY_SHA256_ENV = "LONGWORLD_GH_BINARY_SHA256"
EPISODE_REPLAY_BUNDLE_SCHEMA = "longworld.episode-replay-bundle.v1"
MAX_WORKFLOW_RECORDS = 10_000
MAX_WORKFLOW_RECORD_CHARS = 1_000_000
MAX_EPISODE_BUNDLE_BYTES = 1_000_000
MAX_EPISODES = 1_000
_SHA256 = re.compile(r"[0-9a-f]{64}")


def approved_public_policy_digests(raw: str | None = None) -> frozenset[str]:
    """Return active allowlist pins, including historical hashes after growth.

    Probe and production may list comma-separated SHA-256 digests so adding a
    repository to the canonical allowlist does not invalidate already-signed
    public episode exports that still embed the previous digest.
    """
    text = (
        (os.environ.get(PUBLIC_POLICY_SHA256_ENV, "") if raw is None else raw)
        .strip()
        .lower()
    )
    if not text:
        return frozenset()
    digests: set[str] = set()
    for part in text.replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        if _SHA256.fullmatch(item) is None:
            raise ValueError("public policy pin contains an invalid digest")
        digests.add(item)
    return frozenset(digests)


_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_RFC_NUMBER = re.compile(r"(?mi)^Request for Comments:\s*(\d+)\b")
_RFC_STD = re.compile(r"(?mi)^STD:\s*(\d+)\b")
_RFC_CATEGORY = re.compile(r"(?mi)^Category:\s*([^\r\n]+?)\s*$")
_RFC_MONTH = re.compile(
    r"(?mi)\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{4})\s*$"
)


@dataclass(frozen=True)
class WorkflowFact:
    key: str
    value: str
    record_id: str
    char_start: int
    char_end: int
    source_quote: str


@dataclass(frozen=True)
class WorkflowRecord:
    record_id: str
    kind: str
    occurred_at: str
    text: str
    links: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)
    source_pointer: str = ""


@dataclass(frozen=True)
class RealWorkflow:
    workflow_id: str
    source_kind: str
    source_origin: SourceOrigin
    lineage: SourceLineage
    records: tuple[WorkflowRecord, ...]
    facts: dict[str, WorkflowFact]


def _match_fact(
    key: str, prefix: str, match: re.Match[str], record_id: str, group: int = 1
) -> WorkflowFact:
    value = match.group(group).strip()
    start, end = match.span(group)
    return WorkflowFact(
        key=key,
        value=f"{prefix}{value}",
        record_id=record_id,
        char_start=start,
        char_end=end,
        source_quote=match.group(0).strip(),
    )


def _rfc_number_list(text: str, label: str) -> tuple[list[str], int, int, str]:
    lines = text.splitlines(keepends=True)
    offset = 0
    for index, line in enumerate(lines):
        match = re.match(rf"^{re.escape(label)}:\s*(.*)$", line, re.IGNORECASE)
        if not match:
            offset += len(line)
            continue
        selected = line
        for continuation in lines[index + 1 :]:
            if not re.match(r"^\s+\d[\d, ]*", continuation):
                break
            selected += continuation
        numbers = re.findall(r"\b\d{3,5}\b", selected)
        return numbers, offset, offset + len(selected.rstrip()), selected.strip()
    return [], -1, -1, ""


def parse_rfc_workflow(text: str, lineage: SourceLineage) -> RealWorkflow:
    """Extract traceable RFC header facts from document content, not filenames."""
    number_match = _RFC_NUMBER.search(text)
    if number_match is None:
        raise ProvenanceError(
            "RFC source does not contain a Request for Comments header"
        )
    number = number_match.group(1)
    record_id = f"rfc:{number}:header"
    facts: dict[str, WorkflowFact] = {
        "document_id": _match_fact("document_id", "RFC ", number_match, record_id)
    }
    std_match = _RFC_STD.search(text)
    if std_match is not None:
        facts["standards_track"] = _match_fact(
            "standards_track", "STD ", std_match, record_id
        )
    category_match = _RFC_CATEGORY.search(text)
    if category_match is not None:
        facts["category"] = _match_fact("category", "", category_match, record_id)
    month_match = _RFC_MONTH.search(text[: number_match.start() + 1000])
    if month_match is not None:
        value = f"{month_match.group(1)} {month_match.group(2)}"
        facts["published"] = WorkflowFact(
            key="published",
            value=value,
            record_id=record_id,
            char_start=month_match.start(1),
            char_end=month_match.end(2),
            source_quote=value,
        )
    for label, key in (("Obsoletes", "obsoletes"), ("Updates", "updates")):
        numbers, start, end, quote = _rfc_number_list(text, label)
        if numbers:
            facts[key] = WorkflowFact(
                key=key,
                value=", ".join(f"RFC {item}" for item in numbers),
                record_id=record_id,
                char_start=start,
                char_end=end,
                source_quote=quote,
            )
    record = WorkflowRecord(
        record_id=record_id,
        kind="rfc_header",
        occurred_at=lineage.retrieved_at,
        text=text,
        attributes={key: fact.value for key, fact in facts.items()},
        source_pointer=lineage.source_path,
    )
    return RealWorkflow(
        workflow_id=f"rfc:{number}",
        source_kind="rfc",
        source_origin=SourceOrigin.REAL_PUBLIC,
        lineage=lineage,
        records=(record,),
        facts=facts,
    )


def query_workflow_fact(workflow: RealWorkflow, key: str) -> str:
    """Evaluate a content-grounded lookup over a parsed workflow fact."""
    fact = workflow.facts.get(key)
    if fact is None:
        return "unknown"
    return fact.value


def _validate_public_export_governance(payload: dict[str, Any]) -> None:
    authorization = payload.get("authorization")
    if not isinstance(authorization, dict) or any(
        not str(authorization.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    ):
        raise ProvenanceError("public export authorization receipt is invalid")
    _parse_timestamp(str(authorization["reviewed_at"]), "authorization.reviewed_at")
    privacy_review = payload.get("privacy_review")
    if (
        not isinstance(privacy_review, dict)
        or privacy_review.get("emails") != "redacted"
        or privacy_review.get("secrets") != "fail_closed"
        or privacy_review.get("scanner") != PUBLIC_SCANNER
        or privacy_review.get("scanner_revision") != PUBLIC_SCANNER_REVISION
    ):
        raise ProvenanceError("public export privacy review receipt is invalid")
    public_policy = payload.get("public_policy")
    if (
        not isinstance(public_policy, dict)
        or public_policy.get("record_id") != authorization.get("record_id")
        or _SHA256.fullmatch(str(public_policy.get("sha256") or "")) is None
    ):
        raise ProvenanceError("public export policy receipt is invalid")
    source_client = payload.get("source_client")
    if (
        not isinstance(source_client, dict)
        or not Path(str(source_client.get("path") or "")).is_absolute()
        or _SHA256.fullmatch(str(source_client.get("sha256") or "")) is None
    ):
        raise ProvenanceError("public export source client receipt is invalid")
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    if environment in {"probe", "production"}:
        try:
            approved_policy = approved_public_policy_digests()
        except ValueError as error:
            raise ProvenanceError(
                "public export trust pins do not match active policy"
            ) from error
        expected_client = os.environ.get(GH_BINARY_SHA256_ENV, "").strip().lower()
        if (
            not approved_policy
            or public_policy.get("sha256") not in approved_policy
            or not expected_client
            or source_client.get("sha256") != expected_client
        ):
            raise ProvenanceError("public export trust pins do not match active policy")


def load_git_workflow_export(
    path: Path,
    *,
    attestation_key: bytes | None = None,
    _verified_raw: bytes | None = None,
) -> RealWorkflow:
    """Load a local JSON export while preserving its records and causal links."""
    try:
        raw = (
            _verified_raw
            if _verified_raw is not None
            else read_git_workflow_export_bytes(path)
        )
        raw_text = raw.decode("utf-8")
        payload = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read git workflow export: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        GIT_WORKFLOW_SCHEMA
    ):
        raise ProvenanceError("unsupported git workflow schema")
    if not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("git_workflow"),
        purpose="git_workflow",
    ):
        raise ProvenanceError("git workflow export has no valid producer attestation")
    repository_url = str(payload.get("repository_url") or "")
    revision = str(payload.get("revision") or "")
    exported_at = str(payload.get("exported_at") or "")
    license_id = str(payload.get("license") or "")
    raw_source_origin = payload.get("source_origin")
    if not isinstance(raw_source_origin, str) or not raw_source_origin:
        raise ProvenanceError("git workflow export requires an explicit source origin")
    try:
        source_origin = SourceOrigin(raw_source_origin)
    except ValueError as exc:
        raise ProvenanceError("unsupported git workflow source origin") from exc
    if source_origin not in {
        SourceOrigin.REAL_PUBLIC,
        SourceOrigin.REAL_PRIVATE_EXPORT,
    }:
        raise ProvenanceError("git workflow source origin must be real")
    if source_origin == SourceOrigin.REAL_PRIVATE_EXPORT:
        raise ProvenanceError(
            "private workflow exports are disabled until independently signed "
            "authorization and privacy-scan receipts are verified"
        )
    else:
        _validate_public_export_governance(payload)
    _parse_timestamp(exported_at, "exported_at")
    if not revision:
        raise ProvenanceError("git workflow export has no revision")
    if not license_id:
        raise ProvenanceError("git workflow export has no license or access policy")
    digest = hashlib.sha256(canonical_attested_payload(payload)).hexdigest()
    lineage = SourceLineage(
        provenance_id=f"sha256:{digest}",
        url=repository_url,
        license=license_id,
        retrieved_at=exported_at,
        parser="git_workflow_json@1",
        sha256=digest,
        revision=revision,
        source_path=str(path),
    )
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ProvenanceError("git workflow export contains no records")
    if len(raw_records) > MAX_WORKFLOW_RECORDS:
        raise ProvenanceError("git workflow export contains too many records")
    records: list[WorkflowRecord] = []
    seen: set[str] = set()
    record_kinds: dict[str, str] = {}
    records_by_id: dict[str, WorkflowRecord] = {}
    previous_time = None
    for index, item in enumerate(raw_records):
        if not isinstance(item, dict):
            raise ProvenanceError("git workflow record must be an object")
        record_id = str(item.get("id") or "")
        kind = str(item.get("kind") or "")
        occurred_at = str(item.get("occurred_at") or "")
        text = str(item.get("text") or "")
        links_raw = item.get("links", [])
        attributes = item.get("attributes", {})
        external_source_pointer = str(item.get("source_pointer") or "")
        if not record_id or record_id in seen:
            raise ProvenanceError("git workflow record id is missing or duplicated")
        if kind not in {
            "issue",
            "pull_request",
            "review",
            "commit",
            "ci_run",
            "merge",
            "release",
            "license",
        }:
            raise ProvenanceError(f"unsupported git workflow record kind: {kind!r}")
        current_time = _parse_timestamp(occurred_at, "occurred_at")
        if previous_time is not None and current_time < previous_time:
            raise ProvenanceError("git workflow records are not chronological")
        previous_time = current_time
        if not text.strip():
            raise ProvenanceError(f"git workflow record {record_id} has no text")
        if len(text) > MAX_WORKFLOW_RECORD_CHARS:
            raise ProvenanceError(f"git workflow record {record_id} is too large")
        if not isinstance(links_raw, list) or not all(
            isinstance(link, str) for link in links_raw
        ):
            raise ProvenanceError(f"git workflow record {record_id} has invalid links")
        unknown = [link for link in links_raw if link not in seen]
        if unknown:
            raise ProvenanceError(
                f"unknown workflow link from {record_id}: {', '.join(unknown)}"
            )
        if not isinstance(attributes, dict):
            raise ProvenanceError(
                f"git workflow record {record_id} has invalid attributes"
            )
        if source_origin == SourceOrigin.REAL_PUBLIC and kind == "release":
            tag_commit_sha = str(attributes.get("tag_commit_sha") or "")
            merge_commit_sha = str(attributes.get("merge_commit_sha") or "")
            linked_merges = [
                records_by_id[link]
                for link in links_raw
                if record_kinds.get(link) == "merge"
            ]
            linked_merge_sha = (
                str(linked_merges[0].attributes.get("merge_commit_sha") or "")
                if len(linked_merges) == 1
                else ""
            )
            expected_compare_endpoint = (
                repository_url.replace(
                    "https://github.com/", "https://api.github.com/repos/"
                )
                + f"/compare/{merge_commit_sha}...{tag_commit_sha}"
            )
            if (
                attributes.get("ancestry_verified") is not True
                or _GIT_SHA.fullmatch(tag_commit_sha) is None
                or _GIT_SHA.fullmatch(merge_commit_sha) is None
                or linked_merge_sha != merge_commit_sha
                or revision != merge_commit_sha
                or attributes.get("compare_status") not in {"ahead", "identical"}
                or attributes.get("compare_base_sha") != merge_commit_sha
                or attributes.get("compare_head_sha") != tag_commit_sha
                or attributes.get("compare_endpoint") != expected_compare_endpoint
                or _SHA256.fullmatch(
                    str(attributes.get("compare_response_sha256") or "")
                )
                is None
            ):
                raise ProvenanceError(
                    f"public release ancestry receipt is invalid: {record_id}"
                )
        record_attributes = dict(attributes)
        record_attributes["local_source_pointer"] = f"{path}#/records/{index}"
        records.append(
            WorkflowRecord(
                record_id=record_id,
                kind=kind,
                occurred_at=occurred_at,
                text=text,
                links=tuple(links_raw),
                attributes=record_attributes,
                source_pointer=(
                    external_source_pointer
                    if external_source_pointer
                    else f"{path}#/records/{index}"
                ),
            )
        )
        seen.add(record_id)
        record_kinds[record_id] = kind
        records_by_id[record_id] = records[-1]

    facts: dict[str, WorkflowFact] = {}
    releases = [record for record in records if record.kind == "release"]
    if releases:
        release = releases[-1]
        tag = str(release.attributes.get("tag") or "")
        if tag:
            tag_start = raw_text.find(json.dumps(tag))
            if tag_start >= 0:
                tag_start += 1
            facts["release_tag"] = WorkflowFact(
                key="release_tag",
                value=tag,
                record_id=release.record_id,
                char_start=tag_start,
                char_end=tag_start + len(tag) if tag_start >= 0 else -1,
                source_quote=tag,
            )
    facts["revision"] = WorkflowFact(
        key="revision",
        value=revision,
        record_id=records[-1].record_id,
        char_start=-1,
        char_end=-1,
        source_quote=revision,
    )
    return RealWorkflow(
        workflow_id=f"git:{digest[:16]}",
        source_kind="git_export",
        source_origin=source_origin,
        lineage=lineage,
        records=tuple(records),
        facts=facts,
    )


def read_git_workflow_export_bytes(path: Path) -> bytes:
    """Read a bounded regular workflow export without following a final symlink."""
    try:
        return _read_regular_file(path, MAX_SOURCE_BYTES)
    except OSError as exc:
        raise ProvenanceError(f"cannot read git workflow export: {exc}") from exc


def load_episode_replay_bundle(
    path: Path,
    *,
    attestation_key: bytes | None = None,
    expected_sha256: str | None = None,
    _verified_raw: bytes | None = None,
) -> list[RealWorkflow]:
    """Load an exact, producer-attested set of workflow episodes without globbing."""
    try:
        raw = (
            _verified_raw
            if _verified_raw is not None
            else read_episode_replay_bundle_bytes(path)
        )
        if expected_sha256 is not None and (
            _SHA256.fullmatch(expected_sha256) is None
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ProvenanceError("episode replay bundle digest mismatch")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read episode replay bundle: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        EPISODE_REPLAY_BUNDLE_SCHEMA
    ):
        raise ProvenanceError("unsupported episode replay bundle schema")
    if payload.get("composition") != "chronological_causal_union":
        raise ProvenanceError("unsupported episode replay composition")
    if not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("episode_replay_bundle"),
        purpose="episode_replay_bundle",
    ):
        raise ProvenanceError("episode replay bundle has no valid attestation")
    raw_episodes = payload.get("episodes")
    if (
        not isinstance(raw_episodes, list)
        or not raw_episodes
        or len(raw_episodes) > MAX_EPISODES
    ):
        raise ProvenanceError("episode replay bundle has invalid episode count")
    path_base = str(payload.get("path_base") or "manifest_directory")
    if path_base == "manifest_directory":
        base_directory = path.parent
    elif path_base == "repository_root":
        base_directory = path.parent.parent
    else:
        raise ProvenanceError("unsupported episode replay path base")

    workflows: list[RealWorkflow] = []
    seen_paths: set[str] = set()
    seen_workflows: set[str] = set()
    for entry in raw_episodes:
        if not isinstance(entry, dict):
            raise ProvenanceError("episode replay entry must be an object")
        relative = Path(str(entry.get("path") or ""))
        if (
            not str(relative)
            or relative.is_absolute()
            or ".." in relative.parts
            or str(relative) in seen_paths
        ):
            raise ProvenanceError("episode replay path is unsafe or duplicated")
        declared_digest = str(entry.get("sha256") or "")
        if _SHA256.fullmatch(declared_digest) is None:
            raise ProvenanceError("episode replay digest is invalid")
        export_path = base_directory / relative
        try:
            raw_export = _read_regular_file(export_path, MAX_SOURCE_BYTES)
        except OSError as exc:
            raise ProvenanceError(f"cannot read workflow episode: {exc}") from exc
        if hashlib.sha256(raw_export).hexdigest() != declared_digest:
            raise ProvenanceError(f"episode replay digest mismatch: {relative}")
        workflow = load_git_workflow_export(
            export_path,
            attestation_key=attestation_key,
            _verified_raw=raw_export,
        )
        if workflow.workflow_id in seen_workflows:
            raise ProvenanceError("episode replay bundle duplicates a workflow")
        workflows.append(workflow)
        seen_paths.add(str(relative))
        seen_workflows.add(workflow.workflow_id)
    return workflows


def read_episode_replay_bundle_bytes(path: Path) -> bytes:
    """Read a bounded regular bundle without following a final symlink."""
    try:
        return _read_regular_file(path, MAX_EPISODE_BUNDLE_BYTES)
    except OSError as exc:
        raise ProvenanceError(f"cannot read episode replay bundle: {exc}") from exc
