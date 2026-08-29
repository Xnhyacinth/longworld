from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from longworld.core.attestation import attach_attestation
from longworld.core.documentworkflow import (
    build_wikipedia_workflow_manifest,
    load_wikipedia_workflow_manifest,
)
from longworld.core.provenance import ProvenanceError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch_wikipedia_workflow import fetch_wikipedia_workflow

TEST_KEY = b"wikipedia-live-contract-key-32-bytes"


def _request() -> dict:
    return {
        "schema_version": "longworld.wikimedia-fetch-request.v1",
        "user_agent": "LongWorld Research research@example-research.org",
        "authorization": {
            "record_id": "WIKIMEDIA-PUBLIC-TEST-001",
            "scope": "Ada Lovelace English Wikipedia and Wikidata revisions",
            "basis": "public Wikimedia API research export",
            "reviewed_at": "2026-08-25T09:00:00Z",
        },
        "language": "en",
        "titles": ["Ada Lovelace"],
        "requests_per_second": 5,
    }


def _page(revid: int) -> dict:
    revisions = {
        101: {
            "revid": 101,
            "parentid": 100,
            "timestamp": "2025-03-01T00:00:00Z",
            "slots": {"main": {"content": "Ada maps to Wikidata Q7259."}},
        },
        100: {
            "revid": 100,
            "parentid": 99,
            "timestamp": "2025-02-01T00:00:00Z",
            "slots": {"main": {"content": "Ada was a mathematician."}},
        },
        99: {
            "revid": 99,
            "parentid": 98,
            "timestamp": "2025-01-01T00:00:00Z",
            "slots": {"main": {"content": "Ada studied mathematics."}},
        },
    }
    return {
        "batchcomplete": True,
        "query": {
            "pages": [
                {
                    "pageid": 200,
                    "title": "Ada Lovelace",
                    "pageprops": {"wikibase_item": "Q7259"},
                    "revisions": [revisions[revid]],
                }
            ]
        },
    }


