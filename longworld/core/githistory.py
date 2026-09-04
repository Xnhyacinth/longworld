"""Read source-native, first-parent histories from verified local Git clones."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from longworld.core.publicscan import (
    PUBLIC_SCANNER,
    PUBLIC_SCANNER_REVISION,
    sanitize_public_text,
)
from longworld.core.realworkflow import WorkflowRecord

_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_GIT_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\Z")
_RECORD_SEPARATOR = "\x00LONGWORLD_RECORD\x00"
_FIELD_SEPARATOR = "\x00"
_PATCH_PREFIX = "LONGWORLD_PATCH\x00"
_MAX_COMMITS = 100_000
_MAX_TOKENIZER_CHARS = 128_000
TOKEN_COUNT_CACHE_REVISION = "sha256-text+tokenizer-asset-v1"
TRUNCATION_QUALITY_REVISION = "git-observed-prefix-truncation-v1"
LICENSE_BINDING_POLICY_SCHEMA = "longworld.repo-license-binding-policy.v1"
LICENSE_BINDING_RECEIPT_SCHEMA = "longworld.git-license-binding-receipt.v3"
GIT_OBJECT_PATH_PROOF_SCHEMA = "longworld.git-object-path-proof.v1"
GIT_OBJECT_PATH_ABSENCE_PROOF_SCHEMA = "longworld.git-object-path-absence-proof.v1"
_MAX_GIT_PROOF_OBJECTS = 200_000
_MAX_GIT_PROOF_RAW_BYTES = 40_000_000


class OffsetTokenizer(Protocol):
    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        return_offsets_mapping: bool,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GitCommitTruncation:
    sha: str
    total_chunk_count: int
    emitted_chunk_count: int


@dataclass(frozen=True)
class GitHistoryExtraction:
    records: tuple[WorkflowRecord, ...]
    commit_count: int
    head_revision: str
    reject_reasons: dict[str, int]
    truncated_commits: tuple[GitCommitTruncation, ...] = ()
    commit_revisions: tuple[str, ...] = ()


class DeterministicTokenCountCache:
    """Memoize exact token counts under an immutable tokenizer asset identity."""

    def __init__(self, namespace: str, counter: Callable[[str], int]) -> None:
        if not namespace:
            raise ValueError("token count cache namespace is empty")
        self._namespace = namespace.encode()
        self._counter = counter
        self._counts: dict[bytes, int] = {}
        self._hits = 0
        self._misses = 0

    def __call__(self, text: str) -> int:
        key = hashlib.sha256(self._namespace + b"\0" + text.encode()).digest()
        cached = self._counts.get(key)
        if cached is not None:
            self._hits += 1
            return cached
        count = self._counter(text)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("exact tokenizer returned an invalid token count")
        self._counts[key] = count
        self._misses += 1
        return count

    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self._counts),
            "hits": self._hits,
            "misses": self._misses,
        }


def git_truncation_quality(
    observed_commit_count: int,
    reject_reasons: dict[str, int],
    *,
    maximum_truncated_commit_ratio_ppm: int | None,
) -> dict[str, int | str | None]:
    """Describe and optionally gate only observed prefix-chunk omissions."""
    if (
        isinstance(observed_commit_count, bool)
        or not isinstance(observed_commit_count, int)
        or observed_commit_count < 0
    ):
        raise ValueError("observed Git commit count is invalid")
    truncated = reject_reasons.get("commits_truncated", 0)
    omitted = reject_reasons.get("commit_chunks_truncated", 0)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (truncated, omitted)
    ):
        raise ValueError("Git truncation counters are invalid")
    if truncated > observed_commit_count or (truncated == 0) != (omitted == 0):
        raise ValueError("Git truncation counters are inconsistent")
    if maximum_truncated_commit_ratio_ppm is not None and (
        isinstance(maximum_truncated_commit_ratio_ppm, bool)
        or not isinstance(maximum_truncated_commit_ratio_ppm, int)
        or not 0 <= maximum_truncated_commit_ratio_ppm <= 1_000_000
    ):
        raise ValueError("Git truncation ratio maximum is invalid")
    ratio_ppm = (
        truncated * 1_000_000 // observed_commit_count if observed_commit_count else 0
    )
    exceeds_maximum = (
        maximum_truncated_commit_ratio_ppm is not None
        and observed_commit_count > 0
        and truncated * 1_000_000
        > maximum_truncated_commit_ratio_ppm * observed_commit_count
    )
    if exceeds_maximum:
        raise ValueError("Git truncation ratio exceeds configured maximum")
    if truncated == 0:
        tier = "complete"
    elif maximum_truncated_commit_ratio_ppm is None:
        tier = "not_quality_gated"
    else:
        tier = "within_configured_limit"
    return {
        "revision": TRUNCATION_QUALITY_REVISION,
        "observed_commit_count": observed_commit_count,
        "truncated_commit_count": truncated,
        "omitted_chunk_count": omitted,
        "truncated_commit_ratio_ppm": ratio_ppm,
        "maximum_truncated_commit_ratio_ppm": maximum_truncated_commit_ratio_ppm,
        "tier": tier,
    }


def git_history_scan_coverage(
    source_summaries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Recompute processed first-parent intervals without implying full coverage."""
    grouped: dict[
        str, tuple[dict[str, Any], list[tuple[int, int, dict[str, str | None]]]]
    ] = {}
    for summary in source_summaries:
        repository = summary.get("repository")
        coverage = summary.get("history_coverage")
        skip = summary.get("skip_commits")
        count = summary.get("commit_count")
        anchor = summary.get("history_slice_anchor")
        if (
            not isinstance(repository, str)
            or not repository
            or not isinstance(coverage, dict)
            or isinstance(skip, bool)
            or not isinstance(skip, int)
            or skip < 0
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count <= 0
            or not isinstance(anchor, dict)
            or set(anchor)
            != {
                "oldest_revision",
                "newest_revision",
                "oldest_first_parent_revision",
            }
            or any(
                not isinstance(anchor.get(field), str)
                or _GIT_SHA.fullmatch(str(anchor[field])) is None
                for field in ("oldest_revision", "newest_revision")
            )
            or (
                anchor.get("oldest_first_parent_revision") is not None
                and (
                    not isinstance(anchor["oldest_first_parent_revision"], str)
                    or _GIT_SHA.fullmatch(anchor["oldest_first_parent_revision"])
                    is None
                )
            )
        ):
            raise ValueError("Git source scan interval is invalid")
        included = coverage.get("included_commit_count")
        if (
            isinstance(included, bool)
            or not isinstance(included, int)
            or included <= 0
            or skip + count > included
        ):
            raise ValueError("Git source scan interval escapes eligible history")
        existing = grouped.get(repository)
        if existing is None:
            grouped[repository] = (
                dict(coverage),
                [(skip, skip + count, dict(anchor))],
            )
        else:
            if existing[0] != coverage:
                raise ValueError("Git source scan coverage contracts disagree")
            existing[1].append((skip, skip + count, dict(anchor)))
    output: list[dict[str, Any]] = []
    for repository, (coverage, intervals) in sorted(grouped.items()):
        intervals.sort()
        if any(left[1] > right[0] for left, right in pairwise(intervals)):
            raise ValueError("Git source scan intervals overlap")
        included = int(coverage["included_commit_count"])
        processed = sum(stop - start for start, stop, _anchor in intervals)
        if intervals[0][0] == 0 and intervals[0][2]["newest_revision"] != coverage.get(
            "root_revision"
        ):
            raise ValueError("Git source scan root anchor does not match")
        for newer, older in pairwise(intervals):
            if (
                newer[1] == older[0]
                and newer[2]["oldest_first_parent_revision"]
                != older[2]["newest_revision"]
            ):
                raise ValueError("Git source scan slice first-parent anchors disagree")
        if intervals[-1][1] == included:
            final_anchor = intervals[-1][2]
            if coverage.get("scope") == "approved_license_path_contiguous_suffix":
                if final_anchor["oldest_revision"] != coverage.get(
                    "oldest_included_revision"
                ) or final_anchor["oldest_first_parent_revision"] != coverage.get(
                    "first_excluded_revision"
                ):
                    raise ValueError("Git source scan coverage boundary is unanchored")
            elif final_anchor["oldest_first_parent_revision"] is not None:
                raise ValueError("Git complete source scan does not reach history root")
        complete = bool(
            intervals[0][0] == 0
            and intervals[-1][1] == included
            and all(left[1] == right[0] for left, right in pairwise(intervals))
        )
        output.append(
            {
                "repository": repository,
                "history_coverage": coverage,
                "processed_intervals": [
                    [start, stop] for start, stop, _anchor in intervals
                ],
                "history_slice_anchors": [
                    anchor for _start, _stop, anchor in intervals
                ],
                "processed_commit_count": processed,
                "source_scan_complete": complete,
            }
        )
    return output


