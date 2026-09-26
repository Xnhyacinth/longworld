"""Audit train/eval source components for a frozen selected candidate bank.

Books and Wikipedia snapshots are mapped by default. Extended mode also
replays pinned paper, RFC and finance provenance. Unmapped lanes remain UNKNOWN
even when their group strings differ.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from longworld.synthesis.sharded_candidate_bank import verify_index
from longworld.synthesis.unified_candidate_merge import verify_merge

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p114-source-component-audit.v1"
P118_SCHEMA = "longworld.p118-source-component-audit.v1"
WIKI_POOLS = (
    ROOT / "configs/p76_source_pool_v2.json",
    ROOT / "configs/p80_wiki_task_scale_v1.json",
    ROOT / "configs/p78_wiki_concat_parks_hospitals_source_pool_v1.json",
)
BOOK_CATALOG = ROOT / "data/sources/p113_book_catalog_v1/pg_catalog.csv.gz"


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


def _catalog_author_keys(author: str) -> set[str]:
    keys = set()
    for fragment in author.split(";"):
        parts = fragment.split(",")
        family = " ".join(re.findall(r"[a-z0-9]+", parts[0].casefold()))
        given = (
            " ".join(re.findall(r"[a-z0-9]+", parts[1].casefold()))
            if len(parts) > 1
            else ""
        )
        if family:
            keys.add(family + "|" + given)
    return keys


def _book_catalog_authors(expected_sha: str) -> dict[int, set[str]]:
    if sha(BOOK_CATALOG) != expected_sha:
        raise ValueError("book catalog SHA differs from source manifest pin")
    with gzip.open(BOOK_CATALOG, "rt", encoding="utf-8-sig", newline="") as stream:
        return {
            int(row["Text#"]): _catalog_author_keys(row["Authors"])
            for row in csv.DictReader(stream)
            if row["Type"] == "Text" and row["Language"] == "en"
        }


def _check_book_source_components(records: list[dict[str, Any]]) -> None:
    """Reject reused frozen sources and train/eval work or author overlap."""
    by_identity: dict[str, set[str]] = defaultdict(set)
    for record in records:
        for key in (
            "source_group",
            "ebook_id",
            "catalog_work_key",
            "raw_sha256",
            "body_sha256",
        ):
            identity = f"{key}:{record[key]}"
            if (
                key == "catalog_work_key"
                and by_identity[identity]
                and record["split"] not in by_identity[identity]
            ):
                raise ValueError(
                    f"book work or author component crosses train/eval: {identity}"
                )
            if by_identity[identity]:
                raise ValueError(
                    f"duplicate book source identity across manifests: {identity}"
                )
            by_identity[identity].add(record["split"])
        for author in record["catalog_author_keys"]:
            by_identity["catalog_author:" + author].add(record["split"])
    for identity, splits in by_identity.items():
        if len(splits) > 1 and identity.startswith(
            ("catalog_work_key:", "catalog_author:")
        ):
            raise ValueError(
                f"book work or author component crosses train/eval: {identity}"
            )


def _book_groups(
    rows: list[dict[str, Any]], source_dirs: list[Path]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not source_dirs or len(set(source_dirs)) != len(source_dirs):
        raise ValueError("book source directories must be nonempty and distinct")
    records: dict[str, tuple[dict[str, Any], Path]] = {}
    manifests = []
    all_records = []
    for source_dir in source_dirs:
        manifest_path = source_dir / "manifest.json"
        manifest = _json(manifest_path)
        if (
            manifest.get("schema") != "longworld.p113-book-source-freeze.v1"
            or manifest.get("train_ready") is not False
        ):
            raise ValueError("book source manifest is not frozen candidate material")
        manifests.append({"path": str(source_dir), "sha256": sha(manifest_path)})
        for record in manifest["records"]:
            group = record["source_group"]
            if group in records:
                raise ValueError(
                    "duplicate book source identity across manifests: source_group:"
                    + group
                )
            records[group] = record, source_dir
            all_records.append(record)
    if len(source_dirs) > 1:
        catalog_pins = {
            _json(source_dir / "manifest.json").get("catalog_sha256")
            for source_dir in source_dirs
        }
        if len(catalog_pins) != 1 or not next(iter(catalog_pins)):
            raise ValueError("book manifests lack a shared pinned author catalog")
        catalog_authors = _book_catalog_authors(next(iter(catalog_pins)))
        for record in all_records:
            author_keys = catalog_authors.get(record["ebook_id"])
            if not author_keys or (
                "author_keys" in record and set(record["author_keys"]) != author_keys
            ):
                raise ValueError("book source lacks pinned catalog author identity")
            record["catalog_author_keys"] = sorted(author_keys)
        _check_book_source_components(all_records)
    splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        splits[row["source_group"]].add(row["split"])
    groups = []
    for group, seen_splits in sorted(splits.items()):
        binding = records.get(group)
        if (
            binding is None
            or len(seen_splits) != 1
            or binding[0]["split"] not in seen_splits
        ):
            raise ValueError(
                "selected book group has missing or crossing source binding"
            )
        record, source_dir = binding
        body = source_dir / record["body_file"]
        raw = source_dir / record["raw_file"]
        if sha(body) != record["body_sha256"] or sha(raw) != record["raw_sha256"]:
            raise ValueError("book source body changed")
        identities = [
            "book:ebook:" + str(record["ebook_id"]),
            "book:work:" + record["catalog_work_key"].casefold().strip(),
            "book:author:" + author_key(record["author"]),
            "book:body_sha256:" + record["body_sha256"],
        ]
        if len(source_dirs) > 1:
            identities.extend(
                "book:catalog_author:" + key for key in record["catalog_author_keys"]
            )
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
    if len(source_dirs) == 1:
        return groups, {
            "source_manifest_sha256": manifests[0]["sha256"],
            "identity_scope": "ebook_id, catalog_work_key, normalized catalog author, exact frozen body SHA-256",
        }
    return groups, {
        "source_manifests": manifests,
        "catalog_sha256": next(iter(catalog_pins)),
        "identity_scope": "ebook_id, catalog work and author keys, normalized printed author, exact frozen body SHA-256 across all pinned book cohorts",
    }


def _native_dir(row: dict[str, Any]) -> Path:
    ref = row.get("native_row_ref", "")
    if ".jsonl:" not in ref:
        raise ValueError("selected row lacks native JSONL provenance")
    path = Path(ref.rsplit(".jsonl:", 1)[0] + ".jsonl")
    return path.parent if path.is_absolute() else ROOT / path.parent


def _source_support_rows(
    index_dir: Path, index: dict[str, Any], selected: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve joint-answer views to both pinned native parent tasks."""
    shards = {item["name"]: item for item in index["shards"]}
    joint_shards = set()
    for name in {entry["shard"] for entry in selected}:
        shard = shards[name]
        manifest_path = (index_dir / shard["path"]).resolve() / "manifest.json"
        if sha(manifest_path) != shard["manifest_sha256"]:
            raise ValueError("selected shard manifest differs")
        schema = _json(manifest_path).get("p125_schema")
        if schema is not None:
            if schema != "longworld.p125-joint-task-compiler.v1":
                raise ValueError("unknown joint-answer shard schema")
            joint_shards.add(name)
    joint_cache: dict[Path, tuple[dict[str, Any], dict[str, dict], dict[str, dict]]] = {}
    materialized_cache: dict[Path, tuple[str, dict[str, dict]]] = {}
    support_rows = []
    derived_pins = {}
    for entry in selected:
        row = entry["candidate"]
        is_joint_name = row.get("source_name") == "p125_joint_multi_operation"
        is_joint_operation = row["operation"] == "joint_multi_operation_answer"
        is_joint_shard = entry["shard"] in joint_shards
        if is_joint_name != is_joint_operation or is_joint_name != is_joint_shard:
            raise ValueError("joint-answer shard, source and operation tags disagree")
        if not is_joint_shard:
            support_rows.append(row)
            continue
        shard = shards[entry["shard"]]
        shard_dir = (index_dir / shard["path"]).resolve()
        if shard_dir not in joint_cache:
            if sha(shard_dir / "manifest.json") != shard["manifest_sha256"]:
                raise ValueError("joint-answer shard manifest differs")
            verify_merge(shard_dir)
            manifest = _json(shard_dir / "manifest.json")
            if manifest.get("p125_schema") != "longworld.p125-joint-task-compiler.v1":
                raise ValueError("unknown joint-answer lineage schema")
            if not {
                "sample_index.jsonl",
                "pair_lineage.jsonl",
                "candidate_train.jsonl",
                "candidate_eval.jsonl",
            } <= manifest.get("files_sha256", {}).keys():
                raise ValueError("joint-answer shard lacks pinned lineage or readers")
            joint_rows = _rows(shard_dir / "sample_index.jsonl")
            lineages = _rows(shard_dir / "pair_lineage.jsonl")
            by_id = {item["sample_id"]: item for item in joint_rows}
            by_lineage = {item["sample_id"]: item for item in lineages}
            if (
                len(by_id) != len(joint_rows)
                or len(by_lineage) != len(lineages)
                or set(by_id) != set(by_lineage)
            ):
                raise ValueError("joint-answer lineage inventory differs")
            joint_cache[shard_dir] = manifest, by_id, by_lineage
            derived_pins[str(shard_dir / "manifest.json")] = sha(
                shard_dir / "manifest.json"
            )
        manifest, by_id, by_lineage = joint_cache[shard_dir]
        lineage = by_lineage.get(row["sample_id"])
        if by_id.get(row["sample_id"]) != row or lineage is None:
            raise ValueError("selected joint-answer row lacks pinned lineage")
        ref = row["native_row_ref"].split("/sample_index.jsonl:", 1)
        parent_ids = lineage["component_sample_ids"]
        if (
            len(ref) != 2
            or len(parent_ids) != 2
            or len(set(parent_ids)) != 2
            or ref[1] != "+".join(parent_ids)
        ):
            raise ValueError("joint-answer materialized source path differs")
        materialized_dir = Path(ref[0])
        if not materialized_dir.is_absolute():
            materialized_dir = ROOT / materialized_dir
        materialized_dir = materialized_dir.resolve()
        if materialized_dir not in materialized_cache:
            materialized_path = materialized_dir / "manifest.json"
            materialized_sha = sha(materialized_path)
            if materialized_sha != manifest["source_materialized_manifest_sha256"]:
                raise ValueError("joint-answer source materialization differs")
            source = _json(materialized_path)
            if source.get("schema_version") != "longworld.p95-balanced-materialized-candidates.v1":
                raise ValueError("joint-answer source materialization schema differs")
            if not {"sample_index.jsonl", "train.jsonl", "eval.jsonl"} <= source.get(
                "files_sha256", {}
            ).keys():
                raise ValueError("joint-answer parent index or readers are unpinned")
            for name, digest in source["files_sha256"].items():
                if sha(materialized_dir / name) != digest:
                    raise ValueError("joint-answer source materialization file differs")
            parents = _rows(materialized_dir / "sample_index.jsonl")
            parent_by_id = {item["candidate"]["sample_id"]: item["candidate"] for item in parents}
            if len(parent_by_id) != len(parents) or len(parents) != source["selected_views"]:
                raise ValueError("joint-answer parent inventory differs")
            materialized_cache[materialized_dir] = materialized_sha, parent_by_id
            derived_pins[str(materialized_path)] = materialized_sha
        materialized_sha, parent_by_id = materialized_cache[materialized_dir]
        if row["receipt_sha256"] != materialized_sha or lineage["context_sha256"] != row["context_sha256"]:
            raise ValueError("joint-answer source or context pin differs")
        parents = [parent_by_id.get(sample_id) for sample_id in parent_ids]
        if len(parents) != 2 or any(parent is None for parent in parents):
            raise ValueError("joint-answer parent tasks missing")
        for position, parent in enumerate(parents):
            if (
                any(parent[field] != row[field] for field in ("source_kind", "source_group", "split", "domain", "topic", "context_sha256"))
                or parent["semantic_task_id"] != lineage["component_semantic_task_ids"][position]
                or parent["operation"] != lineage["component_operations"][position]
                or parent["answer_sha256"] != lineage["answer_projection_hashes"][position]
                or not parent.get("dependency_status")
                or parent["dependency_status"]
                != lineage["component_dependency_status"][position]
            ):
                raise ValueError("joint-answer parent source binding differs")
            support_rows.append(parent)
    return support_rows, {
        "selected_joint_views": sum(
            entry["candidate"]["operation"] == "joint_multi_operation_answer"
            for entry in selected
        ),
        "source_support_rows": len(support_rows),
        "source_pins": derived_pins,
    }


