from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from longworld.core.documentworkflow import (
    build_wikipedia_workflow_manifest,
    load_wikipedia_workflow_manifest,
)
from longworld.core.provenance import ProvenanceError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from export_wikipedia_workflow import export_wikipedia_workflow

TEST_KEY = b"wikipedia-test-attestation-key-32-bytes"


def _record(
    tmp_path: Path,
    *,
    record_id: str,
    kind: str,
    source_url: str,
    revision_id: int,
    text: str,
    page_id: int | None = None,
    title: str | None = None,
    parent_revision_id: int | None = None,
    entity_id: str | None = None,
) -> dict:
    source = tmp_path / f"{record_id}.txt"
    source.write_text(text, encoding="utf-8")
    return {
        "record_id": record_id,
        "kind": kind,
        "source_url": source_url,
        "source_file": source.name,
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "revision_id": revision_id,
        "page_id": page_id,
        "title": title,
        "parent_revision_id": parent_revision_id,
        "entity_id": entity_id,
        "occurred_at": "2025-03-01T00:00:00Z",
        "retrieved_at": "2026-08-24T09:00:00Z",
        "access_policy": "CC BY-SA / CC0 public API fixture",
        "parser": {"name": "fixture_text", "version": "1"},
    }


def _input(tmp_path: Path) -> dict:
    old = _record(
        tmp_path,
        record_id="page-200-r100",
        kind="wikipedia_revision",
        source_url="https://en.wikipedia.org/w/index.php?oldid=100",
        revision_id=100,
        page_id=200,
        title="Ada Lovelace",
        text="Ada Lovelace was a mathematician.",
    )
    new = _record(
        tmp_path,
        record_id="page-200-r101",
        kind="wikipedia_revision",
        source_url="https://en.wikipedia.org/w/index.php?oldid=101",
        revision_id=101,
        parent_revision_id=100,
        page_id=200,
        title="Ada Lovelace",
        text=(
            "This revision replaces revision 100. Ada Lovelace was an English "
            "mathematician. The page maps to Wikidata entity Q7259."
        ),
    )
    entity = _record(
        tmp_path,
        record_id="entity-Q7259-r220",
        kind="wikidata_entity_revision",
        source_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q7259"
        ),
        revision_id=220,
        entity_id="Q7259",
        text='Entity Q7259 has English label "Ada Lovelace".',
    )
    records = [old, new, entity]
    relations = []
    for relation_id, kind, source, target, quote in (
        (
            "page-r101-r100",
            "revision_of",
            new,
            old,
            "This revision replaces revision 100",
        ),
        (
            "page-Q7259",
            "page_describes_entity",
            new,
            entity,
            "The page maps to Wikidata entity Q7259",
        ),
        (
            "Q7259-page",
            "entity_resolves_page",
            entity,
            new,
            'Entity Q7259 has English label "Ada Lovelace"',
        ),
    ):
        text = (tmp_path / source["source_file"]).read_text(encoding="utf-8")
        relations.append(
            {
                "relation_id": relation_id,
                "kind": kind,
                "source_record_id": source["record_id"],
                "target_record_id": target["record_id"],
                "evidence_record_id": source["record_id"],
                "evidence_quote": quote,
                "evidence_char_start": text.index(quote),
            }
        )
    return {
        "schema_version": "longworld.wikipedia-workflow-input.v2",
        "source_status": "test_fixture",
        "authorization": {
            "record_id": "TEST-WIKIMEDIA-FIXTURE-001",
            "scope": "read-only fixture export",
            "basis": "test fixture only; not a live API export",
            "reviewed_at": "2026-08-24T10:00:00Z",
        },
        "records": records,
        "relations": relations,
    }


def test_wikipedia_export_preserves_revision_page_entity_workflow(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "manifest.json"
    input_path.write_text(json.dumps(_input(tmp_path)), encoding="utf-8")

    export_wikipedia_workflow(
        input_path,
        output_path,
        attestation_key=TEST_KEY,
        generated_at="2026-08-24T11:00:00Z",
    )
    loaded = load_wikipedia_workflow_manifest(output_path, attestation_key=TEST_KEY)

    assert loaded["data_stage"] == "source_inventory"
    assert loaded["hybrid_train_ready"] is False
    assert loaded["generation_integration"] == "disabled"
    assert {relation["kind"] for relation in loaded["relations"]} == {
        "revision_of",
        "page_describes_entity",
        "entity_resolves_page",
    }
    newer = next(
        record for record in loaded["records"] if record["record_id"] == "page-200-r101"
    )
    assert newer["parent_revision_id"] == 100
    assert "Q7259" in newer["text"]


def test_wikipedia_export_rejects_parent_revision_mismatch(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    payload["records"][1]["parent_revision_id"] = 99

    with pytest.raises(ProvenanceError, match="parent revision"):
        build_wikipedia_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_wikipedia_export_rejects_filename_identity_substitution(
    tmp_path: Path,
) -> None:
    payload = _input(tmp_path)
    payload["records"][2]["entity_id"] = "Q42"

    with pytest.raises(ProvenanceError, match="Wikidata entity"):
        build_wikipedia_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_wikipedia_export_rejects_unmatched_oldid(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    payload["records"][1]["source_url"] = (
        "https://en.wikipedia.org/w/index.php?oldid=999"
    )

    with pytest.raises(ProvenanceError, match="oldid"):
        build_wikipedia_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_wikipedia_export_rejects_url_userinfo(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    payload["records"][1]["source_url"] = (
        "https://attacker@en.wikipedia.org/w/index.php?oldid=101"
    )

    with pytest.raises(ProvenanceError, match="source URL"):
        build_wikipedia_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_wikipedia_export_rejects_fixture_relabelled_as_public_api(
    tmp_path: Path,
) -> None:
    payload = _input(tmp_path)
    payload["source_status"] = "public_api_export"

    with pytest.raises(ProvenanceError, match="fetch receipt"):
        build_wikipedia_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_wikipedia_relation_requires_whole_entity_identity(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    source = tmp_path / payload["records"][1]["source_file"]
    text = source.read_text(encoding="utf-8").replace("Q7259", "Q72590")
    source.write_text(text, encoding="utf-8")
    payload["records"][1]["source_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    payload["relations"][1]["evidence_quote"] = (
        "The page maps to Wikidata entity Q72590"
    )

    with pytest.raises(ProvenanceError, match="entity relation evidence"):
        build_wikipedia_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )
