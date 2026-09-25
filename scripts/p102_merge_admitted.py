"""Combine globally gated P102 connected worlds into one candidate source pool."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p102_connected_wiki_gate import _url_key
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p102-admitted-union.v1"


def _history_contract(gate: dict) -> tuple[dict, tuple[tuple[str, str], ...]]:
    router = gate.get("prior_router")
    pools = gate.get("prior_pools")
    if (
        not isinstance(router, dict)
        or not isinstance(pools, list)
        or not pools
        or any(not isinstance(pin, dict) for pin in pools)
    ):
        raise ValueError("P102 gate lacks pinned prior history")
    return router, tuple((pin["path"], pin["sha256"]) for pin in pools)


def _require_shared_history(expected: tuple | None, gate: dict) -> tuple:
    actual = _history_contract(gate)
    if expected is not None and actual != expected:
        raise ValueError("P102 gates use different prior title/URL registries")
    return actual


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P102 union pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"P102 union pin changed: {relative}")
    return path


def build(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not config.get("gates")
        or output_dir.exists() != verify_only
    ):
        raise ValueError("invalid P102 admitted union config or output state")
    sources = []
    ledger = []
    common = None
    prior_history = None
    seen_titles = set()
    seen_urls = set()
    seen_names = set()
    gross = Counter()
    for item in config["gates"]:
        manifest_path = _pin(item["manifest"])
        pool_path = _pin(item["source_pool"])
        gate = json.loads(manifest_path.read_text())
        pool = json.loads(pool_path.read_text())
        if (
            gate.get("schema") != "longworld.p102-connected-wiki-gate.v1.result"
            or gate.get("source_pool_sha256") != _sha(pool_path)
            or gate.get("train_ready") is not False
            or pool.get("schema") != "longworld.source-batch-pool.v2"
            or gate.get("admitted_groups") != len(pool["sources"])
        ):
            raise ValueError("P102 gate and admitted source pool differ")
        prior_history = _require_shared_history(prior_history, gate)
        admission = manifest_path.parent / "admission.jsonl"
        if _sha(admission) != gate["admission_sha256"]:
            raise ValueError("P102 gate admission ledger changed")
        rows = [json.loads(line) for line in admission.read_text().splitlines() if line]
        by_id = {source["name"]: source for source in pool["sources"]}
        if {row["snapshot_id"] for row in rows if row["status"] == "accepted"} != set(
            by_id
        ):
            raise ValueError("accepted gate ledger and source inventory differ")
        shared = {key: value for key, value in pool.items() if key != "sources"}
        if common is None:
            common = shared
        elif common != shared:
            raise ValueError("gated pools have incompatible reader contracts")
        gross.update(
            frozen_groups=gate["frozen_groups"], admitted_groups=gate["admitted_groups"]
        )
        for row in rows:
            ledger.append({"gate": item["name"], **row})
            if row["status"] != "accepted":
                continue
            title = row["target_title"].casefold()
            url = _url_key(row["target_url"])
            if title in seen_titles or url in seen_urls:
                raise ValueError("new P102 target title or URL repeats across gates")
            seen_titles.add(title)
            seen_urls.add(url)
            source = by_id[row["snapshot_id"]]
            if source["name"] in seen_names or source["split"] != row["split"]:
                raise ValueError("new P102 source name or split repeats")
            seen_names.add(source["name"])
            snapshot = _snapshot(ROOT, source["snapshot"])
            target = next(
                (
                    doc
                    for doc in snapshot["documents"]
                    if doc["title"].casefold() == title
                ),
                None,
            )
            if target is None or _url_key(target["page_url"]) != url:
                raise ValueError("gated target title or URL differs from frozen source")
            sources.append(source)
    assert common is not None
    if not sources:
        raise ValueError("no globally admitted connected worlds")
    pool = {**common, "sources": sources}
    if not verify_only:
        output_dir.mkdir(parents=True)
    contents = {
        "source_pool.json": json.dumps(
            pool, ensure_ascii=False, sort_keys=True, indent=2
        )
        + "\n",
        "admission.jsonl": "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ledger
        ),
    }
    for name, content in contents.items():
        path = output_dir / name
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"P102 union byte replay differs: {name}")
        else:
            path.write_text(content)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "gates": config["gates"],
        "gross_frozen_groups": gross["frozen_groups"],
        "globally_admitted_groups": len(sources),
        "globally_admitted_tasks": sum(
            row["strict_join_tasks"] for row in ledger if row["status"] == "accepted"
        ),
        "splits": dict(sorted(Counter(source["split"] for source in sources).items())),
        "domains": dict(
            sorted(Counter(source["domain"] for source in sources).items())
        ),
        "source_pool_sha256": _sha(output_dir / "source_pool.json"),
        "admission_sha256": _sha(output_dir / "admission.jsonl"),
        "train_ready": False,
    }
    path = output_dir / "manifest.json"
    if verify_only:
        if json.loads(path.read_text()) != manifest:
            raise ValueError("P102 union manifest replay differs")
    else:
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.config, args.output_dir, verify_only=args.verify_only),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