def _paper_groups(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve each selected paper task through its pinned native audit."""
    by_native: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_native[_native_dir(row)].append(row)
    groups: dict[str, dict[str, Any]] = {}
    native_pins = []
    archive_pins: dict[str, str] = {}
    for native_dir, selected in sorted(by_native.items()):
        manifest_path = native_dir / "manifest.json"
        manifest = _json(manifest_path)
        file_pins = manifest.get("files_sha256", {})
        if not all(name in file_pins for name in ("audit.jsonl", "sample_index.jsonl")):
            raise ValueError("paper native manifest lacks complete audit/index pins")
        for name in ("audit.jsonl", "sample_index.jsonl"):
            if sha(native_dir / name) != file_pins[name]:
                raise ValueError("paper native audit or index SHA differs")
        audits = {item["sample_id"]: item for item in _rows(native_dir / "audit.jsonl")}
        indexes = {
            item["sample_id"]: item for item in _rows(native_dir / "sample_index.jsonl")
        }
        if len(audits) != len(_rows(native_dir / "audit.jsonl")) or len(indexes) != len(
            _rows(native_dir / "sample_index.jsonl")
        ):
            raise ValueError("paper native sample ID repeats")
        native_pins.append(
            {
                "path": str(native_dir.relative_to(ROOT)),
                "manifest_sha256": sha(manifest_path),
                "audit_sha256": file_pins["audit.jsonl"],
                "index_sha256": file_pins["sample_index.jsonl"],
            }
        )
        for row in selected:
            sample_id = row["sample_id"]
            native = indexes.get(sample_id)
            audit_row = audits.get(sample_id)
            if (
                native is None
                or audit_row is None
                or native.get("source_group") != row["source_group"]
                or native.get("split") != row["split"]
            ):
                raise ValueError(
                    "selected paper row lacks matching pinned native audit"
                )
            archives = audit_row.get("source_archives") or [
                audit_row.get("source_archive")
            ]
            if not archives or any(not isinstance(item, dict) for item in archives):
                raise ValueError("paper audit lacks complete source archive list")
            identities = set()
            for archive in archives:
                path = ROOT / archive["path"]
                digest = archive["sha256"]
                match = re.fullmatch(
                    r"arxiv-([0-9]+\.[0-9]+)v([0-9]+)\.source\.tar", path.name
                )
                if (
                    match is None
                    or sha(path) != digest
                    or row["source_group"] != "researchlab:arxiv:" + match.group(1)
                    or archive.get("version") != "v" + match.group(2)
                ):
                    raise ValueError("paper source archive pin/work/revision differs")
                archive_pins[str(path.relative_to(ROOT))] = digest
                identities.add("paper:work:arxiv:" + match.group(1))
                identities.add("paper:archive_sha256:" + digest)
            group = row["source_group"]
            if group in groups and groups[group]["split"] != row["split"]:
                raise ValueError("paper source group crosses split")
            item = groups.setdefault(
                group, {"group": group, "split": row["split"], "identities": set()}
            )
            item["identities"].update(identities)
    return [
        {**item, "identities": sorted(item["identities"])}
        for item in sorted(groups.values(), key=lambda value: value["group"])
    ], {
        "native_pins": native_pins,
        "archive_pins": archive_pins,
        "identity_scope": "all selected-task source archives from hash-pinned native audit rows: arXiv work and exact revision tar SHA-256",
    }


def _grounded_groups(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve RFC text pins and controlled-state IDs for selected tasks."""
    p112_dir = ROOT / "data/candidates/p112_world_grounded_rules_v1"
    p112_manifest = _json(p112_dir / "manifest.json")
    if (
        sha(p112_dir / "proofs.jsonl") != p112_manifest["proofs_sha256"]
        or sha(ROOT / "data/capability_records/p109_prose_support_v1/ledger.json")
        != p112_manifest["official_rule_support_sha256"]
    ):
        raise ValueError("P112 grounded proof or RFC support pin differs")
    support_ledger = _json(
        ROOT / "data/capability_records/p109_prose_support_v1/ledger.json"
    )
    p112_proofs = {item["sample_id"]: item for item in _rows(p112_dir / "proofs.jsonl")}
    p109_dir = ROOT / "data/candidates/p109_prose_native_v4"
    p109_manifest = _json(p109_dir / "manifest.json")
    for name in ("sample_index.jsonl", "audit.jsonl"):
        if sha(p109_dir / name) != p109_manifest["files_sha256"][name]:
            raise ValueError("P109 native RFC audit pin differs")
    p109_index = {
        item["sample_id"]: item for item in _rows(p109_dir / "sample_index.jsonl")
    }

    p109_audits = {item["sample_id"]: item for item in _rows(p109_dir / "audit.jsonl")}
    native_cache: dict[Path, tuple[dict[str, Any], dict[str, Any]]] = {}
    groups: dict[str, dict[str, Any]] = {}
    source_pins: dict[str, str] = {}
    for row in rows:
        group = row["source_group"]
        split = row["split"]
        sample_id = row["sample_id"]
        identities = set()
        if sample_id in p112_proofs or sample_id in p109_audits:
            proof = p112_proofs.get(sample_id) or p109_audits[sample_id]
            if proof["source_group"] != group or proof["split"] != split:
                raise ValueError("selected numeric RFC proof source/split differs")
            if sample_id in p109_audits and (
                p109_index[sample_id]["source_group"] != group
                or p109_index[sample_id]["split"] != split
            ):
                raise ValueError("P109 native index source/split differs")
            source = proof["source"]
            path = ROOT / source["source_path"]
            if (
                source not in support_ledger["sources"]
                or sha(path) != source["source_sha256"]
                or sha(ROOT / source["inventory_path"]) != source["inventory_sha256"]
            ):
                raise ValueError("numeric RFC source text SHA differs")
            source_pins[str(path.relative_to(ROOT))] = source["source_sha256"]
            identities.update(
                {
                    "rfc:document:" + source["rfc_id"],
                    "rfc:text_sha256:" + source["source_sha256"],
                    "rfc:simulated_state:"
                    + source["source_sha256"]
                    + ":"
                    + str(proof["seed"]),
                }
            )
        else:
            native_dir = _native_dir(row)
            if native_dir not in native_cache:
                manifest = _json(native_dir / "manifest.json")
                config_path = (
                    ROOT / "configs/p87_hybrid_rfc9114_pilot_v6.json"
                    if native_dir
                    == ROOT / "data/candidates/p87_hybrid_rfc9114_pilot_v6"
                    else native_dir.parent / "config.json"
                )
                config = _json(config_path)
                if sha(config_path) != manifest["config_sha256"]:
                    raise ValueError("hybrid RFC config pin differs")
                for field, path_field in (
                    ("inventory_sha256", "inventory"),
                    ("rules_sha256", "rules_source"),
                    ("filler_sha256", "filler_source"),
                ):
                    if (
                        config[field] != manifest["source_pins"][field]
                        or sha(ROOT / config[path_field]) != config[field]
                    ):
                        raise ValueError("hybrid RFC source pin differs")
                if (
                    sha(native_dir / "sample_index.jsonl")
                    != manifest["files_sha256"]["sample_index.jsonl"]
                ):
                    raise ValueError("hybrid RFC native index pin differs")
                index = {
                    item["sample_id"]: item
                    for item in _rows(native_dir / "sample_index.jsonl")
                }
                native_cache[native_dir] = config, index
            config, index = native_cache[native_dir]
            native = index.get(sample_id)
            if (
                native is None
                or native["source_group"] != group
                or native["split"] != split
                or config["source_group"] != group
                or config["split"] != split
            ):
                raise ValueError("selected hybrid RFC task lacks pinned source binding")
            for path_field, sha_field in (
                ("rules_source", "rules_sha256"),
                ("filler_source", "filler_sha256"),
            ):
                path = config[path_field]
                digest = config[sha_field]
                source_pins[path] = digest
                identities.add("rfc:text_sha256:" + digest)
                match = re.fullmatch(r"rfc([0-9]+)\.txt", Path(path).name)
                if match is None:
                    raise ValueError("hybrid RFC source filename lacks document ID")
                identities.add("rfc:document:rfc" + match.group(1))
            identities.add("rfc:simulated_state:" + native["world_id"])
        if group in groups and groups[group]["split"] != split:
            raise ValueError("grounded RFC group crosses train/eval")
        target = groups.setdefault(
            group, {"group": group, "split": split, "identities": set()}
        )
        target["identities"].update(identities)
    return [
        {**item, "identities": sorted(item["identities"])}
        for item in sorted(groups.values(), key=lambda value: value["group"])
    ], {
        "p112_manifest_sha256": sha(p112_dir / "manifest.json"),
        "p109_manifest_sha256": sha(p109_dir / "manifest.json"),
        "hybrid_native_manifests": {
            str(path.relative_to(ROOT)): sha(path / "manifest.json")
            for path in native_cache
        },
        "source_text_pins": source_pins,
        "identity_scope": "hash-pinned RFC rule/filler text and selected simulated-state IDs/seeds",
    }


def _finance_groups(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Trace every selected issuer task to its frozen filing manifest."""
    native_cache: dict[Path, tuple[dict[str, Any], set[str], str]] = {}
    source_cache: dict[Path, tuple[str, dict[str, Any]]] = {}
    groups: dict[str, dict[str, Any]] = {}
    native_pins = {}
    for row in rows:
        native_dir = _native_dir(row)
        if native_dir not in native_cache:
            receipt_path = native_dir / "BUILD_RECEIPT.json"
            if receipt_path.is_file():
                receipt = _json(receipt_path)
                candidate_path = native_dir / "sft_candidates.jsonl"
                if sha(candidate_path) != receipt["files"][candidate_path.name]:
                    raise ValueError("legacy finance candidate receipt differs")
                sample_ids = {item["sample_id"] for item in _rows(candidate_path)}
                group = receipt["split_group_id"]
                split = receipt["split"]
                source_ref = receipt["source_manifest"]
                pin_path = receipt_path
            else:
                manifest_path = native_dir / "manifest.json"
                manifest = _json(manifest_path)
                pins = manifest.get("file_sha256", manifest.get("files_sha256", {}))
                index_path = native_dir / "sample_index.jsonl"
                if sha(index_path) != pins.get("sample_index.jsonl"):
                    raise ValueError("finance native index pin differs")
                index_rows = _rows(index_path)
                sample_ids = {item["sample_id"] for item in index_rows}
                if len(sample_ids) != len(index_rows):
                    raise ValueError("finance native sample ID repeats")
                group = manifest["source_group"]
                split = manifest["split"]
                source_ref = manifest.get("source_manifest")
                if source_ref is None:
                    upstream = (
                        ROOT
                        / "data/candidates/p96_finance_factorial_wide_batch_v1"
                        / native_dir.name
                        / "manifest.json"
                    )
                    if sha(upstream) != manifest["source_manifest_sha256"]:
                        raise ValueError("P112 finance source batch pin differs")
                    source_ref = _json(upstream)["source_manifest"]
                pin_path = manifest_path
            source_path = Path(source_ref["path"])
            if not source_path.is_absolute():
                source_path = ROOT / source_path
            if sha(source_path) != source_ref["sha256"]:
                raise ValueError("finance signed source manifest pin differs")
            native_cache[native_dir] = (
                {"group": group, "split": split, "source_path": source_path},
                sample_ids,
                str(pin_path),
            )
            native_pins[str(pin_path)] = sha(pin_path)
        binding, sample_ids, _ = native_cache[native_dir]
        if (
            row["sample_id"] not in sample_ids
            or row["source_group"] != binding["group"]
            or row["split"] != binding["split"]
        ):
            raise ValueError("selected finance row lacks pinned issuer binding")
        source_path = binding["source_path"]
        if source_path not in source_cache:
            source_cache[source_path] = sha(source_path), _json(source_path)
        source_sha, source = source_cache[source_path]
        group = row["source_group"]
        issuer = source.get("issuer", {})
        if issuer.get("cik") is not None and issuer["cik"] != group:
            raise ValueError("finance source issuer CIK differs")
        identities = {
            "finance:issuer_cik:" + group,
            "finance:source_manifest_sha256:" + source_sha,
        }
        records = source.get("records", [])
        if not records:
            raise ValueError("finance source manifest has no filing records")
        for record in records:
            if record.get("cik") is not None and record["cik"] != group:
                raise ValueError("finance filing record CIK differs")
            if not isinstance(record.get("source_url"), str):
                raise TypeError("finance filing lacks source URL")
            identities.add("finance:filing_url:" + record["source_url"])
            body = record.get("raw_text", record.get("text"))
            if not isinstance(body, str) or not body:
                raise ValueError("finance filing lacks frozen text")
            identities.add(
                "finance:filing_text_sha256:"
                + hashlib.sha256(body.encode()).hexdigest()
            )
        if group in groups and groups[group]["split"] != row["split"]:
            raise ValueError("finance issuer crosses train/eval")
        target = groups.setdefault(
            group, {"group": group, "split": row["split"], "identities": set()}
        )
        target["identities"].update(identities)
    return [
        {**item, "identities": sorted(item["identities"])}
        for item in sorted(groups.values(), key=lambda value: value["group"])
    ], {
        "native_pins": native_pins,
        "source_manifests": {
            str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path): digest
            for path, (digest, _) in source_cache.items()
        },
        "identity_scope": "all selected-task native issuer receipts and full frozen filing manifests: CIK, filing URL and exact embedded filing text hash",
    }


def _code_groups(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bind selected code rows to frozen task metadata and raw-source banks."""
    native_cache: dict[Path, dict[str, dict[str, Any]]] = {}
    native_file_pins: dict[Path, dict[str, str]] = {}
    native_pins: dict[str, str] = {}
    reader_pins: dict[Path, str] = {}
    bank_cache: dict[Path, dict[str, Any]] = {}
    bank_pins: dict[str, str] = {}
    groups: dict[str, dict[str, Any]] = {}
    code_configs = {
        sha(path): (path, _json(path))
        for path in (
            ROOT / "configs/p99_code_content_v1.json",
            ROOT / "configs/p107_code_oxc_expansion_v1.json",
            ROOT / "configs/p108_code_content_v2.json",
        )
    }


    for row in rows:
        native_dir = _native_dir(row)
        if native_dir not in native_cache:
            receipt_path = native_dir / "BUILD_RECEIPT.json"
            if receipt_path.is_file():
                receipt = _json(receipt_path)
                pins = receipt["files"]
                metadata_path = native_dir / "metadata.jsonl"
                if sha(metadata_path) != pins["metadata.jsonl"]:
                    raise ValueError("code native metadata pin differs")
                metadata = _rows(metadata_path)
                native_pins[str(receipt_path)] = sha(receipt_path)
            else:
                receipt_path = native_dir / "manifest.json"
                receipt = _json(receipt_path)
                pins = receipt.get("files_sha256", {})
                metadata_path = native_dir / "sample_index.jsonl"
                if sha(metadata_path) != pins.get("sample_index.jsonl"):
                    raise ValueError("code native index pin differs")
                metadata = _rows(metadata_path)
                native_pins[str(receipt_path)] = sha(receipt_path)
            if len({item["sample_id"] for item in metadata}) != len(metadata):
                raise ValueError("code native sample ID repeats")
            native_cache[native_dir] = {item["sample_id"]: item for item in metadata}
            native_file_pins[native_dir] = pins
        meta = native_cache[native_dir].get(row["sample_id"])
        if meta is None:
            raise ValueError("selected code task lacks pinned native metadata")
        reader_ref = row["native_row_ref"]
        if ".jsonl:" not in reader_ref:
            raise ValueError("selected code task lacks native reader row")
        reader_name, reader_number = reader_ref.rsplit(":", 1)
        reader_path = Path(reader_name)
        if not reader_path.is_absolute():
            reader_path = ROOT / reader_path
        if (reader_path.parent != native_dir or not reader_number.isdigit()
                or reader_path.name not in {"train.jsonl", "eval.jsonl"}):
            raise ValueError("selected code reader row path differs")
        if reader_path not in reader_pins:
            if sha(reader_path) != native_file_pins[native_dir].get(reader_path.name):
                raise ValueError("code native reader pin differs")
            reader_pins[reader_path] = native_file_pins[native_dir][reader_path.name]
        if "row_index" in meta and (meta["row_index"] != int(reader_number)
                                    or meta.get("output_file") != reader_path.name):
            raise ValueError("selected code metadata row position differs")
        group = row["source_group"]
        if (meta.get("source_group_id", meta.get("source_group")) != group
                or meta["split"] != row["split"]):
            raise ValueError("selected code task has mismatched native source or split")
        if row["source_name"] == "p99_code_content":
            config_sha = _json(native_dir / "manifest.json")["config_sha256"]
            binding = code_configs.get(config_sha)
            if binding is None:
                raise ValueError("P99 code bank config differs")
            entry = next((entry for entry in binding[1]["banks"] if entry["source_group_id"] == group), None)
            if entry is None or entry["split"] != row["split"]:
                raise ValueError("P99 code bank source/split differs")
            bank_dir = ROOT / entry["bank_root"]
            expected_receipt_sha = entry["receipt_sha256"]
        else:
            bank_dir = Path(meta["bank_directory"])
            expected_receipt_sha = None
        if bank_dir not in bank_cache:
            bank_receipt_path = bank_dir / "BUILD_RECEIPT.json"
            actual_receipt_sha = sha(bank_receipt_path)
            if expected_receipt_sha and actual_receipt_sha != expected_receipt_sha:
                raise ValueError("code bank receipt differs from config pin")
            bank = _json(bank_receipt_path)
            if sha(bank_dir / "world.json") != bank["files"]["world.json"]:
                raise ValueError("code bank world pin differs")
            world = _json(bank_dir / "world.json")
            if (world["source_group_id"] != bank["source_group_id"]
                    or world["split"] != bank["split"]
                    or not bank.get("source_bindings")):
                raise ValueError("code bank source identity differs")
            for binding in bank["source_bindings"]:
                path = Path(binding["path"])
                if not path.is_file() and str(path).startswith("/workspace/wynckeliao/longworld-worlds/"):
                    path = ROOT / str(path).removeprefix("/workspace/wynckeliao/longworld-worlds/")
                if not path.is_file() or sha(path) != binding["sha256"]:
                    raise ValueError("code raw source binding pin differs")
            bank_cache[bank_dir] = bank
            bank_pins[str(bank_receipt_path)] = actual_receipt_sha
        bank = bank_cache[bank_dir]
        if bank["source_group_id"] != group or bank["split"] != row["split"]:
            raise ValueError("selected code row lacks pinned bank source binding")
        if meta.get("world_instance_id") and meta["world_instance_id"] != _json(bank_dir / "world.json")["world_instance_id"]:
            raise ValueError("code task world instance differs")
        if group in groups and groups[group]["split"] != row["split"]:
            raise ValueError("code repository crosses train/eval")
        target = groups.setdefault(group, {"group": group, "split": row["split"], "identities": set(), "banks": set()})
        target["identities"].add("code:repository:" + group.casefold().rstrip("/"))
        target["banks"].add(str(bank_dir))
    return [
        {**item, "identities": sorted(item["identities"]), "banks": sorted(item["banks"])}
        for item in sorted(groups.values(), key=lambda value: value["group"])
    ], {
        "native_pins": native_pins,
        "reader_pins": {str(path): digest for path, digest in reader_pins.items()},
        "bank_pins": bank_pins,
        "code_config_pins": {str(path): digest for digest, (path, _) in code_configs.items()},
        "identity_scope": "canonical GitHub repository from selected native task metadata and hash-pinned CodeForge bank world/raw source bindings",
    }


def _simulation_groups(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve generated-world identity from pinned native shards, not labels."""
    legacy: dict[Path, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    shard_cache: dict[Path, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    groups: dict[str, dict[str, Any]] = {}
    source_pins: dict[str, str] = {}
    factor_dir = ROOT / "data/candidates/p112_world_factor_campaign_v2"
    factor_manifest = _json(factor_dir / "manifest.json")
    if sha(factor_dir / "sample_index.jsonl") != factor_manifest["files_sha256"]["sample_index.jsonl"]:
        raise ValueError("P112 simulation adapter index pin differs")
    factor_index = {item["sample_id"]: item for item in _rows(factor_dir / "sample_index.jsonl")}
    for row in rows:
        native_dir = _native_dir(row)
        group = row["source_group"]
        split = row["split"]
        if (native_dir / "sample_index.jsonl").is_file():
            if native_dir not in legacy:
                manifest_path = native_dir / "manifest.json"
                manifest = _json(manifest_path)
                index_path = native_dir / "sample_index.jsonl"
                if sha(index_path) != manifest["files"]["sample_index.jsonl"]:
                    raise ValueError("legacy simulation sample index pin differs")
                index_rows = _rows(index_path)
                if len({item["example_id"] for item in index_rows}) != len(index_rows):
                    raise ValueError("legacy simulation sample ID repeats")
                shards = {item["world_id"]: item for item in manifest["shards"]}
                if len(shards) != len(manifest["shards"]):
                    raise ValueError("legacy simulation world repeats in manifest")
                legacy[native_dir] = (
                    {item["example_id"]: item for item in index_rows},
                    shards,
                    manifest,
                )
                source_pins[str(manifest_path)] = sha(manifest_path)
            indexes, shards, _ = legacy[native_dir]
            meta = indexes.get(row["sample_id"])
            if (meta is None or meta["world_id"] != group or meta["split"] != split
                    or meta["output_file"] not in {"train.jsonl", "eval.jsonl"}):
                raise ValueError("legacy simulation selected row lacks world binding")
            shard = shards.get(group)
            if shard is None or shard["split"] != split:
                raise ValueError("legacy simulation shard split differs")
            shard_dir = native_dir / "shards" / shard["shard_id"]
            for name in ("world.json", "rows.jsonl"):
                if sha(shard_dir / name) != shard["files"][name]:
                    raise ValueError("legacy simulation shard pin differs")
            world = _json(shard_dir / "world.json")
            if world["world_id"] != group:
                raise ValueError("legacy simulation world ID differs")
            world_id = world["world_id"]
            context = world["context"]
            source_pins[str(shard_dir / "world.json")] = shard["files"]["world.json"]
        else:
            if native_dir not in shard_cache:
                receipt_path = native_dir / "receipt.json"
                receipt = _json(receipt_path)
                if (sha(native_dir / "world.json") != receipt["world_sha256"]
                        or sha(native_dir / "rows.jsonl") != receipt["rows_sha256"]):
                    raise ValueError("simulation state shard receipt pin differs")
                world = _json(native_dir / "world.json")
                if world["world_id"] != receipt["world_id"]:
                    raise ValueError("simulation state shard world ID differs")
                native_rows = _rows(native_dir / "rows.jsonl")
                if len({item["example_id"] for item in native_rows}) != len(native_rows):
                    raise ValueError("simulation state shard task repeats")
                shard_cache[native_dir] = (
                    receipt,
                    world,
                    {item["example_id"]: item for item in native_rows},
                )
                source_pins[str(receipt_path)] = sha(receipt_path)
            receipt, world, native_rows = shard_cache[native_dir]
            if row["source_name"] == "p112_world_factor_campaign":
                adapter = factor_index.get(row["sample_id"])
                if (adapter is None or adapter["source_group"] != group
                        or adapter["split"] != split
                        or adapter["native_row_ref"] != row["native_row_ref"]):
                    raise ValueError("P112 simulation alias lacks pinned adapter binding")
                native_id = row["semantic_task_id"]
            else:
                native_id = row["sample_id"]
            native = native_rows.get(native_id)
            if (native is None or native["world_id"] != receipt["world_id"]
                    or native["split"] != split):
                raise ValueError("selected simulation task lacks pinned shard row")
            if row["source_name"] != "p112_world_factor_campaign" and native["source_group"] != group:
                raise ValueError("simulation native source group differs")
            world_id = world["world_id"]
            context = world["reader_context"]
            if hashlib.sha256(context.encode()).hexdigest() != world["context_sha256"]:
                raise ValueError("simulation state context hash differs")
        if group in groups and groups[group]["split"] != split:
            raise ValueError("simulation group crosses train/eval")
        target = groups.setdefault(group, {"group": group, "split": split, "identities": set(), "world_ids": set()})
        target["identities"].update({
            "sim:world:" + world_id,
            "sim:reader_context_sha256:" + hashlib.sha256(context.encode()).hexdigest(),
        })
        target["world_ids"].add(world_id)
    return [
        {**item, "identities": sorted(item["identities"]), "world_ids": sorted(item["world_ids"])}
        for item in sorted(groups.values(), key=lambda value: value["group"])
    ], {
        "source_pins": source_pins,
        "p112_adapter_manifest_sha256": sha(factor_dir / "manifest.json"),
        "identity_scope": "hash-pinned native generated world and final reader-context bytes, including P112 source-group alias to underlying world",
    }

def _wiki_registry(
    selected_groups: dict[str, tuple[str, str, str]],
    extra_source_pools: list[Path] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    pool_paths = sorted(
        (ROOT / "data/capability_records").rglob("*source_pool.json")
    ) + list(WIKI_POOLS) + [
        path if path.is_absolute() else ROOT / path
        for path in extra_source_pools or []
    ]
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
    extra_source_pools: list[Path] | None = None,
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
        {group: next(iter(values)) for group, values in metadata.items()},
        extra_source_pools,
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
    index_dir: Path,
    selection_dir: Path,
    book_source_dirs: Path | list[Path],
    *,
    extended_source_kinds: bool = False,
    p118_code_and_simulation: bool = False,
    wiki_source_pools: list[Path] | None = None,
) -> dict[str, Any]:
    if wiki_source_pools and not p118_code_and_simulation:
        raise ValueError("additional Wiki source pools require P118 audit scope")
    source_dirs = (
        [book_source_dirs] if isinstance(book_source_dirs, Path) else book_source_dirs
    )
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
    support_rows, derived = _source_support_rows(index_dir, index, selected)
    selected_by_kind = Counter(row["source_kind"] for row in rows)
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in support_rows:
        by_kind[row["source_kind"]].append(row)
    reports = {}
    for kind, kind_rows in sorted(by_kind.items()):
        if kind == "real_book":
            groups, pins = _book_groups(kind_rows, source_dirs)
        elif kind == "real_wiki":
            groups, pins = _wiki_groups(kind_rows, wiki_source_pools)
        elif extended_source_kinds and kind in {
            "real_paper_source",
            "real_paper_revision",
        }:
            groups, pins = _paper_groups(kind_rows)
        elif extended_source_kinds and kind == "grounded_simulation":
            groups, pins = _grounded_groups(kind_rows)
        elif extended_source_kinds and kind == "real_finance":
            groups, pins = _finance_groups(kind_rows)
        elif p118_code_and_simulation and kind == "real_code_workflow":
            groups, pins = _code_groups(kind_rows)
        elif p118_code_and_simulation and kind == "controlled_simulation":
            groups, pins = _simulation_groups(kind_rows)
        else:
            reports[kind] = {
                "status": "UNKNOWN",
                "views": selected_by_kind[kind],
                "typed_source_groups": len({r["source_group"] for r in kind_rows}),
                "reason": "complete source-document component mapping is not pinned in this audit",
            }
            if extended_source_kinds:
                reports[kind]["native_refs_without_adjacent_manifest"] = sum(
                    not (_native_dir(row) / "manifest.json").is_file()
                    for row in kind_rows
                )
            continue
        graph = components(groups)
        reports[kind] = {
            "status": "CONFLICT" if graph["conflicts"] else "CERTIFIED",
            "views": selected_by_kind[kind],
            "mapped_views": selected_by_kind[kind],
            **(
                {"source_support_rows": len(kind_rows)}
                if derived["selected_joint_views"]
                else {}
            ),
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
    report = {
        "schema_version": P118_SCHEMA if p118_code_and_simulation else SCHEMA,
        "index_manifest_sha256": sha(index_dir / "manifest.json"),
        "selected_refs_sha256": sha(selection_path),
        "selection_manifest_sha256": sha(selection_dir / "manifest.json"),
        "selected_views": len(rows),
        **(
            {"derived_joint_source_binding": derived}
            if derived["selected_joint_views"]
            else {}
        ),
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
    if len(source_dirs) == 1:
        report["book_source_manifest_sha256"] = sha(source_dirs[0] / "manifest.json")
    else:
        report["book_source_manifests"] = [
            {"path": str(path), "sha256": sha(path / "manifest.json")}
            for path in source_dirs
        ]
    if extended_source_kinds:
        paper_groups = [
            item
            for kind in ("real_paper_source", "real_paper_revision")
            for item in reports.get(kind, {}).get("group_inventory", [])
        ]
        if paper_groups:
            merged: dict[str, dict[str, Any]] = {}
            for item in paper_groups:
                group = item["group"]
                if group in merged and merged[group]["split"] != item["split"]:
                    raise ValueError("paper work crosses kind-level train/eval split")
                target = merged.setdefault(
                    group,
                    {"group": group, "split": item["split"], "identities": set()},
                )
                target["identities"].update(item["identities"])
            graph = components(
                [
                    {**item, "identities": sorted(item["identities"])}
                    for item in merged.values()
                ]
            )
            report["cross_kind_paper_graph"] = graph
            if graph["conflicts"]:
                report["overall_status"] = "CONFLICT"
                report["conflicts"]["cross_kind_paper"] = graph["conflicts"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--book-source", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--extended-source-kinds", action="store_true")
    parser.add_argument("--p118-code-and-simulation", action="store_true")
    parser.add_argument("--wiki-source-pool", type=Path, action="append")
    args = parser.parse_args()
    report = audit(
        args.index,
        args.selection,
        args.book_source,
        extended_source_kinds=args.extended_source_kinds,
        p118_code_and_simulation=args.p118_code_and_simulation,
        wiki_source_pools=args.wiki_source_pool,
    )
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
