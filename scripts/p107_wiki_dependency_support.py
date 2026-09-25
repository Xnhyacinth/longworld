"""Find genuine remote selector→table-scan support in accepted Wiki worlds."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p92_generic_table_scan import PLAIN_NAME
from longworld.synthesis.wiki_row_binding import _rows as visible_rows
from scripts.p105_wiki_grid_batch import _plain_label
from scripts.p106_freeze_width_revisions import _pin, _sha

SCHEMA = "longworld.p107-wiki-dependent-scan-support.v1"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _line_span(text: str, point: int) -> tuple[int, int]:
    if not 0 <= point < len(text):
        raise ValueError("remote selector point outside document")
    left = text.rfind("\n", 0, point) + 1
    right = text.find("\n", point)
    return left, len(text) if right < 0 else right


def _remote_row_candidates(
    target_doc: dict, remote_doc: dict, target_column: str, category: str
) -> list[dict]:
    rows = visible_rows(remote_doc["text"])
    counts = Counter(row.name.value for row in rows)
    found = []
    for row in rows:
        value = row.cell(target_column)
        name = row.name.value
        if value is None or value.value != category:
            continue
        if (
            counts[name] != 1
            or not PLAIN_NAME.fullmatch(name)
            or not _plain_label(name)
            or name in target_doc["text"]
            or category.casefold() in remote_doc["title"].casefold()
        ):
            continue
        name_line = _line_span(remote_doc["text"], row.name.start)
        value_line = _line_span(remote_doc["text"], value.start)
        if (
            name_line != value_line
            or remote_doc["text"][value.start : value.end] != category
        ):
            continue
        outside = (
            remote_doc["text"][: name_line[0]]
            + "\n"
            + remote_doc["text"][name_line[1] :]
        )
        if any(name in line and category in line for line in outside.splitlines()):
            continue
        controls = [
            (column, cell)
            for column, cell in row.columns
            if column not in ("Name", target_column)
            and cell.value
            and _plain_label(cell.value)
            and cell.value != category
        ]
        if not controls:
            continue
        control_column, control_cell = controls[0]
        found.append(
            {
                "remote_doc_id": remote_doc["doc_id"],
                "remote_title": remote_doc["title"],
                "selector_name": name,
                "selector_name_span": [row.name.start, row.name.end],
                "selector_column": target_column,
                "selector_value": category,
                "selector_value_span": [value.start, value.end],
                "selector_line_span": list(name_line),
                "control_column": control_column,
                "control_value": control_cell.value,
                "control_value_span": [control_cell.start, control_cell.end],
                "identity_evidence": "unique exact row name in named remote document; no target-document name occurrence",
            }
        )
    return found


def build(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != "longworld.p107-wiki-dependent-scan-probe.v1":
        raise ValueError("P107 probe config invalid")
    pools = [json.loads(_pin(pin).read_text()) for pin in config["source_pools"]]
    source_by_name = {
        source["name"]: source for pool in pools for source in pool["sources"]
    }
    native_rows = []
    for source in config["native_sources"]:
        manifest_path = _pin(source["manifest"])
        final_audit = json.loads(_pin(source["final_audit"]).read_text())
        manifest = json.loads(manifest_path.read_text())
        if (
            final_audit["verified_readers"] != manifest["candidate_views"]
            or final_audit["native_manifest_sha256"] != source["manifest"]["sha256"]
        ):
            raise ValueError("P107 source native/final-reader audit differs")
        directory = manifest_path.parent
        for filename, expected in manifest["files_sha256"].items():
            if _sha(directory / filename) != expected:
                raise ValueError(f"P107 source file changed: {filename}")
        indices = _jsonl(directory / "sample_index.jsonl")
        proofs = {row["sample_id"]: row for row in _jsonl(directory / "audit.jsonl")}
        readers = {
            row["sample_id"]: row
            for split in ("train", "eval")
            for row in _jsonl(directory / f"{split}.jsonl")
        }
        if set(proofs) != set(readers) or len(indices) != len(proofs):
            raise ValueError("P107 native reader/proof inventory differs")
        native_rows.extend(
            (
                source["name"],
                index,
                proofs[index["sample_id"]],
                readers[index["sample_id"]],
            )
            for index in indices
        )
    programs = {}
    for lane, index, proof, reader in native_rows:
        key = (
            index["source_group"],
            proof["source_doc_id"],
            proof["source_table_heading"],
            proof["source_column"],
            proof["category"],
        )
        programs.setdefault(key, (lane, index, proof, reader))
    snapshots = {}
    ledger = []
    gross_remote_rows = 0
    for key, (lane, index, proof, reader) in sorted(programs.items()):
        group = index["source_group"]
        source = source_by_name[group]
        if group not in snapshots:
            snapshots[group] = json.loads(_pin(source["snapshot"]).read_text())
        snapshot = snapshots[group]
        target = next(
            doc
            for doc in snapshot["documents"]
            if doc["doc_id"] == proof["source_doc_id"]
        )
        if (
            source["split"] != index["split"]
            or target["title"] != proof["source_title"]
        ):
            raise ValueError("P107 target source/split differs")
        candidates = []
        for remote in snapshot["documents"]:
            if remote["doc_id"] == target["doc_id"]:
                continue
            candidates.extend(
                _remote_row_candidates(
                    target, remote, proof["source_column"], proof["category"]
                )
            )
        gross_remote_rows += len(candidates)
        value_counts = Counter(row["value"] for row in proof["candidate_rows"])
        alternatives = sorted(
            value
            for value, count in value_counts.items()
            if value != proof["category"]
            and _plain_label(value)
            and 2 <= count <= min(15, len(proof["candidate_rows"]) - 2)
        )
        candidates.sort(key=lambda row: (row["remote_title"], row["selector_name"]))
        selected = candidates[0] if candidates and alternatives else None
        status = (
            "remote_selector_supported"
            if selected is not None
            else "no_remote_exact_typed_selector"
            if not candidates
            else "no_answer_changing_target_alternative"
        )
        if selected is not None:
            selected = {**selected, "alternative_target_category": alternatives[0]}
            user = reader["messages"][0]["content"]
            context = user.split("\n\nQUESTION\n", 1)[0]
            remote_doc = next(
                doc
                for doc in snapshot["documents"]
                if doc["doc_id"] == selected["remote_doc_id"]
            )
            if context.count(remote_doc["text"]) != 1:
                raise ValueError("P107 remote reader document not unique")
            start = context.index(remote_doc["text"])
            for kind in ("selector_name", "selector_value", "control_value"):
                left, right = selected[kind + "_span"]
                if context[start + left : start + right] != selected[kind]:
                    raise ValueError("P107 remote reader cell span differs")
            selected["remote_doc_reader_start"] = start
        ledger.append(
            {
                "base_lane": lane,
                "base_sample_id": index["sample_id"],
                "source_group": group,
                "world_id": index["world_id"],
                "split": index["split"],
                "domain": index["domain"],
                "topic": index["topic"],
                "target_doc_id": target["doc_id"],
                "target_title": target["title"],
                "target_heading": proof["source_table_heading"],
                "target_column": proof["source_column"],
                "target_category": proof["category"],
                "target_answer": proof["answer"],
                "status": status,
                "remote_row_candidates": len(candidates),
                "selected_remote": selected,
            }
        )
    return {
        "schema": SCHEMA,
        "config_sha256": _sha(config_path),
        "source_pins": config,
        "base_reader_views": len(native_rows),
        "unique_target_programs": len(programs),
        "gross_remote_matching_rows": gross_remote_rows,
        "productive_worlds": len(
            {
                row["world_id"]
                for row in ledger
                if row["status"] == "remote_selector_supported"
            }
        ),
        "status_counts": dict(sorted(Counter(row["status"] for row in ledger).items())),
        "supported_splits": dict(
            sorted(
                Counter(
                    row["split"]
                    for row in ledger
                    if row["status"] == "remote_selector_supported"
                ).items()
            )
        ),
        "reader_tasks_admitted": 0,
        "ledger": ledger,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = build(args.config)
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.verify_only:
        if not args.output.is_file() or args.output.read_text() != content:
            raise ValueError("P107 support replay differs")
    else:
        if args.output.exists():
            raise ValueError("P107 support ledger already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "base_reader_views",
                    "unique_target_programs",
                    "gross_remote_matching_rows",
                    "productive_worlds",
                    "status_counts",
                    "supported_splits",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
