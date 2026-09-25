"""P101 source support follows pinned visible table rows."""

import hashlib
import json

import pytest

from scripts import p101_wiki_support_matrix as support


def test_exact_shared_name_requires_two_visible_table_pages(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(support, "ROOT", tmp_path)
    snapshot = {
        "documents": [
            {
                "title": "First list",
                "text": "## List\nName | Selector\nAster | A1\nBirch | B1\n",
            },
            {
                "title": "Second list",
                "text": "## List\nName | Target\nAster | North\nCedar | South\n",
            },
        ]
    }
    raw = json.dumps(snapshot).encode()
    (tmp_path / "source.json").write_bytes(raw)
    source = {
        "name": "example",
        "split": "train",
        "domain": "botany",
        "topic": "plants",
        "snapshot": {"path": "source.json", "sha256": hashlib.sha256(raw).hexdigest()},
    }
    report = support._load_source(source)
    assert report["documents_with_typed_rows"] == 2
    assert report["shared_name_page_pairs"] == [
        {
            "first_title": "First list",
            "second_title": "Second list",
            "shared_names": 1,
        }
    ]


def test_source_snapshot_requires_matching_workspace_pin(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(support, "ROOT", tmp_path)
    source = {"snapshot": {"path": "../escape.json", "sha256": "0" * 64}}
    with pytest.raises(ValueError, match="inside workspace"):
        support._load_source(source)
