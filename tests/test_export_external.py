import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_external_llamafactory import record_to_sharegpt, write_sharegpt


def test_acc_dialogs():
    rec = record_to_sharegpt(
        {
            "dialogs": [{"role": "user", "content": "context\n\nQ?"}],
            "ground_truth_answer": "42",
            "context_length": "8k",
        }
    )
    assert rec["conversations"][0]["from"] == "human"
    assert rec["conversations"][-1] == {"from": "gpt", "value": "42"}


def test_longtrace_messages():
    rec = record_to_sharegpt(
        {
            "source": "longqa",
            "input_messages": [{"role": "user", "content": "Use the context..."}],
            "label": "nucleus",
        }
    )
    assert rec["conversations"][-1]["value"] == "nucleus"


def test_docqa_prompt_reward():
    rec = record_to_sharegpt(
        {
            "prompt": [{"role": "user", "content": "Please read..."}],
            "reward_model": {
                "ground_truth": "The correct answer is (C).",
                "style": "rule",
            },
        }
    )
    assert rec["conversations"][-1]["value"] == "The correct answer is (C)."


def test_loongrl_prompt_list_gold():
    rec = record_to_sharegpt(
        {
            "prompt": [{"role": "user", "content": "trace UUIDA-1 then answer"}],
            "reward_model": {"ground_truth": ["Paris"], "style": "rule"},
            "extra_info": {"input_question": "capital?"},
        }
    )
    assert rec["conversations"][-1]["value"] == "Paris"


def test_literal_video_tag_is_escaped():
    rec = record_to_sharegpt(
        {
            "dialogs": [
                {
                    "role": "user",
                    "content": 'Indicate "extraction" of direct <video> URL',
                }
            ],
            "ground_truth_answer": "ok",
        }
    )
    human = rec["conversations"][0]["value"]
    assert "<video>" not in human
    assert "< video>" in human


def test_longmit_all_docs_cot():
    rec = record_to_sharegpt(
        {
            "all_docs": [{"id": "a", "content": "Alice lives in Paris."}],
            "question": "Where does Alice live?",
            "answer": "Paris",
            "hop": 2,
            "language": "en",
            "type": "intra_doc",
        }
    )
    human = rec["conversations"][0]["value"]
    assert "Passage 1:" in human
    assert "Alice lives in Paris." in human
    assert "complete reasoning process" in human
    assert rec["conversations"][-1]["value"] == "Paris"


def test_sharegpt_to_messages_roles():
    from export_external_llamafactory import sharegpt_to_messages

    rec = record_to_sharegpt(
        {
            "dialogs": [{"role": "user", "content": "context\n\nQ?"}],
            "ground_truth_answer": "42",
        }
    )
    out = sharegpt_to_messages(rec)
    assert out["messages"][0]["role"] == "system"
    assert out["messages"][1] == {"role": "user", "content": "context\n\nQ?"}
    assert out["messages"][-1] == {"role": "assistant", "content": "42"}


def test_sharegpt_to_messages_passthrough():
    from export_external_llamafactory import sharegpt_to_messages

    out = sharegpt_to_messages(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }
    )
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][-1]["content"] == "hello"


def test_external_export_deduplicates_and_preserves_workflow_identity(
    tmp_path: Path,
) -> None:
    row = {
        "dialogs": [
            {"role": "user", "content": "long repository context"},
            {"role": "assistant", "content": "original reasoning"},
        ],
        "ground_truth_answer": "fixed answer",
        "instance_id": "issue-17",
        "repo": "org/repo",
        "base_commit": "a" * 40,
        "context_length": "100k",
    }
    destination = tmp_path / "acc_swe.jsonl"

    meta = write_sharegpt([row, dict(row)], destination, "acc_swe")
    exported = [json.loads(line) for line in destination.read_text().splitlines()]

    assert len(exported) == 1
    assert exported[0]["data_stage"] == "external_candidate"
    assert exported[0]["source_metadata"] == {
        "record_id": "issue-17",
        "repo": "org/repo",
        "revision": "a" * 40,
        "declared_context_length": "100k",
    }
    assert meta["n"] == 1
    assert meta["duplicates"] == 1


def test_external_export_rejects_every_row_in_a_conflicting_prompt_group(
    tmp_path: Path,
) -> None:
    def row(answer: str) -> dict:
        return {
            "dialogs": [{"role": "user", "content": "identical context"}],
            "ground_truth_answer": answer,
            "instance_id": f"issue-{answer}",
            "repo": "org/repo",
            "base_commit": answer * 40,
        }

    destination = tmp_path / "conflicts.jsonl"
    meta = write_sharegpt([row("a"), row("b")], destination, "acc_swe")

    assert destination.read_text() == ""
    assert meta["n"] == 0
    assert meta["conflicting_rows"] == 2
    assert meta["conflict_groups"] == 1
