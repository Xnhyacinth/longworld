from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import longworld.core.promotion as promotion_module
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    LOCAL_PROBE_COMBINED_ROLES_ENV,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
    verify_attestation,
)
from longworld.core.filingworkflow import build_sec_filing_manifest
from longworld.core.graph import graph_stats
from longworld.core.pack import (
    SEP,
    compute_view_metrics,
    estimate_tokens,
    join_artifacts,
    prompt_document_prefix,
    prompt_query_boundary,
    wrap_prompt,
)
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_AUDIT_PURPOSE,
    DENSE_RANKING_PURPOSE,
    LEGACY_RELEASE_GATE_REVISION,
    PROMOTION_SCHEMA,
    QUALITY_REPORT_BINDING_REVISION,
    RELEASE_GATE_PURPOSE,
    RELEASE_GATE_REVISION,
    RELEASE_SELECTION_PURPOSE,
    RELEASE_SELECTION_SCHEMA,
    STRICT_REPLAY_REVISION,
    PromotionError,
    _candidate_has_source_bound_proof,
    _candidate_has_verified_real_source,
    _candidate_include_program_joins,
    _candidate_n_workstreams,
    _independent_verification_replay,
    _materialize_synthetic_replay,
    _missing_required_view_coverage_by_world,
    _n_workstreams,
    _replayed_quality_metrics,
    _resolved_local_tokenizer_revision,
    _synthetic_replay_materialization,
    candidate_sha256,
    candidate_structural_preflight,
    create_dense_audit,
    create_train_ready_report,
    promote_candidate,
    promoted_row_set_sha256,
    release_gate_revision_supported,
    select_release_worlds,
    serialized_row_sha256,
    stable_semantic_base_task_id,
    validate_predecessor_gate_receipt,
)
from longworld.core.realworkflow import load_episode_replay_bundle
from longworld.core.record_contract import sft_row_errors
from longworld.core.release_profile import (
    RELEASE_PROFILES,
    release_profile,
    release_profile_sha256,
)
from longworld.core.sampler import materialize
from longworld.core.semantic import (
    boilerplate_char_fraction,
    pulse_doc_ratio,
    sentence_near_dup_ratio,
)
from longworld.core.sourcebundle import LoadedSourceWorkflowBundle
from longworld.core.taskproof import TASK_PROOF_RECEIPT_SCHEMA
from longworld.core.taskreplaysidecar import task_candidate_content_commitment
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    artifact_classification,
    classify_artifact,
)
from longworld.core.verify import Verification
from longworld.core.views import render_cf_view

KEY = b"longworld-dense-promotion-test-key-32-bytes"


@pytest.fixture(autouse=True)
def _semantic_attestation_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    for role, environment_name in ROLE_KEY_ENVS.items():
        monkeypatch.setenv(environment_name, KEY.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"test-{role}-v1")
    monkeypatch.setenv("LONGWORLD_PUBLIC_POLICY_SHA256", "d" * 64)
    monkeypatch.setenv("LONGWORLD_GH_BINARY_SHA256", "e" * 64)


def test_workstream_reconstruction_accepts_any_bound_workflow_stage() -> None:
    assert (
        _n_workstreams(
            [
                {
                    "artifact_id": "w.focal.workflow_10_review",
                    "text": "review",
                    "text_sha256": "digest",
                }
            ]
        )
        == 11
    )


def test_candidate_workstream_count_is_explicit_for_non_code_domains() -> None:
    bindings = [
        {
            "artifact_id": "lab.focal.experiment_xp01_recovery",
            "text": "recovery",
            "text_sha256": "digest",
        }
    ]

    assert _candidate_n_workstreams({"n_workstreams": 36}, bindings) == 36
    with pytest.raises(PromotionError, match="n_workstreams"):
        _candidate_n_workstreams({"n_workstreams": True}, bindings)
    assert _candidate_include_program_joins({"include_program_joins": False}) is False
    assert _candidate_include_program_joins({}) is True
    with pytest.raises(PromotionError, match="include_program_joins"):
        _candidate_include_program_joins({"include_program_joins": "false"})


def test_synthetic_replay_caches_baseline_but_returns_isolated_copies(
    monkeypatch,
) -> None:
    calls: list[tuple[int, str, int, bool]] = []

    def fake_materialize(
        seed: int,
        *,
        n_parallel: int,
        n_pulses: int,
        domain: str,
        n_workstreams: int,
        include_program_joins: bool,
    ) -> dict:
        assert (n_parallel, n_pulses) == (0, 0)
        calls.append((seed, domain, n_workstreams, include_program_joins))
        return {"history": []}

    monkeypatch.setattr("longworld.core.promotion.materialize", fake_materialize)
    _materialize_synthetic_replay.cache_clear()

    first = _synthetic_replay_materialization(17, "researchlab", 88, False)
    second = _synthetic_replay_materialization(17, "researchlab", 88, False)
    first["history"].append("mutated")

    assert first is not second
    assert second["history"] == []
    assert calls == [(17, "researchlab", 88, False)]
    _materialize_synthetic_replay.cache_clear()


