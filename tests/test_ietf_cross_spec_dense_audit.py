from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.project_task_candidate_views as task_view_cli
from longworld.core import taskpromotion
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.pack import SEP
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_RANKING_PURPOSE,
    candidate_sha256,
)
from longworld.core.taskpromotion import create_task_dense_audit
from longworld.core.taskreplaysidecar import (
    IETF_OAUTH_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from tests.test_ietf_cross_spec_projection import (
    CANDIDATE_KEY,
    SOURCE_KEY,
    _projection_candidate,
)

RANKER_KEY = b"ietf-dense-ranker-key-32-bytes-minimum"
AUDITOR_KEY = b"ietf-dense-auditor-key-32-bytes-minimum"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


def test_public_dense_audit_replays_projected_ietf_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    for role, key in (
        ("candidate", CANDIDATE_KEY),
        ("source", SOURCE_KEY),
        ("ranker", RANKER_KEY),
        ("auditor", AUDITOR_KEY),
    ):
        monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"probe-ietf-dense-{role}-v1")

    parent, token_counter = _projection_candidate(tmp_path)
    task = parent["ietf_requirement_task"]
    adapter_id, adapter_revision, schema_version = IETF_OAUTH_TASK_REPLAY_ADAPTER
    signed_sidecar = build_task_replay_sidecar(
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
            "tokenizer_model_id": parent["tokenizer_model_id"],
            "tokenizer_revision": parent["tokenizer_revision"],
            "tokenizer_asset_manifest_sha256": parent[
                "tokenizer_asset_manifest_sha256"
            ],
            "candidate_content_commitments": [
                task_candidate_content_commitment(parent)
            ],
        },
        source_attestation_key=SOURCE_KEY,
    )
    sidecar_raw = (
        json.dumps(
            signed_sidecar,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    sidecar_path = tmp_path / "TASK_REPLAY_SIDECAR.json"
    sidecar_path.write_bytes(sidecar_raw)
    parent["task_replay_sidecar"] = task_replay_sidecar_binding(
        sidecar_raw, source_attestation_key=SOURCE_KEY
    )
    parent = attach_attestation(
        parent, CANDIDATE_KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    parent_path = tmp_path / "parents.jsonl"
    parent_path.write_text(
        json.dumps(parent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    monkeypatch.setattr(
        task_view_cli, "task_sidecar_token_counter", lambda _: token_counter
    )
    output_dir = tmp_path / "projected"
    task_view_cli.project(parent_path, sidecar_path, output_dir)

    candidates = [
        json.loads(line)
        for line in (output_dir / "candidates.jsonl").read_text().splitlines()
    ]
    candidate = next(row for row in candidates if row["view"] == "full")
    order_edges = candidate["verified_derived_order_relation_edges"]
    authenticated_relations = {
        relation["relation_id"]: relation
        for relation in task["source_manifest"]["relations"]
    }
    assert order_edges
    assert all(
        edge["source_relation_id"] in authenticated_relations
        and edge["source_relation_kind"]
        == authenticated_relations[edge["source_relation_id"]]["kind"]
        and edge["source_relation_kind"]
        in {
            "published_as",
            "updates",
            "normative_reference",
            "informative_reference",
        }
        and edge["parent_occurred_at"] < edge["child_occurred_at"]
        for edge in order_edges
    )
    v3_raw = (output_dir / "TASK_REPLAY_SIDECAR_V3.json").read_bytes()
    v3_binding = task_replay_sidecar_binding(v3_raw, source_attestation_key=SOURCE_KEY)
    sidecar = load_task_replay_sidecar(
        output_dir,
        "TASK_REPLAY_SIDECAR_V3.json",
        v3_binding,
        source_attestation_key=SOURCE_KEY,
    )
    documents = candidate["document_context"].split(SEP)
    ranking = attach_attestation(
        {
            "schema_version": "dense-ranking-v2",
            "ranker_type": "dense_embedding",
            "query_id": candidate["query_id"],
            "candidate_sha256": candidate_sha256(candidate),
            "query_sha256": hashlib.sha256(candidate["question"].encode()).hexdigest(),
            "model": {
                "provider": "huggingface",
                "model_id": "sentence-transformers/all-MiniLM-L6-v2",
                "revision": MODEL_REVISION,
                "backend": "sentence-transformers-6.0.0",
                "score_metric": "dot_product",
                "chunking": {
                    "strategy": "tokenizer_token_windows",
                    "max_tokens": 192,
                    "overlap_tokens": 32,
                    "aggregation": "max_similarity",
                },
            },
            "artifacts": [
                {
                    "rank": rank,
                    "artifact_id": classification["artifact_id"],
                    "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                    "score": 1.0 - rank / 1000.0,
                    "chunk_count": 1,
                }
                for rank, (classification, document) in enumerate(
                    zip(candidate["artifact_classification"], documents, strict=True),
                    start=1,
                )
            ],
        },
        RANKER_KEY,
        purpose=DENSE_RANKING_PURPOSE,
    )
    monkeypatch.setattr(
        taskpromotion, "task_sidecar_token_counter", lambda _: token_counter
    )

    audit = create_task_dense_audit(
        candidate,
        ranking,
        sidecar,
        candidate_attestation_key=CANDIDATE_KEY,
        ranking_attestation_key=RANKER_KEY,
        audit_attestation_key=AUDITOR_KEY,
        source_attestation_key=SOURCE_KEY,
    )

    assert audit["expected_answer"] == candidate["answer"]
    assert audit["counterfactual_replay_answer"] == candidate["cf_answer"]
    assert audit["embedding_topk_insufficient"] is True
