from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from longworld.core.eurlexworkflow import (
    EURLEX_PMS_ANSWER_PROGRAM,
    EURLEX_PMS_TASK_SCHEMA,
    EurLexWorkflowError,
    eurlex_canonical_json,
    eurlex_document,
    extract_pms_chain,
    materialize_eurlex_counterfactual,
    replay_eurlex_pms_candidate,
    replay_eurlex_pms_raw_slice,
    replay_pms_chain,
    verify_eurlex_pms_payload,
)
from longworld.core.pack import SEP, wrap_prompt


def _raw() -> bytes:
    # Deliberately short, synthetic unit-test document, never a training source.
    return b"""<html><body>
<p>Article 10</p><p>General obligations of manufacturers</p>
<p>10. Manufacturers of devices shall implement and keep up to date the post-market surveillance system in accordance with Article 83.</p>
<p>Article 11</p><p>Other duties</p>
<p>Article 15</p><p>Person responsible for regulatory compliance</p>
<p>The post-market surveillance obligations are complied with in accordance with Article 10(10).</p>
<p>Article 16</p><p>Other duties</p>
<p>Article 83</p><p>Post-market surveillance system of the manufacturer</p>
<p>Data improve risk management as referred to in Chapter I of Annex I.</p>
<p>Article 84</p><p>Post-market surveillance plan</p>
<p>The post-market surveillance system referred to in Article 83 shall be based on a post-market surveillance plan, the requirements for which are set out in Section 1.1 of Annex III.</p>
<p>Article 85</p><p>Another report</p>
<p>ANNEX I</p><p>CHAPTER I</p>
<p>3. Manufacturers shall establish, implement, document and maintain a risk management system.</p>
<p>based on the evaluation of the impact of the information referred to in point (e), if necessary amend control measures in line with the requirements of Section 4.</p>
<p>4. Risk control measures adopted shall follow the following order of priority: (a) eliminate hazards; (b) use protection; and (c) inform users.</p>
<p>5. Another requirement</p>
<p>ANNEX III</p><p>1.1. The post-market surveillance plan shall include suitable indicators and threshold values for risk management as referred to in Section 3 of Annex I.</p>
<p>ANNEX IV</p></body></html>"""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fixture(raw: bytes | None = None):
    raw = _raw() if raw is None else raw
    spans = extract_pms_chain(raw)
    artifacts = [
        {"artifact_id": f"act:{span['byte_start']}", "source_span": span}
        for span in spans
    ]
    question = (
        "Trace the PMS references and report visible terminal source availability."
    )
    notice = b"you can re-use the legal documents published in EUR-Lex for commercial or non-commercial purposes; includes third-party works"
    sources = {
        "act": {
            "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0745",
            "raw_utf8": raw.decode(),
            "bytes": len(raw),
            "sha256": _sha(raw),
        },
        "legal_notice": {
            "url": "https://eur-lex.europa.eu/content/legal-notice/legal-notice.html",
            "raw_utf8": notice.decode(),
            "bytes": len(notice),
            "sha256": _sha(notice),
        },
    }
    receipt = {
        "schema_version": "longworld.eurlex-source-receipt.v1",
        "authorization_record_id": "test",
        "preflight_config_sha256": "a" * 64,
        "sources": sources,
        "train_ready": False,
        "production_eligible": False,
    }
    receipt_raw = eurlex_canonical_json(receipt) + "\n"
    ids = [item["artifact_id"] for item in artifacts]
    task = {
        "schema_version": EURLEX_PMS_TASK_SCHEMA,
        "answer_program_id": EURLEX_PMS_ANSWER_PROGRAM,
        "question": question,
        "source_receipt_sha256": _sha(receipt_raw.encode()),
        "artifacts": artifacts,
        "essential_artifact_ids": ids,
        "cf_artifact_id": ids[-1],
    }
    source_binding = {
        "source_receipt_sha256": task["source_receipt_sha256"],
        "source_bundle_sha256": _sha(
            eurlex_canonical_json(
                {key: value["sha256"] for key, value in sources.items()}
            ).encode()
        ),
        "preflight_config_sha256": "a" * 64,
        "authorization_record_id": "test",
    }
    payload = {
        **source_binding,
        "eurlex_pms_task": task,
        "task_sha256": _sha(eurlex_canonical_json(task).encode()),
        "source_receipt_raw_utf8": receipt_raw,
    }
    documents = [eurlex_document(span) for span in spans]
    classes = [
        {
            "artifact_id": item["artifact_id"],
            "source_record_id": item["artifact_id"],
            "source_origin": "real_public",
            "provenance_id": "eurlex-source-span-sha256:"
            + item["source_span"]["raw_span_sha256"],
            "derived_text_sha256": _sha(document.encode()),
            "source_byte_start": item["source_span"]["byte_start"],
            "source_byte_end": item["source_span"]["byte_end"],
            "source_sha256": _sha(raw),
        }
        for item, document in zip(artifacts, documents, strict=True)
    ]
    candidate = {
        "eurlex_pms_task": task,
        "question": question,
        "answer_program_id": EURLEX_PMS_ANSWER_PROGRAM,
        "artifact_classification": classes,
        "document_context": SEP.join(documents),
        "query_timing": "first",
        "context": wrap_prompt(question, SEP.join(documents), "first"),
        "source_binding": source_binding,
        "task_replay_sidecar": {"sha256": "b" * 64},
        "counterfactual_twin": {
            "target_artifact_id": ids[-1],
            "provenance_operation": "withhold_source_body",
            "parent_value": False,
            "value": True,
        },
    }
    return candidate, payload


