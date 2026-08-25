#!/usr/bin/env python3
"""Extend an attested episode bundle with explicit, verified exports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.realworkflow import (
    EPISODE_REPLAY_BUNDLE_SCHEMA,
    load_episode_replay_bundle,
    load_git_workflow_export,
    read_episode_replay_bundle_bytes,
    read_git_workflow_export_bytes,
)


def extend_bundle(bundle_path: Path, episodes: list[Path]) -> int:
    key = attestation_key_from_env("episode_replay_bundle")
    if key is None:
        raise ValueError("LONGWORLD_ATTESTATION_KEY must contain at least 32 bytes")
    bundle_raw = read_episode_replay_bundle_bytes(bundle_path)
    load_episode_replay_bundle(bundle_path, _verified_raw=bundle_raw)
    payload = json.loads(bundle_raw)
    existing = {str(entry["path"]) for entry in payload["episodes"]}
    root = bundle_path.parent.parent
    additions: list[dict[str, str]] = []
    for episode in episodes:
        episode_raw = read_git_workflow_export_bytes(episode)
        resolved = episode.resolve()
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("episode must be inside the repository") from error
        relative_text = relative.as_posix()
        if relative_text in existing:
            raise ValueError(f"episode is already in the bundle: {relative_text}")
        load_git_workflow_export(resolved, _verified_raw=episode_raw)
        additions.append(
            {
                "path": relative_text,
                "sha256": hashlib.sha256(episode_raw).hexdigest(),
            }
        )
        existing.add(relative_text)
    unsigned = {
        "schema_version": EPISODE_REPLAY_BUNDLE_SCHEMA,
        "composition": "chronological_causal_union",
        "path_base": "repository_root",
        "episodes": [*payload["episodes"], *additions],
    }
    signed = attach_attestation(unsigned, key, purpose="episode_replay_bundle")
    _write_bundle_atomic(bundle_path, signed)
    load_episode_replay_bundle(bundle_path)
    return len(additions)


def create_bundle(bundle_path: Path, episodes: list[Path]) -> int:
    """Create a new signed bundle from exports validated by the active source key."""
    key = attestation_key_from_env("episode_replay_bundle")
    if key is None:
        raise ValueError("source attestation key must contain at least 32 bytes")
    if bundle_path.exists():
        raise FileExistsError(f"bundle already exists: {bundle_path}")
    if not episodes:
        raise ValueError("at least one episode is required")
    root = bundle_path.parent.parent.resolve()
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for episode in episodes:
        episode_raw = read_git_workflow_export_bytes(episode)
        resolved = episode.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError("episode must be inside the repository") from error
        if relative in seen:
            raise ValueError(f"duplicate episode: {relative}")
        load_git_workflow_export(
            resolved, attestation_key=key, _verified_raw=episode_raw
        )
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(episode_raw).hexdigest(),
            }
        )
        seen.add(relative)
    payload = attach_attestation(
        {
            "schema_version": EPISODE_REPLAY_BUNDLE_SCHEMA,
            "composition": "chronological_causal_union",
            "path_base": "repository_root",
            "episodes": entries,
        },
        key,
        purpose="episode_replay_bundle",
    )
    _write_bundle_atomic(bundle_path, payload)
    load_episode_replay_bundle(bundle_path, attestation_key=key)
    return len(entries)


def _write_bundle_atomic(bundle_path: Path, payload: dict) -> None:
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{bundle_path.name}.", suffix=".tmp", dir=bundle_path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, bundle_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--episode", type=Path, action="append", required=True)
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()
    count = (
        create_bundle(args.bundle, args.episode)
        if args.create
        else extend_bundle(args.bundle, args.episode)
    )
    print(json.dumps({"added": count, "bundle": str(args.bundle)}))


if __name__ == "__main__":
    main()
