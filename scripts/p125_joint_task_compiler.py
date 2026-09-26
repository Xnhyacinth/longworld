"""Compile one bounded joint-answer task from two audited operations per world.

This is independent multi-operation use of the same final reader context. It
does not assert serial dataflow, shortest proof distance, or new source facts.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    _answer_hash,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p125-joint-task-compiler.v1"
MARKERS = ("\n\nQUESTION\n", "\n\nQuestion:\n", "\n\nQuestion:", "\n\nSource records:\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _pin(pin: dict[str, str]) -> Path:
    if set(pin) != {"path", "sha256"}:
        raise ValueError("source pin needs path and sha256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source pin escapes workspace")
    path = (ROOT / relative).resolve(strict=True)
    if not (path.is_relative_to(ROOT) or path.is_relative_to((ROOT / "data").resolve())):
        raise ValueError("source pin escapes workspace data storage")
    if _sha(path) != pin["sha256"]:
        raise ValueError("source materialization changed")
    return path


def split_reader(user: str, expected_context_sha256: str) -> tuple[str, str, str]:
    """Find the exact adapter-defined context, on either side of the query."""
    matches = []
    for marker in MARKERS:
        if user.count(marker) != 1:
            continue
        left, right = user.split(marker, 1)
        if left and right and _hash_text(left) == expected_context_sha256:
            matches.append((left, right.strip(), "context_first"))
        if left and right and _hash_text(right) == expected_context_sha256:
            matches.append((right, left.strip(), "question_first"))
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1 or not matches[0][1]:
        raise ValueError("unrecognized_or_ambiguous_reader_boundary")
    return matches[0]


def operation_family(operation: str) -> str:
    """Parameter variants of one solver operation do not count as two skills."""
    return operation.split(":", 1)[0]


def pair_reason(a: dict, b: dict) -> str | None:
    if not a.get("dependency_status") or not b.get("dependency_status"):
        return "missing_component_dependency_status"
    if a["source_kind"] != b["source_kind"] or a["domain"] != b["domain"]:
        return "source_semantics_mismatch"
    if a["semantic_task_id"] == b["semantic_task_id"]:
        return "same_semantic_task"
    if operation_family(a["operation"]) == operation_family(b["operation"]):
        return "same_operation_family"
    if a["answer_sha256"] == b["answer_sha256"]:
        return "same_answer"
    return None


def _source(materialized: Path) -> tuple[dict[str, dict], dict]:
    manifest = json.loads((materialized / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != "longworld.p95-balanced-materialized-candidates.v1"
        or manifest.get("train_ready") is not False
    ):
        raise ValueError("wrong materialized candidate source")
    for name, digest in manifest["files_sha256"].items():
        if _sha(materialized / name) != digest:
            raise ValueError(f"materialized source changed: {name}")
    index_rows = [json.loads(line) for line in (materialized / "sample_index.jsonl").read_text().splitlines()]
    by_index = {row["candidate"]["sample_id"]: row for row in index_rows}
    if len(by_index) != len(index_rows) or len(by_index) != manifest["selected_views"]:
        raise ValueError("materialized index inventory differs")
    return by_index, manifest


def _selected_readers(materialized: Path, index: dict[str, dict], wanted: set[str]) -> dict[str, dict]:
    """Scan each pinned reader file once, retaining only chosen component rows."""
    positions: dict[str, dict[int, str]] = {"train": {}, "eval": {}}
    for sample in wanted:
        indexed = index[sample]
        split = indexed["candidate"]["split"]
        position = indexed["materialized_row_index"]
        if position in positions[split]:
            raise ValueError("selected readers share one source position")
        positions[split][position] = sample
    by_reader = {}
    for split in ("train", "eval"):
        with (materialized / f"{split}.jsonl").open() as stream:
            for position, line in enumerate(stream):
                expected = positions[split].get(position)
                if expected is None:
                    continue
                row = json.loads(line)
                sample = row["sample_id"]
                if sample != expected or sample in by_reader:
                    raise ValueError("materialized reader identity differs")
                indexed = index[sample]
                digest = _hash_text(_line(row).rstrip("\n"))
                if digest != indexed["reader_sha256"]:
                    raise ValueError("materialized reader SHA differs")
                by_reader[sample] = row
    if set(by_reader) != wanted:
        raise ValueError("selected materialized reader inventory incomplete")
    return by_reader


def _choose_pairs(index: dict[str, dict], max_worlds: int) -> tuple[list[tuple[dict, dict]], list[dict], dict]:
    by_world: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_context: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    operation_frequency = Counter()
    for row in index.values():
        candidate = row["candidate"]
        world = (candidate["source_kind"], candidate["source_group"])
        by_world[world].append(candidate)
        by_context[(*world, candidate["context_sha256"])].append(candidate)
        operation_frequency[operation_family(candidate["operation"])] += 1
    selected, ledger = [], []
    support: Counter[str] = Counter()
    for world in sorted(by_world):
        values = by_world[world]
        operations = {operation_family(row["operation"]) for row in values}
        if len(operations) < 2:
            ledger.append({"source_kind": world[0], "source_group": world[1], "status": "unsupported", "reason": "single_operation_world"})
            support["single_operation_world"] += 1
            continue
        legal = []
        reasons = Counter()
        for key in sorted(k for k in by_context if k[:2] == world):
            for a, b in itertools.combinations(sorted(by_context[key], key=lambda row: row["sample_id"]), 2):
                reason = pair_reason(a, b)
                if reason:
                    reasons[reason] += 1
                    continue
                rarity = 1 / operation_frequency[operation_family(a["operation"])] + 1 / operation_frequency[operation_family(b["operation"])]
                legal.append((-rarity, a["sample_id"], b["sample_id"], a, b))
        if not legal:
            reason = (
                "no_shared_context"
                if not reasons
                else min(reasons, key=lambda key: (-reasons[key], key))
            )
            ledger.append({"source_kind": world[0], "source_group": world[1], "status": "unsupported", "reason": reason, "pair_rejections": dict(sorted(reasons.items()))})
            support[reason] += 1
            continue
        legal.sort(key=lambda row: row[:3])
        _, _, _, a, b = legal[0]
        if len(selected) >= max_worlds:
            ledger.append({"source_kind": world[0], "source_group": world[1], "status": "unsupported", "reason": "world_budget", "legal_pair_options": len(legal)})
            support["world_budget"] += 1
            continue
        selected.append((a, b))
        ledger.append({"source_kind": world[0], "source_group": world[1], "status": "selected_for_compilation", "legal_pair_options": len(legal), "task_ids": [a["sample_id"], b["sample_id"]]})
        support["selected_for_compilation"] += 1
    return selected, ledger, {"worlds": len(by_world), "same_context_cells": len(by_context), "worlds_by_decision": dict(sorted(support.items()))}


def compile(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config" or not 1 <= config.get("max_worlds", 0) <= 500 or not 1 <= config.get("max_seq_len", 0) <= 262144:
        raise ValueError("invalid P125 joint compiler config")
    materialized_manifest = _pin(config["materialized_manifest"])
    materialized = materialized_manifest.parent
    output = (ROOT / output).absolute()
    if output.exists() != verify_only:
        raise ValueError("P125 output must be new or --verify-only")
    index, source_manifest = _source(materialized)
    pairs, decisions, matrix = _choose_pairs(index, config["max_worlds"])
    readers = _selected_readers(materialized, index, {task["sample_id"] for pair in pairs for task in pair})
    tokenizer = get_tokenizer()
    candidate_ledger = CandidateLedger()
    rows = {"train": [], "eval": []}
    sample_index, proofs = [], []
    rejected = Counter()
    for a, b in pairs:
        try:
            ra, rb = readers[a["sample_id"]], readers[b["sample_id"]]
            ca, qa, order_a = split_reader(ra["messages"][0]["content"], a["context_sha256"])
            cb, qb, order_b = split_reader(rb["messages"][0]["content"], b["context_sha256"])
            if ca != cb or a["split"] != b["split"] or a["topic"] != b["topic"]:
                raise ValueError("component_context_or_split_mismatch")
            if qa.casefold() == qb.casefold():
                raise ValueError("identical_component_questions")
            answers = []
            for component, reader in ((a, ra), (b, rb)):
                raw = reader["messages"][1]["content"]
                if _answer_hash(raw) != component["answer_sha256"]:
                    raise ValueError("component_answer_drift")
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError:
                    decoded = raw
                else:
                    if _answer_hash(_line(decoded).rstrip("\n")) != component["answer_sha256"]:
                        raise ValueError("component_answer_decode_drift")
                answers.append(decoded)
            question = (
                "Answer both independent questions using the source context. "
                "Return one JSON object with keys A and B; give each answer in the format its question requests.\n\n"
                f"A. {qa}\n\nB. {qb}"
            )
            user = ca + "\n\nQUESTION\n" + question
            answer = json.dumps({"A": answers[0], "B": answers[1]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            messages = [{"role": "user", "content": user}, {"role": "assistant", "content": answer}]
            encoded = tokenize_assistant_only(tokenizer, messages, config["max_seq_len"])
            full = len(encoded["input_ids"])
            supervised = sum(value != -100 for value in encoded["labels"])
            if not supervised or encoded["labels"] != [-100] * (full - supervised) + encoded["input_ids"][full - supervised:]:
                raise ValueError("assistant_mask_invalid")
            digest = _hash_text(_line({"source": a["source_group"], "context": a["context_sha256"], "tasks": [a["semantic_task_id"], b["semantic_task_id"]]}))[:24]
            sample_id = "p125-joint-" + digest
            reader = {"sample_id": sample_id, "messages": messages}
            native_index = {"sample_id": sample_id, "task_id": digest, "source_kind": a["source_kind"], "source_group": a["source_group"], "domain": a["domain"], "topic": a["topic"], "operation": "joint_multi_operation_answer", "split": a["split"], "dependency_status": "component_bounded_only", "tokenizer_profile": "pinned-chat-template", "full_chat_tokens": full, "input_tokens": full - supervised, "supervised_tokens": supervised, "answer_sha256": _answer_hash(answer)}
            audit_reader(reader, native_index, tokenizer, config["max_seq_len"])
            binding = AdapterBinding(source_kind=a["source_kind"], source_group=a["source_group"], domain=a["domain"], topic=a["topic"], operation=native_index["operation"], evidence_profile="two_audited_same_context_tasks", tokenizer_profile="pinned-chat-template", receipt_path=materialized_manifest, receipt_sha256=_sha(materialized_manifest))
            candidate = normalize_native_candidate(native_index, reader, binding, context_text=ca)
            candidate_ledger.add(candidate)
            split = candidate.split
            position = len(rows[split])
            rows[split].append(reader)
            record = candidate.to_dict()
            record.update(source_name="p125_joint_multi_operation", native_row_ref=f"{materialized}/sample_index.jsonl:{a['sample_id']}+{b['sample_id']}", output_file=f"candidate_{split}.jsonl", row_index=position)
            sample_index.append(record)
            proofs.append({"sample_id": sample_id, "component_sample_ids": [a["sample_id"], b["sample_id"]], "component_semantic_task_ids": [a["semantic_task_id"], b["semantic_task_id"]], "component_operations": [a["operation"], b["operation"]], "component_dependency_status": [a["dependency_status"], b["dependency_status"]], "context_sha256": a["context_sha256"], "component_reader_order": [order_a, order_b], "answer_projection_hashes": [a["answer_sha256"], b["answer_sha256"]], "claim_limit": "two existing audited tasks jointly answered in one identical source context; no serial dependency or new evidence-distance certificate"})
        except (ValueError, OverflowError) as error:
            rejected[str(error)] += 1
            decisions.append({"source_kind": a["source_kind"], "source_group": a["source_group"], "status": "rejected_at_reader_gate", "reason": str(error), "task_ids": [a["sample_id"], b["sample_id"]]})
    files = {
        "candidate_train.jsonl": "".join(_line(row) for row in rows["train"]).encode(),
        "candidate_eval.jsonl": "".join(_line(row) for row in rows["eval"]).encode(),
        "sample_index.jsonl": "".join(_line(row) for row in sample_index).encode(),
        "pair_lineage.jsonl": "".join(_line(row) for row in proofs).encode(),
        "decision_ledger.jsonl": "".join(_line(row) for row in decisions).encode(),
    }
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    for decision in decisions:
        reason = decision.get("reason", decision["status"])
        by_kind[decision["source_kind"]][reason] += 1
    pair_counts = Counter(
        tuple(operation_family(operation) for operation in proof["component_operations"])
        for proof in proofs
    )
    result = {"schema_version": "longworld.unified-candidates.v1", "p125_schema": SCHEMA, "code_sha256": _sha(Path(__file__)), "config_sha256": _sha(config_path), "source_materialized_manifest_sha256": _sha(materialized_manifest), "source_selected_views": source_manifest["selected_views"], "source_worlds": matrix["worlds"], "source_same_context_cells": matrix["same_context_cells"], "source_world_decisions": matrix["worlds_by_decision"], "source_kind_support_matrix": {kind: dict(sorted(counts.items())) for kind, counts in sorted(by_kind.items())}, "planned_pair_worlds": len(pairs), "reader_gate_rejections": dict(sorted(rejected.items())), "candidate_views": candidate_ledger.rows, "source_scoped_semantic_tasks": candidate_ledger.independent_tasks, "independent_semantic_tasks": candidate_ledger.independent_semantic_tasks, "reused_component_tasks": len({task for proof in proofs for task in proof["component_sample_ids"]}), "views_by_lane": {"p125_joint_multi_operation": candidate_ledger.rows}, "splits": {split: len(rows[split]) for split in ("train", "eval")}, "by_source_kind": dict(sorted(Counter(row["source_kind"] for row in sample_index).items())), "operation_family_pair_counts": {" + ".join(pair): count for pair, count in sorted(pair_counts.items())}, "length_bins": dict(sorted(Counter(row["length_bin"] for row in sample_index).items())), "files_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}, "claim_limit": "joint answer to two existing audited operations on byte-identical reader context; no serial dependence, shortest-proof distance, or model gain established", "train_ready": False}
    files["manifest.json"] = (_line(result)).encode()
    if verify_only:
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("P125 output inventory differs")
        for name, content in files.items():
            if (output / name).read_bytes() != content:
                raise ValueError(f"P125 byte replay differs: {name}")
        verify_merge(output)
    else:
        output.mkdir(parents=True)
        for name, content in files.items():
            (output / name).write_bytes(content)
        verify_merge(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = compile(args.config, args.output, verify_only=args.verify_only)
    print(json.dumps({key: result[key] for key in ("planned_pair_worlds", "candidate_views", "by_source_kind", "reader_gate_rejections")}, sort_keys=True))


if __name__ == "__main__":
    main()
