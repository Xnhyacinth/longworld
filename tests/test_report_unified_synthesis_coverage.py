import json

from scripts import report_unified_synthesis_coverage as coverage


def test_report_separates_multi_operation_from_shared_fact_proof(tmp_path, monkeypatch):
    rows = [
        {
            "source_kind": "real_wiki",
            "source_name": "wiki",
            "source_group": "group-a",
            "domain": "nature",
            "topic": "parks",
            "operation": operation,
            "length_bin": "32k",
            "split": "train",
            "evidence_profile": "table",
            "dependency_status": status,
            "task_key": f"task-{i}",
            "semantic_task_id": f"semantic-{i}",
            "answer_sha256": f"answer-{i}",
            "context_sha256": "same-context",
            "full_chat_tokens": 33000,
            "input_tokens": 32990,
            "supervised_tokens": 10,
        }
        for i, (operation, status) in enumerate(
            (("lookup", "scoped"), ("join", "unmeasured"))
        )
    ]
    (tmp_path / "sample_index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    (tmp_path / "manifest.json").write_text("{}\n")
    monkeypatch.setattr(coverage, "verify_merge", lambda _path: {"candidate_views": 2})

    result = coverage.report(tmp_path)

    assert result["source_groups_with_multiple_operations_by_kind"] == {"real_wiki": 1}
    assert result["operations_per_source_group"] == {2: 1}
    assert result["shared_facts_across_operations"] == "unmeasured"
    assert result["loss_mask_tokens"]["supervised"] == 20
    assert result["independent_tasks_by_operation"] == {"join": 1, "lookup": 1}
    assert result["operation_dependency_status"] == [
        {"operation": "join", "status": "unmeasured", "views": 1},
        {"operation": "lookup", "status": "scoped", "views": 1},
    ]
