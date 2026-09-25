"""Measure title, URL and split overlap before merging P97 Wiki intakes."""

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

from longworld.synthesis.p93_wiki_structural_intake import verify as verify_intake
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p97-wiki-intake-overlap.v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pages(source: dict) -> list[dict]:
    snapshot = _snapshot(ROOT, source["snapshot"])
    return snapshot["documents"]


def report(
    prior_pool: Path,
    intake_dirs: list[Path],
    *,
    prior_router: Path | None = None,
) -> dict[str, Any]:
    if (
        ".." in prior_pool.parts
        or any(".." in path.parts for path in intake_dirs)
        or (prior_router is not None and ".." in prior_router.parts)
    ):
        raise ValueError("audit inputs cannot traverse parents")
    prior_pool = prior_pool if prior_pool.is_absolute() else ROOT / prior_pool
    intake_dirs = [path if path.is_absolute() else ROOT / path for path in intake_dirs]
    if prior_router is not None and not prior_router.is_absolute():
        prior_router = ROOT / prior_router
    if (
        not prior_pool.is_relative_to(ROOT)
        or any(not path.is_relative_to(ROOT) for path in intake_dirs)
        or (prior_router is not None and not prior_router.is_relative_to(ROOT))
    ):
        raise ValueError("audit inputs must be workspace-relative")
    pool = json.loads(prior_pool.read_text())
    if pool.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("prior pool schema mismatch")
    prior_titles: dict[str, tuple[str, str]] = {}
    prior_urls: dict[str, tuple[str, str]] = {}
    router_receipt = None
    if prior_router is not None:
        router = json.loads(prior_router.read_text())
        if router.get("schema") != "longworld.p92-source-router.v1.result":
            raise ValueError("prior router schema mismatch")
        router_titles: dict[str, tuple[str, str]] = {}
        router_urls: dict[str, tuple[str, str]] = {}
        for source in router["sources"]:
            if source["source_kind"] != "real_wiki":
                continue
            for pin in source["source_pins"]:
                for doc in _snapshot(ROOT, pin)["documents"]:
                    title, url, split = (
                        doc["title"].casefold(),
                        doc["page_url"],
                        source["split"],
                    )
                    if (
                        title in router_titles and router_titles[title] != (url, split)
                    ) or (url in router_urls and router_urls[url] != (title, split)):
                        raise ValueError(
                            "prior router repeats title/URL with conflicting identity or split"
                        )
                    router_titles[title] = (url, split)
                    router_urls[url] = (title, split)
                    prior_titles.setdefault(title, (source["source_identity"], split))
                    prior_urls.setdefault(url, (source["source_identity"], split))
        router_receipt = {
            "path": str(prior_router.relative_to(ROOT)),
            "sha256": sha(prior_router),
            "unique_titles": len(router_titles),
            "unique_urls": len(router_urls),
        }
    for source in pool["sources"]:
        for doc in _pages(source):
            title, url = doc["title"].casefold(), doc["page_url"]
            if title in prior_titles or url in prior_urls:
                raise ValueError("prior pool repeats a page title or URL")
            prior_titles[title] = (source["name"], source["split"])
            prior_urls[url] = (source["name"], source["split"])
    seen_titles = dict(prior_titles)
    seen_urls = dict(prior_urls)
    overlaps = []
    accepted = []
    intake_receipts = []
    gross = Counter()
    net = Counter()
    for intake_dir in intake_dirs:
        receipt = verify_intake(intake_dir, ROOT)
        source_pool = intake_dir / "source_pool.json"
        sources = json.loads(source_pool.read_text())["sources"]
        if len(sources) != receipt["source_groups"]:
            raise ValueError("intake group count drift")
        intake_receipts.append(
            {
                "manifest": str((intake_dir / "manifest.json").relative_to(ROOT)),
                "manifest_sha256": sha(intake_dir / "manifest.json"),
                "source_pool_sha256": sha(source_pool),
                "groups": len(sources),
                "pages": receipt["frozen_pages"],
                "facts": receipt["frozen_facts"],
                "http_requests": receipt["http_requests"],
                "failures": len(receipt["failures"]),
            }
        )
        gross.update(
            groups=len(sources),
            pages=receipt["frozen_pages"],
            facts=receipt["frozen_facts"],
        )
        for source in sources:
            pages = _pages(source)
            if len({doc["title"].casefold() for doc in pages}) != len(pages) or len(
                {doc["page_url"] for doc in pages}
            ) != len(pages):
                raise ValueError("new snapshot repeats a page title or URL")
            title_overlap = [
                {
                    "title": doc["title"],
                    "prior_source": seen_titles[doc["title"].casefold()][0],
                    "prior_split": seen_titles[doc["title"].casefold()][1],
                }
                for doc in pages
                if doc["title"].casefold() in seen_titles
            ]
            url_overlap = [
                {
                    "url": doc["page_url"],
                    "prior_source": seen_urls[doc["page_url"]][0],
                    "prior_split": seen_urls[doc["page_url"]][1],
                }
                for doc in pages
                if doc["page_url"] in seen_urls
            ]
            if title_overlap or url_overlap:
                overlaps.append(
                    {
                        "source": source["name"],
                        "split": source["split"],
                        "title_overlap": title_overlap,
                        "url_overlap": url_overlap,
                        "cross_split": any(
                            row["prior_split"] != source["split"]
                            for row in title_overlap + url_overlap
                        ),
                    }
                )
                continue
            accepted.append(source["name"])
            net.update(
                groups=1,
                pages=len(pages),
                facts=len(_snapshot(ROOT, source["snapshot"])["facts"]),
            )
            for doc in pages:
                seen_titles[doc["title"].casefold()] = (source["name"], source["split"])
                seen_urls[doc["page_url"]] = (source["name"], source["split"])
    return {
        "schema": SCHEMA + ".result",
        "prior_pool": {
            "path": str(prior_pool.relative_to(ROOT)),
            "sha256": sha(prior_pool),
        },
        "prior_router": router_receipt,
        "intakes": intake_receipts,
        "gross": dict(sorted(gross.items())),
        "net_novel": dict(sorted(net.items())),
        "accepted_groups": accepted,
        "overlap_groups": overlaps,
        "cross_split_overlap_groups": sum(row["cross_split"] for row in overlaps),
        "url_only_overlap_groups": sum(
            bool(row["url_overlap"]) and not row["title_overlap"] for row in overlaps
        ),
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-router", type=Path)
    parser.add_argument("--prior-pool", type=Path, required=True)
    parser.add_argument("--intake-dir", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = report(args.prior_pool, args.intake_dir, prior_router=args.prior_router)
    if args.verify_only:
        if json.loads(args.output.read_text()) != result:
            raise ValueError("P97 overlap replay drift")
    else:
        if args.output.exists():
            raise ValueError("P97 overlap output must be new")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