def _candidate(
    view: str = "full", query_type: str = "program_join", *, seed: int = 1
) -> tuple[dict, list]:
    n_workstreams = (
        5
        if query_type
        in {"release_eligibility", "release_ci_matrix", "release_license_matrix"}
        else 0
    )
    materialized = materialize(
        seed,
        n_parallel=0,
        n_pulses=0,
        domain="codeforge",
        n_workstreams=n_workstreams,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == query_type
        and (
            query_type != "program_join"
            or query.query_id.endswith("join:delayed_effect+hidden_bridge")
        )
    )
    source_artifacts = materialized.artifacts["focal"]
    if view == "cf":
        _, source_artifacts = render_cf_view(world, spec)
    index = {artifact.artifact_id: artifact for artifact in source_artifacts}
    essential = [index[artifact_id] for artifact_id in spec.essential_artifact_ids]
    distractors = [
        artifact
        for artifact in source_artifacts
        if artifact.artifact_id not in spec.essential_artifact_ids
        and artifact_classification(artifact).workflow_kind.value != "background_only"
    ][:3]
    artifacts = distractors + essential
    if view == "ordered_artifact_view":
        artifacts.sort(key=lambda artifact: (artifact.time, artifact.artifact_id))
    gates = {
        field: True
        for field in Verification.model_fields
        if field not in {"production_mode", "candidate_mode"}
    }
    gates["embedding_topk_insufficient"] = False
    document_context = join_artifacts(artifacts)
    context = wrap_prompt(spec.question, document_context, "first")
    metrics = compute_view_metrics(
        artifacts,
        set(spec.essential_artifact_ids),
        query_timing="first",
        context=context,
    )
    row = {
        "world_id": world.world_id,
        "seed": world.seed,
        "schema_version": "p3.0",
        "data_product": "worldlong_valid_v1",
        "query_id": (
            f"{spec.query_id}:first:{metrics.position_bucket}:{metrics.length_bucket}"
        ),
        "query_type": spec.query_type,
        "query_timing": "first",
        "position_bucket": metrics.position_bucket,
        "length_bucket": metrics.length_bucket,
        "question": spec.question,
        "answer": spec.cf_answer if view == "cf" else spec.answer,
        "cf_answer": spec.cf_answer,
        "view": view,
        "context": context,
        "document_context": document_context,
        "essential_artifact_ids": list(spec.essential_artifact_ids),
        "verification": Verification(
            production_mode=False, candidate_mode=True, **gates
        ).model_dump(),
        "view_verification": {
            "production_eligible": True,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": spec.cf_answer if view == "cf" else spec.answer,
            "strict_replay_answer": spec.cf_answer if view == "cf" else spec.answer,
        },
        "artifact_classification": [
            {
                "artifact_id": artifact.artifact_id,
                "workflow_id": world.world_id,
                "workflow_kind": "synthetic_executable",
                "evidence_role": (
                    "causal_gold"
                    if artifact.artifact_id in spec.essential_artifact_ids
                    else (
                        "causal_supporting"
                        if set(spec.sufficient_event_ids).intersection(
                            artifact.reveals_events
                        )
                        else "structural_hard_negative"
                    )
                ),
                "source_origin": "synthetic_world",
                "provenance_id": (
                    "synthetic-sha256:"
                    + hashlib.sha256(artifact.text.encode()).hexdigest()
                ),
            }
            for artifact in artifacts
        ],
        "workflow_ids": [world.world_id],
        "training_objective": "sft",
        "composition_method": (
            "counterfactual_twin"
            if view == "cf"
            else "causal_timeline"
            if view == "ordered_artifact_view"
            else "same_case_dossier"
        ),
        "domain": "codeforge",
        "split": "train",
        "motif": spec.motif,
        "data_stage": "candidate",
        "base_task_id": hashlib.sha256(
            f"{world.world_id}|{spec.base_task_group or spec.query_id}".encode()
        ).hexdigest()[:20],
        "dossier_id": hashlib.sha256(
            json.dumps(
                {
                    "world_id": world.world_id,
                    "query_id": spec.query_id,
                    "query_timing": "first",
                    "artifact_ids": sorted(
                        artifact.artifact_id for artifact in artifacts
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:20],
    }
    return attach_attestation(
        row, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    ), artifacts


def _ranking(row: dict, artifacts: list) -> dict:
    return attach_attestation(
        {
            "schema_version": "dense-ranking-v2",
            "ranker_type": "dense_embedding",
            "query_id": row["query_id"],
            "candidate_sha256": candidate_sha256(row),
            "query_sha256": hashlib.sha256(row["question"].encode()).hexdigest(),
            "model": {
                "provider": "huggingface",
                "model_id": "sentence-transformers/all-MiniLM-L6-v2",
                "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
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
                    "artifact_id": artifact.artifact_id,
                    "text_sha256": hashlib.sha256(artifact.text.encode()).hexdigest(),
                    "score": 1.0 - rank / 100.0,
                    "chunk_count": 1,
                }
                for rank, artifact in enumerate(artifacts, start=1)
            ],
        },
        KEY,
        purpose=DENSE_RANKING_PURPOSE,
    )


def _resign_ranking(ranking: dict) -> dict:
    return attach_attestation(ranking, KEY, purpose=DENSE_RANKING_PURPOSE)


def _episode_bundle(tmp_path: Path) -> Path:
    export_path = tmp_path / "workflow.json"
    records = [
        {
            "id": "issue:17",
            "kind": "issue",
            "occurred_at": "2025-01-02T09:00:00Z",
            "text": "Issue 17 reports folded-header corruption in parser-core.",
            "links": [],
            "attributes": {},
        },
        {
            "id": "pr:18",
            "kind": "pull_request",
            "occurred_at": "2025-01-05T11:00:00Z",
            "text": "Pull request 18 proposes parser-core version 2.4.1.",
            "links": ["issue:17"],
            "attributes": {"package": "parser-core", "version": "2.4.1"},
        },
        {
            "id": "review:18",
            "kind": "review",
            "occurred_at": "2025-01-06T08:00:00Z",
            "text": "Review 18 approved the recovery candidate.",
            "links": ["pr:18"],
            "attributes": {"result": "approved"},
        },
        {
            "id": "commit:cafe123",
            "kind": "commit",
            "occurred_at": "2025-01-07T12:00:00Z",
            "text": "Commit cafe123 packages parser-core version 2.4.1.",
            "links": ["review:18"],
            "attributes": {
                "commit": "cafe123",
                "package": "parser-core",
                "version": "2.4.1",
            },
        },
        {
            "id": "ci:passed",
            "kind": "ci_run",
            "occurred_at": "2025-01-08T15:00:00Z",
            "text": "CI run ci-passed reports passed across the recovery matrix.",
            "links": ["commit:cafe123"],
            "attributes": {"run": "ci-passed", "result": "passed"},
        },
        {
            "id": "license:18",
            "kind": "license",
            "occurred_at": "2025-01-09T09:00:00Z",
            "text": "Apache-2.0 is compatible for redistribution.",
            "links": ["commit:cafe123"],
            "attributes": {"license": "Apache-2.0", "compatible": True},
        },
        {
            "id": "merge:18",
            "kind": "merge",
            "occurred_at": "2025-01-09T12:00:00Z",
            "text": "Merge 18 records the validated release candidate.",
            "links": ["ci:passed", "license:18"],
            "attributes": {"merge_commit_sha": "b" * 40},
        },
        {
            "id": "release:v2.4.1",
            "kind": "release",
            "occurred_at": "2025-01-10T17:00:00Z",
            "text": "Release tag v2.4.1 published after the linked gates.",
            "links": ["merge:18", "ci:passed", "license:18"],
            "attributes": {
                "tag": "v2.4.1",
                "tag_commit_sha": "a" * 40,
                "merge_commit_sha": "b" * 40,
                "ancestry_verified": True,
                "compare_status": "ahead",
                "compare_base_sha": "b" * 40,
                "compare_head_sha": "a" * 40,
                "compare_endpoint": (
                    "https://api.github.com/repos/example/parser/compare/"
                    f"{'b' * 40}...{'a' * 40}"
                ),
                "compare_response_sha256": "c" * 64,
            },
        },
    ]
    export = attach_attestation(
        {
            "schema_version": "longworld.git-workflow.v1",
            "source_origin": "real_public",
            "repository_url": "https://github.com/example/parser",
            "revision": "b" * 40,
            "license": "Apache-2.0",
            "exported_at": "2026-08-20T08:00:00Z",
            "authorization": {
                "record_id": "PUBLIC-GITHUB-TERMS",
                "scope": "read-only workflow export",
                "basis": "public repository",
                "reviewed_at": "2026-08-20T07:00:00Z",
            },
            "privacy_review": {
                "emails": "redacted",
                "secrets": "fail_closed",
                "scanner": "longworld-public-secret-patterns",
                "scanner_revision": "v2",
            },
            "public_policy": {
                "record_id": "PUBLIC-GITHUB-TERMS",
                "sha256": "d" * 64,
            },
            "source_client": {"path": "/usr/bin/gh", "sha256": "e" * 64},
            "records": records,
        },
        KEY,
        purpose="git_workflow",
    )
    export_path.write_text(json.dumps(export), encoding="utf-8")
    export_digest = hashlib.sha256(export_path.read_bytes()).hexdigest()
    bundle_path = tmp_path / "episodes.json"
    bundle = attach_attestation(
        {
            "schema_version": "longworld.episode-replay-bundle.v1",
            "composition": "chronological_causal_union",
            "episodes": [{"path": export_path.name, "sha256": export_digest}],
        },
        KEY,
        purpose="episode_replay_bundle",
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    return bundle_path


def _real_candidate(bundle_path: Path) -> tuple[dict, list]:
    workflows = load_episode_replay_bundle(bundle_path)
    materialized = materialize(
        73,
        n_parallel=0,
        n_pulses=0,
        domain="codeforge",
        real_workflows=workflows,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "version_selection"
    )
    index = {
        artifact.artifact_id: artifact for artifact in materialized.artifacts["focal"]
    }
    essential = [index[artifact_id] for artifact_id in spec.essential_artifact_ids]
    strict = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if set(artifact.reveals_events).intersection(spec.sufficient_event_ids)
        and artifact.artifact_id not in spec.essential_artifact_ids
    ]
    proof_ids = {artifact.artifact_id for artifact in [*essential, *strict]}
    distractors = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id not in proof_ids
        and artifact_classification(artifact).workflow_kind.value != "background_only"
    ][:3]
    artifacts = distractors + essential + strict
    gates = {
        field: True
        for field in Verification.model_fields
        if field not in {"production_mode", "candidate_mode"}
    }
    gates["embedding_topk_insufficient"] = False
    document_context = join_artifacts(artifacts)
    context = wrap_prompt(spec.question, document_context, "first")
    metrics = compute_view_metrics(
        artifacts,
        set(spec.essential_artifact_ids),
        query_timing="first",
        context=context,
    )
    row = {
        "world_id": world.world_id,
        "seed": world.seed,
        "schema_version": "p3.0",
        "data_product": "worldlong_valid_v1",
        "query_id": (
            f"{spec.query_id}:first:{metrics.position_bucket}:{metrics.length_bucket}"
        ),
        "query_type": spec.query_type,
        "query_timing": "first",
        "position_bucket": metrics.position_bucket,
        "length_bucket": metrics.length_bucket,
        "question": spec.question,
        "answer": spec.answer,
        "cf_answer": spec.cf_answer,
        "view": "full",
        "context": context,
        "document_context": document_context,
        "essential_artifact_ids": list(spec.essential_artifact_ids),
        "verification": Verification(
            production_mode=False, candidate_mode=True, **gates
        ).model_dump(),
        "view_verification": {
            "production_eligible": True,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": spec.answer,
            "strict_replay_answer": spec.answer,
        },
        "artifact_classification": [],
        "workflow_ids": [world.world_id],
        "training_objective": "sft",
        "composition_method": "same_case_dossier",
        "domain": "codeforge",
        "motif": spec.motif,
        "data_stage": "candidate",
        "base_task_id": hashlib.sha256(
            f"{world.world_id}|{spec.base_task_group or spec.query_id}".encode()
        ).hexdigest()[:20],
        "dossier_id": hashlib.sha256(
            json.dumps(
                {
                    "world_id": world.world_id,
                    "query_id": spec.query_id,
                    "query_timing": "first",
                    "artifact_ids": sorted(
                        artifact.artifact_id for artifact in artifacts
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:20],
        "episode_replay_bundle": {
            "schema_version": "longworld.episode-replay-bundle.v1",
            "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            "composition": "chronological_causal_union",
        },
    }
    for artifact in artifacts:
        classification = artifact_classification(artifact)
        source_origin = classification.source_origin.value
        workflow_kind = classification.workflow_kind.value
        workflow_id = classification.workflow_id
        provenance_id = classification.provenance_id
        if not workflow_id or workflow_kind == "unclassified":
            source_origin = "synthetic_world"
            workflow_kind = "synthetic_executable"
            workflow_id = world.world_id
            provenance_id = (
                "synthetic-sha256:" + hashlib.sha256(artifact.text.encode()).hexdigest()
            )
        row["artifact_classification"].append(
            {
                "artifact_id": artifact.artifact_id,
                "workflow_id": workflow_id,
                "workflow_kind": workflow_kind,
                "evidence_role": (
                    "causal_gold"
                    if artifact.artifact_id in spec.essential_artifact_ids
                    else (
                        "causal_supporting"
                        if set(spec.sufficient_event_ids).intersection(
                            artifact.reveals_events
                        )
                        else "structural_hard_negative"
                    )
                ),
                "source_origin": source_origin,
                "provenance_id": provenance_id,
            }
        )
    return attach_attestation(
        row, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    ), artifacts


def _configure_probe_role_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    for role, environment_name in ROLE_KEY_ENVS.items():
        monkeypatch.setenv(environment_name, KEY.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"test-{role}-v1")
    monkeypatch.setenv("LONGWORLD_PUBLIC_POLICY_SHA256", "d" * 64)
    monkeypatch.setenv("LONGWORLD_GH_BINARY_SHA256", "e" * 64)


def _source_workflow_bundle(
    tmp_path: Path,
) -> tuple[Path, LoadedSourceWorkflowBundle]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from build_source_workflow_bundle import build_source_workflow_bundle

    filings = []
    for index, form in enumerate(("10-K", "10-K/A"), start=1):
        accession = f"0000000001-26-{index:06d}"
        filing_date = f"2026-02-{19 + index:02d}"
        revenue = f"USD {40 + index * 2} million"
        text = "\n".join(
            (
                f"ACCESSION NUMBER: {accession}",
                f"CONFORMED SUBMISSION TYPE: {form}",
                f"FILED AS OF DATE: {filing_date.replace('-', '')}",
                "CONFORMED PERIOD OF REPORT: 20251231",
                f"Revenue after audit adjustment: {revenue}",
            )
        )
        source_path = tmp_path / f"filing-{index}.txt"
        source_path.write_text(text, encoding="utf-8")
        source_sha256 = hashlib.sha256(text.encode()).hexdigest()
        facts = []
        for fact_id, field, value in (
            ("accession", "accession", accession),
            ("form", "form", form),
            ("filing_date", "filing_date", filing_date.replace("-", "")),
            ("report_date", "report_date", "20251231"),
            ("revenue", "revenue", revenue),
        ):
            quote = next(line for line in text.splitlines() if value in line)
            facts.append(
                {
                    "fact_id": fact_id,
                    "field": field,
                    "value": value,
                    "evidence_quote": quote,
                    "evidence_char_start": text.index(quote),
                }
            )
        filings.append(
            {
                "accession": accession,
                "cik": "0000000001",
                "form": form,
                "filing_date": filing_date,
                "report_date": "2025-12-31",
                "source_url": (
                    "https://www.sec.gov/Archives/edgar/data/1/"
                    f"{accession.replace('-', '')}/{accession}.txt"
                ),
                "source_file": source_path.name,
                "source_sha256": source_sha256,
                "retrieved_at": "2026-08-24T09:00:00Z",
                "access_policy": "authorized read-only SEC filing export",
                "parser": {"name": "sec_fixture_text", "version": "1"},
                "derived_facts": facts,
            }
        )
    manifest = build_sec_filing_manifest(
        {
            "schema_version": "longworld.sec-filing-input.v1",
            "source_status": "authorized_download",
            "authorization": {
                "record_id": "TEST-SEC-AUTHORIZED-001",
                "scope": "read-only source replay test",
                "basis": "unit-test fixture only",
                "reviewed_at": "2026-08-24T10:00:00Z",
            },
            "filings": filings,
        },
        tmp_path,
        generated_at="2026-08-24T11:00:00Z",
    )
    manifest_path = tmp_path / "sec-manifest.json"
    manifest_path.write_text(
        json.dumps(attach_attestation(manifest, KEY, purpose="source_manifest")),
        encoding="utf-8",
    )
    bundle_path = tmp_path / "source-bundle.json"
    loaded = build_source_workflow_bundle(
        [("sec_filing", manifest_path)],
        bundle_path,
        attestation_key=KEY,
    )
    return bundle_path, loaded


def _source_candidate(loaded: LoadedSourceWorkflowBundle) -> tuple[dict, list]:
    materialized = materialize(
        91,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        n_workstreams=0,
        source_workflows=list(loaded.workflows),
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "program_join"
        and query.query_id.endswith("join:multi_hop+version_diff")
    )
    index = {
        artifact.artifact_id: artifact for artifact in materialized.artifacts["focal"]
    }
    essential = [index[artifact_id] for artifact_id in spec.essential_artifact_ids]
    strict = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if set(artifact.reveals_events).intersection(spec.sufficient_event_ids)
        and artifact.artifact_id not in spec.essential_artifact_ids
    ]
    proof_ids = {artifact.artifact_id for artifact in [*essential, *strict]}
    distractors = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id not in proof_ids
        and artifact_classification(artifact).workflow_kind.value != "background_only"
    ][:3]
    artifacts = distractors + essential + strict
    gates = {
        field: True
        for field in Verification.model_fields
        if field not in {"production_mode", "candidate_mode"}
    }
    gates["embedding_topk_insufficient"] = False
    document_context = join_artifacts(artifacts)
    context = wrap_prompt(spec.question, document_context, "first")
    metrics = compute_view_metrics(
        artifacts,
        set(spec.essential_artifact_ids),
        query_timing="first",
        context=context,
    )
    row = {
        "world_id": world.world_id,
        "seed": world.seed,
        "schema_version": "p3.0",
        "data_product": "worldlong_valid_v1",
        "query_id": (
            f"{spec.query_id}:first:{metrics.position_bucket}:{metrics.length_bucket}"
        ),
        "query_type": spec.query_type,
        "query_timing": "first",
        "position_bucket": metrics.position_bucket,
        "length_bucket": metrics.length_bucket,
        "question": spec.question,
        "answer": spec.answer,
        "cf_answer": spec.cf_answer,
        "view": "full",
        "context": context,
        "document_context": document_context,
        "essential_artifact_ids": list(spec.essential_artifact_ids),
        "verification": Verification(
            production_mode=False, candidate_mode=True, **gates
        ).model_dump(),
        "view_verification": {
            "production_eligible": True,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": spec.answer,
            "strict_replay_answer": spec.answer,
        },
        "artifact_classification": [],
        "workflow_ids": [world.world_id],
        "training_objective": "sft",
        "composition_method": "same_case_dossier",
        "domain": "company",
        "motif": spec.motif,
        "data_stage": "candidate",
        "n_workstreams": 0,
        "base_task_id": hashlib.sha256(
            f"{world.world_id}|{spec.base_task_group or spec.query_id}".encode()
        ).hexdigest()[:20],
        "dossier_id": hashlib.sha256(
            json.dumps(
                {
                    "world_id": world.world_id,
                    "query_id": spec.query_id,
                    "query_timing": "first",
                    "artifact_ids": sorted(
                        artifact.artifact_id for artifact in artifacts
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:20],
        "source_workflow_bundle": {
            "schema_version": "longworld.source-workflow-bundle.v1",
            "adapter_revision": loaded.adapter_revision,
            "sha256": loaded.bundle_sha256,
            "binding_digest": loaded.binding_digest,
        },
    }
    for artifact in artifacts:
        classification = artifact_classification(artifact)
        source_origin = classification.source_origin.value
        workflow_kind = classification.workflow_kind.value
        workflow_id = classification.workflow_id
        provenance_id = classification.provenance_id
        if not workflow_id or workflow_kind == "unclassified":
            source_origin = "synthetic_world"
            workflow_kind = "synthetic_executable"
            workflow_id = world.world_id
            provenance_id = (
                "synthetic-sha256:" + hashlib.sha256(artifact.text.encode()).hexdigest()
            )
        row["artifact_classification"].append(
            {
                "artifact_id": artifact.artifact_id,
                "workflow_id": workflow_id,
                "workflow_kind": workflow_kind,
                "evidence_role": (
                    "causal_gold"
                    if artifact.artifact_id in spec.essential_artifact_ids
                    else (
                        "causal_supporting"
                        if set(spec.sufficient_event_ids).intersection(
                            artifact.reveals_events
                        )
                        else "structural_hard_negative"
                    )
                ),
                "source_origin": source_origin,
                "provenance_id": provenance_id,
            }
        )
    return attach_attestation(
        row, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    ), artifacts


def test_two_stage_dense_audit_strictly_replays_and_signs_train_ready_row() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)

    promoted = promote_candidate(candidate, audit, KEY)

    assert promoted["data_stage"] == "train_ready"
    assert promoted["verification"]["production_mode"] is True
    assert promoted["verification"]["candidate_mode"] is False
    assert promoted["verification"]["embedding_topk_insufficient"] is True
    assert promoted["promotion"]["candidate_sha256"] == candidate_sha256(candidate)
    assert promoted["promotion"]["dense_model_revision"] == (
        "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    )
    assert promoted["promotion"]["dense_model_backend"] == (
        "sentence-transformers-6.0.0"
    )
    assert promoted["promotion"]["dense_score_metric"] == "dot_product"
    assert promoted["promotion"]["dense_chunking"] == {
        "strategy": "tokenizer_token_windows",
        "max_tokens": 192,
        "overlap_tokens": 32,
        "aggregation": "max_similarity",
    }
    assert sft_row_errors(promoted, attestation_key=KEY) == []

    wrong_backend = {
        **promoted,
        "promotion": {
            **promoted["promotion"],
            "dense_model_backend": "unapproved-backend",
        },
    }
    wrong_backend = attach_attestation(wrong_backend, KEY, purpose="sft_row")
    assert "missing_or_invalid_promotion" in sft_row_errors(
        wrong_backend, attestation_key=KEY
    )


def test_combined_probe_promotion_exposes_content_and_trust_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_probe_role_keys(monkeypatch)
    monkeypatch.setenv(
        LOCAL_PROBE_COMBINED_ROLES_ENV, LOCAL_PROBE_TRUST_ISOLATION_VALUE
    )
    _materialize_synthetic_replay.cache_clear()
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)

    promoted = promote_candidate(candidate, audit, KEY)
    candidate_report = attach_attestation(
        {
            "schema_version": promoted["schema_version"],
            "data_product": promoted["data_product"],
            "data_stage": "candidate",
            "release_profile_id": "p3-probe-12-v1",
            "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            "n_worlds": 1,
            "n_rows": 1,
            "target_promoted_worlds": 1,
            "candidate_row_set_sha256": promoted_row_set_sha256([candidate]),
            "retention": 1.0,
            "n_clones": 0,
        },
        KEY,
        purpose="quality_report",
    )
    report = create_train_ready_report(
        candidate_report,
        [candidate],
        [promoted],
        KEY,
    )

    expected = {
        "trust_scope": "local_probe",
        "diagnostic_only": True,
        "trust_valid_for_production": False,
        "production_eligible": False,
    }
    assert {field: promoted.get(field) for field in expected} == expected
    assert promoted["view_verification"]["content_gate_eligible"] is True
    assert promoted["view_verification"]["production_eligible"] is False
    assert {field: report.get(field) for field in expected} == expected
    assert sft_row_errors(promoted, attestation_key=KEY) == []

    malformed = {
        **{key: value for key, value in promoted.items() if key != "attestation"},
        "trust_valid_for_production": True,
    }
    malformed = attach_attestation(malformed, KEY, purpose="sft_row")
    assert "invalid_local_probe_diagnostic_boundary" in sft_row_errors(
        malformed, attestation_key=KEY
    )
    _materialize_synthetic_replay.cache_clear()


def test_probe_promotion_cannot_be_resigned_as_legacy_production_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_probe_role_keys(monkeypatch)
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    promoted = promote_candidate(candidate, audit, KEY)
    stripped = deepcopy(promoted)
    stripped.pop("attestation")
    for field in (
        "trust_scope",
        "diagnostic_only",
        "content_gate_eligible",
        "trust_valid_for_production",
        "production_eligible",
    ):
        stripped.pop(field, None)
    stripped["view_verification"]["production_eligible"] = True

    monkeypatch.delenv(ATTESTATION_ENVIRONMENT_ENV)
    resigned = attach_attestation(stripped, KEY, purpose="sft_row")

    assert resigned["attestation"]["scheme"] == "hmac-sha256"
    assert "invalid_or_missing_attestation" in sft_row_errors(
        resigned, attestation_key=KEY
    )


def test_strict_exact_sft_contract_requires_matching_tokenizer_asset_digests() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    promoted = promote_candidate(candidate, audit, KEY)
    exact = {
        **{key: value for key, value in promoted.items() if key != "attestation"},
        "length_bucket": "16k",
        "tokenizer_context_tokens": 16_001,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "tokenizer_asset_manifest_sha256": "b" * 64,
        "promotion": {
            **promoted["promotion"],
            "tokenizer_asset_manifest_sha256": "b" * 64,
        },
    }
    exact = attach_attestation(exact, KEY, purpose="sft_row")

    assert sft_row_errors(exact, attestation_key=KEY) == []
    missing = attach_attestation(
        {
            **{key: value for key, value in exact.items() if key != "attestation"},
            "promotion": {
                key: value
                for key, value in exact["promotion"].items()
                if key != "tokenizer_asset_manifest_sha256"
            },
        },
        KEY,
        purpose="sft_row",
    )
    mismatched = attach_attestation(
        {
            **{key: value for key, value in exact.items() if key != "attestation"},
            "promotion": {
                **exact["promotion"],
                "tokenizer_asset_manifest_sha256": "c" * 64,
            },
        },
        KEY,
        purpose="sft_row",
    )

    assert "missing_or_invalid_promotion" in sft_row_errors(
        missing, attestation_key=KEY
    )
    assert "missing_or_invalid_promotion" in sft_row_errors(
        mismatched, attestation_key=KEY
    )


def test_legacy_exact_sft_contract_allows_token_metadata_without_asset_binding() -> (
    None
):
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    promoted = promote_candidate(candidate, audit, KEY)
    legacy_exact = {
        **{key: value for key, value in promoted.items() if key != "attestation"},
        "length_bucket": "16k",
        "tokenizer_context_tokens": 16_001,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
    }
    legacy_exact = attach_attestation(legacy_exact, KEY, purpose="sft_row")

    assert sft_row_errors(legacy_exact, attestation_key=KEY) == []


def test_128k_sft_contract_requires_complete_exact_tokenizer_binding() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    promoted = promote_candidate(candidate, audit, KEY)
    exact = {
        **{key: value for key, value in promoted.items() if key != "attestation"},
        "length_bucket": "128k",
        "tokenizer_context_tokens": 128_100,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "tokenizer_asset_manifest_sha256": "b" * 64,
        "promotion": {
            **promoted["promotion"],
            "tokenizer_asset_manifest_sha256": "b" * 64,
        },
    }
    exact = attach_attestation(exact, KEY, purpose="sft_row")
    assert sft_row_errors(exact, attestation_key=KEY) == []

    for field in (
        "tokenizer_context_tokens",
        "tokenizer_revision",
        "tokenizer_asset_manifest_sha256",
    ):
        missing = attach_attestation(
            {
                key: value
                for key, value in exact.items()
                if key not in {field, "attestation"}
            },
            KEY,
            purpose="sft_row",
        )
        assert "invalid_exact_token_binding" in sft_row_errors(
            missing, attestation_key=KEY
        )


def test_dense_audit_and_promotion_bind_replayed_tokenizer_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_digest = "b" * 64
    candidate, artifacts = _candidate()
    candidate["tokenizer_asset_manifest_sha256"] = asset_digest
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    def replay_with_asset(*args, **kwargs):
        verification, notes, tokenizer = _independent_verification_replay(
            *args, **kwargs
        )
        notes["exact_token_replay"] = {
            "tokenizer_context_tokens": 16_001,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": ("a7b0d22b993d71000cf2eadfb37222a67cee521e"),
            "tokenizer_asset_manifest_sha256": asset_digest,
        }
        return verification, notes, tokenizer

    monkeypatch.setattr(
        "longworld.core.promotion._independent_verification_replay",
        replay_with_asset,
    )
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    promoted = promote_candidate(candidate, audit, KEY)

    assert audit["tokenizer_asset_manifest_sha256"] == asset_digest
    assert promoted["tokenizer_asset_manifest_sha256"] == asset_digest
    assert promoted["promotion"]["tokenizer_asset_manifest_sha256"] == asset_digest


def test_promotion_rejects_resigned_dense_audit_tokenizer_asset_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_digest = "b" * 64
    candidate, artifacts = _candidate()
    candidate["tokenizer_asset_manifest_sha256"] = asset_digest
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    def replay_with_asset(*args, **kwargs):
        verification, notes, tokenizer = _independent_verification_replay(
            *args, **kwargs
        )
        notes["exact_token_replay"] = {
            "tokenizer_context_tokens": 16_001,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": ("a7b0d22b993d71000cf2eadfb37222a67cee521e"),
            "tokenizer_asset_manifest_sha256": asset_digest,
        }
        return verification, notes, tokenizer

    monkeypatch.setattr(
        "longworld.core.promotion._independent_verification_replay",
        replay_with_asset,
    )
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    tampered = attach_attestation(
        {
            **{key: value for key, value in audit.items() if key != "attestation"},
            "tokenizer_asset_manifest_sha256": "c" * 64,
        },
        KEY,
        purpose=DENSE_AUDIT_PURPOSE,
    )

    with pytest.raises(PromotionError, match="tokenizer asset binding mismatch"):
        promote_candidate(candidate, tampered, KEY)


def test_release_selection_rejects_tokenizer_assets_outside_profile() -> None:
    revision = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
    candidate, _artifacts = _candidate()
    candidate.update(
        {
            "length_bucket": "16k",
            "tokenizer_context_tokens": 16_001,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": "c" * 64,
        }
    )
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="do not match release profile"):
        select_release_worlds(
            [candidate],
            [],
            "p7-github-source-slice-1-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_dense_audit_rejects_unapproved_exact_tokenizer_pin() -> None:
    candidate, artifacts = _candidate()
    candidate.update(
        {
            "length_bucket": "16k",
            "tokenizer_context_tokens": 16_000,
            "tokenizer_model_id": "/tmp/mutable-tokenizer",
            "tokenizer_revision": "0" * 40,
        }
    )
    candidate["query_id"] = candidate["query_id"].rsplit(":", 1)[0] + ":16k"
    candidate = attach_attestation(
        {key: value for key, value in candidate.items() if key != "attestation"},
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )

    with pytest.raises(PromotionError, match="tokenizer pin is not approved"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


@pytest.mark.parametrize("tokens", [127_999, 131_073])
def test_candidate_to_promotion_rejects_128k_out_of_exact_band(
    monkeypatch: pytest.MonkeyPatch, tokens: int
) -> None:
    revision = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    exact_candidate = attach_attestation(
        {
            **{key: value for key, value in candidate.items() if key != "attestation"},
            "query_id": candidate["query_id"].rsplit(":", 1)[0] + ":128k",
            "length_bucket": "128k",
            "tokenizer_context_tokens": tokens,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": "b" * 64,
        },
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    rebound_audit = attach_attestation(
        {
            **{key: value for key, value in audit.items() if key != "attestation"},
            "query_id": exact_candidate["query_id"],
            "candidate_sha256": candidate_sha256(exact_candidate),
            "tokenizer_asset_manifest_sha256": "b" * 64,
        },
        KEY,
        purpose=DENSE_AUDIT_PURPOSE,
    )
    monkeypatch.setattr(
        "longworld.core.promotion._resolved_local_tokenizer_revision",
        lambda _model_id, _revision: revision,
    )
    monkeypatch.setattr(
        "longworld.core.promotion.resolved_tokenizer_asset_manifest_sha256",
        lambda _model_id, _revision: "b" * 64,
    )

    with pytest.raises(PromotionError, match=rf"exact_128k_out_of_range:{tokens}"):
        promote_candidate(exact_candidate, rebound_audit, KEY)


def test_dense_audit_recounts_exact_tokens_from_reconstructed_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a7b0d22b993d71000cf2eadfb37222a67cee521e"

    class FakeTokenizer:
        def __init__(self) -> None:
            self.init_kwargs = {"_commit_hash": revision}

        @staticmethod
        def encode(text: str, *, add_special_tokens: bool) -> list[int]:
            assert text
            assert not add_special_tokens
            return [0] * 16_001

    monkeypatch.setattr(
        "longworld.core.promotion._load_replay_tokenizer",
        lambda _model_id, _revision: FakeTokenizer(),
    )
    monkeypatch.setattr(
        "longworld.core.promotion._resolved_local_tokenizer_revision",
        lambda _model_id, _revision: revision,
    )
    monkeypatch.setattr(
        "longworld.core.promotion.resolved_tokenizer_asset_manifest_sha256",
        lambda _model_id, _revision: "b" * 64,
    )
    candidate, artifacts = _candidate()
    candidate.update(
        {
            "length_bucket": "16k",
            "tokenizer_context_tokens": 16_000,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": "b" * 64,
        }
    )
    candidate["query_id"] = candidate["query_id"].rsplit(":", 1)[0] + ":16k"
    candidate = attach_attestation(
        {key: value for key, value in candidate.items() if key != "attestation"},
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )

    with pytest.raises(PromotionError, match="token count does not replay"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_dense_audit_rejects_missing_tokenizer_asset_manifest_digest() -> None:
    revision = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
    candidate, artifacts = _candidate()
    candidate.update(
        {
            "length_bucket": "16k",
            "tokenizer_context_tokens": 16_000,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": revision,
        }
    )
    candidate["query_id"] = candidate["query_id"].rsplit(":", 1)[0] + ":16k"
    candidate = attach_attestation(
        {key: value for key, value in candidate.items() if key != "attestation"},
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )

    with pytest.raises(PromotionError, match="asset manifest digest is missing"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_dense_audit_rejects_changed_local_tokenizer_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
    monkeypatch.setattr(
        "longworld.core.promotion.resolved_tokenizer_asset_manifest_sha256",
        lambda _model_id, _revision: "c" * 64,
    )
    monkeypatch.setattr(
        "longworld.core.promotion._resolved_local_tokenizer_revision",
        lambda _model_id, _revision: revision,
    )
    candidate, artifacts = _candidate()
    candidate.update(
        {
            "length_bucket": "16k",
            "tokenizer_context_tokens": 16_000,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": "b" * 64,
        }
    )
    candidate["query_id"] = candidate["query_id"].rsplit(":", 1)[0] + ":16k"
    candidate = attach_attestation(
        {key: value for key, value in candidate.items() if key != "attestation"},
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )

    with pytest.raises(PromotionError, match="asset manifest does not match"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_dense_audit_rejects_tokenizer_assets_mutated_during_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a7b0d22b993d71000cf2eadfb37222a67cee521e"

    class FakeTokenizer:
        @staticmethod
        def encode(text: str, *, add_special_tokens: bool) -> list[int]:
            assert text
            assert not add_special_tokens
            return [0] * 16_001

    asset_digests = iter(("b" * 64, "c" * 64))
    monkeypatch.setattr(
        "longworld.core.promotion.resolved_tokenizer_asset_manifest_sha256",
        lambda _model_id, _revision: next(asset_digests),
    )
    monkeypatch.setattr(
        "longworld.core.promotion._resolved_local_tokenizer_revision",
        lambda _model_id, _revision: revision,
    )
    monkeypatch.setattr(
        "longworld.core.promotion._load_replay_tokenizer",
        lambda _model_id, _revision: FakeTokenizer(),
    )
    candidate, artifacts = _candidate()
    candidate.update(
        {
            "length_bucket": "16k",
            "tokenizer_context_tokens": 16_001,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": "b" * 64,
        }
    )
    candidate["query_id"] = candidate["query_id"].rsplit(":", 1)[0] + ":16k"
    candidate = attach_attestation(
        {key: value for key, value in candidate.items() if key != "attestation"},
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )

    with pytest.raises(PromotionError, match="assets changed while loading"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_resolved_local_tokenizer_revision_uses_cache_snapshot_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a7b0d22b993d71000cf2eadfb37222a67cee521e"

    monkeypatch.setattr(
        "transformers.utils.hub.cached_file",
        lambda *_args, **_kwargs: (
            f"/cache/models--Qwen--Qwen3.5-4B/snapshots/{revision}/"
            "tokenizer_config.json"
        ),
    )

    assert _resolved_local_tokenizer_revision("Qwen/Qwen3.5-4B", revision) == revision


def test_promotion_recomputes_quality_metrics_from_replayed_serialized_view() -> None:
    candidate, artifacts = _candidate()
    candidate.update(
        {
            "boilerplate_token_ratio": 0.999,
            "pulse_doc_ratio": 0.999,
            "near_dup_sentence_ratio": 0.999,
            "actual_context_tokens": 1,
            "natural_tokens": 1,
            "n_unique_docs": 999,
            "n_clones": 999,
            "semantic_tokens": {
                "event_bearing": 999999,
                "internal": 999999,
                "generic_background": 999999,
            },
            "difficulty": {"context_tokens": 1, "max_evidence_distance": 0},
        }
    )
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)

    promoted = promote_candidate(candidate, audit, KEY)

    metrics = compute_view_metrics(
        artifacts,
        set(candidate["essential_artifact_ids"]),
        query_timing=candidate["query_timing"],
        context=candidate["context"],
    )
    document_context = join_artifacts(artifacts)
    assert promoted["document_context"] == document_context
    assert promoted["boilerplate_token_ratio"] == round(
        boilerplate_char_fraction(document_context), 4
    )
    assert promoted["pulse_doc_ratio"] == round(pulse_doc_ratio(artifacts), 4)
    assert promoted["near_dup_sentence_ratio"] == round(
        sentence_near_dup_ratio(artifacts), 4
    )
    assert promoted["actual_context_tokens"] == metrics.context_tokens
    assert promoted["natural_tokens"] == metrics.context_tokens
    assert promoted["difficulty"]["context_tokens"] == metrics.context_tokens
    assert promoted["n_unique_docs"] == len(artifacts)
    assert promoted["n_clones"] == 0
    assert sum(
        promoted["semantic_tokens"][key] for key in ("internal", "generic_background")
    ) == sum(estimate_tokens(artifact.text) for artifact in artifacts)
    assert (
        promoted["semantic_tokens"]["event_bearing"]
        <= promoted["semantic_tokens"]["internal"]
    )
    assert promoted["semantic_tokens"] != candidate["semantic_tokens"]
    expected_graph = promoted["graph"]
    assert promoted["difficulty"]["proof_depth"] == expected_graph["proof_depth"]
    assert promoted["difficulty"]["state_updates"] > 0
    assert promoted["hop_count"] == expected_graph["hop_count"]
    assert promoted["searchart_width"] == len(promoted["essential_artifact_ids"])
    assert promoted["program_ops"]


def test_promotion_uses_replayed_graph_not_the_declared_spec_depth() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)

    promoted = promote_candidate(candidate, audit, KEY)
    materialized = materialize(
        candidate["seed"],
        n_parallel=0,
        n_pulses=0,
        domain=candidate["domain"],
        n_workstreams=0,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if candidate["query_id"].startswith(f"{query.query_id}:")
    )
    replayed = graph_stats(world, spec)

    assert spec.proof_depth != replayed["proof_depth"]
    assert promoted["graph"] == replayed
    assert promoted["difficulty"]["proof_depth"] == replayed["proof_depth"]
    assert promoted["hop_count"] == replayed["hop_count"]


def test_replayed_quality_keeps_exact_requested_bucket() -> None:
    candidate, artifacts = _candidate()
    candidate["length_bucket"] = "64k"
    candidate["position_bucket"] = "back"
    materialized = materialize(
        candidate["seed"],
        n_parallel=0,
        n_pulses=0,
        domain=candidate["domain"],
        n_workstreams=0,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if candidate["query_id"].startswith(f"{query.query_id}:")
    )

    replayed = _replayed_quality_metrics(
        candidate,
        world,
        spec,
        artifacts,
        {
            "exact_token_replay": {
                "tokenizer_context_tokens": 64_100,
                "tokenizer_model_id": "Qwen/Qwen3.5-4B",
                "tokenizer_revision": "a" * 40,
            }
        },
        token_counter=lambda text: 64_100 if text else 0,
    )

    assert replayed["length_bucket"] == "64k"
    assert replayed["tokenizer_context_tokens"] == 64_100


def test_replayed_exact_metrics_use_tokenizer_for_position_and_distance() -> None:
    candidate, artifacts = _candidate()
    materialized = materialize(
        candidate["seed"],
        n_parallel=0,
        n_pulses=0,
        domain=candidate["domain"],
        n_workstreams=0,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if candidate["query_id"].startswith(f"{query.query_id}:")
    )
    heavy_suffix = artifacts.pop(0)
    heavy_suffix.text = "重" * 500
    artifacts.append(heavy_suffix)
    document_context = join_artifacts(artifacts)
    prompt = wrap_prompt(spec.question, document_context, "first")
    total_weight = sum(40 if char == "重" else 1 for char in prompt)

    def skewed_token_counter(text: str) -> int:
        weight = sum(40 if char == "重" else 1 for char in text)
        return (weight * 64_100) // total_weight

    exact_metrics = compute_view_metrics(
        artifacts,
        set(spec.essential_artifact_ids),
        query_timing="first",
        context=prompt,
        token_counter=skewed_token_counter,
        token_prefix=prompt_document_prefix(spec.question, "first"),
        query_boundary_tokens=prompt_query_boundary(
            spec.question, document_context, "first", skewed_token_counter
        ),
    )
    approximate_metrics = compute_view_metrics(
        artifacts,
        set(spec.essential_artifact_ids),
        query_timing="first",
        context=prompt,
    )
    assert exact_metrics.position_bucket != approximate_metrics.position_bucket

    candidate["length_bucket"] = "64k"
    candidate["position_bucket"] = exact_metrics.position_bucket
    replayed = _replayed_quality_metrics(
        candidate,
        world,
        spec,
        artifacts,
        {
            "exact_token_replay": {
                "tokenizer_context_tokens": 64_100,
                "tokenizer_model_id": "Qwen/Qwen3.5-4B",
                "tokenizer_revision": "a" * 40,
            }
        },
        token_counter=skewed_token_counter,
    )

    assert replayed["actual_context_tokens"] == 64_100
    assert replayed["position_bucket"] == exact_metrics.position_bucket
    assert replayed["evidence_distance"] == exact_metrics.max_evidence_distance


def test_replayed_real_source_ratio_uses_prompt_marginal_not_artifact_sum() -> None:
    candidate, artifacts = _candidate()
    materialized = materialize(
        candidate["seed"],
        n_parallel=0,
        n_pulses=0,
        domain=candidate["domain"],
        n_workstreams=0,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if candidate["query_id"].startswith(f"{query.query_id}:")
    )
    for artifact in artifacts:
        classify_artifact(
            artifact,
            source_origin=SourceOrigin.REAL_PUBLIC,
            workflow_kind=WorkflowKind.HYBRID_CAUSAL,
            evidence_role=EvidenceRole.CAUSAL_GOLD,
            workflow_id=world.world_id,
            provenance_id=f"test:{artifact.artifact_id}",
        )

    def boundary_heavy_token_counter(text: str) -> int:
        return 60_100 + len(text) if text else 0

    document_context = join_artifacts(artifacts)
    prompt = wrap_prompt(spec.question, document_context, "first")
    metrics = compute_view_metrics(
        artifacts,
        set(spec.essential_artifact_ids),
        query_timing="first",
        context=prompt,
        token_counter=boundary_heavy_token_counter,
        token_prefix=prompt_document_prefix(spec.question, "first"),
        query_boundary_tokens=prompt_query_boundary(
            spec.question,
            document_context,
            "first",
            boundary_heavy_token_counter,
        ),
    )
    candidate["length_bucket"] = "64k"
    candidate["position_bucket"] = metrics.position_bucket
    total_tokens = boundary_heavy_token_counter(prompt)

    replayed = _replayed_quality_metrics(
        candidate,
        world,
        spec,
        artifacts,
        {
            "exact_token_replay": {
                "tokenizer_context_tokens": total_tokens,
                "tokenizer_model_id": "Qwen/Qwen3.5-4B",
                "tokenizer_revision": "a" * 40,
            }
        },
        token_counter=boundary_heavy_token_counter,
    )

    empty_prompt_tokens = boundary_heavy_token_counter(
        wrap_prompt(spec.question, "", "first")
    )
    assert replayed["real_source_token_ratio"] == round(
        (total_tokens - empty_prompt_tokens) / total_tokens, 4
    )
    assert 0.0 < replayed["real_source_token_ratio"] < 1.0


def test_promotion_rejects_a_resigned_incorrect_audited_dup_ratio() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    tampered = attach_attestation(
        {
            **{key: value for key, value in audit.items() if key != "attestation"},
            "near_dup_sentence_ratio": min(
                1.0, float(audit["near_dup_sentence_ratio"]) + 0.1
            ),
        },
        KEY,
        purpose=DENSE_AUDIT_PURPOSE,
    )

    with pytest.raises(PromotionError, match="near-duplicate replay binding mismatch"):
        promote_candidate(candidate, tampered, KEY)


def test_promotion_rejects_resigned_strict_growth_metric_tamper() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    payload = {key: value for key, value in audit.items() if key != "attestation"}
    metrics = dict(payload["strict_growth_metrics"])
    semantic = dict(metrics["semantic_tokens"])
    semantic["event_bearing"] += 1
    metrics["semantic_tokens"] = semantic
    payload["strict_growth_metrics"] = metrics
    tampered = attach_attestation(payload, KEY, purpose=DENSE_AUDIT_PURPOSE)

    with pytest.raises(PromotionError, match="strict-growth replay binding mismatch"):
        promote_candidate(candidate, tampered, KEY)


def test_sft_contract_rejects_legacy_promotion_schema() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    promoted = promote_candidate(candidate, audit, KEY)
    payload = {key: value for key, value in promoted.items() if key != "attestation"}
    payload["promotion"] = {
        **dict(payload["promotion"]),
        "schema_version": "train-ready-promotion-v1",
    }
    legacy = attach_attestation(payload, KEY, purpose="sft_row")

    assert "missing_or_invalid_promotion" in sft_row_errors(legacy, attestation_key=KEY)


def test_dense_audit_attests_replayed_strict_growth_metrics() -> None:
    candidate, artifacts = _candidate()
    payload = {key: value for key, value in candidate.items() if key != "attestation"}
    payload["semantic_tokens"] = {
        "event_bearing": 999_999,
        "internal": 999_999,
        "generic_background": 0,
    }
    candidate = attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)

    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)

    replayed = audit["strict_growth_metrics"]
    assert replayed["semantic_tokens"] != payload["semantic_tokens"]
    assert replayed["semantic_tokens"]["internal"] > 0
    assert replayed["strict_support_event_count"] > 0
    assert replayed["graph"]["proof_depth"] > 0


def test_promotion_digest_binds_complete_auditor_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    promoted = promote_candidate(candidate, audit, KEY)
    replacement_auditor_key = b"replacement-auditor-key-at-least-32-bytes"
    monkeypatch.setenv(ROLE_KEY_ENVS["auditor"], replacement_auditor_key.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["auditor"], "test-auditor-replacement-v1")
    resigned_audit = attach_attestation(
        audit, replacement_auditor_key, purpose=DENSE_AUDIT_PURPOSE
    )

    resigned = promote_candidate(
        candidate,
        resigned_audit,
        candidate_attestation_key=KEY,
        audit_attestation_key=replacement_auditor_key,
        promotion_attestation_key=KEY,
    )

    assert promoted["promotion"]["dense_audit_sha256"] == serialized_row_sha256(audit)
    assert resigned["promotion"]["dense_audit_sha256"] == serialized_row_sha256(
        resigned_audit
    )
    assert (
        resigned["promotion"]["dense_audit_sha256"]
        != promoted["promotion"]["dense_audit_sha256"]
    )


def test_train_ready_report_binds_the_exact_promoted_row_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    promoted = promote_candidate(candidate, audit, KEY)
    candidate_report = attach_attestation(
        {
            "schema_version": "p3.0",
            "data_product": "worldlong_valid_v1",
            "data_stage": "candidate",
            "release_profile_id": "p3-probe-12-v1",
            "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            "n_worlds": 1,
            "n_rows": 1,
            "candidate_row_set_sha256": promoted_row_set_sha256([candidate]),
            "target_promoted_worlds": 1,
            "retention": 0.5,
            "n_clones": 0,
        },
        KEY,
        purpose="quality_report",
    )

    report = create_train_ready_report(candidate_report, [candidate], [promoted], KEY)

    assert verify_attestation(report, KEY, purpose="quality_report")
    assert report["data_stage"] == "train_ready"
    assert report["release_profile_id"] == "p3-probe-12-v1"
    assert report["tokenizer_model_id"] == "Qwen/Qwen3.5-4B"
    assert len(report["tokenizer_revision"]) == 40
    assert report["tokenizer_asset_manifest_sha256"] is None
    assert report["report_binding_revision"] == QUALITY_REPORT_BINDING_REVISION
    assert report["candidate_row_set_sha256"] == promoted_row_set_sha256([candidate])
    assert report["n_rows"] == 1
    assert report["n_worlds"] == 1
    assert report["promoted_row_set_sha256"] == promoted_row_set_sha256([promoted])
    changed = {**promoted, "answer": "tampered"}
    assert report["promoted_row_set_sha256"] != promoted_row_set_sha256([changed])

    monkeypatch.setattr(
        promotion_module,
        "_cumulative_history_violations_by_world",
        lambda _rows, _profile: {"world-focal": ("flat_strict_growth",)},
    )
    with pytest.raises(PromotionError, match="strict cumulative source history"):
        create_train_ready_report(candidate_report, [candidate], [promoted], KEY)

    other_candidate, _ = _candidate("cf")
    other_report = attach_attestation(
        {
            **{
                key: value
                for key, value in candidate_report.items()
                if key != "attestation"
            },
            "candidate_row_set_sha256": promoted_row_set_sha256([other_candidate]),
        },
        KEY,
        purpose="quality_report",
    )
    with pytest.raises(PromotionError, match="do not belong"):
        create_train_ready_report(other_report, [other_candidate], [promoted], KEY)


def test_train_ready_report_rejects_asymmetric_counterfactual_filtering() -> None:
    factual, factual_artifacts = _candidate("full")
    counterfactual, _ = _candidate("cf")
    counterfactual["query_id"] += ":cf-metrics-drift"
    counterfactual = attach_attestation(
        counterfactual, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    factual_audit = create_dense_audit(
        factual, _ranking(factual, factual_artifacts), KEY, k=3
    )
    promoted_factual = promote_candidate(factual, factual_audit, KEY)
    candidates = [factual, counterfactual]
    candidate_report = attach_attestation(
        {
            "schema_version": "p3.0",
            "data_product": "worldlong_valid_v1",
            "data_stage": "candidate",
            "release_profile_id": "p3-probe-12-v1",
            "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            "n_worlds": 1,
            "n_rows": 2,
            "candidate_row_set_sha256": promoted_row_set_sha256(candidates),
            "target_promoted_worlds": 1,
            "retention": 0.5,
            "n_clones": 0,
        },
        KEY,
        purpose="quality_report",
    )

    with pytest.raises(PromotionError, match="asymmetric"):
        create_train_ready_report(candidate_report, candidates, [promoted_factual], KEY)


def _mark_candidate_as_real(candidate: dict) -> None:
    candidate["source_family_ids"] = ["github.com/example/repo"]
    source_relation = {
        "parent_record_id": "issue-1",
        "child_record_id": "commit-1",
        "relation_provenance": "authentic_source",
    }
    candidate["source_relation_edges"] = [source_relation]
    candidate["authentic_source_relation_edges"] = [source_relation]
    candidate["episode_replay_bundle"] = {
        "schema_version": "longworld.episode-replay-bundle.v1",
        "sha256": "a" * 64,
        "composition": "chronological_causal_union",
    }
    candidate["artifact_classification"][0] = {
        **candidate["artifact_classification"][0],
        "workflow_kind": "hybrid_causal",
        "source_origin": "real_public",
        "evidence_role": "causal_supporting",
    }


def test_real_workflow_selection_does_not_require_a_cross_source_relation() -> None:
    candidate, _ = _candidate()
    _mark_candidate_as_real(candidate)
    candidate["source_relation_edges"] = []
    candidate["authentic_source_relation_edges"] = []

    assert _candidate_has_verified_real_source(candidate)


def _selection_audit(
    candidate: dict, *, near_dup_sentence_ratio: float | None = None
) -> dict:
    if near_dup_sentence_ratio is None:
        near_dup_sentence_ratio = float(candidate.get("near_dup_sentence_ratio") or 0.0)
    payload = {
        "schema_version": PROMOTION_SCHEMA,
        "query_id": candidate["query_id"],
        "candidate_sha256": candidate_sha256(candidate),
        "ranking_sha256": "b" * 64,
        "ranker_type": "dense_embedding",
        "model": {
            "provider": "huggingface",
            "model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
            "backend": "sentence-transformers-6.0.0",
            "score_metric": "dot_product",
            "chunking": {
                "strategy": "tokenizer_token_windows",
                "max_tokens": 192,
                "overlap_tokens": 32,
                "aggregation": "max_similarity",
            },
        },
        "k": 3,
        "top_k": [{"rank": rank} for rank in range(1, 4)],
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "expected_answer": candidate["answer"],
        "embedding_topk_insufficient": True,
        "verification_replay_sha256": "c" * 64,
        "near_dup_sentence_ratio": near_dup_sentence_ratio,
        "strict_growth_metrics": {
            "semantic_growth_group_id": candidate.get("semantic_growth_group_id", ""),
            "semantic_tokens": {
                "event_bearing": 0,
                "internal": 0,
                **dict(candidate.get("semantic_tokens") or {}),
            },
            "strict_support_event_count": candidate.get(
                "strict_support_event_count", 0
            ),
            "graph": {
                "n_essential_events": (candidate.get("graph") or {}).get(
                    "n_essential_events", 0
                ),
                "proof_depth": (candidate.get("graph") or {}).get("proof_depth", 0),
            },
            "authentic_source_relation_edges": list(
                candidate.get("authentic_source_relation_edges") or []
            ),
        },
    }
    asset_digest = candidate.get("tokenizer_asset_manifest_sha256")
    if asset_digest:
        payload["tokenizer_asset_manifest_sha256"] = asset_digest
    return attach_attestation(payload, KEY, purpose=DENSE_AUDIT_PURPOSE)


def _p13_six_domain_selection_inputs() -> tuple[list[dict], list[dict]]:
    template, _ = _candidate()
    profile = release_profile("p13-authentic-six-domain-probe-12-v1")
    tokens_by_band = {"16k": 16_000, "32k": 32_000, "64k": 64_000}
    domains = tuple(dict(profile.promoted_domain_world_quotas))
    candidates = []
    audits = []
    for domain in domains:
        for world_index in range(2):
            world_id = f"p13-{domain}-{world_index}"
            for level, band in enumerate(("16k", "32k", "64k"), start=1):
                relations = [
                    {
                        "parent_record_id": f"{world_id}-revision-{index}",
                        "child_record_id": f"{world_id}-revision-{index + 1}",
                        "relation_provenance": "authentic_source",
                    }
                    for index in range(level)
                ]
                for view in ("full", "cf", "ordered_artifact_view"):
                    candidate = {
                        **deepcopy(
                            {
                                key: value
                                for key, value in template.items()
                                if key != "attestation"
                            }
                        ),
                        "world_id": world_id,
                        "query_id": f"{world_id}-{band}-{view}",
                        "dossier_id": f"{world_id}-{band}",
                        "domain": domain,
                        "view": view,
                        "query_timing": "first",
                        "length_bucket": band,
                        "tokenizer_context_tokens": tokens_by_band[band],
                        "tokenizer_model_id": profile.tokenizer_model_id,
                        "tokenizer_revision": profile.tokenizer_revision,
                        "tokenizer_asset_manifest_sha256": (
                            profile.tokenizer_asset_manifest_sha256
                        ),
                        "semantic_growth_group_id": f"{world_id}-history",
                        "semantic_tokens": {
                            "event_bearing": 8_000 * level,
                            "internal": 10_000 * level,
                            "generic_background": 0,
                        },
                        "strict_support_event_count": 3 * level,
                        "source_relation_edges": relations,
                        "authentic_source_relation_edges": relations,
                        "graph": {
                            **dict(template.get("graph") or {}),
                            "n_essential_events": 2 * level,
                            "proof_depth": level + 1,
                        },
                    }
                    _mark_candidate_as_real(candidate)
                    candidate["source_family_ids"] = [f"source/{domain}/{world_index}"]
                    candidate["source_relation_edges"] = relations
                    candidate["authentic_source_relation_edges"] = relations
                    candidate = attach_attestation(
                        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
                    )
                    candidates.append(candidate)
                    audits.append(_selection_audit(candidate))
    return candidates, audits


def test_required_view_coverage_uses_row_contract_late_timing_vocabulary() -> None:
    profile = replace(
        release_profile("p13-authentic-six-domain-probe-12-v1"),
        required_exact_length_buckets=("16k",),
        required_view_timings=(("ordered_artifact_view", "late"),),
    )
    row = {
        "world_id": "late-timing-world",
        "length_bucket": "16k",
        "tokenizer_context_tokens": 16_000,
        "tokenizer_model_id": profile.tokenizer_model_id,
        "tokenizer_revision": profile.tokenizer_revision,
        "tokenizer_asset_manifest_sha256": profile.tokenizer_asset_manifest_sha256,
        "view": "ordered_artifact_view",
        "query_timing": "late",
    }

    assert _missing_required_view_coverage_by_world([row], profile) == {}
    with pytest.raises(PromotionError, match="required view coverage is invalid"):
        _missing_required_view_coverage_by_world(
            [row], replace(profile, required_view_timings=(("full", "later"),))
        )


def test_p13_selection_requires_every_view_in_every_exact_bucket() -> None:
    candidates, audits = _p13_six_domain_selection_inputs()
    removed = next(
        candidate
        for candidate in candidates
        if candidate["world_id"] == "p13-finance-0"
        and candidate["length_bucket"] == "32k"
        and candidate["view"] == "cf"
    )
    removed_digest = candidate_sha256(removed)

    with pytest.raises(PromotionError, match="required view coverage"):
        select_release_worlds(
            [candidate for candidate in candidates if candidate is not removed],
            [audit for audit in audits if audit["candidate_sha256"] != removed_digest],
            "p13-authentic-six-domain-probe-12-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_p13_selection_requires_every_selected_row_to_be_source_bound() -> None:
    candidates, audits = _p13_six_domain_selection_inputs()
    index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate["world_id"] == "p13-cyber-0"
        and candidate["length_bucket"] == "16k"
        and candidate["view"] == "ordered_artifact_view"
    )
    payload = {
        key: value for key, value in candidates[index].items() if key != "attestation"
    }
    payload.pop("episode_replay_bundle")
    payload["source_family_ids"] = []
    payload["artifact_classification"] = [
        {
            **item,
            "workflow_kind": "synthetic_executable",
            "source_origin": "synthetic_world",
        }
        for item in payload["artifact_classification"]
    ]
    candidates[index] = attach_attestation(
        payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    audits[index] = _selection_audit(candidates[index])

    with pytest.raises(PromotionError, match="all rows to be source-bound"):
        select_release_worlds(
            candidates,
            audits,
            "p13-authentic-six-domain-probe-12-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_p13_selection_signs_domain_stratified_world_maps() -> None:
    candidates, audits = _p13_six_domain_selection_inputs()

    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p13-authentic-six-domain-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    assert len({row["world_id"] for row in selected}) == 12
    assert receipt["worlds_by_domain"] == receipt["selected_worlds_by_domain"]
    assert {
        domain: len(world_ids)
        for domain, world_ids in receipt["worlds_by_domain"].items()
    } == dict(
        release_profile(
            "p13-authentic-six-domain-probe-12-v1"
        ).promoted_domain_world_quotas
    )
    split_worlds = receipt["split_worlds_by_domain"]
    assert sum(map(len, split_worlds["eval"].values())) == 2
    assert (
        len([domain for domain, world_ids in split_worlds["eval"].items() if world_ids])
        == 2
    )
    assert all(len(world_ids) >= 1 for world_ids in split_worlds["train"].values())
    assert {
        world_id: split
        for split, domain_worlds in split_worlds.items()
        for world_ids in domain_worlds.values()
        for world_id in world_ids
    } == receipt["split_by_world"]


def test_p13_train_ready_report_rechecks_and_signs_domain_view_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates, audits = _p13_six_domain_selection_inputs()
    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p13-authentic-six-domain-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )
    selection_digest = serialized_row_sha256(receipt)
    rows = [
        {
            **candidate,
            "data_stage": "train_ready",
            "split": receipt["split_by_world"][candidate["world_id"]],
            "promotion": {
                "candidate_sha256": candidate_sha256(candidate),
                "dense_audit_sha256": receipt["audit_sha256_by_candidate"][
                    candidate_sha256(candidate)
                ],
                "release_selection_sha256": selection_digest,
                "tokenizer_asset_manifest_sha256": candidate[
                    "tokenizer_asset_manifest_sha256"
                ],
            },
        }
        for candidate in selected
    ]
    candidate_report = attach_attestation(
        {
            "schema_version": candidates[0]["schema_version"],
            "data_product": candidates[0]["data_product"],
            "data_stage": "candidate",
            "release_profile_id": "p13-authentic-six-domain-probe-12-v1",
            "release_profile_sha256": release_profile_sha256(
                "p13-authentic-six-domain-probe-12-v1"
            ),
            "n_worlds": 12,
            "n_rows": len(candidates),
            "target_promoted_worlds": 12,
            "candidate_row_set_sha256": promoted_row_set_sha256(candidates),
            "retention": 1.0,
            "n_clones": 0,
        },
        KEY,
        purpose="quality_report",
    )
    monkeypatch.setattr(promotion_module, "sft_row_errors", lambda *_a, **_k: [])

    report = create_train_ready_report(
        candidate_report,
        candidates,
        rows,
        KEY,
        release_selection_receipt=receipt,
    )

    assert report["worlds_by_domain"] == {
        domain: 2
        for domain in dict(
            release_profile(
                "p13-authentic-six-domain-probe-12-v1"
            ).promoted_domain_world_quotas
        )
    }
    assert report["split_worlds_by_domain"] == receipt["split_worlds_by_domain"]


def _p12_sec_exact_bucket_selection_inputs(
    bands: tuple[str, ...],
) -> tuple[list[dict], list[dict]]:
    template, _ = _candidate()
    profile = release_profile("p12-sec-source-slice-1-v1")
    tokens_by_band = {
        "16k": 16_000,
        "32k": 32_000,
        "64k": 64_000,
        "128k": 128_000,
    }
    candidates = []
    audits = []
    for band in bands:
        level = {"16k": 1, "32k": 2, "64k": 3, "128k": 4}[band]
        candidate = {
            **json.loads(
                json.dumps(
                    {
                        key: value
                        for key, value in template.items()
                        if key != "attestation"
                    }
                )
            ),
            "world_id": "p12-sec-exact-world",
            "query_id": f"p12-sec-{band}",
            "domain": "company",
            "length_bucket": band,
            "tokenizer_context_tokens": tokens_by_band[band],
            "tokenizer_model_id": profile.tokenizer_model_id,
            "tokenizer_revision": profile.tokenizer_revision,
            "tokenizer_asset_manifest_sha256": (
                profile.tokenizer_asset_manifest_sha256
            ),
        }
        _mark_candidate_as_real(candidate)
        relations = [
            {
                "parent_record_id": f"filing-{index}",
                "child_record_id": f"filing-{index + 1}",
                "relation_provenance": "authentic_source",
            }
            for index in range(level)
        ]
        candidate.update(
            {
                "semantic_growth_group_id": "p12-sec-real-history",
                "semantic_tokens": {
                    "event_bearing": 8_000 * level,
                    "internal": 10_000 * level,
                    "generic_background": 0,
                },
                "strict_support_event_count": 4 * level,
                "source_relation_edges": relations,
                "authentic_source_relation_edges": relations,
                "graph": {
                    **dict(candidate.get("graph") or {}),
                    "n_essential_events": 2 * level,
                    "proof_depth": level + 1,
                },
            }
        )
        candidate = attach_attestation(
            candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        candidates.append(candidate)
        audits.append(_selection_audit(candidate))
    return candidates, audits


def test_p12_sec_selection_rejects_a_world_missing_required_128k() -> None:
    candidates, audits = _p12_sec_exact_bucket_selection_inputs(("16k", "32k", "64k"))

    with pytest.raises(PromotionError, match="required exact length buckets"):
        select_release_worlds(
            candidates,
            audits,
            "p12-sec-source-slice-1-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_p12_sec_selection_accepts_all_four_required_exact_buckets() -> None:
    candidates, audits = _p12_sec_exact_bucket_selection_inputs(
        ("16k", "32k", "64k", "128k")
    )

    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p12-sec-source-slice-1-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    assert {row["length_bucket"] for row in selected} == {
        "16k",
        "32k",
        "64k",
        "128k",
    }
    assert receipt["n_selected_worlds"] == 1


def _p12_wiki_exact_bucket_selection_inputs(
    bands: tuple[str, ...],
) -> tuple[list[dict], list[dict]]:
    template, _ = _candidate()
    profile = release_profile("p12-wiki-source-slice-1-v1")
    tokens_by_band = {"16k": 16_000, "32k": 32_000, "64k": 64_000}
    candidates = []
    audits = []
    for band in bands:
        level = {"16k": 1, "32k": 2, "64k": 3}[band]
        candidate = {
            **json.loads(
                json.dumps(
                    {
                        key: value
                        for key, value in template.items()
                        if key != "attestation"
                    }
                )
            ),
            "world_id": "p12-wiki-exact-world",
            "query_id": f"p12-wiki-{band}",
            "dossier_id": f"p12-wiki-{band}",
            "domain": "researchlab",
            "length_bucket": band,
            "tokenizer_context_tokens": tokens_by_band[band],
            "tokenizer_model_id": profile.tokenizer_model_id,
            "tokenizer_revision": profile.tokenizer_revision,
            "tokenizer_asset_manifest_sha256": (
                profile.tokenizer_asset_manifest_sha256
            ),
        }
        _mark_candidate_as_real(candidate)
        candidate["artifact_classification"] = _source_bound_classifications(candidate)
        relations = [
            {
                "parent_record_id": f"revision-{index}",
                "child_record_id": f"revision-{index + 1}",
                "relation_provenance": "authentic_source",
            }
            for index in range(level)
        ]
        candidate.update(
            {
                "semantic_growth_group_id": "p12-wiki-real-history",
                "semantic_tokens": {
                    "event_bearing": 10_000 * level,
                    "internal": 12_000 * level,
                    "generic_background": 0,
                },
                "strict_support_event_count": 4 * level,
                "source_relation_edges": relations,
                "authentic_source_relation_edges": relations,
                "graph": {
                    **dict(candidate.get("graph") or {}),
                    "n_essential_events": 2 * level,
                    "proof_depth": level + 1,
                },
            }
        )
        candidate = attach_attestation(
            candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        candidates.append(candidate)
        audits.append(_selection_audit(candidate))
    return candidates, audits


def test_p12_wiki_selection_rejects_a_world_missing_required_32k() -> None:
    candidates, audits = _p12_wiki_exact_bucket_selection_inputs(("16k", "64k"))

    with pytest.raises(PromotionError, match="required exact length buckets"):
        select_release_worlds(
            candidates,
            audits,
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_p12_wiki_selection_accepts_all_three_required_exact_buckets() -> None:
    candidates, audits = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))

    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    assert {row["length_bucket"] for row in selected} == {"16k", "32k", "64k"}
    assert receipt["n_selected_worlds"] == 1


def test_p12_wiki_selection_uses_replayed_not_candidate_growth() -> None:
    candidates, audits = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    replayed = []
    for audit in audits:
        payload = {key: value for key, value in audit.items() if key != "attestation"}
        payload["strict_growth_metrics"] = {
            "semantic_growth_group_id": "p12-wiki-real-history",
            "semantic_tokens": {"event_bearing": 10_000, "internal": 10_000},
            "strict_support_event_count": 4,
            "graph": {"n_essential_events": 2, "proof_depth": 2},
            "authentic_source_relation_edges": [
                {
                    "parent_record_id": "revision-0",
                    "child_record_id": "revision-1",
                    "relation_provenance": "authentic_source",
                }
            ],
        }
        replayed.append(attach_attestation(payload, KEY, purpose=DENSE_AUDIT_PURPOSE))

    with pytest.raises(PromotionError, match="strict replay cumulative history"):
        select_release_worlds(
            candidates,
            replayed,
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_strict_growth_metrics_require_event_bearing_to_be_internal_subset() -> None:
    metrics = {
        "semantic_growth_group_id": "invalid-semantic-partition",
        "semantic_tokens": {"event_bearing": 2, "internal": 1},
        "strict_support_event_count": 1,
        "graph": {"n_essential_events": 1, "proof_depth": 1},
        "authentic_source_relation_edges": [],
    }

    assert not promotion_module._strict_growth_metrics_are_valid(metrics)
    metrics["semantic_tokens"]["event_bearing"] = "not-an-int"
    assert not promotion_module._strict_growth_metrics_are_valid(metrics)


def _p12_wiki_promoted_rows(candidates: list[dict], receipt: dict) -> list[dict]:
    selection_digest = serialized_row_sha256(receipt)
    return [
        {
            **candidate,
            "data_stage": "train_ready",
            "split": receipt["split_by_world"][candidate["world_id"]],
            "promotion": {
                "candidate_sha256": candidate_sha256(candidate),
                "dense_audit_sha256": receipt["audit_sha256_by_candidate"][
                    candidate_sha256(candidate)
                ],
                "release_selection_sha256": selection_digest,
                "tokenizer_asset_manifest_sha256": candidate[
                    "tokenizer_asset_manifest_sha256"
                ],
            },
        }
        for candidate in candidates
    ]


def _p12_wiki_candidate_report(candidates: list[dict]) -> dict:
    return attach_attestation(
        {
            "schema_version": candidates[0]["schema_version"],
            "data_product": candidates[0]["data_product"],
            "data_stage": "candidate",
            "release_profile_id": "p12-wiki-source-slice-1-v1",
            "release_profile_sha256": release_profile_sha256(
                "p12-wiki-source-slice-1-v1"
            ),
            "n_worlds": 1,
            "n_rows": len(candidates),
            "target_promoted_worlds": 1,
            "candidate_row_set_sha256": promoted_row_set_sha256(candidates),
            "retention": 1.0,
            "n_clones": 0,
        },
        KEY,
        purpose="quality_report",
    )


def test_p12_wiki_train_ready_report_rejects_missing_required_32k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates, audits = _p12_wiki_exact_bucket_selection_inputs(("16k", "64k"))
    rows = _p12_wiki_promoted_rows(
        candidates,
        {
            "split_by_world": {"p12-wiki-exact-world": "train"},
            "audit_sha256_by_candidate": {
                candidate_sha256(candidate): serialized_row_sha256(audit)
                for candidate, audit in zip(candidates, audits)
            },
            "attestation": {},
        },
    )
    monkeypatch.setattr("longworld.core.promotion.sft_row_errors", lambda *_a, **_k: [])

    with pytest.raises(PromotionError, match="missing required exact length buckets"):
        create_train_ready_report(
            _p12_wiki_candidate_report(candidates),
            candidates,
            rows,
            KEY,
        )


def test_p12_wiki_train_ready_report_accepts_all_three_required_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates, audits = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )
    rows = _p12_wiki_promoted_rows(selected, receipt)
    monkeypatch.setattr("longworld.core.promotion.sft_row_errors", lambda *_a, **_k: [])

    report = create_train_ready_report(
        _p12_wiki_candidate_report(candidates),
        candidates,
        rows,
        KEY,
        release_selection_receipt=receipt,
    )

    assert report["by_length"] == {"16k": 1, "32k": 1, "64k": 1}


def test_release_world_selection_is_deterministic_and_world_atomic() -> None:
    template, _ = _candidate()
    candidates = []
    audits = []
    for index in range(14):
        candidate = {
            **json.loads(
                json.dumps(
                    {
                        key: value
                        for key, value in template.items()
                        if key != "attestation"
                    }
                )
            ),
            "world_id": f"world-{index:02d}",
            "query_id": f"query-{index:02d}",
        }
        if index == 0:
            _mark_candidate_as_real(candidate)
        candidate = attach_attestation(
            candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        candidates.append(candidate)
        audits.append(_selection_audit(candidate))

    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p3-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )
    reversed_selected, reversed_receipt = select_release_worlds(
        list(reversed(candidates)),
        list(reversed(audits)),
        "p3-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    assert {row["world_id"] for row in selected} == {
        row["world_id"] for row in reversed_selected
    }
    assert receipt == reversed_receipt
    assert len(receipt["split_by_world"]) == 12
    assert list(receipt["split_by_world"].values()).count("train") == 10
    assert list(receipt["split_by_world"].values()).count("eval") == 2


def test_legacy_release_profile_does_not_require_a_tokenizer_asset_pin() -> None:
    template, _ = _candidate()
    candidates = []
    audits = []
    for index in range(14):
        candidate = {
            **json.loads(
                json.dumps(
                    {
                        key: value
                        for key, value in template.items()
                        if key != "attestation"
                    }
                )
            ),
            "world_id": f"legacy-exact-world-{index:02d}",
            "query_id": f"legacy-exact-query-{index:02d}",
            "length_bucket": "16k",
            "tokenizer_context_tokens": 16_001,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": ("a7b0d22b993d71000cf2eadfb37222a67cee521e"),
            "tokenizer_asset_manifest_sha256": "b" * 64,
        }
        if index == 0:
            _mark_candidate_as_real(candidate)
        candidate = attach_attestation(
            candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        candidates.append(candidate)
        audits.append(_selection_audit(candidate))

    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p3-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    assert len({row["world_id"] for row in selected}) == 12
    assert receipt["tokenizer_asset_manifest_sha256"] is None


def _p4_selection_inputs(
    counts: dict[str, int],
) -> tuple[list[dict], list[dict], str]:
    template, _ = _candidate()
    candidates = []
    audits = []
    real_world_id = "codeforge-world-00"
    for domain, count in counts.items():
        for index in range(count):
            candidate = {
                **json.loads(
                    json.dumps(
                        {
                            key: value
                            for key, value in template.items()
                            if key != "attestation"
                        }
                    )
                ),
                "world_id": f"{domain}-world-{index:02d}",
                "query_id": f"{domain}-query-{index:02d}",
                "domain": domain,
            }
            if candidate["world_id"] == real_world_id:
                _mark_candidate_as_real(candidate)
            candidate = attach_attestation(
                candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
            )
            candidates.append(candidate)
            audits.append(_selection_audit(candidate))
    return candidates, audits, real_world_id


def test_p4_release_world_selection_enforces_exact_domain_quotas() -> None:
    candidates, audits, real_world_id = _p4_selection_inputs(
        {"company": 6, "researchlab": 6, "codeforge": 6}
    )

    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p4-multidomain-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    selected_world_domains = {row["world_id"]: row["domain"] for row in selected}
    assert Counter(selected_world_domains.values()) == {
        "company": 4,
        "researchlab": 4,
        "codeforge": 4,
    }
    assert receipt["promoted_domain_world_quotas"] == {
        "company": 4,
        "researchlab": 4,
        "codeforge": 4,
    }
    assert {
        domain: len(world_ids)
        for domain, world_ids in receipt["selected_worlds_by_domain"].items()
    } == receipt["promoted_domain_world_quotas"]
    assert real_world_id in selected_world_domains
    assert receipt["split_by_world"][real_world_id] == "train"


def test_domain_selection_balances_an_all_real_surplus_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "p4-multidomain-probe-12-v1"
    monkeypatch.setitem(
        RELEASE_PROFILES,
        profile_id,
        replace(
            release_profile(profile_id),
            min_real_train_worlds=10,
            min_real_eval_worlds=2,
            min_real_exact_64k_worlds_by_domain=(
                ("company", 4),
                ("researchlab", 4),
                ("codeforge", 4),
            ),
        ),
    )
    profile_digest = release_profile_sha256(profile_id)
    ordered_ids = sorted(
        (f"surplus-world-{index:02d}" for index in range(18)),
        key=lambda world_id: hashlib.sha256(
            f"{profile_digest}|select|{world_id}".encode()
        ).hexdigest(),
    )
    domains = {
        **{world_id: "company" for world_id in ordered_ids[:5]},
        **{world_id: "researchlab" for world_id in ordered_ids[5:9]},
        **{world_id: "codeforge" for world_id in ordered_ids[9:12]},
        **{world_id: "company" for world_id in ordered_ids[12:13]},
        **{world_id: "researchlab" for world_id in ordered_ids[13:15]},
        **{world_id: "codeforge" for world_id in ordered_ids[15:]},
    }
    template, _ = _candidate()
    candidates = []
    audits = []
    for index, world_id in enumerate(ordered_ids):
        candidate = {
            **json.loads(
                json.dumps(
                    {
                        key: value
                        for key, value in template.items()
                        if key != "attestation"
                    }
                )
            ),
            "world_id": world_id,
            "query_id": f"surplus-query-{index:02d}",
            "domain": domains[world_id],
            "length_bucket": "64k",
            "tokenizer_context_tokens": 64_000,
            "tokenizer_model_id": release_profile(profile_id).tokenizer_model_id,
            "tokenizer_revision": release_profile(profile_id).tokenizer_revision,
            "tokenizer_asset_manifest_sha256": "d" * 64,
        }
        _mark_candidate_as_real(candidate)
        candidate = attach_attestation(
            candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        candidates.append(candidate)
        audits.append(_selection_audit(candidate))

    non_exact_candidates = []
    non_exact_audits = []
    for candidate in candidates:
        non_exact = {
            key: value
            for key, value in candidate.items()
            if key
            not in {
                "attestation",
                "tokenizer_context_tokens",
                "tokenizer_model_id",
                "tokenizer_revision",
            }
        }
        non_exact["length_bucket"] = "4k"
        non_exact = attach_attestation(
            non_exact, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        non_exact_candidates.append(non_exact)
        non_exact_audits.append(_selection_audit(non_exact))
    with pytest.raises(PromotionError, match="real exact-64K domain worlds"):
        select_release_worlds(
            non_exact_candidates,
            non_exact_audits,
            profile_id,
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )

    selected, _ = select_release_worlds(
        candidates,
        audits,
        profile_id,
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    assert Counter({row["world_id"]: row["domain"] for row in selected}.values()) == {
        "company": 4,
        "researchlab": 4,
        "codeforge": 4,
    }


def test_p4_release_world_selection_keeps_real_workflows_from_each_domain() -> None:
    candidates, audits, original_real_world_id = _p4_selection_inputs(
        {"company": 6, "researchlab": 6, "codeforge": 6}
    )
    profile_digest = release_profile_sha256("p4-multidomain-probe-12-v1")
    template, _ = _candidate()
    original_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate["world_id"] == original_real_world_id
    )
    unsigned = {
        key: value
        for key, value in candidates[original_index].items()
        if key != "attestation"
    }
    for field in (
        "source_family_ids",
        "source_relation_edges",
        "episode_replay_bundle",
    ):
        unsigned.pop(field, None)
    unsigned["artifact_classification"] = template["artifact_classification"]
    candidates[original_index] = attach_attestation(
        unsigned, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    audits[original_index] = _selection_audit(candidates[original_index])

    first_real_index = min(
        range(len(candidates)),
        key=lambda index: hashlib.sha256(
            f"{profile_digest}|select|{candidates[index]['world_id']}".encode()
        ).hexdigest(),
    )
    unsigned = {
        key: value
        for key, value in candidates[first_real_index].items()
        if key != "attestation"
    }
    _mark_candidate_as_real(unsigned)
    candidates[first_real_index] = attach_attestation(
        unsigned, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    audits[first_real_index] = _selection_audit(candidates[first_real_index])
    first_real_world_id = candidates[first_real_index]["world_id"]
    first_real_domain = candidates[first_real_index]["domain"]

    baseline, _ = select_release_worlds(
        candidates,
        audits,
        "p4-multidomain-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )
    baseline_worlds = {row["world_id"] for row in baseline}
    second_real_world_id = next(
        candidate["world_id"]
        for candidate in candidates
        if candidate["domain"] != first_real_domain
        and candidate["world_id"] not in baseline_worlds
    )
    for index, candidate in enumerate(candidates):
        if candidate["world_id"] != second_real_world_id:
            continue
        unsigned = {
            key: value for key, value in candidate.items() if key != "attestation"
        }
        _mark_candidate_as_real(unsigned)
        candidates[index] = attach_attestation(
            unsigned, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        audits[index] = _selection_audit(candidates[index])
        break

    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p4-multidomain-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    selected_worlds = {row["world_id"] for row in selected}
    assert first_real_world_id in selected_worlds
    assert second_real_world_id in selected_worlds
    assert set(receipt["real_worlds_by_split"]["train"]) | set(
        receipt["real_worlds_by_split"]["eval"]
    ) == {first_real_world_id, second_real_world_id}


def test_p4_release_world_selection_fails_closed_on_domain_shortfall() -> None:
    candidates, audits, _ = _p4_selection_inputs(
        {"company": 3, "researchlab": 7, "codeforge": 8}
    )

    with pytest.raises(PromotionError, match="company.*3<4"):
        select_release_worlds(
            candidates,
            audits,
            "p4-multidomain-probe-12-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_release_world_selection_excludes_a_world_over_the_row_dup_limit() -> None:
    template, _ = _candidate()
    profile_digest = release_profile_sha256("p3-probe-12-v1")
    world_ids = [f"world-{index:02d}" for index in range(14)]
    bad_world_id = min(
        world_ids[1:],
        key=lambda world_id: hashlib.sha256(
            f"{profile_digest}|select|{world_id}".encode()
        ).hexdigest(),
    )
    candidates = []
    audits = []
    for index, world_id in enumerate(world_ids):
        candidate = {
            **json.loads(
                json.dumps(
                    {
                        key: value
                        for key, value in template.items()
                        if key != "attestation"
                    }
                )
            ),
            "world_id": world_id,
            "query_id": f"query-{index:02d}",
            # Selection must not trust this producer-authored value.
            "near_dup_sentence_ratio": 0.0,
        }
        if index == 0:
            _mark_candidate_as_real(candidate)
        candidate = attach_attestation(
            candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        candidates.append(candidate)
        audits.append(
            _selection_audit(
                candidate,
                near_dup_sentence_ratio=(0.2501 if world_id == bad_world_id else 0.0),
            )
        )
        if world_id == bad_world_id:
            sibling = attach_attestation(
                {
                    **json.loads(
                        json.dumps(
                            {
                                key: value
                                for key, value in candidate.items()
                                if key != "attestation"
                            }
                        )
                    ),
                    "query_id": f"query-{index:02d}-sibling",
                },
                KEY,
                purpose=CANDIDATE_ATTESTATION_PURPOSE,
            )
            candidates.append(sibling)
            audits.append(_selection_audit(sibling))

    selected, receipt = select_release_worlds(
        candidates,
        audits,
        "p3-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    assert bad_world_id not in {row["world_id"] for row in selected}
    assert receipt["n_eligible_worlds"] == 13
    assert receipt["n_selected_worlds"] == 12


def test_release_world_selection_rejects_audit_without_replayed_dup_ratio() -> None:
    template, _ = _candidate()
    candidate = attach_attestation(
        {
            **{key: value for key, value in template.items() if key != "attestation"},
            "world_id": "world-missing-audited-ratio",
            "query_id": "query-missing-audited-ratio",
        },
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    audit = _selection_audit(candidate)
    audit = attach_attestation(
        {
            key: value
            for key, value in audit.items()
            if key not in {"attestation", "near_dup_sentence_ratio"}
        },
        KEY,
        purpose=DENSE_AUDIT_PURPOSE,
    )

    with pytest.raises(PromotionError, match="audit contract is invalid"):
        select_release_worlds(
            [candidate],
            [audit],
            "p3-probe-12-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_release_world_selection_keeps_verified_real_workflow_in_train() -> None:
    template, _ = _candidate()
    profile_digest = release_profile_sha256("p3-probe-12-v1")
    world_ids = [f"world-{index:02d}" for index in range(12)]
    real_world_id = min(
        world_ids,
        key=lambda world_id: hashlib.sha256(
            f"{profile_digest}|eval|{world_id}".encode()
        ).hexdigest(),
    )
    candidates = []
    audits = []
    for index, world_id in enumerate(world_ids):
        candidate = {
            **json.loads(
                json.dumps(
                    {
                        key: value
                        for key, value in template.items()
                        if key != "attestation"
                    }
                )
            ),
            "world_id": world_id,
            "query_id": f"query-{index:02d}",
        }
        if world_id == real_world_id:
            _mark_candidate_as_real(candidate)
        candidate = attach_attestation(
            candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        candidates.append(candidate)
        audits.append(_selection_audit(candidate))

    _, receipt = select_release_worlds(
        candidates,
        audits,
        "p3-probe-12-v1",
        candidate_attestation_key=KEY,
        audit_attestation_key=KEY,
    )

    assert receipt["split_by_world"][real_world_id] == "train"
    assert receipt["real_worlds_by_split"] == {"train": [real_world_id], "eval": []}


def test_release_world_selection_rejects_a_signature_only_audit() -> None:
    candidate, _ = _candidate()
    audit = attach_attestation(
        {"candidate_sha256": candidate_sha256(candidate)},
        KEY,
        purpose=DENSE_AUDIT_PURPOSE,
    )

    with pytest.raises(PromotionError, match="audit contract"):
        select_release_worlds(
            [candidate],
            [audit],
            "p3-probe-12-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_production_world_selection_requires_a_gate_pass_receipt() -> None:
    with pytest.raises(PromotionError, match="predecessor gate"):
        select_release_worlds(
            [],
            [],
            "p3-production-48-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_production_accepts_only_a_pinned_green_probe_gate_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auditor_key = b"probe-auditor-gate-key-material-at-least-32-bytes"
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", auditor_key.decode())
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", "probe-gate-v1")
    receipt = attach_attestation(
        {
            "schema_version": "longworld-release-gate-pass-v1",
            "gate_revision": RELEASE_GATE_REVISION,
            "release_profile_id": "p3-probe-12-v1",
            "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            "predecessor_profile_id": None,
            "quality_report_sha256": "a" * 64,
            "source_file_sha256": {
                "quality_report.json": "a" * 64,
                "train.jsonl": "b" * 64,
                "eval.jsonl": "c" * 64,
            },
            "metrics_sha256": "d" * 64,
            "n_worlds": 12,
            "n_rows": 106,
            "ok": True,
            "errors": [],
        },
        auditor_key,
        purpose="release_gate_pass",
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "production")

    gate_digest, report_digest = validate_predecessor_gate_receipt(
        receipt,
        "p3-production-48-v1",
        attestation_key=auditor_key,
        key_id="probe-gate-v1",
    )

    assert gate_digest == serialized_row_sha256(receipt)
    assert report_digest == "a" * 64
    tampered = {**receipt, "ok": False}
    with pytest.raises(PromotionError, match="attestation"):
        validate_predecessor_gate_receipt(
            tampered,
            "p3-production-48-v1",
            attestation_key=auditor_key,
            key_id="probe-gate-v1",
        )


def test_production_predecessor_rejects_hmac_signed_fake_kms_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auditor_key = b"production-auditor-gate-key-material-at-least-32-bytes"
    key_id = "production-auditor-2026-08"
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "production")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", auditor_key.decode())
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", key_id)
    fake_approval = {
        "verified": True,
        "scheme": "ecdsa-p256-sha256-v1",
        "approval_key_id": ("aws-kms://arn:aws:kms:us-east-1:123456789012:key/fake"),
        "approval_envelope_sha256": "1" * 64,
        "trust_roots_sha256": "2" * 64,
        "approval_statement_sha256": "3" * 64,
        "statement": {},
        "signature": {},
    }
    receipt = attach_attestation(
        {
            "schema_version": "longworld-release-gate-pass-v1",
            "gate_revision": RELEASE_GATE_REVISION,
            "release_profile_id": "p3-production-48-v1",
            "release_profile_sha256": release_profile_sha256("p3-production-48-v1"),
            "predecessor_profile_id": "p3-probe-12-v1",
            "quality_report_sha256": "a" * 64,
            "source_file_sha256": {
                "quality_report.json": "a" * 64,
                "train.jsonl": "b" * 64,
                "eval.jsonl": "c" * 64,
            },
            "metrics_sha256": "d" * 64,
            "n_worlds": 48,
            "n_rows": 100,
            "production_approval": fake_approval,
            "ok": True,
            "errors": [],
        },
        auditor_key,
        purpose="release_gate_pass",
    )

    with pytest.raises(PromotionError, match="production approval"):
        validate_predecessor_gate_receipt(
            receipt,
            "p3-production-210-v1",
            attestation_key=auditor_key,
            key_id=key_id,
        )


def test_promotion_chain_accepts_distinct_role_keys_and_rejects_cross_role_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_key = b"candidate-role-key-material-at-least-32-bytes"
    ranking_key = b"ranking-role-key-material-at-least-32-bytes"
    audit_key = b"auditor-role-key-material-at-least-32-bytes"
    promotion_key = b"promotion-role-key-material-at-least-32-bytes"
    report_key = b"report-role-key-material-at-least-32-bytes"
    role_keys = {
        "candidate": candidate_key,
        "ranker": ranking_key,
        "auditor": audit_key,
        "promotion": promotion_key,
        "report": report_key,
    }
    for role, key in role_keys.items():
        monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"test-distinct-{role}-v1")
    candidate, artifacts = _candidate()
    candidate = attach_attestation(
        candidate, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    ranking = attach_attestation(
        _ranking(candidate, artifacts), ranking_key, purpose=DENSE_RANKING_PURPOSE
    )

    audit = create_dense_audit(
        candidate,
        ranking,
        k=3,
        candidate_attestation_key=candidate_key,
        ranking_attestation_key=ranking_key,
        audit_attestation_key=audit_key,
    )
    promoted = promote_candidate(
        candidate,
        audit,
        candidate_attestation_key=candidate_key,
        audit_attestation_key=audit_key,
        promotion_attestation_key=promotion_key,
    )
    candidate_report = attach_attestation(
        {
            "schema_version": "p3.0",
            "data_product": "worldlong_valid_v1",
            "data_stage": "candidate",
            "release_profile_id": "p3-probe-12-v1",
            "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            "n_worlds": 1,
            "n_rows": 1,
            "candidate_row_set_sha256": promoted_row_set_sha256([candidate]),
            "target_promoted_worlds": 1,
            "retention": 0.5,
            "n_clones": 0,
        },
        report_key,
        purpose="quality_report",
    )
    report = create_train_ready_report(
        candidate_report,
        [candidate],
        [promoted],
        report_attestation_key=report_key,
        candidate_attestation_key=candidate_key,
        promotion_attestation_key=promotion_key,
    )

    assert verify_attestation(audit, audit_key, purpose="dense_retrieval_audit")
    assert not verify_attestation(audit, promotion_key, purpose="dense_retrieval_audit")
    assert verify_attestation(promoted, promotion_key, purpose="sft_row")
    assert verify_attestation(report, report_key, purpose="quality_report")


@pytest.mark.parametrize(
    "query_type",
    ["release_eligibility", "release_ci_matrix", "release_license_matrix"],
)
def test_counterfactual_release_candidates_have_no_short_context_shortcut(
    query_type: str,
) -> None:
    candidate, artifacts = _candidate("cf", query_type)

    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)

    assert audit["embedding_topk_insufficient"]
    assert verify_attestation(audit, KEY, purpose=DENSE_AUDIT_PURPOSE)


def test_dense_audit_rejects_view_composition_mismatch() -> None:
    candidate, artifacts = _candidate("full")
    candidate["composition_method"] = "causal_timeline"
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="composition"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_dense_audit_rejects_shuffled_ordered_view() -> None:
    candidate, artifacts = _candidate("ordered_artifact_view")
    documents = candidate["document_context"].split(SEP)
    candidate["artifact_classification"][0:2] = reversed(
        candidate["artifact_classification"][0:2]
    )
    documents[0:2] = reversed(documents[0:2])
    candidate["document_context"] = SEP.join(documents)
    candidate["context"] = wrap_prompt(
        candidate["question"], candidate["document_context"], "first"
    )
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    ranking = _ranking(candidate, artifacts)
    text_by_id = {
        item["artifact_id"]: text
        for item, text in zip(candidate["artifact_classification"], documents)
    }
    for item in ranking["artifacts"]:
        item["text_sha256"] = hashlib.sha256(
            text_by_id[item["artifact_id"]].encode()
        ).hexdigest()
    ranking = _resign_ranking(ranking)

    with pytest.raises(PromotionError, match="chronological"):
        create_dense_audit(candidate, ranking, KEY, k=3)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("ranker_type", "lexical_tfidf", "dense_embedding"),
        ("model.revision", "main", "pinned model revision"),
        ("model.revision", "1110a243", "pinned model revision"),
    ],
)
def test_dense_audit_rejects_lexical_or_unpinned_rankings(
    field: str, value: str, message: str
) -> None:
    candidate, artifacts = _candidate()
    ranking = _ranking(candidate, artifacts)
    if field == "model.revision":
        ranking["model"]["revision"] = value
    else:
        ranking[field] = value
    ranking = _resign_ranking(ranking)

    with pytest.raises(PromotionError, match=message):
        create_dense_audit(candidate, ranking, KEY, k=3)


def test_dense_audit_binds_candidate_and_every_artifact_text() -> None:
    candidate, artifacts = _candidate()
    ranking = _ranking(candidate, artifacts)
    ranking["artifacts"][0]["text_sha256"] = "0" * 64
    ranking = _resign_ranking(ranking)

    with pytest.raises(PromotionError, match="artifact text digest"):
        create_dense_audit(candidate, ranking, KEY, k=3)


def test_dense_audit_rejects_unsigned_or_tampered_dense_ranking() -> None:
    candidate, artifacts = _candidate()
    unsigned = _ranking(candidate, artifacts)
    unsigned.pop("attestation")
    with pytest.raises(PromotionError, match="ranking attestation"):
        create_dense_audit(candidate, unsigned, KEY, k=3)

    tampered = _ranking(candidate, artifacts)
    tampered["artifacts"][0]["score"] = 99.0
    with pytest.raises(PromotionError, match="ranking attestation"):
        create_dense_audit(candidate, tampered, KEY, k=3)

    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    candidate["document_context"] += "\ntampered"
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    with pytest.raises(PromotionError, match="candidate digest"):
        promote_candidate(candidate, audit, KEY)


def test_dense_audit_rejects_topk_that_strictly_replays_to_gold() -> None:
    candidate, artifacts = _candidate()
    essential = set(candidate["essential_artifact_ids"])
    artifacts.sort(key=lambda artifact: artifact.artifact_id not in essential)

    with pytest.raises(PromotionError, match="dense top-k solves"):
        create_dense_audit(
            candidate, _ranking(candidate, artifacts), KEY, k=len(essential)
        )


def test_promotion_fails_if_candidate_answer_no_longer_matches_replay() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    candidate["answer"] = "forged"
    candidate["view_verification"]["expected_answer"] = "forged"
    candidate["view_verification"]["strict_replay_answer"] = "forged"
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    ranking = _ranking(candidate, artifacts)
    with pytest.raises(PromotionError, match="answer does not match"):
        create_dense_audit(candidate, ranking, KEY, k=3)

    with pytest.raises(PromotionError, match="candidate digest"):
        promote_candidate(candidate, audit, KEY)


def test_dense_audit_rejects_unsigned_candidate_or_prompt_not_bound_to_documents() -> (
    None
):
    candidate, artifacts = _candidate()
    unsigned = dict(candidate)
    unsigned.pop("attestation")
    with pytest.raises(PromotionError, match="producer attestation"):
        create_dense_audit(unsigned, _ranking(unsigned, artifacts), KEY, k=3)

    candidate["context"] = "unrelated copied prose"
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    with pytest.raises(PromotionError, match="prompt does not match"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_real_or_hybrid_candidate_fails_closed_without_episode_replay_bundle() -> None:
    candidate, artifacts = _candidate()
    candidate["artifact_classification"][0].update(
        source_origin="real_public", workflow_kind="hybrid_causal"
    )
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="episode_replay_bundle"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_unrelated_episode_bundle_cannot_launder_synthetic_artifacts_as_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())
    bundle_path = _episode_bundle(tmp_path)
    candidate, artifacts = _candidate()
    for classification in candidate["artifact_classification"]:
        classification.update(
            source_origin="real_public", workflow_kind="hybrid_causal"
        )
    candidate["source_origins"] = ["real_public"]
    candidate["workflow_kinds"] = ["hybrid_causal"]
    candidate["source_family_ids"] = ["github.com/fake/repository"]
    candidate["source_relation_id"] = "fake-relation"
    candidate["source_relation_edges"] = [
        {"parent_record_id": "fake", "child_record_id": "fake"}
    ]
    candidate["episode_replay_bundle"] = {
        "schema_version": "longworld.episode-replay-bundle.v1",
        "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "composition": "chronological_causal_union",
    }
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="classification"):
        create_dense_audit(
            candidate,
            _ranking(candidate, artifacts),
            KEY,
            k=3,
            episode_bundle_path=bundle_path,
        )


def test_candidate_motif_must_match_the_replayed_query() -> None:
    candidate, artifacts = _candidate()
    candidate["motif"] = "fake-profile-motif"
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="motif"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_semantic_base_task_id_ignores_world_and_source_instance_labels() -> None:
    materialized = materialize(1, n_parallel=0, n_pulses=0, domain="codeforge")
    spec = materialized.queries[0]

    first = replace(spec, query_id="world-a:query", base_task_group="source:record-a")
    second = replace(spec, query_id="world-b:query", base_task_group="source:record-b")

    assert stable_semantic_base_task_id(first) == stable_semantic_base_task_id(second)


def test_answer_program_id_ignores_declared_proof_depth_but_binds_operations() -> None:
    import longworld.core.promotion as promotion_module

    materialized = materialize(1, n_parallel=0, n_pulses=0, domain="codeforge")
    spec = materialized.queries[0]
    shallow = replace(spec, proof_depth=2)
    deep = replace(spec, proof_depth=9)
    changed = replace(
        spec,
        program_ops=[*(spec.program_ops or []), {"op": "EXTRA_SEMANTIC_JOIN"}],
    )

    assert promotion_module.stable_answer_program_id(shallow) == (
        promotion_module.stable_answer_program_id(deep)
    )
    assert promotion_module.stable_answer_program_id(shallow) != (
        promotion_module.stable_answer_program_id(changed)
    )


def test_answer_program_id_ignores_release_cycle_scale() -> None:
    import longworld.core.promotion as promotion_module

    materialized = materialize(1, n_parallel=0, n_pulses=0, domain="codeforge")
    spec = materialized.queries[0]
    two_cycles = replace(
        spec,
        query_type="release_supersession_trace",
        motif="release_supersession_trace",
        program_ops=[
            {"op": "FOLLOW_REQUIRED_INPUTS"},
            {"op": "REQUIRE_EACH_RELEASE_CI", "cycle_count": 2},
        ],
    )
    three_cycles = replace(
        two_cycles,
        program_ops=[
            {"op": "FOLLOW_REQUIRED_INPUTS"},
            {"op": "REQUIRE_EACH_RELEASE_CI", "cycle_count": 3},
        ],
    )

    assert promotion_module.stable_answer_program_id(two_cycles) == (
        promotion_module.stable_answer_program_id(three_cycles)
    )


def test_answer_program_id_ignores_instance_ids_but_binds_semantic_operands() -> None:
    import longworld.core.promotion as promotion_module

    materialized = materialize(1, n_parallel=0, n_pulses=0, domain="codeforge")
    spec = materialized.queries[0]
    first = replace(
        spec,
        program_ops=[
            {
                "op": "ADOPT_PRIMARY",
                "event_id": "world-1.adopt",
                "answer_key": "world-1.answer",
                "field": "version",
            }
        ],
    )
    second = replace(
        spec,
        program_ops=[
            {
                "op": "ADOPT_PRIMARY",
                "event_id": "world-2.adopt",
                "answer_key": "world-2.answer",
                "field": "version",
            }
        ],
    )
    changed = replace(
        second,
        program_ops=[
            {
                "op": "ADOPT_PRIMARY",
                "event_id": "world-2.adopt",
                "answer_key": "world-2.answer",
                "field": "license",
            }
        ],
    )

    assert promotion_module.stable_answer_program_id(first) == (
        promotion_module.stable_answer_program_id(second)
    )
    assert promotion_module.stable_answer_program_id(first) != (
        promotion_module.stable_answer_program_id(changed)
    )


def test_answer_program_id_falls_back_to_semantic_expression_for_empty_ops() -> None:
    import longworld.core.promotion as promotion_module

    materialized = materialize(1, n_parallel=0, n_pulses=0, domain="codeforge")
    spec = materialized.queries[0]
    first = replace(spec, program_ops=[], gold_expression="read version then select")
    second = replace(spec, program_ops=[], gold_expression="read license then select")

    assert promotion_module.stable_answer_program_id(first) != (
        promotion_module.stable_answer_program_id(second)
    )


def test_gate_revision_dispatch_is_current_for_p10_and_read_only_for_legacy() -> None:
    assert release_gate_revision_supported(
        "p10-source-rich-production-48-v1", RELEASE_GATE_REVISION
    )
    assert not release_gate_revision_supported(
        "p10-source-rich-production-48-v1", LEGACY_RELEASE_GATE_REVISION
    )
    assert not release_gate_revision_supported(
        "p12-current-source-probe-12-v1", LEGACY_RELEASE_GATE_REVISION
    )
    assert not release_gate_revision_supported(
        "p12-current-source-probe-12-v2", LEGACY_RELEASE_GATE_REVISION
    )
    assert release_gate_revision_supported(
        "p3-production-48-v1", LEGACY_RELEASE_GATE_REVISION
    )
    assert not release_gate_revision_supported(
        "p99-future-production-999-v1", LEGACY_RELEASE_GATE_REVISION
    )
    assert not release_gate_revision_supported("", LEGACY_RELEASE_GATE_REVISION)


def test_p10_selection_rejects_a_legacy_predecessor_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auditor_key = b"probe-auditor-gate-key-material-at-least-32-bytes"
    predecessor_id = "p12-current-source-probe-12-v1"
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", auditor_key.decode())
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", "probe-gate-v1")
    receipt = attach_attestation(
        {
            "schema_version": "longworld-release-gate-pass-v1",
            "gate_revision": LEGACY_RELEASE_GATE_REVISION,
            "release_profile_id": predecessor_id,
            "release_profile_sha256": release_profile_sha256(predecessor_id),
            "predecessor_profile_id": None,
            "quality_report_sha256": "a" * 64,
            "source_file_sha256": {
                "quality_report.json": "a" * 64,
                "train.jsonl": "b" * 64,
                "eval.jsonl": "c" * 64,
            },
            "metrics_sha256": "d" * 64,
            "n_worlds": 12,
            "n_rows": 12,
            "ok": True,
            "errors": [],
        },
        auditor_key,
        purpose=RELEASE_GATE_PURPOSE,
    )

    with pytest.raises(PromotionError, match="predecessor gate receipt contract"):
        validate_predecessor_gate_receipt(
            receipt,
            "p10-source-rich-production-48-v1",
            attestation_key=auditor_key,
            key_id="probe-gate-v1",
        )


@pytest.mark.parametrize(
    ("field", "forged", "message"),
    [
        ("source_family_ids", ["github.com/fake/repository"], "source family"),
        ("canonical_topology", "fake-topology", "canonical topology"),
        ("topology_family", "fake-family", "topology family"),
        ("executable_proof_id", "random-proof-id", "executable proof"),
        ("answer_program_id", "random-program-id", "answer program"),
        ("base_task_id", "random-base-task-id", "base task"),
        (
            "semantic_base_task_id",
            "random-semantic-base-task-id",
            "semantic base task",
        ),
        ("cf_answer", "forged-counterfactual", "counterfactual answer"),
        ("cf_op", "forged-op", "counterfactual operator"),
        ("proof_graph", {"forged": True}, "proof graph"),
    ],
)
def test_dense_audit_rejects_forged_replay_derived_metadata(
    field: str, forged: object, message: str
) -> None:
    candidate, artifacts = _candidate()
    candidate[field] = forged
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match=message):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_promotion_rejects_split_that_disagrees_with_trusted_policy() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)

    with pytest.raises(PromotionError, match="trusted split policy"):
        promote_candidate(candidate, audit, KEY, expected_split="eval")


def test_promotion_rejects_dense_audit_not_bound_by_world_selection() -> None:
    candidate, artifacts = _candidate()
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)
    digest = candidate_sha256(candidate)
    receipt = attach_attestation(
        {
            "schema_version": "longworld-release-world-selection-v1",
            "selected_candidate_sha256": [digest],
            "split_by_world": {candidate["world_id"]: "train"},
            "audit_sha256_by_candidate": {digest: "0" * 64},
        },
        KEY,
        purpose="release_world_selection",
    )

    with pytest.raises(PromotionError, match="world selection"):
        promote_candidate(
            candidate,
            audit,
            KEY,
            release_selection_receipt=receipt,
        )


@pytest.mark.parametrize("field", ["split_strategy", "holdout"])
def test_dense_audit_rejects_forged_split_group_metadata(field: str) -> None:
    candidate, artifacts = _candidate()
    candidate["split_strategy"] = "world"
    candidate["holdout"] = {
        "strategy": "world",
        "group_id": hashlib.sha256(
            f"world|{candidate['world_id']}".encode()
        ).hexdigest()[:16],
    }
    if field == "split_strategy":
        candidate[field] = "topology"
    else:
        candidate[field]["group_id"] = "0" * 16
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="split strategy|holdout"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_dense_audit_rejects_forged_replayed_evidence_role() -> None:
    candidate, artifacts = _candidate()
    essential_id = candidate["essential_artifact_ids"][0]
    classification = next(
        item
        for item in candidate["artifact_classification"]
        if item["artifact_id"] == essential_id
    )
    classification["evidence_role"] = "structural_hard_negative"
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="evidence role"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


@pytest.mark.parametrize("forged_role", ["causal_gold", "causal_supporting"])
def test_dense_audit_rejects_causal_role_on_nonproof_artifact(
    forged_role: str,
) -> None:
    candidate, artifacts = _candidate()
    distractor = next(
        item
        for item in candidate["artifact_classification"]
        if item["evidence_role"] == "structural_hard_negative"
    )
    distractor["evidence_role"] = forged_role
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="evidence role"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_dense_audit_rejects_window_artifact_not_in_replayed_view() -> None:
    candidate, artifacts = _candidate()
    candidate["window_artifact_ids"] = ["forged-artifact-id"]
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="window artifact ids"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_promotion_recomputes_window_artifacts_instead_of_inheriting_subset() -> None:
    candidate, artifacts = _candidate()
    candidate["window_artifact_ids"] = [artifacts[-1].artifact_id]
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    audit = create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)

    promoted = promote_candidate(candidate, audit, KEY)

    assert promoted["window_artifact_ids"] != candidate["window_artifact_ids"]
    assert promoted["window_artifact_ids"][0] == artifacts[0].artifact_id


def test_dense_audit_rejects_fake_top_level_real_source_summaries() -> None:
    candidate, artifacts = _candidate()
    candidate["source_origins"] = ["real_public"]
    candidate["workflow_kinds"] = ["hybrid_causal"]
    candidate["real_source_family_ids"] = ["github.com/fake/repository"]
    candidate["source_relation_id"] = "fake-relation"
    candidate["source_relation_edges"] = [
        {"parent_record_id": "fake", "child_record_id": "fake"}
    ]
    candidate["real_source_verified"] = True
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )

    with pytest.raises(PromotionError, match="real source verification"):
        create_dense_audit(candidate, _ranking(candidate, artifacts), KEY, k=3)


def test_real_candidate_replays_from_exact_hash_bound_episode_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())
    bundle_path = _episode_bundle(tmp_path)
    candidate, artifacts = _real_candidate(bundle_path)
    ranking = _ranking(candidate, artifacts)

    audit = create_dense_audit(
        candidate, ranking, KEY, k=3, episode_bundle_path=bundle_path
    )
    promoted = promote_candidate(candidate, audit, KEY, episode_bundle_path=bundle_path)

    binding = candidate["episode_replay_bundle"]
    assert audit["episode_replay_bundle"] == binding
    assert promoted["promotion"]["episode_replay_bundle"] == binding
    assert promoted["answer"] == "parser-core@2.4.1 -> v2.4.1"
    assert promoted["real_source_verified"] is True
    assert promoted["source_family_ids"] == ["github.com/example/parser"]
    assert promoted["real_source_family_ids"] == ["github.com/example/parser"]
    assert promoted["real_source_workflow_ids"]
    assert promoted["source_relation_edges"]
    assert promoted["context_source_relation_count"] > 0
    assert sft_row_errors(promoted, attestation_key=KEY) == []


def test_real_replay_caches_only_materialization_and_revalidates_source_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import longworld.core.promotion as promotion_module

    bundle_path = _episode_bundle(tmp_path)
    candidate, artifacts = _real_candidate(bundle_path)
    ranking = _ranking(candidate, artifacts)
    materialize_calls = 0
    load_calls = 0
    actual_materialize = promotion_module.materialize
    actual_loader = promotion_module.load_episode_replay_bundle
    promotion_module._REAL_REPLAY_MATERIALIZATION_CACHE.clear()

    def counting_materialize(*args, **kwargs):
        nonlocal materialize_calls
        materialize_calls += 1
        return actual_materialize(*args, **kwargs)

    def counting_loader(*args, **kwargs):
        nonlocal load_calls
        load_calls += 1
        return actual_loader(*args, **kwargs)

    monkeypatch.setattr(promotion_module, "materialize", counting_materialize)
    monkeypatch.setattr(promotion_module, "load_episode_replay_bundle", counting_loader)

    audit = create_dense_audit(
        candidate, ranking, KEY, k=3, episode_bundle_path=bundle_path
    )
    promote_candidate(candidate, audit, KEY, episode_bundle_path=bundle_path)

    assert load_calls == 2
    assert materialize_calls == 1


def test_real_candidate_rejects_missing_or_mismatched_episode_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())
    bundle_path = _episode_bundle(tmp_path)
    candidate, artifacts = _real_candidate(bundle_path)
    ranking = _ranking(candidate, artifacts)

    with pytest.raises(PromotionError, match="episode_replay_bundle"):
        create_dense_audit(candidate, ranking, KEY, k=3)

    candidate["episode_replay_bundle"]["sha256"] = "0" * 64
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    with pytest.raises(PromotionError, match="digest"):
        create_dense_audit(
            candidate,
            _ranking(candidate, artifacts),
            KEY,
            k=3,
            episode_bundle_path=bundle_path,
        )


def test_real_jsonl_cli_threads_episode_sidecar_through_both_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings, promote_rows

    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())
    bundle_path = _episode_bundle(tmp_path)
    candidate, artifacts = _real_candidate(bundle_path)
    candidates_path = tmp_path / "real-candidates.jsonl"
    rankings_path = tmp_path / "real-rankings.jsonl"
    audits_path = tmp_path / "real-audits.jsonl"
    output_path = tmp_path / "real-train-ready.jsonl"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    rankings_path.write_text(
        json.dumps(_ranking(candidate, artifacts)) + "\n", encoding="utf-8"
    )

    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            audits_path,
            k=3,
            episode_bundle_path=bundle_path,
        )
        == 1
    )
    assert (
        promote_rows(
            candidates_path,
            audits_path,
            output_path,
            episode_bundle_path=bundle_path,
        )
        == 1
    )
    promoted = json.loads(output_path.read_text(encoding="utf-8"))
    assert (
        promoted["promotion"]["episode_replay_bundle"]
        == candidate["episode_replay_bundle"]
    )


def test_real_jsonl_cli_registry_matches_explicit_replay_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings, promote_rows

    bundle_path = _episode_bundle(tmp_path)
    candidate, artifacts = _real_candidate(bundle_path)
    candidates_path = tmp_path / "real-candidates.jsonl"
    rankings_path = tmp_path / "real-rankings.jsonl"
    explicit_audits = tmp_path / "explicit-audits.jsonl"
    registry_audits = tmp_path / "registry-audits.jsonl"
    explicit_rows = tmp_path / "explicit-rows.jsonl"
    registry_rows = tmp_path / "registry-rows.jsonl"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    rankings_path.write_text(
        json.dumps(_ranking(candidate, artifacts)) + "\n", encoding="utf-8"
    )
    registry_path = tmp_path / "replay-registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v1",
                "episode_replay_bundles": {
                    candidate["episode_replay_bundle"]["sha256"]: bundle_path.name
                },
                "source_workflow_bundles": {},
            }
        ),
        encoding="utf-8",
    )

    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            explicit_audits,
            k=3,
            episode_bundle_path=bundle_path,
        )
        == 1
    )
    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            registry_audits,
            k=3,
            replay_registry_path=registry_path,
        )
        == 1
    )
    assert explicit_audits.read_bytes() == registry_audits.read_bytes()

    assert (
        promote_rows(
            candidates_path,
            explicit_audits,
            explicit_rows,
            episode_bundle_path=bundle_path,
        )
        == 1
    )
    assert (
        promote_rows(
            candidates_path,
            registry_audits,
            registry_rows,
            replay_registry_path=registry_path,
        )
        == 1
    )
    assert explicit_rows.read_bytes() == registry_rows.read_bytes()


def test_source_jsonl_cli_registry_matches_explicit_replay_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings, promote_rows

    _configure_probe_role_keys(monkeypatch)
    bundle_path, loaded = _source_workflow_bundle(tmp_path)
    candidate, artifacts = _source_candidate(loaded)
    candidates_path = tmp_path / "source-candidates.jsonl"
    rankings_path = tmp_path / "source-rankings.jsonl"
    explicit_audits = tmp_path / "source-explicit-audits.jsonl"
    registry_audits = tmp_path / "source-registry-audits.jsonl"
    explicit_rows = tmp_path / "source-explicit-rows.jsonl"
    registry_rows = tmp_path / "source-registry-rows.jsonl"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    rankings_path.write_text(
        json.dumps(_ranking(candidate, artifacts)) + "\n", encoding="utf-8"
    )
    registry_path = tmp_path / "source-replay-registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v1",
                "episode_replay_bundles": {},
                "source_workflow_bundles": {
                    candidate["source_workflow_bundle"]["sha256"]: bundle_path.name
                },
            }
        ),
        encoding="utf-8",
    )

    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            explicit_audits,
            k=3,
            source_bundle_path=bundle_path,
        )
        == 1
    )
    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            registry_audits,
            k=3,
            replay_registry_path=registry_path,
        )
        == 1
    )
    assert explicit_audits.read_bytes() == registry_audits.read_bytes()

    assert (
        promote_rows(
            candidates_path,
            explicit_audits,
            explicit_rows,
            source_bundle_path=bundle_path,
        )
        == 1
    )
    assert (
        promote_rows(
            candidates_path,
            registry_audits,
            registry_rows,
            replay_registry_path=registry_path,
        )
        == 1
    )
    assert explicit_rows.read_bytes() == registry_rows.read_bytes()
    promoted = json.loads(registry_rows.read_text(encoding="utf-8"))
    assert (
        promoted["promotion"]["source_workflow_bundle"]
        == candidate["source_workflow_bundle"]
    )


