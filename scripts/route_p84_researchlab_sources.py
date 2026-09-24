"""Route signed, frozen arXiv revision families into the P66 native taskbank.

The signed source manifest defines the version inventory and archive hashes.
The existing P66 compiler decides whether a family has substantive long-form
revision tasks; zero-yield families remain in the rejection receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import materialize_p66_researchlab_taskbank as native

SCHEMA = "longworld.p84-researchlab-source-route.v1"
ARCHIVE = re.compile(r"arxiv-(\d{4}\.\d{4,5})v(\d+)\.source\.tar\Z")


def _path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source path must be workspace-relative")
    return ROOT / relative


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pinned(pin: dict[str, str]) -> Path:
    path = _path(pin["path"])
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"pinned file changed: {pin['path']}")
    return path


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _family(entry: dict[str, Any], prior_work_ids: set[str]) -> dict[str, Any]:
    bundle_path = _pinned(entry["signed_bundle"])
    bundle = json.loads(bundle_path.read_text())
    attestation = bundle.get("attestation", {})
    if (
        attestation.get("role") != "source"
        or attestation.get("scheme") != "hmac-sha256-v2"
        or not attestation.get("key_id")
        or len(bundle.get("entries", [])) != 1
    ):
        raise ValueError("source bundle identity is invalid")
    item = bundle["entries"][0]
    manifest_path = bundle_path.parent / item["path"]
    if _sha(manifest_path) != item["sha256"]:
        raise ValueError("signed source manifest changed")
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("source_status") != "public_api_export"
        or not manifest.get("authorization", {}).get("basis")
        or not manifest.get("records")
    ):
        raise ValueError("source manifest lacks provenance or local authorization")
    sources = []
    work_ids = set()
    for record in manifest["records"]:
        source_record = bundle_path.parent / record["source_file"]
        if (
            _sha(source_record) != record["source_sha256"]
            or hashlib.sha256(record["text"].encode()).hexdigest()
            != record["text_sha256"]
        ):
            raise ValueError("signed source record changed")
        rendered = json.loads(record["text"])
        archive = rendered["source_archive"]
        filename = record["record_id"].replace("arxiv:", "arxiv-") + ".source.tar"
        match = ARCHIVE.fullmatch(filename)
        if match is None or record.get("revision_id") != f"v{match.group(2)}":
            raise ValueError("source revision/archive mapping is invalid")
        work_ids.add(match.group(1))
        relative = str((bundle_path.parent / filename).relative_to(ROOT))
        path = _path(relative)
        if not path.is_file() or _sha(path) != archive.get("sha256"):
            raise ValueError("source archive hash changed")
        sources.append(
            {
                "version": f"v{match.group(2)}",
                "path": relative,
                "sha256": archive["sha256"],
            }
        )
    if len(work_ids) != 1 or work_ids & prior_work_ids:
        raise ValueError("source work overlaps prior bank or mixes papers")
    if len({source["version"] for source in sources}) != len(sources):
        raise ValueError("duplicate source revision")
    sources.sort(key=lambda source: int(source["version"][1:]))
    if entry["split"] not in {"train", "eval"}:
        raise ValueError("invalid source family split")
    return {
        "family_id": entry["family_id"],
        "kind": "arxiv_source_tar",
        "split": entry["split"],
        "source_workflow_bundle": {
            **entry["signed_bundle"],
            "key_id": attestation["key_id"],
        },
        "sources": sources,
    }


def route(config_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or not 1 <= len(config.get("families", [])) <= 20:
        raise ValueError("invalid P84 source route")
    prior_path = _pinned(config["prior_bank_config"])
    prior = json.loads(prior_path.read_text())
    if prior.get("schema_version") != native.SCHEMA:
        raise ValueError("wrong prior ResearchLab bank schema")
    prior_work_ids = set()
    for family in prior["families"]:
        for source in family["sources"]:
            match = ARCHIVE.fullmatch(Path(source["path"]).name)
            if match:
                prior_work_ids.add(match.group(1))
    families = []
    names = set()
    for entry in config["families"]:
        if entry["family_id"] in names:
            raise ValueError("duplicate family ID")
        names.add(entry["family_id"])
        families.append(_family(entry, prior_work_ids))
    native._init_tokenizer(prior["tokenizer"])
    selected = []
    reports = []
    for family in families:
        specs, rejects = native._tar_specs(family)
        reports.append(
            {
                "family_id": family["family_id"],
                "split": family["split"],
                "versions": len(family["sources"]),
                "native_specs": len(specs),
                "native_source_rejects": len(rejects),
                "source_rejection_reasons": sorted(
                    {item["reason"] for item in rejects}
                ),
                "version_pairs": sorted(
                    {(item["old_version"], item["new_version"]) for item in specs}
                ),
                "capacity_bins": {
                    str(capacity): sum(
                        item["expected_capacity"] == capacity for item in specs
                    )
                    for capacity in (65536, 131072, 262144)
                },
            }
        )
        if specs:
            selected.append(family)
    if not selected:
        raise ValueError("no source family supports native long revision tasks")
    output_dir.mkdir(parents=True)
    native_config = {
        "schema_version": native.SCHEMA,
        "data_product": "p84_researchlab_indexed_sources_v1",
        "local_derivative_synthesis_authorized": True,
        "tokenizer": prior["tokenizer"],
        "families": selected,
    }
    _write(output_dir / "native_config.json", native_config)
    receipt = {
        "schema": SCHEMA + ".result",
        "route_config_sha256": _sha(config_path),
        "prior_bank_config_sha256": _sha(prior_path),
        "native_config_sha256": _sha(output_dir / "native_config.json"),
        "families_scanned": len(families),
        "families_selected": len(selected),
        "reports": reports,
        "train_ready": False,
    }
    _write(output_dir / "routing_receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            route(args.config, args.output_dir), ensure_ascii=False, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
