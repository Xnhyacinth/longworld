#!/usr/bin/env python3
"""Official OpenAI MRCR / GraphWalks graders against a vLLM OpenAI server.

MRCR: SequenceMatcher ratio after required hash prefix (openai/mrcr README).
GraphWalks: last-line ``Final Answer: [...]`` then set precision/recall/F1
(openai/graphwalks README). Empty∩empty F1 is 1.0, not 0.0.
Official-F1 defect (kept for comparability with past summary.json): it
returns 1.0 whenever precision+recall==0, so disjoint non-empty sets and
parse failures also score 1.0. The corrected key ``f1_standard`` scores
those cases 0.0; only empty∩empty is 1.0.

Do not flatten MRCR into one user string. The prompt is already a chat.
Do not enable thinking: the MRCR hash-prefix grader zeros any extra text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

MRCR_BINS = (4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1_048_576)
_FINAL_ANSWER = re.compile(r"Final Answer:\s*\[(.*)\]")


def grade_mrcr(response: str, answer: str, prefix: str) -> float:
    text = response or ""
    if not text.startswith(prefix):
        return 0.0
    return float(
        SequenceMatcher(
            None, text.removeprefix(prefix), answer.removeprefix(prefix)
        ).ratio()
    )


def extract_graphwalks_list(response: str) -> tuple[list[str], bool]:
    # Official card: last line only. Capturing group is required; stripping
    # brackets from the full "Final Answer: [...]" match does not extract nodes.
    line = (response or "").rstrip("\n").split("\n")[-1]
    if "Final Answer:" not in line:
        return [], True
    match = _FINAL_ANSWER.search(line)
    if match is None:
        return [], True
    content = match.group(1).strip()
    if not content:
        return [], False
    return [item.strip().strip("'\"") for item in content.split(",") if item.strip()], False


def grade_graphwalks(response: str, gold: list[str]) -> dict[str, float | bool]:
    sampled, parse_error = extract_graphwalks_list(response)
    sampled_set = set(sampled)
    truth_set = set(gold)
    n_overlap = len(sampled_set & truth_set)
    n_golden = len(truth_set)
    n_sampled = len(sampled_set)
    recall = n_overlap / n_golden if n_golden > 0 else 0.0
    precision = n_overlap / n_sampled if n_sampled > 0 else 0.0
    if recall + precision > 0:
        f1 = 2 * (recall * precision) / (recall + precision)
    else:
        f1 = 1.0
    if parse_error:
        # Parse failure: standard F1 has nothing correct to score.
        f1_standard = 0.0
    elif n_golden == 0 and n_sampled == 0:
        f1_standard = 1.0
    elif recall + precision > 0:
        f1_standard = 2 * (recall * precision) / (recall + precision)
    else:
        # Disjoint non-empty sets: harmonic formula is 0/0, standard is 0.0.
        f1_standard = 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "f1_standard": f1_standard,
        "f1_parse_zero": 0.0 if parse_error else f1,
        "format_fail": parse_error,
        "empty_empty": n_golden == 0 and n_sampled == 0,
    }


def _self_check() -> None:
    assert grade_mrcr("abcREST", "abcREST", "abc") == 1.0
    assert grade_mrcr("wrongREST", "abcREST", "abc") == 0.0
    gold = ["a", "b"]
    ok = grade_graphwalks("reason\nFinal Answer: [a, b]", gold)
    assert ok["precision"] == 1.0 and ok["f1"] == 1.0 and ok["format_fail"] is False
    empty = grade_graphwalks("Final Answer: []", [])
    assert empty["f1"] == 1.0
    assert empty["f1_standard"] == 1.0
    bad = grade_graphwalks("no list here", gold)
    # Official F1 is 1 when both precision and recall are 0, including parse misses.
    # f1_standard instead scores parse failure and disjoint sets as 0.0.
    assert bad["format_fail"] is True and bad["f1"] == 1.0
    assert bad["f1_standard"] == 0.0
    assert bad["precision"] == 0.0 and bad["f1_parse_zero"] == 0.0
    disjoint = grade_graphwalks("Final Answer: [x]", gold)
    assert disjoint["f1"] == 1.0
    assert disjoint["f1_standard"] == 0.0
    partial = grade_graphwalks("Final Answer: [a]", gold)
    assert partial["f1"] == 2 * (1.0 * 0.5) / (1.0 + 0.5)
    assert partial["f1_standard"] == partial["f1"]


def _load_messages(raw) -> list[dict]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list) or not raw:
        raise ValueError("prompt is not a message list")
    messages = []
    for item in raw:
        role = str(item.get("role") or "user")
        content = item.get("content")
        if content is None:
            content = ""
        messages.append({"role": role, "content": str(content)})
    return messages


def _nodes(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [str(item) for item in list(raw)]


def _bin_label(n_tokens: int) -> str:
    prev = 0
    for bound in MRCR_BINS:
        if n_tokens <= bound:
            if prev == 0:
                return f"[0,{bound}]"
            return f"({prev},{bound}]"
        prev = bound
    return f">({MRCR_BINS[-1]}"


def load_rows(task: str, data_root: Path) -> list[dict]:
    import pandas as pd

    rows: list[dict] = []
    if task.startswith("mrcr_"):
        needle = "2needle" if task.endswith("2needle") else "4needle"
        for path in sorted((data_root / "openai_mrcr" / needle).glob("*.parquet")):
            frame = pd.read_parquet(path)
            for index, row in frame.iterrows():
                rows.append(
                    {
                        "uid": f"{path.name}:{index}",
                        "task": task,
                        "messages": _load_messages(row["prompt"]),
                        "answer": str(row["answer"]),
                        "prefix": str(row["random_string_to_prepend"]),
                        "n_chars": int(row["n_chars"]),
                        "n_needles": int(row["n_needles"]),
                    }
                )
        return rows
    if task.startswith("graphwalks_"):
        kind = "parents" if task.endswith("parents") else "bfs"
        path = data_root / "openai_graphwalks" / "graphwalks_128k_and_shorter.parquet"
        frame = pd.read_parquet(path)
        gold_col = "answer_nodes" if "answer_nodes" in frame.columns else "answer"
        for index, row in frame.iterrows():
            if str(row["problem_type"]).lower() != kind:
                continue
            prompt = str(row["prompt"])
            rows.append(
                {
                    "uid": f"{path.name}:{kind}:{index}",
                    "task": task,
                    "messages": [{"role": "user", "content": prompt}],
                    "gold": _nodes(row[gold_col]),
                    "n_chars": int(row["prompt_chars"]),
                    "problem_type": kind,
                }
            )
        return rows
    raise ValueError(f"unknown task {task}")


def count_tokens(tokenizer, messages: list[dict]) -> int:
    try:
        return len(
            tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True
            )
        )
    except Exception:
        text = "\n".join(m["content"] for m in messages)
        return len(tokenizer.encode(text))


def complete(client, model: str, messages: list[dict], max_tokens: int, seed: int) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=max_tokens,
        seed=seed,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    choice = response.choices[0]
    content = choice.message.content or ""
    return content


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(task: str, records: list[dict]) -> dict:
    scored = [r for r in records if r.get("status") == "ok"]
    skipped = [r for r in records if r.get("status") == "skipped"]
    failed = [r for r in records if r.get("status") == "error"]
    out: dict = {
        "task": task,
        "n_loaded": len(records),
        "n_scored": len(scored),
        "n_skipped_overlength": len(skipped),
        "n_error": len(failed),
    }
    if task.startswith("mrcr_"):
        scores = [float(r["score"]) for r in scored]
        out["sequence_matcher_mean"] = mean(scores)
        by_bin: dict[str, list[float]] = defaultdict(list)
        for row in scored:
            by_bin[row["bin"]].append(float(row["score"]))
        out["by_bin"] = {k: {"n": len(v), "mean": mean(v)} for k, v in sorted(by_bin.items())}
    else:
        out["precision"] = mean([float(r["precision"]) for r in scored])
        out["recall"] = mean([float(r["recall"]) for r in scored])
        out["f1"] = mean([float(r["f1"]) for r in scored])
        out["f1_standard"] = mean([float(r["f1_standard"]) for r in scored])
        out["f1_parse_zero"] = mean([float(r["f1_parse_zero"]) for r in scored])
        out["format_fail_rate"] = mean([1.0 if r["format_fail"] else 0.0 for r in scored])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--task", choices=("mrcr_2needle", "mrcr_4needle", "graphwalks_parents", "graphwalks_bfs"))
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="model")
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--max-model-len", type=int, default=131072)
    parser.add_argument("--max-gen-toks", type=int, default=4096)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--row-start", type=int, default=0)
    parser.add_argument("--row-end", type=int, default=None, help="exclusive slice after load")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-jsonl", type=Path)
    args = parser.parse_args()
    if args.self_check:
        _self_check()
        print("self-check ok")
        if args.task is None:
            return 0
    if args.task is None or args.data_root is None or args.tokenizer is None or args.out_json is None:
        parser.error("--task --data-root --tokenizer --out-json are required")

    from openai import OpenAI
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(args.tokenizer), trust_remote_code=True)
    rows = load_rows(args.task, args.data_root)
    rows = rows[args.row_start : args.row_end]
    budget = args.max_model_len - args.max_gen_toks
    client = OpenAI(base_url=args.base_url, api_key="EMPTY", timeout=args.timeout)

    def run_one(row: dict) -> dict:
        n_tokens = count_tokens(tokenizer, row["messages"])
        record = {
            "uid": row["uid"],
            "task": row["task"],
            "n_chars": row["n_chars"],
            "n_tokens": n_tokens,
            "bin": _bin_label(n_tokens),
        }
        if n_tokens > budget:
            record["status"] = "skipped"
            record["reason"] = "overlength"
            return record
        try:
            text = complete(client, args.model, row["messages"], args.max_gen_toks, args.seed)
        except Exception as error:
            record["status"] = "error"
            record["error"] = f"{type(error).__name__}: {error}"
            return record
        record["status"] = "ok"
        record["response_chars"] = len(text)
        # Store the raw response so later waves can be re-graded format-free.
        # The 0917 P64 diagnosis needed this and it did not exist: MRCR 0.0038
        # zeroes ~489/491 rows on a missing 10-char prefix, and there was no
        # way to ask what the content actually was.
        record["response"] = text[:4096]
        if args.task.startswith("mrcr_"):
            record["score"] = grade_mrcr(text, row["answer"], row["prefix"])
            record["hash_ok"] = text.startswith(row["prefix"])
        else:
            graded = grade_graphwalks(text, row["gold"])
            record.update(graded)
        return record

    started = time.monotonic()
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [pool.submit(run_one, row) for row in rows]
        for i, future in enumerate(as_completed(futures), start=1):
            records.append(future.result())
            if i % 25 == 0 or i == len(futures):
                print(f"{args.task} {i}/{len(futures)}", flush=True)

    records.sort(key=lambda item: item["uid"])
    summary = summarize(args.task, records)
    summary["wall_seconds"] = round(time.monotonic() - started, 3)
    summary["max_model_len"] = args.max_model_len
    summary["max_gen_toks"] = args.max_gen_toks
    summary["row_start"] = args.row_start
    if args.row_end is not None:
        summary["row_end"] = args.row_end
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2) + "\n")
    if args.out_jsonl is not None:
        args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.out_jsonl.open("w") as handle:
            for row in records:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["n_error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
