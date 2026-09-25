from collections import Counter

import pytest

from scripts import compose_p94_real_pair_length as composer
from scripts.compose_p94_real_pair_length import _compose, _length_bin, _reader_replay


def _source():
    docs = [
        ("a", "First", "Name | Established\nAlpha | 1930"),
        ("b", "Second", "unrelated page"),
        ("c", "Third", "another page"),
        ("d", "Fourth", "Name | Established\nBeta | 2000"),
    ]
    parts, layouts = [], []
    cursor = 0
    for doc_id, title, text in docs:
        header = f"[{doc_id}] {title}\n"
        parts.append(header + text + "\n\n")
        layouts.append(
            {
                "doc_id": doc_id,
                "title": title,
                "text_start": cursor + len(header),
                "text_end": cursor + len(header) + len(text),
            }
        )
        cursor += len(parts[-1])
    return "".join(parts[:-1]) + parts[-1][:-2], layouts


def test_whole_filler_inserted_between_source_pages_and_years_still_required():
    context, layouts = _source()
    filler = {
        "doc_id": "f",
        "title": "Filler",
        "text": "Name | Established\nGamma | 1950",
    }
    composed, shifted, inserted = _compose(context, layouts, [filler])
    assert composed[: layouts[1]["text_end"]] == context[: layouts[1]["text_end"]]
    assert (
        composed[shifted[3]["text_start"] : shifted[3]["text_end"]]
        == "Name | Established\nBeta | 2000"
    )
    assert (
        composed[inserted[0]["text_start"] : inserted[0]["text_end"]] == filler["text"]
    )
    program = {
        "sides": [{"doc_id": "a", "label": "Alpha"}, {"doc_id": "d", "label": "Beta"}]
    }
    answer = {"years": {"Alpha": 1930, "Beta": 2000}, "earlier": "Alpha"}
    result = _reader_replay(composed, shifted, program, answer)
    assert result["status"] == "two_named_source_year_cells_independently_removed"
    assert len(result["cell_spans"]) == 2


def test_parser_rejects_wrong_answer_after_composition():
    context, layouts = _source()
    program = {
        "sides": [{"doc_id": "a", "label": "Alpha"}, {"doc_id": "d", "label": "Beta"}]
    }
    with pytest.raises(ValueError, match="disagrees with gold"):
        _reader_replay(
            context,
            layouts,
            program,
            {"years": {"Alpha": 1930, "Beta": 2001}, "earlier": "Alpha"},
        )


def test_parser_uses_frozen_alias_surface_in_named_source_page():
    context, layouts = _source()
    program = {
        "sides": [
            {"doc_id": "a", "label": "Alpha"},
            {"doc_id": "d", "label": "Canonical Beta"},
        ]
    }
    answer = {"years": {"Alpha": 1930, "Canonical Beta": 2000}, "earlier": "Alpha"}
    parsed = _reader_replay(
        context,
        layouts,
        program,
        answer,
        {"Alpha": ("Alpha",), "Canonical Beta": ("Canonical Beta", "Beta")},
    )
    assert len(parsed["cell_spans"]) == 2


def test_filler_selection_blocks_cross_split_title_and_source_overlap(monkeypatch):
    snapshots = {
        "target": {"documents": [{"doc_id": "a", "title": "Target", "text": "x"}]},
        "eval_other": {
            "documents": [
                {"doc_id": "b", "title": "Useful", "text": "body"},
                {"doc_id": "c", "title": "Shared", "text": "body"},
                {"doc_id": "d", "title": "Target", "text": "body"},
            ]
        },
        "train_other": {
            "documents": [{"doc_id": "e", "title": "Shared", "text": "body"}]
        },
    }
    monkeypatch.setattr(
        composer, "_snapshot", lambda _root, pin: snapshots[pin["path"]]
    )
    pool = {
        "sources": [
            {
                "name": name,
                "split": "train" if name == "train_other" else "eval",
                "topic": "schools",
                "snapshot": {"path": name},
            }
            for name in snapshots
        ]
    }
    candidates, rejects = composer._filler_candidates(
        pool, pool["sources"][0], snapshots["target"]
    )
    assert [row["title"] for row in candidates] == ["Useful"]
    assert rejects == Counter({"cross_split_title": 1, "source_page_overlap": 1})


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (32767, "lt32k"),
        (32768, "32k"),
        (65536, "64k"),
        (131072, "128k"),
        (262144, "over256k"),
    ],
)
def test_exact_length_bins(tokens, expected):
    assert _length_bin(tokens) == expected
