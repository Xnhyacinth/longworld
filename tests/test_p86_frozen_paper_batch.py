"""Regression tests for pinned-source and visible-revision admission."""

from __future__ import annotations

import json

import pytest

from scripts import run_p86_frozen_paper_batch as batch


def _line(word: str) -> str:
    return (
        "The experiment measured this configuration across repeated evaluation "
        "runs and compared its observed behavior against the reference system "
        f"before applying the documented {word} correction to the final results."
    )


def test_identical_copy_is_removed_without_collapsing_different_content() -> None:
    files, removed = batch._dedupe(
        {
            "main.tex": "distinct main source",
            "copy/main.tex": "distinct main source",
            "appendix/main.tex": "independent appendix source",
        }
    )
    assert removed == 1
    assert files == {
        "main.tex": "distinct main source",
        "appendix/main.tex": "independent appendix source",
    }


def test_visible_solver_requires_the_old_and_new_prose() -> None:
    old, new = _line("baseline"), _line("revised")
    assert batch._quality_hunks(old, new, 120)
    context = batch._render(
        "v1", "v2", ["section.tex"], [], {"section.tex": old}, {"section.tex": new}
    )
    selectors = {"section.tex": batch._anchor(old)}
    expected = {"section.tex": {"v1": old, "v2": new}}
    assert batch._solve(context, selectors, "v1", "v2") == expected
    start = context.index(new)
    removed = context[:start] + "?" * len(new) + context[start + len(new) :]
    assert batch._solve(removed, selectors, "v1", "v2") is None


def test_inventory_rejects_archive_hash_drift(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "ROOT", tmp_path)
    folder = tmp_path / "data/source_inventory/paper"
    folder.mkdir(parents=True)
    for version in (1, 2):
        (folder / f"arxiv-2401.12345v{version}.source.tar").write_bytes(b"different")
    inventory = {
        "source_status": "public_api_export",
        "authorization": {"basis": "local fixture"},
        "records": [
            {
                "work_id": "arxiv:2401.12345",
                "revision_id": f"v{version}",
                "source_archive_file": f"arxiv-2401.12345v{version}.source.tar",
                "source_archive_sha256": "0" * 64,
            }
            for version in (1, 2)
        ],
    }
    path = folder / "paper_fetch_inventory.json"
    path.write_text(json.dumps(inventory))
    pin = {"path": str(path.relative_to(tmp_path)), "sha256": batch._sha(path)}
    with pytest.raises(ValueError, match="archive provenance changed"):
        batch._inventory({"family_id": "paper", "split": "train", "inventory": pin})


def test_frozen_batch_receipt_and_mask_if_mounted() -> None:
    output = batch.ROOT / "data/candidates/p86_frozen_paper_batch_v2"
    if not (output / "manifest.json").is_file():
        pytest.skip("frozen batch is not mounted")
    manifest = json.loads((output / "manifest.json").read_text())
    indexes = [
        json.loads(line)
        for line in (output / "sample_index.jsonl").read_text().splitlines()
    ]
    audits = [
        json.loads(line) for line in (output / "audit.jsonl").read_text().splitlines()
    ]
    assert manifest["train_ready"] is False
    assert manifest["quality_admitted_tasks"] == len(indexes) == len(audits)
    assert manifest["prior_source_pair_overlaps"] == 0
    assert manifest["prior_exact_answer_overlaps"] == 0
    assert manifest["question_style"] == "explicit_revision_alignment"
    assert manifest["source_works"] == len(
        {row["work_id"] for row in manifest["source_families"]}
    )
    assert all(
        row["input_tokens"] >= 32769 - row["supervised_tokens"] for row in indexes
    )
    assert all(row["supervised_tokens"] > 0 for row in indexes)
    assert all(row["bounded_evidence_extent_tokens"] >= 8192 for row in audits)
    assert all(
        len(row["line_deletion_checks"]) == len(row["evidence_spans"]) for row in audits
    )
    assert all(
        len(row["record_deletion_checks"]) == len(row["evidence_spans"])
        for row in audits
    )
    assert all(
        0 <= span["final_start_token"] < span["final_end_token"]
        for row in audits
        for span in row["evidence_spans"]
    )
