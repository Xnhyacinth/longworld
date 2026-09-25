"""Normalize verified native readers into one small candidate shard.

This does not promote training eligibility. Native audits remain sidecars;
the model-visible export contains only sample_id and two messages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
)
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    _answer_hash,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError(f"blank native row: {path}")
            yield json.loads(line)


def _verified_files(directory: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("train_ready") is not False:
        raise ValueError("native source is not candidate-only")
    for name, digest in manifest["files_sha256"].items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or _sha(directory / path) != digest:
            raise ValueError(f"native output changed: {name}")


def _context(messages: list[dict[str, str]]) -> str:
    user = messages[0]["content"]
    marker = "\n\nQUESTION\n"
    if user.count(marker) != 1:
        raise ValueError("reader context/question boundary is missing or repeated")
    return user.split(marker, 1)[0]


def _paper(paper_dir: Path) -> Iterator[tuple[Any, dict[str, Any], str]]:
    manifest_path = paper_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "longworld.p86-frozen-paper-batch.v1.result":
        raise ValueError("wrong paper native schema")
    _verified_files(paper_dir, manifest)
    indexes = _rows(paper_dir / "sample_index.jsonl")
    audits = _rows(paper_dir / "audit.jsonl")
    readers = {
        split: _rows(paper_dir / f"{split}.jsonl") for split in ("train", "eval")
    }
    count = 0
    for index in indexes:
        split = index["split"]
        if split not in readers:
            raise ValueError("invalid paper split")
        reader = next(readers[split], None)
        audit = next(audits, None)
        if (
            reader is None
            or audit is None
            or reader["sample_id"] != index["sample_id"]
            or audit["sample_id"] != index["sample_id"]
            or not audit.get("reader_replay")
            or len(audit.get("line_deletion_checks", []))
            != len(audit.get("evidence_spans", []))
            or len(audit.get("record_deletion_checks", []))
            != len(audit.get("evidence_spans", []))
            or reader["messages"][1]["content"]
            != json.dumps(
                audit["answer"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ):
            raise ValueError("paper index, reader or audit disagree")
        binding = AdapterBinding(
            source_kind="real_paper_revision",
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["operation"],
            evidence_profile="unique_exact_lines_visible_replay",
            tokenizer_profile="pinned-chat-template",
            receipt_path=manifest_path,
            receipt_sha256=_sha(manifest_path),
        )
        candidate = normalize_native_candidate(
            index, reader, binding, context_text=_context(reader["messages"])
        )
        count += 1
        yield candidate, reader, f"{paper_dir}/{split}.jsonl:{count - 1}"
    if (
        count != manifest["quality_admitted_tasks"]
        or next(audits, None) is not None
        or any(next(stream, None) is not None for stream in readers.values())
    ):
        raise ValueError("paper native row counts disagree")


def _state(state_dir: Path) -> Iterator[tuple[Any, dict[str, Any], str]]:
    manifest_path = state_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("schema_version")
        != "longworld.p86-state-shared-world.v2.batch-manifest.v1"
        or manifest.get("train_ready") is not False
    ):
        raise ValueError("wrong state native schema")
    count = 0
    accepted = 0
    for shard in sorted((state_dir / "shards").iterdir()):
        receipt_path = shard / "receipt.json"
        if not receipt_path.exists():
            if not (shard / "reject.json").exists():
                raise ValueError("state shard lacks an outcome")
            continue
        accepted += 1
        receipt = json.loads(receipt_path.read_text())
        world_path, rows_path = shard / "world.json", shard / "rows.jsonl"
        if (
            _sha(world_path) != receipt["world_sha256"]
            or _sha(rows_path) != receipt["rows_sha256"]
        ):
            raise ValueError("state shard receipt changed")
        world = json.loads(world_path.read_text())
        rows = list(_rows(rows_path))
        if len(rows) != receipt["reader_rows"] or len(rows) != len(world["tasks"]):
            raise ValueError("state shard task/reader count differs")
        for position, (row, task) in enumerate(zip(rows, world["tasks"])):
            sample_id = row["example_id"]
            reader = {"sample_id": sample_id, "messages": row["messages"]}
            if (
                row["world_id"] != world["world_id"]
                or row["semantic_task_id"] != sample_id
                or sample_id != world["world_id"] + ":" + task["task_id"]
                or row["operation"] != task["operation"]
                or json.loads(reader["messages"][1]["content"]) != task["answer"]
                or (
                    task["operation"].startswith("asof_")
                    and not task.get("text_interventions")
                )
            ):
                raise ValueError("state task, reader or native intervention disagree")
            binding = AdapterBinding(
                source_kind="controlled_simulation",
                source_group=row["source_group"],
                domain="simulation",
                topic="shared_record_state",
                operation=row["operation"],
                evidence_profile="bounded_visible_record_event_interventions",
                tokenizer_profile="pinned-chat-template",
                receipt_path=receipt_path,
                receipt_sha256=_sha(receipt_path),
            )
            candidate = normalize_native_candidate(
                row, reader, binding, context_text=_context(reader["messages"])
            )
            count += 1
            yield candidate, reader, f"{rows_path}:{position}"
    if accepted != manifest["accepted_jobs"] or count != manifest["reader_rows"]:
        raise ValueError("state native manifest counts disagree")


def _hybrid(hybrid_dir: Path) -> Iterator[tuple[Any, dict[str, Any], str]]:
    manifest_path = hybrid_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != "longworld.p87-hybrid-rfc-pilot.v1":
        raise ValueError("wrong hybrid native schema")
    _verified_files(hybrid_dir, manifest)
    readers = _rows(hybrid_dir / "train.jsonl")
    count = 0
    tasks: set[str] = set()
    for index in _rows(hybrid_dir / "sample_index.jsonl"):
        reader = next(readers, None)
        if reader is None or reader["sample_id"] != index["sample_id"]:
            raise ValueError("hybrid index and reader disagree")
        gold = json.loads(reader["messages"][1]["content"])
        if (
            index["split"] != "train"
            or index["rule_removal_answer"] is not None
            or index["altered_rule_answer"] == gold
            or index["state_removal_answer"] == gold
            or not 0
            <= index["rule_token_span"][0]
            < index["rule_token_span"][1]
            <= index["input_tokens"]
            or not 0
            <= index["state_token_span"][0]
            < index["state_token_span"][1]
            <= index["input_tokens"]
        ):
            raise ValueError("hybrid rule/state intervention or mask span disagrees")
        binding = AdapterBinding(
            source_kind="grounded_simulation",
            source_group=index["source_group"],
            domain="protocol",
            topic="http3_quic",
            operation=index["operation"],
            evidence_profile="real_rule_simulated_state_bounded",
            tokenizer_profile="pinned-chat-template",
            receipt_path=manifest_path,
            receipt_sha256=_sha(manifest_path),
        )
        candidate = normalize_native_candidate(
            index, reader, binding, context_text=_context(reader["messages"])
        )
        tasks.add(candidate.semantic_task_id)
        count += 1
        yield candidate, reader, f"{hybrid_dir}/train.jsonl:{count - 1}"
    if (
        count != manifest["candidate_views"]
        or len(tasks) != manifest["independent_tasks"]
        or next(readers, None) is not None
    ):
        raise ValueError("hybrid native row counts disagree")


def _wiki_numeric(directory: Path) -> Iterator[tuple[Any, dict[str, Any], str]]:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "longworld.p91-wiki-numeric-table.v1.result":
        raise ValueError("wrong Wiki numeric native schema")
    _verified_files(directory, manifest)
    readers = _rows(directory / "train.jsonl")
    audits = _rows(directory / "audit.jsonl")
    count = 0
    tasks: set[str] = set()
    for index in _rows(directory / "sample_index.jsonl"):
        reader = next(readers, None)
        audit = next(audits, None)
        if reader is None or audit is None:
            raise ValueError("Wiki numeric reader or audit is missing")
        sample_id = index["example_id"]
        answer = json.loads(reader["messages"][1]["content"])
        rows = audit.get("candidate_rows", [])
        if (
            reader.get("sample_id") != sample_id
            or audit.get("example_id") != sample_id
            or audit.get("answer") != answer
            or index["split"] != "train"
            or index["operation"] != "closed_numeric_table_interval"
            or index.get("task_type") != index["operation"]
            or len(rows) != index["candidate_rows"]
            or sum(bool(row["selected"]) for row in rows) != index["selected_rows"]
            or not audit.get("intervention", {}).get("hit_answer")
            or audit["intervention"]["hit_answer"] == answer
        ):
            raise ValueError("Wiki numeric index, reader or evidence disagree")
        binding = AdapterBinding(
            source_kind="real_wiki",
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["operation"],
            evidence_profile="closed_numeric_table_all_rows_replayed",
            tokenizer_profile="pinned-chat-template",
            receipt_path=manifest_path,
            receipt_sha256=_sha(manifest_path),
        )
        candidate = normalize_native_candidate(
            index, reader, binding, context_text=_context(reader["messages"])
        )
        tasks.add(candidate.semantic_task_id)
        count += 1
        yield candidate, reader, f"{directory}/train.jsonl:{count - 1}"
    if (
        count != manifest["candidate_views"]
        or len(tasks) != manifest["independent_tasks"]
        or next(readers, None) is not None
        or next(audits, None) is not None
    ):
        raise ValueError("Wiki numeric native row counts disagree")


def _wiki_new_only(directory: Path) -> Iterator[tuple[Any, dict[str, Any], str]]:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get(
        "schema_version"
    ) != "longworld.p92-factorial-batch.v1.wiki-new-only.v2" or manifest.get(
        "global_semantic_tasks"
    ) != manifest.get("candidate_views"):
        raise ValueError("wrong Wiki new-only native schema")
    _verified_files(directory, manifest)
    mask = json.loads((directory / "mask_audit.json").read_text())
    if (
        mask.get("schema_version")
        != "longworld.p92-factorial-batch.v1.wiki-new-only-mask.v1"
        or mask.get("tokenizer")
        != {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION}
        or mask.get("source_manifest_sha256") != _sha(manifest_path)
        or mask.get("checked_rows") != manifest["candidate_views"]
        or mask.get("status") != "all_final_reader_assistant_masks_checked"
        or mask.get("splits") != manifest["splits"]
        or mask.get("train_ready") is not False
    ):
        raise ValueError("Wiki new-only final mask receipt disagrees")
    readers = {
        split: _rows(directory / f"{split}.jsonl") for split in ("train", "eval")
    }
    audits = _rows(directory / "audit.jsonl")
    count = 0
    split_counts: Counter[str] = Counter()
    tasks: set[tuple[str, str]] = set()
    full_tokens = 0
    supervised_tokens = 0
    for index in _rows(directory / "sample_index.jsonl"):
        split = index["split"]
        if split not in readers:
            raise ValueError("invalid Wiki new-only split")
        reader = next(readers[split], None)
        audit = next(audits, None)
        if reader is None or audit is None:
            raise ValueError("Wiki new-only reader or audit is missing")
        sample_id = index["example_id"]
        messages = reader["messages"]
        intervention = audit.get("reader_text_intervention", {})
        if (
            reader["example_id"] != sample_id
            or audit.get("example_id") != sample_id
            or audit.get("source_group") != index["source_group"]
            or index["task_type"] != "table_cell_lookup"
            or intervention.get("status") != index.get("dependency_status")
            or json.loads(messages[1]["content"])
            != audit.get("value_blind_reader_parser_answer")
        ):
            raise ValueError("Wiki new-only index, reader or audit disagree")
        binding = AdapterBinding(
            source_kind="real_wiki",
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["task_type"],
            evidence_profile=index["evidence_status"],
            tokenizer_profile="pinned-chat-template",
            receipt_path=manifest_path,
            receipt_sha256=_sha(manifest_path),
        )
        candidate = normalize_native_candidate(
            index, reader, binding, context_text=_context(messages)
        )
        count += 1
        split_counts[split] += 1
        tasks.add((candidate.source_group, candidate.semantic_task_id))
        full_tokens += candidate.full_chat_tokens
        supervised_tokens += candidate.supervised_tokens
        yield candidate, reader, f"{directory}/{split}.jsonl:{split_counts[split] - 1}"
    if (
        count != manifest["candidate_views"]
        or len(tasks) != manifest["source_scoped_semantic_tasks"]
        or dict(split_counts) != manifest["splits"]
        or full_tokens != mask["full_chat_tokens"]
        or supervised_tokens != mask["supervised_tokens"]
        or next(audits, None) is not None
        or any(next(stream, None) is not None for stream in readers.values())
    ):
        raise ValueError("Wiki new-only native row counts disagree")


def _wiki_generic_year(directory: Path) -> Iterator[tuple[Any, dict[str, Any], str]]:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") not in {
        "longworld.p92-generic-table-scan.v1.result",
        "longworld.p95-wiki-table-sweep.v1.result",
        "longworld.p95-semiclosed-year-table.v1.result",
    }:
        raise ValueError("wrong generic Wiki year-table schema")
    _verified_files(directory, manifest)
    mask_path = directory / "mask_audit.json"
    mask = json.loads(mask_path.read_text())
    if (
        mask.get("schema_version") != "longworld.p94-wiki-native-mask-audit.v1"
        or mask.get("source_manifest_sha256") != _sha(manifest_path)
        or mask.get("source_index_sha256") != _sha(directory / "sample_index.jsonl")
        or mask.get("source_audit_sha256") != _sha(directory / "audit.jsonl")
        or mask.get("tokenizer")
        != {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION}
        or mask.get("checked_rows") != manifest["candidate_views"]
        or mask.get("scope")
        != "native_oracle_and_scoped_intervention_plus_training_assistant_mask"
        or mask.get("train_ready") is not False
    ):
        raise ValueError("generic Wiki year-table mask receipt disagrees")
    readers = {
        split: _rows(directory / f"{split}.jsonl") for split in ("train", "eval")
    }
    audits = _rows(directory / "audit.jsonl")
    counts: Counter[str] = Counter()
    tasks: set[str] = set()
    full_tokens = supervised_tokens = 0
    for index in _rows(directory / "sample_index.jsonl"):
        split = index["split"]
        if split not in readers:
            raise ValueError("generic Wiki year-table split invalid")
        reader = next(readers[split], None)
        audit = next(audits, None)
        if reader is None or audit is None:
            raise ValueError("generic Wiki year-table reader or audit missing")
        sample_id = index["sample_id"]
        reader_hash = hashlib.sha256(
            json.dumps(
                {"sample_id": sample_id, "messages": reader["messages"]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        answer = json.loads(reader["messages"][1]["content"])
        rows = audit.get("candidate_rows", [])
        intervention = audit.get("intervention", {})
        if (
            reader.get("sample_id") != sample_id
            or audit.get("sample_id") != sample_id
            or mask.get("reader_sha256", {}).get(sample_id) != reader_hash
            or audit.get("answer") != answer
            or index["operation"] != "closed_year_table_interval"
            or index.get("task_type") != index["operation"]
            or len(rows) != index["candidate_rows"]
            or sum(bool(row["selected"]) for row in rows) != index["selected_rows"]
            or answer.get("count") != index["selected_rows"]
            or intervention.get("hit_answer") == answer
            or intervention.get("near_miss_value") is None
        ):
            raise ValueError("generic Wiki year-table evidence or answer differs")
        binding = AdapterBinding(
            source_kind="real_wiki",
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["operation"],
            evidence_profile=(
                "complete_visible_projected_year_table_rows_replayed"
                if manifest["schema"] == "longworld.p95-semiclosed-year-table.v1.result"
                else "complete_visible_year_table_rows_replayed"
            ),
            tokenizer_profile="pinned-chat-template",
            receipt_path=manifest_path,
            receipt_sha256=_sha(manifest_path),
        )
        candidate = normalize_native_candidate(
            index, reader, binding, context_text=_context(reader["messages"])
        )
        counts[split] += 1
        tasks.add(candidate.semantic_task_id)
        full_tokens += candidate.full_chat_tokens
        supervised_tokens += candidate.supervised_tokens
        yield candidate, reader, f"{directory}/{split}.jsonl:{counts[split] - 1}"
    if (
        sum(counts.values()) != manifest["candidate_views"]
        or len(tasks) != manifest["independent_tasks"]
        or set(mask.get("reader_sha256", {}))
        != {row["sample_id"] for row in _rows(directory / "sample_index.jsonl")}
        or dict(counts) != mask.get("splits")
        or full_tokens != mask.get("full_chat_tokens")
        or supervised_tokens != mask.get("supervised_tokens")
        or next(audits, None) is not None
        or any(next(stream, None) is not None for stream in readers.values())
    ):
        raise ValueError("generic Wiki year-table native counts disagree")


def _wiki_real_pair_length(
    directory: Path, config_path: Path
) -> Iterator[tuple[Any, dict[str, Any], str]]:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "longworld.p94-real-pair-length.v1.export.v1":
        raise ValueError("wrong real Wiki pair-length schema")
    _verified_files(directory, manifest)
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != "longworld.p94-real-pair-length.v1"
        or manifest.get("config_sha256") != _sha(config_path)
        or manifest.get("composer_sha256")
        != _sha(ROOT / "scripts/compose_p94_real_pair_length.py")
        or manifest.get("tokenizer")
        != {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION}
    ):
        raise ValueError("real Wiki pair-length code, config or tokenizer changed")
    for name, field in (
        ("source_pool", "source_pool_sha256"),
        ("native_manifest", "native_manifest_sha256"),
        ("native_mask_audit", "native_mask_audit_sha256"),
    ):
        pin = config[name]
        relative = Path(pin["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or _sha(ROOT / relative) != pin["sha256"]
            or manifest[field] != pin["sha256"]
        ):
            raise ValueError(f"real Wiki pair-length source pin changed: {name}")
    native_dir = ROOT / Path(config["native_manifest"]["path"]).parent
    _verified_files(native_dir, json.loads((native_dir / "manifest.json").read_text()))
    native_index = {
        row["example_id"]: row for row in _rows(native_dir / "sample_index.jsonl")
    }
    native_readers = {
        row["example_id"]: row for row in _rows(native_dir / "eval.jsonl")
    }
    readers = _rows(directory / "eval.jsonl")
    audits = _rows(directory / "audit.jsonl")
    tasks: set[str] = set()
    lengths: Counter[str] = Counter()
    count = 0
    for index in _rows(directory / "sample_index.jsonl"):
        reader = next(readers, None)
        audit = next(audits, None)
        if reader is None or audit is None:
            raise ValueError("real Wiki pair-length reader or audit missing")
        sample_id = index["example_id"]
        native = native_index.get(index["native_example_id"])
        original = native_readers.get(index["native_example_id"])
        answer = reader["messages"][1]["content"]
        reader_hash = hashlib.sha256(
            json.dumps(
                {"sample_id": sample_id, "messages": reader["messages"]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        spans = audit.get("evidence_token_spans", [])
        if (
            reader["example_id"] != sample_id
            or audit.get("example_id") != sample_id
            or native is None
            or original is None
            or index["semantic_task_id"] != native["task_id"]
            or index["source_group"] != native["source_group"]
            or index["split"] != native["split"]
            or index["operation"] != native["task_type"]
            or answer != original["messages"][1]["content"]
            or index["answer_sha256"] != _answer_hash(answer)
            or audit.get("mask_reader_sha256") != reader_hash
            or audit.get("semantic_task_id") != index["semantic_task_id"]
            or audit.get("reader_intervention", {}).get("status")
            != "two_named_source_year_cells_independently_removed"
            or len(spans) != 2
            or audit.get("evidence_extent_tokens")
            != max(span[1] for span in spans) - min(span[0] for span in spans)
            or index["evidence_extent_tokens"] != audit["evidence_extent_tokens"]
        ):
            raise ValueError("real Wiki pair-length task, mask or evidence differs")
        binding = AdapterBinding(
            source_kind="real_wiki",
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["operation"],
            evidence_profile="two_named_table_year_cells_scoped_reader_replay",
            tokenizer_profile="pinned-chat-template",
            receipt_path=manifest_path,
            receipt_sha256=_sha(manifest_path),
        )
        normalized_reader = {"sample_id": sample_id, "messages": reader["messages"]}
        candidate = normalize_native_candidate(
            index, normalized_reader, binding, context_text=_context(reader["messages"])
        )
        tasks.add(candidate.semantic_task_id)
        lengths[candidate.length_bin] += 1
        yield candidate, normalized_reader, f"{directory}/eval.jsonl:{count}"
        count += 1
    if (
        count != manifest["views"]
        or len(tasks) != manifest["semantic_tasks_reused"]
        or dict(lengths) != manifest["views_by_length"]
        or next(readers, None) is not None
        or next(audits, None) is not None
    ):
        raise ValueError("real Wiki pair-length native row counts disagree")


def _wiki_real_scan_length(
    directory: Path, config_path: Path
) -> Iterator[tuple[Any, dict[str, Any], str]]:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "longworld.p94-real-scan-length.v1.export.v1":
        raise ValueError("wrong real Wiki scan-length schema")
    _verified_files(directory, manifest)
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != "longworld.p94-real-scan-length.v1"
        or manifest.get("config_sha256") != _sha(config_path)
        or manifest.get("composer_sha256")
        != _sha(ROOT / "scripts/compose_p94_real_scan_length.py")
        or manifest.get("pair_helpers_sha256")
        != _sha(ROOT / "scripts/compose_p94_real_pair_length.py")
        or manifest.get("tokenizer")
        != {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION}
    ):
        raise ValueError("real Wiki scan-length code, config or tokenizer changed")
    for name, field in (
        ("source_pool", "source_pool_sha256"),
        ("native_manifest", "native_manifest_sha256"),
    ):
        pin = config[name]
        relative = Path(pin["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or _sha(ROOT / relative) != pin["sha256"]
            or manifest[field] != pin["sha256"]
        ):
            raise ValueError(f"real Wiki scan-length source pin changed: {name}")
    native_dir = ROOT / Path(config["native_manifest"]["path"]).parent
    _verified_files(native_dir, json.loads((native_dir / "manifest.json").read_text()))
    native_index = {
        row["example_id"]: row for row in _rows(native_dir / "sample_index.jsonl")
    }
    native_readers = {
        row["example_id"]: row for row in _rows(native_dir / "train.jsonl")
    }
    readers = _rows(directory / "train.jsonl")
    audits = _rows(directory / "audit.jsonl")
    tasks: set[str] = set()
    lengths: Counter[str] = Counter()
    count = 0
    for index in _rows(directory / "sample_index.jsonl"):
        reader = next(readers, None)
        audit = next(audits, None)
        if reader is None or audit is None:
            raise ValueError("real Wiki scan-length reader or audit missing")
        sample_id = index["example_id"]
        native = native_index.get(index["native_example_id"])
        original = native_readers.get(index["native_example_id"])
        answer = reader["messages"][1]["content"]
        reader_hash = hashlib.sha256(
            json.dumps(
                {"sample_id": sample_id, "messages": reader["messages"]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        spans = audit.get("evidence_token_spans", [])
        intervention = audit.get("reader_intervention", {})
        if (
            reader["example_id"] != sample_id
            or audit.get("example_id") != sample_id
            or native is None
            or original is None
            or index["semantic_task_id"] != native["task_id"]
            or index["source_group"] != native["source_group"]
            or index["split"] != native["split"]
            or index["operation"] != native["task_type"]
            or index["operation"] != "dense_table_interval_scan"
            or answer != original["messages"][1]["content"]
            or index["answer_sha256"] != _answer_hash(answer)
            or audit.get("mask_reader_sha256") != reader_hash
            or audit.get("semantic_task_id") != index["semantic_task_id"]
            or intervention.get("status")
            != "scoped_named_table_hit_and_near_miss_replayed"
            or intervention.get("eligible_row_count") != len(spans)
            or intervention.get("near_miss_answer") != json.loads(answer)
            or len(spans) < 8
            or audit.get("evidence_extent_tokens")
            != max(span[1] for span in spans) - min(span[0] for span in spans)
            or index["evidence_extent_tokens"] != audit["evidence_extent_tokens"]
            or index["last_evidence_to_query_tokens"]
            != audit.get("last_evidence_to_query_tokens")
        ):
            raise ValueError("real Wiki scan-length task, mask or evidence differs")
        binding = AdapterBinding(
            source_kind="real_wiki",
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["operation"],
            evidence_profile="complete_named_table_scan_scoped_reader_replay",
            tokenizer_profile="pinned-chat-template",
            receipt_path=manifest_path,
            receipt_sha256=_sha(manifest_path),
        )
        normalized_reader = {"sample_id": sample_id, "messages": reader["messages"]}
        candidate = normalize_native_candidate(
            index, normalized_reader, binding, context_text=_context(reader["messages"])
        )
        tasks.add(candidate.semantic_task_id)
        lengths[candidate.length_bin] += 1
        yield candidate, normalized_reader, f"{directory}/train.jsonl:{count}"
        count += 1
    if (
        count != manifest["views"]
        or len(tasks) != manifest["semantic_tasks_reused"]
        or dict(lengths) != manifest["views_by_length"]
        or next(readers, None) is not None
        or next(audits, None) is not None
    ):
        raise ValueError("real Wiki scan-length native row counts disagree")


def build(
    paper_dir: Path | None,
    state_dir: Path | None,
    output: Path,
    *,
    hybrid_dir: Path | None = None,
    wiki_numeric_dir: Path | None = None,
    wiki_new_only_dir: Path | None = None,
    wiki_generic_year_dir: Path | None = None,
    wiki_real_pair_length_dir: Path | None = None,
    wiki_real_pair_length_config: Path | None = None,
    wiki_real_scan_length_dir: Path | None = None,
    wiki_real_scan_length_config: Path | None = None,
    generation: str | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("canonical P86 output must be new")
    if generation is not None and not re.fullmatch(r"p[0-9]+", generation):
        raise ValueError("generation must be a p-number label")
    if bool(wiki_real_pair_length_dir) != bool(wiki_real_pair_length_config):
        raise ValueError("pair-length directory and config must be provided together")
    if bool(wiki_real_scan_length_dir) != bool(wiki_real_scan_length_config):
        raise ValueError("scan-length directory and config must be provided together")
    output.parent.mkdir(parents=True, exist_ok=True)
    ledger = CandidateLedger()
    positions = Counter()
    views = Counter()
    lengths = Counter()
    with tempfile.TemporaryDirectory(
        prefix="p86-unified-shard-", dir=output.parent
    ) as raw:
        temp = Path(raw)
        with (
            (temp / "candidate_train.jsonl").open("x", encoding="utf-8") as train,
            (temp / "candidate_eval.jsonl").open("x", encoding="utf-8") as eval_file,
            (temp / "sample_index.jsonl").open("x", encoding="utf-8") as index_file,
        ):
            files = {"train": train, "eval": eval_file}
            lanes = []
            if paper_dir is not None:
                lanes.append(
                    (
                        f"paper_{generation}" if generation else "paper_p86",
                        _paper(paper_dir),
                    )
                )
            if state_dir is not None:
                lanes.append(
                    (
                        f"state_{generation}" if generation else "state_p86",
                        _state(state_dir),
                    )
                )
            if hybrid_dir is not None:
                lanes.append(
                    (
                        f"hybrid_{generation}" if generation else "hybrid_p87",
                        _hybrid(hybrid_dir),
                    )
                )
            if wiki_numeric_dir is not None:
                lanes.append(
                    (
                        f"wiki_numeric_{generation}"
                        if generation
                        else "wiki_numeric_p91",
                        _wiki_numeric(wiki_numeric_dir),
                    )
                )
            if wiki_new_only_dir is not None:
                lanes.append(
                    (
                        f"wiki_new_{generation}" if generation else "wiki_new_p92",
                        _wiki_new_only(wiki_new_only_dir),
                    )
                )
            if wiki_generic_year_dir is not None:
                lanes.append(
                    (
                        f"wiki_generic_year_{generation}"
                        if generation
                        else "wiki_generic_year_p94",
                        _wiki_generic_year(wiki_generic_year_dir),
                    )
                )
            if wiki_real_pair_length_dir is not None:
                lanes.append(
                    (
                        f"wiki_real_pair_length_{generation}"
                        if generation
                        else "wiki_real_pair_length_p94",
                        _wiki_real_pair_length(
                            wiki_real_pair_length_dir, wiki_real_pair_length_config
                        ),
                    )
                )
            if wiki_real_scan_length_dir is not None:
                lanes.append(
                    (
                        f"wiki_real_scan_length_{generation}"
                        if generation
                        else "wiki_real_scan_length_p94",
                        _wiki_real_scan_length(
                            wiki_real_scan_length_dir, wiki_real_scan_length_config
                        ),
                    )
                )
            if not lanes:
                raise ValueError("at least one native candidate lane is required")
            for lane, iterator in lanes:
                for candidate, reader, ref in iterator:
                    ledger.add(candidate)
                    split = candidate.split
                    files[split].write(
                        json.dumps(
                            {
                                "sample_id": candidate.sample_id,
                                "messages": reader["messages"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    index_file.write(
                        json.dumps(
                            {
                                **candidate.to_dict(),
                                "source_name": lane,
                                "native_row_ref": ref,
                                "output_file": f"candidate_{split}.jsonl",
                                "row_index": positions[split],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    positions[split] += 1
                    views[lane] += 1
                    lengths[candidate.length_bin] += 1
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": dict(views),
            "splits": dict(positions),
            "length_bins": dict(sorted(lengths.items())),
            "native_receipts": {
                **(
                    {"paper_manifest_sha256": _sha(paper_dir / "manifest.json")}
                    if paper_dir is not None
                    else {}
                ),
                **(
                    {"state_manifest_sha256": _sha(state_dir / "manifest.json")}
                    if state_dir is not None
                    else {}
                ),
                **(
                    {"hybrid_manifest_sha256": _sha(hybrid_dir / "manifest.json")}
                    if hybrid_dir is not None
                    else {}
                ),
                **(
                    {
                        "wiki_numeric_manifest_sha256": _sha(
                            wiki_numeric_dir / "manifest.json"
                        )
                    }
                    if wiki_numeric_dir is not None
                    else {}
                ),
                **(
                    {
                        "wiki_new_only_manifest_sha256": _sha(
                            wiki_new_only_dir / "manifest.json"
                        ),
                        "wiki_new_only_mask_sha256": _sha(
                            wiki_new_only_dir / "mask_audit.json"
                        ),
                    }
                    if wiki_new_only_dir is not None
                    else {}
                ),
                **(
                    {
                        "wiki_generic_year_manifest_sha256": _sha(
                            wiki_generic_year_dir / "manifest.json"
                        ),
                        "wiki_generic_year_mask_sha256": _sha(
                            wiki_generic_year_dir / "mask_audit.json"
                        ),
                    }
                    if wiki_generic_year_dir is not None
                    else {}
                ),
                **(
                    {
                        "wiki_real_pair_length_manifest_sha256": _sha(
                            wiki_real_pair_length_dir / "manifest.json"
                        )
                    }
                    if wiki_real_pair_length_dir is not None
                    else {}
                ),
                **(
                    {
                        "wiki_real_scan_length_manifest_sha256": _sha(
                            wiki_real_scan_length_dir / "manifest.json"
                        )
                    }
                    if wiki_real_scan_length_dir is not None
                    else {}
                ),
            },
            "files_sha256": {
                name: _sha(temp / name)
                for name in (
                    "candidate_train.jsonl",
                    "candidate_eval.jsonl",
                    "sample_index.jsonl",
                )
            },
            "train_ready": False,
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        os.rename(temp, output)
    return verify_merge(output)


def verify(
    paper_dir: Path | None,
    state_dir: Path | None,
    output: Path,
    *,
    hybrid_dir: Path | None = None,
    wiki_numeric_dir: Path | None = None,
    wiki_new_only_dir: Path | None = None,
    wiki_generic_year_dir: Path | None = None,
    wiki_real_pair_length_dir: Path | None = None,
    wiki_real_pair_length_config: Path | None = None,
    wiki_real_scan_length_dir: Path | None = None,
    wiki_real_scan_length_config: Path | None = None,
    generation: str | None = None,
) -> dict[str, Any]:
    """Recompile from the pinned native lanes and compare final reader hashes."""
    stored = verify_merge(output)
    with tempfile.TemporaryDirectory(
        prefix="p86-unified-replay-", dir=output.parent
    ) as raw:
        rebuilt = build(
            paper_dir,
            state_dir,
            Path(raw) / "merged",
            hybrid_dir=hybrid_dir,
            wiki_numeric_dir=wiki_numeric_dir,
            wiki_new_only_dir=wiki_new_only_dir,
            wiki_generic_year_dir=wiki_generic_year_dir,
            wiki_real_pair_length_dir=wiki_real_pair_length_dir,
            wiki_real_pair_length_config=wiki_real_pair_length_config,
            wiki_real_scan_length_dir=wiki_real_scan_length_dir,
            wiki_real_scan_length_config=wiki_real_scan_length_config,
            generation=generation,
        )
        if rebuilt != stored:
            raise ValueError("P86 unified shard differs from native replay")
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-dir", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--hybrid-dir", type=Path)
    parser.add_argument("--wiki-numeric-dir", type=Path)
    parser.add_argument("--wiki-new-only-dir", type=Path)
    parser.add_argument("--wiki-generic-year-dir", type=Path)
    parser.add_argument("--wiki-real-pair-length-dir", type=Path)
    parser.add_argument("--wiki-real-pair-length-config", type=Path)
    parser.add_argument("--wiki-real-scan-length-dir", type=Path)
    parser.add_argument("--wiki-real-scan-length-config", type=Path)
    parser.add_argument("--generation")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        verify(
            args.paper_dir,
            args.state_dir,
            args.output,
            hybrid_dir=args.hybrid_dir,
            wiki_numeric_dir=args.wiki_numeric_dir,
            wiki_new_only_dir=args.wiki_new_only_dir,
            wiki_generic_year_dir=args.wiki_generic_year_dir,
            wiki_real_pair_length_dir=args.wiki_real_pair_length_dir,
            wiki_real_pair_length_config=args.wiki_real_pair_length_config,
            wiki_real_scan_length_dir=args.wiki_real_scan_length_dir,
            wiki_real_scan_length_config=args.wiki_real_scan_length_config,
            generation=args.generation,
        )
        if args.verify_only
        else build(
            args.paper_dir,
            args.state_dir,
            args.output,
            hybrid_dir=args.hybrid_dir,
            wiki_numeric_dir=args.wiki_numeric_dir,
            wiki_new_only_dir=args.wiki_new_only_dir,
            wiki_generic_year_dir=args.wiki_generic_year_dir,
            wiki_real_pair_length_dir=args.wiki_real_pair_length_dir,
            wiki_real_pair_length_config=args.wiki_real_pair_length_config,
            wiki_real_scan_length_dir=args.wiki_real_scan_length_dir,
            wiki_real_scan_length_config=args.wiki_real_scan_length_config,
            generation=args.generation,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
