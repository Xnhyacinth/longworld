"""Reader-only P133 adapter keeps campaign and code pins fail-closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p133_reader_unified_adapter as adapter


def test_campaign_pin_rejects_path_escape() -> None:
    with pytest.raises(ValueError, match="workspace-relative"):
        adapter._load_pin(
            {
                "campaign_manifest": "../elsewhere/manifest.json",
                "campaign_manifest_sha256": "x",
            }
        )


def test_campaign_pin_rejects_wrong_sha() -> None:
    with pytest.raises(ValueError, match="manifest SHA differs"):
        adapter._load_pin(
            {
                "campaign_manifest": "configs/p133_reader_unified_adapter_v1.json",
                "campaign_manifest_sha256": "0" * 64,
            }
        )


def test_verify_rejects_changed_adapter_code(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"compiler_sha256": "stale"}))
    with pytest.raises(ValueError, match="compiler SHA differs"):
        adapter.compile_reader(
            Path("configs/p133_reader_unified_adapter_v1.json"),
            tmp_path,
            verify_only=True,
        )
