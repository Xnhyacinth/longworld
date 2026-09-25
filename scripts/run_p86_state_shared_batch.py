"""Bounded process-pool batch for shared-record as-of data candidates.

Freeze plan, world, reader rows and receipts per job.  Program/provenance
remain in world.json; only messages and admission metadata reach reader rows.
The output is candidate-only and does not claim natural-document transfer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from bisect import bisect_right
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import p86_state_shared_world as shared
from scripts.run_capability_records import MODEL, REVISION
from scripts.run_shared_record_taskbank import _tokenizer
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p86-state-shared-batch-config.v1"
BINS = (32768, 65536, 131072, 262144)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan(config: dict) -> list[dict]:
    if config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported P86 batch config")
    for key in ("seed_base", "consumed_records", "variants", "max_full_chat_tokens"):
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError(f"invalid {key}")
    cells = config.get("cells")
    if not isinstance(cells, list) or not cells:
        raise ValueError("P86 plan needs cells")
    jobs = []
    for cell_index, cell in enumerate(cells):
        if set(cell) != {"length_records", "depth", "worlds"} or any(
            type(cell[key]) is not int or cell[key] < 1 for key in cell
        ):
            raise ValueError("invalid P86 cell")
        for index in range(cell["worlds"]):
            seed = config["seed_base"] + len(jobs)
            jobs.append(
                {
                    "job_id": f"cell{cell_index:02d}-seed{seed}",
                    "cell_id": f"L{cell['length_records']}-H{cell['depth']}",
                    "seed": seed,
                    "length_records": cell["length_records"],
                    "depth": cell["depth"],
                    "consumed_records": config["consumed_records"],
                    "variants": config["variants"],
                }
            )
    return jobs


def _generate(job: dict) -> tuple[dict, dict | None, str | None]:
    try:
        world = shared.build_world(
            job["seed"],
            job["length_records"],
            job["consumed_records"],
            job["depth"],
            job["variants"],
        )
        return job, world, None
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return job, None, f"{type(exc).__name__}: {exc}"


def _fact_token_positions(
    context: str, messages: list[dict], tokenizer, expected_ids: list[int]
) -> dict[str, tuple[int, int]]:
    """Map visible fact lines to positions in the actual rendered chat tokens."""
    rendered = _render_chat(tokenizer, messages, generation_prompt=False)
    start = rendered.find(context)
    if start < 0 or rendered.find(context, start + 1) >= 0:
        raise ValueError("reader context not uniquely placed in chat template")
    encoded = tokenizer(rendered, truncation=False, return_offsets_mapping=True)
    if list(encoded["input_ids"]) != expected_ids:
        raise ValueError("offset tokenizer disagrees with final assistant-mask tokens")
    offsets = encoded["offset_mapping"]
    lines = context.splitlines(keepends=True)
    starts = []
    cursor = 0
    for line in lines:
        starts.append(cursor)
        cursor += len(line)
    ids = [None] + [json.loads(line)["id"] for line in lines[1:]]
    positions: dict[str, list[int]] = {}
    for token_index, (left, right) in enumerate(offsets):
        if right <= left or not start <= left < start + len(context):
            continue
        line_index = bisect_right(starts, left - start) - 1
        if line_index < 1:
            continue
        fact_id = ids[line_index]
        if fact_id not in positions:
            positions[fact_id] = [token_index, token_index]
        else:
            positions[fact_id][1] = token_index
    if len(positions) != len(ids) - 1:
        raise ValueError("one or more visible facts lack final-chat token positions")
    return {key: (value[0], value[1]) for key, value in positions.items()}


def _reader_rows(world: dict, tokenizer, max_tokens: int) -> list[dict]:
    rows = []
    first_input_ids = None
    for task in world["tasks"]:
        messages = [
            {
                "role": "user",
                "content": world["reader_context"]
                + "\n\nQUESTION\n"
                + task["instruction"],
            },
            {"role": "assistant", "content": _dump(task["answer"])},
        ]
        encoded = tokenize_assistant_only(tokenizer, messages, max_tokens)
        if first_input_ids is None:
            first_input_ids = encoded["input_ids"]
        labels = encoded["labels"]
        masked = sum(label == -100 for label in labels)
        supervised = len(labels) - masked
        if (
            not masked
            or not supervised
            or any(value != -100 for value in labels[:masked])
        ):
            raise ValueError("invalid assistant-only mask")
        if any(value == -100 for value in labels[masked:]):
            raise ValueError("non-contiguous assistant supervision")
        rows.append(
            {
                "schema_version": VERSION_READER,
                "example_id": world["world_id"] + ":" + task["task_id"],
                "semantic_task_id": world["world_id"] + ":" + task["task_id"],
                "world_id": world["world_id"],
                "source_group": world["world_id"],
                "source_kind": "controlled_simulation",
                "split": "eval" if world["seed"] % 5 == 0 else "train",
                "operation": task["operation"],
                "pair_id": world["world_id"] + ":" + task["pair_id"],
                "context_sha256": world["context_sha256"],
                "messages": messages,
                "full_chat_tokens": len(labels),
                "input_tokens": masked,
                "supervised_tokens": supervised,
                "mask_contract": "assistant_only_no_truncation",
                "train_ready": False,
            }
        )
    positions = _fact_token_positions(
        world["reader_context"],
        rows[0]["messages"],
        tokenizer,
        first_input_ids,
    )
    if any(row["full_chat_tokens"] > max_tokens for row in rows):
        raise ValueError("full chat exceeds token budget")
    for row, task in zip(rows, world["tasks"]):
        required = [positions[fact_id] for fact_id in task["consumed"]]
        row["required_fact_span_tokens"] = (
            max(end for _, end in required) - min(start for start, _ in required) + 1
        )
        row["last_required_fact_to_question_tokens"] = row["input_tokens"] - max(
            end for _, end in required
        )
    return rows


VERSION_READER = shared.VERSION + ".reader.v1"


def _receipt(
    job: dict, world: dict, rows: list[dict], world_sha: str, rows_sha: str
) -> dict:
    return {
        "job": job,
        "world_id": world["world_id"],
        "world_sha256": world_sha,
        "rows_sha256": rows_sha,
        "reader_rows": len(rows),
        "shared_state_facts": sum(
            len(value["state_fact_ids"]) for value in world["shared_fact_ids"].values()
        ),
        "shared_cross_record_facts": sum(
            len(value["cross_operation_record_ids"])
            for value in world["shared_fact_ids"].values()
        ),
        "text_interventions": sum(
            len(task.get("text_interventions", {}).get("probed_fact_ids", []))
            for task in world["tasks"]
        ),
        "temporal_contrast_checks": world["temporal_contrast_checks"],
        "event_case_counts": dict(
            sorted(
                Counter(
                    name
                    for cases in world["event_cases"].values()
                    for name, event_ids in cases.items()
                    for _ in event_ids
                ).items()
            )
        ),
        "min_full_chat_tokens": min(row["full_chat_tokens"] for row in rows),
        "max_full_chat_tokens": max(row["full_chat_tokens"] for row in rows),
        "min_supervised_tokens": min(row["supervised_tokens"] for row in rows),
        "max_supervised_tokens": max(row["supervised_tokens"] for row in rows),
        "min_required_fact_span_tokens": min(
            row["required_fact_span_tokens"] for row in rows
        ),
        "max_required_fact_span_tokens": max(
            row["required_fact_span_tokens"] for row in rows
        ),
    }


def _write_shard_contents(
    shard: Path,
    job: dict,
    world: dict | None,
    error: str | None,
    tokenizer,
    max_tokens: int,
) -> None:
    (shard / "job.json").write_text(_dump(job) + "\n", encoding="utf-8")
    if error is not None:
        (shard / "reject.json").write_text(
            _dump({"job": job, "reason": error}) + "\n", encoding="utf-8"
        )
        return
    assert world is not None
    try:
        rows = _reader_rows(world, tokenizer, max_tokens)
        if not shared.validate_world(world)["passed"]:
            raise ValueError("post-generation replay failed")
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        (shard / "reject.json").write_text(
            _dump({"job": job, "reason": f"{type(exc).__name__}: {exc}"}) + "\n",
            encoding="utf-8",
        )
        return
    world_path, rows_path = shard / "world.json", shard / "rows.jsonl"
    world_path.write_text(_dump(world) + "\n", encoding="utf-8")
    rows_path.write_text("".join(_dump(row) + "\n" for row in rows), encoding="utf-8")
    receipt = _receipt(job, world, rows, _sha(world_path), _sha(rows_path))
    (shard / "receipt.json").write_text(_dump(receipt) + "\n", encoding="utf-8")


def _write_result(
    output: Path,
    job: dict,
    world: dict | None,
    error: str | None,
    tokenizer,
    max_tokens: int,
) -> None:
    """Publish a complete per-job outcome by one directory rename."""
    shards = output / "shards"
    shards.mkdir(parents=True, exist_ok=True)
    target = shards / job["job_id"]
    if target.exists():
        raise ValueError(f"job outcome already exists: {job['job_id']}")
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=shards) as temporary:
        staged = Path(temporary)
        _write_shard_contents(staged, job, world, error, tokenizer, max_tokens)
        staged.rename(target)


def _bin(tokens: int) -> str:
    for ceiling in BINS:
        if tokens < ceiling:
            return f"<{ceiling}"
    return ">=262144"


def verify(output: Path, config: dict, tokenizer, *, require_manifest: bool) -> dict:
    jobs = plan(config)
    plan_path = output / "plan.json"
    if not plan_path.is_file() or json.loads(plan_path.read_text()) != config:
        raise ValueError("frozen P86 plan mismatch")
    accepted, rejected = [], []
    operation_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    token_bins: Counter[str] = Counter()
    source_splits: dict[str, str] = {}
    semantic_task_ids: set[str] = set()
    for job in jobs:
        shard = output / "shards" / job["job_id"]
        if not shard.is_dir() or json.loads((shard / "job.json").read_text()) != job:
            raise ValueError(f"missing or mismatched job shard: {job['job_id']}")
        reject_path, receipt_path = shard / "reject.json", shard / "receipt.json"
        if reject_path.exists() == receipt_path.exists():
            raise ValueError(f"job has neither or both outcomes: {job['job_id']}")
        if reject_path.exists():
            rejection = json.loads(reject_path.read_text())
            if rejection.get("job") != job or not rejection.get("reason"):
                raise ValueError("invalid reject receipt")
            rejected.append(rejection)
            continue
        receipt = json.loads(receipt_path.read_text())
        world_path, rows_path = shard / "world.json", shard / "rows.jsonl"
        if (
            receipt["job"] != job
            or receipt["world_sha256"] != _sha(world_path)
            or receipt["rows_sha256"] != _sha(rows_path)
        ):
            raise ValueError("accepted shard hash or job mismatch")
        world = json.loads(world_path.read_text())
        if not shared.validate_world(world)["passed"]:
            raise ValueError("world replay mismatch")
        rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
        expected = _reader_rows(world, tokenizer, config["max_full_chat_tokens"])
        if rows != expected:
            raise ValueError("reader/mask/token replay mismatch")
        if receipt != _receipt(job, world, rows, _sha(world_path), _sha(rows_path)):
            raise ValueError(
                "accepted receipt disagrees with verified world and reader"
            )
        for row in rows:
            previous_split = source_splits.setdefault(row["source_group"], row["split"])
            if (
                previous_split != row["split"]
                or row["semantic_task_id"] in semantic_task_ids
            ):
                raise ValueError("source split or semantic task identity collision")
            semantic_task_ids.add(row["semantic_task_id"])
            operation_counts[row["operation"]] += 1
            split_counts[row["split"]] += 1
            token_bins[_bin(row["full_chat_tokens"])] += 1
        accepted.append(receipt)
    manifest = {
        "schema_version": shared.VERSION + ".batch-manifest.v1",
        "planned_jobs": len(jobs),
        "accepted_jobs": len(accepted),
        "rejected_jobs": len(rejected),
        "accepted_cells": dict(
            sorted(Counter(item["job"]["cell_id"] for item in accepted).items())
        ),
        "rejected_cells": dict(
            sorted(Counter(item["job"]["cell_id"] for item in rejected).items())
        ),
        "rejection_reasons": dict(
            sorted(Counter(item["reason"] for item in rejected).items())
        ),
        "reader_rows": sum(item["reader_rows"] for item in accepted),
        "source_groups": len(source_splits),
        "semantic_tasks": len(semantic_task_ids),
        "cross_split_source_groups": 0,
        "operations": dict(sorted(operation_counts.items())),
        "split_rows": dict(sorted(split_counts.items())),
        "actual_full_chat_token_bins": dict(sorted(token_bins.items())),
        "min_full_chat_tokens": min(
            (item["min_full_chat_tokens"] for item in accepted), default=0
        ),
        "max_full_chat_tokens": max(
            (item["max_full_chat_tokens"] for item in accepted), default=0
        ),
        "min_supervised_tokens": min(
            (item["min_supervised_tokens"] for item in accepted), default=0
        ),
        "max_supervised_tokens": max(
            (item["max_supervised_tokens"] for item in accepted), default=0
        ),
        "min_required_fact_span_tokens": min(
            (item["min_required_fact_span_tokens"] for item in accepted), default=0
        ),
        "max_required_fact_span_tokens": max(
            (item["max_required_fact_span_tokens"] for item in accepted), default=0
        ),
        "shared_state_fact_ids": sum(item["shared_state_facts"] for item in accepted),
        "shared_cross_operation_record_ids": sum(
            item["shared_cross_record_facts"] for item in accepted
        ),
        "bounded_reader_text_interventions": sum(
            item["text_interventions"] for item in accepted
        ),
        "effective_reveal_contrast_checks": sum(
            item["temporal_contrast_checks"] for item in accepted
        ),
        "event_case_counts": dict(
            sorted(
                Counter(
                    {
                        name: sum(
                            item["event_case_counts"].get(name, 0) for item in accepted
                        )
                        for name in (
                            "timely",
                            "future_effective",
                            "future_reveal",
                            "effective_boundary",
                            "reveal_boundary",
                            "future_effective_boundary",
                            "future_reveal_boundary",
                        )
                    }
                ).items()
            )
        ),
        "tokenizer": {"model_id": MODEL, "revision": REVISION},
        "source_kind": "controlled_simulation",
        "train_ready": False,
        "dependency_claim": "bounded exact-fact text interventions; no global natural-language shortcut proof",
    }
    manifest_path = output / "manifest.json"
    if require_manifest and not manifest_path.is_file():
        raise ValueError("frozen P86 manifest is missing")
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("frozen P86 manifest mismatch")
    return manifest


def run(config_path: Path, output: Path, *, workers: int, resume: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    jobs = plan(config)
    if workers < 1:
        raise ValueError("workers must be positive")
    if output.exists():
        if not resume:
            raise ValueError("output exists; use --resume for the pinned plan")
        if json.loads((output / "plan.json").read_text()) != config:
            raise ValueError("resume plan mismatch")
    else:
        output.mkdir(parents=True)
        (output / "plan.json").write_text(_dump(config) + "\n", encoding="utf-8")
    tokenizer = _tokenizer()
    pending = [job for job in jobs if not (output / "shards" / job["job_id"]).exists()]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        work = iter(pending)
        active = {}
        for _ in range(min(workers * 2, len(pending))):
            job = next(work)
            active[executor.submit(_generate, job)] = job
        while active:
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                active.pop(future)
                job, world, error = future.result()
                _write_result(
                    output, job, world, error, tokenizer, config["max_full_chat_tokens"]
                )
                following = next(work, None)
                if following is not None:
                    active[executor.submit(_generate, following)] = following
    manifest = verify(output, config, tokenizer, require_manifest=False)
    (output / "manifest.json").write_text(_dump(manifest) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    result = (
        verify(args.output, config, _tokenizer(), require_manifest=True)
        if args.verify_only
        else run(args.config, args.output, workers=args.workers, resume=args.resume)
    )
    print(_dump(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
