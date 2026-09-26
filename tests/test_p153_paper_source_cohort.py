"""Behavioral checks for official-source cohort replay and prior-work exclusion."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.p153_paper_reference_batch import build as build_references
from scripts.p153_paper_reference_batch import run as replay_references
from scripts.p153_paper_source_cohort import ROOT, build, run


def test_frozen_cohort_replays_and_excludes_prior_paper_works() -> None:
    config = ROOT / "configs/p153_paper_source_cohort_v1.json"
    output = ROOT / "data/candidates/p153_paper_source_cohort_v1"
    result = run(config, output, verify_only=True)
    assert result["official_atom_pages_replayed"] == 24
    catalog = json.loads((output / "admitted_catalog.json").read_text())
    previous = {
        json.loads(line)["work_id"]
        for line in (
            ROOT / "data/candidates/p127_same_file_paper_reference_v5/support_matrix.jsonl"
        ).read_text().splitlines()
    }
    works = catalog["selected_works"]
    assert len(works) == len({work["work_id"] for work in works})
    assert not previous & {work["work_id"] for work in works}
    assert all(
        work["versions"][-1] == f"v{work['latest_version']}" for work in works
    )
    assert len({work["category_query"] for work in works}) == len(works)


def test_cohort_rejects_drifted_source_pin(tmp_path: Path) -> None:
    config = json.loads((ROOT / "configs/p153_paper_source_cohort_v1.json").read_text())
    config["prior_paper_manifest"]["sha256"] = "0" * 64
    path = tmp_path / "bad_config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="pin drift"):
        build(path, ROOT / "data/candidates/p153_invalid")


def test_reused_paper_compilation_has_no_unseen_tasks() -> None:
    manifest = replay_references(
        ROOT / "configs/p153_paper_reference_reuse_v1.json",
        ROOT / "data/candidates/p153_paper_reference_reuse_v2",
        verify_only=True,
    )
    assert manifest["source_mode"] == "reused_p127_archives"
    assert manifest["source_works"] == 29
    assert manifest["candidate_views"] == 0
    assert manifest["novel_source_groups"] == 0
    assert manifest["train_ready"] is False


def test_new_compiler_rejects_transport_stopped_empty_packet(tmp_path: Path) -> None:
    fetch = ROOT / "data/candidates/p153_paper_source_fetch_v1/manifest.json"
    config = json.loads((ROOT / "configs/p153_paper_reference_reuse_v1.json").read_text())
    del config["prior_paper_manifest"]
    config["fetch_manifest"] = {
        "path": str(fetch.relative_to(ROOT)),
        "sha256": hashlib.sha256(fetch.read_bytes()).hexdigest(),
    }
    path = tmp_path / "transport_stopped.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="nonempty local-only source packet"):
        build_references(path, ROOT / "data/candidates/p153_invalid")
