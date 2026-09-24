"""Audit final P84 ResearchLab reader rows, hashes, split and SFT masks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p84-researchlab-reader-audit.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def audit(route_dir: Path, native_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise ValueError("audit output directory must be new")
    route = json.loads((route_dir / "routing_receipt.json").read_text())
    native_config_path = route_dir / "native_config.json"
    if route.get("native_config_sha256") != _sha(native_config_path):
        raise ValueError("routed native config changed")
    native_config = json.loads(native_config_path.read_text())
    receipt = json.loads((native_dir / "BUILD_RECEIPT.json").read_text())
    if (
        receipt.get("schema_version") != "longworld.p66-researchlab-taskbank-receipt.v1"
        or receipt.get("config_sha256") != _sha(native_config_path)
        or receipt.get("family_splits")
        != {
            family["family_id"]: family["split"] for family in native_config["families"]
        }
    ):
        raise ValueError("native receipt and route differ")
    for relative, digest in receipt["files"].items():
        path = Path(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or _sha(native_dir / path) != digest
        ):
            raise ValueError("native output hash mismatch")
    candidates = _rows(native_dir / "candidates.jsonl")
    train = _rows(native_dir / "train.jsonl")
    eval_rows = _rows(native_dir / "eval.jsonl")
    if (
        len(candidates) != receipt["semantic_tasks"]
        or len(train) != receipt["train_rows"]
        or len(eval_rows) != receipt["eval_rows"]
    ):
        raise ValueError("native reader row count drift")
    by_id = {row["semantic_task_id"]: row for row in candidates}
    if len(by_id) != len(candidates) or {
        row["sample_id"] for row in train + eval_rows
    } != set(by_id):
        raise ValueError("reader and semantic task IDs differ")
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            native_config["tokenizer"]["model_id"],
            revision=native_config["tokenizer"]["revision"],
            local_files_only=True,
            trust_remote_code=False,
        )
    audits = []
    for expected_split, row in [("train", row) for row in train] + [
        ("eval", row) for row in eval_rows
    ]:
        task = by_id[row["sample_id"]]
        context_path = native_dir / task["context_path"]
        context = context_path.read_text()
        messages = row["messages"]
        if (
            task["split"] != expected_split
            or task["source_group_id"] != "researchlab:" + task["family_id"]
            or _sha(context_path) != task["context_sha256"]
            or [message["role"] for message in messages] != ["user", "assistant"]
            or messages[0]["content"] != context + "\n\nQuestion:\n" + task["question"]
            or messages[1]["content"]
            != json.dumps(
                task["answer"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ):
            raise ValueError("final reader content or split differs from task")
        encoded = tokenize_assistant_only(tokenizer, messages, 131072)
        supervised = sum(label != -100 for label in encoded["labels"])
        prefix = len(encoded["labels"]) - supervised
        if (
            supervised != task["assistant_tokens"]
            or len(encoded["input_ids"]) != task["full_hf_chat_tokens"]
            or supervised < 1
            or any(label != -100 for label in encoded["labels"][:prefix])
            or any(label == -100 for label in encoded["labels"][prefix:])
        ):
            raise ValueError("assistant-only mask differs from native receipt")
        audits.append(
            {
                "semantic_task_id": task["semantic_task_id"],
                "split": expected_split,
                "full_hf_chat_tokens": len(encoded["input_ids"]),
                "masked_prefix_tokens": prefix,
                "supervised_tokens": supervised,
                "context_sha256": task["context_sha256"],
                "reader_row_sha256": hashlib.sha256(
                    json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest(),
            }
        )
    output_dir.mkdir(parents=True)
    audits.sort(key=lambda item: item["semantic_task_id"])
    audit_path = output_dir / "sample_audit.jsonl"
    audit_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in audits
        )
    )
    report = {
        "schema": SCHEMA,
        "routing_receipt_sha256": _sha(route_dir / "routing_receipt.json"),
        "native_receipt_sha256": _sha(native_dir / "BUILD_RECEIPT.json"),
        "sample_audit_sha256": _sha(audit_path),
        "semantic_tasks": len(audits),
        "train_rows": len(train),
        "eval_rows": len(eval_rows),
        "unique_contexts": len({row["context_sha256"] for row in audits}),
        "full_chat_tokens": {
            "min": min(row["full_hf_chat_tokens"] for row in audits),
            "max": max(row["full_hf_chat_tokens"] for row in audits),
        },
        "supervised_tokens": sum(row["supervised_tokens"] for row in audits),
        "masked_prefix_tokens": sum(row["masked_prefix_tokens"] for row in audits),
        "assistant_only_mask_verified": True,
        "local_training_candidate": receipt["local_training_candidate"],
        "strict_long_dependency_verified": receipt["strict_long_dependency_verified"],
        "train_ready": False,
    }
    (output_dir / "audit_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-dir", required=True, type=Path)
    parser.add_argument("--native-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            audit(args.route_dir, args.native_dir, args.output_dir),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
