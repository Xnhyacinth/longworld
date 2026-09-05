from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.core.pack import SEP, wrap_prompt
from reports.p52_govinfo_bill_disposition_pipeline import (
    MODIFIED,
    RETAINED,
    UNKNOWN,
    P52Blocker,
    build_registered_parent,
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
