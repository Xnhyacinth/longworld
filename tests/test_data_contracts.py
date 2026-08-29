from __future__ import annotations

import hashlib
import json
import random
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from longworld.core.attestation import (
    ATTESTATION_ENV,
    ATTESTATION_ENVIRONMENT_ENV,
    PREDECESSOR_GATE_KEY_ENV,
    PREDECESSOR_GATE_KEY_ID_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
    attestation_key_from_env,
)
from longworld.core.pack import (
    compute_view_metrics,
    dependency_class_for_view,
    pack_view,
)
from longworld.core.promotion import STRICT_REPLAY_REVISION, promoted_row_set_sha256
from longworld.core.provenance import ProvenanceError
from longworld.core.record_contract import replay_bundle_binding_valid, sft_row_errors
from longworld.core.release_profile import (
    RELEASE_PROFILES,
    release_profile,
    release_profile_sha256,
)
from longworld.core.render import Artifact
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    classify_artifact,
)
from longworld.core.verify import Verification
from longworld.domains.company.queries import QuerySpec

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import export_llamafactory
import quality_gate
from export_llamafactory import (
    COND_VIEWS,
    to_sharegpt,
    validate_condition_views,
    validate_export_meta,
    validate_export_twins,
    validate_release_transform,
    validate_token_spread,
    write_b5w_v1_sampler,
    write_condition,
)
from export_llamafactory import (
    filter_rows as filter_llamafactory_rows,
)
from generate import (
    _real_source_tokens,
    _source_families,
    band_growth_reject_reason,
    composition_for_view,
    distinct_strict_view_contexts,
    emit_records,
    enforce_source_requirements,
    episode_replay_binding,
    filter_real_workflow_queries,
    holdout_assignment,
    prepack_verification_green,
    prune_short_ordered_view,
    real_workflow_artifacts_for_query,
    real_workflow_buckets_for_query,
    real_workflow_bundle_for_seed,
    real_workflow_corridor_ids,
    resolve_world_targets,
    source_workflow_artifacts_for_query,
    source_workflow_bundle_for_seed,
    stable_digest,
    strict_view_evidence_reject_reason,
    tokenizer_token_count,
    uses_legacy_exit_bars,
)


def test_replay_bundle_binding_requires_exact_top_level_promotion_match() -> None:
    binding = {
        "schema_version": "longworld.source-workflow-bundle.v1",
        "adapter_revision": "sourceworkflow@1",
        "sha256": "a" * 64,
        "binding_digest": "b" * 64,
    }
    row = {
        "source_workflow_bundle": binding,
        "promotion": {"source_workflow_bundle": dict(binding)},
    }

    assert replay_bundle_binding_valid(row)

    row["promotion"]["source_workflow_bundle"]["binding_digest"] = "c" * 64
    assert not replay_bundle_binding_valid(row)


def test_quality_real_source_accounting_is_relation_independent() -> None:
    row = {
        "world_id": "wiki-static-world",
        "domain": "researchlab",
        "motif": "wiki_claim_reconstruction",
        "base_task_id": "wiki-static-base",
        "context": "verified public source body",
        "question": "q",
        "answer": "a",
        "split": "train",
        "data_stage": "train_ready",
        "real_source_verified": True,
        "real_source_family_ids": ["wikipedia.org/wiki/Thomas_Jefferson"],
        "real_source_workflow_ids": ["wiki-static-workflow"],
        "source_relation_edges": [],
        "authentic_source_relation_edges": [],
        "episode_replay_bundle": {
            "schema_version": "longworld.episode-replay-bundle.v1",
            "sha256": "a" * 64,
            "composition": "chronological_causal_union",
        },
        "promotion": {
            "real_source_verified": True,
            "episode_replay_bundle": {
                "schema_version": "longworld.episode-replay-bundle.v1",
                "sha256": "a" * 64,
                "composition": "chronological_causal_union",
            },
        },
    }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert result["n_real_source_families"] == 1
    assert result["n_unique_real_source_workflows"] == 1
    assert result["n_real_source_relations"] == 0


from quality_gate import evaluate_quality

TEST_ATTESTATION_KEY = b"longworld-test-attestation-key-32-bytes"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _attestation_key(monkeypatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, TEST_ATTESTATION_KEY.decode())


def test_source_workflow_seed_routing_is_domain_typed_not_researchlab_only() -> None:
    configured = "data/source_inventory/sec/source-bundle.json"
    cfg = {
        "source_workflow_bundle": configured,
        "source_workflow_seeds": [7],
    }

    assert source_workflow_bundle_for_seed(cfg, seed=7, domain="company") == (
        ROOT / configured
    )
    assert source_workflow_bundle_for_seed(cfg, seed=8, domain="company") is None


def test_training_does_not_expose_the_producer_attestation_key() -> None:
    for config in (ROOT / "configs" / "llamafactory").glob("*.yaml"):
        assert "trust_remote_code: true" not in config.read_text()

    launcher = (ROOT / "scripts" / "train_llamafactory.sh").read_text()
    assert f"unset {ATTESTATION_ENV}" in launcher
    assert all(name in launcher for name in ROLE_KEY_ENVS.values())
    assert "validate_training_export.py" in launcher
    assert '"B2"|"B4"|"B5_8k")' in launcher
    assert "unsupported for the signed long-context release" in launcher
    b5w_branch = launcher.index('if [[ "$COND" == "B5w" ]]; then')
    ordinary_branch = launcher.index("else", b5w_branch)
    assert launcher.index('--required-output "$COND.json"') > ordinary_branch
    assert (
        '--required-output "B5w.datasets.yaml"' in launcher[b5w_branch:ordinary_branch]
    )

    for name in ("train_swift.sh", "train_baselines_128k.sh"):
        swift_launcher = (ROOT / "scripts" / name).read_text()
        assert f"unset {ATTESTATION_ENV}" in swift_launcher
        assert ATTESTATION_ENVIRONMENT_ENV in swift_launcher
        assert all(value in swift_launcher for value in ROLE_KEY_ENVS.values())
        assert all(value in swift_launcher for value in ROLE_KEY_ID_ENVS.values())
        assert PREDECESSOR_GATE_KEY_ENV in swift_launcher
        assert PREDECESSOR_GATE_KEY_ID_ENV in swift_launcher
    waiter = (ROOT / "scripts" / "wait_gpu_then_train.sh").read_text()
    assert PREDECESSOR_GATE_KEY_ENV in waiter
    assert PREDECESSOR_GATE_KEY_ID_ENV in waiter
    assert (
        "validate_training_export.py"
        in (ROOT / "scripts" / "train_swift.sh").read_text()
    )

    evaluator = (ROOT / "scripts" / "eval_causal.py").read_text()
    assert "trust_remote_code=True" not in evaluator
    assert "attestation_environment_names" in evaluator
    assert "os.environ.pop(name, None)" in evaluator

    readme = (ROOT / "README.md").read_text()
    assert "use-a-secret-of-at-least-32-bytes" not in readme


def test_published_placeholder_is_not_an_attestation_key(monkeypatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, "use-a-secret-of-at-least-32-bytes")
    assert attestation_key_from_env() is None


def _artifact(aid: str, text: str, *, event: str | None = None) -> Artifact:
    return Artifact(
        artifact_id=aid,
        doc_type="email",
        time=date(2026, 1, 1),
        project="p",
        prefix="focal",
        reveals_events=[event] if event else [],
        text=text,
        facts=[],
        slots={"content_plan": {"new_propositions": [aid]}},
        is_focal=True,
    )


def test_real_source_tokens_use_provenance_classification_not_doc_type() -> None:
    repo_body = _artifact("w.repo", "real pull request body " * 20)
    classify_artifact(
        repo_body,
        source_origin=SourceOrigin.REAL_PUBLIC,
        workflow_kind=WorkflowKind.HYBRID_CAUSAL,
        evidence_role=EvidenceRole.CAUSAL_GOLD,
        workflow_id="w",
        provenance_id="github:psf/requests#1",
    )
    synthetic_source_pack = _artifact("w.synthetic", "synthetic filler " * 20)
    synthetic_source_pack.doc_type = "source_pack"
    classify_artifact(
        synthetic_source_pack,
        source_origin=SourceOrigin.SYNTHETIC_WORLD,
        workflow_kind=WorkflowKind.SYNTHETIC_EXECUTABLE,
        evidence_role=EvidenceRole.CAUSAL_SUPPORTING,
        workflow_id="w",
        provenance_id="synthetic-sha256:test",
    )

    assert _real_source_tokens([repo_body, synthetic_source_pack]) > 0
    assert _real_source_tokens([synthetic_source_pack]) == 0


def test_real_ci_corridor_uses_only_the_bound_source_interval() -> None:
    def record(
        artifact_id: str,
        order: int,
        *,
        source_url: str = "https://github.com/example/repo",
        workflow_id: str = "git:episode-a",
    ) -> Artifact:
        artifact = _artifact(
            artifact_id, f"repository record {order}", event=artifact_id
        )
        artifact.slots.update(
            {
                "real_workflow_record": True,
                "source_url": source_url,
                "source_workflow_id": workflow_id,
                "params": {
                    "source_order": order,
                    "workflow_id": workflow_id,
                    "source_url": source_url,
                },
            }
        )
        return artifact

    artifacts = [
        record("w.before", 2),
        record("w.failure", 3),
        record("w.middle", 4),
        record("w.recovery", 5),
        record("w.after", 6),
        record("w.other-workflow", 4, workflow_id="git:episode-b"),
        record("w.other-source", 4, source_url="https://github.com/other/repo"),
    ]
    spec = _spec("w.failure", "w.recovery", query_type="ci_regression_origin")
    selected = real_workflow_artifacts_for_query(artifacts, spec)

    assert real_workflow_corridor_ids(artifacts, spec) == {
        "w.failure",
        "w.middle",
        "w.recovery",
    }
    assert {artifact.artifact_id for artifact in selected} == {
        "w.before",
        "w.failure",
        "w.middle",
        "w.recovery",
        "w.after",
    }


def test_strict_view_distance_gate_rejects_a_local_derived_view() -> None:
    metrics = SimpleNamespace(evidence_count=2, max_evidence_distance=678)

    assert strict_view_evidence_reject_reason(metrics, minimum_tokens=8000) == (
        "view_distance_shortfall:678<8000"
    )
    assert strict_view_evidence_reject_reason(metrics, minimum_tokens=0) is None


def test_prepack_gate_defers_only_layout_dependent_retrieval_checks() -> None:
    verification = Verification(
        production_mode=False,
        candidate_mode=True,
        full_sufficient=True,
        minimal_sufficient=True,
        semantic_sufficient=True,
        strict_executable_sufficient=True,
        remove_one_fails=True,
        counterfactual_changes_answer=True,
        counterfactual_replay_sufficient=True,
        local_window_insufficient=False,
        contiguous_windows_insufficient=False,
        closed_book_unsolved=True,
        distractor_invariance_gold=True,
        surface_match=True,
        schema_ok=True,
        no_shortcut=True,
        min_complexity=True,
        bm25_top1_insufficient=False,
        bm25_topk_insufficient=False,
        lexical_tfidf_topk_insufficient=False,
        essential_single_doc_insufficient=True,
        essential_surface_gold_free=True,
        essential_text_grounded=True,
    )

    assert prepack_verification_green(verification)
    assert not prepack_verification_green(
        verification.model_copy(update={"remove_one_fails": False})
    )


def test_strict_views_do_not_publish_a_duplicate_ordered_row() -> None:
    artifact = _artifact("w.a", "same authentic timeline", event="e0")
    views = {
        "full": (artifact.text, [artifact]),
        "cf": ("changed authentic timeline", [artifact]),
        "ordered_artifact_view": (artifact.text, [artifact]),
    }

    assert set(distinct_strict_view_contexts(views)) == {"full", "cf"}


