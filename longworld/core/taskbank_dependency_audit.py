"""Deterministic dependency diagnostics, never a certificate of model difficulty.

Token intervals are coordinates in the exact full-context tokenizer stream.
Numeric-repeat searches are gold-assisted opportunities, not semantic proofs.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections import defaultdict

from longworld.core.taskbank_context import validate_visible_sign

REVISION = "longworld.taskbank-dependency-audit.v2"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def exact_offsets(context, tokenizer):
    encoded = tokenizer(context, add_special_tokens=False, return_offsets_mapping=True)
    offsets = [tuple(pair) for pair in encoded["offset_mapping"]]
    if not offsets or any(b <= a for a, b in offsets):
        raise ValueError("tokenizer must expose nonempty exact text offsets")
    if any(offsets[i][0] > offsets[i + 1][0] for i in range(len(offsets) - 1)):
        raise ValueError("tokenizer offsets are not monotone")
    return offsets


def _interval(start, end, starts, ends):
    left = bisect.bisect_right(ends, start)
    right = bisect.bisect_left(starts, end)
    if left >= right:
        raise ValueError("evidence has no token coverage")
    return left, right


def _coverage(intervals, width, total):
    """Max whole-span coverage over every contiguous token window of width W."""
    events = defaultdict(int)
    last = max(0, total - width)
    for a, b in intervals:
        lo, hi = max(0, b - width), min(a, last)
        if lo <= hi:
            events[lo] += 1
            events[hi + 1] -= 1
    count = best = 0
    witness = 0
    for pos, delta in sorted(events.items()):
        count += delta
        if count > best:
            best, witness = count, pos
    return {
        "max_covered_spans": best,
        "total_spans": len(intervals),
        "all_bound_spans_fit": best == len(intervals),
        "witness_token_start": witness,
        "witness_token_end": min(total, witness + width),
    }


def _occurrences(context, quote):
    # Prevent 12 from matching 312 or 12,000. Preserve the exact signed surface.
    pattern = r"(?<![\d,.])" + re.escape(quote) + r"(?![\d,.])"
    return [(m.start(), m.end()) for m in re.finditer(pattern, context)]


def audit_taskbank_sample(
    row, context, tokenizer=None, *, token_offsets=None, windows=(4096, 8192, 16384)
):
    if sha(context) != row["context_sha256"]:
        raise ValueError("context hash mismatch")
    offsets = (
        token_offsets
        if token_offsets is not None
        else exact_offsets(context, tokenizer)
    )
    if len(offsets) != row["context_tokens"]:
        raise ValueError("context tokenizer count mismatch")
    starts, ends = [p[0] for p in offsets], [p[1] for p in offsets]
    task = row["task_spec"]
    evidence = row["visible_evidence"]
    if not evidence or {e["fact_id"] for e in evidence} != set(
        task["consumed_fact_ids"]
    ):
        raise ValueError("missing or inconsistent bound evidence")
    intervals, line_intervals, repeats, witnesses = [], [], [], []
    headers = list(re.finditer(r"(?m)^=== Annual filing: (.+?) ===$", context))
    latest_start = headers[-1].start() if headers else None
    for e in evidence:
        a, b = e["context_start"], e["context_end"]
        if not 0 <= a < b <= len(context) or context[a:b] != e["quote"]:
            raise ValueError("visible evidence quote mismatch")
        validate_visible_sign(context, a, b, e["value"])
        surface = re.sub(r"[,$()−+\-\s]", "", e["quote"])
        if not surface.isdigit() or int(surface) != abs(e["value"]):
            raise ValueError("bound numeric value does not match visible surface")
        intervals.append(_interval(a, b, starts, ends))
        lo = context.rfind("\n", 0, a) + 1
        hi = context.find("\n", b)
        hi = len(context) if hi < 0 else hi
        line_intervals.append(_interval(lo, hi, starts, ends))
        occurrences = _occurrences(context, e["quote"])
        alternatives = [(x, y) for x, y in occurrences if not (x < b and a < y)]
        repeats.append(
            {
                "fact_id": e["fact_id"],
                "other_exact_numeric_occurrences": len(alternatives),
                "latest_filing_occurrence": latest_start is not None
                and any(x >= latest_start for x, _ in occurrences),
                "alternative_examples": [list(p) for p in alternatives[:3]],
            }
        )
        witnesses.append(
            {
                "fact_id": e["fact_id"],
                "context_start": a,
                "context_end": b,
                "quote": e["quote"],
                "line_start": lo,
                "line_end": hi,
                "line_sha256": sha(context[lo:hi]),
            }
        )
    geometry = {str(w): _coverage(intervals, w, len(offsets)) for w in windows}
    lines = {str(w): _coverage(line_intervals, w, len(offsets)) for w in windows}
    span_width = max(b for _, b in intervals) - min(a for a, _ in intervals)
    # Profile is a triage class, not a successful alternative-proof search.
    profile = "retrieval" if len(evidence) == 1 else "integration"
    if len({e["record_id"] for e in evidence}) > 1 and span_width > max(windows):
        profile = "strict_candidate"
    lookup_replay = "not_applicable"
    if task["family"] == "lookup":
        e = evidence[0]
        expected = {
            "value": e["value"],
            "unit": e["unit"],
            "period_end": e["period"]["end"],
        }
        if task["answer"] != expected:
            raise ValueError("lookup answer mismatch")
        lookup_replay = "passed_using_gold_locator_and_bound_period_unit_metadata"
    return {
        "schema_version": REVISION,
        "sample_id": row["sample_id"],
        "semantic_task_id": row["semantic_task_id"],
        "split": row["split"],
        "family": task["family"],
        "profile": profile,
        "row_sha256": sha(json.dumps(row, sort_keys=True, separators=(",", ":"))),
        "context_sha256": row["context_sha256"],
        "context_tokens": len(offsets),
        "source_manifest_sha256": task["source_manifest_sha256"],
        "source_documents": task["evidence_documents"],
        "bound_span_envelope_tokens": span_width,
        "raw_token_window_support": geometry,
        "whole_visible_line_window_support": lines,
        "support_geometry_is_complete_proof": False,
        "oracle_located_lookup_answer_replay": lookup_replay,
        "visible_leaf_replay": {
            "status": "passed",
            "spans": len(evidence),
            "witnesses": witnesses,
        },
        "numeric_repeat_opportunities": repeats,
        "latest_filing_all_numeric_surfaces_present": all(
            r["latest_filing_occurrence"] for r in repeats
        ),
        "latest_filing_semantic_shortcut": "unknown: metric, year, units, signs and exact-filing basis require independent proof",
        "source_deletion": {
            "bound_leaf_deletion_checked": len(evidence),
            "leaves_with_remaining_exact_numeric_surface": sum(
                r["other_exact_numeric_occurrences"] > 0 for r in repeats
            ),
            "semantic_answer_after_deletion": "unknown",
        },
        "question_only": {
            "deterministic_candidate": "unknown",
            "neural_baseline": "unmeasured",
        },
        "alternative_proof_search_complete": False,
        "strict_long_dependency_verified": False,
        "production_eligible": False,
    }
