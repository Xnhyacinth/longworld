import pytest

from longworld.core.codeforge_taskbank import compile_tasks
from scripts.export_codeforge_taskbank_sft import classify_sample, project_messages
from tests.test_codeforge_taskbank import bank


def test_exact_length_limits_do_not_truncate_or_promote_short_rows():
    assert classify_sample(32768, 33000) == "short"
    assert classify_sample(32769, 65536) == "long"
    assert classify_sample(256000, 262144) == "long"
    assert classify_sample(256000, 262145) == "rejected_overflow"


def test_message_projection_replays_answer_and_rejects_tampering():
    world = bank()
    task = compile_tasks(world, [["p0", "p1"]], split="train")[0]
    messages = project_messages(world, task)
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert "Source records:" in messages[0]["content"]
    task["oracle_answer"] = ["hidden-answer"]
    with pytest.raises(ValueError, match="oracle"):
        project_messages(world, task)
