"""Discover Wiki lists from already legal table shapes, then freeze and triage them.

The seed table is a routing signal only. A new page contributes no task until
the existing strict table parser finds a legal complete-set operation in its
own pinned revision. This module never materializes a reader from a title.
"""

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

from longworld.synthesis.p92_generic_table_scan import intervals
from longworld.synthesis.p92_generic_table_scan import parse_tables as years
from longworld.synthesis.wiki_adapter import HttpError, SnapshotError, WikiHttpFetcher
from scripts.freeze_wiki_title_bundle import freeze_titles
from scripts.p100_wiki_categorical_scan import Row, Table, answer, options
from scripts.p100_wiki_categorical_scan import parse_tables as original_categories
from scripts.run_p92_generic_table_scan import _context
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p117-wiki-shape-intake.v1"
LIST_STEM = re.compile(r"^(List of .+?)(?:\s+(?:in|by|of|from|at|on|for)\s+.+)?$")
ENTITY_HEADER = re.compile(r"[A-Z][A-Za-z]*(?:[ -][A-Za-z]+){0,2}\Z")
NONENTITY = frozenset({"rank", "no", "number", "year", "date", "category", "type", "location", "locality", "province", "region", "country", "notes", "reference", "operator", "line", "status", "capacity", "population", "area", "latitude", "longitude", "coordinates"})


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def pin(value: dict[str, str]) -> Path:
    if set(value) != {"path", "sha256"}:
        raise ValueError("source pin requires path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source pin escapes workspace")
    path = (ROOT / relative).resolve(strict=True)
    if not path.is_relative_to(ROOT) and not path.is_relative_to((ROOT / "data").resolve()):
        raise ValueError("source pin escapes workspace storage")
    if sha(path) != value["sha256"]:
        raise ValueError(f"source pin changed: {relative}")
    return path


def stem(title: str) -> str | None:
    match = LIST_STEM.fullmatch(title)
    if match is None:
        return None
    value = match.group(1)
    return value if len(value.split()) >= 4 else None


def entity_header(value: str) -> bool:
    """Accept a singular, plain entity label; exclude measure and qualifier keys."""
    return bool(ENTITY_HEADER.fullmatch(value)) and value.casefold() not in NONENTITY


def entity_tables(text: str) -> tuple[tuple[Table, ...], tuple[dict, ...]]:
    """P117 versioned copy of the P100 complete-table boundary, with generic key."""
    lines = text.splitlines(keepends=True)
    starts = []
    cursor = 0
    for raw in lines:
        starts.append(cursor)
        cursor += len(raw)
    heading = ""
    seen_pipe_in_section = False
    found, rejected = [], []
    visible: Counter[tuple[str, str]] = Counter()
    for index, raw in enumerate(lines):
        header = raw.rstrip("\r\n")
        if header.startswith("## "):
            heading = header[3:]
            seen_pipe_in_section = False
            continue
        if " | " not in header:
            continue
        if seen_pipe_in_section:
            continue
        seen_pipe_in_section = True
        columns = tuple(header.split(" | "))
        if not heading or len(columns) < 3 or not entity_header(columns[0]):
            continue
        visible[heading, header] += 1
        rows = []
        end = index + 1
        failure = None
        while end < len(lines):
            body = lines[end].rstrip("\r\n")
            if not body or body.startswith("#"):
                break
            if body == header or body.split(" | ", 1)[0] == columns[0]:
                failure = "repeated_header_within_section"
                break
            parts = tuple(body.split(" | "))
            if len(parts) != len(columns):
                failure = "row_width_mismatch"
                break
            name = parts[0].strip()
            from longworld.synthesis.p92_generic_table_scan import PLAIN_NAME
            if not PLAIN_NAME.fullmatch(name) or "Cite " in name:
                failure = "ambiguous_name"
                break
            value_starts, value_ends = [], []
            position = starts[end]
            for part in parts:
                leading = len(part) - len(part.lstrip())
                value = part.strip()
                value_starts.append(position + leading)
                value_ends.append(position + leading + len(value))
                position += len(part) + 3
            rows.append(Row(name, tuple(part.strip() for part in parts), tuple(value_starts), tuple(value_ends)))
            end += 1
        if failure is None:
            next_nonblank = end
            while next_nonblank < len(lines) and not lines[next_nonblank].strip():
                next_nonblank += 1
            if next_nonblank < len(lines) and not lines[next_nonblank].startswith("## "):
                failure = "ambiguous_table_end"
            elif len(rows) < 8:
                failure = "too_few_rows"
            elif len({row.name for row in rows}) != len(rows):
                failure = "duplicate_name"
        if failure:
            rejected.append({"heading": heading, "header": header, "reason": failure})
        else:
            found.append(Table(heading, header, columns, starts[index], starts[end] if end < len(lines) else len(text), tuple(rows)))
    unique = []
    for table in found:
        if visible[table.heading, table.header] != 1:
            rejected.append({"heading": table.heading, "header": table.header, "reason": "ambiguous_visible_key"})
        else:
            unique.append(table)
    return tuple(unique), tuple(rejected)


