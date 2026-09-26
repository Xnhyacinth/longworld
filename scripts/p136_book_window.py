"""Derive and audit a pinned second Gutenberg window without editing source IDs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p113_book_catalog import _work_key
from scripts.p113_book_truth import chapters
from scripts.p114_book_scale import _author_keys, _catalog, _encoded, _write, freeze
from scripts.p124_book_primary_acquire import WINDOW_SCHEMA, _inputs, acquire

REQUEST_SCHEMA = "longworld.p136-book-window-request.v1"
SOURCE_SCHEMA = "longworld.p113-book-source-freeze.v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(binding: dict) -> Path:
    relative = Path(binding["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe P136 source pin path")
    path = ROOT / relative
    if sha(path) != binding["sha256"]:
        raise ValueError("P136 source pin SHA changed: " + binding["path"])
    return path


def inputs(config_path: Path) -> tuple[dict, Path, Path, dict, dict]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != REQUEST_SCHEMA:
        raise ValueError("P136 config schema differs")
    parent_config_path = _pin(config["parent_config"])
    parent_plan_path = _pin(config["parent_plan"])
    source_path = _pin(config["prior_added_source"])
    attempt_path = _pin(config["prior_added_attempts"])
    parent_config = json.loads(parent_config_path.read_text())
    parent_plan = json.loads(parent_plan_path.read_text())
    start, size = config.get("start_offset"), config.get("window_size")
    if (
        type(start) is not int
        or type(size) is not int
        or start < 0
        or size != parent_config["max_attempts"]
        or start + size > len(parent_plan["candidates"])
        or parent_plan.get("config_sha256") != config["parent_config"]["sha256"]
        or parent_plan.get("catalog_sha256") != parent_config["catalog_sha256"]
        or parent_config["requests_per_second"] > 0.5
        or parent_config["workers"] > 4
    ):
        raise ValueError("P136 bounded plan window/config differs")
    source = json.loads(source_path.read_text())
    attempts = json.loads(attempt_path.read_text())
    if (
        source.get("schema") != SOURCE_SCHEMA
        or attempts.get("schema") != "longworld.p114-book-mirror-attempts.v1"
        or attempts.get("attempted") != start
        or [x["ebook_id"] for x in attempts["records"]]
        != [x["ebook_id"] for x in parent_plan["candidates"][:start]]
    ):
        raise ValueError("P136 preceding window receipt differs")
    return config, parent_config_path, parent_plan_path, parent_config, parent_plan


def prepare(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config, _, _, _, parent = inputs(config_path)
    start, size = config["start_offset"], config["window_size"]
    candidates = parent["candidates"][start : start + size]
    ids = [item["ebook_id"] for item in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("P136 window repeats ebook ID")
    receipt = {
        "schema": parent["schema"],
        "p121_schema": parent["p121_schema"],
        "p136_schema": WINDOW_SCHEMA,
        "config_sha256": parent["config_sha256"],
        "catalog_sha256": parent["catalog_sha256"],
        "parent_plan": config["parent_plan"],
        "start_offset": start,
        "window_size": size,
        "window_ebook_ids_sha256": hashlib.sha256(_encoded(ids)).hexdigest(),
        "planned_candidates": size,
        "planned_by_split": dict(
            sorted(Counter(x["split"] for x in candidates).items())
        ),
        "planned_by_topic": dict(
            sorted(Counter(x["topic"] for x in candidates).items())
        ),
        "candidates": candidates,
        "train_ready": False,
    }
    _write(output, _encoded(receipt), verify_only=verify_only)
    _inputs(ROOT / config["parent_config"]["path"], output)
    return receipt


def freeze_window(
    config_path: Path,
    window_plan: Path,
    attempts: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    config, parent_config_path, _, _, _ = inputs(config_path)
    expected = prepare(config_path, window_plan, verify_only=True)
    receipt = acquire(parent_config_path, window_plan, attempts, verify_only=True)
    if (
        receipt.get("p136_start_offset") != config["start_offset"]
        or receipt.get("p136_window_ebook_ids_sha256")
        != expected["window_ebook_ids_sha256"]
    ):
        raise ValueError("P136 attempt window receipt differs before freeze")
    result = freeze(
        parent_config_path, window_plan, attempts, output, verify_only=verify_only
    )
    if result.get("catalog_plan_sha256") != sha(window_plan) or result.get(
        "mirror_attempt_manifest_sha256"
    ) != sha(attempts / "download_manifest.json"):
        raise ValueError("P136 frozen source does not pin exact attempt window")
    return result


def audit_source(
    config_path: Path,
    window_plan: Path,
    catalog_path: Path,
    source_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    config, parent_config_path, _, parent_config, _ = inputs(config_path)
    plan = prepare(config_path, window_plan, verify_only=True)
    source_path = source_dir / "manifest.json"
    source = json.loads(source_path.read_text())
    if (
        source.get("schema") != SOURCE_SCHEMA
        or source.get("catalog_plan_sha256") != sha(window_plan)
        or source.get("config_sha256") != sha(parent_config_path)
        or source.get("attempted_new_sources") != plan["window_size"]
    ):
        raise ValueError("P136 frozen source/window pin differs")
    catalog = _catalog(catalog_path, parent_config["catalog_sha256"])
    by_id = {int(item["Text#"]): item for item in catalog}
    previous = []
    prior_dirs = {}
    for relative, digest in parent_config["prior_source_manifests"].items():
        path = _pin({"path": relative, "sha256": digest})
        manifest = json.loads(path.read_text())
        previous.extend(manifest["records"])
        for item in manifest["records"]:
            if item["ebook_id"] in prior_dirs:
                raise ValueError("prior source ebook ID repeats")
            prior_dirs[item["ebook_id"]] = path.parent
    added_path = _pin(config["prior_added_source"])
    added = json.loads(added_path.read_text())
    previous.extend(added["records"])
    for item in added["records"]:
        if item["ebook_id"] in prior_dirs:
            raise ValueError("prior source ebook ID repeats")
        prior_dirs[item["ebook_id"]] = added_path.parent
    current = source["records"]
    planned = {item["ebook_id"]: item for item in plan["candidates"]}
    old_keys = defaultdict(set)
    old_values = defaultdict(set)
    old_chapters = set()
    for item in previous:
        old_values["ebook_id"].add(item["ebook_id"])
        for field in ("catalog_work_key", "raw_sha256", "body_sha256"):
            old_values[field].add(item[field])
        body_path = prior_dirs[item["ebook_id"]] / item["body_file"]
        raw_path = prior_dirs[item["ebook_id"]] / item["raw_file"]
        if sha(body_path) != item["body_sha256"] or sha(raw_path) != item["raw_sha256"]:
            raise ValueError("prior source bytes differ")
        old_chapters.update(
            hashlib.sha256(ch.text.encode()).hexdigest()
            for ch in chapters(body_path.read_text())
        )
        for key in _author_keys(by_id[item["ebook_id"]]["Authors"]):
            old_keys[key].add(item["split"])
    new_values = defaultdict(set)
    new_chapter_owner = {}
    exact_chapter_overlap = []
    new_repeat_chapters = []
    author_splits = defaultdict(set)
    for item in current:
        planned_item = planned.get(item["ebook_id"])
        if (
            planned_item is None
            or item["split"] != planned_item["split"]
            or item["topic"] != planned_item["topic"]
            or item["author_keys"] != planned_item["author_keys"]
            or item["catalog_work_key"] != planned_item["work_key"]
        ):
            raise ValueError("P136 frozen source differs from window identity")
        for field in ("ebook_id", "catalog_work_key", "raw_sha256", "body_sha256"):
            if item[field] in new_values[field]:
                raise ValueError("duplicate P136 frozen source " + field)
            new_values[field].add(item[field])
        raw_path = source_dir / item["raw_file"]
        body_path = source_dir / item["body_file"]
        if sha(raw_path) != item["raw_sha256"] or sha(body_path) != item["body_sha256"]:
            raise ValueError("P136 frozen source bytes differ")
        for chapter in chapters(body_path.read_text()):
            digest = hashlib.sha256(chapter.text.encode()).hexdigest()
            if digest in old_chapters:
                exact_chapter_overlap.append(item["ebook_id"])
            previous_owner = new_chapter_owner.setdefault(digest, item["ebook_id"])
            if previous_owner != item["ebook_id"]:
                new_repeat_chapters.append([previous_owner, item["ebook_id"], digest])
        for key in item["author_keys"]:
            author_splits[key].add(item["split"])
    overlap = {
        field: sorted(old_values[field] & new_values[field])
        for field in ("ebook_id", "catalog_work_key", "raw_sha256", "body_sha256")
    }
    overlap["recomputed_catalog_work_key"] = sorted(
        {_work_key(by_id[item["ebook_id"]]) for item in previous}
        & {_work_key(by_id[item["ebook_id"]]) for item in current}
    )
    old_new_author_keys = sorted(set(old_keys) & set(author_splits))
    split_conflicts = sorted(
        key
        for key in set(old_keys) | set(author_splits)
        if len(old_keys[key] | author_splits[key]) > 1
    )
    overlap["exact_prior_chapter_ebook_ids"] = sorted(set(exact_chapter_overlap))
    overlap["exact_new_chapters"] = new_repeat_chapters
    if any(overlap.values()) or split_conflicts:
        raise ValueError(
            f"P136 source overlap/split crossing: {overlap}, {split_conflicts}"
        )
    receipt = {
        "schema": "longworld.p136-book-window-source-audit.v1",
        "config_sha256": sha(config_path),
        "window_plan_sha256": sha(window_plan),
        "source_manifest_sha256": sha(source_path),
        "prior_source_manifest_sha256": {
            path: digest
            for path, digest in parent_config["prior_source_manifests"].items()
        }
        | {
            config["prior_added_source"]["path"]: config["prior_added_source"]["sha256"]
        },
        "prior_worlds": len(previous),
        "new_worlds": len(current),
        "old_new_author_keys_same_split": old_new_author_keys,
        "split_conflicts": split_conflicts,
        "overlap": overlap,
        "chapter_overlap_scope": "exact P113 chapter text hashes across all prior and new frozen books",
        "train_ready": False,
    }
    _write(output, _encoded(receipt), verify_only=verify_only)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("prepare", "freeze", "audit-source"), required=True
    )
    parser.add_argument("--window-plan", type=Path)
    parser.add_argument("--attempts", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.phase == "prepare":
        result = prepare(args.config, args.output, verify_only=args.verify_only)
    elif args.phase == "freeze":
        if args.window_plan is None or args.attempts is None:
            parser.error("freeze requires window-plan and attempts")
        result = freeze_window(
            args.config,
            args.window_plan,
            args.attempts,
            args.output,
            verify_only=args.verify_only,
        )
    else:
        if args.window_plan is None or args.catalog is None or args.source_dir is None:
            parser.error("audit-source requires window-plan, catalog and source-dir")
        result = audit_source(
            args.config,
            args.window_plan,
            args.catalog,
            args.source_dir,
            args.output,
            verify_only=args.verify_only,
        )
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"candidates", "records"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