def _git_output(checkout: Path, *args: str) -> str:
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "diff.external=",
            "-C",
            str(checkout),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    return completed.stdout.strip()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _git_object_sha1(object_type: str, content: bytes) -> str:
    header = f"{object_type} {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _git_proof_public_metadata_review(
    objects: Mapping[str, tuple[str, bytes]],
    *,
    scanner_revision: str = PUBLIC_SCANNER_REVISION,
) -> dict[str, Any]:
    if scanner_revision not in {"v2", PUBLIC_SCANNER_REVISION}:
        raise ValueError("Git proof public scanner revision is invalid")
    message_email_count = 0
    header_email_count = 0
    message_decode_replacement_count = 0
    header_decode_replacement_count = 0
    blob_email_count = 0
    blob_decode_replacement_count = 0
    tree_name_email_count = 0
    tree_name_decode_replacement_count = 0
    commit_count = 0
    tree_count = 0
    blob_count = 0
    for object_type, content in objects.values():
        if object_type == "tree":
            tree_count += 1
            for name in _tree_entries(content):
                tree_name_decode_replacement_count += name.count("\ufffd")
                _sanitized_name, redactions = sanitize_public_text(name)
                tree_name_email_count += len(redactions)
            continue
        if object_type == "blob":
            blob_count += 1
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(
                    "Git proof bound blob is not auditable UTF-8 text"
                ) from error
            if text.startswith("\ufeff") or any(
                ord(character) < 32 and character not in "\n\r\t" for character in text
            ):
                raise ValueError("Git proof bound blob is not auditable text")
            _sanitized_blob, redactions = sanitize_public_text(text)
            blob_email_count += len(redactions)
            continue
        if object_type != "commit":  # pragma: no cover - closed object contract
            continue
        commit_count += 1
        if any(byte < 32 and byte not in {9, 10, 13} for byte in content):
            raise ValueError("Git proof commit metadata is not auditable text")
        try:
            header_bytes, message_bytes = content.split(b"\n\n", 1)
        except ValueError as error:
            raise ValueError("Git proof commit message is malformed") from error
        try:
            header = header_bytes.decode("utf-8")
            message = message_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(
                "Git proof commit metadata is not auditable UTF-8 text"
            ) from error
        _sanitized_header, header_redactions = sanitize_public_text(header)
        _sanitized_message, message_redactions = sanitize_public_text(message)
        header_email_count += len(header_redactions)
        message_email_count += len(message_redactions)
    return {
        "scope": "portable_public_git_commit_tree_license_objects",
        "author_committer_emails": "retained_public_git_metadata_not_training_text",
        "commit_messages": "credential_scanned_public_metadata",
        "commit_header_email_count": header_email_count,
        "commit_message_email_count": message_email_count,
        "commit_header_decode_replacement_count": header_decode_replacement_count,
        "commit_message_decode_replacement_count": message_decode_replacement_count,
        "proof_artifact_emails": "retained_public_git_metadata_not_training_text",
        "blob_email_count": blob_email_count,
        "tree_name_email_count": tree_name_email_count,
        "blob_decode_replacement_count": blob_decode_replacement_count,
        "tree_name_decode_replacement_count": tree_name_decode_replacement_count,
        "commit_count": commit_count,
        "tree_count": tree_count,
        "blob_count": blob_count,
        "scanner": PUBLIC_SCANNER,
        "scanner_revision": scanner_revision,
    }


