"""Probe official arXiv OAI metadata and one source with two paced requests.

The official OAI arXivRaw format exposes version history and license. This is
only a source-capacity probe; it does not issue QA or infer reader dependence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.p66_researchlab_taskbank import load_text_tar
from scripts.fetch_paper_workflow import _default_http_get

SCHEMA = "longworld.p88-oai-source-probe.v1"
OAI = "http://www.openarchives.org/OAI/2.0/"
RAW = "http://arxiv.org/OAI/arXivRaw/"
WORK = re.compile(r"\d{4}\.\d{4,5}\Z")
MAX_METADATA_BYTES = 8_000_000
MAX_ARCHIVE_BYTES = 64_000_000


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _split(work_id: str) -> str:
    residue = int.from_bytes(hashlib.sha256(work_id.encode()).digest()[:8], "big") % 10
    return "eval" if residue < 2 else "train"


def _parse(raw: bytes) -> tuple[list[dict[str, Any]], str | None]:
    root = ET.fromstring(raw)
    if root.tag != f"{{{OAI}}}OAI-PMH":
        raise ValueError("response is not OAI-PMH XML")
    errors = root.findall(f"{{{OAI}}}error")
    if errors:
        raise ValueError(
            "OAI-PMH error: " + "; ".join((item.text or "").strip() for item in errors)
        )
    records = []
    for record in root.findall(f"{{{OAI}}}ListRecords/{{{OAI}}}record"):
        item = record.find(f"{{{OAI}}}metadata/{{{RAW}}}arXivRaw")
        if item is None:
            continue
        work_id = item.findtext(f"{{{RAW}}}id") or ""
        if WORK.fullmatch(work_id) is None:
            continue
        versions = sorted(
            {
                child.attrib["version"]
                for child in item.findall(f"{{{RAW}}}version")
                if re.fullmatch(r"v[1-9]\d*", child.attrib.get("version", ""))
            },
            key=lambda value: int(value[1:]),
        )
        records.append(
            {
                "work_id": work_id,
                "title": " ".join((item.findtext(f"{{{RAW}}}title") or "").split()),
                "categories": (item.findtext(f"{{{RAW}}}categories") or "").split(),
                "license_uri": (item.findtext(f"{{{RAW}}}license") or "").strip()
                or None,
                "versions": versions,
                "split": _split(work_id),
                "header_datestamp": record.findtext(
                    f"{{{OAI}}}header/{{{OAI}}}datestamp"
                ),
            }
        )
    token = root.findtext(f"{{{OAI}}}ListRecords/{{{OAI}}}resumptionToken")
    return records, token.strip() if token else None


def _metadata_get(url: str, user_agent: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("OAI metadata redirected or changed status")
            raw = response.read(MAX_METADATA_BYTES + 1)
    except urllib.error.HTTPError as error:
        body = error.read(MAX_METADATA_BYTES + 1)
        raise ValueError(f"OAI HTTP {error.code}; body_sha256={_sha(body)}") from error
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("OAI metadata response exceeded byte cap")
    return raw


def _source_get(url: str, user_agent: str) -> bytes:
    response = _default_http_get(url, {"User-Agent": user_agent}, 60)
    if response.status != 200 or len(response.body) > MAX_ARCHIVE_BYTES:
        raise ValueError("source response status or byte cap failed")
    return response.body


def probe(
    config_path: Path,
    output_dir: Path,
    *,
    metadata_get: Callable[[str, str], bytes] = _metadata_get,
    source_get: Callable[[str, str], bytes] = _source_get,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    if (
        config.get("schema") != SCHEMA
        or config.get("oai_url") != "https://oaipmh.arxiv.org/oai"
        or config.get("metadata_prefix") != "arXivRaw"
        or config.get("oai_set") != "cs:cs:CL"
        or config.get("minimum_request_interval_seconds", 0) < 3
        or config.get("maximum_network_requests") != 2
        or config.get("source_archive_limit") != 1
        or config.get("content_use") != "local_research_only_no_redistribution"
    ):
        raise ValueError("unsafe OAI probe configuration")
    output_dir.mkdir(parents=True)
    query = urllib.parse.urlencode(
        {"verb": "ListRecords", "metadataPrefix": "arXivRaw", "set": "cs:cs:CL"}
    )
    metadata_url = config["oai_url"] + "?" + query
    errors = []
    records = []
    metadata_receipt = None
    archive_receipt = None
    source_capacity = None
    first_at = monotonic()
    try:
        raw = metadata_get(metadata_url, config["user_agent"])
        records, continuation = _parse(raw)
        (output_dir / "metadata.xml").write_bytes(raw)
        metadata_receipt = {
            "url": metadata_url,
            "sha256": _sha(raw),
            "bytes": len(raw),
            "records": len(records),
            "has_resumption_token": continuation is not None,
        }
    except (OSError, ValueError, ET.ParseError) as error:
        errors.append(
            {
                "stage": "metadata",
                "error_type": type(error).__name__,
                "error": str(error),
                "url": metadata_url,
            }
        )
    existing = set(config["exclude_work_ids"])
    eligible = sorted(
        (
            record
            for record in records
            if len(record["versions"]) >= 2
            and record["license_uri"]
            and record["work_id"] not in existing
        ),
        key=lambda record: record["work_id"],
    )
    selected = eligible[0] if eligible and not errors else None
    if selected is not None:
        remaining = config["minimum_request_interval_seconds"] - (
            monotonic() - first_at
        )
        if remaining > 0:
            sleep(remaining)
        version = selected["versions"][-1]
        archive_url = f"https://export.arxiv.org/e-print/{selected['work_id']}{version}"
        try:
            body = source_get(archive_url, config["user_agent"])
            filename = f"arxiv-{selected['work_id']}{version}.source.tar"
            archive_path = output_dir / filename
            archive_path.write_bytes(body)
            archive_receipt = {
                "url": archive_url,
                "file": filename,
                "sha256": _sha(body),
                "bytes": len(body),
                "work_id": selected["work_id"],
                "version": version,
                "license_uri": selected["license_uri"],
            }
            files = load_text_tar(archive_path)
            source_capacity = {
                "readable_source_files": len(files),
                "readable_source_chars": sum(map(len, files.values())),
                "actual_token_capacity_measured": False,
                "revision_pair_capacity_measured": False,
            }
        except (OSError, ValueError, tarfile.TarError) as error:
            errors.append(
                {
                    "stage": "source",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "url": archive_url,
                }
            )
    status = (
        "metadata_failed"
        if metadata_receipt is None
        else "source_failed"
        if errors
        else "source_acquired"
        if archive_receipt
        else "metadata_only"
    )
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_raw),
        "status": status,
        "metadata": metadata_receipt,
        "metadata_records": len(records),
        "multi_revision_licensed_nonexisting_records": len(eligible),
        "recorded_work_ids": [record["work_id"] for record in records],
        "license_status_distribution": dict(
            Counter(
                "reported" if record["license_uri"] else "missing" for record in records
            )
        ),
        "selected_work": selected,
        "source_archive": archive_receipt,
        "source_capacity": source_capacity,
        "network_requests_sent": 1 + (selected is not None),
        "minimum_request_interval_seconds": config["minimum_request_interval_seconds"],
        "content_use": config["content_use"],
        "errors": errors,
        "train_ready": False,
        "claim_limit": "first OAI page and at most one source archive; no revision-pair or final-reader QA admission",
    }
    _write(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = probe(args.config, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 1 if manifest["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
