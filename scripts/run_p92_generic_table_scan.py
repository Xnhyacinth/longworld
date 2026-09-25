"""Batch-compile strict, frozen Wiki year tables into native reader candidates."""

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
from longworld.synthesis.p92_generic_table_scan import (
    answer,
    boundary_replay,
    intervals,
    parse_tables,
)
from scripts.audit_wiki_join_positions import token_span
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p92-generic-table-scan.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _lines(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(_dump(row) + "\n" for row in rows))


def _pinned_path(pin: dict) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("pinned path must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"pinned file hash drift: {relative}")
    return path


def _prior_keys(config: dict) -> dict[tuple[str, str, str, int, int], str]:
    """Deduplicate semantic programs independently of wording or answer hash."""
    keys = {}
    for pin in config["prior_task_audits"]:
        for line in _pinned_path(pin).read_text().splitlines():
            if not line:
                continue
            row = json.loads(line)
            program = row["program"]
            if program.get("op") != "closed_table_interval":
                raise ValueError("prior task audit has another operation")
            key = (
                row["source_group"],
                program["doc_id"],
                program["year_column"],
                program["low"],
                program["high"],
            )
            keys[key] = row["example_id"]
    return keys


def _scan(source: dict) -> tuple[dict, dict, list[tuple[dict, object]], list[dict]]:
    pin = source["snapshot"]
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source path must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"source hash drift: {relative}")
    snapshot = json.loads(path.read_text())
    documents = snapshot["documents"]
    revisions = snapshot["source"]["revisions"]
    if {doc["title"] for doc in documents} != set(revisions):
        raise ValueError("revision inventory mismatch")
    candidates, audits = [], []
    for doc in documents:
        tables, rejected = parse_tables(doc["text"])
        candidates.extend((doc, table) for table in tables)
        audits.append(
            {
                "source_group": source["name"],
                "title": doc["title"],
                "doc_id": doc["doc_id"],
                "accepted_tables": len(tables),
                "rejected_tables": list(rejected),
            }
        )
    return source, snapshot, candidates, audits


def _context(snapshot: dict, selected: dict) -> tuple[str, int]:
    documents = [
        selected,
        *(doc for doc in snapshot["documents"] if doc is not selected),
    ]
    parts: list[str] = []
    start = -1
    for doc in documents:
        prefix = f"=== DOCUMENT: {doc['title']} (revision {snapshot['source']['revisions'][doc['title']]}) ===\n"
        if doc is selected:
            start = sum(map(len, parts)) + len(prefix)
        parts.extend((prefix, doc["text"], "\n\n"))
    return "".join(parts).rstrip(), start


def _duplicate_support(
    context: str, table_start: int, table_end: int, table, result: dict
) -> None:
    outside = context[:table_start] + "\n" + context[table_end:]
    selected = set(result["entries"])
    for row in table.rows:
        if row.name in selected and any(
            row.name in line and str(row.year) in line for line in outside.splitlines()
        ):
            raise ValueError(f"same-line alternate support: {row.name}")


def _compile(
    config: dict, source: dict, snapshot: dict, doc: dict, table, tokenizer
) -> list[tuple[dict, dict, dict]]:
    context, doc_start = _context(snapshot, doc)
    results = []
    selected_intervals = (
        (config["interval_override"],)
        if "interval_override" in config
        else intervals(table, config["max_tasks_per_table"])
    )
    for low, high in selected_intervals:
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
            tables, _ = parse_tables(changed_doc)
            matching = [
                item
                for item in tables
                if item.heading == table.heading and item.header == table.header
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
            f"In the '{table.heading}' table of {doc['title']}, which entries have a value "
            f"from {low} through {high}, inclusive, in the '{table.year_column}' column? "
            "List every matching name and the total count, sorted alphabetically."
        )
        user = context + "\n\nQUESTION\n" + question
        messages = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": _dump(baseline)},
        ]
        encoded = tokenize_assistant_only(
            tokenizer, messages, config["max_full_tokens"]
        )
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
        task_id = hashlib.sha256(
            f"{doc['doc_id']}|{table.header_start}|{low}|{high}".encode()
        ).hexdigest()[:20]
        sample_id = "p92-wiki-table-" + task_id
        reader = {
            "example_id": sample_id,
            "sample_id": sample_id,
            "quality_status": "research_candidate",
            "messages": messages,
        }
        index = {
            "example_id": sample_id,
            "sample_id": sample_id,
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
            "question_style": "natural_table_scan",
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
            "example_id": sample_id,
            "sample_id": sample_id,
            "question": question,
            "answer": baseline,
            "source_doc_id": doc["doc_id"],
            "source_title": doc["title"],
            "source_revision": snapshot["source"]["revisions"][doc["title"]],
            "source_table_heading": table.heading,
            "source_table_header": table.header,
            "candidate_rows": evidence,
            "intervention": intervention,
            "claim_limit": "complete parse of one visible year table and bounded numeric replay; semantic paraphrase shortcuts unchecked",
        }
        results.append((reader, index, audit))
    return results


