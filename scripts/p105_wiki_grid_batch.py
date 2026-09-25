"""Compile bounded complete-set readers from P104 exact-revision Wiki grids."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_wiki_join_positions import token_span
from scripts.p100_wiki_categorical_scan import (
    _plain_value,
    answer,
    options,
    parse_tables,
)
from scripts.p104_wiki_table_grid import parse_tables as parse_raw_tables
from scripts.p105_wiki_reader_cells import render_table, replace_in_document
from scripts.run_p92_generic_table_scan import _context, _dump, _pinned_path, _sha
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p105-wiki-grid-category.v1"


def _plain_label(value: str) -> bool:
    return (
        1 <= len(value) <= 100
        and not any(mark in value for mark in ("|", "{", "}", "[", "]", "<", ">", "\\"))
        and not value.casefold().startswith("cite ")
        and not re.search(r"\b(?:cite|coord|lang|abbr)\s*[:|]", value, re.IGNORECASE)
    )


def _validate_lineage(
    config: dict, ledger: dict, raw_manifest: dict, gate: dict
) -> None:
    if (
        ledger.get("raw_manifest_sha256") != config["raw_manifest"]["sha256"]
        or raw_manifest.get("source_pool_sha256") != config["source_pool"]["sha256"]
        or gate.get("gated_source_pool_sha256") != config["source_pool"]["sha256"]
        or not gate.get("prior_router", {}).get("sha256")
        or not gate.get("prior_pool", {}).get("sha256")
    ):
        raise ValueError("P104/P97/P95/P92 source lineage pin mismatch")


def _source_for(job: dict, raw: dict, source: dict) -> tuple[dict, dict, str, dict]:
    snapshot = json.loads(_pinned_path(source["snapshot"]).read_text())
    document = next(
        doc for doc in snapshot["documents"] if doc["doc_id"] == job["doc_id"]
    )
    if (
        document["title"] != job["title"]
        or document["revision_url"] != raw["revision_url"]
        or document["page_url"] != raw["page_url"]
        or source["name"] != raw["source_group"]
        or source["split"] != raw["split"]
        or source["snapshot"]["sha256"] != raw["source_snapshot_sha256"]
    ):
        raise ValueError("source document/revision identity drift")
    response = json.loads(
        _pinned_path(
            {
                "path": "data/candidates/p104_wiki_width_raw_v1/"
                + raw["response_path"],
                "sha256": raw["response_sha256"],
            }
        ).read_text()
    )
    page = response["query"]["pages"][0]
    revision = page["revisions"][0]
    if page["title"] != job["title"] or revision["revid"] != job["revid"]:
        raise ValueError("raw Wiki revision identity drift")
    return snapshot, document, revision["slots"]["main"]["content"], page


def _final_table(text: str, heading: str):
    tables, rejected = parse_tables("## " + heading + "\n" + text + "\n\n")
    if len(tables) != 1 or rejected:
        reason = rejected[0]["reason"] if rejected else "not_one_table"
        raise ValueError(f"final reader table rejected: {reason}")
    return tables[0]


def _replay(
    context: str,
    table_start: int,
    table_end: int,
    cell_start: int,
    cell_end: int,
    replacement: str,
    heading: str,
    column: int,
    category: str,
) -> tuple[dict, str]:
    changed = context[:cell_start] + replacement + context[cell_end:]
    shift = len(replacement) - (cell_end - cell_start)
    parsed = _final_table(changed[table_start : table_end + shift], heading)
    return answer(parsed, column, category), hashlib.sha256(
        changed.encode()
    ).hexdigest()


def _intervention(
    context: str,
    table_start: int,
    table_end: int,
    visible,
    parsed,
    column: int,
    category: str,
) -> dict:
    baseline = answer(parsed, column, category)
    alternates = sorted(
        {
            row.cells[column]
            for row in parsed.rows
            if row.cells[column] != category and _plain_value(row.cells[column])
        }
    )
    if len(alternates) < 2:
        raise ValueError("no distinct nonchanging control category")
    target_index = next(
        index for index, row in enumerate(parsed.rows) if row.cells[column] != category
    )
    cell = visible.cells[target_index + 1][column]
    old_value = parsed.rows[target_index].cells[column]
    left = table_start + cell.reader_start
    right = table_start + cell.reader_end
    if context[left:right] != old_value:
        raise ValueError("reader intervention cell drift")
    hit, hit_sha = _replay(
        context,
        table_start,
        table_end,
        left,
        right,
        category,
        parsed.heading,
        column,
        category,
    )
    if (
        hit["count"] != baseline["count"] + 1
        or parsed.rows[target_index].name not in hit["entries"]
    ):
        raise ValueError("hit edit failed to change complete-set answer")
    control_value = next(value for value in alternates if value != old_value)
    control, control_sha = _replay(
        context,
        table_start,
        table_end,
        left,
        right,
        control_value,
        parsed.heading,
        column,
        category,
    )
    if control != baseline:
        raise ValueError("control edit changed answer")
    return {
        "changed_name": parsed.rows[target_index].name,
        "reader_cell_start": left,
        "reader_cell_end": right,
        "old_value": old_value,
        "hit_value": category,
        "hit_answer": hit,
        "hit_reader_sha256": hit_sha,
        "control_value": control_value,
        "control_answer": control,
        "control_reader_sha256": control_sha,
    }


def _compile_one(args: tuple) -> tuple[list, list]:
    job, raw, source, max_tokens, max_tasks = args
    snapshot, document, wikitext, _page = _source_for(job, raw, source)
    rejected = []
    try:
        raw_table = next(
            item
            for item in parse_raw_tables(wikitext)
            if item.source_start_line == job["raw_start_lines"][0]
        )
        if raw_table.grid is None:
            raise ValueError("raw grid became invalid")
        visible = render_table(
            wikitext, raw_table.source_start_line, raw_table.source_end_line
        )
        new_text, table_doc_start, table_doc_end = replace_in_document(
            title=job["title"],
            wikitext=wikitext,
            frozen_reader_text=document["text"],
            table=visible,
        )
        parsed = _final_table(visible.text, job["heading"])
        if len(parsed.rows) + 1 != len(visible.cells):
            raise ValueError("reader table row count differs from source grid")
        for row_index, row in enumerate(parsed.rows, 1):
            if tuple(cell.value for cell in visible.cells[row_index]) != row.cells:
                raise ValueError("reader table cell values differ from parsed source")
        selections = options(parsed, max_tasks=max_tasks)
        if not selections:
            raise ValueError("no strict repeated categorical option")
    except (ValueError, StopIteration) as error:
        return [], [
            {"doc_id": job["doc_id"], "heading": job["heading"], "reason": str(error)}
        ]

    changed_doc = dict(document, text=new_text)
    changed_snapshot = dict(
        snapshot,
        documents=[
            changed_doc if doc["doc_id"] == document["doc_id"] else doc
            for doc in snapshot["documents"]
        ],
    )
    context, doc_start = _context(changed_snapshot, changed_doc)
    table_start = doc_start + table_doc_start
    table_end = doc_start + table_doc_end
    if context[table_start:table_end] != visible.text:
        raise ValueError("final reader table context offset drift")
    tokenizer = get_tokenizer()
    accepted = []
    for column, category in selections:
        try:
            baseline = answer(parsed, column, category)
            if (
                not _plain_label(parsed.columns[column])
                or not _plain_label(category)
                or any(not _plain_label(name) for name in baseline["entries"])
            ):
                raise ValueError("unresolved markup in header/category/gold")
            outside = context[:table_start] + "\n" + context[table_end:]
            selected = set(baseline["entries"])
            if any(
                category in line and any(name in line for name in selected)
                for line in outside.splitlines()
            ):
                raise ValueError("same-line alternative support outside target table")
            intervention = _intervention(
                context, table_start, table_end, visible, parsed, column, category
            )
            question = (
                f"In the '{job['heading']}' table of {job['title']}, which entries "
                f"have exactly '{category}' in the '{parsed.columns[column]}' column? "
                "List every matching name and the total count, sorted alphabetically."
            )
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
                raise ValueError("assistant-only mask differs from suffix")
            prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
            if prompt.count(user) != 1:
                raise ValueError("ambiguous user occurrence in final chat")
            offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
                "offset_mapping"
            ]
            user_start = prompt.index(user)
            evidence = []
            for row_index, row in enumerate(parsed.rows, 1):
                name_cell = visible.cells[row_index][0]
                value_cell = visible.cells[row_index][column]
                if name_cell.value != row.name or value_cell.value != row.cells[column]:
                    raise ValueError("source-to-reader evidence cell mismatch")
                left = table_start + value_cell.reader_start
                right = table_start + value_cell.reader_end
                name_left = table_start + name_cell.reader_start
                name_right = table_start + name_cell.reader_end
                if (
                    context[left:right] != value_cell.value
                    or context[name_left:name_right] != row.name
                ):
                    raise ValueError("final reader evidence span drift")
                start_token, end_token = token_span(
                    offsets, user_start + left, user_start + right
                )
                name_start_token, name_end_token = token_span(
                    offsets, user_start + name_left, user_start + name_right
                )
                evidence.append(
                    {
                        "name": row.name,
                        "value": value_cell.value,
                        "selected": value_cell.value == category,
                        "source_name_span": [name_cell.raw_start, name_cell.raw_end],
                        "source_value_span": [value_cell.raw_start, value_cell.raw_end],
                        "reader_name_span": [name_left, name_right],
                        "reader_value_span": [left, right],
                        "prompt_name_token_span": [name_start_token, name_end_token],
                        "prompt_value_token_span": [start_token, end_token],
                    }
                )
            if (
                max(item["prompt_value_token_span"][1] for item in evidence)
                >= full - supervised
            ):
                raise ValueError("evidence outside user prompt")
            digest = hashlib.sha256(
                f"{job['source_group']}|{job['doc_id']}|{job['revid']}|{raw_table.source_start_line}|{column}|{category}".encode()
            ).hexdigest()[:20]
            sample_id = "p105-wiki-grid-" + digest
            index = {
                "sample_id": sample_id,
                "example_id": sample_id,
                "task_id": digest,
                "world_id": snapshot["snapshot_id"],
                "source_group": job["source_group"],
                "split": source["split"],
                "source_kind": "real_wiki",
                "evidence_profile": "wikitext_grid_repaired",
                "domain": source["domain"],
                "topic": source["topic"],
                "operation": "closed_categorical_table_scan",
                "family": "table_scan",
                "task_type": "closed_categorical_table_scan",
                "dependency_status": "raw_to_final_reader_cells_plus_bounded_hit_control",
                "question_style": "explicit_header_complete_set",
                "tokenizer_profile": "pinned-chat-template",
                "full_chat_tokens": full,
                "input_tokens": full - supervised,
                "supervised_tokens": supervised,
                "candidate_rows": len(parsed.rows),
                "selected_rows": baseline["count"],
                "evidence_token_extent": max(
                    item["prompt_value_token_span"][1] for item in evidence
                )
                - min(item["prompt_value_token_span"][0] for item in evidence),
                "answer_sha256": hashlib.sha256(_dump(baseline).encode()).hexdigest(),
            }
            audit = {
                "sample_id": sample_id,
                "source_doc_id": job["doc_id"],
                "source_title": job["title"],
                "source_revision": job["revid"],
                "source_raw_response_sha256": raw["response_sha256"],
                "source_snapshot_sha256": source["snapshot"]["sha256"],
                "source_table_start_line": raw_table.source_start_line,
                "source_table_end_line": raw_table.source_end_line,
                "source_table_heading": job["heading"],
                "final_reader_sha256": hashlib.sha256(context.encode()).hexdigest(),
                "reader_table_span": [table_start, table_end],
                "reader_table_sha256": hashlib.sha256(
                    visible.text.encode()
                ).hexdigest(),
                "source_column": parsed.columns[column],
                "category": category,
                "question": question,
                "answer": baseline,
                "candidate_rows": evidence,
                "intervention": intervention,
                "claim_limit": "complete source-grid rows and bounded final-reader cell edits; unparsed prose alternatives unchecked",
            }
            reader = {
                "sample_id": sample_id,
                "example_id": sample_id,
                "quality_status": "research_candidate",
                "messages": messages,
            }
            accepted.append((reader, index, audit))
        except (ValueError, OverflowError) as error:
            rejected.append(
                {
                    "doc_id": job["doc_id"],
                    "heading": job["heading"],
                    "column": parsed.columns[column],
                    "category": category,
                    "reason": str(error),
                }
            )
    return accepted, rejected


def run(
    config_path: Path, output_dir: Path, *, workers: int, verify_only: bool
) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not 1 <= workers <= 4
        or not 1 <= config.get("max_tasks_per_table", 0) <= 8
        or not 1 <= config.get("max_full_tokens", 0) <= 131072
    ):
        raise ValueError("invalid P105 grid batch config")
    if output_dir.exists() != verify_only:
        raise ValueError("output must be new, or exist for --verify-only")
    ledger = json.loads(_pinned_path(config["target_ledger"]).read_text())
    raw_manifest = json.loads(_pinned_path(config["raw_manifest"]).read_text())
    pool = json.loads(_pinned_path(config["source_pool"]).read_text())
    gate = json.loads(_pinned_path(config["source_gate"]).read_text())
    _validate_lineage(config, ledger, raw_manifest, gate)
    if ledger["reader_tasks_admitted"] != 0 or raw_manifest["failed_pages"]:
        raise ValueError("P104 pinned support/freeze state differs")
    raw_by_doc = {row["doc_id"]: row for row in raw_manifest["records"]}
    source_by_name = {row["name"]: row for row in pool["sources"]}
    jobs = [
        (
            row,
            raw_by_doc[row["doc_id"]],
            source_by_name[row["source_group"]],
            config["max_full_tokens"],
            config["max_tasks_per_table"],
        )
        for row in ledger["ledger"]
        if row["status"] == "grid_candidate_needs_visible_alignment"
    ]
    if any(row[0]["source_group"] not in gate["accepted_groups"] for row in jobs):
        raise ValueError("P105 source group absent from P97 global split gate")
    with ProcessPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(_compile_one, jobs))
    readers = {"train": [], "eval": []}
    indices, audits, rejections = [], [], []
    for accepted, rejected in results:
        rejections.extend(rejected)
        for reader, index, audit in accepted:
            readers[index["split"]].append(reader)
            indices.append(index)
            audits.append(audit)
    if len({row["task_id"] for row in indices}) != len(indices):
        raise ValueError("duplicate P105 semantic task IDs")
    payloads = {
        "train.jsonl": readers["train"],
        "eval.jsonl": readers["eval"],
        "sample_index.jsonl": indices,
        "audit.jsonl": audits,
        "rejected.jsonl": rejections,
    }
    if not verify_only:
        output_dir.mkdir(parents=True)
    for filename, rows in payloads.items():
        content = "".join(_dump(row) + "\n" for row in rows)
        path = output_dir / filename
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"P105 native replay drift: {filename}")
        else:
            path.write_text(content)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "code_sha256": {
            name: _sha(ROOT / "scripts" / name)
            for name in ("p105_wiki_grid_batch.py", "p105_wiki_reader_cells.py")
        },
        "source_pins": config,
        "source_supported_tables": len(jobs),
        "candidate_views": len(indices),
        "independent_tasks": len(indices),
        "split_views": dict(sorted(Counter(row["split"] for row in indices).items())),
        "domains": dict(sorted(Counter(row["domain"] for row in indices).items())),
        "topics": dict(sorted(Counter(row["topic"] for row in indices).items())),
        "operations": dict(
            sorted(Counter(row["operation"] for row in indices).items())
        ),
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
            sorted(Counter(row["reason"] for row in rejections).items())
        ),
        "files_sha256": {name: _sha(output_dir / name) for name in payloads},
        "train_ready": False,
    }
    manifest_path = output_dir / "manifest.json"
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if verify_only:
        if manifest_path.read_text() != content:
            raise ValueError("P105 manifest replay drift")
    else:
        manifest_path.write_text(content)
    return manifest


def main() -> None:
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


if __name__ == "__main__":
    main()
