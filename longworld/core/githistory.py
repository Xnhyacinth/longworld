"""Read source-native, first-parent histories from verified local Git clones."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from longworld.core.publicscan import sanitize_public_text
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
LICENSE_BINDING_RECEIPT_SCHEMA = "longworld.git-license-binding-receipt.v2"


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


def _normalized_license_policy(policy: object) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != {
        "schema_version",
        "approved_path",
        "approved_blobs",
    }:
        raise ValueError("Git license binding policy is invalid")
    path = policy.get("approved_path")
    parsed_path = PurePosixPath(path) if isinstance(path, str) else None
    if (
        policy.get("schema_version") != LICENSE_BINDING_POLICY_SCHEMA
        or parsed_path is None
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
    observed_blobs = [
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
