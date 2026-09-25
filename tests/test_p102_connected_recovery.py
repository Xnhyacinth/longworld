"""P102 recovery ranks frozen, topic-compatible source candidates."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import p102_connected_recovery as recovery


def test_relative_freeze_path_is_not_dropped_by_serializer() -> None:
    path = Path("data/capability_records/p102/snapshots/pair.json")
    assert recovery._workspace_relative(path) == str(path)
    with pytest.raises(ValueError, match="escapes workspace"):
        recovery._workspace_relative(Path("../outside.json"))


def test_ranked_candidates_reject_same_place_wrong_topic(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(recovery, "ROOT", tmp_path)
    snapshot = {
        "snapshot_id": "snapshot_example",
        "source": {"revisions": {"List of power stations in Sweden": 1}},
        "documents": [
            {
                "title": "List of power stations in Sweden",
                "text": "## Stations\nName | Capacity\nAster | 10\nBirch | 20\n",
            }
        ],
    }
    raw = json.dumps(snapshot).encode()
    (tmp_path / "anchor.json").write_bytes(raw)
    source = {
        "topic": "power_stations",
        "snapshot": {"path": "anchor.json", "sha256": hashlib.sha256(raw).hexdigest()},
    }
    titles = [
        "List of Swedish bandy champions",
        "List of power stations in Sweden",
        "List of power stations in Norway",
    ]
    response = {"query": {"search": [{"title": title} for title in titles]}}
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    encoded = json.dumps(response).encode()
    path = search_dir / f"seed_1_{hashlib.sha256(b'topic').hexdigest()[:12]}.json"
    path.write_bytes(encoded)
    seed = {
        "seed": "seed_1",
        "searched_entities": [
            {
                "entity": "topic",
                "titles": titles,
                "response_sha256": hashlib.sha256(encoded).hexdigest(),
            }
        ],
        "previewed": [],
    }
    selected, ledger = recovery._ranked_candidates(
        seed,
        source,
        tmp_path,
        {"list of power stations in sweden"},
        4,
    )
    assert [row["title"] for row in selected] == ["List of power stations in Norway"]
    assert (
        next(
            row for row in ledger if row["title"] == "List of Swedish bandy champions"
        )["status"]
        == "topic_mismatch"
    )
