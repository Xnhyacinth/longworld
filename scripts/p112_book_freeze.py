"""Freeze bounded Project Gutenberg plain text with source and byte receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path

SCHEMA = "longworld.p112-book-freeze.v1"
START = re.compile(
    r"(?im)^\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK .+?\*\*\*\s*$"
)
END = re.compile(
    r"(?im)^\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK .+?\*\*\*\s*$"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _body(raw: bytes, ebook_id: int) -> str:
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    if f"eBook #{ebook_id}" not in text and f"EBook #{ebook_id}" not in text:
        raise ValueError("ebook ID is not visible in downloaded header")
    if "not located in the United States" not in text:
        raise ValueError("Gutenberg jurisdiction notice is missing")
    starts, ends = list(START.finditer(text)), list(END.finditer(text))
    if len(starts) != 1 or len(ends) != 1 or starts[0].end() >= ends[0].start():
        raise ValueError("Gutenberg work body boundary is ambiguous")
    body = text[starts[0].end() : ends[0].start()].strip()
    if len(body) < 20000:
        raise ValueError("work body is too short for chapter synthesis")
    return body + "\n"


def freeze(config: Path, output: Path, *, verify_only: bool = False) -> dict:
    request = json.loads(config.read_text())
    if request.get("schema") != "longworld.p112-book-source-request.v1":
        raise ValueError("book source request schema differs")
    ids = [item["ebook_id"] for item in request["books"]]
    if len(ids) != len(set(ids)) or len(ids) > 12:
        raise ValueError("bounded source request must use unique IDs, at most 12")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for i, book in enumerate(request["books"]):
        ebook_id = book["ebook_id"]
        if (
            type(ebook_id) is not int
            or ebook_id <= 0
            or book["split"] not in {"train", "eval"}
        ):
            raise ValueError("invalid book source ID or split")
        url = f"https://www.gutenberg.org/cache/epub/{ebook_id}/pg{ebook_id}.txt"
        raw_path = output / f"pg{ebook_id}.txt"
        body_path = output / f"pg{ebook_id}.body.txt"
        if raw_path.is_file():
            raw = raw_path.read_bytes()
        elif verify_only:
            raise ValueError(f"missing frozen raw source: {ebook_id}")
        else:
            if i:
                time.sleep(1 / request["requests_per_second"])
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "LongWorld/1.0 bounded research source freeze"},
            )
            with urllib.request.urlopen(req, timeout=60) as response:
                if response.status != 200 or response.geturl() != url:
                    raise ValueError(
                        f"source redirect or HTTP status differs: {ebook_id}"
                    )
                raw = response.read(request["max_bytes_per_book"] + 1)
            if len(raw) > request["max_bytes_per_book"]:
                raise ValueError(f"source exceeds byte cap: {ebook_id}")
            raw_path.write_bytes(raw)
        if len(raw) > request["max_bytes_per_book"]:
            raise ValueError(f"cached source exceeds byte cap: {ebook_id}")
        source_header = (
            raw.decode("utf-8-sig").replace("\r\n", "\n").split("*** START OF", 1)[0]
        )
        if (
            f"Title: {book['title']}" not in source_header
            or f"Author: {book['author']}" not in source_header
        ):
            raise ValueError(f"source title/author header differs: {ebook_id}")
        body = _body(raw, ebook_id).encode()
        if body_path.exists() and body_path.read_bytes() != body:
            raise ValueError(f"frozen body differs: {ebook_id}")
        if not verify_only:
            body_path.write_bytes(body)
        elif not body_path.exists():
            raise ValueError(f"frozen body is missing: {ebook_id}")
        records.append(
            {
                **book,
                "landing_url": f"https://www.gutenberg.org/ebooks/{ebook_id}",
                "text_url": url,
                "raw_sha256": _sha(raw),
                "body_sha256": _sha(body),
                "raw_bytes": len(raw),
                "body_bytes": len(body),
                "raw_file": raw_path.name,
                "body_file": body_path.name,
                "source_group": f"gutenberg-{ebook_id}",
            }
        )
    manifest = {
        "schema": SCHEMA,
        "request_sha256": _sha(config.read_bytes()),
        "policy_url": request["policy_url"],
        "records": records,
        "train_ready": False,
    }
    encoded = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    path = output / "manifest.json"
    if verify_only:
        if path.read_bytes() != encoded:
            raise ValueError("book freeze receipt replay differs")
    elif path.exists() and path.read_bytes() != encoded:
        raise ValueError("book freeze receipt exists with different bytes")
    else:
        path.write_bytes(encoded)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            freeze(args.config, args.output, verify_only=args.verify_only), indent=2
        )
    )


if __name__ == "__main__":
    main()
