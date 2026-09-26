"""Behavioral checks for the catalog-scoped sole-author split policy."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts.p121_book_primary_author import plan


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(
    tmp_path: Path, *, prior_conflict: bool = False
) -> tuple[Path, Path, Path]:
    fields = [
        "Text#",
        "Type",
        "Issued",
        "Title",
        "Language",
        "Authors",
        "Subjects",
        "LoCC",
        "Bookshelves",
    ]
    books = [
        (1, "Alpha, Ann", "Adventure stories"),
        (2, "Alpha, Ann; Ink, Ivy [Illustrator]", "Other subject"),
        (3, "Beta, Bob; Ink, Ivy [Illustrator]", "Other subject"),
        (4, "Beta, Bob", "Adventure stories"),
        (5, "Beta, Bob", "Adventure stories"),
        (6, "Gamma, Gail", "Adventure stories"),
        (7, "Delta, Dan; Paint, Pat [Illustrator]", "Adventure stories"),
        (8, "Alpha, Ann", "Adventure stories"),
        (9, "Various", "Adventure stories"),
        (10, "Alpha, Ann", "Other subject"),
        (11, "Carter, Nicholas (House name)", "Adventure stories"),
        (12, "Blackie & Son", "Adventure stories"),
        (13, "Spinners' Club", "Adventure stories"),
        (14, "Alpha, Alice", "Adventure stories"),
    ]
    plain = tmp_path / "catalog.csv"
    with plain.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for ebook_id, author, subject in books:
            writer.writerow(
                {
                    "Text#": ebook_id,
                    "Type": "Text",
                    "Issued": "2020-01-01",
                    "Title": "Work 1" if ebook_id == 14 else f"Work {ebook_id}",
                    "Language": "en",
                    "Authors": author,
                    "Subjects": subject,
                    "LoCC": "PR",
                    "Bookshelves": "",
                }
            )
    catalog = tmp_path / "catalog.csv.gz"
    catalog.write_bytes(gzip.compress(plain.read_bytes(), mtime=0))
    source = tmp_path / "prior_source.json"
    records = [
        {
            "ebook_id": 1,
            "catalog_work_key": "ann alpha|work 1",
            "split": "train",
        }
    ]
    if prior_conflict:
        records.append(
            {
                "ebook_id": 10,
                "catalog_work_key": "alpha|work 10",
                "split": "eval",
            }
        )
    source.write_text(
        json.dumps(
            {
                "schema": "longworld.p113-book-source-freeze.v1",
                "records": records,
            }
        )
    )
    attempts = tmp_path / "prior_attempts.json"
    attempts.write_text(
        json.dumps(
            {
                "schema": "longworld.p114-book-mirror-attempts.v1",
                "records": [],
            }
        )
    )
    old_plan = tmp_path / "historical.json"
    old_plan.write_text(
        json.dumps(
            {
                "schema": "longworld.p114-book-scale-plan.v1",
                "eligible_unique_works": 1,
            }
        )
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": "longworld.p113-book-catalog-request.v1",
                "p114_scale_schema": "longworld.p114-book-scale-request.v1",
                "p121_schema": "longworld.p121-book-primary-author-request.v2",
                "catalog_sha256": _sha(catalog),
                "prior_source_manifests": {str(source): _sha(source)},
                "prior_attempt_manifests": {str(attempts): _sha(attempts)},
                "historical_p120_plan": {
                    "path": str(old_plan),
                    "sha256": _sha(old_plan),
                },
                "prior_source_manifest": str(tmp_path / "plan/prior_manifest.json"),
                "max_candidates": 3,
                "max_attempts": 1,
                "max_books_per_author_component": 2,
                "split_salt": "test-primary-author",
                "eval_fraction": 0.2,
                "adversarial_example_ids": [4, 6, 11, 12, 13],
                "topics": {"adventure": ["adventure stories"]},
            }
        )
    )
    return config, catalog, source


def test_unselected_illustrator_bridge_no_longer_excludes_sole_author(
    tmp_path: Path,
) -> None:
    config, catalog, source = _fixture(tmp_path)
    output = tmp_path / "plan"
    report = plan(config, catalog, output)
    receipt = json.loads((output / "plan.json").read_text())
    assert report["fair_old_graph_eligible_works"] == 5
    assert report["strict_primary_author_eligible_works"] == 3
    assert report["newly_unlocked"] == 2
    assert {item["ebook_id"] for item in receipt["candidates"]} == {4, 5, 6}
    assert (
        len(
            {
                item["split"]
                for item in receipt["candidates"]
                if item["ebook_id"] in {4, 5}
            }
        )
        == 1
    )
    assert {item["author_component"] for item in receipt["candidates"]} == {
        "beta|bob",
        "gamma|gail",
    }
    assert report["prior_direct_key_cross_split"] == []
    assert report["new_prior_author_key_overlap"] == []
    assert report["prior_stored_catalog_work_mismatches"] == [
        {"ebook_id": 1, "stored": "ann alpha|work 1", "catalog": "alpha|work 1"}
    ]
    assert {
        row["ebook_id"]: row["new_rule_rejection"] for row in report["examples"]
    } == {
        4: None,
        6: None,
        11: "parenthetical_ampersand_or_brace_author",
        12: "parenthetical_ampersand_or_brace_author",
        13: "not_surname_given_name_shape",
    }
    assert plan(config, catalog, output, verify_only=True) == report
    source.write_text(source.read_text() + " ")
    with pytest.raises(ValueError, match="pinned prior manifest SHA differs"):
        plan(config, catalog, output, verify_only=True)


def test_existing_prior_author_split_conflict_rejects_plan(tmp_path: Path) -> None:
    config, catalog, _ = _fixture(tmp_path, prior_conflict=True)
    with pytest.raises(ValueError, match="prior direct author keys cross splits"):
        plan(config, catalog, tmp_path / "plan")
