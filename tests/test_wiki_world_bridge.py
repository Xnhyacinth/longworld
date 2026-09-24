"""Tests for the P74 W2-A wiki snapshot -> SemanticWorld bridge.

Two worlds under test:

- the REAL frozen observatories snapshot on the shared volume
  (data/capability_records/p74_wiki_snapshot_v1), loaded via a
  skipif-missing fixture — never copied into tests/;
- the embedded-wikitext fixture from test_wiki_adapter (built through the
  real adapter pipeline, no network) for exact-count roundtrips.

Charter coverage: .hl/design/p74_real_shared_worlds.md §16 W2-A row —
real frozen snapshot builds a SemanticWorld, >=1 dependency chain executes
on the real document text with proof spans pointing into that text, and
missing version/revocation fields degrade honestly to an unversioned
timeline.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import dependency_ops as ops
from longworld.synthesis import wiki_world_bridge as bridge
from longworld.synthesis import wiki_adapter as wa

REPO = Path(__file__).resolve().parents[1]
REAL_SNAPSHOT_PATH = (
    REPO
    / "data"
    / "capability_records"
    / "p74_wiki_snapshot_v1"
    / "astronomical_observatories_snapshot.json"
)


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def real_world():
    import json

    if not REAL_SNAPSHOT_PATH.exists():
        pytest.skip("real frozen snapshot not on this volume")
    snapshot = json.loads(REAL_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return snapshot, bridge.snapshot_to_world(snapshot)


@pytest.fixture(scope="module")
def synthetic_snapshot():
    # reuse test_wiki_adapter's fixture pipeline (real adapter, no network)
    spec = importlib.util.spec_from_file_location(
        "test_wiki_adapter_fixture", REPO / "tests" / "test_wiki_adapter.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._snapshot()


def _id_labels(world):
    return {entity.entity_id: entity.label for entity in world.entities}


def _doc_texts(world):
    return {doc.doc_id: doc.text for doc in world.documents}


# --- roundtrip: counts and quarantine exclusion -----------------------------


def test_synthetic_roundtrip_counts(synthetic_snapshot):
    world = bridge.snapshot_to_world(synthetic_snapshot)
    assert len(world.entities) == len(synthetic_snapshot["entities"])
    assert len(world.facts) == len(synthetic_snapshot["facts"])
    # relation candidates stay on the snapshot layer; the world exposes them
    # through the bridge report's relations_out counter
    report = world.bridging
    assert report["relations_out"] == len(synthetic_snapshot["relations"])
    # quarantined items never enter the world
    quarantined = {
        item["item_id"] for item in synthetic_snapshot["ungrounded_quarantine"]
    }
    assert quarantined
    assert not quarantined & {fact.fact_id for fact in world.facts}
    assert not quarantined & {entity.entity_id for entity in world.entities}
    # the category-membership relation type is absent from world facts
    assert all("member of category" != fact.relation for fact in world.facts)


def test_real_roundtrip_counts(real_world):
    snapshot, world = real_world
    report = world.bridging
    # every snapshot entity is a world object (stubs add fact-only subjects)
    assert len(world.entities) == len(snapshot["entities"]) + report["stub_entities"]
    # every grounded fact survives: the real snapshot has no spanless facts
    assert len(world.facts) == len(snapshot["facts"])
    assert report["facts_dropped_spanless"] == 0
    assert report["facts_dropped_span_invalid"] == 0
    assert report["facts_dropped_evidence_mismatch"] == 0
    assert report["relations_out"] == len(snapshot["relations"])
    assert report["snapshot_id"] == snapshot["snapshot_id"]


def test_bridge_is_deterministic(real_world):
    import json

    snapshot, world = real_world
    again = bridge.snapshot_to_world(snapshot)
    assert [fact.fact_id for fact in again.facts] == [
        fact.fact_id for fact in world.facts
    ]
    assert [entity.entity_id for entity in again.entities] == [
        entity.entity_id for entity in world.entities
    ]
    assert again.bridging == world.bridging
    assert json.dumps(again.bridging) == json.dumps(world.bridging)


def test_bridge_rejects_non_snapshots():
    with pytest.raises(wa.SnapshotError):
        bridge.snapshot_to_world({"schema_version": "wrong"})


def test_bridge_rejects_spanless_facts(synthetic_snapshot):
    payload = {
        key: value for key, value in synthetic_snapshot.items() if key != "facts"
    }
    broken = [dict(fact) for fact in synthetic_snapshot["facts"]]
    broken[0]["supporting_spans"] = []
    payload["facts"] = broken
    # the adapter's own validator rejects spanless facts before the bridge
    with pytest.raises(wa.SnapshotError):
        bridge.snapshot_to_world(payload)


# --- span integrity: world facts carry verbatim snapshot evidence -----------


@pytest.mark.parametrize("source", ["synthetic", "real"])
def test_every_world_fact_span_is_verbatim(source, synthetic_snapshot, real_world):
    if source == "synthetic":
        snapshot, world = (
            synthetic_snapshot,
            bridge.snapshot_to_world(synthetic_snapshot),
        )
    else:
        snapshot, world = real_world
    docs = _doc_texts(world)
    labels = _id_labels(world)
    aliases_by_entity = {entity.entity_id: entity.aliases for entity in world.entities}
    assert world.facts
    for fact in world.facts:
        assert fact.supporting_spans
        for span in fact.supporting_spans:
            text = docs[span.doc_id][span.start : span.end]
            if fact.value_type == "entity":
                evidence = [labels[fact.value], *aliases_by_entity[fact.value]]
                assert any(needle in text for needle in evidence), (fact.fact_id, text)
            elif fact.value_type == "number":
                assert str(fact.value) in text, (fact.fact_id, text)
            else:
                # the adapter contract: value == primary span verbatim
                assert text == fact.value, (fact.fact_id, text)


def test_real_snapshot_entity_mentions_preserve_exact_surfaces(real_world):
    _, world = real_world
    docs = _doc_texts(world)
    mentioned = 0
    for entity in world.entities:
        if entity.doc_id is None:
            continue
        text = docs[entity.doc_id]
        for mention in entity.mentions:
            assert mention.surface_form == text[mention.start : mention.end]
            mentioned += 1
    assert mentioned == 4687


# --- honest degradation: unversioned timeline -------------------------------


def test_real_timeline_is_unversioned(real_world):
    snapshot, world = real_world
    assert world.bridging["timeline"] == bridge.TIMELINE_MODE == "unversioned"
    assert all(fact.version is None for fact in world.facts)
    assert all("revoked_at" not in fact.qualifiers for fact in world.facts)
    assert snapshot["facts"][0]["version"] is None  # the source never had them


def test_real_as_of_resolves_the_single_value(real_world):
    _, world = real_world
    labels = _id_labels(world)
    aditya = next(eid for eid, label in labels.items() if label == "Aditya-L1")
    # established 2023: not yet in 2019, single value at any later date
    assert world.status_at(aditya, "established in", "2019-01-01") is None
    entry = world.resolve_at(aditya, "established in", "2026-09-01")
    assert entry.value == 2023
    assert world.resolve_at(aditya, "established in", "2023-06-01").value == 2023


def test_real_conflicting_rows_raise_instead_of_folding(real_world):
    # Apache Point's location appears in two frozen list pages with
    # different values and no time/version to order them: an unversioned
    # timeline must raise, never silently pick.
    _, world = real_world
    labels = _id_labels(world)
    apache = next(
        eid for eid, label in labels.items() if label == "Apache Point Observatory"
    )
    with pytest.raises(ValueError, match="ambiguous"):
        world.resolve_at(apache, "located in", "2026-09-01")


def test_real_bridging_report_counts(real_world):
    snapshot, world = real_world
    report = world.bridging
    assert report["mentions_dropped_alias_surface"] == 0
    assert report["mentions_kept_alias_surface"] == 1003
    assert report["mentions_kept"] == report["mentions_in"] == 4687
    assert report["entities_orphaned"] == 0
    # 10 fact-subject surfaces never matched a frozen entity: stub objects
    assert report["stub_entities"] == 10
    stub_ids = {entity.entity_id for entity in world.entities if entity.doc_id is None}
    assert len(stub_ids) == 10
    assert all(
        not entity.mentions for entity in world.entities if entity.entity_id in stub_ids
    )
    # all snapshot objects are untyped (§14 v1 has no type slot); stubs too
    assert report["entities_untyped"] == report["entities_in"]
    assert report["entities_typed"] == 0
    assert all(entity.entity_type is None for entity in world.entities)
    # 77 year facts coerced to numbers; the two decades stayed strings
    assert report["year_facts_coerced"] == 77
    assert report["quantity_coercion_failed"] == 2
    decades = [f.value for f in world.facts if f.value == "1970s" or f.value == "2010s"]
    assert sorted(decades) == ["1970s", "2010s"]
    assert report["subject_resolution"] == {
        "alias": 6,
        "exact": 169,
        "normalized": 3,
        "stub": 18,
    }
    assert report["value_resolution"] == {"canonical": 3, "exact": 32}
    assert report["mismatches"]


# --- the real dependency chain (charter §16 W2-A acceptance) -----------------


def test_real_chain_resolves_location_with_verbatim_proof_spans(real_world):
    snapshot, world = real_world
    program = bridge.as_of_location_program(world, "Aditya-L1")
    result = ops.execute(world, program)
    labels = _id_labels(world)
    # non-degenerate answer: the location entity, not the input binding
    assert labels[result.answer] == "Sun-Earth L1"
    assert result.answer != result.proof[0].ref_id
    # the chain is non-foldable: the location query's `at` key is the
    # computed establishment year, and both resolves consume the binding
    metrics = result.metrics
    assert metrics["non_foldable"] is True
    assert metrics["dependency_depth"] == 3
    assert metrics["state_transition_depth"] == 2
    assert metrics["program_length"] == 3
    # the proof's spans extract verbatim from the world's document text
    docs = _doc_texts(world)
    assert len(result.proof) == 3
    bind, established, located = result.proof
    assert bind.kind == "entity" and bind.value == "Aditya-L1"
    assert established.kind == "fact" and established.value == 2023
    assert located.kind == "fact" and labels[located.value] == "Sun-Earth L1"
    for item in result.proof:
        assert item.spans
        for span, span_text in zip(item.spans, item.span_texts):
            assert docs[span.doc_id][span.start : span.end] == span_text
    # and those documents ARE the snapshot's documents (byte-identical)
    snap_docs = {doc["doc_id"]: doc["text"] for doc in snapshot["documents"]}
    for span in located.spans:
        assert docs[span.doc_id] == snap_docs[span.doc_id]
    # the consumed facts are real frozen fact ids
    assert set(result.consumed_fact_ids) <= {fact.fact_id for fact in world.facts}


def test_real_chain_listing_follows_entity_edges(real_world):
    _, world = real_world
    program = bridge.listing_grounded_locations_program(world)
    result = ops.execute(world, program, require_fold_gate=False)
    labels = _id_labels(world)
    # 30 entity-valued includes-facility edges; 3 entity-valued located-in
    # targets survive the join (Antarctica twice: two facilities there)
    assert len(result.answer) == 3
    # join output follows the follow step's facility order (doc-415874
    # table order: Aditya-L1 first, then the two Antarctic facilities)
    assert [labels[value] for value in result.answer] == [
        "Sun-Earth L1",
        "Antarctica",
        "Antarctica",
    ]
    fact_items = [item for item in result.proof if item.kind == "fact"]
    assert len(fact_items) == 33
    docs = _doc_texts(world)
    for item in fact_items:
        for span, span_text in zip(item.spans, item.span_texts):
            assert docs[span.doc_id][span.start : span.end] == span_text
    # includes-facility rows are the frozen table rows of the list page
    listing_doc = "doc-415874"
    assert all(span.doc_id == listing_doc for span in fact_items[0].spans)


def test_real_chain_rejects_folded_variant(real_world):
    # the executor's fold gate must reject constant programs on the real
    # world exactly as on the simulated one
    _, world = real_world
    labels = _id_labels(world)
    aditya = next(eid for eid, label in labels.items() if label == "Aditya-L1")
    folded = {
        "steps": [
            {"op": "bind", "out": "obs", "entity_id": aditya},
            {
                "op": "bind",
                "out": "loc",
                "subject": "$obs",
                "relation": "located in",
            },
        ],
        "return": "loc",
    }
    with pytest.raises(ValueError, match="fold"):
        ops.execute(world, folded)


# --- synthetic-world mention adaptation --------------------------------------


def test_synthetic_mention_adaptation(synthetic_snapshot):
    world = bridge.snapshot_to_world(synthetic_snapshot)
    report = world.bridging
    # The fixture's alias-surface mention ('Schmidt camera') remains linked.
    assert report["mentions_dropped_alias_surface"] == 0
    assert report["mentions_kept_alias_surface"] == 1
    assert report["entities_orphaned"] == 0
    assert report["mentions_kept"] >= 1
    for entity in world.entities:
        if entity.doc_id is None:
            assert not entity.mentions
            continue
        text = _doc_texts(world)[entity.doc_id]
        for mention in entity.mentions:
            assert mention.surface_form == text[mention.start : mention.end]


def test_synthetic_year_coercion(synthetic_snapshot):
    world = bridge.snapshot_to_world(synthetic_snapshot)
    established = [fact for fact in world.facts if fact.relation == "established in"]
    assert established
    assert all(fact.value_type == "number" for fact in established)
    assert all(fact.value == 1904 for fact in established)
    # definition-list facts stay strings with their verbatim spans
    installed = [fact for fact in world.facts if fact.relation == "meridian circle"]
    assert installed and installed[0].value == "installed in 1904"
    docs = _doc_texts(world)
    span = installed[0].supporting_spans[0]
    assert docs[span.doc_id][span.start : span.end] == "installed in 1904"
