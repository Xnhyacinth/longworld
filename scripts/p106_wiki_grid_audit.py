"""Independently replay P106 P95 source cells, final answers and loss masks."""

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
from scripts.p100_wiki_categorical_scan import answer
from scripts.p105_wiki_grid_audit import (
    _assert_hit_control,
    _by_id,
    _edited_answer,
    _rows,
    _table,
)
from scripts.p106_freeze_width_revisions import _pin, _sha
from scripts.run_p92_generic_table_scan import _dump
from scripts.train_sft import _render_chat

SCHEMA = "longworld.p106-wiki-grid-final-reader-audit.v1"


def audit(native_dir: Path) -> dict:
    manifest_path = native_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for filename, digest in manifest["files_sha256"].items():
        if _sha(native_dir / filename) != digest:
            raise ValueError(f"P106 native file changed: {filename}")
    if (
        manifest.get("schema") != "longworld.p106-wiki-grid-category.v1.result"
        or manifest["candidate_views"] != manifest["independent_tasks"]
        or _sha(ROOT / "scripts/p105_wiki_grid_batch.py")
        != manifest["delegated_p105_compiler_sha256"]
        or _sha(ROOT / "scripts/p105_wiki_reader_cells.py")
        != manifest["delegated_p105_reader_sha256"]
    ):
        raise ValueError("P106 native/delegated compiler receipt differs")
    train = _rows(native_dir / "train.jsonl")
    eval_rows = _rows(native_dir / "eval.jsonl")
    readers = _by_id(train + eval_rows, "reader")
    indices = _rows(native_dir / "sample_index.jsonl")
    index_by_id = _by_id(indices, "index")
    proofs = _by_id(_rows(native_dir / "audit.jsonl"), "proof")
    if (
        set(readers) != set(index_by_id)
        or set(readers) != set(proofs)
        or len(indices) != manifest["candidate_views"]
        or any(index_by_id[row["sample_id"]]["split"] != "train" for row in train)
        or any(index_by_id[row["sample_id"]]["split"] != "eval" for row in eval_rows)
    ):
        raise ValueError("P106 mixed-split reader/index/proof inventory differs")
    pins = manifest["source_pins"]
    raw_manifest = json.loads(_pin(pins["raw_manifest"]).read_text())
    pool = json.loads(_pin(pins["source_pool"]).read_text())
    gate = json.loads(_pin(pins["source_gate"]).read_text())
    _pin(pins["prior_p97_pool"])
    if (
        gate["accepted_groups"] != len(pool["sources"])
        or raw_manifest["source_pins"]["source_pool"]["sha256"]
        != pins["source_pool"]["sha256"]
        or raw_manifest["source_pins"]["prior_p97_pool"]["sha256"]
        != pins["prior_p97_pool"]["sha256"]
    ):
        raise ValueError("P106 P95/P97 global source gate differs")
    raw_by_doc = {row["doc_id"]: row for row in raw_manifest["records"]}
    source_by_name = {row["name"]: row for row in pool["sources"]}
    raw_root = (ROOT / pins["raw_manifest"]["path"]).parent
    snapshots = {}
    tokenizer = get_tokenizer()
    verified = []
    for index in indices:
        sample_id = index["sample_id"]
        reader = readers[sample_id]
        proof = proofs[sample_id]
        if (
            not sample_id.startswith("p106-wiki-grid-")
            or index["source_kind"] != "real_wiki"
            or index["evidence_profile"] != "wikitext_grid_repaired"
            or [message["role"] for message in reader["messages"]]
            != ["user", "assistant"]
        ):
            raise ValueError("P106 reader shape/source kind differs")
        user = reader["messages"][0]["content"]
        suffix = "\n\nQUESTION\n" + proof["question"]
        if not user.endswith(suffix):
            raise ValueError("P106 question boundary differs")
        context = user[: -len(suffix)]
        if hashlib.sha256(context.encode()).hexdigest() != proof["final_reader_sha256"]:
            raise ValueError("P106 final reader hash differs")
        table_span = proof["reader_table_span"]
        table_text = context[table_span[0] : table_span[1]]
        if (
            hashlib.sha256(table_text.encode()).hexdigest()
            != proof["reader_table_sha256"]
        ):
            raise ValueError("P106 final reader table hash differs")
        table = _table(table_text, proof["source_table_heading"])
        column = table.columns.index(proof["source_column"])
        category = proof["category"]
        baseline = answer(table, column, category)
        if baseline != proof["answer"] or reader["messages"][1]["content"] != _dump(
            baseline
        ):
            raise ValueError("P106 final reader answer differs")
        evidence = proof["candidate_rows"]
        if len(evidence) != len(table.rows):
            raise ValueError("P106 positive/negative table row coverage incomplete")
        raw = raw_by_doc[proof["source_doc_id"]]
        group = index["source_group"]
        source = source_by_name[group]
        if group not in snapshots:
            snapshots[group] = json.loads(_pin(source["snapshot"]).read_text())
        doc = next(
            doc
            for doc in snapshots[group]["documents"]
            if doc["doc_id"] == proof["source_doc_id"]
        )
        if (
            raw["source_group"] != group
            or raw["split"] != index["split"]
            or source["split"] != index["split"]
            or raw["source_snapshot_sha256"] != source["snapshot"]["sha256"]
            or proof["source_snapshot_sha256"] != source["snapshot"]["sha256"]
            or raw["response_sha256"] != proof["source_raw_response_sha256"]
            or raw["revid"] != proof["source_revision"]
            or doc["title"] != raw["title"]
            or raw["title"] != proof["source_title"]
            or doc["page_url"] != raw["page_url"]
            or doc["revision_url"] != raw["revision_url"]
        ):
            raise ValueError("P106 source group/title/URL/split/revision differs")
        raw_path = raw_root / raw["response_path"]
        if _sha(raw_path) != raw["response_sha256"]:
            raise ValueError("P106 exact revision response hash differs")
        page = json.loads(raw_path.read_text())["query"]["pages"][0]
        revision = page["revisions"][0]
        if page["title"] != raw["title"] or revision["revid"] != raw["revid"]:
            raise ValueError("P106 exact revision identity differs")
        wikitext = revision["slots"]["main"]["content"]
        prompt = _render_chat(tokenizer, reader["messages"][:1], generation_prompt=True)
        if prompt.count(user) != 1:
            raise ValueError("P106 final user occurrence ambiguous")
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        user_start = prompt.index(user)
        for row, cell in zip(table.rows, evidence):
            if (
                cell["name"] != row.name
                or cell["value"] != row.cells[column]
                or cell["selected"] != (row.cells[column] == category)
            ):
                raise ValueError("P106 row membership differs")
            for kind, expected in (("name", row.name), ("value", row.cells[column])):
                raw_left, raw_right = cell[f"source_{kind}_span"]
                reader_left, reader_right = cell[f"reader_{kind}_span"]
                if (
                    _clean_wikitext_inline(wikitext[raw_left:raw_right]).strip() or "∅"
                ) != expected or context[reader_left:reader_right] != expected:
                    raise ValueError("P106 raw-to-final-reader cell span differs")
                if (
                    list(
                        token_span(
                            offsets, user_start + reader_left, user_start + reader_right
                        )
                    )
                    != cell[f"prompt_{kind}_token_span"]
                ):
                    raise ValueError("P106 evidence token span differs")
        outside = context[: table_span[0]] + "\n" + context[table_span[1] :]
        selected = set(baseline["entries"])
        if any(
            category in line and any(name in line for name in selected)
            for line in outside.splitlines()
        ):
            raise ValueError("P106 same-line alternative support")
        edit = proof["intervention"]
        span = [edit["reader_cell_start"], edit["reader_cell_end"]]
        if context[span[0] : span[1]] != edit["old_value"]:
            raise ValueError("P106 intervention original cell differs")
        edited = {}
        for kind in ("hit", "control"):
            result, digest = _edited_answer(
                context,
                span,
                edit[kind + "_value"],
                table_span,
                proof["source_table_heading"],
                column,
                category,
            )
            if (
                result != edit[kind + "_answer"]
                or digest != edit[kind + "_reader_sha256"]
            ):
                raise ValueError(f"P106 {kind} intervention replay differs")
            edited[kind] = result
        _assert_hit_control(
            baseline, edited["hit"], edited["control"], edit["changed_name"]
        )
        mask = audit_reader(
            {"sample_id": sample_id, "messages": reader["messages"]},
            index,
            tokenizer,
            131072,
        )
        verified.append(
            {
                "sample_id": sample_id,
                "world_id": index["world_id"],
                "split": index["split"],
                "domain": index["domain"],
                "topic": index["topic"],
                "full_chat_tokens": mask["full_chat_tokens"],
                "supervised_tokens": mask["supervised_tokens"],
                "candidate_rows": len(evidence),
                "evidence_token_extent": index["evidence_token_extent"],
                "status": "source_final_reader_hit_control_mask_replayed",
            }
        )
    return {
        "schema": SCHEMA,
        "native_manifest_sha256": _sha(manifest_path),
        "verified_readers": len(verified),
        "worlds": dict(sorted(Counter(row["world_id"] for row in verified).items())),
        "splits": dict(sorted(Counter(row["split"] for row in verified).items())),
        "domains": dict(sorted(Counter(row["domain"] for row in verified).items())),
        "topics": dict(sorted(Counter(row["topic"] for row in verified).items())),
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
            raise ValueError("P106 independent audit replay differs")
    else:
        if output.exists():
            raise ValueError("P106 independent audit already exists")
        output.write_text(content)
    print(
        _dump(
            {
                key: result[key]
                for key in ("verified_readers", "splits", "worlds", "domains", "topics")
            }
        )
    )


if __name__ == "__main__":
    main()
