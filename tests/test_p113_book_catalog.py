"""Contract checks for catalog-driven, work-scoped book planning."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts.p113_book_catalog import plan
from scripts.p113_book_identity import identity_header


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    catalog = tmp_path / "catalog.csv.gz"
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
    plain = tmp_path / "catalog.csv"
    with plain.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for ebook_id, title, lang, subject in (
            (101, "The Hidden Island: An Adventure", "en", "Adventure stories"),
            (102, "The Hidden Island: New Edition", "en", "Adventure stories"),
            (103, "The Locked Room", "en", "Detective and mystery stories"),
            (104, "La chambre", "fr", "Detective and mystery stories"),
        ):
            writer.writerow(
                {
                    "Text#": ebook_id,
                    "Type": "Text",
                    "Issued": "2020-01-01",
                    "Title": title,
                    "Language": lang,
                    "Authors": "Smith, John",
                    "Subjects": subject,
                    "LoCC": "PR",
                    "Bookshelves": "",
                }
            )
    catalog.write_bytes(gzip.compress(plain.read_bytes(), mtime=0))
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": "longworld.p113-book-catalog-request.v1",
                "catalog_url": "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz",
                "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                "mirror_base": "https://gutenberg.pglaf.org/cache/epub",
                "policy_url": "https://www.gutenberg.org/policy/license",
                "max_candidates": 4,
                "max_frozen_books": 2,
                "max_bytes_per_book": 2000000,
                "requests_per_second": 0.5,
                "split_salt": "book-test",
                "eval_fraction": 0.2,
                "exclude_ebook_ids": [],
                "topics": {
                    "adventure": ["adventure stories"],
                    "detective": ["detective and mystery stories"],
                },
            }
        )
    )
    return config, catalog, tmp_path / "plan.json"


def test_catalog_deduplicates_work_and_replays(tmp_path: Path) -> None:
    config, catalog, output = _fixture(tmp_path)
    result = plan(config, catalog, output)
    assert result["catalog_rows"] == 4
    assert result["eligible_unique_works"] == 2
    assert result["planned_candidates"] == 2
    assert {item["ebook_id"] for item in result["candidates"]} == {101, 103}
    assert result["rejections"]["duplicate_catalog_work_variant"] == 1
    assert plan(config, catalog, output, verify_only=True) == result
    modified = bytearray(catalog.read_bytes())
    modified[-1] ^= 1
    catalog.write_bytes(modified)
    with pytest.raises(ValueError, match="catalog SHA"):
        plan(config, catalog, output, verify_only=True)


def test_catalog_identity_requires_title_and_author() -> None:
    book = {"title": "The Hidden Island: An Adventure", "author": "Smith, John"}
    assert identity_header(b"Title: The Hidden Island\nAuthor: John Smith\n", book) == (
        "The Hidden Island",
        "John Smith",
    )
    with pytest.raises(ValueError, match="catalog_author_header_mismatch"):
        identity_header(b"Title: The Hidden Island\nAuthor: Jane Doe\n", book)
