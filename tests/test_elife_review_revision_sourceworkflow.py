from __future__ import annotations

import copy
import hashlib
import re

import pytest

from longworld.core.documentworkflow import (
    audit_elife_review_revision_task,
    build_elife_review_revision_inventory,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.sourceworkflow import (
    PAPER_SOURCE_KIND,
    adapt_elife_review_revision_manifest,
    adapt_source_manifest,
)
from tests.test_elife_review_revision_workflow import _request, _xml_versions


def test_elife_sourceworkflow_redacts_email_and_preserves_task_evidence() -> None:
    v1, v2, review, response = _xml_versions()
    v1 = v1.replace(
        b"</article-meta>",
        b"<email>first.v1@example.org</email><email>second.v1@example.org</email>"
        b"<email>third.v1@example.org</email></article-meta>",
    )
    v2 = v2.replace(
        b"</article-meta>",
        b"<email>first.v2@example.org</email><email>second.v2@example.org</email>"
        b"<email>third.v2@example.org</email></article-meta>",
    )
    inventory = build_elife_review_revision_inventory(
        _request(v1, v2, review, response),
        {1: v1, 2: v2},
        generated_at="2026-09-03T02:00:00Z",
    )

    assert audit_elife_review_revision_task(inventory)["passed"]
    assert [
        record["privacy_review"]["email_redaction_count"]
        for record in inventory["records"]
    ] == [3, 3]
    for raw, record in zip((v1, v2), inventory["records"], strict=True):
        assert record["text"].count("[redacted-email]") == 3
        assert re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", record["text"]) is None
        assert record["source_sha256"] == hashlib.sha256(raw).hexdigest()
        assert (
            record["text_sha256"] == hashlib.sha256(record["text"].encode()).hexdigest()
        )
        assert record["source_sha256"] != record["text_sha256"]
        assert record["provenance_id"] == f"sha256:{record['source_sha256']}"

    (workflow,) = adapt_elife_review_revision_manifest(
        inventory, signed_bundle_authorized=True
    )
    assert (workflow,) == adapt_source_manifest(
        inventory,
        source_kind=PAPER_SOURCE_KIND,
        signed_bundle_authorized=True,
    )

    assert {record.kind for record in workflow.records} == {"elife_revision"}
    assert {relation.kind for relation in workflow.relations} == {
        "implements_revision_delta",
        "requests_revision",
        "responds_to_review",
        "revision_of",
    }
    records = {record.record_id: record for record in workflow.records}
    for relation in workflow.relations:
        for evidence in relation.evidence:
            record = records[evidence.record_id]
            assert record.text[evidence.char_start : evidence.char_end] == (
                evidence.evidence_quote
            )
            assert "@" not in evidence.evidence_quote
            assert evidence.source_sha256 == record.source_sha256
    assert {
        evidence.evidence_quote
        for relation in workflow.relations
        for evidence in relation.evidence
    }.issuperset(
        {
            review,
            response,
            '<fig id="fig5"',
            '<app id="APP9"',
            '<table-wrap id="tbl3"',
        }
    )

    tampered = copy.deepcopy(inventory)
    tampered["task"]["answer"] = "CLAIMED_NOT_VERIFIED"
    with pytest.raises(ProvenanceError, match="eLife inventory does not match"):
        adapt_elife_review_revision_manifest(tampered, signed_bundle_authorized=True)
