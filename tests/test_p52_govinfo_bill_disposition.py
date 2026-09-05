from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from longworld.core.pack import SEP, wrap_prompt
from reports import p52_govinfo_bill_disposition_pipeline as p52
from reports.p52_govinfo_bill_disposition_pipeline import (
    MODIFIED,
    RETAINED,
    UNKNOWN,
    P52Blocker,
    build_registered_parent,
    build_registered_parents,
    cross_schema_dispositions,
    replay_candidate,
    section_units_from_xml,
)

STRUCTURAL_ANCESTORS = {
    "division",
    "title",
    "subtitle",
    "chapter",
    "subchapter",
    "part",
    "subpart",
}
EXCLUDED_TAGS = {"note", "page", "sidenote", "sourceCredit"}


def test_cross_schema_oracle_strips_presentation_and_rejects_ambiguous_keys() -> None:
    bill_xml = b"""\
<bill>
  <title><num>Title I</num>
    <section><num>Sec. 101</num><text>Alpha rule applies to every covered office.</text><page>12</page></section>
    <section><num>Sec. 102</num><text>Old reporting language remains in force.</text></section>
    <section><num>Sec. 103</num><text>First duplicate.</text></section>
    <section><num>Sec. 103</num><text>Second duplicate.</text></section>
  </title>
</bill>
"""
    law_xml = b"""\
<pLaw>
  <title><num value="I">TITLE I</num>
    <section><num value="101">101</num><text>Alpha rule applies to every covered office.</text><sidenote>Editorial note.</sidenote></section>
    <section><num value="102">102</num><text>New reporting language applies to each covered office.</text></section>
    <section><num value="103">103</num><text>Only one target value.</text></section>
  </title>
</pLaw>
"""
    source, source_ambiguous = section_units_from_xml(
        bill_xml,
        structural_ancestors=STRUCTURAL_ANCESTORS,
        excluded_tags=EXCLUDED_TAGS,
    )
    target, target_ambiguous = section_units_from_xml(
        law_xml,
        structural_ancestors=STRUCTURAL_ANCESTORS,
        excluded_tags=EXCLUDED_TAGS,
    )

    result = cross_schema_dispositions(source, target, shingle_size=5, threshold=0.9)

    assert source_ambiguous == {"title:i/section:103"}
    assert target_ambiguous == set()
    assert result == {
        "title:i/section:101": RETAINED,
        "title:i/section:102": MODIFIED,
    }


