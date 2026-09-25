"""P86 batch freeze and receipt checks for accepted shared worlds."""

import json

import pytest

from longworld.synthesis import p86_state_shared_world as shared
from scripts import run_p86_state_shared_batch as runner


def _config() -> dict:
    return {
        "schema_version": runner.SCHEMA,
        "seed_base": 861000,
        "cells": [{"length_records": 200, "depth": 1, "worlds": 1}],
        "consumed_records": 10,
        "variants": 1,
        "max_full_chat_tokens": 262144,
    }


def test_plan_has_distinct_world_seeds_and_rejects_invalid_cell():
    config = _config()
    config["cells"][0]["worlds"] = 3
    jobs = runner.plan(config)
    assert [job["seed"] for job in jobs] == [861000, 861001, 861002]
    config["cells"][0]["depth"] = 0
    with pytest.raises(ValueError, match="invalid P86 cell"):
        runner.plan(config)


def test_hash_bound_reader_and_manifest_are_required(tmp_path, monkeypatch):
    config = _config()
    (tmp_path / "plan.json").write_text(json.dumps(config))
    job = runner.plan(config)[0]
    world = shared.build_world(job["seed"], 200, 10, 1, 1)
    monkeypatch.setattr(
        runner,
        "_reader_rows",
        lambda current, tokenizer, maximum: [
            {
                "operation": task["operation"],
                "split": "eval",
                "full_chat_tokens": 20000,
                "supervised_tokens": 10,
                "required_fact_span_tokens": 1000,
                "source_group": current["world_id"],
                "semantic_task_id": current["world_id"] + ":" + task["task_id"],
            }
            for task in current["tasks"]
        ],
    )
    runner._write_result(tmp_path, job, world, None, None, 262144)
    with pytest.raises(ValueError, match="manifest is missing"):
        runner.verify(tmp_path, config, None, require_manifest=True)
    manifest = runner.verify(tmp_path, config, None, require_manifest=False)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert runner.verify(tmp_path, config, None, require_manifest=True) == manifest
    receipt_path = tmp_path / "shards" / job["job_id"] / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt_path.write_text(json.dumps({**receipt, "temporal_contrast_checks": 0}))
    with pytest.raises(ValueError, match="receipt disagrees"):
        runner.verify(tmp_path, config, None, require_manifest=True)
    receipt_path.write_text(json.dumps(receipt))
    rows_path = tmp_path / "shards" / job["job_id"] / "rows.jsonl"
    rows_path.write_text(rows_path.read_text() + "\n")
    with pytest.raises(ValueError, match="hash or job mismatch"):
        runner.verify(tmp_path, config, None, require_manifest=True)
