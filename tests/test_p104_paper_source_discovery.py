"""P104 selects source works from pinned inventory structure, not paper names."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts import p104_paper_quality_gate as quality
from scripts import p104_paper_source_discovery as discovery


def _json(path: Path, value: dict, root: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return {
        "path": str(path.relative_to(root)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_split_is_work_id_stable():
    assert discovery._split("2303.12712") == "train"
    assert discovery._split("2407.21783") == "eval"


def test_inventory_requires_one_authorized_work(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "ROOT", tmp_path)
    path = tmp_path / "data/source_inventory/test/paper_fetch_inventory.json"
    _json(
        path,
        {
            "source_status": "public_api_export",
            "authorization": {"basis": "local research"},
            "records": [{"work_id": "arxiv:2407.21783"}],
        },
        tmp_path,
    )
    row = discovery._inventory_row(path)
    assert row["work_id"] == "2407.21783"
    _json(
        path,
        {
            "source_status": "public_api_export",
            "records": [{"work_id": "arxiv:2407.21783"}],
        },
        tmp_path,
    )
    assert discovery._inventory_row(path)["reason"] == "missing_work_id_or_source_basis"


def test_discovery_deduplicates_catalog_and_excludes_prior_exposure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(discovery, "ROOT", tmp_path)
    monkeypatch.setattr(discovery, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(
        discovery,
        "_inventory",
        lambda entry: {"work_id": entry["family_id"].removeprefix("old-")},
    )
    monkeypatch.setattr(discovery, "_prior", lambda _pin: (set(), set()))

    def fake_probe(row):
        entry = {
            "family_id": "p104-arxiv-" + row["work_id"],
            "split": discovery._split(row["work_id"]),
            "inventory": {"path": row["inventory"], "sha256": row["inventory_sha256"]},
        }
        return {
            **row,
            "split": entry["split"],
            "cross_file_links": 2,
            "status": "task_source_candidate",
        }, entry

    monkeypatch.setattr(discovery, "_probe", fake_probe)
    root = tmp_path / "data/source_inventory"
    for name, work, versions in (
        ("old", "2203.01928", 3),
        ("new_a", "2303.12712", 2),
        ("new_b", "2303.12712", 4),
        ("indexed", "2407.21783", 3),
    ):
        _json(
            root / name / "paper_fetch_inventory.json",
            {
                "source_status": "public_api_export",
                "authorization": {"basis": "local research"},
                "records": [{"work_id": "arxiv:" + work} for _ in range(versions)],
            },
            tmp_path,
        )
    prior_source = _json(
        tmp_path / "configs/prior.json",
        {
            "schema": "longworld.p86-frozen-paper-batch.v1",
            "families": [{"family_id": "old-2203.01928"}],
        },
        tmp_path,
    )
    prior_index = _json(tmp_path / "index/manifest.json", {}, tmp_path)
    (tmp_path / "index/candidate_refs.jsonl").write_text(
        json.dumps({"candidate": {"source_group": "researchlab:arxiv:2407.21783"}})
        + "\n"
    )
    template = _json(
        tmp_path / "configs/template.json",
        {"schema": "longworld.p96-paper-reference-qa.v1", "workers": 3},
        tmp_path,
    )
    config = tmp_path / "configs/p104.json"
    _json(
        config,
        {
            "schema": discovery.SCHEMA + ".config",
            "inventory_root": "data/source_inventory",
            "output_dir": "data/capability_records/p104_test",
            "max_inventory_files": 10,
            "workers": 4,
            "prior_source_config": prior_source,
            "prior_candidate_index": prior_index,
            "qa_template": template,
        },
        tmp_path,
    )
    outputs = discovery.plan(config)
    manifest = json.loads(outputs["manifest.json"])
    assert (
        manifest["inventory_files"],
        manifest["unique_work_ids"],
        manifest["new_work_ids_probed"],
        manifest["task_source_works"],
    ) == (4, 3, 1, 1)
    assert manifest["raw_cross_file_links"] == 2
    assert manifest["rejection_reasons"]["rejected_duplicate_inventory_for_work"] == 1
    selected = json.loads(outputs["source_config.json"])["families"]
    assert len(selected) == 1
    assert selected[0]["inventory"]["path"].endswith("new_b/paper_fetch_inventory.json")
    output = tmp_path / "data/capability_records/p104_test"
    discovery.run(config, output)
    discovery.run(config, output, verify_only=True)


def test_curated_quality_rejects_tex_and_casefold_answer_shortcuts():
    assert (
        quality.quality_status("Answer \\DV", "Answer \\DV")
        == "rejected_unexpanded_tex_command_in_gold"
    )
    assert (
        quality.quality_status(
            "post-trained language model\nPost-trained Language Model",
            "Post-trained Language Model",
        )
        == "rejected_alternate_visible_title_occurrence"
    )
    assert (
        quality.quality_status(
            "Misconceptions and Fact-Checking", "Misconceptions and Fact-Checking"
        )
        == "accepted_raw_tex_reference"
    )


def test_source_path_rejects_parent_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="workspace-relative"):
        discovery._path("../outside")


@pytest.mark.parametrize("name", ["sample_index.jsonl", "audit.jsonl"])
def test_quality_gate_rejects_native_row_tamper(tmp_path, name):
    path = tmp_path / name
    path.write_text('{"sample_id":"frozen"}\n')
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest()}
    quality._verify_files(tmp_path, hashes)
    path.write_text('{"sample_id":"changed"}\n')
    with pytest.raises(ValueError, match="receipt file changed"):
        quality._verify_files(tmp_path, hashes)
