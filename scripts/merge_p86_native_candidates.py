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
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge


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


def build(
    paper_dir: Path | None,
    state_dir: Path | None,
    output: Path,
    *,
    hybrid_dir: Path | None = None,
    wiki_numeric_dir: Path | None = None,
    wiki_new_only_dir: Path | None = None,
    generation: str | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("canonical P86 output must be new")
    if generation is not None and not re.fullmatch(r"p[0-9]+", generation):
        raise ValueError("generation must be a p-number label")
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
            generation=args.generation,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
