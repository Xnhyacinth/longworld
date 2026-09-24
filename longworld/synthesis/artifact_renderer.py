"""P74 dual-path renderer: frozen-original passthrough and constrained artifacts.

The T6 half of the P74 charter (.hl/design/p74_real_shared_worlds.md §8, §16
T6 row): one semantic world, two expressions, one contract — the answer a
program computes must not depend on which expression the reader sees.

Path A (render_original): the frozen documents joined VERBATIM. Layout, fully
documented so offsets stay resolvable:

    {"render": "path-a-original-passthrough", "schema": <SCHEMA_VERSION>}
    === SCOPE ===
    <one JSON ScopeEntry per line, sorted by task_id>
    === DOCUMENTS ===
    [D1] <title>          <- doc header line, its length fully accounted
    <D1 text verbatim>
                          <- exactly one blank line ("\n\n" joiner) between
    [D2] <title>             consecutive doc blocks
    <D2 text verbatim>

No text is mutated: span_in_joined(doc_id, start, end) returns the exact
offsets of the original (start, end) inside the joined rendering, so every
fact's supporting span resolves to the same characters it pointed at in the
source document. There is deliberately no ENTITIES/FACTS index in path A —
it is the passthrough expression and the offset map replaces the index.

Path B (render_artifact): constrained simulated artifacts per a ContentPlan
(genre x role x purpose). For each fact a deterministic, genre-keyed template
renders one natural-language sentence carrying the fact — no model calls, no
RNG, standard library only. Sentences are assembled per document (a fact's
primary document is its first supporting span's document), and every fact is
RE-VALIDATED against the new text before it is accepted:
  - the subject's label surface must appear in the sentence;
  - the value surface (str(value), or the target entity's label for
    entity-valued facts) must sit at the template's value slot, and the new
    span must slice to exactly that surface in the assembled document text.
Facts that fail re-validation are NOT silently dropped: they are excluded
from the rendered world (the artifact text does not ground them) and listed
as rendering failures with reasons, in ArtifactRendering.failures.

The path-B rendering uses the shared_semantic_world render format (scope +
entities + documents + facts sections), so that module's existing parse
helpers are reused directly: world_from_artifact(rendering) is
shared_semantic_world.from_rendered(rendering.text), and
restricted_to-style scope recovery works unchanged. Only the DOCUMENTS
section differs from world.render(): it carries the artifact sentences, and
the FACTS index carries spans re-pointed into that text.

Relation surfaces: log_entry, change_notice and technical_note render the
RAW relation id ("noise_level"), keeping (subject, relation, value)
literally recoverable from the sentence; review_comment and
meeting_conclusion render a fixed phrase ("measured noise level") from
RELATION_PHRASES — deterministic, but the relation is recoverable through
that mapping table rather than verbatim.

Honest limits:
  - facts with several supporting spans are re-grounded ONCE, into their
    primary document; alternative_spans are not carried into path B;
  - entity mentions are not rendered into artifact text (the shared render
    format does not carry them either);
  - a value surface that also occurs earlier in its own sentence is still
    spanned at its true slot, but the surface is then ambiguous for a naive
    first-match reader; the count is reported in stats as
    value_surface_first_occurrence_mismatches, never hidden;
  - time/version/qualifiers appear in the sentences where the genre calls
    for them; they are always carried by the FACTS index the world is
    rebuilt from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from string import Formatter
from typing import Any

from longworld.synthesis.shared_semantic_world import (
    SCHEMA_VERSION,
    VALUE_TYPES,
    Document,
    Fact,
    ScopeEntry,
    SemanticWorld,
    SpanRef,
    from_rendered,
)

PATH_A_RENDER = "path-a-original-passthrough"
PATH_B_RENDER = "path-b-constrained-artifact"
DOC_JOINER = "\n\n"  # exactly one blank line between consecutive doc blocks

GENRES = (
    "log_entry",
    "review_comment",
    "change_notice",
    "meeting_conclusion",
    "technical_note",
)

# Genres that render the RAW relation id; the others use RELATION_PHRASES.
RAW_RELATION_GENRES = frozenset({"log_entry", "change_notice", "technical_note"})

RELATION_PHRASES: dict[str, str] = {
    "adopts_method": "adopted method",
    "assigns_instrument": "assigned instrument",
    "validity_rule": "validity rule",
    "noise_level": "measured noise level",
    "produces": "produced observation",
    "exposure_count": "recorded exposure count",
    "calibration_status": "calibration status",
    "band": "observed band",
}

# Deterministic sentence templates, keyed by genre then value_type. Each
# template contains exactly one {value} placeholder; the value surface is
# inserted at that slot so its span is exact by construction.
GENRE_TEMPLATES: dict[str, dict[str, str]] = {
    "log_entry": {
        "entity": "{subject} {relation} {value}; logged {time}.",
        "number": "{subject} {relation} {value}{unit}; logged {time}.",
        "string": "{subject} {relation} {value}{qualifier}; logged {time}.",
        "rule": "{subject} sets {relation} {value}; logged {time}.",
    },
    "review_comment": {
        "entity": "Reviewer comment: {subject} {relation} {value}.",
        "number": "Reviewer comment: the {relation} of {subject} reads {value}{unit} as of {time}.",
        "string": "Reviewer comment: {subject} shows {relation} {value}{qualifier}.",
        "rule": "Reviewer comment: {subject} carries {relation} {value}.",
    },
    "change_notice": {
        "entity": "Change notice: {subject} now {relation} {value}{version}.",
        "number": "Change notice: {subject} now reports {relation} {value}{unit}{version}.",
        "string": "Change notice: {subject} now reports {relation} {value}{qualifier}.",
        "rule": "Change notice: the {relation} of {subject} is now {value}{version}.",
    },
    "meeting_conclusion": {
        "entity": "The meeting concluded that {subject} {relation} {value}.",
        "number": "The meeting concluded that the {relation} of {subject} is {value}{unit}.",
        "string": "The meeting concluded that {subject} has {relation} {value}{qualifier}.",
        "rule": "The meeting concluded that {subject} must apply {relation} {value}.",
    },
    "technical_note": {
        "entity": "Technical note: field {relation} of {subject} is {value}.",
        "number": "Technical note: field {relation} of {subject} is {value}{unit}, recorded {time}.",
        "string": "Technical note: field {relation} of {subject} is {value}{qualifier}, recorded {time}.",
        "rule": "Technical note: field {relation} of {subject} is {value}, recorded {time}.",
    },
}

GENRE_PREAMBLES: dict[str, str] = {
    "log_entry": "Operations log for {title}; kept by {role} to {purpose}.",
    "review_comment": "Review comments on {title}, filed by {role} to {purpose}.",
    "change_notice": "Change notices for {title}, issued by {role} to {purpose}.",
    "meeting_conclusion": "Meeting conclusions covering {title}, recorded by {role} to {purpose}.",
    "technical_note": "Technical notes on {title}, prepared by {role} to {purpose}.",
}

_PLACEHOLDER_FIELDS = frozenset(
    {"subject", "relation", "value", "unit", "time", "version", "qualifier"}
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _check_templates() -> None:
    formatter = Formatter()
    for genre in GENRES:
        table = GENRE_TEMPLATES[genre]
        missing = set(VALUE_TYPES) - set(table)
        assert not missing, f"genre {genre} lacks templates for {sorted(missing)}"
        for value_type, template in table.items():
            assert template.count("{value}") == 1, (genre, value_type)
            fields = {
                name for _, name, _, _ in formatter.parse(template) if name is not None
            }
            unknown = fields - _PLACEHOLDER_FIELDS
            assert not unknown, (
                f"{genre}/{value_type} uses unknown fields {sorted(unknown)}"
            )
    for genre, preamble in GENRE_PREAMBLES.items():
        fields = {
            name for _, name, _, _ in formatter.parse(preamble) if name is not None
        }
        assert fields <= {"title", "role", "purpose"}, (genre, fields)


_check_templates()


@dataclass(frozen=True)
class ContentPlan:
    """The constrained generation plan for one path-B artifact set."""

    genre: str
    role: str
    purpose: str

    def __post_init__(self) -> None:
        if self.genre not in GENRES:
            raise ValueError(f"genre {self.genre!r} is not one of {GENRES}")
        if not self.role or not self.role.strip():
            raise ValueError("plan needs a non-empty role")
        if not self.purpose or not self.purpose.strip():
            raise ValueError("plan needs a non-empty purpose")


@dataclass(frozen=True)
class OriginalDocLayout:
    """Where one document's verbatim block sits in the path-A rendering."""

    doc_id: str
    title: str
    block_start: int  # offset of the "[doc_id] title" header line
    text_start: int  # offset of the verbatim document text
    text_end: int


