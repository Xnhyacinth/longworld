"""Added-code tasks use patch contents and preserve negative source scopes."""

import json

from scripts.p99_code_content_tasks import _anchors, added_matches, parse_added_lines


def test_added_line_parser_ignores_removed_lines_and_patch_headers():
    patch = """diff -- src/a.py
--- a/src/a.py
+++ b/src/a.py
-old_identifier()
+new_identifier()
diff -- src/b.py
+second_identifier()
"""
    assert parse_added_lines(patch) == {
        "src/a.py": ["new_identifier()"],
        "src/b.py": ["second_identifier()"],
    }


def test_two_pr_answer_requires_visible_added_code_not_filenames():
    heads = [
        {
            "record_id": "R1",
            "pull_request": 11,
            "text": "diff -- src/a.py\n+new_identifier()",
        },
        {
            "record_id": "R2",
            "pull_request": 22,
            "text": "diff -- src/b.py\n+other_identifier()",
        },
    ]
    tokens = _anchors(heads, json.dumps({"filenames": ["src/a.py", "src/b.py"]}))
    assert tokens == ("new_identifier", "other_identifier")
    assert added_matches(heads, tokens) == [
        {"pull_request": 11, "path": "src/a.py"},
        {"pull_request": 22, "path": "src/b.py"},
    ]
    preserved_files = [{**head, "text": head["text"].splitlines()[0]} for head in heads]
    assert added_matches(preserved_files, tokens) == []


def test_anchor_rejects_identifier_visible_without_added_code():
    heads = [
        {"pull_request": 1, "text": "diff -- src/a.py\n+new_identifier()"},
        {"pull_request": 2, "text": "diff -- src/b.py\n+other_identifier()"},
    ]
    assert _anchors(heads, "A PR summary already mentions new_identifier") is None
