#!/usr/bin/env python3
"""Bank-level lexical overlap: NoLiMa's low-overlap discipline, measured.

Three calibers (P72 G72-5), all ROUGE-1 precision question -> span, all on
the renderer's own tokenizer and stopword convention (_content_tokens:
content unigrams, hyphenated ISO dates and hex ids stay one token):

* evidence (the legacy caliber, question vs the K consumed rows' rendered
  text). The legacy run had a latent bug this upgrade fixes: it passed the
  program dict, not the task, so `consumed` was dropped and the denominator
  silently became every record row of the world. The legacy numbers are
  kept as `legacy_buggy_program_argument` for comparison, and the fixed
  evidence caliber is the new `evidence` key.

* gold_span (question vs the decisive FIELDS of the consumed rows only --
  the amount/date cells the answer actually reads, not whole rows). This is
  the caliber a copy-the-span shortcut needs: if the question's own content
  tokens cover the gold span, no reading is required.

* hard_negative (question vs the nearest same-schema distractor rows: rows
  that share the question's category or entity namespace but fall out of
  scope). This is what a retrieve-and-paraphrase shortcut needs: if the
  question overlaps the near-miss rows more than the gold span, the task
  does not separate evidence from distractor at the lexical layer.

Per family, each caliber reports n / p50 / max / share_over_0.2 with its
denominator stated. join_unanswerable renders under the records executor
contract and its questions carry no consumed provenance on the program, so
its rows report under the fallback caliber and are flagged.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.capability_renderers import (
    _content_tokens,
    lexical_overlap,
    render_question,
)

# The decisive field each row type carries for a content answer: the cells the
# terminal operation actually reads (amount/date for aggregates, x/y/points for
# rules, alias/entity for bindings, kind/reveal for folds).
GOLD_SPAN_FIELDS = {
    "record": ("amount", "date", "category", "x", "y"),
    "reference": ("amount",),
    "event": ("amount", "date", "kind", "reveal"),
    "alias": ("alias", "entity"),
    "demo": ("points", "label"),
}


def _dump(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _span_text(row: dict, fields: tuple[str, ...]) -> str:
    return " ".join(
        str(row[key]) for key in fields if key in row and row[key] is not None
    )


def _question_text(task: dict) -> str:
    """The question as the bank renders it.

    Families the renderer's registry does not own (join_unanswerable's
    adapter key; research_run/research_join/dense_aggregate, which landed
    after the renderer extension) fall back to the stored instruction —
    the question text the row actually carries — same as the bank-calibers
    note on excluded format views.
    """
    program = task["question"]
    if program.get("family") == "join_unanswerable":
        return task["instruction"]
    try:
        return render_question(program, "jsonl", task.get("phrasing_index", 0))
    except ValueError:
        return task["instruction"]


def _parse(context: str) -> list[dict]:
    return [json.loads(line) for line in context.splitlines()[1:]]


def _precision(question_text: str, span_text: str) -> float | None:
    """Shared content tokens / span tokens: ROUGE-1 precision, or None if empty."""
    span_tokens = Counter(_content_tokens(span_text))
    if not span_tokens:
        return None
    question_tokens = set(_content_tokens(question_text))
    shared = sum(1 for token in span_tokens if token in question_tokens)
    return shared / sum(span_tokens.values())


def gold_span_text(rows: list[dict], consumed: list[str]) -> str | None:
    """The decisive fields of the consumed rows: the answer's own support."""
    by_id = {row["id"]: row for row in rows}
    parts = []
    for cid in consumed:
        row = by_id.get(cid)
        if row is None:
            continue
        parts.append(_span_text(row, GOLD_SPAN_FIELDS.get(row.get("type", ""), ())))
    text = " ".join(parts)
    return text or None


def hard_negative_text(rows: list[dict], task: dict, limit: int = 4) -> str | None:
    """Nearest same-schema out-of-scope rows: shared category or entity, not consumed.

    Near-miss selection per row type: record rows share the question's
    category condition values or a consumed row's entity but are outside the
    consumed set; event rows share a consumed entity; demo rows are none
    (they are all evidence). Falling back to same-type rows keeps the
    caliber defined for every family while the primary selectors stay
    question-relevant.
    """
    question = task["question"]
    consumed = set(task.get("consumed") or [])
    by_id = {row["id"]: row for row in rows}
    consumed_rows = [by_id[cid] for cid in consumed if cid in by_id]
    wanted_categories = {
        condition["value"]
        for step in question.get("steps", [])
        if step.get("op") == "filter"
        for condition in step.get("conditions", [])
        if condition.get("field") == "category"
    }
    wanted_entities = {row.get("entity") for row in consumed_rows}
    # The probe row anchors the near-miss schema: prefer a record (the
    # dominant evidence type), because a demo-first consumed order (rule
    # families sort the demo ids first) would otherwise find no same-type
    # out-of-scope rows at all.
    probe = next(
        (row for row in consumed_rows if row.get("type") == "record"),
        consumed_rows[0] if consumed_rows else None,
    )
    near: list[dict] = []
    if probe is not None:
        probe_type = probe.get("type")
        for row in rows:
            if row["id"] in consumed or row.get("type") != probe_type:
                continue
            if probe_type == "record":
                shares = (
                    row.get("category") in wanted_categories
                    or row.get("entity") in wanted_entities
                )
            else:
                shares = row.get("entity") in wanted_entities
            if shares:
                near.append(row)
    if not near:
        # Fallback: same row type as the probe, out of scope, file order.
        probe_type = probe.get("type") if probe is not None else "record"
        near = [
            row
            for row in rows
            if row["id"] not in consumed and row.get("type") == probe_type
        ][:limit]
    near = near[:limit]
    if not near:
        return None
    return " ".join(
        _span_text(row, GOLD_SPAN_FIELDS.get(row.get("type", ""), ())) for row in near
    )