def test_fetch_wikipedia_builds_real_revision_entity_exact_spans(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    metadata = {
        "batchcomplete": True,
        "query": {
            "pages": [
                {
                    "pageid": 200,
                    "title": "Ada Lovelace",
                    "pageprops": {"wikibase_item": "Q7259"},
                    "revisions": [
                        {
                            "revid": 101,
                            "parentid": 100,
                            "timestamp": "2025-03-01T00:00:00Z",
                        },
                        {
                            "revid": 100,
                            "parentid": 99,
                            "timestamp": "2025-02-01T00:00:00Z",
                        },
                        {
                            "revid": 99,
                            "parentid": 98,
                            "timestamp": "2025-01-01T00:00:00Z",
                        },
                    ],
                }
            ]
        },
    }
    entity = {
        "entities": {
            "Q7259": {
                "id": "Q7259",
                "lastrevid": 220,
                "modified": "2025-03-02T00:00:00Z",
                "labels": {"en": {"language": "en", "value": "Ada Lovelace"}},
                "sitelinks": {"enwiki": {"site": "enwiki", "title": "Ada Lovelace"}},
            }
        }
    }
    calls: list[str] = []

    def get(url: str, headers: dict[str, str], timeout: float) -> bytes:
        del timeout
        calls.append(headers["User-Agent"])
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.hostname == "www.wikidata.org":
            return json.dumps(entity).encode()
        if "titles" in query:
            return json.dumps(metadata).encode()
        return json.dumps(_page(int(query["revids"][0]))).encode()

    input_path = fetch_wikipedia_workflow(
        request_path,
        tmp_path / "download",
        http_get=get,
        sleep=lambda _: None,
        generated_at="2026-08-25T10:00:00Z",
    )
    payload = json.loads(input_path.read_text(encoding="utf-8"))

    assert len(calls) == 5
    assert set(calls) == {_request()["user_agent"]}
    assert payload["source_status"] == "public_api_export"
    assert payload["fetch_receipt"]["title_resolutions"] == [
        {"requested_title": "Ada Lovelace", "resolved_title": "Ada Lovelace"}
    ]
    assert len(payload["records"]) == 4
    relation_kinds = [relation["kind"] for relation in payload["relations"]]
    assert relation_kinds.count("revision_of") == 2
    assert set(relation_kinds) == {
        "revision_of",
        "page_describes_entity",
        "entity_resolves_page",
    }
    revision_edges = {
        (relation["source_record_id"], relation["target_record_id"])
        for relation in payload["relations"]
        if relation["kind"] == "revision_of"
    }
    assert revision_edges == {
        ("page-200-r100", "page-200-r99"),
        ("page-200-r101", "page-200-r100"),
    }
    for record in payload["records"]:
        retrieval = urlparse(record["retrieval_url"])
        assert retrieval.scheme == "https"
        if record["kind"] == "wikipedia_revision":
            assert parse_qs(retrieval.query)["revids"] == [str(record["revision_id"])]
            assert parse_qs(urlparse(record["source_url"]).query)["oldid"] == [
                str(record["revision_id"])
            ]
        else:
            assert record["retrieval_url"] == record["source_url"]
    for record in payload["records"]:
        source = input_path.parent / record["source_file"]
        assert (
            record["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        )
    manifest = build_wikipedia_workflow_manifest(
        payload, input_path.parent, generated_at="2026-08-25T10:01:00Z"
    )
    assert manifest["data_stage"] == "source_inventory"
    assert manifest["hybrid_train_ready"] is False

    manifest_path = tmp_path / "public-manifest.json"
    manifest_path.write_text(
        json.dumps(attach_attestation(manifest, TEST_KEY, purpose="source_manifest")),
        encoding="utf-8",
    )
    loaded = load_wikipedia_workflow_manifest(manifest_path, attestation_key=TEST_KEY)
    assert all("retrieval_url" in record for record in loaded["records"])

    first_record = payload["records"][0]
    original_retrieval_url = first_record["retrieval_url"]
    first_record["retrieval_url"] = (
        "https://en.wikipedia.org/w/api.php?action=query&revids=999"
    )
    with pytest.raises(ProvenanceError, match="retrieval URL"):
        build_wikipedia_workflow_manifest(
            payload, input_path.parent, generated_at="2026-08-25T10:01:00Z"
        )

    metadata["query"]["pages"][0]["revisions"][1]["parentid"] = 98
    with pytest.raises(ProvenanceError, match="revision ancestry"):
        fetch_wikipedia_workflow(
            request_path,
            tmp_path / "invalid-ancestry",
            http_get=get,
            sleep=lambda _: None,
            generated_at="2026-08-25T10:00:00Z",
        )

    first_record["retrieval_url"] = original_retrieval_url
    payload["fetch_receipt"]["title_resolutions"][0]["requested_title"] = "Grace Hopper"
    with pytest.raises(ProvenanceError, match="title resolution"):
        build_wikipedia_workflow_manifest(
            payload, input_path.parent, generated_at="2026-08-25T10:01:00Z"
        )

    payload["fetch_receipt"]["title_resolutions"][0]["requested_title"] = "Ada Lovelace"
    parsed_retrieval = urlparse(original_retrieval_url)
    incomplete_query = parse_qs(parsed_retrieval.query)
    incomplete_query.pop("rvprop")
    first_record["retrieval_url"] = parsed_retrieval._replace(
        query=urlencode(incomplete_query, doseq=True)
    ).geturl()
    with pytest.raises(ProvenanceError, match="retrieval URL"):
        build_wikipedia_workflow_manifest(
            payload, input_path.parent, generated_at="2026-08-25T10:01:00Z"
        )


def test_fetch_wikipedia_rejects_page_outside_title_resolution(
    tmp_path: Path,
) -> None:
    request = _request()
    request["titles"] = ["Allowed Page"]
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    metadata = {
        "query": {
            "pages": [
                {
                    "pageid": 200,
                    "title": "Other Page",
                    "pageprops": {"wikibase_item": "Q7259"},
                    "revisions": [
                        {"revid": 101, "parentid": 100},
                        {"revid": 100, "parentid": 99},
                    ],
                }
            ]
        }
    }

    with pytest.raises(ProvenanceError, match="outside the request allowlist"):
        fetch_wikipedia_workflow(
            request_path,
            tmp_path / "download",
            http_get=lambda *_: json.dumps(metadata).encode(),
            sleep=lambda _: None,
            generated_at="2026-08-25T10:00:00Z",
        )
