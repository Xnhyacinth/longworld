from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import scripts.project_task_candidate_views as task_view_cli
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE
from longworld.core.standardsworkflow import (
    build_ietf_cross_spec_requirement_task,
    materialize_ietf_cross_spec_counterfactual,
)
from longworld.core.taskpromotion import build_task_candidate_view_projections
from longworld.core.taskproof import (
    TaskProofError,
    audit_task_view_projection,
    replay_ietf_cross_spec_candidate,
    replay_ietf_cross_spec_raw_slice,
)
from longworld.core.taskreplaysidecar import (
    IETF_OAUTH_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from reports.p24_ietf_oauth_cross_spec_generate import _artifacts_for_bucket
from tests.test_ietf_cross_spec_requirement import _oauth_manifest

CANDIDATE_KEY = b"ietf-projection-candidate-key-32-bytes-minimum"
SOURCE_KEY = b"ietf-projection-source-key-32-bytes-minimum"


class _RepeatedCharacterOffsets:
    def __init__(self, counter) -> None:
        self._counter = counter

    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        return_offsets_mapping: bool,
    ) -> dict[str, list[object]]:
        assert add_special_tokens is False
        assert return_offsets_mapping is True
        count = self._counter(text)
        offsets = [
            (
                min(len(text) - 1, index * len(text) // count),
                min(len(text), min(len(text) - 1, index * len(text) // count) + 1),
            )
            for index in range(count)
        ]
        return {"input_ids": list(range(count)), "offset_mapping": offsets}


def test_materializes_byte_bound_rfc9700_bearer_requirement_exclusion(
    tmp_path,
) -> None:
    task = build_ietf_cross_spec_requirement_task(_oauth_manifest(tmp_path))

    materialized = materialize_ietf_cross_spec_counterfactual(task)

    twin = materialized["counterfactual_twin"]
    parent = materialized["parent_text"]
    child = materialized["text"]
    start, end = twin["char_start"], twin["char_end"]
    byte_start, byte_end = twin["byte_start"], twin["byte_end"]
    assert twin["provenance_operation"] == "exclude_exact_source_span"
    assert twin["record_id"] == "ietf:rfc:9700"
    assert twin["evidence_id"] == "bearer_current"
    assert parent[start:end] == next(
        item["evidence_quote"]
        for item in task["evidence_items"]
        if item["evidence_id"] == "bearer_current"
    )
    assert child[start:end] == " " * (end - start)
    assert parent.encode()[byte_start:byte_end] == twin["parent_value"].encode()
    assert child.encode()[byte_start:byte_end] == twin["value"].encode()
    assert child != parent
    assert hashlib.sha256(parent.encode()).hexdigest() == twin["parent_text_sha256"]
    assert hashlib.sha256(child.encode()).hexdigest() == twin["text_sha256"]
    assert materialized["answer"] == {
        **task["answer"],
        "bearer_transport": "URI_QUERY_DISCOURAGED_OR_CONDITIONAL",
    }


def test_replays_ietf_answer_from_selected_source_artifacts(tmp_path) -> None:
    task = build_ietf_cross_spec_requirement_task(_oauth_manifest(tmp_path))
    records = task["source_manifest"]["records"]
    artifact_ids = [record["record_id"] for record in records]
    candidate = {
        "ietf_requirement_task": task,
        "source_record_ids_by_artifact": {
            artifact_id: [artifact_id] for artifact_id in artifact_ids
        },
        "artifact_classification": [
            {
                "artifact_id": record["record_id"],
                "source_record_id": record["record_id"],
                "source_char_start": 0,
                "source_char_end": len(record["text"]),
            }
            for record in records
        ],
        "document_context": SEP.join(record["text"] for record in records),
    }
    expected = json.dumps(task["answer"], sort_keys=True, separators=(",", ":"))

    assert (
        replay_ietf_cross_spec_candidate(candidate, artifact_ids)["answer"] == expected
    )
    without_update = replay_ietf_cross_spec_candidate(
        candidate,
        [artifact_id for artifact_id in artifact_ids if artifact_id != "ietf:rfc:9700"],
    )
    assert without_update["answer"] != expected


def test_p28_partial_window_does_not_grant_whole_ietf_record(tmp_path) -> None:
    task = build_ietf_cross_spec_requirement_task(_oauth_manifest(tmp_path))
    records = task["source_manifest"]["records"]
    artifact_ids = [record["record_id"] for record in records]
    candidate = {
        "ietf_requirement_task": task,
        "source_record_ids_by_artifact": {
            record["record_id"]: [record["record_id"]] for record in records
        },
        "artifact_classification": [
            {
                "artifact_id": record["record_id"],
                "source_record_id": record["record_id"],
                "source_char_start": 0,
                "source_char_end": (
                    8 if record["record_id"] == "ietf:rfc:9700" else len(record["text"])
                ),
            }
            for record in records
        ],
        "document_context": SEP.join(
            record["text"][:8]
            if record["record_id"] == "ietf:rfc:9700"
            else record["text"]
            for record in records
        ),
    }

    answer = json.loads(
        replay_ietf_cross_spec_candidate(candidate, artifact_ids)["answer"]
    )

    assert answer == {
        "redirect_match": "UNKNOWN",
        "bearer_transport": "URI_QUERY_DISCOURAGED_OR_CONDITIONAL",
        "refresh_protection": "UNKNOWN",
        "pkce": "UNKNOWN",
        "metadata_issuer": "UNKNOWN",
        "multi_as_issuer": "UNKNOWN",
    }


def test_rejects_ietf_artifact_bytes_that_do_not_match_source_span(tmp_path) -> None:
    candidate, _token_counter = _projection_candidate(tmp_path)
    classifications = candidate["artifact_classification"]
    documents = candidate["document_context"].split(SEP)
    index = next(
        index
        for index, item in enumerate(classifications)
        if item["source_record_id"] == "ietf:rfc:6749"
    )
    documents[index] = "X" * len(documents[index])
    candidate["document_context"] = SEP.join(documents)

    with pytest.raises(TaskProofError, match="source bytes"):
        replay_ietf_cross_spec_candidate(
            candidate, [item["artifact_id"] for item in classifications]
        )


def test_replays_ietf_answer_only_from_complete_raw_artifacts(tmp_path) -> None:
    task = build_ietf_cross_spec_requirement_task(_oauth_manifest(tmp_path))
    records = task["source_manifest"]["records"]
    candidate = {
        "ietf_requirement_task": task,
        "source_record_ids_by_artifact": {
            record["record_id"]: [record["record_id"]] for record in records
        },
        "artifact_classification": [
            {
                "artifact_id": record["record_id"],
                "source_record_id": record["record_id"],
                "source_char_start": 0,
                "source_char_end": len(record["text"]),
            }
            for record in records
        ],
        "document_context": SEP.join(record["text"] for record in records),
    }
    expected = json.dumps(task["answer"], sort_keys=True, separators=(",", ":"))

    assert (
        replay_ietf_cross_spec_raw_slice(
            candidate,
            candidate["document_context"],
            left_framed=True,
            right_framed=True,
        )["answer"]
        == expected
    )
    without_update = SEP.join(
        record["text"] for record in records if record["record_id"] != "ietf:rfc:9700"
    )
    assert (
        replay_ietf_cross_spec_raw_slice(
            candidate,
            without_update,
            left_framed=True,
            right_framed=True,
        )["answer"]
        != expected
    )


def _projection_candidate(tmp_path: Path):
    task = build_ietf_cross_spec_requirement_task(_oauth_manifest(tmp_path))
    materialized = materialize_ietf_cross_spec_counterfactual(task)
    records = task["source_manifest"]["records"]
    essential_relation_ids = set(task["essential_relation_ids"])
    essential_record_ids = {item["record_id"] for item in task["evidence_items"]} | {
        endpoint
        for relation in task["source_manifest"]["relations"]
        if relation["relation_id"] in essential_relation_ids
        for endpoint in (relation["source_record_id"], relation["target_record_id"])
    }
    document_context = SEP.join(record["text"] for record in records)
    question = "Resolve the six OAuth deployment requirements from the RFC graph."
    context = wrap_prompt(question, document_context, "first")
    assert len(context) * 5 > 16_000

    candidate = {
        "world_id": "ietf-oauth-cross-spec",
        "query_id": "ietf-oauth-cross-spec-16k",
        "domain": "standards",
        "data_stage": "candidate",
        "training_objective": "sft",
        "length_bucket": "16k",
        "view": "full",
        "composition_method": "same_case_dossier",
        "query_timing": "first",
        "question": question,
        "answer": json.dumps(task["answer"], sort_keys=True, separators=(",", ":")),
        "cf_answer": json.dumps(
            materialized["answer"], sort_keys=True, separators=(",", ":")
        ),
        "document_context": document_context,
        "context": context,
        "artifact_classification": [
            {
                "artifact_id": record["record_id"],
                "workflow_id": "ietf-oauth-cross-spec-fixture",
                "source_origin": "real_public",
                "workflow_kind": "real_source_derived",
                "evidence_role": (
                    "causal_gold"
                    if record["record_id"] in essential_record_ids
                    else "causal_supporting"
                ),
                "provenance_id": f"source-sha256:{record['source_sha256']}",
                "source_record_id": record["record_id"],
                "source_char_start": 0,
                "source_char_end": len(record["text"]),
            }
            for record in records
        ],
        "source_record_ids_by_artifact": {
            record["record_id"]: [record["record_id"]] for record in records
        },
        "essential_artifact_ids": sorted(essential_record_ids),
        "source_binding": {"signed_manifest_sha256": task["source_manifest_sha256"]},
        "source_family_ids": ["ietf_standards"],
        "task_replay_sidecar": {
            "adapter_id": IETF_OAUTH_TASK_REPLAY_ADAPTER[0],
            "adapter_revision": IETF_OAUTH_TASK_REPLAY_ADAPTER[1],
            "sidecar_schema_version": IETF_OAUTH_TASK_REPLAY_ADAPTER[2],
            "sha256": "a" * 64,
        },
        "strict_replay_revision": IETF_OAUTH_TASK_REPLAY_ADAPTER[1],
        "ietf_requirement_task": task,
        "counterfactual_twin": materialized["counterfactual_twin"],
        "answer_program_id": task["answer_program_id"],
        "graph": {"proof_depth": 2, "hop_count": 2},
        "real_source_token_ratio": 1.0,
        "tokenizer_model_id": "test-character-counter",
        "tokenizer_revision": "b" * 40,
        "tokenizer_asset_manifest_sha256": "c" * 64,
    }

    def token_counter(text: str) -> int:
        return max(1, len(text) * 5 - (len(context) * 5 - 16_000))

    token_counter.offset_tokenizer = _RepeatedCharacterOffsets(token_counter)  # type: ignore[attr-defined]
    return candidate, token_counter


def test_projects_source_bound_ietf_full_cf_and_ordered_views(tmp_path) -> None:
    candidate, token_counter = _projection_candidate(tmp_path)

    views = build_task_candidate_view_projections(
        candidate,
        adapter_key=IETF_OAUTH_TASK_REPLAY_ADAPTER,
        token_counter=token_counter,
        candidate_attestation_key=CANDIDATE_KEY,
    )

    by_view = {view["view"]: view for view in views}
    assert set(by_view) == {"full", "cf", "ordered_artifact_view"}
    assert by_view["cf"]["document_context"] != by_view["full"]["document_context"]
    assert by_view["cf"]["answer"] == candidate["cf_answer"]
    assert by_view["cf"]["strict_support_event_count"] == 6
    assert all(
        replay_ietf_cross_spec_candidate(
            by_view["cf"],
            [
                artifact_id
                for artifact_id in by_view["cf"]["essential_artifact_ids"]
                if artifact_id != removed
            ],
            counterfactual=True,
        )["answer"]
        != by_view["cf"]["answer"]
        for removed in by_view["cf"]["essential_artifact_ids"]
    )
    assert by_view["full"]["answer"] == candidate["answer"]
    assert all(audit_task_view_projection(by_view["ordered_artifact_view"]).values())


def test_projects_counterfactual_into_unique_source_span_chunk(tmp_path) -> None:
    candidate, token_counter = _projection_candidate(tmp_path)
    task = candidate["ietf_requirement_task"]
    twin = candidate["counterfactual_twin"]
    record_id = twin["record_id"]
    record = next(
        record
        for record in task["source_manifest"]["records"]
        if record["record_id"] == record_id
    )
    record_text = record["text"]
    old_classifications = candidate["artifact_classification"]
    old_documents = candidate["document_context"].split(SEP)
    record_index = next(
        index
        for index, item in enumerate(old_classifications)
        if item["artifact_id"] == record_id
    )
    base = old_classifications[record_index]
    record_evidence = [
        item for item in task["evidence_items"] if item["record_id"] == record_id
    ]
    ordered_evidence = sorted(record_evidence, key=lambda item: item["char_start"])
    bearer_index = next(
        index
        for index, item in enumerate(ordered_evidence)
        if item["evidence_id"] == twin["evidence_id"]
    )
    containing_start = 0
    containing_end = (
        ordered_evidence[bearer_index]["char_end"]
        + ordered_evidence[bearer_index + 1]["char_start"]
    ) // 2
    spans = [
        (containing_start, containing_end),
        (containing_end, len(record_text)),
    ]
    chunk_classifications = []
    chunk_documents = []
    for start, end in spans:
        document = record_text[start:end]
        classification = deepcopy(base)
        classification.update(
            {
                "artifact_id": f"{record_id}:chars:{start}-{end}",
                "provenance_id": (
                    f"source-span-sha256:{record['source_sha256']}:{start}:{end}:"
                    f"{hashlib.sha256(document.encode()).hexdigest()}"
                ),
                "source_char_start": start,
                "source_char_end": end,
            }
        )
        chunk_classifications.append(classification)
        chunk_documents.append(document)
    candidate["artifact_classification"] = [
        *old_classifications[:record_index],
        *chunk_classifications,
        *old_classifications[record_index + 1 :],
    ]
    candidate["document_context"] = SEP.join(
        [
            *old_documents[:record_index],
            *chunk_documents,
            *old_documents[record_index + 1 :],
        ]
    )
    candidate["context"] = wrap_prompt(
        candidate["question"], candidate["document_context"], "first"
    )
    candidate["source_record_ids_by_artifact"].pop(record_id)
    candidate["source_record_ids_by_artifact"].update(
        {item["artifact_id"]: [record_id] for item in chunk_classifications}
    )
    candidate["essential_artifact_ids"] = [
        artifact_id
        for artifact_id in candidate["essential_artifact_ids"]
        if artifact_id != record_id
    ] + [item["artifact_id"] for item in chunk_classifications]

    views = build_task_candidate_view_projections(
        candidate,
        adapter_key=IETF_OAUTH_TASK_REPLAY_ADAPTER,
        token_counter=token_counter,
        candidate_attestation_key=CANDIDATE_KEY,
    )

    cf = next(view for view in views if view["view"] == "cf")
    changed = next(
        item
        for item in cf["artifact_classification"]
        if item["source_origin"] == "synthetic_counterfactual"
    )
    changed_document = cf["document_context"].split(SEP)[
        cf["artifact_classification"].index(changed)
    ]
    assert changed["source_char_start"] == containing_start
    assert changed["source_char_end"] == containing_end
    assert changed["counterfactual_operation_source_char_start"] == twin["char_start"]
    assert changed["counterfactual_operation_source_char_end"] == twin["char_end"]
    local_start = twin["char_start"] - containing_start
    assert changed["counterfactual_operation_local_char_start"] == local_start
    assert changed["counterfactual_operation_local_char_end"] == local_start + len(
        twin["parent_value"]
    )
    assert (
        changed["counterfactual_child_text_sha256"]
        == hashlib.sha256(changed_document.encode()).hexdigest()
    )


def test_omits_fully_excluded_ietf_counterfactual_chunk(tmp_path) -> None:
    candidate, _token_counter = _projection_candidate(tmp_path)
    task = candidate["ietf_requirement_task"]
    twin = candidate["counterfactual_twin"]
    record_id = twin["record_id"]
    record = next(
        record
        for record in task["source_manifest"]["records"]
        if record["record_id"] == record_id
    )
    record_text = record["text"]
    classifications = candidate["artifact_classification"]
    documents = candidate["document_context"].split(SEP)
    record_index = next(
        index
        for index, item in enumerate(classifications)
        if item["artifact_id"] == record_id
    )
    base = classifications[record_index]
    spans = [
        (0, twin["char_start"]),
        (twin["char_start"], twin["char_end"]),
        (twin["char_end"], len(record_text)),
    ]
    chunks = []
    for start, end in spans:
        document = record_text[start:end]
        classification = deepcopy(base)
        classification.update(
            {
                "artifact_id": f"{record_id}:chars:{start}-{end}",
                "provenance_id": (
                    f"source-span-sha256:{record['source_sha256']}:{start}:{end}:"
                    f"{hashlib.sha256(document.encode()).hexdigest()}"
                ),
                "source_char_start": start,
                "source_char_end": end,
            }
        )
        chunks.append((classification, document))
    candidate["artifact_classification"] = [
        *classifications[:record_index],
        *(classification for classification, _document in chunks),
        *classifications[record_index + 1 :],
    ]
    candidate["document_context"] = SEP.join(
        [
            *documents[:record_index],
            *(document for _classification, document in chunks),
            *documents[record_index + 1 :],
        ]
    )
    candidate["context"] = wrap_prompt(
        candidate["question"], candidate["document_context"], "first"
    )
    parent_context_length = len(candidate["context"])

    def token_counter(text: str) -> int:
        return max(1, 16_200 + len(text) - parent_context_length)

    token_counter.offset_tokenizer = _RepeatedCharacterOffsets(token_counter)  # type: ignore[attr-defined]
    candidate["source_record_ids_by_artifact"].pop(record_id)
    candidate["source_record_ids_by_artifact"].update(
        {classification["artifact_id"]: [record_id] for classification, _ in chunks}
    )
    candidate["essential_artifact_ids"] = [
        artifact_id
        for artifact_id in candidate["essential_artifact_ids"]
        if artifact_id != record_id
    ] + [classification["artifact_id"] for classification, _document in chunks]
    omitted_id = chunks[1][0]["artifact_id"]

    views = build_task_candidate_view_projections(
        candidate,
        adapter_key=IETF_OAUTH_TASK_REPLAY_ADAPTER,
        token_counter=token_counter,
        candidate_attestation_key=CANDIDATE_KEY,
    )

    cf = next(view for view in views if view["view"] == "cf")
    assert omitted_id not in {
        item["artifact_id"] for item in cf["artifact_classification"]
    }
    assert omitted_id not in cf["essential_artifact_ids"]
    assert omitted_id not in cf["source_record_ids_by_artifact"]
    projection = cf["task_view_projection"]
    assert omitted_id not in {
        item["artifact_id"] for item in projection["artifact_bindings"]
    }
    assert omitted_id not in {
        item["artifact_id"] for item in projection["parent_artifact_bindings"]
    }
    receipt = projection["source_token_measurement_receipt"]
    assert omitted_id not in {
        item["artifact_id"] for item in receipt["parent_artifact_token_contributions"]
    }
    assert all(document.strip() for document in cf["document_context"].split(SEP))
    assert all(
        replay_ietf_cross_spec_candidate(
            cf,
            [
                artifact_id
                for artifact_id in cf["essential_artifact_ids"]
                if artifact_id != removed
            ],
            counterfactual=True,
        )["answer"]
        != cf["answer"]
        for removed in cf["essential_artifact_ids"]
    )


def test_coarsens_bearer_with_contiguous_independent_requirement() -> None:
    units = [
        "metadata-current " + "m" * 36,
        "intervening-authentic-source " + "i" * 24,
        "bearer-current " + "b" * 38,
        "trailing-authentic-source " + "t" * 28,
    ]
    text = "\n\n".join(units)
    evidence = []
    for evidence_id, quote in (
        ("metadata_current", "metadata-current"),
        ("bearer_current", "bearer-current"),
    ):
        start = text.index(quote)
        evidence.append(
            {
                "evidence_id": evidence_id,
                "record_id": "ietf:rfc:9700",
                "char_start": start,
                "char_end": start + len(quote),
            }
        )
    task = {
        "question": "Resolve the requirements.",
        "source_manifest": {
            "records": [
                {
                    "record_id": "ietf:rfc:9700",
                    "text": text,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_url": "https://www.rfc-editor.org/rfc/rfc9700.txt",
                    "occurred_at": "2025-01-01T00:00:00Z",
                }
            ]
        },
        "evidence_items": evidence,
    }

    artifacts, _tokens = _artifacts_for_bucket(
        task,
        len,
        "fixture",
        1,
        100_000,
        0,
        chunked_record_ids=frozenset({"ietf:rfc:9700"}),
        chunk_max_tokens=64,
        counterfactual_companion_evidence_id="metadata_current",
    )

    containing = [
        artifact
        for artifact in artifacts
        if all(
            artifact["char_start"] <= item["char_start"]
            and item["char_end"] <= artifact["char_end"]
            for item in evidence
        )
    ]
    assert len(containing) == 1
    [artifact] = containing
    assert artifact["text"] == text[artifact["char_start"] : artifact["char_end"]]
    assert artifact["text"].replace("bearer-current", " ").strip()


def test_public_projection_serializes_source_bound_ietf_v3_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    for role, key in (
        ("candidate", CANDIDATE_KEY),
        ("source", SOURCE_KEY),
        ("ranker", b"ietf-projection-ranker-key-32-bytes-minimum"),
        ("auditor", b"ietf-projection-auditor-key-32-bytes-minimum"),
    ):
        monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"probe-ietf-projection-{role}-v1")
    candidate, token_counter = _projection_candidate(tmp_path)
    task = candidate["ietf_requirement_task"]
    adapter_id, adapter_revision, schema_version = IETF_OAUTH_TASK_REPLAY_ADAPTER
    sidecar = build_task_replay_sidecar(
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        sidecar_schema_version=schema_version,
        replay_payload={
            "source_manifest_sha256": task["source_manifest_sha256"],
            "fetch_inventory_sha256": task["source_manifest"]["fetch_inventory_sha256"],
            "authorization_record_id": task["source_manifest"]["authorization"][
                "record_id"
            ],
            "ietf_requirement_task": task,
            "task_sha256": hashlib.sha256(
                json.dumps(
                    task,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "replay_revision": adapter_revision,
            "tokenizer_model_id": candidate["tokenizer_model_id"],
            "tokenizer_revision": candidate["tokenizer_revision"],
            "tokenizer_asset_manifest_sha256": candidate[
                "tokenizer_asset_manifest_sha256"
            ],
            "candidate_content_commitments": [
                task_candidate_content_commitment(candidate)
            ],
        },
        source_attestation_key=SOURCE_KEY,
    )
    sidecar_raw = (
        json.dumps(sidecar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    sidecar_path = tmp_path / "TASK_REPLAY_SIDECAR.json"
    sidecar_path.write_bytes(sidecar_raw)
    candidate["task_replay_sidecar"] = task_replay_sidecar_binding(
        sidecar_raw, source_attestation_key=SOURCE_KEY
    )
    signed_candidate = attach_attestation(
        candidate, CANDIDATE_KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    candidate_path = tmp_path / "candidates.jsonl"
    candidate_path.write_text(
        json.dumps(
            signed_candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    monkeypatch.setattr(
        task_view_cli, "task_sidecar_token_counter", lambda _: token_counter
    )
    monkeypatch.chdir(tmp_path)
    relative_root = Path("linked-candidate-root")
    relative_root.symlink_to(tmp_path, target_is_directory=True)

    manifest = task_view_cli.project(
        relative_root / "candidates.jsonl",
        relative_root / "TASK_REPLAY_SIDECAR.json",
        relative_root / "projected",
    )

    assert manifest["projection_candidate_count"] == 3
    assert manifest["dense_audit_complete"] is False
    assert (relative_root / "projected/TASK_REPLAY_SIDECAR_V3.json").is_file()
    with pytest.raises(ValueError, match="task candidate input is not"):
        task_view_cli.audit_projections(
            relative_root / "projected", relative_root / "missing-rankings.jsonl"
        )
