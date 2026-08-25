#!/usr/bin/env python3
"""Convert downloaded related-work datasets to LLaMA-Factory ShareGPT json.

Does not train. Reads data/external/* and writes data/external/llamafactory/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYSTEM = (
    "You are a careful analyst of long documents. Use only the provided context. "
    "If the context is insufficient, reply exactly: unanswerable"
)


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def iter_parquet(path: Path):
    from datasets import load_dataset

    files = [path] if path.is_file() else sorted(path.glob("**/*.parquet"))
    if not files:
        return
    ds = load_dataset("parquet", data_files=[str(p) for p in files], split="train")
    for row in ds:
        yield dict(row)


_MM_PLACEHOLDER_RE = re.compile(r"<(video|image|audio)>", re.IGNORECASE)


def _escape_mm_placeholders(text: str) -> str:
    """Keep literal HTML-ish tags from being treated as LLaMA-Factory media tokens."""
    return _MM_PLACEHOLDER_RE.sub(lambda m: f"< {m.group(1)}>", text)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _escape_mm_placeholders(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(x, str) for x in value):
            return _escape_mm_placeholders(value[0])
        return "\n".join(_text(x) for x in value if _text(x))
    if isinstance(value, dict):
        for k in ("ground_truth", "answer", "content", "value", "text"):
            if k in value:
                return _text(value[k])
    return _escape_mm_placeholders(str(value))


def _role(msg: dict) -> str:
    raw = str(msg.get("role") or msg.get("from") or "user").lower()
    if raw in {"assistant", "gpt", "model"}:
        return "gpt"
    if raw in {"system"}:
        return "system"
    return "human"


def messages_to_sharegpt(messages: list, gold: str | None = None) -> dict | None:
    conv = []
    system = SYSTEM
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = _role(msg)
        text = _text(msg.get("content") or msg.get("value"))
        if not text:
            continue
        if role == "system":
            system = text
            continue
        conv.append({"from": role, "value": text})
    if gold:
        gold = gold.strip()
        if conv and conv[-1]["from"] == "gpt":
            conv[-1]["value"] = gold
        else:
            conv.append({"from": "gpt", "value": gold})
    if not conv or conv[0]["from"] != "human":
        return None
    if conv[-1]["from"] != "gpt":
        return None
    return {"conversations": conv, "system": system}


def longmit_to_sharegpt(row: dict) -> dict | None:
    """Official LongMIT instruction format (dataset README)."""
    docs = row.get("all_docs") or []
    question = _text(row.get("question"))
    answer = _text(row.get("answer"))
    if not question or not answer or not isinstance(docs, list) or not docs:
        return None
    lang = str(row.get("language") or "en")
    kind = str(row.get("type") or "")
    cot = kind in {"inter_doc", "intra_doc"}
    if lang == "zh":
        content_key = "文章 {pi}：\n"
        if cot:
            tmpl = (
                "根据给定的段落回答问题。\n\n以下是给定的段落。\n{concat_content}\n\n"
                "请结合上面材料回答以下问题，并且给出完整的推理过程。\n问题：{q}\n答案："
            )
        else:
            tmpl = (
                "根据给定的段落回答问题。只给答案，不要输出任何其他单词。\n\n"
                "以下是给定的段落。\n{concat_content}\n\n"
                "请结合上面材料回答以下问题。只给答案，不要输出任何其他单词。\n问题：{q}\n答案："
            )
    else:
        content_key = "Passage {pi}:\n"
        if cot:
            tmpl = (
                "Answer the question based on the given passages.\n\n"
                "The following are given passages.\n{concat_content}\n\n"
                "Answer the question based on the given passages and provide a complete "
                "reasoning process.\nQuestion:{q}\nAnswer:"
            )
        else:
            tmpl = (
                "Answer the question based on the given passages. Only give me the answer "
                "and do not output any other words.\n\nThe following are given passages.\n"
                "{concat_content}\n\nAnswer the question based on the given passages. "
                "Only give me the answer and do not output any other words.\nQuestion:{q}\nAnswer:"
            )
    parts = []
    for i, doc in enumerate(docs):
        text = _text(doc.get("content") if isinstance(doc, dict) else doc)
        if text:
            parts.append(content_key.format(pi=i + 1) + text)
    if not parts:
        return None
    human = tmpl.format(concat_content="\n".join(parts), q=question)
    return {
        "conversations": [
            {"from": "human", "value": human},
            {"from": "gpt", "value": answer},
        ],
        "system": SYSTEM,
    }


def record_to_sharegpt(row: dict) -> dict | None:
    if isinstance(row.get("all_docs"), list) and row.get("question") is not None:
        return longmit_to_sharegpt(row)
    if "conversations" in row and isinstance(row["conversations"], list):
        return messages_to_sharegpt(row["conversations"], _text(row.get("answer")))
    gold = _text(
        row.get("ground_truth_answer")
        or row.get("label")
        or (row.get("reward_model") or {}).get("ground_truth")
        or row.get("answer")
    )
    for key in ("dialogs", "input_messages", "prompt", "messages"):
        if isinstance(row.get(key), list) and row[key]:
            out = messages_to_sharegpt(row[key], gold or None)
            if out:
                return out
    question = _text(
        row.get("question") or (row.get("extra_info") or {}).get("input_question")
    )
    context = _text(row.get("context"))
    if question and gold:
        human = f"{context}\n\n{question}".strip() if context else question
        return {
            "conversations": [
                {"from": "human", "value": human},
                {"from": "gpt", "value": gold},
            ],
            "system": SYSTEM,
        }
    return None


_ROLE = {
    "human": "user",
    "user": "user",
    "gpt": "assistant",
    "assistant": "assistant",
    "system": "system",
}


def sharegpt_to_messages(rec: dict) -> dict | None:
    """LLaMA-Factory ShareGPT / OpenAI messages → ms-swift messages jsonl."""
    if not isinstance(rec, dict):
        return None
    messages: list[dict] = []
    system = rec.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    raw = rec.get("messages")
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "role" in raw[0]:
        for msg in raw:
            role = _ROLE.get(str(msg.get("role") or "user").lower(), "user")
            content = msg.get("content")
            if content is None:
                content = msg.get("value") or ""
            if role == "system" and messages and messages[0]["role"] == "system":
                messages[0]["content"] = str(content)
                continue
            messages.append({"role": role, "content": str(content)})
    else:
        conv = rec.get("conversations") or rec.get("conversation")
        if not isinstance(conv, list) or not conv:
            return None
        for turn in conv:
            if not isinstance(turn, dict):
                continue
            if "human" in turn or "assistant" in turn:
                if turn.get("human"):
                    messages.append({"role": "user", "content": str(turn["human"])})
                if turn.get("assistant"):
                    messages.append(
                        {"role": "assistant", "content": str(turn["assistant"])}
                    )
                continue
            role = _ROLE.get(
                str(turn.get("from") or turn.get("role") or "user").lower(), "user"
            )
            content = turn.get("value")
            if content is None:
                content = turn.get("content") or ""
            if role == "system":
                if messages and messages[0]["role"] == "system":
                    messages[0]["content"] = str(content)
                else:
                    messages.insert(0, {"role": "system", "content": str(content)})
                continue
            messages.append({"role": role, "content": str(content)})
    if len(messages) < 2:
        return None
    roles = [m["role"] for m in messages if m["role"] != "system"]
    if not roles or roles[0] != "user" or roles[-1] != "assistant":
        return None
    return {"messages": messages}


def write_sharegpt(rows, dest: Path, source: str) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    skipped = 0
    input_targets: defaultdict[str, set[str]] = defaultdict(set)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=dest.parent,
        prefix=f".{dest.name}.stage-",
        delete=False,
    ) as staged:
        staged_path = Path(staged.name)
        for row in rows:
            rec = record_to_sharegpt(row)
            if rec is None:
                skipped += 1
                continue
            rec["source"] = source
            rec["data_stage"] = "external_candidate"
            source_metadata = {
                "record_id": str(row.get("instance_id") or row.get("id") or ""),
                "repo": str(row.get("repo") or ""),
                "revision": str(row.get("base_commit") or ""),
                "declared_context_length": str(row.get("context_length") or ""),
            }
            rec["source_metadata"] = {
                key: value for key, value in source_metadata.items() if value
            }
            input_payload = {
                "system": rec.get("system"),
                "conversations": list(rec.get("conversations") or [])[:-1],
            }
            target_payload = (rec.get("conversations") or [{}])[-1]
            input_digest = hashlib.sha256(
                json.dumps(input_payload, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            target_digest = hashlib.sha256(
                json.dumps(target_payload, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            input_targets[input_digest].add(target_digest)
            rec["_candidate_input_sha256"] = input_digest
            staged.write(json.dumps(rec, ensure_ascii=False) + "\n")

    conflict_inputs = {
        input_digest
        for input_digest, targets in input_targets.items()
        if len(targets) > 1
    }
    n = 0
    duplicates = 0
    conflicting_rows = 0
    seen: set[str] = set()
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=dest.parent,
            prefix=f".{dest.name}.final-",
            delete=False,
        ) as output:
            output_path = Path(output.name)
            with staged_path.open() as staged:
                for line in staged:
                    rec = json.loads(line)
                    input_digest = str(rec.pop("_candidate_input_sha256"))
                    if input_digest in conflict_inputs:
                        conflicting_rows += 1
                        continue
                    digest = hashlib.sha256(
                        json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest()
                    if digest in seen:
                        duplicates += 1
                        continue
                    seen.add(digest)
                    output.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
        output_path.replace(dest)
    finally:
        staged_path.unlink(missing_ok=True)
    return {
        "n": n,
        "skipped": skipped,
        "duplicates": duplicates,
        "conflicting_rows": conflicting_rows,
        "conflict_groups": len(conflict_inputs),
        "path": str(dest),
        "source": source,
    }


def collect_sources(ext: Path) -> dict[str, list[Path]]:
    mapping: dict[str, list[Path]] = {}
    acc = ext / "ACC-dataset"
    if acc.exists():
        for fname, key in (
            ("search_agent_data.jsonl", "acc_search"),
            ("sql_agent_data.jsonl", "acc_sql"),
            ("swe_agent_data.jsonl", "acc_swe"),
        ):
            p = acc / fname
            if p.exists():
                mapping[key] = [p]
    loong = ext / "LoongRL-Train-Data"
    if loong.exists():
        parq = [p for p in loong.glob("**/*.parquet") if ".cache" not in p.parts]
        if parq:
            mapping["loongrl"] = parq
    lt = ext / "LongTraceRL"
    if (lt / "data.jsonl").exists():
        mapping["longtracerl"] = [lt / "data.jsonl"]
    else:
        parq = [p for p in lt.glob("**/*.parquet") if ".cache" not in p.parts]
        if parq:
            mapping["longtracerl"] = parq
    docqa = ext / "DocQA-RL-1.6K"
    if docqa.exists():
        parq = [p for p in docqa.glob("**/*.parquet") if ".cache" not in p.parts]
        train_p = [p for p in parq if "test" not in p.name.lower()] or parq
        if train_p:
            mapping["docqa_rl"] = train_p
    lmit = ext / "LongMIT-128K" / "train.jsonl"
    if lmit.exists():
        mapping["longmit"] = [lmit]
    return mapping


def reservoir_take(rows, k: int, seed: int):
    """Unbiased sample of k rows from a stream (LongMIT is 64k × ~128k)."""
    rng = random.Random(seed)
    buf: list = []
    for i, row in enumerate(rows):
        if i < k:
            buf.append(row)
        else:
            j = rng.randint(0, i)
            if j < k:
                buf[j] = row
    rng.shuffle(buf)
    yield from buf


def iter_files(paths: list[Path]):
    for path in paths:
        if path.suffix == ".jsonl":
            yield from iter_jsonl(path)
        elif path.suffix == ".parquet" or path.is_dir():
            yield from iter_parquet(path)
        elif path.suffix == ".json":
            data = json.loads(path.read_text())
            if isinstance(data, list):
                yield from data
            elif isinstance(data, dict):
                yield data


def write_dataset_info(out_dir: Path, names: list[str]) -> None:
    path = out_dir / "dataset_info.json"
    info = json.loads(path.read_text()) if path.exists() else {}
    for name in names:
        info[name] = {
            "file_name": f"{name}.jsonl",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
            },
        }
    path.write_text(json.dumps(info, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=ROOT / "data" / "external")
    ap.add_argument(
        "--out-dir", type=Path, default=ROOT / "data" / "external" / "llamafactory"
    )
    ap.add_argument("--max-per-source", type=int, default=0, help="0 = all")
    ap.add_argument("--only", nargs="*", default=None, help="subset of source keys")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    sources = collect_sources(args.src)
    if args.only:
        sources = {k: v for k, v in sources.items() if k in set(args.only)}
    if not sources:
        raise SystemExit(
            f"no datasets under {args.src}; run scripts/download_external.sh"
        )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    metas = []
    for name, paths in sources.items():
        rows = iter_files(paths)
        if args.max_per_source:
            rows = reservoir_take(rows, args.max_per_source, args.seed)
        dest = args.out_dir / f"{name}.jsonl"
        meta = write_sharegpt(rows, dest, name)
        metas.append(meta)
        (args.out_dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=2))
        print(json.dumps(meta, indent=2))
    write_dataset_info(args.out_dir, [m["source"] for m in metas])
    summary_path = args.out_dir / "export_summary.json"
    summary = (
        json.loads(summary_path.read_text())
        if summary_path.exists()
        else {"conditions": []}
    )
    by_source = {c["source"]: c for c in summary.get("conditions", [])}
    for meta in metas:
        by_source[meta["source"]] = meta
    summary["conditions"] = list(by_source.values())
    summary_path.write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
