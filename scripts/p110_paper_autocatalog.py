"""Freeze arXiv's category taxonomy and make bounded P105 metadata shards.

Categories are selected from the official page, never entered as a per-domain
vocabulary. This stage requests no paper source and admits no training task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p110-paper-autocatalog.v1"
TAXONOMY_URL = "https://arxiv.org/category_taxonomy"
CATEGORY = re.compile(r"[a-z-]+\.[A-Za-z-]+\Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _pin(pin: dict) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("P110 pin needs path and sha256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P110 pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path.read_bytes()) != pin["sha256"]:
        raise ValueError(f"P110 pin drift: {relative}")
    return path


class _Taxonomy(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.group = ""
        self.in_h4 = False
        self.text = ""
        self.rows: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "h2" and (identity := attributes.get("id", "")).startswith(
            "accordion-head-grp_"
        ):
            self.group = identity.removeprefix("accordion-head-grp_")
        if tag == "h4" and self.group:
            self.in_h4 = True
            self.text = ""

    def handle_data(self, data: str) -> None:
        if self.in_h4:
            self.text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h4" and self.in_h4:
            category = self.text.strip().split(maxsplit=1)[0]
            if CATEGORY.fullmatch(category):
                self.rows.append((self.group, category))
            self.in_h4 = False


def _categories(raw: bytes) -> dict[str, list[str]]:
    parser = _Taxonomy()
    parser.feed(raw.decode("utf-8"))
    groups: dict[str, list[str]] = defaultdict(list)
    for group, category in parser.rows:
        groups[group].append(category)
    flat = [category for rows in groups.values() for category in rows]
    if len(flat) < 100 or len(flat) != len(set(flat)) or len(groups) < 6:
        raise ValueError("official taxonomy lacks expected distinct dotted categories")
    return dict(groups)


def _config(config_path: Path) -> dict:
    value = json.loads(config_path.read_text())
    if (
        value.get("schema") != SCHEMA + ".config"
        or value.get("taxonomy_url") != TAXONOMY_URL
        or type(value.get("queries")) is not int
        or not 16 <= value["queries"] <= 40
        or type(value.get("max_per_group")) is not int
        or not 1 <= value["max_per_group"] <= 8
        or type(value.get("shard_size")) is not int
        or not 2 <= value["shard_size"] <= 10
        or value.get("request_interval_seconds", 0) < 3.2
        or value.get("page_size", 0) not in range(1, 101)
        or value.get("content_use") != "local_research_only_no_redistribution"
    ):
        raise ValueError("P110 plan exceeds bounded metadata contract")
    _pin(value["prior_catalog_config"])
    _pin(value["prior_candidate_index"])
    for pin in value["prior_source_configs"]:
        _pin(pin)
    return value


def build(config_path: Path, taxonomy: bytes, output_dir: Path) -> dict[str, bytes]:
    config = _config(config_path)
    groups = _categories(taxonomy)
    previous = json.loads(_pin(config["prior_catalog_config"]).read_text())
    excluded = set(previous["queries"])
    ranked = {
        group: sorted(
            (category for category in values if "cat:" + category not in excluded),
            key=lambda category: (_sha(category.encode()), category),
        )[: config["max_per_group"]]
        for group, values in groups.items()
    }
    selected = []
    for position in range(config["max_per_group"]):
        for group in sorted(ranked):
            if position < len(ranked[group]):
                selected.append({"group": group, "category": ranked[group][position]})
            if len(selected) == config["queries"]:
                break
        if len(selected) == config["queries"]:
            break
    if len(selected) != config["queries"]:
        raise ValueError("taxonomy cannot satisfy bounded group-balanced plan")
    outputs = {"taxonomy.html": taxonomy}
    for offset in range(0, len(selected), config["shard_size"]):
        shard = selected[offset : offset + config["shard_size"]]
        name = f"metadata_config_{offset // config['shard_size']:02d}.json"
        outputs[name] = _dump(
            {
                "schema": "longworld.p105-paper-catalog.v1.config",
                "api_url": "https://export.arxiv.org/api/query",
                "user_agent": config["user_agent"],
                "content_use": config["content_use"],
                "queries": ["cat:" + row["category"] for row in shard],
                "page_size": config["page_size"],
                "request_interval_seconds": config["request_interval_seconds"],
                "minimum_latest_version": 2,
                "per_query_work_limit": 1,
                "work_limit": len(shard),
                "frozen_inventory_root": config["frozen_inventory_root"],
                "prior_source_configs": config["prior_source_configs"],
                "prior_candidate_index": config["prior_candidate_index"],
            }
        )
    outputs["category_ledger.jsonl"] = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
        for row in selected
    )
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path.read_bytes()),
        "taxonomy_url": TAXONOMY_URL,
        "taxonomy_sha256": _sha(taxonomy),
        "official_dotted_categories": sum(map(len, groups.values())),
        "official_groups": len(groups),
        "selected_queries": len(selected),
        "selected_groups": len({row["group"] for row in selected}),
        "shards": sorted(
            name for name in outputs if name.startswith("metadata_config_")
        ),
        "files_sha256": {name: _sha(data) for name, data in outputs.items()},
        "content_use": config["content_use"],
        "train_ready": False,
    }
    outputs["manifest.json"] = _dump(manifest)
    return outputs


def run(
    config_path: Path, output_dir: Path, taxonomy_path: Path | None, verify_only: bool
) -> dict:
    if verify_only:
        taxonomy = (output_dir / "taxonomy.html").read_bytes()
    else:
        if taxonomy_path is None:
            raise ValueError("new P110 plan requires frozen taxonomy input")
        taxonomy = taxonomy_path.read_bytes()
    outputs = build(config_path, taxonomy, output_dir)
    if verify_only:
        if {path.name for path in output_dir.iterdir()} != set(outputs):
            raise ValueError("P110 plan file inventory changed")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P110 plan replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P110 plan output must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            (output_dir / name).write_bytes(content)
    return json.loads(outputs["manifest.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--taxonomy-html", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.config, args.output_dir, args.taxonomy_html, args.verify_only),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
