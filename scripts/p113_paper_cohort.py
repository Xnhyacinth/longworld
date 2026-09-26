"""Plan new arXiv source work from already frozen P110 official metadata.

This only plans a bounded acquisition. Frozen source, shape, reader, and mask
results must be produced and checked by their separate existing gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p113_paper_shape import _pin

SCHEMA = "longworld.p113-paper-cohort.v1"


def _dump(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def build(config_path: Path, output_dir: Path) -> dict[str, bytes]:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("max_new_works") != 6
        or config.get("workers") != 4
    ):
        raise ValueError("P113 cohort contract changed")
    catalog_path = _pin(config["metadata_catalog"])
    fetch_path = _pin(config["prior_fetch_manifest"])
    template_path = _pin(config["acquire_template"])
    catalog = json.loads(catalog_path.read_text())
    prior = json.loads(fetch_path.read_text())
    template = json.loads(template_path.read_text())
    if (
        catalog.get("status") != "complete"
        or catalog.get("schema") != "longworld.p105-paper-catalog.v1.result"
        or catalog.get("content_use") != "local_research_only_no_redistribution"
        or prior.get("schema") != "longworld.p105-paper-acquisition.v1.fetch-result"
        or prior.get("catalog_manifest_sha256") != config["metadata_catalog"]["sha256"]
        or prior.get("status") != "transport_blocked"
        or template.get("schema") != "longworld.p105-paper-acquisition.v1.config"
    ):
        raise ValueError("P113 metadata and prior source run do not match")
    attempted = {row["work_id"] for row in prior["source_receipts"] + prior["errors"]}
    pending = [
        row for row in catalog["selected_works"] if row["work_id"] not in attempted
    ]
    if len(pending) < config["max_new_works"]:
        raise ValueError("P113 has too few previously unattempted source works")
    chosen = pending[: config["max_new_works"]]
    if (
        len({row["work_id"] for row in chosen}) != len(chosen)
        or any(row["license_status"] != "not_reported_by_atom" for row in chosen)
        or any(len(row["versions"]) != 2 for row in chosen)
    ):
        raise ValueError("P113 selected work provenance differs")
    new_catalog = {
        "schema": "longworld.p105-paper-catalog.v1.result",
        "status": "complete",
        "content_use": "local_research_only_no_redistribution",
        "selected_count": len(chosen),
        "selected_works": chosen,
        "source_metadata_manifest_sha256": config["metadata_catalog"]["sha256"],
        "prior_fetch_manifest_sha256": config["prior_fetch_manifest"]["sha256"],
    }
    outputs = {"source_catalog.json": _dump(new_catalog)}
    source_config = {
        **template,
        "catalog_manifest": {
            "path": str((output_dir / "source_catalog.json").relative_to(ROOT)),
            "sha256": hashlib.sha256(outputs["source_catalog.json"]).hexdigest(),
        },
        "work_limit": len(chosen),
        "max_archive_bytes_total": 250_000_000,
    }
    outputs["acquire_config.json"] = _dump(source_config)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "prior_selected_works": len(catalog["selected_works"]),
        "prior_attempted_works": len(attempted),
        "pending_works": len(pending),
        "planned_new_works": len(chosen),
        "planned_splits": {
            split: sum(row["split"] == split for row in chosen)
            for split in ("train", "eval")
        },
        "planned_categories": sorted({row["category_query"] for row in chosen}),
        "source_catalog_sha256": hashlib.sha256(
            outputs["source_catalog.json"]
        ).hexdigest(),
        "acquire_config_sha256": hashlib.sha256(
            outputs["acquire_config.json"]
        ).hexdigest(),
        "content_use": "local_research_only_no_redistribution",
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
            raise ValueError("P113 cohort inventory drift")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P113 cohort replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P113 cohort output must be new")
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