def legal_shape(text: str, max_options: int, *, generic: bool = False) -> tuple[int, int, list[str]]:
    y_tables, y_rejected = years(text)
    c_tables, c_rejected = (entity_tables if generic else original_categories)(text)
    year_cells = sum(len(intervals(table, max_options)) for table in y_tables)
    categorical_cells = sum(len(options(table, max_tasks=max_options)) for table in c_tables)
    rejected = [str(row) for row in y_rejected]
    rejected.extend(row["reason"] for row in c_rejected)
    return year_cells, categorical_cells, rejected


def seeds(pools: list[dict], max_options: int, generic: bool = False) -> tuple[list[dict], set[str]]:
    found: dict[str, dict] = {}
    prior_titles: set[str] = set()
    for pool in pools:
        if pool.get("schema") != "longworld.source-batch-pool.v2":
            raise ValueError("prior Wiki source pool schema differs")
        for source in pool["sources"]:
            snapshot = _snapshot(ROOT, source["snapshot"])
            for doc in snapshot["documents"]:
                prior_titles.add(doc["title"].casefold())
                route = stem(doc["title"])
                if route is None:
                    continue
                y, c, _ = legal_shape(doc["text"], max_options, generic=generic)
                if y + c:
                    table_columns = Counter(
                        table.columns[column]
                        for table in (entity_tables if generic else original_categories)(doc["text"])[0]
                        for column, _ in options(table, max_tasks=max_options)
                    )
                    query_column = table_columns.most_common(1)[0][0] if table_columns else years(doc["text"])[0][0].year_column
                    previous = found.get(route.casefold())
                    row = {"stem": route, "domain": source["domain"], "seed_title": doc["title"], "seed_group": source["name"], "year_cells": y, "categorical_cells": c, **({"query_column": query_column} if generic else {})}
                    if previous is None or (y + c, doc["title"]) > (previous["year_cells"] + previous["categorical_cells"], previous["seed_title"]):
                        found[route.casefold()] = row
    return sorted(found.values(), key=lambda row: (-(row["year_cells"] + row["categorical_cells"]), row["stem"])), prior_titles


def _fetch_group(job: dict) -> dict:
    path = ROOT / job["snapshot_path"]
    try:
        receipt = freeze_titles(WikiHttpFetcher(job["api"]), job["titles"], job["source_group"], path)
    except (HttpError, SnapshotError, OSError, ValueError) as error:
        return {"source_group": job["source_group"], "status": "freeze_failed", "reason": f"{type(error).__name__}:{error}"}
    return {"source_group": job["source_group"], "status": "frozen", "snapshot": {"path": job["snapshot_path"], "sha256": sha(path)}, "revisions": receipt["revisions"], "pages": receipt["pages"], "facts": receipt["facts"], "license": receipt["license"]}


def _triage(source: dict, max_options: int, generic: bool = False) -> tuple[dict, list[dict]]:
    snapshot = _snapshot(ROOT, source["snapshot"])
    rows = []
    for doc in snapshot["documents"]:
        y, c, rejected = legal_shape(doc["text"], max_options, generic=generic)
        rows.append({"source_group": source["name"], "split": source["split"], "doc_id": doc["doc_id"], "title": doc["title"], "revision_url": doc["revision_url"], "body_sha256": hashlib.sha256(doc["text"].encode()).hexdigest(), "year_cells": y, "categorical_cells": c, "reject_reasons": dict(Counter(rejected)), "status": "legal_table_cell" if y + c else "no_legal_l2_table_cell"})
    return source, rows


