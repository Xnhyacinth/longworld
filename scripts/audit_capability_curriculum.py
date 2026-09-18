"""Audit actual multi-QA exports and bounded prefix/suffix information witnesses.

An identical observed window with different required answers certifies only that
window's insufficiency for the pair. Parser rejection is never such a witness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_capability_world_pipeline as base
from scripts.run_capability_curriculum import compiler

WINDOWS = (4096, 8192, 16384)
SUFFIX = "\nReturn one JSON object mapping every question id to its answer."


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _loads(text):
    return json.loads(text, object_pairs_hook=_unique_object)


def verify_hashes(root, hashes, required):
    if set(hashes) != set(required):
        raise ValueError("required hash entries mismatch")
    for name, digest in hashes.items():
        if Path(name).name != name or base.sha(root / name) != digest:
            raise ValueError("payload hash mismatch: " + name)


def parse_exported_messages(messages):
    if len(messages) != 2 or [m["role"] for m in messages] != ["user", "assistant"]:
        raise ValueError("expected isolated user/assistant branch")
    body = messages[0]["content"]
    if body.startswith("TOPIC METADATA (sampling label; all events are simulated):\n"):
        _, metadata, body = body.split("\n", 2)
        _loads(metadata)
    context, tail = body.rsplit("\n\nQUESTIONS\n", 1)
    if not tail.endswith(SUFFIX):
        raise ValueError("missing answer instruction")
    questions = _loads(tail[: -len(SUFFIX)])
    ids = [q["id"] for q in questions]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("duplicate or missing question IDs")
    answers = _loads(messages[1]["content"])
    if not isinstance(answers, dict) or set(answers) != set(ids):
        raise ValueError("assistant answer IDs mismatch")
    return context, questions, answers


def bounded_window_witnesses(left, right, answer_changed, windows=WINDOWS):
    return {
        f"{side}:{size}": bool(
            answer_changed
            and (
                left[:size] == right[:size]
                if side == "prefix"
                else left[-size:] == right[-size:]
            )
        )
        for size in windows
        for side in ("prefix", "suffix")
    }


def _token_ids(tokenizer, text):
    return tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"]


def audit_export(source: Path, tokenizer, windows=WINDOWS):
    manifest = _loads((source / "manifest.json").read_text())
    plan = _loads((source / "plan.json").read_text())
    fingerprint = hashlib.sha256(base.canonical(plan).encode()).hexdigest()
    if fingerprint != manifest["fingerprint"]:
        raise ValueError("plan fingerprint mismatch")
    verify_hashes(source, manifest["files"], {"train.jsonl", "eval.jsonl"})
    exported, split_seeds = {}, defaultdict(set)
    split_counts = Counter()
    for split in ("train", "eval"):
        for line in (source / f"{split}.jsonl").read_text().splitlines():
            row = _loads(line)
            if row["split"] != split or row["example_id"] in exported:
                raise ValueError("duplicate example or split mismatch")
            exported[row["example_id"]] = row
            split_seeds[split].add(row["world_seed"])
            split_counts[split] += 1
    if split_seeds["train"] & split_seeds["eval"]:
        raise ValueError("world seed split leakage")
    summary, details, visited = defaultdict(Counter), [], set()
    shards = manifest["shards"]
    if len(shards) != manifest["completed_shards"] or len(
        {s["shard_id"] for s in shards}
    ) != len(shards):
        raise ValueError("shard count/identity mismatch")
    actual_shards = {p.name for p in (source / "shards").iterdir() if p.is_dir()}
    if actual_shards != {s["shard_id"] for s in shards}:
        raise ValueError("missing or unlisted shard")
    total_qa = 0
    for receipt in shards:
        shard_id = receipt["shard_id"]
        if Path(shard_id).name != shard_id:
            raise ValueError("unsafe shard path")
        shard = source / "shards" / shard_id
        if (
            _loads((shard / "receipt.json").read_text()) != receipt
            or receipt["fingerprint"] != fingerprint
        ):
            raise ValueError("receipt mismatch")
        verify_hashes(shard, receipt["files"], {"world.json", "rows.jsonl"})
        bundle = _loads((shard / "world.json").read_text())
        rows = [
            _loads(line) for line in (shard / "rows.jsonl").read_text().splitlines()
        ]
        # Joint packaging exports exactly one factual/counterfactual pair per
        # shard; split packaging (workflow) exports one row per supervised
        # question per view, so a shard carries 2 * n_questions rows. Both
        # views must always be present; the pairing below is by question id
        # within one shard either way.
        if not rows or {r["view"] for r in rows} != {"factual", "counterfactual"}:
            raise ValueError("missing paired view")
        if receipt.get("packaging") == "joint" and len(rows) != 2:
            raise ValueError("joint shard must hold exactly two rows")
        if receipt.get("rows_per_view") and len(rows) != 2 * receipt["rows_per_view"]:
            raise ValueError("row count disagrees with receipt rows_per_view")
        family = receipt["family"]
        module = compiler(family)
        stats = summary[family]
        views = {}
        for row in rows:
            identity = row["example_id"]
            if identity in visited or exported.get(identity) != row:
                raise ValueError("shard/export mismatch")
            visited.add(identity)
            for key in ("world_id", "family", "n_records", "split", "world_seed"):
                if row[key] != receipt[key]:
                    raise ValueError("row/receipt metadata mismatch: " + key)
            if (
                row["world_id"] != bundle["world_id"]
                or row["world_seed"] != bundle["seed"]
                or row["family"] != bundle["family"]
            ):
                raise ValueError("world metadata mismatch")
            if row["topic"] != bundle.get(
                "topic", bundle.get("lineage", {}).get("topic")
            ):
                raise ValueError("topic metadata mismatch")
            if row["topic"]:
                visible_topic = row["messages"][0]["content"].split("\n", 2)[1]
                if _loads(visible_topic) != row["topic"]:
                    raise ValueError("visible topic metadata mismatch")
            context, questions, answers = parse_exported_messages(row["messages"])
            variant = bundle if row["view"] == "factual" else bundle["counterfactual"]
            tasks = {t["task_id"]: t for t in variant["tasks"]}
            # A joint row carries the whole variant context verbatim. A split
            # row's context is a RE-RENDERED prefix of the variant world
            # (workflow_context re-renders with a corrected header, e.g. the
            # event count), so string-prefix comparison does not hold; the
            # row's context must reproduce exactly from the variant plus the
            # question's cutoff.
            if receipt.get("packaging") == "split":
                task = tasks[row["supervised_question_id"]]
                expected = module.workflow_context(
                    json.loads(variant["context"]), task["question"]["cutoff"]
                )
                if context != expected:
                    raise ValueError("split row context is not the cutoff re-render")
            elif context != variant["context"]:
                raise ValueError("visible task/context coverage mismatch")
            # Joint rows supervise every task; split rows supervise exactly
            # the one named by supervised_question_id.
            if receipt.get("packaging") == "split":
                if (
                    set(answers) != {row["supervised_question_id"]}
                    or len(questions) != row["qa_count"]
                    or row["qa_count"] != 1
                ):
                    raise ValueError("split row answer coverage mismatch")
            elif set(tasks) != set(answers) or len(questions) != row["qa_count"]:
                raise ValueError("visible task/answer coverage mismatch")
            for question in questions:
                task = tasks[question["id"]]
                if (
                    question["question"] != task["question"]
                    or question["instruction"] != task["prompt"]
                ):
                    raise ValueError("visible question/instruction mismatch")
                actual = module.solve_visible(context, question["question"])
                if base.canonical(actual) != base.canonical(answers[question["id"]]):
                    raise ValueError(
                        "actual assistant answer fails independent visible solver"
                    )
                if base.canonical(actual) != base.canonical(task["answer"]):
                    raise ValueError("stored gold disagrees with visible solver")
                stats["verified_qa_answers"] += 1
                compact = task.get("controls", {}).get("oracle_compact_context")
                if compact is not None:
                    stats["oracle_compact_tested"] += 1
                    if module.solve_visible(compact, question["question"]) != actual:
                        raise ValueError("oracle compact answer mismatch")
                    stats["oracle_compact_passed"] += 1
                    stats["oracle_compact_tokens_sum"] += len(
                        _token_ids(tokenizer, compact)
                    )
            # Pair by question id within the view: joint rows contribute all
            # ids at once, split rows contribute their single supervised id.
            # The counterfactual must answer the SAME questions with the same
            # instructions; for split rows the pairing key is the question id
            # (the contexts legitimately differ -- that is the point of the
            # prefix contract).
            entry = views.setdefault(
                row["view"],
                {"questions": {}, "answers": {}, "token_ids": {}},
            )
            context_ids = _token_ids(tokenizer, context)
            for question in questions:
                qid = question["id"]
                if qid in entry["questions"]:
                    raise ValueError("duplicate question id within view")
                entry["questions"][qid] = question
                entry["answers"][qid] = answers[qid]
                entry["token_ids"][qid] = context_ids
            stats["rows"] += 1
            total_qa += len(questions)
        left, right = views["factual"], views["counterfactual"]
        if set(left["questions"]) != set(right["questions"]):
            raise ValueError("counterfactual changes question set")
        for qid, question in left["questions"].items():
            other = right["questions"][qid]
            if (
                question["question"] != other["question"]
                or question["instruction"] != other["instruction"]
            ):
                raise ValueError("counterfactual changes question/instruction")
        witnesses = Counter()
        changed = 0
        for qid in left["questions"]:
            answer_changed = base.canonical(left["answers"][qid]) != base.canonical(
                right["answers"][qid]
            )
            changed += answer_changed
            # Window witnesses compare the two views' rendered contexts. For
            # joint rows there is one context per view; for split rows each
            # question has its own prefix pair, so compare that question's
            # factual/counterfactual prefix tokens.
            left_ids = left["token_ids"][qid]
            right_ids = right["token_ids"][qid]
            for name, found in bounded_window_witnesses(
                left_ids, right_ids, answer_changed, windows
            ).items():
                witnesses[name] += found
        stats["changed_pairs"] += changed
        stats["unchanged_pairs"] += len(left["questions"]) - changed
        stats["world_shards"] += 1
        for name, count in witnesses.items():
            stats["identical_window_changed_answer:" + name] += count
        if receipt["rows"] != len(rows) or receipt["qa_pairs"] != sum(
            r["qa_count"] for r in rows
        ):
            raise ValueError("receipt counts mismatch")
        details.append(
            {
                "shard_id": shard_id,
                "family": family,
                "changed_pairs": changed,
                "unchanged_pairs": len(left["questions"]) - changed,
                "window_witnesses": dict(witnesses),
                "context_tokens": [
                    len(left["token_ids"][qid0]) if (qid0 := next(iter(left["token_ids"]), None)) else 0,
                    len(right["token_ids"][qid1]) if (qid1 := next(iter(right["token_ids"]), None)) else 0,
                ],
            }
        )
    if (
        visited != set(exported)
        or len(visited) != manifest["rows"]
        or total_qa != manifest["qa_pairs"]
        or dict(split_counts) != {k: v for k, v in manifest["split_rows"].items() if v}
    ):
        raise ValueError("manifest/export counts mismatch")
    return {
        "schema_version": "longworld.curriculum-export-audit.v1",
        "passed": True,
        "manifest_sha256": base.sha(source / "manifest.json"),
        "tokenizer": {
            "model": base.MODEL,
            "revision": base.REVISION,
            "add_special_tokens": False,
        },
        "rows": len(visited),
        "qa_answers": total_qa,
        "families": {k: dict(v) for k, v in summary.items()},
        "shards": details,
        "scope": "Paired identical prefix/suffix context tokens plus identical queries with changed answers certify only the specified window-only observation insufficiency. Oracle compact is privileged selection. Missing-header parser rejection is not evidence of necessity.",
        "strict_long_dependency_verified": False,
        "model_utility_measured": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("audit output must be new")
    state = _loads((args.input / "plan.json").read_text())
    if (
        base.resolved_tokenizer_asset_manifest_sha256(base.MODEL, base.REVISION)
        != state["tokenizer_assets_sha256"]
    ):
        raise ValueError("pinned tokenizer assets mismatch")
    base._init_worker()
    result = audit_export(args.input, base._TOKENIZER)
    base.write_new_json(args.output, result)
    print(
        base.canonical(
            {
                "passed": result["passed"],
                "rows": result["rows"],
                "qa_answers": result["qa_answers"],
            }
        )
    )