def _artifact(artifact_id: str, payload: dict[str, object]) -> tuple[dict, str]:
    return (
        {"artifact_id": artifact_id},
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def test_replay_requires_relation_and_both_cross_schema_section_ends() -> None:
    relation = _artifact(
        "relation",
        {
            "artifact_type": "govinfo_transition",
            "bill_id": "118-HR-4366",
            "from_stage": "enr",
            "to_stage": "law",
            "action_date": "2024-03-09",
            "action_texts": ["Became Public Law No: 118-42"],
        },
    )
    source_a = _artifact(
        "source-a",
        {
            "artifact_type": "govinfo_section",
            "bill_id": "118-HR-4366",
            "stage": "enr",
            "base_key": "division:a/title:i/section:1",
            "source_text": "The old alpha rule applies to agencies and offices.",
            "oracle_text": "The old alpha rule applies to agencies and offices.",
        },
    )
    target_a = _artifact(
        "target-a",
        {
            "artifact_type": "govinfo_section",
            "bill_id": "118-HR-4366",
            "stage": "law",
            "base_key": "division:a/title:i/section:1",
            "source_text": "The new alpha rule applies to agencies and offices.",
            "oracle_text": "The new alpha rule applies to agencies and offices.",
        },
    )
    source_b = _artifact(
        "source-b",
        {
            "artifact_type": "govinfo_section",
            "bill_id": "118-HR-4366",
            "stage": "enr",
            "base_key": "division:b/title:i/section:2",
            "source_text": "The beta rule remains unchanged for every covered office.",
            "oracle_text": "The beta rule remains unchanged for every covered office.",
        },
    )
    target_b = _artifact(
        "target-b",
        {
            "artifact_type": "govinfo_section",
            "bill_id": "118-HR-4366",
            "stage": "law",
            "base_key": "division:b/title:i/section:2",
            "source_text": "The beta rule remains unchanged for every covered office.",
            "oracle_text": "The beta rule remains unchanged for every covered office.",
        },
    )
    artifacts = [relation, source_a, target_a, source_b, target_b]
    question = "Return D01 and D02. Codes: R=retained; M=modified; U=unknown."
    candidate = {
        "question": question,
        "query_timing": "first",
        "oracle_shingle_size": 5,
        "oracle_threshold": 0.9,
        "requested_dispositions": [
            {
                "code": "D01",
                "bill_id": "118-HR-4366",
                "base_key": "division:a/title:i/section:1",
                "from_stage": "enr",
                "to_stage": "law",
            },
            {
                "code": "D02",
                "bill_id": "118-HR-4366",
                "base_key": "division:b/title:i/section:2",
                "from_stage": "enr",
                "to_stage": "law",
            },
        ],
        "artifact_classification": [item[0] for item in artifacts],
        "document_context": SEP.join(item[1] for item in artifacts),
    }
    candidate["context"] = wrap_prompt(question, candidate["document_context"], "first")
    selected = [item[0]["artifact_id"] for item in artifacts]

    assert replay_candidate(candidate, selected) == {
        "D01": MODIFIED,
        "D02": RETAINED,
    }
    for removed in selected:
        assert replay_candidate(
            candidate,
            [artifact_id for artifact_id in selected if artifact_id != removed],
        ) != {"D01": MODIFIED, "D02": RETAINED}

    counterfactual_documents = candidate["document_context"].split(SEP)
    changed = json.loads(counterfactual_documents[2])
    changed["source_text"] = json.loads(counterfactual_documents[1])["source_text"]
    changed["oracle_text"] = json.loads(counterfactual_documents[1])["oracle_text"]
    counterfactual_documents[2] = json.dumps(
        changed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    candidate["document_context"] = SEP.join(counterfactual_documents)
    candidate["context"] = wrap_prompt(question, candidate["document_context"], "first")

    assert replay_candidate(candidate, selected) == {
        "D01": RETAINED,
        "D02": RETAINED,
    }
    assert (
        replay_candidate(candidate, ["relation", "source-a", "target-a"])["D02"]
        == UNKNOWN
    )

    candidate["oracle_shingle_size"] = 0
    with pytest.raises(P52Blocker, match="oracle parameters"):
        replay_candidate(candidate, selected)


def test_registered_parent_keeps_authentic_full_pool_and_virtual_counterfactual() -> (
    None
):
    relation = _artifact(
        "relation",
        {
            "action_date": "2024-03-09",
            "action_texts": ["Became Public Law No: 118-42"],
            "artifact_type": "govinfo_transition",
            "bill_id": "118-HR-4366",
            "from_stage": "enr",
            "status_source_sha256": "1" * 64,
            "to_stage": "law",
        },
    )
    source = _artifact(
        "source",
        {
            "artifact_type": "govinfo_section",
            "base_key": "division:a/title:i/section:1",
            "bill_id": "118-HR-4366",
            "oracle_text": "The old alpha rule applies to agencies and offices.",
            "source_sha256": "2" * 64,
            "source_text": "The old alpha rule applies to agencies and offices.",
            "stage": "enr",
            "stage_date": "2024-03-08",
        },
    )
    target = _artifact(
        "target",
        {
            "artifact_type": "govinfo_section",
            "base_key": "division:a/title:i/section:1",
            "bill_id": "118-HR-4366",
            "oracle_text": "The new alpha rule applies to agencies and offices.",
            "source_sha256": "3" * 64,
            "source_text": "The new alpha rule applies to agencies and offices.",
            "stage": "law",
            "stage_date": "2024-03-09",
        },
    )
    artifacts = [relation, source, target]
    request = {
        "base_key": "division:a/title:i/section:1",
        "bill_id": "118-HR-4366",
        "code": "D01",
        "from_stage": "enr",
        "to_stage": "law",
    }
    question = "Return D01. Codebook: R=retained; M=modified; U=unknown."
    document_context = SEP.join(item[1] for item in artifacts)
    source_candidate = {
        "answer_program_id": "legacy-p52-program",
        "artifact_classification": [
            {
                **item[0],
                "evidence_role": "causal_gold",
                "source_origin": "real_public",
                "workflow_kind": "real_source_derived",
            }
            for item in artifacts
        ],
        "context": wrap_prompt(question, document_context, "first"),
        "document_context": document_context,
        "essential_artifact_ids": ["relation", "source", "target"],
        "graph": {"hop_count": 2, "proof_depth": 2},
        "length_bucket": "32k",
        "query_id": "govinfo-test:32k",
        "query_timing": "first",
        "question": question,
        "requested_dispositions": [request],
        "source_binding": {
            "authorization_record_id": "p52-registered-test",
            "preflight_config_sha256": "4" * 64,
            "source_bundle_sha256": "5" * 64,
            "source_receipt_sha256": "6" * 64,
        },
        "world_id": "govinfo-test",
    }
    task = {
        "answer_program_id": "govinfo.bill_disposition.cross_schema.v1",
        "bill_id": "118-HR-4366",
        "counterfactual_code": "D01",
        "from_stage": "enr",
        "oracle_revision": "p52-govinfo-cross-schema-section-disposition-v1",
        "oracle_shingle_size": 5,
        "oracle_threshold": 0.9,
        "requested_dispositions": [request],
        "schema_version": "longworld.govinfo-bill-disposition-task.v1",
        "source_receipt_sha256": "6" * 64,
        "to_stage": "law",
    }

    parent = build_registered_parent(source_candidate, task)

    assert parent["view"] == "full"
    assert parent["composition_method"] == "same_case_dossier"
    assert parent["answer"] == '{"D01":"M"}'
    assert parent["cf_answer"] == '{"D01":"R"}'
    assert parent["govinfo_disposition_task"] == task
    assert all(
        item["source_origin"] == "real_public"
        for item in parent["artifact_classification"]
    )


def test_registered_32k_task_uses_structurally_distant_modified_sections() -> None:
    config = json.loads(
        Path(
            "configs/p52_govinfo_bill_disposition_structural_span_generation_v2.json"
        ).read_text()
    )

    assert config["requested_keys"][:2] == [
        "division:a/title:i/section:138",
        "division:f/title:ii/section:219",
    ]


class _ArtifactCountTokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> range:
        assert add_special_tokens is False
        return range(100 + 1000 * text.count('"artifact_type"'))


class _StructuralSpanBlockerTokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> range:
        assert add_special_tokens is False
        if '"parent_value_sha256"' in text:
            return range(31000)
        if '"parent_text_sha256"' in text:
            return range(32000)
        return range(32621)


def _fake_registered_source_state(config_path: Path, *, fillers: int) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    status_sha = "1" * 64
    bill_sha = "2" * 64
    law_sha = "3" * 64
    status_url = "https://www.govinfo.gov/status.xml"
    bill_url = "https://www.govinfo.gov/bill.xml"
    law_url = "https://www.govinfo.gov/law.xml"
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for index, base_key in enumerate(config["requested_keys"], start=1):
        for stage, stage_date, source_url, source_sha, label in (
            ("enr", "2024-03-08", bill_url, bill_sha, "source"),
            ("law", "2024-03-09", law_url, law_sha, "target"),
        ):
            text = (
                f"Unique {label} provision {index} for {base_key} establishes "
                f"a distinct compliance rule with enough words for comparison."
            )
            records[(stage, base_key)] = {
                "base_key": base_key,
                "bill_id": config["bill_id"],
                "canonical": text.lower(),
                "oracle_text": text,
                "record_id": f"{config['bill_id']}:{stage}:{base_key}",
                "source_sha256": source_sha,
                "source_url": source_url,
                "stage": stage,
                "stage_date": stage_date,
                "text": text,
            }
    filler_records = []
    for index in range(fillers):
        text = (
            f"Unique background section {index} records an independent administrative "
            "requirement that is unrelated to every requested disposition."
        )
        filler_records.append(
            {
                "base_key": f"division:z/title:i/section:{index + 1}",
                "bill_id": config["bill_id"],
                "canonical": text.lower(),
                "oracle_text": text,
                "record_id": f"{config['bill_id']}:enr:filler-{index}",
                "source_sha256": bill_sha,
                "source_url": bill_url,
                "stage": "enr",
                "stage_date": "2024-03-08",
                "text": text,
            }
        )
    sources = [
        {
            "bytes": 100,
            "raw_xml_persisted": False,
            "sha256": status_sha,
            "url": status_url,
        },
        {
            "bytes": 200,
            "raw_xml_persisted": False,
            "sha256": bill_sha,
            "url": bill_url,
        },
        {
            "bytes": 300,
            "raw_xml_persisted": False,
            "sha256": law_sha,
            "url": law_url,
        },
    ]
    bundle = p52._sha256_text(
        "".join(f"{source['url']}:{source['sha256']}\n" for source in sources)
    )
    return {
        "action_date": "2024-03-09",
        "action_texts": ["Became Public Law No: 118-42"],
        "bill_id": config["bill_id"],
        "fillers": filler_records,
        "from_stage": config["from_stage"],
        "records": records,
        "source_bundle_sha256": bundle,
        "source_receipts": sources,
        "status_source_sha256": status_sha,
        "status_source_url": status_url,
        "to_stage": config["to_stage"],
    }


def _set_registered_role_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_SOURCE_ATTESTATION_KEY", "s" * 32)
    monkeypatch.setenv("LONGWORLD_SOURCE_ATTESTATION_KEY_ID", "test-source")
    monkeypatch.setenv("LONGWORLD_CANDIDATE_ATTESTATION_KEY", "c" * 32)
    monkeypatch.setenv("LONGWORLD_CANDIDATE_ATTESTATION_KEY_ID", "test-candidate")


def test_registered_parent_wrapper_builds_three_nested_source_bound_bands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path(
        "configs/p52_govinfo_bill_disposition_registered_generation_v1.json"
    )
    state = _fake_registered_source_state(config_path, fillers=140)
    _set_registered_role_keys(monkeypatch)
    monkeypatch.setattr(
        p52, "_verified_source_state", lambda _config, _preflight: state
    )
    monkeypatch.setattr(
        p52, "_load_tokenizer", lambda _config: _ArtifactCountTokenizer()
    )
    monkeypatch.setattr(
        p52,
        "_read_jsonl",
        lambda _path: (_ for _ in ()).throw(AssertionError("old P52 views were read")),
    )

    parents, sidecar_raw, source_raw, receipt = build_registered_parents(config_path)

    assert [parent["length_bucket"] for parent in parents] == ["32k", "64k", "128k"]
    artifact_sets = [
        {item["artifact_id"] for item in parent["artifact_classification"]}
        for parent in parents
    ]
    assert artifact_sets[0] < artifact_sets[1] < artifact_sets[2]
    assert [len(items) for items in artifact_sets] == [32, 64, 128]
    assert all(parent["view"] == "full" for parent in parents)
    assert all(
        item["source_origin"] == "real_public"
        for parent in parents
        for item in parent["artifact_classification"]
    )
    for parent in parents:
        assert {
            field: parent[field]
            for field in (
                "promotion_eligible",
                "train_ready",
                "production_eligible",
                "promoted",
            )
        } == {
            "promotion_eligible": False,
            "train_ready": False,
            "production_eligible": False,
            "promoted": False,
        }
        assert parent["padding_tokens"] == 0
        assert parent["cloned_artifacts"] == 0
        assert parent["split_or_truncated_sections"] == 0
        assert (
            parent["source_binding"]["source_bundle_sha256"]
            == state["source_bundle_sha256"]
        )
        assert parent["task_replay_sidecar"]["sha256"] == p52._sha256_bytes(sidecar_raw)
    assert receipt["parent_candidate_count"] == 3
    assert receipt["padding_tokens"] == 0
    assert receipt["cloned_artifacts"] == 0
    assert receipt["split_or_truncated_sections"] == 0
    assert receipt["source_receipt_sha256"] == p52._sha256_bytes(source_raw)
    sidecar = json.loads(sidecar_raw)
    assert len(sidecar["replay_payload"]["candidate_content_commitments"]) == 3
    assert (
        json.loads(source_raw)["source_bundle_sha256"] == state["source_bundle_sha256"]
    )


def test_structural_span_wrapper_fails_closed_on_counterfactual_exact_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path(
        "configs/p52_govinfo_bill_disposition_structural_span_generation_v2.json"
    )
    state = _fake_registered_source_state(config_path, fillers=0)
    _set_registered_role_keys(monkeypatch)
    monkeypatch.setattr(
        p52, "_verified_source_state", lambda _config, _preflight: state
    )
    monkeypatch.setattr(
        p52, "_load_tokenizer", lambda _config: _StructuralSpanBlockerTokenizer()
    )

    with pytest.raises(
        P52Blocker,
        match=(
            r"32k exact natural pack unavailable: .*'full': 32621.*"
            r"'cf': 31000.*'ordered_artifact_view': 32621"
        ),
    ):
        build_registered_parents(config_path)
