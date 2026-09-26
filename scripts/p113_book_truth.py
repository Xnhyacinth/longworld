"""Exact, auditable single-line quotation attribution for real-book tasks.

This contract is intentionally syntactic: a quote counts only when a nearby
explicit name and speech verb occur in one of the declared arrangements.
Implicit dialogue, pronouns, and character-coreference are out of scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VERBS = frozenset(
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
NAME = r"(?:(?:Mr\.|Mrs\.|Miss|Dr\.|Sir|Lady|Lord|Inspector)[ \t]+)?[A-Z][a-zA-Z'’-]+(?:[ \t]+[A-Z][a-zA-Z'’-]+){0,2}"
QUOTE = r"(?P<open>[\"“])(?P<quote>[^\"”\n]{1,2000})(?P<close>[\"”])"
_V = "(?i:" + "|".join(sorted(VERBS)) + ")"
AFTER = re.compile(
    QUOTE
    + rf"[ \t]*[,;]?[ \t]*(?:(?P<verb_a>{_V})[ \t]+(?P<name_a>{NAME})|(?P<name_b>{NAME})[ \t]+(?P<verb_b>{_V}))(?=[,.;:!?\n]|$)"
)
BEFORE = re.compile(
    rf"(?P<name_c>{NAME})[ \t]+(?P<verb_c>{_V})[ \t]*[,;:]?[ \t]*" + QUOTE
)
HEADING = re.compile(
    r"(?im)^[ \t]*(?:CHAPTER|Chapter)[ \t]+(?P<num>[IVXLCDM]+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|THIRTEEN|FOURTEEN|FIFTEEN|SIXTEEN|SEVENTEEN|EIGHTEEN|NINETEEN|TWENTY|[0-9]+)(?:\.|\b)(?:[ \t.:—-]+[^\n]*)?$|^[ \t]*(?P<roman>[IVXLCDM]+)\.[ \t]+[A-Z][A-Z ’'\-]+[ \t]*$"
)
_NUMBER_WORDS = [
    "ONE",
    "TWO",
    "THREE",
    "FOUR",
    "FIVE",
    "SIX",
    "SEVEN",
    "EIGHT",
    "NINE",
    "TEN",
    "ELEVEN",
    "TWELVE",
    "THIRTEEN",
    "FOURTEEN",
    "FIFTEEN",
    "SIXTEEN",
    "SEVENTEEN",
    "EIGHTEEN",
    "NINETEEN",
    "TWENTY",
]
_WORDS_TO_ROMAN = dict(
    zip(
        _NUMBER_WORDS,
        [
            "I",
            "II",
            "III",
            "IV",
            "V",
            "VI",
            "VII",
            "VIII",
            "IX",
            "X",
            "XI",
            "XII",
            "XIII",
            "XIV",
            "XV",
            "XVI",
            "XVII",
            "XVIII",
            "XIX",
            "XX",
        ],
        strict=True,
    )
)
_BAD_NAMES = frozenset(
    {
        "he",
        "she",
        "they",
        "i",
        "we",
        "you",
        "it",
        "the",
        "a",
        "an",
        "his",
        "her",
        "my",
        "our",
        "one",
        "that",
        "this",
        "there",
        "yes",
        "no",
        "mr",
        "mrs",
        "miss",
        "dr",
        "sir",
        "lady",
        "lord",
        "inspector",
    }
)


@dataclass(frozen=True)
class Chapter:
    title: str
    text: str
    start: int


@dataclass(frozen=True)
class Attribution:
    quote: str
    label: str
    verb: str
    quote_start: int
    quote_end: int
    label_start: int
    label_end: int
    form: str


def _chapter_num(raw: str) -> str:
    number = raw.upper()
    return _WORDS_TO_ROMAN.get(number, number)


def chapters(body: str) -> list[Chapter]:
    matches = list(HEADING.finditer(body))
    if len(matches) < 8:
        raise ValueError("fewer_than_eight_chapter_headings")
    # A table of contents may repeat chapter labels. The last opening chapter
    # begins the actual body; reject residual duplicate labels rather than
    # silently collapse noncontiguous sections or multiple volumes.
    starts = [
        i
        for i, match in enumerate(matches)
        if _chapter_num(match.group("num") or match.group("roman")) in {"I", "1"}
    ]
    if not starts:
        raise ValueError("no_first_chapter_heading")
    matches = matches[starts[-1] :]
    if len(matches) < 8:
        raise ValueError("fewer_than_eight_body_chapters")
    seen = set()
    result = []
    for i, match in enumerate(matches):
        number = _chapter_num(match.group("num") or match.group("roman"))
        if number in seen:
            raise ValueError("repeated_body_chapter_number")
        seen.add(number)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chapter_text = body[match.start() : end].rstrip()
        if len(chapter_text) < 1000:
            raise ValueError("short_or_toc_chapter_body")
        result.append(Chapter(f"Chapter {number}", chapter_text, match.start()))
    return result


def _clean_label(value: str) -> str | None:
    label = " ".join(value.split())
    if not label or label.casefold().rstrip(".") in _BAD_NAMES:
        return None
    if len(label) > 60:
        return None
    return label


def speeches(text: str) -> list[Attribution]:
    """Generator parser: two anchored regex forms, with no inferred pronouns."""
    result = []
    occupied_quotes = set()
    for form, pattern in (("quote_then_tag", AFTER), ("tag_then_quote", BEFORE)):
        for match in pattern.finditer(text):
            if (match.group("open"), match.group("close")) not in {
                ('"', '"'),
                ("“", "”"),
            }:
                continue
            label_group = (
                "name_a"
                if match.groupdict().get("name_a")
                else "name_b"
                if match.groupdict().get("name_b")
                else "name_c"
            )
            verb_group = (
                "verb_a"
                if match.groupdict().get("verb_a")
                else "verb_b"
                if match.groupdict().get("verb_b")
                else "verb_c"
            )
            label = _clean_label(match.group(label_group))
            quote = match.group("quote")
            key = (match.start("quote"), match.end("quote"))
            if label is None or key in occupied_quotes:
                continue
            occupied_quotes.add(key)
            result.append(
                Attribution(
                    quote,
                    label,
                    match.group(verb_group).casefold(),
                    *key,
                    match.start(label_group),
                    match.end(label_group),
                    form,
                )
            )
    return sorted(result, key=lambda item: item.quote_start)


def audit_speeches(text: str) -> list[Attribution]:
    """Independent quote-first scanner. Regex matching is not reused here."""
    result = []
    for open_mark, close_mark in (('"', '"'), ("“", "”")):
        pos = 0
        while True:
            start = text.find(open_mark, pos)
            if start < 0:
                break
            close = text.find(close_mark, start + 1)
            line_end = text.find("\n", start + 1)
            if close < 0 or (line_end >= 0 and line_end < close):
                pos = start + 1
                continue
            quote = text[start + 1 : close]
            pos = close + 1
            if not 1 <= len(quote) <= 2000:
                continue
            after = text[close + 1 : min(len(text), close + 120)].split("\n", 1)[0]
            before = text[max(0, start - 120) : start].split("\n")[-1]
            # Match whitespace-delimited words after tokenizing the local tag.
            tail = re.match(r"^[ \t]*[,;]?[ \t]*(.+)", after)
            found = None
            if tail:
                segment = tail.group(1)
                # End the tag at the next punctuation boundary. A subsequent
                # sentence starting with a capital must not extend the name.
                stop = re.search(
                    r"[,.!:;?]", segment.replace("Mr.", "Mr_").replace("Dr.", "Dr_")
                )
                if stop:
                    segment = segment[: stop.start()]
                terms = list(re.finditer(r"[A-Za-z][A-Za-z'’.-]*", segment))
                if terms:
                    if terms[0].group().casefold() in VERBS:
                        label_parts = terms[1:]
                        if 1 <= len(label_parts) <= 4 and all(
                            term.group()[:1].isupper() for term in label_parts
                        ):
                            rel = tail.start(1) + terms[1].start()
                            found = (
                                " ".join(term.group() for term in label_parts),
                                terms[0].group().casefold(),
                                close + 1 + rel,
                                close + 1 + tail.start(1) + terms[-1].end(),
                                "quote_then_tag",
                            )
                    elif terms[0].group()[:1].isupper():
                        verb_idx = len(terms) - 1
                        if (
                            1 <= verb_idx <= 4
                            and terms[verb_idx].group().casefold() in VERBS
                            and all(
                                term.group()[:1].isupper() for term in terms[:verb_idx]
                            )
                        ):
                            found = (
                                " ".join(term.group() for term in terms[:verb_idx]),
                                terms[verb_idx].group().casefold(),
                                close + 1 + tail.start(1) + terms[0].start(),
                                close + 1 + tail.start(1) + terms[verb_idx - 1].end(),
                                "quote_then_tag",
                            )
            if found is None:
                before_terms = list(re.finditer(r"[A-Za-z][A-Za-z'’.-]*", before))
                if before_terms and before_terms[-1].group().casefold() in VERBS:
                    verb_idx = len(before_terms) - 1
                    names = []
                    for term in reversed(before_terms[max(0, verb_idx - 3) : verb_idx]):
                        if not term.group()[:1].isupper():
                            break
                        names.append(term)
                    names.reverse()
                    if names:
                        found = (
                            " ".join(term.group() for term in names),
                            before_terms[-1].group().casefold(),
                            start - len(before) + names[0].start(),
                            start - len(before) + names[-1].end(),
                            "tag_then_quote",
                        )
            if found:
                label = _clean_label(found[0])
                if label:
                    result.append(
                        Attribution(
                            quote,
                            label,
                            found[1],
                            start + 1,
                            close,
                            found[2],
                            found[3],
                            found[4],
                        )
                    )
    return sorted(result, key=lambda item: item.quote_start)


def alias_uncertain(label: str, all_labels: set[str]) -> bool:
    """Fail closed when a longer/shorter explicit label may name the same person."""
    tokens = label.casefold().replace(".", "").split()
    core = tokens[-1]
    for other in all_labels:
        if other.casefold() == label.casefold():
            continue
        other_tokens = other.casefold().replace(".", "").split()
        if core in other_tokens or tokens[0] in other_tokens:
            return True
    return False
