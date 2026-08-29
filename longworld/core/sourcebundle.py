"""Signed, exact-byte loading for real-source workflow inventories.

The bundle is the authorization boundary between disabled source inventories and
the pure adapters in :mod:`longworld.core.sourceworkflow`.  Each inventory keeps
its own ``source_manifest`` attestation; the enclosing bundle independently binds
the literal path, exact bytes, schema, adapter revision, and resulting connected
components.  No loader in this module promotes a fixture or claims production
replay.
"""

from __future__ import annotations

import glob
import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    PURPOSE_ROLES,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.documentworkflow import (
    _PAPER_RELATION_ROLES,
    _WIKIMEDIA_RELATION_KINDS,
    MAX_DOCUMENT_MANIFEST_BYTES,
    PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA,
    PAPER_WORKFLOW_MANIFEST_SCHEMA,
    WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA,
    _audit_fetched_paper_manifest,
    _audit_manifest,
    _paper_record_identity,
    _validate_paper_relation,
    _validate_public_wikimedia_records,
    _validate_wikipedia_relation,
    _wikipedia_record_identity,
)
from longworld.core.filingworkflow import (
    MAX_SEC_MANIFEST_BYTES,
    SEC_FILING_MANIFEST_SCHEMA,
    _audit_exported_manifest,
)
from longworld.core.issuerfilingworkflow import (
    ISSUER_IR_FILING_MANIFEST_SCHEMA,
    ISSUER_IR_SOURCE_KIND,
    MAX_ISSUER_IR_MANIFEST_BYTES,
    _audit_issuer_ir_filing_manifest,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.sourceworkflow import (
    PAPER_SOURCE_KIND,
    SEC_SOURCE_KIND,
    SOURCE_WORKFLOW_ADAPTER_REVISION_V1,
    SOURCE_WORKFLOW_ADAPTER_REVISION_V2,
    SOURCE_WORKFLOW_ADAPTER_REVISIONS,
    WIKIMEDIA_SOURCE_KIND,
    SourceWorkflow,
    adapt_source_manifest,
)

SOURCE_WORKFLOW_BUNDLE_SCHEMA = "longworld.source-workflow-bundle.v1"
SOURCE_WORKFLOW_BUNDLE_PURPOSE = "source_workflow_bundle"
SOURCE_WORKFLOW_ADAPTER_REVISION = SOURCE_WORKFLOW_ADAPTER_REVISION_V1
SOURCE_WORKFLOW_ADAPTER_REVISION_LATEST = SOURCE_WORKFLOW_ADAPTER_REVISION_V2
MAX_SOURCE_WORKFLOW_BUNDLE_BYTES = 2_000_000
MAX_SOURCE_WORKFLOW_ENTRIES = 512

_SHA256_LENGTH = 64
_ENTRY_FIELDS = {
    "kind",
    "target_domain",
    "path",
    "sha256",
    "schema_version",
    "adapter_revision",
}
_KIND_CONTRACTS = {
    SEC_SOURCE_KIND: (
        "company",
        frozenset({SEC_FILING_MANIFEST_SCHEMA}),
        MAX_SEC_MANIFEST_BYTES,
    ),
    PAPER_SOURCE_KIND: (
        "researchlab",
        frozenset(
            {PAPER_WORKFLOW_MANIFEST_SCHEMA, PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA}
        ),
        MAX_DOCUMENT_MANIFEST_BYTES,
    ),
    WIKIMEDIA_SOURCE_KIND: (
        "researchlab",
        frozenset({WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA}),
        MAX_DOCUMENT_MANIFEST_BYTES,
    ),
    ISSUER_IR_SOURCE_KIND: (
        "company",
        frozenset({ISSUER_IR_FILING_MANIFEST_SCHEMA}),
        MAX_ISSUER_IR_MANIFEST_BYTES,
    ),
}


@dataclass(frozen=True)
class SourceBundleBinding:
    kind: str
    target_domain: str
    path: str
    sha256: str
    schema_version: str
    adapter_revision: str
    component_digests: tuple[str, ...]


@dataclass(frozen=True)
class LoadedSourceWorkflowBundle:
    bundle_sha256: str
    binding_digest: str
    adapter_revision: str
    bindings: tuple[SourceBundleBinding, ...]
    workflows: tuple[SourceWorkflow, ...]


@dataclass(frozen=True)
class _Entry:
    kind: str
    target_domain: str
    path: str
    sha256: str
    schema_version: str
    adapter_revision: str
    max_bytes: int


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProvenanceError("source workflow entry path is unsafe")
    if "\\" in value or ":" in value or "\x00" in value or glob.has_magic(value):
        raise ProvenanceError("source workflow entry path is unsafe")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.suffix.lower() != ".json"
    ):
        raise ProvenanceError("source workflow entry path is unsafe")
    return value


