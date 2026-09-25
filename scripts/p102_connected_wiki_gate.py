"""Admit frozen Wiki JOIN pairs only after global title, URL and split checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_row_binding, wiki_world_bridge
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p102-connected-wiki-gate.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("gate pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"gate pin changed: {relative}")
    return path


def _url_key(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path:
        raise ValueError("Wiki page URL is not canonical HTTPS")
    return parsed.netloc.casefold() + unquote(parsed.path).rstrip("/").casefold()


def _register(
    titles: dict[str, tuple[str, str]],
    urls: dict[str, tuple[str, str]],
    doc: dict,
    split: str,
) -> None:
    title = doc["title"].casefold()
    url = _url_key(doc["page_url"])
    if title in titles and titles[title] != (url, split):
        raise ValueError("prior title has conflicting URL or split")
    if url in urls and urls[url] != (title, split):
        raise ValueError("prior URL has conflicting title or split")
    titles[title] = (url, split)
    urls[url] = (title, split)


def _prior_inventory(config: dict) -> tuple[dict, dict, dict[str, dict]]:
    titles: dict[str, tuple[str, str]] = {}
    urls: dict[str, tuple[str, str]] = {}
    router = json.loads(_pin(config["prior_router"]).read_text())
    if router.get("schema") != "longworld.p92-source-router.v1.result":
        raise ValueError("wrong P92 router schema")
    for source in router["sources"]:
        if source["source_kind"] != "real_wiki":
            continue
        for pin in source["source_pins"]:
            for doc in _snapshot(ROOT, pin)["documents"]:
                _register(titles, urls, doc, source["split"])
    base_by_name = {}
    for pool_pin in config["prior_pools"]:
        pool = json.loads(_pin(pool_pin).read_text())
        if pool.get("schema") != "longworld.source-batch-pool.v2":
            raise ValueError("wrong prior source-pool schema")
        for source in pool["sources"]:
            for doc in _snapshot(ROOT, source["snapshot"])["documents"]:
                _register(titles, urls, doc, source["split"])
            if pool_pin == config["prior_pools"][-1]:
                if source["name"] in base_by_name:
                    raise ValueError("base source name repeated")
                base_by_name[source["name"]] = source
    return titles, urls, base_by_name


def _candidate_rows(
    config: dict, titles: dict, urls: dict, base_by_name: dict
) -> tuple[list[dict], dict]:
    acquisition_path = _pin(config["acquisition_manifest"])
    raw_pool_path = _pin(config["acquisition_pool"])
    acquisition = json.loads(acquisition_path.read_text())
    raw_pool = json.loads(raw_pool_path.read_text())
    if (
        acquisition.get("schema")
        not in {
            "longworld.connected-wiki-intake.v1.result",
            "longworld.p102-connected-recovery.v1.result",
        }
        or raw_pool.get("schema") != "longworld.source-batch-pool.v2"
        or acquisition.get("frozen_groups") != len(raw_pool["sources"])
        or acquisition.get("base_pool_sha256") != config["prior_pools"][-1]["sha256"]
    ):
        raise ValueError("connected acquisition and source pool differ")
    frozen_by_id = {}
    for report in acquisition["seeds"]:
        anchor = base_by_name.get(report["anchor_source"])
        if anchor is None or report["split"] != anchor["split"]:
            raise ValueError("connected seed anchor or split differs")
        anchor_docs = {
            doc["title"].casefold(): doc
            for doc in _snapshot(ROOT, anchor["snapshot"])["documents"]
        }
        for item in report["frozen"]:
            key = item["snapshot_id"]
            if key in frozen_by_id:
                raise ValueError("connected frozen snapshot ID repeats")
            frozen_by_id[key] = (report, item, anchor_docs)
    if len(frozen_by_id) != len(raw_pool["sources"]):
        raise ValueError("accepted source inventory differs from acquisition")
    rows = []
    for source in raw_pool["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        group = frozen_by_id.get(snapshot["snapshot_id"])
        if group is None or source["name"] != snapshot["snapshot_id"]:
            raise ValueError("connected source missing frozen acquisition")
        report, frozen, anchor_docs = group
        if (
            source["split"] != report["split"]
            or source["split"] not in {"train", "eval"}
            or source["snapshot"]["sha256"] != frozen["sha256"]
            or snapshot["source"]["revisions"] != frozen["revisions"]
        ):
            raise ValueError("connected source pin or split differs")
        docs = snapshot["documents"]
        if (
            len(docs) != 2
            or len({doc["title"].casefold() for doc in docs}) != 2
            or len({_url_key(doc["page_url"]) for doc in docs}) != 2
        ):
            raise ValueError("connected pair needs two distinct visible pages")
        target = next((doc for doc in docs if doc["title"] == frozen["title"]), None)
        if target is None:
            raise ValueError("frozen target page missing")
        anchor_doc = next(doc for doc in docs if doc is not target)
        prior_anchor = anchor_docs.get(anchor_doc["title"].casefold())
        if prior_anchor is None or _url_key(prior_anchor["page_url"]) != _url_key(
            anchor_doc["page_url"]
        ):
            raise ValueError("frozen anchor differs from pinned base page")
        task_ids = [
            task.task_id
            for task in wiki_row_binding.build_join_tasks(
                wiki_world_bridge.snapshot_to_world(snapshot), max_tasks=32
            )
        ]
        if (
            task_ids != frozen["task_ids"]
            or len(task_ids) != frozen["strict_join_tasks"]
            or not task_ids
        ):
            raise ValueError("frozen JOIN tasks do not replay")
        title_key = target["title"].casefold()
        url_key = _url_key(target["page_url"])
        rows.append(
            {
                "source": source,
                "snapshot_id": snapshot["snapshot_id"],
                "domain": source["domain"],
                "topic": source["topic"],
                "split": source["split"],
                "anchor_title": anchor_doc["title"],
                "target_title": target["title"],
                "target_url": target["page_url"],
                "target_title_key": title_key,
                "target_url_key": url_key,
                "strict_join_tasks": len(task_ids),
                "prior_title": title_key in titles,
                "prior_url": url_key in urls,
                "prior_title_split": titles.get(title_key, (None, None))[1],
                "prior_url_split": urls.get(url_key, (None, None))[1],
            }
        )
    return rows, raw_pool


def build(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or len(config.get("prior_pools", [])) != 2
        or output_dir.exists() != verify_only
    ):
        raise ValueError("invalid P102 gate config or output state")
    titles, urls, base_by_name = _prior_inventory(config)
    rows, raw_pool = _candidate_rows(config, titles, urls, base_by_name)
    title_counts = Counter(row["target_title_key"] for row in rows)
    url_counts = Counter(row["target_url_key"] for row in rows)
    accepted, ledger = [], []
    for row in rows:
        reasons = []
        if row["prior_title"]:
            reasons.append("prior_title")
        if row["prior_url"]:
            reasons.append("prior_url")
        if row["prior_title_split"] not in (None, row["split"]) or row[
            "prior_url_split"
        ] not in (None, row["split"]):
            reasons.append("cross_split_prior")
        if title_counts[row["target_title_key"]] > 1:
            reasons.append("duplicate_new_title")
        if url_counts[row["target_url_key"]] > 1:
            reasons.append("duplicate_new_url")
        if not reasons:
            accepted.append(row["source"])
        ledger.append(
            {
                key: value
                for key, value in row.items()
                if key
                not in {
                    "source",
                    "target_title_key",
                    "target_url_key",
                    "prior_title",
                    "prior_url",
                    "prior_title_split",
                    "prior_url_split",
                }
            }
            | {"status": "accepted" if not reasons else "rejected", "reasons": reasons}
        )
    admitted = {**raw_pool, "sources": accepted}
    if not verify_only:
        output_dir.mkdir(parents=True)
    files = {
        "source_pool.json": json.dumps(
            admitted, ensure_ascii=False, sort_keys=True, indent=2
        )
        + "\n",
        "admission.jsonl": "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ledger
        ),
    }
    for name, content in files.items():
        path = output_dir / name
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"P102 gate byte replay differs: {name}")
        else:
            path.write_text(content)
    result = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "prior_router": config["prior_router"],
        "prior_pools": config["prior_pools"],
        "acquisition_manifest": config["acquisition_manifest"],
        "acquisition_pool": config["acquisition_pool"],
        "prior_titles": len(titles),
        "prior_urls": len(urls),
        "frozen_groups": len(rows),
        "admitted_groups": len(accepted),
        "admitted_tasks": sum(
            row["strict_join_tasks"] for row in rows if row["source"] in accepted
        ),
        "admitted_domains": dict(
            sorted(
                Counter(
                    row["domain"] for row in rows if row["source"] in accepted
                ).items()
            )
        ),
        "rejection_reasons": dict(
            sorted(
                Counter(reason for row in ledger for reason in row["reasons"]).items()
            )
        ),
        "source_pool_sha256": _sha(output_dir / "source_pool.json"),
        "admission_sha256": _sha(output_dir / "admission.jsonl"),
        "train_ready": False,
    }
    manifest = output_dir / "manifest.json"
    if verify_only:
        if json.loads(manifest.read_text()) != result:
            raise ValueError("P102 gate manifest replay differs")
    else:
        manifest.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.config, args.output_dir, verify_only=args.verify_only),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
