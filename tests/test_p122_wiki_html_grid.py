"""Adversarial source-grid tests: structure is necessary, not QA admission."""

import json

import pytest

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
    assert note["text"] == "2"
    assert note["raw_visible_text"] == "2[1]"
    assert note["citation_markers"] == ["[1]"]
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


def test_line_break_and_toc_navigation_are_not_entity_text_or_section() -> None:
    rows = "".join(
        f"<tr><td>Hospital<br>Rumah {i}</td><td>City</td><td>General</td></tr>"
        for i in range(8)
    )
    html = (
        "<div id='toc' role='navigation'><h2>Contents</h2></div>"
        "<h2>Hospitals</h2><table class='wikitable'>"
        "<tr><th>Name</th><th>City</th><th>Type</th></tr>" + rows + "</table>"
    )
    value, reasons = parsed(html)
    assert not reasons and value["section_path"] == ["Hospitals"]
    first = value["origins"][value["rows"][1][0]]
    assert first["text"] == "Hospital\nRumah 0"
    assert first["multiline"] is True


def test_stale_oldid_cache_requires_bound_receipt(tmp_path, monkeypatch) -> None:
    import scripts.p122_wiki_html_grid as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    selected = {
        "title": "List of hospitals",
        "domain": "healthcare",
        "split": "train",
        "oldid": 123,
        "page_url": "https://en.wikipedia.org/wiki/List_of_hospitals",
        "revision_url": "https://en.wikipedia.org/w/index.php?oldid=123",
        "rendered_body_sha256": "a" * 64,
        "snapshot": {"path": "source.json", "sha256": "b" * 64},
    }
    monkeypatch.setattr(module, "sources", lambda config: [selected])
    calls = []

    class FakeFetcher:
        def __init__(self, *args):
            pass

        def get_json(self, params):
            calls.append(params)
            return {
                "parse": {
                    "revid": 123,
                    "title": selected["title"],
                    "text": "<table></table>",
                }
            }

    monkeypatch.setattr(module, "WikiHttpFetcher", FakeFetcher)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": module.SCHEMA + ".config",
                "requests_per_second": 0.5,
                "max_html_bytes_per_page": 1000,
            }
        )
    )
    output = tmp_path / "frozen"
    key = module.sha(f"{selected['title']}\0{selected['oldid']}".encode())[:20]
    cache = output / "html" / f"{key}.html"
    cache.parent.mkdir(parents=True)
    cache.write_text("stale")
    with pytest.raises(ValueError, match="unbound"):
        module.freeze(config, output)
    assert not calls
    cache.unlink()
    module.freeze(config, output)
    assert len(calls) == 1
    receipt = cache.with_suffix(".receipt.json")
    stored = json.loads(receipt.read_text())
    stored["parsed_revid"] = 122
    receipt.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="receipt differs"):
        module.freeze(config, output, verify_only=True)


def test_frozen_oldid_grids_replay_and_preserve_cell_evidence() -> None:
    import hashlib
    import json

    from scripts.p122_wiki_html_grid import ROOT, compile_grids, freeze

    source = ROOT / "data/candidates/p122_wiki_html_source_v2"
    output = ROOT / "data/candidates/p122_wiki_html_grid_v2"
    if not (output / "manifest.json").exists():
        return
    frozen = freeze(
        ROOT / "configs/p122_wiki_html_grid_v2.json", source, verify_only=True
    )
    report = compile_grids(source, output, verify_only=True)
    assert frozen["pages"] == report["pages"] == 12
    assert report["gross_wikitables"] == 53
    assert report["grid_valid_tables"] >= 1
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
