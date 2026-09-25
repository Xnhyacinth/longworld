"""P94 frozen-source selection keeps page titles unique across splits."""

from __future__ import annotations

from scripts import build_p94_wiki_structural_pool as merge


def test_rejects_entire_duplicate_bundle_without_changing_prior(monkeypatch):
    documents = {
        "old": ["List of libraries in Pakistan", "List of libraries in Nepal"],
        "fresh": ["List of hospitals in Kenya", "List of hospitals in Uganda"],
        "repeat": ["List of hospitals in Kenya", "List of hospitals in Tanzania"],
    }
    monkeypatch.setattr(
        merge,
        "_snapshot",
        lambda _root, pin: {
            "documents": [{"title": title} for title in documents[pin["path"]]]
        },
    )
    sources = [
        {"name": name, "split": split, "snapshot": {"path": name}}
        for name, split in (
            ("old", "eval"),
            ("fresh", "train"),
            ("repeat", "eval"),
        )
    ]
    selected, rejected = merge.select(
        sources, {"list of libraries in pakistan": {"train"}}
    )
    assert [item["name"] for item in selected] == ["fresh"]
    assert [item["source"] for item in rejected] == ["old", "repeat"]
    assert rejected[0]["overlaps"][0]["cross_split"] is True
    assert rejected[1]["overlaps"][0]["cross_split"] is True


def test_rejects_same_split_reuse_as_not_novel(monkeypatch):
    monkeypatch.setattr(
        merge,
        "_snapshot",
        lambda _root, _pin: {"documents": [{"title": "List of museums in India"}]},
    )
    selected, rejected = merge.select(
        [{"name": "museum", "split": "eval", "snapshot": {"path": "x"}}],
        {"list of museums in india": {"eval"}},
    )
    assert selected == []
    assert rejected[0]["overlaps"][0]["cross_split"] is False