def test_mixed_replay_registry_is_byte_deterministic_across_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings, promote_rows

    _configure_probe_role_keys(monkeypatch)
    episode_path = _episode_bundle(tmp_path)
    episode_candidate, episode_artifacts = _real_candidate(episode_path)
    source_path, loaded = _source_workflow_bundle(tmp_path)
    source_candidate, source_artifacts = _source_candidate(loaded)
    candidates = [source_candidate, episode_candidate]
    rankings = [
        _ranking(source_candidate, source_artifacts),
        _ranking(episode_candidate, episode_artifacts),
    ]
    candidates_path = tmp_path / "mixed-candidates.jsonl"
    rankings_path = tmp_path / "mixed-rankings.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
    )
    rankings_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rankings), encoding="utf-8"
    )
    registry_path = tmp_path / "mixed-replay-registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v1",
                "episode_replay_bundles": {
                    episode_candidate["episode_replay_bundle"]["sha256"]: (
                        episode_path.name
                    )
                },
                "source_workflow_bundles": {
                    source_candidate["source_workflow_bundle"]["sha256"]: (
                        source_path.name
                    )
                },
            }
        ),
        encoding="utf-8",
    )

    serial_audits = tmp_path / "mixed-serial-audits.jsonl"
    parallel_audits = tmp_path / "mixed-parallel-audits.jsonl"
    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            serial_audits,
            k=3,
            workers=1,
            replay_registry_path=registry_path,
        )
        == 2
    )
    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            parallel_audits,
            k=3,
            workers=2,
            replay_registry_path=registry_path,
        )
        == 2
    )
    assert serial_audits.read_bytes() == parallel_audits.read_bytes()

    serial_rows = tmp_path / "mixed-serial-rows.jsonl"
    parallel_rows = tmp_path / "mixed-parallel-rows.jsonl"
    assert (
        promote_rows(
            candidates_path,
            serial_audits,
            serial_rows,
            workers=1,
            replay_registry_path=registry_path,
        )
        == 2
    )
    assert (
        promote_rows(
            candidates_path,
            parallel_audits,
            parallel_rows,
            workers=2,
            replay_registry_path=registry_path,
        )
        == 2
    )
    assert serial_rows.read_bytes() == parallel_rows.read_bytes()


