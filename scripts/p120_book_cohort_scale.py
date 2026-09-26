"""Plan and acquire a new Gutenberg cohort without prior-work or author leakage.

The frozen source and task stages deliberately reuse P114 and P116. This file
only owns catalog expansion and globally rate-limited, bounded mirror requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p113_book_catalog import _work_key
from scripts.p114_book_scale import _catalog, _components, _encoded, _write

PLAN_SCHEMA = "longworld.p114-book-scale-plan.v1"
DOWNLOAD_SCHEMA = "longworld.p114-book-mirror-attempts.v1"
SOURCE_SCHEMA = "longworld.p113-book-source-freeze.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _config(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    if (
        cfg.get("p120_schema") != "longworld.p120-book-cohort-request.v1"
        or cfg.get("schema") != "longworld.p113-book-catalog-request.v1"
        or cfg.get("p114_scale_schema") != "longworld.p114-book-scale-request.v1"
        or not 1 <= cfg.get("max_candidates", 0) <= 2000
        or not 1 <= cfg.get("max_attempts", 0) <= 300
        or not 1 <= cfg.get("workers", 0) <= 8
        or not 0 < cfg.get("requests_per_second", 0) <= 0.5
        or not cfg.get("mirror_base", "").startswith("https://gutenberg.pglaf.org/")
        or not cfg.get("prior_source_manifests")
        or not cfg.get("prior_attempt_manifests")
    ):
        raise ValueError("invalid bounded P120 catalog campaign")
    return cfg


def plan(
    config_path: Path, catalog_path: Path, output: Path, *, verify_only: bool = False
) -> dict:
    cfg = _config(config_path)
    rows = _catalog(catalog_path, cfg["catalog_sha256"])
    prior_records = []
    prior_pins = {}
    for path_text in cfg["prior_source_manifests"]:
        path = ROOT / path_text
        source = json.loads(path.read_text())
        if source.get("schema") != SOURCE_SCHEMA:
            raise ValueError(f"prior source schema differs: {path}")
        prior_records.extend(source["records"])
        prior_pins[path_text] = _sha(path.read_bytes())
    prior_attempt_ids = set()
    attempt_pins = {}
    for path_text in cfg["prior_attempt_manifests"]:
        path = ROOT / path_text
        attempted = json.loads(path.read_text())
        if attempted.get("schema") != DOWNLOAD_SCHEMA:
            raise ValueError(f"prior attempt schema differs: {path}")
        prior_attempt_ids.update(item["ebook_id"] for item in attempted["records"])
        attempt_pins[path_text] = _sha(path.read_bytes())
    prior_ids = {item["ebook_id"] for item in prior_records}
    prior_works = {item["catalog_work_key"] for item in prior_records}
    # Include every English text in the closure, not just the selected books:
    # an unselected co-authored work can connect two apparently separate authors.
    all_ids = {
        int(row["Text#"])
        for row in rows
        if row["Type"] == "Text" and row["Language"] == "en"
    }
    roots, keys_by_id = _components(rows, all_ids)
    if any(not keys_by_id.get(ebook_id) for ebook_id in prior_ids):
        raise ValueError("prior source lacks catalog author identity")
    prior_components = {roots[keys_by_id[ebook_id][0]] for ebook_id in prior_ids}
    groups = defaultdict(list)
    rejections = Counter()
    for row in rows:
        if row["Type"] != "Text" or row["Language"] != "en":
            rejections["non_english_or_non_text"] += 1
            continue
        ebook_id = int(row["Text#"])
        keys = keys_by_id.get(ebook_id, ())
        if not keys or not row["Title"].strip():
            rejections["missing_or_collective_author_or_title"] += 1
            continue
        work = _work_key(row)
        if ebook_id in prior_ids or work in prior_works:
            rejections["prior_source_work_or_id"] += 1
            continue
        if ebook_id in prior_attempt_ids:
            rejections["prior_mirror_attempt"] += 1
            continue
        component = roots[keys[0]]
        if component in prior_components:
            rejections["prior_author_component"] += 1
            continue
        subject = (row["Subjects"] + "; " + row["Bookshelves"]).casefold()
        topics = [
            topic
            for topic, terms in cfg["topics"].items()
            if any(term.casefold() in subject for term in terms)
        ]
        if not topics:
            rejections["outside_requested_subjects"] += 1
            continue
        topic = min(
            topics,
            key=lambda name: (
                _sha(f"{cfg['split_salt']}:{work}:{name}".encode()),
                name,
            ),
        )
        groups[work].append(
            {
                "ebook_id": ebook_id,
                "title": row["Title"],
                "author": row["Authors"],
                "topics": topics,
                "topic": topic,
                "domain": "literature",
                "work_key": work,
                "author_keys": list(keys),
                "author_component": component,
                "split": "eval"
                if int(
                    _sha(f"{cfg['split_salt']}:author-component:{component}".encode())[
                        :8
                    ],
                    16,
                )
                / 0x100000000
                < cfg["eval_fraction"]
                else "train",
            }
        )
    pools = defaultdict(list)
    for variants in groups.values():
        variants.sort(key=lambda item: item["ebook_id"])
        item = variants[0]
        item["catalog_variant_ids"] = [variant["ebook_id"] for variant in variants]
        pools[item["topic"]].append(item)
        rejections["duplicate_catalog_work_variant"] += len(variants) - 1
    for topic, pool in pools.items():
        pool.sort(
            key=lambda item: (
                _sha(f"{cfg['split_salt']}:rank:{item['work_key']}".encode()),
                item["ebook_id"],
            )
        )
    candidates = []
    while len(candidates) < cfg["max_candidates"] and any(pools.values()):
        for topic in sorted(pools):
            if pools[topic] and len(candidates) < cfg["max_candidates"]:
                candidates.append(pools[topic].pop(0))
    if len(candidates) < cfg["max_attempts"]:
        raise ValueError("fewer independent catalog candidates than attempt budget")
    prior = {
        "schema": SOURCE_SCHEMA,
        "records": prior_records,
        "origin": "p120_frozen_prior_union",
        "source_manifest_sha256": prior_pins,
    }
    prior_path = ROOT / cfg["prior_source_manifest"]
    _write(prior_path, _encoded(prior), verify_only=verify_only)
    receipt = {
        "schema": PLAN_SCHEMA,
        "p120_schema": "longworld.p120-book-cohort-plan.v1",
        "config_sha256": _sha(config_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "prior_source_manifest_sha256": _sha(prior_path.read_bytes()),
        "prior_source_pins": prior_pins,
        "prior_attempt_pins": attempt_pins,
        "catalog_rows": len(rows),
        "eligible_unique_works": len(groups),
        "planned_candidates": len(candidates),
        "planned_by_topic": dict(
            sorted(Counter(row["topic"] for row in candidates).items())
        ),
        "planned_by_split": dict(
            sorted(Counter(row["split"] for row in candidates).items())
        ),
        "excluded": dict(sorted(rejections.items())),
        "candidates": candidates,
        "train_ready": False,
    }
    _write(output / "plan.json", _encoded(receipt), verify_only=verify_only)
    return receipt


class _RateLimiter:
    def __init__(self, rps: float) -> None:
        self.interval = 1 / rps
        self.next_start = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_start - now)
            self.next_start = max(now, self.next_start) + self.interval
        if delay:
            time.sleep(delay)


def _fetch(item: dict, cfg: dict, limiter: _RateLimiter, output: Path) -> dict:
    ebook_id = item["ebook_id"]
    url = f"{cfg['mirror_base']}/{ebook_id}/pg{ebook_id}.txt"
    base = {
        "ebook_id": ebook_id,
        "url": url,
        "topic": item["topic"],
        "split": item["split"],
    }
    for filename, cached_status in (
        (f"pg{ebook_id}.txt", "downloaded"),
        (f"pg{ebook_id}.overcap.prefix", "over_byte_cap"),
    ):
        cached_path = output / "attempts" / filename
        if cached_path.exists():
            raw = cached_path.read_bytes()
            if (len(raw) <= cfg["max_bytes_per_book"]) != (
                cached_status == "downloaded"
            ):
                raise ValueError(f"cached mirror size status differs: {ebook_id}")
            return {
                **base,
                "status": cached_status,
                "raw_file": str(cached_path.relative_to(output)),
                "raw_bytes": len(raw),
                "raw_sha256": _sha(raw),
            }
    limiter.wait()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "LongWorld/1.0 bounded catalog-driven mirror research"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError(f"mirror redirect/status differs: {ebook_id}")
            raw = response.read(cfg["max_bytes_per_book"] + 1)
    except urllib.error.HTTPError as error:
        if error.code in {403, 429}:
            raise ValueError(f"mirror access stopped with HTTP {error.code}") from error
        return {**base, "status": f"http_{error.code}"}
    except (urllib.error.URLError, TimeoutError) as error:
        return {**base, "status": type(error).__name__}
    status = "over_byte_cap" if len(raw) > cfg["max_bytes_per_book"] else "downloaded"
    path = (
        output
        / "attempts"
        / (
            f"pg{ebook_id}.overcap.prefix"
            if status == "over_byte_cap"
            else f"pg{ebook_id}.txt"
        )
    )
    _write(path, raw, verify_only=False)
    return {
        **base,
        "status": status,
        "raw_file": str(path.relative_to(output)),
        "raw_bytes": len(raw),
        "raw_sha256": _sha(raw),
    }


def download(
    config_path: Path, plan_path: Path, output: Path, *, verify_only: bool = False
) -> dict:
    cfg = _config(config_path)
    planned = json.loads(plan_path.read_text())
    if (
        planned.get("schema") != PLAN_SCHEMA
        or planned.get("p120_schema") != "longworld.p120-book-cohort-plan.v1"
        or planned["config_sha256"] != _sha(config_path.read_bytes())
    ):
        raise ValueError("P120 plan/config differs")
    items = planned["candidates"][: cfg["max_attempts"]]
    if verify_only or (output / "download_manifest.json").exists():
        from scripts.p114_book_scale import download as verify_p114_download

        receipt = verify_p114_download(config_path, plan_path, output, verify_only=True)
        if receipt.get(
            "p120_schema"
        ) != "longworld.p120-book-mirror-attempts.v1" or receipt.get(
            "status_counts"
        ) != dict(sorted(Counter(row["status"] for row in receipt["records"]).items())):
            raise ValueError("P120 mirror receipt status accounting differs")
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
        "schema": DOWNLOAD_SCHEMA,
        "p120_schema": "longworld.p120-book-mirror-attempts.v1",
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
    catalog_path: Path,
    source_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    """Reject prior-source collisions before task materialization."""
    cfg = _config(config_path)
    source_path = source_dir / "manifest.json"
    source = json.loads(source_path.read_text())
    if source.get("schema") != SOURCE_SCHEMA or source.get("config_sha256") != _sha(
        config_path.read_bytes()
    ):
        raise ValueError("P120 source/config pin differs")
    prior_path = ROOT / cfg["prior_source_manifest"]
    prior = json.loads(prior_path.read_text())
    if prior.get("schema") != SOURCE_SCHEMA or source.get(
        "prior_source_manifest_sha256"
    ) != _sha(prior_path.read_bytes()):
        raise ValueError("P120 prior source union pin differs")
    rows = _catalog(catalog_path, cfg["catalog_sha256"])
    all_ids = {
        int(row["Text#"])
        for row in rows
        if row["Type"] == "Text" and row["Language"] == "en"
    }
    roots, keys_by_id = _components(rows, all_ids)
    old = prior["records"]
    new = source["records"]
    for field in ("ebook_id", "catalog_work_key", "raw_sha256", "body_sha256"):
        if len({item[field] for item in new}) != len(new):
            raise ValueError(f"duplicate P120 source {field}")
    old_components = {roots[keys_by_id[item["ebook_id"]][0]] for item in old}
    new_components = {roots[keys_by_id[item["ebook_id"]][0]] for item in new}
    overlaps = {
        "ebook_id": sorted(
            {item["ebook_id"] for item in old} & {item["ebook_id"] for item in new}
        ),
        "catalog_work_key": sorted(
            {item["catalog_work_key"] for item in old}
            & {item["catalog_work_key"] for item in new}
        ),
        "raw_sha256": sorted(
            {item["raw_sha256"] for item in old} & {item["raw_sha256"] for item in new}
        ),
        "body_sha256": sorted(
            {item["body_sha256"] for item in old}
            & {item["body_sha256"] for item in new}
        ),
        "author_component": sorted(old_components & new_components),
    }
    split_by_component = defaultdict(set)
    for item in new:
        split_by_component[roots[keys_by_id[item["ebook_id"]][0]]].add(item["split"])
    if any(overlaps.values()) or any(
        len(splits) != 1 for splits in split_by_component.values()
    ):
        raise ValueError(f"P120 source overlap or split crossing: {overlaps}")
    receipt = {
        "schema": "longworld.p120-book-source-overlap-audit.v1",
        "config_sha256": _sha(config_path.read_bytes()),
        "source_manifest_sha256": _sha(source_path.read_bytes()),
        "prior_source_manifest_sha256": _sha(prior_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "prior_source_worlds": len(old),
        "new_source_worlds": len(new),
        "new_author_components": len(new_components),
        "overlaps": overlaps,
        "train_ready": False,
    }
    _write(output, _encoded(receipt), verify_only=verify_only)
    return receipt


def audit_shards(
    source_dir: Path,
    prior_refs_dir: Path,
    unified_dirs: list[Path],
    output: Path,
    *,
    screen_dir: Path | None = None,
    verify_only: bool = False,
) -> dict:
    """Check new task identities against the current complete candidate bank."""
    if not unified_dirs:
        raise ValueError("no new unified book shard supplied")
    source_path = source_dir / "manifest.json"
    source = json.loads(source_path.read_text())
    new_groups = {item["source_group"] for item in source["records"]}
    prior_path = prior_refs_dir / "candidate_refs.jsonl"
    prior_rows = [
        json.loads(line)["candidate"] for line in prior_path.read_text().splitlines()
    ]
    seen_sample = {row["sample_id"] for row in prior_rows}
    seen_task = {row["semantic_task_id"] for row in prior_rows}
    prior_groups = {
        row["source_group"] for row in prior_rows if row["source_kind"] == "real_book"
    }
    group_overlap = sorted(new_groups & prior_groups)
    if group_overlap:
        raise ValueError(f"P120 book source group already in bank: {group_overlap}")
    shard_pins = {}
    counts = Counter()
    for shard_dir in unified_dirs:
        from longworld.synthesis.unified_candidate_merge import verify_merge

        verify_merge(shard_dir)
        manifest_path = shard_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("source_manifest_sha256") != _sha(source_path.read_bytes()):
            if screen_dir is None or manifest.get("screen_manifest_sha256") is None:
                raise ValueError(f"book shard source pin missing: {shard_dir}")
            screen_path = screen_dir / "manifest.json"
            screen = json.loads(screen_path.read_text())
            if manifest["screen_manifest_sha256"] != _sha(
                screen_path.read_bytes()
            ) or screen.get("source_manifest_sha256") != _sha(source_path.read_bytes()):
                raise ValueError(f"book shard screen/source pin differs: {shard_dir}")
        index_path = shard_dir / "sample_index.jsonl"
        rows = [json.loads(line) for line in index_path.read_text().splitlines()]
        for row in rows:
            if (
                row["source_kind"] != "real_book"
                or row["source_group"] not in new_groups
            ):
                raise ValueError("book candidate outside frozen P120 sources")
            if row["sample_id"] in seen_sample or row["semantic_task_id"] in seen_task:
                raise ValueError(
                    "P120 task/view overlaps prior bank or another new shard"
                )
            seen_sample.add(row["sample_id"])
            seen_task.add(row["semantic_task_id"])
            counts[(str(shard_dir), row["split"])] += 1
        shard_pins[str(shard_dir)] = {
            "manifest_sha256": _sha(manifest_path.read_bytes()),
            "sample_index_sha256": _sha(index_path.read_bytes()),
            "views": len(rows),
        }
    receipt = {
        "schema": "longworld.p120-book-shard-overlap-audit.v1",
        "source_manifest_sha256": _sha(source_path.read_bytes()),
        "prior_refs_sha256": _sha(prior_path.read_bytes()),
        "prior_views": len(prior_rows),
        "new_shards": shard_pins,
        "new_views_by_shard_split": {
            f"{shard}:{split}": count
            for (shard, split), count in sorted(counts.items())
        },
        "source_group_overlap": group_overlap,
        "train_ready": False,
    }
    _write(output, _encoded(receipt), verify_only=verify_only)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=("plan", "download", "audit-source", "audit-shards"),
        required=True,
    )
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--plan-dir", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--prior-refs-dir", type=Path)
    parser.add_argument("--unified-dir", type=Path, action="append")
    parser.add_argument("--screen-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.phase == "plan":
        if args.catalog is None:
            parser.error("plan needs --catalog")
        result = plan(
            args.config, args.catalog, args.output, verify_only=args.verify_only
        )
    elif args.phase == "download":
        if args.plan_dir is None:
            parser.error("download needs --plan-dir")
        result = download(
            args.config,
            args.plan_dir / "plan.json",
            args.output,
            verify_only=args.verify_only,
        )
    elif args.phase == "audit-source":
        if args.catalog is None or args.source_dir is None:
            parser.error("audit-source needs --catalog and --source-dir")
        result = audit_source(
            args.config,
            args.catalog,
            args.source_dir,
            args.output,
            verify_only=args.verify_only,
        )
    else:
        if (
            args.source_dir is None
            or args.prior_refs_dir is None
            or not args.unified_dir
        ):
            parser.error(
                "audit-shards needs --source-dir, --prior-refs-dir and --unified-dir"
            )
        result = audit_shards(
            args.source_dir,
            args.prior_refs_dir,
            args.unified_dir,
            args.output,
            screen_dir=args.screen_dir,
            verify_only=args.verify_only,
        )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"candidates", "records"}},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
