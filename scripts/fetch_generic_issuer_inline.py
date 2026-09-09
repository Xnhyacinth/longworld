#!/usr/bin/env python3
"""Acquire only registered official source URLs from an explicit P64 request."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.issuer_generic_inline import (
    SCHEMA,
    SOURCE_FAMILY,
    parse_generic_inline,
    validate_listing,
    validate_request,
)
from longworld.core.issuerinlineworkflow import normalize_inline_source


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect requires a new explicit official-source request")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    issuer = validate_request(request)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    opener = build_opener(NoRedirect)

    def acquire(url, name):
        raw_path = out / (name + ".raw.html")
        cache_path = out / (name + ".retrieval.json")
        if raw_path.exists() and cache_path.exists():
            raw = raw_path.read_bytes()
            cached = json.loads(cache_path.read_text())
            if cached != {
                "source_url": url,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }:
                raise ValueError("cached source URL or digest mismatch")
        else:
            with opener.open(
                Request(url, headers={"User-Agent": request["user_agent"]}), timeout=45
            ) as response:
                if response.status != 200:
                    raise ValueError("non-200 source response")
                raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise ValueError("source exceeds byte limit")
            raw_path.write_bytes(raw)
            cache_path.write_text(
                json.dumps(
                    {
                        "source_url": url,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw),
                    }
                )
            )
            time.sleep(0.5)
        normalized, receipt = normalize_inline_source(raw)
        (out / (name + ".html")).write_bytes(normalized)
        return {
            "source_url": url,
            "raw_text": raw.decode(),
            "text": normalized.decode(),
            "normalization": receipt,
            "retrieval_bytes": len(raw),
            "bytes": len(normalized),
        }

    listing = acquire(request["listing_url"], "listing")
    validate_listing(listing["text"], request)
    records = []
    for filing in sorted(request["filings"], key=lambda r: r["report_date"]):
        source = acquire(filing["source_url"], filing["report_date"])
        derived = parse_generic_inline(
            source["text"], issuer=issuer, report_date=filing["report_date"]
        )
        records.append({**filing, **source, "derived": derived})
    payload = {
        "schema_version": SCHEMA,
        "source_family": SOURCE_FAMILY,
        "production_eligible": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "request": request,
        "listing": listing,
        "records": records,
        "rights_status": "public_official_filing_access_verified; redistribution_and_training_rights_not_adjudicated",
    }
    signed = attach_attestation(
        payload, attestation_key_from_env("source_manifest"), purpose="source_manifest"
    )
    dest = out / "source_manifest.json"
    dest.write_text(json.dumps(signed, ensure_ascii=False, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "source_manifest": str(dest),
                "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                "documents": len(records),
                "source_bytes": [r["bytes"] for r in records],
                "production_eligible": False,
            }
        )
    )


if __name__ == "__main__":
    main()
