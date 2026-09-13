import copy
import json

import pytest

from scripts.run_capability_curriculum import build_messages, make_plan


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
