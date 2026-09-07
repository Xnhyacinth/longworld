from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from longworld.core.eurlexworkflow import (
    EurLexWorkflowError,
    adoption_endpoints,
    article_span,
    investigational_statement_spans,
    manufacturer_years,
    relation_spans,
    replay_qualification_change,
    replay_referenced_statement,
    replay_referenced_statement_raw_slice,
    replay_source_bound_qualification_change,
    source_span,
    verify_referenced_statement_sources,
    verify_span,
)
from longworld.core.pack import SEP


def _article(number: int, diploma: str, experience: str) -> bytes:
    return (
        f"<p>Article {number}</p><p>Person responsible for regulatory compliance</p>"
        "<p>1. Manufacturers shall have available a qualified person.</p>"
        f"<p>(a) diploma and at least {diploma} years of professional experience;</p>"
        f"<p>(b) {experience} years of professional experience.</p>"
        "<p>Without prejudice to national provisions.</p>"
        f"<p>Article {number + 1}</p><p>Another subject</p>"
    ).encode()


def _metadata() -> bytes:
    return b"""<ROOT><WORK>
    <RESOURCE_LEGAL_ID_CELEX><VALUE>act-id</VALUE></RESOURCE_LEGAL_ID_CELEX>
    <RESOURCE_LEGAL_ADOPTS_RESOURCE_LEGAL><SAMEAS><URI>
    <IDENTIFIER>proposal-id</IDENTIFIER><TYPE>celex</TYPE>
    </URI></SAMEAS></RESOURCE_LEGAL_ADOPTS_RESOURCE_LEGAL>
    <NESTED><RESOURCE_LEGAL_ID_CELEX><VALUE>wrong-id</VALUE>
    </RESOURCE_LEGAL_ID_CELEX></NESTED></WORK></ROOT>"""


def test_exact_relation_spans_bind_direct_work_subject_and_adoption() -> None:
    raw = _metadata()
    spans = relation_spans(raw)
    assert adoption_endpoints(spans) == ("proposal-id", "act-id")
    for span in spans:
        verify_span(span, raw)


def test_article_mapping_uses_title_not_matching_article_numbers() -> None:
    before = article_span(_article(13, "two", "five"), "proposal")
    after = article_span(_article(15, "one", "four"), "act")
    spans = [before, after, *relation_spans(_metadata())]
    identity = {"proposal": "proposal-id", "act": "act-id"}
    answer = replay_qualification_change(spans, identity)
    assert answer["answer"]["diploma_route_years"] == [2, 1]
    assert answer["answer"]["experience_only_years"] == [5, 4]
    assert answer["answer"]["year_changes"] == [-1, -1]
    for index in range(len(spans)):
        assert (
            replay_qualification_change(spans[:index] + spans[index + 1 :], identity)[
                "status"
            ]
            == "UNKNOWN"
        )


def test_source_text_tampering_is_detected() -> None:
    raw = _article(13, "two", "five")
    span = article_span(raw, "proposal")
    changed = deepcopy(span)
    changed["text"] = changed["text"].replace("five", "four")
    with pytest.raises(EurLexWorkflowError, match="changed"):
        verify_span(changed, raw)
    with pytest.raises(EurLexWorkflowError, match="outside"):
        source_span(raw, "proposal", 0, len(raw) + 1)


def test_ambiguous_official_position_years_are_not_guessed() -> None:
    span = article_span(_article(13, "two", "five three"), "position")
    with pytest.raises(EurLexWorkflowError, match="ambiguous"):
        manufacturer_years(span["text"])


def test_duplicate_articles_and_unbound_adoption_target_fail_closed() -> None:
    before = article_span(_article(13, "two", "five"), "proposal")
    after = article_span(_article(15, "one", "four"), "act")
    spans = [before, after, *relation_spans(_metadata())]
    identity = {"proposal": "proposal-id", "act": "act-id"}
    assert (
        replay_qualification_change([*spans, before], identity)["status"] == "UNKNOWN"
    )
    assert (
        replay_qualification_change(spans, {"proposal": "wrong", "act": "act-id"})[
            "status"
        ]
        == "UNKNOWN"
    )


