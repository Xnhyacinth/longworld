from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

import longworld.core.macrovintage as macro_vintage_module
from longworld.core.macrovintage import (
    MACRO_VINTAGE_PACKING_PLAN_SCHEMA,
    audit_macro_vintage_pipeline_candidate,
    build_macro_vintage_pipeline_candidates,
    replay_macro_vintage_pipeline_raw_slice,
    replay_macro_vintage_pipeline_selection,
)
from longworld.core.macrovintageworkflow import (
    MACRO_VINTAGE_PARSER_REVISION,
    MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA,
    MAX_MACRO_VINTAGE_WORKFLOW_MANIFEST_BYTES,
    build_macro_verified_packing_plan,
)
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.provenance import ProvenanceError
from scripts.materialize_macro_vintage_histories import _read_json


def _token_count(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _observation(
    series_id: str, period: str, vintage_date: str, value: str, index: int
) -> dict[str, object]:
    observation_id = f"bea:{series_id.lower()}:{period}:{vintage_date}:row{index}"
    text = json.dumps(
        {
            "available_at": vintage_date,
            "estimate_label": f"estimate-{index}",
            "period": period,
            "series_id": series_id,
            "unit": "billions_current_dollars",
            "value": value,
            "vintage_date": vintage_date,
        },
        sort_keys=True,
    )
    return {
        "observation_id": observation_id,
        "record_kind": "macro_vintage_observation",
        "series_id": series_id,
        "period": period,
        "estimate_label": f"estimate-{index}",
        "vintage_date": vintage_date,
        "available_at": vintage_date,
        "value": value,
        "unit": "billions_current_dollars",
        "release_date_text": f"release {vintage_date} unique observation {index}",
        "source_sha256": "b" * 64,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "provenance": {
            "parser": MACRO_VINTAGE_PARSER_REVISION,
            "sheet_name": "Vintage History",
            "sheet_part": "xl/worksheets/sheet1.xml",
            "row_number": index,
            "period_cell": f"A{index}",
            "estimate_label_cell": f"B{index}",
            "value_cell": f"C{index}",
            "release_date_cell": f"G{index}",
        },
        "source_status": "local_probe_public_download",
        "source_origin": "local_probe_public_endpoint_observation",
        "source_url": "https://apps.bea.gov/national/xls/gdp-gdi-vintage-history.xlsx",
        "retrieval_url": "https://apps.bea.gov/national/xls/gdp-gdi-vintage-history.xlsx",
        "license": "public domain",
        "terms_url": "https://www.bea.gov/help/faq/147",
        "attribution": "U.S. Bureau of Economic Analysis",
    }


def _workflow_manifest(
    *,
    target_vintage_count: int = 4,
    trajectory_count: int = 22,
    series_id: str = "BEA_GDP_CURRENT_DOLLARS",
    row_start: int = 10,
) -> dict[str, object]:
    observations: list[dict[str, object]] = []
    relations: list[dict[str, object]] = []
    trajectories: list[dict[str, object]] = []
    start = date(2020, 1, 1)
    row = row_start
    for trajectory_index in range(trajectory_count):
        period = f"{2020 + trajectory_index // 4}Q{trajectory_index % 4 + 1}"
        if trajectory_index == 0 and target_vintage_count == 10:
            values = [
                "100",
                "101",
                "101",
                "103",
                "103",
                "105",
                "106",
                "106",
                "108",
                "109",
            ]
        else:
            values = ["100", "101", "101", str(103 + trajectory_index)]
        selected: list[dict[str, object]] = []
        for vintage_index, value in enumerate(values):
            vintage = (start + timedelta(days=30 * vintage_index + trajectory_index)).isoformat()
            selected.append(_observation(series_id, period, vintage, value, row))
            row += 1
        observations.extend(selected)
        relation_ids: list[str] = []
        for prior, current in pairwise(selected):
            relation_id = (
                f"bea:transition:{series_id.lower()}:{period}:"
                f"{prior['vintage_date']}:{current['vintage_date']}"
            )
            relation_ids.append(relation_id)
            changed = current["value"] != prior["value"]
            relations.append(
                {
                    "relation_id": relation_id,
                    "kind": (
                        "revises_observation"
                        if changed
                        else "supersedes_without_observed_value_change"
                    ),
                    "answer_value_changed": changed,
                    "source_release_date_text": current["release_date_text"],
                    "series_id": series_id,
                    "period": period,
                    "source_observation_id": current["observation_id"],
                    "target_observation_id": prior["observation_id"],
                    "relation_provenance": "verified_derived_temporal_same_series",
                    "evidence": [
                        {
                            "observation_id": prior["observation_id"],
                            "cell_reference": prior["provenance"]["value_cell"],
                            "source_sha256": "b" * 64,
                        },
                        {
                            "observation_id": current["observation_id"],
                            "cell_reference": current["provenance"]["value_cell"],
                            "source_sha256": "b" * 64,
                        },
                    ],
                }
            )
        trajectories.append(
            {
                "trajectory_id": f"bea:trajectory:{series_id.lower()}:{period}",
                "series_id": series_id,
                "period": period,
                "observation_ids": [item["observation_id"] for item in selected],
                "relation_ids": relation_ids,
            }
        )
    return {
        "schema_version": MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA,
        "raw_source_sha256": "b" * 64,
        "fetch_inventory_sha256": "f" * 64,
        "fetch_receipt": {
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:00:01Z",
            "retrieval": {
                "requested_url": "https://apps.bea.gov/national/xls/gdp-gdi-vintage-history.xlsx",
                "final_url": "https://apps.bea.gov/national/xls/gdp-gdi-vintage-history.xlsx",
                "status": 200,
                "raw_bytes": 1_024,
                "sha256": "b" * 64,
            },
        },
        "generated_at": "2026-01-01T00:00:02Z",
        "parser": MACRO_VINTAGE_PARSER_REVISION,
        "authorization": {"record_id": "bea-public-probe-1"},
        "observations": observations,
        "relations": relations,
        "trajectories": trajectories,
    }


def test_background_packing_stays_within_the_target_series() -> None:
    manifest = _workflow_manifest(trajectory_count=22)
    unrelated = _workflow_manifest(
        trajectory_count=22,
        series_id="ZZZ_UNRELATED_SERIES",
        row_start=10_000,
    )
    for field in ("observations", "relations", "trajectories"):
        manifest[field].extend(unrelated[field])

    [candidate] = build_macro_vintage_pipeline_candidates(
        manifest,
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-same-series-background-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )

    assert all(
        ":bea_gdp_current_dollars:" in trajectory_id
        for trajectory_id in candidate["background_trajectory_ids"]
    )


def test_audit_rejects_cross_series_serialized_background_observation() -> None:
    [candidate] = build_macro_vintage_pipeline_candidates(
        _workflow_manifest(),
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-cross-series-audit-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    corrupted = deepcopy(candidate)
    background_ids = {
        artifact_id
        for trajectory_id in corrupted["background_trajectory_ids"]
        for artifact_id in corrupted["trajectory_prefix_artifact_ids"][trajectory_id]
    }
    records = [
        json.loads(value) for value in corrupted["document_context"].split(SEP)
    ]
    index = next(
        position
        for position, record in enumerate(records)
        if record["record_type"] == "macro_vintage_observation"
        and record["artifact_id"] in background_ids
    )
    records[index]["source_payload"]["series_id"] = "ZZZ_UNRELATED_SERIES"
    documents = [
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for value in records
    ]
    corrupted["document_context"] = SEP.join(documents)
    corrupted["context"] = wrap_prompt(
        corrupted["question"], corrupted["document_context"], "first"
    )
    corrupted["artifact_classification"][index]["provenance_id"] = (
        "sha256:" + hashlib.sha256(documents[index].encode()).hexdigest()
    )

    audit = audit_macro_vintage_pipeline_candidate(corrupted)

    assert audit["trajectory_prefix_packing_valid"] is False
    assert not all(audit.values())


@pytest.mark.parametrize(
    "raw, message",
    [
        ('{"world_id":"first","world_id":"second"}', "duplicate JSON key"),
        ('{"value":NaN}', "non-finite JSON value"),
    ],
)
def test_macro_materializer_json_reader_rejects_ambiguous_values(
    tmp_path: Path, raw: str, message: str
) -> None:
    path = tmp_path / "ambiguous.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ProvenanceError, match=message):
        _read_json(path, 1_024)


def test_builds_real_ordered_16k_path_and_replays_changed_and_unchanged_edges() -> None:
    [candidate] = build_macro_vintage_pipeline_candidates(
        _workflow_manifest(),
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )

    artifact_ids = [item["artifact_id"] for item in candidate["artifact_classification"]]
    assert len(artifact_ids) == len(set(artifact_ids))
    assert 16_000 <= candidate["tokenizer_context_tokens"] <= 16_384
    assert 16_000 <= candidate["tokenizer_document_context_tokens"] <= 16_384
    assert candidate["length_fill_method"] == "distinct_chronological_source_records"
    assert candidate["promotion_blocker_code"] == "missing_source_sidecar"
    assert candidate["pipeline_capabilities"]["generic_strict_replay"] is False
    assert candidate["pipeline_capabilities"]["generic_promotion"] is False
    assert len(candidate["background_trajectory_ids"]) >= 2
    assert all(
        length >= 1
        for trajectory_id, length in candidate["trajectory_prefix_lengths"].items()
        if trajectory_id not in candidate["target_trajectory_ids"]
    )
    assert len(candidate["essential_artifact_ids"]) == 7
    assert any(
        item["evidence_role"] == "natural_background"
        for item in candidate["artifact_classification"]
    )
    assert "supersedes_without_observed_value_change" in candidate["answer"]
    assert candidate["query_timing"] == "first"
    assert "BEA_GDP_CURRENT_DOLLARS" in candidate["question"]
    assert "2020Q1" in candidate["question"]
    assert candidate["macro_task"]["decision_date"] in candidate["question"]
    assert candidate["context"] == wrap_prompt(
        candidate["question"], candidate["document_context"], "first"
    )
    assert candidate["tokenizer_essential_span_tokens"] >= 8_193
    assert candidate["minimum_essential_span_tokens"] == 8_193
    assert replay_macro_vintage_pipeline_selection(candidate, artifact_ids)["answer"] == candidate["answer"]
    assert (
        replay_macro_vintage_pipeline_selection(
            candidate, artifact_ids, counterfactual=True
        )["answer"]
        == candidate["cf_answer"]
    )
    assert candidate["answer"] != candidate["cf_answer"]
    assert all(audit_macro_vintage_pipeline_candidate(candidate).values())


@pytest.mark.parametrize("field", ["question", "context"])
def test_macro_audit_rejects_serialized_prompt_corruption(field: str) -> None:
    [candidate] = build_macro_vintage_pipeline_candidates(
        _workflow_manifest(),
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-prompt-corruption-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    corrupted = dict(candidate)
    corrupted[field] = "X" * len(str(candidate[field]))

    audit = audit_macro_vintage_pipeline_candidate(corrupted)

    assert audit["serialized_prompt_valid"] is False
    assert not all(audit.values())

    records = [json.loads(value) for value in candidate["document_context"].split(SEP)]
    selected_ids = {value["artifact_id"] for value in records}
    for record in records:
        if record["record_type"] == "macro_vintage_relation":
            relation = record["source_payload"]
            assert relation["source_observation_id"] in selected_ids
            assert relation["target_observation_id"] in selected_ids

    observation = next(
        value
        for value in records
        if value["record_type"] == "macro_vintage_observation"
    )["source_payload"]
    assert "text" not in observation
    assert "text_sha256" not in observation
    assert "source_url" not in observation
    assert "license" not in observation


def test_selection_replay_reconstructs_only_from_selected_document_bytes() -> None:
    [candidate] = build_macro_vintage_pipeline_candidates(
        _workflow_manifest(),
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-selection-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    selected = list(candidate["essential_artifact_ids"])
    expected = replay_macro_vintage_pipeline_selection(candidate, selected)
    assert expected["answer"] == candidate["answer"]

    forged = dict(candidate)
    forged["essential_artifact_ids"] = []
    assert replay_macro_vintage_pipeline_selection(forged, selected) == expected

    records = {
        value["artifact_id"]: value
        for value in map(json.loads, candidate["document_context"].split(SEP))
    }
    removed_by_kind: dict[str, str] = {}
    for artifact_id in selected:
        record = records[artifact_id]
        if record["record_type"] == "macro_vintage_observation":
            removed_by_kind.setdefault("observation", artifact_id)
        else:
            relation_kind = record["source_payload"]["kind"]
            removed_by_kind.setdefault(relation_kind, artifact_id)
    assert {
        "observation",
        "revises_observation",
        "supersedes_without_observed_value_change",
    } <= set(removed_by_kind)
    for artifact_id in removed_by_kind.values():
        replay = replay_macro_vintage_pipeline_selection(
            candidate, [value for value in selected if value != artifact_id]
        )
        assert replay["answer"] == "unknown"


def test_replay_rejects_rebound_fabricated_source_and_relation_semantics() -> None:
    [candidate] = build_macro_vintage_pipeline_candidates(
        _workflow_manifest(),
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-provenance-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )

    def mutate(record_type: str, **updates: object) -> dict[str, object]:
        corrupted = dict(candidate)
        records = [
            json.loads(value) for value in candidate["document_context"].split(SEP)
        ]
        index = next(
            position
            for position, value in enumerate(records)
            if value["record_type"] == record_type
            and value["artifact_id"] in candidate["essential_artifact_ids"]
        )
        records[index]["source_payload"].update(updates)
        documents = [
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for value in records
        ]
        classifications = [dict(value) for value in candidate["artifact_classification"]]
        classifications[index]["provenance_id"] = (
            "sha256:" + hashlib.sha256(documents[index].encode()).hexdigest()
        )
        corrupted["document_context"] = SEP.join(documents)
        corrupted["artifact_classification"] = classifications
        return corrupted

    wrong_source = mutate("macro_vintage_observation", source_sha256="e" * 64)
    fabricated_relation = mutate(
        "macro_vintage_relation",
        relation_provenance="fabricated",
        answer_value_changed=False,
    )
    relation_record = next(
        value
        for value in map(json.loads, candidate["document_context"].split(SEP))
        if value["record_type"] == "macro_vintage_relation"
        and value["artifact_id"] in candidate["essential_artifact_ids"]
    )
    wrong_cell_evidence = json.loads(
        json.dumps(relation_record["source_payload"]["evidence"])
    )
    wrong_cell_evidence[0]["cell_reference"] = "ZZZ"
    wrong_cell = mutate("macro_vintage_relation", evidence=wrong_cell_evidence)
    assert not all(audit_macro_vintage_pipeline_candidate(wrong_source).values())
    assert not all(audit_macro_vintage_pipeline_candidate(fabricated_relation).values())
    assert not all(audit_macro_vintage_pipeline_candidate(wrong_cell).values())


def test_raw_slice_requires_complete_approved_records_and_corruption_fails() -> None:
    [candidate] = build_macro_vintage_pipeline_candidates(
        _workflow_manifest(),
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-raw-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    documents = candidate["document_context"].split(SEP)
    by_id = {
        classification["artifact_id"]: document
        for classification, document in zip(
            candidate["artifact_classification"], documents, strict=True
        )
    }
    minimal_raw = SEP.join(
        by_id[value] for value in candidate["essential_artifact_ids"]
    )

    assert (
        replay_macro_vintage_pipeline_raw_slice(
            candidate,
            minimal_raw,
            left_framed=True,
            right_framed=True,
        )["answer"]
        == candidate["answer"]
    )
    assert (
        replay_macro_vintage_pipeline_raw_slice(
            candidate,
            minimal_raw[17:],
            left_framed=False,
            right_framed=True,
        )["answer"]
        != candidate["answer"]
    )

    corrupted = dict(candidate)
    corrupted["document_context"] = candidate["document_context"].replace(
        '"value":"103"', '"value":"999"', 1
    )
    assert not all(audit_macro_vintage_pipeline_candidate(corrupted).values())


def test_prefix_packing_skips_an_oversized_early_trajectory_deterministically() -> None:
    manifest = _workflow_manifest()
    oversized_trajectory = next(
        value
        for value in manifest["trajectories"]
        if value["period"] == "2020Q2"
    )
    oversized_ids = set(oversized_trajectory["observation_ids"])
    for observation in manifest["observations"]:
        if observation["observation_id"] in oversized_ids:
            observation["release_date_text"] += " " + " ".join(
                f"release-detail-{index}" for index in range(5_000)
            )

    def build() -> dict:
        [candidate] = build_macro_vintage_pipeline_candidates(
            manifest,
            workflow_manifest_sha256="a" * 64,
            world_id="bea-macro-vintage-gap-test",
            target_series_id="BEA_GDP_CURRENT_DOLLARS",
            target_period="2020Q1",
            bands=(("16k", 16_000, 16_384),),
            token_counter=_token_count,
            tokenizer_model_id="Qwen/Qwen3.5-4B",
            tokenizer_revision="c" * 40,
            tokenizer_asset_manifest_sha256="d" * 64,
        )
        return candidate

    first = build()
    second = build()
    assert oversized_trajectory["trajectory_id"] not in first[
        "background_trajectory_ids"
    ]
    assert len(first["background_trajectory_ids"]) >= 2
    assert first["trajectory_prefix_lengths"] == second["trajectory_prefix_lengths"]
    assert first["document_context"] == second["document_context"]


def test_prefix_packing_preserves_the_reference_selection_and_bytes() -> None:
    [candidate] = build_macro_vintage_pipeline_candidates(
        _workflow_manifest(),
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-equivalence-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )

    selection = json.dumps(
        candidate["trajectory_prefix_lengths"],
        sort_keys=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(selection.encode()).hexdigest() == (
        "4344c6f0d2a3b690ea4d1050c1a683490377c61a2af14e7ef7c53323f7f12b18"
    )
    assert hashlib.sha256(candidate["document_context"].encode()).hexdigest() == (
        "f61ed66dcc956ee1bd147781e6a48c93b9d38e8e8caace47c1a8a3306d089d0d"
    )
    # The reference frontier keeps cheap early prefixes while selecting full
    # extensions from trajectories processed near the end of sorted order.
    # This makes the golden sensitive to pruning before a group is exhausted.
    assert candidate["trajectory_prefix_lengths"][
        "bea:trajectory:bea_gdp_current_dollars:2020Q2"
    ] == 1
    assert candidate["trajectory_prefix_lengths"][
        "bea:trajectory:bea_gdp_current_dollars:2023Q3"
    ] == 4


def test_multiband_target_revision_path_grows_strict_semantic_support() -> None:
    candidates = build_macro_vintage_pipeline_candidates(
        _workflow_manifest(target_vintage_count=10, trajectory_count=50),
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-semantic-growth-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(
            ("16k", 16_000, 16_384),
            ("32k", 32_000, 32_768),
            ("64k", 64_000, 65_536),
        ),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )

    target_trajectory_id = (
        "bea:trajectory:bea_gdp_current_dollars:2020Q1"
    )
    assert [
        value["trajectory_prefix_lengths"][target_trajectory_id]
        for value in candidates
    ] == [4, 7, 10]
    assert [len(value["essential_artifact_ids"]) for value in candidates] == [
        7,
        13,
        19,
    ]
    assert [value["strict_support_event_count"] for value in candidates] == [
        4,
        7,
        10,
    ]
    assert [value["graph"]["proof_depth"] for value in candidates] == [4, 7, 10]
    assert [
        len(value["authentic_source_relation_edges"]) for value in candidates
    ] == [4, 7, 10]
    assert [len(value["source_relation_ids"]) for value in candidates] == [
        7,
        13,
        19,
    ]
    document_ids = [
        {item["artifact_id"] for item in value["artifact_classification"]}
        for value in candidates
    ]
    assert document_ids[0] < document_ids[1] < document_ids[2]
    for candidate in candidates:
        steps = json.loads(candidate["answer"])["revision_steps"]
        assert {value["relation_kind"] for value in steps} == {
            "revises_observation",
            "supersedes_without_observed_value_change",
        }
        assert steps[-1]["relation_kind"] == "revises_observation"
        assert candidate["length_fill_method"] == (
            "distinct_chronological_source_records"
        )
        assert all(audit_macro_vintage_pipeline_candidate(candidate).values())


def test_verified_packing_plan_skips_dp_and_rebuilds_identical_candidate(
    monkeypatch,
) -> None:
    kwargs = {
        "manifest": _workflow_manifest(),
        "workflow_manifest_sha256": "a" * 64,
        "world_id": "bea-macro-vintage-packing-plan-test",
        "target_series_id": "BEA_GDP_CURRENT_DOLLARS",
        "target_period": "2020Q1",
        "bands": (("16k", 16_000, 16_384),),
        "token_counter": _token_count,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "c" * 40,
        "tokenizer_asset_manifest_sha256": "d" * 64,
    }
    reference = build_macro_vintage_pipeline_candidates(**kwargs)
    candidate = reference[0]
    packing_plan = {
        "schema_version": MACRO_VINTAGE_PACKING_PLAN_SCHEMA,
        "workflow_manifest_sha256": "a" * 64,
        "world_id": "bea-macro-vintage-packing-plan-test",
        "target_series_id": "BEA_GDP_CURRENT_DOLLARS",
        "target_period": "2020Q1",
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "c" * 40,
        "tokenizer_asset_manifest_sha256": "d" * 64,
        "bands": [
            {
                "length_bucket": "16k",
                "band_lower_tokens": 16_000,
                "band_upper_tokens": 16_384,
                "trajectory_prefix_lengths": candidate[
                    "trajectory_prefix_lengths"
                ],
                "artifact_ids": [
                    value["artifact_id"]
                    for value in candidate["artifact_classification"]
                ],
                "document_context_sha256": hashlib.sha256(
                    candidate["document_context"].encode()
                ).hexdigest(),
                "context_tokens": candidate["tokenizer_context_tokens"],
            }
        ],
    }

    def unexpected_dp(*_args, **_kwargs):
        raise AssertionError("packing DP must not run for a verified plan")

    monkeypatch.setattr(macro_vintage_module, "bisect_right", unexpected_dp)
    rebuilt = build_macro_vintage_pipeline_candidates(
        **kwargs,
        verified_packing_plan=packing_plan,
    )

    assert rebuilt == reference


def test_verified_packing_plan_drift_fails_closed() -> None:
    kwargs = {
        "manifest": _workflow_manifest(),
        "workflow_manifest_sha256": "a" * 64,
        "world_id": "bea-macro-vintage-packing-plan-drift-test",
        "target_series_id": "BEA_GDP_CURRENT_DOLLARS",
        "target_period": "2020Q1",
        "bands": (("16k", 16_000, 16_384),),
        "token_counter": _token_count,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "c" * 40,
        "tokenizer_asset_manifest_sha256": "d" * 64,
    }
    [candidate] = build_macro_vintage_pipeline_candidates(**kwargs)
    packing_plan = {
        "schema_version": MACRO_VINTAGE_PACKING_PLAN_SCHEMA,
        "workflow_manifest_sha256": "a" * 64,
        "world_id": "bea-macro-vintage-packing-plan-drift-test",
        "target_series_id": "BEA_GDP_CURRENT_DOLLARS",
        "target_period": "2020Q1",
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "c" * 40,
        "tokenizer_asset_manifest_sha256": "d" * 64,
        "bands": [
            {
                "length_bucket": "16k",
                "band_lower_tokens": 16_000,
                "band_upper_tokens": 16_384,
                "trajectory_prefix_lengths": candidate[
                    "trajectory_prefix_lengths"
                ],
                "artifact_ids": [
                    value["artifact_id"]
                    for value in candidate["artifact_classification"]
                ],
                "document_context_sha256": "0" * 64,
                "context_tokens": candidate["tokenizer_context_tokens"],
            }
        ],
    }

    with pytest.raises(ProvenanceError, match="packing plan"):
        build_macro_vintage_pipeline_candidates(
            **kwargs,
            verified_packing_plan=packing_plan,
        )


def test_verified_packing_plan_rejects_cross_series_prefix() -> None:
    manifest = _workflow_manifest(trajectory_count=22)
    unrelated = _workflow_manifest(
        trajectory_count=22,
        series_id="ZZZ_UNRELATED_SERIES",
        row_start=10_000,
    )
    for field in ("observations", "relations", "trajectories"):
        manifest[field].extend(unrelated[field])
    kwargs = {
        "manifest": manifest,
        "workflow_manifest_sha256": "a" * 64,
        "world_id": "bea-macro-vintage-cross-series-cache-test",
        "target_series_id": "BEA_GDP_CURRENT_DOLLARS",
        "target_period": "2020Q1",
        "bands": (("16k", 16_000, 16_384),),
        "token_counter": _token_count,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "c" * 40,
        "tokenizer_asset_manifest_sha256": "d" * 64,
    }
    [candidate] = build_macro_vintage_pipeline_candidates(**kwargs)
    packing_plan = build_macro_verified_packing_plan(
        [candidate], bands=kwargs["bands"]
    )
    prefixes = packing_plan["bands"][0]["trajectory_prefix_lengths"]
    replaced = next(
        value
        for value in candidate["background_trajectory_ids"]
        if value in prefixes
    )
    length = prefixes.pop(replaced)
    unrelated_trajectory_id = next(
        value["trajectory_id"]
        for value in unrelated["trajectories"]
        if value["period"] == "2020Q2"
    )
    prefixes[unrelated_trajectory_id] = length

    with pytest.raises(ProvenanceError, match="semantic prefix"):
        build_macro_vintage_pipeline_candidates(
            **kwargs,
            verified_packing_plan=packing_plan,
        )


def test_exact_token_prefilter_does_not_replay_an_over_upper_finalist(
    monkeypatch,
) -> None:
    manifest = _workflow_manifest()
    [reference] = build_macro_vintage_pipeline_candidates(
        manifest,
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-prefilter-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=_token_count,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )
    rejected_exact_texts = {
        reference["document_context"],
        reference["context"],
    }

    def reject_reference_finalist(value: str) -> int:
        if value in rejected_exact_texts:
            return 16_385
        return _token_count(value)

    replay_calls = 0
    replay = macro_vintage_module.replay_macro_vintage_pipeline_selection

    def counting_replay(*args, **kwargs):
        nonlocal replay_calls
        replay_calls += 1
        return replay(*args, **kwargs)

    monkeypatch.setattr(
        macro_vintage_module,
        "replay_macro_vintage_pipeline_selection",
        counting_replay,
    )
    [candidate] = build_macro_vintage_pipeline_candidates(
        manifest,
        workflow_manifest_sha256="a" * 64,
        world_id="bea-macro-vintage-prefilter-test",
        target_series_id="BEA_GDP_CURRENT_DOLLARS",
        target_period="2020Q1",
        bands=(("16k", 16_000, 16_384),),
        token_counter=reject_reference_finalist,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
        tokenizer_asset_manifest_sha256="d" * 64,
    )

    assert candidate["document_context"] != reference["document_context"]
    assert replay_calls == 2 + 3 + len(candidate["essential_artifact_ids"])


def test_materializer_accepts_bounded_workflow_larger_than_generic_manifest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({"body": "x" * 2_100_000}))

    loaded, raw = _read_json(
        path, MAX_MACRO_VINTAGE_WORKFLOW_MANIFEST_BYTES
    )

    assert loaded["body"].startswith("x")
    assert len(raw) > 2_000_000
