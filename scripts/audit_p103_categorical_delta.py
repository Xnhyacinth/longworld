"""Audit categorical scan capacity and novelty across frozen P97/P95 pools."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p103-categorical-delta.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _url_key(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path:
        raise ValueError("noncanonical page URL")
    return parsed.netloc.casefold() + unquote(parsed.path).rstrip("/").casefold()


def _inventory(pool_path: Path) -> tuple[dict, dict, set[tuple[str, str]]]:
    pool = json.loads(pool_path.read_text())
    if pool.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("source pool schema mismatch")
    names, ids, pages = {}, {}, set()
    for source in pool["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        if source["name"] in names or snapshot["snapshot_id"] in ids:
            raise ValueError("source name or snapshot ID repeats")
        names[source["name"]] = source
        ids[snapshot["snapshot_id"]] = source["name"]
        for doc in snapshot["documents"]:
            key = (doc["title"].casefold(), _url_key(doc["page_url"]))
            if key in pages:
                raise ValueError("page repeats within source pool")
            pages.add(key)
    return names, ids, pages


def _check_disjoint_pages(
    left: set[tuple[str, str]], right: set[tuple[str, str]]
) -> None:
    titles = {title for title, _ in left}
    urls = {url for _, url in left}
    if any(title in titles or url in urls for title, url in right):
        raise ValueError("P95 categorical pool repeats P97 page title or URL")


def _task_ids(rows: list[dict]) -> set[str]:
    ids = [row["task_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("categorical task IDs repeat within native batch")
    return set(ids)


def _native(native_dir: Path, pool_path: Path) -> tuple[dict, list[dict], list[dict]]:
    manifest = json.loads((native_dir / "manifest.json").read_text())
    if manifest.get(
        "schema"
    ) != "longworld.p100-wiki-categorical-scan.v1.result" or manifest["source_pool"][
        "sha256"
    ] != _sha(pool_path):
        raise ValueError("native categorical source pin differs")
    for name, digest in manifest["files_sha256"].items():
        if _sha(native_dir / name) != digest:
            raise ValueError(f"native file hash drift: {name}")
    index = _lines(native_dir / "sample_index.jsonl")
    pages = _lines(native_dir / "page_audit.jsonl")
    if (
        len(index) != manifest["candidate_views"]
        or len(pages) != manifest["pages_screened"]
    ):
        raise ValueError("native categorical inventory differs")
    _task_ids(index)
    return manifest, index, pages


def _support_rows(
    label: str,
    sources: dict,
    ids: dict,
    indices: list[dict],
    page_rows: list[dict],
) -> list[dict]:
    counts = defaultdict(Counter)
    for page in page_rows:
        name = page["source_group"]
        if name not in sources:
            raise ValueError("page audit references unknown source")
        counts[name]["pages"] += 1
        counts[name]["eligible_tables"] += page["eligible_tables"]
        counts[name].update(row["reason"] for row in page["rejected_tables"])
    for row in indices:
        name = ids.get(row["world_id"])
        if name is None:
            raise ValueError("candidate references unknown snapshot world")
        counts[name]["accepted_tasks"] += 1
    return [
        {
            "pool": label,
            "source": name,
            "split": source["split"],
            "domain": source["domain"],
            "topic": source["topic"],
            "pages": counts[name]["pages"],
            "eligible_tables": counts[name]["eligible_tables"],
            "accepted_tasks": counts[name]["accepted_tasks"],
            "row_width_mismatch": counts[name]["row_width_mismatch"],
            "other_table_rejections": sum(
                value
                for reason, value in counts[name].items()
                if reason
                not in {
                    "pages",
                    "eligible_tables",
                    "accepted_tasks",
                    "row_width_mismatch",
                }
            ),
        }
        for name, source in sorted(sources.items())
    ]


def build(
    p97_pool: Path, p100_native: Path, p95_pool: Path, p103_native: Path
) -> tuple[dict, str]:
    p97_sources, p97_ids, p97_pages = _inventory(p97_pool)
    p95_sources, p95_ids, p95_pages = _inventory(p95_pool)
    _check_disjoint_pages(p97_pages, p95_pages)
    _, old_rows, old_pages = _native(p100_native, p97_pool)
    current, new_rows, new_pages = _native(p103_native, p95_pool)
    if _task_ids(old_rows) & _task_ids(new_rows):
        raise ValueError("P103 categorical task repeats P100 semantic task")
    support = _support_rows("p97", p97_sources, p97_ids, old_rows, old_pages)
    support += _support_rows("p95", p95_sources, p95_ids, new_rows, new_pages)
    if sum(row["accepted_tasks"] for row in support if row["pool"] == "p95") != len(
        new_rows
    ):
        raise ValueError("P103 support matrix loses candidate tasks")
    manifest = {
        "schema": SCHEMA + ".result",
        "p97_source_pool_sha256": _sha(p97_pool),
        "p95_source_pool_sha256": _sha(p95_pool),
        "p100_native_manifest_sha256": _sha(p100_native / "manifest.json"),
        "p103_native_manifest_sha256": _sha(p103_native / "manifest.json"),
        "gross": {
            "source_groups": current["source_groups_screened"],
            "pages": current["pages_screened"],
            "eligible_tables": current["tables_structurally_eligible"],
            "tables_with_options": current["tables_with_category_options"],
            "candidate_views": current["candidate_views"],
        },
        "net_new_tasks": len(new_rows),
        "baseline_p100_tasks": len(old_rows),
        "page_title_or_url_overlap": 0,
        "semantic_task_overlap": 0,
        "productive_groups": len(
            {
                row["source"]
                for row in support
                if row["pool"] == "p95" and row["accepted_tasks"]
            }
        ),
        "new_task_domains": current["domains"],
        "new_task_topics": current["topics"],
        "new_task_splits": current["split_views"],
        "new_task_length_bins": current["length_bins"],
        "row_width_mismatch": {
            label: sum(
                row["row_width_mismatch"] for row in support if row["pool"] == label
            )
            for label in ("p97", "p95")
        },
        "table_rejections_other": {
            label: sum(
                row["other_table_rejections"] for row in support if row["pool"] == label
            )
            for label in ("p97", "p95")
        },
        "train_ready": False,
    }
    return manifest, "".join(_dump(row) + "\n" for row in support)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p97-pool", type=Path, required=True)
    parser.add_argument("--p100-native", type=Path, required=True)
    parser.add_argument("--p95-pool", type=Path, required=True)
    parser.add_argument("--p103-native", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest, support = build(
        args.p97_pool, args.p100_native, args.p95_pool, args.p103_native
    )
    output = args.output_dir
    if args.verify_only:
        if (
            json.loads((output / "manifest.json").read_text()) != manifest
            or (output / "support_matrix.jsonl").read_text() != support
        ):
            raise ValueError("P103 categorical delta replay drift")
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        (output / "support_matrix.jsonl").write_text(support)
    print(_dump(manifest))


if __name__ == "__main__":
    main()
