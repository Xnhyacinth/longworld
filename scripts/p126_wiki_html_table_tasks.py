"""Compile bounded complete-set QA from pinned, semantically screened HTML grids.

The input is P122's source-shape audit. This compiler independently evaluates
every visible row and a pair of reader-text cell edits before admitting a QA.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    _answer_hash,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p126-wiki-html-table-tasks.v1"
QUESTION = "\n\nQUESTION\n"
GEOGRAPHY = re.compile(r"\b(?:city|town|state|country|region|district|neighbou?rhood|borough|province|location|rank|number|no\.)\b", re.IGNORECASE)
GEOGRAPHIC_LIST = re.compile(r"\b(?:cities|towns|states|countries|regions|districts|neighbou?rhoods|boroughs|provinces|locations)\b", re.IGNORECASE)
BAD_TARGET = re.compile(r"\b(?:rank|no\.|number|year|date|capacity|beds?|staff|population|ratio|enrolment|attendance|ref|citation|note|image|photo|acronym|summary|description|area|mw|mwh)\b", re.IGNORECASE)
TITLE_STOP = {"list", "of", "the", "largest", "current", "australian", "football", "league", "by", "named", "after", "people", "and", "in", "for"}
PLAIN = re.compile(r"[^\t\n|{}\[\]<>]{2,120}\Z")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pin(value: dict[str, str]) -> Path:
    if set(value) != {"path", "sha256"}:
        raise ValueError("input pin needs path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "data":
        raise ValueError("input pin must be workspace data path")
    path = ROOT / relative
    if not path.is_file() or sha(path) != value["sha256"]:
        raise ValueError(f"input pin differs: {relative}")
    return path


def singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ses") and len(word) > 5:
        return word[:-2]
    return word[:-1] if word.endswith("s") and len(word) > 4 else word


def identity_header(title: str, path: list[str]) -> bool:
    """Infer an entity column from the list title, never from a domain label."""
    text = " ".join(path).casefold()
    if not text or GEOGRAPHY.search(text) or BAD_TARGET.search(text) or GEOGRAPHIC_LIST.search(title):
        return False
    if re.search(r"\bname\b", text):
        return True
    title_words = {singular(word) for word in re.findall(r"[a-z]+", title.casefold()) if word not in TITLE_STOP}
    return any(word in title_words for word in re.findall(r"[a-z]+", text) if len(word) >= 4)


def clean_cell(cell: dict) -> str:
    text = cell["text"].replace("\t", " ").replace("\n", " ⏎ ").strip()
    suffix = " ".join(f"[{ref}]" for ref in cell["footnote_refs"])
    return (text + (" " + suffix if suffix else "")).strip()


def selected_cell_ok(cell: dict, html: str, *, key: bool) -> bool:
    text = cell["text"]
    left, right = cell["html_span"]
    source = html[left:right].casefold()
    return not (
        not text
        or "\n" in text
        or "\t" in text
        or "|" in text
        or len(text) > (120 if key else 80)
        or not re.search(r"[A-Za-z]", text)
        or cell["rowspan"] != 1
        or cell["colspan"] != 1
        or cell["footnote_refs"]
        or cell["citation_markers"]
        or "mw-collapsible" in source
        or "aria-hidden" in source
        or "visibility:hidden" in source.replace(" ", "")
        or (key and not PLAIN.fullmatch(text))
    )


def column_options(grid: dict, html: str, title: str) -> tuple[list[tuple[int, int, str]], Counter[str]]:
    rows = grid["rows"][grid["header_rows"] :]
    origins = grid["origins"]
    choices = []
    reasons: Counter[str] = Counter()
    for key in range(grid["width"]):
        if not identity_header(title, grid["header_paths"][key]):
            continue
        key_cells = [origins[row[key]] for row in rows]
        names = [cell["text"] for cell in key_cells]
        if any(not selected_cell_ok(cell, html, key=True) for cell in key_cells):
            reasons["key_cell_ambiguous_or_qualified"] += 1
            continue
        if len({name.casefold() for name in names}) != len(names):
            reasons["duplicate_entity_key"] += 1
            continue
        for target in range(grid["width"]):
            if target == key or BAD_TARGET.search(" ".join(grid["header_paths"][target])):
                continue
            cells = [origins[row[target]] for row in rows]
            if any(not selected_cell_ok(cell, html, key=False) for cell in cells):
                reasons["target_cell_missing_multiline_spanned_or_qualified"] += 1
                continue
            values = [cell["text"] for cell in cells]
            counts = Counter(values)
            if len(counts) < 3:
                reasons["no_nonchanging_control_category"] += 1
                continue
            admitted = [
                value
                for value, count in counts.items()
                if 2 <= count <= min(15, len(rows) - 2)
            ]
            if not admitted:
                reasons["no_nontrivial_complete_set"] += 1
                continue
            for category in sorted(admitted, key=lambda value: (counts[value], value)):
                choices.append((key, target, category))
    if not choices:
        reasons["no_semantically_legal_key_target_pair"] += 1
    return choices, reasons


def render_context(entry: dict) -> tuple[str, dict[tuple[int, int], tuple[int, int]]]:
    """Render a normal table and footnotes; keep exact visible cell char spans."""
    grid = entry["grid"]
    parts, spans = [], {}
    length = 0

    def append(text: str) -> None:
        nonlocal length
        parts.append(text)
        length += len(text)

    append(f"Wikipedia article: {entry['title']}\nRevision: {entry['revision_url']}\n")
    if grid["section_path"]:
        append("Section: " + " / ".join(grid["section_path"]) + "\n")
    if grid["caption"]:
        append("Caption: " + grid["caption"] + "\n")
    append("TABLE_START\n")
    append("\t".join(" / ".join(path) for path in grid["header_paths"]) + "\n")
    footnotes = {}
    for row_index, row in enumerate(grid["rows"][grid["header_rows"] :]):
        for column, origin in enumerate(row):
            if column:
                append("\t")
            cell = grid["origins"][origin]
            visible = clean_cell(cell)
            spans[row_index, column] = (length, length + len(visible))
            append(visible)
            footnotes.update(cell["footnotes"])
        append("\n")
    append("TABLE_END\n")
    if footnotes:
        append("Footnotes:\n")
        for ref, note in sorted(footnotes.items()):
            append(f"[{ref}] {note.replace(chr(10), ' ')}\n")
    return "".join(parts).rstrip("\n"), spans


def parse_context(context: str) -> tuple[list[str], list[list[str]]]:
    if context.count("\nTABLE_START\n") != 1 or context.count("\nTABLE_END") != 1:
        raise ValueError("reader table boundary ambiguous")
    table = context.split("\nTABLE_START\n", 1)[1].split("\nTABLE_END", 1)[0]
    lines = table.splitlines()
    if len(lines) < 9:
        raise ValueError("reader table row universe too small")
    header = lines[0].split("\t")
    body = [line.split("\t") for line in lines[1:]]
    if any(len(row) != len(header) for row in body):
        raise ValueError("reader table row width changed")
    return header, body


def answer_from_reader(context: str, key: int, target: int, category: str) -> dict:
    _, body = parse_context(context)
    names = [row[key] for row in body if row[target] == category]
    if not 2 <= len(names) <= min(15, len(body) - 2):
        raise ValueError("degenerate reader answer")
    if len(set(names)) != len(names):
        raise ValueError("reader answer duplicates an entity")
    return {"count": len(names), "entries": sorted(names)}


def intervention(context: str, spans: dict, key: int, target: int, category: str) -> dict:
    _, body = parse_context(context)
    baseline = answer_from_reader(context, key, target, category)
    values = sorted({row[target] for row in body if row[target] != category})
    if len(values) < 2:
        raise ValueError("no distinct nonchanging target value")
    row_index = next(index for index, row in enumerate(body) if row[target] == values[0])
    left, right = spans[row_index, target]
    if context[left:right] != values[0]:
        raise ValueError("reader target span changed")
    changed = context[:left] + category + context[right:]
    hit = answer_from_reader(changed, key, target, category)
    if hit["count"] != baseline["count"] + 1 or body[row_index][key] not in hit["entries"]:
        raise ValueError("reader hit intervention did not change membership")
    control = context[:left] + values[1] + context[right:]
    if answer_from_reader(control, key, target, category) != baseline:
        raise ValueError("reader control intervention changed answer")
    return {
        "row_index": row_index,
        "reader_target_span": [left, right],
        "old_value": values[0],
        "hit_value": category,
        "hit_answer": hit,
        "hit_context_sha256": digest(changed),
        "control_value": values[1],
        "control_answer": baseline,
        "control_context_sha256": digest(control),
    }


def compile(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or not 1 <= config.get("max_tasks_per_table", 0) <= 4
        or not 1 <= config.get("max_tasks_per_page", 0) <= 8
        or not 1 <= config.get("max_seq_len", 0) <= 262144
    ):
        raise ValueError("invalid P126 config")
    source_manifest = pin(config["source_manifest"])
    grid_manifest = pin(config["grid_manifest"])
    source = json.loads(source_manifest.read_text())
    grid_receipt = json.loads(grid_manifest.read_text())
    if (
        source.get("schema") != "longworld.p122-wiki-html-grid.v2.source"
        or grid_receipt.get("schema") != "longworld.p122-wiki-html-grid.v2.grid"
        or grid_receipt["source_manifest_sha256"] != sha(source_manifest)
        or grid_receipt["files_sha256"]["valid_grids.jsonl"] != sha(grid_manifest.parent / "valid_grids.jsonl")
    ):
        raise ValueError("P122 source/grid receipts disagree")
    source_pages = {row["title"]: row for row in source["records"]}
    entries = [json.loads(raw) for raw in (grid_manifest.parent / "valid_grids.jsonl").read_text().splitlines()]
    if len(entries) != grid_receipt["grid_valid_tables"]:
        raise ValueError("valid grid inventory changed")
    output = (ROOT / output).absolute()
    if output.exists() != verify_only:
        raise ValueError("P126 output must be new or existing for replay")
    tokenizer = get_tokenizer()
    ledger = CandidateLedger()
    readers = {"train": [], "eval": []}
    indexes, audits, decisions = [], [], []
    by_page = Counter()
    page_answer_sets: dict[str, set[tuple[str, ...]]] = {}
    supported_tables = 0
    for entry in entries:
        page = source_pages[entry["title"]]
        html_path = pin({"path": page["html_path"], "sha256": page["html_sha256"]})
        html = html_path.read_text()
        if (entry["split"], entry["oldid"], entry["html_sha256"]) != (page["split"], page["oldid"], page["html_sha256"]):
            raise ValueError("grid/source identity differs")
        for cell in entry["grid"]["origins"].values():
            left, right = cell["html_span"]
            if digest(html[left:right]) != cell["html_sha256"]:
                raise ValueError("grid source HTML cell span differs")
        options, rejected = column_options(entry["grid"], html, entry["title"])
        if options:
            supported_tables += 1
        if not options:
            decisions.append({"table_id": entry["table_id"], "title": entry["title"], "split": entry["split"], "status": "unsupported", "reasons": dict(sorted(rejected.items()))})
            continue
        context, spans = render_context(entry)
        seen_pairs = set()
        table_count = 0
        for key, target, category in options:
            pair = (key, target)
            if pair in seen_pairs:
                continue  # At most one answer value per key/target semantics.
            reason = None
            if table_count >= config["max_tasks_per_table"] or by_page[entry["title"]] >= config["max_tasks_per_page"]:
                reason = "table_or_page_task_cap"
                seen_pairs.add(pair)
            else:
                try:
                    gold = answer_from_reader(context, key, target, category)
                    answer_set = tuple(gold["entries"])
                    if answer_set in page_answer_sets.get(entry["title"], set()):
                        raise ValueError("duplicate_answer_set_in_page")
                    edited = intervention(context, spans, key, target, category)
                    question = (
                        f"In the table from {entry['title']!r}, which entries in the "
                        f"{' / '.join(entry['grid']['header_paths'][key])!r} column have "
                        f"exactly {category!r} in the {' / '.join(entry['grid']['header_paths'][target])!r} column? "
                        "List every matching entry and the total count, sorted alphabetically, as JSON."
                    )
                    user = context + QUESTION + question
                    answer = dump(gold)
                    messages = [{"role": "user", "content": user}, {"role": "assistant", "content": answer}]
                    encoded = tokenize_assistant_only(tokenizer, messages, config["max_seq_len"])
                    full = len(encoded["input_ids"])
                    supervised = sum(value != -100 for value in encoded["labels"])
                    if not supervised or encoded["labels"] != [-100] * (full - supervised) + encoded["input_ids"][full - supervised:]:
                        raise ValueError("assistant mask invalid")
                    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
                    if prompt.count(user) != 1:
                        raise ValueError("reader user text is not unique in chat")
                    offset_map = tokenizer(prompt, truncation=False, return_offsets_mapping=True)["offset_mapping"]
                    prompt_start = prompt.index(user)
                    evidence = []
                    for row_index, row in enumerate(entry["grid"]["rows"][entry["grid"]["header_rows"] :]):
                        key_cell = entry["grid"]["origins"][row[key]]
                        target_cell = entry["grid"]["origins"][row[target]]
                        key_start, key_end = spans[row_index, key]
                        value_start, value_end = spans[row_index, target]
                        if context[key_start:key_end] != key_cell["text"] or context[value_start:value_end] != target_cell["text"]:
                            raise ValueError("reader cell value/offset differs")
                        token_start, token_end = token_span(offset_map, prompt_start + value_start, prompt_start + value_end)
                        evidence.append({"row": row_index, "entity": key_cell["text"], "value": target_cell["text"], "selected": target_cell["text"] == category, "key_char_span": [key_start, key_end], "target_char_span": [value_start, value_end], "target_token_span": [token_start, token_end], "source_key_html_sha256": key_cell["html_sha256"], "source_value_html_sha256": target_cell["html_sha256"]})
                    if max(row["target_token_span"][1] for row in evidence) >= full - supervised:
                        raise ValueError("evidence outside final prompt")
                    semantic_id = digest(dump({"table": entry["table_id"], "key": key, "target": target, "category": category}))[:24]
                    sample_id = "p126-wiki-table-" + semantic_id
                    reader = {"sample_id": sample_id, "messages": messages}
                    raw_index = {"sample_id": sample_id, "task_id": semantic_id, "source_kind": "real_wiki", "source_group": json.loads(pin(page["snapshot"]).read_text())["snapshot_id"], "domain": entry["domain"], "topic": re.sub(r"[^a-z0-9]+", "_", entry["title"].casefold()).strip("_")[:48], "operation": "html_complete_categorical_table_scan", "split": entry["split"], "dependency_status": "bounded_full_reader_row_scan_and_hit_control_edit", "tokenizer_profile": "pinned-chat-template", "full_chat_tokens": full, "input_tokens": full - supervised, "supervised_tokens": supervised, "answer_sha256": _answer_hash(answer)}
                    audit_reader(reader, raw_index, tokenizer, config["max_seq_len"])
                    binding = AdapterBinding(source_kind="real_wiki", source_group=raw_index["source_group"], domain=raw_index["domain"], topic=raw_index["topic"], operation=raw_index["operation"], evidence_profile="p122_complete_html_grid_reader", tokenizer_profile="pinned-chat-template", receipt_path=grid_manifest, receipt_sha256=sha(grid_manifest))
                    candidate = normalize_native_candidate(raw_index, reader, binding, context_text=context)
                    ledger.add(candidate)
                    split = candidate.split
                    position = len(readers[split])
                    readers[split].append(reader)
                    index = candidate.to_dict()
                    index.update(source_name="p126_wiki_html_table_scan", native_row_ref=f"{grid_manifest.parent / 'valid_grids.jsonl'}:{entry['table_id']}", output_file=f"candidate_{split}.jsonl", row_index=position)
                    indexes.append(index)
                    audits.append({"sample_id": sample_id, "table_id": entry["table_id"], "source_title": entry["title"], "source_oldid": entry["oldid"], "source_html_sha256": entry["html_sha256"], "key_header": entry["grid"]["header_paths"][key], "target_header": entry["grid"]["header_paths"][target], "target_category": category, "answer": gold, "all_candidate_rows": evidence, "intervention": edited, "claim_limit": "full visible table scan and one bounded reader-text hit/control; equivalent support outside table not exhaustively checked"})
                    page_answer_sets.setdefault(entry["title"], set()).add(answer_set)
                    seen_pairs.add(pair)
                    by_page[entry["title"]] += 1
                    table_count += 1
                except (ValueError, OverflowError) as error:
                    reason = str(error)
            decisions.append({"table_id": entry["table_id"], "title": entry["title"], "split": entry["split"], "key": key, "target": target, "category": category, "status": "admitted" if reason is None else "rejected", "reason": reason})
    files = {"candidate_train.jsonl": "".join(dump(row) + "\n" for row in readers["train"]).encode(), "candidate_eval.jsonl": "".join(dump(row) + "\n" for row in readers["eval"]).encode(), "sample_index.jsonl": "".join(dump(row) + "\n" for row in indexes).encode(), "audit.jsonl": "".join(dump(row) + "\n" for row in audits).encode(), "decision_ledger.jsonl": "".join(dump(row) + "\n" for row in decisions).encode()}
    reasons = Counter(row["reason"] for row in decisions if row.get("reason"))
    reasons.update(reason for row in decisions for reason in row.get("reasons", {}))
    result = {"schema_version": "longworld.unified-candidates.v1", "p126_schema": SCHEMA, "code_sha256": sha(Path(__file__)), "config_sha256": sha(config_path), "source_manifest_sha256": sha(source_manifest), "grid_manifest_sha256": sha(grid_manifest), "gross_grids": len(entries), "key_target_supported_grids": supported_tables, "candidate_views": ledger.rows, "source_scoped_semantic_tasks": ledger.independent_tasks, "independent_semantic_tasks": ledger.independent_semantic_tasks, "views_by_lane": {"p126_wiki_html_table_scan": ledger.rows}, "splits": {split: len(readers[split]) for split in ("train", "eval")}, "source_pages_with_tasks": len(by_page), "domains_with_tasks": dict(sorted(Counter(row["domain"] for row in indexes).items())), "length_bins": dict(sorted(Counter(row["length_bin"] for row in indexes).items())), "full_chat_tokens": sum(row["full_chat_tokens"] for row in indexes), "supervised_tokens": sum(row["supervised_tokens"] for row in indexes), "rejection_reasons": dict(sorted(reasons.items())), "files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}, "claim_limit": "structured HTML table complete scan and bounded reader-cell edits; no full-page prose alternative-proof or long-distance/model-gain claim", "train_ready": False}
    files["manifest.json"] = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if verify_only:
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("P126 output inventory differs")
        for name, data in files.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"P126 byte replay differs: {name}")
        verify_merge(output)
    else:
        output.mkdir(parents=True)
        for name, data in files.items():
            (output / name).write_bytes(data)
        verify_merge(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = compile(args.config, args.output, verify_only=args.verify_only)
    print(json.dumps({key: result[key] for key in ("gross_grids", "key_target_supported_grids", "candidate_views", "splits", "rejection_reasons")}, sort_keys=True))


if __name__ == "__main__":
    main()