@pytest.mark.parametrize(
    ("binding_field", "forged_value"),
    (("sha256", "0" * 64), ("adapter_revision", "sourceworkflow@2")),
)
def test_source_replay_registry_fails_closed_on_binding_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding_field: str,
    forged_value: str,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    _configure_probe_role_keys(monkeypatch)
    bundle_path, loaded = _source_workflow_bundle(tmp_path)
    candidate, artifacts = _source_candidate(loaded)
    candidate["source_workflow_bundle"][binding_field] = forged_value
    candidate = attach_attestation(
        candidate, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    candidates_path = tmp_path / f"forged-{binding_field}-candidates.jsonl"
    rankings_path = tmp_path / f"forged-{binding_field}-rankings.jsonl"
    output_path = tmp_path / f"forged-{binding_field}-must-not-exist.jsonl"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    rankings_path.write_text(
        json.dumps(_ranking(candidate, artifacts)) + "\n", encoding="utf-8"
    )
    registry_path = tmp_path / f"forged-{binding_field}-registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v1",
                "episode_replay_bundles": {},
                "source_workflow_bundles": {
                    candidate["source_workflow_bundle"]["sha256"]: bundle_path.name
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PromotionError, match="bundle digest does not match"):
        audit_rankings(
            candidates_path,
            rankings_path,
            output_path,
            k=3,
            replay_registry_path=registry_path,
        )
    assert not output_path.exists()


def test_real_jsonl_cli_registry_fails_closed_on_missing_bundle_digest(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    bundle_path = _episode_bundle(tmp_path)
    candidate, artifacts = _real_candidate(bundle_path)
    candidates_path = tmp_path / "real-candidates.jsonl"
    rankings_path = tmp_path / "real-rankings.jsonl"
    registry_path = tmp_path / "empty-replay-registry.json"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    rankings_path.write_text(
        json.dumps(_ranking(candidate, artifacts)) + "\n", encoding="utf-8"
    )
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v1",
                "episode_replay_bundles": {},
                "source_workflow_bundles": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PromotionError, match="missing from registry"):
        audit_rankings(
            candidates_path,
            rankings_path,
            tmp_path / "must-not-exist.jsonl",
            k=3,
            replay_registry_path=registry_path,
        )


def test_replay_registry_routes_source_and_episode_candidates_independently(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import _candidate_replay_paths, _load_replay_registry

    episode_digest = "a" * 64
    source_digest = "b" * 64
    episode_path = tmp_path / "episode.json"
    source_path = tmp_path / "source.json"
    episode_path.write_text("{}", encoding="utf-8")
    source_path.write_text("{}", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v1",
                "episode_replay_bundles": {episode_digest: episode_path.name},
                "source_workflow_bundles": {source_digest: source_path.name},
            }
        ),
        encoding="utf-8",
    )
    registry = _load_replay_registry(registry_path)

    assert _candidate_replay_paths(
        {"episode_replay_bundle": {"sha256": episode_digest}},
        None,
        None,
        registry,
    ) == (episode_path.absolute(), None)
    assert _candidate_replay_paths(
        {"source_workflow_bundle": {"sha256": source_digest}},
        None,
        None,
        registry,
    ) == (None, source_path.absolute())
    with pytest.raises(PromotionError, match="cannot bind two"):
        _candidate_replay_paths(
            {
                "episode_replay_bundle": {"sha256": episode_digest},
                "source_workflow_bundle": {"sha256": source_digest},
            },
            None,
            None,
            registry,
        )


