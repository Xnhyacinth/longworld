import hashlib
import json
from pathlib import Path

import pytest

from scripts.p113_legal_cell_plan import SCHEMA, plan, run


def _write(root: Path, name: str, value: dict) -> dict[str, str]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _fixture(root: Path) -> dict:
    snapshot = _write(
        root,
        "data/wiki.json",
        {
            "snapshot_id": "wiki:a",
            "source": {"revisions": {"List of A": "123"}},
            "documents": [{"title": "List of A", "doc_id": "doc:a", "text": "table"}],
        },
    )
    wiki_pool = _write(
        root,
        "configs/wiki_pool.json",
        {
            "schema": "longworld.source-batch-pool.v2",
            "sources": [
                {
                    "domain": "geography",
                    "topic": "a",
                    "split": "train",
                    "snapshot": snapshot,
                }
            ],
        },
    )
    finance_source = _write(
        root,
        "data/finance.json",
        {
            "request": {"issuer_key": "issuer_a", "issuer": {"cik": "1234567890"}},
            "records": [
                {"report_date": f"{year}-12-31", "form": "10-K"}
                for year in range(2021, 2025)
            ],
        },
    )
    finance_catalog = _write(
        root,
        "configs/finance_catalog.json",
        {
            "schema_version": "longworld.finance-taskbank-long-catalog.v1",
            "jobs": [
                {
                    "issuer": "issuer_a",
                    "source_manifest": finance_source["path"],
                    "source_manifest_sha256": finance_source["sha256"],
                }
            ],
        },
    )
    split = _write(
        root,
        "data/finance_split.json",
        {
            "jobs": [
                {"issuer": "issuer_a", "split": "eval", "source_group": "1234567890"}
            ]
        },
    )
    return {
        "schema": SCHEMA,
        "inventories": [
            {"kind": "wiki_pool", "pin": wiki_pool},
            {"kind": "finance_catalog", "pin": finance_catalog, "split_pin": split},
        ],
        "recipes": [
            {
                "name": "wiki_preflight",
                "source_kind": "wiki_snapshot",
                "required_shape": "wiki_list_document",
                "semantic_parameters": {"operation": ["lookup", "scan"]},
            },
            {
                "name": "finance_preflight",
                "source_kind": "annual_reports",
                "required_shape": "four_annual_reports",
                "semantic_parameters": {"endpoint": ["earliest", "latest"]},
            },
        ],
        "renderers": ["native", "natural"],
        "target_lengths": [32768, 65536],
    }


def test_frozen_sources_and_presentation_axes(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    jobs, unsupported, result = plan(config, tmp_path)
    assert (
        result["source_groups"],
        result["semantic_cells"],
        result["planned_jobs"],
        result["unsupported_cells"],
    ) == (2, 4, 16, 4)
    assert {job["split"] for job in jobs if job["source_kind"] == "annual_reports"} == {
        "eval"
    }
    assert {job["split"] for job in jobs if job["source_kind"] == "wiki_snapshot"} == {
        "train"
    }
    assert len({job["semantic_id"] for job in jobs}) == 4
    assert len({job["job_id"] for job in jobs}) == 16
    assert {row["reason"] for row in unsupported} == {"source_kind_mismatch"}

    config["renderers"] = ["native"]
    config["target_lengths"] = [131072]
    shorter, _, _ = plan(config, tmp_path)
    assert {job["semantic_id"] for job in shorter} == {
        job["semantic_id"] for job in jobs
    }

    wiki_path = tmp_path / config["inventories"][0]["pin"]["path"]
    listing = json.loads(wiki_path.read_text())
    listing["sources"][0]["domain"] = "changed_label"
    config["inventories"][0]["pin"] = _write(
        tmp_path, "configs/wiki_pool.json", listing
    )
    relabeled, _, _ = plan(config, tmp_path)
    assert {job["semantic_id"] for job in relabeled} == {
        job["semantic_id"] for job in shorter
    }


def test_source_hash_split_identity_and_replay(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    config_path = tmp_path / "configs/plan.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output = tmp_path / "output"
    result = run(config_path, output, root=tmp_path)
    assert result["native_tasks_executed"] == 0
    assert run(config_path, output, root=tmp_path, verify_only=True) == result
    with pytest.raises(FileExistsError):
        run(config_path, output, root=tmp_path)
    (output / "supported_jobs.jsonl").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="replay differs"):
        run(config_path, output, root=tmp_path, verify_only=True)

    split_path = tmp_path / config["inventories"][1]["split_pin"]["path"]
    split = json.loads(split_path.read_text())
    split["jobs"][0]["source_group"] = "wrong"
    config["inventories"][1]["split_pin"] = _write(
        tmp_path, "data/finance_split.json", split
    )
    with pytest.raises(ValueError, match="CIK mismatch"):
        plan(config, tmp_path)

    snapshot_path = tmp_path / "data/wiki.json"
    snapshot_path.write_text(snapshot_path.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="pin hash mismatch"):
        plan(_fixture_config_without_rewriting_sources(tmp_path), tmp_path)


def test_cross_split_wiki_page_is_not_planned(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    second = _write(
        tmp_path,
        "data/wiki_eval.json",
        {
            "snapshot_id": "wiki:b",
            "source": {"revisions": {"List of A": "456"}},
            "documents": [
                {"title": "List of A", "doc_id": "doc:b", "text": "new table"}
            ],
        },
    )
    pool = json.loads((tmp_path / "configs/wiki_pool.json").read_text())
    pool["sources"].append(
        {"domain": "geography", "topic": "b", "split": "eval", "snapshot": second}
    )
    config["inventories"][0]["pin"] = _write(tmp_path, "configs/wiki_pool.json", pool)
    jobs, unsupported, _ = plan(config, tmp_path)
    assert {job["source_kind"] for job in jobs} == {"annual_reports"}
    assert sum(row["reason"] == "cross_split_page_conflict" for row in unsupported) == 4


def _fixture_config_without_rewriting_sources(root: Path) -> dict:
    """Read the original nested pins before a changed source is accepted."""
    return json.loads((root / "configs/plan.json").read_text())
