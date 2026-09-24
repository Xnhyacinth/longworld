#!/usr/bin/env python3
"""Freeze a MediaWiki category into a SourceSnapshot v1 (charter §14).

T4 first-slice CLI for the P74 route A adapter
(``longworld/synthesis/wiki_adapter.py``): freezes one category's pages at
pinned revids, extracts candidate facts with verbatim spans, validates the
snapshot, and writes it plus a small freeze log.

Network policy: try the live MediaWiki API first; if that fails, fall back
to a local cache directory (``--from-cache``); if neither works, exit 3 with
a BLOCKER message.  All HTTP access lives here (and in the adapter's fetcher
class), never in tests.

Exit codes: 0 success, 2 usage/contract error, 3 blocked (no live fetch and
no usable cache), 4 unexpected failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.wiki_adapter import (
    MAX_PAGES,
    SNAPSHOT_KIND,
    SOURCE_SNAPSHOT_SCHEMA,
    USER_AGENT,
    HttpError,
    Member,
    PageRecord,
    SnapshotError,
    WikiHttpFetcher,
    build_snapshot,
    canonical_json,
    page_url,
    parse_links_payload,
    parse_members_payload,
    parse_revision_payload,
    parse_rightsinfo_payload,
    snapshot_from_dict,
)

DEFAULT_API = "https://en.wikipedia.org/w/api.php"
BLOCKER_EXIT = 3
CONTRACT_EXIT = 2
UNEXPECTED_EXIT = 4


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def freeze_live(fetcher: WikiHttpFetcher, category: str, limit: int) -> dict | None:
    """Fetch everything from the live API; returns freeze inputs or None."""
    try:
        members = fetcher.fetch_category_members(category, limit)
        if not members:
            raise SnapshotError("category has no page members")
        pages: dict[int, PageRecord] = {}
        for member in members:
            pages[member.pageid] = fetcher.fetch_revision(member.pageid)
        rights_payload = fetcher.get_json(
            {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "meta": "siteinfo",
                "siprop": "rightsinfo|general",
            }
        )
        rights = parse_rightsinfo_payload(rights_payload)
        link_meta = fetcher.fetch_links_meta([member.title for member in members])
        return {
            "members": members,
            "pages": pages,
            "rights": rights,
            "link_meta": link_meta,
            "fetched_via": "live-api",
        }
    except (HttpError, SnapshotError) as error:
        print(f"[freeze] live fetch failed: {error}", file=sys.stderr)
        return None


def freeze_from_cache(cache_dir: Path, category: str, limit: int) -> dict | None:
    """Rebuild freeze inputs from cached API payload JSON files.

    Cache layout (each file is one raw MediaWiki API response):
    ``members.json`` — the categorymembers listing;
    ``wikipedia-<pageid>-r<revid>.json`` — one revision payload per page;
    ``siteinfo.json`` — the rightsinfo payload;
    ``links-<pageid>.json`` — link pageprops payloads (optional).
    """
    members_file = cache_dir / "members.json"
    if not members_file.is_file():
        print(f"[freeze] cache missing members.json in {cache_dir}", file=sys.stderr)
        return None
    try:
        members = parse_members_payload(json.loads(members_file.read_text("utf-8")))
        members = members[:limit]
        pages: dict[int, PageRecord] = {}
        for member in members:
            # any cached revision payload for this pageid works: the revid is
            # whatever the cache froze, and it is pinned as-is
            candidates = sorted(cache_dir.glob(f"wikipedia-{member.pageid}-r*.json"))
            if not candidates:
                print(
                    f"[freeze] cache has no revision for {member.title}",
                    file=sys.stderr,
                )
                return None
            payload = json.loads(candidates[0].read_text("utf-8"))
            pages[member.pageid] = parse_revision_payload(payload)
        siteinfo = cache_dir / "siteinfo.json"
        if siteinfo.is_file():
            rights = parse_rightsinfo_payload(json.loads(siteinfo.read_text("utf-8")))
        else:
            rights = {
                "text": "Creative Commons Attribution-Share Alike 4.0",
                "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
                "page_url_prefix": "https://en.wikipedia.org/wiki/",
            }
            print(
                "[freeze] cache has no siteinfo.json; assuming site default license",
                file=sys.stderr,
            )
        link_meta = []
        for member in members:
            link_file = cache_dir / f"links-{member.pageid}.json"
            if link_file.is_file():
                link_meta.extend(
                    parse_links_payload(json.loads(link_file.read_text("utf-8")))
                )
        return {
            "members": members,
            "pages": pages,
            "rights": rights,
            "link_meta": link_meta,
            "fetched_via": "local-cache",
        }
    except (OSError, json.JSONDecodeError, SnapshotError) as error:
        print(f"[freeze] cache read failed: {error}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Freeze a MediaWiki category into a SourceSnapshot v1"
    )
    parser.add_argument("category", help='e.g. "Category:Astronomical observatories"')
    parser.add_argument("out", help="output snapshot JSON path")
    parser.add_argument(
        "--pages",
        type=int,
        default=15,
        help=f"page budget (10-30 recommended, hard cap {MAX_PAGES})",
    )
    parser.add_argument("--api", default=DEFAULT_API, help="MediaWiki api.php base URL")
    parser.add_argument(
        "--from-cache",
        type=Path,
        default=None,
        help="fallback cache directory of raw API payloads",
    )
    args = parser.parse_args(argv)

    if not args.category.startswith("Category:"):
        print(
            "[freeze] usage: category must start with 'Category:' (list-page "
            "support arrives with the next adapter slice)",
            file=sys.stderr,
        )
        return CONTRACT_EXIT
    if not (1 <= args.pages <= MAX_PAGES):
        print(f"[freeze] --pages must be within 1..{MAX_PAGES}", file=sys.stderr)
        return CONTRACT_EXIT

    fetcher = WikiHttpFetcher(api_base=args.api)
    inputs = freeze_live(fetcher, args.category, args.pages)
    if inputs is None and args.from_cache is not None:
        inputs = freeze_from_cache(args.from_cache, args.category, args.pages)
    if inputs is None:
        print(
            "BLOCKER: cannot freeze the snapshot — live MediaWiki API unreachable "
            "and no usable local cache. The adapter and CLI still ship; rerun with "
            "network access or provide --from-cache with members.json, "
            "wikipedia-<pageid>-r<revid>.json and siteinfo.json payloads.",
            file=sys.stderr,
        )
        return BLOCKER_EXIT

    frozen_at = _utc_now()
    try:
        snapshot = build_snapshot(
            members=inputs["members"],
            pages=inputs["pages"],
            link_meta=inputs["link_meta"],
            rights=inputs["rights"],
            category_title=args.category,
            frozen_at=frozen_at,
            fetched_via=inputs["fetched_via"],
            generator_params={
                "list": "categorymembers",
                "cmtype": "page",
                "limit": args.pages,
                "api": args.api,
            },
        )
    except SnapshotError as error:
        print(f"[freeze] snapshot contract violation: {error}", file=sys.stderr)
        return CONTRACT_EXIT

    # loader-side roundtrip check before writing
    snapshot_from_dict(json.loads(canonical_json(snapshot).decode("utf-8")))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(out_path, canonical_json(snapshot))

    log = {
        "schema_version": "longworld.wiki-freeze-log.v1",
        "snapshot_id": snapshot["snapshot_id"],
        "category": args.category,
        "frozen_at": frozen_at,
        "fetched_via": inputs["fetched_via"],
        "api": args.api,
        "user_agent": USER_AGENT,
        "http_requests": fetcher.requests,
        "pages": len(snapshot["documents"]),
        "revisions": snapshot["source"]["revisions"],
        "entities": len(snapshot["entities"]),
        "facts": len(snapshot["facts"]),
        "relations": len(snapshot["relations"]),
        "quarantine": len(snapshot["ungrounded_quarantine"]),
        "license": snapshot["source"]["license"],
        "snapshot_path": str(out_path),
        "snapshot_sha256": _sha256(canonical_json(snapshot)),
    }
    log_path = out_path.with_suffix(".freeze-log.json")
    _atomic_write(
        log_path,
        json.dumps(log, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8"),
    )
    print(
        f"[freeze] {snapshot['snapshot_id']}: {len(snapshot['documents'])} pages, "
        f"{len(snapshot['facts'])} facts, {len(snapshot['entities'])} entities, "
        f"{len(snapshot['ungrounded_quarantine'])} quarantined -> {out_path}"
    )
    return 0


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
