"""Content-address the local files that define exact tokenizer behavior."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from longworld.core.attestation import sanitized_attestation_environment

TOKENIZER_ASSET_MANIFEST_SCHEMA = "longworld.tokenizer-assets.v1"

_COMMIT_SHA = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_TOKENIZER_ASSET_NAMES = frozenset(
    {
        "added_tokens.json",
        "chat_template.jinja",
        "config.json",
        "merges.txt",
        "sentencepiece.bpe.model",
        "special_tokens_map.json",
        "spiece.model",
        "tokenizer.json",
        "tokenizer.model",
        "tokenizer_config.json",
        "vocab.json",
    }
)
_ENCODING_ASSET_NAMES = frozenset(
    {
        "merges.txt",
        "sentencepiece.bpe.model",
        "spiece.model",
        "tokenizer.json",
        "tokenizer.model",
        "vocab.json",
    }
)


class TokenizerAssetError(ValueError):
    """A pinned tokenizer snapshot cannot produce a trustworthy manifest."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def tokenizer_asset_manifest(
    snapshot_dir: Path,
    model_id: str,
    revision: str,
) -> dict[str, Any]:
    """Return a canonical inventory of tokenizer-defining snapshot files."""
    if not model_id or _COMMIT_SHA.fullmatch(revision) is None:
        raise TokenizerAssetError("tokenizer model id or revision is malformed")
    if not snapshot_dir.is_dir():
        raise TokenizerAssetError("tokenizer snapshot directory is unavailable")

    files: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.iterdir(), key=lambda item: item.name):
        if path.name not in _TOKENIZER_ASSET_NAMES or not path.is_file():
            continue
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise TokenizerAssetError(
                f"tokenizer asset cannot be read: {path.name}"
            ) from error
        files.append(
            {
                "path": path.name,
                "size_bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )
    present = {item["path"] for item in files}
    if "tokenizer_config.json" not in present:
        raise TokenizerAssetError("tokenizer config asset is missing")
    if present.isdisjoint(_ENCODING_ASSET_NAMES):
        raise TokenizerAssetError("tokenizer encoding asset is missing")
    return {
        "schema_version": TOKENIZER_ASSET_MANIFEST_SCHEMA,
        "model_id": model_id,
        "revision": revision.lower(),
        "files": files,
    }


def tokenizer_asset_manifest_sha256(manifest: dict[str, Any]) -> str:
    """Digest a manifest using the repository's canonical JSON encoding."""
    payload = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resolved_tokenizer_asset_manifest_sha256(model_id: str, revision: str) -> str:
    """Resolve and digest the exact local Hugging Face tokenizer snapshot."""
    if _COMMIT_SHA.fullmatch(revision) is None:
        raise TokenizerAssetError("tokenizer revision is malformed")
    with sanitized_attestation_environment():
        from transformers.utils.hub import cached_file

        resolved = cached_file(
            model_id,
            "tokenizer_config.json",
            revision=revision,
            local_files_only=True,
        )
    if not resolved:
        raise TokenizerAssetError("tokenizer snapshot cannot be resolved")
    config_path = Path(resolved)
    parts = config_path.parts
    try:
        snapshot_index = parts.index("snapshots")
        resolved_revision = parts[snapshot_index + 1]
    except (ValueError, IndexError) as error:
        raise TokenizerAssetError(
            "tokenizer is not loaded from a pinned snapshot"
        ) from error
    if resolved_revision.lower() != revision.lower():
        raise TokenizerAssetError("tokenizer snapshot revision does not match pin")
    manifest = tokenizer_asset_manifest(config_path.parent, model_id, revision)
    return tokenizer_asset_manifest_sha256(manifest)
