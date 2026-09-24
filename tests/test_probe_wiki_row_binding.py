"""Optional integration checks for a locally generated, pinned reader export."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts import probe_wiki_row_binding

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/p80_wiki_task_scale_v1.json"
OUTPUT = ROOT / "data/candidates/p80_wiki_row_binding_probe_v3"


def _local_output() -> Path:
    if not (OUTPUT / "manifest.json").is_file():
        pytest.skip("optional locally generated row-binding export is absent")
    return OUTPUT


def test_export_reverifies_final_reader_rows_and_source_pins():
    result = probe_wiki_row_binding.verify_output(CONFIG, _local_output())
    assert result == {"verified_rows": 16, "verified_source_groups": 18}


def test_export_rejects_wrong_split_local_row_index_even_with_rehashed_file(
    tmp_path: Path,
):
    shutil.copytree(_local_output(), tmp_path / "copy")
    copied = tmp_path / "copy"
    index_path = copied / "sample_index.jsonl"
    lines = index_path.read_text().splitlines()
    first = json.loads(lines[0])
    first["row_index"] = 1
    lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True)
    index_path.write_text("\n".join(lines) + "\n")
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files_sha256"]["sample_index.jsonl"] = hashlib.sha256(
        index_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n"
    )
    with pytest.raises(ValueError, match="final reader mismatch"):
        probe_wiki_row_binding.verify_output(CONFIG, copied)
