"""Fail-closed provenance records for real source and workflow ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from longworld.core.attestation import attestation_key_from_env, verify_attestation

SOURCE_PACK_SCHEMA = "longworld.source-pack.v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_HASH_RE = re.compile(r"^[0-9a-f]{16,64}$")
MAX_MANIFEST_BYTES = 2_000_000
MAX_SOURCE_BYTES = 16_000_000
MAX_SOURCE_DOCUMENTS = 512


class ProvenanceError(ValueError):
    """A source cannot be proven to match its declared lineage."""


def _parse_timestamp(value: str, field: str) -> datetime:
    if not value or not isinstance(value, str):
        raise ProvenanceError(f"missing {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProvenanceError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ProvenanceError(f"{field} must include a timezone")
    return parsed


@dataclass(frozen=True)
class SourceLineage:
    provenance_id: str
    url: str
    license: str
    retrieved_at: str
    parser: str
    sha256: str
    revision: str = ""
    source_path: str = ""

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ProvenanceError("sha256 must be the full 64-character digest")
        if self.provenance_id != f"sha256:{self.sha256}":
            raise ProvenanceError("provenance_id must identify the full source hash")
        parsed_url = urlparse(self.url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ProvenanceError("lineage url must be an absolute HTTP(S) URL")
        if not self.license.strip():
            raise ProvenanceError("missing source license")
        _parse_timestamp(self.retrieved_at, "retrieved_at")
        parser_name, separator, parser_version = self.parser.partition("@")
        if not separator or not parser_name or not parser_version:
            raise ProvenanceError("parser must be '<name>@<version>'")


@dataclass(frozen=True)
class SourceDocument:
    file: str
    stem: str
    text: str
    lineage: SourceLineage | None
    provenance_verified: bool


def _read_manifest(pack_dir: Path) -> dict:
    manifest_path = pack_dir / "MANIFEST.json"
    try:
        raw = json.loads(
            _read_regular_file(manifest_path, MAX_MANIFEST_BYTES).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read source manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProvenanceError("source manifest must be a JSON object")
    return raw


def _read_regular_file(path: Path, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ProvenanceError(f"source path is not a regular file: {path.name}")
        if info.st_size > max_bytes:
            raise ProvenanceError(f"source file exceeds size limit: {path.name}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(1_048_576, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise ProvenanceError(f"source file exceeds size limit: {path.name}")
        return data
    finally:
        os.close(fd)


def _eligible_files(pack_dir: Path) -> set[str]:
    return {
        path.name
        for path in pack_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".txt", ".md"}
    }


def _safe_source_path(pack_dir: Path, file_name: object) -> Path:
    if not isinstance(file_name, str) or not file_name:
        raise ProvenanceError("manifest source file must be a non-empty string")
    path = Path(file_name)
    if path.name != file_name or path.suffix.lower() not in {".txt", ".md"}:
        raise ProvenanceError(f"invalid source filename: {file_name!r}")
    return pack_dir / file_name


def load_verified_source_documents(pack_dir: Path) -> list[SourceDocument]:
    """Load a v2 source pack only when every file and lineage field verifies."""
    if not pack_dir.is_dir():
        raise ProvenanceError(f"source pack directory does not exist: {pack_dir}")
    manifest = _read_manifest(pack_dir)
    if manifest.get("schema_version") != SOURCE_PACK_SCHEMA:
        raise ProvenanceError("source manifest is not verified v2")
    if not verify_attestation(
        manifest,
        attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError("source manifest has no valid producer attestation")
    _parse_timestamp(str(manifest.get("generated_at") or ""), "generated_at")
    docs = manifest.get("docs")
    if not isinstance(docs, list) or not docs:
        raise ProvenanceError("verified manifest must contain source documents")
    if len(docs) > MAX_SOURCE_DOCUMENTS:
        raise ProvenanceError("source manifest contains too many documents")
    if manifest.get("n") != len(docs):
        raise ProvenanceError("manifest count does not match docs")

    declared_files: list[str] = []
    loaded: list[SourceDocument] = []
    for entry in docs:
        if not isinstance(entry, dict):
            raise ProvenanceError("manifest document must be an object")
        source_path = _safe_source_path(pack_dir, entry.get("file"))
        declared_files.append(source_path.name)
        try:
            raw = _read_regular_file(source_path, MAX_SOURCE_BYTES)
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProvenanceError(f"cannot read {source_path.name}: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        declared_hash = entry.get("sha256")
        if not isinstance(declared_hash, str) or not _SHA256_RE.fullmatch(
            declared_hash
        ):
            raise ProvenanceError(f"{source_path.name} has a truncated/invalid sha256")
        if digest != declared_hash:
            raise ProvenanceError(f"hash mismatch for {source_path.name}")
        if entry.get("bytes") != len(raw) or entry.get("chars") != len(text):
            raise ProvenanceError(f"size mismatch for {source_path.name}")
        parser = entry.get("parser")
        if not isinstance(parser, dict):
            raise ProvenanceError(f"missing parser metadata for {source_path.name}")
        parser_name = parser.get("name")
        parser_version = parser.get("version")
        if not isinstance(parser_name, str) or not isinstance(parser_version, str):
            raise ProvenanceError(f"invalid parser metadata for {source_path.name}")
        lineage = SourceLineage(
            provenance_id=f"sha256:{digest}",
            url=str(entry.get("url") or ""),
            license=str(entry.get("license") or ""),
            retrieved_at=str(entry.get("retrieved_at") or ""),
            parser=f"{parser_name}@{parser_version}",
            sha256=digest,
            revision=str(entry.get("revision") or ""),
            source_path=source_path.name,
        )
        loaded.append(
            SourceDocument(
                file=source_path.name,
                stem=source_path.stem,
                text=text.strip(),
                lineage=lineage,
                provenance_verified=True,
            )
        )

    if len(declared_files) != len(set(declared_files)):
        raise ProvenanceError("manifest contains duplicate source files")
    if set(declared_files) != _eligible_files(pack_dir):
        raise ProvenanceError("manifest and source directory file sets differ")
    return loaded


def load_source_documents(
    pack_dir: Path, *, allow_legacy: bool = False
) -> list[SourceDocument]:
    """Load verified sources, or hash-checked legacy sources for diagnostics only.

    A malformed v2 pack never falls back to legacy behavior. Legacy documents have
    no ``SourceLineage`` and therefore cannot be classified as causal evidence.
    """
    manifest = _read_manifest(pack_dir)
    if manifest.get("schema_version") == SOURCE_PACK_SCHEMA:
        return load_verified_source_documents(pack_dir)
    if not allow_legacy:
        raise ProvenanceError("legacy source manifest is diagnostic-only")
    docs = manifest.get("docs")
    if not isinstance(docs, list) or manifest.get("n") != len(docs):
        raise ProvenanceError("invalid legacy source manifest")
    if len(docs) > MAX_SOURCE_DOCUMENTS:
        raise ProvenanceError("legacy source manifest contains too many documents")
    loaded: list[SourceDocument] = []
    declared: list[str] = []
    for entry in docs:
        if not isinstance(entry, dict):
            raise ProvenanceError("legacy manifest document must be an object")
        source_path = _safe_source_path(pack_dir, entry.get("file"))
        declared.append(source_path.name)
        try:
            raw = _read_regular_file(source_path, MAX_SOURCE_BYTES)
            text = raw.decode("utf-8", errors="replace")
        except OSError as exc:
            raise ProvenanceError(f"cannot read {source_path.name}: {exc}") from exc
        expected = entry.get("sha256")
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        if not isinstance(expected, str) or not _LEGACY_HASH_RE.fullmatch(expected):
            raise ProvenanceError(f"invalid legacy hash for {source_path.name}")
        if not digest.startswith(expected):
            raise ProvenanceError(f"legacy hash mismatch for {source_path.name}")
        loaded.append(
            SourceDocument(
                file=source_path.name,
                stem=source_path.stem,
                text=text.strip(),
                lineage=None,
                provenance_verified=False,
            )
        )
    if len(declared) != len(set(declared)):
        raise ProvenanceError("legacy manifest contains duplicate files")
    if set(declared) != _eligible_files(pack_dir):
        raise ProvenanceError("legacy manifest and source directory file sets differ")
    return loaded
