#!/usr/bin/env python3
"""Export a frozen train.jsonl under P73 answer contracts.

Rebuilds each row's assistant message (and, for answer-only, the user
instruction) per capability_contracts, re-measures tokens with the bank's own
tokenizer recipe, and writes per-contract train/index/receipt outputs. The
source file is never modified.

Usage:
  python scripts/export_contract_arms.py \
    --train data/capability_records/p72_training_launch/arm_a_p71_main_train.jsonl \
    --out data/capability_records/p73_contract_arms
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis.capability_contracts import (  # noqa: E402
    CONTRACTS,
    minimal_evidence,
    project_answer,
    render_answer,
    user_message_for_contract,
)

MODEL = "Qwen/Qwen3.5-4B"
REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
ID_PATTERN = None


def _init_tokenizer():
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        MODEL, revision=REVISION, local_files_only=True, trust_remote_code=False
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _measure(tokenizer, messages) -> dict:
    from scripts.train_sft import _render_chat, tokenize_assistant_only

    prompt = [m for m in messages if m["role"] != "assistant"]
    full_tokens = len(
        tokenizer(
            _render_chat(tokenizer, messages, generation_prompt=False), truncation=False
        )["input_ids"]
    )
    input_tokens = len(
        tokenizer(
            _render_chat(tokenizer, prompt, generation_prompt=True), truncation=False
        )["input_ids"]
    )
    encoded = tokenize_assistant_only(tokenizer, messages, 1 << 30)
    return {
        "full_chat_tokens": full_tokens,
        "input_tokens": input_tokens,
        "supervised_tokens": sum(label != -100 for label in encoded["labels"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--contracts",
        default=",".join(CONTRACTS),
        help="comma-separated subset of contracts to export",
    )
    args = parser.parse_args()
    wanted = [c for c in args.contracts.split(",") if c]
    for contract in wanted:
        if contract not in CONTRACTS:
            raise SystemExit(f"unknown contract: {contract}")

    rows = [json.loads(line) for line in args.train.open()]
    tokenizer = _init_tokenizer()

    receipt = {
        "source_train": str(args.train),
        "source_sha256": _sha256(args.train),
        "contracts": {},
    }
    for contract in wanted:
        out_dir = args.out / contract
        out_dir.mkdir(parents=True, exist_ok=True)
        index_rows = []
        fam_counts: dict[str, int] = {}
        sup_total = 0
        full_total = 0
        with (out_dir / "train.jsonl").open("w") as fh:
            for row in rows:
                new_row = dict(row)
                if contract == "full-provenance":
                    messages = row["messages"]
                else:
                    answer = json.loads(row["messages"][-1]["content"])
                    projected = (
                        project_answer(row["family"], answer, contract)
                        if contract == "answer-only"
                        else minimal_evidence(row["family"], answer)
                    )
                    user = user_message_for_contract(
                        row["family"], row["messages"][0]["content"], contract
                    )
                    messages = [
                        {"role": "user", "content": user},
                        {"role": "assistant", "content": render_answer(projected)},
                    ]
                    new_row["messages"] = messages
                tokens = _measure(tokenizer, messages)
                new_row.update(tokens)
                new_row["contract"] = contract
                fh.write(json.dumps(new_row, ensure_ascii=False) + "\n")
                fam_counts[row["family"]] = fam_counts.get(row["family"], 0) + 1
                sup_total += tokens["supervised_tokens"]
                full_total += tokens["full_chat_tokens"]
                index_rows.append(
                    {
                        k: row.get(k)
                        for k in (
                            "example_id",
                            "semantic_task_id",
                            "world_id",
                            "group_id",
                            "family",
                            "split",
                            "language",
                            "renderer",
                            "length_records",
                            "depth",
                            "consumed_records",
                            "token_target",
                            "row_index",
                            "admission_status",
                        )
                    }
                    | tokens
                    | {"contract": contract}
                )
        with (out_dir / "sample_index.jsonl").open("w") as fh:
            for r in index_rows:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        receipt["contracts"][contract] = {
            "rows": len(rows),
            "family_counts": dict(sorted(fam_counts.items())),
            "full_chat_tokens": full_total,
            "supervised_tokens": sup_total,
            "out_dir": str(out_dir),
        }
        print(
            f"{contract:24s} rows={len(rows):5d} full_chat_tokens={full_total:,} "
            f"supervised_tokens={sup_total:,}"
        )
    (args.out / "arms.json").write_text(json.dumps(receipt, indent=1) + "\n")
    print(f"wrote {args.out / 'arms.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
