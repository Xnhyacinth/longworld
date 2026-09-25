"""Behavioral checks for source-aware candidate reference balancing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import select_p90_balanced_candidates as balanced


def _entry(
    sample: str,
    *,
    group: str,
    task: str,
    split: str = "train",
    operation: str = "join",
    length: str = "lt32k",
) -> dict:
    full_tokens = {"lt32k": 110, "32k": 32770, "64k": 65540, "128k": 131080}[length]
    return {
        "shard": "toy",
        "candidate": {
            "sample_id": sample,
            "source_kind": "real_wiki",
            "domain": "nature",
            "topic": "parks",
            "source_group": group,
            "semantic_task_id": task,
            "split": split,
            "operation": operation,
            "length_bin": length,
            "input_tokens": full_tokens - 10,
            "supervised_tokens": 10,
            "full_chat_tokens": full_tokens,
        },
    }


def _index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict]
) -> Path:
    path = tmp_path / "index"
    path.mkdir()
    raw = "".join(json.dumps(entry) + "\n" for entry in entries)
    (path / "candidate_refs.jsonl").write_text(raw)
    (path / "manifest.json").write_text('{"train_ready":false}\n')
    digest = hashlib.sha256(raw.encode()).hexdigest()
    monkeypatch.setattr(
        balanced,
        "verify_index",
        lambda _: {"candidate_views": len(entries), "refs_sha256": digest},
    )
    return path


def test_balances_cells_and_groups_without_repeating_semantic_task() -> None:
    entries = [
        _entry("a", group="big", task="one"),
        _entry("a-64", group="big", task="one", length="64k"),
        _entry("b", group="big", task="two"),
        _entry("c", group="small", task="three"),
        _entry("d", group="other", task="four", operation="aggregate"),
        _entry("e", group="eval", task="five", split="eval"),
    ]
    selected = balanced._choose(
        entries,
        seed=90,
        max_per_group=1,
        max_per_cell=2,
        max_per_kind_by_split={"train": 3, "eval": 1},
    )
    assert len(selected) == 4
    assert len({balanced._group(entry) for entry in selected}) == 4
    assert len({balanced._task(entry) for entry in selected}) == 4
    assert {balanced._cell(entry)[0] for entry in selected} == {"train", "eval"}
    assert {balanced._cell(entry)[2] for entry in selected} == {"join", "aggregate"}


def test_rejects_source_group_crossing_train_eval() -> None:
    entries = [
        _entry("train", group="shared", task="one"),
        _entry("eval", group="shared", task="two", split="eval"),
    ]
    with pytest.raises(ValueError, match="source group crosses train/eval split"):
        balanced._choose(
            entries,
            seed=90,
            max_per_group=2,
            max_per_cell=2,
            max_per_kind_by_split={"train": 2, "eval": 2},
        )


def test_rejects_declared_length_that_differs_from_final_chat_tokens() -> None:
    entry = _entry("wrong", group="one", task="one")
    entry["candidate"]["length_bin"] = "64k"
    with pytest.raises(ValueError, match="physical length bin changed"):
        balanced._choose(
            [entry],
            seed=90,
            max_per_group=2,
            max_per_cell=2,
            max_per_kind_by_split={"train": 2, "eval": 2},
        )


def test_kind_split_cap_preserves_rare_operation_cell() -> None:
    entries = [
        _entry("common-1", group="one", task="one"),
        _entry("common-2", group="two", task="two"),
        _entry("rare", group="three", task="three", operation="aggregate"),
    ]
    selected = balanced._choose(
        entries,
        seed=90,
        max_per_group=2,
        max_per_cell=4,
        max_per_kind_by_split={"train": 2, "eval": 2},
    )
    assert len(selected) == 2
    assert {balanced._cell(entry)[2] for entry in selected} == {"join", "aggregate"}


def test_supervised_token_cap_limits_a_dominant_kind_without_dropping_other_kinds() -> (
    None
):
    entries = [
        _entry("wiki-1", group="one", task="one"),
        _entry("wiki-2", group="two", task="two"),
        _entry("wiki-3", group="three", task="three"),
        _entry("finance", group="four", task="four"),
    ]
    entries[-1]["candidate"]["source_kind"] = "real_finance"
    selected = balanced._choose(
        entries,
        seed=90,
        max_per_group=2,
        max_per_cell=4,
        max_per_kind_by_split={"train": 4, "eval": 4},
        max_supervised_tokens_by_kind={"real_wiki": 15},
    )
    assert len(selected) == 2
    assert balanced._coverage(selected)["by_source_kind"] == {
        "real_finance": 1,
        "real_wiki": 1,
    }
    assert (
        balanced._coverage(selected)["by_source_kind_tokens"]["real_wiki"][
            "supervised_tokens"
        ]
        == 10
    )


def test_select_replays_exact_bytes_and_detects_selected_ref_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [
        _entry("a", group="one", task="a"),
        _entry("b", group="two", task="b", length="64k"),
        _entry("c", group="three", task="c", split="eval"),
    ]
    index = _index(tmp_path, monkeypatch, entries)
    kwargs = {
        "seed": 90,
        "max_per_group": 1,
        "max_per_cell": 2,
        "max_per_kind_by_split": {"train": 2, "eval": 1},
    }
    first = tmp_path / "first"
    second = tmp_path / "second"
    report = balanced.select(index, first, **kwargs)
    assert report == balanced.select(index, second, **kwargs)
    assert (first / "selected_refs.jsonl").read_bytes() == (
        second / "selected_refs.jsonl"
    ).read_bytes()
    assert balanced.verify_selection(index, first, **kwargs) == report
    assert report["train_ready"] is False
    assert report["after"]["input_tokens"] == 65730
    assert report["after"]["supervised_tokens"] == 30
    assert report["after"]["by_domain"] == {"nature": 3}
    assert report["after"]["by_source_kind_groups"] == {"real_wiki": 3}
    (first / "selected_refs.jsonl").write_text("changed\n")
    with pytest.raises(ValueError, match="selected references changed"):
        balanced.verify_selection(index, first, **kwargs)


def test_pinned_codeforge_proof_gate_keeps_only_content_backed_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = [
        _entry(name, group=name, task=name)
        for name in ("certified", "failed", "uncovered")
    ]
    for entry in code:
        entry["candidate"]["source_kind"] = "real_code_workflow"
    wiki = _entry("wiki", group="wiki", task="wiki")
    index = _index(tmp_path, monkeypatch, [*code, wiki])
    proofs = []
    for name, positive in (("certified", True), ("failed", False)):
        proofs.append(
            {
                "source_group_id": name,
                "semantic_task_id": name,
                "sample_id": name,
                "split": "train",
                "full_message_tokens": 110,
                "classification": (
                    "scoped_long_input_file_aggregation_certificate"
                    if positive
                    else "outside_filename_copy_grammar"
                ),
                "scoped_long_input_certificate": positive,
                "content_backed_scoped_certificate": positive,
            }
        )
    proof_path = tmp_path / "proofs.jsonl"
    proof_path.write_text("".join(json.dumps(row) + "\n" for row in proofs))
    proof_hash = hashlib.sha256(proof_path.read_bytes()).hexdigest()
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.codeforge-reading-proof-build.v2",
                "profile_id": "p65-codeforge-filename-copy-content-backed-v1",
                "files": {"proofs.jsonl": proof_hash},
                "primary_rows": 2,
                "qualified_existing_semantic_tasks": 1,
            }
        )
    )
    monkeypatch.setattr(balanced, "ROOT", tmp_path)
    pins = [
        {
            "receipt": {
                "path": "receipt.json",
                "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            },
            "proofs": {"path": "proofs.jsonl", "sha256": proof_hash},
        }
    ]
    kwargs = {
        "seed": 95,
        "max_per_group": 2,
        "max_per_cell": 4,
        "max_per_kind_by_split": {"train": 4, "eval": 4},
        "codeforge_proofs": pins,
    }
    report = balanced.select(index, tmp_path / "selection", **kwargs)
    assert report["quality_gate"]["counts"] == {
        "code_views": 3,
        "content_backed_views": 1,
        "excluded_failed_proof": 1,
        "excluded_without_proof": 1,
        "proof_identity_match_views": 2,
        "proof_record_views": 2,
    }
    assert report["eligible"]["views"] == 2
    assert report["after"]["by_source_kind"] == {
        "real_code_workflow": 1,
        "real_wiki": 1,
    }
    assert balanced.verify_selection(index, tmp_path / "selection", **kwargs) == report
    proof_path.write_text(proof_path.read_text() + "{}\n")
    with pytest.raises(ValueError, match="CodeForge proof pin mismatch"):
        balanced.verify_selection(index, tmp_path / "selection", **kwargs)
