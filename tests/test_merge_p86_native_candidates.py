"""P86 native paper and state readers share one candidate-only export."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import merge_p86_native_candidates as merged


def test_context_boundary_must_be_unique() -> None:
    messages = [{"role": "user", "content": "source\n\nQUESTION\nquery"}]
    assert merged._context(messages) == "source"
    with pytest.raises(ValueError, match="boundary"):
        merged._context([{"role": "user", "content": "source only"}])
    with pytest.raises(ValueError, match="boundary"):
        merged._context(
            [{"role": "user", "content": "a\n\nQUESTION\nb\n\nQUESTION\nc"}]
        )


def test_native_file_hash_change_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "native"
    source.mkdir()
    payload = source / "train.jsonl"
    payload.write_text("first\n")
    manifest = {
        "train_ready": False,
        "files_sha256": {"train.jsonl": merged._sha(payload)},
    }
    merged._verified_files(source, manifest)
    payload.write_text("changed\n")
    with pytest.raises(ValueError, match="native output changed"):
        merged._verified_files(source, manifest)


def test_p86_pilot_merges_native_lanes_without_audit_leakage(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "data/candidates"
    paper = root / "p86_frozen_paper_batch_v2"
    state = root / "p86_state_shared_pilot_v3"
    if not (paper / "manifest.json").exists() or not (state / "manifest.json").exists():
        pytest.skip("frozen P86 native pilot artifacts are not mounted")
    output = tmp_path / "merged"
    result = merged.build(paper, state, output)
    assert result["candidate_views"] == 103
    assert result["independent_semantic_tasks"] == 103
    assert result["views_by_lane"] == {"paper_p86": 7, "state_p86": 96}
    assert result["train_ready"] is False
    for split in ("train", "eval"):
        with (output / f"candidate_{split}.jsonl").open() as stream:
            for line in stream:
                row = json.loads(line)
                assert set(row) == {"sample_id", "messages"}
                assert [message["role"] for message in row["messages"]] == [
                    "user",
                    "assistant",
                ]
                assert "shared_fact_ids" not in row["messages"][0]["content"]


def test_numeric_wiki_lane_uses_the_native_audit_and_shared_source_group(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1] / "data/candidates"
    paper = root / "p86_frozen_paper_batch_v2"
    state = root / "p86_state_shared_pilot_v3"
    numeric = root / "p91_wiki_numeric_table_v5"
    if not all((path / "manifest.json").exists() for path in (paper, state, numeric)):
        pytest.skip("frozen native pilot artifacts are not mounted")
    output = tmp_path / "merged"
    result = merged.build(paper, state, output, wiki_numeric_dir=numeric)
    assert result["candidate_views"] == 106
    assert result["views_by_lane"]["wiki_numeric_p91"] == 3
    rows = [
        row
        for line in (output / "sample_index.jsonl").read_text().splitlines()
        if (row := json.loads(line))["source_name"] == "wiki_numeric_p91"
    ]
    assert len(rows) == 3
    assert {row["source_group"] for row in rows} == {"snapshot_965e480e1f65185dc900"}
    assert {row["operation"] for row in rows} == {"closed_numeric_table_interval"}
    assert all(
        row["evidence_profile"] == "closed_numeric_table_all_rows_replayed"
        for row in rows
    )


def test_new_generation_merges_only_its_native_lanes(tmp_path: Path) -> None:
    batch = (
        Path(__file__).resolve().parents[1]
        / "data/candidates/p92_factorial_batch_v2_final"
    )
    if not (batch / "manifest.json").exists():
        pytest.skip("frozen P92 native batch is not mounted")
    native = {
        kind: next(batch.glob(f"{prefix}-*/native"))
        for kind, prefix in (
            ("state", "shared_state_200"),
            ("hybrid", "rfc_rules_new_worlds"),
            ("numeric", "wiki_numeric_intervals"),
        )
    }
    result = merged.build(
        None,
        native["state"],
        tmp_path / "merged",
        hybrid_dir=native["hybrid"],
        wiki_numeric_dir=native["numeric"],
        generation="p92",
    )
    assert result["candidate_views"] == 1615
    assert result["independent_semantic_tasks"] == 1607
    assert result["views_by_lane"] == {
        "state_p92": 1600,
        "hybrid_p92": 12,
        "wiki_numeric_p92": 3,
    }
    assert result["train_ready"] is False


def test_globally_deduped_wiki_reader_lane_binds_mask_receipt(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "data/candidates"
    native = root / "p92_wiki_new_only_60_v2"
    if not (native / "manifest.json").exists():
        pytest.skip("frozen P92 Wiki novelty shard is not mounted")
    result = merged.build(
        None,
        None,
        tmp_path / "merged",
        wiki_new_only_dir=native,
        generation="p92",
    )
    assert result["candidate_views"] == result["independent_semantic_tasks"] == 60
    assert result["views_by_lane"] == {"wiki_new_p92": 60}
    assert result["splits"] == {"train": 22, "eval": 38}
    assert result["native_receipts"]["wiki_new_only_mask_sha256"] == merged._sha(
        native / "mask_audit.json"
    )


def test_wiki_new_only_rejects_wrong_mask_tokenizer(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "data/candidates"
    native = root / "p92_wiki_new_only_60_v2"
    if not (native / "manifest.json").exists():
        pytest.skip("frozen P92 Wiki novelty shard is not mounted")
    altered = tmp_path / "altered"
    shutil.copytree(native, altered)
    receipt_path = altered / "mask_audit.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["tokenizer"]["revision"] = "wrong-revision"
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="mask receipt disagrees"):
        merged.build(
            None,
            None,
            tmp_path / "merged",
            wiki_new_only_dir=altered,
            generation="p92",
        )


def test_generic_year_table_lane_requires_final_mask_and_visible_intervention(
    tmp_path: Path,
) -> None:
    native = (
        Path(__file__).resolve().parents[1]
        / "data/candidates/p94_wiki_generic_table_v1"
    )
    if not (native / "mask_audit.json").exists():
        pytest.skip("frozen P94 generic Wiki table audit is not mounted")
    result = merged.build(
        None,
        None,
        tmp_path / "merged",
        wiki_generic_year_dir=native,
        generation="p94",
    )
    assert result["candidate_views"] == result["independent_semantic_tasks"] == 3
    assert result["views_by_lane"] == {"wiki_generic_year_p94": 3}
    assert result["native_receipts"]["wiki_generic_year_mask_sha256"] == merged._sha(
        native / "mask_audit.json"
    )
    altered = tmp_path / "altered"
    shutil.copytree(native, altered)
    mask_path = altered / "mask_audit.json"
    mask = json.loads(mask_path.read_text())
    mask["reader_sha256"][next(iter(mask["reader_sha256"]))] = "0" * 64
    mask_path.write_text(json.dumps(mask))
    with pytest.raises(ValueError, match="evidence or answer differs"):
        merged.build(
            None,
            None,
            tmp_path / "rejected",
            wiki_generic_year_dir=altered,
            generation="p94",
        )


def test_real_pair_length_views_reuse_native_tasks_and_bind_reader_proof(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    native = root / "data/candidates/p94_real_pair_length_v2"
    config = root / "configs/p94_real_pair_length_v1.json"
    if not (native / "manifest.json").exists():
        pytest.skip("frozen P94 real pair length views are not mounted")
    result = merged.build(
        None,
        None,
        tmp_path / "merged",
        wiki_real_pair_length_dir=native,
        wiki_real_pair_length_config=config,
        generation="p94",
    )
    assert result["candidate_views"] == 12
    assert result["independent_semantic_tasks"] == 4
    assert result["length_bins"] == {"32k": 4, "64k": 4, "128k": 4}
    altered = tmp_path / "altered"
    shutil.copytree(native, altered)
    audit_path = altered / "audit.jsonl"
    audits = [json.loads(line) for line in audit_path.read_text().splitlines()]
    audits[0]["evidence_token_spans"][1][0] += 1
    audit_path.write_text(
        "".join(json.dumps(row) + "\n" for row in audits), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="native output changed"):
        merged.build(
            None,
            None,
            tmp_path / "rejected",
            wiki_real_pair_length_dir=altered,
            wiki_real_pair_length_config=config,
            generation="p94",
        )


def test_real_scan_length_views_reuse_dense_scan_tasks_and_mask_receipts(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    native = root / "data/candidates/p94_real_scan_length_v1"
    config = root / "configs/p94_real_scan_length_v1.json"
    if not (native / "manifest.json").exists():
        pytest.skip("frozen P94 real scan length views are not mounted")
    result = merged.build(
        None,
        None,
        tmp_path / "merged",
        wiki_real_scan_length_dir=native,
        wiki_real_scan_length_config=config,
        generation="p94",
    )
    assert result["candidate_views"] == 36
    assert result["independent_semantic_tasks"] == 12
    assert result["length_bins"] == {"32k": 12, "64k": 12, "128k": 12}
    assert result["splits"] == {"train": 36}
    altered = tmp_path / "altered"
    shutil.copytree(native, altered)
    index_path = altered / "sample_index.jsonl"
    indexes = [json.loads(line) for line in index_path.read_text().splitlines()]
    indexes[0]["semantic_task_id"] = "wrong-task"
    index_path.write_text(
        "".join(json.dumps(row) + "\n" for row in indexes), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="native output changed"):
        merged.build(
            None,
            None,
            tmp_path / "rejected",
            wiki_real_scan_length_dir=altered,
            wiki_real_scan_length_config=config,
            generation="p94",
        )
