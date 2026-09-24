#!/usr/bin/env python3
"""Generate the P73 v2 multi-band shared-world bank (8K/32K/64K targets).

v1 (scripts/generate_p73_pilot.py) landed every world in ONE ~64K cluster —
67-92K full-chat tokens, zero worlds at 8K or 32K (see .hl/p73_execution.md
and the v1 verification.gates.json length_band_verdict). This generator gives
each band its own capacity-driven length_records, calibrated per
(band, family group) with the pinned Qwen tokenizer over the bank's own
messages layout (user: context + QUESTION + instruction; assistant: canonical
answer). The calibration probes ran 3 then 5 seeded probe worlds per
(band, side, L) through the exact same sw.build_shared_world path
(consumed_per_family=20, depth=2) and measured each world's mean full-chat
tokens; the probe scripts live in job tmp, the measured table is embedded
below and lands in the manifest.

Honest infeasibility: the 8K band is INFEASIBLE for both family groups. The
shared-world builder's per-family floor (`max(64, L // n_families)`) puts the
smallest legal 3-family records world at 17.6K-21.9K full-chat tokens and the
smallest 4-family F world at ~26.9K (and 4/5 minimum-L F seeds fail
generation outright: asof_state's distractor budget cannot host K=5 at
per-family L=64). 8K*1.25 = 10,240 tokens is unreachable by length_records
reduction while keeping the multi-family shared-world contract (families and
groups are unchanged from v1 by design), so the 8K cells are recorded as
infeasible in the manifest, never forced.

Seeds: base 862000 with a +30 clearance — v1's slots are 861000-861020
(records) and 862000-862020 (F), so band 0 group 0 cannot start at 862000
itself. Layout: 862030 + band_seed_offset + group_index*200 + w, with
32K offset 250 and 64K offset 500, keeping every v2 seed in the 862xxx
space the wave assigned, collision-free against v1 and the probe seeds.

World-level split (seed % 5 == 0 -> eval) and the two family groups are the
v1 contract, unchanged.

Usage:
  python scripts/generate_p73_pilot_v2.py --out data/capability_records/p73_shared_v2 \
      [--worlds 14]
(--worlds is worlds PER BAND, split across the two family groups.)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import capability_shared_world as sw  # noqa: E402

CONFIG = {
    "groups": [
        ("filter_aggregate", "group_compare", "join_lookup"),
        ("alias_locate", "asof_state", "rule_holdout", "set_complete"),
    ],
    "consumed_per_family": 20,
    "depth": 2,
}

# One entry per band. `length_records` is per family group (index into
# CONFIG["groups"]); a band that cannot be hit honestly carries status
# "infeasible" and generates zero worlds.
BANDS = [
    {
        "band": "8K",
        "target_full_chat_tokens": 8192,
        "seed_offset": 0,
        "length_records": [None, None],
        "status": "infeasible",
    },
    {
        "band": "32K",
        "target_full_chat_tokens": 32768,
        "seed_offset": 250,
        "length_records": [310, 320],
        "status": "feasible",
    },
    {
        "band": "64K",
        "target_full_chat_tokens": 65536,
        "seed_offset": 500,
        "length_records": [670, 720],
        "status": "feasible",
    },
]

SEED_BASE = 862000
# v1's F-side seeds occupy 862000-862020; the clearance lifts the first v2
# slot past that block, and the band offsets (32K 250, 64K 500) keep every
# v2 seed inside the 862xxx base (max 862030 + 500 + 200 + 13 = 862743).
SEED_CLEARANCE = 30

# Calibration evidence, measured 2026-09-24 with the pinned tokenizer
# (Qwen/Qwen3.5-4B, revision a7b0d22b993d71000cf2eadfb37222a67cee521e)
# through the runner's measure_tokens over the bank's messages layout.
# Probe seeds round 1 (3 worlds/point): 863500 + k*977, k=0..2;
# round 2 (5 worlds/point): 863500 + k*977, k=0..4. World tokens are the
# mean full-chat tokens over the world's rows.
CALIBRATION = {
    "method": (
        "probe worlds built with the exact bank path (sw.build_shared_world, "
        "consumed_per_family=20, depth=2), each world's mean full-chat tokens "
        "measured with the pinned tokenizer via the runner's measure_tokens "
        "over the bank messages layout (user: context + QUESTION + "
        "instruction; assistant: canonical answer)"
    ),
    "probe_seed_base": 863500,
    "probe_seed_stride": 977,
    "probe_seeds_round2": [863500, 864477, 865454, 866431, 867408],
    "tokenizer": {
        "model_id": "Qwen/Qwen3.5-4B",
        "revision": "a7b0d22b993d71000cf2eadfb37222a67cee521e",
    },
    # Round-1 coarse grid (3 probe worlds per point), floored entries
    # deduplicated: the per-family floor makes every records-side L<=192
    # request identical, and every F-side L<=256 request identical.
    "coarse_grid": [
        {
            "band": "8K",
            "side": "records",
            "length_records": 64,
            "median_full_chat_tokens": 20529,
            "worlds_failed": 0,
            "note": "per-family floor 64 -> identical for every L<=192 request",
        },
        {
            "band": "8K",
            "side": "records",
            "length_records": 256,
            "median_full_chat_tokens": 26656,
            "worlds_failed": 0,
        },
        {
            "band": "8K",
            "side": "F",
            "length_records": 64,
            "median_full_chat_tokens": 26912,
            "worlds_failed": 2,
            "note": "per-family floor 64 -> identical for every L<=256 request",
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 320,
            "median_full_chat_tokens": 33470,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 350,
            "median_full_chat_tokens": 36461,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 380,
            "median_full_chat_tokens": 39844,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 410,
            "median_full_chat_tokens": 42471,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 440,
            "median_full_chat_tokens": 45130,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 470,
            "median_full_chat_tokens": 47757,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 320,
            "median_full_chat_tokens": 32574,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 350,
            "median_full_chat_tokens": 35179,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 380,
            "median_full_chat_tokens": 38212,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 410,
            "median_full_chat_tokens": 40856,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 440,
            "median_full_chat_tokens": 40943,
            "worlds_failed": 0,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 470,
            "median_full_chat_tokens": 43383,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 620,
            "median_full_chat_tokens": 60943,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 650,
            "median_full_chat_tokens": 63578,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 680,
            "median_full_chat_tokens": 66248,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 710,
            "median_full_chat_tokens": 68881,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 740,
            "median_full_chat_tokens": 71502,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "F",
            "length_records": 640,
            "median_full_chat_tokens": 58893,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "F",
            "length_records": 670,
            "median_full_chat_tokens": 61350,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "F",
            "length_records": 700,
            "median_full_chat_tokens": 64199,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "F",
            "length_records": 730,
            "median_full_chat_tokens": 64901,
            "worlds_failed": 0,
        },
        {
            "band": "64K",
            "side": "F",
            "length_records": 760,
            "median_full_chat_tokens": 67653,
            "worlds_failed": 0,
        },
    ],
    # Round-2 final candidates (5 probe worlds per point); the chosen L per
    # (band, side) is the entry whose median sits closest to the band target.
    "final_candidates": [
        {
            "band": "32K",
            "side": "records",
            "length_records": 300,
            "world_mean_full_chat_tokens": [31966, 34497, 30528, 32182, 31494],
            "median_full_chat_tokens": 31966,
            "chosen": False,
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 310,
            "world_mean_full_chat_tokens": [32698, 35694, 31288, 33650, 32435],
            "median_full_chat_tokens": 32435,
            "chosen": True,
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 320,
            "world_mean_full_chat_tokens": [33470, 36588, 32002, 34484, 33224],
            "median_full_chat_tokens": 33224,
            "chosen": False,
        },
        {
            "band": "32K",
            "side": "records",
            "length_records": 330,
            "world_mean_full_chat_tokens": [33949, 35412, 35769, 33657, 35424],
            "median_full_chat_tokens": 35412,
            "chosen": False,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 310,
            "world_mean_full_chat_tokens": [29076, 31419, 31882, 26359, 32707],
            "median_full_chat_tokens": 31419,
            "chosen": False,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 320,
            "world_mean_full_chat_tokens": [30125, 32574, 33025, 27300, 33892],
            "median_full_chat_tokens": 32574,
            "chosen": True,
        },
        {
            "band": "32K",
            "side": "F",
            "length_records": 330,
            "world_mean_full_chat_tokens": [30820, 33326, 33787, 27942, 34672],
            "median_full_chat_tokens": 33326,
            "chosen": False,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 660,
            "world_mean_full_chat_tokens": [66121, 63770, 64678, 66632, 58188],
            "median_full_chat_tokens": 64678,
            "chosen": False,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 670,
            "world_mean_full_chat_tokens": [66932, 64551, 65456, 67422, 58915],
            "median_full_chat_tokens": 65456,
            "chosen": True,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 680,
            "world_mean_full_chat_tokens": [67746, 65345, 66248, 68230, 59656],
            "median_full_chat_tokens": 66248,
            "chosen": False,
        },
        {
            "band": "64K",
            "side": "records",
            "length_records": 690,
            "world_mean_full_chat_tokens": [68767, 66415, 67301, 69259, 60632],
            "median_full_chat_tokens": 67301,
            "chosen": False,
        },
        {
            "band": "64K",
            "side": "F",
            "length_records": 720,
            "world_mean_full_chat_tokens": [63922, 66010, 64220, 66101, 73156],
            "median_full_chat_tokens": 66010,
            "chosen": True,
        },
        {
            "band": "64K",
            "side": "F",
            "length_records": 730,
            "world_mean_full_chat_tokens": [64594, 66717, 64901, 66816, 73939],
            "median_full_chat_tokens": 66717,
            "chosen": False,
        },
        {
            "band": "64K",
            "side": "F",
            "length_records": 740,
            "world_mean_full_chat_tokens": [65619, 67772, 65939, 67884, 75133],
            "median_full_chat_tokens": 67772,
            "chosen": False,
        },
    ],
    # 8K infeasibility evidence: the smallest shared worlds each side can
    # build at all (per-family floor 64), measured over 5 probe seeds.
    "eight_k_floor": [
        {
            "case": "floor-records-3fam",
            "requested_length_records": 192,
            "worlds_ok": 5,
            "worlds_failed": 0,
            "world_mean_full_chat_tokens": [20447, 21901, 20529, 17620, 19859],
            "note": "every records-side L<=192 request collapses to this floor",
        },
        {
            "case": "floor-F-4fam",
            "requested_length_records": 256,
            "worlds_ok": 1,
            "worlds_failed": 4,
            "world_mean_full_chat_tokens": [26912],
            "note": (
                "every F-side L<=256 request collapses to this floor; 4/5 "
                "seeds fail generation (asof_state cannot host K=5 at "
                "per-family L=64)"
            ),
        },
    ],
    "eight_k_single_family_reference": (
        "single-family worlds at L=64 measure ~4.8-8.3K tokens, but a "
        "single-family group is not a shared world; the v1 family groups are "
        "unchanged in v2 by design, so that path is not a legal 8K fallback"
    ),
}

INFEASIBLE_CELLS = [
    {
        "band": "8K",
        "group_index": 0,
        "side": "records",
        "target_full_chat_tokens": 8192,
        "band_ceiling_tokens": 10240,
        "reason": (
            "the shared-world per-family floor (64 rows) floors the smallest "
            "3-family merged world at 17,620-21,901 full-chat tokens across 5 "
            "probe seeds; 8K*1.25 = 10,240 is unreachable by length_records "
            "reduction without abandoning multi-family sharing"
        ),
    },
    {
        "band": "8K",
        "group_index": 1,
        "side": "F",
        "target_full_chat_tokens": 8192,
        "band_ceiling_tokens": 10240,
        "reason": (
            "the smallest 4-family F world measures 26,912 tokens and 4/5 "
            "minimum-L probe seeds fail generation entirely (asof_state's "
            "distractor budget cannot host K=5 at per-family L=64); 8K*1.25 "
            "= 10,240 is unreachable"
        ),
    },
]


def band_seed(band: dict, group_index: int, w: int) -> int:
    """The v2 seed layout: base + clearance + band offset + group + world."""
    return SEED_BASE + SEED_CLEARANCE + band["seed_offset"] + group_index * 200 + w


def build_bank(out_dir: Path, worlds_per_band: int) -> dict:
    """Generate every feasible band's worlds; returns the manifest dict."""
    rows_out = []
    contexts_out = []
    worlds_out = []
    fam_counts: dict[str, int] = {}
    band_stats: dict[str, dict] = {}
    skipped: list[dict] = []
    n_worlds = 0
    for band in BANDS:
        if band["status"] != "feasible":
            continue
        per_group = max(1, worlds_per_band // len(CONFIG["groups"]))
        band_worlds = 0
        band_rows = 0
        for group_index, group in enumerate(CONFIG["groups"]):
            if not group:
                continue
            length_records = band["length_records"][group_index]
            for w in range(per_group):
                seed = band_seed(band, group_index, w)
                try:
                    world = sw.build_shared_world(
                        seed,
                        group,
                        length_records,
                        CONFIG["consumed_per_family"],
                        CONFIG["depth"],
                    )
                except ValueError as exc:
                    print(f"skip seed {seed}: {exc}", file=sys.stderr)
                    skipped.append(
                        {
                            "band": band["band"],
                            "group_index": group_index,
                            "w": w,
                            "seed": seed,
                            "error": str(exc),
                        }
                    )
                    continue
                n_worlds += 1
                band_worlds += 1
                worlds_out.append(world)
                contexts_out.append(
                    {
                        "world_id": world["world_id"],
                        "band": band["band"],
                        "context": world["context"],
                    }
                )
                for task in world["tasks"]:
                    fam_counts[task["family"]] = fam_counts.get(task["family"], 0) + 1
                    band_rows += 1
                    rows_out.append(
                        {
                            "schema_version": world["schema_version"],
                            "example_id": (f"{world['world_id']}:{task['task_id']}"),
                            "semantic_task_id": (
                                f"{world['world_id']}:{task['task_id']}"
                            ),
                            "world_id": world["world_id"],
                            "group_id": world["world_id"],
                            "family": task["family"],
                            "capability_level": task.get("capability_level"),
                            "band": band["band"],
                            "context_sha256": world["context_sha256"],
                            "context": world["context"],
                            "question": task["question"],
                            "instruction": task["instruction"],
                            "answer": task["answer"],
                            "consumed": task["consumed"],
                            "consumed_count": task["consumed_count"],
                            "shared_with": sorted(
                                set(
                                    t["family"]
                                    for t in world["tasks"]
                                    if t["family"] != task["family"]
                                )
                            ),
                            "honesty": world["honesty"],
                        }
                    )
                shard_dir = (
                    out_dir / "shards" / f"shared-{band['band']}-{group_index}-{w}"
                )
                shard_dir.mkdir(parents=True, exist_ok=True)
                shard_world = {
                    **world,
                    "band": band["band"],
                    "band_target_full_chat_tokens": band["target_full_chat_tokens"],
                    "requested_length_records": length_records,
                }
                (shard_dir / "world.json").write_text(
                    json.dumps(shard_world, ensure_ascii=False) + "\n"
                )
        band_stats[band["band"]] = {
            "worlds": band_worlds,
            "rows": band_rows,
            "length_records_by_group": {
                f"group_{i}": band["length_records"][i]
                for i in range(len(CONFIG["groups"]))
            },
            "target_full_chat_tokens": band["target_full_chat_tokens"],
        }
    # Split by WORLD (seed % 5 == 0 -> eval), the v1 contract: one world
    # lives in exactly one split, so group_id=world exposure accounting
    # stays leak-free and eval worlds spread over both family groups.
    eval_worlds = {w["world_id"] for w in worlds_out if w["seed"] % 5 == 0}
    train = [r for r in rows_out if r["world_id"] not in eval_worlds]
    eval_rows = [r for r in rows_out if r["world_id"] in eval_worlds]
    for name, rows in (("train.jsonl", train), ("eval.jsonl", eval_rows)):
        with (out_dir / name).open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out_dir / "contexts.jsonl").open("w") as fh:
        for c in contexts_out:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    manifest = {
        "schema_version": "longworld.p73-shared-bank.v2",
        "worlds": n_worlds,
        "rows": len(rows_out),
        "families": dict(sorted(fam_counts.items())),
        "split_rows": {
            split: len(rows) for split, rows in (("train", train), ("eval", eval_rows))
        },
        "split_basis": (
            "world_seed_mod_5: seed % 5 == 0 -> eval; one world lives in "
            "exactly one split, so group_id=world exposure accounting stays "
            "leak-free"
        ),
        "shared_context": "one context per world, multiple families (see shared_with)",
        "bands": {
            "config": [
                {
                    "band": b["band"],
                    "target_full_chat_tokens": b["target_full_chat_tokens"],
                    "status": b["status"],
                    "length_records_by_group": (
                        {
                            f"group_{i}": b["length_records"][i]
                            for i in range(len(CONFIG["groups"]))
                        }
                        if b["status"] == "feasible"
                        else None
                    ),
                }
                for b in BANDS
            ],
            "per_band": band_stats,
            "infeasible_cells": INFEASIBLE_CELLS,
        },
        "calibration": CALIBRATION,
        "seeds": {
            "base": SEED_BASE,
            "clearance": SEED_CLEARANCE,
            "layout": (
                "862030 + band_seed_offset + group_index*200 + w "
                "(32K offset 250, 64K offset 500); v1 occupied "
                "861000-861020 (records) and 862000-862020 (F), so the "
                "clearance keeps v2 collision-free inside the 862xxx base"
            ),
            "skipped": skipped,
        },
        "honesty": {
            "source_kind": "simulated",
            "strict_long_dependency_verified": False,
            "model_utility_measured": False,
            "production_eligible": False,
            "band_honesty": (
                "8K is infeasible for both family groups (recorded in "
                "bands.infeasible_cells, never forced); 32K/64K worlds are "
                "capacity-driven length_records, real measured lengths in "
                "verification.gates.json"
            ),
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--worlds",
        type=int,
        default=14,
        help="worlds per band, split across the two family groups",
    )
    args = parser.parse_args()
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_bank(out_dir, args.worlds)
    print(f"worlds={manifest['worlds']} rows={manifest['rows']}")
    print(json.dumps(manifest["families"], indent=0))
    print(
        "bands: "
        + json.dumps(
            {
                band: stats["worlds"]
                for band, stats in manifest["bands"]["per_band"].items()
            }
        )
    )
    if manifest["seeds"]["skipped"]:
        print(
            f"skipped seeds: {len(manifest['seeds']['skipped'])}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
