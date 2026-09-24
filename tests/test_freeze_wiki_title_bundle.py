"""Contract checks for explicit title resolution; HTTP is not used in tests."""

from __future__ import annotations

import pytest

from longworld.synthesis.wiki_adapter import SnapshotError
from scripts.freeze_wiki_title_bundle import resolve_titles


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get_json(self, params):
        assert params["titles"] == "List of observatories|List of radio telescopes"
        return {"query": {"pages": self.pages}}


def test_resolve_titles_returns_pinned_ids() -> None:
    pages = [
        {"pageid": 1, "title": "List of observatories"},
        {"pageid": 2, "title": "List of radio telescopes"},
    ]
    assert [
        member.pageid
        for member in resolve_titles(
            FakeFetcher(pages), ["List of observatories", "List of radio telescopes"]
        )
    ] == [1, 2]


def test_resolve_titles_rejects_missing_page() -> None:
    pages = [
        {"pageid": 1, "title": "List of observatories"},
        {"missing": True, "title": "List of radio telescopes"},
    ]
    with pytest.raises(SnapshotError, match="missing page"):
        resolve_titles(
            FakeFetcher(pages), ["List of observatories", "List of radio telescopes"]
        )
