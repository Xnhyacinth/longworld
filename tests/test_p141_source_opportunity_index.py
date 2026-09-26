"""Regression checks for frozen real-source opportunity accounting."""

import copy
import json
from pathlib import Path

import pytest

from scripts import p141_source_opportunity_index as index

CONFIG = index.ROOT / "configs/p141_source_opportunity_v1.json"
OUTPUT = index.ROOT / "data/candidates/p141_source_opportunity_v1"


def test_frozen_campaign_replays_and_keeps_shape_separate_from_qa() -> None:
    report = index.run(CONFIG, OUTPUT, verify_only=True)
    assert report["source_units"] == 170
    assert report["by_source_kind"] == {
        "real_finance": 3,
        "real_paper_tex": 23,
        "real_wiki_html": 144,
    }
    assert report["rejection_reasons"]["no_parser_accepted_html_grid"] == 51
    assert report["batch_proposal"][2]["p139_selected_tasks"] == 1
    assert report["train_ready"] is False
    rows = index._jsonl(OUTPUT / "opportunities.jsonl")
    assert all("gold" not in row and "reader" not in row for row in rows)
    assert all("source_pins" in row and row["admission_status"] for row in rows)


def test_grid_shape_corruption_and_pin_drift_are_rejected(tmp_path: Path) -> None:
    original = json.loads(
        (
            index.ROOT
            / "data/candidates/p131_wiki_html_campaign_v1/chunks/chunk00/grid/valid_grids.jsonl"
        )
        .open()
        .readline()
    )
    source = index._wiki_sources(
        json.loads(
            (
                index.ROOT
                / "data/candidates/p119_wiki_structural_intake_v2/source_pool.json"
            ).read_text()
        )
    )[original["title"]]
    broken = copy.deepcopy(original)
    broken["grid"]["rows"][broken["grid"]["header_rows"]][0] = "missing-origin"
    with pytest.raises(ValueError, match="origin missing"):
        index._grid_opportunity(broken, source, tmp_path / "grid.jsonl")
    with pytest.raises(ValueError, match="source pin drift"):
        index._pin(
            {"path": "configs/p141_source_opportunity_v1.json", "sha256": "0" * 64}
        )


def test_output_byte_mutation_fails_verify_only(tmp_path: Path) -> None:
    target = tmp_path / "opportunities"
    target.mkdir()
    for name, data in index.build(CONFIG).items():
        (target / name).write_bytes(data)
    index.run(CONFIG, target, verify_only=True)
    with (target / "report.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="replay drift"):
        index.run(CONFIG, target, verify_only=True)
