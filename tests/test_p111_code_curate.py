"""P111 curator requires source-pair novelty across both earlier code pools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p111_code_curate as curate

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/p111_code_curate_v1.json"


def test_second_prior_rejects_same_pr_pair_under_a_new_semantic_id():
    index = [{"sample_id": "new", "semantic_task_id": "new-id"}]
    audit = [
        {
            "sample_id": "new",
            "source_group": "https://github.com/denoland/deno",
            "source_prs": [20, 10],
        }
    ]
    with pytest.raises(ValueError, match="repeats P107"):
        curate._no_second_prior_overlap(
            index,
            audit,
            set(),
            {("https://github.com/denoland/deno", (10, 20))},
        )
    with pytest.raises(ValueError, match="repeats P107"):
        curate._no_second_prior_overlap(index, audit, {"new-id"}, set())


def test_second_prior_rejects_missing_sample_audit():
    with pytest.raises(ValueError, match="repeats sample ID"):
        curate._no_second_prior_overlap(
            [{"sample_id": "new", "semantic_task_id": "id"}],
            [{"sample_id": "other", "source_group": "repo", "source_prs": [1, 2]}],
            set(),
            set(),
        )


def test_actual_curator_binds_two_new_deno_pairs_and_both_prior_pools():
    outputs = curate.build(CONFIG)
    manifest = json.loads(outputs["manifest.json"])
    ledger = [json.loads(line) for line in outputs["quality_ledger.jsonl"].splitlines()]
    assert manifest["gross_reader_views"] == manifest["candidate_views"] == 2
    assert manifest["rejected_prior_pr_pairs"] == 0
    assert manifest["second_prior_raw_pr_pairs_checked"] == 82
    assert {tuple(row["source_prs"]) for row in ledger} == {
        (36482, 36785),
        (36696, 36785),
    }
    assert {row["status"] for row in ledger} == {"accepted_new_pr_pair"}


def test_second_prior_manifest_sha_tamper_fails(tmp_path):
    config = json.loads(CONFIG.read_text())
    config["second_prior_native_manifest"]["sha256"] = "0" * 64
    copied = tmp_path / "tampered.json"
    copied.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="source pin drift"):
        curate.build(copied)
