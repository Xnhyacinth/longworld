"""Tests for the T4 wiki SourceSnapshot adapter (longworld.synthesis.wiki_adapter).

All fixtures are embedded wikitext — no network in tests.  The fixture page
is a representative MediaWiki article: an infobox, a wikitable with a
name/established/location schema, a bulleted list, definition-list items,
headings, bold/linked prose, and one deliberately ungrounded relation
(category membership, which never has a body span).
"""

from __future__ import annotations

import json

import pytest

from longworld.synthesis import wiki_adapter as wa


FIXTURE_TITLE = "Testhill Observatory"
FIXTURE_WIKITEXT = """{{short description|Astronomical observatory in Testville}}
{{Infobox Observatory
| name            = Testhill Observatory
| organization    = [[Northland Astronomy Council]]
| code            = 771
| location        = [[Testville]], [[Northland]]
| established     = 1904
| altitude        = {{convert|1200|m|ft}}
}}

The '''Testhill Observatory''' is an astronomical observatory located in [[Testville]], [[Northland]]. It was established in 1904 on the Testhill ridge.

== Telescopes ==
{| class="wikitable"
! Telescope !! Aperture !! Year
|-
| [[Meridian telescope]] || 0.25 m || 1904
|-
| [[Reflector telescope]] || 1.0 m || 1927
|-
| [[Testhill Schmidt camera|Schmidt camera]] || 0.5 m || 1950
|}

== Directors ==
* [[Ada Meridian]] (1904–1920)
* [[Bob Zenith]] (1920–1946)

== Instruments ==
; Meridian circle: installed in 1904
; Chronograph: installed in 1906
"""

CATEGORY = "Category:Test observatories"


def _page_record() -> wa.PageRecord:
    return wa.PageRecord(
        pageid=9001,
        title=FIXTURE_TITLE,
        revid=123456,
        timestamp="2026-01-02T03:04:05Z",
        wikitext=FIXTURE_WIKITEXT,
        pageprops={"wikibase_item": "Q9001", "wikibase-shortdesc": "test fixture"},
    )


def _link_meta() -> list[wa.LinkMeta]:
    return [
        wa.LinkMeta(pageid=101, title="Testville", wikibase_item="Q101"),
        wa.LinkMeta(pageid=102, title="Northland", wikibase_item="Q102"),
        wa.LinkMeta(
            pageid=103, title="Northland Astronomy Council", wikibase_item="Q103"
        ),
        wa.LinkMeta(pageid=104, title="Meridian telescope", wikibase_item=None),
        wa.LinkMeta(pageid=105, title="Reflector telescope", wikibase_item=None),
        wa.LinkMeta(pageid=106, title="Ada Meridian", wikibase_item=None),
        wa.LinkMeta(pageid=107, title="Bob Zenith", wikibase_item=None),
    ]


def _rights() -> dict[str, str]:
    return {
        "text": "Creative Commons Attribution-Share Alike 4.0",
        "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
        "page_url_prefix": "https://en.wikipedia.org/wiki/",
    }


def _snapshot() -> dict:
    page = _page_record()
    return wa.build_snapshot(
        members=[wa.Member(pageid=page.pageid, title=page.title)],
        pages={page.pageid: page},
        link_meta=_link_meta(),
        rights=_rights(),
        category_title=CATEGORY,
        frozen_at="2026-09-23T00:00:00Z",
    )


def test_explicit_title_bundle_does_not_claim_category_membership() -> None:
    page = _page_record()
    snapshot = wa.build_snapshot(
        members=[wa.Member(pageid=page.pageid, title=page.title)],
        pages={page.pageid: page},
        link_meta=_link_meta(),
        rights=_rights(),
        category_title="Observatory lists bundle",
        collection_kind="title_bundle",
        frozen_at="2026-09-23T00:00:00Z",
    )
    assert snapshot["source"]["category"] is None
    assert snapshot["source"]["collection_kind"] == "title_bundle"
    assert snapshot["source"]["kind"] == "mediawiki_title_bundle"
    assert not any(
        item["relation"] == "member of category"
        for item in snapshot["ungrounded_quarantine"]
    )
    wa.snapshot_from_dict(snapshot)


def test_foundation_year_table_header_requires_a_plain_year() -> None:
    assert wa._cell_semantics("Year offoundation", "1872") == (
        "established in",
        "1872",
        None,
    )
    assert wa._cell_semantics("Year offoundation", "Aberystwyth") is None