def run(config_path: Path, output_dir: Path, *, workers: int = 2) -> dict:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or not 1 <= workers <= 16:
        raise ValueError("invalid scan configuration")
    pin = config["source_pool"]
    path = _pinned_path(pin)
    pool = json.loads(path.read_text())
    if pool.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("wrong source pool schema")
    sources = sorted(pool["sources"], key=lambda item: (item["split"], item["name"]))
    prior_keys = _prior_keys(config)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        scans = list(executor.map(_scan, sources))
    tokenizer = None
    readers = {"train": [], "eval": []}
    indices, audits, pages, rejected = [], [], [], []
    seen_titles: dict[str, str] = {}
    seen_tables: set[tuple[str, str, int]] = set()
    accepted_groups: set[str] = set()
    for source, snapshot, candidates, page_audit in scans:
        pages.extend(page_audit)
        for doc, table in candidates:
            title_key = doc["title"].casefold()
            prior = seen_titles.setdefault(title_key, source["split"])
            if prior != source["split"]:
                rejected.append(
                    {
                        "source_group": source["name"],
                        "title": doc["title"],
                        "reason": "source_split_leak_risk",
                    }
                )
                continue
            table_key = (title_key, table.header, table.header_start)
            if table_key in seen_tables:
                rejected.append(
                    {
                        "source_group": source["name"],
                        "title": doc["title"],
                        "reason": "duplicate_source_table",
                    }
                )
                continue
            seen_tables.add(table_key)
            for interval in intervals(table, config["max_tasks_per_table"]):
                duplicate_key = (
                    snapshot["snapshot_id"],
                    doc["doc_id"],
                    table.year_column,
                    *interval,
                )
                if duplicate_key in prior_keys:
                    rejected.append(
                        {
                            "source_group": source["name"],
                            "title": doc["title"],
                            "heading": table.heading,
                            "interval": interval,
                            "reason": "duplicate_existing",
                            "prior_example_id": prior_keys[duplicate_key],
                        }
                    )
                    continue
                try:
                    if tokenizer is None:
                        tokenizer = get_tokenizer()
                    triplets = _compile(
                        {**config, "interval_override": interval},
                        source,
                        snapshot,
                        doc,
                        table,
                        tokenizer,
                    )
                except (ValueError, OverflowError) as exc:
                    rejected.append(
                        {
                            "source_group": source["name"],
                            "title": doc["title"],
                            "heading": table.heading,
                            "interval": interval,
                            "reason": str(exc),
                        }
                    )
                    continue
                for reader, index, audit in triplets:
                    audit["header_start"] = table.header_start
                    readers[source["split"]].append(reader)
                    indices.append(index)
                    audits.append(audit)
                    accepted_groups.add(source["name"])
    output_dir.mkdir(parents=True)
    for split in ("train", "eval"):
        _lines(output_dir / f"{split}.jsonl", readers[split])
    _lines(output_dir / "sample_index.jsonl", indices)
    _lines(output_dir / "audit.jsonl", audits)
    _lines(output_dir / "page_audit.jsonl", pages)
    _lines(output_dir / "rejected.jsonl", rejected)
    result = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "source_pool_sha256": pin["sha256"],
        "prior_task_audit_sha256": [
            item["sha256"] for item in config["prior_task_audits"]
        ],
        "source_groups_scanned": len(sources),
        "unique_pages_scanned": len({page["doc_id"] for page in pages}),
        "schema_eligible_tables": sum(len(candidates) for _, _, candidates, _ in scans),
        "eligible_source_groups": len(accepted_groups),
        "eligible_source_group_names": sorted(accepted_groups),
        "candidate_views": len(indices),
        "independent_tasks": len({index["task_id"] for index in indices}),
        "train_views": len(readers["train"]),
        "eval_views": len(readers["eval"]),
        "rejected_tables": sum(len(page["rejected_tables"]) for page in pages),
        "candidate_rejections": dict(
            sorted(Counter(row["reason"] for row in rejected).items())
        ),
        "full_chat_tokens": [row["full_chat_tokens"] for row in indices],
        "evidence_token_extents": [row["evidence_token_extent"] for row in indices],
        "query_to_first_evidence_tokens": [
            row["query_to_first_evidence_tokens"] for row in indices
        ],
        "length_bins": dict(
            sorted(
                Counter(
                    "<32K"
                    if row["full_chat_tokens"] < 32768
                    else "32-64K"
                    if row["full_chat_tokens"] < 65536
                    else "64-128K"
                    if row["full_chat_tokens"] < 131072
                    else "128K+"
                    for row in indices
                ).items()
            )
        ),
        "mask_audited": len(indices),
        "train_ready": False,
        "files_sha256": {
            name: _sha(output_dir / name)
            for name in (
                "train.jsonl",
                "eval.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
                "page_audit.jsonl",
                "rejected.jsonl",
            )
        },
    }
    _write(output_dir / "manifest.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    print(_dump(run(args.config, args.output_dir, workers=args.workers)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
