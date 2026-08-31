from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import longworld.core.macrovintage as macrovintage_module
from longworld.core.gamecompat import (
    GAME_COMPAT_SOURCE_SCHEMA,
    audit_game_compatibility_task,
    build_game_compatibility_task,
    replay_game_compatibility_task,
)
from longworld.core.macrovintage import (
    MACRO_VINTAGE_SOURCE_SCHEMA,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    audit_macro_vintage_task,
    build_macro_vintage_task,
    replay_macro_vintage_task,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.visualcompat import (
    VISUAL_COMPAT_SOURCE_SCHEMA,
    audit_visual_compatibility_task,
    build_visual_compatibility_task,
    replay_visual_compatibility_task,
)
from scripts.export_static_domain_task import export_static_domain_task


def _record(
    record_id: str,
    record_kind: str,
    occurred_at: str,
    facts: dict[str, str],
) -> dict[str, Any]:
    lines = [f"{field}: {value}" for field, value in facts.items()]
    text = "\n".join(lines)
    cursor = 0
    bound_facts = []
    for index, (field, value) in enumerate(facts.items()):
        quote = lines[index]
        bound_facts.append(
            {
                "fact_id": f"{record_id}:{field}",
                "field": field,
                "value": value,
                "evidence_quote": quote,
                "char_start": cursor,
                "char_end": cursor + len(quote),
            }
        )
        cursor += len(quote) + 1
    return {
        "record_id": record_id,
        "record_kind": record_kind,
        "occurred_at": occurred_at,
        "source_family": "unit_test_real_source_contract",
        "source_origin": "real_public",
        "source_url": f"https://example.test/source/{record_id}",
        "retrieval_url": f"https://example.test/source/{record_id}",
        "source_sha256": hashlib.sha256(f"raw:{record_id}".encode()).hexdigest(),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
        "facts": bound_facts,
    }


def _relation(
    relation_id: str,
    kind: str,
    source: dict[str, Any],
    target: dict[str, Any],
    source_facts: tuple[str, ...],
    target_facts: tuple[str, ...],
    *,
    provenance: str,
) -> dict[str, Any]:
    return {
        "relation_id": relation_id,
        "kind": kind,
        "source_record_id": source["record_id"],
        "target_record_id": target["record_id"],
        "relation_provenance": provenance,
        "evidence": [
            {
                "record_id": source["record_id"],
                "fact_ids": sorted(
                    f"{source['record_id']}:{source_fact}"
                    for source_fact in source_facts
                ),
            },
            {
                "record_id": target["record_id"],
                "fact_ids": sorted(
                    f"{target['record_id']}:{target_fact}"
                    for target_fact in target_facts
                ),
            },
        ],
    }


def _macro_payload() -> dict[str, Any]:
    first = _record(
        "gdp-2025q1-v1",
        "macro_vintage_observation",
        "2025-04-30",
        {
            "series_id": "GDPA",
            "period": "2025-Q1",
            "vintage_date": "2025-04-30",
            "available_at": "2025-04-30",
            "value": "101.25",
        },
    )
    second = _record(
        "gdp-2025q1-v2",
        "macro_vintage_observation",
        "2025-05-29",
        {
            "series_id": "GDPA",
            "period": "2025-Q1",
            "vintage_date": "2025-05-29",
            "available_at": "2025-05-29",
            "value": "102.00",
        },
    )
    future = _record(
        "gdp-2025q1-v3",
        "macro_vintage_observation",
        "2025-06-26",
        {
            "series_id": "GDPA",
            "period": "2025-Q1",
            "vintage_date": "2025-06-26",
            "available_at": "2025-06-26",
            "value": "103.50",
        },
    )
    relations = [
        _relation(
            "gdp-revision-v2-v1",
            "revises_observation",
            second,
            first,
            ("value",),
            ("value",),
            provenance="verified_derived_temporal_same_series",
        ),
        _relation(
            "gdp-revision-v3-v2",
            "revises_observation",
            future,
            second,
            ("value",),
            ("value",),
            provenance="verified_derived_temporal_same_series",
        ),
    ]
    return {
        "schema_version": MACRO_VINTAGE_SOURCE_SCHEMA,
        "world_id": "macro-gdp-vintage-test",
        "source_records": [first, second, future],
        "source_relations": relations,
        "query": {
            "series_id": "GDPA",
            "period": "2025-Q1",
            "decision_date": "2025-06-01",
        },
        "counterfactual_twin": {
            "record_id": second["record_id"],
            "fact_id": f"{second['record_id']}:value",
            "parent_value": "102.00",
            "value": "99.00",
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_exact_fact",
        },
    }


def _visual_payload() -> dict[str, Any]:
    model = _record(
        "model-revision-a1",
        "visual_model_revision",
        "2026-01-10",
        {
            "model_id": "org/model-a",
            "model_revision": "a1b2c3d4",
            "component_name": "diffusers",
            "min_component_version": "0.31.0",
            "max_component_version": "0.35.2",
            "available_at": "2026-01-10",
        },
    )
    release = _record(
        "diffusers-release-0341",
        "visual_component_release",
        "2026-02-01",
        {
            "component_name": "diffusers",
            "component_version": "0.34.1",
            "released_at": "2026-02-01",
        },
    )
    relation = _relation(
        "model-a1-evaluated-diffusers-0341",
        "compatibility_evaluated_against",
        model,
        release,
        ("min_component_version", "max_component_version"),
        ("component_version",),
        provenance="verified_derived_cross_source",
    )
    return {
        "schema_version": VISUAL_COMPAT_SOURCE_SCHEMA,
        "world_id": "visual-model-component-test",
        "source_records": [model, release],
        "source_relations": [relation],
        "query": {
            "model_record_id": model["record_id"],
            "release_record_id": release["record_id"],
            "decision_date": "2026-03-01",
        },
        "counterfactual_twin": {
            "record_id": release["record_id"],
            "fact_id": f"{release['record_id']}:component_version",
            "parent_value": "0.34.1",
            "value": "0.40.0",
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_exact_fact",
        },
    }


def _game_payload() -> dict[str, Any]:
    release = _record(
        "game-release-430",
        "game_release",
        "2026-02-01",
        {
            "project": "example-game",
            "version": "4.3.0",
            "released_at": "2026-02-01",
            "min_save_schema": "12",
            "max_save_schema": "14",
            "protocol_version": "9",
        },
    )
    save = _record(
        "save-slot-17",
        "game_save_observation",
        "2026-02-10",
        {
            "project": "example-game",
            "save_id": "slot-17",
            "save_schema": "13",
            "created_by_release": "4.2.0",
            "observed_at": "2026-02-10",
        },
    )
    peer = _record(
        "server-east-observation",
        "game_protocol_observation",
        "2026-02-11",
        {
            "project": "example-game",
            "endpoint_id": "server-east",
            "protocol_version": "9",
            "observed_at": "2026-02-11",
        },
    )
    save_relation = _relation(
        "release-430-save-17",
        "save_compatibility_evaluated_against",
        release,
        save,
        ("min_save_schema", "max_save_schema"),
        ("save_schema",),
        provenance="verified_derived_cross_source",
    )
    protocol_relation = _relation(
        "release-430-server-east",
        "protocol_compatibility_evaluated_against",
        release,
        peer,
        ("protocol_version",),
        ("protocol_version",),
        provenance="verified_derived_cross_source",
    )
    return {
        "schema_version": GAME_COMPAT_SOURCE_SCHEMA,
        "world_id": "game-save-protocol-test",
        "source_records": [release, save, peer],
        "source_relations": [save_relation, protocol_relation],
        "query": {
            "release_record_id": release["record_id"],
            "save_record_id": save["record_id"],
            "peer_record_id": peer["record_id"],
            "decision_date": "2026-03-01",
        },
        "counterfactual_twin": {
            "record_id": release["record_id"],
            "fact_id": f"{release['record_id']}:protocol_version",
            "parent_value": "9",
            "value": "10",
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_exact_fact",
        },
    }


@pytest.mark.parametrize(
    ("payload_factory", "builder", "replay", "audit"),
    (
        (
            _macro_payload,
            build_macro_vintage_task,
            replay_macro_vintage_task,
            audit_macro_vintage_task,
        ),
        (
            _visual_payload,
            build_visual_compatibility_task,
            replay_visual_compatibility_task,
            audit_visual_compatibility_task,
        ),
        (
            _game_payload,
            build_game_compatibility_task,
            replay_game_compatibility_task,
            audit_game_compatibility_task,
        ),
    ),
)
def test_static_adapter_replays_state_answer_cf_and_remove_one(
    payload_factory: Any, builder: Any, replay: Any, audit: Any
) -> None:
    task = builder(payload_factory())

    assert replay(task) == task["answer"]
    assert replay(task, counterfactual=True) == task["cf_answer"]
    assert task["answer"] != task["cf_answer"]
    assert all(audit(task).values())
    for removed in task["essential_evidence_ids"]:
        selected = [
            evidence_id
            for evidence_id in task["essential_evidence_ids"]
            if evidence_id != removed
        ]
        assert replay(task, evidence_ids=selected) != task["answer"]


def test_macro_replay_uses_decision_vintage_not_future_observation() -> None:
    task = build_macro_vintage_task(_macro_payload())

    assert task["state"]["current_vintage_record_id"] == "gdp-2025q1-v2"
    assert task["state"]["prior_vintage_record_id"] == "gdp-2025q1-v1"
    assert task["state"]["revision_delta"] == "0.75"
    assert "gdp-2025q1-v3" not in task["essential_evidence_ids"]


def test_static_replay_filters_sources_before_remove_one_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = build_macro_vintage_task(_macro_payload())
    original = macrovintage_module._evaluate
    selected_source_counts: list[int] = []

    def observe_selected_sources(
        candidate: dict[str, Any], sources: Any, counterfactual: bool
    ) -> Any:
        if candidate.get("_selected_evidence_ids") is not None:
            selected_source_counts.append(len(sources.records))
        return original(candidate, sources, counterfactual)

    monkeypatch.setattr(macrovintage_module, "_evaluate", observe_selected_sources)
    selected = [
        evidence_id
        for evidence_id in task["essential_evidence_ids"]
        if evidence_id != "gdp-2025q1-v1"
    ]

    assert replay_macro_vintage_task(task, evidence_ids=selected) == "unknown"
    assert selected_source_counts == [1]


def test_visual_and_game_answers_are_source_fact_programs() -> None:
    visual = build_visual_compatibility_task(_visual_payload())
    game = build_game_compatibility_task(_game_payload())

    assert visual["state"]["compatible"] is True
    assert game["state"]["save_compatible"] is True
    assert game["state"]["protocol_compatible"] is True
    assert game["state"]["overall_compatible"] is True


def test_visual_adapter_retains_additional_real_history_records() -> None:
    payload = _visual_payload()
    older_release = _record(
        "diffusers-release-0320",
        "visual_component_release",
        "2025-11-01",
        {
            "component_name": "diffusers",
            "component_version": "0.32.0",
            "released_at": "2025-11-01",
        },
    )
    payload["source_records"].insert(1, older_release)

    task = build_visual_compatibility_task(payload)

    assert len(task["source_records"]) == 3
    assert replay_visual_compatibility_task(task) == task["answer"]


@pytest.mark.parametrize(
    ("payload_factory", "builder", "replay"),
    (
        (_macro_payload, build_macro_vintage_task, replay_macro_vintage_task),
        (
            _visual_payload,
            build_visual_compatibility_task,
            replay_visual_compatibility_task,
        ),
        (_game_payload, build_game_compatibility_task, replay_game_compatibility_task),
    ),
)
def test_static_adapters_fail_closed_on_extra_fields_and_body_corruption(
    payload_factory: Any, builder: Any, replay: Any
) -> None:
    payload = payload_factory()
    payload["filename_hint"] = "do-not-use-stems"
    with pytest.raises(ProvenanceError, match="schema"):
        builder(payload)

    task = builder(payload_factory())
    corrupted = deepcopy(task)
    corrupted["source_records"][0]["text"] += " forged"
    with pytest.raises(ProvenanceError, match="provenance|body|digest"):
        replay(corrupted)


def test_static_adapters_require_real_source_relations() -> None:
    for payload_factory, builder in (
        (_macro_payload, build_macro_vintage_task),
        (_visual_payload, build_visual_compatibility_task),
        (_game_payload, build_game_compatibility_task),
    ):
        payload = payload_factory()
        payload["source_relations"] = []
        with pytest.raises(ProvenanceError, match="relation"):
            builder(payload)


def test_static_adapters_reject_unknown_relation_operators() -> None:
    for payload_factory, builder in (
        (_macro_payload, build_macro_vintage_task),
        (_visual_payload, build_visual_compatibility_task),
        (_game_payload, build_game_compatibility_task),
    ):
        payload = payload_factory()
        payload["source_relations"][0]["kind"] = "unregistered_operator"
        with pytest.raises(ProvenanceError, match="relation"):
            builder(payload)


def test_visual_adapter_rejects_binary_weight_or_base64_padding_as_text() -> None:
    payload = _visual_payload()
    encoded_weight = base64.b64encode(b"\x00" * 1_024).decode()
    payload["source_records"][0]["text"] = encoded_weight
    payload["source_records"][0]["text_sha256"] = hashlib.sha256(
        encoded_weight.encode()
    ).hexdigest()

    with pytest.raises(ProvenanceError, match="base64"):
        build_visual_compatibility_task(payload)


def test_static_adapter_exporter_writes_audited_nonproduction_candidate(
    tmp_path: Path,
) -> None:
    payload_path = tmp_path / "macro-source.json"
    candidate_path = tmp_path / "macro-candidate.jsonl"
    audit_path = tmp_path / "macro-audit.json"
    raw = json.dumps(_macro_payload(), sort_keys=True).encode()
    payload_path.write_bytes(raw)

    receipt = export_static_domain_task(
        MACRO_VINTAGE_TASK_REPLAY_ADAPTER[0],
        payload_path,
        candidate_path,
        audit_path,
    )

    candidate = json.loads(candidate_path.read_text())
    assert replay_macro_vintage_task(candidate) == candidate["answer"]
    assert receipt["source_payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["source_binding_status"] == "requires_signed_replay_sidecar"
    assert receipt["train_ready"] is False
    assert receipt["production_eligible"] is False
    assert receipt["generation_integration"] == "disabled"
    assert receipt["promotion_eligible"] is False
    assert receipt["promoted"] is False
    assert candidate["train_ready"] is False
    assert candidate["production_eligible"] is False
    assert candidate["promotion_eligible"] is False
    assert candidate["promoted"] is False
    assert candidate["complete_world"] is False
    assert candidate["generation_integration"] == "disabled"
    assert "attestation" not in receipt
    assert "attestation" not in candidate
    assert json.loads(audit_path.read_text()) == receipt
