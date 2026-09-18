import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.p66_ietf_taskbank import (
    admission_reason,
    canonical,
    exact_range,
    minimum_positive_evidence_cover,
)
from scripts import materialize_p66_ietf_taskbank as materialize


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_exact_ranges_are_numeric_not_capacity_bins():
    assert exact_range(65_000) == "64k"
    assert exact_range(70_000) is None
    assert exact_range(260_000) == "256k"


def test_positive_evidence_cover_excludes_unknown_branch():
    task = {
        "evidence_items": [
            {"evidence_id": "removed", "evidence_quote": "missing quote"},
            {"evidence_id": "kept", "evidence_quote": "visible quote"},
        ]
    }
    result = minimum_positive_evidence_cover(
        "x visible quote y", task, {"removed": "UNKNOWN", "kept": "YES"}
    )
    assert result["missing_evidence_ids"] == []
    assert result["char_cover"] == len("visible quote")


def test_admission_is_fail_closed():
    assert (
        admission_reason(
            view="full",
            context_tokens=65_000,
            question_only_em=True,
            positive_cover_tokens=20_000,
        )
        == "question_only_codebook_exact"
    )
    assert (
        admission_reason(
            view="cf",
            context_tokens=32_000,
            question_only_em=False,
            positive_cover_tokens=20_000,
        )
        == "below_long_context_floor"
    )
    assert (
        admission_reason(
            view="cf",
            context_tokens=65_000,
            question_only_em=False,
            positive_cover_tokens=10_000,
        )
        == "positive_evidence_fits_16k_window"
    )
    assert (
        admission_reason(
            view="cf",
            context_tokens=65_000,
            question_only_em=False,
            positive_cover_tokens=20_000,
        )
        == "accepted_local_long_candidate"
    )


def test_validation_rejects_changed_output_tree(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    output.mkdir()
    payload = output / "train.jsonl"
    payload.write_text("original\n")
    receipt = {
        "schema_version": materialize.RECEIPT,
        "config_sha256": "config-digest",
        "files": {"train.jsonl": hashlib.sha256(payload.read_bytes()).hexdigest()},
    }
    (output / "BUILD_RECEIPT.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(materialize, "build", lambda *args: receipt)

    payload.write_text("changed\n")
    with pytest.raises(ValueError, match="output tree"):
        materialize.validate(tmp_path / "config.json", output, 1)


class _InlinePool:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def map(self, function, iterable):
        return [function(item) for item in iterable]


def _evaluated(row: dict, reason: str) -> dict:
    answer = {"kept": "YES"}
    question_text = row["context"] + "\n\nQuestion:\n" + row["question"]
    return {
        "row": row,
        "answer": answer,
        "factual_answer": "YES",
        "messages": [
            {"role": "user", "content": question_text},
            {"role": "assistant", "content": canonical(answer)},
        ],
        "context_tokens": 65_000,
        "full_hf_chat_tokens": 65_100,
        "assistant_tokens": 4,
        "question_only_answer_em": False,
        "question_only_prediction": "NO",
        "positive_evidence_cover": {"char_start": 0, "char_end": 4, "tokens": 4},
        "admission_reason": reason,
    }


def test_metadata_emits_the_shared_local_training_eligible_gate(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [
        {
            "query_id": f"query-{view}",
            "view": view,
            "world_id": "world-a",
            "semantic_base_task_id": "semantic",
            "base_task_id": "base",
            "answer_program_id": "program",
            "question": "question?",
            "context": f"{view} seed context",
        }
        for view in ("full", "cf", "ordered_artifact_view")
    ]
    reasons = {
        "full seed context": "accepted_local_long_candidate",
        "cf seed context": "question_only_codebook_exact",
        "ordered_artifact_view seed context": "positive_evidence_fits_16k_window",
    }
    inherited = tmp_path / "inherited.jsonl"
    inherited.write_text("".join(json.dumps(row) + "\n" for row in rows))
    # The gate name is shared vocabulary (prepare_p64_training.py,
    # taskbank_training.py read "local_training_eligible"); a row that emits
    # "local_training_candidate" is silently dropped by every such consumer.
    monkeypatch.setattr(
        materialize,
        "checked_config",
        lambda path: {
            "sources": [{"path": str(inherited), "sha256": "test-only"}],
            "tokenizer": {"model_id": "Qwen/Qwen3.5-4B", "revision": "0" * 40},
            "family_split": {"world-a": "train"},
        },
    )
    monkeypatch.setattr(materialize, "ProcessPoolExecutor", _InlinePool)
    monkeypatch.setattr(
        materialize,
        "evaluate",
        lambda payload: _evaluated(payload[0], reasons[payload[0]["context"]]),
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

    candidates = _jsonl(output / "candidates.jsonl")
    rejects = _jsonl(output / "rejects.jsonl")
    assert [row["source_projection_query_id"] for row in candidates] == ["query-full"]
    assert candidates[0]["local_training_eligible"] is True
    assert "local_training_candidate" not in candidates[0]
    assert all(row["local_training_eligible"] is False for row in rejects)
    assert all("local_training_candidate" not in row for row in rejects)
    # The SFT seed is still written off the same accepted row.
    seeds = _jsonl(output / "train.jsonl")
    assert [row["sample_id"] for row in seeds] == [candidates[0]["sample_id"]]
    receipt = json.loads((output / "BUILD_RECEIPT.json").read_text())
    assert receipt["accepted_local_long_candidates"] == 1
    assert receipt["rejection_reasons"] == {
        "question_only_codebook_exact": 1,
        "positive_evidence_fits_16k_window": 1,
    }
