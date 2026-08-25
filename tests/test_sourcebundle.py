from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import longworld.core.sourcebundle as sourcebundle_module
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.filingworkflow import (
    SEC_FILING_MANIFEST_SCHEMA,
    build_sec_filing_manifest,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.sourcebundle import (
    SOURCE_WORKFLOW_ADAPTER_REVISION,
    SOURCE_WORKFLOW_BUNDLE_PURPOSE,
    SOURCE_WORKFLOW_BUNDLE_SCHEMA,
    load_source_workflow_bundle,
)
from longworld.core.sourceworkflow import SEC_SOURCE_KIND
from longworld.core.taxonomy import SourceOrigin

TEST_KEY = b"source-bundle-test-key-with-32-bytes"


@pytest.fixture(autouse=True)
def _source_producer_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], TEST_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-source-bundle-v1")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sec_input(tmp_path: Path, *, source_status: str = "authorized_download") -> dict:
    filings = []
    for index, (form, revenue) in enumerate(
        (("10-K", "USD 40 million"), ("10-K/A", "USD 42 million")), start=1
    ):
        accession = f"0000000001-26-{index:06d}"
        text = "\n".join(
            (
                f"FORM: {form}",
                "REPORT DATE: 2025-12-31",
                f"AUDITED REVENUE: {revenue}",
            )
        )
        source_path = tmp_path / f"filing-{index}.txt"
        source_path.write_text(text, encoding="utf-8")
        facts = []
        for fact_id, field, value in (
            ("form", "form", form),
            ("report_date", "report_date", "2025-12-31"),
            ("revenue", "revenue", revenue),
        ):
            quote = next(line for line in text.splitlines() if value in line)
            facts.append(
                {
                    "fact_id": fact_id,
                    "field": field,
                    "value": value,
                    "evidence_quote": quote,
                    "evidence_char_start": text.index(quote),
                }
            )
        filings.append(
            {
                "accession": accession,
                "cik": "0000000001",
                "form": form,
                "filing_date": f"2026-02-{19 + index:02d}",
                "report_date": "2025-12-31",
                "source_url": f"https://www.sec.gov/Archives/{accession}.txt",
                "source_file": source_path.name,
                "source_sha256": _sha256(text.encode()),
                "retrieved_at": "2026-08-24T09:00:00Z",
                "access_policy": "authorized read-only SEC workflow export",
                "parser": {"name": "sec_fixture_text", "version": "1"},
                "derived_facts": facts,
            }
        )
    return {
        "schema_version": "longworld.sec-filing-input.v1",
        "source_status": source_status,
        "authorization": {
            "record_id": "SEC-WORKFLOW-AUTH-001",
            "scope": "read-only workflow source normalization",
            "basis": "authorized source bundle test",
            "reviewed_at": "2026-08-24T10:00:00Z",
        },
        "filings": filings,
    }


def _write_source_manifest(
    tmp_path: Path,
    *,
    name: str = "sec-manifest.json",
    source_status: str = "authorized_download",
    signed: bool = True,
) -> Path:
    payload = build_sec_filing_manifest(
        _sec_input(tmp_path, source_status=source_status),
        tmp_path,
        generated_at="2026-08-24T11:00:00Z",
    )
    if signed:
        payload = attach_attestation(payload, TEST_KEY, purpose="source_manifest")
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _entry(path: str, raw: bytes) -> dict:
    return {
        "kind": SEC_SOURCE_KIND,
        "target_domain": "company",
        "path": path,
        "sha256": _sha256(raw),
        "schema_version": SEC_FILING_MANIFEST_SCHEMA,
        "adapter_revision": SOURCE_WORKFLOW_ADAPTER_REVISION,
    }


def test_paper_fetch_manifest_v2_is_an_allowed_bundle_entry_contract() -> None:
    entry = sourcebundle_module._entry_from_raw(
        {
            "kind": "paper_workflow",
            "target_domain": "researchlab",
            "path": "paper-manifest.json",
            "sha256": "a" * 64,
            "schema_version": "longworld.paper-workflow-manifest.v2",
            "adapter_revision": SOURCE_WORKFLOW_ADAPTER_REVISION,
        }
    )

    assert entry.schema_version == "longworld.paper-workflow-manifest.v2"


def _write_bundle(
    tmp_path: Path,
    entries: list[dict],
    *,
    signed: bool = True,
    name: str = "source-bundle.json",
) -> Path:
    payload = {
        "schema_version": SOURCE_WORKFLOW_BUNDLE_SCHEMA,
        "n": len(entries),
        "entries": entries,
    }
    if signed:
        payload = attach_attestation(
            payload, TEST_KEY, purpose=SOURCE_WORKFLOW_BUNDLE_PURPOSE
        )
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_bundle(tmp_path: Path) -> tuple[Path, Path]:
    manifest_path = _write_source_manifest(tmp_path)
    bundle_path = _write_bundle(
        tmp_path, [_entry(manifest_path.name, manifest_path.read_bytes())]
    )
    return bundle_path, manifest_path