def validate_git_object_proof_public_export_governance(
    payload: dict[str, Any],
) -> None:
    """Validate the narrow public-Git metadata exception from raw objects."""
    parser = payload.get("parser")
    binding = payload.get("license_binding")
    policy = binding.get("policy") if isinstance(binding, dict) else None
    proof = binding.get("object_path_proof") if isinstance(binding, dict) else None
    privacy_review = payload.get("privacy_review")
    if (
        payload.get("schema_version") != "longworld.git-history-source-manifest.v1"
        or payload.get("source_origin") != "real_public"
        or not isinstance(parser, dict)
        or parser.get("name") != "git_first_parent_patch"
        or parser.get("revision") != "v6"
        or parser.get("first_parent") is not True
        or not isinstance(binding, dict)
        or binding.get("schema_version") != LICENSE_BINDING_RECEIPT_SCHEMA
        or not isinstance(policy, dict)
        or not isinstance(proof, dict)
        or not isinstance(privacy_review, dict)
        or privacy_review.get("emails")
        != "redacted_training_text_public_git_metadata_retained"
    ):
        raise ValueError("public Git export governance contract is invalid")
    verify_git_object_path_proof(
        proof,
        expected_path=str(policy.get("approved_path") or ""),
        expected_commit_bindings=binding.get("commit_bindings") or [],
        expected_license_blobs=binding.get("observed_license_blobs"),
    )
    if payload.get("git_object_proof_privacy_review") != proof.get(
        "public_metadata_review"
    ):
        raise ValueError("public Git proof privacy review is unbound")

    # Reuse the generic authorization/policy/client gates without widening its
    # redacted-email contract for any other source family.
    from longworld.core import realworkflow

    generic_payload = dict(payload)
    generic_payload["privacy_review"] = {
        **privacy_review,
        "emails": "redacted",
        "scanner": realworkflow.PUBLIC_SCANNER,
        "scanner_revision": realworkflow.PUBLIC_SCANNER_REVISION,
    }
    realworkflow._validate_public_export_governance(generic_payload)


def _batch_git_objects(
    checkout: Path,
    object_ids: Sequence[str],
    *,
    maximum_total_raw_bytes: int,
) -> dict[str, tuple[str, bytes]]:
    unique_ids = list(dict.fromkeys(object_ids))
    if not unique_ids or any(_GIT_SHA.fullmatch(value) is None for value in unique_ids):
        raise ValueError("Git object request identity is invalid")
    if maximum_total_raw_bytes <= 0:
        raise ValueError("Git object request exceeds the portable proof limit")
    preflight = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(checkout),
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ],
        input=("\n".join(unique_ids) + "\n").encode(),
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    metadata = preflight.stdout.decode("ascii").splitlines()
    preflight_bytes = 0
    if len(metadata) != len(unique_ids):
        raise ValueError("Git object batch preflight is incomplete")
    for expected_id, line in zip(unique_ids, metadata, strict=True):
        fields = line.split()
        if (
            len(fields) != 3
            or fields[0] != expected_id
            or fields[1] not in {"commit", "tree", "blob"}
        ):
            raise ValueError("Git object batch preflight is invalid")
        try:
            size = int(fields[2])
        except ValueError as error:
            raise ValueError("Git object batch preflight is invalid") from error
        preflight_bytes += size
        if size < 0 or preflight_bytes > maximum_total_raw_bytes:
            raise ValueError("Git object request exceeds the portable proof limit")
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(checkout),
            "cat-file",
            "--batch",
        ],
        input=("\n".join(unique_ids) + "\n").encode(),
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    payload = completed.stdout
    cursor = 0
    objects: dict[str, tuple[str, bytes]] = {}
    for expected_id in unique_ids:
        header_end = payload.find(b"\n", cursor)
        if header_end < 0:
            raise ValueError("Git object batch is truncated")
        fields = payload[cursor:header_end].decode("ascii").split()
        if (
            len(fields) != 3
            or fields[0] != expected_id
            or fields[1] not in {"commit", "tree", "blob"}
        ):
            raise ValueError("Git object batch is invalid")
        try:
            size = int(fields[2])
        except ValueError as error:
            raise ValueError("Git object batch is invalid") from error
        content_start = header_end + 1
        content_end = content_start + size
        if (
            size < 0
            or content_end >= len(payload)
            or payload[content_end : content_end + 1] != b"\n"
        ):
            raise ValueError("Git object batch is truncated")
        content = payload[content_start:content_end]
        if _git_object_sha1(fields[1], content) != expected_id:
            raise ValueError("Git object batch returned an invalid identity")
        objects[expected_id] = (fields[1], content)
        cursor = content_end + 1
    if cursor != len(payload):
        raise ValueError("Git object batch has trailing data")
    return objects


def _commit_header(content: bytes) -> tuple[str, tuple[str, ...]]:
    try:
        header = content.split(b"\n\n", 1)[0]
        tree_values = [
            line.removeprefix(b"tree ")
            for line in header.splitlines()
            if line.startswith(b"tree ")
        ]
        parent_values = [
            line.removeprefix(b"parent ")
            for line in header.splitlines()
            if line.startswith(b"parent ")
        ]
    except (AttributeError, TypeError) as error:  # pragma: no cover - bytes contract
        raise ValueError("Git commit object is malformed") from error
    if len(tree_values) != 1:
        raise ValueError("Git commit object has no unique tree")
    try:
        tree_sha = tree_values[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("Git commit tree identity is invalid") from error
    if _GIT_SHA.fullmatch(tree_sha) is None:
        raise ValueError("Git commit tree identity is invalid")
    try:
        parents = tuple(value.decode("ascii") for value in parent_values)
    except UnicodeDecodeError as error:
        raise ValueError("Git commit parent identity is invalid") from error
    if any(_GIT_SHA.fullmatch(parent) is None for parent in parents):
        raise ValueError("Git commit parent identity is invalid")
    return tree_sha, parents


def _commit_tree_sha(content: bytes) -> str:
    return _commit_header(content)[0]


def _tree_entries(content: bytes) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    cursor = 0
    while cursor < len(content):
        space = content.find(b" ", cursor)
        nul = content.find(b"\0", space + 1)
        if space <= cursor or nul < 0 or nul + 21 > len(content):
            raise ValueError("Git tree object is malformed")
        try:
            mode = content[cursor:space].decode("ascii")
            name = content[space + 1 : nul].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Git tree object has an invalid path") from error
        object_id = content[nul + 1 : nul + 21].hex()
        if (
            not mode
            or not name
            or "/" in name
            or name in entries
            or _GIT_SHA.fullmatch(object_id) is None
        ):
            raise ValueError("Git tree object is malformed")
        entries[name] = (mode, object_id)
        cursor = nul + 21
    return entries


def _validated_git_path(path: str) -> tuple[str, ...]:
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or _GIT_PATH.fullmatch(path) is None
    ):
        raise ValueError("Git object proof path is invalid")
    return parsed.parts