def test_snapshot_identity_changes_when_extraction_changes() -> None:
    snapshot = _snapshot()
    source = snapshot["source"]
    docs = snapshot["documents"]
    facts = snapshot["facts"]
    label = source["collection_label"]
    original = wa._snapshot_id(label, source, docs, facts)
    changed_fact = [*facts[:-1], {**facts[-1], "relation": "corrected relation"}]
    assert wa._snapshot_id(label, source, docs, changed_fact) != original
    changed_docs = [*docs[:-1], {**docs[-1], "text": docs[-1]["text"] + "\n"}]
    assert wa._snapshot_id(label, source, changed_docs, facts) != original
    assert (
        wa._snapshot_id(label, {**source, "kind": wa.TITLE_BUNDLE_KIND}, docs, facts)
        != original
    )


def test_repeated_table_header_changes_the_active_column_meaning() -> None:
    text = (
        "# List of test hospitals\n"
        "Name (other name) | Location | Established\n"
        "Alpha Hospital | Athens | 1900\n"
        "Name (other name) | Location | Established/New building\n"
        "Beta Hospital | Athens | 2000\n"
    )
    table_lines = [line for line in wa.structured_lines(text) if line.cells]
    assert [line.kind for line in table_lines] == [
        "table_header",
        "table_row",
        "table_header",
        "table_row",
    ]
    page = wa.PageRecord(
        pageid=72,
        title="List of test hospitals",
        revid=7201,
        timestamp="2026-09-24T00:00:00Z",
        wikitext=(
            '{| class="wikitable"\n'
            "! Name (other name) !! Location !! Established\n"
            "|-\n| Alpha Hospital || Athens || 1900\n"
            "|}\n"
            '{| class="wikitable"\n'
            "! Name (other name) !! Location !! Established/New building\n"
            "|-\n| Beta Hospital || Athens || 2000\n"
            "|}\n"
        ),
    )
    snapshot = wa.build_snapshot(
        members=[wa.Member(page.pageid, page.title)],
        pages={page.pageid: page},
        link_meta=[],
        rights=_rights(),
        category_title="test hospital lists",
        collection_kind="title_bundle",
        frozen_at="2026-09-24T00:00:00Z",
    )
    founded = {
        (fact["subject"], fact["value"])
        for fact in snapshot["facts"]
        if fact["relation"] == "established in"
    }
    assert len(founded) == 1
    assert next(iter(founded))[1] == "1900"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_emits_title_infobox_table_and_list_structure() -> None:
    rendered = wa.render_wikitext(FIXTURE_TITLE, FIXTURE_WIKITEXT)
    lines = rendered.text.splitlines()
    assert lines[0] == f"# {FIXTURE_TITLE}"
    assert "organization: Northland Astronomy Council" in lines
    assert "established: 1904" in lines
    # table header row then data rows, pipe-joined
    assert "Telescope | Aperture | Year" in lines
    assert "Meridian telescope | 0.25 m | 1904" in lines
    # prose with bold stripped and links resolved to display text (one
    # rendered line per wikitext paragraph, not per sentence)
    prose = " ".join(lines)
    assert (
        "The Testhill Observatory is an astronomical observatory located in "
        "Testville, Northland. It was established in 1904 on the Testhill ridge."
        in prose
    )
    # bullets render as dash items
    assert "- Ada Meridian (1904–1920)" in lines
    # definition list renders as '- term: definition'
    assert "- Meridian circle: installed in 1904" in lines


def test_sections_cover_the_whole_text_in_order() -> None:
    rendered = wa.render_wikitext(FIXTURE_TITLE, FIXTURE_WIKITEXT)
    sections = rendered.sections
    titles = [section["title"] for section in sections]
    assert titles[0] == ""  # lead section
    assert "Telescopes" in titles and "Directors" in titles
    assert sections[0]["start"] == 0
    assert sections[-1]["end"] == len(rendered.text)
    starts = [section["start"] for section in sections]
    assert starts == sorted(starts)
    for left, right in zip(sections, sections[1:]):
        assert left["end"] == right["start"]


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def test_parse_members_payload_and_revision_payload_shapes() -> None:
    members = wa.parse_members_payload(
        {
            "query": {
                "categorymembers": [
                    {"pageid": 9001, "title": FIXTURE_TITLE, "ns": 0, "type": "page"}
                ]
            }
        }
    )
    assert members == [wa.Member(pageid=9001, title=FIXTURE_TITLE)]
    payload = {
        "query": {
            "pages": [
                {
                    "pageid": 9001,
                    "ns": 0,
                    "title": FIXTURE_TITLE,
                    "pageprops": {"wikibase_item": "Q9001"},
                    "revisions": [
                        {
                            "revid": 123456,
                            "parentid": 123455,
                            "timestamp": "2026-01-02T03:04:05Z",
                            "slots": {
                                "main": {
                                    "contentmodel": "wikitext",
                                    "contentformat": "text/x-wiki",
                                    "content": FIXTURE_WIKITEXT,
                                }
                            },
                        }
                    ],
                }
            ]
        }
    }
    page = wa.parse_revision_payload(payload)
    assert page.revid == 123456
    assert page.wikitext == FIXTURE_WIKITEXT
    assert page.pageprops["wikibase_item"] == "Q9001"