def test_digest_consistent_body_replacement_does_not_override_pinned_receipt() -> None:
    original = _article(13, "two", "five")
    replacement = _article(13, "two", "four")
    span = article_span(replacement, "proposal")
    receipt = {
        "source_id": "proposal",
        "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:proposal-id",
        "expected_sha256": hashlib.sha256(original).hexdigest(),
        "expected_bytes": len(original),
    }
    with pytest.raises(EurLexWorkflowError, match="receipt mismatch"):
        replay_source_bound_qualification_change(
            [span], {"proposal": replacement}, [receipt]
        )


def _statement_document(chapter: str = "II") -> bytes:
    return (
        "<p>Article 15</p><p>Person responsible for regulatory compliance</p>"
        f"<p>in the case of investigational devices, the statement referred to in Section 4.1 of Chapter {chapter} of Annex XV is issued.</p>"
        "<p>Article 16</p><p>Other article</p>"
        "<p>ANNEX XV</p><p>CHAPTER I</p><p>4.1. A signed statement by the first issuer that the device in question conforms to the requirements apart from the experimental aspects and that, with regard to those aspects, every precaution is taken.</p>"
        "<p>CHAPTER II</p><p>4.1. A signed statement by the second issuer that the device in question conforms to the requirements apart from the experimental aspects and that, with regard to those aspects, every precaution is taken.</p>"
        "<p>ANNEX XVI</p>"
    ).encode()


def test_reference_target_is_selected_by_parsed_chapter_not_configuration() -> None:
    for chapter, issuer in [("I", "the first issuer"), ("II", "the second issuer")]:
        raw = _statement_document(chapter)
        spans = investigational_statement_spans(raw)
        for span in spans:
            verify_span(span, raw)
        result = replay_referenced_statement(spans)
        assert result["answer"]["reference"]["chapter"] == chapter
        assert result["answer"]["issuer"] == issuer
        for index in range(len(spans)):
            assert (
                replay_referenced_statement(spans[:index] + spans[index + 1 :])[
                    "status"
                ]
                == "UNKNOWN"
            )


def test_reference_to_absent_target_fails_instead_of_using_similar_section() -> None:
    with pytest.raises(EurLexWorkflowError, match="target is absent"):
        investigational_statement_spans(_statement_document("III"))


def test_article_without_following_heading_fails_closed() -> None:
    with pytest.raises(EurLexWorkflowError, match="end heading"):
        article_span(
            b"<p>Article 15 Person responsible for regulatory compliance</p>", "act"
        )


def test_reference_raw_slice_replays_visible_text_and_rejects_missing_scope() -> None:
    spans = investigational_statement_spans(_statement_document())
    text = SEP.join(span["text"] for span in spans)
    assert replay_referenced_statement_raw_slice(text) == replay_referenced_statement(
        spans
    )
    assert (
        replay_referenced_statement_raw_slice(
            SEP.join(span["text"] for span in spans[1:])
        )["status"]
        == "UNKNOWN"
    )
    assert (
        replay_referenced_statement_raw_slice(
            text.replace("CHAPTER II", "CHAPTER III")
        )["status"]
        == "UNKNOWN"
    )
    assert replay_referenced_statement_raw_slice(text[:-10])["status"] == "UNKNOWN"


def test_statement_node_binding_rejects_valid_span_outside_resolved_graph() -> None:
    raw = _statement_document()
    receipt = {
        "source_id": "act",
        "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0745",
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_bytes": len(raw),
    }
    spans = investigational_statement_spans(raw)
    verify_referenced_statement_sources(spans, raw, receipt)
    verify_referenced_statement_sources(spans[:-1], raw, receipt)
    with pytest.raises(EurLexWorkflowError, match="outside the resolved"):
        verify_referenced_statement_sources(
            [source_span(raw, "act", 0, 10)], raw, receipt
        )
