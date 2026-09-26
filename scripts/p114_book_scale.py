"""Scale frozen Project Gutenberg books with author-component split hygiene.

The source stage only admits original UTF-8 book bodies. P113 owns task truth,
independent reader auditing, unified conversion and final mask verification.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p112_book_freeze import _body
from scripts.p113_book_catalog import _norm
from scripts.p113_book_catalog import plan as base_catalog_plan
from scripts.p113_book_identity import identity_header
from scripts.p113_book_truth import audit_speeches, chapters, speeches

PLAN_SCHEMA = "longworld.p114-book-scale-plan.v1"
DOWNLOAD_SCHEMA = "longworld.p114-book-mirror-attempts.v1"
SOURCE_SCHEMA = "longworld.p113-book-source-freeze.v1"
COMPARE_SCHEMA = "longworld.p114-book-scale-overlap.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _encoded(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _write(path: Path, content: bytes, *, verify_only: bool) -> None:
    if verify_only:
        if path.read_bytes() != content:
            raise ValueError(f"frozen replay differs: {path}")
    elif path.exists() and path.read_bytes() != content:
        raise ValueError(f"frozen path exists with different bytes: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _config(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    if (
        cfg.get("p114_scale_schema") != "longworld.p114-book-scale-request.v1"
        or cfg.get("schema") != "longworld.p113-book-catalog-request.v1"
        or not 1 <= cfg.get("max_attempts", 0) <= 300
        or not 1 <= cfg.get("max_frozen_books", 0) <= 100
        or not 1 <= cfg.get("max_books_per_author_component", 0) <= 4
        or not 0 < cfg.get("requests_per_second", 0) <= 0.5
        or not cfg.get("mirror_base", "").startswith("https://gutenberg.pglaf.org/")
    ):
        raise ValueError("invalid bounded P114 book-scale config")
    return cfg


def _author_keys(author_text: str) -> tuple[str, ...]:
    keys = []
    for fragment in author_text.split(";"):
        parts = [part.strip() for part in fragment.split(",")]
        if not parts[0]:
            continue
        name = _norm(parts[0]) + ("|" + _norm(parts[1]) if len(parts) > 1 else "")
        if name in {"anonymous", "various", "unknown", "|"} or not name.strip("|"):
            return ()
        keys.append(name)
    return tuple(sorted(set(keys)))


def _catalog(catalog_path: Path, expected_sha: str) -> list[dict[str, str]]:
    if _sha(catalog_path.read_bytes()) != expected_sha:
        raise ValueError("official catalog SHA differs")
    with gzip.open(catalog_path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if set(reader.fieldnames or []) != {
            "Text#",
            "Type",
            "Issued",
            "Title",
            "Language",
            "Authors",
            "Subjects",
            "LoCC",
            "Bookshelves",
        }:
            raise ValueError("catalog columns differ")
        return list(reader)


def _components(
    rows: list[dict[str, str]], component_ebook_ids: set[int]
) -> tuple[dict[str, str], dict[int, tuple[str, ...]]]:
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    keys_by_id = {}
    for row in rows:
        if row["Type"] != "Text" or row["Language"] != "en":
            continue
        keys = _author_keys(row["Authors"])
        ebook_id = int(row["Text#"])
        keys_by_id[ebook_id] = keys
        if ebook_id not in component_ebook_ids:
            continue
        if keys:
            first = find(keys[0])
            for other in keys[1:]:
                other_root = find(other)
                first = find(first)
                if other_root != first:
                    parent[max(first, other_root)] = min(first, other_root)
    roots = {key: find(key) for key in parent}
    return roots, keys_by_id


def plan(
    config_path: Path, catalog_path: Path, output: Path, *, verify_only: bool = False
) -> dict:
    cfg = _config(config_path)
    output.mkdir(parents=True, exist_ok=True)
    base_path = output / "base_plan.json"
    base = base_catalog_plan(
        config_path, catalog_path, base_path, verify_only=verify_only
    )
    rows = _catalog(catalog_path, cfg["catalog_sha256"])
    prior_path = ROOT / cfg["prior_source_manifest"]
    prior = json.loads(prior_path.read_text())
    if prior.get("schema") != SOURCE_SCHEMA:
        raise ValueError("prior repaired book source schema differs")
    prior_ids = {item["ebook_id"] for item in prior["records"]}
    prior_works = {item["catalog_work_key"] for item in prior["records"]}
    roots, keys_by_id = _components(
        rows, prior_ids | {item["ebook_id"] for item in base["candidates"]}
    )
    prior_components = set()
    for ebook_id in prior_ids:
        keys = keys_by_id.get(ebook_id, ())
        if not keys:
            raise ValueError(f"prior source has no catalog author identity: {ebook_id}")
        prior_components.add(roots[keys[0]])
    candidates = []
    excluded = Counter()
    for item in base["candidates"]:
        ebook_id = item["ebook_id"]
        keys = keys_by_id.get(ebook_id, ())
        if not keys:
            excluded["missing_or_collective_author_identity"] += 1
            continue
        component = roots[keys[0]]
        if ebook_id in prior_ids or item["work_key"] in prior_works:
            excluded["prior_work_or_ebook"] += 1
            continue
        if component in prior_components:
            excluded["prior_author_component"] += 1
            continue
        split_value = (
            int(
                _sha((cfg["split_salt"] + ":author-component:" + component).encode())[
                    :8
                ],
                16,
            )
            / 0x100000000
        )
        candidates.append(
            {
                **item,
                "author_keys": list(keys),
                "author_component": component,
                "split": "eval" if split_value < cfg["eval_fraction"] else "train",
            }
        )
    if len(candidates) < cfg["max_attempts"]:
        raise ValueError(
            "too few distinct new-author planned sources for bounded attempt quota"
        )
    receipt = {
        "schema": PLAN_SCHEMA,
        "config_sha256": _sha(config_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "base_plan_sha256": _sha(base_path.read_bytes()),
        "prior_source_manifest_sha256": _sha(prior_path.read_bytes()),
        "catalog_rows": len(rows),
        "base_planned": len(base["candidates"]),
        "eligible_after_prior_author_exclusion": len(candidates),
        "planned_by_topic": dict(
            sorted(Counter(x["topic"] for x in candidates).items())
        ),
        "planned_by_split": dict(
            sorted(Counter(x["split"] for x in candidates).items())
        ),
        "excluded": dict(sorted(excluded.items())),
        "candidates": candidates,
        "train_ready": False,
    }
    _write(output / "plan.json", _encoded(receipt), verify_only=verify_only)
    return receipt


def download(
    config_path: Path, plan_path: Path, output: Path, *, verify_only: bool = False
) -> dict:
    cfg = _config(config_path)
    planned = json.loads(plan_path.read_text())
    if planned.get("schema") != PLAN_SCHEMA or planned["config_sha256"] != _sha(
        config_path.read_bytes()
    ):
        raise ValueError("book scale plan/config pin differs")
    if verify_only:
        receipt = json.loads((output / "download_manifest.json").read_text())
        if (
            receipt.get("schema") != DOWNLOAD_SCHEMA
            or receipt.get("config_sha256") != _sha(config_path.read_bytes())
            or receipt.get("plan_sha256") != _sha(plan_path.read_bytes())
            or receipt.get("attempted") != cfg["max_attempts"]
            or [x["ebook_id"] for x in receipt["records"]]
            != [x["ebook_id"] for x in planned["candidates"][: cfg["max_attempts"]]]
        ):
            raise ValueError("book mirror attempt receipt pin/order differs")
        for item, record in zip(
            planned["candidates"][: cfg["max_attempts"]],
            receipt["records"],
            strict=True,
        ):
            url = f"{cfg['mirror_base']}/{item['ebook_id']}/pg{item['ebook_id']}.txt"
            if (
                record["url"] != url
                or record["topic"] != item["topic"]
                or record["split"] != item["split"]
            ):
                raise ValueError("book mirror attempt source metadata differs")
            if record["status"] in {"downloaded", "over_byte_cap"}:
                raw = (output / record["raw_file"]).read_bytes()
                if len(raw) != record["raw_bytes"] or _sha(raw) != record["raw_sha256"]:
                    raise ValueError("book mirror attempt raw bytes differ")
                if (record["status"] == "downloaded") != (
                    len(raw) <= cfg["max_bytes_per_book"]
                ):
                    raise ValueError("book mirror attempt size status differs")
            elif "raw_file" in record or "raw_sha256" in record:
                raise ValueError("transport-only attempt unexpectedly has raw bytes")
        return receipt
    attempt_dir = output / "attempts"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    previous_request = None
    records = []
    for item in planned["candidates"][: cfg["max_attempts"]]:
        ebook_id = item["ebook_id"]
        url = f"{cfg['mirror_base']}/{ebook_id}/pg{ebook_id}.txt"
        path = attempt_dir / f"pg{ebook_id}.txt"
        if path.exists():
            raw = path.read_bytes()
            status = "downloaded"
        elif verify_only:
            raise ValueError(f"missing frozen attempted book: {ebook_id}")
        else:
            if previous_request is not None:
                time.sleep(
                    max(
                        0,
                        1 / cfg["requests_per_second"]
                        - (time.monotonic() - previous_request),
                    )
                )
            previous_request = time.monotonic()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "LongWorld/1.0 bounded catalog-driven mirror research"
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    if response.status != 200 or response.geturl() != url:
                        raise ValueError(f"mirror redirect/status differs: {ebook_id}")
                    raw = response.read(cfg["max_bytes_per_book"] + 1)
            except urllib.error.HTTPError as error:
                if error.code in {403, 429}:
                    raise ValueError(
                        f"mirror access stopped with HTTP {error.code}"
                    ) from error
                records.append(
                    {
                        "ebook_id": ebook_id,
                        "url": url,
                        "topic": item["topic"],
                        "split": item["split"],
                        "status": f"http_{error.code}",
                    }
                )
                continue
            except (urllib.error.URLError, TimeoutError) as error:
                records.append(
                    {
                        "ebook_id": ebook_id,
                        "url": url,
                        "topic": item["topic"],
                        "split": item["split"],
                        "status": type(error).__name__,
                    }
                )
                continue
            status = (
                "over_byte_cap"
                if len(raw) > cfg["max_bytes_per_book"]
                else "downloaded"
            )
            path = attempt_dir / (
                f"pg{ebook_id}.overcap.prefix"
                if status == "over_byte_cap"
                else f"pg{ebook_id}.txt"
            )
            path.write_bytes(raw)
        if len(raw) > cfg["max_bytes_per_book"]:
            status = "over_byte_cap"
            if path.name.endswith(".txt"):
                raise ValueError("oversize cached text path is ambiguous")
        records.append(
            {
                "ebook_id": ebook_id,
                "url": url,
                "topic": item["topic"],
                "split": item["split"],
                "status": status,
                "raw_file": str(path.relative_to(output)),
                "raw_bytes": len(raw),
                "raw_sha256": _sha(raw),
            }
        )
    receipt = {
        "schema": DOWNLOAD_SCHEMA,
        "config_sha256": _sha(config_path.read_bytes()),
        "plan_sha256": _sha(plan_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "mirror_base": cfg["mirror_base"],
        "attempted": len(records),
        "status_counts": dict(sorted(Counter(x["status"] for x in records).items())),
        "records": records,
        "train_ready": False,
    }
    _write(
        output / "download_manifest.json", _encoded(receipt), verify_only=verify_only
    )
    return receipt


def freeze(
    config_path: Path,
    plan_path: Path,
    download_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    cfg = _config(config_path)
    planned = json.loads(plan_path.read_text())
    attempted = json.loads((download_dir / "download_manifest.json").read_text())
    if (
        planned.get("schema") != PLAN_SCHEMA
        or attempted.get("schema") != DOWNLOAD_SCHEMA
        or planned["config_sha256"] != _sha(config_path.read_bytes())
        or attempted["plan_sha256"] != _sha(plan_path.read_bytes())
        or attempted["attempted"] != cfg["max_attempts"]
        or [x["ebook_id"] for x in attempted["records"]]
        != [x["ebook_id"] for x in planned["candidates"][: cfg["max_attempts"]]]
    ):
        raise ValueError("book scale source inputs disagree")
    candidates = {x["ebook_id"]: x for x in planned["candidates"]}
    viable = []
    ledger = []
    rejects = Counter()
    for attempt in attempted["records"]:
        ebook_id = attempt["ebook_id"]
        item = candidates[ebook_id]
        entry = {
            "ebook_id": ebook_id,
            "topic": item["topic"],
            "split": item["split"],
            "author_component": item["author_component"],
            "download_status": attempt["status"],
        }
        if attempt["status"] != "downloaded":
            reason = attempt["status"]
            rejects[reason] += 1
            ledger.append({**entry, "status": "rejected", "reason": reason})
            continue
        raw_path = download_dir / attempt["raw_file"]
        raw = raw_path.read_bytes()
        if _sha(raw) != attempt["raw_sha256"] or len(raw) != attempt["raw_bytes"]:
            raise ValueError(f"downloaded raw receipt differs: {ebook_id}")
        try:
            title, author = identity_header(raw, item)
            body = _body(raw, ebook_id).encode()
            chapter_list = chapters(body.decode())
            named = [speeches(chapter.text) for chapter in chapter_list]
            # This is a source-capacity heuristic only. Native task compilation
            # applies independent full-text truth, interventions and token gates.
            labels = [{speech.label for speech in group} for group in named]
            shared_pairs = sum(
                bool(labels[i] & labels[j])
                for i in range(len(labels))
                for j in range(i + 1, len(labels))
            )
            if shared_pairs < 3:
                raise ValueError("insufficient_cross_chapter_named_speech")
            independent_count = sum(
                len(audit_speeches(chapter.text)) for chapter in chapter_list
            )
        except (UnicodeError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else "non_utf8_text"
            rejects[reason] += 1
            ledger.append({**entry, "status": "rejected", "reason": reason})
            continue
        viable.append(
            {
                **item,
                "header_title": title,
                "header_author": author,
                "raw": raw,
                "body": body,
                "raw_sha256": _sha(raw),
                "body_sha256": _sha(body),
                "chapter_count": len(chapter_list),
                "shared_chapter_pairs": shared_pairs,
                "independent_attributions": independent_count,
            }
        )
        ledger.append(
            {
                **entry,
                "status": "viable",
                "raw_sha256": _sha(raw),
                "body_sha256": _sha(body),
                "chapter_count": len(chapter_list),
                "shared_chapter_pairs": shared_pairs,
            }
        )
    pools = defaultdict(list)
    for item in viable:
        pools[item["topic"]].append(item)
    for pool in pools.values():
        pool.sort(key=lambda item: (-item["shared_chapter_pairs"], item["ebook_id"]))
    selected = []
    selected_by_component = Counter()

    def choose(item: dict) -> bool:
        if (
            selected_by_component[item["author_component"]]
            >= cfg["max_books_per_author_component"]
        ):
            return False
        selected.append(item)
        selected_by_component[item["author_component"]] += 1
        return True

    eval_target = min(
        math.ceil(cfg["max_frozen_books"] * cfg["eval_fraction"]),
        sum(x["split"] == "eval" for x in viable),
    )
    for restrict_eval in (True, False):
        while len(selected) < (
            eval_target if restrict_eval else cfg["max_frozen_books"]
        ):
            progressed = False
            for topic in sorted(pools):
                pool = pools[topic]
                for index, item in enumerate(pool):
                    if restrict_eval and item["split"] != "eval":
                        continue
                    if (
                        selected_by_component[item["author_component"]]
                        >= cfg["max_books_per_author_component"]
                    ):
                        continue
                    pool.pop(index)
                    choose(item)
                    progressed = True
                    break
                if len(selected) >= (
                    eval_target if restrict_eval else cfg["max_frozen_books"]
                ):
                    break
            if not progressed:
                break
    split_by_component = defaultdict(set)
    for item in selected:
        split_by_component[item["author_component"]].add(item["split"])
    if any(len(splits) != 1 for splits in split_by_component.values()):
        raise ValueError("author component crosses train/eval")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for item in selected:
        ebook_id = item["ebook_id"]
        raw_file = f"pg{ebook_id}.txt"
        body_file = f"pg{ebook_id}.body.txt"
        _write(output / raw_file, item["raw"], verify_only=verify_only)
        _write(output / body_file, item["body"], verify_only=verify_only)
        records.append(
            {
                "ebook_id": ebook_id,
                "title": item["header_title"],
                "author": item["header_author"],
                "catalog_title": item["title"],
                "catalog_author": item["author"],
                "catalog_work_key": item["work_key"],
                "author_component": item["author_component"],
                "author_keys": item["author_keys"],
                "domain": item["domain"],
                "topic": item["topic"],
                "split": item["split"],
                "source_group": f"gutenberg-{ebook_id}",
                "landing_url": f"https://www.gutenberg.org/ebooks/{ebook_id}",
                "text_url": f"{cfg['mirror_base']}/{ebook_id}/pg{ebook_id}.txt",
                "raw_file": raw_file,
                "raw_sha256": item["raw_sha256"],
                "raw_bytes": len(item["raw"]),
                "body_file": body_file,
                "body_sha256": item["body_sha256"],
                "body_bytes": len(item["body"]),
                "chapter_count": item["chapter_count"],
                "shared_chapter_pairs": item["shared_chapter_pairs"],
                "origin_lane": "p114_catalog_mirror_scale",
            }
        )
    ledger_bytes = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in ledger
    ).encode()
    receipt = {
        "schema": SOURCE_SCHEMA,
        "p114_scale_schema": "longworld.p114-book-source-freeze.v1",
        "source_truth_code_sha256": _sha(
            (ROOT / "scripts/p113_book_truth.py").read_bytes()
        ),
        "broad_gate_code_sha256": _sha(
            (ROOT / "scripts/p114_book_broad_gate.py").read_bytes()
        ),
        "config_sha256": _sha(config_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "catalog_plan_sha256": _sha(plan_path.read_bytes()),
        "mirror_attempt_manifest_sha256": _sha(
            (download_dir / "download_manifest.json").read_bytes()
        ),
        "prior_source_manifest_sha256": _sha(
            (ROOT / cfg["prior_source_manifest"]).read_bytes()
        ),
        "policy_url": cfg["policy_url"],
        "attempted_new_sources": len(attempted["records"]),
        "viable_new_sources": len(viable),
        "selected_new_sources": len(selected),
        "source_worlds": len(records),
        "selected_by_topic": dict(
            sorted(Counter(x["topic"] for x in selected).items())
        ),
        "selected_by_split": dict(
            sorted(Counter(x["split"] for x in selected).items())
        ),
        "source_rejections": dict(sorted(rejects.items())),
        "viable_unselected_by_cap_or_author": len(viable) - len(selected),
        "attempt_ledger_sha256": _sha(ledger_bytes),
        "records": records,
        "train_ready": False,
    }
    _write(output / "attempt_ledger.jsonl", ledger_bytes, verify_only=verify_only)
    _write(output / "manifest.json", _encoded(receipt), verify_only=verify_only)
    return receipt


def compare(
    config_path: Path,
    source_dir: Path,
    unified_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    cfg = _config(config_path)
    prior_source = json.loads((ROOT / cfg["prior_source_manifest"]).read_text())
    current_source = json.loads((source_dir / "manifest.json").read_text())
    prior_unified_dir = ROOT / cfg["prior_unified_dir"]
    current_manifest = json.loads((unified_dir / "manifest.json").read_text())
    prior_manifest = json.loads((prior_unified_dir / "manifest.json").read_text())
    if (
        current_source.get("p114_scale_schema")
        != "longworld.p114-book-source-freeze.v1"
        or current_manifest["source_manifest_sha256"]
        != _sha((source_dir / "manifest.json").read_bytes())
        or prior_manifest["source_manifest_sha256"]
        != _sha((ROOT / cfg["prior_source_manifest"]).read_bytes())
    ):
        raise ValueError("book overlap input source pins differ")
    prior_index = [
        json.loads(line)
        for line in (prior_unified_dir / "sample_index.jsonl").read_text().splitlines()
        if line
    ]
    current_index = [
        json.loads(line)
        for line in (unified_dir / "sample_index.jsonl").read_text().splitlines()
        if line
    ]
    prior_groups = {x["source_group"] for x in prior_index}
    current_groups = {x["source_group"] for x in current_index}
    prior_tasks = {x["semantic_task_id"] for x in prior_index}
    current_tasks = {x["semantic_task_id"] for x in current_index}
    prior_raws = {x["raw_sha256"] for x in prior_source["records"]}
    current_raws = {x["raw_sha256"] for x in current_source["records"]}
    prior_works = {x["catalog_work_key"] for x in prior_source["records"]}
    current_works = {x["catalog_work_key"] for x in current_source["records"]}
    catalog_rows = _catalog(
        ROOT / "data/sources/p113_book_catalog_v1/pg_catalog.csv.gz",
        cfg["catalog_sha256"],
    )
    author_by_id = {
        int(row["Text#"]): _author_keys(row["Authors"])
        for row in catalog_rows
        if row["Type"] == "Text" and row["Language"] == "en"
    }
    prior_authors = {
        key
        for item in prior_source["records"]
        for key in author_by_id.get(item["ebook_id"], ())
    }
    current_authors = {
        key for item in current_source["records"] for key in item["author_keys"]
    }
    overlap = {
        "source_groups": sorted(prior_groups & current_groups),
        "semantic_task_ids": sorted(prior_tasks & current_tasks),
        "raw_sha256": sorted(prior_raws & current_raws),
        "catalog_work_keys": sorted(prior_works & current_works),
        "author_keys": sorted(prior_authors & current_authors),
    }
    if any(overlap.values()):
        raise ValueError("P114 book scale overlaps repaired P113 source or task")
    component_splits = defaultdict(set)
    for item in current_source["records"]:
        component_splits[item["author_component"]].add(item["split"])
    if any(len(value) != 1 for value in component_splits.values()):
        raise ValueError("P114 author component crosses split")
    receipt = {
        "schema": COMPARE_SCHEMA,
        "prior_source_manifest_sha256": _sha(
            (ROOT / cfg["prior_source_manifest"]).read_bytes()
        ),
        "current_source_manifest_sha256": _sha(
            (source_dir / "manifest.json").read_bytes()
        ),
        "prior_unified_manifest_sha256": _sha(
            (prior_unified_dir / "manifest.json").read_bytes()
        ),
        "current_unified_manifest_sha256": _sha(
            (unified_dir / "manifest.json").read_bytes()
        ),
        "prior_tasks": len(prior_index),
        "current_tasks": len(current_index),
        "current_source_worlds": len(current_source["records"]),
        "author_components": len(component_splits),
        "overlap": overlap,
        "train_ready": False,
    }
    _write(output, _encoded(receipt), verify_only=verify_only)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("plan", "download", "freeze", "compare"), required=True
    )
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--plan-dir", type=Path)
    parser.add_argument("--download-dir", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--unified-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.phase == "plan":
        if args.catalog is None:
            parser.error("--catalog is required for plan")
        result = plan(
            args.config, args.catalog, args.output, verify_only=args.verify_only
        )
    elif args.phase == "download":
        if args.plan_dir is None:
            parser.error("--plan-dir is required for download")
        result = download(
            args.config,
            args.plan_dir / "plan.json",
            args.output,
            verify_only=args.verify_only,
        )
    elif args.phase == "freeze":
        if args.plan_dir is None or args.download_dir is None:
            parser.error("--plan-dir and --download-dir are required for freeze")
        result = freeze(
            args.config,
            args.plan_dir / "plan.json",
            args.download_dir,
            args.output,
            verify_only=args.verify_only,
        )
    else:
        if args.source_dir is None or args.unified_dir is None:
            parser.error("--source-dir and --unified-dir are required for compare")
        result = compare(
            args.config,
            args.source_dir,
            args.unified_dir,
            args.output,
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
