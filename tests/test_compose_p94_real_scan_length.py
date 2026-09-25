import pytest

from scripts.compose_p94_real_scan_length import _append, _scan_replay


def _source():
    table = "Name | Established\n" + "\n".join(
        f"School {i} | {1900 + i * 10}" for i in range(8)
    )
    header = "[target] List of schools in Example\n"
    context = header + table
    layout = {
        "doc_id": "target",
        "title": "List of schools in Example",
        "text_start": len(header),
        "text_end": len(context),
    }
    program = {
        "op": "closed_table_interval",
        "doc_id": "target",
        "year_column": "Established",
        "low": 1910,
        "high": 1940,
        "candidate_fact_ids": [str(i) for i in range(8)],
    }
    answer = {"count": 4, "entries": [f"School {i}" for i in range(1, 5)]}
    return context, layout, program, answer


def test_appended_whole_page_does_not_change_named_table_scan():
    context, layout, program, answer = _source()
    filler = {
        "doc_id": "other",
        "title": "List of schools elsewhere",
        "text": "Name | Established\nOther School | 1920",
    }
    composed, filler_layouts = _append(context, [filler])
    assert composed[: len(context)] == context
    assert (
        composed[filler_layouts[0]["text_start"] : filler_layouts[0]["text_end"]]
        == filler["text"]
    )
    replay = _scan_replay(composed, layout, program, answer, expected_candidates=8)
    assert replay["status"] == "scoped_named_table_hit_and_near_miss_replayed"
    assert replay["hit_answer"]["count"] == 5
    assert replay["near_miss_answer"] == answer
    assert len(replay["year_cell_spans"]) == 8


def test_scan_replay_rejects_changed_gold_and_candidate_universe():
    context, layout, program, answer = _source()
    with pytest.raises(ValueError, match="answer differs"):
        _scan_replay(
            context,
            layout,
            program,
            {"count": 3, "entries": answer["entries"]},
            expected_candidates=8,
        )
    with pytest.raises(ValueError, match="candidate universe differs"):
        _scan_replay(context, layout, program, answer, expected_candidates=9)
