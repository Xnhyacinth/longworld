#!/usr/bin/env python3
"""Export the C (matched control) and D (witness-rich) arms of a P73 bank.

Design .hl/design/p73_counterexample_synthesis.md §4: D is every train row
whose witness-audit distinguished_fraction clears the threshold (0.5 first);
C is the same-size control matched to D per family — witness-poor rows first
(the contrast the experiment needs), then a fill drawn from D's own rows that
brings C's empty-answer count and answer-size histogram up to D's. Where the
witness-poor pool is too thin (families whose rich rate is ~1.0), C necessarily
overlaps D; the overlap is measured and recorded, never forced away (design
§8: record honestly, do not force). Eval rows are never read: they stay the
shared held-out measurement for both arms.

Arms follow the p71 precedent (scripts/extract_arms.py): one directory per
arm holding train.jsonl + sample_index.jsonl, sha-linked from arms.json at
the bank root. Tokens are re-measured per row through the runner's
measure_tokens with the pinned tokenizer — full_chat_tokens /
input_tokens / supervised_tokens are never copied from another artifact.
Deterministic throughout: no RNG, every ordering an explicit sort key.

Usage:
  python scripts/export_p73_arms.py --bank data/capability_records/p73_shared_v1 \
    [--threshold 0.5] [--workers 8] [--no-measure-tokens]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.recommend_training_budget import derive_budget_report
from scripts.run_capability_records import (  # noqa: E402
    MODEL,
    REVISION,
    _init_worker,
    canonical,
    measure_tokens,
    sha,
)

GBS = 16


def _row_hash(row: dict) -> str:
    """Hash order inside a selection pool, decorrelated from id sequence.

    Same rationale as extract_arms.world_order: example ids embed plan order,
    which correlates with family scheduling, so selection sorts by hash.
    """
    return hashlib.sha256(f"{row['family']}|{row['example_id']}".encode()).hexdigest()


def is_empty_answer(answer) -> bool:
    """The witness audit's own empty rule, reused verbatim."""
    if isinstance(answer, dict):
        return answer.get("empty") is True or answer.get("ids") == []
    return answer == []


def answer_size(family: str, answer) -> int:
    """The answer's enumeration magnitude (design §4 answer-size matching).

    join/filter compare breakdown size, set_complete id count; the other
    families use their own natural enumeration count. Empty verdicts are
    size 0 by construction (count 0 / ids []).
    """
    if isinstance(answer, list):
        return len(answer)
    if not isinstance(answer, dict):
        # set_complete scalar verdicts: a count, or a boolean contains flag
        # (bool before int: bool is an int subclass and would return itself)
        if isinstance(answer, bool):
            return 1 if answer else 0
        if isinstance(answer, int):
            return answer
        return 0
    if family == "alias_locate":
        if isinstance(answer.get("count"), int):
            return answer["count"]
        if isinstance(answer.get("ids"), list):
            return len(answer["ids"])
        if "id" in answer:
            return 1
        return 0
    if family == "set_complete":
        if isinstance(answer.get("count"), int):
            return answer["count"]
        if isinstance(answer.get("ids"), list):
            return len(answer["ids"])
        if isinstance(answer.get("entities"), (list, dict)):
            return len(answer["entities"])
        return 1 if answer.get("contains") else 0
    if family == "filter_aggregate":
        return len(answer.get("breakdown") or {})
    if family == "join_lookup":
        by = answer.get("by_entity")
        return len(by) if isinstance(by, (list, dict)) else 0
    if family == "group_compare":
        return len(answer.get("groups") or {})
    if family == "asof_state":
        entities = answer.get("entities")
        return len(entities) if isinstance(entities, (list, dict)) else 1
    if family == "rule_holdout":
        if isinstance(answer.get("count"), int):
            return answer["count"]
        for key in ("labels", "entities"):
            value = answer.get(key)
            if isinstance(value, (list, dict)):
                return len(value)
        return 0
    return 0


