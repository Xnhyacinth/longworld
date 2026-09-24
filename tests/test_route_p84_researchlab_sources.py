"""Signed arXiv source routing preserves revision and split boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import route_p84_researchlab_sources as route

CONFIG = route.ROOT / "configs/p84_researchlab_source_route_v1.json"


def _entries() -> list[dict]:
    if not CONFIG.is_file():
        pytest.skip("P84 source route config is unavailable")
    return json.loads(CONFIG.read_text())["families"]


def test_signed_aevb_inventory_routes_all_eleven_versions() -> None:
    family = route._family(_entries()[1], set())
    assert family["family_id"] == "aevb-arxiv-1312.6114"
    assert family["split"] == "train"
    assert [item["version"] for item in family["sources"]] == [
        f"v{version}" for version in range(1, 12)
    ]
    assert all(
        route._sha(route._path(item["path"])) == item["sha256"]
        for item in family["sources"]
    )


def test_prior_work_id_excludes_same_paper_even_under_new_family_label() -> None:
    with pytest.raises(ValueError, match="overlaps prior bank"):
        route._family(_entries()[1], {"1312.6114"})


def test_wrong_prior_config_pin_fails_before_output(tmp_path: Path) -> None:
    config = json.loads(CONFIG.read_text())
    config["prior_bank_config"]["sha256"] = "0" * 64
    path = tmp_path / "wrong-pin.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="pinned file changed"):
        route.route(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()
