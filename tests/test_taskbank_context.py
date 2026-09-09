import pytest

from longworld.core.taskbank_context import (
    assemble_context,
    bind_visible_evidence,
    render_document,
    text_sha256,
)


def document(text, record_id="filing-2024"):
    return {
        "text": text,
        "source_sha256": text_sha256(text),
        "record_id": record_id,
        "report_date": "2024-12-31",
        "filing_date": "2025-02-01",
        "source_url": "https://issuer.example/annual",
    }


def test_render_preserves_headers_cells_and_visible_negative_evidence():
    source = '<h2>Cash flow, USD millions</h2><table><tr><th>2024</th></tr><tr><td>Investing</td><td>(<ix:nonfraction sign="-">123</ix:nonfraction>)</td></tr></table>'
    doc = document(source)
    result = render_document(doc)
    assert "Cash flow, USD millions" in result["text"]
    assert "\tInvesting\t(123)" in result["text"]
    start = source.index("123")
    span = {"start": start, "end": start + 3, "quote": "123"}
    bound = bind_visible_evidence(doc, result, span)
    assert result["text"][bound["visible_start"] : bound["visible_end"]] == "123"


@pytest.mark.parametrize(
    "wrapper",
    [
        "<ix:hidden>{}</ix:hidden>",
        '<span style="display: none">{}</span>',
        "<div hidden>{}</div>",
        "<script>{}</script>",
    ],
)
def test_hidden_gold_cannot_become_visible_evidence(wrapper):
    source = "<p>Visible annual report</p>" + wrapper.format("9876")
    doc = document(source)
    rendered = render_document(doc)
    assert "9876" not in rendered["text"]
    start = source.index("9876")
    with pytest.raises(ValueError, match="hidden"):
        bind_visible_evidence(
            doc, rendered, {"start": start, "end": start + 4, "quote": "9876"}
        )


def test_context_contains_complete_documents_once_and_exact_evidence_offsets():
    docs = [document("<p>Summary and value 123</p><p>Final note</p>")]
    rendered = {doc["record_id"]: render_document(doc) for doc in docs}
    text, offsets = assemble_context(docs, rendered, [docs[0]["record_id"]])
    body = rendered[docs[0]["record_id"]]["text"]
    assert text[offsets[docs[0]["record_id"]] :] == body
    assert text.count("Summary") == 1 and "Final note" in text
    with pytest.raises(ValueError, match="duplicated"):
        assemble_context(docs, rendered, [docs[0]["record_id"]] * 2)


def test_source_tamper_fails_before_rendering():
    doc = document("<p>123</p>")
    doc["text"] = "<p>999</p>"
    with pytest.raises(ValueError, match="hash"):
        render_document(doc)


def test_entity_before_evidence_and_inline_word_spacing():
    source = "<p>A&amp;B 123</p><span>Net</span> <span>income</span>"
    doc = document(source)
    result = render_document(doc)
    assert "Net income" in result["text"]
    start = source.index("123")
    bind_visible_evidence(
        doc, result, {"start": start, "end": start + 3, "quote": "123"}
    )


def test_binding_rejects_other_document_and_duplicate_document_list():
    first = document("<p>Revenue 123</p>")
    second = document("<p>Expense 123</p>")
    start = first["text"].index("123")
    with pytest.raises(ValueError, match="binding"):
        bind_visible_evidence(
            first,
            render_document(second),
            {"start": start, "end": start + 3, "quote": "123"},
        )
    with pytest.raises(ValueError, match="duplicated"):
        assemble_context(
            [first, first],
            {first["record_id"]: render_document(first)},
            [first["record_id"]],
        )


def test_hidden_negative_sign_is_not_a_reading_answer():
    source = '<p>Cash</p><td><ix:nonfraction sign="-">123</ix:nonfraction></td>'
    doc = document(source)
    start = source.index("123")
    with pytest.raises(ValueError, match="sign"):
        bind_visible_evidence(
            doc,
            render_document(doc),
            {"start": start, "end": start + 3, "quote": "123", "value": -123},
        )


