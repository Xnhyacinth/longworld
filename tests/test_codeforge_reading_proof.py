from __future__ import annotations

import itertools
import json

from longworld.core.codeforge_reading_proof import (
    AliasIndex,
    alias_surfaces,
    audit_file_task,
    minimum_cover,
    occurrence_table,
    source_text_control,
    visible_file_reader,
    window_coverage,
)
from longworld.core.codeforge_taskbank import compile_tasks, render_context
from tests.test_codeforge_taskbank import bank


def test_aliases_include_basename_json_and_url_and_find_every_overlap():
    aliases = alias_surfaces("src/a b.py")
    assert "a b.py" in aliases
    assert "src%2Fa%20b.py" in aliases
    index = AliasIndex(["src/a b.py", "other/a b.py", "aaa"])
    hits = list(index.find("SRC/A B.PY -- a%20b.py -- aaaaa"))
    assert {label for hit in hits for label in hit["labels"]} == {0, 1, 2}
    assert len([h for h in hits if h["surface"] == "aaa"]) == 3


def test_ambiguous_basename_is_granted_to_every_gold_path():
    text = "shared.py"
    table = occurrence_table(
        text,
        ["first/shared.py", "second/shared.py"],
        [(i, i + 1) for i in range(len(text))],
    )
    groups = [[], []]
    for hit in table["occurrences"]:
        for label in hit["labels"]:
            groups[label].append((hit["token_start"], hit["token_end"]))
    assert minimum_cover(groups)["minimum_tokens"] == len(text)
    assert window_coverage(groups, len(text), len(text))["all_labels_fit"]


def test_occurrence_index_matches_naive_every_start_search():
    text = 'Abc ababa a%20b.py dir/a b.py "a\\\\b.py"'
    index = AliasIndex(["a", "ab", "abc", "ba", "dir/a b.py", "a\\b.py"])
    actual = {(hit["alias_id"], hit["start"], hit["end"]) for hit in index.find(text)}
    expected = {
        (i, start, start + len(p["surface"]))
        for i, p in enumerate(index.patterns)
        for start in range(len(text))
        if text.lower().startswith(p["surface"], start)
    }
    assert actual == expected


def test_minimum_cover_uses_short_alternative_occurrences():
    occurrences = [[(0, 3), (50, 52)], [(9, 12), (53, 55)]]
    result = minimum_cover(occurrences)
    assert result["minimum_tokens"] == 5
    assert result["start"] == 50 and result["end"] == 55
    assert window_coverage(occurrences, 5, 100)["all_labels_fit"]
    assert not window_coverage(occurrences, 4, 100)["all_labels_fit"]


def test_minimum_and_window_sweeps_match_exhaustive_small_cases():
    options = [(0, 1), (1, 3), (2, 4), (4, 6)]
    for a, b in itertools.product(itertools.combinations(options, 2), repeat=2):
        groups = [list(a), list(b)]
        brute = min(max(x[1], y[1]) - min(x[0], y[0]) for x in a for y in b)
        assert minimum_cover(groups)["minimum_tokens"] == brute
        for width in range(1, 7):
            expected = max(
                sum(any(s <= x and y <= s + width for x, y in g) for g in groups)
                for s in range(max(0, 6 - width) + 1)
            )
            assert window_coverage(groups, width, 6)["max_labels"] == expected


def test_visible_reader_uses_source_fields_and_reports_real_contributions():
    visible = json.loads(render_context(bank(), ["p0", "p1"]))
    union = visible_file_reader(visible, "merged_files_union")
    assert union["answer"] == ["a.py", "b.py", "c.rs", "shared.py"]
    assert union["all_active_sources_change_answer"]
    approved = visible_file_reader(visible, "approved_merge_files")
    assert not approved["approval_changes_answer"]
    shared = visible_file_reader(visible, "shared_changed_files")
    assert shared["answer"] == ["shared.py"]
    assert not union["latest_head_only_exact_answer"]


def test_question_aliases_are_free_outside_every_raw_window():
    world = bank()
    text = render_context(world, ["p0", "p1"])
    task = next(
        t
        for t in compile_tasks(world, [["p0", "p1"]], split="train")
        if t["program_id"] == "merged_files_union"
    )
    task["query"] += " Candidate filenames: " + ", ".join(task["oracle_answer"])
    table = occurrence_table(
        text, task["oracle_answer"], [(i, i + 1) for i in range(len(text))]
    )
    result = audit_file_task(text, task, table, len(text))
    assert result["minimum_alias_cover"]["minimum_tokens"] == 0
    assert set(result["free_question_filenames"]) == set(task["oracle_answer"])
    assert all(v["all_labels_fit"] for v in result["raw_token_window_tests"].values())
    assert not result["scoped_long_input_certificate"]


def test_content_control_preserves_hex_filename_and_every_header_alias():
    required = "src/" + "a" * 40 + ".py"
    distractor = "b" * 64 + ".txt"
    opaque = "c" * 40
    visible = {
        "records": [
            {
                "text": f"commit {opaque}\ndiff -- {required}\n@@ hunk\n\ndiff -- {distractor}\n@@ hunk",
                "attributes": {"irrelevant": "METADATA_REMOVED"},
            }
        ]
    }
    text, report = source_text_control(visible, [required])
    assert required in text and distractor in text
    assert "diff -- " + required in text
    assert "diff -- " + distractor in text
    assert opaque not in text and "METADATA_REMOVED" not in text
    assert len(report["normalized_sha_intervals_in_joined_source_text"]) == 1
    assert len(report["alias_overlapping_sha_intervals_preserved"]) == 2
    assert not report["standalone_task_solver"]
