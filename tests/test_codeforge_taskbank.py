from __future__ import annotations

import json
from copy import deepcopy

import pytest

from longworld.core.codeforge_taskbank import (
    compile_tasks,
    evaluate_program,
    render_context,
)


def bank():
    records = []
    episodes = []
    for i, paths in enumerate((["a.py", "shared.py"], ["b.py", "shared.py", "c.rs"])):
        p = f"p{i}"
        rows = [
            ("pr", "pull_request", "PR", [], {"number": i + 1}),
            ("old", "commit", "diff -- old.py\n-old\n+new", [], {"sha": f"old{i}"}),
            (
                "head",
                "commit",
                "\n".join("diff -- " + x + "\n-x\n+y" for x in paths),
                [],
                {"sha": f"head{i}"},
            ),
            (
                "fail",
                "ci_run",
                "CI check build conclusion=failure",
                ["old"],
                {
                    "head_sha": f"old{i}",
                    "name": "build",
                    "app_slug": "ci",
                    "conclusion": "failure",
                },
            ),
            (
                "pass",
                "ci_run",
                "CI check build conclusion=success",
                ["head"],
                {
                    "head_sha": f"head{i}",
                    "name": "build",
                    "app_slug": "ci",
                    "conclusion": "success",
                },
            ),
            (
                "review",
                "review",
                "state=APPROVED",
                ["pr", "head"],
                {"state": "APPROVED"},
            ),
            (
                "merge",
                "merge",
                "Merged",
                ["pr", "head", "review"],
                {"commit": f"head{i}"},
            ),
            (
                "release",
                "release",
                f"Release v{i}",
                ["merge", "pass"],
                {"tag": f"v{i}"},
            ),
        ]
        mapping = {}
        for index, (rid, kind, text, links, attrs) in enumerate(rows):
            key = p + ":" + rid
            mapping[rid] = key
            records.append(
                {
                    "record_id": key,
                    "original_id": rid,
                    "kind": kind,
                    "text": text,
                    "links": links,
                    "attributes": attrs,
                    "occurred_at": f"2026-01-0{i + 1}T00:00:{index:02d}Z",
                    "source_pointer": "https://example.com/" + key,
                }
            )
        episodes.append(
            {
                "episode_id": p,
                "record_ids": list(mapping.values()),
                "record_map": mapping,
            }
        )
    return {
        "world_id": "world",
        "source_collection_id": "source",
        "world_instance_id": "world",
        "source_group_id": "repo",
        "domain": "codeforge",
        "records": records,
        "episodes": episodes,
    }


def test_programs_follow_links_and_have_distinct_outputs():
    world = bank()
    scope = ["p0", "p1"]
    assert evaluate_program(world, scope, "shared_changed_files")["answer"] == [
        "shared.py"
    ]
    assert evaluate_program(world, scope, "largest_changed_file_set")["answer"] == {
        "pull_requests": [2],
        "file_count": 3,
    }
    assert evaluate_program(world, scope, "recovered_ci_checks")["answer"] == {
        "1": ["ci/build"],
        "2": ["ci/build"],
    }
    tasks = compile_tasks(world, [scope], split="train")
    assert len({t["program_id"] for t in tasks}) >= 5
    assert all("messages" not in t and "context" not in t for t in tasks)
    assert all(
        t["semantic_task_id"] and t["variant_family_id"] and t["sample_id"]
        for t in tasks
    )
    assert "diff -- shared.py" in render_context(world, scope)


def test_linked_head_change_affects_oracle_and_missing_head_fails():
    world = bank()
    changed = deepcopy(world)
    next(r for r in changed["records"] if r["record_id"] == "p1:head")["text"] = (
        "diff -- only.rs\n-x\n+y"
    )
    assert (
        evaluate_program(changed, ["p0", "p1"], "shared_changed_files")["answer"] == []
    )
    next(r for r in changed["records"] if r["record_id"] == "p1:merge")["attributes"][
        "commit"
    ] = "missing"
    with pytest.raises(ValueError, match="head|commit"):
        evaluate_program(changed, ["p0", "p1"], "merged_files_union")


def test_review_and_ci_conditions_are_not_inferred_from_names():
    world = bank()
    next(r for r in world["records"] if r["record_id"] == "p0:review")["attributes"][
        "state"
    ] = "COMMENTED"
    assert evaluate_program(world, ["p0", "p1"], "approved_merge_files")["answer"] == [
        "b.py",
        "c.rs",
        "shared.py",
    ]
    next(r for r in world["records"] if r["record_id"] == "p1:pass")["attributes"][
        "conclusion"
    ] = "failure"
    assert (
        evaluate_program(world, ["p0", "p1"], "recovered_ci_checks")["answer"]["2"]
        == []
    )


def test_scope_rejects_duplicates_and_single_episode():
    with pytest.raises(ValueError, match="scope"):
        evaluate_program(bank(), ["p0", "p0"], "merged_files_union")
    with pytest.raises(ValueError, match="scope"):
        compile_tasks(bank(), [["p0"]], split="train")


def test_render_uses_short_refs_and_shares_identical_license_text():
    world = bank()
    for i, episode in enumerate(world["episodes"]):
        key = str(i) * 64
        world["records"].append(
            {
                "record_id": key,
                "original_id": "license:" + key,
                "kind": "license",
                "text": "UNIQUE_LICENSE_BODY",
                "links": [],
                "attributes": {},
                "occurred_at": "2020-01-01T00:00:00Z",
                "source_pointer": "https://example.com/license",
            }
        )
        episode["record_ids"].append(key)
        episode["record_map"]["license:" + key] = key
    text = render_context(world, ["p0", "p1"])
    assert text.count("UNIQUE_LICENSE_BODY") == 1
    assert "0" * 64 not in text
    assert "1" * 64 not in text


def test_oracles_replay_from_only_rendered_reader_fields():
    source = bank()
    rendered = json.loads(render_context(source, ["p0", "p1"]))
    visible = {
        "records": [
            dict(r, record_id=r["id"], original_id=r["id"]) for r in rendered["records"]
        ],
        "episodes": [],
    }
    for i, scope in enumerate(rendered["scope"]):
        visible["episodes"].append(
            {
                "episode_id": f"p{i}",
                "record_ids": scope["records"],
                "record_map": {key: key for key in scope["records"]},
            }
        )
    for task in compile_tasks(source, [["p0", "p1"]], split="train"):
        replay = evaluate_program(visible, ["p0", "p1"], task["program_id"])
        assert replay["answer"] == task["oracle_answer"]
