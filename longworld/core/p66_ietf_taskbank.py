"""Fail-closed filtering for inherited IETF succession projections."""

from __future__ import annotations

import hashlib
import json

REVISION = "longworld.p66-ietf-taskbank.v1"
EXACT_RANGES = (
    ("64k", 64000, 65536),
    ("128k", 128000, 131072),
    ("256k", 256000, 262144),
)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def exact_range(tokens: int) -> str | None:
    return next(
        (name for name, low, high in EXACT_RANGES if low <= tokens <= high), None
    )


def minimum_positive_evidence_cover(context: str, task: dict, answer: dict) -> dict:
    positions = []
    missing = []
    for item in task["evidence_items"]:
        if answer[item["evidence_id"]] == "UNKNOWN":
            continue
        quote = item["evidence_quote"]
        start = context.find(quote)
        if start < 0:
            missing.append(item["evidence_id"])
        else:
            positions.append((start, start + len(quote)))
    return {
        "char_start": min((p[0] for p in positions), default=None),
        "char_end": max((p[1] for p in positions), default=None),
        "char_cover": (
            max(p[1] for p in positions) - min(p[0] for p in positions)
            if positions
            else 0
        ),
        "missing_evidence_ids": missing,
    }


def admission_reason(
    *,
    view: str,
    context_tokens: int,
    question_only_em: bool,
    positive_cover_tokens: int,
) -> str:
    if view != "cf":
        return (
            "question_only_codebook_exact"
            if question_only_em
            else "non_counterfactual_view"
        )
    if context_tokens <= 32768:
        return "below_long_context_floor"
    if positive_cover_tokens <= 16384:
        return "positive_evidence_fits_16k_window"
    return "accepted_local_long_candidate"