@dataclass(frozen=True)
class OriginalRendering:
    """Path A output: the frozen documents joined verbatim + the offset map."""

    text: str
    header_line: str
    scope: tuple[ScopeEntry, ...]
    scope_block: str
    doc_layouts: tuple[OriginalDocLayout, ...]

    def _layout(self, doc_id: str) -> OriginalDocLayout:
        for layout in self.doc_layouts:
            if layout.doc_id == doc_id:
                return layout
        raise KeyError(f"no document {doc_id!r} in this rendering")

    def span_in_joined(self, doc_id: str, start: int, end: int) -> tuple[int, int]:
        """Exact offsets of a (doc_id, start, end) span inside the rendering."""
        layout = self._layout(doc_id)
        length = layout.text_end - layout.text_start
        if not (0 <= start < end <= length):
            raise ValueError(f"span ({start}, {end}) is out of range for {doc_id}")
        return (layout.text_start + start, layout.text_start + end)

    def span_text(self, doc_id: str, start: int, end: int) -> str:
        joined_start, joined_end = self.span_in_joined(doc_id, start, end)
        return self.text[joined_start:joined_end]

    def document_text(self, doc_id: str) -> str:
        layout = self._layout(doc_id)
        return self.text[layout.text_start : layout.text_end]


@dataclass(frozen=True)
class RenderedFact:
    """One fact's path-B outcome: its sentence, re-validated span, or failure."""

    fact_id: str
    ok: bool
    genre: str
    doc_id: str | None
    sentence: str | None
    span: SpanRef | None
    reason: str | None
    first_occurrence_is_slot: bool = False


