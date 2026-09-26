"""Recount P154 final readers and independently replay source/edit/mask gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts import p126_wiki_html_table_tasks as tables
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.train_sft import _render_chat

PLAIN = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,6})\Z")


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recount(context: str, spec: dict) -> dict[str, int]:
    _, body = tables.parse_context(context)
    column, kind, low, high, operation = (
        spec[key] for key in ("column", "kind", "low", "high", "operation")
    )
    eligible = []
    for row in body:
        raw = row[column]
        if not PLAIN.fullmatch(raw):
            continue
        value = int(raw.replace(",", ""))
        if kind == "calendar_year":
            if len(raw) != 4 or not 1800 <= value <= 2100:
                continue
        elif kind == "capacity_integer":
            if not 0 < value <= 999999:
                continue
        else:
            raise ValueError("unknown reader numeric kind")
        eligible.append(value)
    selected = [x for x in eligible if low <= x <= high]
    if operation == "interval_count":
        return {"count": len(selected), "eligible_rows": len(eligible)}
    if operation == "interval_sum" and kind == "capacity_integer":
        return {"sum": sum(selected), "count": len(selected)}
    raise ValueError("unknown reader operation")


def changed(context: str, span: list[int], old: str, new: int) -> str:
    left, right = span
    if context[left:right] != old or old == str(new):
        raise ValueError("reader edit source span differs")
    return context[:left] + str(new) + context[right:]


def audit(native: Path) -> dict:
    manifest = verify_merge(native)
    if manifest.get("p154_schema") != "longworld.p154-typed-grid-grammar.v1":
        raise ValueError("wrong P154 schema")
    config = json.loads((ROOT / "configs/p154_typed_grid_grammar_v1.json").read_text())
    campaign = tables.pin(config["campaign_manifest"])
    pool = tables.pin(config["source_pool"])
    if digest(campaign) != manifest["campaign_manifest_sha256"] or digest(pool) != manifest["source_pool_sha256"]:
        raise ValueError("P154 frozen source pins differ")
    campaign_data = json.loads(campaign.read_text())
    ledger_path = campaign.parent / "chunk_ledger.jsonl"
    if digest(ledger_path) != campaign_data["chunk_ledger_sha256"]:
        raise ValueError("P131 chunk ledger differs")
    grids, pages = {}, {}
    for chunk in rows(ledger_path):
        directory = campaign.parent / "chunks" / chunk["chunk"]
        source_path = directory / "source/source_manifest.json"
        grid_path = directory / "grid/manifest.json"
        if digest(source_path) != chunk["source_manifest_sha256"] or digest(grid_path) != chunk["grid_manifest_sha256"]:
            raise ValueError("P131 child receipt differs")
        source = json.loads(source_path.read_text())
        grid = json.loads(grid_path.read_text())
        valid = grid_path.parent / "valid_grids.jsonl"
        if digest(valid) != grid["files_sha256"]["valid_grids.jsonl"]:
            raise ValueError("P131 frozen grids differ")
        pages.update({x["title"]: x for x in source["records"]})
        grids.update({x["table_id"]: x for x in rows(valid)})
    index = rows(native / "sample_index.jsonl")
    proofs = rows(native / "audit.jsonl")
    if len(index) != len(proofs) or len(index) != manifest["candidate_views"]:
        raise ValueError("P154 index/proof count differs")
    by_id = {row["sample_id"]: row for row in index}
    readers = {}
    for split in ("train", "eval"):
        for position, reader in enumerate(rows(native / f"candidate_{split}.jsonl")):
            row = by_id[reader["sample_id"]]
            if row["split"] != split or row["row_index"] != position:
                raise ValueError("P154 reader pointer differs")
            readers[reader["sample_id"]] = reader
    if len(readers) != len(index):
        raise ValueError("P154 reader inventory differs")
    pool_sources = json.loads(pool.read_text())["sources"]
    source_groups = {
        json.loads(tables.pin(source["snapshot"]).read_text())["documents"][0]["title"]:
        json.loads(tables.pin(source["snapshot"]).read_text())["snapshot_id"]
        for source in pool_sources
    }
    tokenizer = get_tokenizer()
    groups: dict[str, set[str]] = defaultdict(set)
    total_rows = 0
    operations = Counter()
    max_distance = 0
    for proof in proofs:
        sample_id = proof["sample_id"]
        row = by_id[sample_id]
        reader = readers[sample_id]
        entry = grids[proof["table_id"]]
        page = pages[entry["title"]]
        html = tables.pin({"path": page["html_path"], "sha256": page["html_sha256"]}).read_text()
        context, spans = tables.render_context(entry)
        user = reader["messages"][0]["content"]
        if user.split("\n\nQUESTION\n", 1)[0] != context:
            raise ValueError("P154 final reader context differs from frozen grid")
        if row["source_group"] != source_groups[entry["title"]] or row["split"] != entry["split"]:
            raise ValueError("P154 page source group/split differs")
        groups[row["source_group"]].add(row["split"])
        spec = proof["spec"]
        column = spec["column"]
        if entry["grid"]["header_paths"][column] != spec["header"]:
            raise ValueError("P154 source header differs")
        gold = recount(context, spec)
        if gold != proof["answer"] or gold != json.loads(reader["messages"][1]["content"]):
            raise ValueError("P154 independent reader computation differs")
        evidence = proof["candidate_rows"]
        body = entry["grid"]["rows"][entry["grid"]["header_rows"] :]
        if len(evidence) != len(body):
            raise ValueError("P154 omitted source grid row")
        prompt = _render_chat(tokenizer, reader["messages"][:1], generation_prompt=True)
        prompt_start = prompt.index(user)
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)["offset_mapping"]
        bounds = []
        for index, (item, cells) in enumerate(zip(evidence, body, strict=True)):
            cell = entry["grid"]["origins"][cells[column]]
            left, right = cell["html_span"]
            if (
                item["row"] != index
                or hashlib.sha256(html[left:right].encode()).hexdigest() != item["source_html_sha256"]
                or item["source_html_span"] != [left, right]
                or item["reader_char_span"] != list(spans[index, column])
                or context[slice(*item["reader_char_span"])] != item["value"]
            ):
                raise ValueError("P154 HTML/reader cell evidence differs")
            raw = item["value"]
            value = int(raw.replace(",", "")) if PLAIN.fullmatch(raw) else None
            if value is not None and spec["kind"] == "calendar_year" and (len(raw) != 4 or not 1800 <= value <= 2100):
                value = None
            if value is not None and spec["kind"] == "capacity_integer" and not 0 < value <= 999999:
                value = None
            if item["parsed_integer"] != value or item["selected"] != (value is not None and spec["low"] <= value <= spec["high"]):
                raise ValueError("P154 row membership differs")
            source_fragment = html[left:right].casefold()
            if value is not None and (
                cell["rowspan"] != 1
                or cell["colspan"] != 1
                or cell["footnote_refs"]
                or cell["citation_markers"]
                or "aria-hidden" in source_fragment
                or "mw-collapsible" in source_fragment
                or "visibility:hidden" in source_fragment.replace(" ", "")
            ):
                raise ValueError("P154 numeric reader cell lacks unambiguous source support")
            start, end = item["reader_char_span"]
            if start < end:
                expected = list(token_span(offsets, prompt_start + start, prompt_start + end))
                if item["reader_token_span"] != expected:
                    raise ValueError("P154 final chat evidence token span differs")
                bounds.append(expected)
        positions = proof["final_chat_positions"]
        question_start = prompt_start + len(context) + len("\n\nQUESTION\n")
        query_token = token_span(offsets, question_start, prompt_start + len(user))[0]
        if (
            positions["first_evidence_token"] != min(x[0] for x in bounds)
            or positions["last_evidence_token"] != max(x[1] for x in bounds)
            or positions["question_start_token"] != query_token
            or positions["first_evidence_to_question_tokens"] != query_token - min(x[0] for x in bounds)
            or positions["last_evidence_to_question_tokens"] != query_token - max(x[1] for x in bounds)
        ):
            raise ValueError("P154 final chat distance differs")
        edits = proof["interventions"]
        near_span, hit_span = edits["reader_edit_spans"]
        variants = (
            (near_span, edits["near_miss_original"], edits["incoming_value"], edits["incoming_answer"]),
            (hit_span, edits["hit_original"], edits["removal_value"], edits["removal_answer"]),
            (near_span, edits["near_miss_original"], edits["control_value"], edits["control_answer"]),
        )
        for position, (span, old, new, expected) in enumerate(variants):
            variant = changed(context, span, old, new)
            if recount(variant, spec) != expected or hashlib.sha256(variant.encode()).hexdigest() != edits["reader_context_sha256"][position]:
                raise ValueError("P154 reader edit does not independently replay")
        if spec["operation"] == "interval_sum":
            variant = changed(context, hit_span, edits["hit_original"], edits["within_interval_value"])
            if recount(variant, spec) != edits["within_interval_answer"]:
                raise ValueError("P154 in-interval sum edit differs")
        audit_reader(reader, row, tokenizer, 131072)
        operations[spec["operation"]] += 1
        total_rows += len(evidence)
        max_distance = max(max_distance, positions["first_evidence_to_question_tokens"])
    if any(len(splits) != 1 for splits in groups.values()):
        raise ValueError("P154 source world crosses train/eval")
    return {
        "status": "independent_final_reader_grid_replay_passed",
        "tasks": len(proofs), "source_groups": len(groups),
        "all_candidate_rows_replayed": total_rows,
        "operations": dict(sorted(operations.items())),
        "maximum_first_evidence_to_question_tokens": max_distance,
        "source_split_overlap": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/candidates/p154_typed_grid_grammar_v1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.input)
    content = json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(content, end="")


if __name__ == "__main__":
    main()
