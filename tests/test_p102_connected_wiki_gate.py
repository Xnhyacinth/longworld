"""P102 global page identity uses both title and canonical URL."""

import pytest

from scripts.p102_connected_wiki_gate import _register, _url_key


def test_canonical_url_deduplicates_encoded_page_path() -> None:
    assert _url_key("https://en.wikipedia.org/wiki/Foo%20Bar") == _url_key(
        "https://en.wikipedia.org/wiki/Foo Bar"
    )


def test_title_and_url_cannot_switch_split_or_identity() -> None:
    titles: dict = {}
    urls: dict = {}
    first = {
        "title": "List of plants",
        "page_url": "https://en.wikipedia.org/wiki/List_of_plants",
    }
    _register(titles, urls, first, "train")
    _register(titles, urls, first, "train")
    with pytest.raises(ValueError, match="conflicting URL or split"):
        _register(titles, urls, first, "eval")
    with pytest.raises(ValueError, match="conflicting title or split"):
        _register(
            titles,
            urls,
            {"title": "Alias of plants", "page_url": first["page_url"]},
            "train",
        )
