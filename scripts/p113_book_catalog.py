"""Plan a bounded, work-deduplicated book cohort from a pinned PG catalog."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

SCHEMA = "longworld.p113-book-catalog-plan.v1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _work_key(row: dict[str, str]) -> str:
    # Catalog entries often differ only by subtitle or volume suffix. Collapsing
    # these is deliberately conservative for work-level split hygiene.
    title = re.split(
        r"[:;]|\b(?:vol(?:ume)?|book|part)\s+[ivxlcdm0-9]+\b",
        row["Title"],
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    creator = row["Authors"].split(";")[0].split(",", 1)[0]
    return _norm(creator) + "|" + _norm(title)


def plan(
    config: Path, catalog: Path, output: Path, *, verify_only: bool = False
) -> dict:
    cfg = json.loads(config.read_text())
    if cfg.get("schema") != "longworld.p113-book-catalog-request.v1":
        raise ValueError("catalog request schema differs")
    raw = catalog.read_bytes()
    if _sha(raw) != cfg["catalog_sha256"]:
        raise ValueError("catalog SHA differs from pinned request")
    if not (1 <= cfg["max_candidates"] <= 1000 and 1 <= cfg["max_frozen_books"] <= 100):
        raise ValueError("unbounded book plan")
    with gzip.open(catalog, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = set(reader.fieldnames or [])
        rows = list(reader)
    expected = {
        "Text#",
        "Type",
        "Issued",
        "Title",
        "Language",
        "Authors",
        "Subjects",
        "LoCC",
        "Bookshelves",
    }
    if columns != expected:
        raise ValueError("catalog columns differ")
    groups: dict[str, list[dict]] = defaultdict(list)
    rejected = Counter()
    excluded = set(cfg["exclude_ebook_ids"])
    catalog_rows = 0
    for row in rows:
        catalog_rows += 1
        if row["Type"] != "Text" or row["Language"] != "en":
            rejected["non_english_or_non_text"] += 1
            continue
        ebook_id = int(row["Text#"])
        if ebook_id in excluded:
            rejected["prior_source_id"] += 1
            continue
        if not row["Authors"].strip() or not row["Title"].strip():
            rejected["missing_work_identity"] += 1
            continue
        text = (row["Subjects"] + "; " + row["Bookshelves"]).casefold()
        topics = [
            name
            for name, terms in cfg["topics"].items()
            if any(term.casefold() in text for term in terms)
        ]
        if not topics:
            rejected["outside_requested_topics"] += 1
            continue
        key = _work_key(row)
        if not key or key.startswith("|") or key.endswith("|"):
            rejected["empty_normalized_work_identity"] += 1
            continue
        groups[key].append(
            {
                "ebook_id": ebook_id,
                "title": row["Title"],
                "author": row["Authors"],
                "topics": topics,
                "work_key": key,
            }
        )
    # One ebook per normalized work. Rank from a seed fixed in the config, then
    # round-robin topics to avoid a large subject class monopolizing the cap.
    pools: dict[str, list[dict]] = defaultdict(list)
    for key, variants in groups.items():
        variants.sort(key=lambda item: item["ebook_id"])
        item = variants[0]
        item["catalog_variant_ids"] = [v["ebook_id"] for v in variants]
        item["split"] = (
            "eval"
            if int(_sha((cfg["split_salt"] + ":split:" + key).encode())[:8], 16)
            / 0x100000000
            < cfg["eval_fraction"]
            else "train"
        )
        topic = min(
            item["topics"],
            key=lambda name: (_sha((cfg["split_salt"] + name + key).encode()), name),
        )
        item["topic"] = topic
        item["domain"] = "literature"
        pools[topic].append(item)
        rejected["duplicate_catalog_work_variant"] += len(variants) - 1
    for topic, entries in pools.items():
        entries.sort(
            key=lambda item: (
                _sha((cfg["split_salt"] + ":rank:" + item["work_key"]).encode()),
                item["ebook_id"],
            )
        )
    selected = []
    while len(selected) < cfg["max_candidates"] and any(pools.values()):
        for topic in sorted(pools):
            if pools[topic] and len(selected) < cfg["max_candidates"]:
                selected.append(pools[topic].pop(0))
    result = {
        "schema": SCHEMA,
        "catalog_url": cfg["catalog_url"],
        "catalog_sha256": cfg["catalog_sha256"],
        "request_sha256": _sha(config.read_bytes()),
        "catalog_rows": catalog_rows,
        "eligible_unique_works": len(groups),
        "planned_candidates": len(selected),
        "planned_by_topic": dict(
            sorted(Counter(item["topic"] for item in selected).items())
        ),
        "planned_by_split": dict(
            sorted(Counter(item["split"] for item in selected).items())
        ),
        "rejections": dict(sorted(rejected.items())),
        "candidates": selected,
        "train_ready": False,
    }
    encoded = (
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    if verify_only:
        if output.read_bytes() != encoded:
            raise ValueError("catalog plan replay differs")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and output.read_bytes() != encoded:
            raise ValueError("catalog plan exists with different bytes")
        output.write_bytes(encoded)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = plan(args.config, args.catalog, args.output, verify_only=args.verify_only)
    print(json.dumps({k: v for k, v in result.items() if k != "candidates"}, indent=2))


if __name__ == "__main__":
    main()
