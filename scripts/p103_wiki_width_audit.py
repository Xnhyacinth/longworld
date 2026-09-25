"""Read-only census of P100 width rejections on the frozen P97 Wiki pool."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "data/capability_records/p97_wiki_gated_delta_v1/source_pool.json"
AUDIT = ROOT / "data/candidates/p100_wiki_categorical_scan_v3/page_audit.jsonl"
POOL_SHA256 = "094353d647ae888821ede5683fd912d125f336018317ffc9364b40b7cd840268"
AUDIT_SHA256 = "8e3e008858a122d4a722c96d132f24d21bf9eeef5897417e4053bf09de379846"


def _pinned_bytes(path: Path, expected_sha256: str) -> bytes:
    content = path.read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"frozen input changed: {path}: {actual} != {expected_sha256}")
    return content


def main() -> None:
    pool = json.loads(_pinned_bytes(POOL, POOL_SHA256))
    pages = [
        json.loads(line) for line in _pinned_bytes(AUDIT, AUDIT_SHA256).splitlines()
    ]
    sources = {source["name"]: source for source in pool["sources"]}
    documents: dict[tuple[str, str], list[str]] = {}
    gross = 0
    width_pages: set[tuple[str, str]] = set()
    width_groups: set[str] = set()
    unique: dict[tuple[str, str, str], dict] = {}
    for page in pages:
        source_group = page["source_group"]
        doc_id = page["doc_id"]
        for reject in page["rejected_tables"]:
            if reject["reason"] != "row_width_mismatch":
                continue
            gross += 1
            width_pages.add((source_group, doc_id))
            width_groups.add(source_group)
            key = (doc_id, reject["heading"], reject["header"])
            if key in unique:
                continue
            document_key = (source_group, doc_id)
            if document_key not in documents:
                source = sources[source_group]
                snapshot_path = ROOT / source["snapshot"]["path"]
                snapshot = json.loads(
                    _pinned_bytes(snapshot_path, source["snapshot"]["sha256"])
                )
                document = next(
                    doc for doc in snapshot["documents"] if doc["doc_id"] == doc_id
                )
                documents[document_key] = document["text"].splitlines()
            lines = documents[document_key]
            heading_line = "## " + reject["heading"]
            candidates = [
                index
                for index, line in enumerate(lines)
                if line == reject["header"] and heading_line in lines[:index]
            ]
            if not candidates:
                raise ValueError(f"rejected header absent from reader: {key}")
            expected_width = len(reject["header"].split(" | "))
            first_mismatch = None
            for header_index in candidates:
                row_index = header_index + 1
                while row_index < len(lines):
                    body = lines[row_index]
                    if not body or body.startswith("#"):
                        break
                    observed_width = len(body.split(" | "))
                    if observed_width != expected_width:
                        first_mismatch = {
                            "prior_full_width_rows": row_index - header_index - 1,
                            "expected_width": expected_width,
                            "observed_width": observed_width,
                            "body": body,
                        }
                        break
                    row_index += 1
                if first_mismatch is not None:
                    break
            if first_mismatch is None:
                raise ValueError(f"rejection cannot be replayed from reader: {key}")
            unique[key] = {"domain": sources[source_group]["domain"], **first_mismatch}
    prior_rows = Counter(
        "0"
        if row["prior_full_width_rows"] == 0
        else "1"
        if row["prior_full_width_rows"] == 1
        else "2-7"
        if row["prior_full_width_rows"] < 8
        else "8+"
        for row in unique.values()
    )
    direction = Counter(
        "shorter" if row["observed_width"] < row["expected_width"] else "longer"
        for row in unique.values()
    )
    print(
        json.dumps(
            {
                "source_pool_sha256": POOL_SHA256,
                "page_audit_sha256": AUDIT_SHA256,
                "gross_width_rejections": gross,
                "unique_doc_heading_header": len(unique),
                "affected_pages": len(width_pages),
                "affected_source_groups": len(width_groups),
                "first_mismatch_prior_full_width_rows": dict(prior_rows),
                "first_mismatch_direction": dict(direction),
                "affected_domains": dict(
                    sorted(Counter(row["domain"] for row in unique.values()).items())
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
