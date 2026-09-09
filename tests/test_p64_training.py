"""P64 handoff boundaries with small projections, never fixture training inventory."""

import json
import os
import threading
import time
from pathlib import Path

import pytest
import yaml

from longworld.core.attestation import attach_attestation
from scripts import prepare_p64_training as prep


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prep._canonical(value) + "\n")
    return path


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_REPORT_ATTESTATION_KEY", "r" * 64)
    monkeypatch.setenv(
        "LONGWORLD_REPORT_ATTESTATION_KEY_ID", "probe-report-p64-training-test"
    )
    monkeypatch.setattr(prep, "tokenizer_digest", lambda: "a" * 64)
    worlds = []
    inputs = []
    for split, group in [("train", "0000000001"), ("eval", "0000000002")]:
        root = tmp_path / ("finance-" + split)
        identity = "finance-" + split
        task = {
            "schema_version": "longworld.finance-taskbank-long-sample.v1",
            "sample_id": identity,
            "semantic_task_id": identity,
            "variant_family_id": "variants:" + identity,
            "variant": "full",
            "split": split,
            "split_group_id": group,
        }
        sample = {
            "sample_id": identity,
            "messages": [
                {"role": "user", "content": "Complete filing and question " + split},
                {"role": "assistant", "content": '{"value":12}'},
            ],
        }
        inputs += [
            write(root / "tasks.jsonl", task),
            write(root / "sft_candidates.jsonl", sample),
        ]
        worlds.append(
            {
                "domain": "finance",
                "name": identity,
                "root": root,
                "config": {"split": split, "split_group_id": group},
                "receipt": {"accepted_semantic_tasks": 1},
                "job": {},
            }
        )
    root = tmp_path / "codeforge"
    metadata = []
    groups = {}
    for split in prep.SPLITS:
        group = "https://github.com/example/" + split
        groups[group] = split
        identity = "codeforge-" + split
        inputs.append(
            write(
                root / (split + ".jsonl"),
                {
                    "messages": [
                        {"role": "user", "content": "Exact source records " + split},
                        {"role": "assistant", "content": '{"record_id":"r1"}'},
                    ]
                },
            )
        )
        metadata.append(
            {
                "source_group_id": group,
                "split": split,
                "output_file": split + ".jsonl",
                "classification": "long",
                "row_index": 0,
                "semantic_task_id": identity,
                "sample_id": identity,
            }
        )
    path = root / "metadata.jsonl"
    path.write_text("".join(prep._canonical(m) + "\n" for m in metadata))
    inputs.append(path)
    worlds.append(
        {
            "domain": "codeforge",
            "name": "codeforge",
            "root": root,
            "config": {"groups": groups},
            "receipt": {},
            "job": {},
        }
    )

    def collect(*args):
        return worlds, sorted(
            (prep._binding(p) for p in inputs), key=lambda b: b["path"]
        )

    monkeypatch.setattr(prep, "collect", collect)

    def source(world, logs):
        assert "LONGWORLD_REPORT_ATTESTATION_KEY" not in os.environ
        return {"status": "PASS", "name": world["name"], "domain": world["domain"]}

    monkeypatch.setattr(prep, "validate_source", source)

    def load(*args):
        assert "LONGWORLD_REPORT_ATTESTATION_KEY" not in os.environ
        return object()

    monkeypatch.setattr(prep, "_load_tokenizer", load)

    def count(*args):
        assert "LONGWORLD_REPORT_ATTESTATION_KEY" not in os.environ
        return 100

    monkeypatch.setattr(prep, "_count_messages", count)
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(yaml.safe_dump(prep.RECIPE, sort_keys=False))
    monkeypatch.setattr(prep, "code_bindings", lambda p: [prep._binding(p)])
    args = [
        tmp_path / "finance.json",
        tmp_path / "finance",
        tmp_path / "code.json",
        tmp_path / "code",
        tmp_path / "prepared",
        recipe,
    ]
    return args, worlds


def test_separate_and_combined_are_same_primary_inventory(handoff, tmp_path):
    args, worlds = handoff
    m = prep.prepare(*args, workers=2)
    assert m["counts"] == {
        "finance": {"train": 1, "eval": 1},
        "codeforge": {"train": 1, "eval": 1},
    }
    assert m["canonical_tasks"] == m["samples"] == 4
    assert m["combined_counts"] == {"train": 2, "eval": 2}
    result = prep.validate(
        args[4] / prep.MANIFEST, snapshot_root=tmp_path / "snapshots"
    )
    snapshot = Path(result["snapshot_dir"])
    assert snapshot.stat().st_mode & 0o777 == 0o500
    assert (snapshot / "codeforge_train.jsonl").stat().st_mode & 0o777 == 0o400
    original = json.loads((worlds[-1]["root"] / "train.jsonl").read_text())
    copied = json.loads((snapshot / "codeforge_train.jsonl").read_text())
    assert copied["messages"] == original["messages"]
    assert result["production_eligible"] is False


def test_duplicate_semantic_tasks_cannot_be_views(handoff):
    args, worlds = handoff
    path = worlds[1]["root"] / "tasks.jsonl"
    task = json.loads(path.read_text())
    task["semantic_task_id"] = "finance-train"
    task["variant_family_id"] = "variants:finance-train"
    write(path, task)
    with pytest.raises(ValueError, match="duplicate canonical"):
        prep.prepare(*args)


