import copy

import pytest

from scripts.audit_capability_curriculum import (
    bounded_window_witnesses,
    parse_exported_messages,
    verify_hashes,
)


def test_paired_windows_certify_only_identical_observations():
    assert bounded_window_witnesses([1, 2, 3], [9, 2, 3], True, (2,)) == {
        "prefix:2": False,
        "suffix:2": True,
    }
    assert not any(bounded_window_witnesses([1, 2], [1, 2], False, (2,)).values())
    assert not any(bounded_window_witnesses([1, 2], [2, 3], True, (4,)).values())


def test_actual_assistant_is_parsed_and_question_ids_are_unique():
    messages = [
        {
            "role": "user",
            "content": 'context\n\nQUESTIONS\n[{"id":"q","question":{},"instruction":"read"}]\nReturn one JSON object mapping every question id to its answer.',
        },
        {"role": "assistant", "content": '{"q":7}'},
    ]
    assert parse_exported_messages(messages)[2] == {"q": 7}
    forged = copy.deepcopy(messages)
    forged[1]["content"] = '{"q":7,"q":8}'
    with pytest.raises(ValueError):
        parse_exported_messages(forged)
    forged[1]["content"] = '{"other":7}'
    with pytest.raises(ValueError):
        parse_exported_messages(forged)


def test_missing_hash_and_payload_forgery_fail_closed(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("original")
    with pytest.raises(ValueError, match="required"):
        verify_hashes(tmp_path, {}, {"rows.jsonl"})
    from scripts.run_capability_world_pipeline import sha

    digest = sha(path)
    path.write_text("forged")
    with pytest.raises(ValueError, match="hash"):
        verify_hashes(tmp_path, {"rows.jsonl": digest}, {"rows.jsonl"})


def _export_fixture(root, forge=False):
    import hashlib

    from longworld.synthesis.capability_curriculum import generate_bundle
    from scripts import run_capability_world_pipeline as base
    from scripts.run_capability_curriculum import build_messages

    bundle = generate_bundle(2)
    state = {"test": True}
    fingerprint = hashlib.sha256(base.canonical(state).encode()).hexdigest()
    shard = root / "shards" / "example"
    shard.mkdir(parents=True)
    rows = []
    for name, view in [
        ("factual", bundle),
        ("counterfactual", bundle["counterfactual"]),
    ]:
        messages = build_messages(view["context"], view["tasks"], None)
        if forge and name == "factual":
            answers = {t["task_id"]: t["answer"] for t in view["tasks"]}
            answers["state-0"] = -900
            messages[1]["content"] = base.canonical(answers)
        rows.append(
            {
                "example_id": name,
                "world_id": bundle["world_id"],
                "world_seed": 2,
                "family": "ledger",
                "n_records": 120,
                "split": "train",
                "view": name,
                "topic": None,
                "messages": messages,
                "qa_count": 16,
            }
        )
    (shard / "world.json").write_text(base.canonical(bundle))
    payload = "".join(base.canonical(row) + "\n" for row in rows)
    (shard / "rows.jsonl").write_text(payload)
    receipt = {
        "shard_id": "example",
        "fingerprint": fingerprint,
        "world_id": bundle["world_id"],
        "world_seed": 2,
        "family": "ledger",
        "n_records": 120,
        "split": "train",
        "rows": 2,
        "qa_pairs": 32,
        "files": {
            name: base.sha(shard / name) for name in ("world.json", "rows.jsonl")
        },
    }
    (shard / "receipt.json").write_text(base.canonical(receipt))
    (root / "plan.json").write_text(base.canonical(state))
    (root / "train.jsonl").write_text(payload)
    (root / "eval.jsonl").write_text("")
    manifest = {
        "fingerprint": fingerprint,
        "completed_shards": 1,
        "shards": [receipt],
        "rows": 2,
        "qa_pairs": 32,
        "split_rows": {"train": 2, "eval": 0},
        "files": {
            name: base.sha(root / name) for name in ("train.jsonl", "eval.jsonl")
        },
    }
    (root / "manifest.json").write_text(base.canonical(manifest))


class CharacterTokenizer:
    def __call__(self, text, **kwargs):
        return {"input_ids": list(text.encode())}


def test_audit_reads_actual_exported_answers_and_rejects_rehashed_forgery(tmp_path):
    from scripts.audit_capability_curriculum import audit_export

    valid = tmp_path / "valid"
    _export_fixture(valid)
    result = audit_export(valid, CharacterTokenizer(), (128,))
    assert result["passed"] and result["qa_answers"] == 32
    assert result["families"]["ledger"]["changed_pairs"] == 3
    forged = tmp_path / "forged"
    _export_fixture(forged, forge=True)
    with pytest.raises(ValueError, match="actual assistant answer"):
        audit_export(forged, CharacterTokenizer(), (128,))