def _walk_git_path(
    objects: Mapping[str, tuple[str, bytes]],
    commit_sha: str,
    parts: Sequence[str],
    *,
    visited: set[str] | None = None,
) -> str:
    commit = objects.get(commit_sha)
    if commit is None or commit[0] != "commit":
        raise ValueError("Git object proof commit is missing")
    if visited is not None:
        visited.add(commit_sha)
    current_sha = _commit_tree_sha(commit[1])
    for index, part in enumerate(parts):
        tree = objects.get(current_sha)
        if tree is None or tree[0] != "tree":
            raise ValueError("Git object proof tree is missing")
        if visited is not None:
            visited.add(current_sha)
        entry = _tree_entries(tree[1]).get(part)
        if entry is None:
            raise ValueError("Git object proof tree path is missing")
        mode, child_sha = entry
        final = index == len(parts) - 1
        child = objects.get(child_sha)
        if child is None:
            raise ValueError("Git object proof path object is missing")
        if final:
            if mode in {"40000", "040000"} or child[0] != "blob":
                raise ValueError("Git object proof path does not resolve to a blob")
            if visited is not None:
                visited.add(child_sha)
            return child_sha
        if mode not in {"40000", "040000"} or child[0] != "tree":
            raise ValueError("Git object proof path component is not a tree")
        current_sha = child_sha
    raise ValueError("Git object proof path is empty")  # pragma: no cover