@dataclass(frozen=True)
class ArtifactRendering:
    """Path B output: constrained artifacts plus the re-validation records."""

    text: str
    plan: ContentPlan
    scope: tuple[ScopeEntry, ...]
    scope_block: str
    documents: tuple[Document, ...]
    facts: tuple[Fact, ...]  # re-validated facts, spans re-pointed
    rendered_facts: tuple[RenderedFact, ...]
    stats: dict[str, Any]

    @property
    def failures(self) -> tuple[RenderedFact, ...]:
        return tuple(record for record in self.rendered_facts if not record.ok)


def _scope_section(scope: tuple[ScopeEntry, ...]) -> tuple[str, tuple[str, ...]]:
    lines = tuple(_dump(entry.to_dict()) for entry in scope)
    block = "\n".join(["=== SCOPE ===", *lines])
    return block, lines


def render_original(
    world: SemanticWorld, scope_entries: tuple[ScopeEntry, ...] = ()
) -> OriginalRendering:
    """Path A: join the frozen documents verbatim; offsets stay resolvable."""
    scope = tuple(sorted(scope_entries, key=lambda entry: entry.task_id))
    scope_block, _ = _scope_section(scope)
    header_line = _dump({"render": PATH_A_RENDER, "schema": SCHEMA_VERSION})
    prefix = "\n".join([header_line, scope_block, "=== DOCUMENTS ==="])
    blocks: list[str] = []
    layouts: list[OriginalDocLayout] = []
    offset = len(prefix) + 1
    for doc in world.documents:
        header = f"[{doc.doc_id}] {doc.title}"
        block = f"{header}\n{doc.text}"
        text_start = offset + len(header) + 1
        layouts.append(
            OriginalDocLayout(
                doc_id=doc.doc_id,
                title=doc.title,
                block_start=offset,
                text_start=text_start,
                text_end=text_start + len(doc.text),
            )
        )
        blocks.append(block)
        offset += len(block) + len(DOC_JOINER)
    text = prefix + "\n" + DOC_JOINER.join(blocks) if blocks else prefix + "\n"
    return OriginalRendering(
        text=text,
        header_line=header_line,
        scope=scope,
        scope_block=scope_block,
        doc_layouts=tuple(layouts),
    )


def world_from_original(
    rendering: OriginalRendering, world: SemanticWorld
) -> SemanticWorld:
    """Rebuild the path-A world: documents sliced verbatim from the joined text."""
    documents = tuple(
        Document(doc.doc_id, doc.title, rendering.document_text(doc.doc_id))
        for doc in world.documents
    )
    return SemanticWorld(documents, world.entities, world.facts)


def _relation_surface(relation: str, genre: str) -> str:
    if genre in RAW_RELATION_GENRES:
        return relation
    return RELATION_PHRASES.get(relation, relation.replace("_", " "))


