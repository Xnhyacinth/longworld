"""P99 coordinates pinned P97 intakes without weakening the source gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import run_p99_wiki_campaign as campaign


def _json(path: Path, value: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return {
        "path": str(path.relative_to(campaign.ROOT)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _fixture(tmp_path: Path, monkeypatch) -> tuple[Path, dict]:
    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    base = {
        "schema": "longworld.source-batch-pool.v2",
        "requested_recipes": ["wiki_table_lookup"],
        "max_tasks_by_recipe": {"wiki_table_lookup": 4},
        "prior_source_manifest": {"path": "source", "sha256": "x"},
        "sources": [{"name": "old_base"}],
    }
    prior = {**base, "sources": [{"name": "old_base"}, {"name": "p95_new"}]}
    base_pin = _json(tmp_path / "base.json", base)
    prior_pin = _json(tmp_path / "prior.json", prior)
    router_pin = _json(
        tmp_path / "router.json", {"schema": "longworld.p92-source-router.v1.result"}
    )
    catalog_pin = _json(tmp_path / "catalog.json", {})
    shard = tmp_path / "planned/shards/shard_0001.json"
    shard_pin = _json(shard, {"schema": "longworld.p93-wiki-structural-intake.v1"})
    planned = {
        "catalog_sha256": catalog_pin["sha256"],
        "base_pool": base_pin,
        "shards": [
            {
                "shard": 1,
                "config_path": "shards/shard_0001.json",
                "config_sha256": shard_pin["sha256"],
            }
        ],
    }
    plan_pin = _json(tmp_path / "planned/manifest.json", planned)
    monkeypatch.setattr(campaign, "verify_plan", lambda *_args, **_kwargs: planned)
    config = {
        "schema": campaign.SCHEMA,
        "catalog": catalog_pin,
        "plan_manifest": plan_pin,
        "base_pool": base_pin,
        "dedup_prior_pool": prior_pin,
        "dedup_prior_router": router_pin,
        "shards": [1],
        "intake_workers": 1,
        "task_workers": 1,
    }
    config_path = tmp_path / "campaign.json"
    _json(config_path, config)
    return config_path, config


def test_preflight_keeps_acquisition_base_separate_from_global_dedup_prior(
    tmp_path, monkeypatch
):
    config_path, config = _fixture(tmp_path, monkeypatch)
    actual, _plan, rows = campaign.preflight(config_path)
    assert actual["base_pool"] != actual["dedup_prior_pool"]
    assert rows[0]["shard"] == 1
    config["base_pool"] = config["dedup_prior_pool"]
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="acquisition base"):
        campaign.preflight(config_path)


def test_dedup_prior_must_extend_base_without_changing_sources(tmp_path, monkeypatch):
    config_path, config = _fixture(tmp_path, monkeypatch)
    prior = json.loads((tmp_path / "prior.json").read_text())
    prior["sources"] = [{"name": "p95_new"}]
    config["dedup_prior_pool"] = _json(tmp_path / "prior.json", prior)
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="unchanged acquisition base"):
        campaign.preflight(config_path)


def test_plan_is_replayable_and_never_acquires(tmp_path, monkeypatch):
    config_path, _config = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        campaign,
        "acquire",
        lambda *_args, **_kwargs: pytest.fail("unexpected HTTP acquisition"),
    )
    output = tmp_path / "output"
    first = campaign.run(config_path, output)
    assert campaign.run(config_path, output) == first
    assert first["dedup_prior_pool_sha256"] != first["dedup_prior_router_sha256"]
    with pytest.raises(ValueError, match="no frozen intake"):
        campaign.run(config_path, output, mode="gate")


def test_frozen_intake_receipt_must_match_shard_and_base(tmp_path, monkeypatch):
    _config_path, config = _fixture(tmp_path, monkeypatch)
    intake = tmp_path / "intake"
    _json(
        intake / "source_pool.json",
        {**json.loads((tmp_path / "base.json").read_text()), "sources": []},
    )
    receipt = {
        "config_sha256": "right",
        "base_pool": config["base_pool"],
        "source_groups": 0,
    }
    monkeypatch.setattr(campaign, "verify_intake", lambda *_args: receipt)
    assert campaign._receipt(intake, "right", config["base_pool"]) == receipt
    with pytest.raises(ValueError, match="does not match"):
        campaign._receipt(intake, "wrong", config["base_pool"])
    receipt["base_pool"] = config["dedup_prior_pool"]
    with pytest.raises(ValueError, match="does not match"):
        campaign._receipt(intake, "right", config["base_pool"])


def test_verify_rejects_partial_batch_without_running_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    batch = tmp_path / "batch"
    _json(batch / "result.json", {})
    _json(batch / "merged/manifest.json", {})
    _json(
        batch / "batch/batch_manifest.json", {"job_receipt_sha256": {"job_a": "hash"}}
    )
    with pytest.raises(ValueError, match="missing native job receipt"):
        campaign._complete_batch(batch)
    _json(batch / "batch/jobs/job_a/receipt.json", {})
    campaign._complete_batch(batch)


def test_verify_only_does_not_create_missing_files(tmp_path):
    with pytest.raises(ValueError, match="cannot verify missing"):
        campaign._write(tmp_path / "not-here.json", {}, verify_only=True)
    assert not (tmp_path / "not-here.json").exists()
