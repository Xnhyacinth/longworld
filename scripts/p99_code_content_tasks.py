"""Compile source-backed added-code tasks from pinned CodeForge banks.

One candidate asks for every (PR, changed path) whose merged-head added lines
contain either of two identifiers. Each identifier is selected from a
different real PR. The audit distinguishes local content support from a
scoped two-source, >16K evidence-span certificate; neither is a global proof
of irreducible long-context reasoning.
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

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.codeforge_reading_proof import (
    _record_spans,
    token_interval,
    visible_file_reader,
)
from longworld.core.codeforge_taskbank import canonical, identity, render_context
from longworld.core.taskbank_dependency_audit import exact_offsets

SCHEMA = "longworld.p99-code-content.v1"
TOKENIZER_ID = "Qwen/Qwen3.5-4B"
TOKENIZER_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]{7,31}(?![A-Za-z0-9_])"
)
DIFF_PATTERN = re.compile(r"^diff -- (.+)$")
OUTPUTS = (
    "train.jsonl",
    "eval.jsonl",
    "sample_index.jsonl",
    "audit.jsonl",
    "rejects.jsonl",
)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _bound_path(raw: str) -> Path:
    """Relocate old workspace receipts only under this repository's root."""
    path = Path(raw)
    if path.is_file():
        return path
    prefix = "/workspace/wynckeliao/longworld-worlds/"
    if not raw.startswith(prefix):
        raise ValueError("unavailable CodeForge source binding: " + raw)
    relative = Path(raw[len(prefix) :])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe relocated CodeForge source binding: " + raw)
    relocated = ROOT / relative
    if not relocated.is_file():
        raise ValueError("unavailable relocated CodeForge source binding: " + raw)
    return relocated


def parse_added_lines(text: str) -> dict[str, list[str]]:
    """Only + lines under a visible diff header, never summary or ---/+++ lines."""
    paths: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        match = DIFF_PATTERN.fullmatch(line)
        if match:
            current = match.group(1)
            paths.setdefault(current, [])
        elif (
            current is not None and line.startswith("+") and not line.startswith("+++")
        ):
            paths[current].append(line[1:])
    return paths


def added_matches(heads: list[dict], tokens: tuple[str, str]) -> list[dict]:
    """Execute the task using only the final reader's selected head text."""
    matches = []
    for head in heads:
        for path, lines in parse_added_lines(head["text"]).items():
            if any(
                any(
                    re.search(
                        rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", line
                    )
                    for line in lines
                )
                for token in tokens
            ):
                matches.append({"pull_request": head["pull_request"], "path": path})
    return sorted(matches, key=lambda row: (row["pull_request"], row["path"]))


def _head_rows(visible: dict) -> list[dict]:
    replay = visible_file_reader(visible, "merged_files_union")
    records = {row["id"]: row for row in visible["records"]}
    return [
        {
            "record_id": item["head_id"],
            "pull_request": item["pull_request"],
            "text": records[item["head_id"]]["text"],
        }
        for item in replay["heads"]
    ]


def _anchors(heads: list[dict], control_context: str) -> tuple[str, str] | None:
    if len(heads) != 2:
        return None
    names = [path.lower() for head in heads for path in parse_added_lines(head["text"])]
    choices = []
    for head in heads:
        patch = parse_added_lines(head["text"])
        other = next(other for other in heads if other is not head)
        tokens = set()
        for lines in patch.values():
            for line in lines:
                tokens.update(TOKEN_PATTERN.findall(line))
        clean = sorted(
            token
            for token in tokens
            if ("_" in token or any(char.isupper() for char in token[1:]))
            and all(token.lower() not in name for name in names)
            and token not in other["text"]
            and head["text"].count(token) == 1
            and not re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
                control_context,
            )
        )
        if not clean:
            return None
        choices.append(clean)
    for left in choices[0]:
        for right in choices[1]:
            if left != right:
                return left, right
    return None


def _support_positions(
    context: str, visible: dict, heads: list[dict], tokens: tuple[str, str], tokenizer
) -> list[dict]:
    offsets = exact_offsets(context, tokenizer)
    starts, ends = [a for a, _ in offsets], [b for _, b in offsets]
    spans = _record_spans(context, visible)
    evidence = []
    for head, token in zip(heads, tokens, strict=True):
        patch = parse_added_lines(head["text"])
        lines = [line for lines in patch.values() for line in lines if token in line]
        if len(lines) != 1:
            raise ValueError("identifier has ambiguous added-line support")
        serialized_line = json.dumps("+" + lines[0], ensure_ascii=False)[1:-1]
        record_start, record_end = spans[head["record_id"]]
        at = context.find(serialized_line, record_start, record_end)
        if at < 0:
            raise ValueError("added line is absent from final reader context")
        position = context.find(token, at, at + len(serialized_line))
        if position < 0:
            raise ValueError("identifier is absent from added line")
        left, right = token_interval(position, position + len(token), starts, ends)
        evidence.append(
            {
                "pull_request": head["pull_request"],
                "record_id": head["record_id"],
                "identifier": token,
                "token_start": left,
                "token_end": right,
            }
        )
    return evidence


