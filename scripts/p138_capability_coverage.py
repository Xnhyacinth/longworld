"""Audit declared task capabilities in a frozen candidate bank and selection.

This is a metadata inventory, not a semantic, dependency, or training certificate.
Unknown and ambiguous routes remain visible rather than being guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.p113_coverage_matrix import build_report as base_coverage

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p138-capability-coverage.v2"
CONFIG_SCHEMA = "longworld.p138-capability-taxonomy.v1"
MATCH_FIELDS = ("source_kind", "source_name", "topic", "evidence_profile")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _route_matches(row: dict[str, Any], route: dict[str, Any]) -> bool:
    if any(row.get(field) != route[field] for field in MATCH_FIELDS if field in route):
        return False
    if "source_group_prefix" in route and not str(row.get("source_group", "")).startswith(
        route["source_group_prefix"]
    ):
        return False
    operation = row.get("operation")
    return operation in route.get("operation", ()) or any(
        isinstance(operation, str) and operation.startswith(prefix)
        for prefix in route.get("operation_prefix", ())
    )


def classify(row: dict[str, Any], routes: list[dict[str, Any]]) -> tuple[str, str | None, str]:
    matches = [route for route in routes if _route_matches(row, route)]
    if not matches:
        return "unknown", None, "unmapped"
    # More constrained source/topic/evidence routes override broad operation
    # families. Ties are ambiguous even when their labels happen to agree.
    top = max(
        sum(field in route for field in (*MATCH_FIELDS, "source_group_prefix"))
        for route in matches
    )
    winners = [
        route
        for route in matches
        if sum(field in route for field in (*MATCH_FIELDS, "source_group_prefix")) == top
    ]
    if len(winners) != 1:
        return "unknown", None, "ambiguous"
    winner = winners[0]
    return winner["capability"], winner.get("native_mechanism"), "declared"


def _length_bin(full_chat_tokens: int) -> str:
    for bound, label in (
        (32768, "lt32k"),
        (65536, "32k"),
        (131072, "64k"),
        (262144, "128k"),
    ):
        if full_chat_tokens < bound:
            return label
    return "ge256k"


def summarize(entries: list[dict[str, Any]], routes: list[dict[str, Any]]) -> dict[str, Any]:
    task_rows: dict[tuple[str, str], dict[str, Any]] = {}
    task_classes: dict[tuple[str, str], tuple[str, str | None, str]] = {}
    group_classes: dict[tuple[str, str], set[str]] = defaultdict(set)
    group_tasks: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    by_capability: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"views": 0, "tasks": set(), "groups": set(), "input_tokens": 0, "supervised_tokens": 0}
    )
    cross_cells: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"views": 0, "tasks": set(), "groups": set(), "input_tokens": 0, "supervised_tokens": 0}
    )
    labels = {name: Counter() for name in ("source_kind", "source_name", "domain", "topic", "length_bin", "split", "dependency_status", "evidence_profile")}
    mechanisms: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"views": 0, "tasks": set(), "groups": set(), "capabilities": set()}
    )
    route_status = Counter()
    mismatched_length_label = 0
    total_input = total_supervised = 0
    for entry in entries:
        row = entry["candidate"]
        task = (row["source_kind"], row["semantic_task_id"])
        group = (row["source_kind"], row["source_group"])
        classification = classify(row, routes)
        if task in task_classes and task_classes[task] != classification:
            raise ValueError(f"inconsistent capability across task views: {task}")
        task_classes[task] = classification
        task_rows.setdefault(task, row)
        capability, mechanism, status = classification
        route_status[status] += 1
        full = row["full_chat_tokens"]
        if row["input_tokens"] + row["supervised_tokens"] != full:
            raise ValueError("final-chat token accounting differs")
        length_bin = _length_bin(full)
        mismatched_length_label += row.get("length_bin") != length_bin
        for field, counter in labels.items():
            value = length_bin if field == "length_bin" else row.get(field)
            counter["<null>" if value is None else str(value)] += 1
        slot = by_capability[capability]
        slot["views"] += 1
        slot["tasks"].add(task)
        slot["groups"].add(group)
        slot["input_tokens"] += row["input_tokens"]
        slot["supervised_tokens"] += row["supervised_tokens"]
        cross = cross_cells[(capability, row["source_kind"], length_bin)]
        cross["views"] += 1
        cross["tasks"].add(task)
        cross["groups"].add(group)
        cross["input_tokens"] += row["input_tokens"]
        cross["supervised_tokens"] += row["supervised_tokens"]
        group_tasks[group].add(task)
        if capability not in ("unknown", "composite_reuse"):
            group_classes[group].add(capability)
        if mechanism:
            mechanism_slot = mechanisms[mechanism]
            mechanism_slot["views"] += 1
            mechanism_slot["tasks"].add(task)
            mechanism_slot["groups"].add(group)
            mechanism_slot["capabilities"].add(capability)
        total_input += row["input_tokens"]
        total_supervised += row["supervised_tokens"]
    multi = {
        f"{kind}:{group}": sorted(capabilities)
        for (kind, group), capabilities in sorted(group_classes.items())
        if len(capabilities) > 1
    }
    return {
        "views": len(entries),
        "independent_semantic_tasks": len(task_rows),
        "typed_source_groups": len(group_tasks),
        "input_tokens": total_input,
        "supervised_tokens": total_supervised,
        "supervised_fraction": total_supervised / (total_input + total_supervised) if entries else 0,
        "null_dependency_status_views": labels["dependency_status"]["<null>"],
        "actual_length_bin_mismatch_views": mismatched_length_label,
        "route_status_views": dict(sorted(route_status.items())),
        "dimensions_views": {key: dict(sorted(value.items())) for key, value in labels.items()},
        "capabilities": {
            key: {
                "views": slot["views"],
                "tasks": len(slot["tasks"]),
                "typed_source_groups": len(slot["groups"]),
                "input_tokens": slot["input_tokens"],
                "supervised_tokens": slot["supervised_tokens"],
            }
            for key, slot in sorted(by_capability.items())
        },
        "capability_source_length": {
            capability: {
                kind: {
                    length: {
                        "views": cell["views"],
                        "tasks": len(cell["tasks"]),
                        "typed_source_groups": len(cell["groups"]),
                        "input_tokens": cell["input_tokens"],
                        "supervised_tokens": cell["supervised_tokens"],
                    }
                    for (cell_capability, cell_kind, length), cell in sorted(cross_cells.items())
                    if cell_capability == capability and cell_kind == kind
                }
                for kind in sorted({cell_kind for cell_capability, cell_kind, _ in cross_cells if cell_capability == capability})
            }
            for capability in sorted({cell_capability for cell_capability, _, _ in cross_cells})
        },
        "native_mechanisms": {
            key: {
                "views": slot["views"],
                "tasks": len(slot["tasks"]),
                "typed_source_groups": len(slot["groups"]),
                "capabilities": sorted(slot["capabilities"]),
            }
            for key, slot in sorted(mechanisms.items())
        },
        "known_multi_capability_groups": len(multi),
        "known_multi_capability_group_details": multi,
        "group_identity_scope": "typed source_group only; source connected components require the separate source audit",
    }


def build_report(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != CONFIG_SCHEMA or not isinstance(config.get("routes"), list):
        raise ValueError("invalid capability taxonomy")
    inputs = config["input"]
    index_dir = ROOT / inputs["index"]
    selection_dir = ROOT / inputs["selection"]
    pinned_files = {
        "index_manifest_sha256": index_dir / "manifest.json",
        "index_refs_sha256": index_dir / "candidate_refs.jsonl",
        "selection_manifest_sha256": selection_dir / "manifest.json",
        "selection_refs_sha256": selection_dir / "selected_refs.jsonl",
    }
    for key, path in pinned_files.items():
        if _sha(path) != inputs[key]:
            raise ValueError(f"pinned input changed: {key}")
    # Existing verifier checks bank shards, row identity, selection rank, and
    # the selection manifest's binding to this index.
    base = base_coverage(index_dir, selection_dir)
    candidates = _rows(index_dir / "candidate_refs.jsonl")
    selected = _rows(selection_dir / "selected_refs.jsonl")
    candidate = summarize(candidates, config["routes"])
    selection = summarize(selected, config["routes"])
    if (
        candidate["views"] != base["candidate"]["views"]
        or selection["views"] != base["selected"]["views"]
        or selection["independent_semantic_tasks"] != base["selected"]["independent_semantic_tasks"]
    ):
        raise ValueError("capability totals differ from frozen coverage")
    return {
        "schema": SCHEMA,
        "taxonomy_sha256": _sha(config_path),
        "input_pins": {key: inputs[key] for key in sorted(pinned_files)},
        "candidate": candidate,
        "selected": selection,
        "interpretation": "Capabilities are declared task-operation families, not independent cognitive mechanisms. Only explicit P133 state-transition identities appear under native_mechanisms; P125 composites are reuse views, not a new capability. Unknown and ambiguous routes are not silently assigned.",
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/p138_capability_taxonomy_v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/candidates/p138_capability_coverage_v2/report.json")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    report = build_report(args.config)
    if args.verify_only:
        if json.loads(args.output.read_text(encoding="utf-8")) != report:
            raise ValueError("capability report does not replay")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    print(json.dumps({"candidate_views": report["candidate"]["views"], "selected_tasks": report["selected"]["independent_semantic_tasks"], "selected_known_multi_capability_groups": report["selected"]["known_multi_capability_groups"], "report": str(args.output)}))


if __name__ == "__main__":
    main()
