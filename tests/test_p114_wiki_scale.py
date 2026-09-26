"""The frozen Wiki scale shard retains only independently screened L1 rows."""

import json

from scripts.p114_wiki_scale import ROOT, compile


def test_frozen_source_to_reader_screen_replays() -> None:
    output = ROOT / "data/candidates/p114_wiki_scale_screen_v1"
    result = compile(ROOT / "configs/p114_wiki_scale_v1.json", output, verify_only=True)
    assert result["gross_native_views"] == 79
    assert result["candidate_views"] == result["independent_semantic_tasks"] == 61
    assert result["quality_decisions"] == {
        "answer_concentration_cap": 17,
        "answer_in_question": 1,
        "selected": 61,
    }
    assert result["operations"] == {"table_cell_lookup": 61}
    assert result["length_bins"] == {"lt32k": 57, "32k": 4}
    assert all(
        row["fact_spans"] == 1 and row["evidence_to_query_tokens"] < 1000
        for row in result["long_32k_rows"]
    )
    decisions = [
        json.loads(line)
        for line in (output / "decisions.jsonl").read_text().splitlines()
    ]
    assert (
        next(
            row for row in decisions if row["sample_id"] == "p75-93113e08977d034ac183"
        )["reason"]
        == "answer_in_question"
    )
