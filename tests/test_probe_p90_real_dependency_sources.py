"""Regression checks for the frozen real-source dependency probe."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import probe_p90_real_dependency_sources as probe


def test_frozen_probe_reports_zero_novel_dependency_without_relaxing_joins(
    tmp_path: Path,
) -> None:
    config = probe.ROOT / "configs/p90_real_dependency_probe_v1.json"
    result = probe.run(config, tmp_path / "probe")
    assert result["new_wiki_pages"] == 12
    assert result["new_page_join_pairs"] == 1
    assert result["new_page_pair_statuses"] == {"domain_or_topic_mismatch": 1}
    assert result["new_wiki_typed_pages"] == 5
    assert result["new_wiki_closed_established_tables"] == 0
    assert result["novel_wiki_join_tasks"] == 0
    assert result["new_real_l2_l3_tasks"] == 0
    assert result["paper"]["revision_pairs"] == 20
    assert result["paper"]["existing_admitted_tasks"] == 7
    pages = json.loads((tmp_path / "probe" / "wiki_pages.json").read_text())
    assert len(pages) == 12
    assert len({page["title"] for page in pages}) == 12


def test_wrong_pin_fails_before_creating_output(tmp_path: Path) -> None:
    config = json.loads(
        (probe.ROOT / "configs/p90_real_dependency_probe_v1.json").read_text()
    )
    config["new_wiki_pool"]["sha256"] = "0" * 64
    config_path = tmp_path / "bad.json"
    config_path.write_text(json.dumps(config))
    output = tmp_path / "probe"
    with pytest.raises(ValueError, match="probe input pin changed"):
        probe.run(config_path, output)
    assert not output.exists()
