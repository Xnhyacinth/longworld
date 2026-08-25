"""Provenance-aware public documents used as role-bearing source packs."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

from longworld.core.provenance import (
    ProvenanceError,
    SourceDocument,
    load_source_documents,
)
from longworld.core.render import Artifact
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    classify_artifact,
)

ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = ROOT / "data" / "source_pack"
# ~225k tokens. One long RFC may fill a 256k cap; 16k packs skip oversized.
MAX_SOURCE_CHARS = 900_000


def _iter_documents(
    pack_dir: Path = PACK_DIR, *, allow_legacy: bool = True
) -> list[SourceDocument]:
    if not pack_dir.is_dir():
        return []
    try:
        docs = load_source_documents(pack_dir, allow_legacy=allow_legacy)
    except ProvenanceError:
        # Fail closed: an invalid v2 pack must never fall back to cached files.
        return []
    out: list[SourceDocument] = []
    for doc in docs:
        text = doc.text
        if len(text) < 400:
            continue
        if len(text) > MAX_SOURCE_CHARS:
            text = text[:MAX_SOURCE_CHARS] + "\n[truncated source pack]"
        out.append(
            SourceDocument(
                file=doc.file,
                stem=doc.stem,
                text=text,
                lineage=doc.lineage,
                provenance_verified=doc.provenance_verified,
            )
        )
    return out


def _iter_texts(
    pack_dir: Path = PACK_DIR, *, allow_legacy: bool = True
) -> list[tuple[str, str]]:
    """Compatibility view; provenance-aware callers should use `_iter_documents`."""
    return [
        (document.stem, document.text)
        for document in _iter_documents(pack_dir, allow_legacy=allow_legacy)
    ]


def source_pack_artifacts(
    world_id: str,
    start: date,
    n: int = 8,
    deny_substrings: list[str] | None = None,
    *,
    pack_dir: Path = PACK_DIR,
    allow_legacy: bool = True,
) -> list[Artifact]:
    docs = _iter_documents(pack_dir, allow_legacy=allow_legacy)
    if not docs:
        return []
    # Longest first so 128k/256k leftover span uses rfc9110, not mit-license.
    docs.sort(key=lambda item: -len(item.text))
    deny = [d for d in (deny_substrings or []) if d and len(d) >= 6]
    arts: list[Artifact] = []
    for i, document in enumerate(docs):
        if len(arts) >= n:
            break
        stem, text = document.stem, document.text
        if any(d in text for d in deny):
            continue
        digest = hashlib.sha256(text.encode()).hexdigest()[:8]
        lineage = document.lineage
        source_details = ""
        if lineage is not None:
            source_details = (
                f"Source-URL: {lineage.url}\n"
                f"License: {lineage.license}\n"
                f"Retrieved-at: {lineage.retrieved_at}\n"
                f"Content-SHA256: {lineage.sha256}\n"
            )
        body = (
            f"# Public source pack · {stem}\n"
            f"Doc-id: {world_id}.source.{stem}\n"
            f"{source_details}\n"
            f"{text}"
        )
        artifact = Artifact(
            artifact_id=f"{world_id}.source.{stem}.{digest}",
            doc_type="source_pack",
            time=start + timedelta(days=min(i, 20)),
            project="public",
            prefix="source",
            reveals_events=[],
            text=body,
            facts=[],
            slots={
                "event_type": "source_pack",
                "ground_values": [],
                "source_stem": stem,
                "source_role": "natural_background",
                "provenance_verified": document.provenance_verified,
                "training_objective": (
                    "unassigned" if document.provenance_verified else "diagnostic"
                ),
                "source_lineage": (
                    {
                        "provenance_id": lineage.provenance_id,
                        "url": lineage.url,
                        "license": lineage.license,
                        "retrieved_at": lineage.retrieved_at,
                        "parser": lineage.parser,
                        "sha256": lineage.sha256,
                        "source_path": lineage.source_path,
                    }
                    if lineage is not None
                    else {}
                ),
                "content_plan": {
                    "artifact_type": "source_pack",
                    "communicative_goal": "external_background",
                    "new_propositions": [f"source:{stem}"],
                },
            },
            is_focal=False,
            role="natural_background",
        )
        classify_artifact(
            artifact,
            source_origin=(
                SourceOrigin.REAL_PUBLIC
                if document.provenance_verified
                else SourceOrigin.UNKNOWN
            ),
            workflow_kind=WorkflowKind.BACKGROUND_ONLY,
            evidence_role=EvidenceRole.NATURAL_BACKGROUND,
            workflow_id=(
                f"source:{lineage.provenance_id}"
                if lineage is not None
                else f"legacy-source:{stem}"
            ),
            provenance_id=lineage.provenance_id if lineage is not None else "",
        )
        arts.append(artifact)
    return arts
