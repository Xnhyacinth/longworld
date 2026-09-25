"""Resume a Wiki native batch through the data-directory symlink."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_unified_synthesis_batch import _wiki_resume_output


def test_wiki_resume_preserves_recorded_absolute_path_spelling(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    output = actual / "native"
    index = alias / "native/batch/jobs/job-1/sample_index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text("", encoding="utf-8")
    inventory = output / "batch/inventory_inputs.json"
    inventory.write_text(json.dumps({"inputs": [{"index": str(index)}]}))

    assert _wiki_resume_output(output) == alias / "native"


def test_wiki_resume_rejects_inventory_from_another_output(tmp_path: Path) -> None:
    output = tmp_path / "native"
    inventory = output / "batch/inventory_inputs.json"
    inventory.parent.mkdir(parents=True)
    other = tmp_path / "other/batch/jobs/job-1/sample_index.jsonl"
    other.parent.mkdir(parents=True)
    other.write_text("", encoding="utf-8")
    inventory.write_text(json.dumps({"inputs": [{"index": str(other)}]}))

    with pytest.raises(ValueError, match="another batch"):
        _wiki_resume_output(output)
