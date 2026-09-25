"""Exhaust bounded cross-file TeX targets without relaxing final quality gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.p66_researchlab_taskbank import load_text_tar
from scripts.p96_paper_caption_qa import (
    REF,
    _active,
    _records,
    _targets,
    cue_for_reference,
    render_files,
    resolve,
    shortcut_reason,
)
from scripts.p105_paper_quality_gate import _quality_status, _source_rows
from scripts.p110_paper_autocatalog import _pin
from scripts.p110_paper_probe import run as replay_probe
from scripts.run_p86_frozen_paper_batch import _dedupe

SCHEMA = "longworld.p110-paper-target-resample.v1"


def _dump(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _scan(capacity: dict) -> dict:
    archive = ROOT / capacity["latest_archive"]["path"]
    if (
        hashlib.sha256(archive.read_bytes()).hexdigest()
        != capacity["latest_archive"]["sha256"]
    ):
        raise ValueError("P110 target resample source archive changed")
    files, _duplicates = _dedupe(load_text_tar(archive))
    context = render_files(files)
    records = _records(context)
    targets = _targets(records)
    counts = Counter()
    candidate_rows = []
    for path, (source, offset) in records.items():
        for ref in REF.finditer(source):
            counts["refs_examined_all_files"] += 1
            if not _active(source, ref.start()):
                counts["commented_reference"] += 1
                continue
            options = targets.get(ref.group(1), [])
            if len(options) != 1 or options[0]["path"] == path:
                counts["nonunique_or_same_file_target"] += 1
                continue
            counts["unique_cross_file_target"] += 1
            cue = cue_for_reference(source, ref)
            if cue is None or context.count(cue) != 1:
                counts["nonunique_or_nonprose_cue"] += 1
                continue
            counts["unique_prose_cue"] += 1
            target = options[0]
            resolved = resolve(context, cue)
            if (
                resolved is None
                or resolved["reference_span"]
                != [offset + ref.start(), offset + ref.end()]
                or resolved["caption_span"] != target["caption_span"]
            ):
                counts["final_reader_resolver_mismatch"] += 1
                continue
            counts["final_reader_resolved"] += 1
            shortcut = shortcut_reason(
                cue, ref.group(1), resolved["caption"], resolved["kind"]
            )
            status = (
                "rejected_" + shortcut
                if shortcut
                else _quality_status(
                    context,
                    resolved["caption"],
                    tuple(resolved["caption_span"]),
                )
            )
            counts[status] += 1
            candidate_rows.append(
                {
                    "source_path": path,
                    "target_path": target["path"],
                    "target_kind": target["kind"],
                    "reference_label": ref.group(1),
                    "answer": resolved["caption"],
                    "status": status,
                }
            )
    return {
        "work_id": capacity["work_id"],
        "category_query": capacity["category_query"],
        "split": capacity["split"],
        "source_archive": capacity["latest_archive"],
        "counts": dict(sorted(counts.items())),
        "candidate_rows": candidate_rows,
    }


def build(config_path: Path) -> dict[str, bytes]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("P110 target resampling config changed")
    probe_config_path = _pin(config["probe_config"])
    probe_path = _pin(config["probe_manifest"])
    quality_path = _pin(config["quality_config"])
    probe = replay_probe(probe_config_path, probe_path.parent, verify_only=True)
    if (
        hashlib.sha256(probe_path.read_bytes()).hexdigest()
        != config["probe_manifest"]["sha256"]
    ):
        raise ValueError("P110 probe changed during resampling")
    quality, _raw, _native = _source_rows(json.loads(quality_path.read_text()))
    capacities = _rows(probe_path.parent / "capacity_index.jsonl")
    examined = [
        _scan(row) for row in capacities if row["status"] == "task_source_candidate"
    ]
    if len(examined) != probe["source_shape_works"]:
        raise ValueError("P110 source-shape inventory differs")
    outputs = {
        "resample_ledger.jsonl": b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
            for row in examined
        ),
        "native_quality_ledger.jsonl": b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
            for row in quality
        ),
    }
    status = Counter(
        item["status"] for row in examined for item in row["candidate_rows"]
    )
    summary = {
        "schema": SCHEMA + ".result",
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "probe_manifest_sha256": config["probe_manifest"]["sha256"],
        "source_shape_works": len(examined),
        "all_file_refs_examined": sum(
            row["counts"].get("refs_examined_all_files", 0) for row in examined
        ),
        "candidate_status": dict(sorted(status.items())),
        "quality_accepted_targets_before_length": status["accepted_raw_tex_reference"],
        "native_raw_views": len(quality),
        "native_quality_status": dict(
            sorted(Counter(row["status"] for row in quality).items())
        ),
        "files_sha256": {
            name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()
        },
        "claim_limit": "same exact-answer uniqueness and shortcut gates; no new reader QA compiled",
        "train_ready": False,
    }
    outputs["manifest.json"] = _dump(summary)
    return outputs


def run(config_path: Path, output_dir: Path, verify_only: bool) -> dict:
    outputs = build(config_path)
    if verify_only:
        if {path.name for path in output_dir.iterdir()} != set(outputs):
            raise ValueError("P110 target resampling file inventory changed")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P110 target resampling replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P110 target resampling output must be new")
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
