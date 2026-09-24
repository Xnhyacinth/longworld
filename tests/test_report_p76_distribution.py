"""The distribution report counts native operators only for selected tasks."""

from __future__ import annotations

import json

from scripts.report_p76_distribution import report


def test_join_selected_native_operations_and_count_duplicate_once(tmp_path) -> None:
    finance = tmp_path / "finance" / "issuer"
    codeforge = tmp_path / "codeforge" / "repo"
    finance.mkdir(parents=True)
    codeforge.mkdir(parents=True)
    (finance / "tasks.jsonl").write_text(
        json.dumps({"semantic_task_id": "f1", "task_spec": {"family": "ratio"}}) + "\n"
    )
    (codeforge / "tasks.jsonl").write_text(
        json.dumps({"semantic_task_id": "c1", "program_id": "merged_files_union"})
        + "\n"
    )
    index = tmp_path / "sample_index.jsonl"
    rows = [
        {
            "product": "p64",
            "domain": "finance",
            "task_id": "f1",
            "new_independent_task": True,
            "source_kind": "real_workflow",
            "length_bin": "32k_to_lt64k",
            "source_group": "issuer",
            "family": None,
        },
        {
            "product": "p66",
            "domain": "codeforge",
            "task_id": "c1",
            "new_independent_task": False,
            "source_kind": "real_workflow",
            "length_bin": "128k_to_lt256k",
            "source_group": "repo",
            "family": None,
        },
    ]
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = report(index, finance.parent, codeforge.parent)
    assert result["unique_tasks"] == 1
    assert result["operations"] == {"ratio": 1}
    assert result["source_groups_by_kind"] == {"real_workflow": 1}
