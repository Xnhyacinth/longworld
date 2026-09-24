"""Add frozen, same-split documents between necessary Wiki JOIN evidence.

This creates longer reader views of existing semantic tasks. It does not count
them as new independent questions, and it retains bounded table interventions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_row_binding, wiki_world_bridge
from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_wiki_join_positions import token_span
from scripts.discover_connected_wiki_pairs import _prior_unified_titles
from scripts.run_source_pool_batch import _snapshot
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.wiki-join-distance-compose.v1"
SEPARATOR = "\n\nQUESTION\n"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pin(pin: dict) -> Path:
    path = Path(pin["path"])
    if not path.is_absolute():
        path = ROOT / path
    if _sha(path) != pin["sha256"]:
        raise ValueError(f"source pin changed: {path}")
    return path


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def insert_filler(
    task: wiki_row_binding.JoinTask, documents: list[dict]
) -> wiki_row_binding.JoinTask:
    """Keep both source documents byte-for-byte while shifting second spans."""
    filler = "".join(
        f"\n\n=== DOCUMENT: {doc['title']} ===\n{doc['text']}" for doc in documents
    )
    shift = len(filler)
    first_end = task.first_text_span[1]
    return replace(
        task,
        context=task.context[:first_end] + filler + task.context[first_end:],
        second_text_span=(
            task.second_text_span[0] + shift,
            task.second_text_span[1] + shift,
        ),
        target_span=(task.target_span[0] + shift, task.target_span[1] + shift),
    )


def _token_positions(tokenizer, messages: list[dict], task) -> dict:
    user = messages[0]["content"]
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    user_start = prompt.find(user)
    if user_start < 0 or prompt.count(user) != 1:
        raise ValueError("composed reader is not unique in chat template")
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    selector = token_span(
        offsets,
        user_start + task.first_span[0],
        user_start + task.first_span[1],
    )
    target = token_span(
        offsets,
        user_start + task.target_span[0],
        user_start + task.target_span[1],
    )
    query = token_span(
        offsets,
        user_start + len(task.context) + len(SEPARATOR),
        user_start + len(user),
    )[0]
    return {
        "selector_token_span": selector,
        "target_token_span": target,
        "query_token_start": query,
        "evidence_extent_tokens": max(selector[1], target[1])
        - min(selector[0], target[0]),
    }


def run(config_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("distance-composed output must be new")
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != SCHEMA:
        raise ValueError("wrong distance-composition config")
    source_pool = json.loads(_pin(config["source_pool"]).read_text())
    filler_pool = json.loads(_pin(config["filler_pool"]).read_text())
    native_manifest_path = _pin(config["native_manifest"])
    native_dir = native_manifest_path.parent
    native_manifest = json.loads(native_manifest_path.read_text())
    for name, digest in native_manifest["files_sha256"].items():
        if _sha(native_dir / name) != digest:
            raise ValueError("native row JOIN export changed")
    source = next(
        item for item in source_pool["sources"] if item["name"] == config["source_name"]
    )
    filler_source = next(
        item
        for item in filler_pool["sources"]
        if item["name"] == config["filler_source"]
    )
    if source["split"] != filler_source["split"] or source["split"] != "train":
        raise ValueError("source and filler must share the train split")
    registry = _prior_unified_titles(config["prior_unified_batch"])
    source_snapshot = _snapshot(ROOT, source["snapshot"])
    filler_snapshot = _snapshot(ROOT, filler_source["snapshot"])
    filler_by_title = {doc["title"]: doc for doc in filler_snapshot["documents"]}
    fillers = [filler_by_title[title] for title in config["filler_titles"]]
    if len(fillers) != len({doc["title"] for doc in fillers}) or len(fillers) < 1:
        raise ValueError("filler titles must be unique and nonempty")
    source_titles = {doc["title"] for doc in source_snapshot["documents"]}
    for doc in fillers:
        if doc["title"] in source_titles or registry.get(doc["title"].casefold()) != {
            "train"
        }:
            raise ValueError("filler title overlaps target or split")
    tasks = wiki_row_binding.build_join_tasks(
        wiki_world_bridge.snapshot_to_world(source_snapshot), max_tasks=1000
    )
    original_index = {
        row["example_id"]: row for row in _rows(native_dir / "sample_index.jsonl")
    }
    original_readers = {
        row["example_id"]: row for row in _rows(native_dir / "train.jsonl")
    }
    tokenizer = get_tokenizer()
    rows = []
    index = []
    audits = []
    rejected: dict[str, int] = {}
    for task in tasks:
        native_id = (
            "wiki-row-"
            + hashlib.sha256(
                f"{source_snapshot['snapshot_id']}|{task.task_id}".encode()
            ).hexdigest()[:20]
        )
        native = original_index.get(native_id)
        old_reader = original_readers.get(native_id)
        if (
            native is None
            or old_reader is None
            or native["split"] != "train"
            or old_reader["messages"][0]["content"]
            != task.context + SEPARATOR + task.question
            or old_reader["messages"][1]["content"] != task.answer
        ):
            raise ValueError("recompiled task differs from frozen native JOIN")
        bound_name = task.context[task.bound_name_span[0] : task.bound_name_span[1]]
        if any(
            wiki_row_binding._source_contains(task.answer, doc["text"])
            or wiki_row_binding._source_contains(bound_name, doc["text"])
            for doc in fillers
        ):
            rejected["filler_repeats_answer_or_bound_entity"] = (
                rejected.get("filler_repeats_answer_or_bound_entity", 0) + 1
            )
            continue
        composed = insert_filler(task, fillers)
        if wiki_row_binding.reader_replay(composed, composed.context) != task.answer:
            raise ValueError("composed reader changed answer")
        interventions = wiki_row_binding.reader_interventions(composed)
        messages = [
            {"role": "user", "content": composed.context + SEPARATOR + task.question},
            {"role": "assistant", "content": task.answer},
        ]
        try:
            encoded = tokenize_assistant_only(
                tokenizer, messages, config["maximum_full_chat_tokens"]
            )
        except ValueError:
            rejected["over_token_budget"] = rejected.get("over_token_budget", 0) + 1
            continue
        full = len(encoded["labels"])
        if full < config["minimum_full_chat_tokens"]:
            rejected["under_token_budget"] = rejected.get("under_token_budget", 0) + 1
            continue
        positions = _token_positions(tokenizer, messages, composed)
        if (
            positions["evidence_extent_tokens"]
            < config["minimum_evidence_extent_tokens"]
        ):
            rejected["short_evidence_extent"] = (
                rejected.get("short_evidence_extent", 0) + 1
            )
            continue
        masked = sum(label == -100 for label in encoded["labels"])
        if (
            max(positions["selector_token_span"][1], positions["target_token_span"][1])
            > masked
        ):
            raise ValueError("composed evidence crosses assistant mask")
        sample_id = (
            "wiki-row-distance-"
            + hashlib.sha256(
                f"{native_id}|{','.join(config['filler_titles'])}".encode()
            ).hexdigest()[:20]
        )
        rows.append({"example_id": sample_id, "messages": messages})
        index.append(
            {
                "example_id": sample_id,
                "semantic_task_id": task.task_id,
                "source_group": source["name"],
                "source_kind": "real_wiki",
                "split": "train",
                "domain": source["domain"],
                "topic": source["topic"],
                "operation": "cross_document_table_join",
                "evidence_profile": "bounded_table_reader_dependency",
                "dependency_status": "two_cell_interventions_passed",
                "output_file": "train.jsonl",
                "row_index": len(rows) - 1,
                "context_chars": len(composed.context),
                "context_sha256": hashlib.sha256(composed.context.encode()).hexdigest(),
                "input_tokens": masked,
                "supervised_tokens": full - masked,
                "full_chat_tokens": full,
                "filler_documents": len(fillers),
                "evidence_extent_tokens": positions["evidence_extent_tokens"],
                "train_ready": False,
            }
        )
        audits.append(
            {
                "example_id": sample_id,
                "native_example_id": native_id,
                "source_snapshot_sha256": source["snapshot"]["sha256"],
                "filler_snapshot_sha256": filler_source["snapshot"]["sha256"],
                "filler_titles": config["filler_titles"],
                "selector_span": composed.first_span,
                "target_span": composed.target_span,
                "interventions": interventions,
                **positions,
                "necessity_scope": "two_named_table_cells_after_same_split_real_document_insertion",
            }
        )
    if not rows:
        raise ValueError("no distance-composed tasks passed")
    output.mkdir(parents=True)
    for name, items in (
        ("train.jsonl", rows),
        ("eval.jsonl", []),
        ("sample_index.jsonl", index),
        ("audit.jsonl", audits),
    ):
        (output / name).write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in items
            ),
            encoding="utf-8",
        )
    manifest = {
        "schema": SCHEMA + ".export.v1",
        "config_sha256": _sha(config_path),
        "native_manifest_sha256": _sha(native_manifest_path),
        "source_snapshot_sha256": source["snapshot"]["sha256"],
        "filler_snapshot_sha256": filler_source["snapshot"]["sha256"],
        "views": len(rows),
        "new_independent_semantic_tasks": 0,
        "source_semantic_tasks_reused": len({row["semantic_task_id"] for row in index}),
        "rejected_by_reason": rejected,
        "full_chat_tokens": {
            "min": min(row["full_chat_tokens"] for row in index),
            "max": max(row["full_chat_tokens"] for row in index),
        },
        "evidence_extent_tokens": {
            "min": min(row["evidence_extent_tokens"] for row in index),
            "max": max(row["evidence_extent_tokens"] for row in index),
        },
        "files_sha256": {
            name: _sha(output / name)
            for name in (
                "train.jsonl",
                "eval.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
            )
        },
        "train_ready": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify_output(config_path: Path, output: Path) -> dict:
    """Recompile frozen sources and compare every final reader and audit byte."""
    stored = json.loads((output / "manifest.json").read_text())
    if stored.get("schema") != SCHEMA + ".export.v1":
        raise ValueError("wrong distance-composed export schema")
    for name, expected in stored["files_sha256"].items():
        if _sha(output / name) != expected:
            raise ValueError(f"distance-composed file changed: {name}")
    with tempfile.TemporaryDirectory(prefix="wiki-distance-verify-") as raw:
        regenerated = run(config_path, Path(raw) / "batch")
        if regenerated != stored:
            raise ValueError("distance-composed manifest differs from replay")
        for name in stored["files_sha256"]:
            if _sha(output / name) != _sha(Path(raw) / "batch" / name):
                raise ValueError(f"distance-composed reader differs: {name}")
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        verify_output(args.config, args.output)
        if args.verify_only
        else run(args.config, args.output)
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