def test_source_payload_checks_exact_spans_and_referenced_scope() -> None:
    _candidate, payload = _fixture()
    verify_eurlex_pms_payload(payload)
    changed = deepcopy(payload)
    span = changed["eurlex_pms_task"]["artifacts"][0]["source_span"]
    span["text"] = span["text"].replace("10(10)", "10(11)")
    changed["task_sha256"] = _sha(
        eurlex_canonical_json(changed["eurlex_pms_task"]).encode()
    )
    with pytest.raises(EurLexWorkflowError, match="exact source span"):
        verify_eurlex_pms_payload(changed)


def test_surveillance_scope_is_consumed_by_later_annex_resolution() -> None:
    spans = extract_pms_chain(_raw())
    assert replay_pms_chain(spans)["status"] == "PASS"
    changed = deepcopy(spans)
    changed[2]["text"] = changed[2]["text"].replace("Annex I", "Annex II")
    result = replay_pms_chain(changed)
    assert result["status"] == "PARTIAL"
    assert (
        result["answer"]["first_unresolved_reference"]
        == "plan risk-management target consistent with surveillance scope"
    )


def test_risk_section_in_later_chapter_cannot_inherit_earlier_chapter_scope() -> None:
    raw = _raw().replace(
        b"<p>3. Manufacturers", b"<p>CHAPTER II</p><p>3. Manufacturers"
    )
    spans = extract_pms_chain(raw)
    assert replay_pms_chain(spans)["status"] == "PARTIAL"
    _candidate, payload = _fixture(raw)
    with pytest.raises(EurLexWorkflowError, match="reference graph"):
        verify_eurlex_pms_payload(payload)


def test_terminal_priority_does_not_require_adjacent_informing_sentence() -> None:
    spans = extract_pms_chain(_raw())
    assert "Manufacturers shall inform users" not in spans[-1]["text"]
    assert replay_pms_chain(spans)["answer"]["risk_control_priority"] == [
        "eliminate hazards",
        "use protection",
        "inform users",
    ]


def test_withheld_is_visible_and_every_cf_prefix_node_remains_necessary() -> None:
    candidate, _payload = _fixture()
    ids = candidate["eurlex_pms_task"]["essential_artifact_ids"]
    full = replay_eurlex_pms_candidate(candidate, ids)["answer"]
    cf = replay_eurlex_pms_candidate(candidate, ids, counterfactual=True)["answer"]
    assert json.loads(full)["terminal_source_availability"] == "AVAILABLE"
    assert json.loads(cf)["terminal_source_availability"] == "WITHHELD"
    artifacts = list(
        zip(
            candidate["artifact_classification"],
            candidate["document_context"].split(SEP),
            strict=True,
        )
    )
    transformed = materialize_eurlex_counterfactual(candidate, artifacts)
    child = deepcopy(candidate)
    child["artifact_classification"] = [item[0] for item in transformed]
    child["document_context"] = SEP.join(item[1] for item in transformed)
    assert '"source_body_withheld":true' in child["document_context"]
    assert replay_eurlex_pms_candidate(child, ids)["answer"] == cf
    assert (
        replay_eurlex_pms_candidate(child, ids, counterfactual=True)["answer"] == full
    )
    for removed in ids:
        assert (
            replay_eurlex_pms_candidate(child, [key for key in ids if key != removed])[
                "answer"
            ]
            != cf
        )
    assert (
        replay_eurlex_pms_raw_slice(
            child, child["document_context"], left_framed=True, right_framed=True
        )["answer"]
        == cf
    )
    without_receipt = SEP.join(item[1] for item in transformed[:-1])
    missing = replay_eurlex_pms_raw_slice(
        child, without_receipt, left_framed=True, right_framed=True
    )
    assert json.loads(missing["answer"])["terminal_source_availability"] == "MISSING"


def test_hidden_counterfactual_metadata_does_not_change_visible_answer() -> None:
    candidate, _payload = _fixture()
    ids = candidate["eurlex_pms_task"]["essential_artifact_ids"]
    before = replay_eurlex_pms_candidate(candidate, ids)["answer"]
    candidate["counterfactual_twin"]["value"] = False
    assert replay_eurlex_pms_candidate(candidate, ids)["answer"] == before


def test_cf_recovery_rejects_hidden_body_not_matching_authenticated_parent() -> None:
    candidate, _payload = _fixture()
    artifacts = list(
        zip(
            candidate["artifact_classification"],
            candidate["document_context"].split(SEP),
            strict=True,
        )
    )
    transformed = materialize_eurlex_counterfactual(candidate, artifacts)
    child = deepcopy(candidate)
    child["artifact_classification"] = [item[0] for item in transformed]
    child["document_context"] = SEP.join(item[1] for item in transformed)
    target = child["eurlex_pms_task"]["artifacts"][-1]["source_span"]
    target["text"] = target["text"].replace("eliminate hazards", "ignore hazards")
    with pytest.raises(EurLexWorkflowError, match="parent.*digest"):
        replay_eurlex_pms_candidate(
            child,
            child["eurlex_pms_task"]["essential_artifact_ids"],
            counterfactual=True,
        )


def test_coherent_section_renumbering_changes_extracted_and_replayed_target() -> None:
    raw = (
        _raw()
        .replace(b"Section 3", b"Section 7")
        .replace(b"3. Manufacturers", b"7. Manufacturers")
        .replace(b"Section 4", b"Section 8")
        .replace(b"4. Risk control", b"8. Risk control")
        .replace(b"5. Another", b"9. Another")
    )
    spans = extract_pms_chain(raw)
    result = replay_pms_chain(spans)
    assert result["status"] == "PASS"
    assert result["answer"]["parsed_path"][-2:] == [
        "Annex I Section 7",
        "Annex I Section 8",
    ]
