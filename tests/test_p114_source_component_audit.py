"""Source components must cross group IDs and detect alias-level split leaks."""

from __future__ import annotations

import pytest

from scripts.p114_source_component_audit import (
    _check_book_source_components,
    author_key,
    components,
    page_key,
    revision_key,
)


def _book_record(group: str, split: str, *, ebook: int, work: str, author: str) -> dict:
    return {
        "source_group": group,
        "split": split,
        "ebook_id": ebook,
        "catalog_work_key": work,
        "raw_sha256": "raw-" + group,
        "body_sha256": "body-" + group,
        "catalog_author_keys": [author],
    }


def test_cross_manifest_book_source_identity_rejected_even_with_same_split() -> None:
    first = _book_record("book-a", "train", ebook=1, work="a|work", author="a|one")
    second = _book_record("book-b", "train", ebook=1, work="b|work", author="b|two")
    with pytest.raises(ValueError, match="duplicate book source identity"):
        _check_book_source_components([first, second])


def test_cross_manifest_duplicate_work_rejected_even_with_same_split() -> None:
    first = _book_record("book-a", "train", ebook=1, work="a|work", author="a|one")
    second = _book_record("book-b", "train", ebook=2, work="a|work", author="b|two")
    with pytest.raises(ValueError, match="duplicate book source identity"):
        _check_book_source_components([first, second])


@pytest.mark.parametrize("shared_field", ["catalog_work_key", "catalog_author_keys"])
def test_cross_manifest_book_work_or_author_split_rejected(shared_field: str) -> None:
    first = _book_record("book-a", "train", ebook=1, work="a|work", author="a|one")
    second = _book_record("book-b", "eval", ebook=2, work="b|work", author="b|two")
    second[shared_field] = first[shared_field]
    with pytest.raises(ValueError, match="work or author component crosses train/eval"):
        _check_book_source_components([first, second])


def test_same_author_same_split_is_allowed_for_distinct_books() -> None:
    first = _book_record("book-a", "train", ebook=1, work="a|work", author="a|one")
    second = _book_record("book-b", "train", ebook=2, work="a|other", author="a|one")
    _check_book_source_components([first, second])


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
