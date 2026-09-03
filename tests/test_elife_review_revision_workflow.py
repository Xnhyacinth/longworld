from __future__ import annotations

import copy
import hashlib

import pytest

from longworld.core.documentworkflow import (
    ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA,
    ELIFE_REVIEW_REVISION_REQUEST_SCHEMA,
    audit_elife_review_revision_task,
    build_elife_review_revision_inventory,
)
from longworld.core.provenance import ProvenanceError


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def _xml_versions() -> tuple[bytes, bytes, str, str]:
    review = (
        "The authors should expand the bacteria-to-cancer generalization and "
        "show the corresponding figure, appendix, and table."
    )
    response = (
        "We added Figure 5, expanded Appendix 9, and added Appendix Table 3 "
        "to address this generalization."
    )
    v1 = f"""<article>
  <front><article-meta>
    <article-id pub-id-type="publisher-id">94586</article-id>
    <article-id pub-id-type="doi">10.7554/eLife.94586</article-id>
    <article-id pub-id-type="doi" specific-use="version">10.7554/eLife.94586.1</article-id>
    <title-group><article-title>Overflow metabolism</article-title></title-group>
    <pub-date date-type="original-publication" iso-8601-date="2024-02-07" />
    <permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/" xmlns:xlink="http://www.w3.org/1999/xlink" /></permissions>
  </article-meta></front>
  <body><p>The original claim is limited to bacteria.</p></body>
  <back><app-group /></back>
  <sub-article id="sa1" article-type="referee-report">
    <front-stub><article-id pub-id-type="doi">10.7554/eLife.94586.1.sa2</article-id></front-stub>
    <body><p>{review}</p></body>
  </sub-article>
</article>
""".encode()
    v2 = f"""<article>
  <front><article-meta>
    <article-id pub-id-type="publisher-id">94586</article-id>
    <article-id pub-id-type="doi">10.7554/eLife.94586</article-id>
    <article-id pub-id-type="doi" specific-use="version">10.7554/eLife.94586.2</article-id>
    <title-group><article-title>Overflow metabolism</article-title></title-group>
    <pub-date date-type="update" iso-8601-date="2024-12-19" />
    <permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/" xmlns:xlink="http://www.w3.org/1999/xlink" /></permissions>
  </article-meta></front>
  <body><p>The claim now covers bacteria and cancer.</p><fig id="fig5"><caption><p>Figure 5.</p></caption></fig></body>
  <back><app-group><app id="APP9"><title>Appendix 9</title><table-wrap id="tbl3"><caption><p>Appendix Table 3.</p></caption></table-wrap></app></app-group></back>
  <sub-article id="sa3" article-type="author-comment">
    <front-stub><article-id pub-id-type="doi">10.7554/eLife.94586.2.sa0</article-id></front-stub>
    <body><disp-quote><p>{review}</p></disp-quote><p>{response}</p></body>
  </sub-article>
</article>
""".encode()
    return v1, v2, review, response


def _request(v1: bytes, v2: bytes, review: str, response: str) -> dict[str, object]:
    return {
        "schema_version": ELIFE_REVIEW_REVISION_REQUEST_SCHEMA,
        "data_product": "p21_researchlab_elife_94586_review_revision_64k_v1",
        "authorization": {
            "record_id": "P21-PUBLIC-ELIFE-94586-20260903-01",
            "scope": "bounded public eLife v1/v2 source inventory",
            "basis": "official eLife article XML at one pinned Git commit",
            "reviewed_at": "2026-09-03T00:00:00Z",
            "allowed_actions": ["fetch_pinned_elife_xml"],
        },
        "source": {
            "repository": "elifesciences/elife-article-xml",
            "repository_url": "https://github.com/elifesciences/elife-article-xml",
            "repository_commit": "d" * 40,
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        },
        "candidate": {
            "publisher_id": "94586",
            "canonical_doi": "10.7554/eLife.94586",
            "title": "Overflow metabolism",
            "transition_program": "researchlab.review_response_revision_claim_disposition.v1",
        },
        "versions": [
            {
                "version": 1,
                "path": "preprints/elife-preprint-94586-v1.xml",
                "version_doi": "10.7554/eLife.94586.1",
                "effective_date": "2024-02-07",
                "bytes": len(v1),
                "sha256": _sha256(v1),
                "git_blob_sha1": _git_blob_sha1(v1),
            },
            {
                "version": 2,
                "path": "preprints/elife-preprint-94586-v2.xml",
                "version_doi": "10.7554/eLife.94586.2",
                "effective_date": "2024-12-19",
                "bytes": len(v2),
                "sha256": _sha256(v2),
                "git_blob_sha1": _git_blob_sha1(v2),
            },
        ],
        "oracle_witness": {
            "review_paragraph_sha256": _sha256(review.encode()),
            "response_paragraph_sha256": _sha256(response.encode()),
            "required_v1_to_v2_object_transitions": {
                "body_figure_fig5": [0, 1],
                "appendix_APP9": [0, 1],
                "appendix_table_tbl3": [0, 1],
            },
        },
        "length_policy": {
            "allowed_bands": ["64k"],
            "exact_64k_band": [65536, 67584],
            "measured_near_dedup_capacity": 79016,
        },
    }


def test_elife_inventory_binds_bytes_identity_relation_and_remove_one_replay() -> None:
    v1, v2, review, response = _xml_versions()
    request = _request(v1, v2, review, response)

    inventory = build_elife_review_revision_inventory(
        request,
        {1: v1, 2: v2},
        generated_at="2026-09-03T01:00:00Z",
    )
    audit = audit_elife_review_revision_task(inventory)

    assert inventory["schema_version"] == ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA
    assert [
        (item["version"], item["version_doi"]) for item in inventory["records"]
    ] == [
        (1, "10.7554/eLife.94586.1"),
        (2, "10.7554/eLife.94586.2"),
    ]
    assert audit == {
        "program_id": "researchlab.review_response_revision_claim_disposition.v1",
        "strict_answer": "VERIFIED_IMPLEMENTED",
        "without_controlling_review": "UNKNOWN_NO_REVIEW_CLAIM",
        "without_direct_response": "UNKNOWN_NO_AUTHOR_RESPONSE",
        "without_required_revision_delta": {
            "appendix_APP9": "CLAIMED_NOT_VERIFIED",
            "appendix_table_tbl3": "CLAIMED_NOT_VERIFIED",
            "body_figure_fig5": "CLAIMED_NOT_VERIFIED",
        },
        "remove_review_fails": True,
        "remove_response_fails": True,
        "remove_delta_fails": True,
        "model_written_gold": False,
        "passed": True,
    }

    mismatched = copy.deepcopy(request)
    mismatched["versions"][1]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(ProvenanceError, match="eLife v2 sha256 mismatch"):
        build_elife_review_revision_inventory(
            mismatched,
            {1: v1, 2: v2},
            generated_at="2026-09-03T01:00:00Z",
        )
