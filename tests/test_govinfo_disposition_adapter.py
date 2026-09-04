from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)
from longworld.core.govinfodisposition import (
    GOVINFO_DISPOSITION_TASK_SCHEMA,
    GovInfoDispositionError,
    govinfo_chronology,
    materialize_govinfo_counterfactual,
    replay_govinfo_disposition,
    replay_govinfo_disposition_raw_slice,
    validate_govinfo_candidate_source_binding,
    verify_govinfo_replay_payload,
)
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.provenance import ProvenanceError
from longworld.core.taskpromotion import (
    _canonical_task_identifiers,
    _task_view_replay,
    build_task_candidate_view_projections,
)
from longworld.core.taskproof import _adapter_key, _projection_chronology
from longworld.core.taskreplaysidecar import (
    GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER,
    GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER_V3,
    TASK_REPLAY_ADAPTER_REGISTRY,
    build_task_replay_sidecar,
)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _document(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate() -> dict[str, object]:
    relation = _document(
        {
            "action_date": "2024-03-23",
            "action_texts": ["Became Public Law No: 118-47."],
            "artifact_type": "govinfo_transition",
            "bill_id": "118-HR-4366",
            "from_stage": "enr",
            "status_source_sha256": "1" * 64,
            "to_stage": "law",
        }
    )
    source_a = _document(
        {
            "artifact_type": "govinfo_section",
            "base_key": "division:a/title:i/section:1",
            "bill_id": "118-HR-4366",
            "oracle_text": "The old alpha rule applies to agencies and offices.",
            "source_sha256": "2" * 64,
            "source_text": "The old alpha rule applies to agencies and offices.",
            "stage": "enr",
            "stage_date": "2024-03-22",
        }
    )
    target_a = _document(
        {
            "artifact_type": "govinfo_section",
            "base_key": "division:a/title:i/section:1",
            "bill_id": "118-HR-4366",
            "oracle_text": "The new alpha rule applies to agencies and offices.",
            "source_sha256": "3" * 64,
            "source_text": "The new alpha rule applies to agencies and offices.",
            "stage": "law",
            "stage_date": "2024-03-23",
        }
    )
    source_b = _document(
        {
            "artifact_type": "govinfo_section",
            "base_key": "division:b/title:i/section:2",
            "bill_id": "118-HR-4366",
            "oracle_text": "The beta rule remains unchanged for every covered office.",
            "source_sha256": "2" * 64,
            "source_text": "The beta rule remains unchanged for every covered office.",
            "stage": "enr",
            "stage_date": "2024-03-22",
        }
    )
    target_b = _document(
        {
            "artifact_type": "govinfo_section",
            "base_key": "division:b/title:i/section:2",
            "bill_id": "118-HR-4366",
            "oracle_text": "The beta rule remains unchanged for every covered office.",
            "source_sha256": "3" * 64,
            "source_text": "The beta rule remains unchanged for every covered office.",
            "stage": "law",
            "stage_date": "2024-03-23",
        }
    )
    ids = ["relation", "source-a", "target-a", "source-b", "target-b"]
    documents = [relation, source_a, target_a, source_b, target_b]
    classifications = [
        {
            "artifact_id": artifact_id,
            "evidence_role": "causal_gold",
            "provenance_id": f"sha256:{hashlib.sha256(document.encode()).hexdigest()}",
            "source_origin": "real_public",
            "workflow_id": "p52-test",
            "workflow_kind": "real_source_derived",
        }
        for artifact_id, document in zip(ids, documents, strict=True)
    ]
    question = (
        "Return D01 and D02 as JSON. Codebook: R=retained; M=modified; U=unknown."
    )
    document_context = SEP.join(documents)
    requests = [
        {
            "base_key": "division:a/title:i/section:1",
            "bill_id": "118-HR-4366",
            "code": "D01",
            "from_stage": "enr",
            "to_stage": "law",
        },
        {
            "base_key": "division:b/title:i/section:2",
            "bill_id": "118-HR-4366",
            "code": "D02",
            "from_stage": "enr",
            "to_stage": "law",
        },
    ]
    return {
        "answer_program_id": "govinfo.bill_disposition.cross_schema.v1",
        "artifact_classification": classifications,
        "context": wrap_prompt(question, document_context, "first"),
        "counterfactual_twin": {
            "provenance_operation": "replace_target_with_authenticated_source_body",
            "source_artifact_id": "source-a",
            "target_artifact_id": "target-a",
            "parent_value": {
                "oracle_text": json.loads(target_a)["oracle_text"],
                "source_text": json.loads(target_a)["source_text"],
            },
            "value": {
                "oracle_text": json.loads(source_a)["oracle_text"],
                "source_text": json.loads(source_a)["source_text"],
            },
        },
        "document_context": document_context,
        "govinfo_disposition_task": {
            "answer_program_id": "govinfo.bill_disposition.cross_schema.v1",
            "bill_id": "118-HR-4366",
            "counterfactual_code": "D01",
            "from_stage": "enr",
            "oracle_revision": "p52-govinfo-cross-schema-section-disposition-v1",
            "oracle_shingle_size": 5,
            "oracle_threshold": 0.9,
            "requested_dispositions": requests,
            "schema_version": GOVINFO_DISPOSITION_TASK_SCHEMA,
            "source_receipt_sha256": "4" * 64,
            "to_stage": "law",
        },
        "oracle_revision": "p52-govinfo-cross-schema-section-disposition-v1",
        "oracle_shingle_size": 5,
        "oracle_threshold": 0.9,
        "query_timing": "first",
        "question": question,
        "requested_dispositions": requests,
        "source_binding": {"source_receipt_sha256": "4" * 64},
        "task_replay_sidecar": {"sha256": "5" * 64},
    }


def test_replay_requires_every_relation_and_endpoint_and_virtual_cf() -> None:
    candidate = _candidate()
    ids = [item["artifact_id"] for item in candidate["artifact_classification"]]

    factual = replay_govinfo_disposition(candidate, ids)
    counterfactual = replay_govinfo_disposition(candidate, ids, counterfactual=True)

    assert factual["answer"] == '{"D01":"M","D02":"R"}'
    assert counterfactual["answer"] == '{"D01":"R","D02":"R"}'
    for removed in ids:
        assert (
            replay_govinfo_disposition(
                candidate, [item for item in ids if item != removed]
            )["answer"]
            != factual["answer"]
        )


def test_materialized_counterfactual_and_raw_slice_replay_are_byte_grounded() -> None:
    candidate = _candidate()
    artifacts = list(
        zip(
            candidate["artifact_classification"],
            str(candidate["document_context"]).split(SEP),
            strict=True,
        )
    )
    materialized = materialize_govinfo_counterfactual(candidate, artifacts)
    projected = deepcopy(candidate)
    projected["artifact_classification"] = [item[0] for item in materialized]
    projected["document_context"] = SEP.join(item[1] for item in materialized)
    projected["context"] = wrap_prompt(
        str(projected["question"]), str(projected["document_context"]), "first"
    )
    ids = [item["artifact_id"] for item in projected["artifact_classification"]]

    assert replay_govinfo_disposition(projected, ids)["answer"] == (
        '{"D01":"R","D02":"R"}'
    )
    twin = projected["counterfactual_twin"]
    twin["parent_value"], twin["value"] = twin["value"], twin["parent_value"]
    assert (
        replay_govinfo_disposition(projected, ids, counterfactual=True)["answer"]
        == '{"D01":"M","D02":"R"}'
    )
    assert (
        replay_govinfo_disposition_raw_slice(
            projected,
            str(projected["document_context"]),
            left_framed=True,
            right_framed=True,
        )["answer"]
        == '{"D01":"R","D02":"R"}'
    )
    assert [item[1]["artifact_id"] for item in govinfo_chronology(materialized)] == [
        "source-a",
        "source-b",
        "relation",
        "target-a",
        "target-b",
    ]


def test_sidecar_payload_verifier_binds_exact_source_receipt_and_task() -> None:
    candidate = _candidate()
    task = candidate["govinfo_disposition_task"]
    receipt = {
        "authorization": {"record_id": "p52-govinfo-bill-disposition-candidate-v1"},
        "data_stage": "source_inventory",
        "preflight_config_sha256": "5" * 64,
        "production_eligible": False,
        "raw_xml_persisted": False,
        "schema_version": "longworld.p52-govinfo-source-receipt.v1",
        "source_bundle_sha256": "",
        "source_count": 3,
        "sources": [
            {
                "bytes": 1,
                "raw_xml_persisted": False,
                "sha256": value * 64,
                "url": f"https://www.govinfo.gov/{value}",
            }
            for value in "123"
        ],
        "train_ready": False,
    }
    receipt["source_bundle_sha256"] = hashlib.sha256(
        (
            "\n".join(
                f"{source['url']}:{source['sha256']}" for source in receipt["sources"]
            )
            + "\n"
        ).encode()
    ).hexdigest()
    receipt_raw = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    task["source_receipt_sha256"] = hashlib.sha256(receipt_raw.encode()).hexdigest()
    payload = {
        "authorization_record_id": receipt["authorization"]["record_id"],
        "govinfo_disposition_task": task,
        "preflight_config_sha256": receipt["preflight_config_sha256"],
        "source_bundle_sha256": receipt["source_bundle_sha256"],
        "source_receipt_raw_utf8": receipt_raw,
        "source_receipt_sha256": hashlib.sha256(receipt_raw.encode()).hexdigest(),
        "task_sha256": _canonical_sha256(task),
    }

    verify_govinfo_replay_payload(payload)

    forged = deepcopy(payload)
    forged["govinfo_disposition_task"]["oracle_threshold"] = 0.8
    with pytest.raises(GovInfoDispositionError, match="task digest"):
        verify_govinfo_replay_payload(forged)


def test_candidate_source_binding_rejects_unlisted_derivative_hash() -> None:
    candidate = _candidate()
    for classification, document in zip(
        candidate["artifact_classification"],
        str(candidate["document_context"]).split(SEP),
        strict=True,
    ):
        value = json.loads(document)
        source_sha = value.get("status_source_sha256") or value.get("source_sha256")
        classification.update(
            {
                "derived_text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                "source_record_id": classification["artifact_id"],
                "source_sha256": source_sha,
                "source_url": f"https://www.govinfo.gov/{source_sha[0]}",
            }
        )
    candidate["source_record_ids_by_artifact"] = {
        item["artifact_id"]: [item["source_record_id"]]
        for item in candidate["artifact_classification"]
    }
    sources = [
        {
            "bytes": 1,
            "raw_xml_persisted": False,
            "sha256": value * 64,
            "url": f"https://www.govinfo.gov/{value}",
        }
        for value in "123"
    ]
    source_bundle_sha256 = hashlib.sha256(
        (
            "\n".join(f"{item['url']}:{item['sha256']}" for item in sources) + "\n"
        ).encode()
    ).hexdigest()
    receipt = {
        "authorization": {"record_id": "p52-govinfo-bill-disposition-candidate-v1"},
        "data_stage": "source_inventory",
        "preflight_config_sha256": "6" * 64,
        "production_eligible": False,
        "raw_xml_persisted": False,
        "schema_version": "longworld.p52-govinfo-source-receipt.v1",
        "source_bundle_sha256": source_bundle_sha256,
        "source_count": len(sources),
        "sources": sources,
        "train_ready": False,
    }
    receipt_raw = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    receipt_sha256 = hashlib.sha256(receipt_raw.encode()).hexdigest()
    candidate["govinfo_disposition_task"]["source_receipt_sha256"] = receipt_sha256
    candidate["source_binding"] = {
        "authorization_record_id": receipt["authorization"]["record_id"],
        "preflight_config_sha256": receipt["preflight_config_sha256"],
        "source_bundle_sha256": source_bundle_sha256,
        "source_receipt_sha256": receipt_sha256,
    }
    payload = {
        **candidate["source_binding"],
        "govinfo_disposition_task": candidate["govinfo_disposition_task"],
        "source_receipt_raw_utf8": receipt_raw,
        "task_sha256": _canonical_sha256(candidate["govinfo_disposition_task"]),
    }

    validate_govinfo_candidate_source_binding(candidate, payload)
    candidate["artifact_classification"][0]["source_sha256"] = "f" * 64
    with pytest.raises(GovInfoDispositionError, match="artifact source"):
        validate_govinfo_candidate_source_binding(candidate, payload)


def _registered_candidate() -> dict[str, object]:
    candidate = _candidate()
    ids = [item["artifact_id"] for item in candidate["artifact_classification"]]
    candidate.update(
        {
            "answer": replay_govinfo_disposition(candidate, ids)["answer"],
            "cf_answer": replay_govinfo_disposition(
                candidate, ids, counterfactual=True
            )["answer"],
            "composition_method": "same_case_dossier",
            "data_stage": "candidate",
            "domain": "government_legislation",
            "length_bucket": "32k",
            "strict_replay_revision": GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[1],
            "tokenizer_asset_manifest_sha256": "6" * 64,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": "7" * 40,
            "training_objective": "sft",
            "view": "full",
        }
    )
    candidate["task_replay_sidecar"] = {
        "adapter_id": GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[0],
        "adapter_revision": GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[1],
        "sidecar_schema_version": GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[2],
        "sha256": "8" * 64,
    }
    return candidate


def test_registered_dispatch_replays_identity_and_chronology() -> None:
    candidate = _registered_candidate()
    ids = [item["artifact_id"] for item in candidate["artifact_classification"]]
    documents = str(candidate["document_context"]).split(SEP)

    assert _adapter_key(candidate) == GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER
    assert (
        _task_view_replay(candidate, GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER, ids)[
            "answer"
        ]
        == candidate["answer"]
    )
    assert (
        _task_view_replay(
            candidate,
            GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER,
            ids,
            counterfactual=True,
        )["answer"]
        == candidate["cf_answer"]
    )
    assert (
        _canonical_task_identifiers(candidate, GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER)[
            "answer_program_id"
        ]
        == candidate["answer_program_id"]
    )
    chronology = _projection_chronology(
        candidate, candidate["artifact_classification"], documents
    )
    assert [item["artifact_id"] for item in chronology] == [
        "source-a",
        "source-b",
        "relation",
        "target-a",
        "target-b",
    ]


def test_registered_parent_projects_three_distinct_source_bound_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["candidate"], "c" * 32)
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["candidate"], "p52-candidate-test")
    candidate = _registered_candidate()
    candidate.update(
        {
            "actual_context_tokens": 32_100,
            "dossier_id": "govinfo-projection-test",
            "essential_artifact_ids": [
                item["artifact_id"] for item in candidate["artifact_classification"]
            ],
            "graph": {"hop_count": 2, "proof_depth": 2},
            "query_id": "govinfo-test:32k",
            "real_source_token_ratio": 0.9,
            "tokenizer_context_tokens": 32_100,
            "world_id": "govinfo-test",
        }
    )
    candidate["source_record_ids_by_artifact"] = {
        item["artifact_id"]: [item["artifact_id"]]
        for item in candidate["artifact_classification"]
    }

    def token_counter(text: str) -> int:
        return 32_000 + min(700, len(text) // 10)

    projected = build_task_candidate_view_projections(
        candidate,
        adapter_key=GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER,
        token_counter=token_counter,
        candidate_attestation_key=b"c" * 32,
    )

    assert {item["view"] for item in projected} == {
        "full",
        "cf",
        "ordered_artifact_view",
    }
    assert len({item["document_context"] for item in projected}) == 3
    cf = next(item for item in projected if item["view"] == "cf")
    assert cf["answer"] == candidate["cf_answer"]
    assert cf["cf_answer"] == candidate["answer"]
    assert (
        sum(
            item["source_origin"] == "synthetic_counterfactual"
            for item in cf["artifact_classification"]
        )
        == 1
    )


def test_registered_sidecar_contract_is_closed_and_task_hash_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], "s" * 32)
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "p52-registered-source-test")
    task = deepcopy(_candidate()["govinfo_disposition_task"])
    sources = [
        {
            "bytes": 10,
            "raw_xml_persisted": False,
            "sha256": "1" * 64,
            "url": "https://www.govinfo.gov/source.xml",
        }
    ]
    source_bundle_sha256 = hashlib.sha256(
        f"{sources[0]['url']}:{sources[0]['sha256']}\n".encode()
    ).hexdigest()
    receipt = {
        "authorization": {"record_id": "p52-registered-test"},
        "data_stage": "source_inventory",
        "preflight_config_sha256": "2" * 64,
        "production_eligible": False,
        "raw_xml_persisted": False,
        "schema_version": "longworld.p52-govinfo-source-receipt.v1",
        "source_bundle_sha256": source_bundle_sha256,
        "source_count": 1,
        "sources": sources,
        "train_ready": False,
    }
    receipt_raw = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    task["source_receipt_sha256"] = hashlib.sha256(receipt_raw.encode()).hexdigest()
    payload = {
        "authorization_record_id": "p52-registered-test",
        "candidate_content_commitments": [
            {
                "content_sha256": "3" * 64,
                "length_bucket": "32k",
                "world_id": "govinfo-test",
            }
        ],
        "govinfo_disposition_task": task,
        "preflight_config_sha256": receipt["preflight_config_sha256"],
        "replay_revision": GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[1],
        "source_bundle_sha256": source_bundle_sha256,
        "source_receipt_raw_utf8": receipt_raw,
        "source_receipt_sha256": task["source_receipt_sha256"],
        "task_sha256": _canonical_sha256(task),
        "tokenizer_asset_manifest_sha256": "4" * 64,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "5" * 40,
    }

    assert GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER in TASK_REPLAY_ADAPTER_REGISTRY
    assert GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER_V3 in TASK_REPLAY_ADAPTER_REGISTRY
    sidecar = build_task_replay_sidecar(
        adapter_id=GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[0],
        adapter_revision=GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[1],
        replay_payload=payload,
        source_attestation_key=b"s" * 32,
    )
    assert sidecar["replay_payload"] == payload

    forged = deepcopy(payload)
    forged["task_sha256"] = "f" * 64
    with pytest.raises(ProvenanceError, match="GovInfo task replay payload"):
        build_task_replay_sidecar(
            adapter_id=GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[0],
            adapter_revision=GOVINFO_DISPOSITION_TASK_REPLAY_ADAPTER[1],
            replay_payload=forged,
            source_attestation_key=b"s" * 32,
        )
