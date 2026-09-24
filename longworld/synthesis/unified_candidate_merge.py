"""Stream verified native readers into one candidate-only long-context bank."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from itertools import islice
from pathlib import Path
from typing import Any

from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    NativeCandidate,
    TokenCounts,
    _answer_hash,
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
    accepted_output_files: set[str] | None = None,
) -> Iterator[tuple[dict[str, Any], dict[str, Any], str]]:
    """Join giant readers in one pass, retaining offsets only when revisited."""
    offsets: dict[str, list[int]] = {name: [] for name in paths}
    scanned_ends = {name: 0 for name in paths}
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
            if use_native_row_index and output_file not in (
                accepted_output_files
                if accepted_output_files is not None
                else {"train.jsonl", "eval.jsonl", "short.jsonl"}
            ):
                raise ValueError(f"invalid {index_name} output file")
            stream = streams[file_key]
            if use_native_row_index:
                position = index["row_index"]
                if type(position) is not int or position < 0:
                    raise ValueError(f"invalid {index_name} row index")
                if position < len(offsets[file_key]):
                    stream.seek(offsets[file_key][position])
                    line = stream.readline()
                else:
                    stream.seek(scanned_ends[file_key])
                    while len(offsets[file_key]) <= position:
                        start = stream.tell()
                        line = stream.readline()
                        if not line:
                            raise ValueError(f"invalid {index_name} row index")
                        offsets[file_key].append(start)
                    scanned_ends[file_key] = stream.tell()
            else:
                position = cursors[file_key]
                line = stream.readline()
            if not line:
                raise ValueError(f"invalid {index_name} row index")
            row = json.loads(line)
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


def _wiki_delta(lane: dict[str, Any]) -> Iterator[tuple[Any, dict[str, Any], str]]:
    paths = lane["paths"]
    fields = NativeCandidate.__dataclass_fields__
    for index, row, ref in _indexed_rows(
        Path(paths["sample_index"]),
        {split: Path(paths[split]) for split in ("train", "eval")},
        index_name="Wiki delta",
        use_native_row_index=True,
        accepted_output_files={"candidate_train.jsonl", "candidate_eval.jsonl"},
    ):
        if index["output_file"] != f"candidate_{index['split']}.jsonl":
            raise ValueError("Wiki delta split/output file mismatch")
        candidate = NativeCandidate(**{name: index[name] for name in fields})
        if row.get("sample_id") != candidate.sample_id:
            raise ValueError("Wiki delta reader/index sample ID mismatch")
        messages = row.get("messages")
        if (
            not isinstance(messages, list)
            or len(messages) != 2
            or [message.get("role") for message in messages] != ["user", "assistant"]
            or _answer_hash(messages[1]["content"]) != candidate.answer_sha256
        ):
            raise ValueError("Wiki delta reader/answer mismatch")
        yield candidate, row, ref


def _wiki_row_join(lane: dict[str, Any]) -> Iterator[tuple[Any, dict[str, Any], str]]:
    paths = lane["paths"]
    receipt = Path(paths["manifest"])
    for index, row, ref in _indexed_rows(
        Path(paths["sample_index"]),
        {split: Path(paths[split]) for split in ("train", "eval")},
        index_name="Wiki row join",
        use_native_row_index=True,
    ):
        if index["output_file"] != f"{index['split']}.jsonl":
            raise ValueError("Wiki row join split/output file mismatch")
        context = row["messages"][0]["content"][: index["context_chars"]]
        binding = _binding(
            index,
            receipt,
            source_kind="real_wiki",
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["operation"],
            evidence_profile=index["evidence_profile"],
            tokenizer_profile="pinned-chat-template",
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
        # This is a separately tokenized generation prompt, not the SFT mask
        # boundary. Chat-template suffixes and cross-boundary tokenization can
        # make it equal to, or slightly longer than, the completed chat.
        if type(prompt_tokens) is not int or prompt_tokens <= 0:
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


def _shared_record(
    lane: dict[str, Any],
) -> Iterator[tuple[Any, dict[str, Any], str]]:
    """Read verified paired-operation worlds without exposing their sidecars."""
    for raw_receipt in lane["receipt_paths"]:
        receipt = Path(raw_receipt)
        shard = receipt.parent
        native = json.loads(receipt.read_text())
        rows_path = shard / "rows.jsonl"
        if (
            _sha(rows_path) != native["rows_sha256"]
            or _sha(shard / "world.json") != native["world_sha256"]
        ):
            raise ValueError("shared-record shard changed")
        for row in _rows(rows_path):
            user = row["messages"][0]["content"]
            separator = "\n\nQUESTION\n"
            if user.count(separator) != 1 or row["train_ready"] is not False:
                raise ValueError("shared-record reader boundary or status changed")
            context = user.split(separator, 1)[0]
            index = {
                "sample_id": row["example_id"],
                "semantic_task_id": row["semantic_task_id"],
                "group_id": row["world_id"],
                "split": row["split"],
                "context_sha256": row["context_sha256"],
                "evidence_status": "controlled_shared_row_deletion",
                "dependency_status": "native_shared_row_deletion_only",
            }
            binding = _binding(
                index,
                receipt,
                source_kind="controlled_simulation",
                source_group=row["world_id"],
                domain="simulation",
                topic="shared_record",
                operation=row["operation"],
                evidence_profile="controlled_shared_row_deletion",
                tokenizer_profile="pinned-chat-template",
            )
            counts = TokenCounts(
                row["input_tokens"],
                row["supervised_tokens"],
                row["full_chat_tokens"],
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
                f"{rows_path}:{row['example_id']}",
            )


def _finance(
    lane: dict[str, Any], *, workers: int = 1
) -> Iterator[tuple[Any, dict[str, Any], str]]:
    from scripts.materialize_finance_histories import _load_tokenizer
    from scripts.train_sft import tokenize_assistant_only

    if workers < 1:
        raise ValueError("workers must be positive")
    root = Path(lane["paths"]["root"])
    batch = json.loads((root / "BATCH_RECEIPT.json").read_text())
    tokenizers: dict[tuple[str, str], Any] = {}

    def issuer_rows(
        issuer: str, executor: ThreadPoolExecutor | None
    ) -> Iterator[tuple[Any, dict[str, Any], str]]:
        directory = root / issuer
        receipt = directory / "BUILD_RECEIPT.json"
        native = json.loads(receipt.read_text())
        model = native["tokenizer"]
        tokenizer_key = (model["model_id"], model["revision"])
        if tokenizer_key not in tokenizers:
            tokenizers[tokenizer_key] = _load_tokenizer(*tokenizer_key)
        tokenizer = tokenizers[tokenizer_key]

        def normalize(pair: tuple[dict[str, Any], dict[str, Any]]):
            task, row = pair
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
            return (
                candidate,
                row,
                f"{directory / 'sft_candidates.jsonl'}:{task['sample_id']}",
            )

        source = zip(
            _rows(directory / "tasks.jsonl"),
            _rows(directory / "sft_candidates.jsonl"),
            strict=True,
        )
        if executor is None:
            for pair in source:
                yield normalize(pair)
        else:
            # At most 2 * workers giant reader rows can be in flight. map()
            # returns in source order and propagates every normalization error.
            while pairs := list(islice(source, workers * 2)):
                yield from executor.map(normalize, pairs)

    if workers == 1:
        for issuer in sorted(job["name"] for job in batch["jobs"]):
            yield from issuer_rows(issuer, None)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for issuer in sorted(job["name"] for job in batch["jobs"]):
                yield from issuer_rows(issuer, executor)


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
    "wiki_candidate_delta": _wiki_delta,
    "wiki_row_join_probe": _wiki_row_join,
    "capability_records": _simulation,
    "shared_record_taskbank": _shared_record,
    "finance_taskbank": _finance,
    "codeforge_taskbank": _codeforge,
}


def _native_reader_rows(lane: dict[str, Any], workers: int):
    if lane["kind"] == "finance_taskbank":
        return _finance(lane, workers=workers)
    return READERS[lane["kind"]](lane)


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
    batch_dir: Path,
    output: Path,
    lanes: dict[str, dict[str, Any]],
    *,
    workers: int = 1,
) -> dict[str, Any]:
    """Materialize final reader bytes and index after every native lane verifies."""
    if workers < 1:
        raise ValueError("workers must be positive")
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
                for candidate, reader, ref in _native_reader_rows(lane, workers):
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
    *,
    workers: int = 1,
) -> dict[str, Any]:
    """Add only new lanes to a verified bank without re-tokenizing old readers."""
    if workers < 1:
        raise ValueError("workers must be positive")
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
                for candidate, reader, ref in _native_reader_rows(lane, workers):
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
