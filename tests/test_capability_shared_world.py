"""Tests for the P73 shared-world compiler (capability_shared_world)."""

from __future__ import annotations

import hashlib
import json

from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records
from longworld.synthesis import capability_shared_world as sw
from scripts.measure_witness_coverage import audit_bank


def _family_solve_context(world: dict, family: str) -> str:
    """Rebuild the per-family solve context from the merged world's rows."""
    body = [json.loads(line) for line in world["context"].splitlines()[1:]]
    from longworld.synthesis.capability_records import _row_from_item

    parsed = [_row_from_item(item) for item in body]
    hdr = {
        "schema": records.VERSION,
        "family": family,
        "rules": records.PROTOCOLS[family],
    }
    return "\n".join(
        [json.dumps(hdr, ensure_ascii=False, sort_keys=True, separators=(",", ":"))]
        + [
            json.dumps(
                r.visible(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            for r in parsed
        ]
    )


def test_records_side_shares_one_context_across_families():
    world = sw.build_shared_world(
        870001, ("filter_aggregate", "join_lookup", "group_compare"), 600, 20, 2
    )
    families = {t["family"] for t in world["tasks"]}
    assert families == {"filter_aggregate", "join_lookup", "group_compare"}
    assert len(set(t["source_world_id"] for t in world["tasks"])) == 3
    assert world["length_records"] > 200


def test_f_side_shares_one_context_across_families():
    world = sw.build_shared_world(
        870002,
        ("alias_locate", "asof_state", "rule_holdout", "set_complete"),
        800,
        20,
        2,
    )
    families = {t["family"] for t in world["tasks"]}
    assert families == {"alias_locate", "asof_state", "rule_holdout", "set_complete"}
    assert world["length_records"] > 300


def test_records_answers_solve_on_merged_rows():
    world = sw.build_shared_world(
        870003, ("filter_aggregate", "join_lookup"), 400, 20, 2
    )
    for task in world["tasks"]:
        ctx = _family_solve_context(world, task["family"])
        assert records.solve_visible(ctx, task["question"]) == task["answer"]


def test_honesty_and_task_ids():
    world = sw.build_shared_world(870004, ("alias_locate", "asof_state"), 400, 20, 2)
    assert world["honesty"]["source_kind"] == "simulated"
    assert world["honesty"]["model_utility_measured"] is False
    ids = [t["task_id"] for t in world["tasks"]]
    assert len(set(ids)) == len(ids)


def test_same_seed_same_world():
    w1 = sw.build_shared_world(870005, ("filter_aggregate",), 400, 20, 2)
    w2 = sw.build_shared_world(870005, ("filter_aggregate",), 400, 20, 2)
    assert w1["context"] == w2["context"]
    assert w1["world_id"] == w2["world_id"]


def test_rejects_mixed_side_group():
    try:
        sw.build_shared_world(870006, ("filter_aggregate", "alias_locate"), 400, 20, 2)
    except ValueError:
        return
    raise AssertionError("mixed-side group must be rejected")


def test_tasks_carry_solve_context_and_answers_match_it():
    for family_group in (
        ("filter_aggregate", "join_lookup"),
        ("alias_locate", "asof_state"),
        ("rule_holdout",),
    ):
        world = sw.build_shared_world(870007, family_group, 400, 20, 2)
        for task in world["tasks"]:
            assert "solve_context" in task, (family_group, task["task_id"])
            ctx = task["solve_context"]
            # the stored context is the exact one the answer was solved with
            assert (
                sw.solve_task(task["family"], ctx, task["question"]) == task["answer"]
            )
            assert (
                hashlib.sha256(ctx.encode()).hexdigest()[:16]
                == task["solve_context_sha256"]
            )
            # the solve context is family-scoped: its header is the family's
            # own contract, not the shared multi-family header
            header = json.loads(ctx.splitlines()[0])
            assert header["family"] == task["family"]
            if task["family"] in ("filter_aggregate", "join_lookup"):
                assert header["schema"] == records.VERSION
            else:
                assert header["schema"] == families.VERSION
            # its rows are a subset of the merged context's rows
            body_ids = {json.loads(line)["id"] for line in ctx.splitlines()[1:]}
            merged_ids = {
                json.loads(line)["id"] for line in world["context"].splitlines()[1:]
            }
            assert body_ids <= merged_ids


def test_solve_context_omits_other_families_rows_on_f_side():
    # F-family solvers validate row schemas strictly, so each task's solve
    # context holds only its own family's sub-rows, verbatim inside the merged
    # context. Records-side tasks solve over the FULL merged rows (shared
    # schema), so no subset assertion applies there.
    world = sw.build_shared_world(
        870008, ("alias_locate", "asof_state", "rule_holdout"), 600, 20, 2
    )
    by_family = {}
    for task in world["tasks"]:
        by_family.setdefault(task["family"], set()).update(
            line for line in task["solve_context"].splitlines()[1:]
        )
    alias_rows, asof_rows = by_family["alias_locate"], by_family["asof_state"]
    assert alias_rows and asof_rows
    assert not (alias_rows & asof_rows)


def test_audit_bank_on_a_tiny_shared_bank(tmp_path):
    bank = tmp_path / "p73_tiny"
    shard = bank / "shards" / "shared-0-0"
    shard.mkdir(parents=True)
    world = sw.build_shared_world(
        870009, ("filter_aggregate", "join_lookup"), 400, 20, 2
    )
    (shard / "world.json").write_text(json.dumps(world, ensure_ascii=False) + "\n")
    report = audit_bank(bank, None)
    assert report["rows_audited"] == len(world["tasks"])
    assert report["rows_audited"] > 0
    for task in world["tasks"]:
        example_id = f"{world['world_id']}:{task['task_id']}"
        detail = report["example_details"][example_id]
        assert detail["family"] == task["family"]
        assert detail["answer"] == task["answer"]
    # family buckets are populated, not the empty pre-fix report
    for fam in ("filter_aggregate", "join_lookup"):
        stats = report["families"][fam]
        assert stats["rows"] > 0
        assert "witness_rich" in stats


def test_audit_bank_legacy_single_family_world_still_works(tmp_path):
    # legacy p71/p72 banks: one JSON world object per shard, single family,
    # tasks solve over world["context"] directly
    bank = tmp_path / "legacy_tiny"
    shard = bank / "shards" / "w0"
    shard.mkdir(parents=True)
    world = records.generate_world(870010, "filter_aggregate", 200, 20, 2)
    (shard / "world.json").write_text(json.dumps(world, ensure_ascii=False) + "\n")
    report = audit_bank(bank, None)
    assert report["rows_audited"] == len(world["tasks"])
    assert report["families"]["filter_aggregate"]["rows"] == len(world["tasks"])
