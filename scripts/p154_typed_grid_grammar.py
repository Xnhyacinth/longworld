"""Compile typed numeric tasks from frozen Wikipedia HTML grids.

The grammar is independent of domain labels: column headers determine the
numeric type, and each operation is executed on all displayed table rows.
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
from scripts import p126_wiki_html_table_tasks as table
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p154-typed-grid-grammar.v1"
YEAR = re.compile(r"(?:year|built|opened|commissioned)\Z")
CAPACITY = re.compile(r"capacity(?:\s*\(mw\)|\s*/\s*mw|\s*mw)?\Z")
INTEGER = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,6})\Z")


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def column_type(path: list[str]) -> str | None:
    header = " ".join(" ".join(path).casefold().split())
    if YEAR.fullmatch(header):
        return "calendar_year"
    if CAPACITY.fullmatch(header):
        return "capacity_integer"
    return None


def parse_number(raw: str, kind: str) -> int | None:
    if not INTEGER.fullmatch(raw):
        return None
    value = int(raw.replace(",", ""))
    if kind == "calendar_year":
        return value if len(raw) == 4 and 1800 <= value <= 2100 else None
    if kind == "capacity_integer":
        return value if 0 < value <= 999999 else None
    raise ValueError("unknown numeric type")


def evaluate(
    context: str, column: int, kind: str, low: int, high: int, operation: str
) -> dict[str, int]:
    header, rows = table.parse_context(context)
    if not 0 <= column < len(header) or low >= high:
        raise ValueError("invalid typed grid query")
    values = [parse_number(row[column], kind) for row in rows]
    selected = [value for value in values if value is not None and low <= value <= high]
    if operation == "interval_count":
        return {"count": len(selected), "eligible_rows": sum(x is not None for x in values)}
    if operation == "interval_sum":
        if kind != "capacity_integer":
            raise ValueError("only capacity columns support interval sum")
        return {"sum": sum(selected), "count": len(selected)}
    raise ValueError("unknown numeric operation")


def intervals(values: list[int], minimum_hits: int) -> list[tuple[int, int]]:
    distinct = sorted(set(values))
    if len(distinct) < 4:
        return []
    candidates = []
    for start in (0, len(distinct) // 4, len(distinct) // 2):
        width = max(2, len(distinct) // 3)
        end = min(len(distinct) - 1, start + width)
        low, high = distinct[start], distinct[end]
        hits = sum(low <= x <= high for x in values)
        if low < high and minimum_hits <= hits <= len(values) - minimum_hits:
            candidates.append((low, high))
    return list(dict.fromkeys(candidates))


def choices(entry: dict, html: str, config: dict) -> tuple[list[dict], Counter[str]]:
    grid = entry["grid"]
    body = grid["rows"][grid["header_rows"] :]
    good = []
    rejects: Counter[str] = Counter()
    for column, header in enumerate(grid["header_paths"]):
        kind = column_type(header)
        if kind is None:
            continue
        values = []
        bad_support = False
        for row in body:
            cell = grid["origins"][row[column]]
            visible = table.clean_cell(cell)
            number = parse_number(visible, kind)
            if number is None:
                continue
            left, right = cell["html_span"]
            source = html[left:right].casefold()
            if (
                cell["rowspan"] != 1
                or cell["colspan"] != 1
                or cell["footnote_refs"]
                or cell["citation_markers"]
                or "aria-hidden" in source
                or "mw-collapsible" in source
                or "visibility:hidden" in source.replace(" ", "")
            ):
                bad_support = True
                break
            values.append(number)
        if bad_support:
            rejects["ambiguous_visible_numeric_support"] += 1
            continue
        if len(values) < config["minimum_numeric_rows"]:
            rejects["too_few_plain_numeric_rows"] += 1
            continue
        candidates = intervals(values, config["minimum_hits"])
        if not candidates:
            rejects["no_nontrivial_numeric_interval"] += 1
            continue
        for low, high in candidates[: config["intervals_per_column"]]:
            for operation in config["operators_by_type"][kind]:
                good.append(
                    {
                        "column": column,
                        "header": header,
                        "kind": kind,
                        "low": low,
                        "high": high,
                        "operation": operation,
                    }
                )
    return good, rejects


def edit(context: str, span: tuple[int, int], old: str, new: int) -> str:
    left, right = span
    replacement = str(new)
    if context[left:right] != old or old == replacement:
        raise ValueError("reader cell span or replacement differs")
    return context[:left] + replacement + context[right:]


def outside(value: int, low: int, high: int, kind: str) -> int:
    floor, ceiling = (1800, 2100) if kind == "calendar_year" else (1, 999999)
    options = [low - 1, high + 1, value - 1, value + 1]
    valid = [x for x in options if floor <= x <= ceiling and x != value and not low <= x <= high]
    if not valid:
        raise ValueError("no valid outside interval edit")
    return min(valid, key=lambda x: (abs(x - value), x))


def interventions(context: str, spans: dict, spec: dict) -> dict:
    column, kind, low, high, operation = (
        spec[key] for key in ("column", "kind", "low", "high", "operation")
    )
    _, rows = table.parse_context(context)
    original = evaluate(context, column, kind, low, high, operation)
    parsed = [(i, parse_number(row[column], kind)) for i, row in enumerate(rows)]
    hits = [(i, x) for i, x in parsed if x is not None and low <= x <= high]
    misses = [(i, x) for i, x in parsed if x is not None and not low <= x <= high]
    if len(hits) < 2 or len(misses) < 2:
        raise ValueError("no sufficient hit or near-miss rows")
    near_i, near = min(misses, key=lambda p: (min(abs(p[1] - low), abs(p[1] - high)), p[0]))
    hit_i, hit = hits[0]
    incoming = low if near < low else high
    edited_hit = edit(context, spans[near_i, column], rows[near_i][column], incoming)
    removal_value = outside(hit, low, high, kind)
    removed = edit(context, spans[hit_i, column], rows[hit_i][column], removal_value)
    control_value = outside(near, low, high, kind)
    control = edit(context, spans[near_i, column], rows[near_i][column], control_value)
    hit_answer = evaluate(edited_hit, column, kind, low, high, operation)
    removed_answer = evaluate(removed, column, kind, low, high, operation)
    control_answer = evaluate(control, column, kind, low, high, operation)
    field = "count" if operation == "interval_count" else "sum"
    delta = 1 if operation == "interval_count" else incoming
    removed_delta = 1 if operation == "interval_count" else hit
    if (
        hit_answer[field] != original[field] + delta
        or removed_answer[field] != original[field] - removed_delta
        or control_answer != original
    ):
        raise ValueError("reader hit/removal/control dependence differs")
    if operation == "interval_sum":
        within = low if hit != low else high
        changed = edit(context, spans[hit_i, column], rows[hit_i][column], within)
        changed_answer = evaluate(changed, column, kind, low, high, operation)
        if changed_answer != {"sum": original["sum"] + within - hit, "count": original["count"]}:
            raise ValueError("within-interval value dependence differs")
    else:
        within = None
        changed_answer = None
    return {
        "hit_row": hit_i,
        "near_miss_row": near_i,
        "original": original,
        "near_miss_original": rows[near_i][column],
        "hit_original": rows[hit_i][column],
        "incoming_value": incoming,
        "incoming_answer": hit_answer,
        "removal_value": removal_value,
        "removal_answer": removed_answer,
        "control_value": control_value,
        "control_answer": control_answer,
        "within_interval_value": within,
        "within_interval_answer": changed_answer,
        "reader_edit_spans": [list(spans[near_i, column]), list(spans[hit_i, column])],
        "reader_context_sha256": [
            hashlib.sha256(text.encode()).hexdigest() for text in (edited_hit, removed, control)
        ],
    }


def _source_rows(campaign: Path, pool: Path) -> list[tuple[dict, dict, Path, dict]]:
    campaign_data = json.loads(campaign.read_text())
    pool_data = json.loads(pool.read_text())
    ledger_path = campaign.parent / "chunk_ledger.jsonl"
    if sha(ledger_path) != campaign_data["chunk_ledger_sha256"]:
        raise ValueError("campaign chunk ledger changed")
    chunks = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    records = []
    entries = []
    for chunk in chunks:
        directory = campaign.parent / "chunks" / chunk["chunk"]
        source_path = directory / "source/source_manifest.json"
        grid_path = directory / "grid/manifest.json"
        if sha(source_path) != chunk["source_manifest_sha256"] or sha(grid_path) != chunk["grid_manifest_sha256"]:
            raise ValueError("campaign child receipt changed")
        source = json.loads(source_path.read_text())
        grid = json.loads(grid_path.read_text())
        valid_path = grid_path.parent / "valid_grids.jsonl"
        if sha(valid_path) != grid["files_sha256"]["valid_grids.jsonl"]:
            raise ValueError("campaign valid grid changed")
        records.extend(source["records"])
        pages = {row["title"]: row for row in source["records"]}
        for raw in valid_path.read_text().splitlines():
            row = json.loads(raw)
            entries.append((row, pages[row["title"]], grid_path, grid))
    if len(entries) != campaign_data["valid_grids"]:
        raise ValueError("campaign grid count changed")
    labels = table.pool_labels(records, pool_data["sources"])
    return [(row, page, receipt, labels[row["title"]]) for row, page, receipt, _ in entries]


def compile(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("operators_by_type")
        != {"calendar_year": ["interval_count"], "capacity_integer": ["interval_count", "interval_sum"]}
        or not 8 <= config.get("minimum_numeric_rows", 0) <= 30
        or not 2 <= config.get("minimum_hits", 0) <= 10
        or not 1 <= config.get("intervals_per_column", 0) <= 3
        or not 1 <= config.get("max_tasks_per_page", 0) <= 20
        or not 1 <= config.get("max_tasks_per_table", 0) <= 10
        or not 1 <= config.get("max_seq_len", 0) <= 131072
    ):
        raise ValueError("invalid P154 typed task grammar config")
    campaign = table.pin(config["campaign_manifest"])
    pool = table.pin(config["source_pool"])
    entries = _source_rows(campaign, pool)
    output = (ROOT / output).absolute()
    if output.exists() != verify_only:
        raise ValueError("P154 output must be new or existing for replay")
    tokenizer = get_tokenizer()
    ledger = CandidateLedger()
    readers = {"train": [], "eval": []}
    indexes, audits, decisions = [], [], []
    per_page: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for entry, page, receipt, labels in entries:
        html_path = table.pin({"path": page["html_path"], "sha256": page["html_sha256"]})
        html = html_path.read_text()
        if any(entry[key] != page[key] for key in ("title", "oldid", "html_sha256", "domain", "split", "revision_url")):
            raise ValueError("grid/source page identity differs")
        for cell in entry["grid"]["origins"].values():
            left, right = cell["html_span"]
            if hashlib.sha256(html[left:right].encode()).hexdigest() != cell["html_sha256"]:
                raise ValueError("source HTML origin cell changed")
        specs, rejected = choices(entry, html, config)
        reasons.update(rejected)
        context, spans = table.render_context(entry)
        admitted_table = 0
        for spec in specs:
            reason = None
            if admitted_table >= config["max_tasks_per_table"] or per_page[entry["title"]] >= config["max_tasks_per_page"]:
                reason = "table_or_page_task_cap"
            else:
                try:
                    column, kind, low, high, operation = (
                        spec[key] for key in ("column", "kind", "low", "high", "operation")
                    )
                    gold = evaluate(context, column, kind, low, high, operation)
                    if gold["count"] < config["minimum_hits"]:
                        raise ValueError("reader query degenerated")
                    edits = interventions(context, spans, spec)
                    label = " / ".join(spec["header"])
                    directive = "count the rows" if operation == "interval_count" else "sum the displayed numeric values"
                    answer_keys = '"count" and "eligible_rows"' if operation == "interval_count" else '"sum" and "count"'
                    statement = (
                        f"In the displayed table from {entry['title']!r}, {directive} whose {label!r} "
                        f"cell is a plain, unannotated {'four-digit calendar year' if kind == 'calendar_year' else 'positive integer'} "
                        f"between {low} and {high}, inclusive. Interpret commas only as thousands separators. "
                        "Exclude blanks, ranges, notes, approximate and annotated values. "
                        f"Return JSON with keys {answer_keys}."
                    )
                    user = context + "\n\nQUESTION\n" + statement
                    answer_text = dump(gold)
                    messages = [{"role": "user", "content": user}, {"role": "assistant", "content": answer_text}]
                    encoded = tokenize_assistant_only(tokenizer, messages, config["max_seq_len"])
                    ids, mask = encoded["input_ids"], encoded["labels"]
                    supervised = sum(value != -100 for value in mask)
                    if not supervised or mask != [-100] * (len(ids) - supervised) + ids[len(ids) - supervised :]:
                        raise ValueError("assistant-only mask differs")
                    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
                    if prompt.count(user) != 1:
                        raise ValueError("reader table is not unique in final chat")
                    prompt_start = prompt.index(user)
                    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)["offset_mapping"]
                    evidence = []
                    body = entry["grid"]["rows"][entry["grid"]["header_rows"] :]
                    for row_index, row in enumerate(body):
                        cell = entry["grid"]["origins"][row[column]]
                        visible = table.clean_cell(cell)
                        left, right = spans[row_index, column]
                        if context[left:right] != visible:
                            raise ValueError("reader cell span differs")
                        token_bounds = list(token_span(offsets, prompt_start + left, prompt_start + right)) if left < right else None
                        parsed = parse_number(visible, kind)
                        evidence.append({
                            "row": row_index,
                            "value": visible,
                            "parsed_integer": parsed,
                            "selected": parsed is not None and low <= parsed <= high,
                            "reader_char_span": [left, right],
                            "reader_token_span": token_bounds,
                            "source_html_span": cell["html_span"],
                            "source_html_sha256": cell["html_sha256"],
                        })
                    valid_bounds = [row["reader_token_span"][1] for row in evidence if row["reader_token_span"] is not None]
                    if not valid_bounds or max(valid_bounds) >= len(ids) - supervised:
                        raise ValueError("numeric evidence outside final prompt")
                    question_start = prompt_start + len(context) + len("\n\nQUESTION\n")
                    question_token = token_span(offsets, question_start, question_start + len(statement))[0]
                    first_evidence = min(row["reader_token_span"][0] for row in evidence if row["reader_token_span"] is not None)
                    last_evidence = max(valid_bounds)
                    if not first_evidence < last_evidence < question_token:
                        raise ValueError("final evidence and question token order differs")
                    semantic = hashlib.sha256(dump({"table": entry["table_id"], **spec}).encode()).hexdigest()[:24]
                    sample_id = "p154-grid-" + semantic
                    reader = {"sample_id": sample_id, "messages": messages}
                    raw_index = {
                        "sample_id": sample_id, "task_id": semantic,
                        "source_kind": "real_wiki", "source_group": labels["source_group"],
                        "domain": entry["domain"], "topic": labels["topic"],
                        "operation": "html_typed_" + operation, "split": entry["split"],
                        "dependency_status": "bounded_full_table_row_scan_and_numeric_cell_edits",
                        "tokenizer_profile": "pinned-chat-template",
                        "full_chat_tokens": len(ids), "input_tokens": len(ids) - supervised,
                        "supervised_tokens": supervised, "answer_sha256": _answer_hash(answer_text),
                    }
                    audit_reader(reader, raw_index, tokenizer, config["max_seq_len"])
                    binding = AdapterBinding(
                        source_kind="real_wiki", source_group=labels["source_group"],
                        domain=entry["domain"], topic=labels["topic"],
                        operation=raw_index["operation"], evidence_profile="p122_html_grid_typed_numeric_row_header_unit",
                        tokenizer_profile="pinned-chat-template", receipt_path=receipt, receipt_sha256=sha(receipt),
                    )
                    candidate = normalize_native_candidate(raw_index, reader, binding, context_text=context)
                    ledger.add(candidate)
                    split = candidate.split
                    position = len(readers[split])
                    readers[split].append(reader)
                    index = candidate.to_dict()
                    index.update(source_name="p154_typed_grid_grammar", native_row_ref=f"{receipt.parent / 'valid_grids.jsonl'}:{entry['table_id']}", output_file=f"candidate_{split}.jsonl", row_index=position)
                    indexes.append(index)
                    audits.append({
                        "sample_id": sample_id, "table_id": entry["table_id"],
                        "source_title": entry["title"], "source_oldid": entry["oldid"],
                        "source_html_sha256": entry["html_sha256"], "spec": spec,
                        "answer": gold, "candidate_rows": evidence, "interventions": edits,
                        "final_chat_positions": {
                            "first_evidence_token": first_evidence,
                            "last_evidence_token": last_evidence,
                            "question_start_token": question_token,
                            "first_evidence_to_question_tokens": question_token - first_evidence,
                            "last_evidence_to_question_tokens": question_token - last_evidence,
                        },
                        "bounded_scope": "all displayed table rows; outside-table alternatives not searched",
                    })
                    per_page[entry["title"]] += 1
                    admitted_table += 1
                except (ValueError, OverflowError) as error:
                    reason = str(error)
            decisions.append({"table_id": entry["table_id"], "title": entry["title"], "spec": spec, "status": "admitted" if reason is None else "rejected", "reason": reason})
            if reason:
                reasons[reason] += 1
    files = {
        "candidate_train.jsonl": "".join(dump(row) + "\n" for row in readers["train"]).encode(),
        "candidate_eval.jsonl": "".join(dump(row) + "\n" for row in readers["eval"]).encode(),
        "sample_index.jsonl": "".join(dump(row) + "\n" for row in indexes).encode(),
        "audit.jsonl": "".join(dump(row) + "\n" for row in audits).encode(),
        "decision_ledger.jsonl": "".join(dump(row) + "\n" for row in decisions).encode(),
    }
    manifest = {
        "schema_version": "longworld.unified-candidates.v1", "p154_schema": SCHEMA,
        "train_ready": False, "code_sha256": sha(Path(__file__)), "config_sha256": sha(config_path),
        "campaign_manifest_sha256": sha(campaign), "source_pool_sha256": sha(pool),
        "gross_grids": len(entries), "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "views_by_lane": {"p154_typed_grid_grammar": ledger.rows},
        "splits": {name: len(rows) for name, rows in readers.items()},
        "source_pages_with_tasks": len(per_page),
        "domains_with_tasks": dict(sorted(Counter(row["domain"] for row in indexes).items())),
        "topics_with_tasks": dict(sorted(Counter(row["topic"] for row in indexes).items())),
        "operations": dict(sorted(Counter(row["operation"] for row in indexes).items())),
        "length_bins": dict(sorted(Counter(row["length_bin"] for row in indexes).items())),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in indexes),
        "supervised_tokens": sum(row["supervised_tokens"] for row in indexes),
        "max_first_evidence_to_question_tokens": max((row["final_chat_positions"]["first_evidence_to_question_tokens"] for row in audits), default=0),
        "rejection_reasons": dict(sorted(reasons.items())),
        "files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        "claim_limit": "typed numeric row count/sum from frozen real grids with bounded reader cell interventions; no full-page alternative proof or long model gain",
    }
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if verify_only:
        if {p.name for p in output.iterdir()} != set(files):
            raise ValueError("P154 output inventory differs")
        for name, data in files.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"P154 byte replay differs: {name}")
    else:
        output.mkdir(parents=True)
        for name, data in files.items():
            (output / name).write_bytes(data)
    verify_merge(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/p154_typed_grid_grammar_v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/candidates/p154_typed_grid_grammar_v1")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(dump(compile(args.config, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