def test_promote_rows_writes_an_empty_split_only_when_selection_quota_is_zero(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import promote_rows

    candidates_path = tmp_path / "eval-candidates.jsonl"
    audits_path = tmp_path / "eval-audits.jsonl"
    output_path = tmp_path / "eval.jsonl"
    candidates_path.write_text("", encoding="utf-8")
    audits_path.write_text("", encoding="utf-8")
    profile_id = "p7-github-source-slice-1-v1"
    profile = release_profile(profile_id)
    selection = attach_attestation(
        {
            "schema_version": RELEASE_SELECTION_SCHEMA,
            "release_profile_id": profile_id,
            "release_profile_sha256": release_profile_sha256(profile_id),
            "split_strategy": profile.split_strategy,
            "n_selected_worlds": 1,
            "split_by_world": {"code-world": "train"},
        },
        KEY,
        purpose=RELEASE_SELECTION_PURPOSE,
    )
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selection), encoding="utf-8")

    assert (
        promote_rows(
            candidates_path,
            audits_path,
            output_path,
            expected_split="eval",
            release_selection_path=selection_path,
        )
        == 0
    )
    assert output_path.read_bytes() == b""

    with pytest.raises(ValueError, match="selected split is not empty"):
        promote_rows(
            candidates_path,
            audits_path,
            tmp_path / "train.jsonl",
            expected_split="train",
            release_selection_path=selection_path,
        )


