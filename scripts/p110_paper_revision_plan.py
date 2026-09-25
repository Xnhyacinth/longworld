"""Compile all frozen P110 two-revision works into a P86 revision-QA plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p105_paper_acquire import _config as acquire_config
from scripts.p105_paper_acquire import _existing_inventory, _work_dir
from scripts.p110_paper_autocatalog import _pin
from scripts.p110_paper_probe import ALLOWED_STATUS

SCHEMA = "longworld.p110-paper-revision-plan.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def build(config_path: Path) -> dict[str, bytes]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("P110 revision config schema changed")
    fetch_path = _pin(config["fetch_manifest"])
    fetch = json.loads(fetch_path.read_text())
    probe_path = _pin(config["probe_manifest"])
    probed = json.loads(probe_path.read_text())
    acquire_path = _pin(config["acquire_config"])
    _acquire, catalog = acquire_config(acquire_path)
    prior_template = json.loads(_pin(config["revision_template"]).read_text())
    if (
        fetch.get("schema") != "longworld.p105-paper-acquisition.v1.fetch-result"
        or fetch.get("status") not in ALLOWED_STATUS
        or fetch.get("config_sha256") != _sha(acquire_path)
        or fetch.get("catalog_manifest_sha256")
        != _acquire["catalog_manifest"]["sha256"]
        or prior_template.get("schema") != "longworld.p86-frozen-paper-batch.v1"
        or probed.get("schema") != "longworld.p110-paper-probe.v1.result"
        or probed.get("fetch_manifest_sha256") != _sha(fetch_path)
        or probed.get("frozen_works") != fetch.get("frozen_works")
    ):
        raise ValueError("P110 frozen source or revision template is not complete")
    families = []
    receipts = []
    for work in catalog["selected_works"]:
        cached = _existing_inventory(
            _work_dir(fetch_path.parent, work["work_id"]), work
        )
        if cached is None:
            continue
        receipt, entry = cached
        entry["family_id"] = "p110-revision-arxiv-" + work["work_id"]
        families.append(entry)
        receipts.append(receipt)
    if receipts != fetch["source_receipts"] or len(receipts) != fetch["frozen_works"]:
        raise ValueError("P110 source receipt or work order changed")
    if not families:
        raise ValueError("P110 source packet has no two-revision work")
    prior_template.update(
        workers=4,
        min_final_tokens=8192,
        max_final_tokens=131072,
        min_evidence_extent_tokens=2048,
        families=families,
    )
    outputs = {"revision_config.json": _dump(prior_template)}
    outputs["manifest.json"] = _dump(
        {
            "schema": SCHEMA + ".result",
            "config_sha256": _sha(config_path),
            "fetch_manifest_sha256": _sha(fetch_path),
            "source_works": len(families),
            "non_cs_works": sum(
                not receipt["query"].startswith("cat:cs.") for receipt in receipts
            ),
            "operation_scope": "explicit raw-TeX two-revision prose alignment",
            "files_sha256": {
                name: hashlib.sha256(content).hexdigest()
                for name, content in outputs.items()
            },
            "train_ready": False,
        }
    )
    return outputs


def run(config_path: Path, output_dir: Path, verify_only: bool) -> dict:
    outputs = build(config_path)
    if verify_only:
        if {path.name for path in output_dir.iterdir()} != set(outputs):
            raise ValueError("P110 revision plan inventory changed")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P110 revision plan replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P110 revision plan output must be new")
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
