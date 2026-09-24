"""Losslessly compose disjoint frozen Wiki snapshots for longer reader contexts.

Composition changes the document set and its position/length distribution. It
does not create new factual relations or certify long-range dependency.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from longworld.synthesis import wiki_adapter

SCHEMA = "longworld.wiki-snapshot-concat.v1"
_COLLECTIONS = (
    ("documents", "doc_id"),
    ("entities", "entity_id"),
    ("facts", "fact_id"),
    ("relations", "relation_id"),
    ("ungrounded_quarantine", "item_id"),
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pinned(path: Path, sha256: str) -> tuple[dict[str, Any], str]:
    """Read one immutable source and reject a changed or invalid snapshot."""
    raw = path.read_bytes()
    actual = _digest(raw)
    if actual != sha256:
        raise ValueError(f"snapshot hash mismatch: {path}")
    snapshot = json.loads(raw)
    if not isinstance(snapshot, dict):
        raise TypeError(f"snapshot is not an object: {path}")
    wiki_adapter.validate_snapshot(snapshot)
    revisions = snapshot["source"]["revisions"]
    for doc in snapshot["documents"]:
        title = doc["title"]
        if not doc.get("revision_url", "").endswith(f"oldid={revisions[title]}"):
            raise ValueError(f"revision URL mismatch: {path}: {title}")
    return snapshot, actual


def compose_snapshots(
    components: Sequence[tuple[dict[str, Any], str, str]], *, split: str
) -> dict[str, Any]:
    """Merge disjoint source records without rewriting IDs, text, or offsets.

    Each tuple is ``(snapshot, pinned_sha256, declared_split)``. Repeated page
    titles and *any* repeated record IDs fail, including otherwise identical
    entities: v1 cannot represent their distinct anchor mentions losslessly.
    """
    if split not in {"train", "eval"} or len(components) < 2:
        raise ValueError("composition needs at least two same-split snapshots")
    merged: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in _COLLECTIONS}
    seen: dict[str, set[str]] = {name: set() for name, _ in _COLLECTIONS}
    revisions: dict[str, int] = {}
    provenance: list[dict[str, Any]] = []
    licenses: list[dict[str, Any] | str] = []
    seen_sources: set[str] = set()
    for snapshot, sha256, declared_split in components:
        wiki_adapter.validate_snapshot(snapshot)
        if declared_split != split:
            raise ValueError("component split mismatch")
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise ValueError("component needs pinned sha256")
        if sha256 in seen_sources or snapshot["snapshot_id"] in {
            item["snapshot_id"] for item in provenance
        }:
            raise ValueError("duplicate component snapshot")
        seen_sources.add(sha256)
        source = snapshot["source"]
        for title, revision in source["revisions"].items():
            if title in revisions:
                raise ValueError(f"duplicate page title/revision: {title}")
            revisions[title] = revision
        for name, key in _COLLECTIONS:
            for record in snapshot[name]:
                record_id = record[key]
                if record_id in seen[name]:
                    raise ValueError(f"conflicting {key}: {record_id}")
                seen[name].add(record_id)
                merged[name].append(copy.deepcopy(record))
        licenses.append(source["license"])
        provenance.append(
            {
                "snapshot_id": snapshot["snapshot_id"],
                "sha256": sha256,
                "frozen_at": snapshot["frozen_at"],
                "source": copy.deepcopy(source),
                "document_ids": [doc["doc_id"] for doc in snapshot["documents"]],
            }
        )
    if any(license != licenses[0] for license in licenses[1:]):
        raise ValueError("component license metadata mismatch")
    for name, key in _COLLECTIONS:
        merged[name].sort(key=lambda record: record[key])
    identity = _digest(
        json.dumps(
            [(item["snapshot_id"], item["sha256"]) for item in provenance],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    snapshot = {
        "schema_version": wiki_adapter.SOURCE_SNAPSHOT_SCHEMA,
        "snapshot_id": f"snapshot_concat_{identity[:20]}",
        "frozen_at": max(item["frozen_at"] for item in provenance),
        "source": {
            "kind": "mediawiki_frozen_composite",
            "license": copy.deepcopy(licenses[0]),
            "revisions": dict(sorted(revisions.items())),
            "composition_schema": SCHEMA,
            "composition_split": split,
            "components": provenance,
        },
        **merged,
    }
    wiki_adapter.validate_snapshot(snapshot)
    return snapshot
