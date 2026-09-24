"""Conservative relation evidence checks for Wiki locate candidates.

The shared-world loader checks that a value occurs in a cited span.  This
module checks the surrounding row or sentence before a Wiki fact is offered
as a reader example.  It does not establish natural-language entailment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from longworld.synthesis import wiki_adapter
from longworld.synthesis.shared_semantic_world import Fact, SemanticWorld, SpanRef


@dataclass(frozen=True)
class EvidenceCheck:
    supported: bool
    reason: str
    span: SpanRef | None = None
    row: str | None = None
    header: str | None = None


_MARKUP = re.compile(
    r"https?://|citation needed|\b(?:convert|cite web)\s*\|", re.IGNORECASE
)


def _line_at(text: str, span: SpanRef) -> tuple[str, int]:
    start = text.rfind("\n", 0, span.start) + 1
    end = text.find("\n", span.end)
    if end < 0:
        end = len(text)
    return text[start:end], start


def _subject_surfaces(world: SemanticWorld, fact: Fact) -> tuple[str, ...]:
    entity = world.objects[fact.subject]
    return (entity.label, *entity.aliases)


def _table_check(
    world: SemanticWorld, fact: Fact, span: SpanRef, row: str, row_start: int
) -> EvidenceCheck:
    doc = world._docs[span.doc_id]
    column = fact.qualifiers["table_column"]
    rows = row.split(" | ")
    if not rows or rows[0].strip() not in _subject_surfaces(world, fact):
        return EvidenceCheck(False, "table row does not identify fact subject")
    for header_line in reversed(doc.text[:row_start].splitlines()):
        headers = header_line.split(" | ")
        if column not in headers or len(headers) != len(rows):
            continue
        index = headers.index(column)
        if index == 0 or not (0 <= index < len(rows)):
            continue
        cell = rows[index].strip()
        semantics = wiki_adapter._cell_semantics(column, cell)
        if semantics is None or semantics[0] != fact.relation:
            return EvidenceCheck(False, "table header does not express relation")
        cell_start = row_start + sum(len(part) + 3 for part in rows[:index])
        if not (cell_start <= span.start < span.end <= cell_start + len(rows[index])):
            return EvidenceCheck(False, "value span falls outside declared column")
        if _MARKUP.search(cell) or len(cell) > 200:
            return EvidenceCheck(
                False, "table cell contains markup or an overlong value"
            )
        return EvidenceCheck(
            True,
            "table subject, header and value column aligned",
            span,
            row,
            header_line,
        )
    return EvidenceCheck(False, "matching table header and row shape not found")


def _inline_check(
    world: SemanticWorld, fact: Fact, span: SpanRef, row: str, row_start: int
) -> EvidenceCheck:
    doc = world._docs[span.doc_id]
    surfaces = _subject_surfaces(world, fact)
    if _MARKUP.search(row) or len(row) > 400:
        return EvidenceCheck(False, "line contains markup or too much unrelated text")
    if ": " in row and doc.title in surfaces:
        key, value = row.lstrip("-* ").split(": ", 1)
        if wiki_adapter._relation_for_key(
            key.strip()
        ) == fact.relation and span.start >= row_start + row.find(value):
            return EvidenceCheck(
                True, "document subject and relation key aligned", span, row
            )
    # Keep only prose that names the fact subject in the sentence containing
    # the value. A page title elsewhere does not bind a claim about another
    # object in the same paragraph.
    relative = span.start - row_start
    boundary = row.rfind(". ", 0, relative)
    sentence_start = boundary + 2 if boundary >= 0 else 0
    sentence = row[sentence_start:]
    subject_at = min((sentence.find(s) for s in surfaces if s in sentence), default=-1)
    relation_at = sentence.find(fact.relation)
    value_at = span.start - row_start - sentence_start
    if 0 <= subject_at < relation_at < value_at:
        if re.search(
            r"\b(?:not|never|without|neither|no longer)\b",
            sentence[:value_at],
            re.IGNORECASE,
        ):
            return EvidenceCheck(False, "sentence negates or excludes the relation")
        return EvidenceCheck(
            True, "sentence names subject, relation and value", span, sentence
        )
    return EvidenceCheck(False, "line does not bind subject, relation and value")


def check_locate_fact(world: SemanticWorld, fact: Fact) -> EvidenceCheck:
    """Check a fact's complete visible row/sentence, accepting any valid support."""
    failures: list[str] = []
    for span in fact.supporting_spans:
        doc = world._docs[span.doc_id]
        row, row_start = _line_at(doc.text, span)
        if "table_column" in fact.qualifiers:
            result = _table_check(world, fact, span, row, row_start)
        else:
            result = _inline_check(world, fact, span, row, row_start)
        if result.supported:
            return result
        failures.append(result.reason)
    return EvidenceCheck(False, "; ".join(dict.fromkeys(failures)))