def build_git_object_path_proof(
    checkout: Path,
    *,
    commit_revisions: Sequence[str],
    path: str,
) -> dict[str, Any]:
    """Build a portable proof of commit -> tree path -> blob identities."""
    parts = _validated_git_path(path)
    revisions = list(commit_revisions)
    if (
        not revisions
        or len(revisions) != len(set(revisions))
        or any(_GIT_SHA.fullmatch(value) is None for value in revisions)
    ):
        raise ValueError("Git object proof commit history is invalid")
    if not checkout.is_dir():
        raise ValueError("Git object proof checkout is unavailable")

    objects = _batch_git_objects(
        checkout,
        revisions,
        maximum_total_raw_bytes=_MAX_GIT_PROOF_RAW_BYTES,
    )
    current = [_commit_tree_sha(objects[revision][1]) for revision in revisions]
    for index, part in enumerate(parts):
        fetched = _batch_git_objects(
            checkout,
            current,
            maximum_total_raw_bytes=_MAX_GIT_PROOF_RAW_BYTES
            - sum(len(content) for _kind, content in objects.values()),
        )
        objects.update(fetched)
        children: list[str] = []
        for tree_sha in current:
            tree_type, tree_content = objects[tree_sha]
            if tree_type != "tree":
                raise ValueError("Git object proof path component is not a tree")
            entry = _tree_entries(tree_content).get(part)
            if entry is None:
                raise ValueError("Git object proof tree path is missing")
            mode, child_sha = entry
            final = index == len(parts) - 1
            if final == (mode in {"40000", "040000"}):
                raise ValueError("Git object proof path has an invalid object type")
            children.append(child_sha)
        current = children
    objects.update(
        _batch_git_objects(
            checkout,
            current,
            maximum_total_raw_bytes=_MAX_GIT_PROOF_RAW_BYTES
            - sum(len(content) for _kind, content in objects.values()),
        )
    )
    commit_bindings = [
        {"revision": revision, "git_blob_sha": blob_sha}
        for revision, blob_sha in zip(revisions, current, strict=True)
    ]
    for binding in commit_bindings:
        if (
            _walk_git_path(objects, binding["revision"], parts)
            != binding["git_blob_sha"]
        ):
            raise ValueError("Git object proof tree path does not bind the blob")
    for older, newer in pairwise(revisions):
        _tree, parents = _commit_header(objects[newer][1])
        if not parents or parents[0] != older:
            raise ValueError("Git object proof is not a first-parent history")
    serialized_objects: list[dict[str, Any]] = [
        {
            "oid": object_id,
            "type": object_type,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        for object_id, (object_type, content) in sorted(objects.items())
    ]
    total_raw_bytes = sum(int(item["size"]) for item in serialized_objects)
    if (
        len(serialized_objects) > _MAX_GIT_PROOF_OBJECTS
        or total_raw_bytes > _MAX_GIT_PROOF_RAW_BYTES
    ):
        raise ValueError("Git object proof exceeds the portable proof limit")
    proof: dict[str, Any] = {
        "schema_version": GIT_OBJECT_PATH_PROOF_SCHEMA,
        "hash_algorithm": "sha1",
        "path": path,
        "commit_bindings": commit_bindings,
        "objects": serialized_objects,
        "object_count": len(serialized_objects),
        "total_raw_bytes": total_raw_bytes,
        "public_metadata_review": _git_proof_public_metadata_review(objects),
    }
    proof["proof_sha256"] = hashlib.sha256(_canonical_bytes(proof)).hexdigest()
    return proof


def verify_git_object_path_proof(
    proof: object,
    *,
    expected_path: str,
    expected_commit_bindings: Sequence[Mapping[str, str]],
    expected_license_blobs: Sequence[Mapping[str, str | int]] | None = None,
) -> dict[str, int]:
    """Independently verify raw Git objects and every path traversal."""
    parts = _validated_git_path(expected_path)
    fields = {
        "schema_version",
        "hash_algorithm",
        "path",
        "commit_bindings",
        "objects",
        "object_count",
        "total_raw_bytes",
        "public_metadata_review",
        "proof_sha256",
    }
    if not isinstance(proof, dict) or set(proof) != fields:
        raise ValueError("Git object path proof shape is invalid")
    unsigned = {key: value for key, value in proof.items() if key != "proof_sha256"}
    if (
        proof.get("schema_version") != GIT_OBJECT_PATH_PROOF_SCHEMA
        or proof.get("hash_algorithm") != "sha1"
        or proof.get("path") != expected_path
        or proof.get("proof_sha256")
        != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    ):
        raise ValueError("Git object path proof identity is invalid")
    expected_bindings = [dict(item) for item in expected_commit_bindings]
    if proof.get("commit_bindings") != expected_bindings or not expected_bindings:
        raise ValueError("Git object path proof commit bindings do not match")
    raw_objects = proof.get("objects")
    if not isinstance(raw_objects, list) or not raw_objects:
        raise ValueError("Git object path proof object set is invalid")
    if (
        isinstance(proof.get("object_count"), bool)
        or not isinstance(proof.get("object_count"), int)
        or proof["object_count"] != len(raw_objects)
        or proof["object_count"] > _MAX_GIT_PROOF_OBJECTS
    ):
        raise ValueError("Git object path proof object count is invalid")
    objects: dict[str, tuple[str, bytes]] = {}
    total_raw_bytes = 0
    for item in raw_objects:
        if not isinstance(item, dict) or set(item) != {
            "oid",
            "type",
            "size",
            "sha256",
            "content_base64",
        }:
            raise ValueError("Git object path proof object is invalid")
        object_id = item.get("oid")
        object_type = item.get("type")
        size = item.get("size")
        encoded = item.get("content_base64")
        if (
            not isinstance(object_id, str)
            or _GIT_SHA.fullmatch(object_id) is None
            or object_id in objects
            or object_type not in {"commit", "tree", "blob"}
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(encoded, str)
        ):
            raise ValueError("Git object path proof object is invalid")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError(
                "Git object path proof object encoding is invalid"
            ) from error
        if (
            base64.b64encode(content).decode("ascii") != encoded
            or len(content) != size
            or hashlib.sha256(content).hexdigest() != item.get("sha256")
            or _git_object_sha1(str(object_type), content) != object_id
        ):
            raise ValueError("Git object identity does not replay")
        total_raw_bytes += len(content)
        if total_raw_bytes > _MAX_GIT_PROOF_RAW_BYTES:
            raise ValueError("Git object path proof exceeds the portable proof limit")
        objects[object_id] = (str(object_type), content)
    if proof.get("total_raw_bytes") != total_raw_bytes:
        raise ValueError("Git object path proof byte count is invalid")
    public_metadata_review = proof.get("public_metadata_review")
    proof_scanner_revision = (
        public_metadata_review.get("scanner_revision")
        if isinstance(public_metadata_review, dict)
        else ""
    )
    if public_metadata_review != _git_proof_public_metadata_review(
        objects, scanner_revision=str(proof_scanner_revision)
    ):
        raise ValueError("Git object path proof public metadata review is invalid")
    observed_blobs: set[str] = set()
    reachable_objects: set[str] = set()
    revisions: list[str] = []
    for binding in expected_bindings:
        if set(binding) != {"revision", "git_blob_sha"}:
            raise ValueError("Git object path proof commit binding is invalid")
        revision = binding.get("revision")
        blob_sha = binding.get("git_blob_sha")
        if (
            not isinstance(revision, str)
            or _GIT_SHA.fullmatch(revision) is None
            or not isinstance(blob_sha, str)
            or _GIT_SHA.fullmatch(blob_sha) is None
            or _walk_git_path(objects, revision, parts, visited=reachable_objects)
            != blob_sha
        ):
            raise ValueError("Git object proof tree path does not bind the blob")
        revisions.append(revision)
        observed_blobs.add(blob_sha)
    for older, newer in pairwise(revisions):
        _tree, parents = _commit_header(objects[newer][1])
        if not parents or parents[0] != older:
            raise ValueError("Git object proof is not a first-parent history")
    if reachable_objects != set(objects):
        raise ValueError("Git object proof is not the exact reachable closure")
    if expected_license_blobs is not None:
        approved: dict[str, tuple[str, int]] = {}
        for item in expected_license_blobs:
            if not isinstance(item, Mapping) or set(item) != {
                "git_blob_sha",
                "sha256",
                "size",
            }:
                raise ValueError("Git object proof approved metadata is invalid")
            oid = item.get("git_blob_sha")
            sha256 = item.get("sha256")
            size = item.get("size")
            if (
                not isinstance(oid, str)
                or oid in approved
                or _GIT_SHA.fullmatch(oid) is None
                or not isinstance(sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
            ):
                raise ValueError("Git object proof approved metadata is invalid")
            approved[oid] = (sha256, size)
        if set(approved) != observed_blobs:
            raise ValueError("Git object proof approved metadata does not match")
        for oid, (sha256, size) in approved.items():
            object_type, content = objects[oid]
            if (
                object_type != "blob"
                or len(content) != size
                or hashlib.sha256(content).hexdigest() != sha256
            ):
                raise ValueError("Git object proof approved metadata does not match")
    return {
        "commit_count": len(expected_bindings),
        "object_count": len(objects),
        "total_raw_bytes": total_raw_bytes,
        "unique_blob_count": len(observed_blobs),
    }


def git_object_path_proof_history_anchor(proof: object) -> dict[str, str | None]:
    """Extract a verified proof's chronological boundary identities."""
    if not isinstance(proof, dict):
        raise TypeError("Git object path proof is invalid")
    bindings = proof.get("commit_bindings")
    raw_objects = proof.get("objects")
    if (
        not isinstance(bindings, list)
        or not bindings
        or not isinstance(raw_objects, list)
    ):
        raise ValueError("Git object path proof history is invalid")
    oldest = bindings[0].get("revision") if isinstance(bindings[0], dict) else None
    newest = bindings[-1].get("revision") if isinstance(bindings[-1], dict) else None
    if (
        not isinstance(oldest, str)
        or _GIT_SHA.fullmatch(oldest) is None
        or not isinstance(newest, str)
        or _GIT_SHA.fullmatch(newest) is None
    ):
        raise ValueError("Git object path proof history is invalid")
    matches = [item for item in raw_objects if item.get("oid") == oldest]
    if len(matches) != 1 or matches[0].get("type") != "commit":
        raise ValueError("Git object path proof oldest commit is missing")
    try:
        content = base64.b64decode(matches[0]["content_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Git object path proof oldest commit is invalid") from error
    if _git_object_sha1("commit", content) != oldest:
        raise ValueError("Git object path proof oldest commit is invalid")
    _tree, parents = _commit_header(content)
    return {
        "oldest_revision": oldest,
        "newest_revision": newest,
        "oldest_first_parent_revision": parents[0] if parents else None,
    }


def build_git_object_path_absence_proof(
    checkout: Path, *, commit_revision: str, path: str
) -> dict[str, Any]:
    """Build a portable commit/tree proof that one path component is absent."""
    parts = _validated_git_path(path)
    if _GIT_SHA.fullmatch(commit_revision) is None or not checkout.is_dir():
        raise ValueError("Git object absence proof commit is invalid")
    objects = _batch_git_objects(
        checkout,
        [commit_revision],
        maximum_total_raw_bytes=_MAX_GIT_PROOF_RAW_BYTES,
    )
    current_sha = _commit_tree_sha(objects[commit_revision][1])
    missing_index: int | None = None
    for index, part in enumerate(parts):
        fetched = _batch_git_objects(
            checkout,
            [current_sha],
            maximum_total_raw_bytes=_MAX_GIT_PROOF_RAW_BYTES
            - sum(len(content) for _kind, content in objects.values()),
        )
        objects.update(fetched)
        tree_type, tree_content = fetched[current_sha]
        if tree_type != "tree":
            raise ValueError("Git object absence proof path component is not a tree")
        entry = _tree_entries(tree_content).get(part)
        if entry is None:
            missing_index = index
            break
        mode, child_sha = entry
        if index == len(parts) - 1 or mode not in {"40000", "040000"}:
            raise ValueError("Git object absence proof path exists")
        current_sha = child_sha
    if missing_index is None:
        raise ValueError("Git object absence proof path exists")
    serialized_objects = [
        {
            "oid": object_id,
            "type": object_type,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        for object_id, (object_type, content) in sorted(objects.items())
    ]
    proof: dict[str, Any] = {
        "schema_version": GIT_OBJECT_PATH_ABSENCE_PROOF_SCHEMA,
        "hash_algorithm": "sha1",
        "commit_revision": commit_revision,
        "path": path,
        "missing_component_index": missing_index,
        "missing_component": parts[missing_index],
        "objects": serialized_objects,
        "object_count": len(serialized_objects),
        "total_raw_bytes": sum(item["size"] for item in serialized_objects),
        "public_metadata_review": _git_proof_public_metadata_review(objects),
    }
    proof["proof_sha256"] = hashlib.sha256(_canonical_bytes(proof)).hexdigest()
    return proof


def verify_git_object_path_absence_proof(
    proof: object, *, expected_commit_revision: str, expected_path: str
) -> dict[str, int]:
    """Independently verify raw commit/tree objects prove a path is absent."""
    fields = {
        "schema_version",
        "hash_algorithm",
        "commit_revision",
        "path",
        "missing_component_index",
        "missing_component",
        "objects",
        "object_count",
        "total_raw_bytes",
        "public_metadata_review",
        "proof_sha256",
    }
    parts = _validated_git_path(expected_path)
    if not isinstance(proof, dict) or set(proof) != fields:
        raise ValueError("Git object absence proof shape is invalid")
    unsigned = {key: value for key, value in proof.items() if key != "proof_sha256"}
    if (
        proof.get("schema_version") != GIT_OBJECT_PATH_ABSENCE_PROOF_SCHEMA
        or proof.get("hash_algorithm") != "sha1"
        or proof.get("commit_revision") != expected_commit_revision
        or proof.get("path") != expected_path
        or proof.get("proof_sha256")
        != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    ):
        raise ValueError("Git object absence proof identity is invalid")
    raw_objects = proof.get("objects")
    if not isinstance(raw_objects, list) or not raw_objects:
        raise ValueError("Git object absence proof objects are invalid")
    objects: dict[str, tuple[str, bytes]] = {}
    total = 0
    for item in raw_objects:
        if not isinstance(item, dict) or set(item) != {
            "oid",
            "type",
            "size",
            "sha256",
            "content_base64",
        }:
            raise ValueError("Git object absence proof object is invalid")
        oid = item.get("oid")
        object_type = item.get("type")
        size = item.get("size")
        encoded = item.get("content_base64")
        if not isinstance(encoded, str):
            raise TypeError("Git object absence proof object is invalid")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (TypeError, ValueError) as error:
            raise ValueError("Git object absence proof object is invalid") from error
        if (
            not isinstance(oid, str)
            or oid in objects
            or _GIT_SHA.fullmatch(oid) is None
            or object_type not in {"commit", "tree"}
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or len(content) != size
            or hashlib.sha256(content).hexdigest() != item.get("sha256")
            or _git_object_sha1(str(object_type), content) != oid
        ):
            raise ValueError("Git object absence proof object is invalid")
        objects[oid] = (str(object_type), content)
        total += len(content)
    if (
        proof.get("object_count") != len(objects)
        or proof.get("total_raw_bytes") != total
        or len(objects) > _MAX_GIT_PROOF_OBJECTS
        or total > _MAX_GIT_PROOF_RAW_BYTES
    ):
        raise ValueError("Git object absence proof size is invalid")
    public_metadata_review = proof.get("public_metadata_review")
    proof_scanner_revision = (
        public_metadata_review.get("scanner_revision")
        if isinstance(public_metadata_review, dict)
        else ""
    )
    if public_metadata_review != _git_proof_public_metadata_review(
        objects, scanner_revision=str(proof_scanner_revision)
    ):
        raise ValueError("Git object absence proof public metadata review is invalid")
    commit = objects.get(expected_commit_revision)
    if commit is None or commit[0] != "commit":
        raise ValueError("Git object absence proof commit is missing")
    visited = {expected_commit_revision}
    current_sha = _commit_tree_sha(commit[1])
    missing_index: int | None = None
    for index, part in enumerate(parts):
        tree = objects.get(current_sha)
        if tree is None or tree[0] != "tree":
            raise ValueError("Git object absence proof tree is missing")
        visited.add(current_sha)
        entry = _tree_entries(tree[1]).get(part)
        if entry is None:
            missing_index = index
            break
        mode, child_sha = entry
        if index == len(parts) - 1 or mode not in {"40000", "040000"}:
            raise ValueError("Git object absence proof path exists")
        current_sha = child_sha
    if (
        missing_index is None
        or proof.get("missing_component_index") != missing_index
        or proof.get("missing_component") != parts[missing_index]
        or visited != set(objects)
    ):
        raise ValueError("Git object absence proof does not prove exact absence")
    return {"object_count": len(objects), "total_raw_bytes": total}


def _normalized_license_policy(policy: object) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != {
        "schema_version",
        "approved_path",
        "approved_blobs",
    }:
        raise ValueError("Git license binding policy is invalid")
    path = policy.get("approved_path")
    if not isinstance(path, str):
        raise TypeError("Git license binding policy is invalid")
    parsed_path = PurePosixPath(path)
    if (
        policy.get("schema_version") != LICENSE_BINDING_POLICY_SCHEMA
        or parsed_path.is_absolute()
        or not parsed_path.parts
        or any(part in {"", ".", ".."} for part in parsed_path.parts)
        or _GIT_PATH.fullmatch(path) is None
    ):
        raise ValueError("Git license binding policy is invalid")
    raw_blobs = policy.get("approved_blobs")
    if not isinstance(raw_blobs, list) or not raw_blobs:
        raise ValueError("Git license binding policy has no approved blobs")
    approved_blobs: list[dict[str, str | int]] = []
    seen_git_blobs: set[str] = set()
    for item in raw_blobs:
        if not isinstance(item, dict) or set(item) != {
            "git_blob_sha",
            "sha256",
            "size",
        }:
            raise ValueError("Git license binding policy blob is invalid")
        git_blob_sha = item.get("git_blob_sha")
        sha256 = item.get("sha256")
        size = item.get("size")
        if (
            not isinstance(git_blob_sha, str)
            or _GIT_SHA.fullmatch(git_blob_sha) is None
            or git_blob_sha in seen_git_blobs
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
        ):
            raise ValueError("Git license binding policy blob is invalid")
        seen_git_blobs.add(git_blob_sha)
        approved_blobs.append(
            {"git_blob_sha": git_blob_sha, "sha256": sha256, "size": size}
        )
    return {
        "schema_version": LICENSE_BINDING_POLICY_SCHEMA,
        "approved_path": str(parsed_path),
        "approved_blobs": approved_blobs,
    }


def _batch_object_contents(checkout: Path, object_ids: list[str]) -> dict[str, bytes]:
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(checkout),
            "cat-file",
            "--batch",
        ],
        input=("\n".join(object_ids) + "\n").encode(),
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    payload = completed.stdout
    cursor = 0
    contents: dict[str, bytes] = {}
    for expected_id in object_ids:
        header_end = payload.find(b"\n", cursor)
        if header_end < 0:
            raise ValueError("Git license blob batch is truncated")
        fields = payload[cursor:header_end].decode("ascii").split()
        if len(fields) != 3 or fields[0] != expected_id or fields[1] != "blob":
            raise ValueError("Git license blob batch is invalid")
        try:
            size = int(fields[2])
        except ValueError as error:
            raise ValueError("Git license blob batch is invalid") from error
        content_start = header_end + 1
        content_end = content_start + size
        if (
            content_end >= len(payload)
            or payload[content_end : content_end + 1] != b"\n"
        ):
            raise ValueError("Git license blob batch is truncated")
        contents[expected_id] = payload[content_start:content_end]
        cursor = content_end + 1
    if cursor != len(payload):
        raise ValueError("Git license blob batch has trailing data")
    return contents


def bind_git_license_history(
    checkout: Path,
    commit_revisions: tuple[str, ...],
    policy: object,
) -> dict[str, Any]:
    """Bind every selected commit to an independently approved license blob."""
    normalized_policy = _normalized_license_policy(policy)
    if not commit_revisions or any(
        _GIT_SHA.fullmatch(revision) is None for revision in commit_revisions
    ):
        raise ValueError("Git license binding commit history is invalid")
    path = str(normalized_policy["approved_path"])
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(checkout),
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ],
        input="".join(f"{revision}:{path}\n" for revision in commit_revisions),
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    lines = completed.stdout.splitlines()
    if len(lines) != len(commit_revisions):
        raise ValueError("Git license binding batch is incomplete")
    commit_bindings: list[dict[str, str]] = []
    blob_sizes: dict[str, int] = {}
    for revision, line in zip(commit_revisions, lines, strict=True):
        fields = line.split()
        if len(fields) != 3 or fields[1] != "blob":
            raise ValueError(
                f"Git license path is missing at selected commit: {revision}"
            )
        git_blob_sha, _, raw_size = fields
        if _GIT_SHA.fullmatch(git_blob_sha) is None:
            raise ValueError("Git license binding returned an invalid blob identity")
        try:
            size = int(raw_size)
        except ValueError as error:
            raise ValueError(
                "Git license binding returned an invalid blob size"
            ) from error
        if size <= 0:
            raise ValueError("Git license binding returned an empty license")
        prior_size = blob_sizes.setdefault(git_blob_sha, size)
        if prior_size != size:
            raise ValueError("Git license blob size is inconsistent")
        commit_bindings.append({"revision": revision, "git_blob_sha": git_blob_sha})
    object_ids = sorted(blob_sizes)
    contents = _batch_object_contents(checkout, object_ids)
    observed_blobs: list[dict[str, str | int]] = [
        {
            "git_blob_sha": object_id,
            "sha256": hashlib.sha256(contents[object_id]).hexdigest(),
            "size": len(contents[object_id]),
        }
        for object_id in object_ids
    ]
    approved = {
        (
            str(item["git_blob_sha"]),
            str(item["sha256"]),
            int(item["size"]),
        )
        for item in normalized_policy["approved_blobs"]
    }
    if any(
        (item["git_blob_sha"], item["sha256"], item["size"]) not in approved
        for item in observed_blobs
    ):
        raise ValueError("Git license history contains a blob not approved by policy")
    transition_count = sum(
        left["git_blob_sha"] != right["git_blob_sha"]
        for left, right in pairwise(commit_bindings)
    )
    object_path_proof = build_git_object_path_proof(
        checkout,
        commit_revisions=commit_revisions,
        path=path,
    )
    verify_git_object_path_proof(
        object_path_proof,
        expected_path=path,
        expected_commit_bindings=commit_bindings,
        expected_license_blobs=observed_blobs,
    )
    return {
        "schema_version": LICENSE_BINDING_RECEIPT_SCHEMA,
        "policy": normalized_policy,
        "policy_sha256": hashlib.sha256(
            _canonical_bytes(normalized_policy)
        ).hexdigest(),
        "selected_commit_count": len(commit_bindings),
        "selected_first_revision": commit_bindings[0]["revision"],
        "selected_last_revision": commit_bindings[-1]["revision"],
        "commit_bindings": commit_bindings,
        "commit_bindings_sha256": hashlib.sha256(
            _canonical_bytes(commit_bindings)
        ).hexdigest(),
        "observed_license_blobs": observed_blobs,
        "transition_count": transition_count,
        "object_path_proof": object_path_proof,
    }


def _history_segments(
    checkout: Path,
    max_commits: int,
    skip_commits: int,
    root_revision: str,
) -> list[str]:
    pretty = "%x00LONGWORLD_RECORD%x00%H%x00%P%x00%cI%x00%B%x00LONGWORLD_PATCH%x00"
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "diff.external=",
            "-C",
            str(checkout),
            "log",
            "--first-parent",
            "--reverse",
            f"--max-count={max_commits}",
            f"--skip={skip_commits}",
            "--patch",
            "--no-ext-diff",
            "--no-color",
            "--no-renames",
            f"--format={pretty}",
            root_revision,
        ],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    return [
        segment
        for segment in completed.stdout.split(_RECORD_SEPARATOR)
        if segment.strip()
    ]


def _token_offsets(tokenizer: OffsetTokenizer, text: str) -> list[tuple[int, int]]:
    encoded = tokenizer(
        text,
        add_special_tokens=False,
        return_offsets_mapping=True,
    )
    raw = encoded.get("offset_mapping")
    if not isinstance(raw, list):
        raise TypeError("exact tokenizer did not return character offsets")
    offsets: list[tuple[int, int]] = []
    for item in raw:
        if (
            not isinstance(item, (list, tuple))
            or len(item) != 2
            or not all(isinstance(value, int) for value in item)
        ):
            raise ValueError("exact tokenizer returned invalid character offsets")
        start, end = item
        if start < 0 or end < start or end > len(text):
            raise ValueError("exact tokenizer returned out-of-range offsets")
        if end > start:
            offsets.append((start, end))
    return offsets


def _split_exact_source_text(
    text: str, tokenizer: OffsetTokenizer, max_record_tokens: int
) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    segment_start = 0
    while segment_start < len(text):
        segment_end = min(segment_start + _MAX_TOKENIZER_CHARS, len(text))
        if segment_end < len(text):
            newline = text.rfind("\n", segment_start, segment_end)
            if newline > segment_start:
                segment_end = newline + 1
        segment = text[segment_start:segment_end]
        offsets = _token_offsets(tokenizer, segment)
        if not offsets:
            if chunks:
                start, _, previous = chunks[-1]
                chunks[-1] = (start, segment_end, previous + segment)
            segment_start = segment_end
            continue
        local_start = 0
        for token_start in range(0, len(offsets), max_record_tokens):
            token_end = min(token_start + max_record_tokens, len(offsets))
            local_end = (
                len(segment) if token_end == len(offsets) else offsets[token_end - 1][1]
            )
            chunk = segment[local_start:local_end]
            if chunk.strip():
                chunks.append(
                    (
                        segment_start + local_start,
                        segment_start + local_end,
                        chunk,
                    )
                )
            elif chunks:
                start, _, previous = chunks[-1]
                chunks[-1] = (
                    start,
                    segment_start + local_end,
                    previous + chunk,
                )
            local_start = local_end
        segment_start = segment_end
    return chunks


def extract_first_parent_history(
    checkout: Path,
    *,
    repository: str,
    tokenizer: OffsetTokenizer,
    max_commits: int,
    skip_commits: int = 0,
    root_revision: str | None = None,
    max_record_tokens: int = 768,
    max_chunks_per_commit: int = 0,
) -> GitHistoryExtraction:
    """Extract chronological commit messages and patches without inventing edges."""
    if _REPOSITORY.fullmatch(repository) is None:
        raise ValueError("GitHub repository identity is invalid")
    if max_commits <= 0 or max_commits > _MAX_COMMITS:
        raise ValueError("Git history commit limit is invalid")
    if skip_commits < 0 or skip_commits > _MAX_COMMITS:
        raise ValueError("Git history commit offset is invalid")
    if max_record_tokens <= 0:
        raise ValueError("Git history record token limit is invalid")
    if max_chunks_per_commit < 0 or max_chunks_per_commit > 10_000:
        raise ValueError("Git history commit chunk cap is invalid")
    if not checkout.is_dir():
        raise ValueError("Git history checkout is unavailable")
    selected_root = root_revision or _git_output(checkout, "rev-parse", "HEAD")
    if _GIT_SHA.fullmatch(selected_root) is None:
        raise ValueError("Git history checkout has no full HEAD revision")
    if _git_output(checkout, "cat-file", "-t", selected_root) != "commit":
        raise ValueError("Git history root revision is not a commit")

    segments = _history_segments(checkout, max_commits, skip_commits, selected_root)
    records: list[WorkflowRecord] = []
    rejects: Counter[str] = Counter()
    truncated_commits: list[GitCommitTruncation] = []
    commit_revisions: list[str] = []
    previous_sha = ""
    previous_record_id = ""
    for segment in segments:
        fields = segment.split(_FIELD_SEPARATOR, 4)
        if len(fields) != 5:
            raise ValueError("Git history stream has an invalid record boundary")
        sha, raw_parents, occurred_at, message, patch_payload = fields
        if not patch_payload.startswith(_PATCH_PREFIX):
            raise ValueError("Git history stream has an invalid patch boundary")
        patch = patch_payload.removeprefix(_PATCH_PREFIX)
        sha = sha.strip()
        raw_parents = raw_parents.strip()
        occurred_at = occurred_at.strip()
        if _GIT_SHA.fullmatch(sha) is None or any(
            _GIT_SHA.fullmatch(parent) is None for parent in raw_parents.split()
        ):
            raise ValueError("Git history stream has an invalid object identity")
        commit_revisions.append(sha)
        try:
            timestamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Git history stream has an invalid timestamp") from error
        if timestamp.tzinfo is None:
            raise ValueError("Git history stream timestamp lacks a timezone")
        parents = raw_parents.split()
        if previous_sha and (not parents or parents[0] != previous_sha):
            raise ValueError("Git history stream is not a first-parent chain")
        previous_sha = sha
        source_text = f"commit {sha}\n{message.strip()}\n\n{patch.lstrip()}".strip()
        try:
            clean_text, redactions = sanitize_public_text(source_text)
        except ValueError:
            rejects["credential_shaped_commit"] += 1
            previous_record_id = ""
            continue
        chunks = _split_exact_source_text(clean_text, tokenizer, max_record_tokens)
        if not chunks:
            rejects["empty_commit"] += 1
            previous_record_id = ""
            continue
        total_chunk_count = len(chunks)
        if max_chunks_per_commit and total_chunk_count > max_chunks_per_commit:
            rejects["commit_chunks_truncated"] += (
                total_chunk_count - max_chunks_per_commit
            )
            rejects["commits_truncated"] += 1
            chunks = chunks[:max_chunks_per_commit]
        emitted_chunk_count = len(chunks)
        if emitted_chunk_count < total_chunk_count:
            truncated_commits.append(
                GitCommitTruncation(
                    sha=sha,
                    total_chunk_count=total_chunk_count,
                    emitted_chunk_count=emitted_chunk_count,
                )
            )
        for chunk_index, (char_start, char_end, chunk) in enumerate(chunks):
            record_id = f"git:{repository}:commit:{sha}:chunk:{chunk_index:04d}"
            records.append(
                WorkflowRecord(
                    record_id=record_id,
                    kind="commit",
                    occurred_at=occurred_at,
                    text=chunk,
                    links=(previous_record_id,) if previous_record_id else (),
                    attributes={
                        "sha": sha,
                        "parents": parents,
                        "chunk_index": chunk_index,
                        "chunk_count": emitted_chunk_count,
                        "commit_chunk_count_total": total_chunk_count,
                        "commit_chunk_count_emitted": emitted_chunk_count,
                        "commit_was_truncated": (
                            emitted_chunk_count < total_chunk_count
                        ),
                        "source_char_start": char_start,
                        "source_char_end": char_end,
                        "redactions": redactions,
                    },
                    source_pointer=(
                        f"https://github.com/{repository}/commit/{sha}"
                        f"#source-char={char_start}-{char_end}"
                    ),
                )
            )
            previous_record_id = record_id
    if not commit_revisions:
        raise ValueError("Git history selected slice is empty")
    return GitHistoryExtraction(
        records=tuple(records),
        commit_count=len(segments),
        head_revision=commit_revisions[-1],
        reject_reasons=dict(rejects),
        truncated_commits=tuple(truncated_commits),
        commit_revisions=tuple(commit_revisions),
    )