def test_short_ordered_view_is_omitted_without_dropping_full_cf_twins() -> None:
    a = _artifact("w.a", "semantic evidence a", event="e0")
    b = _artifact("w.b", "semantic evidence b", event="e1")
    views = {
        "full": ("long factual context", [a, b]),
        "cf": ("long counterfactual context", [a, b]),
        "ordered_artifact_view": ("short ordered context", [a, b]),
    }

    kept, reason = prune_short_ordered_view(
        views,
        _spec("w.a", "w.b"),
        query_timing="late",
        minimum_tokens=8000,
    )

    assert set(kept) == {"full", "cf"}
    assert str(reason).startswith("view_distance_shortfall:")
    assert str(reason).endswith("<8000")


def test_ordered_view_pruning_uses_exact_token_distance_when_available() -> None:
    a = _artifact("w.a", "semantic evidence a", event="e0")
    b = _artifact("w.b", "semantic evidence b", event="e1")
    views = {
        "full": ("long factual context", [a, b]),
        "cf": ("long counterfactual context", [a, b]),
        "ordered_artifact_view": ("semantic evidence a\nsemantic evidence b", [a, b]),
    }

    kept, reason = prune_short_ordered_view(
        views,
        _spec("w.a", "w.b"),
        query_timing="late",
        minimum_tokens=8000,
        token_counter=lambda text: len(text) * 1_000,
    )

    assert set(kept) == {"full", "cf", "ordered_artifact_view"}
    assert reason is None


def test_real_workflow_bundle_is_explicitly_seed_and_domain_scoped() -> None:
    cfg = {
        "real_workflow_bundle": "configs/public_repo_episodes.json",
        "real_workflow_seeds": [2],
    }

    assert real_workflow_bundle_for_seed(cfg, seed=2, domain="codeforge") == (
        ROOT / "configs" / "public_repo_episodes.json"
    )
    assert real_workflow_bundle_for_seed(cfg, seed=5, domain="codeforge") is None
    assert real_workflow_bundle_for_seed(cfg, seed=2, domain="company") is None


def test_cross_repository_duplicate_proof_bodies_fail_closed() -> None:
    first = _artifact("w.a", "identical protected source body", event="e0")
    second = _artifact("w.b", "identical protected source body", event="e1")
    body_sha256 = hashlib.sha256(first.text.encode()).hexdigest()
    for artifact, source_url in (
        (first, "https://github.com/example/first"),
        (second, "https://github.com/example/second"),
    ):
        artifact.slots.update(
            {
                "real_workflow_record": True,
                "source_url": source_url,
                "source_workflow_id": artifact.artifact_id,
                "params": {"body_sha256": body_sha256},
            }
        )

    with pytest.raises(ValueError, match="duplicate source bodies"):
        real_workflow_artifacts_for_query(
            [first, second],
            _spec("w.a", "w.b"),
        )


def test_world_budget_override_requires_an_explicit_promotion_target() -> None:
    cfg = {"n_worlds": 14, "target_promoted_worlds": 12}

    assert resolve_world_targets(cfg, None, None) == (14, 12)
    assert resolve_world_targets(cfg, 60, 48) == (60, 48)
    with pytest.raises(ValueError, match="requires --target-promoted-worlds"):
        resolve_world_targets(cfg, 60, None)
    with pytest.raises(ValueError, match="cannot exceed"):
        resolve_world_targets(cfg, 12, 13)


def test_surface_gate_compares_the_same_parallel_dossier() -> None:
    cfg = yaml.safe_load((ROOT / "configs" / "p3_valid.yaml").read_text())
    cfg.update(
        {
            "domains": ["company"],
            "length_buckets": {"4k": 4096},
            "eval_length_buckets": {},
            "exact_tokenizer": {},
            "n_workstreams": 4,
            "n_source_pack": 0,
            "strict_semantic_verification": False,
            "data_stage": "diagnostic",
        }
    )

    rows, _, _ = emit_records(3, cfg, "train")

    assert rows
    assert all(row["verification"]["surface_match"] for row in rows)
    assert all(
        row["holdout"]
        == {
            "strategy": "world",
            "group_id": stable_digest(f"world|{row['world_id']}", size=16),
        }
        for row in rows
    )


