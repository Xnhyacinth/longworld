"""Compile exact numeric interval counts from pinned Wikipedia HTML grids.

Every visible table row is classified. Only unannotated standalone integers
count; source HTML row/column evidence and reader hit/near-miss edits are
checked before a candidate is admitted.
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
from scripts import p126_wiki_html_table_tasks as prior
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p150-wiki-numeric-interval.v1"
QUESTION = "\n\nQUESTION\n"
HEADERS = {
    "dead": "integer_count",
    "injured": "integer_count",
    "year": "calendar_year",
    "date": "calendar_year",
}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_number(value: str, unit: str) -> int | None:
    if not re.fullmatch(r"[0-9]{1,4}", value):
        return None
    number = int(value)
    if unit == "calendar_year" and (len(value) != 4 or not 1800 <= number <= 2100):
        return None
    return number


def answer(context: str, column: int, low: int, high: int, unit: str) -> dict[str, int]:
    header, rows = prior.parse_context(context)
    if not 0 <= column < len(header) or low > high:
        raise ValueError("invalid numeric interval query")
    values = [_parse_number(row[column], unit) for row in rows]
    return {
        "count": sum(value is not None and low <= value <= high for value in values),
        "eligible_rows": sum(value is not None for value in values),
    }


def intervals(
    values: list[int], unit: str, config: dict[str, Any]
) -> list[tuple[int, int, int, int]]:
    if unit == "calendar_year":
        proposed = [(start, start + 9) for start in range(1800, 2101, 10)]
    else:
        proposed = [(start, start + 1) for start in range(min(50, max(values) + 1))]
    accepted = []
    for low, high in proposed:
        hits = sum(low <= value <= high for value in values)
        near = len(values) - hits
        if (
            config["minimum_hits"] <= hits <= config["maximum_hits"]
            and near >= config["minimum_near_misses"]
        ):
            accepted.append((low, high, hits, near))
    return sorted(accepted, key=lambda row: (-min(row[2], row[3]), -row[2], row[0]))


def options(
    entry: dict, html: str, config: dict[str, Any]
) -> tuple[list[dict[str, Any]], Counter[str]]:
    grid = entry["grid"]
    body = grid["rows"][grid["header_rows"] :]
    rejected: Counter[str] = Counter()
    result = []
    for column, path in enumerate(grid["header_paths"]):
        header = " ".join(path).strip().casefold()
        unit = HEADERS.get(header)
        if unit is None:
            continue
        values = []
        bad_support = False
        for row in body:
            cell = grid["origins"][row[column]]
            displayed = prior.clean_cell(cell)
            parsed = _parse_number(displayed, unit)
            if parsed is None:
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
            values.append(parsed)
        if bad_support:
            rejected["ambiguous_visible_numeric_support"] += 1
            continue
        if len(values) < config["minimum_numeric_rows"]:
            rejected["too_few_plain_numeric_rows"] += 1
            continue
        choices = intervals(values, unit, config)
        if not choices:
            rejected["no_nontrivial_interval_with_near_misses"] += 1
            continue
        low, high, hits, near = choices[0]
        result.append(
            {
                "column": column,
                "header": path,
                "unit": unit,
                "low": low,
                "high": high,
                "hits": hits,
                "near_misses": near,
                "eligible_rows": len(values),
            }
        )
    if not result:
        rejected["no_supported_numeric_column"] += 1
    return result, rejected


def _edit(context: str, span: tuple[int, int], old: str, new: str) -> str:
    left, right = span
    if context[left:right] != old or old == new:
        raise ValueError("reader numeric cell offset or edit differs")
    return context[:left] + new + context[right:]


def _alternate_outside(old: int, low: int, high: int, unit: str) -> int:
    floor = 1800 if unit == "calendar_year" else 0
    ceiling = 2100 if unit == "calendar_year" else 9999
    candidates = (old - 1, old + 1, low - 1, high + 1)
    valid = [
        value
        for value in candidates
        if floor <= value <= ceiling and value != old and not low <= value <= high
    ]
    if not valid:
        raise ValueError("no bounded nonchanging numeric control")
    return min(valid, key=lambda value: (abs(value - old), value))


def interventions(context: str, spans: dict, option: dict[str, Any]) -> dict[str, Any]:
    column, low, high, unit = (option[key] for key in ("column", "low", "high", "unit"))
    _, rows = prior.parse_context(context)
    baseline = answer(context, column, low, high, unit)
    values = [
        (index, _parse_number(row[column], unit)) for index, row in enumerate(rows)
    ]
    hits = [
        (index, value)
        for index, value in values
        if value is not None and low <= value <= high
    ]
    misses = [
        (index, value)
        for index, value in values
        if value is not None and not low <= value <= high
    ]
    if not hits or not misses:
        raise ValueError("numeric hit or near-miss missing")
    near_index, near_value = min(
        misses, key=lambda item: (min(abs(item[1] - low), abs(item[1] - high)), item[0])
    )
    hit_index, hit_value = min(
        hits, key=lambda item: (min(abs(item[1] - low), abs(item[1] - high)), item[0])
    )
    replacement = low if near_value < low else high
    hit_changed = _edit(
        context, spans[near_index, column], rows[near_index][column], str(replacement)
    )
    hit_answer = answer(hit_changed, column, low, high, unit)
    control_value = _alternate_outside(near_value, low, high, unit)
    control_changed = _edit(
        context, spans[near_index, column], rows[near_index][column], str(control_value)
    )
    control_answer = answer(control_changed, column, low, high, unit)
    miss_value = _alternate_outside(hit_value, low, high, unit)
    removed = _edit(
        context, spans[hit_index, column], rows[hit_index][column], str(miss_value)
    )
    removed_answer = answer(removed, column, low, high, unit)
    if (
        hit_answer != {**baseline, "count": baseline["count"] + 1}
        or control_answer != baseline
        or removed_answer != {**baseline, "count": baseline["count"] - 1}
    ):
        raise ValueError("reader hit/control/removal intervention differs")
    return {
        "near_miss_row": near_index,
        "near_miss_span": list(spans[near_index, column]),
        "near_miss_original": near_value,
        "hit_edit": replacement,
        "hit_edit_answer": hit_answer,
        "hit_context_sha256": hashlib.sha256(hit_changed.encode()).hexdigest(),
        "control_edit": control_value,
        "control_answer": control_answer,
        "control_context_sha256": hashlib.sha256(control_changed.encode()).hexdigest(),
        "hit_row": hit_index,
        "hit_span": list(spans[hit_index, column]),
        "hit_original": hit_value,
        "removal_edit": miss_value,
        "removal_answer": removed_answer,
        "removal_context_sha256": hashlib.sha256(removed.encode()).hexdigest(),
    }


def compile(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or not 1 <= config.get("minimum_numeric_rows", 0) <= 20
        or not 1 <= config.get("minimum_hits", 0) <= 10
        or not 1 <= config.get("minimum_near_misses", 0) <= 10
        or not 3 <= config.get("maximum_hits", 0) <= 30
        or not 1 <= config.get("max_tasks_per_table", 0) <= 2
        or not 1 <= config.get("max_tasks_per_page", 0) <= 2
        or not 1 <= config.get("max_seq_len", 0) <= 131072
    ):
        raise ValueError("invalid P150 numeric task config")
    source_path = prior.pin(config["source_manifest"])
    pool_path = prior.pin(config["source_pool"])
    grid_path = prior.pin(config["grid_manifest"])
    source = json.loads(source_path.read_text())
    pool = json.loads(pool_path.read_text())
    grid_receipt = json.loads(grid_path.read_text())
    if (
        source.get("schema") != "longworld.p122-wiki-html-grid.v2.source"
        or pool.get("schema") != "longworld.source-batch-pool.v2"
        or grid_receipt.get("schema") != "longworld.p122-wiki-html-grid.v2.grid"
        or grid_receipt["source_manifest_sha256"] != _sha(source_path)
        or grid_receipt["files_sha256"]["valid_grids.jsonl"]
        != _sha(grid_path.parent / "valid_grids.jsonl")
    ):
        raise ValueError("P148 source and grid pins disagree")
    source_pages = {row["title"]: row for row in source["records"]}
    labels = prior.pool_labels(source["records"], pool["sources"])
    entries = [
        json.loads(raw)
        for raw in (grid_path.parent / "valid_grids.jsonl").read_text().splitlines()
    ]
    if len(entries) != grid_receipt["grid_valid_tables"]:
        raise ValueError("P148 valid grid inventory differs")
    output = (ROOT / output).absolute()
    if output.exists() != verify_only:
        raise ValueError("P150 output must be new or existing for byte replay")
    tokenizer = get_tokenizer()
    ledger = CandidateLedger()
    readers = {"train": [], "eval": []}
    indexes, audits, decisions = [], [], []
    per_page = Counter()
    source_pages_with_options = set()
    supported_grids = 0
    for entry in entries:
        page = source_pages[entry["title"]]
        html_path = prior.pin(
            {"path": page["html_path"], "sha256": page["html_sha256"]}
        )
        html = html_path.read_text()
        if (entry["split"], entry["oldid"], entry["html_sha256"]) != (
            page["split"],
            page["oldid"],
            page["html_sha256"],
        ):
            raise ValueError("grid/source identity differs")
        for cell in entry["grid"]["origins"].values():
            left, right = cell["html_span"]
            if (
                hashlib.sha256(html[left:right].encode()).hexdigest()
                != cell["html_sha256"]
            ):
                raise ValueError("source HTML cell span changed")
        choices, rejected = options(entry, html, config)
        if choices:
            supported_grids += 1
            source_pages_with_options.add(entry["title"])
        if not choices:
            decisions.append(
                {
                    "table_id": entry["table_id"],
                    "title": entry["title"],
                    "status": "unsupported",
                    "reasons": dict(sorted(rejected.items())),
                }
            )
            continue
        context, spans = prior.render_context(entry)
        table_count = 0
        for choice in choices:
            reason = None
            if (
                table_count >= config["max_tasks_per_table"]
                or per_page[entry["title"]] >= config["max_tasks_per_page"]
            ):
                reason = "table_or_page_task_cap"
            else:
                try:
                    column = choice["column"]
                    low, high, unit = choice["low"], choice["high"], choice["unit"]
                    gold = answer(context, column, low, high, unit)
                    if gold != {
                        "count": choice["hits"],
                        "eligible_rows": choice["eligible_rows"],
                    }:
                        raise ValueError(
                            "reader numeric answer differs from source grid"
                        )
                    changed = interventions(context, spans, choice)
                    label = " / ".join(choice["header"])
                    statement = (
                        f"In the displayed table from {entry['title']!r}, count every row "
                        f"whose {label!r} cell is a plain, unannotated "
                        f"{'four-digit calendar year' if unit == 'calendar_year' else 'integer'} "
                        f"between {low} and {high}, inclusive. Blanks, ranges, notes, "
                        "approximate and annotated values do not qualify. "
                        'Return JSON with keys "count" and "eligible_rows"; the latter '
                        "counts all rows whose chosen cell is a qualifying plain integer."
                    )
                    user = context + QUESTION + statement
                    answer_text = _dump(gold)
                    messages = [
                        {"role": "user", "content": user},
                        {"role": "assistant", "content": answer_text},
                    ]
                    encoded = tokenize_assistant_only(
                        tokenizer, messages, config["max_seq_len"]
                    )
                    ids, mask = encoded["input_ids"], encoded["labels"]
                    supervised = sum(value != -100 for value in mask)
                    if (
                        not supervised
                        or mask
                        != [-100] * (len(ids) - supervised)
                        + ids[len(ids) - supervised :]
                    ):
                        raise ValueError("assistant-only mask differs")
                    prompt = _render_chat(
                        tokenizer, messages[:1], generation_prompt=True
                    )
                    if prompt.count(user) != 1:
                        raise ValueError("user table is not unique in final chat")
                    prompt_start = prompt.index(user)
                    offsets = tokenizer(
                        prompt, truncation=False, return_offsets_mapping=True
                    )["offset_mapping"]
                    evidence = []
                    body = entry["grid"]["rows"][entry["grid"]["header_rows"] :]
                    for row_index, row in enumerate(body):
                        cell = entry["grid"]["origins"][row[column]]
                        visible = prior.clean_cell(cell)
                        left, right = spans[row_index, column]
                        if context[left:right] != visible:
                            raise ValueError("reader cell offset differs")
                        token_bounds = (
                            list(
                                token_span(
                                    offsets, prompt_start + left, prompt_start + right
                                )
                            )
                            if left < right
                            else None
                        )
                        value = _parse_number(visible, unit)
                        evidence.append(
                            {
                                "row": row_index,
                                "value": visible,
                                "parsed_integer": value,
                                "selected": value is not None and low <= value <= high,
                                "reader_char_span": [left, right],
                                "reader_token_span": token_bounds,
                                "source_html_span": cell["html_span"],
                                "source_html_sha256": cell["html_sha256"],
                            }
                        )
                    if (
                        max(
                            row["reader_token_span"][1]
                            for row in evidence
                            if row["reader_token_span"] is not None
                        )
                        >= len(ids) - supervised
                    ):
                        raise ValueError("numeric evidence outside final prompt")
                    semantic = hashlib.sha256(
                        _dump(
                            {
                                "table": entry["table_id"],
                                "column": column,
                                "low": low,
                                "high": high,
                            }
                        ).encode()
                    ).hexdigest()[:24]
                    sample_id = "p150-wiki-numeric-" + semantic
                    reader = {"sample_id": sample_id, "messages": messages}
                    raw_index = {
                        "sample_id": sample_id,
                        "task_id": semantic,
                        "source_kind": "real_wiki",
                        "source_group": labels[entry["title"]]["source_group"],
                        "domain": entry["domain"],
                        "topic": labels[entry["title"]]["topic"],
                        "operation": "html_numeric_interval_count",
                        "split": entry["split"],
                        "dependency_status": "bounded_full_table_row_scan_hit_removal_and_near_miss_edits",
                        "tokenizer_profile": "pinned-chat-template",
                        "full_chat_tokens": len(ids),
                        "input_tokens": len(ids) - supervised,
                        "supervised_tokens": supervised,
                        "answer_sha256": _answer_hash(answer_text),
                    }
                    audit_reader(reader, raw_index, tokenizer, config["max_seq_len"])
                    binding = AdapterBinding(
                        source_kind="real_wiki",
                        source_group=raw_index["source_group"],
                        domain=raw_index["domain"],
                        topic=raw_index["topic"],
                        operation=raw_index["operation"],
                        evidence_profile="p122_html_grid_numeric_row_header_unit",
                        tokenizer_profile="pinned-chat-template",
                        receipt_path=grid_path,
                        receipt_sha256=_sha(grid_path),
                    )
                    candidate = normalize_native_candidate(
                        raw_index, reader, binding, context_text=context
                    )
                    ledger.add(candidate)
                    split = candidate.split
                    position = len(readers[split])
                    readers[split].append(reader)
                    index = candidate.to_dict()
                    index.update(
                        source_name="p150_wiki_html_numeric_interval",
                        native_row_ref=f"{grid_path.parent / 'valid_grids.jsonl'}:{entry['table_id']}",
                        output_file=f"candidate_{split}.jsonl",
                        row_index=position,
                    )
                    indexes.append(index)
                    audits.append(
                        {
                            "sample_id": sample_id,
                            "table_id": entry["table_id"],
                            "source_title": entry["title"],
                            "source_oldid": entry["oldid"],
                            "source_html_sha256": entry["html_sha256"],
                            "header": choice["header"],
                            "unit": unit,
                            "interval": [low, high],
                            "answer": gold,
                            "candidate_rows": evidence,
                            "interventions": changed,
                            "bounded_scope": "all displayed table rows checked; outside-table alternatives not searched",
                        }
                    )
                    per_page[entry["title"]] += 1
                    table_count += 1
                except (ValueError, OverflowError) as error:
                    reason = str(error)
            decisions.append(
                {
                    "table_id": entry["table_id"],
                    "title": entry["title"],
                    "header": choice["header"],
                    "unit": choice["unit"],
                    "interval": [choice["low"], choice["high"]],
                    "status": "admitted" if reason is None else "rejected",
                    "reason": reason,
                }
            )
    files = {
        "candidate_train.jsonl": "".join(
            _dump(row) + "\n" for row in readers["train"]
        ).encode(),
        "candidate_eval.jsonl": "".join(
            _dump(row) + "\n" for row in readers["eval"]
        ).encode(),
        "sample_index.jsonl": "".join(_dump(row) + "\n" for row in indexes).encode(),
        "audit.jsonl": "".join(_dump(row) + "\n" for row in audits).encode(),
        "decision_ledger.jsonl": "".join(
            _dump(row) + "\n" for row in decisions
        ).encode(),
    }
    reasons = Counter(row["reason"] for row in decisions if row.get("reason"))
    reasons.update(reason for row in decisions for reason in row.get("reasons", {}))
    result = {
        "schema_version": "longworld.unified-candidates.v1",
        "p150_schema": SCHEMA,
        "train_ready": False,
        "code_sha256": _sha(Path(__file__)),
        "config_sha256": _sha(config_path),
        "source_manifest_sha256": _sha(source_path),
        "source_pool_sha256": _sha(pool_path),
        "grid_manifest_sha256": _sha(grid_path),
        "gross_grids": len(entries),
        "numeric_supported_grids": supported_grids,
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "views_by_lane": {"p150_wiki_html_numeric_interval": ledger.rows},
        "splits": {split: len(readers[split]) for split in readers},
        "source_pages_with_options": len(source_pages_with_options),
        "source_pages_with_tasks": len(per_page),
        "domains_with_tasks": dict(
            sorted(Counter(row["domain"] for row in indexes).items())
        ),
        "topics_with_tasks": dict(
            sorted(Counter(row["topic"] for row in indexes).items())
        ),
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in indexes).items())
        ),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in indexes),
        "supervised_tokens": sum(row["supervised_tokens"] for row in indexes),
        "rejection_reasons": dict(sorted(reasons.items())),
        "files_sha256": {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        },
        "claim_limit": "plain visible numeric table interval count with full row scan and bounded cell edits; no full-page alternative proof or long model gain",
    }
    files["manifest.json"] = (
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    if verify_only:
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("P150 output inventory differs")
        for name, data in files.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"P150 byte replay differs: {name}")
        verify_merge(output)
    else:
        output.mkdir(parents=True)
        for name, data in files.items():
            (output / name).write_bytes(data)
        verify_merge(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/p150_wiki_numeric_interval_v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/candidates/p150_wiki_numeric_interval_v1",
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(compile(args.config, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
