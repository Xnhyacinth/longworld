"""Read source-native, first-parent histories from verified local Git clones."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from longworld.core.publicscan import sanitize_public_text
from longworld.core.realworkflow import WorkflowRecord

_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RECORD_SEPARATOR = "\x00LONGWORLD_RECORD\x00"
_FIELD_SEPARATOR = "\x00"
_PATCH_PREFIX = "LONGWORLD_PATCH\x00"
_MAX_COMMITS = 100_000
_MAX_TOKENIZER_CHARS = 128_000
TOKEN_COUNT_CACHE_REVISION = "sha256-text+tokenizer-asset-v1"
TRUNCATION_QUALITY_REVISION = "git-observed-prefix-truncation-v1"


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


def _history_segments(checkout: Path, max_commits: int, skip_commits: int) -> list[str]:
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
            "HEAD",
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
    head_revision = _git_output(checkout, "rev-parse", "HEAD")
    if _GIT_SHA.fullmatch(head_revision) is None:
        raise ValueError("Git history checkout has no full HEAD revision")

    segments = _history_segments(checkout, max_commits, skip_commits)
    records: list[WorkflowRecord] = []
    rejects: Counter[str] = Counter()
    truncated_commits: list[GitCommitTruncation] = []
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
    return GitHistoryExtraction(
        records=tuple(records),
        commit_count=len(segments),
        head_revision=head_revision,
        reject_reasons=dict(rejects),
        truncated_commits=tuple(truncated_commits),
    )