def test_parse_payloads_reject_malformed_input() -> None:
    with pytest.raises(wa.SnapshotError):
        wa.parse_members_payload({"query": {"categorymembers": []}})
    with pytest.raises(wa.SnapshotError):
        wa.parse_members_payload({"query": {"categorymembers": [{"pageid": 0}]}})
    with pytest.raises(wa.SnapshotError):
        wa.parse_revision_payload({"query": {"pages": []}})
    with pytest.raises(wa.SnapshotError):
        wa.parse_revision_payload(
            {"query": {"pages": [{"pageid": 1, "title": "x", "revisions": []}]}}
        )
    with pytest.raises(wa.SnapshotError):
        wa.parse_links_payload({"query": {"pages": "nope"}})
    with pytest.raises(wa.SnapshotError):
        wa.parse_rightsinfo_payload({"query": {}})


# ---------------------------------------------------------------------------
# Snapshot contract (§14)
# ---------------------------------------------------------------------------


def test_snapshot_has_the_charter_fields() -> None:
    snapshot = _snapshot()
    assert snapshot["schema_version"] == "longworld.source-snapshot.v1"
    for field in (
        "snapshot_id",
        "frozen_at",
        "source",
        "documents",
        "entities",
        "facts",
        "relations",
        "ungrounded_quarantine",
    ):
        assert field in snapshot
    source = snapshot["source"]
    assert source["kind"] == "mediawiki_category"
    assert source["fetched_via"] == "live-api"
    assert source["revisions"] == {FIXTURE_TITLE: 123456}
    license_info = source["license"]
    # the site states "Attribution-Share Alike" (with a space); never CC0
    assert "Attribution" in license_info["text"]
    assert "Share" in license_info["text"]
    assert "creativecommons.org" in license_info["url"]
    assert "CC0" not in license_info["text"]


def test_every_fact_span_is_a_verbatim_substring_with_exact_offsets() -> None:
    snapshot = _snapshot()
    texts = {doc["doc_id"]: doc["text"] for doc in snapshot["documents"]}
    assert snapshot["facts"], "fixture should yield at least one fact"
    for fact in snapshot["facts"]:
        assert fact["supporting_spans"], "grounded facts need spans"
        for span in fact["supporting_spans"]:
            text = texts[span["doc_id"]]
            assert text[span["start"] : span["end"]] == fact["value"]
            assert text.count(fact["value"]) >= 1
            # the span boundaries cut exactly at the value edges
            assert span["start"] < span["end"]


def test_infobox_fact_offsets_are_exact() -> None:
    snapshot = _snapshot()
    doc = next(d for d in snapshot["documents"] if d["title"] == FIXTURE_TITLE)
    text = doc["text"]
    org = next(
        f
        for f in snapshot["facts"]
        if f["relation"] == "operated by"
        and f["value"] == "Northland Astronomy Council"
    )
    span = org["supporting_spans"][0]
    assert (span["start"], span["end"]) == (
        text.find("Northland Astronomy Council"),
        text.find("Northland Astronomy Council") + len("Northland Astronomy Council"),
    )
    established = next(
        f for f in snapshot["facts"] if f["relation"] == "established in"
    )
    assert established["value"] == "1904"
    assert established["value_type"] == "year"
    assert established["time"] == "1904"
    assert established["unit"] is None


def test_table_facts_carry_column_qualifiers_and_years() -> None:
    snapshot = _snapshot()
    telescope_facts = {
        (f["subject"], f["value"]): f
        for f in snapshot["facts"]
        if f["relation"] == "uses telescope"
    }
    assert ("Testhill Observatory", "Reflector telescope") in telescope_facts
    fact = telescope_facts[("Testhill Observatory", "Reflector telescope")]
    assert fact["qualifiers"]["table_column"] == "Telescope"
    # row-level established years come out as year facts on the row subject
    year_facts = {
        (f["subject"], f["value"])
        for f in snapshot["facts"]
        if f["relation"] == "active in"
    }
    assert ("Reflector telescope", "1927") in year_facts
    assert ("Schmidt camera", "1950") in year_facts


def test_entities_carry_mentions_external_qid_and_doc_id() -> None:
    snapshot = _snapshot()
    by_label = {entity["label"]: entity for entity in snapshot["entities"]}
    # the page's own entity: anchored to its doc, qid from pageprops
    own = by_label[FIXTURE_TITLE]
    assert own["external_qid"] == "Q9001"
    assert own["doc_id"] == "doc-9001"
    text = next(
        doc["text"] for doc in snapshot["documents"] if doc["doc_id"] == own["doc_id"]
    )
    assert own["mentions"], "own entity should mention itself in its doc"
    for mention in own["mentions"]:
        assert text[mention["start"] : mention["end"]] == FIXTURE_TITLE
    # a link-only entity keeps the qid the link pageprops carried
    council = by_label["Northland Astronomy Council"]
    assert council["external_qid"] == "Q103"
    # qid-less link entities keep external_qid as None, not a guess
    ada = by_label["Ada Meridian"]
    assert ada["external_qid"] is None
    assert ada["doc_id"] == "doc-9001"
    assert ada["mentions"], "list-item entity should have a mention"


