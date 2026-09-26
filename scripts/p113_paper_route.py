"""Route only source-shape-positive frozen works to the P96 paper compiler."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p110_paper_probe import run as replay_probe
from scripts.p113_paper_shape import _pin
from scripts.p113_paper_shape import run as replay_shape

SCHEMA = "longworld.p113-paper-route.v1"


def _dump(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def build(config_path: Path, output_dir: Path) -> dict[str, bytes]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("P113 route schema differs")
    probe_config = _pin(config["probe_config"])
    probe_manifest = _pin(config["probe_manifest"])
    shape_config = _pin(config["shape_config"])
    shape_manifest = _pin(config["shape_manifest"])
    replay_probe(probe_config, probe_manifest.parent, verify_only=True)
    replay_shape(shape_config, shape_manifest.parent, verify_only=True)
    probe = json.loads(probe_manifest.read_text())
    shape = json.loads(shape_manifest.read_text())
    if (
        shape["new_unlabeled_works"] != probe["frozen_works"]
        or shape["frozen_works"] != probe["frozen_works"]
        or shape["source_shape_positive_works"] > probe["source_shape_works"]
    ):
        raise ValueError("P113 shape and source probe inventories differ")
    rows = [
        json.loads(x)
        for x in (shape_manifest.parent / "work_matrix.jsonl").read_text().splitlines()
    ]
    positive = {row["work_id"] for row in rows if row["source_shape_positive"]}
    source = json.loads((probe_manifest.parent / "source_config.json").read_text())
    if source.get("schema") != "longworld.p86-frozen-paper-batch.v1":
        raise ValueError("P113 probe source config has wrong schema")
    selected = [
        family
        for family in source["families"]
        if family["family_id"].removeprefix("p110-arxiv-") in positive
    ]
    if len(selected) != len(positive):
        raise ValueError("P113 positive work lacks P110 source family")
    new_source = {"schema": source["schema"], "families": selected}
    outputs = {"source_config.json": _dump(new_source)}
    qa_template = json.loads(_pin(config["qa_template"]).read_text())
    qa_template.update(
        source_config={
            "path": str((output_dir / "source_config.json").relative_to(ROOT)),
            "sha256": hashlib.sha256(outputs["source_config.json"]).hexdigest(),
        },
        prior_candidate_index=config["prior_candidate_index"],
        workers=4,
    )
    _pin(config["prior_candidate_index"])
    outputs["qa_config.json"] = _dump(qa_template)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "frozen_works": shape["frozen_works"],
        "raw_source_shape_works": probe["source_shape_works"],
        "screen_positive_works": len(selected),
        "routed_splits": {
            split: sum(x["split"] == split for x in selected)
            for split in ("train", "eval")
        },
        "source_config_sha256": hashlib.sha256(
            outputs["source_config.json"]
        ).hexdigest(),
        "qa_config_sha256": hashlib.sha256(outputs["qa_config.json"]).hexdigest(),
        "claim_limit": "source-shape route only; P96/P105 reader and mask gates still required",
        "train_ready": False,
    }
    outputs["manifest.json"] = _dump(manifest)
    return outputs


def run(config_path: Path, output_dir: Path, verify_only: bool = False) -> dict:
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    outputs = build(config_path, output_dir)
    if verify_only:
        if not output_dir.is_dir() or {x.name for x in output_dir.iterdir()} != set(
            outputs
        ):
            raise ValueError("P113 route inventory drift")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P113 route replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P113 route output must be new")
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
