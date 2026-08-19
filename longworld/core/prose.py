"""Unique intervening prose so packing grows from documents, not clones."""

from __future__ import annotations

import random

_SENTENCES = [
    "The weekly review did not restate private hashes, scores, or SPDX tokens.",
    "Scheduling notes mention only process, never the authoritative channel.",
    "A reader who wants numerals must open the artifact that actually wrote them.",
    "This paragraph exists to occupy timeline space between causal events.",
    "Standup chatter is not a legal instrument and is not a git tag.",
    "Infrastructure tickets can block work without changing world state.",
    "Mirror lag and quota waits are exogenous; they do not rewrite history.",
    "Please do not treat this note as a substitute for the enabling issue.",
    "The author copied no leaderboard cell and no contract version token.",
    "Later documents may supersede earlier ones; this file does not decide that.",
    "Search snippets in the public anchors are distractors, not private state.",
    "CI flakes belong in CI logs; license identifiers belong in LICENSE.",
    "Camera-ready prose can linger after a table is updated; that is intentional.",
    "Customer email is not an amendment. Changelog quote is not a tag.",
    "If two independent faults exist, both documents are required.",
    "Delayed causes stay inert until a later witness event makes them relevant.",
    "Rollback and recovery are different from never having shipped the change.",
    "Partial observability is the point: no single file holds the join.",
    "Token budgets for packing must come from unique text, never from clones.",
    "The notebook calendar lists event types; it does not dump field assignments.",
]


def unique_prose(salt: str, n: int = 10) -> str:
    rng = random.Random(salt)
    n = max(4, n)
    parts = [rng.choice(_SENTENCES) for _ in range(n)]
    return " ".join(parts)
