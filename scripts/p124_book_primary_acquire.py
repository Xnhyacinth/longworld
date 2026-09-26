"""Acquire P121 primary-author books and audit frozen source independence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p113_book_catalog import _work_key
from scripts.p113_book_truth import chapters
from scripts.p114_book_scale import _author_keys, _catalog, _encoded, _write
from scripts.p114_book_scale import download as verify_download
from scripts.p120_book_cohort_scale import _fetch, _RateLimiter
from scripts.p121_book_primary_author import _config

PLAN_SCHEMA = "longworld.p114-book-scale-plan.v1"
SOURCE_SCHEMA = "longworld.p113-book-source-freeze.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _inputs(config_path: Path, plan_path: Path) -> tuple[dict, dict]:
    cfg = _config(config_path)
    plan = json.loads(plan_path.read_text())
    if (
        plan.get("schema") != PLAN_SCHEMA
        or plan.get("p121_schema") != "longworld.p121-book-primary-author-plan.v2"
        or plan.get("config_sha256") != _sha(config_path.read_bytes())
        or plan.get("catalog_sha256") != cfg["catalog_sha256"]
        or len(plan.get("candidates", [])) < cfg["max_attempts"]
    ):
        raise ValueError("P121 primary-author plan/config pin differs")
    return cfg, plan


def acquire(
    config_path: Path, plan_path: Path, output: Path, *, verify_only: bool = False
) -> dict:
    cfg, plan = _inputs(config_path, plan_path)
    items = plan["candidates"][: cfg["max_attempts"]]
    if verify_only or (output / "download_manifest.json").exists():
        receipt = verify_download(config_path, plan_path, output, verify_only=True)
        if receipt.get(
            "p124_schema"
        ) != "longworld.p124-book-primary-mirror-attempts.v1" or receipt.get(
            "status_counts"
        ) != dict(sorted(Counter(row["status"] for row in receipt["records"]).items())):
            raise ValueError("P124 mirror receipt accounting differs")
        return receipt
    output.mkdir(parents=True, exist_ok=True)
    limiter = _RateLimiter(cfg["requests_per_second"])
    records: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
        pending = {}
        next_item = iter(items)

        def submit_one() -> bool:
            try:
                item = next(next_item)
            except StopIteration:
                return False
            future = pool.submit(_fetch, item, cfg, limiter, output)
            pending[future] = item["ebook_id"]
            return True

        for _ in range(cfg["workers"]):
            submit_one()
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                ebook_id = pending.pop(future)
                records[ebook_id] = future.result()
                submit_one()
    ordered = [records[item["ebook_id"]] for item in items]
    receipt = {
        "schema": "longworld.p114-book-mirror-attempts.v1",
        "p124_schema": "longworld.p124-book-primary-mirror-attempts.v1",
        "config_sha256": _sha(config_path.read_bytes()),
        "plan_sha256": _sha(plan_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "mirror_base": cfg["mirror_base"],
        "attempted": len(ordered),
        "status_counts": dict(
            sorted(Counter(row["status"] for row in ordered).items())
        ),
        "records": ordered,
        "train_ready": False,
    }
    _write(output / "download_manifest.json", _encoded(receipt), verify_only=False)
    return receipt


def audit_source(
    config_path: Path,
    plan_path: Path,
    catalog_path: Path,
    source_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    cfg, plan = _inputs(config_path, plan_path)
    source_path = source_dir / "manifest.json"
    source = json.loads(source_path.read_text())
    prior_path = ROOT / cfg["prior_source_manifest"]
    prior = json.loads(prior_path.read_text())
    if (
        source.get("schema") != SOURCE_SCHEMA
        or source.get("config_sha256") != _sha(config_path.read_bytes())
        or source.get("catalog_plan_sha256") != _sha(plan_path.read_bytes())
        or source.get("prior_source_manifest_sha256") != _sha(prior_path.read_bytes())
        or prior.get("schema") != SOURCE_SCHEMA
    ):
        raise ValueError("P124 source/plan/prior pins differ")
    catalog = _catalog(catalog_path, cfg["catalog_sha256"])
    by_id = {int(row["Text#"]): row for row in catalog}
    previous, current = prior["records"], source["records"]
    planned = {item["ebook_id"]: item for item in plan["candidates"]}
    overlap: dict[str, list] = {}
    for field in ("ebook_id", "catalog_work_key", "raw_sha256", "body_sha256"):
        old_values = {item[field] for item in previous}
        new_values = [item[field] for item in current]
        overlap[field] = sorted(old_values & set(new_values))
        if len(new_values) != len(set(new_values)):
            raise ValueError(f"duplicate new source {field}")
    old_keys = {
        key
        for item in previous
        for key in _author_keys(by_id[item["ebook_id"]]["Authors"])
    }
    new_keys = {key for item in current for key in item["author_keys"]}
    overlap["author_key"] = sorted(old_keys & new_keys)
    prior_catalog_works = {_work_key(by_id[item["ebook_id"]]) for item in previous}
    new_catalog_works = {_work_key(by_id[item["ebook_id"]]) for item in current}
    overlap["recomputed_catalog_work_key"] = sorted(
        prior_catalog_works & new_catalog_works
    )
    split_by_key: dict[str, set[str]] = defaultdict(set)
    for item in current:
        planned_item = planned.get(item["ebook_id"])
        if (
            planned_item is None
            or any(
                planned_item[field] != item[field]
                for field in ("split", "topic", "author_keys")
            )
            or planned_item["work_key"] != item["catalog_work_key"]
        ):
            raise ValueError(
                "frozen source differs from planned primary-author identity"
            )
        for key in item["author_keys"]:
            split_by_key[key].add(item["split"])
    prior_book_dirs = {}
    original_prior_records = []
    for path, expected_sha in cfg["prior_source_manifests"].items():
        manifest_path = ROOT / path
        if _sha(manifest_path.read_bytes()) != expected_sha:
            raise ValueError("P124 pinned prior source manifest changed")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schema") != SOURCE_SCHEMA:
            raise ValueError("P124 prior source schema changed")
        original_prior_records.extend(manifest["records"])
        for item in manifest["records"]:
            if item["ebook_id"] in prior_book_dirs:
                raise ValueError("prior source ebook ID repeats")
            prior_book_dirs[item["ebook_id"]] = manifest_path.parent
    if original_prior_records != previous:
        raise ValueError("P124 prior source union differs from original manifests")
    chapter_hashes = set()
    for item in previous:
        body_path = prior_book_dirs[item["ebook_id"]] / item["body_file"]
        if _sha(body_path.read_bytes()) != item["body_sha256"]:
            raise ValueError("prior book body SHA differs")
        chapter_hashes.update(
            _sha(ch.text.encode()) for ch in chapters(body_path.read_text())
        )
    duplicate_chapters = []
    new_chapter_owner: dict[str, tuple[int, str]] = {}
    repeated_new_chapters = []
    for item in current:
        body = source_dir / item["body_file"]
        if _sha(body.read_bytes()) != item["body_sha256"]:
            raise ValueError("new book body SHA differs")
        for chapter in chapters(body.read_text()):
            chapter_sha = _sha(chapter.text.encode())
            if chapter_sha in chapter_hashes:
                duplicate_chapters.append(item["ebook_id"])
            owner = new_chapter_owner.setdefault(
                chapter_sha, (item["ebook_id"], item["split"])
            )
            if owner[0] != item["ebook_id"]:
                repeated_new_chapters.append(
                    {
                        "first_ebook_id": owner[0],
                        "first_split": owner[1],
                        "second_ebook_id": item["ebook_id"],
                        "second_split": item["split"],
                        "chapter_sha256": chapter_sha,
                    }
                )
    overlap["exact_prior_chapter"] = sorted(set(duplicate_chapters))
    overlap["exact_new_chapter"] = repeated_new_chapters
    split_crossings = sorted(
        key for key, splits in split_by_key.items() if len(splits) > 1
    )
    if any(overlap.values()) or split_crossings:
        raise ValueError(
            f"P124 source overlap/split crossing: {overlap}, {split_crossings}"
        )
    receipt = {
        "schema": "longworld.p124-book-primary-source-audit.v1",
        "plan_sha256": _sha(plan_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "prior_manifest_sha256": _sha(prior_path.read_bytes()),
        "source_manifest_sha256": _sha(source_path.read_bytes()),
        "prior_source_worlds": len(previous),
        "new_source_worlds": len(current),
        "new_author_keys": len(new_keys),
        "overlap": overlap,
        "author_key_split_crossings": split_crossings,
        "chapter_overlap_scope": "exact chapter text hashes across prior and new P113-parsed books only",
        "train_ready": False,
    }
    _write(output, _encoded(receipt), verify_only=verify_only)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--phase", choices=("download", "audit-source"), required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.phase == "download":
        result = acquire(
            args.config, args.plan, args.output, verify_only=args.verify_only
        )
    else:
        if args.catalog is None or args.source_dir is None:
            parser.error("audit-source requires --catalog and --source-dir")
        result = audit_source(
            args.config,
            args.plan,
            args.catalog,
            args.source_dir,
            args.output,
            verify_only=args.verify_only,
        )
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, indent=2))


if __name__ == "__main__":
    main()
