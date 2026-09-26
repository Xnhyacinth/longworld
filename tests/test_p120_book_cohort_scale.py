"""Full-catalog author closure and frozen-prior replay checks for P120."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts.p120_book_cohort_scale import audit_source, plan


def test_unselected_coauthor_work_blocks_new_source(tmp_path: Path) -> None:
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
        for ebook_id, author, subject in (
            (1, "Alpha, Ann", "Adventure stories"),
            (2, "Alpha, Ann; Beta, Bob", "No requested subject"),
            (3, "Beta, Bob", "Adventure stories"),
            (4, "Gamma, Gail", "Adventure stories"),
            (5, "Delta, Dan", "Adventure stories"),
            (6, "Epsilon, Eve", "Adventure stories"),
        ):
            writer.writerow(
                {
                    "Text#": ebook_id,
                    "Type": "Text",
                    "Issued": "2020-01-01",
                    "Title": f"Work {ebook_id}",
                    "Language": "en",
                    "Authors": author,
                    "Subjects": subject,
                    "LoCC": "PR",
                    "Bookshelves": "",
                }
            )
    catalog = tmp_path / "catalog.csv.gz"
    catalog.write_bytes(gzip.compress(plain.read_bytes(), mtime=0))
    source_path = tmp_path / "source.json"
    source_path.write_text(
        json.dumps(
            {
                "schema": "longworld.p113-book-source-freeze.v1",
                "records": [
                    {
                        "ebook_id": 1,
                        "catalog_work_key": "alpha|work 1",
                        "raw_sha256": "prior-raw",
                        "body_sha256": "prior-body",
                    }
                ],
            }
        )
    )
    attempts_path = tmp_path / "attempts.json"
    attempts_path.write_text(
        json.dumps(
            {
                "schema": "longworld.p114-book-mirror-attempts.v1",
                "records": [{"ebook_id": 5}],
            }
        )
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": "longworld.p113-book-catalog-request.v1",
                "p114_scale_schema": "longworld.p114-book-scale-request.v1",
                "p120_schema": "longworld.p120-book-cohort-request.v1",
                "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                "prior_source_manifests": [str(source_path)],
                "prior_attempt_manifests": [str(attempts_path)],
                "prior_source_manifest": str(tmp_path / "plan/prior_manifest.json"),
                "mirror_base": "https://gutenberg.pglaf.org/cache/epub",
                "max_candidates": 6,
                "max_attempts": 1,
                "workers": 2,
                "requests_per_second": 0.5,
                "split_salt": "test-full-catalog",
                "eval_fraction": 0.2,
                "topics": {"adventure": ["adventure stories"]},
            }
        )
    )
    output = tmp_path / "plan"
    result = plan(config, catalog, output)
    assert {item["ebook_id"] for item in result["candidates"]} == {4, 6}
    assert result["excluded"]["prior_author_component"] == 2
    assert result["excluded"]["prior_mirror_attempt"] == 1
    assert plan(config, catalog, output, verify_only=True) == result
    source_dir = tmp_path / "new_source"
    source_dir.mkdir()
    new = {
        "schema": "longworld.p113-book-source-freeze.v1",
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "prior_source_manifest_sha256": hashlib.sha256(
            (output / "prior_manifest.json").read_bytes()
        ).hexdigest(),
        "records": [
            {
                "ebook_id": 4,
                "source_group": "gutenberg-4",
                "catalog_work_key": "gamma|work 4",
                "raw_sha256": "new-raw",
                "body_sha256": "new-body",
                "split": "train",
            }
        ],
    }
    (source_dir / "manifest.json").write_text(json.dumps(new))
    audited = audit_source(config, catalog, source_dir, tmp_path / "audit.json")
    assert audited["new_source_worlds"] == 1
    assert all(not overlap for overlap in audited["overlaps"].values())
    new["records"][0]["body_sha256"] = "prior-body"
    (source_dir / "manifest.json").write_text(json.dumps(new))
    with pytest.raises(ValueError, match="source overlap"):
        audit_source(config, catalog, source_dir, tmp_path / "audit_collision.json")
    source_path.write_text(source_path.read_text() + " ")
    with pytest.raises(ValueError, match="frozen replay differs"):
        plan(config, catalog, output, verify_only=True)
