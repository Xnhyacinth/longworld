"""Audit train/eval source components for a frozen selected candidate bank.

Only books and Wikipedia snapshots have complete source-document mappings in
this audit. Other lanes remain UNKNOWN even if their group strings differ.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from longworld.synthesis.sharded_candidate_bank import verify_index

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p114-source-component-audit.v1"
WIKI_POOLS = (
    ROOT / "configs/p76_source_pool_v2.json",
    ROOT / "configs/p80_wiki_task_scale_v1.json",
    ROOT / "configs/p78_wiki_concat_parks_hospitals_source_pool_v1.json",
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def author_key(author: str) -> str:
    """Normalize catalog spelling and common surname-first author aliases."""
    value = unicodedata.normalize("NFKC", author).casefold().strip()
    if value.count(",") == 1:
        family, given = value.split(",", 1)
        value = given.strip() + " " + family.strip()
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


def page_key(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.netloc.casefold() != "en.wikipedia.org" or not parsed.path.startswith(
        "/wiki/"
    ):
        raise ValueError("snapshot document lacks canonical enwiki article URL")
    title = (
        unicodedata.normalize("NFKC", unquote(parsed.path[6:]))
        .replace("_", " ")
        .strip()
        .casefold()
    )
    if not title:
        raise ValueError("empty Wikipedia article title")
    return "wiki:page:" + title


def revision_key(page: str, url: str) -> str:
    values = parse_qs(urlsplit(url).query).get("oldid", [])
    if len(values) != 1 or not values[0].isdigit():
        raise ValueError("snapshot document lacks revision oldid")
    return page + "@" + values[0]


def components(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Connect source groups sharing any declared underlying identity."""
    parents = {item["group"]: item["group"] for item in groups}
    if len(parents) != len(groups):
        raise ValueError("source group repeats in component graph")

    def root(group: str) -> str:
        while parents[group] != group:
            parents[group] = parents[parents[group]]
            group = parents[group]
        return group

    by_identity: dict[str, set[str]] = defaultdict(set)
    for item in groups:
        for identity in item["identities"]:
            by_identity[identity].add(item["group"])
    for members in by_identity.values():
        first = min(members)
        for member in members:
            parents[root(member)] = root(first)
    joined: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in groups:
        joined[root(item["group"])].append(item)
    conflicts = [
        {
            "groups": sorted(member["group"] for member in members),
            "splits": sorted({member["split"] for member in members}),
            "shared_identities": sorted(
                identity
                for identity, holders in by_identity.items()
                if len(holders & {member["group"] for member in members}) > 1
            ),
        }
        for members in joined.values()
        if len({member["split"] for member in members}) > 1
    ]
    return {
        "source_groups": len(groups),
        "components": len(joined),
        "component_sizes": {
            str(size): count
            for size, count in sorted(
                Counter(len(members) for members in joined.values()).items()
            )
        },
        "underlying_identities": len(by_identity),
        "conflicts": sorted(conflicts, key=lambda item: item["groups"]),
    }


