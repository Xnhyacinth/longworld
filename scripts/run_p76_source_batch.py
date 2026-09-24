"""Run frozen P76 Wiki source recipes with isolated jobs and verified resume.

The coordinator hashes sources and checks pinned title/revision metadata without
tokenizing or rendering samples. Workers materialize one source at a time and
call the existing reader exporters. Every job and the batch remain research
candidates; this command never trains a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONFIG_SCHEMA = "longworld.p76-source-batch-config.v1"
LOCK_SCHEMA = "longworld.p76-source-batch-lock.v1"
RECEIPT_SCHEMA = "longworld.p76-source-job-receipt.v1"
BATCH_SCHEMA = "longworld.p76-source-batch.v1"
CODE_PATHS = (
    "scripts/run_p76_source_batch.py",
    "scripts/export_p76_wiki_tables.py",
    "scripts/export_p76_wiki_scan.py",
    "longworld/synthesis/wiki_table_tasks.py",
    "longworld/synthesis/wiki_table_scan.py",
    "longworld/synthesis/reader_view.py",
    "longworld/synthesis/wiki_adapter.py",
    "longworld/synthesis/wiki_evidence.py",
    "longworld/synthesis/wiki_world_bridge.py",
    "longworld/synthesis/shared_semantic_world.py",
    "longworld/synthesis/world_task_bank.py",
    "longworld/synthesis/length_controller.py",
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(_dump(value) + "\n")


def _code_bindings() -> dict[str, str]:
    return {name: _sha(ROOT / name) for name in CODE_PATHS}


def _job_id(job: dict[str, Any]) -> str:
    name = job.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,60}", name):
        raise ValueError("job name must be stable lowercase snake_case")
    digest = hashlib.sha256(_dump(job).encode("utf-8")).hexdigest()[:16]
    return f"{name}-{digest}"


def _prior_titles(spec: dict[str, Any]) -> dict[str, set[str]]:
    path = _path(spec["path"])
    if not path.is_file() or _sha(path) != spec["sha256"]:
        raise ValueError("prior source manifest hash mismatch")
    titles = {"train": set(), "eval": set()}
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            split = row.get("split")
            if split not in titles:
                continue
            revisions = row.get("revisions")
            if not isinstance(revisions, dict):
                raise TypeError(f"prior source row {line_no} lacks revisions")
            titles[split].update(revisions)
    return titles


def preflight(
    config_path: Path,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], dict[str, str]]:
    """Verify all immutable inputs and title components before workers start."""
    config = _json(config_path)
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError("wrong source batch config schema")
    config_sha = _sha(config_path)
    raw_jobs = config.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise ValueError("batch config needs jobs")
    prior = _prior_titles(config["prior_source_manifest"])
    jobs: list[dict[str, Any]] = []
    names: set[str] = set()
    identities: set[tuple[str, str, str | None]] = set()
    groups: dict[str, tuple[str, str, dict[str, int]]] = {}
    verified_sources: set[str] = set()
    for raw in raw_jobs:
        if not isinstance(raw, dict):
            raise TypeError("job must be an object")
        job = dict(raw)
        job_id = _job_id(job)
        name = job["name"]
        if name in names:
            raise ValueError(f"duplicate job name: {name}")
        names.add(name)
        recipe = job.get("recipe")
        expected_policy = {
            "wiki_table_pair": "paired_32k_64k_or_native",
            "wiki_table_scan": "native_whole_pages",
        }.get(recipe)
        if expected_policy is None or job.get("length_policy") != expected_policy:
            raise ValueError(f"unsupported recipe or length policy: {name}")
        if job.get("split") not in {"train", "eval"}:
            raise ValueError(f"invalid split: {name}")
        if not all(
            isinstance(job.get(key), str) and job[key].strip()
            for key in ("domain", "topic")
        ):
            raise ValueError(f"missing domain/topic: {name}")
        if not isinstance(job.get("max_tasks"), int) or not 1 <= job["max_tasks"] <= 32:
            raise ValueError(f"invalid max_tasks: {name}")
        if recipe == "wiki_table_scan" and not job.get("table_title"):
            raise ValueError(f"scan job lacks table_title: {name}")
        if recipe == "wiki_table_pair" and "table_title" in job:
            raise ValueError(f"pair job has unexpected table_title: {name}")
        source = job.get("snapshot")
        if not isinstance(source, dict):
            raise TypeError(f"snapshot must be an object: {name}")
        path = _path(source["path"])
        if not path.is_file() or _sha(path) != source["sha256"]:
            raise ValueError(f"snapshot hash mismatch: {name}")
        revisions = source.get("revisions")
        if (
            not isinstance(revisions, dict)
            or not revisions
            or any(
                not isinstance(title, str) or not isinstance(revid, int)
                for title, revid in revisions.items()
            )
        ):
            raise ValueError(f"snapshot title/revision map invalid: {name}")
        if recipe == "wiki_table_scan" and job["table_title"] not in revisions:
            raise ValueError(f"named table is not in source title set: {name}")
        if source["sha256"] not in verified_sources:
            _verify_source_metadata(job)
            verified_sources.add(source["sha256"])
        identity = (source["sha256"], recipe, job.get("table_title"))
        if identity in identities:
            raise ValueError(f"duplicate source/recipe job: {name}")
        identities.add(identity)
        group_id = source["snapshot_id"]
        binding = (source["sha256"], job["split"], revisions)
        if group_id in groups and groups[group_id] != binding:
            raise ValueError(f"source group conflict: {group_id}")
        groups[group_id] = binding
        titles = set(revisions)
        opposite = "eval" if job["split"] == "train" else "train"
        if titles & prior[opposite]:
            raise ValueError(f"opposite prior split title overlap: {name}")
        job["job_id"] = job_id
        jobs.append(job)
    # A shared title connects source groups. All jobs in one connected
    # component must use the same split, even when snapshot IDs differ.
    parents = list(range(len(jobs)))

    def find(index: int) -> int:
        while parents[index] != index:
            index = parents[index]
        return index

    for left in range(len(jobs)):
        for right in range(left + 1, len(jobs)):
            overlap = set(jobs[left]["snapshot"]["revisions"]) & set(
                jobs[right]["snapshot"]["revisions"]
            )
            if overlap:
                a, b = find(left), find(right)
                parents[b] = a
    components: dict[int, set[str]] = {}
    for index, job in enumerate(jobs):
        components.setdefault(find(index), set()).add(job["split"])
    if any(len(splits) != 1 for splits in components.values()):
        raise ValueError("connected source titles cross train/eval splits")
    return (
        config,
        config_sha,
        sorted(jobs, key=lambda job: job["job_id"]),
        _code_bindings(),
    )


def _verify_source_metadata(job: dict[str, Any]) -> None:
    source = job["snapshot"]
    path = _path(source["path"])
    if _sha(path) != source["sha256"]:
        raise ValueError("worker snapshot hash changed")
    snapshot = _json(path)
    if snapshot.get("snapshot_id") != source["snapshot_id"]:
        raise ValueError("worker snapshot ID changed")
    actual = snapshot.get("source", {}).get("revisions")
    titles = {doc["title"] for doc in snapshot.get("documents", [])}
    if actual != source["revisions"] or titles != set(source["revisions"]):
        raise ValueError("worker snapshot titles/revisions changed")
    for doc in snapshot["documents"]:
        revision_url = doc.get("revision_url", "")
        if not revision_url.endswith(f"oldid={actual[doc['title']]}"):
            raise ValueError("worker page revision URL disagrees with source")


def _verify_export(
    directory: Path, job: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    manifest = _json(directory / "manifest.json")
    if (
        manifest.get("source_group") != job["snapshot"]["snapshot_id"]
        or manifest.get("split") != job["split"]
        or manifest.get("domain") != job["domain"]
        or manifest.get("topic") != job["topic"]
        or manifest.get("train_ready") is not False
        or manifest.get("candidate_rows", 0) <= 0
        or manifest.get("independent_tasks", 0) <= 0
        or manifest.get("rejected_rows") != 0
    ):
        raise ValueError(f"export admission failed: {job['job_id']}")
    if (
        job["recipe"] == "wiki_table_scan"
        and manifest.get("table_title") != job["table_title"]
    ):
        raise ValueError("scan export table title drift")
    for name, expected in manifest["files_sha256"].items():
        if _sha(directory / name) != expected:
            raise ValueError(f"export file hash drift: {name}")
    files = {
        path.name: _sha(path)
        for path in directory.iterdir()
        if path.is_file() and path.name != "receipt.json"
    }
    if set(files) != {*manifest["files_sha256"], "manifest.json"}:
        raise ValueError("export has unexpected or missing files")
    return manifest, files


def _worker(
    job: dict[str, Any], directory: Path, config_sha: str, code: dict[str, str]
) -> dict[str, Any]:
    if _code_bindings() != code:
        raise ValueError("worker code changed before execution")
    _verify_source_metadata(job)
    snapshot = _path(job["snapshot"]["path"])
    if job["recipe"] == "wiki_table_pair":
        from scripts.export_p76_wiki_tables import export

        export(
            snapshot,
            directory,
            max_tasks=job["max_tasks"],
            split=job["split"],
            domain=job["domain"],
            topic=job["topic"],
        )
    else:
        from scripts.export_p76_wiki_scan import export

        export(
            snapshot,
            directory,
            table_title=job["table_title"],
            domain=job["domain"],
            topic=job["topic"],
            split=job["split"],
            max_tasks=job["max_tasks"],
        )
    manifest, files = _verify_export(directory, job)
    if _code_bindings() != code or _sha(snapshot) != job["snapshot"]["sha256"]:
        raise ValueError("worker inputs changed during export")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "job_id": job["job_id"],
        "job": job,
        "config_sha256": config_sha,
        "code_sha256": code,
        "snapshot_sha256": job["snapshot"]["sha256"],
        "files_sha256": files,
        "candidate_rows": manifest["candidate_rows"],
        "independent_tasks": manifest["independent_tasks"],
        "train_ready": False,
    }
    _write_new(directory / "receipt.json", receipt)
    return receipt


def _existing_receipt(
    directory: Path, job: dict[str, Any], config_sha: str, code: dict[str, str]
) -> dict[str, Any]:
    receipt_path = directory / "receipt.json"
    if not receipt_path.is_file():
        raise ValueError(f"partial job directory lacks receipt: {directory}")
    receipt = _json(receipt_path)
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("job_id") != job["job_id"]
        or receipt.get("job") != job
        or receipt.get("config_sha256") != config_sha
        or receipt.get("code_sha256") != code
        or receipt.get("snapshot_sha256") != job["snapshot"]["sha256"]
        or receipt.get("train_ready") is not False
    ):
        raise ValueError(f"job receipt binding mismatch: {job['job_id']}")
    manifest, actual = _verify_export(directory, job)
    if receipt.get("files_sha256") != actual or (
        receipt.get("candidate_rows") != manifest["candidate_rows"]
        or receipt.get("independent_tasks") != manifest["independent_tasks"]
    ):
        raise ValueError(f"job receipt file or count mismatch: {job['job_id']}")
    if {path.name for path in directory.iterdir()} != {*actual, "receipt.json"}:
        raise ValueError(f"unexpected job file: {job['job_id']}")
    return receipt


def _inventory(jobs: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "product": f"p76_wiki_{job['name']}",
            "index": str(output_dir / "jobs" / job["job_id"] / "sample_index.jsonl"),
            "sha256": _sha(output_dir / "jobs" / job["job_id"] / "sample_index.jsonl"),
            "source_group": job["snapshot"]["snapshot_id"],
            "split": job["split"],
        }
        for job in jobs
    ]


def run(
    config_path: Path, output_dir: Path, *, workers: int = 2, resume: bool = False
) -> dict[str, Any]:
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    config, config_sha, jobs, code = preflight(config_path)
    lock = {
        "schema": LOCK_SCHEMA,
        "config_sha256": config_sha,
        "code_sha256": code,
        "prior_source_manifest_sha256": config["prior_source_manifest"]["sha256"],
        "job_ids": [job["job_id"] for job in jobs],
        "train_ready": False,
    }
    if output_dir.exists():
        if not resume:
            raise ValueError(f"batch output already exists: {output_dir}")
        if (output_dir / "errors.json").exists():
            raise ValueError("batch has prior job errors; use a new output directory")
        if _json(output_dir / "batch_lock.json") != lock:
            raise ValueError("batch resume lock mismatch")
    else:
        if resume:
            raise ValueError("cannot resume a missing batch")
        output_dir.mkdir(parents=True)
        (output_dir / "jobs").mkdir()
        _write_new(output_dir / "batch_lock.json", lock)
    receipts: dict[str, dict[str, Any]] = {}
    pending = []
    for job in jobs:
        directory = output_dir / "jobs" / job["job_id"]
        if directory.exists():
            receipts[job["job_id"]] = _existing_receipt(
                directory, job, config_sha, code
            )
        else:
            pending.append(job)
    if not pending and (output_dir / "batch_manifest.json").is_file():
        manifest = _json(output_dir / "batch_manifest.json")
        if (
            manifest.get("schema") != BATCH_SCHEMA
            or manifest.get("config_sha256") != config_sha
            or manifest.get("job_receipt_sha256")
            != {
                job["job_id"]: _sha(
                    output_dir / "jobs" / job["job_id"] / "receipt.json"
                )
                for job in jobs
            }
            or _json(output_dir / "inventory_inputs.json")
            != {"inputs": _inventory(jobs, output_dir)}
        ):
            raise ValueError("batch final manifest or inventory drift on resume")
        return manifest
    if (output_dir / "batch_manifest.json").exists():
        raise ValueError("finished batch manifest exists with missing jobs")
    errors = []
    if pending:
        with ProcessPoolExecutor(max_workers=min(workers, len(pending))) as pool:
            futures = {
                pool.submit(
                    _worker, job, output_dir / "jobs" / job["job_id"], config_sha, code
                ): job
                for job in pending
            }
            for future in as_completed(futures):
                job = futures[future]
                try:
                    receipts[job["job_id"]] = future.result()
                except Exception as error:  # noqa: BLE001 - record then fail the batch
                    errors.append(
                        {
                            "job_id": job["job_id"],
                            "error_type": type(error).__name__,
                            "reason": str(error),
                        }
                    )
    errors.sort(key=lambda row: row["job_id"])
    if errors:
        _write_new(output_dir / "errors.json", {"errors": errors})
        raise RuntimeError(
            f"source batch has {len(errors)} failed jobs; see errors.json"
        )
    if _code_bindings() != code or _sha(config_path) != config_sha:
        raise ValueError("code or config changed during batch")
    inputs = _inventory(jobs, output_dir)
    _write_new(output_dir / "inventory_inputs.json", {"inputs": inputs})
    summary = {
        "schema": BATCH_SCHEMA,
        "config_sha256": config_sha,
        "train_ready": False,
        "training_release_eligible": False,
        "jobs": len(jobs),
        "source_groups": len({job["snapshot"]["snapshot_id"] for job in jobs}),
        "splits": dict(sorted(Counter(job["split"] for job in jobs).items())),
        "recipes": dict(sorted(Counter(job["recipe"] for job in jobs).items())),
        "candidate_rows": sum(
            receipt["candidate_rows"] for receipt in receipts.values()
        ),
        "independent_tasks": sum(
            receipt["independent_tasks"] for receipt in receipts.values()
        ),
        "job_receipt_sha256": {
            job["job_id"]: _sha(output_dir / "jobs" / job["job_id"] / "receipt.json")
            for job in jobs
        },
    }
    _write_new(output_dir / "batch_manifest.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/p76_source_batch_v1.json"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(
        _dump(
            run(args.config, args.output_dir, workers=args.workers, resume=args.resume)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
