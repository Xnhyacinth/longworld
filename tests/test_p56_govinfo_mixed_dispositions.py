import json
from pathlib import Path

import pytest

from reports import p52_govinfo_bill_disposition_pipeline as p52


def test_disposition_policy_is_explicit_and_defaults_to_modified_only():
    assert p52._allowed_requested_dispositions({}) == {p52.MODIFIED}
    assert p52._allowed_requested_dispositions(
        {"requested_disposition_policy": "mixed_retained_modified"}
    ) == {p52.MODIFIED, p52.RETAINED}
    with pytest.raises(p52.P52Blocker):
        p52._allowed_requested_dispositions(
            {"requested_disposition_policy": "allow_anything"}
        )


def test_every_mixed_bucket_needs_both_labels_and_modified_cf_anchor():
    from test_p52_govinfo_bill_disposition import _fake_registered_source_state

    path = Path("configs/p52_govinfo_bill_disposition_registered_generation_v1.json")
    config = json.loads(path.read_text())
    config["requested_disposition_policy"] = "mixed_retained_modified"
    state = _fake_registered_source_state(path, fillers=0)
    for value in state["records"].values():
        value["oracle_canonical"] = p52.p49._canonical(value["oracle_text"])
    first = config["requested_keys"][0]
    source = state["records"][("enr", first)]
    target = state["records"][("law", first)]
    target["oracle_text"] = source["oracle_text"]
    target["oracle_canonical"] = source["oracle_canonical"]
    _, requests = p52._question(config, 2)
    p52._entries_for_requests(config, state, requests)
    with pytest.raises(p52.P52Blocker, match="both R and M"):
        p52._entries_for_requests(config, state, requests[:1])
    config["counterfactual_code"] = "D01"
    with pytest.raises(p52.P52Blocker, match="modified"):
        p52._entries_for_requests(config, state, requests)


def test_default_entry_bytes_are_unchanged():
    from test_p52_govinfo_bill_disposition import _fake_registered_source_state

    path = Path("configs/p52_govinfo_bill_disposition_registered_generation_v1.json")
    config = json.loads(path.read_text())
    state = _fake_registered_source_state(path, fillers=0)
    _, requests = p52._question(config, 2)
    assert (
        p52._sha256_bytes(
            p52._canonical_bytes(p52._entries_for_requests(config, state, requests))
        )
        == "660ebca19d3bbdbef375e98693968fb3548632c8fc771aac84ee8a2a640f6508"
    )


def test_seeded_keys_are_nested_mixed_and_order_independent():
    from reports.p56_govinfo_mixed_dispositions_20260906 import select_keys

    pools = {
        "R": [f"r{i:02d}" for i in range(20)],
        "M": [f"m{i:02d}" for i in range(40)],
    }
    specification = {
        "seed": 2026090601,
        "request_counts": [4, 8, 16],
        "retained_per_block": 1,
    }
    keys, labels, anchor = select_keys(pools, specification)
    assert select_keys(
        {k: list(reversed(v)) for k, v in pools.items()}, specification
    ) == (keys, labels, anchor)
    assert len(set(keys)) == 16
    for count in [4, 8, 16]:
        assert set(labels[:count]) == {"R", "M"}
        assert labels[:count].count("R") == count // 4
    assert labels[int(anchor[1:]) - 1] == "M"
    assert int(anchor[1:]) <= 4


def test_constant_baseline_reports_failure_on_mixed_answers():
    from reports.p56_govinfo_mixed_dispositions_20260906 import baseline_scores

    scores = baseline_scores('{"D01":"R","D02":"M","D03":"M","D04":"M"}')
    assert scores["always_M"] == {
        "exact_match": False,
        "correct_labels": 3,
        "total_labels": 4,
        "label_accuracy": 0.75,
    }
    assert scores["always_R"]["exact_match"] is False
    assert scores["fixed_D02_R_else_M"]["correct_labels"] == 2