def _book_groups(
    rows: list[dict[str, Any]], source_dir: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = source_dir / "manifest.json"
    manifest = _json(manifest_path)
    if (
        manifest.get("schema") != "longworld.p113-book-source-freeze.v1"
        or manifest.get("train_ready") is not False
    ):
        raise ValueError("book source manifest is not frozen candidate material")
    records = {record["source_group"]: record for record in manifest["records"]}
    if len(records) != len(manifest["records"]):
        raise ValueError("book source group repeats")
    splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        splits[row["source_group"]].add(row["split"])
    groups = []
    for group, seen_splits in sorted(splits.items()):
        record = records.get(group)
        if (
            record is None
            or len(seen_splits) != 1
            or record["split"] not in seen_splits
        ):
            raise ValueError(
                "selected book group has missing or crossing source binding"
            )
        body = source_dir / record["body_file"]
        if sha(body) != record["body_sha256"]:
            raise ValueError("book source body changed")
        identities = [
            "book:ebook:" + str(record["ebook_id"]),
            "book:work:" + record["catalog_work_key"].casefold().strip(),
            "book:author:" + author_key(record["author"]),
            "book:body_sha256:" + record["body_sha256"],
        ]
        groups.append(
            {
                "group": group,
                "split": record["split"],
                "identities": identities,
                "ebook_id": record["ebook_id"],
                "title": record["title"],
                "author": record["author"],
                "body_sha256": record["body_sha256"],
            }
        )
    return groups, {
        "source_manifest_sha256": sha(manifest_path),
        "identity_scope": "ebook_id, catalog_work_key, normalized catalog author, exact frozen body SHA-256",
    }


def _wiki_registry(
    selected_groups: dict[str, tuple[str, str, str]],
) -> dict[str, list[dict[str, Any]]]:
    pool_paths = sorted(
        (ROOT / "data/capability_records").rglob("*source_pool.json")
    ) + list(WIKI_POOLS)
    seen_snapshots: dict[Path, tuple[str, dict[str, Any]]] = {}
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pool_path in pool_paths:
        if not pool_path.is_file():
            continue
        pool = _json(pool_path)
        if not isinstance(pool.get("sources"), list):
            continue
        pool_digest = sha(pool_path)
        for source in pool["sources"]:
            if not isinstance(source, dict) or not isinstance(
                source.get("snapshot"), dict
            ):
                continue
            pin = source["snapshot"]
            if not all(field in pin for field in ("path", "sha256")):
                continue
            snapshot_path = ROOT / pin["path"]
            if snapshot_path not in seen_snapshots:
                if not snapshot_path.is_file():
                    continue
                actual = sha(snapshot_path)
                seen_snapshots[snapshot_path] = actual, _json(snapshot_path)
            digest, snapshot = seen_snapshots[snapshot_path]
            if digest != pin["sha256"]:
                if source.get("name") in selected_groups:
                    raise ValueError(
                        "selected Wiki source alias has an invalid snapshot pin"
                    )
                continue
            matched = {
                source.get("name"),
                snapshot.get("snapshot_id"),
            } & selected_groups.keys()
            for group in matched:
                if (
                    source.get("domain"),
                    source.get("topic"),
                    source.get("split"),
                ) != selected_groups[group]:
                    continue
                found[group].append(
                    {
                        "snapshot_path": str(snapshot_path.relative_to(ROOT)),
                        "snapshot_sha256": digest,
                        "pool_path": str(pool_path.relative_to(ROOT)),
                        "pool_sha256": pool_digest,
                    }
                )
    return found


def _wiki_groups(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    splits: dict[str, set[str]] = defaultdict(set)
    metadata: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in rows:
        splits[row["source_group"]].add(row["split"])
        metadata[row["source_group"]].add((row["domain"], row["topic"], row["split"]))
    if any(len(values) != 1 for values in metadata.values()):
        raise ValueError(
            "Wiki source group has inconsistent domain/topic/split binding"
        )
    registry = _wiki_registry(
        {group: next(iter(values)) for group, values in metadata.items()}
    )
    groups = []
    document_occurrences = 0
    documents_without_text = 0
    for group, seen_splits in sorted(splits.items()):
        pins = registry.get(group, [])
        digests = {pin["snapshot_sha256"] for pin in pins}
        if len(seen_splits) != 1 or len(digests) != 1:
            raise ValueError(
                f"selected Wiki group lacks an unambiguous pinned snapshot: {group}"
            )
        pin = min(pins, key=lambda item: (item["pool_path"], item["snapshot_path"]))
        snapshot = _json(ROOT / pin["snapshot_path"])
        if group not in {snapshot["snapshot_id"]} | {
            source.get("name")
            for source in _json(ROOT / pin["pool_path"]).get("sources", [])
            if isinstance(source, dict)
            and isinstance(source.get("snapshot"), dict)
            and source["snapshot"].get("sha256") == pin["snapshot_sha256"]
        }:
            raise ValueError("Wiki source-group alias is not in pinned pool")
        identities = set()
        for document in snapshot["documents"]:
            page = page_key(document["page_url"])
            identities.add(page)
            identities.add(
                "wiki:revision:" + revision_key(page, document["revision_url"])
            )
            doc_id = document.get("doc_id")
            if not isinstance(doc_id, str) or not doc_id:
                raise ValueError("Wiki document lacks page ID")
            identities.add("wiki:doc_id:" + doc_id)
            if isinstance(document.get("text"), str) and document["text"]:
                identities.add(
                    "wiki:text_sha256:"
                    + hashlib.sha256(document["text"].encode()).hexdigest()
                )
            else:
                documents_without_text += 1
            document_occurrences += 1
        groups.append(
            {
                "group": group,
                "split": next(iter(seen_splits)),
                "identities": sorted(identities),
                "snapshot_sha256": pin["snapshot_sha256"],
                "snapshot_path": pin["snapshot_path"],
                "pool_path": pin["pool_path"],
                "pool_sha256": pin["pool_sha256"],
                "documents": len(snapshot["documents"]),
            }
        )
    return groups, {
        "document_occurrences": document_occurrences,
        "documents_without_text": documents_without_text,
        "identity_scope": "all documents in hash-pinned snapshots: canonical enwiki page URL, oldid, doc_id; exact text SHA-256 where nonempty",
    }


def audit(
    index_dir: Path, selection_dir: Path, book_source_dir: Path
) -> dict[str, Any]:
    index = verify_index(index_dir)
    selection_path = selection_dir / "selected_refs.jsonl"
    selection = _json(selection_dir / "manifest.json")
    if (
        selection.get("train_ready") is not False
        or selection.get("input_manifest_sha256") != sha(index_dir / "manifest.json")
        or selection.get("input_refs_sha256") != index["refs_sha256"]
        or selection.get("selected_refs_sha256") != sha(selection_path)
    ):
        raise ValueError("selection does not bind frozen candidate index")
    indexed = {
        entry["candidate"]["sample_id"]: entry
        for entry in _rows(index_dir / "candidate_refs.jsonl")
    }
    selected = _rows(selection_path)
    if (
        len(indexed) != index["candidate_views"]
        or len(selected) != selection["after"]["views"]
    ):
        raise ValueError("candidate or selected row count differs")
    rows = []
    for rank, entry in enumerate(selected):
        row = entry["candidate"]
        if entry.get("selection_rank") != rank or indexed.get(row["sample_id"]) != {
            key: value for key, value in entry.items() if key != "selection_rank"
        }:
            raise ValueError("selected source row differs from index")
        rows.append(row)
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_kind[row["source_kind"]].append(row)
    reports = {}
    for kind, kind_rows in sorted(by_kind.items()):
        if kind == "real_book":
            groups, pins = _book_groups(kind_rows, book_source_dir)
        elif kind == "real_wiki":
            groups, pins = _wiki_groups(kind_rows)
        else:
            reports[kind] = {
                "status": "UNKNOWN",
                "views": len(kind_rows),
                "typed_source_groups": len({r["source_group"] for r in kind_rows}),
                "reason": "complete source-document component mapping is not pinned in this audit",
            }
            continue
        graph = components(groups)
        reports[kind] = {
            "status": "CONFLICT" if graph["conflicts"] else "CERTIFIED",
            "views": len(kind_rows),
            "mapped_views": len(kind_rows),
            "identity_scope": pins["identity_scope"],
            "graph": graph,
            "source_pins": {
                key: value for key, value in pins.items() if key != "identity_scope"
            },
            "group_inventory": groups,
        }
    unknown_views = sum(
        report["views"] for report in reports.values() if report["status"] == "UNKNOWN"
    )
    conflicts = {
        kind: report["graph"]["conflicts"]
        for kind, report in reports.items()
        if report["status"] == "CONFLICT"
    }
    return {
        "schema_version": SCHEMA,
        "index_manifest_sha256": sha(index_dir / "manifest.json"),
        "selected_refs_sha256": sha(selection_path),
        "selection_manifest_sha256": sha(selection_dir / "manifest.json"),
        "book_source_manifest_sha256": sha(book_source_dir / "manifest.json"),
        "selected_views": len(rows),
        "by_source_kind": reports,
        "certified_views": len(rows) - unknown_views,
        "unknown_views": unknown_views,
        "overall_status": "CONFLICT"
        if conflicts
        else "UNKNOWN"
        if unknown_views
        else "CERTIFIED",
        "conflicts": conflicts,
        "scope": "source identity graph only; no gold, semantic-dependency or training-readiness certificate",
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--book-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    report = audit(args.index, args.selection, args.book_source)
    if args.verify_only:
        if _json(args.output) != report:
            raise ValueError("source-component audit does not replay")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    print(
        json.dumps(
            {
                "selected_views": report["selected_views"],
                "certified_views": report["certified_views"],
                "unknown_views": report["unknown_views"],
                "overall_status": report["overall_status"],
            }
        )
    )


if __name__ == "__main__":
    main()
