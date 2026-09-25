"""Resampling keeps exact-answer and shortcut checks intact."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import p110_paper_target_resample as resample


def test_cross_file_target_duplicate_answer_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"frozen-source")
    monkeypatch.setattr(resample, "ROOT", tmp_path)
    source = (
        "A detailed comparison of model behavior over multiple experimental settings "
        "is given in \\ref{sec:target}.\n"
    )
    target = "\\section{Unique Target Heading}\\label{sec:target}\n"
    files = {"results.tex": source, "methods.tex": target}
    monkeypatch.setattr(resample, "load_text_tar", lambda _path: files)
    monkeypatch.setattr(resample, "_dedupe", lambda value: (value, 0))
    capacity = {
        "work_id": "2601.00001",
        "category_query": "cat:stat.CO",
        "split": "train",
        "latest_archive": {
            "path": "source.tar",
            "sha256": hashlib.sha256(b"frozen-source").hexdigest(),
        },
    }
    accepted = resample._scan(capacity)
    assert accepted["counts"]["accepted_raw_tex_reference"] == 1
    files["unrelated.tex"] = (
        "The same title appears elsewhere: Unique Target Heading.\n"
    )
    rejected = resample._scan(capacity)
    assert rejected["counts"]["rejected_alternate_visible_title_occurrence"] == 1
    assert rejected["counts"].get("accepted_raw_tex_reference", 0) == 0
