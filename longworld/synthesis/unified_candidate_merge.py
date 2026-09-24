"""Stream verified native readers into one candidate-only long-context bank."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    NativeCandidate,
    TokenCounts,
    normalize_native_candidate,
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _indexed_rows(
    index_path: Path,
    paths: dict[str, Path],
    *,
    index_name: str,
    use_native_row_index: bool = False,
) -> Iterator[tuple[dict[str, Any], dict[str, Any], str]]:
    """Join indexes with giant reader rows using byte offsets, never body maps."""
    offsets: dict[str, list[int]] = {}
    for split, path in paths.items():
        offsets[split] = []
        with path.open("rb") as stream:
            while stream.readline():
                offsets[split].append(stream.tell())
        offsets[split].insert(0, 0)
        offsets[split].pop()
    cursors = Counter()
    with ExitStack() as stack:
        streams = {
            name: stack.enter_context(path.open("rb")) for name, path in paths.items()
        }
        for index in _rows(index_path):
            split = index["split"]
            if split not in {"train", "eval"}:
                raise ValueError(f"invalid {index_name} split")
            output_file = index.get("output_file")
            if output_file is None and use_native_row_index:
                continue  # Explicitly rejected by the native exporter.
            file_key = "short" if output_file == "short.jsonl" else split
            if file_key not in streams:
                raise ValueError(f"invalid {index_name} output file")
            if use_native_row_index and output_file not in {
                "train.jsonl",
                "eval.jsonl",
                "short.jsonl",
            }:
                raise ValueError(f"invalid {index_name} output file")
            position = index["row_index"] if use_native_row_index else cursors[file_key]
            if type(position) is not int or not 0 <= position < len(offsets[file_key]):
                raise ValueError(f"invalid {index_name} row index")
            stream = streams[file_key]
            stream.seek(offsets[file_key][position])
            row = json.loads(stream.readline())
            cursors[file_key] += 1
            yield index, row, f"{paths[file_key]}:{position}"


def _binding(
    index: dict[str, Any],
    receipt: Path,
    *,
    source_kind: str,
    source_group: str,
    domain: str,
    topic: str | None,
    operation: str,
    evidence_profile: str,
    tokenizer_profile: str,
) -> AdapterBinding:
    return AdapterBinding(
        source_kind=source_kind,
        source_group=source_group,
        domain=domain,
        topic=topic,
        operation=operation,
        evidence_profile=evidence_profile,
        tokenizer_profile=tokenizer_profile,
        receipt_path=receipt,
        receipt_sha256=_sha(receipt),
    )


def _wiki(lane: dict[str, Any]) -> Iterator[tuple[Any, dict[str, Any], str]]:
    paths = lane["paths"]
    root = Path(paths["manifest"]).parent.parent
    receipt = root / "result.json"
    for index, row, ref in _indexed_rows(
        Path(paths["sample_index"]),
        {split: Path(paths[split]) for split in ("train", "eval")},
        index_name="Wiki",
    ):
        user = row["messages"][0]["content"]
        context = user[: index["context_chars"]]
        binding = _binding(
            index,
            receipt,
            source_kind=index["source_kind"],
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["task_type"],
            evidence_profile=index["evidence_status"],
            tokenizer_profile=index["token_measurement"],
        )
        yield (
            normalize_native_candidate(index, row, binding, context_text=context),
            row,
            ref,
        )


def _simulation(lane: dict[str, Any]) -> Iterator[tuple[Any, dict[str, Any], str]]:
    paths = lane["paths"]
    receipt = Path(paths["manifest"])
    for index, row, ref in _indexed_rows(
        Path(paths["sample_index"]),
        {split: Path(paths[split]) for split in ("train", "eval")},
        index_name="simulation",
    ):
        user = row["messages"][0]["content"]
        separator = "\n\nQUESTION\n"
        if user.count(separator) != 1:
            raise ValueError("simulation reader source boundary missing")
        context = user.split(separator, 1)[0]
        # The native `input_tokens` counts a separately rendered generation
        # prompt. The loss-mask boundary is full length minus supervised labels.
        prompt_tokens = index["input_tokens"]
        full = index["full_message_tokens"]
        supervised = index["supervised_tokens"]
        if not 0 < prompt_tokens < full:
            raise ValueError("simulation prompt token count is invalid")
        counts = TokenCounts(full - supervised, supervised, full)
        index = {key: value for key, value in index.items() if key != "input_tokens"}
        binding = _binding(
            index,
            receipt,
            source_kind="controlled_simulation",
            source_group=index["world_id"],
            domain="simulation",
            topic=None,
            operation=index["family"],
            evidence_profile="native_solver_recheck",
            tokenizer_profile="pinned-chat-template",
        )
        yield (
            normalize_native_candidate(
                index,
                row,
                binding,
                context_text=context,
                token_counts=counts,
            ),
            row,
            ref,
        )


def _finance(lane: dict[str, Any]) -> Iterator[tuple[Any, dict[str, Any], str]]:
    from scripts.materialize_finance_histories import _load_tokenizer
    from scripts.train_sft import tokenize_assistant_only

    root = Path(lane["paths"]["root"])
    batch = json.loads((root / "BATCH_RECEIPT.json").read_text())
    tokenizers: dict[tuple[str, str], Any] = {}
    for issuer in sorted(job["name"] for job in batch["jobs"]):
        directory = root / issuer
        receipt = directory / "BUILD_RECEIPT.json"
        native = json.loads(receipt.read_text())
        model = native["tokenizer"]
        tokenizer_key = (model["model_id"], model["revision"])
        if tokenizer_key not in tokenizers:
            tokenizers[tokenizer_key] = _load_tokenizer(*tokenizer_key)
        tokenizer = tokenizers[tokenizer_key]
        for task, row in zip(
            _rows(directory / "tasks.jsonl"),
            _rows(directory / "sft_candidates.jsonl"),
            strict=True,
        ):
            if task["sample_id"] != row["sample_id"]:
                raise ValueError("Finance task and reader sample disagree")
            context_path = directory / task["context_path"]
            context = context_path.read_text()
            encoded = tokenize_assistant_only(tokenizer, row["messages"], 262144)
            labels = encoded["labels"]
            full = len(labels)
            supervised = sum(label != -100 for label in labels)
            index = {
                "sample_id": task["sample_id"],
                "semantic_task_id": task["semantic_task_id"],
                "split": task["split"],
                "group_id": task["split_group_id"],
                "context_sha256": task["context_sha256"],
                "evidence_status": "native_visible_evidence",
                "dependency_status": (
                    "native_strict_verified"
                    if task.get("strict_long_dependency_verified")
                    else "native_candidate"
                ),
            }
            binding = _binding(
                index,
                receipt,
                source_kind="real_finance",
                source_group=task["split_group_id"],
                domain="finance",
                topic=issuer,
                operation=task["task_spec"]["family"],
                evidence_profile="native_visible_evidence",
                tokenizer_profile="pinned-chat-template",
            )
            counts = TokenCounts(full - supervised, supervised, full)
            candidate = normalize_native_candidate(
                index, row, binding, context_text=context, token_counts=counts
            )
            yield (
                candidate,
                row,
                f"{directory / 'sft_candidates.jsonl'}:{task['sample_id']}",
            )


def _codeforge(lane: dict[str, Any]) -> Iterator[tuple[Any, dict[str, Any], str]]:
    paths = lane["paths"]
    root = Path(paths["root"])
    receipt = root / "BUILD_RECEIPT.json"
    for index, row, ref in _indexed_rows(
        root / "metadata.jsonl",
        {split: root / f"{split}.jsonl" for split in ("train", "eval", "short")},
        index_name="CodeForge",
        use_native_row_index=True,
    ):
        user = row["messages"][0]["content"]
        separator = "\n\nSource records:\n"
        if user.count(separator) != 1:
            raise ValueError("CodeForge reader source boundary missing")
        context = user.split(separator, 1)[1]
        index = {**index, "group_id": index["source_group_id"]}
        binding = _binding(
            index,
            receipt,
            source_kind="real_code_workflow",
            source_group=index["source_group_id"],
            domain="codeforge",
            topic=Path(index["bank_directory"]).name,
            operation=index["program_id"],
            evidence_profile="native_program_replay",
            tokenizer_profile="pinned-chat-template",
        )
        full = index["full_message_tokens"]
        assistant = index["assistant_tokens"]
        counts = TokenCounts(full - assistant, assistant, full)
        yield (
            normalize_native_candidate(
                index, row, binding, context_text=context, token_counts=counts
            ),
            row,
            ref,
        )


READERS = {
    "wiki_source_pool": _wiki,
    "capability_records": _simulation,
    "finance_taskbank": _finance,
    "codeforge_taskbank": _codeforge,
}


def verify_merge(output: Path) -> dict[str, Any]:
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("schema_version") != "longworld.unified-candidates.v1":
        raise ValueError("wrong unified candidate manifest")
    for name, digest in manifest["files_sha256"].items():
        if _sha(output / name) != digest:
            raise ValueError(f"unified candidate file changed: {name}")
    if sum(manifest["splits"].values()) != manifest["candidate_views"]:
        raise ValueError("unified candidate split count mismatch")
    return manifest


def merge(
    batch_dir: Path, output: Path, lanes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Materialize final reader bytes and index after every native lane verifies."""
    if output.exists():
        raise ValueError("unified candidate merge output already exists")
    ledger = CandidateLedger()
    counts = Counter()
    splits = Counter()
    with tempfile.TemporaryDirectory(prefix="unified-", dir=batch_dir) as temp:
        tmp = Path(temp)
        positions = Counter()
        with (
            (tmp / "candidate_train.jsonl").open("x") as train,
            (tmp / "candidate_eval.jsonl").open("x") as eval_stream,
            (tmp / "sample_index.jsonl").open("x") as index_stream,
        ):
            destinations = {"train": train, "eval": eval_stream}
            for name, lane in lanes.items():
                before = ledger.rows
                for candidate, reader, ref in READERS[lane["kind"]](lane):
                    ledger.add(candidate)
                    split = candidate.split
                    sample = {
                        "sample_id": candidate.sample_id,
                        "messages": reader["messages"],
                    }
                    destinations[split].write(
                        json.dumps(sample, ensure_ascii=False) + "\n"
                    )
                    record = candidate.to_dict()
                    record.update(
                        source_name=name,
                        native_row_ref=ref,
                        output_file=f"candidate_{split}.jsonl",
                        row_index=positions[split],
                    )
                    index_stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    positions[split] += 1
                    splits[split] += 1
                if ledger.rows - before != lane["rows"]:
                    raise ValueError(
                        f"unified merge/native lane row count differs: {name}"
                    )
                counts[name] = ledger.rows - before
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": dict(counts),
            "splits": dict(splits),
            "length_bins": dict(
                sorted(
                    Counter(
                        json.loads(line)["length_bin"]
                        for line in (tmp / "sample_index.jsonl").open()
                    ).items()
                )
            ),
            "files_sha256": {
                name: _sha(tmp / name)
                for name in (
                    "candidate_train.jsonl",
                    "candidate_eval.jsonl",
                    "sample_index.jsonl",
                )
            },
            "train_ready": False,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.rename(tmp, output)
    return manifest


