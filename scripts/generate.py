#!/usr/bin/env python3
"""Generate CausalTwin JSONL from mixed executable worlds (unique packing)."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.anchors import public_anchor_artifacts
from longworld.core.causal import build_causal_graph
from longworld.core.graph import (
    graph_stats,
    random_walk_event_ids,
    typed_walk_event_ids,
)
from longworld.core.pack import estimate_tokens, join_artifacts, pack_view, wrap_prompt
from longworld.core.sampler import materialize
from longworld.core.verify import ProofGraph, SampleRecord, verify_question
from longworld.core.views import memory_card, render_cf_view, split_views, view_answer


def load_cfg(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def flatten_parallel(mat) -> list:
    arts = []
    for k, v in mat.artifacts.items():
        if k == "focal":
            continue
        arts.extend(v)
    return arts


def extra_filler(
    seed: int, n: int, n_parallel: int, n_pulses: int, domain: str, domains: list[str]
) -> list:
    filler = []
    pool = list(domains) or [domain]
    off = pool.index(domain) if domain in pool else 0
    for i in range(n):
        d = pool[(off + i + 1) % len(pool)]
        extra = materialize(
            seed * 1_000_003 + 17 + i,
            n_parallel=n_parallel,
            n_pulses=n_pulses,
            domain=d,
        )
        for arts in extra.artifacts.values():
            filler.extend(arts)
    return filler


def _trainable(spec) -> bool:
    if "decoy" in spec.query_id:
        return False
    if spec.query_type in {"historical_state", "aggregation"}:
        return False
    return True


def _buckets_for_split(cfg: dict, split: str) -> dict[str, int]:
    buckets = dict(cfg.get("length_buckets", {"8k": 8000}))
    if split != "train":
        extra = dict(cfg.get("eval_length_buckets") or {})
        buckets.update(extra)
    return buckets


def emit_records(
    seed: int, cfg: dict, split: str
) -> tuple[list[dict], list[dict], dict]:
    n_parallel = int(cfg.get("n_parallel", 2))
    n_pulses = int(cfg.get("n_pulses", 14))
    domains = list(cfg.get("domains") or ["company"])
    domain = domains[seed % len(domains)]
    mat = materialize(seed, n_parallel=n_parallel, n_pulses=n_pulses, domain=domain)
    stats = {"attempts": 0, "kept_slots": 0, "n_clones": 0}
    rejects: list[dict] = []
    if not mat.scan_ok:
        return (
            [],
            [
                {
                    "seed": seed,
                    "world_id": mat.spec["world_id"],
                    "reason": "consistency_scan",
                    "issues": [i.detail for i in mat.scan_issues[:8]],
                }
            ],
            stats,
        )

    focal_w = mat.worlds["focal"]
    focal_arts = mat.artifacts["focal"]
    parallel_arts = flatten_parallel(mat)
    filler = extra_filler(
        seed,
        int(cfg.get("extra_filler_worlds", 8)),
        n_parallel=max(1, n_parallel),
        n_pulses=n_pulses,
        domain=domain,
        domains=domains,
    )
    from datetime import date as _date

    start = _date.fromisoformat(str(mat.spec.get("start") or "2026-01-08"))
    anchors = public_anchor_artifacts(mat.spec["world_id"], start, n=8)
    unique_pool = filler + parallel_arts + anchors
    graph = build_causal_graph(focal_w)

    kept: list[dict] = []
    timings = list(cfg.get("query_timings", ["first", "late"]))
    positions = list(cfg.get("position_buckets", ["front", "middle", "back"]))
    buckets = _buckets_for_split(cfg, split)
    frac_map = dict(cfg.get("min_evidence_frac") or {})
    surface_min = float(cfg.get("surface_min", 0.82))
    keep_one_pos = bool(cfg.get("keep_one_position", True))

    for spec in mat.queries:
        if not _trainable(spec):
            continue
        _, cf_arts = render_cf_view(focal_w, spec)
        views = split_views(focal_arts, parallel_arts, spec, cf_arts)
        ver, notes = verify_question(
            focal_w,
            spec,
            focal_arts + parallel_arts,
            cf_artifacts=cf_arts,
            surface_min=surface_min,
        )
        stats["attempts"] += 1
        if not ver.all_green():
            rejects.append(
                {
                    "seed": seed,
                    "world_id": mat.spec["world_id"],
                    "query_id": spec.query_id,
                    "reason": "prepack_gate",
                    "notes": {
                        k: notes.get(k)
                        for k in (
                            "full_ans",
                            "min_ans",
                            "cf_ans",
                            "closed_ans",
                            "surface_ratio",
                            "shortcut",
                            "visibility_gap",
                        )
                    },
                    "gates": ver.model_dump(),
                }
            )
            continue

        walk_ids = random_walk_event_ids(
            graph,
            starts=list(spec.essential_event_ids),
            n_walks=12,
            walk_len=8,
            forbid=set(spec.essential_event_ids),
            rng=random.Random(seed * 17 + sum(ord(c) for c in spec.query_id)),
        )
        typed = typed_walk_event_ids(
            graph,
            starts=list(spec.essential_event_ids),
            allowed_kinds={
                "causes",
                "enables",
                "derived_from",
                "supersedes",
                "contradicts",
            },
            n_walks=8,
            walk_len=6,
            forbid=set(spec.essential_event_ids),
            rng=random.Random(seed * 19 + sum(ord(c) for c in spec.query_id)),
        )
        walk_ids = list(dict.fromkeys(walk_ids + typed))
        gstat = graph_stats(focal_w, spec)
        qh = sum(ord(ch) for ch in spec.query_id)
        rng = random.Random(seed * 31 + qh)
        for timing in timings:
            for bname, target in buckets.items():
                min_frac = float(frac_map.get(bname, 0.35))
                candidates: list[tuple[int, object, object]] = []
                for pos in positions:
                    stats["attempts"] += 1
                    packed = pack_view(
                        views["full"],
                        spec,
                        unique_pool,
                        query_timing=timing,
                        position_bucket=pos,
                        length_bucket=bname,
                        target_tokens=int(target),
                        rng=rng,
                        min_distance_frac=min_frac,
                        walk_ids=walk_ids,
                    )
                    if packed.n_clones:
                        stats["n_clones"] += packed.n_clones
                    if not packed.ok:
                        rejects.append(
                            {
                                "seed": seed,
                                "query_id": spec.query_id,
                                "reason": packed.reject_reason or "pack_fail",
                                "timing": timing,
                                "position": pos,
                                "bucket": bname,
                            }
                        )
                        continue
                    if any("#pad" in a.artifact_id for a in packed.artifacts):
                        stats["n_clones"] += 1
                        rejects.append(
                            {
                                "seed": seed,
                                "query_id": spec.query_id,
                                "reason": "clone_forbidden",
                                "bucket": bname,
                            }
                        )
                        continue
                    ver2, notes2 = verify_question(
                        focal_w,
                        spec,
                        focal_arts + parallel_arts,
                        cf_artifacts=cf_arts,
                        window_ids=packed.window_ids,
                        surface_min=surface_min,
                    )
                    if not ver2.all_green():
                        rejects.append(
                            {
                                "seed": seed,
                                "query_id": spec.query_id,
                                "reason": "pack_gate",
                                "timing": timing,
                                "position": pos,
                                "bucket": bname,
                                "window_ans": notes2.get("window_ans"),
                                "gates": ver2.model_dump(),
                            }
                        )
                        continue
                    candidates.append((packed.max_evidence_distance, packed, ver2))
                if not candidates:
                    continue
                if keep_one_pos:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    candidates = candidates[:1]
                for dist, packed, ver2 in candidates:
                    proof = ProofGraph(
                        necessary_nodes=list(spec.essential_event_ids),
                        sufficient_set=list(spec.sufficient_event_ids),
                        answer_expression=spec.gold_expression,
                        cf_event_id=spec.cf_event_id,
                        cf_op=spec.cf_op,
                    )
                    vis_gap = int(notes.get("visibility_gap") or 0)
                    ess = set(spec.essential_artifact_ids)
                    cf_map = {a.artifact_id: a for a in views["cf"]}
                    packed_cf = [
                        cf_map.get(a.artifact_id.split("#")[0], a)
                        for a in packed.artifacts
                    ]
                    view_contexts = {
                        "full": packed.text,
                        "cf": join_artifacts(packed_cf),
                        "minimal": join_artifacts(views["minimal"]),
                        "distractor_only": join_artifacts(
                            [a for a in packed.artifacts if a.artifact_id not in ess]
                        ),
                        "trajectory": join_artifacts(
                            sorted(
                                packed.artifacts,
                                key=lambda a: (a.time, a.artifact_id),
                            )
                        ),
                        "memory": memory_card(focal_w, spec),
                    }
                    slot_qid = (
                        f"{spec.query_id}:{timing}:{packed.position_bucket}:{bname}"
                    )
                    for view_name, context in view_contexts.items():
                        rec = SampleRecord(
                            world_id=mat.spec["world_id"],
                            seed=seed,
                            schema_version=str(
                                mat.spec.get("schema_version") or "p1.0"
                            ),
                            query_id=slot_qid,
                            query_type=spec.query_type,
                            query_timing=timing,
                            question=spec.question,
                            answer=view_answer(spec, view_name),
                            cf_answer=spec.cf_answer,
                            view=view_name,
                            context=wrap_prompt(spec.question, context, timing),
                            proof_graph=proof,
                            difficulty={
                                "context_tokens": estimate_tokens(context),
                                "max_evidence_distance": packed.max_evidence_distance,
                                "proof_depth": spec.proof_depth,
                                "state_updates": len(focal_w.state.history),
                                "query_delay": 1 if timing == "late" else 0,
                                "distractor_similarity": 1.0 if parallel_arts else 0.0,
                                "visibility_gap": vis_gap,
                            },
                            verification=ver2,
                            essential_artifact_ids=list(spec.essential_artifact_ids),
                            window_artifact_ids=packed.window_ids,
                            position_bucket=packed.position_bucket,
                            length_bucket=bname,
                            split=split,
                            cf_op=spec.cf_op,
                        )
                        dumped = rec.model_dump()
                        dumped["graph"] = gstat
                        dumped["n_unique_docs"] = packed.n_unique
                        dumped["n_clones"] = packed.n_clones
                        dumped["motif"] = spec.motif
                        dumped["topology_id"] = spec.topology_id
                        dumped["topology_family"] = (spec.topology_id or "").split(":")[
                            0
                        ]
                        dumped["domain"] = spec.domain or domain
                        dumped["truth_regime"] = spec.truth_regime
                        kept.append(dumped)
                    stats["kept_slots"] += 1
    return kept, rejects, stats


def _job(payload: tuple[int, dict, str]) -> tuple[list[dict], list[dict], dict]:
    seed, cfg, split = payload
    return emit_records(seed, cfg, split)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=ROOT / "configs" / "p0.yaml")
    ap.add_argument("--n-worlds", type=int, default=None)
    ap.add_argument("--seed-start", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "p0")
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    n_worlds = args.n_worlds or int(cfg.get("n_worlds", 50))
    train_ratio = float(cfg.get("train_ratio", 0.8))

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    train_f = (out / "train.jsonl").open("w")
    eval_f = (out / "eval.jsonl").open("w")
    rej_f = (out / "reject_log.jsonl").open("w")

    if n_worlds <= 1:
        n_train_w = n_worlds
    else:
        n_train_w = min(n_worlds - 1, max(1, int(n_worlds * train_ratio)))

    jobs = [
        (args.seed_start + i, cfg, "train" if i < n_train_w else "eval")
        for i in range(n_worlds)
    ]

    n_kept = 0
    n_q = 0
    n_rej = 0
    n_att = 0
    n_kept_slots = 0
    n_clones = 0
    type_c: Counter = Counter()
    view_c: Counter = Counter()
    len_c: Counter = Counter()
    motif_c: Counter = Counter()
    domain_c: Counter = Counter()
    topo_c: Counter = Counter()
    family_c: Counter = Counter()
    rej_c: Counter = Counter()
    split_q: dict[str, set[str]] = {"train": set(), "eval": set()}
    world_ids: set[str] = set()
    answers: Counter = Counter()

    def consume(kept: list[dict], rejects: list[dict], st: dict) -> None:
        nonlocal n_kept, n_q, n_rej, n_att, n_kept_slots, n_clones
        n_att += int(st.get("attempts") or 0)
        n_kept_slots += int(st.get("kept_slots") or 0)
        n_clones += int(st.get("n_clones") or 0)
        for r in rejects:
            rej_f.write(json.dumps(r, default=str) + "\n")
            n_rej += 1
            rej_c[str(r.get("reason", "?"))] += 1
        seen_q: set[str] = set()
        for rec in kept:
            dest = train_f if rec["split"] == "train" else eval_f
            dest.write(json.dumps(rec) + "\n")
            n_kept += 1
            type_c[rec["query_type"]] += 1
            view_c[rec["view"]] += 1
            len_c[rec["length_bucket"]] += 1
            motif_c[rec.get("motif") or "?"] += 1
            domain_c[rec.get("domain") or "?"] += 1
            topo_c[rec.get("topology_id") or "?"] += 1
            family_c[rec.get("topology_family") or "?"] += 1
            world_ids.add(rec["world_id"])
            if rec["view"] == "full":
                seen_q.add(rec["query_id"])
                split_q[rec["split"]].add(rec["query_id"])
                answers[str(rec["answer"])] += 1
        n_q += len(seen_q)

    if args.workers <= 1:
        for i, job in enumerate(jobs):
            consume(*_job(job))
            if (i + 1) % 5 == 0 or (i + 1) == n_worlds:
                print(
                    f"worlds={i + 1}/{n_worlds} rows={n_kept} questions={n_q} rejects={n_rej}",
                    flush=True,
                )
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(_job, job) for job in jobs]
            done = 0
            for fut in as_completed(futs):
                kept, rejects, st = fut.result()
                consume(kept, rejects, st)
                done += 1
                if done % 5 == 0 or done == n_worlds:
                    print(
                        f"worlds={done}/{n_worlds} rows={n_kept} questions={n_q} rejects={n_rej}",
                        flush=True,
                    )

    train_f.close()
    eval_f.close()
    rej_f.close()

    retention = (n_kept_slots / n_att) if n_att else 0.0
    report = {
        "schema_version": "p1.1",
        "n_worlds": n_worlds,
        "n_world_ids": len(world_ids),
        "n_rows": n_kept,
        "n_questions": n_q,
        "n_rejects": n_rej,
        "n_attempts": n_att,
        "n_kept_slots": n_kept_slots,
        "retention": round(retention, 4),
        "n_clones": n_clones,
        "unique_full_answers": len(answers),
        "n_topologies": len(topo_c),
        "n_topology_families": len(family_c),
        "n_motifs": len(motif_c),
        "n_domains": len(domain_c),
        "train_questions": len(split_q["train"]),
        "eval_questions": len(split_q["eval"]),
        "by_query_type": dict(type_c),
        "by_view": dict(view_c),
        "by_length": dict(len_c),
        "by_motif": dict(motif_c),
        "by_domain": dict(domain_c),
        "by_topology_family": dict(family_c),
        "reject_reasons": dict(rej_c),
        "gold_cfr": 1.0 if n_kept else 0.0,
    }
    (out / "quality_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if n_clones:
        raise SystemExit(f"clone packing leaked {n_clones} documents")
    if n_worlds >= 50 and n_q < 200:
        raise SystemExit(f"P0 exit bar missed: only {n_q} questions (need >=200)")
    if n_worlds >= 8 and n_q < 40:
        raise SystemExit(f"P0 week-1 bar missed: only {n_q} questions (need >=40)")


if __name__ == "__main__":
    main()