def run(config_path: Path, output: Path, verify_only: bool = False) -> dict:
    output = (ROOT / output).absolute()
    resolved = output.resolve()
    if not resolved.is_relative_to(ROOT) and not resolved.is_relative_to((ROOT / "data").resolve()):
        raise ValueError("P117 output escapes workspace storage")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config" or not 1 <= config.get("workers", 0) <= 4 or not 1 <= config.get("max_seeds", 0) <= 40 or not 1 <= config.get("titles_per_seed", 0) <= 5 or not 1 <= config.get("search_limit", 0) <= 50 or not 1 <= config.get("max_options_per_table", 0) <= 16:
        raise ValueError("invalid P117 shape-intake config")
    if output.exists() != verify_only:
        raise ValueError("output must be new, or exist for --verify-only")
    pools = [json.loads(pin(item).read_text()) for item in config["prior_pools"]]
    carried = [json.loads(pin(item).read_text()) for item in config.get("carry_frozen_pools", [])]
    generic = config.get("generic_entity_header", False)
    if type(generic) is not bool:
        raise ValueError("generic_entity_header must be boolean")
    selected, prior_titles = seeds(pools, config["max_options_per_table"], generic)
    selected = selected[:config["max_seeds"]]
    if verify_only:
        manifest = json.loads((output / "manifest.json").read_text())
        if manifest["config_sha256"] != sha(config_path) or manifest["seed_routes"] != selected or ("code_sha256" in manifest and manifest["code_sha256"] != sha(Path(__file__))):
            raise ValueError("P117 config or seed route drift")
        frozen_pool = json.loads((output / "source_pool.json").read_text())
        with ProcessPoolExecutor(max_workers=config["workers"]) as executor:
            scans = list(executor.map(_triage, frozen_pool["sources"], [config["max_options_per_table"]] * len(frozen_pool["sources"]), [generic] * len(frozen_pool["sources"])))
        expected_ledger = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for _, rows in scans for row in rows)
        if (output / "shape_ledger.jsonl").read_text() != expected_ledger:
            raise ValueError("P117 shape ledger does not replay from pinned source")
        for row in manifest["discovery"]:
            if "raw_response" in row and sha(ROOT / row["raw_response"]["path"]) != row["raw_response"]["sha256"]:
                raise ValueError("P117 search response drift")
        if sha(output / "source_pool.json") != manifest["source_pool_sha256"] or sha(output / "shape_ledger.jsonl") != manifest["shape_ledger_sha256"]:
            raise ValueError("P117 result drift")
        return manifest
    output.mkdir(parents=True)
    fetcher = WikiHttpFetcher(config.get("api", "https://en.wikipedia.org/w/api.php"))
    jobs, discovery, used = [], [], set(prior_titles)
    for row in selected:
        query = f'intitle:"{row["stem"]}"' + (f' "{row["query_column"]}"' if generic else "")
        try:
            payload = fetcher.get_json({"action": "query", "format": "json", "formatversion": 2, "list": "search", "srsearch": query, "srnamespace": 0, "srlimit": config["search_limit"]})
            raw = output / "discovery" / (hashlib.sha256(query.encode()).hexdigest()[:12] + ".json")
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(dump(payload))
            titles = []
            for item in payload["query"]["search"]:
                title = item["title"]
                if title.casefold().startswith(row["stem"].casefold()) and title.casefold() not in used:
                    titles.append(title)
                    used.add(title.casefold())
                if len(titles) == config["titles_per_seed"]:
                    break
            entry = {"seed": row, "query": query, "raw_response": {"path": str(raw.relative_to(ROOT)), "sha256": sha(raw)}, "novel_titles": titles, "status": "selected" if titles else "no_novel_matching_title"}
            discovery.append(entry)
            if titles:
                digest = hashlib.sha256(row["stem"].casefold().encode()).hexdigest()[:12]
                name = "p117_wiki_" + hashlib.sha256((digest + "|" + "|".join(titles)).encode()).hexdigest()[:12]
                relative = f"{output.relative_to(ROOT)}/snapshots/{name}.json"
                jobs.append({"source_group": name, "stem": row["stem"], "domain": row["domain"], "titles": titles, "snapshot_path": relative, "api": config.get("api", "https://en.wikipedia.org/w/api.php")})
        except (HttpError, SnapshotError, KeyError, TypeError, ValueError) as error:
            discovery.append({"seed": row, "query": query, "status": "search_failed", "reason": f"{type(error).__name__}:{error}"})
    with ProcessPoolExecutor(max_workers=config["workers"]) as executor:
        frozen = list(executor.map(_fetch_group, jobs))
    sources = []
    receipts = {row["source_group"]: row for row in frozen}
    for job in jobs:
        receipt = receipts[job["source_group"]]
        if receipt["status"] != "frozen":
            continue
        split = "eval" if int(hashlib.sha256(job["stem"].encode()).hexdigest()[:8], 16) % 5 == 0 else "train"
        sources.append({"name": job["source_group"], "domain": job["domain"], "topic": re.sub(r"[^a-z0-9]+", "_", job["stem"].lower()).strip("_"), "split": split, "snapshot": receipt["snapshot"]})
    carried_sources = [source for pool in carried for source in pool["sources"]]
    names = Counter(source["name"] for source in carried_sources + sources)
    carried_sources = [
        {**source, "name": source["name"] + "_" + source["snapshot"]["sha256"][:8]}
        if names[source["name"]] > 1 else source
        for source in carried_sources
    ]
    if len({source["name"] for source in carried_sources + sources}) != len(carried_sources + sources):
        raise ValueError("P117 carried/new source group identity repeats")
    all_sources = carried_sources + sources
    with ProcessPoolExecutor(max_workers=config["workers"]) as executor:
        scans = list(executor.map(_triage, all_sources, [config["max_options_per_table"]] * len(all_sources), [generic] * len(all_sources)))
    ledger = [row for _, rows in scans for row in rows]
    pool = {**pools[0], "sources": all_sources}
    (output / "source_pool.json").write_bytes(dump(pool))
    (output / "shape_ledger.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ledger))
    result = {"schema": SCHEMA, "config_sha256": sha(config_path), "code_sha256": sha(Path(__file__)), "input_pools": config["prior_pools"], "carry_frozen_pools": config.get("carry_frozen_pools", []), "seed_routes": selected, "discovery": discovery, "freeze_receipts": frozen, "new_frozen_groups": len(sources), "all_frozen_groups": len(all_sources), "gross_frozen_groups": len(all_sources), "gross_frozen_pages": len(ledger), "legal_l2_year_cells": sum(row["year_cells"] for row in ledger), "legal_l2_categorical_cells": sum(row["categorical_cells"] for row in ledger), "positive_groups": len({row["source_group"] for row in ledger if row["status"] == "legal_table_cell"}), "source_pool_sha256": sha(output / "source_pool.json"), "shape_ledger_sha256": sha(output / "shape_ledger.jsonl"), "train_ready": False}
    (output / "manifest.json").write_bytes(dump(result))
    return result


def compile_candidates(intake: Path, output: Path, *, verify_only: bool = False) -> dict:
    """Compile the bounded categorical cell route from pinned final reader bytes."""
    from longworld.synthesis.length_controller import get_tokenizer
    from scripts.audit_wiki_join_positions import token_span
    from scripts.train_sft import _render_chat, tokenize_assistant_only

    intake = (ROOT / intake).absolute()
    output = (ROOT / output).absolute()
    if output.exists() != verify_only:
        raise ValueError("candidate output must be new, or exist for replay")
    source_manifest = json.loads((intake / "manifest.json").read_text())
    if source_manifest.get("code_sha256") != sha(Path(__file__)):
        raise ValueError("source intake parser code changed")
    pool_path = intake / "source_pool.json"
    if sha(pool_path) != source_manifest["source_pool_sha256"]:
        raise ValueError("source pool differs from pinned intake")
    pool = json.loads(pool_path.read_text())
    tokenizer = get_tokenizer()
    readers = {"train": [], "eval": []}
    indices, audits, decisions = [], [], []
    for source in pool["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        answer_counts = Counter()
        group_kept = 0
        for doc in snapshot["documents"]:
            tables, _ = entity_tables(doc["text"])
            context, doc_start = _context(snapshot, doc)
            for table in tables:
                for column, category in options(table, max_tasks=4):
                    reason = None
                    baseline = answer(table, column, category)
                    answer_text = json.dumps(baseline, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    try:
                        if baseline["count"] > 10 or answer_counts[answer_text] >= 2 or group_kept >= 8:
                            raise ValueError("answer_or_group_concentration_cap")
                        if any(name.casefold() in doc["title"].casefold() for name in baseline["entries"]):
                            raise ValueError("answer_in_page_title")
                        start = doc_start + table.header_start
                        end = doc_start + table.table_end
                        outside = context[:start] + "\n" + context[end:]
                        if any(category in line and any(name in line for name in baseline["entries"]) for line in outside.splitlines()):
                            raise ValueError("same_line_alternate_support")
                        target = next(row for row in table.rows if row.cells[column] != category)
                        left, right = doc_start + target.starts[column], doc_start + target.ends[column]
                        if context[left:right] != target.cells[column]:
                            raise ValueError("reader_cell_offset_drift")
                        alternatives = sorted({row.cells[column] for row in table.rows if row.cells[column] not in (category, target.cells[column])})
                        if not alternatives:
                            raise ValueError("no_nonchanging_control")
                        intervention = {}
                        for label, replacement in (("hit", category), ("control", alternatives[0])):
                            changed = context[:left] + replacement + context[right:]
                            changed_doc = changed[doc_start:doc_start + len(doc["text"]) - (right - left) + len(replacement)]
                            matches = [item for item in entity_tables(changed_doc)[0] if item.heading == table.heading and item.header == table.header and len(item.rows) == len(table.rows)]
                            if len(matches) != 1:
                                raise ValueError("intervention_changed_table_boundary")
                            value = answer(matches[0], column, category)
                            if (label == "hit" and (value["count"] != baseline["count"] + 1 or target.name not in value["entries"])) or (label == "control" and value != baseline):
                                raise ValueError("intervention_did_not_separate_answer")
                            intervention[label] = {"replacement": replacement, "answer": value, "reader_sha256": hashlib.sha256(changed.encode()).hexdigest()}
                        question = f"In the '{table.heading}' table of {doc['title']}, use the '{table.columns[column]}' column. Which entries have exactly '{category}'? Give every matching name and the total count, sorted alphabetically."
                        user = context + "\n\nQUESTION\n" + question
                        messages = [{"role": "user", "content": user}, {"role": "assistant", "content": answer_text}]
                        encoded = tokenize_assistant_only(tokenizer, messages, 131072)
                        full = len(encoded["input_ids"])
                        supervised = sum(label != -100 for label in encoded["labels"])
                        if not supervised or encoded["labels"] != [-100] * (full - supervised) + encoded["input_ids"][full - supervised:]:
                            raise ValueError("assistant_mask_invalid")
                        prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
                        if prompt.count(user) != 1:
                            raise ValueError("ambiguous_user_in_chat")
                        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)["offset_mapping"]
                        base = prompt.index(user) + doc_start
                        evidence = []
                        for row in table.rows:
                            begin, finish = token_span(offsets, base + row.starts[column], base + row.ends[column])
                            evidence.append({"name": row.name, "value": row.cells[column], "selected": row.cells[column] == category, "reader_cell_start": doc_start + row.starts[column], "reader_cell_end": doc_start + row.ends[column], "prompt_token_start": begin, "prompt_token_end": finish})
                        if max(row["prompt_token_end"] for row in evidence) >= full - supervised:
                            raise ValueError("evidence_outside_prompt")
                        digest = hashlib.sha256(f"{snapshot['snapshot_id']}|{doc['doc_id']}|{table.header_start}|{column}|{category}".encode()).hexdigest()[:20]
                        sample_id = "p117-wiki-category-" + digest
                        if sample_id in {row["sample_id"] for row in indices}:
                            raise ValueError("duplicate_semantic_task")
                        readers[source["split"]].append({"sample_id": sample_id, "example_id": sample_id, "quality_status": "research_candidate", "messages": messages})
                        indices.append({"sample_id": sample_id, "example_id": sample_id, "task_id": digest, "source_group": source["name"], "world_id": snapshot["snapshot_id"], "split": source["split"], "source_kind": "real_wiki", "domain": source["domain"], "topic": source["topic"], "operation": "closed_categorical_table_scan", "family": "table_scan", "task_type": "closed_categorical_table_scan", "dependency_status": "bounded_visible_cell_hit_and_control_replay", "tokenizer_profile": "pinned-chat-template", "full_chat_tokens": full, "input_tokens": full - supervised, "supervised_tokens": supervised, "candidate_rows": len(table.rows), "selected_rows": baseline["count"], "evidence_token_extent": max(row["prompt_token_end"] for row in evidence) - min(row["prompt_token_start"] for row in evidence), "query_to_first_evidence_tokens": full - supervised - min(row["prompt_token_start"] for row in evidence), "answer_sha256": hashlib.sha256(answer_text.encode()).hexdigest()})
                        audits.append({"sample_id": sample_id, "question": question, "answer": baseline, "source_doc_id": doc["doc_id"], "source_title": doc["title"], "source_revision": snapshot["source"]["revisions"][doc["title"]], "source_table_heading": table.heading, "source_table_header": table.header, "source_column": table.columns[column], "category": category, "candidate_rows": evidence, "intervention": intervention, "claim_limit": "complete table row scan and bounded cell edits; prose and unparsed alternate support unchecked"})
                        answer_counts[answer_text] += 1
                        group_kept += 1
                    except (ValueError, OverflowError) as error:
                        reason = str(error)
                    decisions.append({"source_group": source["name"], "title": doc["title"], "heading": table.heading, "column": table.columns[column], "category": category, "status": "kept" if reason is None else "rejected", "reason": reason})
    from scripts.audit_unified_reader_mask import audit_reader
    by_reader = {row["sample_id"]: row for split in ("train", "eval") for row in readers[split]}
    for index in indices:
        raw = by_reader[index["sample_id"]]
        audit_reader({"sample_id": raw["sample_id"], "messages": raw["messages"]}, index, tokenizer, 131072)
    payload = {"train.jsonl": readers["train"], "eval.jsonl": readers["eval"], "sample_index.jsonl": indices, "audit.jsonl": audits, "decisions.jsonl": decisions}
    files = {name: "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows).encode() for name, rows in payload.items()}
    manifest = {"schema": SCHEMA + ".candidates", "source_manifest_sha256": sha(intake / "manifest.json"), "code_sha256": sha(Path(__file__)), "candidate_views": len(indices), "independent_tasks": len(indices), "gross_cells": len(decisions), "split_views": dict(Counter(row["split"] for row in indices)), "full_chat_tokens": sum(row["full_chat_tokens"] for row in indices), "supervised_tokens": sum(row["supervised_tokens"] for row in indices), "files_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}, "train_ready": False}
    files["manifest.json"] = dump(manifest)
    if verify_only:
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("P117 candidate output inventory differs")
        for name, content in files.items():
            if (output / name).read_bytes() != content:
                raise ValueError(f"P117 candidate replay differs: {name}")
    else:
        output.mkdir(parents=True)
        for name, content in files.items():
            (output / name).write_bytes(content)
    return manifest


def export_unified(native: Path, output: Path, *, verify_only: bool = False) -> dict:
    """Adapt exactly verified native readers to the shared candidate contract."""
    from longworld.synthesis.unified_candidate_contract import (
        AdapterBinding,
        CandidateLedger,
        normalize_native_candidate,
    )
    from longworld.synthesis.unified_candidate_merge import verify_merge

    native = (ROOT / native).absolute()
    output = (ROOT / output).absolute()
    if output.exists() != verify_only:
        raise ValueError("unified output must be new, or exist for replay")
    manifest_path = native / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != SCHEMA + ".candidates" or manifest.get("code_sha256") != sha(Path(__file__)):
        raise ValueError("native candidate receipt differs")
    for name, digest in manifest["files_sha256"].items():
        if sha(native / name) != digest:
            raise ValueError(f"native candidate file changed: {name}")
    rows = {split: [json.loads(line) for line in (native / f"{split}.jsonl").read_text().splitlines()] for split in ("train", "eval")}
    indices = [json.loads(line) for line in (native / "sample_index.jsonl").read_text().splitlines()]
    by_reader = {row["sample_id"]: (split, offset, row) for split in ("train", "eval") for offset, row in enumerate(rows[split])}
    if len(by_reader) != len(indices) or len({row["sample_id"] for row in indices}) != len(indices):
        raise ValueError("native reader/index inventory differs")
    ledger = CandidateLedger()
    output_rows = {"train": [], "eval": []}
    output_index = []
    lengths = Counter()
    for index in indices:
        sample_id = index["sample_id"]
        split, _, raw = by_reader[sample_id]
        if split != index["split"]:
            raise ValueError("native split differs")
        reader = {"sample_id": sample_id, "messages": raw["messages"]}
        user = reader["messages"][0]["content"]
        marker = "\n\nQUESTION\n"
        if user.count(marker) != 1:
            raise ValueError("reader question boundary differs")
        binding = AdapterBinding(source_kind="real_wiki", source_group=index["source_group"], domain=index["domain"], topic=index["topic"], operation=index["operation"], evidence_profile="complete_visible_categorical_table_rows_replayed", tokenizer_profile="pinned-chat-template", receipt_path=manifest_path, receipt_sha256=sha(manifest_path))
        candidate = normalize_native_candidate(index, reader, binding, context_text=user.split(marker, 1)[0])
        ledger.add(candidate)
        position = len(output_rows[split])
        output_rows[split].append(reader)
        record = candidate.to_dict()
        record.update(source_name="p117_wiki_shape", native_row_ref=f"{native}/{split}.jsonl:{position}", output_file=f"candidate_{split}.jsonl", row_index=position)
        output_index.append(record)
        lengths[candidate.length_bin] += 1
    files = {
        "candidate_train.jsonl": "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in output_rows["train"]).encode(),
        "candidate_eval.jsonl": "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in output_rows["eval"]).encode(),
        "sample_index.jsonl": "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in output_index).encode(),
    }
    result = {"schema_version": "longworld.unified-candidates.v1", "candidate_views": ledger.rows, "source_scoped_semantic_tasks": ledger.independent_tasks, "independent_semantic_tasks": ledger.independent_semantic_tasks, "views_by_lane": {"p117_wiki_shape": ledger.rows}, "splits": {split: len(rows) for split, rows in output_rows.items()}, "length_bins": dict(sorted(lengths.items())), "native_manifest_sha256": sha(manifest_path), "source_manifest_sha256": manifest["source_manifest_sha256"], "files_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}, "train_ready": False}
    files["manifest.json"] = dump(result)
    if verify_only:
        verify_merge(output)
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("unified output inventory differs")
        for name, content in files.items():
            if (output / name).read_bytes() != content:
                raise ValueError(f"unified byte replay differs: {name}")
    else:
        output.mkdir(parents=True)
        for name, content in files.items():
            (output / name).write_bytes(content)
        verify_merge(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--compile-from", type=Path)
    parser.add_argument("--export-native", type=Path)
    args = parser.parse_args()
    if args.export_native:
        value = export_unified(args.export_native, args.output, verify_only=args.verify_only)
        print(json.dumps({key: value[key] for key in ("candidate_views", "independent_semantic_tasks", "length_bins")}, sort_keys=True))
    elif args.compile_from:
        value = compile_candidates(args.compile_from, args.output, verify_only=args.verify_only)
        print(json.dumps({key: value[key] for key in ("candidate_views", "gross_cells", "full_chat_tokens", "supervised_tokens")}, sort_keys=True))
    else:
        value = run(args.config, args.output, verify_only=args.verify_only)
        print(json.dumps({key: value[key] for key in ("gross_frozen_groups", "gross_frozen_pages", "legal_l2_year_cells", "legal_l2_categorical_cells", "positive_groups")}, sort_keys=True))


if __name__ == "__main__":
    main()
