import pytest

from scripts import prepare_p66_multidomain_handoff as handoff


def sample(sample_id: str) -> dict:
    return {
        "sample_id": sample_id,
        "messages": [
            {"role": "user", "content": "context and question"},
            {"role": "assistant", "content": "answer"},
        ],
    }


def meta(
    sample_id: str,
    *,
    semantic: str = "semantic",
    context: str = "context-a",
    split: str = "train",
) -> dict:
    return {
        "domain": "macro_vintage",
        "split": split,
        "group_id": "bea-vintage-series:gdp",
        "world_instance_id": "world",
        "context_id": context,
        "semantic_task_id": semantic,
        "sample_id": sample_id,
    }


def test_checked_sample_accepts_native_macro_envelope() -> None:
    value = {**sample("s1"), "split": "train"}
    assert handoff.checked_sample(value, "s1") == value


def test_checked_sample_rejects_extra_fields() -> None:
    value = {**sample("s1"), "unexpected": True}
    with pytest.raises(ValueError, match="unexpected SFT envelope"):
        handoff.checked_sample(value, "s1")


def test_identity_audit_allows_distinct_length_views_of_one_task() -> None:
    rows = [
        (sample("s64"), meta("s64", context="context-64")),
        (sample("s128"), meta("s128", context="context-128")),
        (sample("s256"), meta("s256", context="context-256")),
    ]
    assert handoff.audit_identities(rows) == {
        "canonical_tasks": 1,
        "training_views": 3,
        "samples": 3,
        "groups": 1,
    }


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                (sample("s1"), meta("s1")),
                (sample("s2"), meta("s2")),
            ],
            "duplicate training view",
        ),
        (
            [
                (sample("s1"), meta("s1")),
                (sample("s1"), meta("s1", context="context-b")),
            ],
            "duplicate training view or sample",
        ),
        (
            [
                (sample("s1"), meta("s1")),
                (
                    sample("s2"),
                    meta(
                        "s2", semantic="semantic-2", context="context-b", split="eval"
                    ),
                ),
            ],
            "source group crosses train/eval",
        ),
    ],
)
def test_identity_audit_fails_closed(rows, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        handoff.audit_identities(rows)


@pytest.mark.parametrize(
    ("tokens", "capacity", "exact"),
    [
        (64000, "64k", "64k"),
        (65537, "128k", "other_long"),
        (128000, "128k", "128k"),
        (131073, "256k", "other_long"),
        (256000, "256k", "256k"),
    ],
)
def test_full_chat_capacity_and_exact_ranges(tokens, capacity, exact) -> None:
    assert handoff.capacity(tokens) == capacity
    assert handoff.exact_range(tokens) == exact


def test_all_six_domains_have_exact_expected_split_counts() -> None:
    assert set(handoff.EXPECTED_COUNTS) == set(handoff.DOMAINS)
    assert sum(sum(value.values()) for value in handoff.EXPECTED_COUNTS.values()) == 200
    assert sum(value["train"] for value in handoff.EXPECTED_COUNTS.values()) == 132
    assert sum(value["eval"] for value in handoff.EXPECTED_COUNTS.values()) == 68
    assert (
        sum(
            sum(handoff.EXPECTED_COUNTS[domain].values())
            for domain in handoff.NET_NEW_DOMAINS
        )
        == 36
    )
    assert {
        domain
        for domain, counts in handoff.EXPECTED_COUNTS.items()
        if sum(counts.values())
    } == {"codeforge", "finance_disclosure", "cyber_osv"}
    assert all("PENDING" not in spec.receipt_sha256 for spec in handoff.SOURCE_SPECS)
