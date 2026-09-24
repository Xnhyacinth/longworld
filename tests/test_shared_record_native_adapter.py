import hashlib
import json

import pytest

from longworld.synthesis import shared_record_native_adapter as adapter
from longworld.synthesis.unified_candidate_merge import _shared_record


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_shared_record_reader_preserves_two_operations_on_one_context(tmp_path):
    shard = tmp_path / "shards" / "one"
    shard.mkdir(parents=True)
    context = '{"schema":"shared"}\n{"id":"record-a"}'
    rows = []
    for operation in ("group_compare", "filter_aggregate"):
        sample_id = f"world-a:{operation}"
        rows.append(
            {
                "example_id": sample_id,
                "semantic_task_id": sample_id,
                "world_id": "world-a",
                "split": "train",
                "operation": operation,
                "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                "messages": [
                    {"role": "user", "content": context + "\n\nQUESTION\nsolve"},
                    {"role": "assistant", "content": '"gold"'},
                ],
                "full_chat_tokens": 100,
                "input_tokens": 90,
                "supervised_tokens": 10,
                "train_ready": False,
            }
        )
    rows_path = shard / "rows.jsonl"
    rows_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    world_path = shard / "world.json"
    world_path.write_text("{}")
    receipt = shard / "receipt.json"
    receipt.write_text(
        json.dumps({"rows_sha256": _sha(rows_path), "world_sha256": _sha(world_path)})
    )

    compiled = list(_shared_record({"receipt_paths": [str(receipt)]}))

    assert [candidate.operation for candidate, _, _ in compiled] == [
        "group_compare",
        "filter_aggregate",
    ]
    assert len({candidate.source_group for candidate, _, _ in compiled}) == 1
    assert len({candidate.context_sha256 for candidate, _, _ in compiled}) == 1


def test_shared_record_plan_rejects_wrong_frozen_seed(tmp_path, monkeypatch):
    config = {
        "schema_version": adapter.SCHEMA,
        "seed_base": 100,
        "worlds": 1,
        "length_records": 400,
        "consumed_records": 20,
        "depth": 2,
        "variants": 2,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    output = tmp_path / "output"
    shard = output / "shards" / "one"
    shard.mkdir(parents=True)
    (output / "manifest.json").write_text("{}")
    (shard / "world.json").write_text(
        json.dumps(
            {
                "seed": 101,
                "length_records": 400,
                "consumed_records": 20,
                "depth": 2,
                "n_variants": 2,
            }
        )
    )
    (shard / "receipt.json").write_text("{}")
    monkeypatch.setattr(adapter.native, "_tokenizer", lambda: None)
    monkeypatch.setattr(
        adapter.native,
        "verify",
        lambda _output, _tokenizer, **_kwargs: {
            "worlds": 1,
            "reader_rows": 4,
            "operations": {"group_compare": 2, "filter_aggregate": 2},
        },
    )

    with pytest.raises(ValueError, match="world seeds differ"):
        adapter.verify_native(config_path, output)


def test_shared_record_adapter_builds_missing_batch_and_replays(tmp_path, monkeypatch):
    config = {
        "schema_version": adapter.SCHEMA,
        "seed_base": 100,
        "worlds": 1,
        "length_records": 400,
        "consumed_records": 20,
        "depth": 2,
        "variants": 2,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    output = tmp_path / "batch"
    built = []

    def fake_build(path, **kwargs):
        built.append(kwargs)
        shard = path / "shards" / "one"
        shard.mkdir(parents=True)
        (path / "manifest.json").write_text("{}")
        (shard / "world.json").write_text(
            json.dumps(
                {
                    "seed": 100,
                    "length_records": 400,
                    "consumed_records": 20,
                    "depth": 2,
                    "n_variants": 2,
                }
            )
        )
        (shard / "receipt.json").write_text("{}")

    monkeypatch.setattr(adapter.native, "build", fake_build)
    monkeypatch.setattr(adapter.native, "_tokenizer", lambda: None)
    monkeypatch.setattr(
        adapter.native,
        "verify",
        lambda _output, _tokenizer, **_kwargs: {
            "worlds": 1,
            "reader_rows": 4,
            "operations": {"group_compare": 2, "filter_aggregate": 2},
        },
    )

    result = adapter.run_native(config_path, output, workers=2)

    assert result["rows"] == 4
    assert built == [
        {
            "worlds": 1,
            "seed_base": 100,
            "length_records": 400,
            "consumed_records": 20,
            "depth": 2,
            "variants": 2,
            "workers": 2,
        }
    ]
    assert adapter.run_native(config_path, output, workers=2)["rows"] == 4
    assert len(built) == 1
