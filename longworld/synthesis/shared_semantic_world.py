"""P74 shared semantic world: typed objects from a frozen source snapshot.

The P73 shared world (capability_shared_world.py) merges per-family ROWS into
one context but keeps per-family solvers and per-family solve ranges: the
records families solve over the merged rows while each F family solves over
the sub-rows it was generated with, and only the generator knows which rows
those were. This module is the T1 half of the P74 charter
(.hl/design/p74_real_shared_worlds.md): ONE semantic world W built from a
frozen SourceSnapshot, queried by many tasks through explicit scope.

Layers here:
- SourceSnapshot (charter §14 v1 contract): documents, entities with mentions,
  facts with supporting spans, ungrounded relation candidates. The loader is
  schema-strict and validates fact->span integrity at load: every fact's
  supporting spans must point at real document text and must contain the fact's
  evidence (the value itself, or the target entity's label/alias), and a fact
  with no supporting span is rejected — ungrounded graph edges belong in the
  quarantined `relations` area, never in `facts`.
- SemanticWorld: typed objects, typed (entity-valued) relations, and a state
  timeline per (subject, relation): entries carry value, version,
  effective_from (the fact's `time`) and revoked_at (the qualifier of the same
  name). As-of resolution picks the latest effective entry still active.
- render()/from_rendered(): the frozen documents concatenated with an explicit
  SCOPE section (which object families, relations and documents each task
  queries) plus ENTITIES/FACTS indices. This is the charter §0.1 fix: the task
  range is recoverable from the RENDERED TEXT plus the question alone —
  restricted_to(scope) rebuilds the candidate world a scope-respecting reader
  would solve over, with no generator internals.

One additive field beyond §14 v1: an optional `entity_type` on entities (the
charter's entity schema has no type slot, but typed objects need one). This is
a declared drift point for the T4 adapter wave to adjudicate.

Simulated data only; no RNG, no network, standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "shared-semantic-world-v1"
VALUE_TYPES = ("string", "number", "entity", "rule")
_KNOWN_FACT_KEYS = (
    "fact_id",
    "subject",
    "relation",
    "value",
    "value_type",
    "unit",
    "time",
    "version",
    "qualifiers",
    "supporting_spans",
    "alternative_spans",
    "source_hash",
)
_KNOWN_ENTITY_KEYS = (
    "entity_id",
    "label",
    "aliases",
    "external_qid",
    "doc_id",
    "mentions",
    "entity_type",
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class SpanRef:
    doc_id: str
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return {"doc_id": self.doc_id, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    text: str
    sections: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "text": self.text,
            "sections": [dict(section) for section in self.sections],
        }


@dataclass(frozen=True)
class EntityMention:
    start: int
    end: int

    def to_dict(self) -> dict[str, int]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class Entity:
    entity_id: str
    label: str
    aliases: tuple[str, ...] = ()
    external_qid: str | None = None
    doc_id: str | None = None
    mentions: tuple[EntityMention, ...] = ()
    entity_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "label": self.label,
            "aliases": list(self.aliases),
            "external_qid": self.external_qid,
            "doc_id": self.doc_id,
            "mentions": [mention.to_dict() for mention in self.mentions],
            "entity_type": self.entity_type,
        }


@dataclass(frozen=True)
class Fact:
    fact_id: str
    subject: str
    relation: str
    value: Any
    value_type: str
    unit: str | None = None
    time: str | None = None
    version: str | None = None
    qualifiers: dict[str, Any] = field(default_factory=dict)
    supporting_spans: tuple[SpanRef, ...] = ()
    alternative_spans: tuple[SpanRef, ...] = ()
    source_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "subject": self.subject,
            "relation": self.relation,
            "value": self.value,
            "value_type": self.value_type,
            "unit": self.unit,
            "time": self.time,
            "version": self.version,
            "qualifiers": dict(self.qualifiers),
            "supporting_spans": [span.to_dict() for span in self.supporting_spans],
            "alternative_spans": [span.to_dict() for span in self.alternative_spans],
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True)
class RelationCandidate:
    """A graph edge that may lack text evidence: quarantined, never gold."""

    relation_id: str
    subject: str
    object: str
    relation_type: str
    qualifiers: dict[str, Any] = field(default_factory=dict)
    supporting_spans: tuple[SpanRef, ...] = ()
    grounded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "subject": self.subject,
            "object": self.object,
            "relation_type": self.relation_type,
            "qualifiers": dict(self.qualifiers),
            "supporting_spans": [span.to_dict() for span in self.supporting_spans],
            "grounded": self.grounded,
        }


@dataclass(frozen=True)
class SourceSnapshot:
    snapshot_id: str
    frozen_at: str
    source: dict[str, Any]
    documents: tuple[Document, ...]
    entities: tuple[Entity, ...]
    facts: tuple[Fact, ...]
    relations: tuple[RelationCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "frozen_at": self.frozen_at,
            "source": dict(self.source),
            "documents": [doc.to_dict() for doc in self.documents],
            "entities": [entity.to_dict() for entity in self.entities],
            "facts": [fact.to_dict() for fact in self.facts],
            "relations": [edge.to_dict() for edge in self.relations],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SourceSnapshot:
        """Load and validate one frozen snapshot; reject on any drift."""
        _require(isinstance(payload, dict), "snapshot payload must be an object")
        for key in (
            "snapshot_id",
            "frozen_at",
            "source",
            "documents",
            "entities",
            "facts",
        ):
            _require(key in payload, f"snapshot is missing {key}")
        _require(
            isinstance(payload.get("source"), dict) and "kind" in payload["source"],
            "snapshot source must carry at least a kind",
        )
        documents = tuple(_document_from_item(item) for item in payload["documents"])
        entities = tuple(_entity_from_item(item) for item in payload["entities"])
        facts = tuple(_fact_from_item(item) for item in payload["facts"])
        relations = tuple(
            _relation_from_item(item) for item in payload.get("relations", [])
        )
        snapshot = cls(
            snapshot_id=payload["snapshot_id"],
            frozen_at=payload["frozen_at"],
            source=dict(payload["source"]),
            documents=documents,
            entities=entities,
            facts=facts,
            relations=relations,
        )
        _validate(documents, entities, facts)
        return snapshot


@dataclass(frozen=True)
class ScopeEntry:
    """The explicit query range for one task, rendered into the text."""

    task_id: str
    object_families: tuple[str, ...]
    relations: tuple[str, ...]
    documents: tuple[str, ...]
    time_range: tuple[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "object_families": list(self.object_families),
            "relations": list(self.relations),
            "documents": list(self.documents),
            "time_range": list(self.time_range) if self.time_range else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ScopeEntry:
        for key in ("task_id", "object_families", "relations", "documents"):
            _require(key in payload and payload[key], f"scope entry is missing {key}")
        return cls(
            task_id=payload["task_id"],
            object_families=tuple(payload["object_families"]),
            relations=tuple(payload["relations"]),
            documents=tuple(payload["documents"]),
            time_range=(
                tuple(payload["time_range"]) if payload.get("time_range") else None
            ),
        )


@dataclass(frozen=True)
class StateEntry:
    """One timeline value: what held, from when, until when, by which fact."""

    fact_id: str
    value: Any
    version: str | None
    effective_from: str | None
    revoked_at: str | None
    spans: tuple[SpanRef, ...]


def _document_from_item(item: dict[str, Any]) -> Document:
    _require(isinstance(item, dict), "document must be an object")
    for key in ("doc_id", "title", "text"):
        _require(key in item and isinstance(item[key], str), f"document needs {key}")
    sections = tuple(dict(section) for section in item.get("sections", []))
    return Document(
        doc_id=item["doc_id"],
        title=item["title"],
        text=item["text"],
        sections=sections,
    )


def _entity_from_item(item: dict[str, Any]) -> Entity:
    _require(isinstance(item, dict), "entity must be an object")
    for key in ("entity_id", "label"):
        _require(key in item and isinstance(item[key], str), f"entity needs {key}")
    unknown = set(item) - set(_KNOWN_ENTITY_KEYS)
    _require(
        not unknown, f"entity {item['entity_id']} has unknown keys {sorted(unknown)}"
    )
    doc_id = item.get("doc_id")
    mentions = tuple(
        EntityMention(start=int(m["start"]), end=int(m["end"]))
        for m in item.get("mentions", [])
    )
    if doc_id is not None and mentions:
        _require(
            all(m.end > m.start for m in mentions),
            f"entity {item['entity_id']} has an empty mention",
        )
    return Entity(
        entity_id=item["entity_id"],
        label=item["label"],
        aliases=tuple(item.get("aliases", [])),
        external_qid=item.get("external_qid"),
        doc_id=doc_id,
        mentions=mentions,
        entity_type=item.get("entity_type"),
    )


def _spans_from(items: Any, owner: str) -> tuple[SpanRef, ...]:
    _require(isinstance(items, list), f"{owner} supporting_spans must be a list")
    spans = []
    for item in items:
        _require(
            isinstance(item, dict) and {"doc_id", "start", "end"} <= set(item),
            f"{owner} has a malformed span",
        )
        spans.append(
            SpanRef(
                doc_id=item["doc_id"], start=int(item["start"]), end=int(item["end"])
            )
        )
    return tuple(spans)


def _fact_from_item(item: dict[str, Any]) -> Fact:
    _require(isinstance(item, dict), "fact must be an object")
    for key in ("fact_id", "subject", "relation", "value", "value_type"):
        _require(key in item, f"fact is missing {key}")
    unknown = set(item) - set(_KNOWN_FACT_KEYS)
    _require(not unknown, f"fact {item['fact_id']} has unknown keys {sorted(unknown)}")
    value_type = item["value_type"]
    _require(value_type in VALUE_TYPES, f"fact {item['fact_id']} has bad value_type")
    spans = _spans_from(item.get("supporting_spans", []), item["fact_id"])
    _require(
        bool(spans),
        f"fact {item['fact_id']} has no supporting span: ungrounded edges belong "
        "in the snapshot relations area, not in facts",
    )
    return Fact(
        fact_id=item["fact_id"],
        subject=item["subject"],
        relation=item["relation"],
        value=item["value"],
        value_type=value_type,
        unit=item.get("unit"),
        time=item.get("time"),
        version=item.get("version"),
        qualifiers=dict(item.get("qualifiers", {})),
        supporting_spans=spans,
        alternative_spans=_spans_from(
            item.get("alternative_spans", []), item["fact_id"]
        ),
        source_hash=item.get("source_hash"),
    )


def _relation_from_item(item: dict[str, Any]) -> RelationCandidate:
    _require(isinstance(item, dict), "relation candidate must be an object")
    for key in ("relation_id", "subject", "object", "relation_type"):
        _require(key in item, f"relation candidate is missing {key}")
    spans = _spans_from(item.get("supporting_spans", []), item["relation_id"])
    return RelationCandidate(
        relation_id=item["relation_id"],
        subject=item["subject"],
        object=item["object"],
        relation_type=item["relation_type"],
        qualifiers=dict(item.get("qualifiers", {})),
        supporting_spans=spans,
        grounded=bool(spans) and bool(item.get("grounded", False)),
    )


def _evidence_text(fact: Fact, entities: dict[str, Entity]) -> list[str]:
    """The strings a supporting span must contain for the fact to be grounded."""
    if fact.value_type == "entity":
        target = entities.get(fact.value)
        if target is None:
            raise ValueError(
                f"fact {fact.fact_id} references unknown entity {fact.value}"
            )
        return [target.label, *target.aliases]
    return [str(fact.value)]


def _validate(
    documents: tuple[Document, ...],
    entities: tuple[Entity, ...],
    facts: tuple[Fact, ...],
) -> None:
    """Fact->span integrity: spans point at real text carrying the evidence."""
    docs = {doc.doc_id: doc for doc in documents}
    _require(len(docs) == len(documents), "duplicate doc_id in documents")
    by_id = {entity.entity_id: entity for entity in entities}
    _require(len(by_id) == len(entities), "duplicate entity_id in entities")
    fact_ids = {fact.fact_id for fact in facts}
    _require(len(fact_ids) == len(facts), "duplicate fact_id in facts")
    for fact in facts:
        _require(
            fact.subject in by_id,
            f"fact {fact.fact_id} has unknown subject {fact.subject}",
        )
        for span in fact.supporting_spans:
            _validate_span(span, docs, f"fact {fact.fact_id}")
            text = docs[span.doc_id].text[span.start : span.end]
            _require(
                any(needle in text for needle in _evidence_text(fact, by_id)),
                f"fact {fact.fact_id} span in {span.doc_id} does not carry its value",
            )
    for entity in entities:
        if entity.doc_id is None:
            continue
        _require(
            entity.doc_id in docs,
            f"entity {entity.entity_id} cites unknown doc {entity.doc_id}",
        )
        text = docs[entity.doc_id].text
        for mention in entity.mentions:
            _require(
                0 <= mention.start < mention.end <= len(text),
                f"entity {entity.entity_id} has an out-of-range mention",
            )
            _require(
                entity.label in text[mention.start : mention.end],
                f"entity {entity.entity_id} has a mention that is not its label",
            )


def _validate_span(span: SpanRef, docs: dict[str, Document], owner: str) -> None:
    _require(span.doc_id in docs, f"{owner} cites unknown doc {span.doc_id}")
    text = docs[span.doc_id].text
    _require(0 <= span.start < span.end <= len(text), f"{owner} span is out of range")


class SemanticWorld:
    """One unified world: objects, typed relations, state timelines, scopes."""

    def __init__(
        self,
        documents: tuple[Document, ...],
        entities: tuple[Entity, ...],
        facts: tuple[Fact, ...],
    ) -> None:
        _validate(documents, entities, facts)
        self.documents = documents
        self.entities = entities
        self.facts = facts
        self._docs = {doc.doc_id: doc for doc in documents}
        self.objects = {entity.entity_id: entity for entity in entities}
        self.facts_by_id = {fact.fact_id: fact for fact in facts}
        self.objects_by_family: dict[str | None, tuple[str, ...]] = {}
        for entity in entities:
            self.objects_by_family.setdefault(entity.entity_type, ())
            self.objects_by_family[entity.entity_type] = self.objects_by_family[
                entity.entity_type
            ] + (entity.entity_id,)
        timeline: dict[tuple[str, str], list[StateEntry]] = {}
        for fact in facts:
            timeline.setdefault((fact.subject, fact.relation), []).append(
                StateEntry(
                    fact_id=fact.fact_id,
                    value=fact.value,
                    version=fact.version,
                    effective_from=fact.time,
                    revoked_at=fact.qualifiers.get("revoked_at"),
                    spans=fact.supporting_spans,
                )
            )
        self.timeline = {
            key: tuple(sorted(entries, key=_entry_order))
            for key, entries in timeline.items()
        }

    @classmethod
    def from_snapshot(cls, snapshot: SourceSnapshot) -> SemanticWorld:
        return cls(snapshot.documents, snapshot.entities, snapshot.facts)

    # --- query surface used by the dependency operators ---

    def label_of(self, entity_id: str) -> str:
        return self.objects[entity_id].label

    def bind_entity(
        self,
        family: str | None = None,
        label: str | None = None,
        entity_id: str | None = None,
    ) -> Entity:
        """Bind exactly one object by family and label (or entity_id)."""
        if entity_id is not None:
            _require(entity_id in self.objects, f"unknown entity {entity_id}")
            entity = self.objects[entity_id]
        else:
            _require(family is not None and label is not None, "bind needs a label")
            matches = [
                self.objects[eid]
                for eid in self.objects_by_family.get(family, ())
                if self.objects[eid].label == label
            ]
            _require(
                len(matches) == 1,
                f"label {label!r} in family {family!r} binds {len(matches)} objects",
            )
            entity = matches[0]
        if family is not None:
            _require(
                entity.entity_type == family,
                f"entity {entity.entity_id} is not of family {family!r}",
            )
        return entity

    def lookup_fact(
        self, subject: str, relation: str, version: str | None = None
    ) -> Fact:
        """Keyed lookup: the unique fact for (subject, relation[, version])."""
        matches = [
            fact
            for fact in self.facts
            if fact.subject == subject
            and fact.relation == relation
            and (version is None or fact.version == version)
        ]
        _require(
            len(matches) == 1,
            f"({subject}, {relation}, version={version}) matches {len(matches)} facts",
        )
        return matches[0]

    def follow(self, subject: str, relation: str) -> tuple[Fact, ...]:
        """All typed relation edges from subject along relation."""
        return tuple(
            fact
            for fact in self.facts
            if fact.subject == subject
            and fact.relation == relation
            and fact.value_type == "entity"
        )

    def status_at(self, subject: str, relation: str, at: str) -> StateEntry | None:
        """As-of resolution; None when nothing is effective (and unambiguous).

        "No timeline at all" stays an error (a dangling reference is a world
        defect), while "timeline exists, nothing active at `at`" is a legal
        state the join's require_active path treats as exclusion.
        """
        entries = self.timeline.get((subject, relation), ())
        _require(bool(entries), f"no timeline for ({subject}, {relation})")
        active = [
            entry
            for entry in entries
            if (entry.effective_from is None or entry.effective_from <= at)
            and (entry.revoked_at is None or entry.revoked_at > at)
        ]
        if not active:
            return None
        best = max(active, key=_entry_order)
        tied = [entry for entry in active if _entry_order(entry) == _entry_order(best)]
        _require(
            len(tied) == 1,
            f"({subject}, {relation}) is ambiguous at {at}: "
            f"{len(tied)} entries tie for latest",
        )
        return best

    def resolve_at(self, subject: str, relation: str, at: str) -> StateEntry:
        """As-of resolution: latest effective entry still active at `at`."""
        entry = self.status_at(subject, relation, at)
        if entry is None:
            raise ValueError(f"({subject}, {relation}) has no value effective at {at}")
        return entry

    # --- rendering and the scope contract (charter §0.1) ---

    def render(self, scope_entries: tuple[ScopeEntry, ...]) -> str:
        """Frozen documents + explicit scope + entity/fact indices, as text."""
        lines = [
            _dump(
                {
                    "schema": SCHEMA_VERSION,
                    "render": "documents+scope+indices",
                }
            ),
            "=== SCOPE ===",
        ]
        for entry in sorted(scope_entries, key=lambda item: item.task_id):
            lines.append(_dump(entry.to_dict()))
        lines.append("=== ENTITIES ===")
        for entity in sorted(self.entities, key=lambda item: item.entity_id):
            lines.append(
                _dump(
                    {
                        "entity_id": entity.entity_id,
                        "label": entity.label,
                        "entity_type": entity.entity_type,
                    }
                )
            )
        lines.append("=== DOCUMENTS ===")
        for doc in self.documents:
            lines.append(f"[{doc.doc_id}] {doc.title}")
            lines.append(doc.text)
        lines.append("=== FACTS ===")
        for fact in sorted(self.facts, key=lambda item: item.fact_id):
            lines.append(_render_fact(fact))
        return "\n".join(lines) + "\n"

    def restricted_to(self, scope: ScopeEntry) -> SemanticWorld:
        """The candidate world a scope-respecting reader solves over.

        Only the rendered text's scope entry and the parsed facts are used:
        keep facts whose subject family, relation and document span all fall
        inside the scope, clip their spans to the scoped documents, and keep
        the entities those facts name. Timeline supersession survives because
        the scope keeps every fact in the scoped (family, relation, doc)
        buckets, not just the consumed ones.
        """
        docs = tuple(
            doc for doc in self.documents if doc.doc_id in set(scope.documents)
        )
        scoped_docs = {doc.doc_id for doc in docs}
        facts = []
        for fact in self.facts:
            entity = self.objects.get(fact.subject)
            family = entity.entity_type if entity else None
            spans = tuple(
                span for span in fact.supporting_spans if span.doc_id in scoped_docs
            )
            if (
                family in set(scope.object_families)
                and fact.relation in set(scope.relations)
                and bool(spans)
            ):
                facts.append(
                    Fact(
                        fact_id=fact.fact_id,
                        subject=fact.subject,
                        relation=fact.relation,
                        value=fact.value,
                        value_type=fact.value_type,
                        unit=fact.unit,
                        time=fact.time,
                        version=fact.version,
                        qualifiers=dict(fact.qualifiers),
                        supporting_spans=spans,
                        source_hash=fact.source_hash,
                    )
                )
        keep_entities = {fact.subject for fact in facts}
        for fact in facts:
            if fact.value_type == "entity":
                keep_entities.add(fact.value)
        entities = []
        for entity in self.entities:
            if entity.entity_id not in keep_entities:
                continue
            mentions = entity.mentions if entity.doc_id in scoped_docs else ()
            entities.append(
                Entity(
                    entity_id=entity.entity_id,
                    label=entity.label,
                    aliases=entity.aliases,
                    doc_id=entity.doc_id if entity.doc_id in scoped_docs else None,
                    mentions=mentions,
                    entity_type=entity.entity_type,
                )
            )
        return SemanticWorld(tuple(docs), tuple(entities), tuple(facts))


def _render_fact(fact: Fact) -> str:
    return _dump(
        {
            "fact_id": fact.fact_id,
            "subject": fact.subject,
            "relation": fact.relation,
            "value": fact.value,
            "value_type": fact.value_type,
            "unit": fact.unit,
            "time": fact.time,
            "version": fact.version,
            "qualifiers": fact.qualifiers,
            "spans": [span.to_dict() for span in fact.supporting_spans],
        }
    )


def parse_rendered(text: str) -> dict[str, Any]:
    """Parse a rendered world back into scope entries, entities, docs, facts."""
    lines = text.splitlines()
    _require(bool(lines), "empty rendered world")
    header = json.loads(lines[0])
    _require(header.get("schema") == SCHEMA_VERSION, "unknown render schema")
    section = None
    scope: list[ScopeEntry] = []
    entities: list[Entity] = []
    documents: list[Document] = []
    facts: list[Fact] = []
    doc_id: str | None = None
    doc_title = ""
    doc_lines: list[str] = []
    for line in lines[1:]:
        if line in (
            "=== SCOPE ===",
            "=== ENTITIES ===",
            "=== DOCUMENTS ===",
            "=== FACTS ===",
        ):
            _flush_doc(documents, doc_id, doc_title, doc_lines)
            doc_id, doc_title, doc_lines = None, "", []
            section = line
            continue
        if section == "=== SCOPE ===":
            scope.append(ScopeEntry.from_dict(json.loads(line)))
        elif section == "=== ENTITIES ===":
            item = json.loads(line)
            entities.append(
                Entity(
                    entity_id=item["entity_id"],
                    label=item["label"],
                    entity_type=item.get("entity_type"),
                )
            )
        elif section == "=== DOCUMENTS ===":
            if line.startswith("[") and "] " in line:
                _flush_doc(documents, doc_id, doc_title, doc_lines)
                doc_id, title = line.split("] ", 1)
                doc_id, doc_title, doc_lines = doc_id[1:], title, []
            else:
                _require(doc_id is not None, "document text before its header")
                doc_lines.append(line)
        elif section == "=== FACTS ===":
            item = json.loads(line)
            facts.append(
                Fact(
                    fact_id=item["fact_id"],
                    subject=item["subject"],
                    relation=item["relation"],
                    value=item["value"],
                    value_type=item["value_type"],
                    unit=item.get("unit"),
                    time=item.get("time"),
                    version=item.get("version"),
                    qualifiers=dict(item.get("qualifiers", {})),
                    supporting_spans=tuple(
                        SpanRef(
                            doc_id=span["doc_id"],
                            start=int(span["start"]),
                            end=int(span["end"]),
                        )
                        for span in item.get("spans", [])
                    ),
                    source_hash=item.get("source_hash"),
                )
            )
    _flush_doc(documents, doc_id, doc_title, doc_lines)
    _require(
        bool(documents) and bool(facts) and bool(scope),
        "rendered world is missing documents, facts or scope",
    )
    return {
        "header": header,
        "scope": scope,
        "entities": entities,
        "documents": documents,
        "facts": facts,
    }


def _flush_doc(
    documents: list[Document], doc_id: str | None, title: str, lines: list[str]
) -> None:
    if doc_id is None:
        return
    documents.append(
        Document(
            doc_id=doc_id,
            title=title,
            text="\n".join(lines) + "\n" if lines else "",
        )
    )


def from_rendered(text: str) -> tuple[SemanticWorld, list[ScopeEntry]]:
    """Rebuild the world (re-validated) and its scope entries from text alone."""
    parsed = parse_rendered(text)
    world = SemanticWorld(
        tuple(parsed["documents"]), tuple(parsed["entities"]), tuple(parsed["facts"])
    )
    return world, parsed["scope"]


def _entry_order(entry: StateEntry) -> tuple[str, str]:
    return (entry.effective_from or "", entry.version or "")