def _largest_remainder(
    total: int, weights: dict[int, int], caps: dict[int, int]
) -> dict[int, int]:
    """Deterministic largest-remainder allocation of `total` rows over buckets.

    `weights` are the desired shares, `caps` the hard per-bucket availability.
    After proportional floors are capped, leftovers go to buckets with unmet
    demand first (weights minus allocation, largest first), then — only once
    every demand is met — to buckets with the most spare capacity. A pure
    function of the inputs, so a rerun is identical.
    """
    buckets = sorted(set(weights) | set(caps))
    alloc = {b: 0 for b in buckets}
    if total <= 0 or not buckets:
        return alloc
    weight_total = sum(weights.get(b, 0) for b in buckets)
    if weight_total <= 0:
        weights, weight_total = caps, sum(caps.values())
    if weight_total <= 0:
        return alloc
    for b in buckets:
        alloc[b] = min(weights.get(b, 0) * total // weight_total, caps.get(b, 0))
    remain = total - sum(alloc.values())
    for demand, b in sorted(
        ((weights.get(b, 0) - alloc[b], b) for b in buckets),
        key=lambda x: (-x[0], x[1]),
    ):
        if remain <= 0:
            break
        take = min(max(demand, 0), remain)
        alloc[b] += take
        remain -= take
    for capacity, b in sorted(
        ((caps.get(b, 0) - alloc[b], b) for b in buckets), key=lambda x: (-x[0], x[1])
    ):
        if remain <= 0:
            break
        take = min(capacity, remain)
        alloc[b] += take
        remain -= take
    return {b: count for b, count in alloc.items() if count}


def match_family(
    family: str, rows: list[dict], details: dict, threshold: float
) -> dict:
    """One family's D set and matched C set; pure, deterministic, no RNG.

    Priority order when the witness-poor pool cannot fill the quota: family
    quota (exact) > empty-answer fraction (exact when the pool allows) >
    answer-size histogram (matched, deviations recorded) > length (recorded;
    the pilot bank's worlds are near-uniform ~64K).
    """
    frac = lambda r: details[r["example_id"]]["distinguished_fraction"]
    size = lambda r: answer_size(family, details[r["example_id"]]["answer"])
    empty = lambda r: is_empty_answer(details[r["example_id"]]["answer"])

    rich = [r for r in rows if frac(r) >= threshold]
    poor = [r for r in rows if frac(r) < threshold]
    quota = len(rich)
    # witness-poor rows first: lowest fraction, then hash order
    c = sorted(poor, key=lambda r: (frac(r), _row_hash(r)))[:quota]
    chosen = {r["example_id"] for r in c}
    if len(c) < quota:
        # empty-answer alignment: bring C's empty count up to D's
        d_empty = sorted((r for r in rich if empty(r)), key=_row_hash)
        fill_empty = max(
            0, min(quota - len(c), len(d_empty) - sum(1 for r in c if empty(r)))
        )
        for r in d_empty[:fill_empty]:
            c.append(r)
            chosen.add(r["example_id"])
        # answer-size alignment for the remainder, capped at pool availability
        remaining = quota - len(c)
        if remaining > 0:
            pool = [r for r in rich if r["example_id"] not in chosen]
            d_hist = Counter(size(r) for r in rich)
            c_hist = Counter(size(r) for r in c)
            pool_hist = Counter(size(r) for r in pool)
            residual = {b: d_hist[b] - c_hist[b] for b in d_hist}
            weights = {b: v for b, v in residual.items() if v > 0} or dict(pool_hist)
            alloc = _largest_remainder(remaining, weights, pool_hist)
            if sum(alloc.values()) != remaining:
                raise SystemExit(
                    f"{family}: size-bucket allocation filled "
                    f"{sum(alloc.values())} of {remaining}; pool exhausted"
                )
            by_bucket = defaultdict(list)
            for r in pool:
                by_bucket[size(r)].append(r)
            for b in sorted(by_bucket):
                for r in sorted(by_bucket[b], key=_row_hash)[: alloc.get(b, 0)]:
                    c.append(r)
                    chosen.add(r["example_id"])
    if len(c) != quota:
        raise SystemExit(f"{family}: C filled {len(c)} of quota {quota}")
    # export order is the bank's row order, not the selection order
    rich_ids = {r["example_id"] for r in rich}
    return {
        "d": [r for r in rows if r["example_id"] in rich_ids],
        "c": [r for r in rows if r["example_id"] in chosen],
        "poor": poor,
    }


def _quartiles(values: list[int]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)

    def at(fraction: float) -> int:
        return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]

    return {
        "min": ordered[0],
        "p25": at(0.25),
        "median": at(0.5),
        "p75": at(0.75),
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 1),
    }


def _family_stats(
    rows_arm: list[dict], details: dict, family: str, length_of, threshold: float
) -> dict:
    sizes = Counter(
        answer_size(family, details[r["example_id"]]["answer"]) for r in rows_arm
    )
    empties = sum(
        1 for r in rows_arm if is_empty_answer(details[r["example_id"]]["answer"])
    )
    worlds = Counter(r["world_id"] for r in rows_arm)
    return {
        "rows": len(rows_arm),
        "from_poor": sum(
            1
            for r in rows_arm
            if details[r["example_id"]]["distinguished_fraction"] < threshold
        ),
        "from_rich": sum(
            1
            for r in rows_arm
            if details[r["example_id"]]["distinguished_fraction"] >= threshold
        ),
        "empty_rows": empties,
        "empty_fraction": round(empties / len(rows_arm), 4) if rows_arm else 0.0,
        "answer_size_histogram": dict(sorted(sizes.items())),
        "worlds": len(worlds),
        "rows_per_world": (
            {"min": min(worlds.values()), "max": max(worlds.values())} if worlds else {}
        ),
        "length": _quartiles([length_of(r) for r in rows_arm]),
    }