def _entry_from_raw(raw: object) -> _Entry:
    if not isinstance(raw, Mapping) or set(raw) != _ENTRY_FIELDS:
        raise ProvenanceError("source workflow bundle entry is invalid")
    kind = str(raw.get("kind") or "")
    contract = _KIND_CONTRACTS.get(kind)
    if contract is None:
        raise ProvenanceError("source workflow bundle entry kind is invalid")
    expected_domain, expected_schemas, max_bytes = contract
    target_domain = str(raw.get("target_domain") or "")
    schema_version = str(raw.get("schema_version") or "")
    adapter_revision = str(raw.get("adapter_revision") or "")
    declared_sha256 = str(raw.get("sha256") or "")
    if (
        target_domain != expected_domain
        or schema_version not in expected_schemas
        or adapter_revision not in SOURCE_WORKFLOW_ADAPTER_REVISIONS
        or not _is_sha256(declared_sha256)
    ):
        raise ProvenanceError("source workflow bundle entry contract is invalid")
    return _Entry(
        kind=kind,
        target_domain=target_domain,
        path=_safe_relative_path(raw.get("path")),
        sha256=declared_sha256,
        schema_version=schema_version,
        adapter_revision=adapter_revision,
        max_bytes=max_bytes,
    )


def _entry_path(base_directory: Path, relative: str) -> Path:
    candidate = base_directory
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        candidate = candidate / part
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise ProvenanceError(
                f"cannot inspect source workflow entry: {relative}"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise ProvenanceError("source workflow entry path contains a symlink")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ProvenanceError("source workflow entry parent is not a directory")
        if index == len(parts) - 1 and not stat.S_ISREG(info.st_mode):
            raise ProvenanceError("source workflow entry is not a regular file")
    return candidate


def _json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ProvenanceError(f"{label} must be a JSON object")
    return payload


def _verify_document_manifest(payload: dict[str, Any], *, kind: str) -> None:
    if kind == PAPER_SOURCE_KIND:
        schema = payload.get("schema_version")
        if schema == PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA:
            raw_relations = payload.get("relations")
            if not isinstance(raw_relations, list) or not raw_relations:
                raise ProvenanceError("paper fetch manifest has no source relations")
            required_relations = {
                str(relation.get("kind") or "")
                for relation in raw_relations
                if isinstance(relation, dict)
            }
        else:
            required_relations = set(_PAPER_RELATION_ROLES)
        _audit_manifest(
            payload,
            schema_version=str(schema),
            identity_builder=_paper_record_identity,
            required_relations=required_relations,
            relation_validator=_validate_paper_relation,
        )
        if schema == PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA:
            _audit_fetched_paper_manifest(payload)
        return
    _audit_manifest(
        payload,
        schema_version=WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA,
        identity_builder=_wikipedia_record_identity,
        required_relations=set(_WIKIMEDIA_RELATION_KINDS),
        relation_validator=_validate_wikipedia_relation,
    )
    if payload.get("source_status") == "public_api_export":
        _validate_public_wikimedia_records(payload)
    elif "fetch_receipt" in payload:
        raise ProvenanceError("non-public Wikimedia manifest has a fetch receipt")


def _verified_manifest_payload(
    raw: bytes,
    *,
    entry: _Entry,
    base_directory: Path,
    attestation_key: bytes | None,
) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ProvenanceError(f"source workflow entry digest mismatch: {entry.path}")
    payload = _json_object(raw, label="source workflow manifest")
    if payload.get("schema_version") != entry.schema_version:
        raise ProvenanceError("source workflow entry schema does not match its payload")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if not verify_attestation(payload, key, purpose="source_manifest"):
        raise ProvenanceError("source manifest has no valid source attestation")
    if entry.kind == SEC_SOURCE_KIND:
        _audit_exported_manifest(
            payload,
            base_directory=base_directory,
            manifest_name=Path(entry.path).name,
        )
    elif entry.kind == ISSUER_IR_SOURCE_KIND:
        _audit_issuer_ir_filing_manifest(payload)
    else:
        _verify_document_manifest(payload, kind=entry.kind)
    return payload


def _binding_digest(bindings: Sequence[SourceBundleBinding]) -> str:
    payload = {
        "schema_version": SOURCE_WORKFLOW_BUNDLE_SCHEMA,
        "bindings": [
            {
                "kind": binding.kind,
                "target_domain": binding.target_domain,
                "path": binding.path,
                "sha256": binding.sha256,
                "schema_version": binding.schema_version,
                "adapter_revision": binding.adapter_revision,
                "component_digests": list(binding.component_digests),
            }
            for binding in bindings
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def load_source_workflow_bundle(
    path: Path, *, attestation_key: bytes | None = None
) -> LoadedSourceWorkflowBundle:
    """Load one producer-attested set of exact source-workflow manifests."""
    try:
        raw_bundle = _read_regular_file(path, MAX_SOURCE_WORKFLOW_BUNDLE_BYTES)
    except (OSError, ProvenanceError) as exc:
        raise ProvenanceError(f"cannot read source workflow bundle: {exc}") from exc
    payload = _json_object(raw_bundle, label="source workflow bundle")
    if payload.get("schema_version") != SOURCE_WORKFLOW_BUNDLE_SCHEMA:
        raise ProvenanceError("unsupported source workflow bundle schema")
    if PURPOSE_ROLES.get(SOURCE_WORKFLOW_BUNDLE_PURPOSE) != "source":
        raise ProvenanceError(
            "source workflow bundle purpose is not registered to the source role"
        )
    key = attestation_key or attestation_key_from_env(SOURCE_WORKFLOW_BUNDLE_PURPOSE)
    raw_attestation = payload.get("attestation")
    if (
        not isinstance(raw_attestation, Mapping)
        or raw_attestation.get("scheme") != ATTESTATION_V2_SCHEME
        or raw_attestation.get("role") != "source"
        or not verify_attestation(payload, key, purpose=SOURCE_WORKFLOW_BUNDLE_PURPOSE)
    ):
        raise ProvenanceError(
            "source workflow bundle has no valid producer attestation"
        )
    if set(payload) != {"schema_version", "n", "entries", "attestation"}:
        raise ProvenanceError("source workflow bundle fields are invalid")
    raw_entries = payload.get("entries")
    count = payload.get("n")
    if (
        not isinstance(raw_entries, Sequence)
        or isinstance(raw_entries, (str, bytes))
        or not raw_entries
        or len(raw_entries) > MAX_SOURCE_WORKFLOW_ENTRIES
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != len(raw_entries)
    ):
        raise ProvenanceError("source workflow bundle entry count is invalid")

    entries = [_entry_from_raw(raw) for raw in raw_entries]
    adapter_revisions = {entry.adapter_revision for entry in entries}
    if len(adapter_revisions) != 1:
        raise ProvenanceError("source workflow bundle mixes adapter revisions")
    paths = [entry.path for entry in entries]
    if len(set(paths)) != len(paths):
        raise ProvenanceError("source workflow entry path is duplicated")

    workflows: list[SourceWorkflow] = []
    bindings: list[SourceBundleBinding] = []
    seen_components: set[str] = set()
    for entry in sorted(entries, key=lambda item: item.path):
        entry_path = _entry_path(path.parent, entry.path)
        try:
            raw_manifest = _read_regular_file(entry_path, entry.max_bytes)
        except (OSError, ProvenanceError) as exc:
            raise ProvenanceError(
                f"cannot read source workflow entry: {entry.path}"
            ) from exc
        manifest = _verified_manifest_payload(
            raw_manifest,
            entry=entry,
            base_directory=entry_path.parent,
            attestation_key=attestation_key,
        )
        entry_workflows = adapt_source_manifest(
            manifest,
            source_kind=entry.kind,
            signed_bundle_authorized=True,
            adapter_revision=entry.adapter_revision,
        )
        component_digests = tuple(
            sorted(workflow.component_digest for workflow in entry_workflows)
        )
        if any(digest in seen_components for digest in component_digests):
            raise ProvenanceError("source workflow bundle duplicates a component")
        seen_components.update(component_digests)
        workflows.extend(entry_workflows)
        bindings.append(
            SourceBundleBinding(
                kind=entry.kind,
                target_domain=entry.target_domain,
                path=entry.path,
                sha256=entry.sha256,
                schema_version=entry.schema_version,
                adapter_revision=entry.adapter_revision,
                component_digests=component_digests,
            )
        )

    immutable_bindings = tuple(bindings)
    return LoadedSourceWorkflowBundle(
        bundle_sha256=hashlib.sha256(raw_bundle).hexdigest(),
        binding_digest=_binding_digest(immutable_bindings),
        adapter_revision=adapter_revisions.pop(),
        bindings=immutable_bindings,
        workflows=tuple(sorted(workflows, key=lambda workflow: workflow.workflow_id)),
    )
