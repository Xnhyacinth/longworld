"""Freeze the OpenAlex topic hierarchy for reproducible stratified sampling."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_capability_world_pipeline import write_new_json


def validate_topics(topics):
    ids = set()
    parents = {}
    for topic in topics:
        identity = topic["id"]
        if identity in ids:
            raise ValueError("duplicate topic")
        ids.add(identity)
        for level in ("domain", "field", "subfield"):
            node = topic[level]
            if not node["id"] or not node["display_name"]:
                raise ValueError("missing classification node")
        for child, parent in (
            (topic["subfield"]["id"], topic["field"]["id"]),
            (topic["field"]["id"], topic["domain"]["id"]),
        ):
            if child in parents and parents[child] != parent:
                raise ValueError("conflicting taxonomy parent")
            parents[child] = parent
    if not ids:
        raise ValueError("empty topic catalog")


def fetch_page(page):
    url = "https://api.openalex.org/topics?" + urllib.parse.urlencode(
        {"per-page": 200, "page": page, "sort": "id:asc"}
    )
    with urllib.request.urlopen(url, timeout=45) as response:
        raw = response.read()
    payload = json.loads(raw)
    return payload, {
        "url": url,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "response": payload,
    }


def fetch(output, workers=4):
    output.mkdir(parents=True, exist_ok=False)
    first, receipt = fetch_page(1)
    expected = first["meta"]["count"]
    pages = [receipt]
    records = list(first["results"])
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for page, receipt in pool.map(
            fetch_page, range(2, math.ceil(expected / 200) + 1)
        ):
            records.extend(page["results"])
            pages.append(receipt)
    topics = [
        {key: row[key] for key in ("id", "display_name", "domain", "field", "subfield")}
        for row in records
    ]
    validate_topics(topics)
    if len(topics) != expected:
        raise ValueError("incomplete taxonomy snapshot")
    write_new_json(output / "responses.json", pages)
    result = {
        "schema_version": "longworld.openalex-topics.v1",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://api.openalex.org/topics",
        "count": len(topics),
        "topics": sorted(topics, key=lambda item: item["id"]),
        "use": "taxonomy metadata only; simulated events are not historical facts",
    }
    write_new_json(output / "topics.json", result)
    return {
        "topics": len(topics),
        "domains": len({x["domain"]["id"] for x in topics}),
        "fields": len({x["field"]["id"] for x in topics}),
        "subfields": len({x["subfield"]["id"] for x in topics}),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(fetch(args.output, args.workers)))
