from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from longworld.core import taskbank_training as tt


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def bank(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(tt, "ROOT", root)
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_REPORT_ATTESTATION_KEY", "r" * 64)
    monkeypatch.setenv(
        "LONGWORLD_REPORT_ATTESTATION_KEY_ID", "probe-report-taskbank-training-test"
    )
    monkeypatch.setattr(
        tt,
        "_validate_source_world",
        lambda *args: {"status": "PASS", "semantic_tasks": 1},
    )

    def tokenizer(*args):
        assert "LONGWORLD_REPORT_ATTESTATION_KEY" not in os.environ
        return object()

    monkeypatch.setattr(tt, "_load_tokenizer", tokenizer)
    monkeypatch.setattr(tt, "_count_messages", lambda messages, tokenizer, cutoff: 100)
    batch = root / "batch"
    jobs, receipt_jobs = [], []
    for name, group, split in [
        ("dev", "0000000001", "train"),
        ("held", "0000000002", "eval"),
    ]:
        source = root / f"{name}.source.json"
        source_sha = write(source, {"issuer": {"cik": group}})
        config = root / f"{name}.config.json"
        config_sha = write(
            config,
            {
                "schema_version": "longworld.finance-taskbank-build.v1",
                "source_manifest": source.name,
                "split_group_id": group,
                "split": split,
                "holds": {},
                "excluded_split_group_ids": [
                    "0000000002" if split == "train" else "0000000001"
                ],
                "tokenizer": {
                    "model_id": "Qwen/Qwen3.5-4B",
                    "revision": tt.TOKENIZER_REVISION,
                },
            },
        )
        task = {
            "schema_version": "longworld.finance-taskbank-sample.v1",
            "sample_id": name,
            "semantic_task_id": "task:" + name,
            "variant_family_id": "variants:task:" + name,
            "variant": "full",
            "split": split,
            "split_group_id": group,
        }
        messages = {
            "sample_id": name,
            "messages": [
                {"role": "user", "content": "real source question"},
                {"role": "assistant", "content": '{"value":1}'},
            ],
        }
        world = batch / name
        task_sha = write(world / "tasks.jsonl", task)
        sft_sha = write(world / "sft_candidates.jsonl", messages)
        receipt_sha = write(
            world / "BUILD_RECEIPT.json",
            {
                "config_sha256": config_sha,
                "source_manifest": {"sha256": source_sha},
                "split_group_id": group,
                "split": split,
                "accepted_semantic_tasks": 1,
                "tokenizer": {
                    "model_id": "Qwen/Qwen3.5-4B",
                    "revision": tt.TOKENIZER_REVISION,
                    "asset_manifest_sha256": "a" * 64,
                },
                "files": {"tasks.jsonl": task_sha, "sft_candidates.jsonl": sft_sha},
                "code_sha256": {},
            },
        )
        jobs.append(
            {
                "issuer": name,
                "config": config.name,
                "config_sha256": config_sha,
                "source_manifest": source.name,
                "source_manifest_sha256": source_sha,
                "split_group_id": group,
                "split": split,
                "trust_file": str(root / "private.json"),
            }
        )
        receipt_jobs.append(
            {
                "name": name,
                "status": "verified_local_candidates",
                "receipt_sha256": receipt_sha,
                "semantic_tasks": 1,
                "split": split,
                "split_group_id": group,
            }
        )
    catalog = root / "catalog.json"
    catalog_sha = write(
        catalog,
        {
            "schema_version": "longworld.p63-finance-taskbank-source-catalog.v1",
            "jobs": jobs,
            "legacy_union_allowed": False,
        },
    )
    write(
        batch / "BATCH_RECEIPT.json",
        {
            "schema_version": "longworld.finance-taskbank-batch-receipt.v1",
            "catalog_sha256": catalog_sha,
            "jobs": receipt_jobs,
            "production_eligible": False,
            "training_release_eligible": False,
        },
    )
    recipe = root / "TASKBANK.yaml"
    recipe.write_text(tt.RECIPE_TEXT)
    return root, catalog, batch, recipe


def test_prepare_and_private_snapshot_preserve_messages_and_split(bank, tmp_path):
    root, catalog, batch, recipe = bank
    manifest = tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)
    result = tt.validate_training_inputs(
        root / "prepared/TASKBANK_TRAINING_MANIFEST.json",
        snapshot_root=tmp_path / "snapshots",
    )
    assert manifest["local_training_eligible"] is True
    assert manifest["production_eligible"] is False
    snap = Path(result["snapshot_dir"])
    assert (snap / "train.jsonl").stat().st_mode & 0o777 == 0o400
    assert snap.stat().st_mode & 0o777 == 0o500
    assert json.loads((snap / "train.jsonl").read_text())["sample_id"] == "dev"
    assert json.loads((snap / "eval.jsonl").read_text())["sample_id"] == "held"


def test_output_tamper_fails_before_snapshot(bank, tmp_path):
    root, catalog, batch, recipe = bank
    tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)
    (root / "prepared/train.jsonl").write_text("forged\n")
    with pytest.raises(ValueError, match="hash|binding|changed"):
        tt.validate_training_inputs(
            root / "prepared/TASKBANK_TRAINING_MANIFEST.json",
            snapshot_root=tmp_path / "snapshots",
        )
    assert not (tmp_path / "snapshots").exists()


def test_source_member_symlink_is_rejected(bank, tmp_path):
    root, catalog, batch, recipe = bank
    member = batch / "dev/sft_candidates.jsonl"
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_bytes(member.read_bytes())
    member.unlink()
    member.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink|regular"):
        tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)


def test_legacy_row_cannot_enter_taskbank_adapter(bank):
    root, catalog, batch, recipe = bank
    task = batch / "dev/tasks.jsonl"
    data = json.loads(task.read_text())
    data["schema_version"] = "p3.0"
    digest = write(task, data)
    receipt = batch / "dev/BUILD_RECEIPT.json"
    r = json.loads(receipt.read_text())
    r["files"]["tasks.jsonl"] = digest
    rs = write(receipt, r)
    outer = batch / "BATCH_RECEIPT.json"
    b = json.loads(outer.read_text())
    b["jobs"][0]["receipt_sha256"] = rs
    write(outer, b)
    with pytest.raises(ValueError, match="taskbank|legacy|schema"):
        tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)


def test_source_or_config_changed_after_prepare_fails(bank):
    root, catalog, batch, recipe = bank
    tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)
    (root / "dev.source.json").write_text("changed")
    with pytest.raises(ValueError, match="hash|binding|changed"):
        tt.validate_training_inputs(root / "prepared/TASKBANK_TRAINING_MANIFEST.json")


def test_recipe_cannot_silently_subset_prepared_data(bank):
    root, catalog, batch, recipe = bank
    recipe.write_text(recipe.read_text() + "max_samples: 1\n")
    with pytest.raises(ValueError, match="unsupported|recipe"):
        tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)


def test_tokenizer_construction_does_not_overlap(bank, monkeypatch):
    import threading
    import time

    root, catalog, batch, recipe = bank
    constructing = threading.Lock()

    def load(*args):
        assert constructing.acquire(blocking=False), "concurrent lazy import"
        try:
            time.sleep(0.05)
            return object()
        finally:
            constructing.release()

    monkeypatch.setattr(tt, "_load_tokenizer", load)
    tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)
