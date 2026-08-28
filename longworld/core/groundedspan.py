"""Domain-neutral replay checks for facts grounded in visible source text.

The records in this module are deliberately small.  They do not establish that a
source is authentic; callers must verify acquisition and authorization before
constructing them. They make source-to-fact bindings executable and validate
relation closure. They do not prove that cited facts entail a relation type;
`structural_closure_only` must never be counted as semantic relation proof.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, TypeAlias

ClaimedProvenanceClass: TypeAlias = Literal[
    "authentic_source_text",
    "authentic_source_api",
    "verified_derived",
    "synthetic_executable",
]

PROVENANCE_CLASSES = frozenset(
    {
        "authentic_source_text",
        "authentic_source_api",
        "verified_derived",
        "synthetic_executable",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELATION_TYPE = re.compile(r"^[a-z][a-z0-9_]*$")


class GroundedSpanError(ValueError):
    """Raised when visible source evidence cannot be replayed exactly."""


@dataclass(frozen=True)
class GroundedFact:
    fact_id: str
    source_id: str
    text_sha256: str
    quote: str
    char_start: int
    char_end: int
    normalized_quote: str


@dataclass(frozen=True)
class GroundedRelation:
    relation_id: str
    relation_type: str
    source_id: str
    target_id: str
    claimed_provenance_class: ClaimedProvenanceClass
    evidence_fact_ids: tuple[str, ...]
    proof_mode: Literal["structural_closure_only"] = "structural_closure_only"


@dataclass(frozen=True)
class GroundedSource:
    source_id: str
    visible_text: str
    text_sha256: str
    facts: tuple[GroundedFact, ...]
    relations: tuple[GroundedRelation, ...] = ()


def normalize_fact_value(value: str) -> str:
    """Return the sole normalization accepted by the replay contract."""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_grounded_source(source: GroundedSource) -> GroundedSource:
    """Validate one self-contained source and return the original record.

    Use :func:`validate_grounded_sources` for relations whose endpoints live in
    different sources.
    """
    return validate_grounded_sources((source,))[0]


def validate_grounded_sources(
    sources: tuple[GroundedSource, ...],
    *,
    allow_duplicate_text_hashes: bool = False,
) -> tuple[GroundedSource, ...]:
    """Replay fact spans and validate structural relation closure.

    Relation type semantics are intentionally not proven by this validator.
    """
    if not isinstance(sources, tuple) or not sources:
        raise GroundedSpanError("grounded sources must be a non-empty tuple")

    source_ids: set[str] = set()
    source_hashes: set[str] = set()
    fact_ids: set[str] = set()
    fact_owners: dict[str, str] = {}
    span_ids: set[tuple[str, int, int]] = set()
    relations: list[tuple[str, GroundedRelation]] = []
    for source in sources:
        _validate_source_content(source)
        if source.source_id in source_ids:
            raise GroundedSpanError("duplicate source id")
        source_ids.add(source.source_id)
        if not allow_duplicate_text_hashes and source.text_sha256 in source_hashes:
            raise GroundedSpanError("duplicate source text hash")
        source_hashes.add(source.text_sha256)
        for fact in source.facts:
            if fact.fact_id in fact_ids:
                raise GroundedSpanError("duplicate fact id")
            fact_ids.add(fact.fact_id)
            fact_owners[fact.fact_id] = fact.source_id
            span_id = (fact.source_id, fact.char_start, fact.char_end)
            if span_id in span_ids:
                raise GroundedSpanError("duplicate fact span")
            span_ids.add(span_id)
        relations.extend((source.source_id, relation) for relation in source.relations)

    relation_ids: set[str] = set()
    relation_edges: set[tuple[str, str, str]] = set()
    for owner_source_id, relation in relations:
        _validate_relation(relation, owner_source_id, source_ids, fact_owners)
        if relation.relation_id in relation_ids:
            raise GroundedSpanError("duplicate relation id")
        relation_ids.add(relation.relation_id)
        edge = (
            relation.relation_type,
            relation.source_id,
            relation.target_id,
        )
        if edge in relation_edges:
            raise GroundedSpanError("duplicate typed relation")
        relation_edges.add(edge)
    return sources


def facts_in_visible_char_window(
    source: GroundedSource,
    *,
    char_start: int,
    char_end: int,
) -> tuple[GroundedFact, ...]:
    """Return exact facts wholly contained in ``[char_start, char_end)``."""
    _validate_source_content(source)
    if (
        type(char_start) is not int
        or type(char_end) is not int
        or char_start < 0
        or char_start >= char_end
        or char_end > len(source.visible_text)
    ):
        raise GroundedSpanError("visible character window is invalid")
    return tuple(
        fact
        for fact in source.facts
        if char_start <= fact.char_start and fact.char_end <= char_end
    )


def _validate_source_content(source: GroundedSource) -> None:
    if not isinstance(source, GroundedSource):
        raise GroundedSpanError("grounded source has the wrong type")
    _validate_id(source.source_id, "source id")
    if not isinstance(source.visible_text, str) or not source.visible_text:
        raise GroundedSpanError("visible text must be non-empty")
    if not isinstance(source.text_sha256, str) or not _SHA256.fullmatch(
        source.text_sha256
    ):
        raise GroundedSpanError("text sha256 is malformed")
    if sha256_text(source.visible_text) != source.text_sha256:
        raise GroundedSpanError("visible text does not match text sha256")
    if not isinstance(source.facts, tuple) or not source.facts:
        raise GroundedSpanError("source facts must be a non-empty tuple")
    if not isinstance(source.relations, tuple):
        raise GroundedSpanError("source relations must be a tuple")

    local_ids: set[str] = set()
    local_spans: set[tuple[int, int]] = set()
    for fact in source.facts:
        _validate_fact(fact, source)
        if fact.fact_id in local_ids:
            raise GroundedSpanError("duplicate fact id")
        local_ids.add(fact.fact_id)
        span = (fact.char_start, fact.char_end)
        if span in local_spans:
            raise GroundedSpanError("duplicate fact span")
        local_spans.add(span)
    for relation in source.relations:
        _validate_relation_shape(relation)


def _validate_fact(fact: GroundedFact, source: GroundedSource) -> None:
    if not isinstance(fact, GroundedFact):
        raise GroundedSpanError("grounded fact has the wrong type")
    _validate_id(fact.fact_id, "fact id")
    if fact.source_id != source.source_id:
        raise GroundedSpanError("fact belongs to a foreign source")
    if not isinstance(fact.text_sha256, str) or fact.text_sha256 != source.text_sha256:
        raise GroundedSpanError("fact text sha256 does not match its source")
    if (
        type(fact.char_start) is not int
        or type(fact.char_end) is not int
        or fact.char_start < 0
        or fact.char_start >= fact.char_end
        or fact.char_end > len(source.visible_text)
    ):
        raise GroundedSpanError("fact character span is invalid")
    if not isinstance(fact.quote, str) or not fact.quote:
        raise GroundedSpanError("fact quote must be non-empty")
    if source.visible_text[fact.char_start : fact.char_end] != fact.quote:
        raise GroundedSpanError("fact quote does not match its visible text span")
    if (
        not isinstance(fact.normalized_quote, str)
        or not fact.normalized_quote
        or fact.normalized_quote != normalize_fact_value(fact.quote)
    ):
        raise GroundedSpanError("fact normalized quote does not match its quote")


def _validate_relation(
    relation: GroundedRelation,
    owner_source_id: str,
    source_ids: set[str],
    fact_owners: dict[str, str],
) -> None:
    _validate_relation_shape(relation)
    if relation.source_id != owner_source_id:
        raise GroundedSpanError("relation is stored under a foreign source")
    if relation.source_id not in source_ids or relation.target_id not in source_ids:
        raise GroundedSpanError("relation references a missing or foreign source")
    if any(fact_id not in fact_owners for fact_id in relation.evidence_fact_ids):
        raise GroundedSpanError("relation references a missing or foreign fact")
    endpoint_ids = {relation.source_id, relation.target_id}
    if any(
        fact_owners[fact_id] not in endpoint_ids
        for fact_id in relation.evidence_fact_ids
    ):
        raise GroundedSpanError("relation evidence belongs to a non-endpoint source")


def _validate_relation_shape(relation: GroundedRelation) -> None:
    if not isinstance(relation, GroundedRelation):
        raise GroundedSpanError("grounded relation has the wrong type")
    _validate_id(relation.relation_id, "relation id")
    if not isinstance(relation.relation_type, str) or not _RELATION_TYPE.fullmatch(
        relation.relation_type
    ):
        raise GroundedSpanError("relation type is malformed")
    _validate_id(relation.source_id, "relation source id")
    _validate_id(relation.target_id, "relation target id")
    if relation.source_id == relation.target_id:
        raise GroundedSpanError("relation endpoints must be different sources")
    if (
        not isinstance(relation.claimed_provenance_class, str)
        or relation.claimed_provenance_class not in PROVENANCE_CLASSES
    ):
        raise GroundedSpanError("relation provenance class is unsupported")
    if not isinstance(relation.evidence_fact_ids, tuple):
        raise GroundedSpanError("relation evidence fact ids must be a tuple")
    if not relation.evidence_fact_ids:
        raise GroundedSpanError("relation evidence fact ids must be non-empty")
    if len(set(relation.evidence_fact_ids)) != len(relation.evidence_fact_ids):
        raise GroundedSpanError("relation evidence fact ids are duplicated")
    for fact_id in relation.evidence_fact_ids:
        _validate_id(fact_id, "relation evidence fact id")
    if relation.proof_mode != "structural_closure_only":
        raise GroundedSpanError("relation proof mode is unsupported")


def _validate_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}", value
    ):
        raise GroundedSpanError(f"{label} is malformed")
