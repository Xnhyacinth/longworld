"""Behavioral checks for the P75 model-visible reader boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import length_controller, reader_view, world_task_bank
from scripts.demo_p74_world import build_demo_world
from scripts.export_p75_real_reader import _admit_task, export
from scripts.train_sft import tokenize_assistant_only


class TinyTokenizer:
    chat_template = None

    def __call__(self, text, *, truncation=False):
        assert truncation is False
        return {"input_ids": list(range(len(text.split())))}


@pytest.fixture(scope="module")
def example():
    world = build_demo_world()
    bank = world_task_bank.build_task_bank(world, {"locate": 3})
    return world, next(
        task for task in bank.tasks if task.template == "locate_attribute"
    )


def test_reader_has_source_documents_and_no_audit_index(example):
    world, task = example
    row, index, audit = reader_view.compile_task(
        world, task, source_group="source-1", tokenizer=TinyTokenizer()
    )
    user = row["messages"][0]["content"]
    answer = row["messages"][1]["content"]
    assert user.endswith(
        reader_view.QUESTION_SEPARATOR + reader_view.natural_locate_question(task)
    )
    assert "=== FACTS ===" not in user
    assert "=== ENTITIES ===" not in user
    assert "=== SCOPE ===" not in user
    assert "bind the" not in user
    assert "program" not in row and "proof" not in row
    assert json.loads(answer) == task.answer_rendered
    assert index["quality_status"] == "research_candidate"
    assert index["evidence_status"].startswith("value_span_checked")
    assert index["full_chat_tokens"] >= index["input_tokens"]
    assert audit["program"] == task.program
    assert audit["original_question"] == task.question


def test_every_proof_span_maps_to_same_verbatim_source(example):
    world, task = example
    row, _index, audit = reader_view.compile_task(world, task, source_group="source-1")
    context = row["messages"][0]["content"].split(reader_view.QUESTION_SEPARATOR, 1)[0]
    docs = {doc.doc_id: doc for doc in world.documents}
    for mapping in audit["source_to_reader_spans"]:
        source = mapping["source"]
        expected = docs[source["doc_id"]].text[source["start"] : source["end"]]
        assert context[mapping["reader_start"] : mapping["reader_end"]] == expected


def test_pinned_tokenizer_maps_evidence_to_final_prompt_tokens(example):
    world, task = example
    tokenizer = length_controller.get_tokenizer()
    row, index, audit = reader_view.compile_task(
        world,
        task,
        source_group="source-1",
        tokenizer=tokenizer,
    )
    encoded = tokenize_assistant_only(tokenizer, row["messages"], 262144)
    assert sum(label == -100 for label in encoded["labels"]) == index["input_tokens"]
    assert (
        sum(label != -100 for label in encoded["labels"]) == index["supervised_tokens"]
    )
    assert index["evidence_token_mapping"] == "exact_prompt_offsets"
    for mapping in audit["source_to_reader_spans"]:
        assert 0 <= mapping["prompt_token_start"] < mapping["prompt_token_end"]
        assert mapping["prompt_token_end"] <= index["input_tokens"]


def test_unsupported_program_is_not_real_native(example):
    world, _task = example
    bank = world_task_bank.build_task_bank(world)
    state_task = bank.by_family("as_of_state")[0]
    assert (
        _admit_task(world, state_task)
        == "explicit_program_question:unsupported_task_family"
    )
    with pytest.raises(ValueError, match="explicit_program_question"):
        reader_view.compile_task(world, state_task, source_group="source-1")


def test_full_message_budget_rejects_without_truncation(example):
    world, task = example
    with pytest.raises(ValueError, match="full_chat_over_budget"):
        reader_view.compile_task(
            world,
            task,
            source_group="source-1",
            tokenizer=TinyTokenizer(),
            max_full_tokens=1,
        )


def test_real_snapshot_batch_keeps_worlds_in_one_split(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = root / "data/capability_records/p74_wiki_snapshot_v1"
    names = ("volcanoes_of_iceland", "noble_gases")
    paths = [source / f"{name}_snapshot.json" for name in names]
    if any(not path.exists() for path in paths):
        pytest.skip("frozen Wiki snapshots unavailable")
    manifest = export(
        paths, tmp_path, per_family=24, max_full_tokens=10**9, tokenizer=TinyTokenizer()
    )
    assert manifest["train_ready"] is False
    assert manifest["source_groups"] == 2
    sources = [
        json.loads(line)
        for line in (tmp_path / "source_manifest.jsonl").read_text().splitlines()
    ]
    assert all(not Path(row["snapshot_file"]).is_absolute() for row in sources)
    assert {row["split"] for row in sources} == {"train", "eval"}
    index = [
        json.loads(line)
        for line in (tmp_path / "sample_index.jsonl").read_text().splitlines()
    ]
    by_group = {}
    for row in index:
        by_group.setdefault(row["source_group"], set()).add(row["split"])
        assert row["profile"] == "real-native"
    assert all(len(splits) == 1 for splits in by_group.values())
    assert sum(manifest["rows"].values()) == len(index)
    assert (tmp_path / "rejects.jsonl").exists()


def test_export_fails_when_source_titles_cross_train_eval(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = json.loads(
        (
            root
            / "data/capability_records/p74_wiki_snapshot_v1/noble_gases_snapshot.json"
        ).read_text()
    )
    paths = []
    for number in (1, 2):
        clone = {**original, "snapshot_id": f"overlap-{number}"}
        path = tmp_path / f"snapshot-{number}.json"
        path.write_text(json.dumps(clone))
        paths.append(path)
    with pytest.raises(ValueError, match="source title overlap"):
        export(
            paths,
            tmp_path / "out",
            per_family=24,
            max_full_tokens=10**9,
            tokenizer=TinyTokenizer(),
        )
