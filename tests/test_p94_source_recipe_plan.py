import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis import p94_source_recipe_plan as planner


def _write(root: Path, relative: str, value: dict) -> dict:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _fixture(tmp_path: Path, monkeypatch) -> tuple[dict, dict, list]:
    prior = _write(tmp_path, "prior.json", {})
    snapshot = _write(tmp_path, "wiki.json", {"snapshot_id": "wiki_one"})
    wiki = {
        "source_kind": "real_wiki",
        "world_group_id": "wiki_one",
        "split": "train",
        "domain_topics": [{"domain": "history", "topic": "bridges"}],
        "source_pins": [snapshot],
        "source_shape": {"table_columns": ["Name", "Opened"]},
        "document_identities": [{"title": "List of bridges"}],
        "cells": [
            {
                "operation": "wiki_table_lookup",
                "capability": "L1",
                "status": "probe_supported",
            },
            {
                "operation": "wiki_table_pair",
                "capability": "L2",
                "status": "unsupported",
                "reason": "no pair",
            },
        ],
    }
    finance = {
        "source_kind": "real_finance",
        "world_group_id": "issuer:one",
        "split": "eval",
        "domain_topics": [{"domain": "finance", "topic": "issuer_one"}],
        "source_pins": [prior],
        "cells": [
            {
                "operation": "aggregate",
                "capability": "L2",
                "status": "observed_candidate",
            }
        ],
    }
    matrix = {
        "schema": planner.MATRIX_SCHEMA,
        "input_sha256": {prior["path"]: prior["sha256"]},
        "sources": [finance, wiki],
    }
    pool = {
        "schema": planner.NATIVE_POOL_SCHEMA,
        "prior_source_manifest": prior,
        "sources": [
            {
                "name": "wiki_one",
                "snapshot": snapshot,
                "domain": "history",
                "topic": "bridges",
                "split": "train",
            }
        ],
    }
    job = {
        "name": "wiki_one_lookup",
        "recipe": "wiki_table_lookup",
        "snapshot": {**snapshot, "snapshot_id": "wiki_one"},
        "domain": "history",
        "topic": "bridges",
        "split": "train",
        "length_policy": "native_whole_pages",
    }
    monkeypatch.setattr(planner, "_native_jobs", lambda *_: [job])
    config = {
        "schema": planner.SCHEMA,
        "source_matrix": _write(tmp_path, "matrix.json", matrix),
        "native_source_pool": _write(tmp_path, "pool.json", pool),
        "length_policies": ["native_whole_pages", "target_32768"],
    }
    return config, matrix, [job]


def test_legal_cells_and_unavailable_lanes_are_separate(tmp_path, monkeypatch):
    config, _, jobs = _fixture(tmp_path, monkeypatch)
    plan = planner.compile_plan(config, root=tmp_path)
    assert plan["native_job_config"]["jobs"] == jobs
    assert plan["native_jobs"] == 1
    assert plan["potential_unwired_recipes"] == [
        {
            "world_group_id": "wiki_one",
            "operation": "generic_year_table_interval",
            "status": "unprobed_no_native_batch_compiler",
            "basis": "column names in source shape; complete table not established",
            "year_columns": ["Opened"],
        }
    ]
    assert plan["cell_status_counts"] == {
        "executable_native_job": 1,
        "observed_only_no_native_batch_compiler": 2,
        "unsupported_length_policy": 1,
        "unsupported_source_structure": 2,
    }
    supported = next(
        cell for cell in plan["cells"] if cell["status"] == "executable_native_job"
    )
    assert supported["world_group_id"] == "wiki_one"
    assert supported["operation"] == "wiki_table_lookup"
    assert len(supported["native_job_ids"]) == 1
    assert plan == planner.compile_plan(config, root=tmp_path)


def test_changed_frozen_matrix_is_rejected(tmp_path, monkeypatch):
    config, matrix, _ = _fixture(tmp_path, monkeypatch)
    matrix["sources"][0]["split"] = "train"
    (tmp_path / "matrix.json").write_text(json.dumps(matrix))
    with pytest.raises(ValueError, match="input pin mismatch"):
        planner.compile_plan(config, root=tmp_path)


def test_cross_split_shared_page_is_rejected(tmp_path, monkeypatch):
    config, matrix, _ = _fixture(tmp_path, monkeypatch)
    other = {**matrix["sources"][1]}
    other["world_group_id"] = "wiki_two"
    other["split"] = "eval"
    other["cells"] = []
    matrix["sources"].append(other)
    config["source_matrix"] = _write(tmp_path, "matrix.json", matrix)
    with pytest.raises(ValueError, match="same Wiki title"):
        planner.compile_plan(config, root=tmp_path)


def test_unsupported_requested_length_policy_fails(tmp_path, monkeypatch):
    config, _, _ = _fixture(tmp_path, monkeypatch)
    config["length_policies"] = ["target_12345"]
    with pytest.raises(ValueError, match="length policies"):
        planner.compile_plan(config, root=tmp_path)


def test_native_job_requires_positive_matrix_probe(tmp_path, monkeypatch):
    config, matrix, _ = _fixture(tmp_path, monkeypatch)
    matrix["sources"][1]["cells"][0]["status"] = "unsupported"
    config["source_matrix"] = _write(tmp_path, "matrix.json", matrix)
    with pytest.raises(ValueError, match="lacks positive matrix probe"):
        planner.compile_plan(config, root=tmp_path)


def test_prior_matrix_limits_incremental_jobs_without_dropping_full_plan(
    tmp_path, monkeypatch
):
    config, matrix, jobs = _fixture(tmp_path, monkeypatch)
    config["prior_source_matrix"] = _write(tmp_path, "older.json", matrix)
    plan = planner.compile_plan(config, root=tmp_path)
    assert plan["native_job_config"]["jobs"] == jobs
    assert plan["new_native_job_config"]["jobs"] == []
    assert plan["native_jobs"] == 1
    assert plan["new_native_jobs"] == 0


def test_prior_world_group_cannot_change_source_pin(tmp_path, monkeypatch):
    config, matrix, _ = _fixture(tmp_path, monkeypatch)
    config["prior_source_matrix"] = _write(tmp_path, "older.json", matrix)
    matrix["sources"][1]["source_pins"] = [{"path": "other", "sha256": "0" * 64}]
    config["source_matrix"] = _write(tmp_path, "matrix.json", matrix)
    with pytest.raises(ValueError, match="prior world identity changed"):
        planner.compile_plan(config, root=tmp_path)
