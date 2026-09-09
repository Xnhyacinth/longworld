import pytest

from longworld.core.taskbank_dependency_audit import (
    _coverage,
    audit_taskbank_sample,
    sha,
)


def sample(context, spans):
    evidence = [
        {
            "fact_id": str(i),
            "record_id": str(i),
            "context_start": a,
            "context_end": b,
            "quote": context[a:b],
            "value": int(context[a:b]),
        }
        for i, (a, b) in enumerate(spans)
    ]
    row = {
        "context_sha256": sha(context),
        "context_tokens": len(context),
        "sample_id": "sample",
        "semantic_task_id": "task",
        "split": "train",
        "visible_evidence": evidence,
        "task_spec": {
            "consumed_fact_ids": [e["fact_id"] for e in evidence],
            "family": "delta",
            "source_manifest_sha256": "source",
            "evidence_documents": [],
        },
    }
    return row


def test_every_window_including_unaligned_best_position():
    got = _coverage([(7, 9), (11, 13)], 6, 20)
    assert got["all_bound_spans_fit"] and got["witness_token_start"] == 7
    assert not _coverage([(7, 9), (11, 13)], 5, 20)["all_bound_spans_fit"]


def test_long_geometry_never_certifies_strict_or_model_failure():
    context = "12\n" + "x" * 20000 + "\n34"
    row = sample(context, [(0, 2), (len(context) - 2, len(context))])
    report = audit_taskbank_sample(
        row, context, token_offsets=[(i, i + 1) for i in range(len(context))]
    )
    assert report["profile"] == "strict_candidate"
    assert report["strict_long_dependency_verified"] is False
    assert report["question_only"]["neural_baseline"] == "unmeasured"


def test_alternative_numeric_presence_is_not_a_semantic_proof():
    context = "=== Annual filing: old ===\n12\n=== Annual filing: new ===\n12\n"
    row = sample(context, [(context.index("12"), context.index("12") + 2)])
    report = audit_taskbank_sample(
        row, context, token_offsets=[(i, i + 1) for i in range(len(context))]
    )
    assert report["latest_filing_all_numeric_surfaces_present"]
    assert (
        report["numeric_repeat_opportunities"][0]["other_exact_numeric_occurrences"]
        == 1
    )
    assert report["latest_filing_semantic_shortcut"].startswith("unknown")
    assert report["profile"] == "retrieval"


def test_tampered_context_and_quote_fail_closed():
    row = sample("12", [(0, 2)])
    with pytest.raises(ValueError, match="hash"):
        audit_taskbank_sample(row, "13", token_offsets=[(0, 1), (1, 2)])
    row["visible_evidence"][0]["quote"] = "13"
    with pytest.raises(ValueError, match="quote"):
        audit_taskbank_sample(row, "12", token_offsets=[(0, 1), (1, 2)])


def test_numeric_magnitude_tamper_is_not_accepted_as_replay():
    row = sample("12", [(0, 2)])
    row["visible_evidence"][0]["value"] = 99
    with pytest.raises(ValueError, match="numeric value"):
        audit_taskbank_sample(row, "12", token_offsets=[(0, 1), (1, 2)])


def test_sweep_matches_exhaustive_raw_window_search():
    import random

    rng = random.Random(73)
    for _ in range(100):
        total = rng.randint(5, 40)
        width = rng.randint(1, total + 3)
        intervals = []
        for _ in range(rng.randint(1, 8)):
            start = rng.randrange(total)
            intervals.append((start, rng.randint(start + 1, total)))
        expected = max(
            sum(start <= a and b <= start + width for a, b in intervals)
            for start in range(max(0, total - width) + 1)
        )
        assert _coverage(intervals, width, total)["max_covered_spans"] == expected
