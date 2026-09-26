"""Rank frozen paper works before the expensive full-reader QA compiler.

This is a source-visible TeX screen. It uses the existing P96 resolver and
P104/P105 shortcut rules; only the downstream compiler can admit a task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.p66_researchlab_taskbank import load_text_tar
from scripts.p96_paper_caption_qa import (
    discover,
    render_files,
    resolve,
    shortcut_reason,
)
from scripts.p104_paper_quality_gate import quality_status
from scripts.run_p86_frozen_paper_batch import _dedupe, _inventory

SCHEMA = "longworld.p113-paper-shape.v1"


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pin(pin: dict[str, str]) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("P113 pin requires a path and SHA-256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P113 input path must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"P113 input pin drift: {relative}")
    return path


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _dump(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _probe(input_row: dict) -> tuple[dict, list[dict]]:
    capacity = input_row["capacity"]
    work_id = capacity["work_id"]
    inventory = {
        "family_id": "p113-arxiv-" + work_id,
        "split": capacity["split"],
        "inventory": {
            "path": capacity["inventory"],
            "sha256": capacity["inventory_sha256"],
        },
    }
    work = _inventory(inventory)
    archive = work["sources"][-1]
    if archive != capacity["latest_archive"]:
        raise ValueError(f"P113 source archive differs from capacity: {work_id}")
    row = {
        "cohort": input_row["cohort"],
        "work_id": work_id,
        "source_group": "researchlab:arxiv:" + work_id,
        "split": capacity["split"],
        "source_archive": archive,
        "license_status": capacity.get("license_status", "not_recorded_in_capacity"),
        "source_status": capacity["status"],
        "source_shape_positive": False,
    }
    try:
        files, duplicates = _dedupe(load_text_tar(ROOT / archive["path"]))
        context = render_files(files)
        links, reasons = discover(context)
    except (OSError, ValueError) as error:
        row.update(
            source_files=0,
            source_chars=0,
            duplicate_paths_removed=0,
            discovered_links=0,
            discovery_reasons={},
            prefilter_reasons={"source_parser_rejected": 1},
            parser_error=type(error).__name__,
        )
        return row, []
    if len(links) != capacity.get("cross_file_links", 0):
        raise ValueError(f"P113 source discovery drift: {work_id}")
    outcomes = []
    for link in links:
        target = link["target"]
        resolved = resolve(context, link["cue"])
        if (
            resolved is None
            or resolved["reference_span"] != link["reference_span"]
            or resolved["caption_span"] != target["caption_span"]
        ):
            status = "rejected_reader_resolution"
        else:
            shortcut = shortcut_reason(
                link["cue"],
                link["reference_label"],
                resolved["caption"],
                resolved["kind"],
            )
            status = (
                "rejected_" + shortcut
                if shortcut
                else quality_status(context, resolved["caption"])
            )
            if status == "accepted_raw_tex_reference":
                for name in ("reference_span", "caption_span"):
                    start, end = resolved[name]
                    masked = context[:start] + "?" * (end - start) + context[end:]
                    if resolve(masked, link["cue"]) is not None:
                        status = "rejected_" + name + "_deletion"
                        break
        outcomes.append(
            {
                "cohort": input_row["cohort"],
                "work_id": work_id,
                "split": capacity["split"],
                "source_path": link["source_path"],
                "target_path": target["path"],
                "target_kind": target["kind"],
                "reference_label": link["reference_label"],
                "reference_span": link["reference_span"],
                "target_span": target["caption_span"],
                "evidence_extent_chars": max(
                    link["reference_span"][1], target["caption_span"][1]
                )
                - min(link["reference_span"][0], target["caption_span"][0]),
                "status": status,
            }
        )
    row.update(
        source_files=len(files),
        source_chars=len(context),
        duplicate_paths_removed=duplicates,
        discovered_links=len(links),
        discovery_reasons=reasons,
        prefilter_reasons=dict(sorted(Counter(x["status"] for x in outcomes).items())),
        source_shape_positive=any(
            x["status"] == "accepted_raw_tex_reference" for x in outcomes
        ),
    )
    return row, outcomes


def build(config_path: Path) -> dict[str, bytes]:
    config = json.loads(config_path.read_text())
    cohorts = config.get("cohorts")
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("workers") != 4
        or not isinstance(cohorts, list)
        or not cohorts
        or len({row["name"] for row in cohorts}) != len(cohorts)
    ):
        raise ValueError("P113 config requires distinct cohorts and four workers")
    jobs = []
    evaluation_cohorts = set()
    for cohort in cohorts:
        if cohort.get("evaluation", True):
            evaluation_cohorts.add(cohort["name"])
        capacity_path = _pin(cohort["capacity_index"])
        for capacity in _jsonl(capacity_path):
            if "latest_archive" in capacity:
                jobs.append({"cohort": cohort["name"], "capacity": capacity})
    ids = [row["capacity"]["work_id"] for row in jobs]
    if len(ids) != len(set(ids)):
        raise ValueError("P113 paper work repeats across cohorts")
    with ProcessPoolExecutor(max_workers=4) as workers:
        probed = list(workers.map(_probe, jobs))
    works = [row for row, _targets in probed]
    targets = [target for _row, found in probed for target in found]
    gold = set()
    for cohort in cohorts:
        if "admitted_quality_ledger" not in cohort:
            continue
        for line in _jsonl(_pin(cohort["admitted_quality_ledger"])):
            if line["status"] == "accepted_raw_tex_reference":
                gold.add(line["source_group"])
    for row in works:
        row["previously_admitted_work"] = (
            row["source_group"] in gold if row["cohort"] in evaluation_cohorts else None
        )
    matrix = Counter(
        (row["source_shape_positive"], row["previously_admitted_work"])
        for row in works
        if row["previously_admitted_work"] is not None
    )
    outputs = {
        "work_matrix.jsonl": b"".join(_dump(row) for row in works),
        "target_ledger.jsonl": b"".join(_dump(row) for row in targets),
    }
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "frozen_works": len(works),
        "source_shape_positive_works": sum(
            row["source_shape_positive"] for row in works
        ),
        "previously_admitted_works": len(gold),
        "new_unlabeled_works": sum(
            row["previously_admitted_work"] is None for row in works
        ),
        "positive_and_admitted": matrix[(True, True)],
        "positive_unadmitted": matrix[(True, False)],
        "negative_but_admitted": matrix[(False, True)],
        "negative_and_unadmitted": matrix[(False, False)],
        "discovered_links": len(targets),
        "prescreen_targets": sum(
            row["status"] == "accepted_raw_tex_reference" for row in targets
        ),
        "target_reasons": dict(sorted(Counter(x["status"] for x in targets).items())),
        "source_cohorts": dict(sorted(Counter(x["cohort"] for x in works).items())),
        "content_use": "local_research_only_no_redistribution",
        "claim_limit": "source-visible TeX only; no token length, prior-task, or final-reader admission",
        "train_ready": False,
        "files_sha256": {
            name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()
        },
    }
    outputs["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    return outputs


def run(config_path: Path, output_dir: Path, verify_only: bool = False) -> dict:
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    outputs = build(config_path)
    if verify_only:
        if not output_dir.is_dir() or {x.name for x in output_dir.iterdir()} != set(
            outputs
        ):
            raise ValueError("P113 prescreen output inventory changed")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P113 prescreen replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P113 prescreen output must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            (output_dir / name).write_bytes(content)
    return json.loads(outputs["manifest.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(run(args.config, args.output_dir, args.verify_only), sort_keys=True)
    )


if __name__ == "__main__":
    main()
