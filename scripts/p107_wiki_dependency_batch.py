"""Compile source-backed remote-selector→complete-table-scan reader tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.wiki_row_binding import _rows as visible_rows
from scripts.audit_wiki_join_positions import token_span
from scripts.p100_wiki_categorical_scan import answer, parse_tables
from scripts.p106_freeze_width_revisions import _pin, _sha
from scripts.run_p92_generic_table_scan import _dump
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p107-wiki-dependent-scan.v1"
SEPARATOR = "\n\nQUESTION\n"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _target_table(context: str, proof: dict):
    left, right = proof["reader_table_span"]
    text = context[left:right]
    tables, rejected = parse_tables(
        "## " + proof["source_table_heading"] + "\n" + text + "\n\n"
    )
    if (
        len(tables) != 1
        or rejected
        or hashlib.sha256(text.encode()).hexdigest() != proof["reader_table_sha256"]
    ):
        raise ValueError("P107 target table differs from source reader")
    return tables[0]


def _require_target_before_remote(target_span: list[int], remote_start: int) -> None:
    if target_span[1] > remote_start:
        raise ValueError("P107 supports target table before remote document only")


def _solve(
    context: str,
    *,
    remote_start: int,
    remote_length: int,
    selector_name: str,
    selector_column: str,
    target_proof: dict,
) -> tuple[str, dict]:
    remote = context[remote_start : remote_start + remote_length]
    matching = [row for row in visible_rows(remote) if row.name.value == selector_name]
    if len(matching) != 1 or matching[0].cell(selector_column) is None:
        raise ValueError("remote selector row/column unresolved")
    category = matching[0].cell(selector_column).value
    table = _target_table(context, target_proof)
    column = table.columns.index(target_proof["source_column"])
    return category, answer(table, column, category)


def _edit_and_solve(
    context: str,
    span: tuple[int, int],
    replacement: str,
    *,
    remote_start: int,
    remote_length: int,
    selector_name: str,
    selector_column: str,
    target_proof: dict,
) -> tuple[str, dict, str]:
    left, right = span
    changed = context[:left] + replacement + context[right:]
    new_length = remote_length + len(replacement) - (right - left)
    category, result = _solve(
        changed,
        remote_start=remote_start,
        remote_length=new_length,
        selector_name=selector_name,
        selector_column=selector_column,
        target_proof=target_proof,
    )
    return category, result, hashlib.sha256(changed.encode()).hexdigest()


def _load_base(native_dir: Path, sample_id: str) -> tuple[dict, dict, dict]:
    index = next(
        row
        for row in _jsonl(native_dir / "sample_index.jsonl")
        if row["sample_id"] == sample_id
    )
    proof = next(
        row
        for row in _jsonl(native_dir / "audit.jsonl")
        if row["sample_id"] == sample_id
    )
    reader = next(
        row
        for row in _jsonl(native_dir / f"{index['split']}.jsonl")
        if row["sample_id"] == sample_id
    )
    return index, proof, reader


def _compile_one(args: tuple) -> tuple[dict, dict, dict]:
    support, native_dir, source, max_tokens = args
    base_index, base_proof, base_reader = _load_base(
        native_dir, support["base_sample_id"]
    )
    if (
        base_index["source_group"] != support["source_group"]
        or base_index["split"] != support["split"]
        or base_proof["source_doc_id"] != support["target_doc_id"]
        or base_proof["answer"] != support["target_answer"]
        or source["name"] != support["source_group"]
        or source["split"] != support["split"]
    ):
        raise ValueError("P107 support/base source split or answer differs")
    snapshot_path = _pin(source["snapshot"])
    snapshot = json.loads(snapshot_path.read_text())
    selector = support["selected_remote"]
    remote_doc = next(
        doc
        for doc in snapshot["documents"]
        if doc["doc_id"] == selector["remote_doc_id"]
    )
    target_doc = next(
        doc
        for doc in snapshot["documents"]
        if doc["doc_id"] == support["target_doc_id"]
    )
    if (
        remote_doc["title"] != selector["remote_title"]
        or target_doc["title"] != support["target_title"]
        or remote_doc["doc_id"] == target_doc["doc_id"]
        or selector["selector_name"] in target_doc["text"]
    ):
        raise ValueError(
            "P107 remote/target document identity or local shortcut differs"
        )
    user = base_reader["messages"][0]["content"]
    base_suffix = SEPARATOR + base_proof["question"]
    if not user.endswith(base_suffix):
        raise ValueError("P107 base question boundary differs")
    context = user[: -len(base_suffix)]
    remote_start = selector["remote_doc_reader_start"]
    if (
        context[remote_start : remote_start + len(remote_doc["text"])]
        != remote_doc["text"]
        or context.count(remote_doc["text"]) != 1
    ):
        raise ValueError("P107 remote document not unique in final reader")
    remote_length = len(remote_doc["text"])
    _require_target_before_remote(base_proof["reader_table_span"], remote_start)
    name_span = tuple(
        remote_start + position for position in selector["selector_name_span"]
    )
    value_span = tuple(
        remote_start + position for position in selector["selector_value_span"]
    )
    control_span = tuple(
        remote_start + position for position in selector["control_value_span"]
    )
    if (
        context[name_span[0] : name_span[1]] != selector["selector_name"]
        or context[value_span[0] : value_span[1]] != selector["selector_value"]
        or context[control_span[0] : control_span[1]] != selector["control_value"]
    ):
        raise ValueError("P107 remote selector cell offset differs")
    line_span = tuple(
        remote_start + position for position in selector["selector_line_span"]
    )
    outside = context[: line_span[0]] + "\n" + context[line_span[1] :]
    if any(
        selector["selector_name"] in line and selector["selector_value"] in line
        for line in outside.splitlines()
    ):
        raise ValueError("P107 alternate same-line selector support")
    category, baseline = _solve(
        context,
        remote_start=remote_start,
        remote_length=remote_length,
        selector_name=selector["selector_name"],
        selector_column=selector["selector_column"],
        target_proof=base_proof,
    )
    if category != support["target_category"] or baseline != base_proof["answer"]:
        raise ValueError("P107 source selector does not determine target answer")
    alternative = selector["alternative_target_category"]
    changed_category, hit, hit_sha = _edit_and_solve(
        context,
        value_span,
        alternative,
        remote_start=remote_start,
        remote_length=remote_length,
        selector_name=selector["selector_name"],
        selector_column=selector["selector_column"],
        target_proof=base_proof,
    )
    if changed_category != alternative or hit == baseline:
        raise ValueError(
            "P107 remote selector intervention did not change downstream set"
        )
    control_value = selector["control_value"] + " control"
    unchanged_category, control, control_sha = _edit_and_solve(
        context,
        control_span,
        control_value,
        remote_start=remote_start,
        remote_length=remote_length,
        selector_name=selector["selector_name"],
        selector_column=selector["selector_column"],
        target_proof=base_proof,
    )
    if unchanged_category != category or control != baseline:
        raise ValueError("P107 unrelated remote-cell edit changed answer")
    # Removing the only selected remote value must make the composed program
    # unresolved, rather than allowing the target table to pick a category.
    try:
        _edit_and_solve(
            context,
            value_span,
            "?",
            remote_start=remote_start,
            remote_length=remote_length,
            selector_name=selector["selector_name"],
            selector_column=selector["selector_column"],
            target_proof=base_proof,
        )
    except ValueError:
        pass
    else:
        raise ValueError("P107 remote-cell deletion left an answer")
    target_edit = base_proof["intervention"]
    target_span = (target_edit["reader_cell_start"], target_edit["reader_cell_end"])
    if context[target_span[0] : target_span[1]] != target_edit["old_value"]:
        raise ValueError("P107 target intervention span differs")
    for kind in ("hit", "control"):
        replacement = target_edit[kind + "_value"]
        changed = context[: target_span[0]] + replacement + context[target_span[1] :]
        shift = len(replacement) - len(target_edit["old_value"])
        changed_proof = dict(base_proof)
        table_start, table_end = base_proof["reader_table_span"]
        changed_proof["reader_table_span"] = [table_start, table_end + shift]
        changed_proof["reader_table_sha256"] = hashlib.sha256(
            changed[table_start : table_end + shift].encode()
        ).hexdigest()
        changed_remote_start = remote_start + (
            shift if target_span[1] <= remote_start else 0
        )
        changed_category, changed_answer = _solve(
            changed,
            remote_start=changed_remote_start,
            remote_length=remote_length,
            selector_name=selector["selector_name"],
            selector_column=selector["selector_column"],
            target_proof=changed_proof,
        )
        if (
            changed_category != category
            or changed_answer != target_edit[kind + "_answer"]
            or hashlib.sha256(changed.encode()).hexdigest()
            != target_edit[kind + "_reader_sha256"]
            or (changed_answer == baseline) != (kind == "control")
        ):
            raise ValueError(f"P107 target {kind} intervention differs")
    question = (
        f"Which entries in the '{support['target_heading']}' table of "
        f"{support['target_title']} share the '{support['target_column']}' value "
        f"of '{selector['selector_name']}' in {selector['remote_title']}? "
        "Give the number of entries and their names in alphabetical order."
    )
    if category.casefold() in question.casefold():
        raise ValueError("P107 question leaks resolved category")
    messages = [
        {"role": "user", "content": context + SEPARATOR + question},
        {"role": "assistant", "content": _dump(baseline)},
    ]
    tokenizer = get_tokenizer()
    encoded = tokenize_assistant_only(tokenizer, messages, max_tokens)
    full = len(encoded["input_ids"])
    supervised = sum(label != -100 for label in encoded["labels"])
    if (
        not supervised
        or encoded["labels"]
        != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
    ):
        raise ValueError("P107 assistant-only mask differs")
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    user_text = messages[0]["content"]
    if prompt.count(user_text) != 1:
        raise ValueError("P107 final user occurrence ambiguous")
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    user_start = prompt.index(user_text)
    remote_name_tokens = token_span(
        offsets, user_start + name_span[0], user_start + name_span[1]
    )
    remote_value_tokens = token_span(
        offsets, user_start + value_span[0], user_start + value_span[1]
    )
    target_evidence = []
    for row in base_proof["candidate_rows"]:
        left, right = row["reader_value_span"]
        if context[left:right] != row["value"]:
            raise ValueError("P107 target row evidence differs")
        target_evidence.append(
            {
                "name": row["name"],
                "value": row["value"],
                "selected": row["selected"],
                "reader_value_span": [left, right],
                "prompt_value_token_span": list(
                    token_span(offsets, user_start + left, user_start + right)
                ),
            }
        )
    all_starts = [
        remote_value_tokens[0],
        *(row["prompt_value_token_span"][0] for row in target_evidence),
    ]
    all_ends = [
        remote_value_tokens[1],
        *(row["prompt_value_token_span"][1] for row in target_evidence),
    ]
    if max(all_ends) >= full - supervised:
        raise ValueError("P107 evidence outside final user prompt")
    digest = hashlib.sha256(
        f"{support['world_id']}|{selector['remote_doc_id']}|{selector['selector_name']}|{support['target_doc_id']}|{support['target_heading']}|{support['target_column']}".encode()
    ).hexdigest()[:20]
    sample_id = "p107-wiki-dependent-" + digest
    index = {
        "sample_id": sample_id,
        "example_id": sample_id,
        "task_id": digest,
        "world_id": support["world_id"],
        "source_group": support["source_group"],
        "split": support["split"],
        "source_kind": "real_wiki",
        "domain": support["domain"],
        "topic": support["topic"],
        "operation": "remote_selector_closed_categorical_scan",
        "family": "join_scan",
        "task_type": "remote_selector_closed_categorical_scan",
        "dependency_status": "reader_visible_remote_selector_and_target_hit_control",
        "question_style": "explicit_remote_selector_then_closed_scan",
        "tokenizer_profile": "pinned-chat-template",
        "full_chat_tokens": full,
        "input_tokens": full - supervised,
        "supervised_tokens": supervised,
        "candidate_rows": len(target_evidence),
        "selected_rows": baseline["count"],
        "evidence_token_extent": max(all_ends) - min(all_starts),
        "remote_target_token_gap": abs(
            remote_value_tokens[0]
            - min(row["prompt_value_token_span"][0] for row in target_evidence)
        ),
        "answer_sha256": hashlib.sha256(_dump(baseline).encode()).hexdigest(),
    }
    proof = {
        "sample_id": sample_id,
        "base_sample_id": support["base_sample_id"],
        "source_group": support["source_group"],
        "split": support["split"],
        "source_snapshot_sha256": _sha(snapshot_path),
        "remote_doc_id": remote_doc["doc_id"],
        "remote_title": remote_doc["title"],
        "remote_page_url": remote_doc["page_url"],
        "remote_revision_url": remote_doc["revision_url"],
        "remote_doc_reader_span": [remote_start, remote_start + remote_length],
        "selector_name": selector["selector_name"],
        "selector_column": selector["selector_column"],
        "selector_value": category,
        "selector_name_span": list(name_span),
        "selector_value_span": list(value_span),
        "selector_name_token_span": list(remote_name_tokens),
        "selector_value_token_span": list(remote_value_tokens),
        "remote_hit_value": alternative,
        "remote_hit_answer": hit,
        "remote_hit_reader_sha256": hit_sha,
        "remote_control_value": control_value,
        "remote_control_span": list(control_span),
        "remote_control_answer": control,
        "remote_control_reader_sha256": control_sha,
        "remote_deletion_unresolved": True,
        "target_doc_id": target_doc["doc_id"],
        "target_title": target_doc["title"],
        "target_page_url": target_doc["page_url"],
        "target_revision_url": target_doc["revision_url"],
        "target_heading": support["target_heading"],
        "target_column": support["target_column"],
        "target_table_span": base_proof["reader_table_span"],
        "target_table_sha256": base_proof["reader_table_sha256"],
        "target_hit": target_edit,
        "candidate_rows": target_evidence,
        "question": question,
        "answer": baseline,
        "final_reader_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "claim_limit": "bounded two-document selector-to-table dataflow; alternative prose support beyond same-line checks unproven",
    }
    reader = {
        "sample_id": sample_id,
        "example_id": sample_id,
        "quality_status": "research_candidate",
        "messages": messages,
    }
    return reader, index, proof


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not 1 <= config.get("workers", 0) <= 4
        or not 1 <= config.get("max_full_tokens", 0) <= 131072
    ):
        raise ValueError("P107 compiler config invalid")
    if output_dir.exists() != verify_only:
        raise ValueError("P107 output must be new, or exist for verify-only")
    support = json.loads(_pin(config["support"]).read_text())
    native_manifest_path = _pin(config["native_manifest"])
    native_dir = native_manifest_path.parent
    final = json.loads(_pin(config["native_final_audit"]).read_text())
    pool = json.loads(_pin(config["source_pool"]).read_text())
    if (
        support["reader_tasks_admitted"] != 0
        or support["productive_worlds"] != 2
        or final["native_manifest_sha256"] != config["native_manifest"]["sha256"]
        or final["verified_readers"]
        != json.loads(native_manifest_path.read_text())["candidate_views"]
    ):
        raise ValueError("P107 support/native source lineage differs")
    sources = {row["name"]: row for row in pool["sources"]}
    jobs = [
        (row, native_dir, sources[row["source_group"]], config["max_full_tokens"])
        for row in support["ledger"]
        if row["status"] == "remote_selector_supported"
    ]
    with ProcessPoolExecutor(max_workers=config["workers"]) as executor:
        compiled = list(executor.map(_compile_one, jobs))
    if len({index["task_id"] for _reader, index, _proof in compiled}) != len(compiled):
        raise ValueError("P107 duplicate semantic task ID")
    payloads = {
        "train.jsonl": [
            reader for reader, index, _proof in compiled if index["split"] == "train"
        ],
        "eval.jsonl": [
            reader for reader, index, _proof in compiled if index["split"] == "eval"
        ],
        "sample_index.jsonl": [index for _reader, index, _proof in compiled],
        "audit.jsonl": [proof for _reader, _index, proof in compiled],
    }
    if not verify_only:
        output_dir.mkdir(parents=True)
    for filename, rows in payloads.items():
        content = "".join(_dump(row) + "\n" for row in rows)
        output = output_dir / filename
        if verify_only:
            if output.read_text() != content:
                raise ValueError(f"P107 reader replay drift: {filename}")
        else:
            output.write_text(content)
    indices = payloads["sample_index.jsonl"]
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "code_sha256": _sha(ROOT / "scripts/p107_wiki_dependency_batch.py"),
        "support_sha256": config["support"]["sha256"],
        "native_manifest_sha256": config["native_manifest"]["sha256"],
        "source_pool_sha256": config["source_pool"]["sha256"],
        "candidate_views": len(indices),
        "independent_tasks": len(indices),
        "split_views": dict(sorted(Counter(row["split"] for row in indices).items())),
        "worlds": dict(sorted(Counter(row["world_id"] for row in indices).items())),
        "domains": dict(sorted(Counter(row["domain"] for row in indices).items())),
        "operations": dict(
            sorted(Counter(row["operation"] for row in indices).items())
        ),
        "files_sha256": {name: _sha(output_dir / name) for name in payloads},
        "train_ready": False,
    }
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    output = output_dir / "manifest.json"
    if verify_only:
        if output.read_text() != content:
            raise ValueError("P107 manifest replay drift")
    else:
        output.write_text(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(run(args.config, args.output_dir, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
