"""Independent high-recall veto for a later plausibly named book quotation.

This intentionally does not determine gold. It searches outside each quoted
span for the printed name and a declared speech verb in either order, allowing
intervening modifiers. A possible match rejects the task even when the name
may actually be an addressee or occur in nearby narration.
"""

from __future__ import annotations

import re

SPEECH_VERBS = frozenset(
    {
        "said",
        "asked",
        "replied",
        "cried",
        "exclaimed",
        "whispered",
        "murmured",
        "answered",
        "shouted",
        "declared",
        "responded",
        "panted",
        "gasped",
        "muttered",
        "remarked",
        "protested",
    }
)
OPEN_TO_CLOSE = {'"': '"', "“": "”", "'": "'", "‘": "’"}
HONORIFICS = ("Mr.", "Mrs.", "Ms.", "Dr.", "St.")


def assert_verb_contract(expected: frozenset[str]) -> None:
    if SPEECH_VERBS != expected:
        raise ValueError("high-recall speech gate and task verb contract differ")


def _quotes(text: str):
    """Manual span scan; no generator or quote-first audit regex is reused."""
    for start, opener in enumerate(text):
        closer = OPEN_TO_CLOSE.get(opener)
        if closer is None:
            continue
        if opener == "'" and start > 0 and text[start - 1].isalnum():
            continue
        line_end = text.find("\n", start + 1)
        limit = min(len(text), start + 2002)
        if line_end >= 0:
            limit = min(limit, line_end)
        end = start + 1
        while end < limit:
            if text[end] == closer:
                if closer == "'" and end + 1 < len(text) and text[end + 1].isalnum():
                    end += 1
                    continue
                if end > start + 1:
                    yield start, end + 1, text[start + 1 : end]
                break
            end += 1


def _protected(text: str) -> str:
    for title in HONORIFICS:
        text = text.replace(title, title[:-1] + "_")
    return text


def _nearby_after(text: str, close: int) -> str:
    region = text[close : close + 200]
    blank = region.find("\n\n")
    if blank >= 0:
        region = region[:blank]
    region = region.lstrip(" \t\n,;:—-.")
    protected = _protected(region)
    stop = next((i for i, char in enumerate(protected) if char in ".!?"), None)
    return region[:stop] if stop is not None else region


def _nearby_before(text: str, opening: int) -> str:
    region = text[max(0, opening - 200) : opening]
    blank = region.rfind("\n\n")
    if blank >= 0:
        region = region[blank + 2 :]
    protected = _protected(region)
    stop = max((i for i, char in enumerate(protected) if char in ".!?"), default=-1)
    return region[stop + 1 :]


def _has_name_and_verb(segment: str, label: str) -> bool:
    if not segment:
        return False
    name = re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", segment, re.IGNORECASE)
    if name is None:
        return False
    return any(
        re.search(r"(?<!\w)" + re.escape(verb) + r"(?!\w)", segment, re.IGNORECASE)
        for verb in SPEECH_VERBS
    )


def later_named_quote_suspicions(
    text: str, label: str, after_quote_end: int
) -> list[dict[str, object]]:
    """Return every later quotation with a plausible same-name speech tag.

    A positive result is intentionally a veto, not a newly inferred answer.
    It includes uncertain addressee/coreference cases in the rejection ledger.
    """
    if not label.strip() or not 0 <= after_quote_end <= len(text):
        raise ValueError("invalid broad speech gate label or answer position")
    result = []
    seen = set()
    for start, end, quote in _quotes(text):
        if start <= after_quote_end:
            continue
        for side, segment in (
            ("after", _nearby_after(text, end)),
            ("before", _nearby_before(text, start)),
        ):
            if not _has_name_and_verb(segment, label):
                continue
            key = (start, end)
            if key not in seen:
                result.append(
                    {
                        "quote": quote,
                        "quote_start": start,
                        "quote_end": end,
                        "side": side,
                        "tag_excerpt": segment[:160],
                    }
                )
                seen.add(key)
    return sorted(result, key=lambda item: int(item["quote_start"]))
