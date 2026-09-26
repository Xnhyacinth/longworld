"""Curate P108 added-code readers against both P99 and P107 PR-pair exposure."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p99_code_content_tasks import canonical
from scripts.p107_code_curate import (
    OUTPUTS,
    _pair,
    _pin,
    _rows,
    _sha,
    _verify_files,
)
from scripts.p107_code_curate import (
    build as build_p107,
)

SECOND_PRIOR_FIELDS = (
    "second_prior_native_manifest",
    "second_prior_curated_manifest",
    "second_prior_mask_manifest",
)


def _second_prior(config: dict) -> tuple[set[str], set[tuple]]:
    """Verify the P107 raw→curated→mask chain and all exposed raw PR pairs."""
    raw_path, curated_path, mask_path = (
        _pin(config[name]) for name in SECOND_PRIOR_FIELDS
    )
    raw = json.loads(raw_path.read_text())
    curated = verify_merge(curated_path.parent)
    mask = json.loads(mask_path.read_text())
    if (
        raw.get("schema_version") != "longworld.p99-code-content.v1"
        or curated.get("curation_schema") != "longworld.p107-code-curation.v1"
        or curated.get("raw_native_manifest_sha256") != _sha(raw_path)
        or curated.get("prior_native_manifest_sha256")
        != config["prior_native_manifest"]["sha256"]
        or curated.get("candidate_views", 0) < 1
        or mask.get("schema_version") != "longworld.unified-reader-mask-all.v1"
        or mask.get("source_manifest_sha256") != _sha(curated_path)
        or mask.get("source_index_sha256")
        != curated["files_sha256"]["sample_index.jsonl"]
        or mask.get("audited_views") != curated["candidate_views"]
        or mask.get("train_ready") is not False
    ):
        raise ValueError("P111 second prior raw/curated/mask chain differs")
    _verify_files(raw_path.parent, raw["files_sha256"])
    raw_index = _rows(raw_path.parent / "sample_index.jsonl")
    raw_audit = _rows(raw_path.parent / "audit.jsonl")
    curated_index = _rows(curated_path.parent / "sample_index.jsonl")
    quality_path = curated_path.parent / "quality_ledger.jsonl"
    raw_ids = {row["semantic_task_id"] for row in raw_index}
    raw_pairs = {_pair(row) for row in raw_audit}
    if (
        len(raw_index) != raw["views"]
        or len(raw_audit) != raw["views"]
        or len(raw_ids) != len(raw_index)
        or len(raw_pairs) != len(raw_audit)
        or len(curated_index) != curated["candidate_views"]
        or _sha(quality_path) != curated["quality_ledger_sha256"]
        or not {row["semantic_task_id"] for row in curated_index} <= raw_ids
    ):
        raise ValueError("P111 second prior task or PR-pair inventory differs")
    return raw_ids, raw_pairs


def _no_second_prior_overlap(
    raw_index: list[dict],
    raw_audit: list[dict],
    old_ids: set[str],
    old_pairs: set[tuple],
) -> None:
    if len(raw_index) != len(raw_audit):
        raise ValueError("P111 new native index/audit length differs")
    audit_by_sample = {row["sample_id"]: row for row in raw_audit}
    if len(audit_by_sample) != len(raw_audit) or {
        row["sample_id"] for row in raw_index
    } != set(audit_by_sample):
        raise ValueError("P111 new native audit repeats sample ID")
    for row in raw_index:
        pair = _pair(audit_by_sample[row["sample_id"]])
        if row["semantic_task_id"] in old_ids or pair in old_pairs:
            raise ValueError("P111 added-code PR pair or semantic task repeats P107")


def build(config_path: Path) -> dict[str, str]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != "longworld.p107-code-curation.v1.config" or any(
        field not in config for field in SECOND_PRIOR_FIELDS
    ):
        raise ValueError("P111 curator config requires both prior pools")
    old_ids, old_pairs = _second_prior(config)
    new_path = _pin(config["raw_native_manifest"])
    new = json.loads(new_path.read_text())
    _verify_files(new_path.parent, new["files_sha256"])
    _no_second_prior_overlap(
        _rows(new_path.parent / "sample_index.jsonl"),
        _rows(new_path.parent / "audit.jsonl"),
        old_ids,
        old_pairs,
    )
    outputs = build_p107(config_path)
    manifest = json.loads(outputs["manifest.json"])
    ledger = _rows_from_text(outputs["quality_ledger.jsonl"])
    if len(ledger) != new["views"] or manifest["candidate_views"] != sum(
        row["status"] == "accepted_new_pr_pair" for row in ledger
    ):
        raise ValueError("P111 curator gross/net ledger differs")
    manifest.update(
        second_prior_native_manifest_sha256=config["second_prior_native_manifest"][
            "sha256"
        ],
        second_prior_curated_manifest_sha256=config["second_prior_curated_manifest"][
            "sha256"
        ],
        second_prior_mask_manifest_sha256=config["second_prior_mask_manifest"][
            "sha256"
        ],
        second_prior_raw_pr_pairs_checked=len(old_pairs),
        second_prior_raw_semantic_ids_checked=len(old_ids),
        claim_limit="P99 finite added-code two-witness and one 16K raw window; no unrestricted dependency proof",
    )
    outputs["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return outputs


def _rows_from_text(value: str) -> list[dict]:
    return [json.loads(line) for line in value.splitlines()]


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    outputs = build(config_path)
    if verify_only:
        if not output_dir.is_dir() or {
            path.name for path in output_dir.iterdir()
        } != set(OUTPUTS):
            raise ValueError("P111 curated output inventory differs")
        for name, content in outputs.items():
            if (output_dir / name).read_text() != content:
                raise ValueError(f"P111 curated replay differs: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P111 curated output must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            (output_dir / name).write_text(content)
    return verify_merge(output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(canonical(run(args.config, args.output_dir, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
