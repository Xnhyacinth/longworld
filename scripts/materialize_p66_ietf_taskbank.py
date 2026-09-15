#!/usr/bin/env python3
"""Materialize and replay a fail-closed P66 IETF succession taskbank."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.p57pipeline import question_only_codebook_prediction
from longworld.core.p66_ietf_taskbank import (
    REVISION,
    admission_reason,
    canonical,
    exact_range,
    minimum_positive_evidence_cover,
    sha256_text,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

SCHEMA = "longworld.p66-ietf-taskbank-build.v1"
RECEIPT = "longworld.p66-ietf-taskbank-receipt.v1"
CODE = (
    "longworld/core/p66_ietf_taskbank.py",
    "scripts/materialize_p66_ietf_taskbank.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def evaluate(payload: tuple[dict, dict]) -> dict:
    row, tokenizer_config = payload
    answer = (
        json.loads(row["answer"]) if isinstance(row["answer"], str) else row["answer"]
    )
    factual = row["ietf_requirement_task"]["answer"]
    prediction = question_only_codebook_prediction(row["question"])
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_config["model_id"],
            revision=tokenizer_config["revision"],
            local_files_only=True,
            trust_remote_code=False,
        )
        cover = minimum_positive_evidence_cover(
            row["context"], row["ietf_requirement_task"], answer
        )
        if cover["char_start"] is None:
            cover_tokens = 0
        else:
            cover_tokens = len(
                tokenizer(
                    row["context"][cover["char_start"] : cover["char_end"]],
                    add_special_tokens=False,
                )["input_ids"]
            )
        context_tokens = len(
            tokenizer(row["context"], add_special_tokens=False)["input_ids"]
        )
        messages = [
            {
                "role": "user",
                "content": row["context"] + "\n\nQuestion:\n" + row["question"],
            },
            {"role": "assistant", "content": canonical(answer)},
        ]
        from scripts.train_sft import tokenize_assistant_only

        encoded = tokenize_assistant_only(tokenizer, messages, 1 << 30)
    reason = admission_reason(
        view=row["view"],
        context_tokens=context_tokens,
        question_only_em=prediction == answer,
        positive_cover_tokens=cover_tokens,
    )
    return {
        "row": row,
        "answer": answer,
        "factual_answer": factual,
        "messages": messages,
        "context_tokens": context_tokens,
        "full_hf_chat_tokens": len(encoded["input_ids"]),
        "assistant_tokens": sum(label != -100 for label in encoded["labels"]),
        "question_only_answer_em": prediction == answer,
        "question_only_prediction": prediction,
        "positive_evidence_cover": {**cover, "tokens": cover_tokens},
        "admission_reason": reason,
    }


def checked_config(path: Path) -> dict:
    config = json.loads(path.read_text())
    if config.get("schema_version") != SCHEMA or not config.get("sources"):
        raise ValueError("unsupported P66 IETF config")
    for source in config["sources"]:
        p = ROOT / source["path"]
        if not p.is_file() or p.is_symlink() or digest(p) != source["sha256"]:
            raise ValueError("source projection binding mismatch")
    return config


def build(config_path: Path, output: Path, workers: int) -> dict:
    config = checked_config(config_path)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "contexts").mkdir()
    inherited = []
    bindings = []
    for source in config["sources"]:
        path = ROOT / source["path"]
        bindings.append({"path": source["path"], "sha256": source["sha256"]})
        rows = load_jsonl(path)
        if {row["view"] for row in rows} != {"full", "cf", "ordered_artifact_view"}:
            raise ValueError("incomplete inherited view set")
        inherited.extend(rows)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        evaluated = list(
            pool.map(evaluate, ((row, config["tokenizer"]) for row in inherited))
        )
    evaluated.sort(key=lambda item: item["row"]["query_id"] + ":" + item["row"]["view"])
    candidates, rejects, train, eval_rows = [], [], [], []
    family_split = config["family_split"]
    for item in evaluated:
        row = item.pop("row")
        accepted = item["admission_reason"] == "accepted_local_long_candidate"
        sample_id = sha256_text(
            canonical(
                [
                    REVISION,
                    row["query_id"],
                    row["view"],
                    item["answer"],
                    sha256_text(row["context"]),
                ]
            )
        )
        split = family_split[row["world_id"]]
        metadata = {
            "schema_version": "longworld.p66-ietf-taskbank-sample.v1",
            "sample_id": sample_id,
            "semantic_task_id": row["semantic_base_task_id"],
            "variant_family_id": row["base_task_id"],
            "source_group_id": "ietf:" + row["world_id"],
            "world_instance_id": row["world_id"],
            "split": split,
            "source_projection_query_id": row["query_id"],
            "view": row["view"],
            "answer_program_id": row["answer_program_id"],
            "context_tokens": item["context_tokens"],
            "full_hf_chat_tokens": item["full_hf_chat_tokens"],
            "assistant_tokens": item["assistant_tokens"],
            "capacity_bin": next(
                (x for x in (65536, 131072, 262144) if item["context_tokens"] <= x),
                None,
            ),
            "exact_numeric_range": exact_range(item["context_tokens"]),
            "question_only_answer_em": item["question_only_answer_em"],
            "positive_evidence_cover": item["positive_evidence_cover"],
            "latest_document_only_answer_em": "unmeasured",
            "compact_complete_record_controls": "unmeasured",
            "admission_reason": item["admission_reason"],
            "local_training_candidate": accepted,
            "strict_long_dependency_verified": False,
            "training_release_eligible": False,
            "production_eligible": False,
        }
        if accepted:
            context_sha = sha256_text(row["context"])
            context_path = output / "contexts" / f"{context_sha}.txt"
            if not context_path.exists():
                context_path.write_text(row["context"])
            metadata["context_path"] = f"contexts/{context_sha}.txt"
            metadata["context_sha256"] = context_sha
            candidates.append(metadata)
            sample = {"sample_id": sample_id, "messages": item["messages"]}
            (train if split == "train" else eval_rows).append(sample)
        else:
            rejects.append(metadata)

    def write_jsonl(name: str, rows: list[dict]) -> None:
        (output / name).write_text("".join(canonical(row) + "\n" for row in rows))

    write_jsonl("candidates.jsonl", candidates)
    write_jsonl("rejects.jsonl", rejects)
    write_jsonl("train.jsonl", train)
    write_jsonl("eval.jsonl", eval_rows)
    files = {
        p.relative_to(output).as_posix(): digest(p)
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    receipt = {
        "schema_version": RECEIPT,
        "revision": REVISION,
        "source_domain": "ietf",
        "source_families": len(config["sources"]),
        "source_worlds": len(
            {row["world_instance_id"] for row in candidates + rejects}
        ),
        "inherited_rows": len(evaluated),
        "accepted_local_long_candidates": len(candidates),
        "train_rows": len(train),
        "eval_rows": len(eval_rows),
        "capacity_bins": dict(Counter(str(row["capacity_bin"]) for row in candidates)),
        "exact_numeric_ranges": dict(
            Counter(row["exact_numeric_range"] or "none" for row in candidates)
        ),
        "rejection_reasons": dict(Counter(row["admission_reason"] for row in rejects)),
        "controls": {
            "question_only": "measured",
            "positive_evidence_4k_8k_16k": "16k upper-bound measured; 4k/8k implied for rejects",
            "latest_document_only": "unmeasured",
            "compact_complete_record": "unmeasured",
            "neural": "unmeasured",
        },
        "strict_long_dependency_verified": False,
        "training_release_eligible": False,
        "production_eligible": False,
        "input_bindings": bindings,
        "config_sha256": digest(config_path),
        "code_sha256": {name: digest(ROOT / name) for name in CODE},
        "tokenizer_asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
            config["tokenizer"]["model_id"], config["tokenizer"]["revision"]
        ),
        "files": files,
    }
    (output / "BUILD_RECEIPT.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )
    return receipt


def validate(config_path: Path, output: Path, workers: int) -> dict:
    receipt = json.loads((output / "BUILD_RECEIPT.json").read_text())
    if receipt.get("schema_version") != RECEIPT:
        raise ValueError("invalid P66 receipt schema")
    with tempfile.TemporaryDirectory(prefix="p66-ietf-replay-") as temp:
        replay = Path(temp) / "out"
        rebuilt = build(config_path, replay, workers)
        if (
            rebuilt["files"] != receipt["files"]
            or rebuilt["config_sha256"] != receipt["config_sha256"]
        ):
            raise ValueError("native replay differs")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    result = (
        validate(args.config, args.output, args.workers)
        if args.validate
        else build(args.config, args.output, args.workers)
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