def test_real_jsonl_cli_rejects_registry_with_single_bundle_path(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    registry_path = tmp_path / "replay-registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.replay-path-registry.v1",
                "episode_replay_bundles": {},
                "source_workflow_bundles": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot be combined"):
        audit_rankings(
            tmp_path / "unused-candidates.jsonl",
            tmp_path / "unused-rankings.jsonl",
            tmp_path / "must-not-exist.jsonl",
            k=3,
            episode_bundle_path=tmp_path / "unused-bundle.json",
            replay_registry_path=registry_path,
        )


def test_jsonl_two_stage_cli_is_atomic_and_requires_exact_receipt_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings, promote_rows

    candidate, artifacts = _candidate()
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    audits_path = tmp_path / "audits.jsonl"
    output_path = tmp_path / "train_ready.jsonl"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    rankings_path.write_text(
        json.dumps(_ranking(candidate, artifacts)) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())

    assert audit_rankings(candidates_path, rankings_path, audits_path, k=3) == 1
    assert promote_rows(candidates_path, audits_path, output_path) == 1
    promoted = json.loads(output_path.read_text(encoding="utf-8"))
    assert sft_row_errors(promoted, attestation_key=KEY) == []

    rankings_path.write_text("", encoding="utf-8")
    untouched = tmp_path / "must-not-exist.jsonl"
    with pytest.raises(ValueError, match="coverage mismatch"):
        audit_rankings(candidates_path, rankings_path, untouched, k=3)
    assert not untouched.exists()


def test_jsonl_batch_accepts_complete_dossier_twins_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    full, full_artifacts = _candidate("full", "release_eligibility")
    counterfactual, cf_artifacts = _candidate("cf", "release_eligibility")
    assert full["query_id"] == counterfactual["query_id"]
    candidates = [full, counterfactual]
    rankings = [
        _ranking(full, full_artifacts),
        _ranking(counterfactual, cf_artifacts),
    ]
    candidates_path = tmp_path / "siblings.jsonl"
    rankings_path = tmp_path / "sibling-rankings.jsonl"
    audits_path = tmp_path / "sibling-audits.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
    )
    rankings_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rankings), encoding="utf-8"
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())

    assert audit_rankings(candidates_path, rankings_path, audits_path, k=3) == 2
    assert len(audits_path.read_text(encoding="utf-8").splitlines()) == 2