def test_episode_replay_binding_uses_exact_sidecar_bytes(tmp_path: Path) -> None:
    sidecar = tmp_path / "episodes.json"
    sidecar.write_text('{"schema_version":"test"}', encoding="utf-8")

    binding = episode_replay_binding(sidecar)

    assert binding["schema_version"] == "longworld.episode-replay-bundle.v1"
    assert binding["composition"] == "chronological_causal_union"
    assert binding["sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()


def test_episode_replay_binding_rejects_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    sidecar = tmp_path / "episodes.json"
    sidecar.symlink_to(target)

    with pytest.raises(ProvenanceError, match="regular file|symbolic link"):
        episode_replay_binding(sidecar)


def test_real_bundle_world_keeps_only_explicit_real_task_families() -> None:
    queries = [
        SimpleNamespace(query_type="current_state"),
        SimpleNamespace(query_type="version_selection"),
        SimpleNamespace(query_type="license_compatibility"),
    ]

    filtered = filter_real_workflow_queries(
        queries,
        ["version_selection", "ci_regression_origin", "license_compatibility"],
    )

    assert [query.query_type for query in filtered] == [
        "version_selection",
        "license_compatibility",
    ]


def test_real_workflow_task_families_use_honest_length_buckets() -> None:
    buckets = {"16k": 16_000, "32k": 32_768, "64k": 65_536}
    routing = {
        "version_selection": ["64k"],
        "ci_regression_origin": ["16k"],
        "license_compatibility": ["16k"],
        "cross_repo_release_dependency": ["32k"],
    }

    assert real_workflow_buckets_for_query(buckets, "version_selection", routing) == {
        "64k": 65_536
    }
    assert real_workflow_buckets_for_query(
        buckets, "license_compatibility", routing
    ) == {"16k": 16_000}
    assert real_workflow_buckets_for_query(
        buckets, "cross_repo_release_dependency", routing
    ) == {"32k": 32_768}
    assert (
        real_workflow_buckets_for_query(
            buckets,
            "version_selection",
            routing,
            preferred=["128k"],
        )
        == {}
    )
    assert (
        real_workflow_buckets_for_query(
            buckets,
            "sec_filing_eligibility",
            {"sec_filing_eligibility": ["16k"]},
            ["32k"],
        )
        == {}
    )


def test_real_query_pool_excludes_unrelated_repository_episodes() -> None:
    requests = _artifact("w.requests", "requests release", event="release")
    requests.slots.update(
        real_workflow_record=True,
        source_url="https://github.com/psf/requests",
    )
    opensearch = _artifact("w.opensearch", "opensearch license", event="merge")
    opensearch.slots.update(
        real_workflow_record=True,
        source_url="https://github.com/opensearch-project/opensearch-php",
    )
    duplicate = _artifact("w.requests-copy", "requests release", event="release-copy")
    duplicate.slots.update(
        real_workflow_record=True,
        source_url="https://github.com/psf/requests",
        params={"body_sha256": hashlib.sha256(b"requests release").hexdigest()},
    )
    requests.slots["params"] = dict(duplicate.slots["params"])
    synthetic = _artifact("w.synthetic", "synthetic workflow", event="fake")
    spec = _spec(requests.artifact_id, query_type="version_selection")

    selected = real_workflow_artifacts_for_query(
        [duplicate, requests, opensearch, synthetic], spec
    )

    assert [artifact.artifact_id for artifact in selected] == [requests.artifact_id]


def test_real_query_pool_keeps_explicit_synthetic_hybrid_policy() -> None:
    release = _artifact("w.release", "real release", event="release")
    release.slots.update(
        real_workflow_record=True,
        source_url="https://github.com/acme/service",
    )
    policy = _artifact("w.policy", "simulated cross-repo policy", event="policy")
    spec = _spec(
        release.artifact_id,
        policy.artifact_id,
        query_type="cross_repo_release_dependency",
    )

    selected = real_workflow_artifacts_for_query([release, policy], spec)

    assert {artifact.artifact_id for artifact in selected} == {
        release.artifact_id,
        policy.artifact_id,
    }


def test_source_workflow_query_pool_excludes_future_documents() -> None:
    before = _artifact("w.before", "known before checkpoint", event="before")
    before.time = date(2025, 1, 1)
    after = _artifact("w.after", "future control", event="after")
    after.time = date(2025, 3, 1)
    spec = _spec(before.artifact_id, query_type="sec_filing_eligibility")
    spec.as_of = date(2025, 2, 1)

    selected = source_workflow_artifacts_for_query([before, after], spec)

    assert [artifact.artifact_id for artifact in selected] == [before.artifact_id]


def test_source_workflow_query_pool_excludes_unbound_source_packs() -> None:
    section = _artifact("w.wiki_section_early_work", "authentic body", event="early")
    leftover = _artifact(
        "w.wiki_section_appendix_rest", "legacy leftover", event="rest"
    )
    rfc = _artifact("w.source.rfc9110.full", "unbound RFC leftover file")
    rfc.doc_type = "source_pack"
    rfc.reveals_events = []
    spec = _spec(section.artifact_id, query_type="wiki_claim_reconstruction")
    spec.as_of = date(2026, 12, 31)

    selected = source_workflow_artifacts_for_query([section, leftover, rfc], spec)

    assert [artifact.artifact_id for artifact in selected] == [
        section.artifact_id,
        leftover.artifact_id,
    ]


def test_source_workflow_query_pool_excludes_unbound_world_history() -> None:
    section = _artifact("w.wiki.section", "authentic body", event="section")
    section.slots.update(
        event_type="wiki_source_section",
        source_workflow_id="source:wikimedia:bound",
    )
    control = _artifact("w.wiki.control", "claim program", event="control")
    control.slots.update(
        event_type="wiki_claim_answer",
        source_workflow_id="source:wikimedia:bound",
    )
    unrelated_source = _artifact("w.wiki.other", "other authentic body", event="other")
    unrelated_source.slots.update(
        event_type="wiki_source_section",
        source_workflow_id="source:wikimedia:other",
    )
    unrelated_history = _artifact(
        "w.lab.release", "unrelated simulated lab release", event="release"
    )
    hard_negative = _artifact("w.wiki.hard", "nearby entity decoy")
    classify_artifact(
        hard_negative,
        source_origin=SourceOrigin.REAL_PUBLIC,
        workflow_kind=WorkflowKind.HYBRID_CAUSAL,
        evidence_role=EvidenceRole.STRUCTURAL_HARD_NEGATIVE,
        workflow_id="world",
        provenance_id="wiki:hard-negative",
    )
    spec = _spec(
        section.artifact_id,
        control.artifact_id,
        query_type="wiki_claim_reconstruction",
    )

    selected = source_workflow_artifacts_for_query(
        [
            section,
            control,
            unrelated_source,
            unrelated_history,
            hard_negative,
        ],
        spec,
    )

    assert {artifact.artifact_id for artifact in selected} == {
        section.artifact_id,
        control.artifact_id,
        hard_negative.artifact_id,
    }


def test_sec_financial_query_pool_excludes_unrelated_company_cycle() -> None:
    def sec_artifact(
        artifact_id: str, event_type: str, workflow_id: str, *, essential: bool = False
    ) -> Artifact:
        artifact = _artifact(artifact_id, event_type, event=artifact_id)
        artifact.doc_type = event_type
        artifact.slots.update(
            event_type=event_type,
            source_workflow_id=workflow_id,
            real_workflow_record=event_type in {"sec_filing", "sec_source_section"},
        )
        if essential:
            classify_artifact(
                artifact,
                source_origin=SourceOrigin.REAL_DERIVED,
                workflow_kind=WorkflowKind.HYBRID_CAUSAL,
                evidence_role=EvidenceRole.CAUSAL_GOLD,
                workflow_id="world",
                provenance_id=f"derived-sha256:{artifact_id}",
            )
        return artifact

    statement = sec_artifact(
        "w.sec.statement", "sec_source_section", "sec:apple", essential=True
    )
    note = sec_artifact("w.sec.note", "sec_source_section", "sec:apple")
    control = sec_artifact(
        "w.sec.control", "sec_financial_answer", "sec:apple", essential=True
    )
    other_filing = sec_artifact("w.sec.other", "sec_source_section", "sec:amazon")
    cycle = _artifact("w.cycle_roadmap", "unrelated company renewal cycle")
    hard_negative = _artifact("w.sec.hard", "nearby issuer decoy")
    classify_artifact(
        hard_negative,
        source_origin=SourceOrigin.REAL_PUBLIC,
        workflow_kind=WorkflowKind.HYBRID_CAUSAL,
        evidence_role=EvidenceRole.STRUCTURAL_HARD_NEGATIVE,
        workflow_id="world",
        provenance_id="sec:hard-negative",
    )
    spec = _spec(
        statement.artifact_id,
        control.artifact_id,
        query_type="sec_financial_reconstruction",
    )

    selected = source_workflow_artifacts_for_query(
        [statement, note, control, other_filing, cycle, hard_negative], spec
    )

    assert {artifact.artifact_id for artifact in selected} == {
        statement.artifact_id,
        note.artifact_id,
        control.artifact_id,
        hard_negative.artifact_id,
    }


def test_real_query_pool_rejects_same_license_body_from_two_repositories() -> None:
    service = _artifact("w.service-license", "Apache License 2.0", event="service")
    service.slots.update(
        real_workflow_record=True,
        source_url="https://github.com/acme/service",
        params={"body_sha256": hashlib.sha256(b"Apache License 2.0").hexdigest()},
    )
    client = _artifact("w.client-license", "Apache License 2.0", event="client")
    client.slots.update(
        real_workflow_record=True,
        source_url="https://github.com/acme/client",
        params=dict(service.slots["params"]),
    )
    spec = _spec(
        service.artifact_id,
        client.artifact_id,
        query_type="cross_repo_release_dependency",
    )

    with pytest.raises(ValueError, match="duplicate source bodies"):
        real_workflow_artifacts_for_query([service, client], spec)


def test_real_repository_url_is_a_source_family_without_filename_heuristics() -> None:
    artifact = _artifact("w.real", "real repository record", event="release")
    artifact.slots.update(
        real_workflow_record=True,
        source_url="https://github.com/psf/requests",
    )

    assert _source_families([artifact]) == ["github.com/psf/requests"]


def _spec(*essential: str, query_type: str = "current_state") -> QuerySpec:
    return QuerySpec(
        query_id="w:q",
        query_type=query_type,
        question="Which state is effective?",
        answer="RV-1001",
        as_of=None,
        answer_key="k",
        essential_event_ids=[f"e{i}" for i in range(len(essential))],
        essential_artifact_ids=list(essential),
        sufficient_event_ids=[f"e{i}" for i in range(len(essential))],
        cf_event_id="e0",
        cf_param_updates={},
        cf_answer="RV-1002",
        invariance_event_id=None,
        proof_depth=max(2, len(essential)),
    )


def test_singleton_distance_uses_late_query_boundary() -> None:
    filler = _artifact("w.f", "background " * 4000)
    evidence = _artifact("w.e", "effective RV-1001", event="e0")
    metrics = compute_view_metrics([filler, evidence], {"w.e"}, query_timing="late")
    assert metrics.context_tokens > 9000
    assert metrics.evidence_count == 1
    assert metrics.query_evidence_distance < 20
    assert metrics.max_evidence_distance == metrics.query_evidence_distance


def test_pack_singleton_late_does_not_claim_250k_distance() -> None:
    evidence = _artifact("w.e", "effective RV-1001", event="e0")
    filler = [_artifact(f"w.f{i}", f"background-{i} " * 2000) for i in range(30)]
    packed = pack_view(
        [evidence],
        _spec("w.e"),
        filler,
        query_timing="late",
        position_bucket="back",
        length_bucket="256k",
        target_tokens=256000,
        rng=random.Random(0),
        min_semantic_tokens=1,
        walk_ids=[],
    )
    assert packed.ok
    assert packed.max_evidence_distance < 100


def test_each_view_has_truthful_bucket_and_dependency() -> None:
    a = _artifact("w.a", "seed " * 200, event="e0")
    b = _artifact("w.b", "ratify " * 200, event="e1")
    minimal = compute_view_metrics([a, b], {"w.a", "w.b"}, query_timing="late")
    assert minimal.length_bucket == "4k"
    assert (
        dependency_class_for_view(_spec("w.a", "w.b"), minimal, "minimal")
        == "short_curriculum"
    )

    join_spec = _spec("w.a", "w.b", query_type="program_join")
    assert dependency_class_for_view(join_spec, minimal, "full") == "program_join"


def test_b5w_uses_weight_without_duplicate_rows(tmp_path: Path) -> None:
    row = {
        "world_id": "w1",
        "query_id": "w1:q",
        "view": "full",
        "query_type": "revisitation",
        "query_timing": "late",
        "length_bucket": "32k",
        "tokenizer_asset_manifest_sha256": "d" * 64,
        "context": "records",
        "answer": "RV-1001",
        "dependency_class": "deep_dependency",
        "difficulty": {"context_tokens": 100, "max_evidence_distance": 100},
        "training_objective": "sft",
        "composition_method": "same_case_dossier",
        "workflow_ids": ["w1"],
        "view_verification": {
            "production_eligible": True,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": "RV-1001",
            "strict_replay_answer": "RV-1001",
        },
        "verification": {
            "production_mode": True,
            "semantic_sufficient": True,
            "strict_executable_sufficient": True,
            "embedding_topk_insufficient": True,
        },
        "data_stage": "train_ready",
        "promotion": {
            "schema_version": "train-ready-promotion-v1",
            "candidate_sha256": "a" * 64,
            "dense_audit_sha256": "b" * 64,
            "dense_ranking_sha256": "c" * 64,
            "dense_model_provider": "huggingface",
            "dense_model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "dense_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
            "dense_model_backend": "sentence-transformers-6.0.0",
            "dense_score_metric": "dot_product",
            "dense_chunking": {
                "strategy": "tokenizer_token_windows",
                "max_tokens": 192,
                "overlap_tokens": 32,
                "aggregation": "max_similarity",
            },
            "dense_top_k": 3,
            "strict_replay_revision": STRICT_REPLAY_REVISION,
            "strict_replay_answer": "RV-1001",
            "tokenizer_asset_manifest_sha256": "d" * 64,
        },
        "artifact_classification": [
            {
                "artifact_id": "w1.a",
                "workflow_id": "w1",
                "workflow_kind": "synthetic_executable",
                "evidence_role": "causal_gold",
                "source_origin": "synthetic_world",
                "provenance_id": "synthetic-sha256:abc",
            }
        ],
    }
    dest = tmp_path / "B5w.json"
    row = attach_attestation(row, TEST_ATTESTATION_KEY, purpose="sft_row")
    meta = write_condition([row], dest, None, upsample=True)
    exported = json.loads(dest.read_text())
    assert len(exported) == 1
    assert exported[0]["conversations"][0] == {
        "from": "system",
        "value": (
            "You are a careful analyst of long internal records: contracts, "
            "lab notes, git objects, CI logs, and search snapshots. Use only the "
            "provided context. If the context is insufficient, reply exactly: "
            "unanswerable"
        ),
    }
    assert "system" not in exported[0]
    assert exported[0]["sample_weight"] == 3
    assert meta["n"] == 1
    assert meta["weighted_n"] == 3
    sampler = write_b5w_v1_sampler(dest, tmp_path)
    sampler_config = yaml.safe_load((tmp_path / "B5w.datasets.yaml").read_text())
    assert sampler["weighted_n"] == 3
    assert sampler_config["causaltwin_b5w_w3"]["weight"] == 3.0
    assert sampler_config["causaltwin_b5w_w3"]["converter"] == "sharegpt"
    assert sampler_config["causaltwin_b5w_w3"]["path"] == "B5w.json"
    assert sampler["combined_retained"] is True
    assert not (tmp_path / "B5w.weight3.json").exists()

    tampered = dict(row)
    tampered["context"] = "attacker-controlled context"
    assert "invalid_or_missing_attestation" in sft_row_errors(tampered)


def test_b5w_sampler_partitions_mixed_weights_without_combined_duplicate(
    tmp_path: Path,
) -> None:
    combined = tmp_path / "B5w.json"
    rows = [
        {"query_id": "q1", "sample_weight": 1},
        {"query_id": "q2", "sample_weight": 3},
    ]
    combined.write_text(json.dumps(rows) + "\n")

    sampler = write_b5w_v1_sampler(combined, tmp_path)
    index = yaml.safe_load((tmp_path / "B5w.datasets.yaml").read_text())

    assert sampler["combined_retained"] is False
    assert not combined.exists()
    shard_rows = [
        row
        for weight in (1, 3)
        for row in json.loads((tmp_path / f"B5w.weight{weight}.json").read_text())
    ]
    assert shard_rows == rows
    assert index["causaltwin_b5w_w1"]["path"] == "B5w.weight1.json"
    assert index["causaltwin_b5w_w3"]["path"] == "B5w.weight3.json"


def test_b5w_sampler_rejects_same_logical_row_at_multiple_weights(
    tmp_path: Path,
) -> None:
    combined = tmp_path / "B5w.json"
    combined.write_text(
        json.dumps(
            [
                {"query_id": "q1", "sample_weight": 1},
                {"query_id": "q1", "sample_weight": 3},
            ]
        )
        + "\n"
    )

    with pytest.raises(ValueError, match="logical row"):
        write_b5w_v1_sampler(combined, tmp_path)


def test_query_first_release_conditions_never_fall_back_to_late_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "view": "full",
        "query_timing": "late",
        "length_bucket": "64k",
    }

    monkeypatch.setattr(export_llamafactory, "sft_row_errors", lambda _row: [])

    assert filter_llamafactory_rows([row], "B1", {"64k"}) == []


def test_sharegpt_export_keeps_full_cf_dossier_twins_atomic_under_cap(
    tmp_path: Path,
) -> None:
    base = {
        "world_id": "w1",
        "query_id": "w1:q",
        "query_type": "revisitation",
        "query_timing": "first",
        "length_bucket": "64k",
        "tokenizer_asset_manifest_sha256": "d" * 64,
        "context": "factual records",
        "answer": "RV-1001",
        "dependency_class": "deep_dependency",
        "difficulty": {"context_tokens": 100, "max_evidence_distance": 90},
        "training_objective": "sft",
        "composition_method": "same_case_dossier",
        "workflow_ids": ["w1"],
        "dossier_id": "dossier-1",
        "view_verification": {
            "production_eligible": True,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": "RV-1001",
            "strict_replay_answer": "RV-1001",
        },
        "verification": {
            "production_mode": True,
            "semantic_sufficient": True,
            "strict_executable_sufficient": True,
            "embedding_topk_insufficient": True,
        },
        "data_stage": "train_ready",
        "promotion": {
            "schema_version": "train-ready-promotion-v1",
            "candidate_sha256": "a" * 64,
            "dense_audit_sha256": "b" * 64,
            "dense_ranking_sha256": "c" * 64,
            "dense_model_provider": "huggingface",
            "dense_model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "dense_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
            "dense_model_backend": "sentence-transformers-6.0.0",
            "dense_score_metric": "dot_product",
            "dense_chunking": {
                "strategy": "tokenizer_token_windows",
                "max_tokens": 192,
                "overlap_tokens": 32,
                "aggregation": "max_similarity",
            },
            "dense_top_k": 3,
            "strict_replay_revision": STRICT_REPLAY_REVISION,
            "strict_replay_answer": "RV-1001",
            "tokenizer_asset_manifest_sha256": "d" * 64,
        },
        "artifact_classification": [
            {
                "artifact_id": "w1.a",
                "workflow_id": "w1",
                "workflow_kind": "synthetic_executable",
                "evidence_role": "causal_gold",
                "source_origin": "synthetic_world",
                "provenance_id": "synthetic-sha256:abc",
            }
        ],
    }
    full = attach_attestation(
        {**base, "view": "full"}, TEST_ATTESTATION_KEY, purpose="sft_row"
    )
    cf_payload = {
        **base,
        "view": "cf",
        "context": "counterfactual records",
        "answer": "RV-2002",
        "composition_method": "counterfactual_twin",
        "view_verification": {
            **base["view_verification"],
            "expected_answer": "RV-2002",
            "strict_replay_answer": "RV-2002",
        },
        "promotion": {
            **base["promotion"],
            "candidate_sha256": "d" * 64,
            "strict_replay_answer": "RV-2002",
        },
    }
    cf = attach_attestation(cf_payload, TEST_ATTESTATION_KEY, purpose="sft_row")
    dest = tmp_path / "B3.json"

    meta = write_condition(
        [full, cf], dest, token_budget=220, upsample=False, require_cf_twins=True
    )
    exported = json.loads(dest.read_text())

    assert meta["n"] == 2
    assert {row["view"] for row in exported} == {"full", "cf"}
    validate_export_twins(exported, "B3")


