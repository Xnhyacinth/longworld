"""Behavioral checks for legal recipes, stable jobs and resumed artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis.p92_factorial_batch import (
    _aggregate,
    _novelty_counts,
    _run_job,
    _verify_adoption,
    _verify_job,
    plan,
    run,
)

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path) -> tuple[Path, dict]:
    config = json.loads((ROOT / "configs/p92_factorial_batch_v1.json").read_text())
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(config))
    return path, config


def test_plan_expands_only_declared_native_combinations(tmp_path: Path) -> None:
    path, _ = _config(tmp_path)
    resolved = plan(path)
    assert len(resolved["jobs"]) == 3
    assert len(resolved["planned_cells"]) == 10
    assert (
        sum(
            cell["worlds"]
            for cell in resolved["planned_cells"]
            if cell["recipe"] == "shared_state_200"
        )
        == 200
    )
    assert len({job["job_id"] for job in resolved["jobs"]}) == 3
    assert {job["domain"] for job in resolved["jobs"]} == {
        "simulation",
        "protocol",
        "architecture",
    }
    assert plan(path) == resolved


def test_rejects_domain_relabel_and_unsupported_override(tmp_path: Path) -> None:
    path, config = _config(tmp_path)
    config["recipes"][0]["domain"] = "medicine"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="relabel"):
        plan(path)
    config["recipes"][0]["domain"] = "simulation"
    config["recipes"][0]["overrides"]["topic"] = "biology"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="override"):
        plan(path)


def test_rejects_duplicate_numeric_task(tmp_path: Path) -> None:
    path, config = _config(tmp_path)
    config["recipes"][2]["overrides"]["intervals"] = [[80, 110], [80, 110]]
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="distinct"):
        plan(path)


def test_resume_rejects_changed_native_bytes(tmp_path: Path) -> None:
    path, _ = _config(tmp_path)
    job = plan(path)["jobs"][0]
    target = tmp_path / job["job_id"]
    native = target / "native"
    native.mkdir(parents=True)
    (target / "config.json").write_text(json.dumps(job["config"]))
    (native / "manifest.json").write_text("{}")
    (target / "receipt.json").write_text(
        json.dumps(
            {
                "job_id": job["job_id"],
                "base_sha256": job["base_sha256"],
                "files_sha256": {"manifest.json": "incorrect"},
            }
        )
    )
    with pytest.raises(ValueError, match="native artifact changed"):
        _verify_job(job, target)


def test_adopted_receipt_cannot_hide_unpinned_source_code(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_config = tmp_path / "source-config.json"
    source_config.write_text("{}")
    source_plan = {
        "schema_version": "longworld.p92-factorial-batch.v1.resolved",
        "plan_sha256": hashlib.sha256(b"{}").hexdigest(),
        "jobs": [{"name": "job", "job_id": "old-job"}],
    }
    (source / "plan.json").write_text(json.dumps(source_plan))
    (source / "manifest.json").write_text("{}")
    (source / "old-job").mkdir()
    (source / "old-job" / "receipt.json").write_text("{}")
    output = tmp_path / "adopted"
    output.mkdir()
    planned = {"jobs": [{"name": "job"}]}
    (output / "plan.json").write_text("{}")
    (output / "adoption.json").write_text(
        json.dumps(
            {
                "source_schema_version": "longworld.p92-factorial-batch.v1.resolved",
                "source_plan_sha256": hashlib.sha256(
                    (source / "plan.json").read_bytes()
                ).hexdigest(),
                "source_manifest_sha256": hashlib.sha256(b"{}").hexdigest(),
                "source_output_relative": "../source",
                "source_config_relative": "../source-config.json",
                "target_plan_sha256": hashlib.sha256(b"{}").hexdigest(),
                "execution_code_unpinned_at_source": True,
                "native_bytes_copied_and_sha256_verified": True,
            }
        )
    )
    receipt = {
        "adopted_from_job_receipt_sha256": hashlib.sha256(b"{}").hexdigest(),
        "execution_code_unpinned_at_source": True,
    }
    assert _verify_adoption(output, planned, [receipt])
    with pytest.raises(ValueError, match="adoption provenance changed"):
        _verify_adoption(
            output, planned, [{**receipt, "execution_code_unpinned_at_source": False}]
        )
    with pytest.raises(ValueError, match="provenance marker missing"):
        _verify_adoption(output, planned, [{}])
    sidecar = output / "adoption.json"
    altered = json.loads(sidecar.read_text())
    altered["source_schema_version"] = "longworld.p92-factorial-batch.v2.resolved"
    altered["execution_code_unpinned_at_source"] = False
    sidecar.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="adoption source schema changed"):
        _verify_adoption(
            output, planned, [{**receipt, "execution_code_unpinned_at_source": False}]
        )


def test_batch_reuses_same_split_world_and_dedupes_semantics(tmp_path: Path) -> None:
    path, _ = _config(tmp_path)
    planned = plan(path)
    common = {
        "kind": "shared_state",
        "candidate_views": 1,
        "semantic_tasks": 1,
        "source_groups": 1,
        "source_group_ids": ["same-group"],
        "actual_full_chat_token_bins": {"<65536": 1},
        "operation_rows": {"group_compare": 1},
        "split_rows": {"train": 1},
        "native_mask_audited_rows": 1,
        "rejected": 0,
        "rejection_reasons": {},
        "elapsed_seconds": 1.0,
        "domain": "simulation",
        "topic": "shared_record_state",
        "source_group_splits": {"same-group": "train"},
        "source_group_operations": {"same-group": ["group_compare"]},
        "source_scoped_task_answers": {
            '["controlled_simulation","same-group","task-1"]': "answer-a"
        },
        "global_semantic_task_keys": ['["controlled_simulation","task-1"]'],
        "sample_ids": ["sample-1"],
    }
    second = {
        **common,
        "operation_rows": {"asof_sum": 1},
        "source_group_operations": {"same-group": ["asof_sum"]},
        "source_scoped_task_answers": {
            '["controlled_simulation","same-group","task-2"]': "answer-b"
        },
        "global_semantic_task_keys": ['["controlled_simulation","task-2"]'],
        "sample_ids": ["sample-2"],
    }
    result = _aggregate(planned, [common, second])
    assert result["source_groups"] == 1
    assert result["multi_operation_source_groups"] == 1
    assert result["semantic_tasks"] == 2
    repeated = {
        **second,
        "source_scoped_task_answers": common["source_scoped_task_answers"],
        "global_semantic_task_keys": common["global_semantic_task_keys"],
    }
    repeated_result = _aggregate(planned, [common, repeated])
    assert repeated_result["semantic_tasks"] == 1
    assert repeated_result["gross_per_recipe_semantic_tasks"] == 2
    with pytest.raises(ValueError, match="leaked across splits"):
        _aggregate(
            planned, [common, {**second, "source_group_splits": {"same-group": "eval"}}]
        )
    with pytest.raises(ValueError, match="conflicting answer"):
        _aggregate(
            planned,
            [
                common,
                {
                    **repeated,
                    "source_scoped_task_answers": {
                        '["controlled_simulation","same-group","task-1"]': "wrong"
                    },
                },
            ],
        )
    with pytest.raises(ValueError, match="duplicate sample"):
        _aggregate(planned, [common, {**second, "sample_ids": ["sample-1"]}])


def test_pinned_source_pool_expands_supported_only(tmp_path: Path) -> None:
    path, config = _config(tmp_path)
    config["recipes"] = [
        {
            "name": "frozen_wiki_pool",
            "kind": "wiki_source_pool",
            "base_config": "configs/p76_source_pool_v2.json",
            "overrides": {},
            "domain": "from_pinned_sources",
            "topic": "from_pinned_sources",
            "operations": [
                "table_pair_earlier_year",
                "dense_table_interval_scan",
                "table_cell_lookup",
            ],
        }
    ]
    path.write_text(json.dumps(config))
    planned = plan(path)
    assert len(planned["jobs"]) == 1
    assert len(planned["planned_cells"]) > 0
    assert {cell["domain"] for cell in planned["planned_cells"]} != {
        "from_pinned_sources"
    }
    assert planned["jobs"][0]["unsupported_cells"]


def test_baseline_audit_marks_answer_overlap_without_claiming_equivalence() -> None:
    prior = [("source-a", "table_cell_lookup", "old-id", "answer-x")]
    current = [
        ("source-a", "table_cell_lookup", "new-id", "answer-x"),
        ("source-b", "table_cell_lookup", "different-id", "answer-y"),
    ]
    result = _novelty_counts(current, prior)
    assert result["global_task_id_overlap"] == 0
    assert result["source_scoped_task_key_overlap"] == 0
    assert result["same_source_operation_answer_overlap"] == 1
    assert result["previously_unindexed_source_groups"] == 1


def test_v2_resume_rejects_changed_native_compiler_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, config = _config(tmp_path)
    config["schema_version"] = "longworld.p92-factorial-batch.v2"
    config["recipes"] = config["recipes"][:1]
    path.write_text(json.dumps(config))
    frozen = plan(path)
    assert (
        "scripts/run_p86_state_shared_batch.py"
        in frozen["jobs"][0]["compiler_code_sha256"]
    )
    output = tmp_path / "batch"
    output.mkdir()
    (output / "plan.json").write_text(json.dumps(frozen))
    from longworld.synthesis import p92_factorial_batch as module

    original_sha = module._sha

    def changed(file: Path) -> str:
        return (
            "changed"
            if file.name == "run_p86_state_shared_batch.py"
            else original_sha(file)
        )

    monkeypatch.setattr(module, "_sha", changed)
    with pytest.raises(ValueError, match="frozen plan changed"):
        run(path, output, resume=True)


def test_recovers_complete_native_output_without_rerunning_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = _config(tmp_path)
    job = plan(path)["jobs"][2]
    stage = tmp_path / f".stage-{job['job_id']}"
    native = stage / "native"
    native.mkdir(parents=True)
    (stage / "config.json").write_text(json.dumps(job["config"]))
    (native / "manifest.json").write_text("{}")
    from longworld.synthesis import p92_factorial_batch as module
    from scripts import run_p91_wiki_numeric_table as numeric

    monkeypatch.setattr(
        module, "_native_index", lambda *_: ([], {"train_ready": False})
    )
    monkeypatch.setattr(module, "_summarize_job", lambda *_: {"job_id": job["job_id"]})
    monkeypatch.setattr(
        numeric,
        "run",
        lambda *_: (_ for _ in ()).throw(AssertionError("reran compiler")),
    )
    receipt = _run_job(job, tmp_path, 1)
    assert receipt["recovered_elapsed_unknown"] is True
    assert (tmp_path / job["job_id"] / "receipt.json").is_file()


def test_partial_nonresumable_native_output_is_preserved(tmp_path: Path) -> None:
    path, _ = _config(tmp_path)
    job = plan(path)["jobs"][2]
    stage = tmp_path / f".stage-{job['job_id']}"
    (stage / "native").mkdir(parents=True)
    (stage / "config.json").write_text(json.dumps(job["config"]))
    with pytest.raises(ValueError, match="partial native output cannot be resumed"):
        _run_job(job, tmp_path, 1)
    assert (stage / "native").is_dir()
