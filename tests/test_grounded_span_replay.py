from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from longworld.core.groundedspan import (
    ClaimedProvenanceClass,
    GroundedFact,
    GroundedRelation,
    GroundedSource,
    GroundedSpanError,
    facts_in_visible_char_window,
    validate_grounded_source,
    validate_grounded_sources,
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fact(
    source_id: str,
    text: str,
    fact_id: str,
    quote: str,
) -> GroundedFact:
    start = text.index(quote)
    return GroundedFact(
        fact_id=fact_id,
        source_id=source_id,
        text_sha256=_digest(text),
        quote=quote,
        char_start=start,
        char_end=start + len(quote),
        normalized_quote=" ".join(quote.split()),
    )


def _source(source_id: str = "filing:1") -> GroundedSource:
    text = f"Record: {source_id}\nRevenue: USD 42 million\nStatus: audited\n"
    return GroundedSource(
        source_id=source_id,
        visible_text=text,
        text_sha256=_digest(text),
        facts=(
            _fact(source_id, text, f"{source_id}:revenue", "USD 42 million"),
            _fact(source_id, text, f"{source_id}:status", "audited"),
        ),
    )


def test_valid_source_replays_exact_visible_spans() -> None:
    source = _source()

    assert validate_grounded_source(source) is source


@pytest.mark.parametrize(
    "mutation",
    [
        lambda source: replace(source, text_sha256="0" * 64),
        lambda source: replace(source, visible_text=source.visible_text + "corrupt"),
        lambda source: replace(
            source,
            facts=(replace(source.facts[0], source_id="filing:foreign"),),
        ),
        lambda source: replace(
            source,
            facts=(replace(source.facts[0], text_sha256="0" * 64),),
        ),
        lambda source: replace(
            source,
            facts=(
                replace(source.facts[0], char_start=source.facts[0].char_start + 1),
            ),
        ),
        lambda source: replace(
            source,
            facts=(replace(source.facts[0], quote="USD 41 million"),),
        ),
        lambda source: replace(
            source,
            facts=(replace(source.facts[0], normalized_quote="USD 41 million"),),
        ),
    ],
)
def test_source_validation_fails_closed_on_mismatched_bindings(mutation) -> None:
    with pytest.raises(GroundedSpanError):
        validate_grounded_source(mutation(_source()))


def test_source_validation_rejects_duplicate_ids_and_spans() -> None:
    source = _source()
    first = source.facts[0]

    with pytest.raises(GroundedSpanError, match="fact id"):
        validate_grounded_source(replace(source, facts=(first, first)))
    with pytest.raises(GroundedSpanError, match="fact span"):
        validate_grounded_source(
            replace(source, facts=(first, replace(first, fact_id="same-span")))
        )


def test_bundle_validates_structural_cross_source_relation_closure() -> None:
    source = _source()
    other = _source("filing:2")
    relation = GroundedRelation(
        relation_id="amendment-link",
        relation_type="amends_report",
        source_id="filing:1",
        target_id="filing:2",
        evidence_fact_ids=("filing:1:status", "filing:2:status"),
        claimed_provenance_class="authentic_source_text",
    )
    source = replace(source, relations=(relation,))

    assert validate_grounded_sources((source, other)) == (source, other)


def test_relation_cannot_claim_semantic_proof() -> None:
    first = _source("filing:1")
    second = _source("filing:2")
    relation = GroundedRelation(
        relation_id="unsupported-proof",
        relation_type="amends_report",
        source_id="filing:1",
        target_id="filing:2",
        evidence_fact_ids=("filing:1:status", "filing:2:status"),
        claimed_provenance_class="authentic_source_text",
        proof_mode="semantic_proof",  # type: ignore[arg-type]
    )

    with pytest.raises(GroundedSpanError, match="proof mode"):
        validate_grounded_sources((replace(first, relations=(relation,)), second))


@pytest.mark.parametrize(
    "provenance_class",
    [
        "authentic_source_text",
        "authentic_source_api",
        "verified_derived",
        "synthetic_executable",
    ],
)
def test_relation_accepts_only_declared_provenance_classes(
    provenance_class: ClaimedProvenanceClass,
) -> None:
    source = _source()
    other = _source("filing:2")
    relation = GroundedRelation(
        relation_id=f"relation-{provenance_class}",
        relation_type="supports",
        source_id="filing:1",
        target_id="filing:2",
        evidence_fact_ids=("filing:1:status", "filing:2:status"),
        claimed_provenance_class=provenance_class,
    )

    validate_grounded_sources((replace(source, relations=(relation,)), other))


@pytest.mark.parametrize(
    "relation",
    [
        GroundedRelation(
            relation_id="bad-class",
            relation_type="amends_report",
            source_id="filing:1",
            target_id="filing:2",
            evidence_fact_ids=("filing:1:status", "filing:2:status"),
            claimed_provenance_class="claimed_real",  # type: ignore[arg-type]
        ),
        GroundedRelation(
            relation_id="missing-source",
            relation_type="amends_report",
            source_id="filing:1",
            target_id="filing:absent",
            evidence_fact_ids=("filing:1:status",),
            claimed_provenance_class="verified_derived",
        ),
        GroundedRelation(
            relation_id="bad-type",
            relation_type="Amends report",
            source_id="filing:1",
            target_id="filing:2",
            evidence_fact_ids=("filing:1:status", "filing:2:status"),
            claimed_provenance_class="synthetic_executable",
        ),
    ],
)
def test_relation_validation_rejects_unverifiable_edges(
    relation: GroundedRelation,
) -> None:
    source = replace(_source(), relations=(relation,))
    other = _source("filing:2")

    with pytest.raises(GroundedSpanError):
        validate_grounded_sources((source, other))


def test_bundle_rejects_duplicate_global_ids() -> None:
    first = _source("filing:1")
    second = _source("filing:2")
    second = replace(
        second,
        facts=(replace(second.facts[0], fact_id=first.facts[0].fact_id),),
    )

    with pytest.raises(GroundedSpanError, match="fact id"):
        validate_grounded_sources((first, second))
    with pytest.raises(GroundedSpanError, match="source id"):
        validate_grounded_sources((first, first))


def test_bundle_rejects_renamed_copy_sources() -> None:
    first = _source("filing:1")
    copied = replace(
        first,
        source_id="filing:copy",
        facts=tuple(
            replace(
                fact,
                fact_id=f"copy:{index}",
                source_id="filing:copy",
            )
            for index, fact in enumerate(first.facts)
        ),
    )

    with pytest.raises(GroundedSpanError, match="source text hash"):
        validate_grounded_sources((first, copied))


def test_relation_requires_owner_and_endpoint_evidence() -> None:
    first = _source("filing:1")
    second = _source("filing:2")
    third = _source("filing:3")
    base = GroundedRelation(
        relation_id="relation",
        relation_type="supports",
        source_id="filing:1",
        target_id="filing:2",
        evidence_fact_ids=("filing:1:status", "filing:2:status"),
        claimed_provenance_class="verified_derived",
    )

    with pytest.raises(GroundedSpanError, match="stored under"):
        validate_grounded_sources((first, replace(second, relations=(base,))))
    with pytest.raises(GroundedSpanError, match="non-empty"):
        validate_grounded_sources(
            (replace(first, relations=(replace(base, evidence_fact_ids=()),)), second)
        )
    with pytest.raises(GroundedSpanError, match="non-endpoint"):
        validate_grounded_sources(
            (
                replace(
                    first,
                    relations=(
                        replace(
                            base,
                            evidence_fact_ids=("filing:3:status",),
                        ),
                    ),
                ),
                second,
                third,
            )
        )


def test_relation_duplicate_edge_cannot_change_claimed_provenance() -> None:
    first = _source("filing:1")
    second = _source("filing:2")
    relation = GroundedRelation(
        relation_id="first",
        relation_type="supports",
        source_id="filing:1",
        target_id="filing:2",
        evidence_fact_ids=("filing:1:status", "filing:2:status"),
        claimed_provenance_class="verified_derived",
    )
    conflicting = replace(
        relation,
        relation_id="second",
        claimed_provenance_class="synthetic_executable",
    )

    with pytest.raises(GroundedSpanError, match="typed relation"):
        validate_grounded_sources(
            (replace(first, relations=(relation, conflicting)), second)
        )


@pytest.mark.parametrize("source_id", ["filing:\nspoof", "filing:\x00spoof", "Ｆ:1"])
def test_source_id_rejects_controls_and_unicode_lookalikes(source_id: str) -> None:
    with pytest.raises(GroundedSpanError, match="source id"):
        validate_grounded_source(replace(_source(), source_id=source_id))


def test_non_string_hash_fails_with_contract_error() -> None:
    with pytest.raises(GroundedSpanError, match="sha256"):
        validate_grounded_source(replace(_source(), text_sha256=7))  # type: ignore[arg-type]


def test_source_rejects_duplicate_relation_ids_and_edges() -> None:
    relation = GroundedRelation(
        relation_id="first",
        relation_type="supports",
        source_id="filing:1",
        target_id="filing:2",
        evidence_fact_ids=("filing:1:status", "filing:2:status"),
        claimed_provenance_class="verified_derived",
    )
    source = _source()
    other = _source("filing:2")

    with pytest.raises(GroundedSpanError, match="relation id"):
        validate_grounded_sources(
            (replace(source, relations=(relation, relation)), other)
        )
    with pytest.raises(GroundedSpanError, match="typed relation"):
        validate_grounded_sources(
            (
                replace(
                    source,
                    relations=(relation, replace(relation, relation_id="second")),
                ),
                other,
            )
        )


def test_filter_returns_only_facts_wholly_inside_visible_window() -> None:
    source = _source()
    revenue, status = source.facts

    assert facts_in_visible_char_window(
        source,
        char_start=revenue.char_start,
        char_end=status.char_start,
    ) == (revenue,)
    assert facts_in_visible_char_window(
        source,
        char_start=revenue.char_start + 1,
        char_end=status.char_end,
    ) == (status,)


@pytest.mark.parametrize("window", [(-1, 2), (3, 3), (4, 3), (0, 10_000)])
def test_filter_rejects_invalid_visible_window(window: tuple[int, int]) -> None:
    with pytest.raises(GroundedSpanError):
        facts_in_visible_char_window(
            _source(), char_start=window[0], char_end=window[1]
        )