def _qualifier_clause(fact: Fact) -> str:
    revoked = fact.qualifiers.get("revoked_at")
    return f", withdrawn {revoked}" if revoked else ""


def _value_surface(world: SemanticWorld, fact: Fact) -> tuple[str | None, str | None]:
    if fact.value_type == "entity":
        target = world.objects.get(fact.value)
        if target is None:
            return None, "value entity is not in the world"
        surface = target.label
    else:
        surface = str(fact.value)
    if not surface:
        return None, "value surface is empty"
    if "\n" in surface:
        return None, "value surface is multi-line"
    return surface, None


def _fact_sentence(
    world: SemanticWorld, fact: Fact, genre: str
) -> tuple[str | None, int | None, str | None, str | None, str | None]:
    """One deterministic sentence, plus the offset of its value slot.

    Returns (sentence, value_slot, subject_surface, value_surface, reason);
    reason is set when the fact resists templating.
    """
    subject_entity = world.objects.get(fact.subject)
    if subject_entity is None:
        return None, None, None, None, "subject is not an entity in the world"
    subject = subject_entity.label
    if "\n" in subject:
        return None, None, None, None, "subject surface is multi-line"
    value, reason = _value_surface(world, fact)
    if reason is not None:
        return None, None, None, None, reason
    template = GENRE_TEMPLATES[genre][fact.value_type]
    left_fmt, right_fmt = template.split("{value}", 1)
    fields = {
        "subject": subject,
        "relation": _relation_surface(fact.relation, genre),
        "unit": f" {fact.unit}" if fact.unit else "",
        "time": fact.time or "unknown date",
        "version": f" at version {fact.version}" if fact.version else "",
        "qualifier": _qualifier_clause(fact),
    }
    left = left_fmt.format(**fields)
    right = right_fmt.format(**fields)
    return left + value + right, len(left), subject, value, None


def _repointed_fact(fact: Fact, span: SpanRef) -> Fact:
    return Fact(
        fact_id=fact.fact_id,
        subject=fact.subject,
        relation=fact.relation,
        value=fact.value,
        value_type=fact.value_type,
        unit=fact.unit,
        time=fact.time,
        version=fact.version,
        qualifiers=dict(fact.qualifiers),
        supporting_spans=(span,),
        source_hash=fact.source_hash,
    )


