from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.p66_researchlab_taskbank import (
    answer_with_complete_records,
    bundle_path_sets,
    delta_signature,
    exact_numeric_range,
)
from scripts import materialize_p66_researchlab_taskbank as materialize

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
        and row["record_contract_complete"] is True
        and row["local_training_candidate"] is False
        for row in candidates
    )
    # One admission flag per row: the old "local_candidate": True next to
    # "local_training_candidate": False was an eligibility-shaped pair whose
    # True half sat outside the shared vocabulary.
    assert all("local_candidate" not in row for row in candidates)
    assert receipt["record_contract_complete"] == len(candidates)
    assert receipt["local_training_candidate"] == 0
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


class _InlinePool:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def map(self, function, iterable):
        return [function(item) for item in iterable]


def test_admitted_row_has_exactly_one_eligibility_gate(
    tmp_path: Path, monkeypatch
) -> None:
    spec = {
        "family_id": "family-a",
        "split": "train",
        "old_version": "v1",
        "new_version": "v2",
        "paths": ["a"],
        "context": "old a\nnew a",
        "record_spans": {"v1:a": (0, 5), "v2:a": (6, 11)},
        "answer": [{"path": "a", "status": "replaced"}],
        "expected_capacity": 65536,
        "compact_complete_child_tokens": 20_000,
    }
    monkeypatch.setattr(
        materialize,
        "checked_config",
        lambda path: {
            "families": [
                {
                    "family_id": "family-a",
                    "kind": "arxiv_source_tar",
                    "split": "train",
                    "sources": [],
                }
            ],
            "tokenizer": {"model_id": "Qwen/Qwen3.5-4B", "revision": "0" * 40},
        },
    )
    monkeypatch.setattr(materialize, "_init_tokenizer", lambda config: None)
    monkeypatch.setattr(materialize, "ProcessPoolExecutor", _InlinePool)
    monkeypatch.setattr(materialize, "_tar_specs", lambda family: ([spec], []))
    monkeypatch.setattr(
        materialize,
        "_evaluate",
        lambda item: {
            **item,
            "question": "question?",
            "messages": [
                {"role": "user", "content": item["context"]},
                {"role": "assistant", "content": "[]"},
            ],
            "context_tokens": 65_000,
            "full_hf_chat_tokens": 65_100,
            "assistant_tokens": 4,
            "capacity_bin": 65536,
            "exact_numeric_range": "64k",
            "complete_record_span_tokens": 20_000,
            "required_complete_records": 2,
            "shortcut_evidence": {"question_only": "unmeasured"},
            "admission_reason": "complete_record_contract_local_candidate",
        },
    )
    monkeypatch.setattr(
        materialize,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda *args: "test-only",
    )
    output = tmp_path / "output"
    config_path = tmp_path / "config.json"
    config_path.write_text("{}\n")
    materialize.build(config_path, output, 1)

    (candidate,) = _jsonl(output / "candidates.jsonl")
    # Exactly one eligibility-shaped flag, and it is the declared gate.
    assert candidate["local_training_candidate"] is False
    assert candidate["record_contract_complete"] is True
    assert "local_candidate" not in candidate
    # A local-probe corpus still writes its SFT seed: that behavior is
    # unchanged; only the misleading second gate is gone.
    (seed,) = _jsonl(output / "train.jsonl")
    assert seed["sample_id"] == candidate["task_id"]
    receipt = json.loads((output / "BUILD_RECEIPT.json").read_text())
    assert receipt["record_contract_complete"] == 1
    assert receipt["local_training_candidate"] == 0


def test_validation_rejects_missing_output_member(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    output.mkdir()
    payload = output / "candidates.jsonl"
    payload.write_text("candidate\n")
    receipt = {
        "schema_version": materialize.RECEIPT,
        "files": {"candidates.jsonl": hashlib.sha256(payload.read_bytes()).hexdigest()},
    }
    (output / "BUILD_RECEIPT.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(materialize, "build", lambda *args: receipt)

    payload.unlink()
    with pytest.raises(ValueError, match="output tree"):
        materialize.validate(tmp_path / "config.json", output, 1)
