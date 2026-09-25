"""Compile complete categorical table scans from pinned, real Wiki snapshots.

Only contiguous, full-width tables with unique plain names and a complete
plain-valued category column qualify. Every candidate row is read from the
final context; an answer-changing cell edit and a non-changing edit are
reparsed from those same bytes. This is a bounded table dependency check,
not proof that unrelated prose contains no equivalent answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.p92_generic_table_scan import PLAIN_NAME, SUBJECT_COLUMNS
from scripts.audit_wiki_join_positions import token_span
from scripts.run_p92_generic_table_scan import _context, _dump, _pinned_path, _sha
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p100-wiki-categorical-scan.v1"
BAD_VALUE = ("{", "}", "[", "]", "<", ">", "http://", "https://", "Cite ")
BAD_COLUMN = ("url", "ref", "cite", "note", "image", "photo")


@dataclass(frozen=True)
class Row:
    name: str
    cells: tuple[str, ...]
    starts: tuple[int, ...]
    ends: tuple[int, ...]


@dataclass(frozen=True)
class Table:
    heading: str
    header: str
    columns: tuple[str, ...]
    header_start: int
    table_end: int
    rows: tuple[Row, ...]


def _plain_value(value: str) -> bool:
    return 1 <= len(value) <= 50 and not any(mark in value for mark in BAD_VALUE)


def parse_tables(text: str) -> tuple[tuple[Table, ...], tuple[dict, ...]]:
    """Reject a whole table on any row-width, name, or boundary ambiguity."""
    lines = text.splitlines(keepends=True)
    starts = []
    cursor = 0
    for raw in lines:
        starts.append(cursor)
        cursor += len(raw)
    heading = ""
    found: list[Table] = []
    rejected: list[dict] = []
    visible_keys: Counter[tuple[str, str]] = Counter()
    for index, raw in enumerate(lines):
        header = raw.rstrip("\r\n")
        if header.startswith("## "):
            heading = header[3:]
            continue
        columns = tuple(header.split(" | "))
        if not heading or len(columns) < 3 or columns[0] not in SUBJECT_COLUMNS:
            continue
        visible_keys[heading, header] += 1
        rows = []
        end_index = index + 1
        failure = ""
        while end_index < len(lines):
            body = lines[end_index].rstrip("\r\n")
            if not body or body.startswith("#"):
                break
            parts = tuple(body.split(" | "))
            if len(parts) != len(columns):
                failure = "row_width_mismatch"
                break
            name = parts[0].strip()
            if not PLAIN_NAME.fullmatch(name) or "Cite " in name:
                failure = "ambiguous_name"
                break
            cell_starts = []
            cell_ends = []
            position = starts[end_index]
            for part in parts:
                leading = len(part) - len(part.lstrip())
                value = part.strip()
                cell_starts.append(position + leading)
                cell_ends.append(position + leading + len(value))
                position += len(part) + 3
            rows.append(
                Row(
                    name,
                    tuple(part.strip() for part in parts),
                    tuple(cell_starts),
                    tuple(cell_ends),
                )
            )
            end_index += 1
        if not failure:
            next_nonblank = end_index
            while next_nonblank < len(lines) and not lines[next_nonblank].strip():
                next_nonblank += 1
            if next_nonblank < len(lines) and not lines[next_nonblank].startswith(
                "## "
            ):
                failure = "ambiguous_table_end"
            elif len(rows) < 8:
                failure = "too_few_rows"
            elif len({row.name for row in rows}) != len(rows):
                failure = "duplicate_name"
        if failure:
            rejected.append({"heading": heading, "header": header, "reason": failure})
            continue
        found.append(
            Table(
                heading,
                header,
                columns,
                starts[index],
                starts[end_index] if end_index < len(lines) else len(text),
                tuple(rows),
            )
        )
    unique = []
    for table in found:
        if visible_keys[table.heading, table.header] != 1:
            rejected.append(
                {
                    "heading": table.heading,
                    "header": table.header,
                    "reason": "ambiguous_visible_key",
                }
            )
        else:
            unique.append(table)
    return tuple(unique), tuple(rejected)


def options(table: Table, *, max_tasks: int) -> tuple[tuple[int, str], ...]:
    available = []
    for column_index, column in enumerate(table.columns[1:], 1):
        if any(mark in column.casefold() for mark in BAD_COLUMN):
            continue
        values = [row.cells[column_index] for row in table.rows]
        # This is a categorical recipe. Numeric-looking columns in the frozen
        # Wiki text can be shifted under an adjacent header by malformed rows.
        # A separate typed metric recipe must handle those cases.
        if any(
            not _plain_value(value) or not any(ch.isalpha() for ch in value)
            for value in values
        ):
            continue
        counts = Counter(values)
        if len(counts) < 2:
            continue
        for value, count in counts.items():
            if value in {"-", "?", "Unknown", "N/A", "None"}:
                continue
            if 2 <= count <= min(15, len(table.rows) - 2):
                available.append((column_index, value, count))
    # Deterministically spread selections over columns and answer sizes.
    available.sort(key=lambda row: (row[0], row[2], row[1]))
    by_column: dict[int, list[tuple[int, str, int]]] = {}
    for item in available:
        by_column.setdefault(item[0], []).append(item)
    chosen = []
    for rank in range(max(map(len, by_column.values()), default=0)):
        for column in sorted(by_column):
            if rank < len(by_column[column]):
                chosen.append(by_column[column][rank][:2])
                if len(chosen) >= max_tasks:
                    return tuple(chosen)
    return tuple(chosen)


def answer(table: Table, column_index: int, category: str) -> dict:
    entries = sorted(
        row.name for row in table.rows if row.cells[column_index] == category
    )
    if not 2 <= len(entries) <= min(15, len(table.rows) - 2):
        raise ValueError("degenerate category result")
    return {"count": len(entries), "entries": entries}


def _same_table(text: str, table: Table) -> Table:
    matches = [
        item
        for item in parse_tables(text)[0]
        if item.header_start == table.header_start
        and item.heading == table.heading
        and item.header == table.header
    ]
    if len(matches) != 1 or len(matches[0].rows) != len(table.rows):
        raise ValueError("final reader table changed")
    return matches[0]


def _intervene(
    context: str,
    doc_start: int,
    doc_text: str,
    table: Table,
    column_index: int,
    category: str,
) -> dict:
    baseline = answer(table, column_index, category)
    alternate = sorted(
        {
            row.cells[column_index]
            for row in table.rows
            if row.cells[column_index] != category
            and _plain_value(row.cells[column_index])
        }
    )
    if not alternate:
        raise ValueError("no valid control category")
    target = next(row for row in table.rows if row.cells[column_index] != category)
    left, right = (
        doc_start + target.starts[column_index],
        doc_start + target.ends[column_index],
    )
    if context[left:right] != target.cells[column_index]:
        raise ValueError("final reader cell offset drift")

    def replay(replacement: str) -> tuple[dict, str]:
        changed = context[:left] + replacement + context[right:]
        changed_doc = changed[
            doc_start : doc_start + len(doc_text) - (right - left) + len(replacement)
        ]
        result = answer(_same_table(changed_doc, table), column_index, category)
        return result, hashlib.sha256(changed.encode()).hexdigest()

    hit, hit_hash = replay(category)
    if hit["count"] != baseline["count"] + 1 or target.name not in hit["entries"]:
        raise ValueError("hit edit did not change complete-set answer")
    control = next(
        (value for value in alternate if value != target.cells[column_index]),
        None,
    )
    if control is None:
        raise ValueError("no distinct non-changing control value")
    near, near_hash = replay(control)
    if near != baseline:
        raise ValueError("control edit changed answer")
    return {
        "changed_name": target.name,
        "reader_cell_start": left,
        "reader_cell_end": right,
        "old_value": target.cells[column_index],
        "hit_value": category,
        "hit_answer": hit,
        "hit_reader_sha256": hit_hash,
        "control_value": control,
        "control_answer": near,
        "control_reader_sha256": near_hash,
    }


def _duplicate_support(
    context: str, start: int, end: int, table: Table, column_index: int, category: str
) -> None:
    outside = context[:start] + "\n" + context[end:]
    selected = {row.name for row in table.rows if row.cells[column_index] == category}
    for line in outside.splitlines():
        if category in line and any(name in line for name in selected):
            raise ValueError("same-line alternate support")


def _scan_source(source: dict) -> tuple[dict, dict, list, list]:
    snapshot = json.loads(_pinned_path(source["snapshot"]).read_text())
    if {doc["title"] for doc in snapshot["documents"]} != set(
        snapshot["source"]["revisions"]
    ):
        raise ValueError("snapshot revision inventory mismatch")
    found, pages = [], []
    for doc in snapshot["documents"]:
        tables, rejected = parse_tables(doc["text"])
        found.extend((doc, table) for table in tables)
        pages.append(
            {
                "source_group": source["name"],
                "title": doc["title"],
                "doc_id": doc["doc_id"],
                "eligible_tables": len(tables),
                "rejected_tables": list(rejected),
            }
        )
    return source, snapshot, found, pages


def _compile(job: tuple, max_tokens: int) -> tuple[list, list]:
    source, snapshot, doc, table, selections = job
    tokenizer = get_tokenizer()
    context, doc_start = _context(snapshot, doc)
    accepted, rejected = [], []
    for column_index, category in selections:
        try:
            baseline = answer(_same_table(doc["text"], table), column_index, category)
            edit = _intervene(
                context, doc_start, doc["text"], table, column_index, category
            )
            _duplicate_support(
                context,
                doc_start + table.header_start,
                doc_start + table.table_end,
                table,
                column_index,
                category,
            )
            question = f"In the '{table.heading}' table of {doc['title']}, use the column '{table.columns[column_index]}'. Which entries have exactly '{category}' in that column? List every matching name and the total count, sorted alphabetically."
            user = context + "\n\nQUESTION\n" + question
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
                != [-100] * (full - supervised)
                + encoded["input_ids"][full - supervised :]
            ):
                raise ValueError("assistant-only mask failed")
            prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
            if prompt.count(user) != 1:
                raise ValueError("ambiguous user prompt occurrence")
            offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
                "offset_mapping"
            ]
            user_start = prompt.index(user)
            evidence = []
            for row in table.rows:
                token_start, token_end = token_span(
                    offsets,
                    user_start + doc_start + row.starts[column_index],
                    user_start + doc_start + row.ends[column_index],
                )
                evidence.append(
                    {
                        "name": row.name,
                        "value": row.cells[column_index],
                        "selected": row.cells[column_index] == category,
                        "reader_cell_start": doc_start + row.starts[column_index],
                        "reader_cell_end": doc_start + row.ends[column_index],
                        "prompt_token_start": token_start,
                        "prompt_token_end": token_end,
                    }
                )
            if max(row["prompt_token_end"] for row in evidence) >= full - supervised:
                raise ValueError("evidence outside supervised prompt")
            digest = hashlib.sha256(
                f"{snapshot['snapshot_id']}|{doc['doc_id']}|{table.header_start}|{column_index}|{category}".encode()
            ).hexdigest()[:20]
            sample_id = "p100-wiki-category-" + digest
            index = {
                "sample_id": sample_id,
                "example_id": sample_id,
                "task_id": digest,
                "source_group": snapshot["snapshot_id"],
                "world_id": snapshot["snapshot_id"],
                "split": source["split"],
                "source_kind": "real_wiki",
                "domain": source["domain"],
                "topic": source["topic"],
                "operation": "closed_categorical_table_scan",
                "family": "table_scan",
                "task_type": "closed_categorical_table_scan",
                "dependency_status": "bounded_visible_cell_hit_and_control_replay",
                "question_style": "explicit_header_complete_set",
                "tokenizer_profile": "pinned-chat-template",
                "full_chat_tokens": full,
                "input_tokens": full - supervised,
                "supervised_tokens": supervised,
                "candidate_rows": len(table.rows),
                "selected_rows": baseline["count"],
                "evidence_token_extent": max(
                    row["prompt_token_end"] for row in evidence
                )
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
                "source_column": table.columns[column_index],
                "category": category,
                "candidate_rows": evidence,
                "intervention": edit,
                "claim_limit": "complete contiguous table rows and bounded final-reader cell edits; prose alternatives and unparsed tables unchecked",
            }
            accepted.append(
                (
                    {
                        "sample_id": sample_id,
                        "example_id": sample_id,
                        "quality_status": "research_candidate",
                        "messages": messages,
                    },
                    index,
                    audit,
                )
            )
        except (ValueError, OverflowError) as exc:
            rejected.append(
                {
                    "source_group": source["name"],
                    "title": doc["title"],
                    "heading": table.heading,
                    "column": table.columns[column_index],
                    "category": category,
                    "reason": str(exc),
                }
            )
    return accepted, rejected


def run(
    config_path: Path, output_dir: Path, *, workers: int = 4, verify_only: bool = False
) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not 1 <= workers <= 16
        or not 1 <= config.get("max_tasks_per_table", 0) <= 64
        or not 1 <= config.get("max_full_tokens", 0) <= 131072
    ):
        raise ValueError("invalid categorical scan config")
    if output_dir.exists() != verify_only:
        raise ValueError("output must be new, or exist for --verify-only")
    pool = json.loads(_pinned_path(config["source_pool"]).read_text())
    if pool.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("wrong source pool schema")
    sources = sorted(pool["sources"], key=lambda row: (row["split"], row["name"]))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        scans = list(executor.map(_scan_source, sources))
    titles: dict[str, tuple[str, str]] = {}
    seen_tables = set()
    jobs, rejected, pages = [], [], []
    for source, snapshot, tables, page_audit in scans:
        pages.extend(page_audit)
        for doc in snapshot["documents"]:
            key = doc["title"].casefold()
            signature = (
                source["split"],
                hashlib.sha256(doc["text"].encode()).hexdigest(),
            )
            previous = titles.setdefault(key, signature)
            if previous != signature:
                raise ValueError(
                    f"same title has different split or text: {doc['title']}"
                )
        for doc, table in tables:
            table_key = (doc["title"].casefold(), table.header, table.header_start)
            if table_key in seen_tables:
                rejected.append(
                    {
                        "source_group": source["name"],
                        "title": doc["title"],
                        "heading": table.heading,
                        "reason": "duplicate_source_table",
                    }
                )
                continue
            seen_tables.add(table_key)
            selections = options(table, max_tasks=config["max_tasks_per_table"])
            if selections:
                jobs.append((source, snapshot, doc, table, selections))
            else:
                rejected.append(
                    {
                        "source_group": source["name"],
                        "title": doc["title"],
                        "heading": table.heading,
                        "reason": "no_repeated_plain_category",
                    }
                )
    with ProcessPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(
                _compile_job, ((job, config["max_full_tokens"]) for job in jobs)
            )
        )
    readers = {"train": [], "eval": []}
    indices, audits = [], []
    for accepted, failures in results:
        rejected.extend(failures)
        for reader, index, audit in accepted:
            readers[index["split"]].append(reader)
            indices.append(index)
            audits.append(audit)
    if len({row["task_id"] for row in indices}) != len(indices):
        raise ValueError("duplicate semantic task IDs")
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
    for name, rows in payloads.items():
        content = "".join(_dump(row) + "\n" for row in rows)
        path = output_dir / name
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"categorical scan replay drift: {name}")
        else:
            path.write_text(content)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "code_sha256": _sha(ROOT / "scripts/p100_wiki_categorical_scan.py"),
        "source_pool": config["source_pool"],
        "source_groups_screened": len(sources),
        "pages_screened": len(pages),
        "tables_structurally_eligible": sum(page["eligible_tables"] for page in pages),
        "tables_with_category_options": len(jobs),
        "candidate_views": len(indices),
        "independent_tasks": len(indices),
        "split_views": dict(sorted(Counter(row["split"] for row in indices).items())),
        "domains": dict(sorted(Counter(row["domain"] for row in indices).items())),
        "topics": dict(sorted(Counter(row["topic"] for row in indices).items())),
        "operations": {"closed_categorical_table_scan": len(indices)},
        "length_bins": dict(
            sorted(
                Counter(
                    "lt32k"
                    if row["full_chat_tokens"] < 32768
                    else "32k"
                    if row["full_chat_tokens"] < 65536
                    else "64k"
                    if row["full_chat_tokens"] < 131072
                    else "128k"
                    for row in indices
                ).items()
            )
        ),
        "rejection_reasons": dict(
            sorted(Counter(row["reason"] for row in rejected).items())
        ),
        "files_sha256": {name: _sha(output_dir / name) for name in payloads},
        "train_ready": False,
    }
    if verify_only:
        if json.loads((output_dir / "manifest.json").read_text()) != manifest:
            raise ValueError("categorical manifest replay drift")
    else:
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return manifest


def _compile_job(args):
    return _compile(*args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
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