def append(
    batch_dir: Path,
    base_dir: Path,
    output: Path,
    new_lanes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Add only new lanes to a verified bank without re-tokenizing old readers."""
    if output.exists() or not new_lanes:
        raise ValueError("append needs a new output and at least one lane")
    base = verify_merge(base_dir)
    ledger = CandidateLedger()
    existing_names: set[str] = set()
    with (base_dir / "sample_index.jsonl").open() as stream:
        fields = NativeCandidate.__dataclass_fields__
        for line in stream:
            row = json.loads(line)
            existing_names.add(row["source_name"])
            ledger.add(NativeCandidate(**{name: row[name] for name in fields}))
    if ledger.rows != base["candidate_views"] or existing_names & new_lanes.keys():
        raise ValueError("base candidate index or appended lane identity changed")
    positions = Counter(base["splits"])
    length_bins = Counter(base["length_bins"])
    views_by_lane = Counter(base["views_by_lane"])
    with tempfile.TemporaryDirectory(prefix="unified-append-", dir=batch_dir) as temp:
        tmp = Path(temp)
        for name in (
            "candidate_train.jsonl",
            "candidate_eval.jsonl",
            "sample_index.jsonl",
        ):
            shutil.copyfile(base_dir / name, tmp / name)
            if _sha(tmp / name) != base["files_sha256"][name]:
                raise ValueError(f"base reader changed during append: {name}")
        with (
            (tmp / "candidate_train.jsonl").open("a") as train,
            (tmp / "candidate_eval.jsonl").open("a") as eval_stream,
            (tmp / "sample_index.jsonl").open("a") as index_stream,
        ):
            destinations = {"train": train, "eval": eval_stream}
            for name, lane in new_lanes.items():
                before = ledger.rows
                for candidate, reader, ref in READERS[lane["kind"]](lane):
                    ledger.add(candidate)
                    split = candidate.split
                    destinations[split].write(
                        json.dumps(
                            {
                                "sample_id": candidate.sample_id,
                                "messages": reader["messages"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    record = candidate.to_dict()
                    record.update(
                        source_name=name,
                        native_row_ref=ref,
                        output_file=f"candidate_{split}.jsonl",
                        row_index=positions[split],
                    )
                    index_stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    positions[split] += 1
                    length_bins[candidate.length_bin] += 1
                if ledger.rows - before != lane["rows"]:
                    raise ValueError(f"appended/native lane row count differs: {name}")
                views_by_lane[name] = ledger.rows - before
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": dict(views_by_lane),
            "splits": dict(positions),
            "length_bins": dict(sorted(length_bins.items())),
            "base_manifest_sha256": _sha(base_dir / "manifest.json"),
            "appended_lanes": sorted(new_lanes),
            "files_sha256": {
                name: _sha(tmp / name)
                for name in (
                    "candidate_train.jsonl",
                    "candidate_eval.jsonl",
                    "sample_index.jsonl",
                )
            },
            "train_ready": False,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.rename(tmp, output)
    return manifest