def test_dense_audit_filter_writes_accepted_candidates_and_explicit_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    accepted, accepted_artifacts = _candidate(seed=1)
    rejected, rejected_artifacts = _candidate(seed=2)
    rejected["verification"]["surface_match"] = False
    rejected = attach_attestation(rejected, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    audits_path = tmp_path / "audits.jsonl"
    accepted_path = tmp_path / "accepted.jsonl"
    rejects_path = tmp_path / "rejects.jsonl"
    candidates_path.write_text(
        json.dumps(accepted) + "\n" + json.dumps(rejected) + "\n", encoding="utf-8"
    )
    rankings_path.write_text(
        json.dumps(_ranking(accepted, accepted_artifacts))
        + "\n"
        + json.dumps(_ranking(rejected, rejected_artifacts))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())

    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            audits_path,
            k=3,
            accepted_candidates_path=accepted_path,
            rejects_path=rejects_path,
        )
        == 1
    )
    assert len(accepted_path.read_text(encoding="utf-8").splitlines()) == 1
    reject = json.loads(rejects_path.read_text(encoding="utf-8"))
    assert reject["candidate_sha256"] == candidate_sha256(rejected)
    assert "surface gate" in reject["reason"]


def test_world_parallel_audit_and_promotion_are_byte_identical_to_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings, promote_rows

    first, first_artifacts = _candidate(seed=1)
    second, second_artifacts = _candidate(seed=2)
    candidates = [second, first]
    rankings = [
        _ranking(second, second_artifacts),
        _ranking(first, first_artifacts),
    ]
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
    )
    rankings_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rankings), encoding="utf-8"
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())

    serial_audits = tmp_path / "serial-audits.jsonl"
    parallel_audits = tmp_path / "parallel-audits.jsonl"
    assert (
        audit_rankings(candidates_path, rankings_path, serial_audits, k=3, workers=1)
        == 2
    )
    assert (
        audit_rankings(candidates_path, rankings_path, parallel_audits, k=3, workers=2)
        == 2
    )
    assert serial_audits.read_bytes() == parallel_audits.read_bytes()

    serial_rows = tmp_path / "serial-rows.jsonl"
    parallel_rows = tmp_path / "parallel-rows.jsonl"
    assert promote_rows(candidates_path, serial_audits, serial_rows, workers=1) == 2
    assert promote_rows(candidates_path, parallel_audits, parallel_rows, workers=2) == 2
    assert serial_rows.read_bytes() == parallel_rows.read_bytes()
    ordered_worlds = [
        json.loads(line)["world_id"]
        for line in serial_rows.read_text(encoding="utf-8").splitlines()
    ]
    assert ordered_worlds == sorted(ordered_worlds)


def test_world_parallel_filter_rejects_the_complete_world_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    accepted, accepted_artifacts = _candidate("full")
    rejected, rejected_artifacts = _candidate("cf")
    rejected["verification"]["surface_match"] = False
    rejected = attach_attestation(rejected, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
    candidates = [accepted, rejected]
    rankings = [
        _ranking(accepted, accepted_artifacts),
        _ranking(rejected, rejected_artifacts),
    ]
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    audits_path = tmp_path / "audits.jsonl"
    accepted_path = tmp_path / "accepted.jsonl"
    rejects_path = tmp_path / "rejects.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
    )
    rankings_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rankings), encoding="utf-8"
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())

    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            audits_path,
            k=3,
            accepted_candidates_path=accepted_path,
            rejects_path=rejects_path,
            workers=2,
        )
        == 0
    )
    assert audits_path.read_bytes() == b""
    assert accepted_path.read_bytes() == b""
    rejects = [
        json.loads(line)
        for line in rejects_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["candidate_sha256"] for row in rejects} == {
        candidate_sha256(row) for row in candidates
    }
    assert all(row["world_id"] == accepted["world_id"] for row in rejects)
    assert any("world atomic rejection" in row["reason"] for row in rejects)


