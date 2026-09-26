"""Adversarial source-grid tests: structure is necessary, not QA admission."""

from scripts.p122_wiki_html_grid import Tables, grid


def parsed(html: str):
    reader = Tables(html)
    reader.feed(html)
    assert len(reader.tables) == 1
    return grid(reader.tables[0], reader.notes, html)


def test_barcelona_row_header_and_empty_cell_are_preserved() -> None:
    rows = "".join(
        f"<tr><th scope='row'>Museum {i}</th><th>M{i}</th><td></td><td>Art</td></tr>"
        for i in range(8)
    )
    html = (
        "<h2>Museums</h2><table class='wikitable sortable'><caption>Barcelona</caption>"
        "<tr><th>Name</th><th>Acronym</th><th>Image</th><th>Type</th></tr>"
        + rows
        + "</table>"
    )
    value, reasons = parsed(html)
    assert not reasons
    assert value["body_rows"] == 8
    assert value["header_paths"] == [["Name"], ["Acronym"], ["Image"], ["Type"]]
    first = value["rows"][1]
    assert value["origins"][first[0]]["kind"] == "th"
    assert value["origins"][first[2]]["text"] == ""
    assert value["caption"] == "Barcelona"
    assert value["section_path"] == ["Museums"]


def test_rowspan_colspan_and_footnote_resolve_without_shift() -> None:
    rows = "<tr><td rowspan='2'>Plant A</td><td>1</td><td>2<sup class='reference'><a href='#cite_note-1'>[1]</a></sup></td></tr>"
    rows += "<tr><td>3</td><td>4</td></tr>"
    rows += "".join(
        f"<tr><td>Plant {i}</td><td>{i}</td><td>{i + 1}</td></tr>" for i in range(6)
    )
    html = (
        "<table class='wikitable'><tr><th rowspan='2'>Name</th><th colspan='2'>Capacity</th></tr>"
        "<tr><th>2024</th><th>2025</th></tr>" + rows + "</table>"
        "<ol class='references'><li id='cite_note-1'>Capacity revised in 2025.</li></ol>"
    )
    value, reasons = parsed(html)
    assert not reasons
    assert value["width"] == 3 and value["body_rows"] == 8
    assert value["header_paths"] == [
        ["Name"],
        ["Capacity", "2024"],
        ["Capacity", "2025"],
    ]
    assert value["rows"][2][0] == value["rows"][3][0]
    note = value["origins"][value["rows"][2][2]]
    assert note["footnote_refs"] == ["cite_note-1"]
    assert note["footnotes"] == {"cite_note-1": "Capacity revised in 2025."}
    assert html[note["html_span"][0] : note["html_span"][1]].startswith("<td>")


def test_ragged_hidden_nested_or_unresolved_note_cannot_be_valid() -> None:
    header = "<tr><th>Name</th><th>Place</th><th>Type</th></tr>"
    rows = "".join(f"<tr><td>A{i}</td><td>X</td><td>Y</td></tr>" for i in range(8))
    base = "<table class='wikitable'>" + header + rows + "</table>"
    value, reasons = parsed(
        base.replace("<td>A7</td><td>X</td><td>Y</td>", "<td>A7</td><td>X</td>")
    )
    assert value["body_rows"] == 8 and "nonuniform_expanded_width" in reasons
    _, reasons = parsed(
        base.replace("<td>A7</td>", "<td style='display: none'>A7</td>")
    )
    assert "hidden_markup" in reasons
    _, reasons = parsed(
        base.replace("<td>A7</td>", "<td>A7<table><tr><td>N</td></tr></table></td>")
    )
    assert "nested_table" in reasons
    _, reasons = parsed(
        base.replace(
            "<td>A7</td>",
            "<td>A7<sup class='reference'><a href='#cite_note-3'>[3]</a></sup></td>",
        )
    )
    assert "unresolved_citation" in reasons


def test_frozen_oldid_grids_replay_and_preserve_cell_evidence() -> None:
    import hashlib
    import json

    from scripts.p122_wiki_html_grid import ROOT, compile_grids, freeze

    source = ROOT / "data/candidates/p122_wiki_html_source_v1"
    output = ROOT / "data/candidates/p122_wiki_html_grid_v1"
    if not (output / "manifest.json").exists():
        return
    frozen = freeze(
        ROOT / "configs/p122_wiki_html_grid_v1.json", source, verify_only=True
    )
    report = compile_grids(source, output, verify_only=True)
    assert frozen["pages"] == report["pages"] == 12
    assert report["gross_wikitables"] == 53
    assert report["grid_valid_tables"] == 25
    pages = {row["title"]: row for row in frozen["records"]}
    grids = [
        json.loads(line)
        for line in (output / "valid_grids.jsonl").read_text().splitlines()
    ]
    barcelona = [row for row in grids if row["title"] == "List of museums in Barcelona"]
    assert len(barcelona) == 1 and barcelona[0]["grid"]["body_rows"] == 65
    for row in grids:
        value = row["grid"]
        html = (ROOT / pages[row["title"]]["html_path"]).read_text()
        assert all(len(record) == value["width"] for record in value["rows"])
        assert len({tuple(path) for path in value["header_paths"]}) == value["width"]
        for cell in value["origins"].values():
            start, end = cell["html_span"]
            assert (
                hashlib.sha256(html[start:end].encode()).hexdigest()
                == cell["html_sha256"]
            )
            assert all(ref in cell["footnotes"] for ref in cell["footnote_refs"])
