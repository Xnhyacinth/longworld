#!/usr/bin/env python3
"""Per-sample GraphWalks diagnostics over saved eval predictions.

Reads the gold parquet plus each run's ``graphwalks_{parents,bfs}/samples.jsonl``
and reports, per run x subtask x parse mode (official ``Final Answer:`` last-line
parser vs the format-free last-bracket-group parser of
scripts/rescore_graphwalks_free.py):

- n rows, parse_fail_rate, |P| / |G| size stats, precision, recall, f1_standard,
  exact_set_rate, superset_rate, empty_pred_rate, mean Jaccard, mean |P|/|G|.

- false-positive taxonomy: each FP id is ``in_context`` if the id string occurs
  in the sample's prompt, else ``hallucinated``. Because the prompt's edge list
  fully determines the graph (verified: 750/750 gold sets reconstruct from the
  parsed edges; parents gold excludes the query node itself on self-loops),
  in-context FPs are further bucketed by BFS depth from the query node vs the
  gold depth: ``too_shallow`` (d_fp < d_gold), ``too_deep`` (d_fp > d_gold),
  ``same_depth_wrong_node``. Gold depth is 1 for parents (the edge away from
  the parent) and the prompt's BFS depth for bfs.

f1_standard: 1.0 iff both sets empty, 0.0 on parse failure, else the harmonic
mean (0.0 for disjoint non-empty sets). Computed locally; the official client
f1 is deliberately not reused.

Usage:
  python scripts/diagnose_graphwalks_predictions.py \
      --runs b0=data/evals/... --runs arm_a_25=data/evals/... \
      --gold data/evals/mrcr_graphwalks_20260917 \
      --out reports/gw_per_sample_diagnostics_20260921.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_mrcr_graphwalks_client import extract_graphwalks_list  # noqa: E402

GRAPH = "graphwalks_128k_and_shorter.parquet"
ID_LIST = re.compile(r"\[([^\[\]]{4,})\]")
ANY_ID = re.compile(r"[0-9a-f]{8,}")
EDGE = re.compile(r"([0-9a-f]{10})\s*->\s*([0-9a-f]{10})")
QUERY_PARENTS = re.compile(r"Find the parents of node ([0-9a-f]{10})")
QUERY_BFS = re.compile(
    r"Perform a BFS from node ([0-9a-f]{10}) and return only the nodes at exactly depth (\d+)"
)


def parse_ids_format_free(response: str) -> set[str]:
    """Every hex id from the LAST bracket group (mirrors rescore_graphwalks_free)."""
    last = None
    for m in ID_LIST.finditer(response or ""):
        last = m
    if last is None:
        return set()
    return set(ANY_ID.findall(last.group(1)))


def parse_ids_official(response: str) -> tuple[set[str], bool]:
    """Official last-line parser; bool is the parse-failure flag."""
    items, parse_error = extract_graphwalks_list(response)
    return set(items), parse_error


def f1_standard(pred: set[str], gold: set[str], parse_fail: bool) -> float:
    if parse_fail:
        return 0.0
    if not pred and not gold:
        return 1.0
    if not pred or not gold:
        return 0.0
    overlap = len(pred & gold)
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred)
    recall = overlap / len(gold)
    return 2 * precision * recall / (precision + recall)


def median_p90(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    ordered = sorted(values)
    idx90 = min(len(ordered) - 1, int(0.9 * len(ordered)))
    return float(statistics.median(ordered)), float(ordered[idx90])


def load_gold(parquet_path: Path) -> dict[str, dict]:
    """uid -> {gold set, prompt, problem_type, query node, bfs depth, adjacency}."""
    import pandas as pd

    frame = pd.read_parquet(parquet_path)
    out: dict[str, dict] = {}
    for index, row in frame.iterrows():
        ptype = str(row["problem_type"])
        uid = f"{parquet_path.name}:{ptype}:{index}"
        prompt = str(row["prompt"])
        gold = {str(x) for x in row["answer_nodes"]}
        adj: dict[str, set[str]] = {}
        for a, b in EDGE.findall(prompt):
            adj.setdefault(a, set()).add(b)
        n_nodes = len({x for pair in EDGE.findall(prompt) for x in pair})
        if ptype == "parents":
            m = QUERY_PARENTS.search(prompt)
            query, depth = (m.group(1), 1) if m else (None, 1)
        else:
            m = QUERY_BFS.search(prompt)
            query, depth = (m.group(1), int(m.group(2))) if m else (None, None)
        out[uid] = {
            "gold": gold,
            "prompt": prompt,
            "problem_type": ptype,
            "query": query,
            "bfs_depth": depth,
            "adj": adj,
            "n_graph_nodes": n_nodes,
        }
    return out


def bfs_depths(adj: dict[str, set[str]], start: str) -> dict[str, int]:
    """Shortest-path depth of every reachable node from start (start = 0)."""
    depths = {start: 0}
    queue = deque([start])
    while queue:
        u = queue.popleft()
        for v in adj.get(u, ()):
            if v not in depths:
                depths[v] = depths[u] + 1
                queue.append(v)
    return depths


def reverse_bfs_depths(adj: dict[str, set[str]], start: str) -> dict[str, int]:
    """Depth of every node that can REACH start along directed edges.

    Gold parents of q are exactly the reverse-depth-1 nodes (minus q itself,
    self-loops excluded), so parents FPs are bucketed on this distance.
    """
    radj: dict[str, set[str]] = {}
    for a, targets in adj.items():
        for b in targets:
            radj.setdefault(b, set()).add(a)
    return bfs_depths(radj, start)


def classify_fp(
    fp_id: str, sample: dict, depths: dict[str, int] | None, gold_depth: int | None
) -> tuple[str, str | None]:
    """in_context/hallucinated, plus depth bucket for in-context FPs."""
    if fp_id not in sample["prompt"]:
        return "hallucinated", None
    if depths is None or gold_depth is None or fp_id not in depths:
        return "in_context", "unknown_depth"
    d_fp = depths[fp_id]
    if d_fp < gold_depth:
        bucket = "too_shallow"
    elif d_fp > gold_depth:
        bucket = "too_deep"
    else:
        bucket = "same_depth_wrong_node"
    return "in_context", bucket


def score_run(run_name: str, run_path: Path, gold_by_uid: dict[str, dict]) -> dict:
    """Full per-run report: per subtask x parse mode metrics + FP taxonomy."""
    report: dict = {"run": run_name, "path": str(run_path), "subtasks": {}}
    for subtask, dir_name in (
        ("parents", "graphwalks_parents"),
        ("bfs", "graphwalks_bfs"),
    ):
        samples_path = run_path / dir_name / "samples.jsonl"
        if not samples_path.exists():
            report["subtasks"][subtask] = {"error": f"missing {samples_path}"}
            continue
        rows = [json.loads(line) for line in samples_path.open()]
        # Skipped rows (overlength prompts) carry no response and no score in
        # the official summary; error rows likewise. But the rescore
        # denominator is ALL gold-matched rows including skipped (unparsable
        # response counts 0), so metrics are computed over every gold-matched
        # row; n_ok / n_error are reported alongside for transparency.
        matched = [r for r in rows if r.get("uid") in gold_by_uid]
        sub: dict = {
            "n_rows_total": len(rows),
            "n_ok": sum(1 for r in rows if r.get("status") == "ok"),
            "n_error": sum(1 for r in rows if r.get("status") == "error"),
            "n_matched_gold": len(matched),
            "modes": {},
        }
        fp_counts: dict[str, dict[str, int]] = {}
        for mode in ("official_parse", "format_free"):
            if mode == "official_parse":
                parsed = [(parse_ids_official(r.get("response", ""))) for r in matched]
                preds = [p for p, _ in parsed]
                fails = [f for _, f in parsed]
            else:
                preds = [parse_ids_format_free(r.get("response", "")) for r in matched]
                # Format-free parse fail = no bracket group at all; "Final
                # Answer: []" parses to an empty set, not a failure.
                fails = [
                    not (p or has_bracket(r.get("response", "")))
                    for p, r in zip(preds, matched)
                ]
            precisions, recalls, f1s, jaccards, ratios = [], [], [], [], []
            p_sizes, g_sizes = [], []
            exact = superset = empty_pred = 0
            for pred, fail, row in zip(preds, fails, matched):
                sample = gold_by_uid[row["uid"]]
                gold = sample["gold"]
                overlap = len(pred & gold)
                precision = overlap / len(pred) if pred else 0.0
                recall = overlap / len(gold) if gold else 0.0
                precisions.append(precision)
                recalls.append(recall)
                f1s.append(f1_standard(pred, gold, fail))
                union = len(pred | gold)
                jaccards.append(overlap / union if union else 1.0)
                ratios.append(
                    len(pred) / len(gold) if gold else (1.0 if not pred else 0.0)
                )
                p_sizes.append(len(pred))
                g_sizes.append(len(gold))
                exact += int(pred == gold)
                superset += int(gold.issubset(pred))
                empty_pred += int(not pred)
                if mode == "format_free":
                    # FP taxonomy only on the format-free parse (the official
                    # parser returns [] for every arm row, so its FP set is empty
                    # and the taxonomy would be vacuous). parents FPs are
                    # distance-bucketed on the REVERSE graph (a gold parent of q
                    # has an edge q<-p, i.e. reverse-depth 1); bfs FPs on the
                    # forward BFS from the query at the prompt's depth.
                    depths = None
                    if sample["query"]:
                        if subtask == "parents":
                            depths = reverse_bfs_depths(sample["adj"], sample["query"])
                            gold_depth = 1
                        elif sample["bfs_depth"] is not None:
                            depths = bfs_depths(sample["adj"], sample["query"])
                            gold_depth = sample["bfs_depth"]
                        else:
                            gold_depth = None
                    else:
                        gold_depth = None
                    for fp_id in pred - gold:
                        kind, bucket = classify_fp(fp_id, sample, depths, gold_depth)
                        counts = fp_counts.setdefault(fp_id, {})
                        counts[kind] = counts.get(kind, 0) + 1
                        if bucket:
                            key = f"{kind}:{bucket}"
                            counts[key] = counts.get(key, 0) + 1
            n = len(matched) or 1
            p_med, p_p90 = median_p90([float(x) for x in p_sizes])
            g_med, _ = median_p90([float(x) for x in g_sizes])
            sub["modes"][mode] = {
                "n_rows": len(matched),
                "parse_fail_rate": round(sum(fails) / n, 4),
                "pred_size": {
                    "mean": round(sum(p_sizes) / n, 2),
                    "median": round(p_med, 1),
                    "p90": round(p_p90, 1),
                    "max": int(max(p_sizes)) if p_sizes else 0,
                },
                "gold_size": {
                    "mean": round(sum(g_sizes) / n, 2),
                    "median": round(g_med, 1),
                },
                "precision": round(sum(precisions) / n, 4),
                "recall": round(sum(recalls) / n, 4),
                "f1_standard": round(sum(f1s) / n, 4),
                "exact_set_rate": round(exact / n, 4),
                "superset_rate": round(superset / n, 4),
                "empty_pred_rate": round(empty_pred / n, 4),
                "jaccard_mean": round(sum(jaccards) / n, 4),
                "pred_over_gold_ratio_mean": round(sum(ratios) / n, 3),
            }
            if mode == "format_free":
                # Response-shape diagnostics that motivated this script: the
                # review's median/exact numbers are over rows WITH a nonempty
                # parse, and the inflation claim is about giant-list rows.
                ne_sizes = [len(p) for p in preds if p]
                ne_med, ne_p90 = median_p90([float(x) for x in ne_sizes])
                big = [p for p in ne_sizes if p >= 20]
                big_fracs = [
                    len(p) / gold_by_uid[row["uid"]]["n_graph_nodes"]
                    for p, row in zip(preds, matched)
                    if len(p) >= 20
                ]
                empty_gold_rows = [
                    (p, gold_by_uid[row["uid"]]["gold"])
                    for p, row in zip(preds, matched)
                    if not gold_by_uid[row["uid"]]["gold"]
                ]
                empty_gold_nonempty_pred = sum(1 for p, g in empty_gold_rows if p)
                n_ne = len(ne_sizes) or 1
                sub["modes"][mode]["nonempty_parse"] = {
                    "n_rows": len(ne_sizes),
                    "median_pred_size": round(ne_med, 1),
                    "p90_pred_size": round(ne_p90, 1),
                    "exact_among_nonempty": sum(
                        1
                        for p, row in zip(preds, matched)
                        if p and p == gold_by_uid[row["uid"]]["gold"]
                    ),
                    "giant_list_rate": {
                        "n_pred_ge_20": len(big),
                        "frac_of_nonempty": round(len(big) / n_ne, 4),
                        "mean_frac_of_graph_nodes": round(
                            sum(big_fracs) / len(big_fracs), 3
                        )
                        if big_fracs
                        else 0.0,
                    },
                    "empty_gold_rows": len(empty_gold_rows),
                    "empty_gold_with_nonempty_pred": empty_gold_nonempty_pred,
                }
        # Sort the per-id taxonomy so the JSON is byte-identical across runs
        # (set iteration order otherwise varies).
        sub["fp_taxonomy"] = {
            fp_id: dict(sorted(fp_counts[fp_id].items())) for fp_id in sorted(fp_counts)
        }
        report["subtasks"][subtask] = sub
    return report


def has_bracket(response: str) -> bool:
    return bool(ID_LIST.search(response or ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="run name = run root path; repeatable",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        required=True,
        help="directory containing openai_graphwalks/<GRAPH> or the parquet itself",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="report.json output path"
    )
    args = parser.parse_args()

    if args.gold.is_dir():
        parquet = args.gold / "openai_graphwalks" / GRAPH
    else:
        parquet = args.gold
    if not parquet.exists():
        print(f"gold parquet not found: {parquet}", file=sys.stderr)
        return 1
    gold_by_uid = load_gold(parquet)
    print(f"gold: {parquet} rows={len(gold_by_uid)}", flush=True)

    report: dict = {
        "gold_parquet": str(parquet),
        "n_gold_rows": len(gold_by_uid),
        "runs": [],
    }
    for spec in args.runs:
        name, _, path = spec.partition("=")
        run_path = Path(path)
        print(f"scoring {name} ({run_path}) ...", flush=True)
        run_report = score_run(name, run_path, gold_by_uid)
        report["runs"].append(run_report)
        for subtask, sub in run_report["subtasks"].items():
            if "error" in sub:
                print(f"  {name}/{subtask}: {sub['error']}", flush=True)
                continue
            for mode, m in sub["modes"].items():
                print(
                    f"  {name}/{subtask}/{mode}: n={m['n_rows']} "
                    f"P={m['precision']} R={m['recall']} F1={m['f1_standard']} "
                    f"exact={m['exact_set_rate']} superset={m['superset_rate']} "
                    f"|P|med={m['pred_size']['median']} "
                    f"parse_fail={m['parse_fail_rate']}",
                    flush=True,
                )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
