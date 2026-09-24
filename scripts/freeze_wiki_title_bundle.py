"""Freeze an explicit, coherent set of Wikipedia page titles.

This is a source acquisition step, not a task generator. Each line of the
titles file is one page title; pages are pinned to their fetched revisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.wiki_adapter import (
    MAX_PAGES,
    HttpError,
    Member,
    SnapshotError,
    WikiHttpFetcher,
    build_snapshot,
    canonical_json,
    snapshot_from_dict,
)


def load_titles(path: Path) -> list[str]:
    titles = [line.strip() for line in path.read_text("utf-8").splitlines()]
    titles = [title for title in titles if title and not title.startswith("#")]
    if not 1 <= len(titles) <= MAX_PAGES:
        raise ValueError(f"title count must be within 1..{MAX_PAGES}")
    if len(set(titles)) != len(titles) or any("|" in title for title in titles):
        raise ValueError("titles must be unique and cannot contain '|'")
    return titles


def resolve_titles(fetcher: WikiHttpFetcher, titles: list[str]) -> list[Member]:
    """Resolve requested titles to page IDs before fetching frozen revisions."""
    members = []
    for start in range(0, len(titles), 40):
        payload = fetcher.get_json(
            {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "titles": "|".join(titles[start : start + 40]),
            }
        )
        pages = payload.get("query", {}).get("pages", [])
        if not isinstance(pages, list) or len(pages) != len(titles[start : start + 40]):
            raise SnapshotError("title resolution returned a missing or ambiguous page")
        for page in pages:
            if not isinstance(page, dict) or page.get("missing") is True:
                raise SnapshotError("title bundle contains a missing page")
            pageid, title = page.get("pageid"), page.get("title")
            if not isinstance(pageid, int) or pageid <= 0 or not isinstance(title, str):
                raise SnapshotError("title resolution returned an invalid page")
            members.append(Member(pageid, title))
    if len({member.pageid for member in members}) != len(titles):
        raise SnapshotError("title bundle resolves multiple titles to one page")
    return members


def freeze_titles(
    fetcher: WikiHttpFetcher, titles: list[str], label: str, out: Path
) -> dict:
    if not label.strip():
        raise ValueError("collection label is empty")
    members = resolve_titles(fetcher, titles)
    pages = {member.pageid: fetcher.fetch_revision(member.pageid) for member in members}
    rights = fetcher.get_json(
        {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "meta": "siteinfo",
            "siprop": "rightsinfo|general",
        }
    )
    from longworld.synthesis.wiki_adapter import parse_rightsinfo_payload

    links = fetcher.fetch_links_meta([member.title for member in members])
    snapshot = build_snapshot(
        members=members,
        pages=pages,
        link_meta=links,
        rights=parse_rightsinfo_payload(rights),
        category_title=label,
        collection_kind="title_bundle",
        frozen_at=datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        generator_params={"list": "explicit_titles", "requested_titles": titles},
    )
    data = canonical_json(snapshot)
    snapshot_from_dict(json.loads(data))
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(out)
    receipt = {
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": hashlib.sha256(data).hexdigest(),
        "pages": len(snapshot["documents"]),
        "facts": len(snapshot["facts"]),
        "revisions": snapshot["source"]["revisions"],
        "license": snapshot["source"]["license"],
        "http_requests": fetcher.requests,
    }
    out.with_suffix(".freeze-log.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--titles-file", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--api", default="https://en.wikipedia.org/w/api.php")
    args = parser.parse_args(argv)
    try:
        receipt = freeze_titles(
            WikiHttpFetcher(args.api),
            load_titles(args.titles_file),
            args.label,
            args.out,
        )
    except (HttpError, SnapshotError, OSError, ValueError) as error:
        print(f"[freeze-titles] {error}", file=sys.stderr)
        return 3
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
