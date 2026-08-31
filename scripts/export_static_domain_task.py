#!/usr/bin/env python3
"""Build audited nonproduction tasks for registered static domain adapters."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.gamecompat import (
    GAME_COMPAT_ADAPTER_ID,
    GAME_COMPAT_REPLAY_REVISION,
    GAME_COMPAT_SOURCE_SCHEMA,
    GAME_COMPAT_TASK_SCHEMA,
    audit_game_compatibility_task,
    build_game_compatibility_task,
)
from longworld.core.macrovintage import (
    MACRO_VINTAGE_ADAPTER_ID,
    MACRO_VINTAGE_REPLAY_REVISION,
    MACRO_VINTAGE_SOURCE_SCHEMA,
    MACRO_VINTAGE_TASK_SCHEMA,
    audit_macro_vintage_task,
    build_macro_vintage_task,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.staticreplay import canonical_json
from longworld.core.visualcompat import (
    VISUAL_COMPAT_ADAPTER_ID,
    VISUAL_COMPAT_REPLAY_REVISION,
    VISUAL_COMPAT_SOURCE_SCHEMA,
    VISUAL_COMPAT_TASK_SCHEMA,
    audit_visual_compatibility_task,
    build_visual_compatibility_task,
)

MAX_STATIC_SOURCE_PAYLOAD_BYTES = 16_000_000

Builder = Callable[[dict[str, Any]], dict[str, Any]]
Auditor = Callable[[dict[str, Any]], dict[str, bool]]
_ADAPTERS: dict[str, tuple[str, str, str, Builder, Auditor]] = {
    MACRO_VINTAGE_ADAPTER_ID: (
        MACRO_VINTAGE_REPLAY_REVISION,
        MACRO_VINTAGE_SOURCE_SCHEMA,
        MACRO_VINTAGE_TASK_SCHEMA,
        build_macro_vintage_task,
        audit_macro_vintage_task,
    ),
    VISUAL_COMPAT_ADAPTER_ID: (
        VISUAL_COMPAT_REPLAY_REVISION,
        VISUAL_COMPAT_SOURCE_SCHEMA,
        VISUAL_COMPAT_TASK_SCHEMA,
        build_visual_compatibility_task,
        audit_visual_compatibility_task,
    ),
    GAME_COMPAT_ADAPTER_ID: (
        GAME_COMPAT_REPLAY_REVISION,
        GAME_COMPAT_SOURCE_SCHEMA,
        GAME_COMPAT_TASK_SCHEMA,
        build_game_compatibility_task,
        audit_game_compatibility_task,
    ),
}


def _canonical_bytes(value: object) -> bytes:
    return canonical_json(value).encode() + b"\n"


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def export_static_domain_task(
    adapter_id: str,
    source_payload_path: Path,
    candidate_path: Path,
    audit_path: Path,
) -> dict[str, Any]:
    """Validate source facts and write one explicitly nonproduction task."""
    contract = _ADAPTERS.get(adapter_id)
    if contract is None:
        raise ProvenanceError("static task export adapter is not registered")
    resolved_paths = {
        source_payload_path.resolve(),
        candidate_path.resolve(),
        audit_path.resolve(),
    }
    if len(resolved_paths) != 3:
        raise ProvenanceError("static task export paths must be distinct")
    raw = _read_regular_file(source_payload_path, MAX_STATIC_SOURCE_PAYLOAD_BYTES)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("static task source payload is not UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ProvenanceError("static task source payload must be an object")
    replay_revision, source_schema, task_schema, builder, auditor = contract
    task = builder(payload)
    audit = auditor(task)
    if not audit or not all(audit.values()):
        raise ProvenanceError("static task executable audit failed")
    candidate_bytes = _canonical_bytes(task)
    receipt: dict[str, Any] = {
        "schema_version": "longworld.static-domain-task-audit.v1",
        "data_stage": "candidate_task_audit",
        "adapter_id": adapter_id,
        "adapter_revision": replay_revision,
        "source_schema_version": source_schema,
        "task_schema_version": task_schema,
        "source_payload_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "source_binding_status": "requires_signed_replay_sidecar",
        "promotion_status": "ignored_non_world_candidate",
        "n": 1,
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "promoted": False,
        "generation_integration": "disabled",
        "audit": audit,
    }
    _atomic_write(candidate_path, candidate_bytes)
    _atomic_write(audit_path, _canonical_bytes(receipt))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", choices=sorted(_ADAPTERS), required=True)
    parser.add_argument("--source-payload", type=Path, required=True)
    parser.add_argument("--candidate-jsonl", type=Path, required=True)
    parser.add_argument("--audit-out", type=Path, required=True)
    args = parser.parse_args()
    receipt = export_static_domain_task(
        args.adapter,
        args.source_payload,
        args.candidate_jsonl,
        args.audit_out,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
