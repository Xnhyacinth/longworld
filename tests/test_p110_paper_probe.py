"""A transport-stopped cohort may only expose already verified source packets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import p110_paper_probe as probe


def test_partial_fetch_requires_exact_frozen_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    receipt = {
        "work_id": "2601.00001",
        "query": "cat:math.AG",
        "split": "train",
        "license_status": "not_reported_by_atom",
        "license_uri": [],
        "archive_bytes": 100,
    }
    source = tmp_path / "fetch.json"
    source.write_text(
        json.dumps(
            {
                "schema": "longworld.p105-paper-acquisition.v1.fetch-result",
                "status": "transport_blocked",
                "config_sha256": "config-hash",
                "catalog_manifest_sha256": "catalog-hash",
                "content_use": "local_research_only_no_redistribution",
                "selected_works": 2,
                "frozen_works": 1,
                "frozen_source_archives": 2,
                "archive_bytes": 100,
                "source_receipts": [receipt],
                "errors": [{"status": "transport_failure"}],
            }
        )
    )
    acquisition = tmp_path / "acquire.json"
    acquisition.write_text("acquire")
    template = tmp_path / "template.json"
    template.write_text(json.dumps({"schema": "longworld.p96-paper-reference-qa.v1"}))
    prior = tmp_path / "prior.json"
    prior.write_text("prior")
    paths = {
        name: path
        for name, path in (
            ("fetch", source),
            ("acquire", acquisition),
            ("template", template),
            ("prior", prior),
        )
    }
    monkeypatch.setattr(probe, "_pin", lambda pin: paths[pin["path"]])
    monkeypatch.setattr(
        probe,
        "acquire_config",
        lambda _path: (
            {"catalog_manifest": {"sha256": "catalog-hash"}},
            {"selected_works": [{"work_id": "2601.00001"}, {"work_id": "2601.00002"}]},
        ),
    )
    monkeypatch.setattr(
        probe,
        "_sha",
        lambda data: (
            "config-hash" if data == b"acquire" else hashlib.sha256(data).hexdigest()
        ),
    )
    monkeypatch.setattr(
        probe,
        "_existing_inventory",
        lambda _directory, work: (
            (
                receipt,
                {
                    "family_id": "p105-arxiv-2601.00001",
                    "inventory": {"path": "inventory", "sha256": "hash"},
                },
            )
            if work["work_id"] == "2601.00001"
            else None
        ),
    )

    class Pool:
        def __init__(self, max_workers: int) -> None:
            assert max_workers == 4

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def map(self, _fn, _inputs):
            return [
                (
                    {
                        "work_id": "2601.00001",
                        "status": "task_source_candidate",
                        "cross_file_links": 1,
                    },
                    {"family_id": "p104-arxiv-2601.00001", "split": "train"},
                )
            ]

    monkeypatch.setattr(probe, "ProcessPoolExecutor", Pool)
    config = {
        "schema": probe.SCHEMA + ".config",
        "workers": 4,
        "hard_archive_bytes": 250_000_000,
        "fetch_manifest": {"path": "fetch"},
        "acquire_config": {"path": "acquire"},
        "qa_template": {"path": "template"},
        "prior_candidate_index": {
            "path": "prior",
            "sha256": hashlib.sha256(b"prior").hexdigest(),
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    output = tmp_path / "output"
    result = probe.run(config_path, output, False)
    assert result["frozen_works"] == 1
    assert result["source_shape_non_cs_works"] == 1
    assert probe.run(config_path, output, True) == result
    source.write_text(
        source.read_text().replace('"archive_bytes": 100', '"archive_bytes": 101')
    )
    with pytest.raises(ValueError, match="frozen source receipts"):
        probe.build(config_path, output)
