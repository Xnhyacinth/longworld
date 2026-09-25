"""Admit only P97 Wiki delta groups novel by both title and page URL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_p97_wiki_intake_overlap import report, sha
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p97-wiki-delta-gate.v1"


def _root_path(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError("P97 gate path cannot traverse parents")
    path = path if path.is_absolute() else ROOT / path
    if not path.is_relative_to(ROOT):
        raise ValueError("P97 gate path must be workspace-relative")
    return path


def build(
    prior_pool: Path,
    intake_dirs: list[Path],
    delta_pool: Path,
    *,
    prior_router: Path | None = None,
) -> tuple[dict, dict]:
    prior_pool, delta_pool = _root_path(prior_pool), _root_path(delta_pool)
    audit = report(prior_pool, intake_dirs, prior_router=prior_router)
    delta = json.loads(delta_pool.read_text())
    if delta.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("delta source pool schema mismatch")
    source_by_name = {}
    for intake_dir in intake_dirs:
        intake_dir = _root_path(intake_dir)
        for source in json.loads((intake_dir / "source_pool.json").read_text())[
            "sources"
        ]:
            if source["name"] in source_by_name:
                raise ValueError("intake source names repeat")
            source_by_name[source["name"]] = source
    expected_sources = [source_by_name[name] for name in audit["accepted_groups"]]
    if delta["sources"] != expected_sources:
        raise ValueError(
            "delta source records differ from title/URL-clean intake records"
        )
    names = [source["name"] for source in expected_sources]
    prior = json.loads(prior_pool.read_text())
    seen_titles = set()
    seen_urls = set()
    for source in prior["sources"] + delta["sources"]:
        for doc in _snapshot(ROOT, source["snapshot"])["documents"]:
            title, url = doc["title"].casefold(), doc["page_url"]
            if title in seen_titles or url in seen_urls:
                raise ValueError("gated source pool repeats a title or page URL")
            seen_titles.add(title)
            seen_urls.add(url)
    result = {
        "schema": SCHEMA + ".result",
        "gate_sha256": sha(Path(__file__)),
        "overlap_auditor_sha256": sha(
            Path(__file__).with_name("audit_p97_wiki_intake_overlap.py")
        ),
        "prior_pool": {
            "path": str(prior_pool.relative_to(ROOT)),
            "sha256": sha(prior_pool),
        },
        "prior_router": audit["prior_router"],
        "delta_pool": {
            "path": str(delta_pool.relative_to(ROOT)),
            "sha256": sha(delta_pool),
        },
        "gated_source_pool_sha256": sha(delta_pool),
        "intakes": audit["intakes"],
        "gross": audit["gross"],
        "net_novel": audit["net_novel"],
        "overlap_groups": len(audit["overlap_groups"]),
        "cross_split_overlap_groups": audit["cross_split_overlap_groups"],
        "url_only_overlap_groups_rejected": audit["url_only_overlap_groups"],
        "accepted_groups": names,
        "train_ready": False,
    }
    return delta, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-router", type=Path)
    parser.add_argument("--prior-pool", type=Path, required=True)
    parser.add_argument("--intake-dir", type=Path, action="append", required=True)
    parser.add_argument("--delta-pool", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    _, result = build(
        args.prior_pool,
        args.intake_dir,
        args.delta_pool,
        prior_router=args.prior_router,
    )
    output = _root_path(args.output_dir)
    delta_bytes = _root_path(args.delta_pool).read_bytes()
    if args.verify_only:
        if {path.name for path in output.iterdir()} != {
            "source_pool.json",
            "manifest.json",
        }:
            raise ValueError("P97 gate file inventory drift")
        if (output / "source_pool.json").read_bytes() != delta_bytes:
            raise ValueError("P97 gate source pool byte replay drift")
        if json.loads((output / "manifest.json").read_text()) != result:
            raise ValueError("P97 gate manifest replay drift")
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "source_pool.json").write_bytes(delta_bytes)
        (output / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
