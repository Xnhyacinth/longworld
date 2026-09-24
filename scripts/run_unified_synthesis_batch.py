"""Run source-specific long-context compilers under one pinned batch plan.

Each compiler retains its own evidence and answer oracle. This scheduler
controls source selection, replay, receipts and batch-level coverage; it does
not reinterpret a finance table as a code or simulated-state world.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA = "longworld.unified-synthesis-plan.v1"
KINDS = {
    "wiki_source_pool",
    "finance_taskbank",
    "codeforge_taskbank",
    "capability_records",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _root_file(value: str) -> Path:
    member = Path(value)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"source config escapes project root: {value}")
    path = ROOT / member
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"source config is not a file: {value}")
    return resolved


def _root_output(value: str) -> Path:
    member = Path(value)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"source output escapes project root: {value}")
    path = ROOT / member
    parent = path.parent.resolve(strict=True)
    return parent / path.name


def plan(config_path: Path) -> dict[str, Any]:
    config = _json(config_path)
    if config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported unified synthesis plan")
    entries = config.get("sources")
    if not isinstance(entries, list) or not entries:
        raise ValueError("plan needs at least one source")
    names: set[str] = set()
    outputs: set[Path] = set()
    planned = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "name",
            "kind",
            "config",
            "output",
            "generation",
        }:
            raise ValueError("invalid source entry")
        name, kind = entry["name"], entry["kind"]
        cohort = entry["generation"]
        valid_cohort = isinstance(cohort, str) and (
            cohort == "verified_reuse"
            or (cohort.startswith("new_p") and cohort[5:].isdigit())
        )
        if (
            not isinstance(name, str)
            or not name.replace("_", "").isalnum()
            or name in names
            or kind not in KINDS
            or not valid_cohort
        ):
            raise ValueError("duplicate or invalid source identity")
        config_file = _root_file(entry["config"])
        output = _root_output(entry["output"])
        if output in outputs:
            raise ValueError("two source entries share an output")
        names.add(name)
        outputs.add(output)
        planned.append({**entry, "config_sha256": _sha(config_file)})
    return {
        "schema_version": SCHEMA + ".resolved",
        "config_sha256": _sha(config_path),
        "sources": planned,
    }


def _execute(entry: dict[str, Any], *, workers: int) -> dict[str, Any]:
    config = _root_file(entry["config"])
    output = _root_output(entry["output"])
    kind = entry["kind"]
    if kind == "wiki_source_pool":
        from scripts.run_source_pool_batch import run

        native_output = output
        inventory = output / "batch/inventory_inputs.json"
        if inventory.exists():
            first = _json(inventory)["inputs"][0]["index"]
            if not Path(first).is_absolute():
                if Path.cwd().resolve() != ROOT.resolve():
                    raise ValueError(
                        "relative Wiki inventory requires project-root cwd"
                    )
                native_output = Path(entry["output"])
        result = run(config, native_output, workers=workers, resume=output.exists())
        return {
            "source_kind": "real_wiki",
            "status": "verified_native_candidate",
            "rows": result["candidate_views"],
            "semantic_tasks": result["independent_tasks"],
            "source_groups": result["jobs_with_candidates"],
            "operations": result["task_operations"],
            "domains": result["domains"],
            "train_ready": result["train_ready"],
            "paths": {
                "train": str(output / "merged/train.jsonl"),
                "eval": str(output / "merged/eval.jsonl"),
                "sample_index": str(output / "merged/sample_index.jsonl"),
                "manifest": str(output / "merged/manifest.json"),
            },
            "native_receipt_sha256": _sha(output / "result.json"),
        }
    if kind == "capability_records":
        from longworld.synthesis.capability_native_adapter import run_native

        result = run_native(config, output, resume=output.exists())
        result["native_receipt_sha256"] = _sha(output / "manifest.json")
        return result
    from longworld.synthesis import finance_code_native_adapter as adapters

    if kind == "finance_taskbank":
        if output.exists() and not (output / "BATCH_RECEIPT.json").exists():
            raise ValueError(
                f"partial Finance native output cannot resume safely: {output}; "
                "inspect it and use a new output path"
            )
        result = (
            adapters.verify_finance(output, config)
            if output.exists() and (output / "BATCH_RECEIPT.json").exists()
            else adapters.run_finance(config, output, workers=workers)
        )
    else:
        if output.exists() and not (output / "BUILD_RECEIPT.json").exists():
            raise ValueError(
                f"partial CodeForge projection cannot resume safely: {output}; "
                "inspect it and use a new output path"
            )
        result = (
            adapters.verify_codeforge(output, config)
            if output.exists() and (output / "BUILD_RECEIPT.json").exists()
            else adapters.run_codeforge(config, output)
        )
    if result.get("status") != "verified_native_candidate":
        raise ValueError(f"{kind} did not produce verified native candidates")
    return result


def _verified_base_batch(base_batch: Path) -> tuple[dict[str, Any], str]:
    from longworld.synthesis.unified_candidate_merge import verify_merge

    merged = verify_merge(base_batch / "merged")
    base_sha = _sha(base_batch / "merged/manifest.json")
    manifest = _json(base_batch / "manifest.json")
    receipts = manifest.get("lane_receipt_sha256")
    if (
        manifest.get("schema_version") != "longworld.unified-synthesis-batch.v1"
        or manifest.get("merged_manifest_sha256") != base_sha
        or manifest.get("candidate_views") != merged["candidate_views"]
        or not isinstance(receipts, dict)
        or manifest.get("source_count") != len(receipts)
    ):
        raise ValueError("base batch and merged reader manifest disagree")
    for name, digest in receipts.items():
        if not isinstance(name, str) or not name.replace("_", "").isalnum():
            raise ValueError("invalid base lane identity")
        if _sha(base_batch / f"{name}.json") != digest:
            raise ValueError(f"base lane receipt changed: {name}")
    lane_rows = {name: _json(base_batch / f"{name}.json")["rows"] for name in receipts}
    if (
        lane_rows != merged["views_by_lane"]
        or sum(lane_rows.values()) != merged["candidate_views"]
    ):
        raise ValueError("base native lanes and merged reader counts disagree")
    return manifest, base_sha


def run(
    config_path: Path,
    output_dir: Path,
    *,
    workers: int = 2,
    resume: bool = False,
    base_batch: Path | None = None,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be positive")
    resolved = plan(config_path)
    if output_dir.exists():
        if not resume or _json(output_dir / "plan.json") != resolved:
            raise ValueError("existing unified output needs matching --resume")
    else:
        if resume:
            raise ValueError("cannot resume a missing unified output")
        output_dir.mkdir(parents=True)
        (output_dir / "plan.json").write_text(json.dumps(resolved, indent=2) + "\n")
    lanes: dict[str, Any] = {}
    for entry in resolved["sources"]:
        existed_before = _root_output(entry["output"]).exists()
        result = _execute(entry, workers=workers)
        if result.get("rows", 0) < 1 or result.get("train_ready") is True:
            raise ValueError(f"invalid candidate-only result for {entry['name']}")
        lanes[entry["name"]] = {
            "kind": entry["kind"],
            "declared_generation_cohort": entry["generation"],
            "adoption_mode": "verified_existing"
            if existed_before
            else "built_by_batch",
            "config_sha256": entry["config_sha256"],
            **result,
        }
        lane_path = output_dir / f"{entry['name']}.json"
        if lane_path.exists():
            if _json(lane_path) != lanes[entry["name"]]:
                raise ValueError(f"native lane receipt changed: {entry['name']}")
        else:
            lane_path.write_text(json.dumps(lanes[entry["name"]], indent=2) + "\n")
    from longworld.synthesis.unified_candidate_merge import append, merge, verify_merge

    merged_dir = output_dir / "merged"
    base_sha = None
    new_lanes = lanes
    if base_batch is not None:
        base_batch = Path(base_batch)
        base_manifest, base_sha = _verified_base_batch(base_batch)
        base_names = set(base_manifest["lane_receipt_sha256"])
        for name in base_names:
            if name not in lanes or lanes[name] != _json(base_batch / f"{name}.json"):
                raise ValueError(f"base lane changed before append: {name}")
        new_lanes = {
            name: lane for name, lane in lanes.items() if name not in base_names
        }
        if not new_lanes:
            raise ValueError("base append has no new source lanes")
    if merged_dir.exists():
        merged = verify_merge(merged_dir)
        if merged.get("base_manifest_sha256") != base_sha:
            raise ValueError("merged base batch changed")
    elif base_batch is None:
        merged = merge(output_dir, merged_dir, lanes)
    else:
        merged = append(output_dir, base_batch / "merged", merged_dir, new_lanes)
    if merged["candidate_views"] != sum(lane["rows"] for lane in lanes.values()):
        raise ValueError("unified candidate merge lost native rows")
    by_kind = Counter()
    by_cohort = Counter()
    by_adoption = Counter()
    for result in lanes.values():
        by_kind[result["source_kind"]] += result["rows"]
        by_cohort[result["declared_generation_cohort"]] += result["rows"]
        by_adoption[result["adoption_mode"]] += result["rows"]
    manifest = {
        "schema_version": "longworld.unified-synthesis-batch.v1",
        "plan_sha256": _sha(output_dir / "plan.json"),
        "source_count": len(lanes),
        "candidate_views": sum(result["rows"] for result in lanes.values()),
        "semantic_tasks_native_sum": sum(
            result["semantic_tasks"] for result in lanes.values()
        ),
        "views_by_source_kind": dict(sorted(by_kind.items())),
        "views_by_declared_cohort": dict(sorted(by_cohort.items())),
        "views_by_adoption_mode": dict(sorted(by_adoption.items())),
        "lane_receipt_sha256": {
            name: _sha(output_dir / f"{name}.json") for name in sorted(lanes)
        },
        "merged_manifest_sha256": _sha(merged_dir / "manifest.json"),
        "merged_length_bins": merged["length_bins"],
        "train_ready": False,
    }
    if base_batch is not None:
        manifest["base_merged_manifest_sha256"] = base_sha
    path = output_dir / "manifest.json"
    if path.exists():
        if _json(path) != manifest:
            raise ValueError("unified batch manifest drift")
    else:
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--base-batch", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    if args.plan_only:
        print(json.dumps(plan(args.config), ensure_ascii=False, indent=2))
    else:
        if args.output is None:
            parser.error("--output is required unless --plan-only is set")
        print(
            json.dumps(
                run(
                    args.config,
                    args.output,
                    workers=args.workers,
                    resume=args.resume,
                    base_batch=args.base_batch,
                )
            )
        )


if __name__ == "__main__":
    main()
