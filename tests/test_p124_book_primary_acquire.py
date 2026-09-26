"""Source audit rejects chapter leakage across distinct new books."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import p124_book_primary_acquire as acquire


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def test_new_books_cannot_share_a_chapter_across_splits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acquire, "ROOT", tmp_path)
    cfg = {
        "catalog_sha256": "pinned-catalog",
        "prior_source_manifest": "plan/prior_manifest.json",
        "prior_source_manifests": {"old/manifest.json": "pinned-prior"},
    }
    monkeypatch.setattr(acquire, "_config", lambda path: cfg)
    monkeypatch.setattr(
        acquire,
        "_catalog",
        lambda path, digest: [
            {"Text#": str(ebook), "Authors": author, "Title": f"Book {ebook}"}
            for ebook, author in (
                (1, "Old, Olive"),
                (2, "New, Nora"),
                (3, "Other, Oscar"),
            )
        ],
    )
    monkeypatch.setattr(
        acquire,
        "chapters",
        lambda body: [SimpleNamespace(text=part) for part in body.split("\n")],
    )
    config = tmp_path / "config.json"
    config.write_text("{}")
    plan_path = tmp_path / "plan.json"
    plan = {
        "schema": acquire.PLAN_SCHEMA,
        "p121_schema": "longworld.p121-book-primary-author-plan.v2",
        "config_sha256": _sha(config.read_bytes()),
        "catalog_sha256": "pinned-catalog",
        "candidates": [
            {
                "ebook_id": ebook,
                "split": split,
                "topic": "literature",
                "work_key": f"work-{ebook}",
                "author_keys": [key],
            }
            for ebook, split, key in (
                (2, "train", "new|nora"),
                (3, "eval", "other|oscar"),
            )
        ],
    }
    cfg["max_attempts"] = 2
    plan_path.write_text(json.dumps(plan))
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "old.body.txt").write_text("old-only")
    prior = {
        "schema": acquire.SOURCE_SCHEMA,
        "records": [
            {
                "ebook_id": 1,
                "catalog_work_key": "old-work",
                "raw_sha256": "old-raw",
                "body_sha256": _sha(b"old-only"),
                "body_file": "old.body.txt",
                "split": "train",
            }
        ],
    }
    (old_dir / "manifest.json").write_text(json.dumps(prior))
    cfg["prior_source_manifests"]["old/manifest.json"] = _sha(
        (old_dir / "manifest.json").read_bytes()
    )
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "prior_manifest.json").write_text(json.dumps(prior))
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    records = []
    for ebook, split, key, body in (
        (2, "train", "new|nora", "chapter-two\nshared-chapter"),
        (3, "eval", "other|oscar", "chapter-three\nshared-chapter"),
    ):
        filename = f"{ebook}.body.txt"
        (source_dir / filename).write_text(body)
        records.append(
            {
                **plan["candidates"][ebook - 2],
                "catalog_work_key": f"work-{ebook}",
                "raw_sha256": f"raw-{ebook}",
                "body_sha256": _sha(body.encode()),
                "body_file": filename,
            }
        )
    (source_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": acquire.SOURCE_SCHEMA,
                "config_sha256": _sha(config.read_bytes()),
                "catalog_plan_sha256": _sha(plan_path.read_bytes()),
                "prior_source_manifest_sha256": _sha(
                    (plan_dir / "prior_manifest.json").read_bytes()
                ),
                "records": records,
            }
        )
    )
    with pytest.raises(ValueError, match="exact_new_chapter"):
        acquire.audit_source(
            config,
            plan_path,
            tmp_path / "catalog.csv.gz",
            source_dir,
            tmp_path / "audit.json",
        )
