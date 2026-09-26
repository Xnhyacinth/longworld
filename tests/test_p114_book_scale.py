"""Author-component split and prior-source exclusion behavior."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts.p114_book_scale import _author_keys, _components, download, plan


def test_author_component_links_coauthors_only_in_planned_corpus() -> None:
    rows = [
        {
            "Text#": "1",
            "Type": "Text",
            "Language": "en",
            "Authors": "Alpha, Ann; Beta, Bob",
        },
        {"Text#": "2", "Type": "Text", "Language": "en", "Authors": "Beta, Bob"},
        {"Text#": "3", "Type": "Text", "Language": "en", "Authors": "Gamma, Gail"},
        {
            "Text#": "4",
            "Type": "Text",
            "Language": "en",
            "Authors": "Beta, Bob; Gamma, Gail",
        },
    ]
    roots, keys = _components(rows, {1, 2, 3})
    assert roots[keys[1][0]] == roots[keys[2][0]]
    assert roots[keys[1][0]] != roots[keys[3][0]]
    roots_with_bridge, keys_with_bridge = _components(rows, {1, 2, 3, 4})
    assert (
        roots_with_bridge[keys_with_bridge[1][0]]
        == roots_with_bridge[keys_with_bridge[3][0]]
    )
    assert _author_keys("Anonymous") == ()


def test_plan_excludes_prior_author_component(tmp_path: Path) -> None:
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
    with plain.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for ebook_id, author in (
            (1, "Alpha, Ann"),
            (2, "Beta, Bob; Alpha, Ann"),
            (3, "Beta, Bob"),
            (4, "Gamma, Gail"),
            (5, "Delta, Dan"),
        ):
            writer.writerow(
                {
                    "Text#": ebook_id,
                    "Type": "Text",
                    "Issued": "2020-01-01",
                    "Title": f"Adventure {ebook_id}",
                    "Language": "en",
                    "Authors": author,
                    "Subjects": "Adventure stories",
                    "LoCC": "PR",
                    "Bookshelves": "",
                }
            )
    catalog = tmp_path / "catalog.csv.gz"
    catalog.write_bytes(gzip.compress(plain.read_bytes(), mtime=0))
    prior = tmp_path / "prior.json"
    prior.write_text(
        json.dumps(
            {
                "schema": "longworld.p113-book-source-freeze.v1",
                "records": [{"ebook_id": 1, "catalog_work_key": "alpha|adventure 1"}],
            }
        )
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": "longworld.p113-book-catalog-request.v1",
                "p114_scale_schema": "longworld.p114-book-scale-request.v1",
                "catalog_url": "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz",
                "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                "mirror_base": "https://gutenberg.pglaf.org/cache/epub",
                "policy_url": "https://www.gutenberg.org/policy/license",
                "prior_source_manifest": str(prior),
                "prior_unified_dir": str(tmp_path / "unused"),
                "max_candidates": 5,
                "max_attempts": 1,
                "max_frozen_books": 2,
                "max_books_per_author_component": 2,
                "max_bytes_per_book": 2000000,
                "requests_per_second": 0.5,
                "split_salt": "test-author-component",
                "eval_fraction": 0.2,
                "exclude_ebook_ids": [],
                "topics": {"adventure_fiction": ["adventure stories"]},
            }
        )
    )
    result = plan(config, catalog, tmp_path / "planned")
    assert {item["ebook_id"] for item in result["candidates"]} == {4, 5}
    assert result["excluded"]["prior_work_or_ebook"] == 1
    assert result["excluded"]["prior_author_component"] == 2
    assert plan(config, catalog, tmp_path / "planned", verify_only=True) == result


def test_download_replay_checks_bytes_and_transport_only_attempt(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": "longworld.p113-book-catalog-request.v1",
                "p114_scale_schema": "longworld.p114-book-scale-request.v1",
                "mirror_base": "https://gutenberg.pglaf.org/cache/epub",
                "max_attempts": 2,
                "max_frozen_books": 2,
                "max_books_per_author_component": 2,
                "requests_per_second": 0.5,
                "max_bytes_per_book": 10,
            }
        )
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema": "longworld.p114-book-scale-plan.v1",
                "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                "candidates": [
                    {"ebook_id": 101, "topic": "adventure", "split": "train"},
                    {"ebook_id": 102, "topic": "ghost", "split": "eval"},
                ],
            }
        )
    )
    output = tmp_path / "attempts"
    (output / "attempts").mkdir(parents=True)
    raw = b"a book"
    (output / "attempts/pg101.txt").write_bytes(raw)
    receipt = {
        "schema": "longworld.p114-book-mirror-attempts.v1",
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "attempted": 2,
        "records": [
            {
                "ebook_id": 101,
                "url": "https://gutenberg.pglaf.org/cache/epub/101/pg101.txt",
                "topic": "adventure",
                "split": "train",
                "status": "downloaded",
                "raw_file": "attempts/pg101.txt",
                "raw_bytes": len(raw),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
            },
            {
                "ebook_id": 102,
                "url": "https://gutenberg.pglaf.org/cache/epub/102/pg102.txt",
                "topic": "ghost",
                "split": "eval",
                "status": "http_404",
            },
        ],
    }
    (output / "download_manifest.json").write_text(json.dumps(receipt))
    assert download(config, plan_path, output, verify_only=True) == receipt
    (output / "attempts/pg101.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="raw bytes differ"):
        download(config, plan_path, output, verify_only=True)
