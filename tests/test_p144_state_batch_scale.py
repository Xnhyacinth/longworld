"""Batched state scaling keeps seed windows and source identities disjoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p144_state_batch_scale as scale


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scale._dump(value))


def test_plan_derives_disjoint_seed_windows_from_pinned_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scale, "ROOT", tmp_path)
    template_path = tmp_path / "template.json"
    prior_path = tmp_path / "prior.json"
    _write(
        template_path,
        {
            "schema_version": "longworld.p133-state-mechanism-batch.v1",
            "mechanisms": ["partial_reversal", "authorization_hold"],
            "length_records": [400, 800],
            "prior_world_sets": [],
            "seed_base": 1331000,
        },
    )
    _write(prior_path, {"schema_version": "longworld.p133-state-mechanism-output.v1"})
    config = {
        "schema_version": scale.SCHEMA,
        "template": {"path": "template.json", "sha256": scale._sha(template_path)},
        "prior_campaign": {"path": "prior.json", "sha256": scale._sha(prior_path)},
        "seed_base": 1440000,
        "seeds_per_cell": 32,
        "batch_count": 3,
        "batch_workers": 2,
    }
    batches, pins = scale._plan(config)
    assert [item["seed_base"] for item in batches] == [1440000, 1440128, 1440256]
    assert all(item["prior_world_sets"] == [config["prior_campaign"]] for item in batches)
    assert pins["seed_stride"] == 128
    with pytest.raises(ValueError, match="source pin differs"):
        scale._plan({**config, "template": {"path": "template.json", "sha256": "bad"}})


def _batch(output: Path, index: int, seed: int, base_hash: str) -> dict:
    campaign = output / f"batch_{index:03d}" / "campaign"
    world_path = campaign / "worlds" / f"world-{index}" / "world.json"
    _write(
        world_path,
        {
            "seed": seed,
            "world_id": f"world-{index}",
            "base_record_context_sha256": base_hash,
        },
    )
    _write(
        campaign / "manifest.json",
        {
            "world_files_sha256": {
                str(world_path.relative_to(campaign)): scale._sha(world_path)
            }
        },
    )
    reader = output / f"batch_{index:03d}" / "reader" / "sample_index.jsonl"
    reader.parent.mkdir(parents=True, exist_ok=True)
    reader.write_text(json.dumps({"sample_id": f"sample-{index}"}) + "\n")
    return {"batch": index, "gross_source_worlds": 1, "qa_views": 1}


def test_cross_batch_audit_rejects_duplicate_seed_or_base_context(tmp_path: Path) -> None:
    first = _batch(tmp_path, 0, 10, "base-a")
    second = _batch(tmp_path, 1, 11, "base-b")
    assert scale._audit_worlds(tmp_path, [first, second]) == {
        "gross_worlds": 2,
        "unique_seeds": 2,
        "unique_base_record_contexts": 2,
        "unique_reader_tasks": 2,
    }
    _batch(tmp_path, 1, 10, "base-b")
    with pytest.raises(ValueError, match="source identity repeats"):
        scale._audit_worlds(tmp_path, [first, second])
    _batch(tmp_path, 1, 11, "base-a")
    with pytest.raises(ValueError, match="source identity repeats"):
        scale._audit_worlds(tmp_path, [first, second])


def test_verify_only_does_not_create_missing_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scale, "ROOT", tmp_path)
    batch_dir = tmp_path / "batch_000"
    batch_dir.mkdir()
    config = {"seed_base": 1440000}
    (batch_dir / "campaign_config.json").write_text(scale._dump(config))
    monkeypatch.setattr(
        scale, "_run_command", lambda *_args: pytest.fail("verify-only tried to build")
    )
    with pytest.raises(ValueError, match="campaign missing"):
        scale._run_batch(0, config, tmp_path, verify_only=True)
