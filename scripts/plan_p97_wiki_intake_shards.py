"""Shard an unbounded Wiki vocabulary into pinned, valid P93 intake configs.

This is a dry source plan: no HTTP, frozen page, native task or train row is
created. Every generated config can be passed unchanged to the P93 intake.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p93_wiki_structural_intake import (
    NAME,
    POOL_SCHEMA,
    pinned_json,
)
from longworld.synthesis.p93_wiki_structural_intake import (
    SCHEMA as INTAKE_SCHEMA,
)
from longworld.synthesis.p93_wiki_structural_intake import (
    validate as validate_intake,
)

SCHEMA = "longworld.p97-wiki-intake-shards.v1"
MAX_QUERIES = 40
MAX_FAMILIES = 24
MAX_TERMS = 20


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _topic(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")


def _name(base: str, shard: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")[:8]
    digest = hashlib.sha256(f"{base}|{shard}".encode()).hexdigest()[:10]
    name = f"p97_{slug}_{digest}"
    if not NAME.fullmatch(name) or len(name) > 24:
        raise ValueError("generated family name violates P93 contract")
    return name


def _validate_catalog(config: dict) -> None:
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong P97 catalog schema")
    for key, upper in (
        ("max_queries_per_shard", MAX_QUERIES),
        ("max_families_per_shard", MAX_FAMILIES),
        ("max_terms_per_family", MAX_TERMS),
    ):
        if type(config.get(key)) is not int or not 1 <= config[key] <= upper:
            raise ValueError(f"{key} must be within P93 bounds")
    families = config.get("families")
    if not isinstance(families, list) or not families:
        raise ValueError("catalog families must be nonempty")
    names = set()
    terms = {}
    topics = {}
    splits = Counter()
    for family in families:
        if not isinstance(family, dict) or not all(
            isinstance(family.get(key), str) and family[key]
            for key in ("name", "domain", "query_template")
        ):
            raise ValueError("catalog family identity is invalid")
        name = family["name"]
        if not NAME.fullmatch(name) or name in names:
            raise ValueError("catalog family name invalid or repeated")
        names.add(name)
        if family.get("split") not in {"train", "eval"}:
            raise ValueError("catalog family requires explicit train/eval split")
        if family.get("mode") not in {"search", "category"}:
            raise ValueError("catalog family mode invalid")
        if family["query_template"].count("{term}") != 1:
            raise ValueError("catalog query needs one {term}")
        items = family.get("terms")
        if not isinstance(items, list) or not items:
            raise ValueError("catalog family terms must be nonempty")
        for key, maximum in (
            ("max_results_per_term", 50),
            ("bundle_size", 10),
            ("max_bundles_per_term", 5),
        ):
            if type(family.get(key)) is not int or not 1 <= family[key] <= maximum:
                raise ValueError(f"catalog {key} exceeds P93 bound")
        if family["bundle_size"] > family["max_results_per_term"]:
            raise ValueError("catalog bundle_size exceeds result cap")
        for term in items:
            if not isinstance(term, str) or not term.strip():
                raise ValueError("catalog term must be nonempty text")
            folded = term.casefold()
            if folded in terms:
                raise ValueError(f"duplicate discovery term: {term}")
            terms[folded] = family["split"]
            topic = _topic(term)
            if not topic:
                raise ValueError("catalog term has empty P93 topic")
            if topic in topics and topics[topic] != family["split"]:
                raise ValueError(f"topic slug crosses train/eval: {topic}")
            topics[topic] = family["split"]
            splits[family["split"]] += 1
    if not splits["train"] or not splits["eval"]:
        raise ValueError("catalog requires both train and eval terms")


def _records(config: dict) -> list[dict]:
    return [
        {
            "family_index": family_index,
            "term_index": term_index,
            "term": term,
            "split": family["split"],
        }
        for family_index, family in enumerate(config["families"])
        for term_index, term in enumerate(family["terms"])
    ]


def _allocate(config: dict, records: list[dict], count: int) -> list[list[dict]] | None:
    buckets: list[list[dict]] = [[] for _ in range(count)]
    used: set[tuple[int, int]] = set()
    for split in ("eval", "train"):
        selected = [row for row in records if row["split"] == split][:count]
        if len(selected) != count:
            return None
        for shard, row in enumerate(selected):
            buckets[shard].append(row)
            used.add((row["family_index"], row["term_index"]))
    for row in records:
        key = (row["family_index"], row["term_index"])
        if key in used:
            continue
        choices = []
        for shard, bucket in enumerate(buckets):
            family_count = sum(
                item["family_index"] == row["family_index"] for item in bucket
            )
            family_names = {item["family_index"] for item in bucket}
            if (
                len(bucket) < config["max_queries_per_shard"]
                and family_count < config["max_terms_per_family"]
                and (
                    row["family_index"] in family_names
                    or len(family_names) < config["max_families_per_shard"]
                )
            ):
                choices.append(
                    (len(bucket), 0 if family_count else 1, family_count, shard)
                )
        if not choices:
            return None
        shard = min(choices)[-1]
        buckets[shard].append(row)
    return buckets


def _allocate_exact(
    config: dict, records: list[dict], count: int
) -> list[list[dict]] | None:
    """Search only when the fast allocator rejects a potentially legal split."""
    family_sizes = Counter(row["family_index"] for row in records)
    split_sizes = Counter(row["split"] for row in records)
    ordered = sorted(
        records,
        key=lambda row: (
            family_sizes[row["family_index"]],
            split_sizes[row["split"]],
            row["family_index"],
            row["term_index"],
        ),
    )
    remaining = {split: [0] * (len(ordered) + 1) for split in ("train", "eval")}
    for index in range(len(ordered) - 1, -1, -1):
        for split, suffix in remaining.items():
            suffix[index] = suffix[index + 1] + (ordered[index]["split"] == split)
    buckets: list[list[dict]] = [[] for _ in range(count)]
    family_counts = [Counter() for _ in range(count)]
    split_counts = [Counter() for _ in range(count)]
    seen: set[tuple] = set()

    def search(index: int) -> bool:
        if index == len(ordered):
            return all(parts["train"] and parts["eval"] for parts in split_counts)
        if any(
            sum(not parts[split] for parts in split_counts) > remaining[split][index]
            for split in remaining
        ):
            return False
        state = (
            index,
            tuple(sorted(tuple(sorted(counts.items())) for counts in family_counts)),
        )
        if state in seen:
            return False
        seen.add(state)
        if len(seen) > 1_000_000:
            raise ValueError("P97 exact allocation search budget exhausted")
        row = ordered[index]
        family, split = row["family_index"], row["split"]
        symmetric: set[tuple] = set()
        for shard, bucket in enumerate(buckets):
            counts = family_counts[shard]
            signature = tuple(sorted(counts.items()))
            if signature in symmetric:
                continue
            symmetric.add(signature)
            if (
                len(bucket) >= config["max_queries_per_shard"]
                or counts[family] >= config["max_terms_per_family"]
                or (
                    family not in counts
                    and len(counts) >= config["max_families_per_shard"]
                )
            ):
                continue
            bucket.append(row)
            counts[family] += 1
            split_counts[shard][split] += 1
            if search(index + 1):
                return True
            split_counts[shard][split] -= 1
            counts[family] -= 1
            if not counts[family]:
                del counts[family]
            bucket.pop()
        return False

    old_limit = sys.getrecursionlimit()
    if old_limit <= len(ordered) + 100:
        sys.setrecursionlimit(len(ordered) + 200)
    try:
        return buckets if search(0) else None
    finally:
        if sys.getrecursionlimit() != old_limit:
            sys.setrecursionlimit(old_limit)


def _shards(config: dict, records: list[dict]) -> list[list[dict]]:
    families = config["families"]
    minimum = max(
        math.ceil(len(records) / config["max_queries_per_shard"]),
        math.ceil(len(families) / config["max_families_per_shard"]),
        max(
            math.ceil(len(family["terms"]) / config["max_terms_per_family"])
            for family in families
        ),
    )
    counts = Counter(row["split"] for row in records)
    maximum = min(counts["train"], counts["eval"])
    for count in range(minimum, maximum + 1):
        buckets = _allocate(config, records, count)
        if buckets is not None:
            return buckets
    for count in range(minimum, maximum + 1):
        buckets = _allocate_exact(config, records, count)
        if buckets is not None:
            return buckets
    raise ValueError("vocabulary cannot fit P93 bounds with train/eval in every shard")


def plan(config_path: Path) -> tuple[dict[str, str], dict]:
    config = json.loads(config_path.read_text())
    _validate_catalog(config)
    base = pinned_json(ROOT, config["base_pool"])
    if base.get("schema") != POOL_SCHEMA:
        raise ValueError("base pool has wrong source schema")
    records = _records(config)
    buckets = _shards(config, records)
    outputs: dict[str, str] = {}
    coverage = []
    source_names = []
    shard_rows = []
    all_names = set()
    for number, bucket in enumerate(buckets):
        grouped: dict[int, list[dict]] = {}
        for row in bucket:
            grouped.setdefault(row["family_index"], []).append(row)
        families = []
        eval_names = []
        split_counts = Counter()
        domain_counts = Counter()
        topics = set()
        for index, items in sorted(grouped.items()):
            original = config["families"][index]
            name = _name(original["name"], number)
            if name in all_names:
                raise ValueError("generated family name collides across shards")
            all_names.add(name)
            ordered = sorted(items, key=lambda item: item["term_index"])
            family = {
                key: original[key]
                for key in (
                    "domain",
                    "mode",
                    "query_template",
                    "max_results_per_term",
                    "bundle_size",
                    "max_bundles_per_term",
                )
            }
            family.update(name=name, terms=[row["term"] for row in ordered])
            families.append(family)
            split = original["split"]
            if split == "eval":
                eval_names.append(name)
            for position, row in enumerate(ordered, 1):
                term = row["term"]
                topic = _topic(term)
                query = family["query_template"].replace("{term}", term)
                coverage.append(
                    {
                        "shard": number,
                        "original_family": original["name"],
                        "generated_family": name,
                        "domain": original["domain"],
                        "split": split,
                        "term": term,
                        "topic": topic,
                        "query": query,
                        "discovery_key": f"{name}_{position:02d}",
                    }
                )
                for bundle in range(1, family["max_bundles_per_term"] + 1):
                    source_names.append(
                        {
                            "shard": number,
                            "source_name": f"p93_{name}_{position:02d}_{bundle:02d}",
                            "split": split,
                            "topic": topic,
                        }
                    )
                split_counts[split] += 1
                domain_counts[original["domain"]] += 1
                topics.add(topic)
        shard_config = {
            "schema": INTAKE_SCHEMA,
            "base_pool": config["base_pool"],
            "eval_families": eval_names,
            "families": families,
        }
        validate_intake(shard_config)
        if not split_counts["train"] or not split_counts["eval"]:
            raise ValueError("generated shard lacks train or eval queries")
        name = f"shards/shard_{number:04d}.json"
        outputs[name] = (
            json.dumps(shard_config, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        )
        shard_rows.append(
            {
                "shard": number,
                "config_path": name,
                "config_sha256": hashlib.sha256(outputs[name].encode()).hexdigest(),
                "queries": len(bucket),
                "families": len(families),
                "split_queries": dict(sorted(split_counts.items())),
                "domain_queries": dict(sorted(domain_counts.items())),
                "topics": len(topics),
            }
        )
    if len(coverage) != len(records) or len(
        {row["source_name"] for row in source_names}
    ) != len(source_names):
        raise ValueError("planned query or potential source names repeat")
    outputs["coverage.jsonl"] = "".join(_dump(row) + "\n" for row in coverage)
    outputs["potential_source_names.jsonl"] = "".join(
        _dump(row) + "\n" for row in source_names
    )
    split_counts = Counter(row["split"] for row in coverage)
    domain_counts = Counter(row["domain"] for row in coverage)
    manifest = {
        "schema": SCHEMA + ".result",
        "catalog_sha256": _sha(config_path),
        "planner_sha256": _sha(Path(__file__)),
        "base_pool": config["base_pool"],
        "shards": shard_rows,
        "source_families": len(config["families"]),
        "generated_families": len(all_names),
        "queries": len(coverage),
        "split_queries": dict(sorted(split_counts.items())),
        "domain_queries": dict(sorted(domain_counts.items())),
        "topics": len({row["topic"] for row in coverage}),
        "potential_source_names": len(source_names),
        "potential_source_name_sha256": hashlib.sha256(
            outputs["potential_source_names.jsonl"].encode()
        ).hexdigest(),
        "coverage_sha256": hashlib.sha256(
            outputs["coverage.jsonl"].encode()
        ).hexdigest(),
        "http_requests": 0,
        "frozen_pages": 0,
        "native_tasks": 0,
        "requires_global_title_dedup_after_acquisition": True,
        "train_ready": False,
    }
    outputs["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return outputs, manifest


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    outputs, manifest = plan(config_path)
    if verify_only:
        if not output_dir.is_dir():
            raise ValueError("cannot verify missing shard plan")
        actual = {
            str(path.relative_to(output_dir))
            for path in output_dir.rglob("*")
            if path.is_file()
        }
        if actual != set(outputs):
            raise ValueError("P97 shard file inventory drift")
        for name, expected in outputs.items():
            if (output_dir / name).read_text() != expected:
                raise ValueError(f"P97 shard replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P97 shard output directory must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            path = output_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(run(args.catalog, args.output_dir, verify_only=args.verify_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
