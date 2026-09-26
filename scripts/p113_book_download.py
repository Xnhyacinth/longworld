"""Resume a bounded catalog-prefix source freeze from the official PG mirror."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path

SCHEMA = "longworld.p113-book-mirror-attempts.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def download(
    config: Path,
    plan: Path,
    output: Path,
    *,
    max_attempts: int,
    verify_only: bool = False,
) -> dict:
    cfg = json.loads(config.read_text())
    planned = json.loads(plan.read_text())
    if (
        cfg["schema"] != "longworld.p113-book-catalog-request.v1"
        or planned["schema"] != "longworld.p113-book-catalog-plan.v1"
        or planned["request_sha256"] != _sha(config.read_bytes())
        or not cfg["mirror_base"].startswith("https://gutenberg.pglaf.org/")
        or not 1 <= max_attempts <= min(len(planned["candidates"]), 100)
        or not 0 < cfg["requests_per_second"] <= 0.5
    ):
        raise ValueError("book download contract or official mirror differs")
    attempt_dir = output / "attempts"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    records = []
    previous_request = None
    for item in planned["candidates"][:max_attempts]:
        ebook_id = item["ebook_id"]
        url = f"{cfg['mirror_base']}/{ebook_id}/pg{ebook_id}.txt"
        path = attempt_dir / f"pg{ebook_id}.txt"
        if path.exists():
            raw = path.read_bytes()
        elif verify_only:
            raise ValueError(f"missing previously frozen mirror file: {ebook_id}")
        else:
            if previous_request is not None:
                time.sleep(
                    max(
                        0,
                        1 / cfg["requests_per_second"]
                        - (time.monotonic() - previous_request),
                    )
                )
            previous_request = time.monotonic()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "LongWorld/1.0 bounded catalog-driven research source freeze"
                },
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status != 200 or response.geturl() != url:
                    raise ValueError(f"mirror response/redirect differs: {ebook_id}")
                raw = response.read(cfg["max_bytes_per_book"] + 1)
            if len(raw) > cfg["max_bytes_per_book"]:
                raise ValueError(f"mirror source exceeds byte cap: {ebook_id}")
            path.write_bytes(raw)
        if len(raw) > cfg["max_bytes_per_book"]:
            raise ValueError(f"cached mirror source exceeds byte cap: {ebook_id}")
        records.append(
            {
                "ebook_id": ebook_id,
                "url": url,
                "raw_file": str(path.relative_to(output)),
                "raw_bytes": len(raw),
                "raw_sha256": _sha(raw),
                "topic": item["topic"],
                "split": item["split"],
            }
        )
    receipt = {
        "schema": SCHEMA,
        "request_sha256": _sha(config.read_bytes()),
        "plan_sha256": _sha(plan.read_bytes()),
        "catalog_sha256": cfg["catalog_sha256"],
        "mirror_base": cfg["mirror_base"],
        "max_attempts": max_attempts,
        "attempted": len(records),
        "records": records,
        "train_ready": False,
    }
    encoded = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    manifest = output / "download_manifest.json"
    if verify_only:
        if manifest.read_bytes() != encoded:
            raise ValueError("mirror attempt receipt replay differs")
    elif manifest.exists() and manifest.read_bytes() != encoded:
        raise ValueError("mirror attempt receipt exists with different bytes")
    else:
        manifest.write_bytes(encoded)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-attempts", type=int, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    receipt = download(
        args.config,
        args.plan,
        args.output,
        max_attempts=args.max_attempts,
        verify_only=args.verify_only,
    )
    print(json.dumps({k: v for k, v in receipt.items() if k != "records"}, indent=2))


if __name__ == "__main__":
    main()
