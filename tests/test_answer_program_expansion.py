from __future__ import annotations

import json

import pytest

from longworld.core.engine import answer_from_artifacts
from longworld.core.sampler import materialize
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view


@pytest.mark.parametrize(
    ("domain", "query_type"),
    [
        ("company", "legal_financial_release_trace"),
        ("researchlab", "benchmark_revision_conflict"),
        ("codeforge", "failure_recovery_release_trace"),
    ],
)
@pytest.mark.parametrize("seed", [1, 37, 91])
def test_new_answer_programs_are_executable_not_identity_only(
    domain: str, query_type: str, seed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY", "program-expansion-test-key-material-32-bytes"
    )
    materialized = materialize(
        seed,
        n_parallel=0,
        n_pulses=0,
        domain=domain,
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    query = next(item for item in materialized.queries if item.query_type == query_type)
    artifacts = materialized.artifacts["focal"]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]

    assert len(query.program_ops) >= 3
    assert query.answer != query.cf_answer
    assert answer_from_artifacts(world, query, essential) == query.answer
    for dropped in essential:
        assert (
            answer_from_artifacts(
                world,
                query,
                [artifact for artifact in essential if artifact is not dropped],
            )
            != query.answer
        )

    _, counterfactual = render_cf_view(world, query)
    verification, notes = verify_question(
        world,
        query,
        artifacts,
        cf_artifacts=counterfactual,
        verification_mode="candidate",
    )
    assert verification.all_green(), notes


def test_new_program_shapes_are_distinct_from_existing_programs() -> None:
    signatures: set[str] = set()
    new_signatures: set[str] = set()
    new_types = {
        "legal_financial_release_trace",
        "benchmark_revision_conflict",
        "failure_recovery_release_trace",
    }
    for domain in ("company", "researchlab", "codeforge"):
        materialized = materialize(
            37,
            n_parallel=0,
            n_pulses=0,
            domain=domain,
            include_program_joins=False,
        )
        for query in materialized.queries:
            signature = json.dumps(query.program_ops, sort_keys=True)
            if not query.program_ops:
                continue
            if query.query_type in new_types:
                assert signature not in signatures
                new_signatures.add(signature)
            signatures.add(signature)

    assert len(new_signatures) == 3
