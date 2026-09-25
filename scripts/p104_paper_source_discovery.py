"""Shard-ready discovery of frozen arXiv source trees for P96 reference QA.

Inventory files, not a hand-written work list, determine source candidates.
Every selected archive is rehashed by the P86 source verifier. This planner
does not fetch papers, invent gold answers, or admit reader tasks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.p66_researchlab_taskbank import canonical, load_text_tar
from scripts.p96_paper_caption_qa import discover, render_files
from scripts.run_p86_frozen_paper_batch import _dedupe, _inventory
from scripts.run_p96_paper_reference_qa import _prior

SCHEMA = "longworld.p104-paper-source-discovery.v1"
WORK_ID = re.compile(r"arxiv:(\d{4}\.\d{4,5})\Z")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P104 paths must be workspace-relative")
    return ROOT / relative


def _pin(pin: dict[str, str]) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("P104 pin requires path and sha256")
    path = _path(pin["path"])
    if not path.is_file() or sha(path) != pin["sha256"]:
        raise ValueError(f"P104 input pin drift: {pin['path']}")
    return path


def _split(work_id: str) -> str:
    residue = int(hashlib.sha256(work_id.encode()).hexdigest(), 16) % 10
    return "eval" if residue in {0, 1} else "train"


def _inventory_row(path: Path) -> dict:
    payload = json.loads(path.read_text())
    records = payload.get("records", [])
    ids = {item.get("work_id") for item in records}
    if (
        payload.get("source_status") != "public_api_export"
        or not isinstance(records, list)
        or not records
        or len(ids) != 1
    ):
        return {
            "inventory": str(path.relative_to(ROOT)),
            "reason": "invalid_public_single_work_inventory",
        }
    match = WORK_ID.fullmatch(next(iter(ids)) or "")
    if match is None or not payload.get("authorization", {}).get("basis"):
        return {
            "inventory": str(path.relative_to(ROOT)),
            "reason": "missing_work_id_or_source_basis",
        }
    return {
        "inventory": str(path.relative_to(ROOT)),
        "inventory_sha256": sha(path),
        "work_id": match.group(1),
        "recorded_revisions": len(records),
    }


def _signed_bundle(inventory_path: Path, entry: dict) -> dict | None:
    valid = []
    for path in sorted(
        inventory_path.parent.glob("paper_source_workflow_bundle*.signed.json")
    ):
        pin = {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
        try:
            _inventory({**entry, "signed_bundle": pin})
        except ValueError:
            continue
        valid.append(pin)
    return valid[0] if valid else None


def _probe(row: dict) -> tuple[dict, dict | None]:
    path = ROOT / row["inventory"]
    entry = {
        "family_id": "p104-arxiv-" + row["work_id"],
        "split": _split(row["work_id"]),
        "inventory": {"path": row["inventory"], "sha256": row["inventory_sha256"]},
    }
    bundle = _signed_bundle(path, entry)
    if bundle is not None:
        entry["signed_bundle"] = bundle
    work = _inventory(entry)
    source = work["sources"][-1]
    try:
        files, duplicates = _dedupe(load_text_tar(ROOT / source["path"]))
        context = render_files(files)
        links, reasons = discover(context)
    except (OSError, ValueError) as error:
        return (
            {
                **row,
                "split": entry["split"],
                "latest_archive": source,
                "signed_bundle_pinned": work["signed_bundle_pinned"],
                "status": "rejected_source_parser",
                "reason": f"{type(error).__name__}:{error}",
            },
            None,
        )
    capacity = {
        **row,
        "split": entry["split"],
        "latest_archive": source,
        "signed_bundle_pinned": work["signed_bundle_pinned"],
        "source_files": len(files),
        "duplicate_paths_removed": duplicates,
        "source_chars": len(context),
        "cross_file_links": len(links),
        "discovery_reasons": reasons,
        "status": "task_source_candidate" if links else "rejected_no_cross_file_link",
    }
    return capacity, entry if links else None


def plan(config_path: Path) -> dict[str, str]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config" or config.get("workers") != 4:
        raise ValueError("P104 requires four bounded discovery workers")
    root = _path(config["inventory_root"])
    if not root.is_dir():
        raise ValueError("P104 inventory root missing")
    prior_source_path = _pin(config["prior_source_config"])
    prior_index_path = _pin(config["prior_candidate_index"])
    template_path = _pin(config["qa_template"])
    prior_source = json.loads(prior_source_path.read_text())
    if prior_source.get("schema") != "longworld.p86-frozen-paper-batch.v1":
        raise ValueError("wrong prior paper source schema")
    prior_work_ids = {
        _inventory(entry)["work_id"] for entry in prior_source["families"]
    }
    # The existing P96 verifier checks the complete candidate index and pins
    # prior paper answers. Also keep work-level exposure out of both splits.
    _prior(config["prior_candidate_index"])
    prior_index_work_ids = set()
    for line in (
        (prior_index_path.parent / "candidate_refs.jsonl").read_text().splitlines()
    ):
        candidate = json.loads(line)["candidate"]
        source = candidate.get("source_group", "")
        if source.startswith("researchlab:arxiv:"):
            prior_index_work_ids.add(source.rsplit(":", 1)[-1])
    inventory_files = sorted(root.rglob("paper_fetch_inventory.json"))
    if len(inventory_files) > config["max_inventory_files"]:
        raise ValueError("P104 inventory scan exceeds declared bound")
    ledger = [_inventory_row(path) for path in inventory_files]
    grouped: dict[str, list[dict]] = defaultdict(list)
    pending = []
    for row in ledger:
        work_id = row.get("work_id")
        if work_id is None:
            continue
        if work_id in prior_work_ids or work_id in prior_index_work_ids:
            row["status"] = "rejected_prior_work_exposure"
        else:
            grouped[work_id].append(row)
    for work_id, rows in sorted(grouped.items()):
        chosen = min(
            rows, key=lambda row: (-row["recorded_revisions"], row["inventory"])
        )
        pending.append(chosen)
        for row in rows:
            if row is not chosen:
                row["status"] = "rejected_duplicate_inventory_for_work"
    with ProcessPoolExecutor(max_workers=4) as pool:
        examined = list(pool.map(_probe, pending))
    capacities = [row for row, _entry in examined]
    chosen_by_inventory = {row["inventory"]: row for row in capacities}
    for row in ledger:
        if row["inventory"] in chosen_by_inventory:
            row.update(chosen_by_inventory[row["inventory"]])
    families = [entry for _row, entry in examined if entry is not None]
    families.sort(key=lambda row: row["family_id"])
    if len({row["family_id"] for row in families}) != len(families):
        raise ValueError("P104 selected paper work repeats")
    source_config = {
        "schema": "longworld.p86-frozen-paper-batch.v1",
        "families": families,
    }
    outputs: dict[str, str] = {}
    outputs["source_config.json"] = (
        json.dumps(source_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    source_pin = {
        "path": str(
            (_path(config["output_dir"]) / "source_config.json").relative_to(ROOT)
        ),
        "sha256": hashlib.sha256(outputs["source_config.json"].encode()).hexdigest(),
    }
    qa_config = json.loads(template_path.read_text())
    if qa_config.get("schema") != "longworld.p96-paper-reference-qa.v1":
        raise ValueError("wrong P96 QA template schema")
    qa_config.update(
        source_config=source_pin,
        prior_candidate_index=config["prior_candidate_index"],
        workers=4,
    )
    outputs["qa_config.json"] = (
        json.dumps(qa_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    outputs["inventory_ledger.jsonl"] = "".join(canonical(row) + "\n" for row in ledger)
    outputs["capacity_index.jsonl"] = "".join(
        canonical(row) + "\n" for row in capacities
    )
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": sha(config_path),
        "source_root": config["inventory_root"],
        "inventory_files": len(inventory_files),
        "unique_work_ids": len({row["work_id"] for row in ledger if "work_id" in row}),
        "prior_work_ids": len(prior_work_ids | prior_index_work_ids),
        "new_work_ids_probed": len(pending),
        "task_source_works": len(families),
        "raw_cross_file_links": sum(
            row.get("cross_file_links", 0) for row in capacities
        ),
        "selected_splits": dict(
            sorted(Counter(row["split"] for row in families).items())
        ),
        "rejection_reasons": dict(
            sorted(
                Counter(
                    row.get("reason", row.get("status", ""))
                    for row in ledger
                    if row.get("status") != "task_source_candidate"
                ).items()
            )
        ),
        "source_config_sha256": source_pin["sha256"],
        "qa_config_sha256": hashlib.sha256(
            outputs["qa_config.json"].encode()
        ).hexdigest(),
        "files_sha256": {
            name: hashlib.sha256(content.encode()).hexdigest()
            for name, content in outputs.items()
        },
        "source_archives_acquired": 0,
        "native_tasks": 0,
        "train_ready": False,
    }
    outputs["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return outputs


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    if _path(config["output_dir"]) != output_dir:
        raise ValueError("P104 output path differs from pinned source config path")
    outputs = plan(config_path)
    if verify_only:
        if not output_dir.is_dir() or {p.name for p in output_dir.iterdir()} != set(
            outputs
        ):
            raise ValueError("P104 source discovery file inventory drift")
        for name, content in outputs.items():
            if (output_dir / name).read_text() != content:
                raise ValueError(f"P104 source discovery replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P104 source discovery output must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            (output_dir / name).write_text(content)
    return json.loads(outputs["manifest.json"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(canonical(run(args.config, args.output_dir, verify_only=args.verify_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
