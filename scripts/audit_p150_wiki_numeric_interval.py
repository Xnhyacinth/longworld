"""Independently replay P150 final readers, source cells, edits and masks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts import p126_wiki_html_table_tasks as tables
from scripts.audit_unified_reader_mask import audit_reader


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(raw) for raw in path.read_text().splitlines()]


def _recount(
    context: str, column: int, low: int, high: int, unit: str
) -> dict[str, int]:
    _, body = tables.parse_context(context)
    eligible = []
    for row in body:
        visible = row[column]
        plain = bool(re.fullmatch(r"[0-9]{1,4}", visible))
        if unit == "calendar_year":
            plain = plain and len(visible) == 4 and 1800 <= int(visible) <= 2100
        if plain:
            eligible.append(int(visible))
    return {
        "count": sum(low <= value <= high for value in eligible),
        "eligible_rows": len(eligible),
    }


def _changed(context: str, span: list[int], old: str, new: int) -> str:
    left, right = span
    if context[left:right] != old:
        raise ValueError("P150 final reader cell span differs")
    return context[:left] + str(new) + context[right:]


def audit(native: Path) -> dict[str, Any]:
    manifest = verify_merge(native)
    if manifest.get("p150_schema") != "longworld.p150-wiki-numeric-interval.v1":
        raise ValueError("wrong P150 candidate schema")
    grids_path = (
        ROOT
        / "data/candidates/p148_wiki_category_catalog_v1/p122_grid/valid_grids.jsonl"
    )
    grid_manifest = grids_path.parent / "manifest.json"
    if _sha(grid_manifest) != manifest["grid_manifest_sha256"]:
        raise ValueError("P148 frozen grid manifest differs")
    receipt = json.loads(grid_manifest.read_text())
    if _sha(grids_path) != receipt["files_sha256"]["valid_grids.jsonl"]:
        raise ValueError("P148 frozen grid rows differ")
    grids = {row["table_id"]: row for row in _rows(grids_path)}
    source_path = (
        ROOT
        / "data/candidates/p148_wiki_category_catalog_v1/p122_source/source_manifest.json"
    )
    if _sha(source_path) != manifest["source_manifest_sha256"]:
        raise ValueError("P148 HTML source manifest differs")
    source = json.loads(source_path.read_text())
    pages = {row["title"]: row for row in source["records"]}
    index = _rows(native / "sample_index.jsonl")
    proofs = _rows(native / "audit.jsonl")
    if len(index) != len(proofs) or len(index) != manifest["candidate_views"]:
        raise ValueError("P150 final reader/proof counts differ")
    by_id = {row["sample_id"]: row for row in index}
    readers = {}
    for split in ("train", "eval"):
        for position, reader in enumerate(_rows(native / f"candidate_{split}.jsonl")):
            row = by_id[reader["sample_id"]]
            if row["split"] != split or row["row_index"] != position:
                raise ValueError("P150 reader/index pointer differs")
            readers[reader["sample_id"]] = reader
    if len(readers) != len(index):
        raise ValueError("P150 final reader inventory differs")
    tokenizer = get_tokenizer()
    groups: dict[str, set[str]] = defaultdict(set)
    intervals = Counter()
    total_rows = 0
    for proof in proofs:
        sample_id = proof["sample_id"]
        row = by_id[sample_id]
        reader = readers[sample_id]
        entry = grids[proof["table_id"]]
        page = pages[entry["title"]]
        html_path = tables.pin(
            {"path": page["html_path"], "sha256": page["html_sha256"]}
        )
        html = html_path.read_text()
        context, spans = tables.render_context(entry)
        if reader["messages"][0]["content"].split("\n\nQUESTION\n", 1)[0] != context:
            raise ValueError("P150 final table context differs from source grid")
        if row["source_group"] not in {
            json.loads(tables.pin(s["snapshot"]).read_text())["snapshot_id"]
            for s in json.loads(
                (
                    ROOT
                    / "data/candidates/p148_wiki_category_catalog_v1/p119/source_pool.json"
                ).read_text()
            )["sources"]
            if json.loads(tables.pin(s["snapshot"]).read_text())["documents"][0][
                "title"
            ]
            == entry["title"]
        }:
            raise ValueError("P150 source group differs from frozen page")
        if row["split"] != entry["split"] or row["source_kind"] != "real_wiki":
            raise ValueError("P150 source split/kind differs")
        groups[row["source_group"]].add(row["split"])
        unit = proof["unit"]
        low, high = proof["interval"]
        header = [" / ".join(value) for value in entry["grid"]["header_paths"]]
        if header.count(" / ".join(proof["header"])) != 1:
            raise ValueError("P150 source numeric header ambiguous")
        column = header.index(" / ".join(proof["header"]))
        expected = _recount(context, column, low, high, unit)
        if (
            expected != proof["answer"]
            or json.loads(reader["messages"][1]["content"]) != expected
        ):
            raise ValueError("P150 independent reader count differs")
        evidence = proof["candidate_rows"]
        body = entry["grid"]["rows"][entry["grid"]["header_rows"] :]
        if len(evidence) != len(body):
            raise ValueError("P150 evidence does not scan all source rows")
        for item, cells in zip(evidence, body, strict=True):
            source_cell = entry["grid"]["origins"][cells[column]]
            start, end = source_cell["html_span"]
            if (
                hashlib.sha256(html[start:end].encode()).hexdigest()
                != item["source_html_sha256"]
                or item["source_html_span"] != source_cell["html_span"]
                or item["reader_char_span"] != list(spans[item["row"], column])
                or context[slice(*item["reader_char_span"])] != item["value"]
            ):
                raise ValueError("P150 source/reader row-cell evidence differs")
            is_plain = bool(re.fullmatch(r"[0-9]{1,4}", item["value"]))
            if unit == "calendar_year":
                is_plain = (
                    is_plain
                    and len(item["value"]) == 4
                    and 1800 <= int(item["value"]) <= 2100
                )
            parsed = int(item["value"]) if is_plain else None
            if item["parsed_integer"] != parsed or item["selected"] != (
                parsed is not None and low <= parsed <= high
            ):
                raise ValueError("P150 row membership differs")
        edits = proof["interventions"]
        near = _changed(
            context,
            edits["near_miss_span"],
            str(edits["near_miss_original"]),
            edits["hit_edit"],
        )
        control = _changed(
            context,
            edits["near_miss_span"],
            str(edits["near_miss_original"]),
            edits["control_edit"],
        )
        removed = _changed(
            context,
            edits["hit_span"],
            str(edits["hit_original"]),
            edits["removal_edit"],
        )
        for changed, key, target in (
            (near, "hit_edit_answer", expected["count"] + 1),
            (control, "control_answer", expected["count"]),
            (removed, "removal_answer", expected["count"] - 1),
        ):
            recomputed = _recount(changed, column, low, high, unit)
            if recomputed != edits[key] or recomputed["count"] != target:
                raise ValueError("P150 reader intervention fails independent replay")
        mask = audit_reader(reader, row, tokenizer, 131072)
        if mask["full_chat_tokens"] != row["full_chat_tokens"]:
            raise ValueError("P150 final assistant mask differs")
        intervals[unit] += 1
        total_rows += len(evidence)
    if any(len(value) != 1 for value in groups.values()):
        raise ValueError("P150 source world crosses train/eval")
    return {
        "status": "independent_final_reader_numeric_replay_passed",
        "tasks": len(proofs),
        "source_groups": len(groups),
        "all_candidate_rows_replayed": total_rows,
        "task_types": dict(sorted(intervals.items())),
        "source_split_overlap": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "data/candidates/p150_wiki_numeric_interval_v1",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.input)
    content = json.dumps(report, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(content, end="")


if __name__ == "__main__":
    main()
