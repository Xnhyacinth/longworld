from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.documentworkflow import load_wikipedia_real_workflow_episode
from longworld.core.provenance import ProvenanceError
from longworld.core.realworkflow import MAX_WORKFLOW_RECORD_CHARS
from longworld.core.taxonomy import SourceOrigin
from scripts.export_wikipedia_workflow import export_wikipedia_workflow

ROOT = Path(__file__).resolve().parents[1]
AUTHENTIC_MANIFEST = (
    ROOT
    / "data/source_inventory/wikimedia_p5_ada_smoke"
    / "wikipedia_workflow_manifest.signed.json"
)
AUTHENTIC_INPUT = (
    ROOT
    / "data/source_inventory/wikimedia_p5_ada_smoke"
    / "wikipedia_workflow_input.json"
)
AUTHENTIC_KEY = ROOT.parent / ".longworld-trust/p3-probe-12-v1/source.key"


def _authentic_key() -> bytes:
    if (
        not AUTHENTIC_MANIFEST.is_file()
        or not AUTHENTIC_INPUT.is_file()
        or not AUTHENTIC_KEY.is_file()
    ):
        pytest.skip("authentic local Wikimedia inventory/trust material is unavailable")
    return AUTHENTIC_KEY.read_text(encoding="utf-8").strip().encode()


@pytest.fixture(autouse=True)
def _authentic_source_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _authentic_key()
    manifest = json.loads(AUTHENTIC_MANIFEST.read_text(encoding="utf-8"))
    attestation = manifest["attestation"]
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, attestation["environment"])
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], key.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], attestation["key_id"])


def _authentic_payload() -> tuple[dict, bytes]:
    key = _authentic_key()
    return json.loads(AUTHENTIC_MANIFEST.read_text(encoding="utf-8")), key


def _write_signed(path: Path, payload: dict, key: bytes) -> None:
    signed = attach_attestation(payload, key, purpose="source_manifest")
    path.write_text(json.dumps(signed), encoding="utf-8")


def test_materializes_authentic_manifest_as_deterministic_chronological_episode() -> (
    None
):
    key = _authentic_key()

    first = load_wikipedia_real_workflow_episode(
        AUTHENTIC_MANIFEST, attestation_key=key
    )
    second = load_wikipedia_real_workflow_episode(
        AUTHENTIC_MANIFEST, attestation_key=key
    )

    assert first == second
    assert first.source_kind == "wikimedia"
    assert first.source_origin is SourceOrigin.REAL_PUBLIC
    assert first.lineage.license == "CC BY-SA 4.0; GFDL; CC0-1.0"
    assert first.workflow_id.startswith("wikimedia:")
    assert [record.record_id for record in first.records] == [
        "page-974-r1360639004",
        "entity-Q7259-r2531694935",
        "page-974-r1370153024",
    ]
    assert [record.occurred_at for record in first.records] == sorted(
        record.occurred_at for record in first.records
    )
    assert set(first.facts) >= {
        "record:page-974-r1370153024:revision_id",
        "record:page-974-r1370153024:parent_revision_id",
        "record:entity-Q7259-r2531694935:entity_id",
        "relation:page-r1370153024-r1360639004:target",
        "relation:page-974-Q7259:target",
        "relation:Q7259-page-974:target",
    }
    by_id = {record.record_id: record for record in first.records}
    assert by_id["page-974-r1370153024"].attributes["content_license"] == (
        "CC BY-SA 4.0; GFDL"
    )
    assert by_id["entity-Q7259-r2531694935"].attributes["content_license"] == (
        "CC0-1.0"
    )
    for fact in first.facts.values():
        record = by_id[fact.record_id]
        assert record.text[fact.char_start : fact.char_end] == fact.value
        assert fact.value in fact.source_quote
    current = by_id["page-974-r1370153024"]
    assert current.links == (
        "page-974-r1360639004",
        "entity-Q7259-r2531694935",
    )
    relations = {
        relation["kind"]
        for record in first.records
        for relation in record.attributes["source_relations"]
    }
    assert relations == {
        "revision_of",
        "page_describes_entity",
        "entity_resolves_page",
    }


def test_exporter_returns_replayable_episode_for_authentic_public_input(
    tmp_path: Path,
) -> None:
    key = _authentic_key()
    output = tmp_path / "signed-manifest.json"

    episode = export_wikipedia_workflow(
        AUTHENTIC_INPUT,
        output,
        attestation_key=key,
        generated_at="2026-08-25T05:44:04.551595Z",
    )

    assert episode is not None
    assert episode.source_origin is SourceOrigin.REAL_PUBLIC
    assert len(episode.records) == 3
    assert episode.lineage.source_path == str(output)
    assert all(
        record.source_pointer.startswith(f"{output}#/") for record in episode.records
    )
    assert all(".stage#/" not in record.source_pointer for record in episode.records)