def test_world_parallel_filter_outputs_match_serial_for_mixed_worlds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    accepted, accepted_artifacts = _candidate(seed=1)
    rejected, rejected_artifacts = _candidate(seed=2)
    rejected["verification"]["surface_match"] = False
    rejected = attach_attestation(rejected, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    candidates_path.write_text(
        json.dumps(rejected) + "\n" + json.dumps(accepted) + "\n", encoding="utf-8"
    )
    rankings_path.write_text(
        json.dumps(_ranking(rejected, rejected_artifacts))
        + "\n"
        + json.dumps(_ranking(accepted, accepted_artifacts))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())

    outputs: dict[int, tuple[Path, Path, Path]] = {}
    for workers in (1, 2):
        paths = (
            tmp_path / f"audits-{workers}.jsonl",
            tmp_path / f"accepted-{workers}.jsonl",
            tmp_path / f"rejects-{workers}.jsonl",
        )
        assert (
            audit_rankings(
                candidates_path,
                rankings_path,
                paths[0],
                k=3,
                accepted_candidates_path=paths[1],
                rejects_path=paths[2],
                workers=workers,
            )
            == 1
        )
        outputs[workers] = paths

    for serial, parallel in zip(outputs[1], outputs[2], strict=True):
        assert serial.read_bytes() == parallel.read_bytes()


def test_world_parallel_failure_propagates_without_publishing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    accepted, accepted_artifacts = _candidate(seed=1)
    rejected, rejected_artifacts = _candidate(seed=2)
    rejected["verification"]["surface_match"] = False
    rejected = attach_attestation(rejected, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    output_path = tmp_path / "must-not-exist.jsonl"
    candidates_path.write_text(
        json.dumps(accepted) + "\n" + json.dumps(rejected) + "\n",
        encoding="utf-8",
    )
    rankings_path.write_text(
        json.dumps(_ranking(accepted, accepted_artifacts))
        + "\n"
        + json.dumps(_ranking(rejected, rejected_artifacts))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())

    with pytest.raises(PromotionError, match="surface gate"):
        audit_rankings(candidates_path, rankings_path, output_path, k=3, workers=2)
    assert not output_path.exists()


def _with_world_id(candidate: dict, world_id: str) -> dict:
    updated = {
        **{key: value for key, value in candidate.items() if key != "attestation"},
        "world_id": world_id,
    }
    return attach_attestation(updated, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)


def test_candidate_structural_preflight_filters_only_worlds_missing_exact_bands() -> (
    None
):
    complete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    incomplete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "64k"))
    complete = [_with_world_id(row, "complete-world") for row in complete]
    incomplete = [_with_world_id(row, "incomplete-world") for row in incomplete]

    accepted, rejects = candidate_structural_preflight(
        incomplete + complete,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert {row["world_id"] for row in accepted} == {"complete-world"}
    assert {row["world_id"] for row in rejects} == {"incomplete-world"}
    assert {row["missing_exact_length_buckets"] for row in rejects} == {("32k",)}
    assert all(
        row["reason"] == "missing required exact length buckets: 32k" for row in rejects
    )


def test_candidate_structural_preflight_does_not_replace_authoritative_selection() -> (
    None
):
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))

    accepted, rejects = candidate_structural_preflight(
        candidates,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == candidates
    assert rejects == []
    with pytest.raises(PromotionError, match="insufficient fully audited worlds"):
        select_release_worlds(
            accepted,
            [],
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_task_growth_waits_for_authoritative_post_replay_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    task_candidates = []
    for candidate in candidates:
        unsigned = {
            key: value for key, value in candidate.items() if key != "attestation"
        }
        unsigned["task_replay_sidecar"] = {
            "adapter_id": "cyber.kev_history.v1",
            "adapter_revision": "longworld.kev-catalog-history-replay.v1",
            "sidecar_schema_version": "longworld.task-replay-sidecar.v1",
            "sha256": "f" * 64,
        }
        unsigned["domain"] = "cyber"
        unsigned["view"] = "ordered_artifact_view"
        unsigned["composition_method"] = "causal_timeline"
        unsigned["strict_replay_revision"] = "longworld.kev-catalog-history-replay.v1"
        unsigned["domain_history_replay_manifest"] = {
            "replay_revision": "longworld.kev-catalog-history-replay.v1"
        }
        unsigned["context"] = (
            f"{unsigned['context']}\nlength-band:{unsigned['length_bucket']}"
        )
        task_candidates.append(
            attach_attestation(unsigned, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )
    observed: list[list[dict[str, Any]]] = []

    def capture_growth_inputs(
        rows: list[dict[str, Any]], _profile: Any
    ) -> dict[str, tuple[str, ...]]:
        observed.append(rows)
        return {}

    monkeypatch.setattr(
        promotion_module,
        "_cumulative_history_violations_by_world",
        capture_growth_inputs,
    )

    accepted, rejects = candidate_structural_preflight(
        task_candidates,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == task_candidates
    assert rejects == []
    assert observed == [[]]


def test_task_growth_preflight_rejects_malformed_sidecar_marker() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    malformed = []
    for candidate in candidates:
        unsigned = {
            key: value for key, value in candidate.items() if key != "attestation"
        }
        unsigned["task_replay_sidecar"] = "skip-growth"
        malformed.append(
            attach_attestation(unsigned, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    with pytest.raises(PromotionError, match="task replay sidecar is invalid"):
        candidate_structural_preflight(
            malformed,
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
        )


def test_task_growth_preflight_rejects_sidecar_for_wrong_domain() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    mismatched = []
    for candidate in candidates:
        unsigned = {
            key: value for key, value in candidate.items() if key != "attestation"
        }
        unsigned["task_replay_sidecar"] = {
            "adapter_id": "cyber.kev_history.v1",
            "adapter_revision": "longworld.kev-catalog-history-replay.v1",
            "sidecar_schema_version": "longworld.task-replay-sidecar.v1",
            "sha256": "f" * 64,
        }
        mismatched.append(
            attach_attestation(unsigned, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    with pytest.raises(PromotionError, match="task replay sidecar is invalid"):
        candidate_structural_preflight(
            mismatched,
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
        )


def _duplicate_task_content_identity_candidates() -> tuple[dict, dict]:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k",))
    unsigned = {
        key: value for key, value in candidates[0].items() if key != "attestation"
    }
    unsigned.update(
        {
            "task_replay_sidecar": {
                "adapter_id": "cyber.kev_history.v1",
                "adapter_revision": "longworld.kev-catalog-history-replay.v1",
                "sidecar_schema_version": "longworld.task-replay-sidecar.v1",
                "sha256": "f" * 64,
            },
            "domain": "cyber",
            "view": "ordered_artifact_view",
            "composition_method": "causal_timeline",
            "strict_replay_revision": "longworld.kev-catalog-history-replay.v1",
            "domain_history_replay_manifest": {
                "replay_revision": "longworld.kev-catalog-history-replay.v1"
            },
        }
    )
    first = attach_attestation(unsigned, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
    variant = attach_attestation(
        {**unsigned, "generation_integration": "metadata-only-variant"},
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    return first, variant


def test_task_growth_preflight_rejects_duplicate_content_identity() -> None:
    first, variant = _duplicate_task_content_identity_candidates()

    with pytest.raises(PromotionError, match="task content identity is duplicated"):
        candidate_structural_preflight(
            [first, variant],
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
        )


def test_task_growth_preflight_rejects_cross_world_training_clone() -> None:
    first, _ = _duplicate_task_content_identity_candidates()
    unsigned = {key: value for key, value in first.items() if key != "attestation"}
    clone = attach_attestation(
        {
            **unsigned,
            "world_id": "metadata-clone-world",
            "query_id": "metadata-clone-query",
        },
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )

    with pytest.raises(PromotionError, match="task training content is duplicated"):
        candidate_structural_preflight(
            [first, clone],
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
        )


def test_world_selection_rejects_duplicate_task_content_identity() -> None:
    first, variant = _duplicate_task_content_identity_candidates()

    with pytest.raises(PromotionError, match="task content identity is duplicated"):
        select_release_worlds(
            [first, variant],
            [],
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_ranking_audit_without_profile_rejects_task_clone_and_prompt_conflict(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    first, _ = _duplicate_task_content_identity_candidates()
    unsigned = {key: value for key, value in first.items() if key != "attestation"}
    clone = attach_attestation(
        {**unsigned, "world_id": "clone-world", "query_id": "clone-query"},
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    conflict = attach_attestation(
        {**unsigned, "world_id": "conflict-world", "answer": "changed answer"},
        KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    )
    rankings_path = tmp_path / "rankings.jsonl"
    rankings_path.write_text("", encoding="utf-8")

    for label, rows, match in (
        ("clone", [first, clone], "training content is duplicated"),
        ("conflict", [first, conflict], "prompt has conflicting answers"),
    ):
        candidates_path = tmp_path / f"{label}-candidates.jsonl"
        output_path = tmp_path / f"{label}-audits.jsonl"
        candidates_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        with pytest.raises(PromotionError, match=match):
            audit_rankings(candidates_path, rankings_path, output_path, k=3)
        assert not output_path.exists()


def test_train_ready_report_rejects_duplicate_task_content_identity() -> None:
    first, variant = _duplicate_task_content_identity_candidates()
    candidates = [first, variant]
    candidate_report = attach_attestation(
        {
            "schema_version": first["schema_version"],
            "data_product": first["data_product"],
            "data_stage": "candidate",
            "release_profile_id": "p12-wiki-source-slice-1-v1",
            "release_profile_sha256": release_profile_sha256(
                "p12-wiki-source-slice-1-v1"
            ),
            "n_rows": len(candidates),
            "candidate_row_set_sha256": promoted_row_set_sha256(candidates),
            "target_promoted_worlds": 1,
        },
        KEY,
        purpose="quality_report",
    )

    with pytest.raises(PromotionError, match="task content identity is duplicated"):
        create_train_ready_report(candidate_report, candidates, [], KEY)


def test_train_ready_report_rejects_resigned_task_training_content_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, _ = _duplicate_task_content_identity_candidates()
    candidate_report = attach_attestation(
        {
            "schema_version": candidate["schema_version"],
            "data_product": candidate["data_product"],
            "data_stage": "candidate",
            "release_profile_id": "p12-wiki-source-slice-1-v1",
            "release_profile_sha256": release_profile_sha256(
                "p12-wiki-source-slice-1-v1"
            ),
            "n_rows": 1,
            "candidate_row_set_sha256": promoted_row_set_sha256([candidate]),
            "target_promoted_worlds": 1,
        },
        KEY,
        purpose="quality_report",
    )
    unsigned = {key: value for key, value in candidate.items() if key != "attestation"}
    unsigned.update(
        {
            "context": f"{candidate['context']}\nresigned mutation",
            "data_stage": "train_ready",
            "train_ready": True,
            "promotion": {
                "candidate_sha256": candidate_sha256(candidate),
                "task_replay_sidecar": candidate["task_replay_sidecar"],
                "task_candidate_content_commitment": (
                    task_candidate_content_commitment(candidate)
                ),
            },
        }
    )
    mutated = attach_attestation(unsigned, KEY, purpose="sft_row")
    monkeypatch.setattr(promotion_module, "sft_row_errors", lambda *_a, **_k: [])

    with pytest.raises(PromotionError, match="differs from candidate"):
        create_train_ready_report(candidate_report, [candidate], [mutated], KEY)


def _task_report_semantic_inputs() -> tuple[dict, dict, dict, dict]:
    candidate, _ = _duplicate_task_content_identity_candidates()
    candidate_digest = candidate_sha256(candidate)
    source_relation_edges = [
        {
            "parent_record_id": "source-a",
            "child_record_id": "source-b",
            "relation_provenance": "authentic_source",
        }
    ]
    source_relation_id = hashlib.sha256(
        json.dumps(
            source_relation_edges,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:20]
    candidate_report = attach_attestation(
        {
            "schema_version": candidate["schema_version"],
            "data_product": candidate["data_product"],
            "data_stage": "candidate",
            "release_profile_id": "p12-wiki-source-slice-1-v1",
            "release_profile_sha256": release_profile_sha256(
                "p12-wiki-source-slice-1-v1"
            ),
            "n_rows": 1,
            "candidate_row_set_sha256": promoted_row_set_sha256([candidate]),
            "target_promoted_worlds": 1,
        },
        KEY,
        purpose="quality_report",
    )
    verification_values = {field: True for field in Verification.model_fields}
    verification_values["candidate_mode"] = False
    unsigned = {key: value for key, value in candidate.items() if key != "attestation"}
    unsigned.update(
        {
            "data_stage": "train_ready",
            "train_ready": True,
            "split": "train",
            "semantic_growth_group_id": "task-report-semantic-history",
            "semantic_tokens": {
                "internal": 16_000,
                "event_bearing": 12_000,
                "proof_bearing": 4_000,
                "causal_supporting": 2_000,
                "generic_background": 0,
                "measurement_basis": "exact_replay_test",
            },
            "strict_support_event_count": 4,
            "graph": {
                "n_essential_events": 3,
                "n_essential_artifacts": 2,
                "proof_depth": 3,
                "hop_count": 2,
            },
            "authentic_source_relation_edges": source_relation_edges,
            "motif": "task-report-motif",
            "base_task_id": "task-report-base-task",
            "executable_proof_id": "task-report-executable-proof",
            "answer_program_id": "task-report-answer-program",
            "semantic_base_task_id": "task-report-semantic-base-task",
            "real_source_verified": True,
            "real_source_family_ids": ["task-report-source-family"],
            "real_source_workflow_ids": ["task-report-source-workflow"],
            "real_source_token_ratio": 0.75,
            "source_relation_edges": source_relation_edges,
            "source_relation_id": source_relation_id,
            "authentic_source_relation_id": source_relation_id,
            "hybrid_causal_edges": [],
            "context_source_relation_count": 1,
            "task_proof_receipt": {
                "schema_version": TASK_PROOF_RECEIPT_SCHEMA,
                "receipt_sha256": "b" * 64,
            },
            "verification": Verification(**verification_values).model_dump(),
            "view_verification": {
                "expected_answer": candidate["answer"],
                "strict_replay_answer": candidate["answer"],
                "essential_present": True,
                "semantic_text_grounded": True,
                "classification_ok": True,
                "global_proof_green": True,
                "production_eligible": False,
                "content_gate_eligible": True,
            },
            "promotion": {
                "candidate_sha256": candidate_digest,
                "dense_audit_sha256": "a" * 64,
                "task_replay_sidecar": candidate["task_replay_sidecar"],
                "task_candidate_content_commitment": (
                    task_candidate_content_commitment(candidate)
                ),
            },
        }
    )
    commitment = promotion_module.task_semantic_commitment_sha256_from_row(unsigned)
    receipt = attach_attestation(
        {
            "schema_version": RELEASE_SELECTION_SCHEMA,
            "release_profile_id": "p12-wiki-source-slice-1-v1",
            "release_profile_sha256": release_profile_sha256(
                "p12-wiki-source-slice-1-v1"
            ),
            "candidate_row_set_sha256": promoted_row_set_sha256([candidate]),
            "selected_candidate_sha256": [candidate_digest],
            "split_by_world": {candidate["world_id"]: "train"},
            "audit_sha256_by_candidate": {candidate_digest: "a" * 64},
            "task_semantic_commitment_sha256_by_candidate": {
                candidate_digest: commitment
            },
        },
        KEY,
        purpose=RELEASE_SELECTION_PURPOSE,
    )
    unsigned["promotion"].update(
        {
            "release_selection_sha256": serialized_row_sha256(receipt),
            "task_semantic_commitment_sha256": commitment,
        }
    )
    row = attach_attestation(unsigned, KEY, purpose="sft_row")
    return candidate, candidate_report, row, receipt


def test_task_train_ready_report_requires_release_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, candidate_report, row, _receipt = _task_report_semantic_inputs()
    monkeypatch.setattr(promotion_module, "sft_row_errors", lambda *_a, **_k: [])

    with pytest.raises(PromotionError, match="requires a valid release selection"):
        create_train_ready_report(candidate_report, [candidate], [row], KEY)


def test_task_train_ready_report_rejects_resigned_semantic_metadata_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, candidate_report, row, receipt = _task_report_semantic_inputs()
    monkeypatch.setattr(promotion_module, "sft_row_errors", lambda *_a, **_k: [])

    for field in (
        "semantic_tokens",
        "strict_support_event_count",
        "graph",
        "task_proof_receipt",
        "verification",
        "view_verification",
    ):
        unsigned = deepcopy(row)
        unsigned.pop("attestation")
        if field == "semantic_tokens":
            unsigned[field]["proof_bearing"] += 1
        elif field == "strict_support_event_count":
            unsigned[field] += 1
        elif field == "graph":
            unsigned[field]["proof_depth"] += 1
        elif field == "task_proof_receipt":
            unsigned[field]["resigned_mutation"] = True
        elif field == "verification":
            unsigned[field]["semantic_sufficient"] = False
        else:
            unsigned[field]["classification_ok"] = False
        mutated = attach_attestation(unsigned, KEY, purpose="sft_row")

        with pytest.raises(PromotionError, match="differ from release selection"):
            create_train_ready_report(
                candidate_report,
                [candidate],
                [mutated],
                KEY,
                release_selection_receipt=receipt,
            )


@pytest.mark.parametrize(
    "field",
    (
        "world_id",
        "domain",
        "length_bucket",
        "motif",
        "base_task_id",
        "executable_proof_id",
        "answer_program_id",
        "semantic_base_task_id",
        "real_source_verified",
        "real_source_family_ids",
        "real_source_workflow_ids",
        "real_source_token_ratio",
        "source_relation_edges",
        "source_relation_id",
        "authentic_source_relation_id",
        "hybrid_causal_edges",
        "context_source_relation_count",
    ),
)
def test_task_train_ready_report_rejects_resigned_quality_metadata_mutation(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    candidate, candidate_report, row, receipt = _task_report_semantic_inputs()
    monkeypatch.setattr(promotion_module, "sft_row_errors", lambda *_a, **_k: [])
    unsigned = deepcopy(row)
    unsigned.pop("attestation")
    if field == "real_source_verified":
        unsigned[field] = False
    elif field in {"real_source_family_ids", "real_source_workflow_ids"}:
        unsigned[field] = sorted([*unsigned[field], "resigned-mutation"])
    elif field == "source_relation_edges":
        unsigned[field] = [
            *unsigned[field],
            ["resigned-source", "resigned-target", "resigned-relation"],
        ]
        unsigned["source_relation_id"] = hashlib.sha256(
            json.dumps(
                unsigned[field],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:20]
        unsigned["context_source_relation_count"] = len(unsigned[field])
    elif field == "hybrid_causal_edges":
        unsigned[field] = [["resigned-source", "resigned-target", "resigned-relation"]]
    elif field == "real_source_token_ratio":
        unsigned[field] += 0.01
    elif field == "context_source_relation_count":
        unsigned[field] += 1
    else:
        unsigned[field] = f"{unsigned[field]}-resigned-mutation"
    mutated = attach_attestation(unsigned, KEY, purpose="sft_row")

    with pytest.raises(
        PromotionError,
        match=(
            "semantic commitment is invalid|differ from release selection|"
            "task release selection binding is invalid"
        ),
    ):
        create_train_ready_report(
            candidate_report,
            [candidate],
            [mutated],
            KEY,
            release_selection_receipt=receipt,
        )


def _source_bound_classifications(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    classifications = [dict(item) for item in candidate["artifact_classification"]]
    classifications[0]["evidence_role"] = "causal_supporting"
    return classifications


def test_candidate_structural_preflight_rejects_non_growing_real_history() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    resigned = []
    for candidate in candidates:
        payload = {
            **{key: value for key, value in candidate.items() if key != "attestation"},
            "semantic_growth_group_id": "one-real-history",
            "artifact_classification": _source_bound_classifications(candidate),
            "semantic_tokens": {
                "event_bearing": 12_000,
                "internal": 12_000,
                "generic_background": 0,
            },
            "strict_support_event_count": 8,
            "source_relation_edges": [
                {
                    "parent_record_id": "revision-0",
                    "child_record_id": "revision-1",
                    "relation_provenance": "authentic_source",
                }
            ],
            "authentic_source_relation_edges": [
                {
                    "parent_record_id": "revision-0",
                    "child_record_id": "revision-1",
                    "relation_provenance": "authentic_source",
                }
            ],
            "graph": {
                **dict(candidate.get("graph") or {}),
                "n_essential_events": 4,
                "proof_depth": 3,
            },
        }
        resigned.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    accepted, rejects = candidate_structural_preflight(
        resigned,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == []
    assert len(rejects) == 3
    assert all(
        row["reason"].startswith("invalid cumulative source history:")
        for row in rejects
    )
    violations = set(rejects[0]["cumulative_history_violations"])
    assert any("event_bearing_not_growing" in item for item in violations)
    assert any("strict_support_not_growing" in item for item in violations)
    assert any("essential_events_not_growing" in item for item in violations)
    assert any("authentic_relations_not_growing" in item for item in violations)
    assert any("proof_depth_not_growing" in item for item in violations)


def test_candidate_structural_preflight_requires_event_bearing_to_grow_independently() -> (
    None
):
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    event_tokens = {"16k": 12_000, "32k": 8_000, "64k": 4_000}
    internal_tokens = {"16k": 12_000, "32k": 16_000, "64k": 20_000}
    resigned = []
    for candidate in candidates:
        band = str(candidate["length_bucket"])
        payload = {
            **{key: value for key, value in candidate.items() if key != "attestation"},
            "semantic_tokens": {
                "event_bearing": event_tokens[band],
                "internal": internal_tokens[band],
                "generic_background": 0,
            },
        }
        resigned.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    accepted, rejects = candidate_structural_preflight(
        resigned,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == []
    assert any(
        "event_bearing_not_growing" in violation
        for violation in rejects[0]["cumulative_history_violations"]
    )


def test_candidate_structural_preflight_rejects_event_outside_internal_total() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    invalid = []
    for candidate in candidates:
        payload = {
            key: value for key, value in candidate.items() if key != "attestation"
        }
        internal = int(payload["semantic_tokens"]["internal"])
        payload["semantic_tokens"] = {
            **payload["semantic_tokens"],
            "event_bearing": internal + 1,
        }
        invalid.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    accepted, rejects = candidate_structural_preflight(
        invalid,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == []
    assert all(
        any(
            "invalid_growth_metrics" in violation
            for violation in reject["cumulative_history_violations"]
        )
        for reject in rejects
    )


def test_candidate_structural_preflight_does_not_treat_real_hard_negative_as_proof() -> (
    None
):
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    resigned = []
    for candidate in candidates:
        classifications = [
            {**item, "evidence_role": "real_hard_negative"}
            for item in candidate["artifact_classification"]
        ]
        payload = {
            **{key: value for key, value in candidate.items() if key != "attestation"},
            "artifact_classification": classifications,
            "semantic_tokens": {
                "event_bearing": 12_000,
                "internal": 12_000,
                "generic_background": 0,
            },
        }
        resigned.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    accepted, rejects = candidate_structural_preflight(
        resigned,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == resigned
    assert rejects == []


def test_real_hard_negative_world_cannot_satisfy_real_world_quota() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    hard_negatives = []
    for candidate in candidates:
        payload = {
            key: value for key, value in candidate.items() if key != "attestation"
        }
        payload["artifact_classification"] = [
            {**item, "evidence_role": "real_hard_negative"}
            for item in candidate["artifact_classification"]
        ]
        hard_negatives.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )
    audits = [_selection_audit(candidate) for candidate in hard_negatives]

    assert all(_candidate_has_verified_real_source(row) for row in hard_negatives)
    assert not any(_candidate_has_source_bound_proof(row) for row in hard_negatives)

    with pytest.raises(PromotionError, match="real workflow worlds"):
        select_release_worlds(
            hard_negatives,
            audits,
            "p12-wiki-source-slice-1-v1",
            candidate_attestation_key=KEY,
            audit_attestation_key=KEY,
        )


def test_candidate_structural_preflight_accepts_growing_real_history() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    scale = {"16k": 1, "32k": 2, "64k": 3}
    resigned = []
    for candidate in candidates:
        level = scale[str(candidate["length_bucket"])]
        relations = [
            {
                "parent_record_id": f"revision-{index}",
                "child_record_id": f"revision-{index + 1}",
                "relation_provenance": "authentic_source",
            }
            for index in range(level)
        ]
        payload = {
            **{key: value for key, value in candidate.items() if key != "attestation"},
            "semantic_growth_group_id": "one-real-history",
            "artifact_classification": _source_bound_classifications(candidate),
            "semantic_tokens": {
                "event_bearing": 10_000 * level,
                "internal": 12_000 * level,
                "generic_background": 0,
            },
            "strict_support_event_count": 4 * level,
            "source_relation_edges": relations,
            "authentic_source_relation_edges": relations,
            "graph": {
                **dict(candidate.get("graph") or {}),
                "n_essential_events": 2 * level,
                "proof_depth": level + 1,
            },
        }
        resigned.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    accepted, rejects = candidate_structural_preflight(
        resigned,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == resigned
    assert rejects == []


def test_candidate_structural_preflight_includes_real_source_derived_lower_band() -> (
    None
):
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    resigned = []
    for candidate in candidates:
        payload = {
            key: value for key, value in candidate.items() if key != "attestation"
        }
        payload["artifact_classification"] = _source_bound_classifications(candidate)
        if candidate["length_bucket"] == "16k":
            payload["artifact_classification"][0].update(
                source_origin="real_derived", workflow_kind="real_source_derived"
            )
        resigned.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    accepted, rejects = candidate_structural_preflight(
        resigned,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == resigned
    assert rejects == []


def test_candidate_structural_preflight_rejects_cross_group_band_stitching() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    resigned = []
    for candidate in candidates:
        payload = {
            **{key: value for key, value in candidate.items() if key != "attestation"},
            "semantic_growth_group_id": (
                "history-b" if candidate["length_bucket"] == "32k" else "history-a"
            ),
            "artifact_classification": _source_bound_classifications(candidate),
        }
        resigned.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )

    accepted, rejects = candidate_structural_preflight(
        resigned,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == []
    assert len(rejects) == 3
    assert any(
        "missing_buckets=32k" in violation
        for violation in rejects[0]["cumulative_history_violations"]
    )


def test_candidate_structural_preflight_requires_real_growth_identity() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    resigned = [
        attach_attestation(
            {
                key: value
                for key, value in candidate.items()
                if key not in {"attestation", "semantic_growth_group_id"}
            }
            | {"artifact_classification": _source_bound_classifications(candidate)},
            KEY,
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
        )
        for candidate in candidates
    ]

    accepted, rejects = candidate_structural_preflight(
        resigned,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == []
    assert len(rejects) == 3
    assert rejects[0]["cumulative_history_violations"] == (
        "missing_semantic_growth_group",
    )


def test_candidate_structural_preflight_requires_nested_real_relations() -> None:
    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    scale = {"16k": 1, "32k": 2, "64k": 3}
    resigned = []
    for candidate in candidates:
        level = scale[str(candidate["length_bucket"])]
        relations = [
            {
                "parent_record_id": f"band-{level}-revision-{index}",
                "child_record_id": f"band-{level}-revision-{index + 1}",
                "relation_provenance": "authentic_source",
            }
            for index in range(level)
        ]
        resigned.append(
            attach_attestation(
                {
                    **{
                        key: value
                        for key, value in candidate.items()
                        if key != "attestation"
                    },
                    "source_relation_edges": relations,
                    "authentic_source_relation_edges": relations,
                    "artifact_classification": _source_bound_classifications(candidate),
                },
                KEY,
                purpose=CANDIDATE_ATTESTATION_PURPOSE,
            )
        )

    accepted, rejects = candidate_structural_preflight(
        resigned,
        "p12-wiki-source-slice-1-v1",
        candidate_attestation_key=KEY,
    )

    assert accepted == []
    assert any(
        "authentic_relation_history_not_nested" in violation
        for violation in rejects[0]["cumulative_history_violations"]
    )


def test_candidate_preflight_cli_is_byte_deterministic_and_world_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import preflight_candidates

    complete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    incomplete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "64k"))
    candidates = [
        *[_with_world_id(row, "complete-world") for row in complete],
        *[_with_world_id(row, "incomplete-world") for row in incomplete],
    ]
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", KEY.decode())

    outputs: list[tuple[Path, Path]] = []
    for index, rows in enumerate((candidates, list(reversed(candidates)))):
        candidates_path = tmp_path / f"candidates-{index}.jsonl"
        accepted_path = tmp_path / f"accepted-{index}.jsonl"
        rejects_path = tmp_path / f"rejects-{index}.jsonl"
        candidates_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

        assert (
            preflight_candidates(
                candidates_path,
                accepted_path,
                rejects_path,
                release_profile_id="p12-wiki-source-slice-1-v1",
            )
            == 3
        )
        outputs.append((accepted_path, rejects_path))

    assert outputs[0][0].read_bytes() == outputs[1][0].read_bytes()
    assert outputs[0][1].read_bytes() == outputs[1][1].read_bytes()


@pytest.mark.parametrize("aliased_output", ("accepted", "rejects"))
def test_candidate_preflight_rejects_input_and_output_path_aliases(
    tmp_path: Path, aliased_output: str
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import preflight_candidates

    candidate, _ = _candidate()
    candidates_path = tmp_path / "candidates.jsonl"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    accepted_path = tmp_path / "accepted.jsonl"
    rejects_path = tmp_path / "rejects.jsonl"
    if aliased_output == "accepted":
        accepted_path = candidates_path.parent / "." / candidates_path.name
    else:
        rejects_path = accepted_path.parent / "." / accepted_path.name

    with pytest.raises(ValueError, match="path alias"):
        preflight_candidates(
            candidates_path,
            accepted_path,
            rejects_path,
            release_profile_id="p12-wiki-source-slice-1-v1",
        )


def test_jsonl_batch_writer_rejects_second_directory_before_first_publish(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import _write_jsonl_batch_atomic

    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    second.mkdir()

    with pytest.raises(ValueError, match="output target is a directory"):
        _write_jsonl_batch_atomic([(first, [{"row": 1}]), (second, [{"row": 2}])])

    assert not first.exists()


def test_jsonl_batch_writer_cleans_all_staging_on_second_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import promote_candidates as promote_script

    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    actual_fsync = promote_script.os.fsync
    calls = 0

    def fail_second_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second staging failure")
        actual_fsync(descriptor)

    monkeypatch.setattr(promote_script.os, "fsync", fail_second_fsync)

    with pytest.raises(OSError, match="second staging failure"):
        promote_script._write_jsonl_batch_atomic(
            [(first, [{"row": 1}]), (second, [{"row": 2}])]
        )

    assert not first.exists()
    assert not second.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_audit_preflight_skips_incomplete_world_before_strict_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import promote_candidates as promote_script

    complete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    incomplete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "64k"))
    complete = [_with_world_id(row, "complete-world") for row in complete]
    incomplete = [_with_world_id(row, "incomplete-world") for row in incomplete]
    candidates = incomplete + complete
    rankings = [
        {"candidate_sha256": candidate_sha256(candidate)} for candidate in complete
    ]
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    audits_path = tmp_path / "audits.jsonl"
    accepted_path = tmp_path / "accepted.jsonl"
    rejects_path = tmp_path / "rejects.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
    )
    rankings_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rankings), encoding="utf-8"
    )
    replayed_worlds: list[str] = []

    def fake_dense_audit(candidate: dict, _ranking: dict, **_kwargs) -> dict:
        replayed_worlds.append(str(candidate["world_id"]))
        return {"candidate_sha256": candidate_sha256(candidate)}

    monkeypatch.setattr(promote_script, "create_dense_audit", fake_dense_audit)

    assert (
        promote_script.audit_rankings(
            candidates_path,
            rankings_path,
            audits_path,
            k=3,
            accepted_candidates_path=accepted_path,
            rejects_path=rejects_path,
            release_profile_id="p12-wiki-source-slice-1-v1",
        )
        == 3
    )
    assert replayed_worlds == ["complete-world"] * 3
    assert {
        json.loads(line)["world_id"]
        for line in rejects_path.read_text(encoding="utf-8").splitlines()
    } == {"incomplete-world"}


def test_audit_preflight_rejects_rankings_for_structurally_rejected_worlds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import promote_candidates as promote_script

    complete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    incomplete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "64k"))
    complete = [_with_world_id(row, "complete-world") for row in complete]
    incomplete = [_with_world_id(row, "incomplete-world") for row in incomplete]
    candidates = incomplete + complete
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
    )
    rankings_path.write_text(
        "".join(
            json.dumps({"candidate_sha256": candidate_sha256(candidate)}) + "\n"
            for candidate in candidates
        ),
        encoding="utf-8",
    )

    def unexpected_replay(*_args, **_kwargs):
        raise AssertionError("strict replay must not run")

    monkeypatch.setattr(promote_script, "create_dense_audit", unexpected_replay)

    with pytest.raises(ValueError, match="ranking coverage mismatch.*extra="):
        promote_script.audit_rankings(
            candidates_path,
            rankings_path,
            tmp_path / "audits.jsonl",
            k=3,
            accepted_candidates_path=tmp_path / "accepted.jsonl",
            rejects_path=tmp_path / "rejects.jsonl",
            release_profile_id="p12-wiki-source-slice-1-v1",
        )


@pytest.mark.parametrize(
    ("first_output", "second_output"),
    (("audit", "accepted"), ("audit", "rejects"), ("accepted", "rejects")),
)
def test_audit_rejects_output_path_aliases(
    tmp_path: Path, first_output: str, second_output: str
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    candidate, artifacts = _candidate()
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    candidates_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    rankings_path.write_text(
        json.dumps(_ranking(candidate, artifacts)) + "\n", encoding="utf-8"
    )
    paths = {
        "audit": tmp_path / "audits.jsonl",
        "accepted": tmp_path / "accepted.jsonl",
        "rejects": tmp_path / "rejects.jsonl",
    }
    paths[second_output] = paths[first_output].parent / "." / paths[first_output].name

    with pytest.raises(ValueError, match="path alias"):
        audit_rankings(
            candidates_path,
            rankings_path,
            paths["audit"],
            k=3,
            accepted_candidates_path=paths["accepted"],
            rejects_path=paths["rejects"],
        )


def test_audit_preflight_without_filter_outputs_fails_before_strict_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import promote_candidates as promote_script

    incomplete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "64k"))
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    output_path = tmp_path / "must-not-exist.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in incomplete), encoding="utf-8"
    )
    rankings_path.write_text("", encoding="utf-8")

    def unexpected_replay(*_args, **_kwargs):
        raise AssertionError("strict replay must not run")

    monkeypatch.setattr(promote_script, "create_dense_audit", unexpected_replay)

    with pytest.raises(PromotionError, match="structural preflight.*32k"):
        promote_script.audit_rankings(
            candidates_path,
            rankings_path,
            output_path,
            k=3,
            release_profile_id="p12-wiki-source-slice-1-v1",
        )
    assert not output_path.exists()


def test_audit_preflight_reports_cumulative_reject_before_strict_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import promote_candidates as promote_script

    candidates, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "32k", "64k"))
    resigned = []
    for candidate in candidates:
        payload = {
            **{key: value for key, value in candidate.items() if key != "attestation"},
            "artifact_classification": _source_bound_classifications(candidate),
            "semantic_tokens": {
                "event_bearing": 12_000,
                "internal": 12_000,
                "generic_background": 0,
            },
        }
        resigned.append(
            attach_attestation(payload, KEY, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        )
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    output_path = tmp_path / "must-not-exist.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in resigned), encoding="utf-8"
    )
    rankings_path.write_text("", encoding="utf-8")

    def unexpected_replay(*_args, **_kwargs):
        raise AssertionError("strict replay must not run")

    monkeypatch.setattr(promote_script, "create_dense_audit", unexpected_replay)

    with pytest.raises(PromotionError, match="invalid cumulative source history"):
        promote_script.audit_rankings(
            candidates_path,
            rankings_path,
            output_path,
            k=3,
            release_profile_id="p12-wiki-source-slice-1-v1",
        )
    assert not output_path.exists()


def test_parallel_audit_preflight_handles_an_all_rejected_candidate_pool(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from promote_candidates import audit_rankings

    incomplete, _ = _p12_wiki_exact_bucket_selection_inputs(("16k", "64k"))
    candidates_path = tmp_path / "candidates.jsonl"
    rankings_path = tmp_path / "rankings.jsonl"
    audits_path = tmp_path / "audits.jsonl"
    accepted_path = tmp_path / "accepted.jsonl"
    rejects_path = tmp_path / "rejects.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(row) + "\n" for row in incomplete), encoding="utf-8"
    )
    rankings_path.write_text("", encoding="utf-8")

    assert (
        audit_rankings(
            candidates_path,
            rankings_path,
            audits_path,
            k=3,
            accepted_candidates_path=accepted_path,
            rejects_path=rejects_path,
            release_profile_id="p12-wiki-source-slice-1-v1",
            workers=2,
        )
        == 0
    )
    assert audits_path.read_bytes() == b""
    assert accepted_path.read_bytes() == b""
    assert len(rejects_path.read_text(encoding="utf-8").splitlines()) == 2
