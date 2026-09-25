"""Resume a P97 Wiki shard campaign through strict source gating and native jobs.

The coordinator stores only source pins, receipts and plans. P93 owns HTTP and
snapshot freezing; P97 owns global title/URL hygiene; the source-pool batch
runner owns task execution and reader artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p93_wiki_structural_intake import run as acquire
from longworld.synthesis.p93_wiki_structural_intake import verify as verify_intake
from scripts.audit_p97_wiki_intake_overlap import report as overlap_report
from scripts.gate_p97_wiki_delta import build as gate_build
from scripts.plan_p97_wiki_intake_shards import run as verify_plan
from scripts.run_source_pool_batch import run as run_batch

SCHEMA = "longworld.p99-wiki-campaign.v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("campaign paths must be workspace-relative")
    path = ROOT / relative
    if not path.resolve().is_relative_to(
        ROOT.resolve()
    ) and not path.resolve().is_relative_to((ROOT / "data").resolve()):
        raise ValueError("campaign path escapes workspace storage")
    return path


def _pin(pin: dict[str, str]) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("campaign pin needs path and sha256")
    path = _path(pin["path"])
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"campaign pin drift: {pin['path']}")
    return path


def _write(path: Path, value: Any, *, verify_only: bool = False) -> None:
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError(f"campaign replay drift: {path}")
        return
    if verify_only:
        raise ValueError(f"cannot verify missing campaign file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def preflight(config_path: Path) -> tuple[dict, dict, list[dict]]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong campaign schema")
    if any(
        type(config.get(key)) is not int or not 1 <= config[key] <= 4
        for key in ("intake_workers", "task_workers")
    ):
        raise ValueError("campaign worker bounds are 1..4")
    catalog = _pin(config["catalog"])
    manifest_path = _pin(config["plan_manifest"])
    plan_dir = manifest_path.parent
    planned = verify_plan(catalog, plan_dir, verify_only=True)
    if planned["catalog_sha256"] != config["catalog"]["sha256"]:
        raise ValueError("catalog and plan differ")
    if planned["base_pool"] != config["base_pool"]:
        raise ValueError("campaign acquisition base differs from shard base pool")
    base = json.loads(_pin(config["base_pool"]).read_text())
    prior = json.loads(_pin(config["dedup_prior_pool"]).read_text())
    router = json.loads(_pin(config["dedup_prior_router"]).read_text())
    if router.get("schema") != "longworld.p92-source-router.v1.result":
        raise ValueError("campaign dedup router schema mismatch")
    if any(
        pool.get("schema") != "longworld.source-batch-pool.v2" for pool in (base, prior)
    ):
        raise ValueError("campaign source pool schema mismatch")
    if {key: value for key, value in base.items() if key != "sources"} != {
        key: value for key, value in prior.items() if key != "sources"
    }:
        raise ValueError(
            "acquisition base and dedup prior use different source contracts"
        )
    base_sources = {source["name"]: source for source in base["sources"]}
    prior_sources = {source["name"]: source for source in prior["sources"]}
    if len(base_sources) != len(base["sources"]) or len(prior_sources) != len(
        prior["sources"]
    ):
        raise ValueError("campaign source pool repeats a source name")
    if any(prior_sources.get(name) != source for name, source in base_sources.items()):
        raise ValueError("dedup prior must contain the unchanged acquisition base")
    shards = config.get("shards", "all")
    if shards == "all":
        shards = [row["shard"] for row in planned["shards"]]
    if (
        not isinstance(shards, list)
        or not shards
        or any(type(i) is not int for i in shards)
        or len(shards) != len(set(shards))
    ):
        raise ValueError("campaign shards must be distinct integer IDs")
    by_id = {row["shard"]: row for row in planned["shards"]}
    if any(index not in by_id for index in shards):
        raise ValueError("campaign names unknown shard")
    frozen = config.get("frozen_intakes", [])
    if (
        not isinstance(frozen, list)
        or any(not isinstance(row, dict) for row in frozen)
        or len({row.get("shard") for row in frozen}) != len(frozen)
    ):
        raise ValueError("frozen intake shards repeat")
    if any(row.get("shard") not in shards for row in frozen):
        raise ValueError("frozen intake is outside campaign shards")
    for row in frozen:
        _pin(row["manifest"])
    rows = []
    for index in shards:
        row = by_id[index]
        shard_path = plan_dir / row["config_path"]
        if _sha(shard_path) != row["config_sha256"]:
            raise ValueError("shard config hash drift")
        rows.append(
            {
                "shard": index,
                "config": shard_path,
                "config_sha256": row["config_sha256"],
            }
        )
    return config, planned, rows


def _receipt(directory: Path, config_sha: str, base_pool: dict) -> dict:
    receipt = verify_intake(directory, ROOT)
    if receipt["config_sha256"] != config_sha or receipt["base_pool"] != base_pool:
        raise ValueError(f"intake receipt does not match campaign shard: {directory}")
    pool = json.loads((directory / "source_pool.json").read_text())
    prior = json.loads(_pin(base_pool).read_text())
    if {key: value for key, value in pool.items() if key != "sources"} != {
        key: value for key, value in prior.items() if key != "sources"
    }:
        raise ValueError("intake source-pool contract changed")
    if len(pool["sources"]) != receipt["source_groups"]:
        raise ValueError("intake source-group count drift")
    return receipt


def _campaign_intake(output_dir: Path, row: dict) -> Path:
    directory = output_dir / "intakes" / f"shard_{row['shard']:04d}"
    attempts = sorted(directory.glob("attempt_????")) if directory.exists() else []
    for attempt in attempts:
        if (attempt / "manifest.json").is_file():
            return attempt
    raise ValueError(f"shard {row['shard']} has no frozen intake; use --acquire")


def _acquire_pending(
    output_dir: Path, rows: list[dict], workers: int
) -> dict[int, Path]:
    acquired: dict[int, Path] = {}
    pending = []
    for row in rows:
        directory = output_dir / "intakes" / f"shard_{row['shard']:04d}"
        attempts = sorted(directory.glob("attempt_????")) if directory.exists() else []
        complete = [path for path in attempts if (path / "manifest.json").is_file()]
        if complete:
            acquired[row["shard"]] = complete[-1]
        else:
            pending.append((row, directory / f"attempt_{len(attempts):04d}"))
    if pending:
        with ProcessPoolExecutor(max_workers=min(workers, len(pending))) as pool:
            futures = {
                pool.submit(acquire, row["config"], path, ROOT): (row, path)
                for row, path in pending
            }
            failures = []
            for future in as_completed(futures):
                row, path = futures[future]
                try:
                    future.result()
                    acquired[row["shard"]] = path
                except Exception as error:  # noqa: BLE001 - retain failed attempts
                    failures.append(
                        f"shard {row['shard']}: {type(error).__name__}: {error}"
                    )
            if failures:
                raise RuntimeError(
                    "campaign acquisition failed: " + "; ".join(sorted(failures))
                )
    return acquired


def _intakes(
    config: dict, rows: list[dict], output_dir: Path, *, allow_acquire: bool
) -> list[Path]:
    frozen = {
        row["shard"]: _pin(row["manifest"]).parent
        for row in config.get("frozen_intakes", [])
    }
    missing = [row for row in rows if row["shard"] not in frozen]
    if missing and not allow_acquire:
        acquired = {row["shard"]: _campaign_intake(output_dir, row) for row in missing}
    else:
        acquired = _acquire_pending(output_dir, missing, config["intake_workers"])
    return [frozen.get(row["shard"], acquired.get(row["shard"])) for row in rows]


def _complete_batch(directory: Path) -> None:
    """Keep verify-only from executing a missing or partly finished job."""
    for path in (
        directory / "result.json",
        directory / "merged/manifest.json",
        directory / "batch/batch_manifest.json",
    ):
        if not path.is_file():
            raise ValueError(f"cannot verify incomplete native batch: {path}")
    manifest = json.loads((directory / "batch/batch_manifest.json").read_text())
    for job_id in manifest["job_receipt_sha256"]:
        if not (directory / "batch/jobs" / job_id / "receipt.json").is_file():
            raise ValueError(f"cannot verify missing native job receipt: {job_id}")


def run(
    config_path: Path,
    output_dir: Path,
    *,
    mode: str = "plan",
    acquire_missing: bool = False,
) -> dict:
    if mode not in {"plan", "gate", "run", "verify"} or (
        acquire_missing and mode != "run"
    ):
        raise ValueError("invalid campaign mode or acquisition scope")
    config, _planned, rows = preflight(config_path)
    if not output_dir.is_relative_to(ROOT):
        raise ValueError("campaign output must be workspace-relative")
    campaign_plan = {
        "schema": SCHEMA + ".plan",
        "config_sha256": _sha(config_path),
        "planner_manifest_sha256": config["plan_manifest"]["sha256"],
        "dedup_prior_pool_sha256": config["dedup_prior_pool"]["sha256"],
        "dedup_prior_router_sha256": config["dedup_prior_router"]["sha256"],
        "campaign_code_sha256": _sha(Path(__file__)),
        "shards": [
            {"shard": row["shard"], "config_sha256": row["config_sha256"]}
            for row in rows
        ],
        "train_ready": False,
    }
    _write(output_dir / "plan.json", campaign_plan, verify_only=mode == "verify")
    if mode == "plan":
        return campaign_plan
    intake_dirs = _intakes(config, rows, output_dir, allow_acquire=acquire_missing)
    if any(path is None for path in intake_dirs):
        raise ValueError("missing intake directory")
    receipts = [
        _receipt(path, row["config_sha256"], config["base_pool"])
        for row, path in zip(rows, intake_dirs)
    ]
    audit = overlap_report(
        _pin(config["dedup_prior_pool"]),
        intake_dirs,
        prior_router=_pin(config["dedup_prior_router"]),
    )
    _write(output_dir / "overlap.json", audit, verify_only=mode == "verify")
    source_by_name = {}
    prior = json.loads(_pin(config["dedup_prior_pool"]).read_text())
    for directory in intake_dirs:
        for source in json.loads((directory / "source_pool.json").read_text())[
            "sources"
        ]:
            if source["name"] in source_by_name:
                raise ValueError("intake source name repeats")
            source_by_name[source["name"]] = source
    delta = {
        **prior,
        "sources": [source_by_name[name] for name in audit["accepted_groups"]],
    }
    probe_by_name = {
        probe["source"]: probe for receipt in receipts for probe in receipt["probes"]
    }
    if any(name not in probe_by_name for name in audit["accepted_groups"]):
        raise ValueError("accepted source lacks native structure probe")
    accepted_probes = [probe_by_name[name] for name in audit["accepted_groups"]]
    _write(
        output_dir / "native_probes.json",
        {"schema": SCHEMA + ".native-probes", "accepted": accepted_probes},
        verify_only=mode == "verify",
    )
    gate_dir = output_dir / "gate"
    _write(gate_dir / "source_pool.json", delta, verify_only=mode == "verify")
    _, gate = gate_build(
        _pin(config["dedup_prior_pool"]),
        intake_dirs,
        gate_dir / "source_pool.json",
        prior_router=_pin(config["dedup_prior_router"]),
    )
    _write(gate_dir / "manifest.json", gate, verify_only=mode == "verify")
    gated = {
        "schema": SCHEMA + ".gated",
        "plan_sha256": _sha(output_dir / "plan.json"),
        "intakes": [
            {
                "shard": row["shard"],
                "path": str(path.relative_to(ROOT)),
                "manifest_sha256": _sha(path / "manifest.json"),
                "groups": receipt["source_groups"],
            }
            for row, path, receipt in zip(rows, intake_dirs, receipts)
        ],
        "overlap_sha256": _sha(output_dir / "overlap.json"),
        "gate_manifest_sha256": _sha(gate_dir / "manifest.json"),
        "native_probe_sha256": _sha(output_dir / "native_probes.json"),
        "gross": audit["gross"],
        "net_novel": audit["net_novel"],
        "source_groups_by_split": dict(
            sorted(Counter(source["split"] for source in delta["sources"]).items())
        ),
        "source_groups_by_domain": dict(
            sorted(Counter(source["domain"] for source in delta["sources"]).items())
        ),
        "source_topics": len(
            {(source["domain"], source["topic"]) for source in delta["sources"]}
        ),
        "rejected_groups": audit["overlap_groups"],
        "native_potential_jobs": sum(
            probe["potential_jobs"] for probe in accepted_probes
        ),
        "native_supported_groups_by_recipe": dict(
            sorted(
                Counter(
                    recipe
                    for probe in accepted_probes
                    for recipe in probe["supported_recipes"]
                ).items()
            )
        ),
        "native_unsupported_probe_cells": sum(
            len(probe["unsupported_cells"]) for probe in accepted_probes
        ),
        "train_ready": False,
    }
    _write(output_dir / "gate_result.json", gated, verify_only=mode == "verify")
    if mode == "gate":
        return gated
    if not delta["sources"] or not any(
        probe["potential_jobs"] for probe in accepted_probes
    ):
        result = {
            "schema": SCHEMA + ".result",
            "gate_result_sha256": _sha(output_dir / "gate_result.json"),
            "candidate_views": 0,
            "independent_tasks": 0,
            "unsupported_cells": gated["native_unsupported_probe_cells"],
            "reason": "no novel source groups"
            if not delta["sources"]
            else "no native-supported task cells",
            "train_ready": False,
        }
    else:
        batch_dir = output_dir / "batch"
        if mode == "verify":
            _complete_batch(batch_dir)
        batch = run_batch(
            gate_dir / "source_pool.json",
            batch_dir,
            workers=config["task_workers"],
            resume=batch_dir.exists(),
        )
        result = {
            "schema": SCHEMA + ".result",
            "gate_result_sha256": _sha(output_dir / "gate_result.json"),
            "batch_result_sha256": _sha(batch_dir / "result.json"),
            "candidate_views": batch["candidate_views"],
            "independent_tasks": batch["independent_tasks"],
            "unsupported_cells": batch["unsupported_cells"],
            "rejected_rows": batch["rejected_rows"],
            "train_ready": False,
        }
    if mode == "verify":
        if json.loads((output_dir / "result.json").read_text()) != result:
            raise ValueError("campaign result replay drift")
    else:
        _write(output_dir / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("plan", "gate", "run", "verify"), default="plan"
    )
    parser.add_argument(
        "--acquire",
        action="store_true",
        help="allow missing shard intakes to perform HTTP acquisition",
    )
    args = parser.parse_args()
    if args.acquire and args.mode != "run":
        parser.error("--acquire requires --mode run")
    print(
        json.dumps(
            run(
                args.config,
                _path(str(args.output_dir)),
                mode=args.mode,
                acquire_missing=args.acquire,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