def _messages(row: dict) -> list[dict]:
    """The bank's chat shape (gates' measurement recipe): context + QUESTION
    header + instruction; canonical JSON answer."""
    return [
        {
            "role": "user",
            "content": row["context"] + "\n\nQUESTION\n" + row["instruction"],
        },
        {"role": "assistant", "content": canonical(row["answer"])},
    ]


def _measure_row(payload: tuple) -> dict:
    example_id, context, instruction, answer_text = payload
    return {
        "example_id": example_id,
        **measure_tokens(context, instruction, answer_text),
    }


def _write_arm(
    bank: Path,
    arm: str,
    rows: list[dict],
    details: dict,
    tokens_by_id: dict | None,
) -> dict:
    arm_dir = bank / "arms" / arm
    arm_dir.mkdir(parents=True)
    train_path = arm_dir / "train.jsonl"
    index_path = arm_dir / "sample_index.jsonl"
    with train_path.open("x") as stream:
        for row_index, row in enumerate(rows):
            new_row = dict(row)
            new_row["messages"] = _messages(row)
            new_row["arm"] = arm
            new_row["distinguished_fraction"] = details[row["example_id"]][
                "distinguished_fraction"
            ]
            tokens = tokens_by_id.get(row["example_id"]) if tokens_by_id else None
            if tokens:
                new_row.update(
                    {
                        "full_chat_tokens": tokens["full_chat_tokens"],
                        "input_tokens": tokens["input_tokens"],
                        "supervised_tokens": tokens["supervised_tokens"],
                    }
                )
            stream.write(json.dumps(new_row, ensure_ascii=False, sort_keys=True) + "\n")
    with index_path.open("x") as stream:
        for row_index, row in enumerate(rows):
            entry = {
                "example_id": row["example_id"],
                "semantic_task_id": row["semantic_task_id"],
                "world_id": row["world_id"],
                "group_id": row["group_id"],
                "family": row["family"],
                "split": "train",
                "language": "en",
                "renderer": "jsonl",
                "consumed_records": row["consumed_count"],
                "distinguished_fraction": details[row["example_id"]][
                    "distinguished_fraction"
                ],
                "arm": arm,
                "output_file": "train.jsonl",
                "row_index": row_index,
                "admission_status": "completed",
            }
            if tokens_by_id:
                tokens = tokens_by_id[row["example_id"]]
                entry["input_tokens"] = tokens["input_tokens"]
                entry["supervised_tokens"] = tokens["supervised_tokens"]
                entry["full_message_tokens"] = tokens["full_chat_tokens"]
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "train_rows": len(rows),
        "train_sha256": sha(train_path),
        "index_sha256": sha(index_path),
    }


def _arm_budget(rows: list[dict], tokens_by_id: dict | None) -> dict:
    budget = derive_budget_report_rows(rows)
    receipt = {
        "budget_recommendation": budget["budget_recommendation"],
        "budget_four_caliber": {
            "rows": len(rows),
            "supervised_tokens_est_chars_over_3_8": sum(
                max(1, round(len(canonical(r["answer"])) / 3.8)) for r in rows
            ),
            "steps_at_1_epoch": budget["budget_recommendation"]["steps_at_1_epoch"],
        },
        "tokens_measured": tokens_by_id is not None,
    }
    if tokens_by_id:
        full = sum(tokens_by_id[r["example_id"]]["full_chat_tokens"] for r in rows)
        inputs = sum(tokens_by_id[r["example_id"]]["input_tokens"] for r in rows)
        sup = sum(tokens_by_id[r["example_id"]]["supervised_tokens"] for r in rows)
        receipt["budget_four_caliber"].update(
            {"input_tokens": inputs, "supervised_tokens": sup}
        )
        receipt["full_chat_tokens"] = full
        receipt["supervised_fraction"] = round(sup / full, 6)
    return receipt


