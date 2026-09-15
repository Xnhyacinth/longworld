from __future__ import annotations

import json
from pathlib import Path

from longworld.core.p66_researchlab_taskbank import (
    answer_with_complete_records,
    bundle_path_sets,
    delta_signature,
    exact_numeric_range,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/p66_researchlab_taskbank_v1.json"
OUTPUT = ROOT / "data/candidates/p66_researchlab_taskbank_v1"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_delta_signature_rejects_format_only_change() -> None:
    assert delta_signature("\\section{Results}\n", "\\section{results}\n") is None
    assert delta_signature(
        "The value was four units.\n", "The value was eight units.\n"
    ) == {
        "status": "replaced",
        "old_excerpt": "The value was four units.",
        "new_excerpt": "The value was eight units.",
    }


def test_bundle_path_sets_are_distinct_and_bounded() -> None:
    weighted = [("a", 20), ("b", 20), ("c", 20), ("d", 20)]
    groups = bundle_path_sets(weighted, minimum=35, maximum=65)
    assert groups == [("a", "b"), ("b", "c"), ("c", "d")]


def test_exact_ranges_are_separate_from_capacity_bins() -> None:
    assert exact_numeric_range(65_000) == "64k"
    assert exact_numeric_range(70_000) is None
    assert exact_numeric_range(260_000) == "256k"


def test_complete_record_replay_fails_when_any_required_record_is_removed() -> None:
    required = {"v1:a", "v2:a"}
    answer = [{"path": "a", "status": "replaced"}]
    assert answer_with_complete_records(required, required, answer) == answer
    assert answer_with_complete_records({"v2:a"}, required, answer) == "UNKNOWN"


def test_config_preserves_paper_family_split_isolation() -> None:
    config = json.loads(CONFIG.read_text())
    assert {family["family_id"] for family in config["families"]} == {
        "elife-94586",
        "llama3-arxiv-2407.21783",
        "sparks-arxiv-2303.12712",
    }
    assert len({family["family_id"] for family in config["families"]}) == 3
    assert all(family["split"] in {"train", "eval"} for family in config["families"])


def test_materialized_rows_are_complete_record_contract_candidates() -> None:
    receipt = json.loads((OUTPUT / "BUILD_RECEIPT.json").read_text())
    candidates = _jsonl(OUTPUT / "candidates.jsonl")
    assert receipt["semantic_tasks"] == len(candidates)
    assert receipt["strict_long_dependency_verified"] is False
    assert receipt["training_release_eligible"] is False
    assert receipt["production_eligible"] is False
    assert {row["family_id"] for row in candidates} == {
        "elife-94586",
        "sparks-arxiv-2303.12712",
    }
    assert all(row["context_tokens"] > 32768 for row in candidates)
    assert all(row["compact_complete_child_tokens"] > 16384 for row in candidates)
    assert receipt["admission_states"] == {
        "complete_record_contract_local_candidate": 48
    }
    assert all(
        row["admission_state"] == "complete_record_contract_local_candidate"
        and row["local_candidate"] is True
        and row["local_training_candidate"] is False
        for row in candidates
    )
    assert all(
        set(row["shortcut_evidence"].values()) == {"unmeasured"} for row in candidates
    )
    assert receipt["controls"] == {
        "complete_record_contract": "declared and source-record inventory checked",
        "question_only": "unmeasured",
        "latest_revision_only": "unmeasured",
        "cropped_source_4k_8k_16k": "unmeasured",
        "remove_one": "unmeasured",
        "neural": "unmeasured",
    }
    assert len({row["semantic_task_id"] for row in candidates}) == len(candidates)
    assert len({row["context_sha256"] for row in candidates}) == len(candidates)
