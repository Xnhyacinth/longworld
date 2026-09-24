"""Native reader candidate identities survive views without hiding drift."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    TokenCounts,
    normalize_native_candidate,
    physical_length_bin,
)


def _receipt(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "receipt.json"
    path.write_text('{"native":true}\n', encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(tmp_path: Path, **changes) -> AdapterBinding:
    path, digest = _receipt(tmp_path)
    values = {
        "source_kind": "real_wiki",
        "source_group": "snapshot-a",
        "domain": "astronomy",
        "topic": "observatories",
        "operation": "table_pair_earlier_year",
        "evidence_profile": "scoped_table_parser",
        "tokenizer_profile": "pinned-chat-template",
        "receipt_path": path,
        "receipt_sha256": digest,
    }
    values.update(changes)
    return AdapterBinding(**values)


def _row(
    sample_id: str = "sample-a",
    answer: str = '{"earlier":"A","years":{"A":1900,"B":2000}}',
) -> dict:
    return {
        "example_id": sample_id,
        "messages": [
            {"role": "user", "content": "source text\n\nQ: compare"},
            {"role": "assistant", "content": answer},
        ],
    }


def _index(sample_id: str = "sample-a", **changes) -> dict:
    result = {
        "example_id": sample_id,
        "source_group": "snapshot-a",
        "task_id": "task-a",
        "source_kind": "real_wiki",
        "domain": "astronomy",
        "topic": "observatories",
        "task_type": "table_pair_earlier_year",
        "split": "train",
        "full_chat_tokens": 100,
        "input_tokens": 80,
        "supervised_tokens": 20,
        "length_bin": "lt32k",
        "token_measurement": "pinned-chat-template",
        "context_sha256": hashlib.sha256(b"source text").hexdigest(),
        "observed_lineage_token_envelope": {"start": 10, "end": 70},
        "fact_value_token_spans": [{"start": 10, "end": 12}],
    }
    result.update(changes)
    return result


def test_native_views_share_one_task_and_json_key_order_does_not_change_answer(
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path)
    first = normalize_native_candidate(
        _index(), _row(), binding, context_text="source text"
    )
    second = normalize_native_candidate(
        _index(
            "sample-b", full_chat_tokens=40000, input_tokens=39980, length_bin="32k"
        ),
        _row("sample-b", '{"years":{"B":2000,"A":1900},"earlier":"A"}'),
        binding,
        context_text="source text",
    )
    assert first.answer_sha256 == second.answer_sha256
    assert first.task_key == second.task_key
    assert first.length_bin == "lt32k" and second.length_bin == "32k"
    ledger = CandidateLedger()
    assert ledger.add(first) is True
    assert ledger.add(second) is False
    assert ledger.rows == 2 and ledger.independent_tasks == 1


def test_legacy_native_index_can_supply_missing_metadata_and_measured_mask(
    tmp_path: Path,
) -> None:
    binding = _binding(
        tmp_path,
        source_kind="real_workflow",
        source_group="issuer-1",
        domain="finance",
        topic=None,
        operation="period_comparison",
        evidence_profile="source_scoped_certificate",
        tokenizer_profile="p64-pinned-template",
    )
    index = {
        "sample_id": "p64-sample",
        "semantic_task_id": "p64-task",
        "group_id": "issuer-1",
        "domain": "finance",
        "split": "train",
        "full_message_tokens": 100,
    }
    row = _row("p64-sample", "12.5%")
    row["sample_id"] = row.pop("example_id")
    candidate = normalize_native_candidate(
        index,
        row,
        binding,
        context_text="source text",
        token_counts=TokenCounts(80, 20, 100),
    )
    assert candidate.topic == "unknown"
    assert candidate.operation == "period_comparison"
    assert candidate.source_group == "issuer-1"
    assert candidate.receipt_sha256 == binding.receipt_sha256


def test_simulated_record_adapter_supplies_domain_and_unknown_topic(
    tmp_path: Path,
) -> None:
    binding = _binding(
        tmp_path,
        source_kind="simulated_record",
        source_group="world-1",
        domain="simulated_records",
        topic=None,
        operation="alias_locate",
        evidence_profile="symbolic_record_replay",
        tokenizer_profile="p71-pinned-template",
    )
    index = {
        "example_id": "world-1:q0",
        "semantic_task_id": "world-1:q0",
        "group_id": "world-1",
        "family": "alias_locate",
        "split": "eval",
        "full_message_tokens": 100,
        "input_tokens": 80,
        "supervised_tokens": 20,
    }
    candidate = normalize_native_candidate(
        index, _row("world-1:q0", "A"), binding, context_text="source text"
    )
    assert candidate.topic == "unknown"
    assert candidate.source_kind == "simulated_record"
    assert candidate.split == "eval"


def test_context_token_evidence_and_receipt_drift_fail_closed(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    with pytest.raises(ValueError, match="context SHA"):
        normalize_native_candidate(
            _index(context_sha256="0" * 64), _row(), binding, context_text="source text"
        )
    with pytest.raises(ValueError, match="final-chat token equation"):
        normalize_native_candidate(
            _index(supervised_tokens=19), _row(), binding, context_text="source text"
        )
    with pytest.raises(ValueError, match="assistant loss mask"):
        normalize_native_candidate(
            _index(fact_value_token_spans=[{"start": 79, "end": 81}]),
            _row(),
            binding,
            context_text="source text",
        )
    with pytest.raises(ValueError, match="length disagrees"):
        normalize_native_candidate(
            _index(length_bin="64k"), _row(), binding, context_text="source text"
        )
    binding.receipt_path.write_text('{"native":false}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="receipt changed"):
        binding.verify_receipt()


def test_ledger_rejects_sample_split_and_answer_collisions(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    first = normalize_native_candidate(
        _index(), _row(), binding, context_text="source text"
    )
    ledger = CandidateLedger()
    ledger.add(first)
    with pytest.raises(ValueError, match="duplicate sample"):
        ledger.add(first)
    other_answer = normalize_native_candidate(
        _index("sample-b"),
        _row("sample-b", '{"earlier":"B"}'),
        binding,
        context_text="source text",
    )
    with pytest.raises(ValueError, match="changes split or answer"):
        ledger.add(other_answer)
    other_task = replace(
        first,
        sample_id="sample-c",
        semantic_task_id="task-b",
        task_key='["real_wiki","snapshot-a","task-b"]',
        split="eval",
    )
    with pytest.raises(ValueError, match="source group crosses"):
        ledger.add(other_task)


def test_ledger_deduplicates_semantics_across_source_groups(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    first = normalize_native_candidate(
        _index(), _row(), binding, context_text="source text"
    )
    second = replace(
        first,
        sample_id="sample-b",
        source_group="snapshot-b",
        task_key=f'["real_wiki","snapshot-b","{first.semantic_task_id}"]',
    )
    ledger = CandidateLedger()
    ledger.add(first)
    ledger.add(second)
    assert ledger.independent_tasks == 2
    assert ledger.independent_semantic_tasks == 1
    with pytest.raises(ValueError, match="across source groups"):
        ledger.add(
            replace(
                second,
                sample_id="sample-c",
                source_group="snapshot-c",
                task_key=f'["real_wiki","snapshot-c","{first.semantic_task_id}"]',
                answer_sha256="0" * 64,
            )
        )


def test_actual_p76_reader_row_when_local_artifact_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    directory = (
        root
        / "data/candidates/p76_source_batch_v1/jobs/astronomy_table_pair-b223ff6b64e3e527"
    )
    if not directory.is_dir():
        pytest.skip("local P76 source batch is unavailable")
    index = json.loads((directory / "sample_index.jsonl").read_text().splitlines()[0])
    row = json.loads((directory / "train.jsonl").read_text().splitlines()[0])
    receipt = directory / "receipt.json"
    binding = AdapterBinding(
        source_kind="real_wiki",
        source_group=index["source_group"],
        domain=index["domain"],
        topic=index["topic"],
        operation=index["task_type"],
        evidence_profile="scoped_table_parser",
        tokenizer_profile="pinned-chat-template",
        receipt_path=receipt,
        receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
    )
    context = row["messages"][0]["content"].split("\n\nQUESTION\n", 1)[0]
    candidate = normalize_native_candidate(index, row, binding, context_text=context)
    assert candidate.full_chat_tokens == index["full_chat_tokens"]
    assert candidate.length_bin == physical_length_bin(candidate.full_chat_tokens)
    assert (
        candidate.answer_sha256 and candidate.context_sha256 == index["context_sha256"]
    )