def derive_budget_report_rows(rows: list[dict]) -> dict:
    """derive_budget_report over in-memory rows (it reads files by design).

    The shape-masking logic is reused verbatim by materializing the arm's
    rows into a scratch file; keeping one derivation means the step math
    cannot drift between the p71 arms and these. The scratch file lives in
    the project's tmp/ when present (workspace storage rules) and the
    system default otherwise, and is always removed.
    """
    import tempfile

    scratch_dir = str(ROOT / "tmp") if (ROOT / "tmp").is_dir() else None
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, dir=scratch_dir
    ) as stream:
        for row in rows:
            stream.write(
                json.dumps(
                    {"messages": _messages(row)}, ensure_ascii=False, sort_keys=True
                )
                + "\n"
            )
        path = Path(stream.name)
    try:
        return derive_budget_report([path], GBS)
    finally:
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--no-measure-tokens",
        action="store_true",
        help="skip tokenizer measurement (smoke runs); token fields are omitted",
    )
    args = parser.parse_args()
    bank = args.bank

    rows = [json.loads(line) for line in (bank / "train.jsonl").open()]
    witness = json.loads((bank / "verification.witness.json").read_text())
    details = witness["example_details"]
    unmatched = [r["example_id"] for r in rows if r["example_id"] not in details]
    if unmatched:
        raise SystemExit(
            f"{len(unmatched)} train rows missing witness detail: {unmatched[:5]}"
        )
    mismatched = [
        r["example_id"]
        for r in rows
        if details[r["example_id"]]["answer"] != r["answer"]
    ]
    if mismatched:
        raise SystemExit(
            f"{len(mismatched)} rows disagree with the witness answer: {mismatched[:5]}"
        )

    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    families = sorted(by_family)
    matched = {
        fam: match_family(fam, by_family[fam], details, args.threshold)
        for fam in families
    }
    d_ids = {r["example_id"] for fam in families for r in matched[fam]["d"]}
    c_ids = {r["example_id"] for fam in families for r in matched[fam]["c"]}
    arm_rows = {
        "d": [r for r in rows if r["example_id"] in d_ids],
        "c": [r for r in rows if r["example_id"] in c_ids],
    }

    tokens_by_id = None
    if not args.no_measure_tokens:
        union = [r for r in rows if r["example_id"] in d_ids | c_ids]
        payloads = [
            (r["example_id"], r["context"], r["instruction"], canonical(r["answer"]))
            for r in union
        ]
        if args.workers > 1:
            with ProcessPoolExecutor(
                max_workers=args.workers, initializer=_init_worker
            ) as pool:
                measured = list(pool.map(_measure_row, payloads, chunksize=8))
        else:
            _init_worker()
            measured = [_measure_row(p) for p in payloads]
        tokens_by_id = {m["example_id"]: m for m in measured}
        full = sum(m["full_chat_tokens"] for m in measured)
        sup = sum(m["supervised_tokens"] for m in measured)
        print(
            f"measured {len(measured)} rows: full_chat_tokens={full:,} "
            f"supervised_tokens={sup:,}"
        )

    # matched stats: length proxy is full-chat tokens when measured, else
    # context characters (the smoke path still gets a comparable distribution)
    length_of = (
        (lambda r: tokens_by_id[r["example_id"]]["full_chat_tokens"])
        if tokens_by_id
        else (lambda r: len(r["context"]))
    )
    length_key = "full_chat_tokens" if tokens_by_id else "context_chars"
    matched_stats = {}
    notes = []
    for fam in families:
        m = matched[fam]
        quota = len(m["d"])
        fam_d = [r for r in arm_rows["d"] if r["family"] == fam]
        fam_c = [r for r in arm_rows["c"] if r["family"] == fam]
        stats_d = _family_stats(fam_d, details, fam, length_of, args.threshold)
        stats_c = _family_stats(fam_c, details, fam, length_of, args.threshold)
        for side, stats in (("d", stats_d), ("c", stats_c)):
            stats["length"] = {length_key: stats["length"]}
        matched_stats[fam] = {"quota": quota, "d": stats_d, "c": stats_c}
        poor = len(m["poor"])
        if quota == 0:
            notes.append(
                f"{fam}: no train row clears threshold {args.threshold}; both arms empty"
            )
        elif poor == 0:
            notes.append(
                f"{fam}: zero witness-poor train rows at threshold {args.threshold}; "
                "C is a matched subset of D (overlap 100% for this family)"
            )
        elif poor < quota:
            notes.append(
                f"{fam}: witness-poor pool {poor} < quota {quota}; C filled with "
                f"{stats_c['from_rich']} rows drawn from D (recorded, not forced)"
            )
        if stats_d["empty_rows"] != stats_c["empty_rows"]:
            notes.append(
                f"{fam}: empty-answer match infeasible — D {stats_d['empty_rows']} vs "
                f"C {stats_c['empty_rows']} (poor-pool empties exceed D's)"
            )
        drift = {
            b: stats_c["answer_size_histogram"].get(b, 0)
            - stats_d["answer_size_histogram"].get(b, 0)
            for b in set(stats_d["answer_size_histogram"])
            | set(stats_c["answer_size_histogram"])
        }
        if any(drift.values()):
            notes.append(f"{fam}: answer-size histogram drift {drift}")

    overlap_rows = c_ids & d_ids
    per_family_overlap = {
        fam: {
            "rows": len(
                {r["example_id"] for r in matched[fam]["c"]}
                & {r["example_id"] for r in matched[fam]["d"]}
            ),
            "fraction_of_c": round(
                len(
                    {r["example_id"] for r in matched[fam]["c"]}
                    & {r["example_id"] for r in matched[fam]["d"]}
                )
                / max(1, len(matched[fam]["c"])),
                4,
            ),
        }
        for fam in families
    }

    receipts = {}
    for arm in ("c", "d"):
        receipt = _write_arm(bank, arm, arm_rows[arm], details, tokens_by_id)
        receipt.update(_arm_budget(arm_rows[arm], tokens_by_id))
        receipt["worlds"] = len({r["world_id"] for r in arm_rows[arm]})
        receipts[arm] = receipt

    if tokens_by_id:
        means = {
            fam: (
                matched_stats[fam]["d"]["length"][length_key]["mean"],
                matched_stats[fam]["c"]["length"][length_key]["mean"],
            )
            for fam in families
        }
        worst = max((abs(a - b) / max(a, 1), fam) for fam, (a, b) in means.items())
        notes.append(
            "length distortion check (worst per-family mean ratio C/D): "
            f"{worst[1]} {worst[0]:.3f}"
        )
    notes.append(
        f"C∪D covers {len(c_ids | d_ids)}/{len(rows)} train rows; "
        "eval.jsonl is never read by this export"
    )

    receipt = {
        "schema_version": "longworld.p73-arms.v1",
        "bank": str(bank),
        "bank_train_sha256": sha(bank / "train.jsonl"),
        "witness_sha256": sha(bank / "verification.witness.json"),
        "threshold": args.threshold,
        "selection_rule": {
            "d": (
                "train rows with distinguished_fraction >= threshold "
                "(eval rows excluded)"
            ),
            "c": (
                "per family, quota = |D|; witness-poor rows (fraction < threshold) "
                "first in (fraction, sha256(family|example_id)) order, then a fill "
                "from D's rows: empty answers first to match D's empty count, the "
                "remainder by largest-remainder allocation over answer-size buckets "
                "capped at pool availability, hash order inside buckets"
            ),
            "determinism": "no RNG; every ordering is an explicit sort key",
            "budget_calibers": (
                "rows / input_tokens / supervised_tokens / steps_at_1_epoch, plus "
                "full_chat_tokens, supervised_fraction and the chars-over-3.8 "
                "estimate for continuity with verification.gates.json"
            ),
        },
        "arms": receipts,
        "matched": matched_stats,
        "overlap": {
            "rows": len(overlap_rows),
            "fraction_of_c": round(len(overlap_rows) / max(1, len(c_ids)), 4),
            "fraction_of_d": round(len(overlap_rows) / max(1, len(d_ids)), 4),
            "per_family": per_family_overlap,
            "note": (
                "overlap is the honest consequence of thin witness-poor pools, not "
                "a design error: C's contrast to D lives in the poor rows it does "
                "capture plus the recorded matching stats"
            ),
        },
        "honesty": {
            **(rows[0].get("honesty", {}) if rows else {}),
            "notes": notes,
        },
    }
    if tokens_by_id:
        receipt["tokenizer"] = {"model": MODEL, "revision": REVISION}
    arms_path = bank / "arms.json"
    with arms_path.open("x") as stream:
        stream.write(json.dumps(receipt, indent=1, sort_keys=True) + "\n")

    for arm in ("c", "d"):
        r = receipts[arm]
        calibers = r["budget_four_caliber"]
        print(
            f"{arm}: rows={r['train_rows']} worlds={r['worlds']} "
            f"input_tokens={calibers.get('input_tokens', '-')} "
            f"supervised_tokens={calibers.get('supervised_tokens', '-')} "
            f"full_chat_tokens={r.get('full_chat_tokens', '-')}"
        )
    print(
        f"overlap: {len(overlap_rows)} rows "
        f"({receipt['overlap']['fraction_of_c']:.1%} of C)"
    )
    for note in notes:
        print(f"note: {note}")
    print(f"wrote {arms_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
