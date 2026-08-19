#!/usr/bin/env python3
"""Equal-token CausalTwin SFT (Qwen2.5-7B-Instruct LoRA).

Fairness: same backbone, max_steps, batch, max_length, packing.
Conditions differ only by which CausalTwin views (and query timing) are eligible.

  B1 full-only, query-first
  B2 full+min, query-first
  B3 full+cf, query-first
  B4 four-view, query-first
  B5 four-view, first+late
  B6 B5 + 15% short (minimal) replay

GPU:
  bash /workspace/wynckeliao/ops/gpu/hold.sh wrap 0 -- \\
    uv run python scripts/train_sft.py --condition B4
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COND_VIEWS = {
    "B1": {"full"},
    "B2": {"full", "minimal"},
    "B3": {"full", "cf"},
    "B4": {"full", "minimal", "cf", "distractor_only"},
    "B5": {"full", "minimal", "cf", "distractor_only"},
    "B6": {"full", "minimal", "cf", "distractor_only"},
}


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def filter_rows(rows: list[dict], condition: str, length_bucket: str) -> list[dict]:
    views = COND_VIEWS[condition]
    out = [
        r
        for r in rows
        if r["view"] in views and r.get("length_bucket", "8k") == length_bucket
    ]
    if condition in {"B1", "B2", "B3", "B4"}:
        first = [r for r in out if r["query_timing"] == "first"]
        if first:
            out = first
    if condition == "B6":
        shorts = [r for r in out if r["view"] == "minimal"]
        n_short = max(1, int(0.15 * len(out)))
        rng = random.Random(0)
        extra = rng.sample(shorts, min(n_short, len(shorts)))
        out = out + extra
    return out


def to_messages(row: dict) -> dict:
    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a careful analyst of internal company documents. "
                    "Use only the provided context. If the context is insufficient, "
                    "reply exactly: unanswerable"
                ),
            },
            {"role": "user", "content": row["context"]},
            {"role": "assistant", "content": str(row["answer"])},
        ],
        "world_id": row["world_id"],
        "query_id": row["query_id"],
        "view": row["view"],
        "query_type": row["query_type"],
        "query_timing": row["query_timing"],
    }


def write_condition_jsonl(
    rows: list[dict], dest: Path, token_budget: int | None
) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    used = 0
    n = 0
    by_view: dict[str, int] = {}
    with dest.open("w") as f:
        for r in rows:
            t = int(r["difficulty"]["context_tokens"]) + max(
                1, len(str(r["answer"])) // 4
            )
            if token_budget is not None and used + t > token_budget and n > 0:
                break
            f.write(json.dumps(to_messages(r)) + "\n")
            used += t
            n += 1
            by_view[r["view"]] = by_view.get(r["view"], 0) + 1
    return {"n": n, "tokens_est": used, "path": str(dest), "by_view": by_view}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True, choices=list(COND_VIEWS))
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "p0")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "sft")
    ap.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument("--max-seq-len", type=int, default=8192)
    ap.add_argument("--max-steps", type=int, default=80)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--token-budget", type=int, default=None)
    ap.add_argument("--length-bucket", default="8k")
    ap.add_argument("--prepare-only", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    train_path = args.data / "train.jsonl"
    rows = list(iter_jsonl(train_path))
    filtered = filter_rows(rows, args.condition, args.length_bucket)
    rng = random.Random(7)
    rng.shuffle(filtered)

    prepared = args.out_dir / f"{args.condition}.jsonl"
    stats = write_condition_jsonl(filtered, prepared, args.token_budget)
    meta = {
        "condition": args.condition,
        "model": "sshleifer/tiny-gpt2" if args.smoke else args.model,
        "max_steps": 2 if args.smoke else args.max_steps,
        "max_seq_len": 256 if args.smoke else args.max_seq_len,
        **stats,
    }
    (args.out_dir / f"{args.condition}.meta.json").write_text(
        json.dumps(meta, indent=2)
    )
    print(json.dumps(meta, indent=2))
    if args.prepare_only:
        return

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

    import subprocess
    import time

    if not args.smoke:
        phys = int(os.environ["CUDA_VISIBLE_DEVICES"].split(",")[0])
        for _ in range(90):
            used = int(
                subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits",
                        "-i",
                        str(phys),
                    ],
                    text=True,
                )
                .strip()
                .split()[0]
            )
            print(f"nvidia-smi gpu {phys} used={used}MiB", flush=True)
            if used < 2500:
                break
            time.sleep(2)
        else:
            raise SystemExit(f"GPU {phys} did not vacate before torch init")

    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    model_name = "sshleifer/tiny-gpt2" if args.smoke else args.model
    tok = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    ds = load_dataset("json", data_files=str(prepared), split="train")
    max_len = 256 if args.smoke else args.max_seq_len

    def to_text(ex):
        msgs = ex["messages"]
        if tok.chat_template:
            text = tok.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=False
            )
        else:
            text = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
        enc = tok(text, truncation=True, max_length=max_len, padding=False)
        enc["labels"] = list(enc["input_ids"])
        return enc

    ds = ds.map(to_text, remove_columns=ds.column_names, num_proc=1)

    if torch.cuda.is_available() and not args.smoke:
        torch.cuda.empty_cache()
        free, total = torch.cuda.mem_get_info(0)
        print(
            f"cuda free={free / 1024**3:.1f}GiB / {total / 1024**3:.1f}GiB", flush=True
        )
        if free < 40 * 1024**3:
            raise SystemExit(
                f"visible cuda device only has {free / 1024**3:.1f} GiB free"
            )

    dtype = torch.float32 if args.smoke else torch.bfloat16
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        dtype=dtype,
        trust_remote_code=True,
        attn_implementation="eager",
        device_map="cuda:0" if torch.cuda.is_available() and not args.smoke else None,
    )
    if not args.smoke:
        peft_cfg = LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        )
        model = get_peft_model(model, peft_cfg)
        model.enable_input_require_grads()
        model.config.use_cache = False
        model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": True}
        )
        model.print_trainable_parameters()

    out_ckpt = args.out_dir / f"ckpt_{args.condition}"
    targs = TrainingArguments(
        output_dir=str(out_ckpt),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=1 if args.smoke else 4,
        max_steps=2 if args.smoke else args.max_steps,
        learning_rate=args.lr,
        logging_steps=1,
        save_steps=10_000,
        bf16=not args.smoke,
        fp16=False,
        report_to=[],
        remove_unused_columns=False,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": True},
        warmup_steps=2,
        lr_scheduler_type="cosine",
        dataloader_num_workers=0,
        ddp_find_unused_parameters=False,
    )
    collator = DataCollatorForSeq2Seq(tok, padding=True, pad_to_multiple_of=8)
    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=ds,
        data_collator=collator,
        processing_class=tok,
    )
    trainer.train()
    dest = out_ckpt / "final"
    trainer.save_model(str(dest))
    tok.save_pretrained(str(dest))
    print(f"saved {dest}")


if __name__ == "__main__":
    main()
