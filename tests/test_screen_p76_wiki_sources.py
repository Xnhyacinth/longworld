"""Source screening must keep structural support separate from clean values."""

from __future__ import annotations

from longworld.synthesis.wiki_adapter import (
    Member,
    PageRecord,
    build_snapshot,
    canonical_json,
)
from scripts.screen_p76_wiki_sources import screen


def test_template_debris_does_not_count_as_clean_table_evidence(tmp_path) -> None:
    page = PageRecord(
        pageid=71,
        title="List of test observatories",
        revid=7001,
        timestamp="2026-09-24T00:00:00Z",
        wikitext="""{| class="wikitable"
! Name !! Established !! Location
|-
| Alpha Observatory || 1904 || flag|China
|-
| Beta Observatory || 1986 || France
|}
""",
    )
    snapshot = build_snapshot(
        members=[Member(page.pageid, page.title)],
        pages={page.pageid: page},
        link_meta=[],
        rights={
            "text": "Creative Commons Attribution-Share Alike 4.0",
            "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
            "page_url_prefix": "https://en.wikipedia.org/wiki/",
        },
        category_title="test table bundle",
        collection_kind="title_bundle",
        frozen_at="2026-09-24T00:00:00Z",
    )
    path = tmp_path / "test_snapshot.json"
    path.write_bytes(canonical_json(snapshot))
    result = screen(path)
    assert result["structurally_supported"] >= result["clean_structurally_supported"]
    assert result["structurally_supported"] > result["clean_structurally_supported"]
    assert result["clean_structurally_supported"] >= 2
