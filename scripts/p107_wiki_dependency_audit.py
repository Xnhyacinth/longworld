"""Blindly recompute P107 two-document answers from final reader bytes."""

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
from longworld.synthesis.wiki_row_binding import _rows as visible_rows
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.p100_wiki_categorical_scan import answer, parse_tables
from scripts.p106_freeze_width_revisions import _pin, _sha
from scripts.p107_wiki_dependency_batch import _require_target_before_remote
from scripts.run_p92_generic_table_scan import _dump
from scripts.train_sft import _render_chat

SCHEMA = "longworld.p107-wiki-dependent-scan-final-audit.v1"
SEPARATOR = "\n\nQUESTION\n"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _by_id(rows: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or sample_id in result:
            raise ValueError(f"P107 duplicate/absent {label} sample ID")
        result[sample_id] = row
    return result


def _blind_answer(
    context: str,
    proof: dict,
    *,
    remote_start_shift: int = 0,
    remote_length_shift: int = 0,
    target_length_shift: int = 0,
) -> tuple[str, dict]:
    remote_left, remote_right = proof["remote_doc_reader_span"]
    remote = context[
        remote_left + remote_start_shift : remote_right
        + remote_start_shift
        + remote_length_shift
    ]
    matches = [
        row for row in visible_rows(remote) if row.name.value == proof["selector_name"]
    ]
    if len(matches) != 1 or matches[0].cell(proof["selector_column"]) is None:
        raise ValueError("P107 blind remote row unresolved")
    value = matches[0].cell(proof["selector_column"]).value
    table_left, table_right = proof["target_table_span"]
    target = context[table_left : table_right + target_length_shift]
    tables, rejected = parse_tables(
        "## " + proof["target_heading"] + "\n" + target + "\n\n"
    )
    if len(tables) != 1 or rejected:
        raise ValueError("P107 blind target table unresolved")
    column = tables[0].columns.index(proof["target_column"])
    return value, answer(tables[0], column, value)


def _edited(
    context: str,
    span: list[int],
    replacement: str,
    proof: dict,
    *,
    remote: bool,
) -> tuple[str, dict, str]:
    left, right = span
    changed = context[:left] + replacement + context[right:]
    shift = len(replacement) - (right - left)
    category, result = _blind_answer(
        changed,
        proof,
        remote_start_shift=shift
        if not remote and right <= proof["remote_doc_reader_span"][0]
        else 0,
        remote_length_shift=shift if remote else 0,
        target_length_shift=0 if remote else shift,
    )
    return category, result, hashlib.sha256(changed.encode()).hexdigest()


def audit(native_dir: Path, config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    manifest_path = native_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["schema"] != "longworld.p107-wiki-dependent-scan.v1.result" or manifest[
        "config_sha256"
    ] != _sha(config_path):
        raise ValueError("P107 native manifest/config differs")
    for filename, digest in manifest["files_sha256"].items():
        if _sha(native_dir / filename) != digest:
            raise ValueError(f"P107 native file differs: {filename}")
    support = json.loads(_pin(config["support"]).read_text())
    pool = json.loads(_pin(config["source_pool"]).read_text())
    source_by_name = {row["name"]: row for row in pool["sources"]}
    support_by_base = {
        row["base_sample_id"]: row
        for row in support["ledger"]
        if row["status"] == "remote_selector_supported"
    }
    train = _jsonl(native_dir / "train.jsonl")
    eval_rows = _jsonl(native_dir / "eval.jsonl")
    readers = _by_id(train + eval_rows, "reader")
    indices = _jsonl(native_dir / "sample_index.jsonl")
    proofs = _by_id(_jsonl(native_dir / "audit.jsonl"), "proof")
    if (
        set(readers) != set(proofs)
        or len(indices) != len(readers) != manifest["candidate_views"]
        or any(
            row["split"] != "train"
            for row in indices
            if row["sample_id"] in {r["sample_id"] for r in train}
        )
        or any(
            row["split"] != "eval"
            for row in indices
            if row["sample_id"] in {r["sample_id"] for r in eval_rows}
        )
    ):
        raise ValueError("P107 mixed-split reader/proof inventory differs")
    snapshots = {}
    tokenizer = get_tokenizer()
    checked = []
    for index in indices:
        sample_id = index["sample_id"]
        reader, proof = readers[sample_id], proofs[sample_id]
        source = source_by_name[index["source_group"]]
        base = support_by_base.get(proof["base_sample_id"])
        if (
            index["source_kind"] != "real_wiki"
            or index["operation"] != "remote_selector_closed_categorical_scan"
            or source["split"] != index["split"]
            or proof["source_snapshot_sha256"] != _sha(_pin(source["snapshot"]))
            or base is None
            or base["source_group"] != index["source_group"]
            or base["split"] != index["split"]
            or base["world_id"] != index["world_id"]
            or base["target_doc_id"] != proof["target_doc_id"]
            or base["selected_remote"]["remote_doc_id"] != proof["remote_doc_id"]
            or base["selected_remote"]["selector_name"] != proof["selector_name"]
            or base["selected_remote"]["selector_value"] != proof["selector_value"]
        ):
            raise ValueError("P107 source/operation/split binding differs")
        if index["source_group"] not in snapshots:
            snapshots[index["source_group"]] = json.loads(
                _pin(source["snapshot"]).read_text()
            )
        snapshot = snapshots[index["source_group"]]
        remote_doc = next(
            doc
            for doc in snapshot["documents"]
            if doc["doc_id"] == proof["remote_doc_id"]
        )
        target_doc = next(
            doc
            for doc in snapshot["documents"]
            if doc["doc_id"] == proof["target_doc_id"]
        )
        if (
            remote_doc["doc_id"] == target_doc["doc_id"]
            or remote_doc["title"] != proof["remote_title"]
            or target_doc["title"] != proof["target_title"]
            or remote_doc["page_url"] != proof["remote_page_url"]
            or target_doc["page_url"] != proof["target_page_url"]
            or remote_doc["revision_url"] != proof["remote_revision_url"]
            or target_doc["revision_url"] != proof["target_revision_url"]
            or proof["selector_name"] in target_doc["text"]
        ):
            raise ValueError("P107 remote/target source identity or shortcut differs")
        messages = reader["messages"]
        if [message["role"] for message in messages] != ["user", "assistant"]:
            raise ValueError("P107 reader roles differ")
        user = messages[0]["content"]
        suffix = SEPARATOR + proof["question"]
        if (
            not user.endswith(suffix)
            or proof["selector_value"].casefold() in proof["question"].casefold()
        ):
            raise ValueError("P107 question boundary or hidden value leak")
        for visible in (
            proof["remote_title"],
            proof["selector_name"],
            proof["selector_column"],
            proof["target_title"],
            proof["target_heading"],
            proof["target_column"],
        ):
            if visible not in proof["question"]:
                raise ValueError("P107 question omits selector/target identity")
        context = user[: -len(suffix)]
        if hashlib.sha256(context.encode()).hexdigest() != proof["final_reader_sha256"]:
            raise ValueError("P107 final reader hash differs")
        remote_left, remote_right = proof["remote_doc_reader_span"]
        _require_target_before_remote(proof["target_table_span"], remote_left)
        if (
            context[remote_left:remote_right] != remote_doc["text"]
            or context.count(remote_doc["text"]) != 1
        ):
            raise ValueError("P107 remote document not uniquely visible")
        table_left, table_right = proof["target_table_span"]
        if (
            hashlib.sha256(context[table_left:table_right].encode()).hexdigest()
            != proof["target_table_sha256"]
        ):
            raise ValueError("P107 target table hash differs")
        for kind, expected in (
            ("selector_name", proof["selector_name"]),
            ("selector_value", proof["selector_value"]),
        ):
            left, right = proof[kind + "_span"]
            if (
                context[left:right] != expected
                or not remote_left <= left < right <= remote_right
            ):
                raise ValueError("P107 remote selector span differs")
        category, result = _blind_answer(context, proof)
        if (
            category != proof["selector_value"]
            or result != proof["answer"]
            or messages[1]["content"] != _dump(result)
        ):
            raise ValueError("P107 blind answer differs from final reader")
        if result != base["target_answer"]:
            raise ValueError("P107 source support target answer differs")
        evidence = proof["candidate_rows"]
        if (
            len(evidence) != index["candidate_rows"]
            or sum(row["selected"] for row in evidence) != result["count"]
        ):
            raise ValueError("P107 complete target-row coverage differs")
        target_values = Counter(row["value"] for row in evidence)
        if target_values[category] != result["count"]:
            raise ValueError("P107 complete target membership differs")
        name_line = context.rfind("\n", 0, proof["selector_name_span"][0]) + 1
        line_end = context.find("\n", proof["selector_name_span"][0])
        line_end = len(context) if line_end < 0 else line_end
        outside = context[:name_line] + "\n" + context[line_end:]
        if any(
            proof["selector_name"] in line and category in line
            for line in outside.splitlines()
        ):
            raise ValueError("P107 duplicate remote selector support")
        remote_hit = _edited(
            context,
            proof["selector_value_span"],
            proof["remote_hit_value"],
            proof,
            remote=True,
        )
        if (
            remote_hit[0] != proof["remote_hit_value"]
            or remote_hit[1] != proof["remote_hit_answer"]
            or remote_hit[2] != proof["remote_hit_reader_sha256"]
            or remote_hit[1] == result
        ):
            raise ValueError("P107 remote selector hit failed independently")
        remote_control = _edited(
            context,
            proof["remote_control_span"],
            proof["remote_control_value"],
            proof,
            remote=True,
        )
        if (
            remote_control[0] != category
            or remote_control[1] != result
            or remote_control[2] != proof["remote_control_reader_sha256"]
        ):
            raise ValueError("P107 unrelated remote control changed answer")
        try:
            _edited(context, proof["selector_value_span"], "?", proof, remote=True)
        except ValueError:
            pass
        else:
            raise ValueError("P107 remote selector deletion retained answer")
        target_hit = proof["target_hit"]
        for kind in ("hit", "control"):
            span = [target_hit["reader_cell_start"], target_hit["reader_cell_end"]]
            edited = _edited(
                context, span, target_hit[kind + "_value"], proof, remote=False
            )
            if (
                edited[0] != category
                or edited[1] != target_hit[kind + "_answer"]
                or edited[2] != target_hit[kind + "_reader_sha256"]
            ):
                raise ValueError(f"P107 target {kind} replay differs")
        if target_hit["hit_answer"] == result or target_hit["control_answer"] != result:
            raise ValueError("P107 target hit/control dependency lacks contrast")
        prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
        if prompt.count(user) != 1:
            raise ValueError("P107 final user occurrence ambiguous")
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        user_start = prompt.index(user)
        for kind in ("selector_name", "selector_value"):
            left, right = proof[kind + "_span"]
            if (
                list(token_span(offsets, user_start + left, user_start + right))
                != proof[kind + "_token_span"]
            ):
                raise ValueError("P107 remote evidence token span differs")
        for row in evidence:
            left, right = row["reader_value_span"]
            if (
                context[left:right] != row["value"]
                or list(token_span(offsets, user_start + left, user_start + right))
                != row["prompt_value_token_span"]
            ):
                raise ValueError("P107 target evidence/token span differs")
        starts = [
            proof["selector_value_token_span"][0],
            *(row["prompt_value_token_span"][0] for row in evidence),
        ]
        ends = [
            proof["selector_value_token_span"][1],
            *(row["prompt_value_token_span"][1] for row in evidence),
        ]
        if index["evidence_token_extent"] != max(ends) - min(starts) or index[
            "remote_target_token_gap"
        ] != abs(
            proof["selector_value_token_span"][0]
            - min(row["prompt_value_token_span"][0] for row in evidence)
        ):
            raise ValueError("P107 evidence distance differs from final tokens")
        mask = audit_reader(
            {"sample_id": sample_id, "messages": messages}, index, tokenizer, 131072
        )
        checked.append(
            {
                "sample_id": sample_id,
                "world_id": index["world_id"],
                "split": index["split"],
                "domain": index["domain"],
                "full_chat_tokens": mask["full_chat_tokens"],
                "supervised_tokens": mask["supervised_tokens"],
                "remote_target_token_gap": index["remote_target_token_gap"],
                "evidence_token_extent": index["evidence_token_extent"],
                "status": "blind_two_document_answer_and_all_reader_mask_replayed",
            }
        )
    return {
        "schema": SCHEMA,
        "native_manifest_sha256": _sha(manifest_path),
        "checked_readers": len(checked),
        "worlds": dict(sorted(Counter(row["world_id"] for row in checked).items())),
        "splits": dict(sorted(Counter(row["split"] for row in checked).items())),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in checked),
        "supervised_tokens": sum(row["supervised_tokens"] for row in checked),
        "rows": checked,
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = audit(args.native_dir, args.config)
    output = args.native_dir / "mask_audit.json"
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.verify_only:
        if not output.is_file() or output.read_text() != content:
            raise ValueError("P107 independent audit replay differs")
    else:
        if output.exists():
            raise ValueError("P107 independent audit already exists")
        output.write_text(content)
    print(
        _dump(
            {
                key: result[key]
                for key in (
                    "checked_readers",
                    "worlds",
                    "splits",
                    "full_chat_tokens",
                    "supervised_tokens",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
