"""Small candidate-only contract for native long-context reader adapters.

Adapters may supply metadata missing from older indexes, but every candidate
must be bound to a real native receipt and the final model-visible reader row.
This module checks identities and consistency; it neither promotes samples nor
claims that a verified token distance proves global information necessity.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
LENGTH_BINS = (
    (32768, "lt32k"),
    (65536, "32k"),
    (131072, "64k"),
    (262144, "128k"),
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _index_value(index: dict[str, Any], *names: str) -> Any:
    values = [index[name] for name in names if index.get(name) is not None]
    if values and any(value != values[0] for value in values[1:]):
        raise ValueError(f"conflicting aliases: {', '.join(names)}")
    return values[0] if values else None


def physical_length_bin(full_tokens: int) -> str:
    """Physical final-chat bin, never a target or padding label."""
    _nonnegative_int(full_tokens, "full_tokens")
    for upper, name in LENGTH_BINS:
        if full_tokens < upper:
            return name
    return "ge256k"


@dataclass(frozen=True)
class TokenCounts:
    input_tokens: int
    supervised_tokens: int
    full_chat_tokens: int

    def __post_init__(self) -> None:
        for name in ("input_tokens", "supervised_tokens", "full_chat_tokens"):
            _nonnegative_int(getattr(self, name), name)
        if (
            self.input_tokens == 0
            or self.supervised_tokens == 0
            or self.input_tokens + self.supervised_tokens != self.full_chat_tokens
            or self.full_chat_tokens > 262144
        ):
            raise ValueError("invalid assistant-only final-chat token equation")


@dataclass(frozen=True)
class AdapterBinding:
    """Adapter-supplied source semantics, checked against one native receipt."""

    source_kind: str
    source_group: str
    domain: str
    topic: str | None
    operation: str
    evidence_profile: str
    tokenizer_profile: str
    receipt_path: Path
    receipt_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "source_kind",
            "source_group",
            "domain",
            "operation",
            "evidence_profile",
            "tokenizer_profile",
        ):
            _required(getattr(self, name), name)
        if self.topic is not None:
            _required(self.topic, "topic")
        if not isinstance(self.receipt_path, Path) or not self.receipt_path.is_file():
            raise ValueError("native receipt file is missing")
        if not isinstance(self.receipt_sha256, str) or not SHA256.fullmatch(
            self.receipt_sha256
        ):
            raise ValueError("native receipt SHA-256 is invalid")
        if _sha_file(self.receipt_path) != self.receipt_sha256:
            raise ValueError("native receipt SHA-256 mismatch")

    def verify_receipt(self) -> None:
        """Call again before publication to detect source receipt drift."""
        if _sha_file(self.receipt_path) != self.receipt_sha256:
            raise ValueError("native receipt changed after adapter binding")


@dataclass(frozen=True)
class NativeCandidate:
    sample_id: str
    task_key: str
    semantic_task_id: str
    source_kind: str
    source_group: str
    receipt_sha256: str
    domain: str
    topic: str
    operation: str
    evidence_profile: str
    evidence_status: str | None
    dependency_status: str | None
    tokenizer_profile: str
    split: str
    context_sha256: str
    answer_sha256: str
    full_chat_tokens: int
    input_tokens: int
    supervised_tokens: int
    length_bin: str
    source_length_label: str | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _answer_hash(answer: str) -> str:
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError:
        normalized = answer
    else:
        normalized = _dump(parsed)
    return _sha_text(normalized)


def _token_counts(index: dict[str, Any], supplied: TokenCounts | None) -> TokenCounts:
    full = _index_value(index, "full_chat_tokens", "full_message_tokens")
    if full is None and supplied is None:
        raise ValueError("final-chat token count is missing")
    counts = supplied or TokenCounts(
        input_tokens=index.get("input_tokens"),
        supervised_tokens=index.get("supervised_tokens"),
        full_chat_tokens=full,
    )
    for name, value in (
        ("full_chat_tokens", full),
        ("input_tokens", index.get("input_tokens")),
        ("supervised_tokens", index.get("supervised_tokens")),
    ):
        if value is not None and value != getattr(counts, name):
            raise ValueError(f"adapter-measured {name} disagrees with index")
    return counts


def normalize_native_candidate(
    index: dict[str, Any],
    reader_row: dict[str, Any],
    binding: AdapterBinding,
    *,
    context_text: str,
    token_counts: TokenCounts | None = None,
) -> NativeCandidate:
    """Normalize one final reader sample; no source body or batch held in memory."""
    if not isinstance(index, dict) or not isinstance(reader_row, dict):
        raise TypeError("index and reader row must be objects")
    if not isinstance(context_text, str) or not context_text:
        raise ValueError("model-visible context_text is required")
    sample_id = _required(_index_value(index, "sample_id", "example_id"), "sample_id")
    if reader_row.get("sample_id") not in (None, sample_id) or reader_row.get(
        "example_id"
    ) not in (None, sample_id):
        raise ValueError("reader/index sample identity mismatch")
    semantic_id = _required(
        _index_value(index, "semantic_task_id", "task_id"), "semantic_task_id"
    )
    source_group = _index_value(index, "source_group", "group_id")
    if source_group is not None and source_group != binding.source_group:
        raise ValueError("source group disagrees with adapter binding")
    for name, expected in (
        ("source_kind", binding.source_kind),
        ("domain", binding.domain),
    ):
        if index.get(name) is not None and index[name] != expected:
            raise ValueError(f"{name} disagrees with adapter binding")
    topic = index.get("topic")
    if binding.topic is not None and topic is not None and topic != binding.topic:
        raise ValueError("topic disagrees with adapter binding")
    topic = _required(binding.topic or topic or "unknown", "topic")
    declared_op = _index_value(index, "task_type", "operation")
    if declared_op is not None and declared_op != binding.operation:
        raise ValueError("operation disagrees with adapter binding")
    split = index.get("split")
    if split not in {"train", "eval"}:
        raise ValueError("candidate split must be train or eval")
    messages = reader_row.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) != 2
        or any(not isinstance(message, dict) for message in messages)
        or [message.get("role") for message in messages] != ["user", "assistant"]
        or any(
            not isinstance(message.get("content"), str) or not message["content"]
            for message in messages
        )
    ):
        raise ValueError("reader row needs one nonempty user and assistant message")
    if messages[0]["content"].count(context_text) != 1:
        raise ValueError(
            "context must occur exactly once in the reader-visible user message"
        )
    context_sha = _sha_text(context_text)
    if (
        index.get("context_sha256") is not None
        and index["context_sha256"] != context_sha
    ):
        raise ValueError("reader context SHA-256 mismatch")
    answer_sha = _answer_hash(messages[1]["content"])
    if index.get("answer_sha256") is not None and index["answer_sha256"] != answer_sha:
        raise ValueError("reader answer SHA-256 mismatch")
    counts = _token_counts(index, token_counts)
    if (
        index.get("token_measurement") is not None
        and index["token_measurement"] != binding.tokenizer_profile
    ):
        raise ValueError("tokenizer profile disagrees with index")
    physical = physical_length_bin(counts.full_chat_tokens)
    declared = index.get("length_bin")
    if declared not in (None, "native", physical):
        raise ValueError("declared length disagrees with final-chat tokens")
    envelope = index.get("observed_lineage_token_envelope")
    if envelope is not None and (
        not isinstance(envelope, dict)
        or not (
            type(envelope.get("start")) is int
            and type(envelope.get("end")) is int
            and 0 <= envelope["start"] < envelope["end"] <= counts.input_tokens
        )
    ):
        raise ValueError("evidence envelope crosses assistant loss mask")
    for span in index.get("fact_value_token_spans") or ():
        if not isinstance(span, dict) or not (
            type(span.get("start")) is int
            and type(span.get("end")) is int
            and 0 <= span["start"] < span["end"] <= counts.input_tokens
        ):
            raise ValueError("fact evidence span crosses assistant loss mask")
    task_key = _dump([binding.source_kind, binding.source_group, semantic_id])
    return NativeCandidate(
        sample_id=sample_id,
        task_key=task_key,
        semantic_task_id=semantic_id,
        source_kind=binding.source_kind,
        source_group=binding.source_group,
        receipt_sha256=binding.receipt_sha256,
        domain=binding.domain,
        topic=topic,
        operation=binding.operation,
        evidence_profile=binding.evidence_profile,
        evidence_status=index.get("evidence_status"),
        dependency_status=index.get("dependency_status"),
        tokenizer_profile=binding.tokenizer_profile,
        split=split,
        context_sha256=context_sha,
        answer_sha256=answer_sha,
        full_chat_tokens=counts.full_chat_tokens,
        input_tokens=counts.input_tokens,
        supervised_tokens=counts.supervised_tokens,
        length_bin=physical,
        source_length_label=declared if declared == "native" else None,
    )


@dataclass
class CandidateLedger:
    """Incremental split/answer/sample guard for a stream of candidate rows."""

    samples: set[str] = field(default_factory=set)
    group_splits: dict[tuple[str, str], str] = field(default_factory=dict)
    task_answers: dict[str, tuple[str, str]] = field(default_factory=dict)
    global_task_answers: dict[tuple[str, str], tuple[str, str]] = field(
        default_factory=dict
    )
    rows: int = 0

    def add(self, candidate: NativeCandidate) -> bool:
        if candidate.sample_id in self.samples:
            raise ValueError("duplicate sample ID")
        group = (candidate.source_kind, candidate.source_group)
        if group in self.group_splits and self.group_splits[group] != candidate.split:
            raise ValueError("source group crosses train/eval split")
        prior = self.task_answers.get(candidate.task_key)
        if prior is not None and prior != (candidate.split, candidate.answer_sha256):
            raise ValueError("semantic task changes split or answer across views")
        global_key = (candidate.source_kind, candidate.semantic_task_id)
        global_prior = self.global_task_answers.get(global_key)
        if global_prior is not None and global_prior != (
            candidate.split,
            candidate.answer_sha256,
        ):
            raise ValueError(
                "semantic task changes split or answer across source groups"
            )
        self.samples.add(candidate.sample_id)
        self.group_splits[group] = candidate.split
        self.task_answers[candidate.task_key] = (
            candidate.split,
            candidate.answer_sha256,
        )
        self.global_task_answers[global_key] = (
            candidate.split,
            candidate.answer_sha256,
        )
        self.rows += 1
        return prior is None

    @property
    def independent_tasks(self) -> int:
        return len(self.task_answers)

    @property
    def independent_semantic_tasks(self) -> int:
        return len(self.global_task_answers)
