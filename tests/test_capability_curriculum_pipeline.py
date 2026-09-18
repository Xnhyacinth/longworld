import copy
import json

import pytest

from scripts.run_capability_curriculum import (
    build_messages,
    make_plan,
    validate_completed_manifest,
)


def taxonomy():
    return [
        {
            "id": f"t{d}-{n}",
            "display_name": f"Topic{n}",
            "domain": {"id": str(d), "display_name": f"Domain{d}"},
            "field": {"id": f"f{d}-{n}", "display_name": f"Field{n}"},
            "subfield": {"id": f"s{d}-{n}", "display_name": f"Sub{n}"},
        }
        for d in range(4)
        for n in range(8)
    ]


def test_sampling_is_deterministic_balanced_and_topic_split_held_out():
    config = {
        "sampler_seed": 12,
        "topics_per_domain": 4,
        "world_seed_base": 10000,
        "families": ["ledger", "reservation"],
    }
    plan = make_plan(taxonomy(), config)
    assert plan == make_plan(list(reversed(taxonomy())), config)
    assert len(plan) == 32
    assert len({w["seed"] for w in plan}) == 32
    train = {w["topic"]["id"] for w in plan if w["split"] == "train"}
    evaluate = {w["topic"]["id"] for w in plan if w["split"] == "eval"}
    assert not train & evaluate
    assert len(train) == 12 and len(evaluate) == 4


def test_questions_share_one_context_and_do_not_include_gold_metadata():
    tasks = [
        {
            "task_id": "q0",
            "question": {"operation": "state"},
            "prompt": "Compute state.",
            "answer": 4321,
            "controls": {"oracle_compact_context": "hidden-control"},
        },
        {
            "task_id": "q1",
            "question": {"operation": "recall"},
            "prompt": "Recall memo.",
            "answer": "memo-answer",
        },
    ]
    messages = build_messages("CONTEXT_ONLY_ONCE", tasks, None)
    assert messages[0]["content"].count("CONTEXT_ONLY_ONCE") == 1
    assert "hidden-control" not in messages[0]["content"]
    assert "4321" not in messages[0]["content"]
    assert json.loads(messages[1]["content"]) == {"q0": 4321, "q1": "memo-answer"}
    bad = copy.deepcopy(tasks)
    bad[1]["task_id"] = "q0"
    with pytest.raises(ValueError, match="duplicate"):
        build_messages("CONTEXT_ONLY_ONCE", bad, None)


def test_context_in_question_prompt_is_rejected():
    with pytest.raises(ValueError, match="context"):
        build_messages(
            "A long context repeated",
            [
                {
                    "task_id": "q",
                    "question": {},
                    "prompt": "A long context repeated question",
                    "answer": 1,
                }
            ],
            None,
        )


@pytest.mark.parametrize("packaging", ["joint", "split"])
def test_duplicate_ids_and_context_in_prompt_are_rejected_in_both_modes(packaging):
    """Backward compatibility: the two fail-closed guards survive packaging."""
    tasks = [
        {"task_id": "q0", "question": {}, "prompt": "Compute state.", "answer": 1},
        {"task_id": "q1", "question": {}, "prompt": "Recall memo.", "answer": 2},
    ]
    supervised = None if packaging == "joint" else "q0"
    duplicate = copy.deepcopy(tasks)
    duplicate[1]["task_id"] = "q0"
    with pytest.raises(ValueError, match="duplicate"):
        build_messages("CONTEXT", duplicate, None, packaging, supervised)
    embedded = copy.deepcopy(tasks)
    embedded[1]["prompt"] = "CONTEXT appears inside the question"
    with pytest.raises(ValueError, match="context"):
        build_messages("CONTEXT", embedded, None, packaging, supervised)


def test_joint_mode_is_the_historical_single_assistant_object():
    """The v2 packaging contract is unchanged where the family still uses it."""
    tasks = [
        {"task_id": "q0", "question": {"operation": "state"}, "prompt": "Compute.",
         "answer": [1, 2]},
        {"task_id": "q1", "question": {"operation": "recall"}, "prompt": "Recall.",
         "answer": "memo"},
    ]
    messages = build_messages("CONTEXT_ONLY_ONCE", tasks, None)
    assert json.loads(messages[1]["content"]) == {"q0": [1, 2], "q1": "memo"}
    assert messages[0]["content"].count("CONTEXT_ONLY_ONCE") == 1
    assert messages[0]["content"].endswith(
        "\nReturn one JSON object mapping every question id to its answer."
    )


def test_completed_resume_rejects_manifest_field_tampering(tmp_path):
    (tmp_path / "shards").mkdir()
    for name, value in (
        ("plan.json", "{}\n"),
        ("rejects.json", "[]\n"),
        ("train.jsonl", "train\n"),
        ("eval.jsonl", "eval\n"),
    ):
        (tmp_path / name).write_text(value)
    expected = {"files": {}}
    forged = {"files": {}, "production_eligible": True}
    (tmp_path / "manifest.json").write_text(json.dumps(forged))

    with pytest.raises(ValueError, match="manifest"):
        validate_completed_manifest(tmp_path, forged, expected)