def test_statement_packet_preserves_all_sections_and_merges_overlaps():
    from longworld.core.taskbank_context import assemble_statement_context

    source = "<h2>Balance</h2><table><tr><td>Summary 123</td></tr></table><h2>Cash</h2><table><tr><td>Unused 456</td></tr></table>"
    doc = document(source)
    cut = source.index("<h2>Cash")
    doc["sections"] = [
        {"title": "Balance", "start": 0, "end": cut},
        {"title": "Same balance", "start": 0, "end": cut},
        {"title": "Cash", "start": cut, "end": len(source)},
    ]
    rendered = {doc["record_id"]: render_document(doc)}
    text, mappings = assemble_statement_context([doc], rendered, [doc["record_id"]])
    assert text.count("Summary 123") == 1
    assert text.count("Unused 456") == 1
    for mapping in mappings[doc["record_id"]]:
        body = rendered[doc["record_id"]]["text"][
            mapping["visible_start"] : mapping["visible_end"]
        ]
        assert (
            text[mapping["context_start"] : mapping["context_start"] + len(body)]
            == body
        )


def test_numeric_sign_cannot_come_from_another_cell():
    source = '<tr><td>-</td><td><ix:nonfraction sign="-">123</ix:nonfraction></td></tr>'
    doc = document(source)
    start = source.index("123")
    with pytest.raises(ValueError, match="sign"):
        bind_visible_evidence(
            doc,
            render_document(doc),
            {"start": start, "end": start + 3, "quote": "123", "value": -123},
        )


def test_statement_packet_keeps_trailing_encoded_parenthesis():
    from longworld.core.taskbank_context import assemble_statement_context

    source = '<table><tr><td>(<ix:nonfraction sign="-">123</ix:nonfraction>&#41;</td></tr></table>'
    doc = document(source)
    doc["sections"] = [{"title": "Cash", "start": 0, "end": len(source)}]
    text, _ = assemble_statement_context(
        [doc], {doc["record_id"]: render_document(doc)}, [doc["record_id"]]
    )
    assert "(123)" in text


def test_layout_entities_do_not_inflate_context_length():
    doc = document("<p>Revenue" + "&nbsp;" * 1000 + "123</p>")
    rendered = render_document(doc)
    assert "Revenue 123" in rendered["text"]
    assert len(rendered["text"]) < 30
    start = doc["text"].index("123")
    bind_visible_evidence(
        doc, rendered, {"start": start, "end": start + 3, "quote": "123"}
    )


def test_analyst_packet_retains_latest_notes_and_complete_prior_statement():
    from longworld.core.taskbank_context import assemble_analyst_context

    old = document(
        "<table><tr><td>Prior summary 123</td></tr></table><p>Prior note</p>", "old"
    )
    old["report_date"] = "2023-12-31"
    old["sections"] = [
        {"title": "Balance", "start": 0, "end": old["text"].index("<p>")}
    ]
    new = document(
        "<table><tr><td>Latest summary 456</td></tr></table><p>Latest note</p>", "new"
    )
    new["sections"] = [
        {"title": "Balance", "start": 0, "end": new["text"].index("<p>")}
    ]
    docs = [old, new]
    rendered = {d["record_id"]: render_document(d) for d in docs}
    text, mappings = assemble_analyst_context(docs, rendered, ["old", "new"])
    assert text.count("Prior summary 123") == 1
    assert text.count("Latest summary 456") == 1
    assert "Latest note" in text
    for record_id, intervals in mappings.items():
        for interval in intervals:
            body = rendered[record_id]["text"][
                interval["visible_start"] : interval["visible_end"]
            ]
            assert (
                text[interval["context_start"] : interval["context_start"] + len(body)]
                == body
            )
