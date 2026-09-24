"""Source-backed split preflight and receipt integrity for P76 batch jobs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_p76_source_batch import (
    CONFIG_SCHEMA,
    RECEIPT_SCHEMA,
    _code_bindings,
    _dump,
    _existing_receipt,
    _job_id,
    _sha,
    _verify_export,
    preflight,
)


def _write(path: Path, value: object) -> Path:
    path.write_text(_dump(value) + "\n", encoding="utf-8")
    return path


def _snapshot(tmp_path: Path, stem: str, title: str, *, revid: int = 1) -> dict:
    path = _write(
        tmp_path / f"{stem}.json",
        {
            "snapshot_id": f"snapshot_{stem}",
            "source": {"revisions": {title: revid}},
            "documents": [
                {
                    "doc_id": stem,
                    "title": title,
                    "text": "",
                    "revision_url": f"https://en.wikipedia.org/w/index.php?oldid={revid}",
                }
            ],
        },
    )
    return {
        "path": str(path),
        "sha256": _sha(path),
        "snapshot_id": f"snapshot_{stem}",
        "revisions": {title: revid},
    }


def _job(name: str, source: dict, title: str, split: str) -> dict:
    return {
        "name": name,
        "recipe": "wiki_table_scan",
        "snapshot": source,
        "domain": "test",
        "topic": "test_table",
        "split": split,
        "max_tasks": 2,
        "length_policy": "native_whole_pages",
        "table_title": title,
    }


def _config(
    tmp_path: Path, jobs: list[dict], prior_rows: list[dict] | None = None
) -> Path:
    prior = tmp_path / "prior.jsonl"
    prior.write_text(
        "".join(_dump(row) + "\n" for row in (prior_rows or [])),
        encoding="utf-8",
    )
    return _write(
        tmp_path / "config.json",
        {
            "schema": CONFIG_SCHEMA,
            "prior_source_manifest": {"path": str(prior), "sha256": _sha(prior)},
            "jobs": jobs,
        },
    )


def test_real_five_job_config_is_source_backed_and_stable() -> None:
    root = Path(__file__).resolve().parents[1]
    config = root / "configs/p76_source_batch_v1.json"
    payload = json.loads(config.read_text())
    inputs = [
        payload["prior_source_manifest"]["path"],
        *[job["snapshot"]["path"] for job in payload["jobs"]],
    ]
    if any(not (root / path).is_file() for path in inputs):
        pytest.skip("pinned local source artifacts are unavailable")
    _payload, digest, jobs, code = preflight(config)
    assert len(jobs) == 5
    assert len({job["snapshot"]["snapshot_id"] for job in jobs}) == 4
    assert len({job["job_id"] for job in jobs}) == 5
    assert len(digest) == 64 and len(code) >= 10
    assert all(
        job["job_id"] == _job_id({k: v for k, v in job.items() if k != "job_id"})
        for job in jobs
    )


def test_source_title_component_cannot_cross_splits(tmp_path: Path) -> None:
    left = _snapshot(tmp_path, "left", "Shared page")
    right = _snapshot(tmp_path, "right", "Shared page", revid=2)
    config = _config(
        tmp_path,
        [
            _job("left_scan", left, "Shared page", "train"),
            _job("right_scan", right, "Shared page", "eval"),
        ],
    )
    with pytest.raises(ValueError, match="connected source titles"):
        preflight(config)


def test_prior_opposite_split_and_source_hash_fail_before_workers(
    tmp_path: Path,
) -> None:
    source = _snapshot(tmp_path, "source", "Prior page")
    job = _job("source_scan", source, "Prior page", "train")
    prior = [{"split": "eval", "revisions": {"Prior page": 900}}]
    with pytest.raises(ValueError, match="opposite prior split"):
        preflight(_config(tmp_path, [job], prior))
    clean = _config(tmp_path, [job])
    Path(source["path"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        preflight(clean)


def test_duplicate_source_recipe_is_rejected_but_distinct_recipes_can_share_group(
    tmp_path: Path,
) -> None:
    source = _snapshot(tmp_path, "shared", "Table")
    first = _job("first_scan", source, "Table", "eval")
    duplicate = _job("second_scan", source, "Table", "eval")
    with pytest.raises(ValueError, match="duplicate source/recipe"):
        preflight(_config(tmp_path, [first, duplicate]))
    pair = {
        **first,
        "name": "shared_pair",
        "recipe": "wiki_table_pair",
        "length_policy": "paired_32k_64k_or_native",
    }
    del pair["table_title"]
    assert len(preflight(_config(tmp_path, [first, pair]))[2]) == 2


def test_receipt_rejects_mutated_output(tmp_path: Path) -> None:
    source = _snapshot(tmp_path, "receipt", "Table")
    job = _job("receipt_scan", source, "Table", "train")
    job["job_id"] = _job_id(job)
    directory = tmp_path / "job"
    directory.mkdir()
    data = directory / "train.jsonl"
    data.write_text('{"sample_id":"one"}\n', encoding="utf-8")
    manifest = {
        "source_group": source["snapshot_id"],
        "split": "train",
        "domain": "test",
        "topic": "test_table",
        "table_title": "Table",
        "train_ready": False,
        "candidate_rows": 1,
        "independent_tasks": 1,
        "rejected_rows": 0,
        "files_sha256": {"train.jsonl": _sha(data)},
    }
    _write(directory / "manifest.json", manifest)
    files = {
        "train.jsonl": _sha(data),
        "manifest.json": _sha(directory / "manifest.json"),
    }
    code = _code_bindings()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "job_id": job["job_id"],
        "job": job,
        "config_sha256": "test-config",
        "code_sha256": code,
        "snapshot_sha256": source["sha256"],
        "files_sha256": files,
        "candidate_rows": 1,
        "independent_tasks": 1,
        "rejected_rows": 0,
        "train_ready": False,
    }
    _write(directory / "receipt.json", receipt)
    assert _existing_receipt(directory, job, "test-config", code) == receipt
    data.write_text('{"sample_id":"changed"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="export file hash drift"):
        _existing_receipt(directory, job, "test-config", code)


def test_pool_job_records_partial_rejects_without_discarding_accepted_rows(
    tmp_path: Path,
) -> None:
    source = _snapshot(tmp_path, "partial", "Table")
    job = _job("partial_scan", source, "Table", "train")
    job["job_id"] = _job_id(job)
    directory = tmp_path / "export"
    directory.mkdir()
    data = directory / "train.jsonl"
    data.write_text('{"messages":[]}\n', encoding="utf-8")
    _write(
        directory / "manifest.json",
        {
            "source_group": source["snapshot_id"],
            "split": "train",
            "domain": "test",
            "topic": "test_table",
            "table_title": "Table",
            "train_ready": False,
            "candidate_rows": 1,
            "independent_tasks": 1,
            "rejected_rows": 2,
            "files_sha256": {"train.jsonl": _sha(data)},
        },
    )
    with pytest.raises(ValueError, match="export admission failed"):
        _verify_export(directory, job)
    job["allow_task_rejects"] = True
    manifest, _ = _verify_export(directory, job)
    assert manifest["candidate_rows"] == 1
    assert manifest["rejected_rows"] == 2
