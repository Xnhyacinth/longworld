"""Work-level deduplication and source-byte bound for P110 paper intake."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import p110_paper_cohort as cohort


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _work(work_id: str, query: str) -> dict:
    return {
        "work_id": work_id,
        "category_query": query,
        "split": cohort._split(work_id),
        "versions": ["v1", "v2"],
        "latest_version": 2,
        "license_status": "not_reported_by_atom",
        "license_uri": [],
    }


def test_cohort_rejects_prior_duplicate_and_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cohort, "ROOT", tmp_path)
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    shards = []
    pins = {}
    entries_for_query = {}
    for index, works in enumerate(
        [
            [
                _work("2601.00001", "cat:a.AA"),
                _work("2601.00002", "cat:a.AA"),
                _work("2601.00003", "cat:b.AA"),
            ],
            [_work("2601.00003", "cat:c.AA"), _work("2601.00004", "cat:c.AA")],
        ]
    ):
        name = f"metadata_config_{index:02d}.json"
        shard_config = plan_dir / name
        queries = ["cat:a.AA", "cat:b.AA"] if index == 0 else ["cat:c.AA", "cat:d.AA"]
        shard_config.write_text(json.dumps({"queries": queries, "page_size": 100}))
        shards.append(name)
        source = tmp_path / f"metadata_{index}.json"
        source.write_text(
            json.dumps(
                {
                    "schema": "longworld.p105-paper-catalog.v1.result",
                    "status": "complete",
                    "config_sha256": _sha(shard_config),
                    "query_pages": [{"query": query} for query in queries],
                    "metadata_entries": 100,
                    "selected_works": works,
                }
            )
        )
        pins[f"metadata_{index}.json"] = source
        for work in works:
            entries_for_query.setdefault(work["category_query"], []).append(
                {
                    **{
                        key: value
                        for key, value in work.items()
                        if key not in {"split", "versions"}
                    },
                    "latest_version": 2,
                }
            )
    plan = plan_dir / "manifest.json"
    plan.write_text(
        json.dumps(
            {
                "schema": "longworld.p110-paper-autocatalog.v1.result",
                "shards": shards,
                "files_sha256": {name: _sha(plan_dir / name) for name in shards},
            }
        )
    )
    pins["plan/manifest.json"] = plan
    prior = tmp_path / "prior.json"
    prior.write_text(
        json.dumps(
            {"selected_works": [_work("2601.00001", "cat:a.AA")], "selected_count": 1}
        )
    )
    pins["prior.json"] = prior
    monkeypatch.setattr(cohort, "_pin", lambda pin: pins[pin["path"]])
    monkeypatch.setattr(
        cohort,
        "_read_page",
        lambda _dir, _i, query, _url: (
            {"query": query},
            entries_for_query.get(query, []),
        ),
    )
    config = {
        "schema": cohort.SCHEMA + ".config",
        "work_limit": 2,
        "max_source_bytes": cohort.MAX_SOURCE_BYTES,
        "p105_soft_stop_bytes": cohort.MAX_SOURCE_BYTES
        - 2 * cohort.P105_RESPONSE_BOUND,
        "plan_manifest": {"path": "plan/manifest.json", "sha256": _sha(plan)},
        "prior_catalog_manifest": {"path": "prior.json", "sha256": _sha(prior)},
        "metadata_manifests": [
            {
                "path": f"metadata_{index}.json",
                "sha256": _sha(pins[f"metadata_{index}.json"]),
            }
            for index in range(2)
        ],
        "qa_template": {"path": "prior.json", "sha256": _sha(prior)},
        "prior_candidate_index": {"path": "prior.json", "sha256": _sha(prior)},
        "user_agent": "test@example.org",
        "request_interval_seconds": 3.2,
        "permit_metadata_failure": False,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    output = tmp_path / "cohort"
    result = cohort.run(path, output, False)
    assert result["selected_novel_works"] == 2
    assert result["admission_status"] == {
        "cross_shard_duplicate_work": 1,
        "global_work_cap": 1,
        "prior_p105_work": 1,
        "selected_for_source_fetch": 2,
    }
    assert cohort.run(path, output, True) == result
    acquire = json.loads((output / "acquire_config.json").read_text())
    assert (
        acquire["max_archive_bytes_total"] + 2 * cohort.P105_RESPONSE_BOUND
        == cohort.MAX_SOURCE_BYTES
    )
    (output / "work_ledger.jsonl").write_text("tampered\n")
    with pytest.raises(ValueError, match="replay drift"):
        cohort.run(path, output, True)
