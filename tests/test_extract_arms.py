"""Arm extraction contracts: determinism, world atomicity, paired overlap."""

import json
import subprocess
import sys
from pathlib import Path

from scripts.extract_arms import plan_arms, stratum_key, world_order

ROOT = Path(__file__).resolve().parents[1]

# A tiny synthetic index standing in for a bank: 2 strata x 3 worlds x 2 rows
# (each world lives in exactly one stratum — world atomicity).
WORLDS = [f"world-{i}" for i in range(6)]
INDEX_ROWS = []
for stratum_i, (family, depth, target) in enumerate(
    [("filter_aggregate", 1, 8192), ("alias_locate", 1, 32768)]
):
    for world in WORLDS[stratum_i * 3 : stratum_i * 3 + 3]:
        for row_i in range(2):
            INDEX_ROWS.append(
                {
                    "example_id": f"{world}:q{row_i}",
                    "world_id": world,
                    "family": family,
                    "depth": depth,
                    "token_target": target,
                    "split": "train",
                    "rule_structure_id": None
                    if family != "rule_holdout"
                    else "parity_vote",
                }
            )


def write_bank(tmp_path: Path) -> Path:
    bank = tmp_path / "bank"
    bank.mkdir()
    with (bank / "sample_index.jsonl").open("w") as stream:
        for row in INDEX_ROWS:
            stream.write(json.dumps(row) + "\n")
    (bank / "manifest.json").write_text("{}")
    return bank


def test_world_order_is_deterministic_and_hash_decorrelated():
    stratum = ("filter_aggregate", 1, 8192, "train", "")
    order = world_order(stratum, WORLDS)
    assert world_order(stratum, list(reversed(WORLDS))) == order
    assert sorted(order) == sorted(WORLDS)  # permutation, nothing lost


def test_plan_arms_assigns_worlds_atomically(tmp_path):
    bank = write_bank(tmp_path)
    assignment, stats = plan_arms(bank, {"main": 1.0, "mechanism": 0.5})
    # every world lands in main; per-stratum round(3 x 0.5) = 2 worlds
    # additionally land in mechanism (2 strata -> 4 world-slots)
    assert sorted(assignment["main"]) == sorted(WORLDS)
    assert len(assignment["mechanism"]) == 4
    assert stats["worlds_per_arm"] == {"main": 6, "mechanism": 4}


def test_plan_arms_is_a_pure_function_of_index_and_quotas(tmp_path):
    bank = write_bank(tmp_path)
    first, _ = plan_arms(bank, {"main": 0.5})
    second, _ = plan_arms(bank, {"main": 0.5})
    assert first == second
    # and quota prefix: a bigger arm takes the smaller arm's worlds first
    small, _ = plan_arms(bank, {"main": 0.34})
    large, _ = plan_arms(bank, {"main": 0.67})
    assert set(small["main"]) <= set(large["main"])


def test_stratum_key_separates_rule_families():
    base = {
        "family": "rule_holdout",
        "depth": 1,
        "token_target": 32768,
        "split": "train",
        "rule_structure_id": "threshold_class",
    }
    other = dict(base, rule_structure_id="parity_vote")
    assert stratum_key(base) != stratum_key(other)


def test_cli_end_to_end(tmp_path):
    bank = write_bank(tmp_path)
    # a minimal exportable bank: train.jsonl rows for each index row
    rows = [
        {
            "world_id": r["world_id"],
            "example_id": r["example_id"],
            "split": "train",
            "messages": [
                {"role": "user", "content": "ctx"},
                {"role": "assistant", "content": '{"v": 1}'},
            ],
        }
        for r in INDEX_ROWS
    ]
    with (bank / "train.jsonl").open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    config = tmp_path / "arms.json"
    config.write_text(json.dumps({"main": 0.5, "format": 0.5}))
    out = tmp_path / "arms_out"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_arms.py",
            str(bank),
            "--config",
            str(config),
            "--output",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((out / "arms.json").read_text())
    # 2 strata x round(3 x 0.5) = 2 worlds = 4 worlds, 2 rows each -> 8 rows
    assert receipt["arms"]["main"]["train_rows"] == 8
    # paired arms at equal quota take the same sorted prefix -> identical worlds
    assert receipt["world_overlaps"] == {"format&main": 4}
