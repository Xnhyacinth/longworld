"""Independently replay P105 final Wiki readers, source cells and SFT masks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.wiki_adapter import _clean_wikitext_inline
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.p100_wiki_categorical_scan import answer, parse_tables
from scripts.run_p92_generic_table_scan import _dump, _sha
from scripts.train_sft import _render_chat

SCHEMA = "longworld.p105-wiki-grid-final-reader-audit.v1"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _by_id(rows: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or sample_id in result:
            raise ValueError(f"duplicate or absent {label} sample_id")
        result[sample_id] = row
    return result


def _assert_hit_control(
    baseline: dict, hit: dict, control: dict, changed_name: str
) -> None:
    if (
        hit["count"] != baseline["count"] + 1
        or set(hit["entries"]) != set(baseline["entries"]) | {changed_name}
        or control != baseline
    ):
        raise ValueError("hit/control dependency does not change exactly one member")


def _table(text: str, heading: str):
    found, rejected = parse_tables("## " + heading + "\n" + text + "\n\n")
    if len(found) != 1 or rejected:
        raise ValueError("final reader table cannot be independently parsed")
    return found[0]


def _edited_answer(
    context: str,
    span: list[int],
    replacement: str,
    table_span: list[int],
    heading: str,
    column: int,
    category: str,
) -> tuple[dict, str]:
    left, right = span
    changed = context[:left] + replacement + context[right:]
    shift = len(replacement) - (right - left)
    table = _table(changed[table_span[0] : table_span[1] + shift], heading)
    return answer(table, column, category), hashlib.sha256(changed.encode()).hexdigest()


def audit(native_dir: Path) -> dict:
    manifest = json.loads((native_dir / "manifest.json").read_text())
    for filename, digest in manifest["files_sha256"].items():
        if _sha(native_dir / filename) != digest:
            raise ValueError(f"native file changed: {filename}")
    if manifest["candidate_views"] != manifest["independent_tasks"]:
        raise ValueError("native task/view count differs")
    train_readers = _rows(native_dir / "train.jsonl")
    eval_readers = _rows(native_dir / "eval.jsonl")
    readers = train_readers + eval_readers
    indices = _rows(native_dir / "sample_index.jsonl")
    audits = _rows(native_dir / "audit.jsonl")
    if not len(readers) == len(indices) == len(audits) == manifest["candidate_views"]:
        raise ValueError("native reader/index/audit count differs")
    reader_by_id = _by_id(readers, "reader")
    proof_by_id = _by_id(audits, "proof")
    index_by_id = _by_id(indices, "index")
    if set(reader_by_id) != set(proof_by_id) or set(reader_by_id) != set(index_by_id):
        raise ValueError("reader/index/proof sample identities differ")
    if any(
        index_by_id[row["sample_id"]]["split"] != "train" for row in train_readers
    ) or any(index_by_id[row["sample_id"]]["split"] != "eval" for row in eval_readers):
        raise ValueError("reader file and index split differ")
    raw_manifest = json.loads(
        (ROOT / manifest["source_pins"]["raw_manifest"]["path"]).read_text()
    )
    if (
        _sha(ROOT / manifest["source_pins"]["raw_manifest"]["path"])
        != manifest["source_pins"]["raw_manifest"]["sha256"]
    ):
        raise ValueError("raw source manifest pin changed")
    raw_by_doc = {row["doc_id"]: row for row in raw_manifest["records"]}
    pool_pin = manifest["source_pins"]["source_pool"]
    pool_path = ROOT / pool_pin["path"]
    if _sha(pool_path) != pool_pin["sha256"]:
        raise ValueError("P97 source pool pin changed")
    sources = {row["name"]: row for row in json.loads(pool_path.read_text())["sources"]}
    snapshots = {}
    tokenizer = get_tokenizer()
    verified = []
    for index in indices:
        sample_id = index["sample_id"]
        reader = reader_by_id[sample_id]
        proof = proof_by_id[sample_id]
        if (
            index["source_kind"] != "real_wiki"
            or index["evidence_profile"] != "wikitext_grid_repaired"
        ):
            raise ValueError("noncanonical Wiki source kind/evidence profile")
        messages = reader["messages"]
        if [item["role"] for item in messages] != ["user", "assistant"]:
            raise ValueError("unexpected native message roles")
        user = messages[0]["content"]
        suffix = "\n\nQUESTION\n" + proof["question"]
        if not user.endswith(suffix):
            raise ValueError("final reader question differs")
        context = user[: -len(suffix)]
        if hashlib.sha256(context.encode()).hexdigest() != proof["final_reader_sha256"]:
            raise ValueError("final reader byte hash differs")
        table_span = proof["reader_table_span"]
        table_text = context[table_span[0] : table_span[1]]
        if (
            hashlib.sha256(table_text.encode()).hexdigest()
            != proof["reader_table_sha256"]
        ):
            raise ValueError("final reader table hash differs")
        parsed = _table(table_text, proof["source_table_heading"])
        column = parsed.columns.index(proof["source_column"])
        category = proof["category"]
        baseline = answer(parsed, column, category)
        if baseline != proof["answer"] or messages[1]["content"] != _dump(baseline):
            raise ValueError("final reader answer differs")
        evidence = proof["candidate_rows"]
        if len(evidence) != len(parsed.rows):
            raise ValueError("negative/positive candidate coverage incomplete")
        raw = raw_by_doc[proof["source_doc_id"]]
        source_group = index["source_group"]
        source_record = sources[source_group]
        if source_group not in snapshots:
            snapshot_path = ROOT / source_record["snapshot"]["path"]
            if _sha(snapshot_path) != source_record["snapshot"]["sha256"]:
                raise ValueError("P97 snapshot pin changed")
            snapshots[source_group] = json.loads(snapshot_path.read_text())
        snapshot = snapshots[source_group]
        doc = next(
            doc
            for doc in snapshot["documents"]
            if doc["doc_id"] == proof["source_doc_id"]
        )
        if (
            raw["response_sha256"] != proof["source_raw_response_sha256"]
            or raw["revid"] != proof["source_revision"]
            or raw["title"] != proof["source_title"]
            or raw["source_group"] != source_group
            or raw["split"] != index["split"]
            or source_record["split"] != index["split"]
            or raw["source_snapshot_sha256"] != source_record["snapshot"]["sha256"]
            or proof["source_snapshot_sha256"] != source_record["snapshot"]["sha256"]
            or doc["title"] != raw["title"]
            or doc["page_url"] != raw["page_url"]
            or doc["revision_url"] != raw["revision_url"]
        ):
            raise ValueError("source title/URL/split/revision proof differs")
        source_path = (
            ROOT / "data/candidates/p104_wiki_width_raw_v1" / raw["response_path"]
        )
        if _sha(source_path) != raw["response_sha256"]:
            raise ValueError("source response hash differs")
        source = json.loads(source_path.read_text())["query"]["pages"][0]["revisions"][
            0
        ]["slots"]["main"]["content"]
        prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
        if prompt.count(user) != 1:
            raise ValueError("ambiguous final user occurrence")
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        user_start = prompt.index(user)
        for row, cell in zip(parsed.rows, evidence):
            if (
                cell["name"] != row.name
                or cell["value"] != row.cells[column]
                or cell["selected"] != (row.cells[column] == category)
            ):
                raise ValueError("candidate row differs from final table")
            for kind, expected in (("name", row.name), ("value", row.cells[column])):
                raw_left, raw_right = cell[f"source_{kind}_span"]
                reader_left, reader_right = cell[f"reader_{kind}_span"]
                if (
                    _clean_wikitext_inline(source[raw_left:raw_right]).strip() or "∅"
                ) != expected or context[reader_left:reader_right] != expected:
                    raise ValueError("raw-to-final-reader cell span differs")
                tokens = token_span(
                    offsets, user_start + reader_left, user_start + reader_right
                )
                if list(tokens) != cell[f"prompt_{kind}_token_span"]:
                    raise ValueError("evidence token span differs")
        outside = context[: table_span[0]] + "\n" + context[table_span[1] :]
        selected = set(baseline["entries"])
        if any(
            category in line and any(name in line for name in selected)
            for line in outside.splitlines()
        ):
            raise ValueError("same-line alternate support outside table")
        intervention = proof["intervention"]
        edited_span = [
            intervention["reader_cell_start"],
            intervention["reader_cell_end"],
        ]
        if context[edited_span[0] : edited_span[1]] != intervention["old_value"]:
            raise ValueError("intervention original cell differs")
        results = {}
        for kind in ("hit", "control"):
            result, digest = _edited_answer(
                context,
                edited_span,
                intervention[kind + "_value"],
                table_span,
                proof["source_table_heading"],
                column,
                category,
            )
            if (
                result != intervention[kind + "_answer"]
                or digest != intervention[kind + "_reader_sha256"]
            ):
                raise ValueError(f"{kind} final-reader intervention differs")
            results[kind] = result
        _assert_hit_control(
            baseline, results["hit"], results["control"], intervention["changed_name"]
        )
        mask = audit_reader(
            {"sample_id": sample_id, "messages": messages}, index, tokenizer, 131072
        )
        verified.append(
            {
                "sample_id": sample_id,
                "world_id": index["world_id"],
                "split": index["split"],
                "domain": index["domain"],
                "full_chat_tokens": mask["full_chat_tokens"],
                "supervised_tokens": mask["supervised_tokens"],
                "candidate_rows": len(evidence),
                "evidence_token_extent": index["evidence_token_extent"],
                "status": "source_final_reader_hit_control_mask_replayed",
            }
        )
    return {
        "schema": SCHEMA,
        "native_manifest_sha256": _sha(native_dir / "manifest.json"),
        "verified_readers": len(verified),
        "worlds": dict(sorted(Counter(row["world_id"] for row in verified).items())),
        "domains": dict(sorted(Counter(row["domain"] for row in verified).items())),
        "splits": dict(sorted(Counter(row["split"] for row in verified).items())),
        "rows": verified,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = audit(args.native_dir)
    output = args.native_dir / "mask_audit.json"
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.verify_only:
        if not output.is_file() or output.read_text() != content:
            raise ValueError("P105 independent audit replay differs")
    else:
        if output.exists():
            raise ValueError("P105 independent audit already exists")
        output.write_text(content)
    print(
        _dump(
            {
                key: result[key]
                for key in ("verified_readers", "worlds", "domains", "splits")
            }
        )
    )


if __name__ == "__main__":
    main()