def test_sharegpt_export_skips_an_oversized_unit_and_fills_with_later_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_llamafactory, "sft_row_errors", lambda _row: [])

    def row(query_id: str, tokens: int) -> dict:
        return {
            "world_id": "w1",
            "query_id": query_id,
            "view": "full",
            "query_type": "contradiction",
            "query_timing": "first",
            "length_bucket": "16k",
            "context": query_id,
            "answer": "answer",
            "difficulty": {
                "context_tokens": tokens - 1,
                "max_evidence_distance": 1,
            },
            "content_hash": query_id,
        }

    dest = tmp_path / "B1.json"
    meta = write_condition(
        [row("first", 60), row("too-large", 100), row("filler", 40)],
        dest,
        token_budget=100,
        upsample=False,
    )

    assert meta["tokens_est"] == 100
    assert [item["query_id"] for item in json.loads(dest.read_text())] == [
        "first",
        "filler",
    ]


def test_sharegpt_export_does_not_let_the_first_unit_exceed_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_llamafactory, "sft_row_errors", lambda _row: [])

    def row(query_id: str, tokens: int) -> dict:
        return {
            "world_id": "w1",
            "query_id": query_id,
            "view": "full",
            "query_type": "contradiction",
            "query_timing": "first",
            "length_bucket": "16k",
            "context": query_id,
            "answer": "answer",
            "difficulty": {
                "context_tokens": tokens - 1,
                "max_evidence_distance": 1,
            },
            "content_hash": query_id,
        }

    dest = tmp_path / "B1.json"
    meta = write_condition(
        [row("too-large-first", 101), row("fits", 40)],
        dest,
        token_budget=100,
        upsample=False,
    )

    assert meta["tokens_est"] == 40
    assert [item["query_id"] for item in json.loads(dest.read_text())] == ["fits"]


def test_sharegpt_export_fails_when_no_atomic_unit_fits_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_llamafactory, "sft_row_errors", lambda _row: [])
    row = {
        "world_id": "w1",
        "query_id": "too-large",
        "view": "full",
        "query_type": "contradiction",
        "query_timing": "first",
        "length_bucket": "16k",
        "context": "too-large",
        "answer": "answer",
        "difficulty": {"context_tokens": 100, "max_evidence_distance": 1},
        "content_hash": "too-large",
    }

    with pytest.raises(ValueError, match="cannot fit any atomic export unit"):
        write_condition(
            [row],
            tmp_path / "B1.json",
            token_budget=100,
            upsample=False,
        )


def test_sharegpt_export_rejects_an_orphaned_cf_dossier(tmp_path: Path) -> None:
    row = to_sharegpt(
        {
            "world_id": "w1",
            "query_id": "w1:q",
            "view": "full",
            "query_type": "revisitation",
            "query_timing": "first",
            "context": "records",
            "answer": "RV-1001",
            "dossier_id": "dossier-1",
        }
    )

    with pytest.raises(ValueError, match="asymmetric"):
        validate_export_twins([row], "B3")


def test_release_export_uses_only_executable_long_views() -> None:
    assert COND_VIEWS == {
        "B1": {"full"},
        "B3": {"full", "cf"},
        "B5": {"full", "cf", "ordered_artifact_view"},
        "B5w": {"full", "cf", "ordered_artifact_view"},
    }
    with pytest.raises(ValueError, match="unsupported release condition"):
        filter_llamafactory_rows([], "B2", {"64k"})


def test_release_export_rejects_a_condition_missing_one_required_view() -> None:
    with pytest.raises(ValueError, match="missing required views"):
        validate_condition_views(
            [{"view": "full"}, {"view": "cf"}],
            "B5",
        )


def test_release_export_fails_before_signing_an_unfair_token_spread() -> None:
    validate_token_spread([100, 104], 0.05)
    with pytest.raises(ValueError, match="token spread"):
        validate_token_spread([100, 106], 0.05)

    with pytest.raises(ValueError, match="lost required coverage"):
        validate_export_meta(
            {
                "by_view_length": {
                    "full|16k": 2,
                    "cf|16k": 1,
                    "full|64k": 2,
                }
            },
            "B3",
            {"16k", "64k"},
        )


@pytest.mark.parametrize(
    ("conditions", "buckets", "seed", "token_budget"),
    [
        (("B1", "B5"), ("16k", "64k"), 0, None),
        (("B1", "B3", "B5", "B5w"), ("64k",), 0, None),
        (("B1", "B3", "B5", "B5w"), ("16k", "64k"), 7, None),
        (("B1", "B3", "B5", "B5w"), ("16k", "64k"), 0, 1_000_000),
    ],
)
def test_release_export_rejects_mutable_transform_policy(
    conditions: tuple[str, ...],
    buckets: tuple[str, ...],
    seed: int,
    token_budget: int | None,
) -> None:
    with pytest.raises(ValueError, match="release training transform"):
        validate_release_transform(
            "p3-probe-12-v1",
            conditions=conditions,
            train_buckets=set(buckets),
            seed=seed,
            token_budget=token_budget,
        )


def test_sft_contract_rejects_reused_global_verification_for_an_invalid_view() -> None:
    row = {
        "context": "documents",
        "answer": "RV-1001",
        "training_objective": "sft",
        "composition_method": "causal_timeline",
        "workflow_ids": ["w1"],
        "verification": {
            "production_mode": True,
            "semantic_sufficient": True,
            "strict_executable_sufficient": True,
        },
        "view_verification": {
            "production_eligible": False,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": "RV-1001",
            "strict_replay_answer": "RV-1001",
        },
        "artifact_classification": [
            {
                "artifact_id": "w1.a",
                "workflow_id": "w1",
                "workflow_kind": "synthetic_executable",
                "evidence_role": "causal_gold",
                "source_origin": "synthetic_world",
                "provenance_id": "synthetic-sha256:abc",
            }
        ],
    }

    row = attach_attestation(row, TEST_ATTESTATION_KEY, purpose="sft_row")
    assert "view_not_production_eligible" in sft_row_errors(row)


def test_sft_contract_rejects_rows_that_bypass_dense_promotion() -> None:
    row = {
        "context": "documents",
        "answer": "RV-1001",
        "training_objective": "sft",
        "composition_method": "causal_timeline",
        "workflow_ids": ["w1"],
        "verification": {
            "production_mode": True,
            "semantic_sufficient": True,
            "strict_executable_sufficient": True,
            "embedding_topk_insufficient": False,
        },
        "view_verification": {
            "production_eligible": True,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": "RV-1001",
            "strict_replay_answer": "RV-1001",
        },
        "artifact_classification": [
            {
                "artifact_id": "w1.a",
                "workflow_id": "w1",
                "workflow_kind": "synthetic_executable",
                "evidence_role": "causal_gold",
                "source_origin": "synthetic_world",
                "provenance_id": "synthetic-sha256:abc",
            }
        ],
    }
    row = attach_attestation(row, TEST_ATTESTATION_KEY, purpose="sft_row")

    errors = sft_row_errors(row)

    assert "not_train_ready" in errors
    assert "dense_retrieval_gate_failed" in errors
    assert "missing_or_invalid_promotion" in errors


def test_quality_gate_rejects_exact_duplicates_and_fake_long_growth() -> None:
    base = {
        "base_task_id": "base",
        "query_timing": "late",
        "view": "full",
        "context": "same",
        "question": "q",
        "answer": "a",
        "split": "train",
        "length_bucket": "64k",
        "difficulty": {"context_tokens": 64000},
        "semantic_tokens": {
            "event_bearing": 1000,
            "internal": 2000,
            "generic_background": 62000,
        },
    }
    duplicate = dict(base)
    duplicate["base_task_id"] = "other"
    long_row = dict(base)
    long_row.update(length_bucket="128k", context="long")
    long_row["difficulty"] = {"context_tokens": 128000}
    long_row["semantic_tokens"] = {
        "event_bearing": 1000,
        "internal": 2000,
        "generic_background": 126000,
    }
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [base, duplicate, long_row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=1,
        max_generic_growth_share=0.2,
    )
    assert any("exact_duplicate" in e for e in result["errors"])
    assert any("semantic_growth" in e for e in result["errors"])


def test_quality_gate_rejects_same_prompt_with_conflicting_answers() -> None:
    base = {
        "base_task_id": "base",
        "world_id": "w1",
        "query_timing": "late",
        "view": "full",
        "context": "same prompt",
        "question": "q",
        "answer": "factual",
        "split": "train",
        "length_bucket": "16k",
        "difficulty": {"context_tokens": 16000},
        "semantic_tokens": {
            "event_bearing": 8000,
            "internal": 8000,
            "generic_background": 0,
        },
    }
    counterfactual = dict(base, view="cf", answer="counterfactual")

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [base, counterfactual],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=1,
        max_generic_growth_share=0.2,
    )

    assert "conflicting_answers_for_prompt=1" in result["errors"]


def test_quality_gate_rejects_a_single_fake_long_row_without_a_baseline() -> None:
    row = {
        "base_task_id": "base",
        "world_id": "w1",
        "query_timing": "late",
        "view": "full",
        "context": "generic background",
        "question": "q",
        "answer": "a",
        "split": "train",
        "length_bucket": "128k",
        "difficulty": {"context_tokens": 128000},
        "semantic_tokens": {
            "event_bearing": 100,
            "internal": 100,
            "generic_background": 127800,
        },
    }
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=4096,
        max_generic_growth_share=0.2,
    )
    assert any("semantic_density" in error for error in result["errors"])


def test_semantic_growth_compares_distinct_length_bands_not_same_band_variants() -> (
    None
):
    base = {
        "base_task_id": "base",
        "world_id": "w1",
        "query_timing": "late",
        "view": "full",
        "question": "q",
        "answer": "a",
        "split": "train",
    }
    rows = [
        {
            **base,
            "context": "short",
            "length_bucket": "16k",
            "actual_context_tokens": 16000,
            "semantic_tokens": {
                "event_bearing": 15000,
                "internal": 0,
                "generic_background": 0,
            },
        },
        {
            **base,
            "context": "native 32k",
            "length_bucket": "32k",
            "actual_context_tokens": 32800,
            "semantic_tokens": {
                "event_bearing": 31500,
                "internal": 0,
                "generic_background": 0,
            },
        },
        {
            **base,
            "context": "underfilled higher cap",
            "length_bucket": "32k",
            "actual_context_tokens": 36900,
            "semantic_tokens": {
                "event_bearing": 35400,
                "internal": 0,
                "generic_background": 0,
            },
        },
    ]

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=4096,
        max_generic_growth_share=0.2,
    )

    assert not any(error.startswith("semantic_growth:") for error in result["errors"])


