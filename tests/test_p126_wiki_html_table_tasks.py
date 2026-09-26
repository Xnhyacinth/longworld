"""Behavioral checks for the bounded HTML table reader route."""

from scripts.p126_wiki_html_table_tasks import (
    answer_from_reader,
    column_options,
    identity_header,
    intervention,
    render_context,
)


def _grid() -> dict:
    origins = {}
    rows = [["h0", "h1", "h2"]]
    for index in range(8):
        row = []
        for column, value in enumerate(
            (
                f"Museum {index}",
                "East" if index == 7 else "North" if index % 2 else "South",
                "Art",
            )
        ):
            key = f"{index}:{column}"
            origins[key] = {
                "text": value,
                "rowspan": 1,
                "colspan": 1,
                "footnote_refs": [],
                "footnotes": {},
                "citation_markers": [],
                "html_span": [0, len("<td>" + value + "</td>")],
            }
            row.append(key)
        rows.append(row)
    return {
        "origins": origins,
        "rows": rows,
        "header_rows": 1,
        "width": 3,
        "header_paths": [["Name"], ["Region"], ["Type"]],
        "caption": "Museums",
        "section_path": ["Current museums"],
    }


def test_entity_header_uses_list_subject_without_admitting_geo_or_rank() -> None:
    assert identity_header("List of museums in Berlin", ["Name"])
    assert identity_header("List of stadiums by capacity", ["Stadium"])
    assert identity_header("List of airports named after people", ["Airport"])
    assert not identity_header("List of college towns", ["Name"])
    assert not identity_header("List of college towns", ["Town name"])
    assert not identity_header("List of museums", ["City"])
    assert not identity_header("List of museums", ["Rank"])


def test_full_row_gold_and_two_reader_cell_edits() -> None:
    grid = _grid()
    html = "<td>Museum 0</td>"
    entry = {
        "title": "List of museums in Berlin",
        "revision_url": "https://en.wikipedia.org/w/index.php?oldid=1",
        "grid": grid,
    }
    options, rejected = column_options(grid, html, entry["title"])
    assert (0, 1, "North") in options and not rejected.get("duplicate_entity_key")
    context, spans = render_context(entry)
    assert answer_from_reader(context, 0, 1, "North") == {
        "count": 3,
        "entries": ["Museum 1", "Museum 3", "Museum 5"],
    }
    edit = intervention(context, spans, 0, 1, "North")
    assert edit["hit_answer"]["count"] == 4
    assert edit["control_answer"]["count"] == 3


def test_missing_or_qualified_target_rejects_whole_column() -> None:
    grid = _grid()
    grid["origins"]["3:1"]["footnote_refs"] = ["cite_note-1"]
    options, rejected = column_options(grid, "<td>North</td>", "List of museums")
    assert not any(target == 1 for _, target, _ in options)
    assert rejected["target_cell_missing_multiline_spanned_or_qualified"] > 0


def test_duplicate_key_rejects_table_identity() -> None:
    grid = _grid()
    grid["origins"]["3:0"]["text"] = "Museum 2"
    options, rejected = column_options(grid, "<td>Museum 0</td>", "List of museums")
    assert not options
    assert rejected["duplicate_entity_key"] > 0


def test_multiline_target_rejects_instead_of_splitting_category() -> None:
    grid = _grid()
    grid["origins"]["3:1"]["text"] = "North\nSouth"
    options, rejected = column_options(grid, "<td>Museum 0</td>", "List of museums")
    assert not any(target == 1 for _, target, _ in options)
    assert rejected["target_cell_missing_multiline_spanned_or_qualified"] > 0