def render_artifact(
    world: SemanticWorld,
    plan: ContentPlan,
    scope_entries: tuple[ScopeEntry, ...],
) -> ArtifactRendering:
    """Path B: constrained simulated artifacts with per-fact re-validation."""
    scope = tuple(sorted(scope_entries, key=lambda entry: entry.task_id))
    scope_block, _ = _scope_section(scope)
    by_doc: dict[str, list[Fact]] = {}
    for fact in world.facts:
        primary = fact.supporting_spans[0].doc_id
        by_doc.setdefault(primary, []).append(fact)

    documents: list[Document] = []
    new_facts: list[Fact] = []
    records: list[RenderedFact] = []
    section_lines: list[str] = []
    for doc in world.documents:
        preamble = GENRE_PREAMBLES[plan.genre].format(
            title=doc.title, role=plan.role, purpose=plan.purpose
        )
        lines: list[str] = [preamble]
        starts: list[int] = [0]
        offset = len(preamble) + 1
        templated: list[dict[str, Any]] = []
        for fact in by_doc.get(doc.doc_id, []):
            sentence, slot, subject, value, reason = _fact_sentence(
                world, fact, plan.genre
            )
            entry: dict[str, Any] = {
                "fact": fact,
                "sentence": sentence,
                "slot": slot,
                "value": value,
                "reason": reason,
                "line_idx": None,
            }
            if reason is None and sentence.startswith("[") and "] " in sentence:
                entry["reason"] = (
                    "sentence line collides with the document header syntax"
                )
            elif reason is None and subject not in sentence:
                entry["reason"] = "subject surface is missing from the sentence"
            if entry["reason"] is None:
                entry["line_idx"] = len(lines)
                lines.append(sentence)
                starts.append(offset)
                offset += len(sentence) + 1
            templated.append(entry)
        if not any(entry["line_idx"] is not None for entry in templated):
            # A document whose facts all resist templating still names the
            # objects homed there, so the artifact is not silently emptied.
            names = [e.label for e in world.entities if e.doc_id == doc.doc_id]
            if names:
                lines.append("Named here: " + "; ".join(names) + ".")
                starts.append(offset)
                offset += len(lines[-1]) + 1
        doc_text = "\n".join(lines) + "\n"
        for entry in templated:
            fact = entry["fact"]
            if entry["reason"] is not None:
                records.append(
                    RenderedFact(
                        fact_id=fact.fact_id,
                        ok=False,
                        genre=plan.genre,
                        doc_id=doc.doc_id,
                        sentence=entry["sentence"],
                        span=None,
                        reason=entry["reason"],
                    )
                )
                continue
            start = starts[entry["line_idx"]] + entry["slot"]
            span = SpanRef(doc.doc_id, start, start + len(entry["value"]))
            if doc_text[span.start : span.end] != entry["value"]:
                records.append(
                    RenderedFact(
                        fact_id=fact.fact_id,
                        ok=False,
                        genre=plan.genre,
                        doc_id=doc.doc_id,
                        sentence=entry["sentence"],
                        span=None,
                        reason="assembled span does not carry the value surface",
                    )
                )
                continue
            records.append(
                RenderedFact(
                    fact_id=fact.fact_id,
                    ok=True,
                    genre=plan.genre,
                    doc_id=doc.doc_id,
                    sentence=entry["sentence"],
                    span=span,
                    reason=None,
                    first_occurrence_is_slot=(
                        entry["sentence"].find(entry["value"]) == entry["slot"]
                    ),
                )
            )
            new_facts.append(_repointed_fact(fact, span))
        documents.append(Document(doc.doc_id, doc.title, doc_text))
        section_lines.append(f"[{doc.doc_id}] {doc.title}")
        section_lines.append(doc_text[:-1])  # parse_rendered re-adds the newline

    entity_lines = [
        _dump(
            {
                "entity_id": entity.entity_id,
                "label": entity.label,
                "entity_type": entity.entity_type,
            }
        )
        for entity in sorted(world.entities, key=lambda item: item.entity_id)
    ]
    fact_lines = [
        _dump(
            {
                "fact_id": fact.fact_id,
                "subject": fact.subject,
                "relation": fact.relation,
                "value": fact.value,
                "value_type": fact.value_type,
                "unit": fact.unit,
                "time": fact.time,
                "version": fact.version,
                "qualifiers": dict(fact.qualifiers),
                "spans": [span.to_dict() for span in fact.supporting_spans],
            }
        )
        for fact in sorted(new_facts, key=lambda item: item.fact_id)
    ]
    out = [
        _dump({"render": PATH_B_RENDER, "schema": SCHEMA_VERSION}),
        *scope_block.split("\n"),
        "=== ENTITIES ===",
        *entity_lines,
        "=== DOCUMENTS ===",
        *section_lines,
        "=== FACTS ===",
        *fact_lines,
    ]
    text = "\n".join(out) + "\n"

    reasons: dict[str, int] = {}
    for record in records:
        if not record.ok:
            reasons[record.reason] = reasons.get(record.reason, 0) + 1
    stats = {
        "genre": plan.genre,
        "role": plan.role,
        "purpose": plan.purpose,
        "documents": len(documents),
        "facts_total": len(world.facts),
        "facts_revalidated": len(new_facts),
        "facts_failed": len(records) - len(new_facts),
        "failure_reasons": reasons,
        "value_surface_first_occurrence_mismatches": sum(
            1 for record in records if record.ok and not record.first_occurrence_is_slot
        ),
        "model_calls": 0,
        "rng": False,
    }
    return ArtifactRendering(
        text=text,
        plan=plan,
        scope=scope,
        scope_block=scope_block,
        documents=tuple(documents),
        facts=tuple(new_facts),
        rendered_facts=tuple(records),
        stats=stats,
    )


def world_from_artifact(
    rendering: ArtifactRendering,
) -> tuple[SemanticWorld, list[ScopeEntry]]:
    """Rebuild the path-B world from its rendering text (re-validated on load)."""
    return from_rendered(rendering.text)


def parse_scope_section(text: str) -> tuple[ScopeEntry, ...]:
    """Recover the scope entries from either path's rendering text."""
    entries: list[ScopeEntry] = []
    in_scope = False
    for line in text.splitlines():
        if line == "=== SCOPE ===":
            in_scope = True
            continue
        if in_scope and line.startswith("=== ") and line.endswith(" ==="):
            break
        if in_scope and line:
            entries.append(ScopeEntry.from_dict(json.loads(line)))
    return tuple(entries)