def measure(bank: Path, sample: int) -> dict:
    manifest = json.loads((bank / "manifest.json").read_text())
    rng = random.Random(20260919)
    shards = list(manifest["shards"])
    rng.shuffle(shards)
    calibers: dict[str, dict[str, list[float]]] = {
        "evidence": defaultdict(list),
        "gold_span": defaultdict(list),
        "hard_negative": defaultdict(list),
        "legacy_buggy_program_argument": defaultdict(list),
    }
    skipped = Counter()
    checked = 0
    for receipt in shards:
        shard = bank / "shards" / receipt["shard_id"]
        bundle = json.loads((shard / "world.json").read_text())
        rows = [
            json.loads(line) for line in (shard / "rows.jsonl").read_text().splitlines()
        ]
        if not rows:
            continue
        context_rows = _parse(bundle["context"])
        for row in rows:
            if checked >= sample:
                break
            task_id = row["example_id"].split(":")[-1]
            task = next(t for t in bundle["tasks"] if t["task_id"] == task_id)
            family = row["family"]
            question_text = _question_text(task)
            # Legacy caliber, reproduced bit-for-bit: the program dict, so the
            # consumed provenance was dropped and evidence fell back to all
            # record rows.
            try:
                calibers["legacy_buggy_program_argument"][family].append(
                    lexical_overlap(bundle["context"], task["question"], "jsonl")
                )
            except ValueError:
                skipped[family] += 1
            # Evidence (fixed): the K consumed rows' whole rendered text.
            consumed = task.get("consumed") or []
            by_id = {r["id"]: r for r in context_rows}
            evidence_text = " ".join(
                _dump(by_id[cid]) for cid in consumed if cid in by_id
            )
            if evidence_text:
                overlap = _precision(question_text, evidence_text)
                if overlap is not None:
                    calibers["evidence"][family].append(overlap)
            # Gold span: decisive fields of the consumed rows.
            span = gold_span_text(context_rows, consumed)
            if span:
                overlap = _precision(question_text, span)
                if overlap is not None:
                    calibers["gold_span"][family].append(overlap)
            # Hard negative: nearest same-schema out-of-scope rows.
            near = hard_negative_text(context_rows, task)
            if near:
                overlap = _precision(question_text, near)
                if overlap is not None:
                    calibers["hard_negative"][family].append(overlap)
            checked += 1
        if checked >= sample:
            break

    def stats(values: list[float]) -> dict:
        values = sorted(values)
        if not values:
            return {"n": 0, "p50": None, "max": None, "share_over_0.2": None}
        return {
            "n": len(values),
            "p50": round(values[len(values) // 2], 3),
            "max": round(values[-1], 3),
            "share_over_0.2": round(sum(v > 0.2 for v in values) / len(values), 3),
        }

    denominators = {
        "evidence": "question tokens shared with / K consumed rows' full rendered text tokens",
        "gold_span": "question tokens shared with / decisive-field tokens (amount/date/category/x/y/alias/entity/kind/reveal/points/label) of the consumed rows",
        "hard_negative": "question tokens shared with / decisive-field tokens of up to 4 nearest same-schema out-of-scope rows",
        "legacy_buggy_program_argument": "the pre-G72-5 run: program dict passed, consumed dropped, denominator was all record rows",
    }
    return {
        "bank": str(bank),
        "sampled": checked,
        # Legacy key, family-first, same semantics the old run reported (the
        # buggy program-dict caliber) so nothing parsing the old shape breaks.
        "per_family": {
            fam: stats(vals)
            for fam, vals in sorted(calibers["legacy_buggy_program_argument"].items())
        },
        "calibers": {
            caliber: {fam: stats(vals) for fam, vals in sorted(by_fam.items())}
            for caliber, by_fam in calibers.items()
        },
        "caliber_denominators": denominators,
        "skipped_legacy_render": dict(skipped),
        "anchor": "NoLiMa measures R-1 0.069 vs NIAH 0.905; lower is harder",
        "note": (
            "gold_span is the copy-the-span channel: if the question covers the "
            "answer's own support fields, no reading is needed. hard_negative is "
            "the retrieve-and-paraphrase channel: if the question overlaps "
            "near-miss rows at least as much as the gold span, evidence and "
            "distractor are not lexically separated. evidence is the legacy "
            "NoLiMa-style caliber, now actually measured against the consumed "
            "rows (the legacy run's program-dict argument silently measured "
            "against all record rows)."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = measure(args.bank, args.sample)
    print(json.dumps(report, indent=2) if args.json else json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
