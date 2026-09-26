"""Select a bounded new arXiv source cohort from frozen official Atom pages.

No category, topic, or work ID is entered by hand. This stage only proposes
sources; P105 acquisition and the reader compiler make separate decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p104_paper_source_discovery import _split
from scripts.p105_paper_catalog import _page_url, _read_page

SCHEMA = "longworld.p153-paper-source-cohort.v1"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dump(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def pin(value: dict) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("P153 pin requires path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P153 pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or sha(path.read_bytes()) != value["sha256"]:
        raise ValueError(f"P153 pin drift: {relative}")
    return path


def build(config_path: Path, output: Path) -> dict[str, bytes]:
    cfg = json.loads(config_path.read_text())
    if (
        cfg.get("schema") != SCHEMA + ".config"
        or type(cfg.get("work_limit")) is not int
        or not 1 <= cfg["work_limit"] <= 20
        or type(cfg.get("max_per_category")) is not int
        or not 1 <= cfg["max_per_category"] <= 2
        or cfg.get("content_use") != "local_research_only_no_redistribution"
        or len(cfg.get("metadata_manifests", [])) != 3
        or cfg.get("request_interval_seconds", 0) < 3.2
    ):
        raise ValueError("P153 cohort config outside bounded contract")
    plan_path = pin(cfg["taxonomy_plan"])
    plan = json.loads(plan_path.read_text())
    if plan.get("schema") != "longworld.p110-paper-autocatalog.v1.result":
        raise ValueError("P153 needs official P110 taxonomy plan")
    prior_path = pin(cfg["prior_paper_manifest"])
    prior = json.loads(prior_path.read_text())
    if prior.get("source_works") != 34:
        raise ValueError("P153 prior paper inventory changed")
    matrix_path = prior_path.parent / "support_matrix.jsonl"
    if sha(matrix_path.read_bytes()) != prior["files_sha256"]["support_matrix.jsonl"]:
        raise ValueError("P153 prior paper matrix drift")
    prior_ids = {
        json.loads(line)["work_id"] for line in matrix_path.read_text().splitlines()
    }
    pin(cfg["qa_template"])
    pin(cfg["prior_candidate_index"])
    by_category: dict[str, list[dict]] = defaultdict(list)
    ledger = []
    pages = 0
    for shard_name, meta_pin in zip(
        plan["shards"], cfg["metadata_manifests"], strict=True
    ):
        shard_path = plan_path.parent / shard_name
        if sha(shard_path.read_bytes()) != plan["files_sha256"][shard_name]:
            raise ValueError("P153 official shard config drift")
        shard = json.loads(shard_path.read_text())
        metadata_path = pin(meta_pin)
        metadata = json.loads(metadata_path.read_text())
        if (
            metadata.get("schema") != "longworld.p105-paper-catalog.v1.result"
            or metadata.get("config_sha256") != sha(shard_path.read_bytes())
        ):
            raise ValueError("P153 metadata receipt mismatch")
        for index, query in enumerate(shard["queries"]):
            if index >= len(metadata["query_pages"]):
                ledger.append({"query": query, "status": "metadata_page_unavailable"})
                continue
            page = _read_page(
                metadata_path.parent, index, query, _page_url(shard, query)
            )
            if page is None or page[0] != metadata["query_pages"][index]:
                raise ValueError("P153 frozen Atom page drift")
            pages += 1
            for work in page[1]:
                reason = (
                    "already_frozen_work"
                    if work["work_id"] in prior_ids
                    else "single_revision"
                    if work["latest_version"] < 2
                    else "eligible"
                )
                ledger.append(
                    {
                        "query": query,
                        "work_id": work["work_id"],
                        "latest_version": work["latest_version"],
                        "status": reason,
                    }
                )
                if reason == "eligible":
                    by_category[query].append(work)
    # Round-robin official categories; a stable hash diversifies work IDs
    # inside a category without using a hand-maintained title vocabulary.
    ranked = {
        query: sorted(
            {item["work_id"]: item for item in works}.values(),
            key=lambda item: (sha(item["work_id"].encode()), item["work_id"]),
        )[: cfg["max_per_category"]]
        for query, works in by_category.items()
    }
    selected, seen = [], set()
    for position in range(cfg["max_per_category"]):
        for query in sorted(ranked):
            if position >= len(ranked[query]):
                continue
            work = ranked[query][position]
            if work["work_id"] in seen:
                continue
            selected.append(
                {
                    **work,
                    "split": _split(work["work_id"]),
                    "versions": [
                        f"v{work['latest_version'] - 1}",
                        f"v{work['latest_version']}",
                    ],
                }
            )
            seen.add(work["work_id"])
            if len(selected) == cfg["work_limit"]:
                break
        if len(selected) == cfg["work_limit"]:
            break
    if not selected:
        raise ValueError("P153 official metadata provided no novel work")
    catalog = {
        "schema": "longworld.p105-paper-catalog.v1.result",
        "status": "complete",
        "selected_works": selected,
        "selected_count": len(selected),
        "content_use": cfg["content_use"],
        "p153_cohort_config_sha256": sha(config_path.read_bytes()),
    }
    catalog_bytes = dump(catalog)
    catalog_path = output / "admitted_catalog.json"
    acquisition = {
        "schema": "longworld.p105-paper-acquisition.v1.config",
        "catalog_manifest": {
            "path": str(catalog_path.relative_to(ROOT)),
            "sha256": sha(catalog_bytes),
        },
        "content_use": cfg["content_use"],
        "user_agent": cfg["user_agent"],
        "request_interval_seconds": cfg["request_interval_seconds"],
        "workers": 4,
        "work_limit": cfg["work_limit"],
        "max_archive_bytes_total": 121_999_998,
        "qa_template": cfg["qa_template"],
        "prior_candidate_index": cfg["prior_candidate_index"],
    }
    outputs = {
        "admitted_catalog.json": catalog_bytes,
        "acquire_config.json": dump(acquisition),
        "metadata_decisions.jsonl": b"".join(dump(row) for row in ledger),
    }
    manifest = {
        "schema": SCHEMA + ".result",
        "code_sha256": sha(Path(__file__).read_bytes()),
        "config_sha256": sha(config_path.read_bytes()),
        "official_atom_pages_replayed": pages,
        "metadata_decisions": dict(sorted(Counter(x["status"] for x in ledger).items())),
        "selected_novel_works": len(selected),
        "selected_categories": len({row["category_query"] for row in selected}),
        "selected_splits": dict(sorted(Counter(row["split"] for row in selected).items())),
        "files_sha256": {name: sha(data) for name, data in outputs.items()},
        "train_ready": False,
    }
    outputs["manifest.json"] = dump(manifest)
    return outputs


def run(config: Path, output: Path, verify_only: bool = False) -> dict:
    config = config if config.is_absolute() else ROOT / config
    output = output if output.is_absolute() else ROOT / output
    if verify_only and json.loads((output / "manifest.json").read_text())[
        "code_sha256"
    ] != sha(Path(__file__).read_bytes()):
        raise ValueError("P153 cohort code changed since freeze")
    files = build(config, output)
    if verify_only:
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("P153 cohort inventory drift")
        for name, data in files.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"P153 cohort replay drift: {name}")
    else:
        if output.exists():
            raise ValueError("P153 cohort output must be new")
        output.mkdir(parents=True)
        for name, data in files.items():
            (output / name).write_bytes(data)
    return json.loads(files["manifest.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output, args.verify_only), sort_keys=True))


if __name__ == "__main__":
    main()
