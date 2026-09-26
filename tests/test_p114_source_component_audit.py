"""Source components must cross group IDs and detect alias-level split leaks."""

from __future__ import annotations

import pytest

from scripts.p114_source_component_audit import (
    author_key,
    components,
    page_key,
    revision_key,
)


def test_author_alias_connects_distinct_book_groups_across_split() -> None:
    assert author_key("Austen, Jane") == author_key("Jane Austen")
    graph = components(
        [
            {
                "group": "gutenberg-1",
                "split": "train",
                "identities": ["book:author:" + author_key("Austen, Jane")],
            },
            {
                "group": "gutenberg-2",
                "split": "eval",
                "identities": ["book:author:" + author_key("Jane Austen")],
            },
        ]
    )
    assert graph["components"] == 1
    assert graph["conflicts"][0]["groups"] == ["gutenberg-1", "gutenberg-2"]


def test_same_wiki_page_across_revisions_connects_source_groups() -> None:
    first = page_key("https://en.wikipedia.org/wiki/List%5Fof%5Fbridges")
    second = page_key("https://en.wikipedia.org/wiki/list_of_bridges")
    assert first == second
    assert revision_key(
        first, "https://en.wikipedia.org/w/index.php?oldid=123"
    ) != revision_key(second, "https://en.wikipedia.org/w/index.php?oldid=456")
    graph = components(
        [
            {"group": "snapshot-a", "split": "train", "identities": [first]},
            {"group": "snapshot-b", "split": "eval", "identities": [second]},
            {
                "group": "snapshot-c",
                "split": "eval",
                "identities": ["wiki:page:unrelated"],
            },
        ]
    )
    assert graph["components"] == 2
    assert graph["conflicts"][0]["groups"] == ["snapshot-a", "snapshot-b"]


def test_invalid_article_revision_and_duplicate_group_rejected() -> None:
    with pytest.raises(ValueError, match="canonical enwiki"):
        page_key("https://example.com/wiki/List_of_bridges")
    with pytest.raises(ValueError, match="oldid"):
        revision_key("wiki:page:x", "https://en.wikipedia.org/wiki/X")
    with pytest.raises(ValueError, match="repeats"):
        components(
            [
                {"group": "same", "split": "train", "identities": []},
                {"group": "same", "split": "eval", "identities": []},
            ]
        )
