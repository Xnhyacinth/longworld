#!/usr/bin/env python3
"""Format-free GraphWalks rescoring.

The official card grad es the LAST LINE only and requires a "Final Answer: [...]"
prefix; a model that answers with a bare JSON list scores 0.0 with
format_fail_rate 1.0 — that is a grader-interface artifact, not a capability
measurement (measured: graded 0.0 vs format-free 0.075 on the same responses,
against baseline 0.118 graded). This script recomputes precision/recall from any
JSON-shaped id list in the response, using the same gold sets the client loads,
and writes <shard>/summary.format_free.json alongside the official summary.
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd

GRAPH = "graphwalks_128k_and_shorter.parquet"
# The gold ids are unquoted 10-char hex (the card's own example is
# "Final Answer: [node1, node2, node3]"); models answer either bare, quoted, or
# wrapped in prose. Match both quotings, take the LAST bracket group.
ID_LIST = re.compile(r"\[([^\[\]]{4,})\]")
ANY_ID = re.compile(r"[0-9a-f]{8,}")


def parse_ids(response: str) -> set[str]:
    """Every hex id from the LAST bracket group in the response (format-free)."""
    last = None
    for m in ID_LIST.finditer(response or ""):
        last = m
    if last is None:
        return set()
    return set(ANY_ID.findall(last.group(1)))


def main() -> int:
    data_root = Path(sys.argv[1])
    run_root = Path(sys.argv[2])
    df = pd.read_parquet(data_root / "openai_graphwalks" / GRAPH)
    gold_by_uid = {}
    # uids are "<parquet>:<problem_type>:<global parquet row index>" — the eval
    # client's scheme (load_rows). A per-type counter only coincides with it for
    # parents (rows 0-399); bfs rows are 400-749, so per-type counting silently
    # dropped every bfs row from the rescoring.
    for index, row in df.iterrows():
        ptype = row["problem_type"]
        gold_by_uid[f"{GRAPH}:{ptype}:{index}"] = set(row["answer_nodes"])

    written = 0
    for samples in sorted(run_root.glob("*/graphwalks_*/samples.jsonl")):
        rows = [json.loads(line) for line in samples.open()]
        n = hit = 0.0
        n_parse = 0
        for r in rows:
            gold = gold_by_uid.get(r.get("uid"))
            if gold is None:
                continue
            got = parse_ids(r.get("response", ""))
            n += 1
            if got:
                n_parse += 1
                if gold:
                    hit += len(got & gold) / len(got)
        if not n:
            continue
        out = {
            "task": samples.parent.name,
            "model": samples.parent.parent.name,
            "n_rows": int(n),
            "n_with_parsable_list": n_parse,
            "format_free_precision": round(hit / n, 4),
            "note": "precision over ALL rows (unparsable counted as 0); official "
            "graded metric zeroes any row lacking 'Final Answer:' on the last line",
        }
        (samples.parent / "summary.format_free.json").write_text(
            json.dumps(out, indent=1) + "\n"
        )
        print(json.dumps(out))
        written += 1
    print(f"wrote {written} format-free summaries", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
