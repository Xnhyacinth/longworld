"""Compile source-visible year tables with optional unqueried trailing cells."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.p92_generic_table_scan import answer
from longworld.synthesis.p95_semiclosed_table import boundary_replay, parse_tables
from scripts.audit_p94_wiki_native import audit as audit_mask
from scripts.audit_wiki_join_positions import token_span
from scripts.run_p92_generic_table_scan import (
    _context,
    _dump,
    _duplicate_support,
    _lines,
    _pinned_path,
    _sha,
    _write,
)
from scripts.run_p95_wiki_table_sweep import (
    _bank_task_ids,
    _load_sources,
    _plan,
    _task_id,
)
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p95-semiclosed-year-table.v1"


def _scan(source: dict):
    snapshot = json.loads(_pinned_path(source["snapshot"]).read_text())
    documents = snapshot["documents"]
    revisions = snapshot["source"]["revisions"]
    if {doc["title"] for doc in documents} != set(revisions):
        raise ValueError("revision inventory mismatch")
    candidates, pages = [], []
    for doc in documents:
        tables, rejected = parse_tables(doc["text"])
        candidates.extend((doc, table) for table in tables)
        pages.append(
            {
                "source_group": source["name"],
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "accepted_tables": len(tables),
                "rejected_tables": list(rejected),
            }
        )
    return source, snapshot, candidates, pages


def _compile_one(
    source: dict,
    snapshot: dict,
    doc: dict,
    table,
    low: int,
    high: int,
    tokenizer,
    max_tokens: int,
):
    context, doc_start = _context(snapshot, doc)
    baseline = answer(table, low, high)
    intervention = boundary_replay(doc["text"], table, low, high)
    left = doc_start + intervention["value_start"]
    right = doc_start + intervention["value_end"]
    if context[left:right] != intervention["old_value"]:
        raise ValueError("reader intervention span drift")
    for key, expected in (
        ("hit_value", intervention["hit_answer"]),
        ("near_miss_value", baseline),
    ):
        value = intervention[key]
        changed = context[:left] + value + context[right:]
        changed_doc = changed[
            doc_start : doc_start + len(doc["text"]) - (right - left) + len(value)
        ]
        matching = [
            item
            for item in parse_tables(changed_doc)[0]
            if item.header_start == table.header_start
            and item.heading == table.heading
            and item.header == table.header
        ]
        if len(matching) != 1 or answer(matching[0], low, high) != expected:
            raise ValueError("final reader intervention replay failed")
        intervention[key + "_reader_sha256"] = hashlib.sha256(
            changed.encode()
        ).hexdigest()
    _duplicate_support(
        context,
        doc_start + table.header_start,
        doc_start + table.table_end,
        table,
        baseline,
    )
    question = (
        f"In the '{table.heading}' section of {doc['title']}, use the table with "
        f"columns '{table.header}'. Which names have a value from {low} through "
        f"{high}, inclusive, in '{table.year_column}'? List every matching name "
        "and the total count, sorted alphabetically."
    )
    user = context + "\n\nQUESTION\n" + question
    task_id = _task_id(doc, table, low, high)
    sample_id = "p95-wiki-semi-" + task_id
    messages = [
        {"role": "user", "content": user},
        {"role": "assistant", "content": _dump(baseline)},
    ]
    encoded = tokenize_assistant_only(tokenizer, messages, max_tokens)
    full = len(encoded["input_ids"])
    supervised = sum(label != -100 for label in encoded["labels"])
    if (
        not supervised
        or encoded["labels"]
        != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
    ):
        raise ValueError("assistant-only mask failed")
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    if prompt.count(user) != 1:
        raise ValueError("user occurrence is ambiguous")
    user_start = prompt.index(user)
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    evidence = []
    for row in table.rows:
        start, end = token_span(
            offsets,
            user_start + doc_start + row.value_start,
            user_start + doc_start + row.value_end,
        )
        evidence.append(
            {
                "name": row.name,
                "year": row.year,
                "selected": low <= row.year <= high,
                "reader_value_start": doc_start + row.value_start,
                "reader_value_end": doc_start + row.value_end,
                "prompt_token_start": start,
                "prompt_token_end": end,
            }
        )
    if (
        len(evidence) != len(table.rows)
        or max(row["prompt_token_end"] for row in evidence) >= full - supervised
    ):
        raise ValueError("candidate evidence missing from prompt")
    reader = {
        "sample_id": sample_id,
        "example_id": sample_id,
        "quality_status": "research_candidate",
        "messages": messages,
    }
    index = {
        "sample_id": sample_id,
        "example_id": sample_id,
        "task_id": task_id,
        "source_group": snapshot["snapshot_id"],
        "world_id": snapshot["snapshot_id"],
        "split": source["split"],
        "source_kind": "real_wiki",
        "domain": source["domain"],
        "topic": source["topic"],
        "operation": "closed_year_table_interval",
        "family": "table_scan",
        "task_type": "closed_year_table_interval",
        "dependency_status": "bounded_numeric_hit_and_near_miss_replay",
        "question_style": "explicit_header_table_scan",
        "full_chat_tokens": full,
        "input_tokens": full - supervised,
        "supervised_tokens": supervised,
        "candidate_rows": len(table.rows),
        "selected_rows": baseline["count"],
        "evidence_token_extent": max(row["prompt_token_end"] for row in evidence)
        - min(row["prompt_token_start"] for row in evidence),
        "query_to_first_evidence_tokens": full
        - supervised
        - min(row["prompt_token_start"] for row in evidence),
        "answer_sha256": hashlib.sha256(_dump(baseline).encode()).hexdigest(),
    }
    audit = {
        "sample_id": sample_id,
        "example_id": sample_id,
        "question": question,
        "answer": baseline,
        "source_doc_id": doc["doc_id"],
        "source_title": doc["title"],
        "source_revision": snapshot["source"]["revisions"][doc["title"]],
        "source_table_heading": table.heading,
        "source_table_header": table.header,
        "header_start": table.header_start,
        "candidate_rows": evidence,
        "intervention": intervention,
        "claim_limit": "all contiguous rows parsed; optional trailing cells ignored; bounded year-cell replay; prose alternatives unchecked",
    }
    return reader, index, audit


def _compile_table(job: tuple, max_tokens: int):
    source, snapshot, doc, table, chosen = job
    tokenizer = get_tokenizer()
    rows, rejected = [], []
    for low, high in chosen:
        try:
            rows.append(
                _compile_one(
                    source, snapshot, doc, table, low, high, tokenizer, max_tokens
                )
            )
        except (ValueError, OverflowError) as exc:
            rejected.append(
                {
                    "source_group": source["name"],
                    "title": doc["title"],
                    "heading": table.heading,
                    "interval": [low, high],
                    "reason": str(exc),
                }
            )
    return rows, rejected


def _compile_job(args):
    return _compile_table(*args)


def run(
    config_path: Path, output_dir: Path, *, workers: int = 2, verify_only: bool = False
) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not 1 <= workers <= 16
        or not 1 <= config.get("max_tasks_per_table", 0) <= 64
        or not 1 <= config.get("max_full_tokens", 0) <= 131072
    ):
        raise ValueError("invalid semiclosed table config")
    if output_dir.exists() != verify_only:
        raise ValueError("output must be new, or present for --verify-only")
    sources, gross = _load_sources(config)
    prior_ids = _bank_task_ids(config["prior_candidate_index"])
    with ProcessPoolExecutor(max_workers=workers) as executor:
        scans = list(executor.map(_scan, sources))
    work, rejected, counts = _plan(scans, prior_ids, {}, config["max_tasks_per_table"])
    with ProcessPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(
                _compile_job,
                ((job, config["max_full_tokens"]) for job in work),
            )
        )
    rows = []
    for accepted, failed in results:
        rows.extend(accepted)
        rejected.extend(failed)
    readers = {"train": [], "eval": []}
    indices, audits = [], []
    for reader, index, item in rows:
        readers[index["split"]].append(reader)
        indices.append(index)
        audits.append(item)
    if len({row["task_id"] for row in indices}) != len(indices):
        raise ValueError("generated task IDs repeat")
    pages = [
        row for _s, _snapshot, _candidates, page_audit in scans for row in page_audit
    ]
    payloads = {
        "train.jsonl": readers["train"],
        "eval.jsonl": readers["eval"],
        "sample_index.jsonl": indices,
        "audit.jsonl": audits,
        "page_audit.jsonl": pages,
        "rejected.jsonl": rejected,
    }
    if not verify_only:
        output_dir.mkdir(parents=True)
    for name, value in payloads.items():
        path = output_dir / name
        if verify_only:
            if path.read_text() != "".join(_dump(row) + "\n" for row in value):
                raise ValueError(f"semiclosed table replay drift: {name}")
        else:
            _lines(path, value)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "source_pools": config["source_pools"],
        "prior_candidate_index": config["prior_candidate_index"],
        "code_sha256": {
            name: _sha(ROOT / name)
            for name in (
                "scripts/run_p95_semiclosed_table_batch.py",
                "longworld/synthesis/p95_semiclosed_table.py",
                "scripts/run_p95_wiki_table_sweep.py",
            )
        },
        "source_records": gross["pool_records"],
        "unique_source_groups": len(sources),
        "page_occurrences": len(pages),
        "unique_page_titles": len({row["title"].casefold() for row in pages}),
        "schema_eligible_tables": counts["eligible_tables"],
        "parser_rejected_tables": counts["parser_rejected_tables"],
        "intervals_planned": counts["intervals_planned"],
        "candidate_views": len(indices),
        "independent_tasks": len(indices),
        "split_views": dict(sorted(Counter(row["split"] for row in indices).items())),
        "domains": dict(sorted(Counter(row["domain"] for row in indices).items())),
        "topics": dict(sorted(Counter(row["topic"] for row in indices).items())),
        "source_groups": dict(
            sorted(Counter(row["source_group"] for row in indices).items())
        ),
        "candidate_rejections": dict(
            sorted(Counter(row["reason"] for row in rejected).items())
        ),
        "full_chat_tokens": [row["full_chat_tokens"] for row in indices],
        "evidence_token_extents": [row["evidence_token_extent"] for row in indices],
        "files_sha256": {name: _sha(output_dir / name) for name in payloads},
        "train_ready": False,
    }
    if verify_only:
        if json.loads((output_dir / "manifest.json").read_text()) != manifest:
            raise ValueError("semiclosed manifest replay drift")
    else:
        _write(output_dir / "manifest.json", manifest)
    audit_mask(output_dir, "generic_year")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(
            run(
                args.config,
                args.output_dir,
                workers=args.workers,
                verify_only=args.verify_only,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
