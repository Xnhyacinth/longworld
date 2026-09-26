"""Compile real PR-review/comment to final-diff path joins from signed CodeForge banks.

The operation returns every (PR, path) for which a review comment anywhere in
that PR's frozen history names a path present in its final merged-head diff.
The certificate is limited to the displayed records and exact path grammar.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.codeforge_reading_proof import (
    _record_spans,
    token_interval,
    visible_file_reader,
)
from longworld.core.codeforge_taskbank import canonical, identity, render_context
from longworld.core.taskbank_dependency_audit import exact_offsets
from scripts.p99_code_content_tasks import _load_bank, _tokenizer, sha
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p132-review-diff-join.v1"
COMMENT = re.compile(r"^review_comment_id=\d+ on ([^\n]+):\n")
DIFF = re.compile(r"^diff -- ([^\n]+)$", re.MULTILINE)
AMBIGUOUS = re.compile(
    r"^(?:rename (?:from|to) |deleted file mode|new file mode|--- /dev/null|\+\+\+ /dev/null)",
    re.MULTILINE,
)
FILES = (
    "train.jsonl",
    "eval.jsonl",
    "sample_index.jsonl",
    "audit.jsonl",
    "rejects.jsonl",
)


def review_path(record: dict) -> str | None:
    match = COMMENT.match(record["text"]) if record["kind"] == "review" else None
    return match.group(1) if match else None


def source_review_check(world: dict) -> None:
    for record in world["records"]:
        path = review_path(record)
        if path is not None and path != record["attributes"].get("path"):
            raise ValueError("review path text disagrees with pinned native attribute")


def scoped_join(visible: dict) -> tuple[list[dict], list[dict]]:
    """Read the answer from visible text and links, never hidden path attributes."""
    heads = visible_file_reader(visible, "merged_files_union")["heads"]
    records = {record["id"]: record for record in visible["records"]}
    rows = []
    for scope, head in zip(visible["scope"], heads, strict=True):
        if scope["pull_request"] != head["pull_request"]:
            raise ValueError("scope/head PR mismatch")
        head_text = records[head["head_id"]]["text"]
        if AMBIGUOUS.search(head_text):
            raise ValueError(
                "rename/deletion/new-file diff marker needs separate path semantics"
            )
        headers = set(DIFF.findall(head_text))
        if headers != set(head["files"]):
            raise ValueError("merged-head path parser disagreement")
        comments: dict[str, list[str]] = {}
        for key in scope["records"]:
            path = review_path(records[key])
            if path is not None:
                comments.setdefault(path, []).append(key)
        rows.append(
            {
                "pull_request": head["pull_request"],
                "head_id": head["head_id"],
                "comment_records": comments,
                "final_paths": sorted(headers),
                "positive": sorted(set(comments) & headers),
                "excluded": sorted(set(comments) - headers),
            }
        )
    answer = sorted(
        (
            {"pull_request": row["pull_request"], "path": path}
            for row in rows
            for path in row["positive"]
        ),
        key=lambda item: (item["pull_request"], item["path"]),
    )
    return answer, rows


def intervene(visible: dict, row: dict, path: str, side: str) -> dict:
    """Hypothetical reader-text change; this is never a source modification."""
    changed = copy.deepcopy(visible)
    by_id = {record["id"]: record for record in changed["records"]}
    replacement = "__P132_REMOVED_PATH__"
    if side == "comment":
        for key in row["comment_records"][path]:
            record = by_id[key]
            old = COMMENT.match(record["text"])
            if old is None or old.group(1) != path:
                raise ValueError("comment intervention cannot locate exact path")
            record["text"] = record["text"].replace(path, replacement)
    elif side == "target":
        record = by_id[row["head_id"]]
        lines = record["text"].splitlines(keepends=True)
        matches = [
            i
            for i, line in enumerate(lines)
            if line.rstrip("\r\n") == "diff -- " + path
        ]
        if len(matches) != 1:
            raise ValueError("final diff does not uniquely contain target path")
        record["text"] = record["text"].replace(path, replacement)
        if path in record["text"]:
            raise ValueError("target intervention left original path text")
    else:
        raise ValueError("unknown intervention side")
    return changed


def add_excluded_path(visible: dict, row: dict, path: str) -> dict:
    """Hypothetically add a previously reviewed path to the final diff."""
    if path not in row["excluded"]:
        raise ValueError("path is not an excluded review comment")
    changed = copy.deepcopy(visible)
    head = next(
        record for record in changed["records"] if record["id"] == row["head_id"]
    )
    head["text"] += "\ndiff -- " + path + "\n"
    return changed


def evidence_positions(
    context: str, visible: dict, rows: list[dict], tokenizer
) -> dict:
    offsets = exact_offsets(context, tokenizer)
    starts, ends = [a for a, _ in offsets], [b for _, b in offsets]
    record_spans = _record_spans(context, visible)
    evidence = []
    for row in rows:
        for path in row["positive"]:
            supports = {"comment": [], "target": []}
            char_supports = {"comment": [], "target": []}
            for key in row["comment_records"][path]:
                start, stop = record_spans[key]
                needle = json.dumps(" on " + path + ":\n", ensure_ascii=False)[1:-1]
                at = context.find(needle, start, stop)
                if at < 0:
                    raise ValueError("review path absent from final context")
                left = at + len(" on ")
                supports["comment"].append(
                    token_interval(left, left + len(path), starts, ends)
                )
                char_supports["comment"].append((left, left + len(path)))
            start, stop = record_spans[row["head_id"]]
            needle = json.dumps("diff -- " + path, ensure_ascii=False)[1:-1]
            at = context.find(needle, start, stop)
            if at < 0 or context.find(needle, at + 1, stop) >= 0:
                raise ValueError("diff path support absent or repeated in head")
            left = at + len("diff -- ")
            supports["target"].append(
                token_interval(left, left + len(path), starts, ends)
            )
            char_supports["target"].append((left, left + len(path)))
            gap = min(
                max(0, max(a[0], b[0]) - min(a[1], b[1]))
                for a in supports["comment"]
                for b in supports["target"]
            )
            nonproof_occurrences = [
                record["id"]
                for record in visible["records"]
                if path in record["text"]
                and record["id"] != row["head_id"]
                and record["id"] not in row["comment_records"][path]
            ]
            evidence.append(
                {
                    "pull_request": row["pull_request"],
                    "path": path,
                    "comment_record_ids": row["comment_records"][path],
                    "head_record_id": row["head_id"],
                    "context_token_intervals": supports,
                    "context_char_intervals": char_supports,
                    "minimum_pair_gap_tokens": gap,
                    "nonproof_path_record_ids": nonproof_occurrences,
                }
            )
    return {
        "items": evidence,
        "max_item_minimum_gap_tokens": max(
            item["minimum_pair_gap_tokens"] for item in evidence
        ),
    }


def balanced_pairs(pairs: list[tuple[dict, dict]]) -> list[tuple[dict, dict]]:
    """Prefer new PR episodes before reusing one across candidate scopes."""
    remaining = list(pairs)
    exposure: Counter[str] = Counter()
    ordered = []
    while remaining:
        best = min(
            range(len(remaining)),
            key=lambda pos: (
                sum(exposure[episode["episode_id"]] for episode in remaining[pos]),
                max(exposure[episode["episode_id"]] for episode in remaining[pos]),
                pos,
            ),
        )
        pair = remaining.pop(best)
        ordered.append(pair)
        exposure.update(episode["episode_id"] for episode in pair)
    return ordered


def compile_bank(
    world: dict, tokenizer, config: dict, entry: dict
) -> tuple[list[tuple[dict, dict, dict]], list[dict]]:
    source_review_check(world)
    accepted, rejects = [], []
    eligible = []
    for first, second in combinations(world["episodes"], 2):
        scope = [first["episode_id"], second["episode_id"]]
        label = {"source_group": world["source_group_id"], "episode_ids": scope}
        try:
            visible = json.loads(render_context(world, scope))
            _, rows = scoped_join(visible)
            if len(rows) != 2 or any(not row["positive"] for row in rows):
                raise ValueError("both PRs must contribute a reviewed final path")
            if not any(row["excluded"] for row in rows):
                raise ValueError("no commented path excluded by final diff")
            eligible.append((first, second))
        except (ValueError, KeyError, TypeError, IndexError) as error:
            rejects.append({**label, "reason": f"{type(error).__name__}: {error}"})
    for first, second in balanced_pairs(eligible):
        scope = [first["episode_id"], second["episode_id"]]
        label = {"source_group": world["source_group_id"], "episode_ids": scope}
        try:
            context = render_context(world, scope)
            visible = json.loads(context)
            answer, rows = scoped_join(visible)
            if len(rows) != 2 or any(not row["positive"] for row in rows):
                raise ValueError("both PRs must contribute a reviewed final path")
            if not any(row["excluded"] for row in rows):
                raise ValueError("no commented path excluded by final diff")
            if len(accepted) >= config["max_tasks_per_repository"]:
                raise ValueError("repository pilot cap")
            evidence = evidence_positions(context, visible, rows, tokenizer)
            if evidence["max_item_minimum_gap_tokens"] < config["min_pair_gap_tokens"]:
                raise ValueError(
                    "no required review-to-final-diff pair has long token gap"
                )
            intervention_receipts = []
            for row in rows:
                for path in row["positive"]:
                    for side in ("comment", "target"):
                        changed = intervene(visible, row, path, side)
                        changed_answer, _ = scoped_join(changed)
                        expected_removed = [
                            item
                            for item in answer
                            if item
                            != {"pull_request": row["pull_request"], "path": path}
                        ]
                        if changed_answer != expected_removed:
                            raise ValueError(
                                side
                                + "-side text intervention did not remove exactly one answer member"
                            )
                        intervention_receipts.append(
                            {
                                "pull_request": row["pull_request"],
                                "path": path,
                                "side": side,
                                "context_sha256": hashlib.sha256(
                                    canonical(changed).encode()
                                ).hexdigest(),
                                "answer": changed_answer,
                            }
                        )
            excluded = next((row for row in rows if row["excluded"]), None)
            if excluded is None:
                raise ValueError("excluded review path disappeared")
            new_path = excluded["excluded"][0]
            inserted = add_excluded_path(visible, excluded, new_path)
            inserted_answer, _ = scoped_join(inserted)
            expected = sorted(
                [*answer, {"pull_request": excluded["pull_request"], "path": new_path}],
                key=lambda item: (item["pull_request"], item["path"]),
            )
            if inserted_answer != expected:
                raise ValueError(
                    "adding excluded commented path did not add exact member"
                )
            numbers = [row["pull_request"] for row in rows]
            question = (
                f"In repository {world['source_group_id']}, consider merged PRs #{numbers[0]} and "
                f"#{numbers[1]}. For each PR, list every file path that is named by a review "
                "comment anywhere in its supplied history (including earlier revisions) and "
                "also appears as a changed file in that PR's final merged-head diff. "
                "A comment on a path absent from the final diff does not count. Use exact "
                "case-sensitive paths. Return a JSON array sorted by pull_request then path, "
                "with keys pull_request and path; include all matches."
            )
            messages = [
                {
                    "role": "user",
                    "content": question + "\n\nSource records:\n" + context,
                },
                {"role": "assistant", "content": canonical(answer)},
            ]
            encoded = tokenize_assistant_only(
                tokenizer, messages, config["max_chat_tokens"]
            )
            full = len(encoded["input_ids"])
            supervised = sum(label != -100 for label in encoded["labels"])
            rendered = _render_chat(tokenizer, messages, generation_prompt=False)
            if rendered.count(context) != 1:
                raise ValueError("source context not unique in final chat")
            chat_offsets = exact_offsets(rendered, tokenizer)
            if len(chat_offsets) != full:
                raise ValueError("final chat offset/token count mismatch")
            context_start = rendered.index(context)
            chat_starts, chat_ends = (
                [x for x, _ in chat_offsets],
                [y for _, y in chat_offsets],
            )
            for item in evidence["items"]:
                translated = {}
                for side, intervals in item["context_char_intervals"].items():
                    translated[side] = [
                        token_interval(
                            context_start + a, context_start + b, chat_starts, chat_ends
                        )
                        for a, b in intervals
                    ]
                item["final_chat_token_intervals"] = translated
                item["final_chat_minimum_pair_gap_tokens"] = min(
                    max(0, max(a[0], b[0]) - min(a[1], b[1]))
                    for a in translated["comment"]
                    for b in translated["target"]
                )
            evidence["max_item_final_chat_minimum_gap_tokens"] = max(
                item["final_chat_minimum_pair_gap_tokens"] for item in evidence["items"]
            )
            evidence["long_answer_members"] = sum(
                item["final_chat_minimum_pair_gap_tokens"]
                >= config["min_pair_gap_tokens"]
                for item in evidence["items"]
            )
            evidence["long_members_without_other_path_occurrences"] = sum(
                item["final_chat_minimum_pair_gap_tokens"]
                >= config["min_pair_gap_tokens"]
                and not item["nonproof_path_record_ids"]
                for item in evidence["items"]
            )
            if (
                evidence["max_item_final_chat_minimum_gap_tokens"]
                < config["min_pair_gap_tokens"]
            ):
                raise ValueError("no final-chat review-to-diff pair has long token gap")
            semantic = identity(
                {
                    "world": world["world_id"],
                    "scope": scope,
                    "operation": "reviewed_final_diff_paths",
                }
            )
            sample = identity(
                {
                    "semantic_task_id": semantic,
                    "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                }
            )
            index = {
                "sample_id": sample,
                "example_id": sample,
                "semantic_task_id": semantic,
                "task_id": semantic,
                "world_id": world["world_id"],
                "source_group": world["source_group_id"],
                "domain": "codeforge",
                "operation": "reviewed_final_diff_paths",
                "task_type": "reviewed_final_diff_paths",
                "source_kind": "real_code_workflow",
                "split": world["split"],
                "source_bank_root": entry["bank_root"],
                "source_bank_receipt_sha256": entry["receipt_sha256"],
                "bank_directory": str(ROOT / entry["bank_root"]),
                "world_instance_id": world["world_instance_id"],
                "full_chat_tokens": full,
                "assistant_tokens": supervised,
                "dependency_status": "scoped_review_to_final_diff_text_intervention",
                "strict_long_dependency_verified": False,
                "train_ready": False,
                "native_row_ref": "audit.jsonl#sample_id=" + sample,
            }
            audit = {
                "sample_id": sample,
                "semantic_task_id": semantic,
                "source_group": world["source_group_id"],
                "episode_ids": scope,
                "source_prs": numbers,
                "answer": answer,
                "excluded_commented_paths": {
                    str(row["pull_request"]): row["excluded"] for row in rows
                },
                "evidence": evidence,
                "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                "final_chat_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "final_chat_tokens": full,
                "assistant_tokens": supervised,
                "assistant_mask_prefix_tokens": full - supervised,
                "comment_and_target_text_interventions_change_answer": True,
                "intervention_receipts": intervention_receipts,
                "excluded_path_insertion": {
                    "pull_request": excluded["pull_request"],
                    "path": new_path,
                    "context_sha256": hashlib.sha256(
                        canonical(inserted).encode()
                    ).hexdigest(),
                    "answer": inserted_answer,
                },
                "source_bank_root": entry["bank_root"],
                "source_bank_receipt_sha256": entry["receipt_sha256"],
                "reader_visible_replay": True,
                "certificate_scope": "exact review-comment path and final-head diff grammar on the displayed two PR histories; no global shortcut exclusion",
            }
            accepted.append(
                ({"example_id": sample, "messages": messages}, index, audit)
            )
        except (ValueError, KeyError, TypeError, IndexError) as error:
            rejects.append({**label, "reason": f"{type(error).__name__}: {error}"})
    return accepted, rejects


def run(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported P132 config")
    for binding in config["source_configs"]:
        if sha(ROOT / binding["path"]) != binding["sha256"]:
            raise ValueError("source config SHA changed")
    banks = [
        entry
        for binding in config["source_configs"]
        for entry in json.loads((ROOT / binding["path"]).read_text())["banks"]
    ]
    if len({entry["source_group_id"] for entry in banks}) != len(banks):
        raise ValueError("duplicate repository source group")
    tokenizer = _tokenizer()
    candidates, rejects = [], []
    for entry in banks:
        rows, failed = compile_bank(_load_bank(entry), tokenizer, config, entry)
        candidates.extend(rows)
        rejects.extend(failed)
    if not candidates:
        raise ValueError("P132 produced no reader candidates")
    groups = {split: set() for split in ("train", "eval")}
    for _, index, _ in candidates:
        groups[index["split"]].add(index["source_group"])
    if groups["train"] & groups["eval"]:
        raise ValueError("repository split leaked")
    output.mkdir(parents=True, exist_ok=verify_only)
    payload = {name: [] for name in FILES}
    split_positions = Counter()
    for row, index, audit in candidates:
        index["output_file"] = index["split"] + ".jsonl"
        index["row_index"] = split_positions[index["split"]]
        split_positions[index["split"]] += 1
        payload[index["split"] + ".jsonl"].append(row)
        payload["sample_index.jsonl"].append(index)
        payload["audit.jsonl"].append(audit)
    payload["rejects.jsonl"] = rejects
    hashes = {}
    for name, rows in payload.items():
        content = "".join(canonical(row) + "\n" for row in rows)
        if verify_only:
            if (output / name).read_text() != content:
                raise ValueError("P132 replay differs: " + name)
        else:
            (output / name).write_text(content)
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
    manifest = {
        "schema_version": SCHEMA,
        "config_sha256": sha(config_path),
        "code_sha256": sha(Path(__file__)),
        "views": len(candidates),
        "semantic_tasks": len(
            {index["semantic_task_id"] for _, index, _ in candidates}
        ),
        "tasks_by_repository": dict(
            sorted(Counter(index["source_group"] for _, index, _ in candidates).items())
        ),
        "split_views": dict(
            sorted(Counter(index["split"] for _, index, _ in candidates).items())
        ),
        "rejects": len(rejects),
        "reject_reasons": dict(
            sorted(Counter(row["reason"].split(": ", 1)[-1] for row in rejects).items())
        ),
        "min_final_chat_tokens": min(
            index["full_chat_tokens"] for _, index, _ in candidates
        ),
        "max_final_chat_tokens": max(
            index["full_chat_tokens"] for _, index, _ in candidates
        ),
        "min_long_pair_gap_tokens": min(
            audit["evidence"]["max_item_final_chat_minimum_gap_tokens"]
            for _, _, audit in candidates
        ),
        "answer_members": sum(len(audit["answer"]) for _, _, audit in candidates),
        "long_answer_members": sum(
            audit["evidence"]["long_answer_members"] for _, _, audit in candidates
        ),
        "tasks_with_clean_long_member": sum(
            audit["evidence"]["long_members_without_other_path_occurrences"] > 0
            for _, _, audit in candidates
        ),
        "long_distance_claim": "at least one answer member per task has a >=8192 final-chat-token minimum review-comment to final-head diff support gap; not every member",
        "files_sha256": hashes,
        "train_ready": False,
        "strict_long_dependency_verified": False,
        "license_status": "local_research_only",
    }
    if verify_only:
        if json.loads((output / "manifest.json").read_text()) != manifest:
            raise ValueError("P132 manifest replay differs")
    else:
        (output / "manifest.json").write_text(canonical(manifest) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.config, args.output, verify_only=args.verify_only), indent=2
        )
    )


if __name__ == "__main__":
    main()
