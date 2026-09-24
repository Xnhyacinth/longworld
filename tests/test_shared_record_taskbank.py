"""A shared record world must reuse necessary rows across distinct operations."""

import json

import pytest

from longworld.synthesis import capability_records as records
from longworld.synthesis import shared_record_taskbank as shared
from scripts import run_shared_record_taskbank as runner


def test_two_operations_reuse_same_native_rows_and_replay():
    world = shared.build_world(141001, 400, 20, 2, 2)
    assert shared.validate_world(world)["passed"]
    header, rows = records.parse_context(world["reader_context"])
    assert header["family"] == "shared_record"
    assert {task["operation"] for task in world["tasks"]} == set(shared.OPERATIONS)
    assert len(world["tasks"]) == 4
    by_id = {row.id: row for row in rows}
    for pair_id, shared_ids in world["shared_consumed_row_ids"].items():
        pair = [task for task in world["tasks"] if task["pair_id"] == pair_id]
        assert len(pair) == 2
        assert pair[0]["consumed"] == pair[1]["consumed"] == shared_ids
        assert len(shared_ids) >= 4
        assert {by_id[row_id].entity for row_id in shared_ids}
        for task in pair:
            context = shared._solve_context(world["reader_context"], task["operation"])
            assert records.solve_visible(context, task["question"]) == task["answer"]
            for row_id in shared_ids:
                assert shared._value_changes_when_removed(
                    context, task["question"], task["answer"], row_id
                )


def test_tampering_with_shared_provenance_or_visible_contract_fails():
    world = shared.build_world(141002, 400, 20, 2, 2)
    world["tasks"][1]["consumed"] = world["tasks"][1]["consumed"][1:]
    assert not shared.validate_world(world)["passed"]
    untouched = shared.build_world(141002, 400, 20, 2, 2)
    lines = untouched["reader_context"].splitlines()
    header = json.loads(lines[0])
    del header["rules"]["filter_aggregate"]
    untouched["reader_context"] = shared._dump(header) + "\n" + "\n".join(lines[1:])
    assert not shared.validate_world(untouched)["passed"]


def test_verify_only_requires_frozen_manifest(tmp_path, monkeypatch):
    world = shared.build_world(141003, 200, 10, 1, 1)
    monkeypatch.setattr(
        runner,
        "_reader_row",
        lambda current, task, tokenizer: {
            "world_id": current["world_id"],
            "operation": task["operation"],
            "split": "train",
            "full_chat_tokens": 1,
        },
    )
    runner._write_shard(tmp_path, world, None)
    with pytest.raises(ValueError, match="frozen manifest is missing"):
        runner.verify(tmp_path, None, require_manifest=True)
    manifest = runner.verify(tmp_path, None)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert runner.verify(tmp_path, None, require_manifest=True) == manifest
    (tmp_path / "manifest.json").write_text(json.dumps({**manifest, "reader_rows": 0}))
    with pytest.raises(ValueError, match="frozen manifest disagrees"):
        runner.verify(tmp_path, None, require_manifest=True)
