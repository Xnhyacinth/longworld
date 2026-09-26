import json
from pathlib import Path

import pytest

from scripts.p113_paper_shape import ROOT, _pin, build, run


def test_calibrated_frozen_source_screen_replays_without_creating_tasks():
    config = ROOT / "configs/p113_paper_shape_v1.json"
    output = ROOT / "data/capability_records/p113_paper_shape_v2"
    manifest = run(config, output, verify_only=True)
    assert manifest["frozen_works"] == 28
    assert manifest["positive_and_admitted"] == 5
    assert manifest["positive_unadmitted"] == 0
    assert manifest["negative_but_admitted"] == 0
    assert manifest["negative_and_unadmitted"] == 23
    assert manifest["train_ready"] is False
    works = [
        json.loads(line)
        for line in (output / "work_matrix.jsonl").read_text().splitlines()
    ]
    assert {
        row["work_id"]
        for row in works
        if row["cohort"] == "p110" and row["source_shape_positive"]
    } == set()
    assert (
        len(build(config)["target_ledger.jsonl"].splitlines())
        == manifest["discovered_links"]
    )


def test_source_pin_rejects_drift_and_unsafe_paths(tmp_path: Path):
    path = tmp_path / "source.jsonl"
    path.write_text("{}\n")
    with pytest.raises(ValueError, match="workspace-relative"):
        _pin({"path": str(path), "sha256": "0" * 64})
    with pytest.raises(ValueError, match="pin drift"):
        _pin({"path": "configs/p113_paper_shape_v1.json", "sha256": "0" * 64})
