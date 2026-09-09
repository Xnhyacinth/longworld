#!/usr/bin/env python3
"""Materialize or replay a source-readable CodeForge taskbank without training."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.codeforge_taskbank import (
    _facts,
    candidate_scopes,
    canonical,
    compile_tasks,
    context_identity,
    load_world,
    render_context,
)

FILES = ("world.json", "tasks.jsonl", "contexts.jsonl")
BANDS = {"64k": (64000, 65536), "128k": (128000, 131072), "256k": (256000, 262144)}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def materialize(config_path: Path, output: Path, *, validate: bool = False):
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "longworld.codeforge-taskbank-config.v1":
        raise ValueError("invalid codebank config")
    bundle = ROOT / config["source_bundle"]
    world = load_world(bundle, config["source_bundle_sha256"])
    if world["source_group_id"] != config["source_group_id"]:
        raise ValueError("source repository mismatch")
    eligible, rejected = [], []
    for episode in world["episodes"]:
        try:
            _facts(world, episode)
        except ValueError as exc:
            rejected.append({"episode_id": episode["episode_id"], "reason": str(exc)})
        else:
            eligible.append(episode)
    scopes = candidate_scopes({**world, "episodes": eligible})
    contexts, accepted_scopes = [], []
    with sanitized_attestation_environment():
        from scripts.materialize_finance_histories import _load_tokenizer

        tokenizer = _load_tokenizer(
            config["tokenizer"]["model_id"], config["tokenizer"]["revision"]
        )
        for scope in scopes:
            text = render_context(world, scope)
            count = len(tokenizer.encode(text, add_special_tokens=False))
            fits = [label for label, (_, cap) in BANDS.items() if count <= cap]
            context_id = context_identity(world, scope)
            contexts.append(
                {
                    "context_id": context_id,
                    "world_id": world["world_id"],
                    "episode_ids": scope,
                    "exact_context_tokens": count,
                    "context_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "smallest_capacity_bin": "cap" + fits[0] if fits else None,
                    "fitting_capacity_bins": ["cap" + b for b in fits],
                    "legacy_exact_bands": [
                        b for b, (lo, hi) in BANDS.items() if lo <= count <= hi
                    ],
                    "whole_source_episodes": True,
                }
            )
            if fits:
                accepted_scopes.append(scope)
    tasks = compile_tasks(world, accepted_scopes, split=config["split"])
    world["split"] = config["split"]
    payloads = {
        "world.json": canonical(world) + "\n",
        "tasks.jsonl": "".join(canonical(t) + "\n" for t in tasks),
        "contexts.jsonl": "".join(canonical(c) + "\n" for c in contexts),
    }
    inputs = [
        config_path.absolute(),
        Path(__file__).resolve(),
        ROOT / "longworld/core/codeforge_taskbank.py",
        ROOT / "longworld/core/realworkflow.py",
    ]
    receipt = {
        "schema_version": "longworld.codeforge-taskbank-build.v1",
        "world_id": world["world_id"],
        "source_group_id": world["source_group_id"],
        "split": config["split"],
        "source_verified": True,
        "source_episode_count": len(world["episodes"]),
        "eligible_episode_count": len(eligible),
        "rejected_episodes": rejected,
        "record_count": len(world["records"]),
        "context_count": len(contexts),
        "accepted_context_count": len(accepted_scopes),
        "semantic_task_count": len(tasks),
        "program_ids": sorted({t["program_id"] for t in tasks}),
        "context_capacity_counts": {
            label: sum(c["smallest_capacity_bin"] == label for c in contexts)
            for label in ("cap64k", "cap128k", "cap256k")
        },
        "legacy_exact_band_context_counts": {
            b: sum(b in c["legacy_exact_bands"] for c in contexts) for b in BANDS
        },
        "max_exact_context_tokens": max(
            (c["exact_context_tokens"] for c in contexts), default=0
        ),
        "source_bindings": world["source_bindings"],
        "input_bindings": [{"path": str(p), "sha256": digest(p)} for p in inputs],
        "files": {
            name: hashlib.sha256(text.encode()).hexdigest()
            for name, text in payloads.items()
        },
        "production_eligible": False,
        "local_training_eligible": False,
        "genuinely_new_repository": False,
        "context_scope": "complete deduplicated source records from chronological episode windows; no truncation or padding",
    }
    if validate:
        actual = {p.name for p in output.iterdir()}
        if actual != {*FILES, "BUILD_RECEIPT.json"}:
            raise ValueError("unexpected codebank output inventory")
        for name, text in payloads.items():
            p = output / name
            if p.is_symlink() or p.read_text() != text:
                raise ValueError("codebank replay mismatch: " + name)
        if json.loads((output / "BUILD_RECEIPT.json").read_text()) != receipt:
            raise ValueError("codebank receipt replay mismatch")
    else:
        output.mkdir(parents=True, exist_ok=False)
        for name, text in payloads.items():
            (output / name).write_text(text)
        (output / "BUILD_RECEIPT.json").write_text(canonical(receipt) + "\n")
    return {
        k: receipt[k]
        for k in (
            "world_id",
            "source_group_id",
            "source_episode_count",
            "eligible_episode_count",
            "semantic_task_count",
            "context_count",
            "accepted_context_count",
            "context_capacity_counts",
            "legacy_exact_band_context_counts",
            "max_exact_context_tokens",
            "production_eligible",
            "local_training_eligible",
        )
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "status": "PASS",
                **materialize(args.config, args.output, validate=args.validate),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
