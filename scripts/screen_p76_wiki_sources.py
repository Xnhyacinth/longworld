"""Screen frozen Wiki title bundles for usable native evidence units.

This is a cheap source-routing report. A structurally checked value can still
be semantically wrong; the clean-value count excludes obvious template debris
but neither count is a reader-question admission certificate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from longworld.synthesis import wiki_evidence, wiki_world_bridge

_TEMPLATE_DEBRIS = re.compile(
    r"[|{}<>]|https?://|\b(?:cite|coord|convert)\b", re.IGNORECASE
)


def screen(path: Path) -> dict:
    raw = path.read_bytes()
    snapshot = json.loads(raw)
    world = wiki_world_bridge.snapshot_to_world(snapshot)
    structural = Counter()
    clean = Counter()
    clean_documents = Counter()
    rejected = Counter()
    for fact in world.facts:
        if "table_column" not in fact.qualifiers:
            continue
        key = (fact.relation, fact.qualifiers["table_column"])
        check = wiki_evidence.check_locate_fact(world, fact)
        if not check.supported:
            rejected[check.reason] += 1
            continue
        structural[key] += 1
        if not _TEMPLATE_DEBRIS.search(str(fact.value)):
            clean[key] += 1
            doc_ids = {span.doc_id for span in fact.supporting_spans}
            if len(doc_ids) == 1:
                clean_documents[(world._docs[next(iter(doc_ids))].title, *key)] += 1

    def convert(counter: Counter) -> list[dict]:
        return [
            {"relation": relation, "column": column, "count": count}
            for (relation, column), count in sorted(counter.items())
        ]

    return {
        "path": str(path),
        "snapshot_id": snapshot["snapshot_id"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "collection_kind": snapshot.get("source", {}).get("collection_kind"),
        "label": snapshot.get("source", {}).get("collection_label"),
        "documents": len(snapshot["documents"]),
        "facts": len(snapshot["facts"]),
        "table_facts": sum(
            1 for fact in world.facts if "table_column" in fact.qualifiers
        ),
        "structurally_supported": sum(structural.values()),
        "clean_structurally_supported": sum(clean.values()),
        "structural_by_column": convert(structural),
        "clean_by_column": convert(clean),
        "clean_by_document_column": [
            {
                "document": title,
                "relation": relation,
                "column": column,
                "count": count,
            }
            for (title, relation, column), count in sorted(clean_documents.items())
        ],
        "rejected_reasons": dict(sorted(rejected.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    paths = sorted(args.snapshots_dir.glob("*_snapshot.json"))
    if not paths:
        parser.error("no frozen snapshots found")
    result = {
        "schema": "longworld.p76.wiki-source-screen.v1",
        "scope": "source routing only; no tasks or long-dependency admission",
        "groups": [screen(path) for path in paths],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