def test_loads_attested_bundle_and_returns_immutable_effective_binding(
    tmp_path: Path,
) -> None:
    bundle_path, _ = _valid_bundle(tmp_path)

    loaded = load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)

    assert len(loaded.workflows) == 1
    assert loaded.workflows[0].source_origin is SourceOrigin.REAL_PRIVATE_EXPORT
    assert loaded.bindings[0].kind == SEC_SOURCE_KIND
    assert loaded.bindings[0].component_digests == (
        loaded.workflows[0].component_digest,
    )
    assert loaded.bundle_sha256 == _sha256(bundle_path.read_bytes())
    assert len(loaded.binding_digest) == 64
    with pytest.raises(FrozenInstanceError):
        loaded.binding_digest = "0" * 64  # type: ignore[misc]


def test_bundle_and_each_manifest_are_read_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_path, manifest_path = _valid_bundle(tmp_path)
    original_reader = sourcebundle_module._read_regular_file
    reads: list[Path] = []

    def counting_reader(path: Path, max_bytes: int) -> bytes:
        reads.append(path)
        return original_reader(path, max_bytes)

    monkeypatch.setattr(sourcebundle_module, "_read_regular_file", counting_reader)

    load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)

    assert reads == [bundle_path, manifest_path]


def test_unsigned_or_tampered_outer_bundle_never_authorizes_inventory(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_manifest(tmp_path)
    entry = _entry(manifest_path.name, manifest_path.read_bytes())
    unsigned_path = _write_bundle(tmp_path, [entry], signed=False, name="unsigned.json")
    signed_path = _write_bundle(tmp_path, [entry], name="tampered.json")
    tampered = json.loads(signed_path.read_text(encoding="utf-8"))
    tampered["n"] = 2
    signed_path.write_text(json.dumps(tampered), encoding="utf-8")

    for path in (unsigned_path, signed_path):
        with pytest.raises(ProvenanceError, match="bundle.*attestation"):
            load_source_workflow_bundle(path, attestation_key=TEST_KEY)


def test_inner_source_manifest_requires_its_own_valid_attestation(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_manifest(tmp_path, signed=False)
    bundle_path = _write_bundle(
        tmp_path, [_entry(manifest_path.name, manifest_path.read_bytes())]
    )

    with pytest.raises(ProvenanceError, match="manifest.*attestation"):
        load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)


def test_signed_fixture_manifest_cannot_be_promoted_by_a_valid_bundle(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_manifest(tmp_path, source_status="test_fixture")
    bundle_path = _write_bundle(
        tmp_path, [_entry(manifest_path.name, manifest_path.read_bytes())]
    )

    with pytest.raises(ProvenanceError, match="test fixture"):
        load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "../sec-manifest.json",
        "/tmp/sec-manifest.json",
        "*.json",
        "nested/../sec-manifest.json",
        "nested\\sec-manifest.json",
        "C:/sec-manifest.json",
    ),
)
def test_rejects_nonliteral_or_unsafe_entry_paths(
    tmp_path: Path, unsafe_path: str
) -> None:
    manifest_path = _write_source_manifest(tmp_path)
    bundle_path = _write_bundle(
        tmp_path, [_entry(unsafe_path, manifest_path.read_bytes())]
    )

    with pytest.raises(ProvenanceError, match="path.*unsafe"):
        load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)


def test_rejects_final_and_intermediate_symlinks(tmp_path: Path) -> None:
    target_dir = tmp_path / "actual"
    target_dir.mkdir()
    manifest_path = _write_source_manifest(target_dir)
    final_link = tmp_path / "linked-manifest.json"
    final_link.symlink_to(manifest_path)
    directory_link = tmp_path / "linked-directory"
    directory_link.symlink_to(target_dir, target_is_directory=True)

    for name, path in (
        ("final-bundle.json", final_link),
        ("directory-bundle.json", directory_link / manifest_path.name),
    ):
        entry = _entry(str(path.relative_to(tmp_path)), manifest_path.read_bytes())
        bundle_path = _write_bundle(tmp_path, [entry], name=name)
        with pytest.raises(ProvenanceError, match="symlink"):
            load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)


def test_rejects_duplicate_paths_before_reusing_entry_bytes(tmp_path: Path) -> None:
    manifest_path = _write_source_manifest(tmp_path)
    entry = _entry(manifest_path.name, manifest_path.read_bytes())
    bundle_path = _write_bundle(tmp_path, [entry, dict(entry)])

    with pytest.raises(ProvenanceError, match="path.*duplicated"):
        load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)


def test_rejects_same_semantic_component_from_distinct_paths(tmp_path: Path) -> None:
    first = _write_source_manifest(tmp_path, name="first.json")
    second = tmp_path / "second.json"
    second.write_bytes(first.read_bytes())
    bundle_path = _write_bundle(
        tmp_path,
        [
            _entry(first.name, first.read_bytes()),
            _entry(second.name, second.read_bytes()),
        ],
    )

    with pytest.raises(ProvenanceError, match="duplicates.*component"):
        load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("kind", "paper_workflow"),
        ("target_domain", "researchlab"),
        ("schema_version", "longworld.paper-workflow-manifest.v1"),
        ("adapter_revision", "sourceworkflow@999"),
        ("sha256", "0" * 64),
    ),
)
def test_entry_contract_is_bound_to_kind_domain_schema_revision_and_bytes(
    tmp_path: Path, field: str, value: str
) -> None:
    manifest_path = _write_source_manifest(tmp_path)
    entry = _entry(manifest_path.name, manifest_path.read_bytes())
    entry[field] = value
    bundle_path = _write_bundle(tmp_path, [entry])

    with pytest.raises(ProvenanceError, match="entry|digest"):
        load_source_workflow_bundle(bundle_path, attestation_key=TEST_KEY)
