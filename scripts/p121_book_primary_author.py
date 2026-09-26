"""Plan single-untagged-author Gutenberg books without anthology graph closure.

This is a catalog-only identity policy. No book download, body equivalence,
subsection overlap or training-readiness claim follows from its plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p113_book_catalog import _work_key
from scripts.p114_book_scale import (
    _catalog,
    _components,
    _encoded,
    _write,
)

PLAN_SCHEMA = "longworld.p114-book-scale-plan.v1"
SOURCE_SCHEMA = "longworld.p113-book-source-freeze.v1"
ATTEMPT_SCHEMA = "longworld.p114-book-mirror-attempts.v1"
ORGANIZATION = re.compile(
    r"\b(?:club|company|society|corporation|association|institute|"
    r"university|college|press|publishing|publishers|foundation|department|"
    r"bureau|house|ltd|inc|co|committee|library|school|church|sons|"
    r"brothers|trust|board|ministry|office|museum|project|government|"
    r"state|nation|federation|league|union|guild)\b",
    re.IGNORECASE,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _config(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    if (
        cfg.get("p121_schema") != "longworld.p121-book-primary-author-request.v2"
        or cfg.get("schema") != "longworld.p113-book-catalog-request.v1"
        or cfg.get("p114_scale_schema") != "longworld.p114-book-scale-request.v1"
        or not 1 <= cfg.get("max_candidates", 0) <= 2000
        or not 1 <= cfg.get("max_attempts", 0) <= 300
        or not 0 < cfg.get("eval_fraction", 0) < 1
        or not 1 <= cfg.get("max_books_per_author_component", 0) <= 4
        or not isinstance(cfg.get("prior_source_manifests"), dict)
        or not cfg["prior_source_manifests"]
        or not isinstance(cfg.get("prior_attempt_manifests"), dict)
        or not cfg["prior_attempt_manifests"]
        or not isinstance(cfg.get("topics"), dict)
        or not cfg["topics"]
    ):
        raise ValueError("invalid bounded P121 primary-author request")
    return cfg


def _pinned_json(path_text: str, expected_sha: str, schema: str) -> dict:
    path = ROOT / path_text
    raw = path.read_bytes()
    if _sha(raw) != expected_sha:
        raise ValueError(f"pinned prior manifest SHA differs: {path_text}")
    value = json.loads(raw)
    if value.get("schema") != schema:
        raise ValueError(f"pinned prior manifest schema differs: {path_text}")
    return value


def _topic_names(row: dict[str, str], topics: dict[str, list[str]]) -> list[str]:
    text = (row["Subjects"] + "; " + row["Bookshelves"]).casefold()
    return [
        name
        for name, terms in topics.items()
        if any(term.casefold() in text for term in terms)
    ]


def _primary_author_rejection(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    author = row["Authors"]
    if len(keys) != 1 or ";" in author or "[" in author:
        return "multiple_or_role_tagged_author"
    if any(mark in author for mark in "()&{}"):
        return "parenthetical_ampersand_or_brace_author"
    parts = [part.strip() for part in author.split(",")]
    if (
        len(parts) < 2
        or not any(char.isalpha() for char in parts[0])
        or not any(char.isalpha() for char in parts[1])
        or any(char.isdigit() for char in parts[1])
    ):
        return "not_surname_given_name_shape"
    if ORGANIZATION.search(author):
        return "organization_term_in_author"
    return None


def _max_component_rows(rows: list[dict[str, str]], included_ids: set[int]) -> int:
    roots, by_id = _components(rows, included_ids)
    sizes = Counter(
        roots[keys[0]] for keys in by_id.values() if keys and keys[0] in roots
    )
    return max(sizes.values(), default=0)


def plan(
    config_path: Path,
    catalog_path: Path,
    output_dir: Path,
    *,
    verify_only: bool = False,
) -> dict:
    cfg = _config(config_path)
    rows = _catalog(catalog_path, cfg["catalog_sha256"])
    english = [row for row in rows if row["Type"] == "Text" and row["Language"] == "en"]
    by_id = {int(row["Text#"]): row for row in english}
    roots, keys_by_id = _components(rows, set(by_id))
    prior_records = []
    for path_text, digest in cfg["prior_source_manifests"].items():
        prior_records.extend(_pinned_json(path_text, digest, SOURCE_SCHEMA)["records"])
    prior_ids = {item["ebook_id"] for item in prior_records}
    if any(not keys_by_id.get(ebook_id) for ebook_id in prior_ids):
        raise ValueError("prior source lacks catalog author identity")
    prior_work_mismatches = []
    prior_works = set()
    for item in prior_records:
        stored = item["catalog_work_key"]
        recomputed = _work_key(by_id[item["ebook_id"]])
        prior_works.update((stored, recomputed))
        if stored != recomputed:
            prior_work_mismatches.append(
                {"ebook_id": item["ebook_id"], "stored": stored, "catalog": recomputed}
            )
    prior_splits: dict[str, set[str]] = defaultdict(set)
    for item in prior_records:
        for key in keys_by_id[item["ebook_id"]]:
            prior_splits[key].add(item["split"])
    prior_conflicts = sorted(
        key for key, splits in prior_splits.items() if len(splits) != 1
    )
    if prior_conflicts:
        raise ValueError(
            f"prior direct author keys cross splits: {prior_conflicts[:8]}"
        )
    prior_keys = set(prior_splits)
    prior_components = {roots[keys_by_id[ebook_id][0]] for ebook_id in prior_ids}
    attempts = set()
    for path_text, digest in cfg["prior_attempt_manifests"].items():
        attempted = _pinned_json(path_text, digest, ATTEMPT_SCHEMA)
        attempts.update(item["ebook_id"] for item in attempted["records"])
    historical = _pinned_json(
        cfg["historical_p120_plan"]["path"],
        cfg["historical_p120_plan"]["sha256"],
        PLAN_SCHEMA,
    )

    old_groups: dict[str, list[dict]] = defaultdict(list)
    new_groups: dict[str, list[dict]] = defaultdict(list)
    rejected = Counter()
    for row in english:
        ebook_id = int(row["Text#"])
        keys = keys_by_id[ebook_id]
        if not keys or not row["Title"].strip():
            rejected["missing_author_or_title"] += 1
            continue
        work = _work_key(row)
        if ebook_id in prior_ids or work in prior_works:
            rejected["prior_source_work_or_id"] += 1
            continue
        if ebook_id in attempts:
            rejected["prior_mirror_attempt"] += 1
            continue
        topics = _topic_names(row, cfg["topics"])
        if not topics:
            rejected["outside_requested_subjects"] += 1
            continue
        item = {
            "ebook_id": ebook_id,
            "title": row["Title"],
            "author": row["Authors"],
            "topics": topics,
            "work_key": work,
        }
        if roots[keys[0]] not in prior_components:
            old_groups[work].append(item)
        reason = _primary_author_rejection(row, keys)
        if reason is not None:
            rejected[reason] += 1
            continue
        if keys[0] in prior_keys:
            rejected["prior_listed_author_key"] += 1
            continue
        topic = min(
            topics,
            key=lambda name: (
                _sha(f"{cfg['split_salt']}:{work}:{name}".encode()),
                name,
            ),
        )
        component = keys[0]
        fraction = (
            int(_sha(f"{cfg['split_salt']}:author:{component}".encode())[:8], 16)
            / 0x100000000
        )
        new_groups[work].append(
            {
                **item,
                "topic": topic,
                "domain": "literature",
                "author_keys": [component],
                "author_component": component,
                "split": "eval" if fraction < cfg["eval_fraction"] else "train",
            }
        )
    ambiguous = {
        work
        for work, variants in new_groups.items()
        if len({item["author_component"] for item in variants}) != 1
    }
    for work in ambiguous:
        rejected["ambiguous_work_author_identity"] += len(new_groups.pop(work))
    new_author_keys = {
        item["author_component"]
        for variants in new_groups.values()
        for item in variants
    }
    prior_author_overlap = sorted(new_author_keys & prior_keys)
    new_split_by_key: dict[str, set[str]] = defaultdict(set)
    for variants in new_groups.values():
        for item in variants:
            new_split_by_key[item["author_component"]].add(item["split"])
    new_split_conflicts = sorted(
        key for key, splits in new_split_by_key.items() if len(splits) != 1
    )
    if prior_author_overlap or new_split_conflicts:
        raise ValueError("primary-author catalog identity or split conflict")
    for variants in new_groups.values():
        variants.sort(key=lambda item: item["ebook_id"])
        variants[0]["catalog_variant_ids"] = [item["ebook_id"] for item in variants]
        rejected["duplicate_catalog_work_variant"] += len(variants) - 1
    old_works, new_works = set(old_groups), set(new_groups)
    unlocked = new_works - old_works
    lost = old_works - new_works
    pools: dict[str, list[dict]] = defaultdict(list)
    for variants in new_groups.values():
        item = variants[0]
        pools[item["topic"]].append(item)
    for pool in pools.values():
        pool.sort(
            key=lambda item: (
                _sha(f"{cfg['split_salt']}:rank:{item['work_key']}".encode()),
                item["ebook_id"],
            )
        )
    candidates = []
    per_author = Counter()
    while len(candidates) < cfg["max_candidates"] and any(pools.values()):
        progressed = False
        for topic in sorted(pools):
            pool = pools[topic]
            while (
                pool
                and per_author[pool[0]["author_component"]]
                >= cfg["max_books_per_author_component"]
            ):
                pool.pop(0)
                rejected["planned_author_cap"] += 1
            if pool and len(candidates) < cfg["max_candidates"]:
                item = pool.pop(0)
                candidates.append(item)
                per_author[item["author_component"]] += 1
                progressed = True
        if not progressed:
            break
    if len(candidates) < cfg["max_attempts"]:
        raise ValueError("too few new primary-author candidates for attempt budget")
    if {item["author_component"] for item in candidates} & prior_keys:
        raise ValueError("planned author overlaps prior source keys")
    split_by_key: dict[str, set[str]] = defaultdict(set)
    for item in candidates:
        split_by_key[item["author_component"]].add(item["split"])
    if any(len(splits) != 1 for splits in split_by_key.values()):
        raise ValueError("planned author key crosses train/eval")

    prior_union = {
        "schema": SOURCE_SCHEMA,
        "records": prior_records,
        "origin": "p121_frozen_prior_union",
        "source_manifest_sha256": cfg["prior_source_manifests"],
    }
    prior_path = ROOT / cfg["prior_source_manifest"]
    if prior_path.parent.resolve() != output_dir.resolve():
        raise ValueError("P121 prior union must be written beside its plan")
    _write(prior_path, _encoded(prior_union), verify_only=verify_only)
    receipt = {
        "schema": PLAN_SCHEMA,
        "p121_schema": "longworld.p121-book-primary-author-plan.v2",
        "config_sha256": _sha(config_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "prior_source_manifest_sha256": _sha(prior_path.read_bytes()),
        "eligible_unique_works": len(new_works),
        "planned_candidates": len(candidates),
        "planned_by_topic": dict(
            sorted(Counter(item["topic"] for item in candidates).items())
        ),
        "planned_by_split": dict(
            sorted(Counter(item["split"] for item in candidates).items())
        ),
        "excluded": dict(sorted(rejected.items())),
        "candidates": candidates,
        "train_ready": False,
    }
    _write(output_dir / "plan.json", _encoded(receipt), verify_only=verify_only)

    old_component_rows = Counter(roots[keys[0]] for keys in keys_by_id.values() if keys)
    giant_key, giant_size = old_component_rows.most_common(1)[0]
    giant_roles = Counter()
    for row in english:
        keys = keys_by_id[int(row["Text#"])]
        if not keys or roots[keys[0]] != giant_key or len(keys) < 2:
            continue
        for role in ("Illustrator", "Translator", "Editor", "Contributor", "Compiler"):
            if f"[{role}]".casefold() in row["Authors"].casefold():
                giant_roles[role] += 1
    examples = []
    for ebook_id in cfg["adversarial_example_ids"]:
        if ebook_id not in by_id or not keys_by_id[ebook_id]:
            raise ValueError(f"adversarial catalog example lacks author: {ebook_id}")
        row = by_id[ebook_id]
        work = _work_key(row)
        examples.append(
            {
                "ebook_id": ebook_id,
                "title": row["Title"],
                "catalog_author": row["Authors"],
                "normalized_author_key": list(keys_by_id[ebook_id]),
                "eligible_under_old_graph": work in old_works,
                "eligible_under_new_rule": work in new_works,
                "old_graph_component": roots[keys_by_id[ebook_id][0]],
                "new_rule_rejection": _primary_author_rejection(
                    row, keys_by_id[ebook_id]
                ),
            }
        )
    report = {
        "schema": "longworld.p121-book-primary-author-audit.v2",
        "config_sha256": _sha(config_path.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "plan_sha256": _sha((output_dir / "plan.json").read_bytes()),
        "historical_p120_old_graph_eligible_works": historical["eligible_unique_works"],
        "fair_old_graph_eligible_works": len(old_works),
        "strict_primary_author_eligible_works": len(new_works),
        "old_and_new": len(old_works & new_works),
        "newly_unlocked": len(unlocked),
        "old_lost_by_strict_rule": len(lost),
        "newly_unlocked_in_old_giant": sum(
            roots[keys_by_id[new_groups[work][0]["ebook_id"]][0]] == giant_key
            for work in unlocked
        ),
        "unique_new_author_keys": len(new_author_keys),
        "prior_listed_author_keys": len(prior_keys),
        "prior_stored_catalog_work_mismatches": prior_work_mismatches,
        "prior_direct_key_cross_split": prior_conflicts,
        "new_prior_author_key_overlap": prior_author_overlap,
        "new_author_key_cross_split": new_split_conflicts,
        "planned_author_key_cross_split": sorted(
            key for key, splits in split_by_key.items() if len(splits) != 1
        ),
        "new_prior_source_id_overlap": sorted(
            {item["ebook_id"] for variants in new_groups.values() for item in variants}
            & prior_ids
        ),
        "new_prior_work_key_overlap": sorted(new_works & prior_works),
        "old_giant_component": {"key": giant_key, "catalog_rows": giant_size},
        "max_component_rows_without_role_edges": _max_component_rows(
            rows, {int(row["Text#"]) for row in english if "[" not in row["Authors"]}
        ),
        "max_component_rows_without_roles_or_many_authors": _max_component_rows(
            rows,
            {
                int(row["Text#"])
                for row in english
                if "[" not in row["Authors"] and row["Authors"].count(";") <= 1
            },
        ),
        "giant_multi_author_role_row_counts": dict(sorted(giant_roles.items())),
        "ambiguous_work_author_keys": len(ambiguous),
        "examples": examples,
        "identity_scope": "One untagged, personal-name-shaped Gutenberg catalog author key per admitted work; stored and recomputed prior work keys plus exact author-key/ID exclusions. Raw/body/subsection equivalence and real-human pseudonym identity require post-download checks.",
        "train_ready": False,
    }
    _write(output_dir / "report.json", _encoded(report), verify_only=verify_only)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    report = plan(args.config, args.catalog, args.output, verify_only=args.verify_only)
    print(json.dumps({k: v for k, v in report.items() if k != "examples"}, indent=2))


if __name__ == "__main__":
    main()
