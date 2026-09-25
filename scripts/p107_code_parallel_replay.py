"""Replay every P107 code pair in four processes against frozen P99 bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p99_code_content_tasks import (
    _load_bank,
    _tokenizer,
    canonical,
    compile_bank,
)

SCHEMA = "longworld.p107-code-parallel-replay.v1"


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pin(value: dict) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("P107 parallel pin requires path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P107 parallel path must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != value["sha256"]:
        raise ValueError(f"P107 parallel pin drift: {relative}")
    return path


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _verify_files(directory: Path, hashes: dict) -> None:
    for name, expected in hashes.items():
        relative = Path(name)
        if relative.name != name or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("P107 parallel receipt file name is unsafe")
        if _sha(directory / name) != expected:
            raise ValueError(f"P107 parallel receipt file changed: {name}")


def _digest(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _worker(job: tuple[dict, list[int], dict]) -> dict:
    entry, positions, limits = job
    world = _load_bank(entry)
    pairs = list(combinations(world["episodes"], 2))
    tokenizer = _tokenizer()
    outcomes = []
    for position in positions:
        first, second = pairs[position]
        rows, rejects = compile_bank(
            {**world, "episodes": [first, second]},
            tokenizer,
            max_tasks=1,
            min_span=limits["min_evidence_span_tokens"],
            max_chat_tokens=limits["max_chat_tokens"],
        )
        if len(rows) + len(rejects) != 1:
            raise ValueError("P107 worker did not emit one pair outcome")
        scope = (first["episode_id"], second["episode_id"])
        if rows:
            reader, index, audit = rows[0]
            if tuple(audit["episode_ids"]) != scope:
                raise ValueError("P107 worker accepted the wrong pair")
            outcomes.append(
                {
                    "scope": scope,
                    "status": "accepted",
                    "reader_sha256": _digest(reader),
                    "index_sha256": _digest(index),
                    "audit_sha256": _digest(audit),
                }
            )
        else:
            if tuple(rejects[0]["episode_ids"]) != scope:
                raise ValueError("P107 worker rejected the wrong pair")
            outcomes.append(
                {
                    "scope": scope,
                    "status": "rejected",
                    "reject_sha256": _digest(rejects[0]),
                }
            )
    return {"pair_count": len(positions), "outcomes": outcomes}


def _expected(native_path: Path) -> dict[tuple[str, str], dict]:
    directory = native_path.parent
    index = _rows(directory / "sample_index.jsonl")
    audits = _rows(directory / "audit.jsonl")
    readers = {
        split: _rows(directory / f"{split}.jsonl") for split in ("train", "eval")
    }
    pointers = Counter()
    expected = {}
    for item, audit in zip(index, audits, strict=True):
        split = item["split"]
        reader = readers[split][pointers[split]]
        pointers[split] += 1
        scope = tuple(audit["episode_ids"])
        if len(scope) != 2 or scope in expected:
            raise ValueError("P107 serial accepted pair repeats")
        expected[scope] = {
            "scope": scope,
            "status": "accepted",
            "reader_sha256": _digest(reader),
            "index_sha256": _digest(item),
            "audit_sha256": _digest(audit),
        }
    for split, rows in readers.items():
        if pointers[split] != len(rows):
            raise ValueError("P107 serial reader/index count differs")
    for reject in _rows(directory / "rejects.jsonl"):
        scope = tuple(reject["episode_ids"])
        if len(scope) != 2 or scope in expected:
            raise ValueError("P107 serial rejected pair repeats")
        expected[scope] = {
            "scope": scope,
            "status": "rejected",
            "reject_sha256": _digest(reject),
        }
    return expected


def _bank_snapshots() -> list[dict]:
    snapshots = []
    for parent in sorted(ROOT.glob("data/candidates/*codeforge*")):
        for path in sorted(parent.rglob("world.json")):
            world = json.loads(path.read_text())
            snapshots.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "source_group": world["source_group_id"],
                    "split": world["split"],
                    "world_id": world["world_id"],
                }
            )
    return snapshots


def build(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config" or config.get("workers") != 4:
        raise ValueError("P107 parallel replay requires four workers")
    prior_config_path = _pin(config["prior_config"])
    prior_path = _pin(config["prior_native_manifest"])
    expanded_config_path = _pin(config["expanded_config"])
    native_path = _pin(config["expanded_native_manifest"])
    curated_path = _pin(config["curated_manifest"])
    mask_path = _pin(config["curated_mask"])
    prior_config = json.loads(prior_config_path.read_text())
    prior = json.loads(prior_path.read_text())
    expanded = json.loads(expanded_config_path.read_text())
    native = json.loads(native_path.read_text())
    curated = json.loads(curated_path.read_text())
    mask = json.loads(mask_path.read_text())
    if (
        len(expanded["banks"]) != 1
        or expanded["schema_version"] != "longworld.p99-code-content.v1"
        or prior_config["schema_version"] != "longworld.p99-code-content.v1"
        or native["config_sha256"] != _sha(expanded_config_path)
        or mask["source_manifest_sha256"] != _sha(curated_path)
        or mask["audited_views"] != curated["candidate_views"]
    ):
        raise ValueError("P107 parallel source or final mask chain differs")
    _verify_files(prior_path.parent, prior["files_sha256"])
    _verify_files(native_path.parent, native["files_sha256"])
    snapshots = _bank_snapshots()
    worlds = {}
    prior_rows = []
    accepted = Counter(
        row["source_group"] for row in _rows(prior_path.parent / "sample_index.jsonl")
    )
    rejected = Counter(
        row["source_group"] for row in _rows(prior_path.parent / "rejects.jsonl")
    )
    for bank in prior_config["banks"]:
        world = _load_bank(bank)
        group = world["source_group_id"]
        if group in worlds:
            raise ValueError("P107 prior repository repeats")
        worlds[group] = world
        pairs = len(world["episodes"]) * (len(world["episodes"]) - 1) // 2
        attempted = accepted[group] + rejected[group]
        if attempted > pairs:
            raise ValueError("P107 prior attempted more pairs than source supports")
        prior_rows.append(
            {
                "source_group": group,
                "split": world["split"],
                "world_id": world["world_id"],
                "episodes": len(world["episodes"]),
                "pair_capacity": pairs,
                "prior_accepted": accepted[group],
                "prior_rejected": rejected[group],
                "prior_unexamined": pairs - attempted,
            }
        )
    duplicate_snapshots = len(snapshots) - len(
        {(row["source_group"], row["split"], row["world_id"]) for row in snapshots}
    )
    uncovered = [
        row
        for row in snapshots
        if row["source_group"] not in worlds
        or row["world_id"] != worlds[row["source_group"]]["world_id"]
        or row["split"] != worlds[row["source_group"]]["split"]
    ]
    if uncovered:
        raise ValueError("P107 frozen bank inventory has an unreviewed world")
    entry = expanded["banks"][0]
    group = entry["source_group_id"]
    world = worlds[group]
    if _load_bank(entry)["world_id"] != world["world_id"]:
        raise ValueError("P107 expanded bank is not the pinned prior world")
    pairs = list(combinations(world["episodes"], 2))
    jobs = [
        (entry, list(range(offset, len(pairs), 4)), expanded) for offset in range(4)
    ]
    with ProcessPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(_worker, jobs))
    expected = _expected(native_path)
    observed = {
        tuple(row["scope"]): row for result in results for row in result["outcomes"]
    }
    if (
        len(expected) != len(pairs)
        or len(observed) != len(pairs)
        or observed != expected
        or sum(result["pair_count"] for result in results) != len(pairs)
        or sum(row["status"] == "accepted" for row in observed.values())
        != native["views"]
    ):
        raise ValueError(
            "P107 four-worker pair replay differs from serial native bytes"
        )
    return {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "prior_native_manifest_sha256": _sha(prior_path),
        "expanded_native_manifest_sha256": _sha(native_path),
        "curated_manifest_sha256": _sha(curated_path),
        "curated_mask_sha256": _sha(mask_path),
        "code_sha256": {
            "scripts/p99_code_content_tasks.py": _sha(
                ROOT / "scripts/p99_code_content_tasks.py"
            ),
            "scripts/p107_code_parallel_replay.py": _sha(Path(__file__)),
        },
        "bank_snapshots": len(snapshots),
        "unique_source_worlds": len(worlds),
        "duplicate_snapshots": duplicate_snapshots,
        "capacity_by_repository": sorted(
            prior_rows, key=lambda row: row["source_group"]
        ),
        "source_group": group,
        "source_split": world["split"],
        "pair_capacity": len(pairs),
        "workers": 4,
        "worker_pair_counts": [result["pair_count"] for result in results],
        "parallel_accepted": sum(
            row["status"] == "accepted" for row in observed.values()
        ),
        "parallel_rejected": sum(
            row["status"] == "rejected" for row in observed.values()
        ),
        "matched_serial_pair_outcomes": len(pairs),
        "curated_new_views": curated["candidate_views"],
        "curated_mask_checked": mask["audited_views"],
        "train_ready": False,
        "strict_long_dependency_verified": False,
    }


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    result = build(config_path)
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    manifest_path = output_dir / "manifest.json"
    if verify_only:
        if {path.name for path in output_dir.iterdir()} != {"manifest.json"}:
            raise ValueError("P107 parallel output inventory drift")
        if manifest_path.read_text() != content:
            raise ValueError("P107 parallel replay receipt drift")
    else:
        if output_dir.exists():
            raise ValueError("P107 parallel output must be new")
        output_dir.mkdir(parents=True)
        manifest_path.write_text(content)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(canonical(run(args.config, args.output_dir, verify_only=args.verify_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
