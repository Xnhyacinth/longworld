"""Balance frozen candidate references without copying reader bodies.

This is a candidate selection, not a training approval or a quality score.
Cells are (split, source kind, operation, actual tokenizer length bin). A
round-robin gives each occupied cell a chance before filling common cells.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from longworld.synthesis.sharded_candidate_bank import verify_index
from longworld.synthesis.unified_candidate_contract import physical_length_bin

SCHEMA = "longworld.balanced-candidate-selection.v1"
ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def _key(entry: dict[str, Any]) -> str:
    return entry["candidate"]["sample_id"]


def _cell(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    row = entry["candidate"]
    return row["split"], row["source_kind"], row["operation"], row["length_bin"]


def _group(entry: dict[str, Any]) -> tuple[str, str]:
    row = entry["candidate"]
    return row["source_kind"], row["source_group"]


def _task(entry: dict[str, Any]) -> tuple[str, str]:
    row = entry["candidate"]
    return row["source_kind"], row["semantic_task_id"]


def _tie(entry: dict[str, Any], seed: int) -> str:
    return hashlib.sha256(f"{seed}|{_key(entry)}".encode()).hexdigest()


def _coverage(entries: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [entry["candidate"] for entry in entries]
    groups = {_group(entry) for entry in entries}
    tasks = {_task(entry) for entry in entries}
    scoped_tasks = {
        (row["source_kind"], row["source_group"], row["semantic_task_id"])
        for row in rows
    }
    kind_tokens: dict[str, dict[str, int]] = {}
    for row in rows:
        totals = kind_tokens.setdefault(
            row["source_kind"], {"input_tokens": 0, "supervised_tokens": 0}
        )
        totals["input_tokens"] += row["input_tokens"]
        totals["supervised_tokens"] += row["supervised_tokens"]
    return {
        "views": len(rows),
        "independent_semantic_tasks": len(tasks),
        "source_scoped_semantic_tasks": len(scoped_tasks),
        "source_groups": len(groups),
        "by_source_kind_groups": dict(
            sorted(Counter(kind for kind, _ in groups).items())
        ),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "supervised_tokens": sum(row["supervised_tokens"] for row in rows),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in rows),
        "by_split": dict(sorted(Counter(row["split"] for row in rows).items())),
        "by_source_kind": dict(
            sorted(Counter(row["source_kind"] for row in rows).items())
        ),
        "by_source_kind_tokens": dict(sorted(kind_tokens.items())),
        "by_domain": dict(sorted(Counter(row["domain"] for row in rows).items())),
        "by_topic": dict(sorted(Counter(row["topic"] for row in rows).items())),
        "by_length": dict(sorted(Counter(row["length_bin"] for row in rows).items())),
        "by_operation": dict(sorted(Counter(row["operation"] for row in rows).items())),
        "by_cell": {
            "|".join(cell): count
            for cell, count in sorted(
                Counter(_cell(entry) for entry in entries).items()
            )
        },
    }


def _read_entries(index_dir: Path) -> list[dict[str, Any]]:
    entries = []
    with (index_dir / "candidate_refs.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            entries.append(json.loads(line))
    return entries


def _choose(
    entries: list[dict[str, Any]],
    *,
    seed: int,
    max_per_group: int,
    max_per_cell: int,
    max_per_kind_by_split: dict[str, int],
    max_supervised_tokens_by_kind: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    split_by_group: dict[tuple[str, str], str] = {}
    for entry in entries:
        row = entry["candidate"]
        group = _group(entry)
        if group in split_by_group and split_by_group[group] != row["split"]:
            raise ValueError("source group crosses train/eval split")
        split_by_group[group] = row["split"]
        if row["input_tokens"] + row["supervised_tokens"] != row["full_chat_tokens"]:
            raise ValueError("candidate token accounting changed")
        if row["length_bin"] != physical_length_bin(row["full_chat_tokens"]):
            raise ValueError("candidate physical length bin changed")
        buckets[_cell(entry)].append(entry)
    for bucket in buckets.values():
        bucket.sort(key=lambda entry: (_tie(entry, seed), _key(entry)))

    selected: list[dict[str, Any]] = []
    chosen_tasks: set[tuple[str, str]] = set()
    group_counts: Counter[tuple[str, str]] = Counter()
    kind_split_counts: Counter[tuple[str, str]] = Counter()
    kind_supervised_tokens: Counter[str] = Counter()
    token_caps = max_supervised_tokens_by_kind or {}
    # Iterate one pass over each occupied cell per round. Rare cells retain
    # representation while common cells cannot consume the entire selection.
    for _ in range(max_per_cell):
        progressed = False
        for cell in sorted(buckets):
            eligible = (
                entry
                for entry in buckets[cell]
                if _task(entry) not in chosen_tasks
                and group_counts[_group(entry)] < max_per_group
                and kind_split_counts[(cell[0], cell[1])]
                < max_per_kind_by_split[cell[0]]
                and kind_supervised_tokens[cell[1]]
                + entry["candidate"]["supervised_tokens"]
                <= token_caps.get(cell[1], float("inf"))
            )
            pick = min(
                eligible,
                key=lambda entry: (
                    group_counts[_group(entry)],
                    _tie(entry, seed),
                    _key(entry),
                ),
                default=None,
            )
            if pick is None:
                continue
            selected.append(pick)
            chosen_tasks.add(_task(pick))
            group_counts[_group(pick)] += 1
            kind_split_counts[(cell[0], cell[1])] += 1
            kind_supervised_tokens[cell[1]] += pick["candidate"]["supervised_tokens"]
            progressed = True
        if not progressed:
            break
    return selected


def select(
    index_dir: Path,
    output_dir: Path,
    *,
    seed: int,
    max_per_group: int,
    max_per_cell: int,
    max_per_kind_by_split: dict[str, int],
    max_supervised_tokens_by_kind: dict[str, int] | None = None,
) -> dict[str, Any]:
    if (
        output_dir.exists()
        or type(seed) is not int
        or seed < 0
        or type(max_per_group) is not int
        or max_per_group < 1
        or type(max_per_cell) is not int
        or max_per_cell < 1
        or set(max_per_kind_by_split) != {"train", "eval"}
        or any(
            type(value) is not int or value < 1
            for value in max_per_kind_by_split.values()
        )
        or (
            max_supervised_tokens_by_kind is not None
            and (
                not isinstance(max_supervised_tokens_by_kind, dict)
                or any(
                    not isinstance(kind, str)
                    or not kind
                    or type(cap) is not int
                    or cap < 1
                    for kind, cap in max_supervised_tokens_by_kind.items()
                )
            )
        )
    ):
        raise ValueError("new output, nonnegative seed and positive caps required")
    source = verify_index(index_dir)
    entries = _read_entries(index_dir)
    if len(entries) != source["candidate_views"]:
        raise ValueError("candidate reference count changed")
    if max_supervised_tokens_by_kind and not set(max_supervised_tokens_by_kind) <= {
        entry["candidate"]["source_kind"] for entry in entries
    }:
        raise ValueError("supervised token cap names an absent source kind")
    selected = _choose(
        entries,
        seed=seed,
        max_per_group=max_per_group,
        max_per_cell=max_per_cell,
        max_per_kind_by_split=max_per_kind_by_split,
        max_supervised_tokens_by_kind=max_supervised_tokens_by_kind,
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p90-balanced-", dir=output_dir.parent
    ) as raw:
        temp = Path(raw)
        selected_path = temp / "selected_refs.jsonl"
        with selected_path.open("x", encoding="utf-8") as stream:
            for rank, entry in enumerate(selected):
                stream.write(
                    json.dumps(
                        {"selection_rank": rank, **entry},
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        manifest = {
            "schema_version": SCHEMA,
            "input_manifest_sha256": _sha(index_dir / "manifest.json"),
            "input_refs_sha256": source["refs_sha256"],
            "selection_seed": seed,
            "max_per_source_group": max_per_group,
            "max_per_cell": max_per_cell,
            "max_per_source_kind_by_split": max_per_kind_by_split,
            **(
                {"max_supervised_tokens_by_kind": max_supervised_tokens_by_kind}
                if max_supervised_tokens_by_kind is not None
                else {}
            ),
            "before": _coverage(entries),
            "after": _coverage(selected),
            "selected_refs_sha256": _sha(selected_path),
            "selection_scope": "candidate_only_source_aware_balancing",
            "train_ready": False,
        }
        _json(temp / "manifest.json", manifest)
        os.rename(temp, output_dir)
    return manifest


def verify_selection(
    index_dir: Path,
    output_dir: Path,
    *,
    seed: int,
    max_per_group: int,
    max_per_cell: int,
    max_per_kind_by_split: dict[str, int],
    max_supervised_tokens_by_kind: dict[str, int] | None = None,
) -> dict[str, Any]:
    stored = json.loads((output_dir / "manifest.json").read_text())
    if stored.get("selected_refs_sha256") != _sha(output_dir / "selected_refs.jsonl"):
        raise ValueError("selected references changed")
    with tempfile.TemporaryDirectory(
        prefix="p90-balanced-verify-", dir=output_dir.parent
    ) as raw:
        rebuilt_dir = Path(raw) / "selection"
        rebuilt = select(
            index_dir,
            rebuilt_dir,
            seed=seed,
            max_per_group=max_per_group,
            max_per_cell=max_per_cell,
            max_per_kind_by_split=max_per_kind_by_split,
            max_supervised_tokens_by_kind=max_supervised_tokens_by_kind,
        )
        if (
            rebuilt != stored
            or _sha(rebuilt_dir / "selected_refs.jsonl")
            != stored["selected_refs_sha256"]
        ):
            raise ValueError("selection differs from frozen candidate index")
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/p90_balanced_selection_v1.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    index_dir = ROOT / config["input_index"]
    kwargs = {
        "seed": config["seed"],
        "max_per_group": config["max_per_source_group"],
        "max_per_cell": config["max_per_cell"],
        "max_per_kind_by_split": config["max_per_source_kind_by_split"],
        "max_supervised_tokens_by_kind": config.get("max_supervised_tokens_by_kind"),
    }
    result = (
        verify_selection(index_dir, args.output, **kwargs)
        if args.verify_only
        else select(index_dir, args.output, **kwargs)
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
