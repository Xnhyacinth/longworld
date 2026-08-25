#!/usr/bin/env python3
"""Unsigned single-GPU diagnostic SFT for a validated LongWorld release.

Fairness: same backbone, max_steps, batch, max_length, packing.
Conditions differ only by which CausalTwin views (and query timing) are eligible.

  B1 full-only, query-first
  B2 full+min, query-first
  B3 full+cf, query-first
  B4 four-view, query-first
  B5 four-view, first+late
  B6 B5 + 15% short (minimal) replay

This path is deliberately not a release exporter and cannot create a signed
training manifest. The release path is export_llamafactory.py followed by the
manifest-validating LLaMA-Factory or Swift launcher.
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

from longworld.core.attestation import (
    ATTESTATION_ENV,
    ATTESTATION_ENVIRONMENT_ENV,
    PREDECESSOR_GATE_KEY_ENV,
    PREDECESSOR_GATE_KEY_ID_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)
from longworld.core.record_contract import sft_row_errors
from scripts.quality_gate import load_release_product

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
    long_buckets = {"32k", "64k", "128k", "256k"}
    out = []
    for r in rows:
        if sft_row_errors(r):
            continue
        if r["view"] not in views:
            continue
        if r.get("length_bucket", "8k") != length_bucket:
            continue
        if (
            length_bucket in long_buckets
            and r.get("dependency_class") == "local_or_mixed"
        ):
            continue
        out.append(r)
    if condition in {"B1", "B2", "B3", "B4"}:
        out = [r for r in out if r["query_timing"] == "first"]
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


def _render_chat(tokenizer, messages: list[dict], *, generation_prompt: bool) -> str:
    if tokenizer.chat_template:
        try:
            return tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=generation_prompt,
                enable_thinking=False,
            )
        except TypeError:
            return tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=generation_prompt,
            )
    text = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    if generation_prompt:
        text += "\nassistant:"
    return text


def tokenize_assistant_only(tokenizer, messages: list[dict], max_length: int) -> dict:
    """Supervise only a complete answer; reject any source/query truncation."""
    if not messages or messages[-1].get("role") != "assistant":
        raise ValueError("messages must end with an assistant answer")
    prompt = _render_chat(tokenizer, messages[:-1], generation_prompt=True)
    full = _render_chat(tokenizer, messages, generation_prompt=False)
    encoded = tokenizer(full, truncation=False, padding=False)
    prompt_encoded = tokenizer(prompt, truncation=False, padding=False)
    full_ids = list(encoded["input_ids"])
    prompt_ids = list(prompt_encoded["input_ids"])
    prefix_len = 0
    for prompt_id, full_id in zip(prompt_ids, full_ids):
        if prompt_id != full_id:
            break
        prefix_len += 1
    if prefix_len == 0 or prefix_len >= len(full_ids):
        raise ValueError("chat template did not expose an assistant answer boundary")
    answer_ids = full_ids[prefix_len:]
    if len(answer_ids) >= max_length:
        raise ValueError("assistant answer exceeds the training sequence length")
    source_budget = max_length - len(answer_ids)
    source_ids = full_ids[:prefix_len]
    if len(source_ids) > source_budget:
        raise ValueError("source and query exceed the training sequence length")
    input_ids = source_ids + answer_ids
    return {
        "input_ids": input_ids,
        "attention_mask": [1] * len(input_ids),
        "labels": [-100] * len(source_ids) + answer_ids,
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
    ap.add_argument("--model", default="Qwen/Qwen3.5-4B")
    ap.add_argument("--max-seq-len", type=int, default=8192)
    ap.add_argument("--max-steps", type=int, default=80)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--token-budget", type=int, default=None)
    ap.add_argument("--length-bucket", default="8k")
    ap.add_argument("--prepare-only", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--min-free-gb", type=float, default=24.0)
    ap.add_argument("--release-profile", required=True)
    ap.add_argument("--diagnostic-only", action="store_true")
    args = ap.parse_args()

    if not args.diagnostic_only:
        raise SystemExit(
            "--diagnostic-only is required; signed release training must use "
            "export_llamafactory.py and a manifest-validating launcher"
        )

    try:
        product = load_release_product(args.data, args.release_profile)
    except (TypeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    rows = list(product.train_rows)
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
        "data_stage": "diagnostic",
        "signed_release_artifact": False,
        "release_profile_id": args.release_profile,
        **stats,
    }
    meta_path = args.out_dir / f"{args.condition}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    prepared_bytes = prepared.read_bytes()
    prepared_rows = [
        json.loads(line) for line in prepared_bytes.splitlines() if line.strip()
    ]
    print(json.dumps(meta, indent=2))
    if args.prepare_only:
        return

    # Verification is complete; never expose the producer signing secret to model code.
    for name in (
        ATTESTATION_ENV,
        ATTESTATION_ENVIRONMENT_ENV,
        *ROLE_KEY_ENVS.values(),
        *ROLE_KEY_ID_ENVS.values(),
        PREDECESSOR_GATE_KEY_ENV,
        PREDECESSOR_GATE_KEY_ID_ENV,
    ):
        os.environ.pop(name, None)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if args.smoke:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    else:
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

    import subprocess
    import time

    vis = os.environ.get("CUDA_VISIBLE_DEVICES", "0")
    if not args.smoke and vis not in {"", "-1"}:
        phys = vis.split(",")[0].strip()
        if phys.isdigit():
            for _ in range(90):
                try:
                    used = int(
                        subprocess.check_output(
                            [
                                "nvidia-smi",
                                "--query-gpu=memory.used",
                                "--format=csv,noheader,nounits",
                                "-i",
                                phys,
                            ],
                            text=True,
                        )
                        .strip()
                        .split()[0]
                    )
                except (FileNotFoundError, subprocess.CalledProcessError):
                    break
                print(f"nvidia-smi gpu {phys} used={used}MiB", flush=True)
                if used < 2500:
                    break
                time.sleep(2)
            else:
                raise SystemExit(f"GPU {phys} did not vacate before torch init")

    import torch
    from datasets import Dataset
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    model_name = "sshleifer/tiny-gpt2" if args.smoke else args.model
    tok = AutoTokenizer.from_pretrained(model_name, trust_remote_code=False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    ds = Dataset.from_list(prepared_rows)
    max_len = 256 if args.smoke else args.max_seq_len

    def to_text(ex):
        return tokenize_assistant_only(tok, ex["messages"], max_len)

    ds = ds.map(to_text, remove_columns=ds.column_names, num_proc=1)

    if torch.cuda.is_available() and not args.smoke:
        torch.cuda.empty_cache()
        free, total = torch.cuda.mem_get_info(0)
        print(
            f"cuda free={free / 1024**3:.1f}GiB / {total / 1024**3:.1f}GiB", flush=True
        )
        if free < args.min_free_gb * 1024**3:
            raise SystemExit(
                f"visible cuda device only has {free / 1024**3:.1f} GiB free "
                f"(need {args.min_free_gb:g}; pass --min-free-gb)"
            )

    dtype = torch.float32 if args.smoke else torch.bfloat16
    load_kw = {
        "dtype": dtype,
        "trust_remote_code": False,
        "attn_implementation": "eager",
        "device_map": (
            "cuda:0" if torch.cuda.is_available() and not args.smoke else None
        ),
    }
    try:
        model = AutoModelForCausalLM.from_pretrained(model_name, **load_kw)
    except ValueError:
        from transformers import AutoModelForImageTextToText

        model = AutoModelForImageTextToText.from_pretrained(model_name, **load_kw)
    if not args.smoke:
        model.config.use_cache = False
        model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": True}
        )
        n_params = sum(p.numel() for p in model.parameters())
        n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
        print(f"full FT trainable {n_train}/{n_params}", flush=True)

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
