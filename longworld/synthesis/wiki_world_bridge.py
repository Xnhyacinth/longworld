"""P74 W2-A: bridge a frozen wiki SourceSnapshot into the shared SemanticWorld.

The wiki adapter (wiki_adapter.py, T4) and the shared semantic world
(shared_semantic_world.py, T1) were written against the same charter §14
contract by different agents; this module is the reconciliation layer that
turns the adapter's frozen snapshot dicts into a SemanticWorld WITHOUT
rewriting either side.  Every contract drift between the two is adapted
here, counted in a :class:`BridgeReport`, and summarized as human-readable
mismatch notes:

- value vocabulary: the wiki value types are ``entity/string/quantity/
  year/date/geo``; the world only knows ``string/number/entity/rule``.
  ``year`` -> ``number`` (int), ``quantity`` -> ``number`` (float) when the
  surface parses, ``date``/``geo`` -> ``string``; decades like ``1970s``
  fail float coercion and stay strings.
- identity: wiki facts name subjects and values as raw surface strings
  (labels, aliases, ``[url label]`` wrappers, trailing parentheticals); the
  world requires entity ids.  Surfaces resolve label -> alias -> normalized
  label; unresolved fact subjects and entity values become stub entities
  (id-hashed from the surface, no doc anchor, no mentions).
- mentions: preserve the exact surface form of every adapter mention,
  including link surfaces that differ from the entity label.
- types: the wiki snapshot has no ``entity_type`` (§14 v1 has no type slot),
  so every world object is untyped and bind-by-family is unavailable —
  binds use label/entity_id only.  A declared drift point for the T4 wave.
- timeline: wiki facts carry no ``version`` and no ``revoked_at``; every
  timeline is unversioned (``timeline: "unversioned"``).  As-of resolution
  still works — the fact ``time`` becomes ``effective_from`` — and
  conflicting duplicate rows surface as honest ambiguity errors from
  resolve_at rather than a silent pick.
- documents: per-page ``page_url``/``revision_url`` fields are dropped from
  world documents (the world Document has four fields); the license block
  and the pinned per-title revisions stay in ``source``.
- alternative spans: the world loader re-verifies each alternative span by
  exact text equality against the bridged value (an entity id or an int),
  which a raw text span cannot satisfy after coercion, so only spans that
  still verify verbatim survive.

Quarantined snapshot items (category membership, spanless candidates)
never enter the world.  The bridge validates its input with the adapter's
own validator first: only genuine frozen v1 snapshots are accepted.

Determinism: all outputs are content-addressed and sorted; no RNG, no
network, standard library only.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from longworld.synthesis import shared_semantic_world as ssw
from longworld.synthesis import wiki_adapter

TIMELINE_MODE = "unversioned"
_STUB_SALT = "wiki-world-bridge-stub"
_LINK_WRAPPER_RE = re.compile(r"^\[https?://\S+\s+([^\]]+)\]$")
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")
_WIKI_TO_WORLD_TYPE = {
    "entity": "entity",
    "string": "string",
    "date": "string",
    "geo": "string",
}

__all__ = [
    "BridgeReport",
    "TIMELINE_MODE",
    "build_adapted_snapshot",
    "snapshot_to_world",
    "structural_families",
    "structurally_typed_world",
    "as_of_location_program",
    "listing_grounded_locations_program",
]


# ---------------------------------------------------------------------------
# Report: every adaptation decision, counted
# ---------------------------------------------------------------------------


@dataclass
class BridgeReport:
    """Honest accounting of everything the bridge changed or dropped."""

    snapshot_id: str = ""
    timeline: str = TIMELINE_MODE
    # entities
    entities_in: int = 0
    entities_out: int = 0
    entities_typed: int = 0
    entities_untyped: int = 0
    stub_entities: int = 0
    stub_labels: tuple[str, ...] = ()
    ambiguous_labels: tuple[str, ...] = ()
    # mentions
    mentions_in: int = 0
    mentions_kept: int = 0
    mentions_dropped_alias_surface: int = 0
    mentions_kept_alias_surface: int = 0
    entities_orphaned: int = 0
    # facts
    facts_in: int = 0
    facts_out: int = 0
    facts_dropped_spanless: int = 0
    facts_dropped_span_invalid: int = 0
    facts_dropped_evidence_mismatch: int = 0
    alternative_spans_dropped: int = 0
    facts_versioned: int = 0
    facts_revoked: int = 0
    facts_with_time: int = 0
    subject_resolution: dict[str, int] = field(default_factory=dict)
    value_resolution: dict[str, int] = field(default_factory=dict)
    year_facts_coerced: int = 0
    quantity_facts_coerced: int = 0
    quantity_coercion_failed: int = 0
    # relations
    relations_in: int = 0
    relations_out: int = 0
    relation_endpoints_unresolved: int = 0
    # the reconciliation notes (contract drift handled by this bridge)
    mismatches: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "snapshot_id": self.snapshot_id,
            "timeline": self.timeline,
            "entities_in": self.entities_in,
            "entities_out": self.entities_out,
            "entities_typed": self.entities_typed,
            "entities_untyped": self.entities_untyped,
            "stub_entities": self.stub_entities,
            "stub_labels": list(self.stub_labels),
            "ambiguous_labels": list(self.ambiguous_labels),
            "mentions_in": self.mentions_in,
            "mentions_kept": self.mentions_kept,
            "mentions_dropped_alias_surface": self.mentions_dropped_alias_surface,
            "mentions_kept_alias_surface": self.mentions_kept_alias_surface,
            "entities_orphaned": self.entities_orphaned,
            "facts_in": self.facts_in,
            "facts_out": self.facts_out,
            "facts_dropped_spanless": self.facts_dropped_spanless,
            "facts_dropped_span_invalid": self.facts_dropped_span_invalid,
            "facts_dropped_evidence_mismatch": self.facts_dropped_evidence_mismatch,
            "alternative_spans_dropped": self.alternative_spans_dropped,
            "facts_versioned": self.facts_versioned,
            "facts_revoked": self.facts_revoked,
            "facts_with_time": self.facts_with_time,
            "subject_resolution": dict(sorted(self.subject_resolution.items())),
            "value_resolution": dict(sorted(self.value_resolution.items())),
            "year_facts_coerced": self.year_facts_coerced,
            "quantity_facts_coerced": self.quantity_facts_coerced,
            "quantity_coercion_failed": self.quantity_coercion_failed,
            "relations_in": self.relations_in,
            "relations_out": self.relations_out,
            "relation_endpoints_unresolved": self.relation_endpoints_unresolved,
            "mismatches": list(self.mismatches),
        }
        return payload


# ---------------------------------------------------------------------------
# Surface -> entity-id resolution
# ---------------------------------------------------------------------------


def _stub_id(surface: str) -> str:
    digest = hashlib.sha256(f"{_STUB_SALT}|{surface}".encode("utf-8")).hexdigest()
    return "e_" + digest[:16]


def _normalize_surface(surface: str) -> str:
    """Peel wiki junk off a surface: '[url label]' wrappers, trailing '(…)'."""
    text = surface.strip()
    link = _LINK_WRAPPER_RE.match(text)
    if link:
        text = link.group(1)
    return _TRAILING_PAREN_RE.sub("", text).strip()


class _Resolver:
    """Deterministic surface -> entity-id resolution with outcome counting."""

    def __init__(self, entities: list[dict[str, Any]]) -> None:
        label_counts = Counter(entity["label"] for entity in entities)
        self.ambiguous_labels = tuple(
            sorted(label for label, count in label_counts.items() if count > 1)
        )
        self.by_label = {
            entity["label"]: entity["entity_id"]
            for entity in entities
            if label_counts[entity["label"]] == 1
        }
        alias_counts: Counter = Counter()
        alias_owner: dict[str, str] = {}
        for entity in entities:
            for alias in entity.get("aliases") or []:
                alias_counts[alias] += 1
                alias_owner.setdefault(alias, entity["entity_id"])
        self.by_alias = {
            alias: owner
            for alias, owner in alias_owner.items()
            if alias_counts[alias] == 1
        }

    def resolve(self, surface: str) -> tuple[str | None, str]:
        """(entity_id, outcome); outcome in exact/alias/normalized/unresolved."""
        if surface in self.by_label:
            return self.by_label[surface], "exact"
        if surface in self.by_alias:
            return self.by_alias[surface], "alias"
        normalized = _normalize_surface(surface)
        if normalized and normalized != surface and normalized in self.by_label:
            return self.by_label[normalized], "normalized"
        return None, "unresolved"


def _span_in_range(span: dict[str, Any], docs: dict[str, dict[str, Any]]) -> bool:
    doc = docs.get(span.get("doc_id"))
    start, end = span.get("start"), span.get("end")
    return (
        doc is not None
        and isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < end <= len(doc["text"])
    )


def _entity_evidence(
    entity: dict[str, Any], stubs: dict[str, dict[str, Any]]
) -> list[str]:
    """The strings a supporting span must carry for an entity-valued fact."""
    if entity["entity_id"] in stubs:
        return [entity["label"]]
    return [entity["label"], *entity.get("aliases", [])]


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------


def build_adapted_snapshot(
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], BridgeReport]:
    """Adapt one validated wiki §14 snapshot dict to the world loader contract.

    Returns (adapted payload for ssw.SourceSnapshot.from_dict, report).  The
    input is validated with the wiki adapter's own validator first, so only
    genuine frozen v1 snapshots are bridged; nothing is mutated in place.
    """
    wiki_adapter.validate_snapshot(snapshot)
    report = BridgeReport(snapshot_id=snapshot.get("snapshot_id", ""))
    docs = {doc["doc_id"]: doc for doc in snapshot["documents"]}

    # --- entities: preserve each mention's exact surface, including aliases
    resolver = _Resolver(snapshot["entities"])
    report.ambiguous_labels = resolver.ambiguous_labels
    entities: dict[str, dict[str, Any]] = {}
    for entity in snapshot["entities"]:
        report.entities_in += 1
        text = docs[entity["doc_id"]]["text"]
        kept: list[dict[str, Any]] = []
        for mention in entity.get("mentions") or []:
            report.mentions_in += 1
            surface = text[mention["start"] : mention["end"]]
            kept.append(
                {
                    "start": mention["start"],
                    "end": mention["end"],
                    "surface_form": surface,
                }
            )
            report.mentions_kept += 1
            if entity["label"] not in surface:
                report.mentions_kept_alias_surface += 1
        if entity.get("mentions") and not kept:
            report.entities_orphaned += 1
        if entity.get("entity_type"):
            report.entities_typed += 1
        else:
            report.entities_untyped += 1
        entities[entity["entity_id"]] = {
            "entity_id": entity["entity_id"],
            "label": entity["label"],
            "aliases": list(entity.get("aliases") or []),
            "external_qid": entity.get("external_qid"),
            "doc_id": entity["doc_id"],
            "mentions": kept,
            "entity_type": entity.get("entity_type"),
        }

    # --- stub entities for surfaces the world needs as object ids
    stubs: dict[str, dict[str, Any]] = {}

    def _stub(surface: str) -> str:
        label = surface.strip()
        entity_id = _stub_id(label)
        if entity_id not in stubs:
            stubs[entity_id] = {
                "entity_id": entity_id,
                "label": label,
                "aliases": [],
                "external_qid": None,
                "doc_id": None,
                "mentions": [],
                "entity_type": None,
            }
        return entity_id

    def _resolve_subject(surface: str) -> tuple[str, str]:
        entity_id, outcome = resolver.resolve(surface)
        if entity_id is None:
            return _stub(surface), "stub"
        return entity_id, outcome

    def _resolve_value(fact: dict[str, Any]) -> tuple[str, str]:
        qualifiers = fact.get("qualifiers") or {}
        canonical = qualifiers.get("canonical_entity")
        if isinstance(canonical, str) and canonical in resolver.by_label:
            return resolver.by_label[canonical], "canonical"
        entity_id, outcome = resolver.resolve(fact["value"])
        if entity_id is None:
            return _stub(fact["value"]), "stub"
        return entity_id, outcome

    # --- facts: id subjects, coerced values, verified spans
    adapted_facts: list[dict[str, Any]] = []
    for fact in snapshot["facts"]:
        report.facts_in += 1
        spans = [dict(span) for span in fact.get("supporting_spans") or []]
        if not spans:
            report.facts_dropped_spanless += 1
            continue
        if not all(_span_in_range(span, docs) for span in spans):
            report.facts_dropped_span_invalid += 1
            continue
        subject, outcome = _resolve_subject(fact["subject"])
        report.subject_resolution[outcome] = (
            report.subject_resolution.get(outcome, 0) + 1
        )
        wiki_type = fact["value_type"]
        value: Any = fact["value"]
        unit = fact.get("unit")
        if wiki_type == "entity":
            value, outcome = _resolve_value(fact)
            report.value_resolution[outcome] = (
                report.value_resolution.get(outcome, 0) + 1
            )
            world_type = "entity"
            unit = None
        elif wiki_type == "year":
            value = int(fact["value"])
            world_type = "number"
            report.year_facts_coerced += 1
            unit = None
        elif wiki_type == "quantity":
            try:
                value = float(fact["value"])
                world_type = "number"
                report.quantity_facts_coerced += 1
            except ValueError:
                world_type = "string"
                report.quantity_coercion_failed += 1
                unit = None
        else:
            world_type = _WIKI_TO_WORLD_TYPE[wiki_type]
            unit = None
        if fact.get("version"):
            report.facts_versioned += 1
        if (fact.get("qualifiers") or {}).get("revoked_at"):
            report.facts_revoked += 1
        if fact.get("time"):
            report.facts_with_time += 1

        # mirror the world loader's span evidence check so nothing enters
        # the world that the loader would reject after coercion
        if world_type == "entity":
            target = entities.get(value) or stubs[value]
            evidence = _entity_evidence(target, stubs)
        else:
            evidence = [str(value)]
        ok = all(
            any(
                needle in docs[span["doc_id"]]["text"][span["start"] : span["end"]]
                for needle in evidence
            )
            for span in spans
        )
        if not ok:
            report.facts_dropped_evidence_mismatch += 1
            continue

        # alternative spans only survive when they still verify verbatim
        # against the bridged value (the loader checks exact equality)
        alternative = []
        for span in fact.get("alternative_spans") or []:
            if (
                world_type == "string"
                and _span_in_range(span, docs)
                and docs[span["doc_id"]]["text"][span["start"] : span["end"]] == value
            ):
                alternative.append(dict(span))
            else:
                report.alternative_spans_dropped += 1

        adapted_facts.append(
            {
                "fact_id": fact["fact_id"],
                "subject": subject,
                "relation": fact["relation"],
                "value": value,
                "value_type": world_type,
                "unit": unit,
                "time": fact.get("time"),
                "version": fact.get("version"),
                "qualifiers": dict(fact.get("qualifiers") or {}),
                "supporting_spans": spans,
                "alternative_spans": alternative,
                "source_hash": fact.get("source_hash"),
            }
        )

    # --- relation candidates: ids where resolvable, raw surface otherwise
    adapted_relations: list[dict[str, Any]] = []
    for relation in snapshot.get("relations") or []:
        report.relations_in += 1
        subject, _ = resolver.resolve(relation["subject"])
        obj, _ = resolver.resolve(relation["object"])
        if subject is None or obj is None:
            report.relation_endpoints_unresolved += 1
        spans = [dict(span) for span in relation.get("supporting_spans") or []]
        adapted_relations.append(
            {
                "relation_id": relation["relation_id"],
                "subject": subject or relation["subject"],
                "object": obj or relation["object"],
                "relation_type": relation["relation_type"],
                "qualifiers": dict(relation.get("qualifiers") or {}),
                "supporting_spans": spans,
                "grounded": bool(spans),
            }
        )
        report.relations_out += 1

    all_entities = {**entities, **stubs}
    report.stub_entities = len(stubs)
    report.stub_labels = tuple(sorted(stub["label"] for stub in stubs.values()))
    report.entities_out = len(all_entities)
    report.facts_out = len(adapted_facts)
    report.mismatches = _MISMATCH_NOTES

    payload = {
        "snapshot_id": snapshot["snapshot_id"],
        "frozen_at": snapshot["frozen_at"],
        "source": dict(snapshot["source"]),
        "documents": [
            {
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "text": doc["text"],
                "sections": [dict(section) for section in doc.get("sections") or []],
            }
            for doc in snapshot["documents"]
        ],
        "entities": [all_entities[key] for key in sorted(all_entities)],
        "facts": sorted(adapted_facts, key=lambda item: item["fact_id"]),
        "relations": adapted_relations,
    }
    return payload, report


def snapshot_to_world(snapshot: dict[str, Any]) -> ssw.SemanticWorld:
    """Bridge one frozen wiki snapshot dict into a SemanticWorld.

    The returned world carries ``world.bridging``: the BridgeReport dict,
    whose ``timeline`` key is ``"unversioned"`` — the honest note that wiki
    facts have no version/revocation history, so as-of queries resolve to
    the single recorded value (and conflicts raise instead of folding).
    """
    payload, report = build_adapted_snapshot(snapshot)
    loaded = ssw.SourceSnapshot.from_dict(payload)
    world = ssw.SemanticWorld.from_snapshot(loaded)
    world.bridging = report.to_dict()  # type: ignore[attr-defined]
    return world


def _family_name(signature: frozenset[str]) -> str:
    """A short structural name derived only from relation names."""
    core = "+".join(sorted(signature))
    if len(core) > 60:
        digest = hashlib.sha256(core.encode("utf-8")).hexdigest()[:8]
        core = core[:48] + "..." + digest
    return core


def structural_families(world: ssw.SemanticWorld) -> dict[str, str]:
    """Group linked objects by relation shape, without semantic type claims."""
    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    for fact in world.facts:
        outgoing.setdefault(fact.subject, set()).add(fact.relation)
        if fact.value_type == "entity":
            incoming.setdefault(fact.value, set()).add(fact.relation)
    signatures = {entity_id: frozenset(rels) for entity_id, rels in outgoing.items()}
    distinct = sorted(set(signatures.values()), key=lambda s: sorted(s))
    maximal = [s for s in distinct if not any(s < t for t in distinct)]

    families: dict[str, str] = {}
    for entity in world.entities:
        signature = signatures.get(entity.entity_id)
        if signature is not None:
            for candidate in maximal:
                if signature <= candidate:
                    families[entity.entity_id] = "s:" + _family_name(candidate)
                    break
        elif incoming.get(entity.entity_id):
            families[entity.entity_id] = "o:" + _family_name(
                frozenset(incoming[entity.entity_id])
            )
    return families


def structurally_typed_world(
    world: ssw.SemanticWorld,
) -> tuple[ssw.SemanticWorld, dict[str, Any]]:
    """Make fact-linked objects bindable by shape for the task bank.

    The assigned `entity_type` is a structural family key, not an inferred
    semantic class such as observatory or instrument.
    """
    families = structural_families(world)
    subjects = {f.subject for f in world.facts}
    objects = {f.value for f in world.facts if f.value_type == "entity"}
    stats = {
        "rule_subject_family": sum(
            1 for e in world.entities if e.entity_id in subjects
        ),
        "rule_object_role_family": sum(
            1
            for e in world.entities
            if e.entity_id not in subjects and e.entity_id in objects
        ),
        "unlinked_untyped": sum(
            1 for e in world.entities if e.entity_id not in families
        ),
        "families": len(set(families.values())),
        "type_basis": "relation_shape",
    }
    entities = tuple(
        ssw.Entity(
            e.entity_id,
            e.label,
            e.aliases,
            e.external_qid,
            e.doc_id,
            e.mentions,
            families.get(e.entity_id),
        )
        for e in world.entities
    )
    typed = ssw.SemanticWorld(world.documents, entities, world.facts)
    typed.bridging = getattr(world, "bridging", None)  # type: ignore[attr-defined]
    return typed, stats


_MISMATCH_NOTES = (
    "value vocabulary: wiki year/quantity -> world number (int/float), "
    "date/geo -> string; non-numeric quantities ('1970s') stay strings",
    "identity: fact subjects/values are wiki surface strings; resolved "
    "label -> alias -> normalized label, unresolved surfaces become stub "
    "objects with no doc anchor",
    "mentions: exact alias surfaces are preserved and validated against "
    "their anchored document spans",
    "types: the snapshot has no entity_type (§14 v1 has no slot), so world "
    "objects are untyped and bind-by-family is unavailable",
    "timeline: no version and no revoked_at in wiki facts; every timeline "
    "is unversioned and as-of ties raise honest ambiguity errors",
    "documents: per-page page_url/revision_url fields are dropped from "
    "world documents; the license block and pinned revisions stay in source",
    "alternative spans: the world loader verifies them against the bridged "
    "value by exact equality, which coerced values cannot satisfy; only "
    "verbatim-verified string spans survive",
    "quarantine: category membership and spanless candidates never enter "
    "the world (enforced upstream by the adapter validator and re-checked "
    "here)",
)


# ---------------------------------------------------------------------------
# Dependency chains over the bridged world (charter §5, W2-A acceptance)
# ---------------------------------------------------------------------------


def _id_for_label(world: "ssw.SemanticWorld", label: str) -> str:
    """Resolve a label to the entity id the bind op will accept.

    The bind op passes label/entity_id params through literally (no $-ref
    resolution), and world.bind_entity requires a family for label binds —
    but bridged wiki objects are untyped.  Chain constructors therefore
    resolve labels to entity ids up front and bind by entity_id.
    """
    matches = [e.entity_id for e in world.entities if e.label == label]
    if len(matches) != 1:
        raise ValueError(f"label {label!r} binds {len(matches)} bridged objects")
    return matches[0]


def as_of_location_program(
    world: "ssw.SemanticWorld", label: str, as_of: str = "2026-09-01"
) -> dict[str, Any]:
    """A non-foldable chain: bind an object, resolve its location as of a
    date derived from its establishment.

    Steps 2 and 3 take their SUBJECT from step 1's binding, and step 3's
    ``at`` — a query KEY — comes from step 2's computed state resolution,
    so the program cannot fold to a flat AND of constants.  Over the real
    observatories snapshot this answers "where is Aditya-L1 as of its
    establishment?" with the entity 'Sun-Earth L1', proved by spans into
    the frozen table rows.

    The ``at`` binding is the establishment YEAR (an int after year
    coercion); it only ever compares against ``effective_from`` strings
    when a timeline entry carries a time, and located-in entries in the
    wiki snapshot never do (time=None means effective-from-forever).
    """
    return {
        "steps": [
            {
                "op": "bind",
                "out": "observatory",
                "entity_id": _id_for_label(world, label),
            },
            {
                "op": "resolve_version",
                "out": "established",
                "subject": "$observatory",
                "relation": "established in",
                "at": as_of,
            },
            {
                "op": "resolve_version",
                "out": "location",
                "subject": "$observatory",
                "relation": "located in",
                "at": "$established",
            },
        ],
        "return": "location",
    }


def listing_grounded_locations_program(
    world: "ssw.SemanticWorld",
    listing_label: str = "List of astronomical observatories",
) -> dict[str, Any]:
    """Bind a listing, follow its entity edges, keep the grounded targets.

    The join's left set comes from the follow step (a consuming step), and
    every matched fact enters the proof with spans into the frozen table
    rows.  This is a constant-key structure query — it compiles with
    require_fold_gate=False, mirroring how plain locate tasks use the same
    executor path.  A structural probe that the bridged entity edges
    support set-valued operations on real data: over the real snapshot the
    follow reaches 27 facilities and the join keeps the 3 whose located-in
    edge is entity-valued.
    """
    return {
        "steps": [
            {
                "op": "bind",
                "out": "listing",
                "entity_id": _id_for_label(world, listing_label),
            },
            {
                "op": "follow_relation",
                "out": "facilities",
                "subject": "$listing",
                "relation": "includes facility",
            },
            {
                "op": "join_on_bound_result",
                "out": "grounded_locations",
                "left": "$facilities",
                "relation": "located in",
            },
        ],
        "return": "grounded_locations",
    }
