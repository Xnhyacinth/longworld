"""Inventory frozen long-context sample indexes without promoting them to training data.

Example:
  python scripts/build_p76_batch_index.py \
    --input p64=data/sft/p64_primary_training_v2/sample_index.jsonl \
    --input p66=data/hf/LongWorld-Worlds-State/snapshots/2026-09-13/p66_multidomain_candidates/sample_index.jsonl \
    --input p71=data/capability_records/p71_pool_v1/sample_index.jsonl \
    --input p75=data/p75_real_reader_candidates_v1/sample_index.jsonl \
    --output-dir data/candidates/p76_batch_index_v1

Each input is read line by line. The output is an inventory and coverage report,
not a merged training dataset: a repeated sample or semantic task contributes
only once to the corresponding unique count. A future source recipe can use the
same interface if its index supplies an explicit task identity and split.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nonempty(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), None)


def _length_bin(tokens: Any) -> str | None:
    if tokens is None:
        return None
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        raise ValueError("final token count must be a nonnegative integer")
    for ceiling, label in (
        (32768, "lt32k"),
        (65536, "32k_to_lt64k"),
        (131072, "64k_to_lt128k"),
        (262144, "128k_to_lt256k"),
    ):
        if tokens < ceiling:
            return label
    return "ge256k"


def _identity(product: str, row: dict[str, Any]) -> tuple[str, str, str | None]:
    """Return (task namespace, semantic ID, domain), preserving cross-wave IDs."""
    domain = row.get("domain")
    if product in {"p64", "p66"}:
        semantic_id = row.get("semantic_task_id")
        if not domain or not semantic_id:
            raise ValueError("P64/P66 require domain and semantic_task_id")
        return "real_workflow", str(semantic_id), str(domain)
    if product == "p71":
        semantic_id = _nonempty(row.get("semantic_task_id"), row.get("example_id"))
        if not semantic_id:
            raise ValueError("P71 requires semantic_task_id or example_id")
        return "simulated_record", str(semantic_id), domain
    if product == "p73":
        semantic_id = _nonempty(row.get("semantic_task_id"), row.get("example_id"))
        if not semantic_id:
            raise ValueError("P73 requires semantic_task_id or example_id")
        return "simulated_shared", str(semantic_id), domain
    if product == "p75" or product.startswith("p76_wiki"):
        group, task_id = row.get("source_group"), row.get("task_id")
        if not group or not task_id:
            raise ValueError("Wiki index requires source_group and task_id")
        return "real_wiki", _dump([group, task_id]), domain
    semantic_id = _nonempty(row.get("semantic_task_id"), row.get("task_id"))
    if not semantic_id:
        raise ValueError("new product requires semantic_task_id or task_id")
    group = _nonempty(row.get("source_group"), row.get("group_id"))
    return (
        str(_nonempty(row.get("source_kind"), product)),
        _dump([group, semantic_id]),
        domain,
    )


def _normalize(product: str, row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("index row must be an object")
    split = row.get("split")
    if split not in {"train", "eval"}:
        raise ValueError("split must be train or eval")
    namespace, semantic_id, domain = _identity(product, row)
    sample_id = _nonempty(row.get("sample_id"), row.get("example_id"))
    if sample_id is None:
        raise ValueError("sample_id or example_id is required")
    source_kind = _nonempty(
        row.get("source_kind"),
        {
            "p64": "real_workflow",
            "p66": "real_workflow",
            "p71": "simulated_record",
            "p73": "simulated_shared",
            "p75": "real_wiki",
        }.get(product),
        "real_wiki" if product.startswith("p76_wiki") else None,
    )
    group = _nonempty(row.get("source_group"), row.get("group_id"))
    tokens = _nonempty(row.get("full_message_tokens"), row.get("full_chat_tokens"))
    return {
        "product": product,
        "sample_id": str(sample_id),
        "task_id": _nonempty(row.get("semantic_task_id"), row.get("task_id")),
        "task_key": _dump(
            [namespace, None if namespace == "real_wiki" else domain, semantic_id]
        ),
        "world_id": _nonempty(row.get("world_id"), row.get("world_instance_id")),
        "source_group": group,
        "domain": domain,
        "topic": row.get("topic"),
        "family": row.get("family"),
        "task_type": row.get("task_type"),
        "operation": _nonempty(row.get("operation"), row.get("task_type")),
        "source_kind": source_kind,
        "split": split,
        "full_message_tokens": tokens,
        "length_bin": _length_bin(tokens),
        "evidence_status": _nonempty(
            row.get("evidence_status"), row.get("evidence_class")
        ),
        "dependency_status": row.get("dependency_status"),
        "quality_status": _nonempty(
            row.get("quality_status"),
            row.get("admission_status"),
            row.get("admission_state"),
        ),
        "token_measurement": row.get("token_measurement"),
        "document_count": row.get("document_count"),
        "observed_lineage_token_envelope": row.get("observed_lineage_token_envelope"),
        "context_sha256": row.get("context_sha256"),
    }


def _dimension_key(value: Any) -> str:
    return "(missing)" if value is None else str(value)


def build(inputs: list[tuple[str, Path]], output_dir: Path) -> dict[str, Any]:
    """Write a stable, source-bound inventory into a new output directory."""
    if not inputs:
        raise ValueError("at least one --input is required")
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    names = [name for name, _ in inputs]
    if len(names) != len(set(names)):
        raise ValueError("duplicate product names")
    for product, path in inputs:
        if not product or not path.is_file():
            raise ValueError(f"missing input index for {product}: {path}")
    output_dir.mkdir(parents=True)
    task_splits: dict[str, str] = {}
    group_splits: dict[str, str] = {}
    sample_signatures: dict[str, tuple[str, str, Any, Any]] = {}
    worlds: set[tuple[Any, Any, str]] = set()
    groups: set[str] = set()
    worlds_by_product: dict[str, set[tuple[Any, Any, str]]] = defaultdict(set)
    groups_by_product: dict[str, set[str]] = defaultdict(set)
    dimensions: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    dimension_tasks: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    source_rows = accepted_rows = unique_samples = unique_tasks = rejected = 0
    source_inputs: list[dict[str, Any]] = []
    with (
        (output_dir / "sample_index.jsonl").open("x", encoding="utf-8") as index_out,
        (output_dir / "rejects.jsonl").open("x", encoding="utf-8") as rejects_out,
    ):
        for product, path in sorted(inputs, key=lambda item: item[0]):
            digest = hashlib.sha256()
            product_rows = 0
            worlds_by_product[product]
            groups_by_product[product]
            with path.open("rb") as stream:
                for line_no, raw_line in enumerate(stream, 1):
                    digest.update(raw_line)
                    if not raw_line.strip():
                        continue
                    source_rows += 1
                    product_rows += 1
                    try:
                        row = json.loads(raw_line)
                        item = _normalize(product, row)
                        task_key = item["task_key"]
                        sample_key = _dump(
                            [item["source_kind"], item["domain"], item["sample_id"]]
                        )
                        group_key = (
                            _dump(
                                [
                                    item["source_kind"],
                                    None
                                    if item["source_kind"] == "real_wiki"
                                    else item["domain"],
                                    item["source_group"],
                                ]
                            )
                            if item["source_group"] is not None
                            else None
                        )
                        if (
                            task_key in task_splits
                            and task_splits[task_key] != item["split"]
                        ):
                            raise ValueError("task_split_collision")
                        if (
                            group_key in group_splits
                            and group_splits[group_key] != item["split"]
                        ):
                            raise ValueError("source_group_split_collision")
                        sample_signature = (
                            task_key,
                            item["split"],
                            item["source_group"],
                            item["full_message_tokens"],
                        )
                        if (
                            sample_key in sample_signatures
                            and sample_signatures[sample_key] != sample_signature
                        ):
                            raise ValueError("sample_identity_collision")
                    except (ValueError, TypeError, json.JSONDecodeError) as error:
                        rejects_out.write(
                            _dump(
                                {
                                    "product": product,
                                    "source_line": line_no,
                                    "reason": str(error),
                                }
                            )
                            + "\n"
                        )
                        rejected += 1
                        continue
                    new_task = task_key not in task_splits
                    new_sample = sample_key not in sample_signatures
                    task_splits[task_key] = item["split"]
                    sample_signatures[sample_key] = sample_signature
                    if group_key is not None:
                        group_splits[group_key] = item["split"]
                        groups.add(group_key)
                        groups_by_product[product].add(group_key)
                    if item["world_id"] is not None:
                        world_key = (
                            item["source_kind"],
                            item["domain"],
                            str(item["world_id"]),
                        )
                        worlds.add(world_key)
                        worlds_by_product[product].add(world_key)
                    item.update(
                        {
                            "source_index": str(path),
                            "source_line": line_no,
                            "new_independent_task": new_task,
                            "new_sample": new_sample,
                        }
                    )
                    index_out.write(_dump(item) + "\n")
                    accepted_rows += 1
                    unique_tasks += int(new_task)
                    unique_samples += int(new_sample)
                    for field in (
                        "product",
                        "source_kind",
                        "domain",
                        "topic",
                        "family",
                        "operation",
                        "split",
                        "length_bin",
                        "evidence_status",
                        "dependency_status",
                        "quality_status",
                    ):
                        value = _dimension_key(item[field])
                        counts = dimensions[field][value]
                        counts["source_rows"] += 1
                        counts["unique_tasks"] += int(new_task)
                        counts["unique_samples"] += int(new_sample)
                        dimension_tasks[field][value].add(task_key)
            source_inputs.append(
                {
                    "product": product,
                    "path": str(path),
                    "sha256": digest.hexdigest(),
                    "source_rows": product_rows,
                }
            )
    coverage = {
        "source_rows": source_rows,
        "accepted_rows": accepted_rows,
        "rejected_rows": rejected,
        "unique_samples": unique_samples,
        "duplicate_sample_rows": accepted_rows - unique_samples,
        "unique_independent_tasks": unique_tasks,
        "duplicate_task_rows": accepted_rows - unique_tasks,
        "known_worlds": len(worlds),
        "known_source_groups": len(groups),
        "known_worlds_by_product": {
            product: len(values)
            for product, values in sorted(worlds_by_product.items())
        },
        "known_source_groups_by_product": {
            product: len(values)
            for product, values in sorted(groups_by_product.items())
        },
        "dimensions": {
            field: {
                value: {
                    **dict(sorted(counts.items())),
                    "distinct_tasks_present": len(dimension_tasks[field][value]),
                }
                for value, counts in sorted(values.items())
            }
            for field, values in sorted(dimensions.items())
        },
        "counting_note": "Unique task counts use semantic identity across products; missing metadata remains null and is not inferred from topic labels.",
    }
    (output_dir / "coverage.json").write_text(_dump(coverage) + "\n", encoding="utf-8")
    manifest = {
        "schema": "longworld.p76.batch-index.v2",
        "status": "inventory_only",
        "train_ready": False,
        "training_release_eligible": False,
        "source_inputs": source_inputs,
        "files_sha256": {
            name: _sha(output_dir / name)
            for name in ("sample_index.jsonl", "coverage.json", "rejects.jsonl")
        },
    }
    (output_dir / "manifest.json").write_text(_dump(manifest) + "\n", encoding="utf-8")
    return coverage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", action="append", required=True, metavar="PRODUCT=INDEX_JSONL"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    inputs = []
    for value in args.input:
        product, separator, raw_path = value.partition("=")
        if not separator or not product or not raw_path:
            parser.error(f"--input must be PRODUCT=INDEX_JSONL: {value}")
        inputs.append((product, Path(raw_path)))
    coverage = build(inputs, args.output_dir)
    print(
        _dump(
            {
                key: coverage[key]
                for key in (
                    "source_rows",
                    "accepted_rows",
                    "rejected_rows",
                    "unique_samples",
                    "unique_independent_tasks",
                    "known_worlds",
                    "known_source_groups",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
