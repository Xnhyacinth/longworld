from reports import p52_govinfo_bill_disposition_pipeline as p52


def test_eas_eah_question_and_relations_use_actual_transition():
    config = {
        "bill_id": "118-HR-4366",
        "from_stage": "eas",
        "to_stage": "eah",
        "requested_keys": ["section:1", "section:2"],
    }
    question, requests = p52._question(config, 2)
    assert "EAS-to-EAH" in question
    assert "ENR-to-Public-Law" not in question
    assert all(
        ":eas-eah:" in item["relation_id"] for item in p52._relations(config, requests)
    )


def test_original_question_wording_preserved():
    config = {
        "bill_id": "118-HR-4366",
        "from_stage": "enr",
        "to_stage": "law",
        "requested_keys": ["section:1"],
    }
    question, _ = p52._question(config, 1)
    assert (
        question.splitlines()[0]
        == "Resolve each requested GovInfo ENR-to-Public-Law source-text disposition."
    )


def test_registered_prefix_build_preserves_three_band_default(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from test_p52_govinfo_bill_disposition import (
        _ArtifactCountTokenizer,
        _fake_registered_source_state,
        _set_registered_role_keys,
    )

    original = Path(
        "configs/p52_govinfo_bill_disposition_registered_generation_v1.json"
    )
    state = _fake_registered_source_state(original, fillers=140)
    _set_registered_role_keys(monkeypatch)
    monkeypatch.setattr(
        p52, "_verified_source_state", lambda _config, _preflight: state
    )
    monkeypatch.setattr(
        p52, "_load_tokenizer", lambda _config: _ArtifactCountTokenizer()
    )
    for buckets in (["32k"], ["32k", "64k"], ["32k", "64k", "128k"]):
        config = json.loads(original.read_text())
        config["generation_buckets"] = buckets
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config))
        parents, _, _, receipt = p52.build_registered_parents(path)
        assert [parent["length_bucket"] for parent in parents] == buckets
        assert receipt["parent_candidate_count"] == len(buckets)
        assert all(not parent["train_ready"] for parent in parents)


def test_invalid_generation_prefix_rejected(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import pytest
    from test_p52_govinfo_bill_disposition import (
        _ArtifactCountTokenizer,
        _fake_registered_source_state,
        _set_registered_role_keys,
    )

    original = Path(
        "configs/p52_govinfo_bill_disposition_registered_generation_v1.json"
    )
    state = _fake_registered_source_state(original, fillers=0)
    _set_registered_role_keys(monkeypatch)
    monkeypatch.setattr(
        p52, "_verified_source_state", lambda _config, _preflight: state
    )
    monkeypatch.setattr(
        p52, "_load_tokenizer", lambda _config: _ArtifactCountTokenizer()
    )
    for buckets in ([], ["64k"], ["32k", "128k"], ["32k", "32k"]):
        config = json.loads(original.read_text())
        config["generation_buckets"] = buckets
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config))
        with pytest.raises(p52.P52Blocker, match="nested prefix"):
            p52.build_registered_parents(path)


def test_freeze_is_bounded_and_independent_of_record_iteration_order():
    import json
    from copy import deepcopy
    from pathlib import Path

    from test_p52_govinfo_bill_disposition import _fake_registered_source_state

    from reports import p52_govinfo_geometry_screen_20260906 as screen

    class WordTokenizer:
        def encode(self, text, *, add_special_tokens):
            return text.split()

    path = Path("configs/p52_govinfo_bill_disposition_registered_generation_v1.json")
    config = json.loads(path.read_text())
    state = _fake_registered_source_state(path, fillers=0)
    for (stage, key), record in state["records"].items():
        record["oracle_canonical"] = f"{stage} {key} " + (
            "source original baseline value applies here"
            if stage == "enr"
            else "target revised alternative clause differs entirely"
        )
    combinations, deltas = screen.frozen_combinations(config, state, WordTokenizer())
    reordered = deepcopy(state)
    reordered["records"] = dict(reversed(list(reordered["records"].items())))
    assert screen.frozen_combinations(config, reordered, WordTokenizer()) == (
        combinations,
        deltas,
    )
    assert 0 < len(combinations) <= 12
    assert all(
        2 <= len(keys) <= 6 and len(set(keys)) == len(keys) for keys in combinations
    )
    anchors = {
        item["key"]
        for item in sorted(
            deltas,
            key=lambda item: (abs(item["required_only_shared_cf_delta"]), item["key"]),
        )[:3]
    }
    assert all(keys[1] in anchors for keys in combinations)


def test_growth_diagnostic_does_not_count_background_as_new_proof():
    from reports import p52_govinfo_geometry_screen_20260906 as screen

    result = {
        "packs": [
            {
                "bucket": bucket,
                "artifact_count": count,
                "essential_artifact_ids": ["transition", "source", "target"],
                "views": {
                    "full": {
                        "gold": {"D01": "M"},
                        "essential_positions": [
                            {"artifact_tokens": 17},
                            {"artifact_tokens": 101},
                            {"artifact_tokens": 203},
                        ],
                    }
                },
            }
            for bucket, count in [("64k", 80), ("128k", 160)]
        ]
    }
    annotated = screen.add_growth_diagnostics(result)
    diagnostic = annotated["packs"][1]["proof_growth_diagnostic"]
    assert diagnostic["necessary_event_delta"] == 0
    assert diagnostic["essential_document_token_delta"] == 0
    assert diagnostic["diagnostic"] == "NO_NEW_NECESSARY_EVENTS"
    assert diagnostic["shared_proof_growth_gate"] == "NOT_RUN"