def test_quality_gate_requires_a_lower_band_for_every_real_64k_view() -> None:
    row = {
        "base_task_id": "real-base",
        "world_id": "w1",
        "query_timing": "late",
        "view": "full",
        "context": "real workflow history",
        "question": "q",
        "answer": "a",
        "split": "train",
        "length_bucket": "64k",
        "actual_context_tokens": 64000,
        "real_source_verified": True,
        "semantic_growth_group_id": "real-growth",
        "semantic_tokens": {
            "event_bearing": 50000,
            "internal": 1000,
            "generic_background": 13000,
            "proof_bearing": 2000,
            "causal_supporting": 3000,
        },
    }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=4096,
        max_generic_growth_share=0.3,
    )

    assert any("real_64k_missing_lower_band" in error for error in result["errors"])


def test_quality_gate_uses_pinned_tokens_for_real_64k_semantic_growth() -> None:
    row = {
        "base_task_id": "real-exact-base",
        "world_id": "w1",
        "query_timing": "late",
        "view": "full",
        "context": "real workflow history",
        "question": "q",
        "answer": "a",
        "split": "train",
        "length_bucket": "64k",
        "actual_context_tokens": 55000,
        "tokenizer_context_tokens": 64700,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "real_source_verified": True,
        "semantic_growth_group_id": "real-exact-growth",
        "semantic_tokens": {
            "event_bearing": 50000,
            "internal": 1000,
            "generic_background": 4000,
        },
    }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=4096,
        max_generic_growth_share=0.3,
    )

    assert any("real_64k_missing_lower_band" in error for error in result["errors"])


def test_128k_semantic_density_uses_exact_not_estimated_total() -> None:
    row = {
        "base_task_id": "exact-128-density",
        "world_id": "w128",
        "query_timing": "late",
        "view": "full",
        "split": "train",
        "length_bucket": "128k",
        "actual_context_tokens": 50_000,
        "tokenizer_context_tokens": 128_100,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "tokenizer_asset_manifest_sha256": "b" * 64,
        "semantic_tokens": {
            "event_bearing": 0,
            "internal": 0,
            "generic_background": 64_000,
        },
    }

    errors = quality_gate._semantic_growth_errors(
        [row], min_internal_growth=4_096, max_generic_growth_share=0.2
    )

    assert any(
        error.startswith("semantic_density:exact-128-density:") for error in errors
    )


def test_exact_band_growth_uses_exact_token_delta_denominator() -> None:
    base = {
        "base_task_id": "exact-growth-denominator",
        "world_id": "w-growth",
        "query_timing": "late",
        "view": "full",
        "split": "train",
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
    }
    before = {
        **base,
        "length_bucket": "64k",
        "actual_context_tokens": 50_000,
        "tokenizer_context_tokens": 64_100,
        "semantic_tokens": {
            "event_bearing": 20_000,
            "internal": 0,
            "generic_background": 1_000,
        },
    }
    after = {
        **base,
        "length_bucket": "128k",
        "actual_context_tokens": 300_000,
        "tokenizer_context_tokens": 128_100,
        "tokenizer_asset_manifest_sha256": "b" * 64,
        "semantic_tokens": {
            "event_bearing": 25_000,
            "internal": 0,
            "generic_background": 21_000,
        },
    }

    errors = quality_gate._semantic_growth_errors(
        [before, after], min_internal_growth=4_096, max_generic_growth_share=0.2
    )

    assert any(
        error.startswith("semantic_growth:exact-growth-denominator:64k->128k:")
        for error in errors
    )


def test_intrinsic_long_source_does_not_require_artificial_truncation() -> None:
    row = {
        "base_task_id": "intrinsic-paper-base",
        "world_id": "paper-world",
        "query_timing": "late",
        "view": "full",
        "context": "complete authentic revision bodies",
        "question": "q",
        "answer": "a",
        "split": "train",
        "length_bucket": "64k",
        "actual_context_tokens": 55000,
        "tokenizer_context_tokens": 64700,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "real_source_verified": True,
        "real_source_token_ratio": 0.98,
        "context_source_relation_count": 1,
        "evidence_span_tokens": 54500,
        "strict_support_event_count": 4,
        "difficulty": {"proof_depth": 4},
        "graph": {"proof_depth": 4, "hop_count": 4},
        "semantic_growth_group_id": "intrinsic-paper-growth",
        "semantic_tokens": {
            "event_bearing": 54500,
            "internal": 0,
            "generic_background": 0,
        },
    }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=4096,
        max_generic_growth_share=0.2,
    )

    assert not any("real_64k_missing_lower_band" in error for error in result["errors"])


def test_intrinsic_long_source_compares_exact_span_in_exact_token_units() -> None:
    row = {
        "base_task_id": "intrinsic-exact-base",
        "world_id": "exact-world",
        "query_timing": "late",
        "view": "full",
        "context": "complete authentic workflow bodies",
        "question": "q",
        "answer": "a",
        "split": "train",
        "length_bucket": "64k",
        "actual_context_tokens": 55_000,
        "tokenizer_context_tokens": 64_700,
        "tokenizer_evidence_span_tokens": 50_000,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "real_source_verified": True,
        "real_source_token_ratio": 0.98,
        "context_source_relation_count": 1,
        "evidence_span_tokens": 54_500,
        "strict_support_event_count": 4,
        "difficulty": {"proof_depth": 4},
        "graph": {"proof_depth": 4, "hop_count": 4},
        "semantic_growth_group_id": "intrinsic-exact-growth",
        "semantic_tokens": {
            "event_bearing": 54_500,
            "internal": 0,
            "generic_background": 0,
        },
    }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=4096,
        max_generic_growth_share=0.2,
    )

    assert any("real_64k_missing_lower_band" in error for error in result["errors"])


def test_sec_intrinsic_long_source_requires_raw_exact_span() -> None:
    row = {
        "base_task_id": "sec-exact-base",
        "world_id": "sec-world",
        "query_type": "sec_financial_reconstruction",
        "query_timing": "late",
        "view": "full",
        "context": "readable filing workflow",
        "question": "q",
        "answer": "a",
        "split": "train",
        "length_bucket": "64k",
        "actual_context_tokens": 55_000,
        "tokenizer_context_tokens": 64_700,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "real_source_verified": True,
        "real_source_token_ratio": 0.98,
        "context_source_relation_count": 1,
        "evidence_span_tokens": 54_500,
        "strict_support_event_count": 4,
        "graph": {"proof_depth": 4, "hop_count": 4},
        "semantic_growth_group_id": "sec-exact-growth",
        "semantic_tokens": {
            "event_bearing": 54_500,
            "internal": 0,
            "generic_background": 0,
        },
    }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=4096,
        max_generic_growth_share=0.2,
    )

    assert any("real_64k_missing_lower_band" in error for error in result["errors"])


def test_real_band_growth_requires_more_replayed_causal_history() -> None:
    base = {
        "base_task_id": "real-base",
        "world_id": "w1",
        "query_timing": "late",
        "view": "full",
        "context": "real workflow history",
        "question": "q",
        "answer": "a",
        "split": "train",
        "real_source_verified": True,
        "semantic_growth_group_id": "real-growth",
        "semantic_tokens": {
            "event_bearing": 12000,
            "internal": 1000,
            "generic_background": 1000,
            "proof_bearing": 2000,
            "causal_supporting": 3000,
        },
        "context_source_relation_count": 3,
        "authentic_source_relation_edges": [{"id": index} for index in range(3)],
        "strict_support_event_count": 4,
        "difficulty": {"proof_depth": 3},
        "graph": {"proof_depth": 3, "hop_count": 3},
    }
    lower = {**base, "length_bucket": "16k", "actual_context_tokens": 16000}
    higher = {
        **base,
        "length_bucket": "64k",
        "actual_context_tokens": 64000,
        "context": "longer real workflow history",
        "semantic_tokens": {
            **base["semantic_tokens"],
            "event_bearing": 60000,
        },
        "difficulty": {"proof_depth": 3},
        "graph": {"proof_depth": 3, "hop_count": 3},
    }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [lower, higher],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=1,
        max_generic_growth_share=0.3,
    )

    assert any("real_causal_history_growth" in error for error in result["errors"])

    higher["context_source_relation_count"] = 8
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [lower, higher],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=1,
        max_generic_growth_share=0.3,
    )
    assert any("real_causal_history_growth" in error for error in result["errors"])

    higher["authentic_source_relation_edges"] = [{"id": index} for index in range(8)]
    higher["strict_support_event_count"] = 9
    higher["difficulty"] = {"proof_depth": 7}
    higher["graph"] = {"proof_depth": 7, "hop_count": 7}
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [lower, higher],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=1,
        max_generic_growth_share=0.3,
    )

    assert not any("real_causal_history_growth" in error for error in result["errors"])
    assert any("real_proof_token_growth" in error for error in result["errors"])

    higher["semantic_tokens"] = {
        **higher["semantic_tokens"],
        "proof_bearing": 2200,
        "causal_supporting": 3100,
    }
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [lower, higher],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=1,
        max_generic_growth_share=0.3,
    )

    assert not any("real_proof_token_growth" in error for error in result["errors"])


def test_p7_real_growth_rejects_nominal_proof_increment_over_background_growth() -> (
    None
):
    base = {
        "base_task_id": "p7-real-base",
        "world_id": "p7-world",
        "query_timing": "first",
        "view": "full",
        "split": "train",
        "real_source_verified": True,
        "semantic_growth_group_id": "p7-real-growth",
        "context_source_relation_count": 3,
        "strict_support_event_count": 4,
        "difficulty": {"proof_depth": 4},
        "graph": {"proof_depth": 4, "hop_count": 4},
        "semantic_tokens": {
            "event_bearing": 10000,
            "internal": 0,
            "generic_background": 0,
            "proof_bearing": 8200,
            "causal_supporting": 100,
        },
    }
    lower = {**base, "length_bucket": "16k", "actual_context_tokens": 16000}
    higher = {
        **base,
        "length_bucket": "32k",
        "actual_context_tokens": 32000,
        "context_source_relation_count": 4,
        "strict_support_event_count": 5,
        "difficulty": {"proof_depth": 5},
        "graph": {"proof_depth": 5, "hop_count": 5},
        "semantic_tokens": {
            **base["semantic_tokens"],
            "event_bearing": 26000,
            "causal_supporting": 180,
        },
    }

    errors = quality_gate._semantic_growth_errors(
        [lower, higher],
        min_internal_growth=1,
        max_generic_growth_share=0.2,
        require_substantial_real_proof_growth=True,
    )

    assert any("real_proof_growth_share" in error for error in errors)


def test_real_exact_64k_domain_coverage_counts_worlds_not_view_rows() -> None:
    rows = [
        {
            "world_id": "company-world-1",
            "domain": "company",
            "base_task_id": "same-task",
            "view": view,
            "query_timing": timing,
        }
        for view in ("full", "cf", "ordered_artifact_view")
        for timing in ("first", "late")
    ]

    assert quality_gate._real_exact_64k_worlds_by_domain(rows, ["company"]) == {
        "company": 1
    }


def test_quality_gate_rejects_only_the_selected_world_split_axis() -> None:
    def row(split: str, context: str) -> dict:
        return {
            "world_id": "w1",
            "workflow_ids": ["workflow-1"],
            "source_family_ids": ["rfc"],
            "artifact_classification": [{"provenance_id": "sha256:shared-source"}],
            "context": context,
            "question": "q",
            "answer": "a",
            "split": split,
            "length_bucket": "16k",
        }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 2},
        [row("train", "train context"), row("eval", "eval context")],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )
    assert "split_leak_world:w1" in result["errors"]
    assert not any("workflow-1" in error for error in result["errors"])
    assert not any("shared-source" in error for error in result["errors"])
    assert not any("source_family" in error for error in result["errors"])


