"""Collapse predictors over a generated bank, plus the repo gate's own verdict.

Measured here, on a 3-seed x 4-family x 60-record bank (114 rows, the same
construction the pipeline exports), under the hybrid contract:

    distinct_answer_shapes   12
    shape_uniqueness         0.1053   (floor 0.10)
    rows per world           2 joint / 32 split  (ceiling 50)

The repo gate (`--gate`) still exits 1 on this bank, on two counts, and both
are pre-existing metric artifacts rather than packaging regressions:

* `top_instruction_share` is 1.0 for *any* generated bank, because the message
  format has one closing answer instruction shared by every row. A joint-only
  control bank (no workflow rows at all) measures the same 1.0.
* `shape-exposure` is 10880/distinct_shapes at the fixed 680x16 budget; it is
  below 10 only for a bank with more than ~1088 distinct shapes, which this
  generator's answer schemas cannot produce at any bank size. The control bank
  fails it too (725.3 vs 906.7).

Both are asserted below as artifacts, with the control bank as the witness, so
a real regression in the packaging cannot hide behind them.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from longworld.synthesis import capability_curriculum as curriculum
from longworld.synthesis import capability_rules_workflow as workflow
from scripts.measure_sft_collapse_predictors import mask_shape
from scripts.run_capability_curriculum import build_messages, row_context

MODULES = {
    "ledger": curriculum,
    "reservation": curriculum,
    "rule_learning": workflow,
    "workflow": workflow,
}
PACKAGING = {
    "ledger": "joint",
    "reservation": "joint",
    "rule_learning": "joint",
    "workflow": "split",
}
JOINT_FAMILIES = ("ledger", "reservation", "rule_learning")
HYBRID_FAMILIES = JOINT_FAMILIES + ("workflow",)


def build_bank(seeds, families, n_records=60):
    """Every exported row of every world view, as the pipeline would write it."""
    rows = []
    for seed in seeds:
        for family in families:
            module = MODULES[family]
            packaging = PACKAGING[family]
            bundle = module.generate_bundle(seed, family, n_records, 16)
            for view in (bundle, bundle["counterfactual"]):
                tasks = view["tasks"]
                supervised = (
                    [None]
                    if packaging == "joint"
                    else [task["task_id"] for task in tasks]
                )
                for task_id in supervised:
                    task = (
                        None
                        if task_id is None
                        else next(t for t in tasks if t["task_id"] == task_id)
                    )
                    context = (
                        view["context"]
                        if task is None
                        else row_context(module, view, task, packaging)
                    )
                    rows.append(
                        {
                            "family": family,
                            "packaging": packaging,
                            "rows_per_world": len(supervised) * 2,
                            "messages": build_messages(
                                context, tasks, None, packaging, task_id
                            ),
                        }
                    )
    return rows


def write_bank(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as stream:
        for row in rows:
            stream.write(json.dumps({"messages": row["messages"]}) + "\n")
    return path


def gate_report(path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "measure_sft_collapse_predictors.py"),
            "--train",
            str(path),
            "--gate",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.stdout, result.stderr
    return json.loads(result.stdout), result.returncode


def test_rows_per_world_is_within_the_exposure_budget(tmp_path):
    """Split workflow worlds are 32 rows (16 questions x 2 views); cap is 50."""
    rows = build_bank([0], list(HYBRID_FAMILIES), 60)
    by_family = Counter(row["family"] for row in rows)
    for family, count in by_family.items():
        assert count // 2 <= 50, (family, count)
    assert by_family["workflow"] == 32
    assert sum(by_family[f] for f in JOINT_FAMILIES) == 6


def test_shape_uniqueness_is_above_the_collapse_floor(tmp_path):
    """The 0.10 floor is the repo's; splitting every family measured 0.0334."""
    rows = build_bank([0, 1, 2], list(HYBRID_FAMILIES), 60)
    shapes = Counter(mask_shape(row["messages"][1]["content"]) for row in rows)
    assert len(shapes) / len(rows) > 0.10, (len(shapes), len(rows))


def test_joint_families_keep_the_shape_variety_they_had(tmp_path):
    """Control with a wide margin: the split family dilutes, it does not flatten.

    Without workflow rows the same generator measures ~0.6, so this catches a
    real regression in the three joint families behind the aggregate number.
    """
    rows = build_bank([0, 1, 2, 3], list(JOINT_FAMILIES), 60)
    shapes = Counter(mask_shape(row["messages"][1]["content"]) for row in rows)
    assert len(shapes) / len(rows) > 0.5, (len(shapes), len(rows))


def test_gate_violations_on_this_bank_are_the_two_known_artifacts(tmp_path):
    """The bank fails the gate, on exactly the two artifacts -- and so does a
    joint-only control bank, which no packaging change can have affected."""
    hybrid = write_bank(
        build_bank([0, 1, 2], list(HYBRID_FAMILIES), 60), tmp_path / "hybrid.jsonl"
    )
    control = write_bank(
        build_bank([0, 1, 2, 3], list(JOINT_FAMILIES), 60), tmp_path / "control.jsonl"
    )
    hybrid_report, hybrid_exit = gate_report(hybrid)
    control_report, control_exit = gate_report(control)
    assert hybrid_report["gate_passed"] is False
    assert hybrid_exit == 1
    assert control_report["gate_passed"] is False and control_exit == 1
    # A single closing instruction line is a property of the message format.
    for report in (hybrid_report, control_report):
        assert report["template_collapse"]["top_instruction_share"] == 1.0
        assert any("top_instruction_share" in item for item in report["gate_violations"])
    # shape-exposure is 10880/distinct_shapes at the fixed budget.
    for report in (hybrid_report, control_report):
        assert any("shape-exposure" in item for item in report["gate_violations"])
    # The informative number, where the packaging change is visible.
    assert hybrid_report["shape_uniqueness"] > 0.10
    assert control_report["shape_uniqueness"] > 0.5


def test_split_rows_carry_one_shape_per_row_and_the_floor_holds(tmp_path):
    """The split family alone: 32 rows per world, all 4 answer fields present."""
    rows = build_bank([0, 1, 2], ["workflow"], 60)
    assert len(rows) == 96
    for row in rows:
        answer = json.loads(row["messages"][1]["content"])
        assert len(answer) == 1
        fields = next(iter(answer.values()))
        assert set(fields) == {
            "final_value",
            "last_job",
            "next_action_for_last_job",
            "recovery_count",
        }
    shapes = Counter(mask_shape(row["messages"][1]["content"]) for row in rows)
    assert len(shapes) == 1  # the same schema for every row, by answer design
