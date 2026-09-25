"""Probe every safely frozen P110 paper, including a transport-stopped cohort."""

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

from scripts.p104_paper_source_discovery import _probe
from scripts.p105_paper_acquire import _config as acquire_config
from scripts.p105_paper_acquire import _existing_inventory, _work_dir
from scripts.p110_paper_autocatalog import _pin

SCHEMA = "longworld.p110-paper-probe.v1"
ALLOWED_STATUS = {
    "complete",
    "complete_with_source_rejects",
    "transport_blocked",
    "archive_budget_exceeded",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def build(config_path: Path, output_dir: Path) -> dict[str, bytes]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config" or config.get("workers") != 4:
        raise ValueError("P110 source probe needs four bounded workers")
    source_path = _pin(config["fetch_manifest"])
    fetched = json.loads(source_path.read_text())
    acquire_path = _pin(config["acquire_config"])
    acquire, catalog = acquire_config(acquire_path)
    template_path = _pin(config["qa_template"])
    prior_index_path = _pin(config["prior_candidate_index"])
    if (
        fetched.get("schema") != "longworld.p105-paper-acquisition.v1.fetch-result"
        or fetched.get("status") not in ALLOWED_STATUS
        or fetched.get("config_sha256") != _sha(acquire_path.read_bytes())
        or fetched.get("catalog_manifest_sha256")
        != acquire["catalog_manifest"]["sha256"]
        or fetched.get("content_use") != "local_research_only_no_redistribution"
        or fetched.get("selected_works") != len(catalog["selected_works"])
        or fetched.get("archive_bytes", 0) > config["hard_archive_bytes"]
        or config["hard_archive_bytes"] != 250_000_000
    ):
        raise ValueError("P110 source result or byte ceiling is invalid")
    receipts, probe_inputs = [], []
    for work in catalog["selected_works"]:
        cached = _existing_inventory(
            _work_dir(source_path.parent, work["work_id"]), work
        )
        if cached is None:
            continue
        receipt, entry = cached
        receipts.append(receipt)
        probe_inputs.append(
            {
                "inventory": entry["inventory"]["path"],
                "inventory_sha256": entry["inventory"]["sha256"],
                "work_id": work["work_id"],
                "recorded_revisions": 2,
            }
        )
    if (
        receipts != fetched.get("source_receipts")
        or fetched.get("frozen_works") != len(receipts)
        or fetched.get("frozen_source_archives") != 2 * len(receipts)
        or fetched.get("archive_bytes") != sum(row["archive_bytes"] for row in receipts)
        or not receipts
    ):
        raise ValueError("P110 frozen source receipts differ from verified inventories")
    with ProcessPoolExecutor(max_workers=4) as workers:
        examined = list(workers.map(_probe, probe_inputs))
    capacity = []
    families = []
    by_id = {row["work_id"]: row for row in receipts}
    for row, entry in examined:
        source = by_id[row["work_id"]]
        capacity.append(
            {
                **row,
                "category_query": source["query"],
                "license_status": source["license_status"],
                "license_uri": source["license_uri"],
            }
        )
        if entry is not None:
            entry["family_id"] = "p110-arxiv-" + row["work_id"]
            families.append(entry)
    families.sort(key=lambda row: row["family_id"])
    source_config = {
        "schema": "longworld.p86-frozen-paper-batch.v1",
        "families": families,
    }
    source_bytes = _dump(source_config)
    source_config_path = output_dir / "source_config.json"
    qa = json.loads(template_path.read_text())
    qa.update(
        source_config={
            "path": str(source_config_path.relative_to(ROOT)),
            "sha256": _sha(source_bytes),
        },
        prior_candidate_index=config["prior_candidate_index"],
        workers=4,
    )
    if _sha(prior_index_path.read_bytes()) != config["prior_candidate_index"]["sha256"]:
        raise ValueError("P110 prior task universe drift")
    outputs = {
        "source_config.json": source_bytes,
        "qa_config.json": _dump(qa),
        "capacity_index.jsonl": b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
            for row in capacity
        ),
    }
    summary = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path.read_bytes()),
        "fetch_manifest_sha256": _sha(source_path.read_bytes()),
        "fetch_status": fetched["status"],
        "selected_works": fetched["selected_works"],
        "frozen_works": len(receipts),
        "frozen_non_cs_works": sum(
            not row["query"].startswith("cat:cs.") for row in receipts
        ),
        "source_shape_works": len(families),
        "source_shape_non_cs_works": sum(
            not by_id[entry["family_id"].removeprefix("p110-arxiv-")][
                "query"
            ].startswith("cat:cs.")
            for entry in families
        ),
        "raw_cross_file_links": sum(row.get("cross_file_links", 0) for row in capacity),
        "source_shape_status": dict(
            sorted(Counter(row["status"] for row in capacity).items())
        ),
        "acquisition_rejections": dict(
            sorted(Counter(row["status"] for row in fetched["errors"]).items())
        ),
        "qa_config_sha256": _sha(outputs["qa_config.json"]),
        "files_sha256": {name: _sha(data) for name, data in outputs.items()},
        "content_use": "local_research_only_no_redistribution",
        "train_ready": False,
    }
    outputs["probe_manifest.json"] = _dump(summary)
    return outputs


def run(config_path: Path, output_dir: Path, verify_only: bool) -> dict:
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    outputs = build(config_path, output_dir)
    if verify_only:
        if {path.name for path in output_dir.iterdir()} != set(outputs):
            raise ValueError("P110 source probe inventory changed")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P110 source probe replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P110 source probe output must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            (output_dir / name).write_bytes(content)
    return json.loads(outputs["probe_manifest.json"])


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
