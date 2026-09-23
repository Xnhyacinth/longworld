"""Tests for the P73 C/D arm export (scripts/export_p73_arms.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from longworld.synthesis import capability_shared_world as sw
from scripts.measure_witness_coverage import audit_bank

ROOT = Path(__file__).resolve().parents[1]

# Two records-side worlds with different seeds: one world's rows land at
# fraction 1.0, the other at 0.75 (or a mixed split), so D and C are both
# populated. The F-side would also work but records worlds build fastest.
FAMILY_GROUP = ("filter_aggregate", "join_lookup")
SEEDS = (871001, 871002)


def _tiny_bank(tmp_path: Path) -> Path:
    bank = tmp_path / "p73_tiny"
    for index, seed in enumerate(SEEDS):
        shard = bank / "shards" / f"shared-0-{index}"
        shard.mkdir(parents=True)
        world = sw.build_shared_world(seed, FAMILY_GROUP, 400, 20, 2)
        (shard / "world.json").write_text(json.dumps(world, ensure_ascii=False) + "\n")
    report = audit_bank(bank, None)
    (bank / "verification.witness.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n"
    )
    rows = []
    for index, seed in enumerate(SEEDS):
        world = sw.build_shared_world(seed, FAMILY_GROUP, 400, 20, 2)
        for task in world["tasks"]:
            rows.append(
                {
                    "schema_version": world["schema_version"],
                    "example_id": f"{world['world_id']}:{task['task_id']}",
                    "semantic_task_id": f"{world['world_id']}:{task['task_id']}",
                    "world_id": world["world_id"],
                    "group_id": world["world_id"],
                    "family": task["family"],
                    "context": world["context"],
                    "context_sha256": world["context_sha256"],
                    "question": task["question"],
                    "instruction": task["instruction"],
                    "answer": task["answer"],
                    "consumed": task["consumed"],
                    "consumed_count": task["consumed_count"],
                    "honesty": world["honesty"],
                }
            )
    with (bank / "train.jsonl").open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return bank


def _run_export(bank: Path) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "export_p73_arms.py"),
            "--bank",
            str(bank),
            "--no-measure-tokens",
            "--workers",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads((bank / "arms.json").read_text())


def test_arms_family_quotas_match_and_fields_exist(tmp_path):
    bank = _tiny_bank(tmp_path)
    receipt = _run_export(bank)
    assert receipt["schema_version"] == "longworld.p73-arms.v1"
    assert receipt["threshold"] == 0.5
    for arm in ("c", "d"):
        entry = receipt["arms"][arm]
        for field in (
            "train_rows",
            "train_sha256",
            "index_sha256",
            "worlds",
            "budget_four_caliber",
        ):
            assert field in entry, (arm, field)
        assert entry["budget_four_caliber"]["rows"] == entry["train_rows"]
    for family, matched in receipt["matched"].items():
        assert matched["quota"] == matched["d"]["rows"] == matched["c"]["rows"], family
        assert matched["d"]["rows"] > 0, family
    # D is exactly the rows clearing the threshold (deterministic selection rule)
    details = json.loads((bank / "verification.witness.json").read_text())[
        "example_details"
    ]
    d_index = {
        json.loads(line)["example_id"]
        for line in (bank / "arms" / "d" / "sample_index.jsonl").open()
    }
    expected = {
        row_id
        for row_id, detail in details.items()
        if detail["distinguished_fraction"] >= 0.5
    }
    assert d_index == expected
    # overlap accounting is present and truthful
    assert "overlap" in receipt
    c_index = {
        json.loads(line)["example_id"]
        for line in (bank / "arms" / "c" / "sample_index.jsonl").open()
    }
    assert receipt["overlap"]["rows"] == len(c_index & d_index)
    assert receipt["overlap"]["fraction_of_c"] == round(
        len(c_index & d_index) / len(c_index), 4
    )


def test_arms_export_is_deterministic(tmp_path):
    bank1 = _tiny_bank(tmp_path / "first")
    receipt1 = _run_export(bank1)
    bank2 = _tiny_bank(tmp_path / "second")
    receipt2 = _run_export(bank2)
    for arm in ("c", "d"):
        assert (
            receipt1["arms"][arm]["train_sha256"]
            == receipt2["arms"][arm]["train_sha256"]
        )


def test_arms_rows_carry_messages_and_witness_fraction(tmp_path):
    bank = _tiny_bank(tmp_path)
    receipt = _run_export(bank)
    details = json.loads((bank / "verification.witness.json").read_text())[
        "example_details"
    ]
    for arm in ("c", "d"):
        for line in (bank / "arms" / arm / "train.jsonl").open():
            row = json.loads(line)
            assert row["arm"] == arm
            assert len(row["messages"]) == 2
            assert row["messages"][0]["content"].endswith(row["instruction"])
            assert row["messages"][1]["content"] == json.dumps(
                row["answer"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            assert (
                row["distinguished_fraction"]
                == details[row["example_id"]]["distinguished_fraction"]
            )
