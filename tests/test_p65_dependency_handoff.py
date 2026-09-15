"""P65 handoff admission, identity, and signed validation boundaries."""

import json

import pytest

from scripts import prepare_p65_dependency_handoff as prep


def sample(identity: str) -> dict:
    return {
        "sample_id": identity,
        "messages": [
            {"role": "user", "content": "complete natural source context"},
            {"role": "assistant", "content": '{"answer":1}'},
        ],
    }


def meta(domain: str, split: str, identity: str) -> dict:
    return {
        "domain": domain,
        "split": split,
        "group_id": f"{domain}-{split}",
        "world_instance_id": f"world-{domain}-{split}",
        "context_id": f"context-{identity}",
        "semantic_task_id": f"task-{identity}",
        "sample_id": identity,
        "stored_full_message_tokens": 65537,
        "admission_state": "scoped-candidate",
    }


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_REPORT_ATTESTATION_KEY", "r" * 64)
    monkeypatch.setenv("LONGWORLD_REPORT_ATTESTATION_KEY_ID", "probe-p65-handoff-test")
    config = tmp_path / "config.json"
    config.write_text("{}")
    rows = [
        (sample(identity), meta(domain, split, identity))
        for domain in prep.DOMAINS
        for split in prep.SPLITS
        for identity in [f"{domain}-{split}"]
    ]
    inputs = [prep._binding(config)]
    monkeypatch.setattr(prep, "collect", lambda path: (rows, inputs))
    monkeypatch.setattr(prep, "tokenizer_digest", lambda: "a" * 64)
    monkeypatch.setattr(prep, "code_bindings", lambda path: [prep._binding(path)])
    monkeypatch.setattr(prep, "_load_tokenizer", lambda *args: object())
    monkeypatch.setattr(prep, "_count_messages", lambda *args: 65537)
    return config, tmp_path / "output", rows


def test_prepare_and_validate_exact_projection(handoff):
    config, output, _ = handoff
    manifest = prep.prepare(config, output, workers=2)
    assert manifest["combined_counts"] == {"train": 2, "eval": 2}
    assert manifest["full_chat_capacity_bins"] == {"128k": 4}
    assert manifest["quarantined_diagnostics"]["govinfo"]["included_in_sft"] == 0
    assert manifest["strict_long_dependency_verified"] is False
    result = prep.validate(output / prep.MANIFEST)
    assert result["ok"] is True
    assert result["production_eligible"] is False


def test_token_mismatch_fails_before_manifest(handoff, monkeypatch):
    config, output, _ = handoff
    monkeypatch.setattr(prep, "_count_messages", lambda *args: 65536)
    with pytest.raises(ValueError, match="token counts differ"):
        prep.prepare(config, output)
    assert not (output / prep.MANIFEST).exists()


def test_duplicate_task_identity_rejected(handoff):
    config, output, rows = handoff
    rows[1][1]["semantic_task_id"] = rows[0][1]["semantic_task_id"]
    rows[1][1]["domain"] = rows[0][1]["domain"]
    with pytest.raises(ValueError, match="duplicate canonical"):
        prep.prepare(config, output)


def test_resigned_strict_claim_is_rejected(handoff):
    config, output, _ = handoff
    manifest = prep.prepare(config, output)
    manifest["strict_long_dependency_verified"] = True
    resigned = prep.attach_attestation(
        manifest, prep._report_key(), purpose="training_export_manifest"
    )
    (output / prep.MANIFEST).write_text(prep._canonical(resigned) + "\n")
    with pytest.raises(ValueError, match="invalid signed"):
        prep.validate(output / prep.MANIFEST)


def test_index_token_change_is_rejected_even_if_resigned(handoff):
    config, output, _ = handoff
    manifest = prep.prepare(config, output)
    index = output / "sample_index.jsonl"
    rows = index.read_text().splitlines()
    changed = json.loads(rows[0])
    changed["full_message_tokens"] += 1
    rows[0] = prep._canonical(changed)
    index.write_text("\n".join(rows) + "\n")
    for entry in manifest["outputs"]:
        if entry["path"] == index.name:
            entry.update(sha256=prep._digest(index), bytes=index.stat().st_size)
    resigned = prep.attach_attestation(
        manifest, prep._report_key(), purpose="training_export_manifest"
    )
    (output / prep.MANIFEST).write_text(prep._canonical(resigned) + "\n")
    with pytest.raises(ValueError, match="exact source projection"):
        prep.validate(output / prep.MANIFEST)
