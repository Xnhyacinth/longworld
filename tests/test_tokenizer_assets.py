from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from longworld.core.tokenizer_assets import (
    TokenizerAssetError,
    resolved_tokenizer_asset_manifest_sha256,
    tokenizer_asset_manifest,
    tokenizer_asset_manifest_sha256,
)


def _write_snapshot(snapshot: Path) -> None:
    snapshot.mkdir(parents=True)
    (snapshot / "tokenizer_config.json").write_text(
        '{"tokenizer_class":"Qwen2TokenizerFast"}', encoding="utf-8"
    )
    (snapshot / "tokenizer.json").write_text('{"version":"1.0"}', encoding="utf-8")
    (snapshot / "merges.txt").write_text("#version: 0.2\na b\n", encoding="utf-8")
    (snapshot / "vocab.json").write_text('{"a":0,"b":1}', encoding="utf-8")
    (snapshot / "README.md").write_text("not loaded by the tokenizer", encoding="utf-8")


def test_tokenizer_asset_manifest_is_content_addressed_and_deterministic(
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    snapshot = tmp_path / "snapshots" / revision
    _write_snapshot(snapshot)

    manifest = tokenizer_asset_manifest(snapshot, "Qwen/Test", revision)
    digest = tokenizer_asset_manifest_sha256(manifest)

    assert manifest == tokenizer_asset_manifest(snapshot, "Qwen/Test", revision)
    assert [item["path"] for item in manifest["files"]] == [
        "merges.txt",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
    ]
    assert (
        manifest["files"][1]["sha256"]
        == hashlib.sha256((snapshot / "tokenizer.json").read_bytes()).hexdigest()
    )
    assert len(digest) == 64

    (snapshot / "tokenizer.json").write_text('{"version":"mutated"}', encoding="utf-8")
    assert (
        tokenizer_asset_manifest_sha256(
            tokenizer_asset_manifest(snapshot, "Qwen/Test", revision)
        )
        != digest
    )


def test_tokenizer_asset_manifest_accepts_llama_sentencepiece_assets(
    tmp_path: Path,
) -> None:
    revision = "b" * 40
    snapshot = tmp_path / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (snapshot / "tokenizer.model").write_bytes(b"sentencepiece-model")

    manifest = tokenizer_asset_manifest(snapshot, "meta-llama/Test", revision)

    assert [item["path"] for item in manifest["files"]] == [
        "tokenizer.model",
        "tokenizer_config.json",
    ]


def test_tokenizer_asset_manifest_fails_closed_without_encoding_assets(
    tmp_path: Path,
) -> None:
    revision = "c" * 40
    snapshot = tmp_path / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "tokenizer_config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(TokenizerAssetError, match="encoding asset"):
        tokenizer_asset_manifest(snapshot, "Qwen/Test", revision)


def test_resolved_tokenizer_asset_digest_is_fresh_after_snapshot_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "d" * 40
    snapshot = tmp_path / "models--Qwen--Test" / "snapshots" / revision
    _write_snapshot(snapshot)
    monkeypatch.setattr(
        "transformers.utils.hub.cached_file",
        lambda *_args, **_kwargs: str(snapshot / "tokenizer_config.json"),
    )

    before = resolved_tokenizer_asset_manifest_sha256("Qwen/Test", revision)
    (snapshot / "tokenizer.json").write_text(
        '{"version":"changed-after-first-resolution"}', encoding="utf-8"
    )

    assert resolved_tokenizer_asset_manifest_sha256("Qwen/Test", revision) != before
