#!/usr/bin/env python3
"""Fetch a provenance-complete public source pack without stale fallback."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    ATTESTATION_ENV,
    attach_attestation,
    attestation_key_from_env,
)
from longworld.core.provenance import (
    SOURCE_PACK_SCHEMA,
    load_verified_source_documents,
)

OUT = ROOT / "data" / "source_pack"
MAX_DOWNLOAD_BYTES = 16_000_000


@dataclass(frozen=True)
class SourceSpec:
    url: str
    license: str
    revision: str = ""


IETF_LICENSE = "IETF Trust Legal Provisions"
SOURCES: dict[str, SourceSpec] = {
    "rfc2119.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc2119.txt", IETF_LICENSE
    ),
    "rfc3986.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc3986.txt", IETF_LICENSE
    ),
    "rfc4180.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc4180.txt", IETF_LICENSE
    ),
    "rfc4648.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc4648.txt", IETF_LICENSE
    ),
    "rfc5321.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc5321.txt", IETF_LICENSE
    ),
    "rfc6901.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc6901.txt", IETF_LICENSE
    ),
    "rfc8259.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc8259.txt", IETF_LICENSE
    ),
    "rfc9110.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc9110.txt", IETF_LICENSE
    ),
    "rfc9112.txt": SourceSpec(
        "https://www.rfc-editor.org/rfc/rfc9112.txt", IETF_LICENSE
    ),
    "apache-2.0.txt": SourceSpec(
        "https://www.apache.org/licenses/LICENSE-2.0.txt", "Apache-2.0"
    ),
    "gpl-3.0.txt": SourceSpec(
        "https://www.gnu.org/licenses/gpl-3.0.txt", "GPL-3.0-only"
    ),
    "mit-license.txt": SourceSpec("https://www.mit.edu/~amini/LICENSE.md", "MIT"),
}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "longworld-sourcepack/2.0"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        requested = urlparse(url)
        final = urlparse(resp.geturl())
        if (
            requested.scheme != "https"
            or final.scheme != "https"
            or final.hostname != requested.hostname
        ):
            raise ValueError(f"source redirect left the trusted HTTPS host: {url}")
        declared = resp.headers.get("Content-Length")
        if declared and int(declared) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"source response is too large: {url}")
        raw = resp.read(MAX_DOWNLOAD_BYTES + 1)
        if len(raw) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"source response is too large: {url}")
        return raw


def _to_text(raw: bytes) -> tuple[str, str]:
    text = raw.decode("utf-8", errors="replace")
    parser = "plain_text"
    if "<html" in text[:500].lower() or "<!doctype" in text[:200].lower():
        parser = "html_strip"
        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", html.unescape(text))
    return text.strip() + "\n", parser


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_source_pack(
    out: Path = OUT,
    *,
    sources: dict[str, SourceSpec] = SOURCES,
    fetcher: Callable[[str], bytes] = _fetch,
    retrieved_at: str | None = None,
) -> list[dict]:
    """Fetch and verify a complete pack before atomically exposing it.

    Existing files are never reused to complete a partially failed refresh.
    """
    timestamp = retrieved_at or _utc_now()
    attestation_key = attestation_key_from_env("source_manifest")
    if attestation_key is None:
        raise ValueError(f"source fetch requires a 32-byte {ATTESTATION_ENV}")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.stage-", dir=out.parent))
    backup: Path | None = None
    try:
        docs: list[dict] = []
        for name, spec in sorted(sources.items()):
            if Path(name).name != name or Path(name).suffix.lower() not in {
                ".txt",
                ".md",
            }:
                raise ValueError(f"unsafe source filename: {name!r}")
            raw = fetcher(spec.url)
            text, parser_name = _to_text(raw)
            if len(text) < 400:
                raise ValueError(f"source {name} is too short ({len(text)} chars)")
            encoded = text.encode("utf-8")
            (stage / name).write_bytes(encoded)
            docs.append(
                {
                    "file": name,
                    "url": spec.url,
                    "license": spec.license,
                    "retrieved_at": timestamp,
                    "parser": {"name": parser_name, "version": "1"},
                    "revision": spec.revision,
                    "chars": len(text),
                    "bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        manifest = {
            "schema_version": SOURCE_PACK_SCHEMA,
            "generated_at": timestamp,
            "n": len(docs),
            "docs": docs,
        }
        manifest = attach_attestation(
            manifest, attestation_key, purpose="source_manifest"
        )
        (stage / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        load_verified_source_documents(stage)

        if out.exists():
            backup = Path(
                tempfile.mkdtemp(prefix=f".{out.name}.previous-", dir=out.parent)
            )
            backup.rmdir()
            os.replace(out, backup)
        try:
            os.replace(stage, out)
        except BaseException:
            if backup is not None and backup.exists() and not out.exists():
                os.replace(backup, out)
            raise
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
        return docs
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if backup is not None and backup.exists() and not out.exists():
            os.replace(backup, out)


def main() -> None:
    docs = fetch_source_pack()
    for doc in docs:
        print(f"verified {doc['file']} {doc['chars']} chars {doc['sha256']}")
    print(f"manifest n={len(docs)}")


if __name__ == "__main__":
    main()