def test_entity_mentions_are_sorted_and_deduped() -> None:
    snapshot = _snapshot()
    for entity in snapshot["entities"]:
        starts = [mention["start"] for mention in entity["mentions"]]
        assert starts == sorted(starts)
        pairs = [(m["start"], m["end"]) for m in entity["mentions"]]
        assert len(pairs) == len(set(pairs))


def test_quarantine_holds_ungrounded_category_membership() -> None:
    snapshot = _snapshot()
    quarantined = {
        (item["subject"], item["relation"], item["value"])
        for item in snapshot["ungrounded_quarantine"]
    }
    assert (FIXTURE_TITLE, "member of category", CATEGORY) in quarantined
    # nothing ungrounded leaks into facts
    for fact in snapshot["facts"]:
        assert fact["supporting_spans"]
        assert fact["relation"] != "member of category"


def test_relations_are_grounded_entity_facts() -> None:
    snapshot = _snapshot()
    for relation in snapshot["relations"]:
        assert relation["supporting_spans"]
        assert relation["subject"] != relation["object"]
        assert relation["relation_type"]
    operated = [r for r in snapshot["relations"] if r["relation_type"] == "operated by"]
    assert operated
    texts = {doc["doc_id"]: doc["text"] for doc in snapshot["documents"]}
    for relation in operated:
        span = relation["supporting_spans"][0]
        assert texts[span["doc_id"]][span["start"] : span["end"]] in (
            relation["object"],
            relation["qualifiers"].get("canonical_entity", relation["object"]),
        )


def test_documents_are_sorted_and_deterministic() -> None:
    first = _snapshot()
    second = _snapshot()
    assert wa.canonical_json(first) == wa.canonical_json(second)
    doc_ids = [doc["doc_id"] for doc in first["documents"]]
    assert doc_ids == sorted(doc_ids)
    fact_ids = [fact["fact_id"] for fact in first["facts"]]
    assert fact_ids == sorted(fact_ids)
    entity_ids = [entity["entity_id"] for entity in first["entities"]]
    assert entity_ids == sorted(entity_ids)


def test_roundtrip_to_and_from_dict() -> None:
    snapshot = _snapshot()
    payload = json.loads(wa.canonical_json(snapshot).decode("utf-8"))
    loaded = wa.snapshot_from_dict(payload)
    assert wa.canonical_json(loaded) == wa.canonical_json(snapshot)


def test_validation_rejects_bad_snapshots() -> None:
    snapshot = _snapshot()

    broken = json.loads(json.dumps(snapshot))
    broken["facts"][0]["supporting_spans"][0]["end"] += 1  # non-verbatim span
    with pytest.raises(wa.SnapshotError, match="verbatim"):
        wa.validate_snapshot(broken)

    ungrounded = json.loads(json.dumps(snapshot))
    ungrounded["facts"][0]["supporting_spans"] = []  # spanless fact
    with pytest.raises(wa.SnapshotError, match="quarantined"):
        wa.validate_snapshot(ungrounded)

    reordered = json.loads(json.dumps(snapshot))
    reordered["facts"] = list(reversed(reordered["facts"]))
    with pytest.raises(wa.SnapshotError, match="not sorted"):
        wa.validate_snapshot(reordered)

    missing = json.loads(json.dumps(snapshot))
    del missing["documents"]
    with pytest.raises(wa.SnapshotError):
        wa.validate_snapshot(missing)


def test_validation_rejects_cc0_license_claim() -> None:
    snapshot = _snapshot()
    cc0 = json.loads(json.dumps(snapshot))
    cc0["source"]["license"]["text"] = "Text available under CC0"
    with pytest.raises(wa.SnapshotError, match="CC0"):
        wa.validate_snapshot(cc0)


def test_value_classification() -> None:
    surfaces = {"Bologna": "Bologna"}
    assert wa.classify_value("Bologna", surfaces) == "entity"
    assert wa.classify_value("1726", {}) == "year"
    assert wa.classify_value("March 3, 1904", {}) == "date"
    assert wa.classify_value("1.0 m", {}) == "quantity"
    assert wa.classify_value("44.5 N, 11.3 E", {}) == "geo"
    assert wa.classify_value("Schmidt camera", {}) == "string"
    assert wa.quantity_unit("1.0 m") == "m"
    assert wa.quantity_unit("1904") is None
