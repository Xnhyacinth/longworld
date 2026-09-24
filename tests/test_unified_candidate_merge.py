"""Behavioral joins for native reader files with different index conventions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis.unified_candidate_contract import NativeCandidate
from longworld.synthesis.unified_candidate_merge import (
    READERS,
    _indexed_rows,
    _simulation,
    append,
    merge,
    verify_merge,
)
from scripts import run_unified_synthesis_batch as runner

_verified_base_batch = runner._verified_base_batch


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_split_order_overrides_global_native_row_index(tmp_path: Path) -> None:
    train = tmp_path / "train.jsonl"
    eval_path = tmp_path / "eval.jsonl"
    index = tmp_path / "index.jsonl"
    _write_rows(train, [{"example_id": "a"}, {"example_id": "b"}])
    _write_rows(eval_path, [{"example_id": "c"}])
    _write_rows(
        index,
        [
            {"split": "train", "row_index": 0},
            {"split": "eval", "row_index": 2},
            {"split": "train", "row_index": 1},
        ],
    )
    joined = list(
        _indexed_rows(index, {"train": train, "eval": eval_path}, index_name="sim")
    )
    assert [row["example_id"] for _, row, _ in joined] == ["a", "c", "b"]


def test_native_short_file_keeps_declared_split(tmp_path: Path) -> None:
    paths = {name: tmp_path / f"{name}.jsonl" for name in ("train", "eval", "short")}
    _write_rows(paths["train"], [])
    _write_rows(paths["eval"], [])
    _write_rows(paths["short"], [{"messages": [{"role": "user", "content": "x"}]}])
    index = tmp_path / "index.jsonl"
    _write_rows(
        index, [{"split": "eval", "output_file": "short.jsonl", "row_index": 0}]
    )
    joined = list(
        _indexed_rows(index, paths, index_name="code", use_native_row_index=True)
    )
    assert len(joined) == 1
    assert joined[0][0]["split"] == "eval"
    assert joined[0][2].startswith(str(paths["short"]))


def test_native_index_rejects_out_of_range_position(tmp_path: Path) -> None:
    paths = {name: tmp_path / f"{name}.jsonl" for name in ("train", "eval")}
    _write_rows(paths["train"], [{"example_id": "a"}])
    _write_rows(paths["eval"], [])
    index = tmp_path / "index.jsonl"
    _write_rows(
        index, [{"split": "train", "output_file": "train.jsonl", "row_index": 2}]
    )
    with pytest.raises(ValueError, match="row index"):
        list(_indexed_rows(index, paths, index_name="code", use_native_row_index=True))


def test_simulation_hashes_source_without_question(tmp_path: Path) -> None:
    receipt = tmp_path / "manifest.json"
    receipt.write_text("{}\n")
    row = {
        "example_id": "sim-a",
        "split": "train",
        "messages": [
            {"role": "user", "content": "source text\n\nQUESTION\nquery"},
            {"role": "assistant", "content": '"answer"'},
        ],
    }
    index = {
        "example_id": "sim-a",
        "semantic_task_id": "task-a",
        "world_id": "world-a",
        "group_id": "world-a",
        "family": "asof_state",
        "split": "train",
        "row_index": 0,
        "output_file": "train.jsonl",
        "full_message_tokens": 100,
        "input_tokens": 90,
        "supervised_tokens": 8,
    }
    _write_rows(tmp_path / "train.jsonl", [row])
    _write_rows(tmp_path / "eval.jsonl", [])
    _write_rows(tmp_path / "sample_index.jsonl", [index])
    lane = {
        "paths": {
            "manifest": str(receipt),
            "sample_index": str(tmp_path / "sample_index.jsonl"),
            "train": str(tmp_path / "train.jsonl"),
            "eval": str(tmp_path / "eval.jsonl"),
        }
    }
    candidate, _, _ = next(_simulation(lane))
    assert candidate.context_sha256 == hashlib.sha256(b"source text").hexdigest()
    assert candidate.input_tokens == 92


def test_append_reuses_verified_reader_bytes_and_deduplicates_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def candidate(sample: str, group: str) -> NativeCandidate:
        return NativeCandidate(
            sample_id=sample,
            task_key=f'["real_wiki","{group}","task-a"]',
            semantic_task_id="task-a",
            source_kind="real_wiki",
            source_group=group,
            receipt_sha256="a" * 64,
            domain="nature",
            topic="parks",
            operation="table_cell_lookup",
            evidence_profile="native",
            evidence_status="checked",
            dependency_status="scoped",
            tokenizer_profile="pinned-chat-template",
            split="train",
            context_sha256="b" * 64,
            answer_sha256="c" * 64,
            full_chat_tokens=100,
            input_tokens=90,
            supervised_tokens=10,
            length_bin="lt32k",
            source_length_label=None,
        )

    monkeypatch.setitem(
        READERS,
        "fake",
        lambda lane: iter([(lane["candidate"], lane["reader"], "native:0")]),
    )
    reader = {
        "messages": [
            {"role": "user", "content": "source"},
            {"role": "assistant", "content": '"answer"'},
        ]
    }
    base_batch = tmp_path / "base_batch"
    base_batch.mkdir()
    base_dir = base_batch / "merged"
    first_lane = {
        "kind": "fake",
        "rows": 1,
        "candidate": candidate("a", "one"),
        "reader": reader,
    }
    merge(base_batch, base_dir, {"first": first_lane})
    lane_receipt = base_batch / "first.json"
    lane_receipt.write_text('{"rows":1}\n')
    base_manifest = {
        "schema_version": "longworld.unified-synthesis-batch.v1",
        "merged_manifest_sha256": hashlib.sha256(
            (base_dir / "manifest.json").read_bytes()
        ).hexdigest(),
        "candidate_views": 1,
        "source_count": 1,
        "lane_receipt_sha256": {
            "first": hashlib.sha256(lane_receipt.read_bytes()).hexdigest()
        },
    }
    (base_batch / "manifest.json").write_text(json.dumps(base_manifest))
    _verified_base_batch(base_batch)
    base_bytes = (base_dir / "candidate_train.jsonl").read_bytes()
    output = tmp_path / "appended"
    second_lane = {
        "kind": "fake",
        "rows": 1,
        "candidate": candidate("b", "two"),
        "reader": reader,
    }
    result = append(tmp_path, base_dir, output, {"second": second_lane})
    assert result["candidate_views"] == 2
    assert result["source_scoped_semantic_tasks"] == 2
    assert result["independent_semantic_tasks"] == 1
    assert (output / "candidate_train.jsonl").read_bytes().startswith(base_bytes)
    assert verify_merge(output) == result
    lane_receipt.write_text('{"changed":true}\n')
    with pytest.raises(ValueError, match="base lane receipt changed"):
        _verified_base_batch(base_batch)
    lane_receipt.write_text('{"rows":1}\n')
    changed = json.loads((base_dir / "manifest.json").read_text())
    changed["extra"] = "self-consistent-manifest-change"
    (base_dir / "manifest.json").write_text(json.dumps(changed))
    verify_merge(base_dir)
    with pytest.raises(ValueError, match="base batch and merged"):
        _verified_base_batch(base_batch)


@pytest.mark.parametrize("kind", ["finance_taskbank", "codeforge_taskbank"])
def test_partial_native_lane_requires_new_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    monkeypatch.setattr(runner, "_root_file", lambda _: tmp_path / "config.json")
    monkeypatch.setattr(runner, "_root_output", lambda _: partial)
    with pytest.raises(ValueError, match="cannot resume safely"):
        runner._execute(
            {"kind": kind, "config": "ignored", "output": "ignored"}, workers=1
        )
