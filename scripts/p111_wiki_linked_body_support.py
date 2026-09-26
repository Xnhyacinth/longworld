"""Audit grounded list-selector to linked-page body properties, before QA export."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_adapter, wiki_row_binding
from scripts.p111_wiki_linked_freeze import _sha
from scripts.p111_wiki_linked_support import _snapshot
from scripts.p111_wiki_linked_targets import _safe_text, _validate

SCHEMA = "longworld.p111-wiki-linked-body-support.v1"
FIELD = re.compile(r"([a-z][a-z0-9_]{2,32}): (.+)\Z")
BAD_KEY = re.compile(
    r"name|image|photo|map|logo|caption|symbol|flag|width|size|alt|url|website|coordinate|pushpin|native|language|date",
    re.IGNORECASE,
)


def _target_body(record: dict, response_root: Path) -> tuple[str, dict[str, str]]:
    path = response_root / record["response_path"]
    if _sha(path) != record["response_sha256"]:
        raise ValueError("P111 target response drift")
    raw = path.read_bytes()
    verified = _validate(raw, record)
    if verified["target_revid"] != record["target_revid"]:
        raise ValueError("P111 target revision drift")
    page = json.loads(raw)["query"]["pages"][0]
    wikitext = page["revisions"][0]["slots"]["main"]["content"]
    if wikitext.lstrip().upper().startswith("#REDIRECT"):
        return "", {}
    body = wiki_adapter.render_wikitext(record["target_title"], wikitext).text
    props = defaultdict(list)
    for line in body.splitlines()[:120]:
        match = FIELD.fullmatch(line)
        if match is None:
            continue
        key, value = match.groups()
        if BAD_KEY.search(key) or not _safe_text(value):
            continue
        if "cite " in value.casefold() or "&nbsp;" in value:
            continue
        props[key].append(value)
    return body, {key: values[0] for key, values in props.items() if len(values) == 1}


def run(
    target_manifest: Path,
    support_ledger: Path,
    output_path: Path,
    *,
    verify_only: bool = False,
) -> dict:
    target = json.loads(target_manifest.read_text())
    support = json.loads(support_ledger.read_text())
    if target["plan"]["support_ledger_sha256"] != _sha(support_ledger):
        raise ValueError("P111 target/support receipt mismatch")
    by_source = {page["doc_id"]: page for page in support["pages"]}
    records = []
    reasons = Counter()
    for record in target["records"]:
        if record["status"] != "frozen":
            reasons[record["reason"]] += 1
            continue
        source = by_source[record["source_doc_id"]]
        doc = _snapshot(
            {
                **record,
                "doc_id": record["source_doc_id"],
                "title": record["source_title"],
            }
        )
        if doc["title"] != record["source_title"]:
            raise ValueError("P111 list title drift")
        body, props = _target_body(record, target_manifest.parent)
        if not body:
            reasons["target_redirect"] += 1
            continue
        if not props:
            reasons["no_clean_target_field"] += 1
            continue
        candidate = next(
            (
                c
                for c in source["candidates"]
                if c["name"] == record["row_name"]
                and c["target_title"] == record["target_title"]
            ),
            None,
        )
        if candidate is None:
            raise ValueError("P111 target/list link drift")
        rows = wiki_row_binding._rows(doc["text"])
        values = Counter(
            (key, cell.value.casefold()) for row in rows for key, cell in row.columns
        )
        selectors = [
            {"key": key, "value": value}
            for key, value in candidate["visible_columns"].items()
            if key.casefold()
            not in {"name", "station", "site", "castle", "fortress", "institution"}
            and _safe_text(key)
            and _safe_text(value)
            and values[(key, value.casefold())] == 1
            and value.casefold() not in body.casefold()
        ]
        if not selectors:
            reasons["selector_visible_in_target_or_not_unique"] += 1
            continue
        properties = {
            key: value
            for key, value in props.items()
            if value.casefold() not in doc["text"].casefold()
        }
        if not properties:
            reasons["target_values_shortcut_in_list"] += 1
            continue
        records.append(
            {
                "source_group": record["source_group"],
                "source_doc_id": record["source_doc_id"],
                "split": record["split"],
                "domain": record["domain"],
                "topic": record["topic"],
                "source_title": record["source_title"],
                "target_title": record["target_title"],
                "target_revid": record["target_revid"],
                "row_name": record["row_name"],
                "selectors": selectors,
                "properties": properties,
            }
        )
    by_doc = defaultdict(list)
    for row in records:
        by_doc[(row["source_group"], row["source_doc_id"])].append(row)
    pair_support = []
    for (group, doc_id), members in sorted(by_doc.items()):
        for i, first in enumerate(members):
            for second in members[i + 1 :]:
                common = {
                    key: [value, second["properties"][key]]
                    for key, value in first["properties"].items()
                    if key in second["properties"]
                    and value != second["properties"][key]
                }
                if not common:
                    continue
                pair_support.append(
                    {
                        "source_group": group,
                        "source_doc_id": doc_id,
                        "first_target": first["target_title"],
                        "second_target": second["target_title"],
                        "common_distinct_properties": common,
                    }
                )
    result = {
        "schema": SCHEMA,
        "target_manifest_sha256": _sha(target_manifest),
        "support_ledger_sha256": _sha(support_ledger),
        "frozen_targets": target["frozen_targets"],
        "single_target_semantic_support": len(records),
        "two_target_common_field_pairs": len(pair_support),
        "pair_worlds": len({row["source_group"] for row in pair_support}),
        "rejections": dict(sorted(reasons.items())),
        "records": records,
        "pair_support": pair_support,
        "reader_tasks": 0,
        "train_ready": False,
    }
    encoded = (
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    if verify_only:
        if output_path.read_bytes() != encoded:
            raise ValueError("P111 body support replay differs")
    else:
        if output_path.exists() and output_path.read_bytes() != encoded:
            raise ValueError("P111 body support exists with different bytes")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(encoded)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--support-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(
        args.target_manifest,
        args.support_ledger,
        args.output,
        verify_only=args.verify_only,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "frozen_targets",
                    "single_target_semantic_support",
                    "two_target_common_field_pairs",
                    "pair_worlds",
                    "rejections",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
