#!/usr/bin/env python3
"""Field-level decomposition of the supervision-token contract drop.

P73 measured full-provenance vs answer-only supervised tokens over the same
3,972 arm_a rows (2,802,609 -> 395,793). That 85.9% drop is so far only
"fields were removed"; the P74 directive requires attributing it to WHAT was
removed before it may be cited as "ID enumeration share".

Method (exact, no RNG): both exports serialize answers as compact sorted-key
JSON, rows aligned by order. For each row pair, tokenize the FULL assistant
string with offset mapping; locate the char ranges of fields present in full
but absent in answer-only (bracket-balancing scanner); classify every token
whose span overlaps a removed range by what its characters actually are:

- id        : characters inside an id-like string (r/k-prefixed hex, unit-*)
- scalar    : characters inside a bare number
- key/label : characters inside a quoted non-id dict key (e.g. breakdown
              category names) — kept-field names inside removed fragments
              (the "matched":/"pairs": key names) count as structural
- structural: quotes, brackets, commas, removed-field key names

Tokens straddling the removed/kept boundary cannot be attributed exactly;
their count is reported as its own bucket (tokenization-boundary residue)
rather than being silently absorbed. The row-level measured drop
(len(tok(full)) - len(tok(only))) is reported alongside as the ground truth
the classified buckets reconcile against.

Usage:
  python scripts/decompose_supervision.py \
    --full data/capability_records/p73_contract_arms/full-provenance/train.jsonl \
    --only data/capability_records/p73_contract_arms/answer-only/train.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODEL = "Qwen/Qwen3.5-4B"
REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"

# Id surfaces actually used by the capability spine: r/k-prefixed hex of
# varying length (configs emit 8-24 hex chars) and unit-/ship-style ids.
_ID_RE = re.compile(r"\b(?:[rk][0-9a-f]{8,}|unit-[0-9a-f]{3,})\b")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _assistant(row: dict) -> str:
    content = row["messages"][-1]["content"]
    return content if isinstance(content, str) else json.dumps(content)


def _removed_ranges(full: str, full_obj: dict, only_obj: dict) -> list[tuple[int, int]]:
    """Char ranges in `full` of the fields answer-only dropped.

    Top-level and one-level-nested removals (group_compare drops
    left.records/right.records). A bracket-balancing scan locates each
    removed key's exact value substring.
    """

    def scan_value(text: str, colon_end: int) -> tuple[int, int]:
        i = colon_end
        while text[i] in " \n":
            i += 1
        if text[i] == '"':
            j = text.index('"', i + 1)
            return i, j + 1
        depth = 0
        in_str = False
        escape = False
        for j in range(i, len(text)):
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
                if depth == 0:
                    return i, j + 1
            elif depth == 0 and ch == ",":
                return i, j
        return i, len(text)

    def find_key_value(text: str, key: str) -> tuple[int, int] | None:
        marker = f'"{key}":'
        start = 0
        while True:
            at = text.find(marker, start)
            if at < 0:
                return None
            # crude guard: not inside a string (compact JSON: keys are at
            # value boundaries; a hit preceded by an odd quote count is not
            # a key position in our sorted-compact serialization)
            prefix = text[:at]
            if prefix.count('"') % 2 == 0:
                return scan_value(text, at + len(marker))
            start = at + 1

    ranges: list[tuple[int, int]] = []
    for key in sorted(full_obj):
        if key in only_obj:
            fv, ov = full_obj[key], only_obj[key]
            if isinstance(fv, dict) and isinstance(ov, dict):
                for sub in sorted(fv):
                    if sub not in ov:
                        hit = find_key_value(full, sub)
                        # include the "sub": key text: find_key_value scans
                        # from the colon; extend back over the key name
                        if hit:
                            key_at = full.rfind(f'"{sub}":', 0, hit[0])
                            if key_at >= 0:
                                ranges.append((key_at, hit[1]))
            continue
        hit = find_key_value(full, key)
        if hit is None:
            continue
        key_at = full.rfind(f'"{key}":', 0, hit[0])
        ranges.append((key_at if key_at >= 0 else hit[0], hit[1]))
    ranges.sort()
    return ranges


def _classify(full: str, ranges: list[tuple[int, int]], offsets) -> dict[str, int]:
    """Token -> semantic-region attribution.

    Hex ids tokenize into single characters, so per-token regexes would miscount
    them as scalars/keys. Instead build semantic CHAR regions first, then a
    token's bucket is the region containing the majority of its chars (a token
    fully inside a removed id string region is an id token; the wrapper
    quotes/commas of removed fragments are structural).
    """

    # semantic char regions: id strings, quoted non-id strings, bare numbers
    events: list[tuple[int, int, str]] = []
    for m in _ID_RE.finditer(full):
        events.append((m.start(), m.end(), "id"))
    for m in _NUMBER_RE.finditer(full):
        events.append((m.start(), m.end(), "number"))
    for m in re.finditer(r'"[^"\\]*(?:\\.[^"\\]*)*"', full):
        text = full[m.start() : m.end()]
        inner = text[1:-1]
        if not _ID_RE.fullmatch(inner):
            events.append((m.start(), m.end(), "string"))
    events.sort()
    # merge overlapping (id inside quotes wins by insertion order; id regions
    # and their wrapping quotes: treat whole quoted-id as id)
    regions: list[tuple[int, int, str]] = []
    for a, b, kind in events:
        if regions and a < regions[-1][1]:
            pa, pb, pk = regions[-1]
            if pk == "id" or kind == "id":
                regions[-1] = (pa, max(pb, b), "id")
            else:
                regions[-1] = (pa, max(pb, b), pk)
        else:
            regions.append((a, b, kind))

    def region_of(a: int, b: int) -> str | None:
        best, best_len = None, 0
        for ra, rb, rk in regions:
            inter = min(b, rb) - max(a, ra)
            if inter > best_len:
                best, best_len = rk, inter
        return best

    removed_flags = bytearray(len(full))
    for a, b in ranges:
        for k in range(a, b):
            removed_flags[k] = 1
    buckets = defaultdict(int)
    for a, b in offsets:
        if b <= a:
            continue
        overlap = sum(removed_flags[a:b])
        if overlap == 0:
            continue  # kept field: not part of the drop
        if overlap < (b - a):
            buckets["boundary_residue"] += 1
            continue
        kind = region_of(a, b)
        if kind == "id":
            buckets["id"] += 1
        elif kind == "number":
            buckets["scalar"] += 1
        else:
            buckets["key_label_structural"] += 1
    return buckets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--only", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(
        MODEL, revision=REVISION, local_files_only=True, trust_remote_code=False
    )
    per_family: dict[str, dict] = defaultdict(
        lambda: {
            "rows": 0,
            "measured_drop": 0,
            "id": 0,
            "scalar": 0,
            "key_label_structural": 0,
            "boundary_residue": 0,
            "rows_with_removals": 0,
            # contract swaps: answer-only REPLACES a full-side field with a
            # different one (asof single-entity variant: active:true ->
            # entities:null) — not a removal; counted separately, measured_drop
            # for such rows can be negative and that is real
            "swapped_rows": 0,
            "only_side_added_tokens": 0,
        }
    )
    with args.full.open() as fh_full, args.only.open() as fh_only:
        for line_full, line_only in zip(fh_full, fh_only):
            rf = json.loads(line_full)
            ro = json.loads(line_only)
            if rf.get("example_id") != ro.get("example_id"):
                sys.exit("row misalignment between the two exports")
            fam = rf["family"]
            stats = per_family[fam]
            stats["rows"] += 1
            s_full = _assistant(rf)
            s_only = _assistant(ro)
            stats["measured_drop"] += len(tok(s_full)["input_ids"]) - len(
                tok(s_only)["input_ids"]
            )
            try:
                full_obj = json.loads(s_full)
                only_obj = json.loads(s_only)
            except (json.JSONDecodeError, TypeError):
                stats["boundary_residue"] += len(tok(s_full)["input_ids"]) - len(
                    tok(s_only)["input_ids"]
                )
                continue
            if not isinstance(full_obj, dict) or not isinstance(only_obj, dict):
                continue
            ranges = _removed_ranges(s_full, full_obj, only_obj)
            if not ranges:
                if s_full != s_only:
                    stats["swapped_rows"] += 1
                    stats["only_side_added_tokens"] += max(
                        0,
                        len(tok(s_only)["input_ids"]) - len(tok(s_full)["input_ids"]),
                    )
                continue
            stats["rows_with_removals"] += 1
            enc = tok(s_full, return_offsets_mapping=True, add_special_tokens=False)
            buckets = _classify(s_full, ranges, enc["offset_mapping"])
            for k, v in buckets.items():
                stats[k] += v
    totals = {
        k: sum(s[k] for s in per_family.values())
        for k in (
            "rows",
            "measured_drop",
            "id",
            "scalar",
            "key_label_structural",
            "boundary_residue",
            "rows_with_removals",
        )
    }
    report = {
        "rows": totals["rows"],
        "measured_supervised_drop": totals["measured_drop"],
        "classifiable_tokens": {
            "id": totals["id"],
            "scalar": totals["scalar"],
            "key_label_structural": totals["key_label_structural"],
            "boundary_residue": totals["boundary_residue"],
        },
        "per_family": {
            fam: dict(sorted(s.items())) for fam, s in sorted(per_family.items())
        },
    }
    drop = max(1, totals["measured_drop"])
    print(f"rows: {totals['rows']}  measured drop: {totals['measured_drop']}")
    for k in ("id", "scalar", "key_label_structural", "boundary_residue"):
        v = totals[k]
        print(f"  {k:24s} {v:9d}  ({v / drop:.1%} of drop)")
    for fam, s in sorted(per_family.items()):
        if s["measured_drop"]:
            print(
                f"{fam:20s} drop={s['measured_drop']:8d} "
                f"id={s['id']:8d} scalar={s['scalar']:6d} "
                f"key/struct={s['key_label_structural']:6d} "
                f"residue={s['boundary_residue']:5d} rows_with_removal={s['rows_with_removals']}"
            )
    out = args.out or Path(
        "/volume/pt-dev/qjiu/.claude/jobs/3046b56b/tmp/supervision_decomposition.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
