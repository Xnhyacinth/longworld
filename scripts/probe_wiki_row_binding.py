"""Probe pinned Wiki source groups and export bounded two-table reader candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_row_binding, wiki_world_bridge
from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    get_tokenizer,
)
from scripts.run_source_pool_batch import _snapshot
from scripts.train_sft import _render_chat, assistant_prefix_length

SCHEMA = "longworld.wiki-row-binding-probe.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        )
    )


def _prior_splits(config: dict[str, Any]) -> dict[str, set[str]]:
    pin = config["prior_source_manifest"]
    path = Path(pin["path"])
    if not path.is_absolute():
        path = ROOT / path
    if _sha(path) != pin["sha256"]:
        raise ValueError("prior source manifest pin mismatch")
    splits: dict[str, set[str]] = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        for title in row["revisions"]:
            splits.setdefault(title.casefold(), set()).add(row["split"])
    return splits


def _tokens(tokenizer: Any, messages: list[dict[str, str]]) -> tuple[int, int, int]:
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    full = _render_chat(tokenizer, messages, generation_prompt=False)
    prompt_ids = list(tokenizer(prompt, truncation=False)["input_ids"])
    full_ids = list(tokenizer(full, truncation=False)["input_ids"])
    input_tokens = assistant_prefix_length(prompt_ids, full_ids)
    return input_tokens, len(full_ids) - input_tokens, len(full_ids)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    if config.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("wrong source pool schema")
    prior = _prior_splits(config)
    all_splits = {title: set(splits) for title, splits in prior.items()}
    snapshots = []
    for source in config["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        snapshots.append((source, snapshot))
        for title in snapshot["source"]["revisions"]:
            all_splits.setdefault(title.casefold(), set()).add(source["split"])
    rows: dict[str, list[dict[str, Any]]] = {"train": [], "eval": []}
    index: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    tokenizer = None
    for source, snapshot in snapshots:
        conflicts = sorted(
            title
            for title in snapshot["source"]["revisions"]
            if all_splits[title.casefold()] != {source["split"]}
        )
        report: dict[str, Any] = {
            "source": source["name"],
            "split": source["split"],
            "domain": source["domain"],
            "topic": source["topic"],
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_sha256": source["snapshot"]["sha256"],
            "documents": len(snapshot["documents"]),
            "join_tasks": 0,
            "split_conflicts": conflicts,
        }
        if conflicts:
            report["status"] = "rejected_prior_or_pool_split_overlap"
            groups.append(report)
            continue
        world = wiki_world_bridge.snapshot_to_world(snapshot)
        tasks = wiki_row_binding.build_join_tasks(world, max_tasks=1000)
        report["join_tasks"] = len(tasks)
        report["status"] = "productive" if tasks else "no_accepted_two_table_join"
        groups.append(report)
        if tasks and tokenizer is None:
            tokenizer = get_tokenizer()
        for task in tasks:
            sample_id = (
                "wiki-row-"
                + hashlib.sha256(
                    f"{snapshot['snapshot_id']}|{task.task_id}".encode()
                ).hexdigest()[:20]
            )
            messages = [
                {
                    "role": "user",
                    "content": task.context + "\n\nQUESTION\n" + task.question,
                },
                {"role": "assistant", "content": task.answer},
            ]
            input_tokens, supervised_tokens, full_tokens = _tokens(tokenizer, messages)
            output_file = f"{source['split']}.jsonl"
            row_index = len(rows[source["split"]])
            rows[source["split"]].append(
                {
                    "example_id": sample_id,
                    "quality_status": "research_candidate",
                    "messages": messages,
                }
            )
            index.append(
                {
                    "example_id": sample_id,
                    "semantic_task_id": task.task_id,
                    "output_file": output_file,
                    "row_index": row_index,
                    "source_group": source["name"],
                    "source_kind": "real_wiki",
                    "split": source["split"],
                    "domain": source["domain"],
                    "topic": source["topic"],
                    "operation": "cross_document_table_join",
                    "evidence_profile": "bounded_table_reader_dependency",
                    "dependency_status": "two_cell_interventions_passed",
                    "document_count": 2,
                    "context_chars": len(task.context),
                    "context_sha256": hashlib.sha256(task.context.encode()).hexdigest(),
                    "input_tokens": input_tokens,
                    "supervised_tokens": supervised_tokens,
                    "full_chat_tokens": full_tokens,
                    "quality_status": "research_candidate",
                    "train_ready": False,
                }
            )
            audits.append(
                {
                    "example_id": sample_id,
                    "snapshot_id": snapshot["snapshot_id"],
                    "snapshot_sha256": source["snapshot"]["sha256"],
                    "first_title": task.first_title,
                    "second_title": task.second_title,
                    "selector_span": task.first_span,
                    "bound_name_span": task.bound_name_span,
                    "target_span": task.target_span,
                    "target_alternatives": task.alternatives,
                    "reader_replay_answer": wiki_row_binding.reader_replay(
                        task, task.context
                    ),
                    "interventions": wiki_row_binding.reader_interventions(task),
                    "duplicate_answer_surface_elsewhere": False,
                    "necessity_scope": "two_final_reader_table_cells_and_exact_or_numeric_duplicate_surface",
                }
            )
    output_dir.mkdir(parents=True)
    files = {
        "train.jsonl": rows["train"],
        "eval.jsonl": rows["eval"],
        "sample_index.jsonl": index,
        "audit.jsonl": audits,
    }
    for name, content in files.items():
        _jsonl(output_dir / name, content)
    manifest = {
        "schema": SCHEMA,
        "config_sha256": _sha(config_path),
        "source_groups": len(groups),
        "productive_groups": sum(g["join_tasks"] > 0 for g in groups),
        "independent_tasks": len(index),
        "views": len(index),
        "postwrite_verified_rows": len(index),
        "splits": dict(Counter(item["split"] for item in index)),
        "domains": dict(Counter(item["domain"] for item in index)),
        "full_chat_tokens": {
            "min": min((item["full_chat_tokens"] for item in index), default=None),
            "max": max((item["full_chat_tokens"] for item in index), default=None),
        },
        "tokenizer": {"model": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION},
        "evidence_profile": "bounded_table_reader_dependency",
        "groups": groups,
        "files_sha256": {name: _sha(output_dir / name) for name in files},
        "train_ready": False,
        "claim_limit": "bounded two-table reader replay and interventions; not global natural-language proof search",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    verify_output(config_path, output_dir)
    return manifest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def verify_output(config_path: Path, output_dir: Path) -> dict[str, int]:
    """Recheck pins, final reader bytes, split-local indexes, gold and tokens."""
    config = json.loads(config_path.read_text())
    manifest = json.loads((output_dir / "manifest.json").read_text())
    if (
        config.get("schema") != "longworld.source-batch-pool.v2"
        or manifest.get("schema") != SCHEMA
        or manifest.get("config_sha256") != _sha(config_path)
        or manifest.get("tokenizer")
        != {"model": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION}
        or manifest.get("evidence_profile") != "bounded_table_reader_dependency"
    ):
        raise ValueError("row binding config or tokenizer pin mismatch")
    for name, digest in manifest["files_sha256"].items():
        if _sha(output_dir / name) != digest:
            raise ValueError(f"row binding output hash mismatch: {name}")
    by_source = {source["name"]: source for source in config["sources"]}
    groups = {group["source"]: group for group in manifest["groups"]}
    if len(groups) != len(by_source) or set(groups) != set(by_source):
        raise ValueError("row binding source group mismatch")
    prior = _prior_splits(config)
    all_splits = {title: set(splits) for title, splits in prior.items()}
    pinned = {}
    for source in config["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        pinned[source["name"]] = snapshot
        for title in snapshot["source"]["revisions"]:
            all_splits.setdefault(title.casefold(), set()).add(source["split"])
    for name, source in by_source.items():
        snapshot = pinned[name]
        group = groups[name]
        conflicts = sorted(
            title
            for title in snapshot["source"]["revisions"]
            if all_splits[title.casefold()] != {source["split"]}
        )
        if (
            group["snapshot_id"] != snapshot["snapshot_id"]
            or group["snapshot_sha256"] != source["snapshot"]["sha256"]
            or group["split"] != source["split"]
            or group["split_conflicts"] != conflicts
        ):
            raise ValueError(f"row binding source pin or split drift: {name}")
    reader_rows = {
        "train.jsonl": _read_jsonl(output_dir / "train.jsonl"),
        "eval.jsonl": _read_jsonl(output_dir / "eval.jsonl"),
    }
    indexes = _read_jsonl(output_dir / "sample_index.jsonl")
    audits = _read_jsonl(output_dir / "audit.jsonl")
    if not (
        len(indexes)
        == len(audits)
        == manifest["independent_tasks"]
        == manifest["views"]
        == manifest["postwrite_verified_rows"]
        == sum(map(len, reader_rows.values()))
    ):
        raise ValueError("row binding export row count mismatch")
    audit_by_id = {row["example_id"]: row for row in audits}
    if len(audit_by_id) != len(audits):
        raise ValueError("duplicate row binding audit ID")
    tasks_by_source = {}
    for name in {item["source_group"] for item in indexes}:
        if groups[name]["split_conflicts"]:
            raise ValueError("candidate source crosses split")
        world = wiki_world_bridge.snapshot_to_world(pinned[name])
        tasks_by_source[name] = {
            task.task_id: task
            for task in wiki_row_binding.build_join_tasks(world, max_tasks=1000)
        }
        if len(tasks_by_source[name]) != groups[name]["join_tasks"]:
            raise ValueError(f"source task count drift: {name}")
    tokenizer = get_tokenizer() if indexes else None
    seen_ids: set[str] = set()
    for item in indexes:
        sample_id = item["example_id"]
        output_file = item["output_file"]
        row_index = item["row_index"]
        if (
            sample_id in seen_ids
            or output_file != f"{item['split']}.jsonl"
            or type(row_index) is not int
            or not 0 <= row_index < len(reader_rows[output_file])
        ):
            raise ValueError(f"row binding row index mismatch: {sample_id}")
        seen_ids.add(sample_id)
        reader_row = reader_rows[output_file][row_index]
        source = by_source[item["source_group"]]
        task = tasks_by_source[item["source_group"]][item["semantic_task_id"]]
        audit = audit_by_id[sample_id]
        messages = reader_row["messages"]
        if (
            reader_row["example_id"] != sample_id
            or item["split"] != source["split"]
            or item["domain"] != source["domain"]
            or item["topic"] != source["topic"]
            or item["source_kind"] != "real_wiki"
            or item["operation"] != "cross_document_table_join"
            or item["evidence_profile"] != "bounded_table_reader_dependency"
            or item["dependency_status"] != "two_cell_interventions_passed"
            or messages
            != [
                {
                    "role": "user",
                    "content": task.context + "\n\nQUESTION\n" + task.question,
                },
                {"role": "assistant", "content": task.answer},
            ]
            or item["context_chars"] != len(task.context)
            or item["context_sha256"]
            != hashlib.sha256(task.context.encode()).hexdigest()
            or audit["reader_replay_answer"]
            != wiki_row_binding.reader_replay(task, task.context)
            or audit["interventions"]
            != list(wiki_row_binding.reader_interventions(task))
            or _tokens(tokenizer, messages)
            != (
                item["input_tokens"],
                item["supervised_tokens"],
                item["full_chat_tokens"],
            )
        ):
            raise ValueError(f"row binding final reader mismatch: {sample_id}")
    for output_file, rows in reader_rows.items():
        if {i["row_index"] for i in indexes if i["output_file"] == output_file} != set(
            range(len(rows))
        ):
            raise ValueError(f"row binding row coverage mismatch: {output_file}")
    if sum(group["join_tasks"] for group in groups.values()) != len(indexes):
        raise ValueError("row binding manifest task count mismatch")
    return {"verified_rows": len(indexes), "verified_source_groups": len(groups)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "source_groups",
                    "productive_groups",
                    "independent_tasks",
                    "splits",
                    "full_chat_tokens",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
