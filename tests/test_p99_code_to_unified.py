"""P99 unified adapter rejects weak or mismatched native code evidence."""

import json
from pathlib import Path

import pytest

from scripts.p99_code_to_unified import SEPARATOR, _normalize_pair


def _fixture(tmp_path: Path):
    receipt = tmp_path / "manifest.json"
    receipt.write_text("{}\n")
    sample = "p99-example"
    answer = [{"path": "src/a.py", "pull_request": 11}]
    reader = {
        "example_id": sample,
        "messages": [
            {"role": "user", "content": "Which path?" + SEPARATOR + '{"records":[]}'},
            {"role": "assistant", "content": json.dumps(answer)},
        ],
    }
    index = {
        "sample_id": sample,
        "semantic_task_id": "p99-task",
        "source_group": "https://github.com/example/code",
        "source_kind": "real_code_workflow",
        "domain": "codeforge",
        "operation": "added_line_cross_pr_complete_set",
        "split": "train",
        "full_chat_tokens": 100,
        "assistant_tokens": 10,
        "dependency_status": "content_backed_two_source_scoped_certificate",
    }
    proof = {
        "sample_id": sample,
        "answer": answer,
        "reader_visible_replay": True,
        "filename_preserving_added_code_removal_changes_answer": True,
        "all_selected_identifiers_absent_after_added_code_removal": True,
        "single_raw_16k_window_insufficient_for_both_witnesses": True,
        "evidence_token_span": 20000,
    }
    return index, reader, proof, receipt


def test_unified_candidate_keeps_code_proof_scope_in_index(tmp_path: Path):
    index, reader, proof, receipt = _fixture(tmp_path)
    candidate = _normalize_pair(index, reader, proof, receipt)
    assert candidate.source_kind == "real_code_workflow"
    assert candidate.operation == "added_line_cross_pr_complete_set"
    assert candidate.evidence_profile == "reader_visible_added_code_two_pr_control_v1"
    assert candidate.dependency_status == "content_backed_two_source_scoped_certificate"
    assert candidate.input_tokens == 90
    assert candidate.supervised_tokens == 10


@pytest.mark.parametrize(
    "change",
    [
        {"filename_preserving_added_code_removal_changes_answer": False},
        {"all_selected_identifiers_absent_after_added_code_removal": False},
        {"evidence_token_span": 16000},
        {"sample_id": "wrong"},
    ],
)
def test_unified_adapter_rejects_weak_or_mismatched_proof(tmp_path: Path, change: dict):
    index, reader, proof, receipt = _fixture(tmp_path)
    proof.update(change)
    with pytest.raises(ValueError, match="P99 native reader/index/proof"):
        _normalize_pair(index, reader, proof, receipt)