def test_exporter_does_not_publish_a_signed_but_unreplayable_manifest(
    tmp_path: Path,
) -> None:
    key = _authentic_key()
    payload = json.loads(AUTHENTIC_INPUT.read_text(encoding="utf-8"))
    for record in payload["records"]:
        shutil.copyfile(
            AUTHENTIC_INPUT.parent / record["source_file"],
            tmp_path / record["source_file"],
        )
    current = next(
        record
        for record in payload["records"]
        if record["record_id"] == "page-974-r1370153024"
    )
    source_path = tmp_path / current["source_file"]
    body = source_path.read_text(encoding="utf-8") + (" " * MAX_WORKFLOW_RECORD_CHARS)
    source_path.write_text(body, encoding="utf-8")
    current["source_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    output_path = tmp_path / "published.json"

    with pytest.raises(ProvenanceError, match="record.*too large"):
        export_wikipedia_workflow(
            input_path,
            output_path,
            attestation_key=key,
            generated_at="2026-08-25T05:44:04.551595Z",
        )

    assert not output_path.exists()


def test_episode_identity_does_not_depend_on_record_or_relation_labels(
    tmp_path: Path,
) -> None:
    payload, key = _authentic_payload()
    payload.pop("attestation")
    original = load_wikipedia_real_workflow_episode(
        AUTHENTIC_MANIFEST, attestation_key=key
    )
    aliases = {
        record["record_id"]: f"record-{index}"
        for index, record in enumerate(payload["records"])
    }
    for record in payload["records"]:
        record["record_id"] = aliases[record["record_id"]]
    for index, relation in enumerate(reversed(payload["relations"])):
        relation["relation_id"] = f"relation-{index}"
        relation["source_record_id"] = aliases[relation["source_record_id"]]
        relation["target_record_id"] = aliases[relation["target_record_id"]]
        relation["evidence_record_id"] = aliases[relation["evidence_record_id"]]
    payload["relations"].reverse()
    path = tmp_path / "relabeled.json"
    _write_signed(path, payload, key)

    relabeled = load_wikipedia_real_workflow_episode(path, attestation_key=key)

    assert relabeled.workflow_id == original.workflow_id


def test_rejects_manifest_attestation_tampering(tmp_path: Path) -> None:
    payload, key = _authentic_payload()
    payload["records"][0]["occurred_at"] = "2026-01-01T00:00:00Z"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="attestation"):
        load_wikipedia_real_workflow_episode(path, attestation_key=key)


def test_rejects_resigned_semantic_body_corruption(tmp_path: Path) -> None:
    payload, key = _authentic_payload()
    payload.pop("attestation")
    current = next(
        record
        for record in payload["records"]
        if record["record_id"] == "page-974-r1370153024"
    )
    current["text"] = current["text"].replace(
        '"wikibase_item":"Q7259"', '"wikibase_item":"Q7258"', 1
    )
    current["text_sha256"] = hashlib.sha256(current["text"].encode()).hexdigest()
    path = tmp_path / "body-corrupt.json"
    _write_signed(path, payload, key)

    with pytest.raises(ProvenanceError, match="source binding|span|entity"):
        load_wikipedia_real_workflow_episode(path, attestation_key=key)


def test_rejects_resigned_text_that_no_longer_matches_source_provenance(
    tmp_path: Path,
) -> None:
    payload, key = _authentic_payload()
    payload.pop("attestation")
    current = next(
        record
        for record in payload["records"]
        if record["record_id"] == "page-974-r1370153024"
    )
    current["text"] += "\n"
    current["text_sha256"] = hashlib.sha256(current["text"].encode()).hexdigest()
    path = tmp_path / "provenance-mismatch.json"
    _write_signed(path, payload, key)

    with pytest.raises(ProvenanceError, match="source binding"):
        load_wikipedia_real_workflow_episode(path, attestation_key=key)


def test_rejects_symlinked_signed_manifest(tmp_path: Path) -> None:
    key = _authentic_key()
    path = tmp_path / "manifest-link.json"
    path.symlink_to(AUTHENTIC_MANIFEST)

    with pytest.raises(ProvenanceError, match="cannot read"):
        load_wikipedia_real_workflow_episode(path, attestation_key=key)


def test_rejects_workflow_record_over_replay_bound(tmp_path: Path) -> None:
    payload, key = _authentic_payload()
    payload.pop("attestation")
    current = next(
        record
        for record in payload["records"]
        if record["record_id"] == "page-974-r1370153024"
    )
    source = json.loads(current["text"])
    revision = source["query"]["pages"][0]["revisions"][0]
    revision["slots"]["main"]["content"] += "x" * MAX_WORKFLOW_RECORD_CHARS
    current["text"] = json.dumps(source, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(current["text"].encode()).hexdigest()
    current["source_sha256"] = digest
    current["text_sha256"] = digest
    current["provenance_id"] = f"sha256:{digest}"
    for relation in payload["relations"]:
        if relation["evidence_record_id"] != current["record_id"]:
            continue
        relation["evidence_char_start"] = current["text"].index(
            relation["evidence_quote"]
        )
        relation["source_sha256"] = digest
    path = tmp_path / "oversize.json"
    _write_signed(path, payload, key)

    with pytest.raises(ProvenanceError, match="record.*too large"):
        load_wikipedia_real_workflow_episode(path, attestation_key=key)
