#!/usr/bin/env python3
"""Build one signed replay bundle from already source-attested manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.documentworkflow import (
    MAX_DOCUMENT_MANIFEST_BYTES,
    PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA,
    PAPER_WORKFLOW_MANIFEST_SCHEMA,
    WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA,
)
from longworld.core.filingworkflow import (
    MAX_SEC_MANIFEST_BYTES,
    SEC_FILING_MANIFEST_SCHEMA,
)
from longworld.core.issuerfilingworkflow import (
    ISSUER_IR_FILING_MANIFEST_SCHEMA,
    ISSUER_IR_SOURCE_KIND,
    MAX_ISSUER_IR_MANIFEST_BYTES,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.sourcebundle import (
    SOURCE_WORKFLOW_ADAPTER_REVISION,
    SOURCE_WORKFLOW_ADAPTER_REVISION_LATEST,
    SOURCE_WORKFLOW_BUNDLE_PURPOSE,
    SOURCE_WORKFLOW_BUNDLE_SCHEMA,
    LoadedSourceWorkflowBundle,
    load_source_workflow_bundle,
)

_CONTRACTS = {
    "paper_workflow": (
        "researchlab",
        {PAPER_WORKFLOW_MANIFEST_SCHEMA, PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA},
        MAX_DOCUMENT_MANIFEST_BYTES,
    ),
    "sec_filing": ("company", {SEC_FILING_MANIFEST_SCHEMA}, MAX_SEC_MANIFEST_BYTES),
    "wikimedia": (
        "researchlab",
        {WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA},
        MAX_DOCUMENT_MANIFEST_BYTES,
    ),
    ISSUER_IR_SOURCE_KIND: (
        "company",
        {ISSUER_IR_FILING_MANIFEST_SCHEMA},
        MAX_ISSUER_IR_MANIFEST_BYTES,
    ),
}


def build_source_workflow_bundle(
    manifests: list[tuple[str, Path]],
    output_path: Path,
    *,
    attestation_key: bytes | None = None,
    adapter_revision: str = SOURCE_WORKFLOW_ADAPTER_REVISION,
) -> LoadedSourceWorkflowBundle:
    """Write atomically, then reload the exact signed bundle before promotion use."""
    if not manifests:
        raise ProvenanceError("source workflow bundle requires at least one manifest")
    key = attestation_key or attestation_key_from_env(SOURCE_WORKFLOW_BUNDLE_PURPOSE)
    if key is None:
        raise ProvenanceError("source workflow bundle requires a source key")
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    entries: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for kind, manifest_path in manifests:
        contract = _CONTRACTS.get(kind)
        if contract is None:
            raise ProvenanceError("source workflow bundle kind is unsupported")
        target_domain, schemas, max_bytes = contract
        if (
            manifest_path.parent != output_path.parent
            or manifest_path.name == output_path.name
        ):
            raise ProvenanceError("source manifests must share the bundle directory")
        if manifest_path.name in seen_paths:
            raise ProvenanceError("source workflow manifest path is duplicated")
        raw = _read_regular_file(manifest_path, max_bytes)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProvenanceError("source workflow manifest is invalid JSON") from error
        schema = payload.get("schema_version") if isinstance(payload, dict) else None
        if schema not in schemas:
            raise ProvenanceError("source workflow manifest schema does not match kind")
        entries.append(
            {
                "kind": kind,
                "target_domain": target_domain,
                "path": manifest_path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "schema_version": str(schema),
                "adapter_revision": adapter_revision,
            }
        )
        seen_paths.add(manifest_path.name)
    signed = attach_attestation(
        {
            "schema_version": SOURCE_WORKFLOW_BUNDLE_SCHEMA,
            "n": len(entries),
            "entries": sorted(entries, key=lambda entry: entry["path"]),
        },
        key,
        purpose=SOURCE_WORKFLOW_BUNDLE_PURPOSE,
    )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=".source-workflow-bundle-",
            suffix=".json",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(signed, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        loaded = load_source_workflow_bundle(Path(temporary_name), attestation_key=key)
        os.replace(temporary_name, output_path)
        temporary_name = None
        return loaded
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-manifest", action="append", type=Path, default=[])
    parser.add_argument("--sec-manifest", action="append", type=Path, default=[])
    parser.add_argument("--wikimedia-manifest", action="append", type=Path, default=[])
    parser.add_argument("--issuer-ir-manifest", action="append", type=Path, default=[])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--adapter-revision",
        choices=(
            SOURCE_WORKFLOW_ADAPTER_REVISION,
            SOURCE_WORKFLOW_ADAPTER_REVISION_LATEST,
        ),
        default=SOURCE_WORKFLOW_ADAPTER_REVISION,
    )
    args = parser.parse_args()
    manifests = [
        *(("paper_workflow", path) for path in args.paper_manifest),
        *(("sec_filing", path) for path in args.sec_manifest),
        *(("wikimedia", path) for path in args.wikimedia_manifest),
        *((ISSUER_IR_SOURCE_KIND, path) for path in args.issuer_ir_manifest),
    ]
    build_source_workflow_bundle(
        manifests, args.out, adapter_revision=args.adapter_revision
    )
    print(args.out)


if __name__ == "__main__":
    main()
