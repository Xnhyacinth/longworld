"""Compile a P97 Wiki vocabulary from pinned real title/category metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p93_wiki_structural_intake import pinned_json, sha, verify
from scripts.plan_p97_wiki_intake_shards import _topic, _validate_catalog
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p108-wiki-autotopic-catalog.v1"
TITLE = re.compile(r"^List of (.+)$")
CATEGORY = re.compile(r"^Category:Lists of (.+)$")
SAFE = re.compile(r"[A-Za-z][A-Za-z0-9 ,’'\-]*\Z")
BOUNDARY = re.compile(r"\s+(?:in|by|from|at|on|for|of)\s+", re.IGNORECASE)


def _bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _line(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _term(text: str, *, category: bool = False) -> str | None:
    match = (CATEGORY if category else TITLE).fullmatch(text.strip())
    if match is None:
        return None
    phrase = BOUNDARY.split(match.group(1), maxsplit=1)[0]
    phrase = re.sub(r"\s*\([^)]*\)$", "", phrase).strip()
    if (
        not 3 <= len(phrase) <= 60
        or len(phrase.split()) > 6
        or not SAFE.fullmatch(phrase)
    ):
        return None
    return phrase


def _pin_rows(pin: dict[str, str]) -> list[dict]:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P108 metadata pin escapes workspace")
    path = ROOT / relative
    if not path.is_file() or sha(path) != pin["sha256"]:
        raise ValueError(f"P108 metadata pin differs: {relative}")
    value = json.loads(path.read_text())
    if not isinstance(value, list):
        raise TypeError("P108 discovery response is not a list")
    return value


def _prior_titles(pool: dict) -> set[str]:
    titles = set()
    for source in pool["sources"]:
        for doc in _snapshot(ROOT, source["snapshot"])["documents"]:
            titles.add(doc["title"].casefold())
    return titles


def _frozen_evidence(inventory: dict) -> tuple[dict[str, dict], set[str], Counter]:
    candidates: dict[str, dict] = {}
    old_terms: set[str] = set()
    counts: Counter = Counter()

    def add(term: str, domain: str, identity: str, kind: str) -> None:
        key = term.casefold()
        row = candidates.setdefault(
            key,
            {
                "term": term,
                "domains": Counter(),
                "titles": set(),
                "categories": set(),
                "references": set(),
            },
        )
        row["domains"][domain] += 1
        row["titles" if kind == "title" else "categories"].add(identity)
        row["references"].add((kind, identity))

    for pin in inventory["intakes"]:
        path = ROOT / pin["path"]
        pinned_json(ROOT, pin)
        receipt = verify(path.parent, ROOT)
        pool = json.loads((path.parent / "source_pool.json").read_text())
        sources = pool["sources"]
        for discovery in receipt["discovery"]:
            query = discovery["query"]
            match = re.fullmatch(r'intitle:"List of (.+)"', query)
            if match:
                old_terms.add(match.group(1).casefold())
            group = [
                source
                for source in sources
                if source["name"].startswith(f"p93_{discovery['key']}_")
            ]
            if not group:
                counts["discovery_without_frozen_source"] += 1
                continue
            domain = group[0]["domain"]
            if any(source["domain"] != domain for source in group):
                raise ValueError("P108 discovery source domain differs")
            for page in _pin_rows(discovery["raw_response"]):
                response = page.get("response", {}) if isinstance(page, dict) else {}
                query_rows = (
                    response.get("query", {}) if isinstance(response, dict) else {}
                )
                if not isinstance(query_rows, dict):
                    raise TypeError("P108 MediaWiki query response is malformed")
                for key in ("search", "categorymembers"):
                    rows = query_rows.get(key, [])
                    if not isinstance(rows, list):
                        raise TypeError("P108 MediaWiki title list is malformed")
                    for item in rows:
                        title = item.get("title") if isinstance(item, dict) else None
                        if not isinstance(title, str):
                            continue
                        term = _term(title)
                        if term is not None:
                            add(term, domain, title, "title")
                            counts["title_mentions"] += 1
        for source in sources:
            snapshot = _snapshot(ROOT, source["snapshot"])
            for doc in snapshot["documents"]:
                for line in doc["text"].splitlines():
                    term = _term(line, category=True)
                    if term is not None:
                        add(term, source["domain"], line, "category")
                        counts["category_mentions"] += 1
    return candidates, old_terms, counts


def compile(config_path: Path) -> tuple[dict[str, bytes], dict]:
    config = json.loads(config_path.read_text())
    required = {
        "schema",
        "inventory",
        "base_pool",
        "max_terms",
        "eval_modulus",
        "max_results_per_term",
        "bundle_size",
        "max_bundles_per_term",
    }
    if (
        config.get("schema") != SCHEMA
        or not required <= set(config)
        or set(config) - required not in (set(), {"strict_novelty"})
    ):
        raise ValueError("P108 autotopic catalog config differs")
    if type(config.get("strict_novelty", False)) is not bool:
        raise TypeError("P108 strict_novelty must be boolean")
    if (
        type(config["max_terms"]) is not int
        or not 1 <= config["max_terms"] <= 5000
        or type(config["eval_modulus"]) is not int
        or not 2 <= config["eval_modulus"] <= 10
    ):
        raise ValueError("P108 term/split bounds invalid")
    inventory = pinned_json(ROOT, config["inventory"])
    if inventory.get("schema") != "longworld.p108-wiki-autotopic-inventory.v1":
        raise ValueError("P108 seed inventory differs")
    prior = pinned_json(ROOT, config["base_pool"])
    if prior.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("P108 prior pool schema differs")
    prior_titles = _prior_titles(prior)
    prior_topics = {source["topic"] for source in prior["sources"]}
    candidates, old_terms, source_counts = _frozen_evidence(inventory)
    ledger = []
    for key, row in candidates.items():
        domain = min(row["domains"], key=lambda value: (-row["domains"][value], value))
        unseen = sum(title.casefold() not in prior_titles for title in row["titles"])
        topic = _topic(row["term"])
        reason = None
        if key in old_terms:
            reason = "previous_query_term"
        elif topic in prior_topics:
            reason = "previous_source_topic"
        elif not topic:
            reason = "topic_slug_empty"
        elif config.get("strict_novelty", False) and re.search(
            r"\b(?:documented|banned|attended|serving|designated)\b",
            row["term"],
            re.IGNORECASE,
        ):
            reason = "title_clause_fragment"
        elif (
            config.get("strict_novelty", False)
            and unseen == 0
            and len(row["categories"]) < 3
        ):
            reason = "no_prior_novel_title_or_category"
        ledger.append(
            {
                "term": row["term"],
                "domain": domain,
                "topic": topic,
                "source_titles": len(row["titles"]),
                "unseen_titles": unseen,
                "source_categories": len(row["categories"]),
                "domain_votes": dict(sorted(row["domains"].items())),
                "evidence": sorted(
                    [
                        {"kind": kind, "value": value}
                        for kind, value in row["references"]
                    ],
                    key=lambda item: (item["kind"], item["value"]),
                ),
                "score": 4 * unseen + 2 * len(row["titles"]) + len(row["categories"]),
                "reject_reason": reason,
            }
        )
    ledger.sort(key=lambda row: (-row["score"], row["term"].casefold()))
    selected, topics = [], set()
    for row in ledger:
        if row["reject_reason"] is not None:
            continue
        if row["topic"] in topics:
            row["reject_reason"] = "duplicate_topic_slug"
            continue
        if len(selected) >= config["max_terms"]:
            row["reject_reason"] = "term_budget"
            continue
        split = (
            "eval"
            if int(
                hashlib.sha256(f"{row['domain']}|{row['topic']}".encode()).hexdigest(),
                16,
            )
            % config["eval_modulus"]
            == 0
            else "train"
        )
        row["split"] = split
        topics.add(row["topic"])
        selected.append(row)
    if not selected or {row["split"] for row in selected} != {"train", "eval"}:
        raise ValueError("P108 discovered terms lack train/eval coverage")
    families = defaultdict(list)
    for row in selected:
        families[(row["domain"], row["split"])].append(row["term"])
    family_rows = []
    for (domain, split), terms in sorted(families.items()):
        slug = re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")[:8]
        digest = hashlib.sha256(domain.encode()).hexdigest()[:6]
        family_rows.append(
            {
                "name": f"p108_{slug}_{digest}_{split}",
                "domain": domain,
                "split": split,
                "mode": "search",
                "query_template": 'intitle:"List of {term}"',
                "terms": sorted(terms, key=str.casefold),
                "max_results_per_term": config["max_results_per_term"],
                "bundle_size": config["bundle_size"],
                "max_bundles_per_term": config["max_bundles_per_term"],
            }
        )
    catalog = {
        "schema": "longworld.p97-wiki-intake-shards.v1",
        "base_pool": config["base_pool"],
        "max_queries_per_shard": 40,
        "max_families_per_shard": 24,
        "max_terms_per_family": 20,
        "families": family_rows,
    }
    _validate_catalog(catalog)
    ledger_bytes = b"".join(_line(row) for row in ledger)
    catalog_bytes = _bytes(catalog)
    reject_counts = Counter(
        row["reject_reason"] for row in ledger if row["reject_reason"]
    )
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": sha(config_path),
        "inventory": config["inventory"],
        "base_pool": config["base_pool"],
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        "source_counts": dict(sorted(source_counts.items())),
        "candidate_terms": len(ledger),
        "selected_terms": len(selected),
        "rejections": dict(sorted(reject_counts.items())),
        "families": len(family_rows),
        "domains": dict(sorted(Counter(row["domain"] for row in selected).items())),
        "split_terms": dict(sorted(Counter(row["split"] for row in selected).items())),
        "train_ready": False,
    }
    return {
        "catalog.json": catalog_bytes,
        "ledger.jsonl": ledger_bytes,
        "manifest.json": _bytes(manifest),
    }, manifest


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    outputs, manifest = compile(config_path)
    if verify_only:
        if not output_dir.is_dir() or {p.name for p in output_dir.iterdir()} != set(
            outputs
        ):
            raise ValueError("P108 catalog output inventory differs")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P108 catalog replay differs: {name}")
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
        for name, content in outputs.items():
            (output_dir / name).write_bytes(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.config, args.output_dir, verify_only=args.verify_only),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