def _load_bank(entry: dict) -> dict:
    root = ROOT / entry["bank_root"]
    receipt_path = root / "BUILD_RECEIPT.json"
    if sha(receipt_path) != entry["receipt_sha256"]:
        raise ValueError("frozen CodeForge bank receipt changed")
    receipt = json.loads(receipt_path.read_text())
    for name, expected in receipt["files"].items():
        if sha(root / name) != expected:
            raise ValueError("frozen CodeForge bank file changed: " + name)
    for bound in receipt["source_bindings"]:
        if sha(_bound_path(bound["path"])) != bound["sha256"]:
            raise ValueError("frozen CodeForge source binding changed")
    world = json.loads((root / "world.json").read_text())
    if (
        world["source_group_id"] != entry["source_group_id"]
        or world["split"] != entry["split"]
        or receipt["source_group_id"] != entry["source_group_id"]
        or receipt["split"] != entry["split"]
    ):
        raise ValueError("CodeForge repository split/source mismatch")
    return world


def _tokenizer():
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            TOKENIZER_ID,
            revision=TOKENIZER_REVISION,
            local_files_only=True,
            trust_remote_code=False,
        )


def compile_bank(
    world: dict, tokenizer, *, max_tasks: int, min_span: int, max_chat_tokens: int
) -> tuple[list[tuple[dict, dict, dict]], list[dict]]:
    from scripts.train_sft import tokenize_assistant_only

    rows, rejects = [], []
    episodes = world["episodes"]
    for first, second in combinations(episodes, 2):
        if len(rows) >= max_tasks:
            break
        scope = [first["episode_id"], second["episode_id"]]
        label = {"source_group": world["source_group_id"], "episode_ids": scope}
        try:
            context = render_context(world, scope)
            visible = json.loads(context)
            heads = _head_rows(visible)
            if len(heads) != 2:
                raise ValueError("scope has fewer than two unique merge heads")
            control_visible = copy.deepcopy(visible)
            selected_heads = {head["record_id"] for head in heads}
            for record in control_visible["records"]:
                if record["id"] in selected_heads:
                    record["text"] = "\n".join(
                        line
                        for line in record["text"].splitlines()
                        if not (line.startswith("+") and not line.startswith("+++"))
                    )
            control_context = canonical(control_visible)
            tokens = _anchors(heads, control_context)
            if tokens is None:
                raise ValueError("no distinct content-only added-code anchors")
            answer = added_matches(heads, tokens)
            if len(answer) != 2 or len({item["pull_request"] for item in answer}) != 2:
                raise ValueError("both source heads must uniquely contribute to answer")
            control_records = {
                record["id"]: record for record in control_visible["records"]
            }
            controlled = [
                {**head, "text": control_records[head["record_id"]]["text"]}
                for head in heads
            ]
            if added_matches(controlled, tokens):
                raise ValueError("filename-preserving code removal retains answer")
            evidence = _support_positions(context, visible, heads, tokens, tokenizer)
            span = max(item["token_end"] for item in evidence) - min(
                item["token_start"] for item in evidence
            )
            if span <= min_span:
                raise ValueError(f"evidence token span {span} <= {min_span}")
            numbers = [head["pull_request"] for head in heads]
            question = (
                f"Repository {world['source_group_id']}. Across merged pull requests "
                f"#{numbers[0]} and #{numbers[1]}, list every (pull request, changed file path) "
                f"whose merged-head diff has an added code line containing either identifier "
                f"{tokens[0]!r} or {tokens[1]!r} as a whole identifier. Inspect added lines, "
                "not PR titles, filenames alone, or removed lines. Return a JSON array sorted by "
                "pull_request then path, with keys pull_request and path; include all matches."
            )
            messages = [
                {
                    "role": "user",
                    "content": question + "\n\nSource records:\n" + context,
                },
                {"role": "assistant", "content": canonical(answer)},
            ]
            encoded = tokenize_assistant_only(tokenizer, messages, 1 << 30)
            full = len(encoded["input_ids"])
            supervised = sum(value != -100 for value in encoded["labels"])
            if full > max_chat_tokens or supervised <= 0:
                raise ValueError("final chat overflows or has empty assistant mask")
            semantic = identity(
                {
                    "world": world["world_id"],
                    "operation": "added_line_cross_pr_complete_set",
                    "scope": scope,
                    "tokens": tokens,
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
                "operation": "added_line_cross_pr_complete_set",
                "task_type": "added_line_cross_pr_complete_set",
                "source_kind": "real_code_workflow",
                "split": world["split"],
                "full_chat_tokens": full,
                "assistant_tokens": supervised,
                "dependency_status": "content_backed_two_source_scoped_certificate",
                "strict_long_dependency_verified": False,
                "train_ready": False,
            }
            audit = {
                "sample_id": sample,
                "semantic_task_id": semantic,
                "source_group": world["source_group_id"],
                "episode_ids": scope,
                "source_prs": numbers,
                "identifiers": tokens,
                "answer": answer,
                "evidence": evidence,
                "evidence_token_span": span,
                "single_raw_16k_window_insufficient_for_both_witnesses": span
                > min_span,
                "filename_preserving_added_code_removal_changes_answer": True,
                "all_selected_identifiers_absent_after_added_code_removal": True,
                "added_code_removed_context_sha256": hashlib.sha256(
                    control_context.encode()
                ).hexdigest(),
                "reader_visible_replay": True,
                "certificate_scope": "two selected added-line witnesses under exact identifier grammar; does not exclude unknown paraphrases or multi-window retrieval",
            }
            rows.append(({"example_id": sample, "messages": messages}, index, audit))
        except (ValueError, KeyError, TypeError) as error:
            rejects.append({**label, "reason": f"{type(error).__name__}: {error}"})
    return rows, rejects


def run(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported P99 CodeForge config")
    jobs = config.get("banks")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("P99 needs pinned native banks")
    if len({job["source_group_id"] for job in jobs}) != len(jobs):
        raise ValueError("repository source group appears twice")
    if (
        config.get("min_evidence_span_tokens") != 16384
        or config.get("max_chat_tokens") != 262144
    ):
        raise ValueError("unsupported P99 proof/reader limits")
    tokenizer = _tokenizer()
    all_rows, rejects = [], []
    for job in jobs:
        world = _load_bank(job)
        rows, failed = compile_bank(
            world,
            tokenizer,
            max_tasks=config["max_tasks_per_repository"],
            min_span=config["min_evidence_span_tokens"],
            max_chat_tokens=config["max_chat_tokens"],
        )
        all_rows.extend(rows)
        rejects.extend(failed)
    if not all_rows:
        raise ValueError("no content-backed P99 code tasks")
    ids = [index["semantic_task_id"] for _, index, _ in all_rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate semantic code task")
    splits = {name: set() for name in ("train", "eval")}
    for _, index, _ in all_rows:
        splits[index["split"]].add(index["source_group"])
    if splits["train"] & splits["eval"]:
        raise ValueError("repository appears in both splits")
    output.mkdir(parents=True, exist_ok=verify_only)
    payloads = {name: [] for name in OUTPUTS}
    for row, index, audit in all_rows:
        payloads[index["split"] + ".jsonl"].append(row)
        payloads["sample_index.jsonl"].append(index)
        payloads["audit.jsonl"].append(audit)
    payloads["rejects.jsonl"] = rejects
    digests = {}
    for name, values in payloads.items():
        text = "".join(canonical(value) + "\n" for value in values)
        path = output / name
        if verify_only:
            if path.read_text() != text:
                raise ValueError("P99 replay differs: " + name)
        else:
            path.write_text(text)
        digests[name] = hashlib.sha256(text.encode()).hexdigest()
    counts = Counter(index["source_group"] for _, index, _ in all_rows)
    manifest = {
        "schema_version": SCHEMA,
        "status": "content_backed_scoped_candidate",
        "config_sha256": sha(config_path),
        "code_sha256": sha(Path(__file__)),
        "views": len(all_rows),
        "semantic_tasks": len(ids),
        "source_groups": len(counts),
        "tasks_by_repository": dict(sorted(counts.items())),
        "split_views": {
            split: sum(index["split"] == split for _, index, _ in all_rows)
            for split in splits
        },
        "rejected_scopes": len(rejects),
        "reject_reasons": dict(
            sorted(Counter(row["reason"].split(": ", 1)[-1] for row in rejects).items())
        ),
        "min_evidence_span_tokens": min(
            audit["evidence_token_span"] for _, _, audit in all_rows
        ),
        "max_evidence_span_tokens": max(
            audit["evidence_token_span"] for _, _, audit in all_rows
        ),
        "files_sha256": digests,
        "train_ready": False,
        "strict_long_dependency_verified": False,
    }
    manifest_path = output / "manifest.json"
    if verify_only:
        if json.loads(manifest_path.read_text()) != manifest:
            raise ValueError("P99 manifest replay differs")
    else:
        manifest_path.write_text(canonical(manifest) + "\n")
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