def test_quality_gate_audits_topology_without_mislabeling_world_overlap() -> None:
    rows = [
        {
            "world_id": "shared-world",
            "canonical_topology": "chain:3",
            "split_strategy": "topology",
            "context": f"context-{split}",
            "question": "q",
            "answer": "a",
            "split": split,
        }
        for split in ("train", "eval")
    ]
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 2},
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )
    assert "split_leak_topology:chain:3" in result["errors"]
    assert not any(error.startswith("split_leak_world:") for error in result["errors"])


def test_holdout_assignment_is_stable_and_axis_aware() -> None:
    first = holdout_assignment("topology", "chain:3", train_ratio=0.8, seed=7)
    second = holdout_assignment("topology", "chain:3", train_ratio=0.8, seed=7)
    other_axis = holdout_assignment("world", "chain:3", train_ratio=0.8, seed=7)
    assert first == second
    assert first[1] == stable_digest("topology|chain:3", size=16)
    assert other_axis[1] != first[1]


def test_quality_gate_never_accepts_an_empty_product() -> None:
    result = evaluate_quality(
        {"retention": 0.0, "n_worlds": 1},
        [],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )
    assert "empty_product" in result["errors"]


def test_candidate_rows_always_require_the_promotion_contract() -> None:
    report = attach_attestation(
        {
            "data_stage": "candidate",
            "retention": 0.5,
            "n_worlds": 1,
            "target_promoted_worlds": 1,
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )
    row = {
        "data_stage": "candidate",
        "world_id": "w1",
        "domain": "codeforge",
        "motif": "version_selection",
        "context": "context",
        "question": "question",
        "answer": "answer",
        "split": "train",
    }

    result = evaluate_quality(
        report,
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert "dense_promotion_incomplete=1/1" in result["errors"]


def test_tampered_stage_cannot_disable_report_or_promotion_verification() -> None:
    report = attach_attestation(
        {
            "data_stage": "candidate",
            "retention": 0.5,
            "n_worlds": 1,
            "target_promoted_worlds": 1,
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )
    report["data_stage"] = "diagnostic"
    row = {
        "data_stage": "diagnostic",
        "training_objective": "sft",
        "world_id": "w1",
        "domain": "codeforge",
        "motif": "version_selection",
        "context": "context",
        "question": "question",
        "answer": "answer",
        "split": "train",
        "attestation": {
            "scheme": "hmac-sha256",
            "purpose": "candidate_row",
            "digest": "0" * 64,
        },
    }

    result = evaluate_quality(
        report,
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert "invalid_quality_report_attestation" in result["errors"]
    assert "dense_promotion_incomplete=1/1" in result["errors"]


def test_train_ready_rows_require_an_exact_quality_report_row_binding() -> None:
    row = {
        "data_stage": "train_ready",
        "training_objective": "sft",
        "promotion": {},
        "world_id": "w1",
        "domain": "codeforge",
        "motif": "version_selection",
        "context": "context",
        "question": "question",
        "answer": "answer",
        "split": "train",
    }
    report = attach_attestation(
        {
            "data_stage": "train_ready",
            "retention": 0.5,
            "n_worlds": 1,
            "n_world_ids": 1,
            "n_rows": 1,
            "target_promoted_worlds": 1,
            "promoted_row_set_sha256": promoted_row_set_sha256([row]),
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )

    result = evaluate_quality(
        report,
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert "quality_report_row_binding_mismatch" in result["errors"]


def test_release_profile_must_match_report_and_observed_world_scale() -> None:
    report = attach_attestation(
        {
            "data_stage": "candidate",
            "release_profile_id": "p3-probe-12-v1",
            "retention": 0.5,
            "n_worlds": 1,
            "target_promoted_worlds": 1,
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )

    result = evaluate_quality(
        report,
        [
            {
                "world_id": "w1",
                "context": "context",
                "question": "question",
                "answer": "answer",
                "split": "train",
            }
        ],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id="p3-probe-12-v1",
    )

    assert "release_profile_worlds=1/1 expected=12" in result["errors"]
    assert "release_eval_worlds=0<2" in result["errors"]


def test_release_profile_content_digest_is_required() -> None:
    report = attach_attestation(
        {
            "data_stage": "candidate",
            "release_profile_id": "p3-probe-12-v1",
            "retention": 0.5,
            "n_worlds": 12,
            "target_promoted_worlds": 12,
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )

    result = evaluate_quality(
        report,
        [],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id="p3-probe-12-v1",
    )

    assert "release_profile_sha256_mismatch" in result["errors"]


def test_release_profile_thresholds_cannot_be_relaxed_by_callers() -> None:
    report = attach_attestation(
        {
            "data_stage": "candidate",
            "release_profile_id": "p3-probe-12-v1",
            "retention": 0.5,
            "n_worlds": 12,
            "target_promoted_worlds": 12,
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )
    rows = [
        {
            "data_stage": "candidate",
            "world_id": f"w{index}",
            "domain": "codeforge" if index % 2 else "company",
            "motif": f"motif-{index % 5}",
            "context": f"context-{index}",
            "question": "question",
            "answer": "answer",
            "split": "train",
            "boilerplate_token_ratio": 0.2,
        }
        for index in range(12)
    ]

    result = evaluate_quality(
        report,
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        max_near_dup_sentence_ratio=1.0,
        release_profile_id="p3-probe-12-v1",
    )

    assert "mean_boilerplate=0.2000" in result["errors"]


def test_release_profile_must_be_bound_inside_the_signed_report() -> None:
    report = attach_attestation(
        {
            "data_stage": "candidate",
            "release_profile_id": "p3-production-48-v1",
            "retention": 0.5,
            "n_worlds": 12,
            "target_promoted_worlds": 12,
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )

    result = evaluate_quality(
        report,
        [],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id="p3-probe-12-v1",
    )

    assert "release_profile_report_mismatch" in result["errors"]


def test_production_release_profile_requires_all_distinct_role_identities() -> None:
    report = attach_attestation(
        {
            "data_stage": "candidate",
            "release_profile_id": "p3-production-48-v1",
            "retention": 0.5,
            "n_worlds": 48,
            "target_promoted_worlds": 48,
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )

    result = evaluate_quality(
        report,
        [],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id="p3-production-48-v1",
    )

    assert (
        "production_attestation:attestation environment must be production"
        in result["errors"]
    )


def test_explicit_release_profile_cannot_be_ignored_by_diagnostic_metadata() -> None:
    result = evaluate_quality(
        {"data_stage": "diagnostic", "retention": 0.5, "n_worlds": 1},
        [
            {
                "world_id": "w1",
                "domain": "codeforge",
                "motif": "version_selection",
                "context": "context",
                "question": "question",
                "answer": "answer",
                "split": "train",
            }
        ],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id="p3-production-48-v1",
    )

    assert "invalid_quality_report_attestation" in result["errors"]
    assert result["release_profile_id"] == "p3-production-48-v1"


def test_diagnostic_target_metadata_does_not_require_p3_attestation() -> None:
    result = evaluate_quality(
        {
            "data_stage": "diagnostic",
            "retention": 0.5,
            "n_worlds": 12,
            "target_promoted_worlds": 12,
        },
        [
            {
                "world_id": "w1",
                "domain": "company",
                "motif": "chain",
                "context": "context",
                "question": "question",
                "answer": "answer",
                "split": "train",
            }
        ],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert "invalid_quality_report_attestation" not in result["errors"]
    assert not any(
        error.startswith("dense_promotion_incomplete") for error in result["errors"]
    )


def test_quality_gate_reports_observed_domain_and_motif_distributions() -> None:
    rows = [
        {
            "world_id": "w1",
            "domain": "codeforge",
            "motif": "version_selection",
            "context": "context-1",
            "question": "question-1",
            "answer": "answer-1",
            "split": "train",
        },
        {
            "world_id": "w2",
            "domain": "company",
            "motif": "chain",
            "context": "context-2",
            "question": "question-2",
            "answer": "answer-2",
            "split": "train",
        },
    ]

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 2},
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert result["by_domain"] == {"codeforge": 1, "company": 1}
    assert result["by_motif"] == {"version_selection": 1, "chain": 1}


def test_release_profile_gates_answer_program_and_executable_proof_diversity(
    monkeypatch,
) -> None:
    profile_id = "p7-source-rich-probe-12-v1"
    monkeypatch.setitem(
        RELEASE_PROFILES,
        profile_id,
        replace(
            release_profile(profile_id),
            expected_promoted_worlds=2,
            min_train_worlds=2,
            min_eval_worlds=0,
            min_real_train_worlds=0,
            min_real_eval_worlds=0,
            min_domains=1,
            promoted_domain_world_quotas=(),
            min_exact_64k_rows_by_domain=(),
            min_motifs=1,
            min_source_families=0,
            min_real_base_tasks=0,
            min_real_source_relations=0,
            min_real_64k_rows=0,
            min_unique_real_source_workflows=0,
            min_real_exact_64k_rows_by_domain=(),
            min_real_exact_64k_worlds_by_domain=(),
            min_unique_executable_proofs=2,
            min_unique_answer_programs=2,
            min_unique_semantic_base_tasks=2,
        ),
    )
    rows = [
        {
            "world_id": f"world-{index}",
            "domain": "researchlab",
            "motif": "same-motif",
            "context": f"context-{index}",
            "question": f"question-{index}",
            "answer": "answer",
            "split": "train",
            "executable_proof_id": f"proof-{index}",
            "answer_program_id": "copied-program",
            "semantic_base_task_id": "copied-semantic-task",
        }
        for index in range(2)
    ]

    result = evaluate_quality(
        {
            "release_profile_id": profile_id,
            "release_profile_sha256": release_profile_sha256(profile_id),
            "retention": 1.0,
            "n_worlds": 2,
            "target_promoted_worlds": 2,
        },
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id=profile_id,
    )

    assert "release_unique_executable_proofs=2<2" not in result["errors"]
    assert "release_unique_answer_programs=1<2" in result["errors"]
    assert "release_unique_semantic_base_tasks=1<2" in result["errors"]


def test_quality_gate_rejects_superseded_production_profile_issuance() -> None:
    profile_id = "p3-production-48-v1"

    result = evaluate_quality(
        {
            "release_profile_id": profile_id,
            "release_profile_sha256": release_profile_sha256(profile_id),
            "retention": 1.0,
            "n_worlds": 0,
            "target_promoted_worlds": 48,
        },
        [],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id=profile_id,
    )

    assert f"superseded_production_release_profile:{profile_id}" in result["errors"]


def test_current_source_rich_profiles_require_relation_provenance_split() -> None:
    p7 = release_profile("p7-wiki-source-slice-1-v1")
    p12_wiki = release_profile("p12-wiki-source-slice-1-v1")
    p10 = release_profile("p10-source-rich-production-48-v1")
    p12 = release_profile("p12-current-source-probe-12-v1")
    p12_v2 = release_profile("p12-current-source-probe-12-v2")
    legacy = release_profile("p3-probe-12-v1")

    assert quality_gate._requires_relation_provenance_split(p7)
    assert quality_gate._requires_relation_provenance_split(p12_wiki)
    assert quality_gate._requires_relation_provenance_split(p10)
    assert quality_gate._requires_relation_provenance_split(p12)
    assert quality_gate._requires_relation_provenance_split(p12_v2)
    assert not quality_gate._requires_relation_provenance_split(legacy)
    assert quality_gate._requires_substantial_real_proof_growth(p7)
    assert quality_gate._requires_substantial_real_proof_growth(p12_wiki)
    assert quality_gate._requires_substantial_real_proof_growth(p10)
    assert quality_gate._requires_substantial_real_proof_growth(p12)
    assert quality_gate._requires_substantial_real_proof_growth(p12_v2)
    assert not quality_gate._requires_substantial_real_proof_growth(legacy)


def test_quality_gate_rejects_a_tampered_target_report_without_skipping_scale_gates() -> (
    None
):
    report = attach_attestation(
        {
            "data_stage": "candidate",
            "retention": 0.5,
            "n_worlds": 14,
            "target_promoted_worlds": 12,
        },
        TEST_ATTESTATION_KEY,
        purpose="quality_report",
    )
    report["n_worlds"] = 0
    report["target_promoted_worlds"] = 0
    rows = [
        {
            "world_id": f"w{index}",
            "domain": "codeforge" if index % 2 else "company",
            "motif": f"motif-{index % 5}",
            "length_bucket": "16k",
            "context": f"context-{index}",
            "question": "q",
            "answer": "a",
            "split": "train",
        }
        for index in range(8)
    ]

    result = evaluate_quality(
        report,
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert "invalid_quality_report_attestation" in result["errors"]
    assert "dense_promotion_incomplete=8/8" in result["errors"]


def test_twelve_world_gate_requires_long_rows_and_authentic_source_relations() -> None:
    result = evaluate_quality(
        {
            "data_stage": "candidate",
            "retention": 0.5,
            "n_worlds": 14,
            "target_promoted_worlds": 12,
            "by_domain": {"codeforge": 1, "company": 1},
            "by_motif": {str(index): 1 for index in range(5)},
            "by_length": {"16k": 1},
            "n_unique_source_relations": 0,
        },
        [{"context": "c", "question": "q", "answer": "a", "split": "train"}],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )
    assert "need nonzero 64k rows" in result["errors"]
    assert "need nonzero authentic source relations" in result["errors"]
    assert "dense_promotion_incomplete=1/1" in result["errors"]
    assert "world_retention=0/12" in result["errors"]
    assert "need >=2 domains" in result["errors"]
    assert "need >=5 motifs" in result["errors"]


def test_probe_profile_allows_its_declared_single_domain() -> None:
    profile_id = "p3-probe-12-v1"
    result = evaluate_quality(
        {
            "data_stage": "candidate",
            "release_profile_id": profile_id,
            "release_profile_sha256": release_profile_sha256(profile_id),
            "retention": 0.5,
            "n_worlds": 12,
            "target_promoted_worlds": 12,
        },
        [
            {
                "world_id": f"w{index}",
                "domain": "codeforge",
                "motif": f"motif-{index % 5}",
                "context": f"context-{index}",
                "question": "q",
                "answer": "a",
                "split": "train" if index < 10 else "eval",
                "data_stage": "candidate",
            }
            for index in range(12)
        ],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id=profile_id,
    )

    assert "need >=2 domains" not in result["errors"]
    assert not any(error.startswith("release_domains=") for error in result["errors"])


def test_release_gate_requires_real_workflow_worlds_in_training_split() -> None:
    rows = [
        {
            "world_id": f"w{index}",
            "domain": "codeforge" if index % 2 else "company",
            "motif": f"motif-{index % 5}",
            "context": f"context-{index}",
            "question": "q",
            "answer": "a",
            "split": "train" if index < 10 else "eval",
            "data_stage": "train_ready",
            "promotion": {},
        }
        for index in range(12)
    ]
    rows[-1].update(
        real_source_verified=True,
        real_source_family_ids=["github.com/example/repo"],
        source_relation_id="relation-1",
        source_relation_edges=[
            {"parent_record_id": "issue-1", "child_record_id": "commit-1"}
        ],
        promotion={
            "real_source_verified": True,
            "episode_replay_bundle": {"sha256": "a" * 64},
        },
    )
    report = {
        "data_stage": "train_ready",
        "release_profile_id": "p3-probe-12-v1",
        "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
        "target_promoted_worlds": 12,
        "n_worlds": 12,
        "retention": 0.5,
    }

    result = evaluate_quality(
        report,
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id="p3-probe-12-v1",
    )

    assert "release_real_train_worlds=0<1" in result["errors"]


def test_twelve_world_gate_rejects_nominal_64k_rows_below_64000_tokens() -> None:
    row = {
        "context": "c",
        "question": "q",
        "answer": "a",
        "split": "train",
        "length_bucket": "64k",
        "actual_context_tokens": 60_555,
        "source_relation_id": "source-1",
        "source_relation_edges": [
            {
                "parent_record_id": "commit:1",
                "child_record_id": "release:1",
                "relation": "derived_from",
                "parent_source_url": "https://github.com/acme/tool",
                "child_source_url": "https://github.com/acme/tool",
            }
        ],
        "source_origins": ["real_public"],
    }
    result = evaluate_quality(
        {
            "retention": 0.5,
            "n_worlds": 12,
            "by_domain": {"codeforge": 1, "company": 1},
            "by_motif": {str(index): 1 for index in range(5)},
            "by_length": {"16k": 1, "64k": 1},
        },
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert "need >=64000 exact-token 64k row" in result["errors"]


def test_twelve_world_gate_rejects_each_mislabeled_64k_row() -> None:
    good = {
        "world_id": "w-good",
        "domain": "codeforge",
        "motif": "version_selection",
        "context": "good 64k context",
        "question": "q-good",
        "answer": "a-good",
        "split": "train",
        "length_bucket": "64k",
        "tokenizer_context_tokens": 64_001,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "1" * 40,
    }
    bad = {
        **good,
        "world_id": "w-bad",
        "context": "short mislabeled context",
        "question": "q-bad",
        "tokenizer_context_tokens": 63_999,
    }

    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 12},
        [good, bad],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )

    assert "invalid_exact_64k_rows=1/2" in result["errors"]


def test_release_64k_metadata_uses_the_fixed_tokenizer_and_recounts_context(
    monkeypatch,
) -> None:
    profile = {
        "length_bucket": "64k",
        "context": "short context",
        "tokenizer_context_tokens": 64_001,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a7b0d22b993d71000cf2eadfb37222a67cee521e",
    }
    monkeypatch.setattr(
        quality_gate,
        "_tokenizer_context_tokens",
        lambda _context, _model, _revision: 2,
    )

    assert not quality_gate._has_exact_64k_metadata(
        profile,
        expected_model_id="Qwen/Qwen3.5-4B",
        expected_revision="a7b0d22b993d71000cf2eadfb37222a67cee521e",
    )
    profile["tokenizer_model_id"] = "fake/tokenizer"
    assert not quality_gate._has_exact_64k_metadata(
        profile,
        expected_model_id="Qwen/Qwen3.5-4B",
        expected_revision="a7b0d22b993d71000cf2eadfb37222a67cee521e",
    )


def test_release_metadata_recounts_every_strict_long_band(monkeypatch) -> None:
    expected = {"16k": 16_100, "32k": 32_200, "64k": 64_300}
    monkeypatch.setattr(
        quality_gate,
        "_tokenizer_context_tokens",
        lambda context, _model, _revision: expected[context],
    )
    monkeypatch.setattr(
        quality_gate,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model, _revision: "b" * 64,
    )

    for band, tokens in expected.items():
        row = {
            "length_bucket": band,
            "context": band,
            "tokenizer_context_tokens": tokens,
            "tokenizer_model_id": "Qwen/Qwen3.5-4B",
            "tokenizer_revision": "a" * 40,
            "tokenizer_asset_manifest_sha256": "b" * 64,
        }
        assert quality_gate._has_exact_band_metadata(
            row,
            expected_model_id="Qwen/Qwen3.5-4B",
            expected_revision="a" * 40,
            expected_asset_manifest_sha256="b" * 64,
        )
        row["tokenizer_context_tokens"] += 1
        assert not quality_gate._has_exact_band_metadata(
            row,
            expected_model_id="Qwen/Qwen3.5-4B",
            expected_revision="a" * 40,
            expected_asset_manifest_sha256="b" * 64,
        )


def test_release_metadata_rejects_changed_tokenizer_assets(monkeypatch) -> None:
    monkeypatch.setattr(
        quality_gate,
        "_tokenizer_context_tokens",
        lambda _context, _model, _revision: 16_100,
    )
    monkeypatch.setattr(
        quality_gate,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model, _revision: "c" * 64,
    )
    row = {
        "length_bucket": "16k",
        "context": "16k",
        "tokenizer_context_tokens": 16_100,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "tokenizer_asset_manifest_sha256": "b" * 64,
    }

    assert not quality_gate._has_exact_band_metadata(
        row,
        expected_model_id="Qwen/Qwen3.5-4B",
        expected_revision="a" * 40,
        expected_asset_manifest_sha256="b" * 64,
    )


def test_strict_exact_band_gate_rejects_missing_or_mislabeled_metadata(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        quality_gate,
        "_tokenizer_context_tokens",
        lambda _context, _model, _revision: 16_100,
    )
    missing = {"query_id": "missing", "length_bucket": "16k", "context": "a"}
    mislabeled = {
        "query_id": "mislabeled",
        "length_bucket": "32k",
        "context": "b",
        "tokenizer_context_tokens": 16_100,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
    }

    errors = quality_gate._exact_band_metadata_errors(
        [missing, mislabeled],
        expected_model_id="Qwen/Qwen3.5-4B",
        expected_revision="a" * 40,
    )

    assert errors == [
        "invalid_exact_16k:missing",
        "invalid_exact_32k:mislabeled",
    ]


def test_128k_quality_gate_requires_complete_exact_tokenizer_binding(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        quality_gate,
        "_tokenizer_context_tokens",
        lambda _context, _model, _revision: 128_100,
    )
    monkeypatch.setattr(
        quality_gate,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model, _revision: "b" * 64,
    )
    complete = {
        "query_id": "complete",
        "length_bucket": "128k",
        "context": "bound context",
        "tokenizer_context_tokens": 128_100,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "a" * 40,
        "tokenizer_asset_manifest_sha256": "b" * 64,
    }
    rows = []
    for field in (
        "tokenizer_context_tokens",
        "tokenizer_revision",
        "tokenizer_asset_manifest_sha256",
    ):
        rows.append(
            {
                key: value
                for key, value in {**complete, "query_id": f"missing-{field}"}.items()
                if key != field
            }
        )

    errors = quality_gate._exact_band_metadata_errors(
        rows,
        expected_model_id="Qwen/Qwen3.5-4B",
        expected_revision="a" * 40,
    )

    assert errors == [
        "invalid_exact_128k:missing-tokenizer_context_tokens",
        "invalid_exact_128k:missing-tokenizer_revision",
        "invalid_exact_128k:missing-tokenizer_asset_manifest_sha256",
    ]
    assert (
        quality_gate._exact_band_metadata_errors(
            [complete],
            expected_model_id="Qwen/Qwen3.5-4B",
            expected_revision="a" * 40,
        )
        == []
    )
    assert quality_gate._exact_band_metadata_errors(
        [
            {
                **complete,
                "query_id": "mismatched-asset",
                "tokenizer_asset_manifest_sha256": "c" * 64,
            }
        ],
        expected_model_id="Qwen/Qwen3.5-4B",
        expected_revision="a" * 40,
    ) == ["invalid_exact_128k:mismatched-asset"]


def test_p12_sec_quality_gate_requires_all_four_exact_length_buckets(
    monkeypatch,
) -> None:
    profile = release_profile("p12-sec-source-slice-1-v1")
    tokens_by_band = {
        "16k": 16_000,
        "32k": 32_000,
        "64k": 64_000,
        "128k": 128_000,
    }
    monkeypatch.setattr(
        quality_gate,
        "_tokenizer_context_tokens",
        lambda context, _model, _revision: tokens_by_band[context],
    )
    monkeypatch.setattr(
        quality_gate,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model, _revision: profile.tokenizer_asset_manifest_sha256,
    )

    def row(band: str) -> dict:
        return {
            "world_id": "p12-sec-world",
            "query_id": f"p12-sec-{band}",
            "length_bucket": band,
            "context": band,
            "tokenizer_context_tokens": tokens_by_band[band],
            "tokenizer_model_id": profile.tokenizer_model_id,
            "tokenizer_revision": profile.tokenizer_revision,
            "tokenizer_asset_manifest_sha256": (
                profile.tokenizer_asset_manifest_sha256
            ),
        }

    assert quality_gate._required_exact_length_bucket_errors(
        profile,
        [row("16k"), row("32k"), row("64k")],
    ) == ["release_required_exact_length_buckets:p12-sec-world:missing=128k"]
    assert (
        quality_gate._required_exact_length_bucket_errors(
            profile,
            [row("16k"), row("32k"), row("64k"), row("128k")],
        )
        == []
    )


def test_p12_wiki_quality_gate_requires_all_three_exact_length_buckets(
    monkeypatch,
) -> None:
    profile = release_profile("p12-wiki-source-slice-1-v1")
    tokens_by_band = {"16k": 16_000, "32k": 32_000, "64k": 64_000}
    monkeypatch.setattr(
        quality_gate,
        "_tokenizer_context_tokens",
        lambda context, _model, _revision: tokens_by_band[context],
    )
    monkeypatch.setattr(
        quality_gate,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda _model, _revision: profile.tokenizer_asset_manifest_sha256,
    )

    def row(band: str) -> dict:
        return {
            "world_id": "p12-wiki-world",
            "query_id": f"p12-wiki-{band}",
            "length_bucket": band,
            "context": band,
            "tokenizer_context_tokens": tokens_by_band[band],
            "tokenizer_model_id": profile.tokenizer_model_id,
            "tokenizer_revision": profile.tokenizer_revision,
            "tokenizer_asset_manifest_sha256": (
                profile.tokenizer_asset_manifest_sha256
            ),
        }

    assert quality_gate._required_exact_length_bucket_errors(
        profile,
        [row("16k"), row("64k")],
    ) == ["release_required_exact_length_buckets:p12-wiki-world:missing=32k"]
    assert (
        quality_gate._required_exact_length_bucket_errors(
            profile,
            [row("16k"), row("32k"), row("64k")],
        )
        == []
    )


def test_wave2_wikimedia_configs_use_the_exact_multiband_slice_profile() -> None:
    for entity in ("churchill", "jefferson"):
        config = yaml.safe_load(
            (
                ROOT / "configs" / f"p12_wave2_wikimedia_hunk_{entity}_v1.yaml"
            ).read_text()
        )

        assert config["release_profile_id"] == "p12-wiki-source-slice-1-v1"


def test_proof_metadata_gate_requires_graph_authority() -> None:
    good = {
        "query_id": "good",
        "graph": {"proof_depth": 4, "hop_count": 6},
        "difficulty": {"proof_depth": 4},
        "hop_count": 6,
    }
    wrong_depth = {
        **good,
        "query_id": "depth",
        "difficulty": {"proof_depth": 99},
    }
    wrong_hops = {**good, "query_id": "hops", "hop_count": 99}
    missing = {"query_id": "missing", "difficulty": {"proof_depth": 4}}

    assert quality_gate._proof_metadata_errors(
        [good, wrong_depth, wrong_hops, missing]
    ) == [
        "graph_proof_depth_mismatch:depth",
        "graph_hop_count_mismatch:hops",
        "missing_graph_metadata:missing",
    ]


def test_semantic_growth_uses_replayed_graph_depth_not_difficulty_copy() -> None:
    base = {
        "base_task_id": "graph-growth",
        "world_id": "w-graph",
        "query_timing": "first",
        "view": "full",
        "split": "train",
        "real_source_verified": True,
        "semantic_growth_group_id": "graph-growth",
        "context_source_relation_count": 2,
        "strict_support_event_count": 2,
        "difficulty": {"proof_depth": 99},
        "semantic_tokens": {
            "event_bearing": 15_000,
            "internal": 0,
            "generic_background": 0,
            "proof_bearing": 8_000,
            "causal_supporting": 100,
        },
    }
    lower = {
        **base,
        "length_bucket": "16k",
        "actual_context_tokens": 16_000,
        "graph": {"proof_depth": 3, "hop_count": 3},
    }
    higher = {
        **base,
        "length_bucket": "32k",
        "actual_context_tokens": 32_000,
        "context_source_relation_count": 3,
        "strict_support_event_count": 3,
        "difficulty": {"proof_depth": 100},
        "graph": {"proof_depth": 3, "hop_count": 3},
        "semantic_tokens": {
            **base["semantic_tokens"],
            "event_bearing": 31_000,
            "proof_bearing": 9_000,
        },
    }

    errors = quality_gate._semantic_growth_errors(
        [lower, higher],
        min_internal_growth=1,
        max_generic_growth_share=0.2,
    )

    assert any(error.startswith("real_proof_depth_growth:") for error in errors)


def test_exact_64k_metadata_rejects_rows_above_the_bucket_ceiling() -> None:
    row = {
        "length_bucket": "64k",
        "context": "oversized",
        "tokenizer_context_tokens": 65_537,
        "tokenizer_model_id": "Qwen/Qwen3.5-4B",
        "tokenizer_revision": "1" * 40,
    }

    assert not quality_gate._has_exact_64k_metadata(row)


def test_exact_token_count_uses_the_pinned_tokenizer_encoding() -> None:
    class FakeTokenizer:
        def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
            assert text == "real workflow body"
            assert add_special_tokens is False
            return [1, 2, 3, 4]

    assert tokenizer_token_count("real workflow body", FakeTokenizer()) == 4


def test_quality_gate_rejects_repetitive_workflow_sentences() -> None:
    row = {
        "context": "workflow context",
        "question": "q",
        "answer": "a",
        "split": "train",
        "near_dup_sentence_ratio": 0.34,
    }
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        max_near_dup_sentence_ratio=0.25,
    )
    assert any("near_dup_sentence_ratio" in error for error in result["errors"])


def test_quality_gate_rejects_one_repetitive_row_hidden_by_the_mean() -> None:
    rows = [
        {
            "context": "repetitive workflow",
            "question": "q1",
            "answer": "a1",
            "split": "train",
            "near_dup_sentence_ratio": 0.5,
        },
        {
            "context": "varied workflow",
            "question": "q2",
            "answer": "a2",
            "split": "train",
            "near_dup_sentence_ratio": 0.0,
        },
    ]
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        max_near_dup_sentence_ratio=0.25,
    )
    assert any(
        error.startswith("row_near_dup_sentence_ratio:") for error in result["errors"]
    )


def test_quality_gate_rejects_candidate_with_missing_proof_metadata() -> None:
    row = {
        "data_stage": "candidate",
        "query_id": "bad-proof",
        "context": "workflow context",
        "question": "q",
        "answer": "a",
        "split": "train",
        "verification": {"semantic_sufficient": False},
        "view_verification": {"production_eligible": False},
    }
    result = evaluate_quality(
        {"retention": 0.5, "n_worlds": 1},
        [row],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
    )
    assert "missing_base_task_id:bad-proof" in result["errors"]
    assert "missing_semantic_tokens:bad-proof" in result["errors"]
    assert any(
        error.startswith("row_verification:bad-proof:") for error in result["errors"]
    )
    assert any(
        error.startswith("row_view_verification:bad-proof:")
        for error in result["errors"]
    )


def test_verified_source_requirement_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr("generate.source_pack_artifacts", lambda *args, **kwargs: [])
    with pytest.raises(ValueError, match="verified source pack required"):
        enforce_source_requirements(
            {"require_verified_sources": True, "data_stage": "train_ready"}
        )


def test_candidate_generation_does_not_require_release_only_source_pack(
    monkeypatch,
) -> None:
    monkeypatch.setattr("generate.source_pack_artifacts", lambda *args, **kwargs: [])
    enforce_source_requirements(
        {"require_verified_sources": True, "data_stage": "candidate"}
    )


def test_p3_candidate_does_not_budget_unbound_source_pack() -> None:
    cfg = yaml.safe_load((ROOT / "configs" / "p3_valid.yaml").read_text())

    assert cfg["n_source_pack"] == 0
    assert cfg["require_verified_sources"] is False
    assert cfg["real_workflow_bundle"] == "configs/public_repo_episodes_v2.json"
    assert cfg["domains"] == ["codeforge"]
    assert cfg["include_program_joins"] is False
    assert cfg["n_workstreams"] >= 36
    assert cfg["target_promoted_worlds"] == 12
    assert cfg["n_worlds"] > cfg["target_promoted_worlds"]


def test_candidate_verification_keeps_core_gates_but_defers_dense_release_gate() -> (
    None
):
    gates = {
        "full_sufficient": True,
        "minimal_sufficient": True,
        "semantic_sufficient": True,
        "strict_executable_sufficient": True,
        "remove_one_fails": True,
        "counterfactual_changes_answer": True,
        "counterfactual_replay_sufficient": True,
        "local_window_insufficient": True,
        "contiguous_windows_insufficient": True,
        "closed_book_unsolved": True,
        "distractor_invariance_gold": True,
        "surface_match": True,
        "schema_ok": True,
        "no_shortcut": True,
        "min_complexity": True,
        "bm25_top1_insufficient": True,
        "bm25_topk_insufficient": True,
        "lexical_tfidf_topk_insufficient": True,
        "embedding_topk_insufficient": False,
        "essential_single_doc_insufficient": True,
        "essential_surface_gold_free": True,
        "essential_text_grounded": True,
    }

    candidate = Verification(candidate_mode=True, production_mode=False, **gates)
    release = Verification(candidate_mode=False, production_mode=True, **gates)

    assert candidate.candidate_mode
    assert candidate.all_green()
    assert not release.all_green()


def test_candidate_verification_reports_but_does_not_block_on_cf_surface_ratio() -> (
    None
):
    gates = {
        field: True
        for field in Verification.model_fields
        if field not in {"production_mode", "candidate_mode"}
    }
    gates["surface_match"] = False

    candidate = Verification(candidate_mode=True, production_mode=False, **gates)
    release = Verification(candidate_mode=False, production_mode=True, **gates)

    assert candidate.all_green()
    assert not release.all_green()


def test_candidate_verification_rejects_single_essential_surface_answer() -> None:
    gates = {
        field: True
        for field in Verification.model_fields
        if field not in {"production_mode", "candidate_mode"}
    }
    gates["essential_surface_gold_free"] = False

    candidate = Verification(candidate_mode=True, production_mode=False, **gates)
    release = Verification(candidate_mode=False, production_mode=True, **gates)

    assert not candidate.all_green()
    assert not release.all_green()


def test_redundant_long_bands_are_rejected_before_verification() -> None:
    assert band_growth_reject_reason(16000, 32000, 16500) == "no_semantic_growth"
    assert (
        band_growth_reject_reason(16000, 32000, 20000, min_internal_growth=4096)
        == "no_semantic_growth"
    )
    assert (
        band_growth_reject_reason(16000, 64000, 24000, min_internal_growth=4096)
        == "long_cap_underfilled"
    )
    assert (
        band_growth_reject_reason(16000, 32000, 20100, min_internal_growth=4096) is None
    )
    assert band_growth_reject_reason(-1, 256000, 16000) is None


def test_only_chronological_view_is_labeled_causal_timeline() -> None:
    assert composition_for_view("ordered_artifact_view").value == "causal_timeline"
    assert composition_for_view("full").value == "same_case_dossier"
    assert composition_for_view("minimal").value == "same_case_dossier"
    assert composition_for_view("cf").value == "counterfactual_twin"


def test_candidate_uses_quality_gates_not_legacy_p0_count_bars() -> None:
    assert not uses_legacy_exit_bars("candidate")
    assert not uses_legacy_exit_bars("train_ready")
    assert uses_legacy_exit_bars("diagnostic")
