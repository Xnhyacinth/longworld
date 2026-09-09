"""Independent source-regeneration tests for the P64 export contract."""

import hashlib
import json

import pytest

from longworld.core.finance_taskbank_v2 import compile_taskbank
from longworld.core.taskbank_context import render_document
from scripts import materialize_finance_taskbank_v2 as exporter
from tests.test_finance_taskbank import world as world_fixture


class ScaledTokenizer:
    def encode(self, text, **kwargs):
        return range(len(text) * 100)


@pytest.fixture
def exported(tmp_path, monkeypatch):
    world = world_fixture.__wrapped__(tmp_path, monkeypatch)
    bank = compile_taskbank(world)
    docs = {d["record_id"]: d for d in world["documents"]}
    rendered = {i: render_document(d) for i, d in docs.items()}
    monkeypatch.setattr(
        exporter,
        "_environment",
        lambda config: (world, bank, docs, rendered, ScaledTokenizer(), "a" * 64),
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": exporter.SCHEMA,
                "split": "train",
                "tokenizer": {"model_id": "fixture", "revision": "b" * 40},
            }
        )
    )
    output = tmp_path / "export"
    exporter.materialize(config, output)
    return config, output


def mutate_file(output, name, mutate):
    path = output / name
    path.write_text(mutate(path.read_text()))
    receipt_path = output / "BUILD_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt_path.write_text(exporter.canonical(receipt) + "\n")


def test_complete_export_regenerates_context_rows_and_sft(exported):
    config, output = exported
    result = exporter.validate(config, output)
    assert result["status"] == "PASS" and result["semantic_tasks"] > 0


@pytest.mark.parametrize("change", ["scope", "strict", "plan", "extra_row", "sft"])
def test_self_rehashed_semantic_tampering_is_rejected(exported, change):
    config, output = exported
    if change == "sft":
        mutate_file(
            output,
            "sft_candidates.jsonl",
            lambda text: text.replace('"role":"assistant"', '"role":"system"', 1),
        )
    elif change == "extra_row":
        mutate_file(
            output, "tasks.jsonl", lambda text: text + text.splitlines()[0] + "\n"
        )
    else:

        def mutate(text):
            rows = [json.loads(line) for line in text.splitlines()]
            if change == "scope":
                rows[0]["task_spec"]["scope"]["metrics"] = []
            elif change == "strict":
                rows[0]["strict_long_dependency_verified"] = True
            else:
                rows[0]["context_plan"] = []
            return "".join(exporter.canonical(row) + "\n" for row in rows)

        mutate_file(output, "tasks.jsonl", mutate)
    with pytest.raises(ValueError, match="reexecution|extra"):
        exporter.validate(config, output)


@pytest.mark.parametrize(
    "change", ["code", "families", "split", "world", "tokenizer", "limit"]
)
def test_receipt_contract_fields_are_recomputed(exported, change):
    config, output = exported
    path = output / "BUILD_RECEIPT.json"
    receipt = json.loads(path.read_text())
    if change == "code":
        receipt["code_sha256"].pop(next(iter(receipt["code_sha256"])))
    elif change == "families":
        receipt["families"] = {}
    elif change == "split":
        receipt["split"] = "eval"
    elif change == "world":
        receipt["world_instance_id"] = "wrong-world"
    elif change == "tokenizer":
        receipt["tokenizer"]["revision"] = "wrong-revision"
    else:
        receipt["context_limit_with_headroom"] = 1
    path.write_text(exporter.canonical(receipt) + "\n")
    with pytest.raises(ValueError):
        exporter.validate(config, output)
