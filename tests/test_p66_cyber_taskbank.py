from __future__ import annotations

import json

from longworld.core.p66_cyber_taskbank import (
    audit_task,
    canonical_json,
    make_task,
    question,
    replay,
    semantic_advisory,
)
from scripts.materialize_p66_cyber_taskbank import (
    _full_chat_token_count,
    _split_units,
    _targets,
)


class CharTokenizer:
    chat_template = "fake"

    def apply_chat_template(
        self,
        messages,
        *,
        tokenize=False,
        add_generation_prompt=False,
        enable_thinking=False,
    ):
        del enable_thinking
        text = "".join(f"<{item['role']}>{item['content']}" for item in messages)
        if add_generation_prompt:
            text += "<assistant>"
        return [ord(char) for char in text] if tokenize else text

    def __call__(self, text, *, truncation, padding, max_length=None):
        del truncation, padding
        ids = [ord(char) for char in text]
        if max_length is not None:
            ids = ids[:max_length]
        return {"input_ids": ids, "attention_mask": [1] * len(ids)}


def _raw(advisory_id: str, package: str, offset: int) -> dict:
    commit = f"{offset:040x}"
    return {
        "id": advisory_id,
        "aliases": [f"CVE-2025-{1000 + offset}"],
        "published": f"2025-01-{offset + 1:02d}T00:00:00Z",
        "modified": f"2025-02-{offset + 1:02d}T00:00:00Z",
        "summary": f"Issue {offset}",
        "details": f"Real advisory details {offset}",
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": package},
                "versions": ["1.0"],
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"fixed": f"1.{offset}"}]},
                    {"type": "GIT", "events": [{"fixed": commit}]},
                ],
            }
        ],
        "references": [
            {"type": "FIX", "url": f"https://github.com/a/b/commit/{commit}"}
        ],
        "severity": [{"type": "CVSS_V3", "score": "ignored"}],
    }


def _context(count: int = 20) -> str:
    return "\n".join(
        canonical_json(semantic_advisory(_raw(f"PYSEC-2025-{i}", f"pkg-{i}", i)))
        for i in range(count)
    )


def test_projection_excludes_capacity_only_version_and_severity_fields() -> None:
    projected = semantic_advisory(_raw("PYSEC-2025-1", "demo", 1))
    text = canonical_json(projected)
    assert '"versions"' not in text
    assert '"severity"' not in text
    assert '"fixed":"1.1"' in text


def test_all_families_replay_and_target_removal_fails() -> None:
    context = _context()
    targets = ["PYSEC-2025-0", "PYSEC-2025-6", "PYSEC-2025-13", "PYSEC-2025-19"]
    for index, family in enumerate(
        ("lifecycle_matrix", "remediation_commit_join", "temporal_order")
    ):
        task = make_task(
            context=context,
            target_ids=targets,
            family=family,
            world_id="p66-test-world",
            split="train",
            band="64k",
            variant=index,
            source_binding={"archive_sha256": "a" * 64},
        )
        audit = audit_task(task)
        assert all(audit.values()), audit
        assert replay(context, targets, family) == task["answer"]


def test_questions_specify_exact_output_schema() -> None:
    assert "ecosystem_fixed, git_fixed" in question(["id"], "lifecycle_matrix")
    assert "verified_fix_commits" in question(["id"], "remediation_commit_join")
    assert "ordered_ids, earliest_published, latest_modified" in question(
        ["id"], "temporal_order"
    )


def test_split_balances_package_components_without_leakage() -> None:
    units = [
        {
            "id": f"id-{i}",
            "digest": f"digest-{i}",
            "packages": ["shared-a", "shared-b"] if i < 2 else [f"pkg-{i}"],
        }
        for i in range(8)
    ]
    lengths = {item["digest"]: 10 + i for i, item in enumerate(units)}
    result = _split_units(units, lengths)
    train_packages = {p for item in result["train"] for p in item["packages"]}
    eval_packages = {p for item in result["eval"] for p in item["packages"]}
    assert not train_packages & eval_packages
    assert {item["id"] for values in result.values() for item in values} == {
        item["id"] for item in units
    }


def test_target_variants_are_deterministic_spread_and_distinct() -> None:
    units = [{"id": f"id-{i}"} for i in range(100)]
    first = _targets(units, 0)
    second = _targets(units, 1)
    assert first == ["id-0", "id-33", "id-66", "id-99"]
    assert len(set(first)) == 4
    assert first != second


def test_noncanonical_or_missing_target_returns_unknown() -> None:
    context = _context()
    pretty = json.dumps(json.loads(context.splitlines()[0]), indent=2)
    assert replay(pretty, ["PYSEC-2025-0"], "lifecycle_matrix") == "UNKNOWN"
    assert replay(context, ["missing"], "lifecycle_matrix") == "UNKNOWN"


def test_full_chat_count_uses_assistant_only_tokenizer_contract() -> None:
    messages = [
        {"role": "user", "content": "x" * 100},
        {"role": "assistant", "content": "ANSWER"},
    ]
    count = _full_chat_token_count(CharTokenizer(), messages)
    assert count > 100
    assert count != len(messages)
