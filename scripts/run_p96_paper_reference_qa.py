"""Compile bounded natural paper-reference QA from frozen arXiv source trees."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.p66_researchlab_taskbank import canonical, load_text_tar
from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.sharded_candidate_bank import verify_index
from scripts.audit_wiki_join_positions import token_span
from scripts.p96_paper_caption_qa import (
    discover,
    render_files,
    resolve,
    shortcut_reason,
)
from scripts.run_p86_frozen_paper_batch import _dedupe, _inventory, _pinned, _sha
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p96-paper-reference-qa.v1"
SEPARATOR = "\n\nQUESTION\n"


def _prior(pin: dict) -> tuple[set[str], set[tuple[str, str]]]:
    path = _pinned(pin)
    verify_index(path.parent)
    manifest = json.loads(path.read_text())
    if _sha(path.parent / "candidate_refs.jsonl") != manifest["refs_sha256"]:
        raise ValueError("prior candidate index changed")
    task_ids, answers = set(), set()
    for line in (path.parent / "candidate_refs.jsonl").read_text().splitlines():
        row = json.loads(line)["candidate"]
        task_ids.add(row["semantic_task_id"])
        if row["source_kind"].startswith("real_paper"):
            answers.add((row["source_group"], row["answer_sha256"]))
    return task_ids, answers


def _question(candidate: dict) -> str:
    kind = candidate["target"]["kind"]
    cue = candidate["cue"]
    if kind == "section":
        return (
            "In the paper's experiment or results discussion containing the phrase "
            f"‘{cue}’, which section does the nearby citation refer to? "
            "Give that section's exact heading."
        )
    return (
        "In the paper's experiment or results discussion containing the phrase "
        f"‘{cue}’, what is the caption of the nearby cited {kind}? "
        "Give the caption text."
    )


def _candidate(
    context: str,
    work: dict,
    source: dict,
    candidate: dict,
    tokenizer: Any,
    config: dict,
) -> tuple[dict, dict, dict]:
    resolved = resolve(context, candidate["cue"])
    if (
        resolved is None
        or resolved["reference_label"] != candidate["reference_label"]
        or resolved["reference_span"] != candidate["reference_span"]
        or resolved["caption_span"] != candidate["target"]["caption_span"]
    ):
        raise ValueError("final reader source resolver disagrees with planned link")
    shortcut = shortcut_reason(
        candidate["cue"],
        candidate["reference_label"],
        resolved["caption"],
        resolved["kind"],
    )
    if shortcut is not None:
        raise ValueError(shortcut)
    for name in ("reference_span", "caption_span"):
        start, end = resolved[name]
        masked = context[:start] + "?" * (end - start) + context[end:]
        if resolve(masked, candidate["cue"]) is not None:
            raise ValueError(f"{name} deletion did not break reader resolution")
    question = _question(candidate)
    user = context + SEPARATOR + question
    messages = [
        {"role": "user", "content": user},
        {"role": "assistant", "content": resolved["caption"]},
    ]
    encoded = tokenize_assistant_only(tokenizer, messages, config["max_full_tokens"])
    full = len(encoded["input_ids"])
    supervised = sum(label != -100 for label in encoded["labels"])
    if (
        full < config["min_full_tokens"]
        or supervised < 1
        or encoded["labels"]
        != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
    ):
        raise ValueError("physical length or assistant-only mask invalid")
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    if prompt.count(user) != 1:
        raise ValueError("reader user serialization ambiguous")
    user_start = prompt.index(user)
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    spans = {}
    for name in ("reference_span", "caption_span"):
        start, end = resolved[name]
        spans[name] = list(token_span(offsets, user_start + start, user_start + end))
        if spans[name][1] >= full - supervised:
            raise ValueError("paper evidence lies outside user prompt")
    extent = max(span[1] for span in spans.values()) - min(
        span[0] for span in spans.values()
    )
    if extent < config["min_evidence_extent_tokens"]:
        raise ValueError("under_evidence_extent")
    query_start = token_span(
        offsets,
        user_start + len(context) + len(SEPARATOR),
        user_start + len(user),
    )[0]
    if query_start <= max(span[1] for span in spans.values()):
        raise ValueError("paper query precedes required evidence")
    task_id = (
        "p96-paper-"
        + hashlib.sha256(
            canonical(
                [
                    work["work_id"],
                    source["version"],
                    source["sha256"],
                    candidate["reference_label"],
                    candidate["cue"],
                ]
            ).encode()
        ).hexdigest()[:20]
    )
    context_hash = hashlib.sha256(context.encode()).hexdigest()
    answer_hash = hashlib.sha256(resolved["caption"].encode()).hexdigest()
    reader = {"sample_id": task_id, "messages": messages}
    index = {
        "sample_id": task_id,
        "semantic_task_id": task_id,
        "source_kind": "real_paper_source",
        "source_group": "researchlab:arxiv:" + work["work_id"],
        "split": work["split"],
        "domain": "researchlab",
        "topic": "paper:" + work["work_id"],
        "operation": (
            "cross_file_section_reference"
            if resolved["kind"] == "section"
            else "cross_file_caption_reference"
        ),
        "context_sha256": context_hash,
        "answer_sha256": answer_hash,
        "full_chat_tokens": full,
        "input_tokens": full - supervised,
        "supervised_tokens": supervised,
        "evidence_extent_tokens": extent,
        "last_evidence_to_query_tokens": query_start
        - max(span[1] for span in spans.values()),
        "train_ready": False,
    }
    audit = {
        "sample_id": task_id,
        "source_archive": source,
        "source_path": candidate["source_path"],
        "target_path": resolved["target_path"],
        "source_cue": candidate["cue"],
        "reference_label": candidate["reference_label"],
        "target_kind": resolved["kind"],
        "answer": resolved["caption"],
        "reader_char_spans": {
            "reference": resolved["reference_span"],
            "caption": resolved["caption_span"],
        },
        "prompt_token_spans": spans,
        "query_token_start": query_start,
        "reference_deletion_breaks_resolver": True,
        "caption_deletion_breaks_resolver": True,
        "claim_limit": "unique source-level reference and caption/heading; compiled PDF and semantic alternatives unchecked",
    }
    return reader, index, audit


def _work_job(args):
    family_entry, config = args
    work = _inventory(family_entry)
    source = work["sources"][-1]
    files, duplicate_count = _dedupe(load_text_tar(ROOT / source["path"]))
    context = render_files(files)
    discovered, reasons = discover(context)
    candidates = sorted(
        discovered,
        key=lambda item: (
            -abs(item["reference_span"][0] - item["target"]["caption_span"][0]),
            item["reference_label"],
            item["cue"],
        ),
    )
    tokenizer = get_tokenizer() if candidates else None
    accepted = []
    rejected = []
    used_labels = set()
    for candidate in candidates:
        if len(accepted) >= config["max_tasks_per_work"]:
            break
        label = candidate["reference_label"]
        if label in used_labels:
            rejected.append({"label": label, "reason": "duplicate_target_in_world"})
            continue
        try:
            row = _candidate(context, work, source, candidate, tokenizer, config)
        except (ValueError, OverflowError) as exc:
            rejected.append({"label": label, "reason": str(exc)})
            continue
        accepted.append(row)
        used_labels.add(label)
    capacity = {
        "work_id": work["work_id"],
        "source_group": "researchlab:arxiv:" + work["work_id"],
        "split": work["split"],
        "latest_version": source["version"],
        "source_archive": source,
        "signed_bundle_pinned": work["signed_bundle_pinned"],
        "signature_cryptographically_verified": work[
            "signature_cryptographically_verified"
        ],
        "source_files": len(files),
        "duplicate_paths_removed": duplicate_count,
        "source_chars": len(context),
        "discovered_links": len(discovered),
        "accepted_tasks": len(accepted),
        "discovery_reasons": reasons,
        "candidate_reasons": dict(
            sorted(Counter(r["reason"] for r in rejected).items())
        ),
    }
    return capacity, accepted, rejected


def _audit_final(output_dir: Path) -> dict:
    tokenizer = get_tokenizer()
    readers = {}
    for split in ("train", "eval"):
        for line in (output_dir / f"{split}.jsonl").read_text().splitlines():
            row = json.loads(line)
            if row["sample_id"] in readers:
                raise ValueError("reader sample ID repeated")
            readers[row["sample_id"]] = row
    indices = [
        json.loads(line)
        for line in (output_dir / "sample_index.jsonl").read_text().splitlines()
    ]
    audits = [
        json.loads(line)
        for line in (output_dir / "audit.jsonl").read_text().splitlines()
    ]
    if len(readers) != len(indices) or len(indices) != len(audits):
        raise ValueError("final paper row inventories differ")
    checks = []
    for index, audit in zip(indices, audits, strict=True):
        sample_id = index["sample_id"]
        reader = readers[sample_id]
        if audit["sample_id"] != sample_id:
            raise ValueError("paper audit sample differs")
        _pinned(audit["source_archive"])
        user = reader["messages"][0]["content"]
        context, question = user.rsplit(SEPARATOR, 1)
        if not context or not question:
            raise ValueError("paper reader question missing")
        resolved = resolve(context, audit["source_cue"])
        if (
            resolved is None
            or resolved["caption"] != reader["messages"][1]["content"]
            or resolved["caption"] != audit["answer"]
            or resolved["reference_label"] != audit["reference_label"]
            or resolved["reference_span"] != audit["reader_char_spans"]["reference"]
            or resolved["caption_span"] != audit["reader_char_spans"]["caption"]
        ):
            raise ValueError("final paper visible answer differs")
        if (
            shortcut_reason(
                audit["source_cue"],
                audit["reference_label"],
                resolved["caption"],
                resolved["kind"],
            )
            is not None
        ):
            raise ValueError("final paper answer shortcut admitted")
        for name in ("reference", "caption"):
            start, end = audit["reader_char_spans"][name]
            masked = context[:start] + "?" * (end - start) + context[end:]
            if resolve(masked, audit["source_cue"]) is not None:
                raise ValueError("final paper text deletion failed")
        encoded = tokenize_assistant_only(tokenizer, reader["messages"], 262144)
        full = len(encoded["input_ids"])
        supervised = sum(label != -100 for label in encoded["labels"])
        prompt = _render_chat(tokenizer, reader["messages"][:1], generation_prompt=True)
        if prompt.count(user) != 1:
            raise ValueError("final paper user serialization ambiguous")
        user_start = prompt.index(user)
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        token_spans = {}
        for name in ("reference", "caption"):
            start, end = audit["reader_char_spans"][name]
            token_spans[name + "_span"] = list(
                token_span(offsets, user_start + start, user_start + end)
            )
        query_start = token_span(
            offsets,
            user_start + len(context) + len(SEPARATOR),
            user_start + len(user),
        )[0]
        extent = max(span[1] for span in token_spans.values()) - min(
            span[0] for span in token_spans.values()
        )
        distance = query_start - max(span[1] for span in token_spans.values())
        if (
            full != index["full_chat_tokens"]
            or full - supervised != index["input_tokens"]
            or supervised != index["supervised_tokens"]
            or encoded["labels"]
            != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
            or hashlib.sha256(context.encode()).hexdigest() != index["context_sha256"]
            or hashlib.sha256(resolved["caption"].encode()).hexdigest()
            != index["answer_sha256"]
            or token_spans != audit["prompt_token_spans"]
            or query_start != audit["query_token_start"]
            or extent != index["evidence_extent_tokens"]
            or distance != index["last_evidence_to_query_tokens"]
        ):
            raise ValueError("final paper mask or hash differs")
        checks.append(
            {
                "sample_id": sample_id,
                "reader_sha256": hashlib.sha256(canonical(reader).encode()).hexdigest(),
                "full_chat_tokens": full,
                "supervised_tokens": supervised,
            }
        )
    return {
        "schema": SCHEMA + ".final-audit",
        "checked_views": len(checks),
        "full_chat_tokens": sum(item["full_chat_tokens"] for item in checks),
        "supervised_tokens": sum(item["supervised_tokens"] for item in checks),
        "reader_sha256": {item["sample_id"]: item["reader_sha256"] for item in checks},
        "scope": "final-reader reference/caption resolution, two text deletions and exact assistant mask",
        "train_ready": False,
    }


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not 1 <= config.get("workers", 0) <= 8
        or not 1 <= config.get("max_tasks_per_work", 0) <= 32
        or not 1
        <= config.get("min_full_tokens", 0)
        < config.get("max_full_tokens", 0)
        <= 262144
    ):
        raise ValueError("invalid P96 paper reference config")
    if output_dir.exists() != verify_only:
        raise ValueError("output must be new, or present for --verify-only")
    prior_config_path = _pinned(config["source_config"])
    source_config = json.loads(prior_config_path.read_text())
    if source_config.get("schema") != "longworld.p86-frozen-paper-batch.v1":
        raise ValueError("wrong pinned paper source plan")
    prior_ids, prior_answers = _prior(config["prior_candidate_index"])
    families = source_config["families"]
    with ProcessPoolExecutor(max_workers=config["workers"]) as executor:
        results = list(
            executor.map(_work_job, ((family, config) for family in families))
        )
    capacities, rejects, rows = [], [], []
    for capacity, accepted, rejected in results:
        capacities.append(capacity)
        rejects.extend({"work_id": capacity["work_id"], **item} for item in rejected)
        for reader, index, audit in accepted:
            if (
                index["semantic_task_id"] in prior_ids
                or (index["source_group"], index["answer_sha256"]) in prior_answers
            ):
                rejects.append(
                    {
                        "work_id": capacity["work_id"],
                        "label": audit["reference_label"],
                        "reason": "duplicate_prior_bank",
                    }
                )
                continue
            rows.append((reader, index, audit))
    if len({item[1]["semantic_task_id"] for item in rows}) != len(rows):
        raise ValueError("P96 paper semantic task IDs repeat")
    payloads = {
        "train.jsonl": [r for r, i, _a in rows if i["split"] == "train"],
        "eval.jsonl": [r for r, i, _a in rows if i["split"] == "eval"],
        "sample_index.jsonl": [i for _r, i, _a in rows],
        "audit.jsonl": [a for _r, _i, a in rows],
        "capacity_index.jsonl": capacities,
        "rejected.jsonl": rejects,
    }
    if not verify_only:
        output_dir.mkdir(parents=True)
    for name, values in payloads.items():
        content = "".join(canonical(value) + "\n" for value in values)
        path = output_dir / name
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"P96 paper output replay differs: {name}")
        else:
            path.write_text(content)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "source_config": config["source_config"],
        "prior_candidate_index": config["prior_candidate_index"],
        "code_sha256": {
            name: _sha(ROOT / name)
            for name in (
                "scripts/p96_paper_caption_qa.py",
                "scripts/run_p96_paper_reference_qa.py",
                "scripts/run_p86_frozen_paper_batch.py",
            )
        },
        "source_works": len(capacities),
        "productive_works": sum(row["accepted_tasks"] > 0 for row in capacities),
        "candidate_views": len(rows),
        "independent_tasks": len(rows),
        "split_tasks": dict(sorted(Counter(i["split"] for _r, i, _a in rows).items())),
        "operations": dict(
            sorted(Counter(i["operation"] for _r, i, _a in rows).items())
        ),
        "length_bins": dict(
            sorted(
                Counter(
                    "<32K"
                    if i["full_chat_tokens"] < 32768
                    else "32-64K"
                    if i["full_chat_tokens"] < 65536
                    else "64-128K"
                    if i["full_chat_tokens"] < 131072
                    else "128-256K"
                    for _r, i, _a in rows
                ).items()
            )
        ),
        "rejections": dict(sorted(Counter(item["reason"] for item in rejects).items())),
        "files_sha256": {name: _sha(output_dir / name) for name in payloads},
        "train_ready": False,
    }
    manifest_path = output_dir / "manifest.json"
    content = canonical(manifest) + "\n"
    if verify_only:
        if manifest_path.read_text() != content:
            raise ValueError("P96 paper manifest replay differs")
    else:
        manifest_path.write_text(content)
    final = _audit_final(output_dir)
    final["manifest_sha256"] = _sha(manifest_path)
    audit_path = output_dir / "mask_audit.json"
    audit_content = canonical(final) + "\n"
    if verify_only:
        if audit_path.read_text() != audit_content:
            raise ValueError("P96 paper final audit differs")
    else:
        audit_path.write_text(audit_content)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(canonical(run(args.config, args.output_dir, verify_only=args.verify_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