def test_codeforge_repository_split_mismatch_rejected(handoff):
    args, worlds = handoff
    worlds[-1]["config"]["groups"]["https://github.com/example/train"] = "eval"
    with pytest.raises(ValueError, match="repository split"):
        prep.prepare(*args)


def test_overlength_never_truncates(handoff, monkeypatch):
    args, _ = handoff
    monkeypatch.setattr(prep, "_count_messages", lambda *a: 262145)
    with pytest.raises(ValueError, match="truncation forbidden"):
        prep.prepare(*args)
    assert not (args[4] / prep.MANIFEST).exists()


def resign(path, manifest):
    signed = attach_attestation(
        manifest, prep._report_key(), purpose="training_export_manifest"
    )
    write(path, signed)


def test_resigned_projection_change_rejected(handoff):
    args, _ = handoff
    m = prep.prepare(*args)
    root = args[4]
    output = root / "finance_train.jsonl"
    sample = json.loads(output.read_text())
    sample["messages"][-1]["content"] = "forged"
    write(output, sample)
    for entry in m["outputs"]:
        if entry["path"] == output.name:
            entry.update(sha256=prep._digest(output), bytes=output.stat().st_size)
    resign(root / prep.MANIFEST, m)
    with pytest.raises(ValueError, match="exact source projection"):
        prep.validate(root / prep.MANIFEST)


def test_resigned_index_identity_change_rejected(handoff):
    args, _ = handoff
    m = prep.prepare(*args)
    root = args[4]
    p = root / "sample_index.jsonl"
    lines = p.read_text().splitlines()
    row = json.loads(lines[0])
    row["semantic_task_id"] = "invented"
    lines[0] = prep._canonical(row)
    p.write_text("\n".join(lines) + "\n")
    for e in m["outputs"]:
        if e["path"] == p.name:
            e.update(sha256=prep._digest(p), bytes=p.stat().st_size)
    resign(root / prep.MANIFEST, m)
    with pytest.raises(ValueError, match="canonical source"):
        prep.validate(root / prep.MANIFEST)


def test_report_cannot_claim_unverified_strict_dependency(handoff):
    args, _ = handoff
    m = prep.prepare(*args)
    m["strict_long_dependency_verified"] = True
    resign(args[4] / prep.MANIFEST, m)
    with pytest.raises(ValueError, match="invalid signed"):
        prep.validate(args[4] / prep.MANIFEST)


def test_tokenizer_constructors_are_serial(handoff, monkeypatch):
    args, _ = handoff
    lock = threading.Lock()

    def load(*a):
        assert lock.acquire(blocking=False)
        try:
            time.sleep(0.03)
            return object()
        finally:
            lock.release()

    monkeypatch.setattr(prep, "_load_tokenizer", load)
    prep.prepare(*args, workers=4)


def test_validator_registry_rejects_arbitrary_command(tmp_path):
    with pytest.raises(ValueError, match="unsupported domain"):
        prep.validate_source({"domain": "shell", "command": "echo bad"}, tmp_path)


def test_recipe_cannot_subset_or_truncate(tmp_path):
    path = tmp_path / "recipe.yaml"
    path.write_text(yaml.safe_dump({**prep.RECIPE, "max_samples": 1}))
    with pytest.raises(ValueError, match="recipe"):
        prep.recipe(path)


def test_overflow_metadata_is_not_an_extra_training_view(handoff):
    args, worlds = handoff
    p = worlds[-1]["root"] / "metadata.jsonl"
    with p.open("a") as handle:
        for i in range(2):
            handle.write(
                prep._canonical(
                    {
                        "output_file": None,
                        "row_index": None,
                        "classification": "rejected_overflow",
                        "sample_id": f"overflow-{i}",
                    }
                )
                + "\n"
            )
    m = prep.prepare(*args)
    assert m["samples"] == 4
    assert m["combined_counts"] == {"train": 2, "eval": 2}


def test_changed_upstream_input_blocks_snapshot(handoff):
    args, worlds = handoff
    prep.prepare(*args)
    (worlds[0]["root"] / "sft_candidates.jsonl").write_text("tampered\n")
    with pytest.raises(ValueError, match="bindings changed"):
        prep.validate(args[4] / prep.MANIFEST)


def test_sanctioned_data_mount_supports_prepare_and_validate(
    handoff, tmp_path, monkeypatch
):
    args, _ = handoff
    root = tmp_path / "project"
    root.mkdir()
    mounted = tmp_path / "mounted-data"
    mounted.mkdir()
    (root / "data").symlink_to(mounted, target_is_directory=True)
    monkeypatch.setattr(prep, "ROOT", root)
    args[4] = root / "data" / "prepared"
    prep.prepare(*args)
    assert prep.validate(args[4] / prep.MANIFEST)["ok"]


def test_data_mount_does_not_allow_descendant_symlink(handoff, tmp_path, monkeypatch):
    args, _ = handoff
    root = tmp_path / "project"
    root.mkdir()
    mounted = tmp_path / "mounted-data"
    mounted.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "data").symlink_to(mounted, target_is_directory=True)
    (mounted / "redirect").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(prep, "ROOT", root)
    args[4] = root / "data" / "redirect" / "prepared"
    with pytest.raises(ValueError, match="descendant symlink"):
        prep.prepare(*args)
    assert not (outside / "prepared").exists()
