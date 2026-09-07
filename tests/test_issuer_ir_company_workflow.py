from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from longworld.core.engine import semantic_answer_from_artifacts
from longworld.core.issuerfilingworkflow import (
    ISSUER_IR_EQUITY_SECTION,
    ISSUER_IR_OPERATIONS_SECTION,
    _audit_issuer_ir_filing_manifest,
    _statement_table,
    build_issuer_ir_filing_manifest,
    load_issuer_ir_filing_manifest,
    parse_issuer_ir_rendered_metrics,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.render import render_world
from longworld.core.sourceworkflow import adapt_source_manifest
from longworld.domains.company.queries import (
    _issuer_ir_question_declares_schema,
    build_queries,
    eval_answer,
    state_from_artifacts,
)
from longworld.domains.company.schema import sample_world_spec
from longworld.domains.company.simulate import simulate_company
from scripts.build_source_workflow_bundle import build_source_workflow_bundle
from scripts.export_issuer_ir_filing_history import export_issuer_ir_filing_history
from scripts.generate import context_source_relation_count, real_source_relation_edges


def test_real_amazon_history_replays_source_state_and_cross_year_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = Path("data/source_inventory/p12_amazon_ir_history_v4")
    inventory_path = source_root / "issuer_ir_inventory.json"
    if not inventory_path.exists():
        pytest.skip("real Amazon issuer-IR history is not on this host")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    manifest = build_issuer_ir_filing_manifest(
        inventory,
        source_root,
        generated_at="2026-08-28T11:00:00Z",
    )
    assert manifest["source_status"] == "issuer_owned_ir_download"
    assert manifest["source_family"] == "issuer_ir_rendered_xbrl"
    assert manifest["n"] == 4
    assert len(manifest["relations"]) == 3
    assert all(
        record["source_url"].startswith(
            "https://d18rn0p25nwr6d.cloudfront.net/CIK-0001018724/"
        )
        for record in manifest["records"]
    )
    assert all(
        "sec.gov/Archives" not in record["source_url"] for record in manifest["records"]
    )

    key = b"issuer-ir-test-source-key-32bytes"
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_SOURCE_ATTESTATION_KEY", key.decode())
    monkeypatch.setenv("LONGWORLD_SOURCE_ATTESTATION_KEY_ID", "test-issuer-ir-source")
    manifest_path = tmp_path / "issuer-ir-manifest.json"
    export_issuer_ir_filing_history(
        inventory_path,
        manifest_path,
        attestation_key=key,
        generated_at="2026-08-28T11:00:00Z",
    )
    loaded = load_issuer_ir_filing_manifest(manifest_path, attestation_key=key)
    bundle = build_source_workflow_bundle(
        [("issuer_ir_filing", manifest_path)],
        tmp_path / "source-workflow-bundle.json",
        attestation_key=key,
        adapter_revision="sourceworkflow@2",
    )
    assert len(bundle.bindings) == 1
    assert bundle.bindings[0].kind == "issuer_ir_filing"
    assert len(bundle.workflows) == 1
    workflows = adapt_source_manifest(
        loaded,
        source_kind="issuer_ir_filing",
        signed_bundle_authorized=True,
        adapter_revision="sourceworkflow@2",
    )
    assert len(workflows) == 1
    workflow = workflows[0]
    assert workflow.source_kind == "issuer_ir_filing"
    assert workflow.source_families == ("issuer_ir_rendered_xbrl",)
    assert len(workflow.records) == 4
    assert len(workflow.relations) == 3

    spec = sample_world_spec(
        1205,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=list(workflows),
    )
    world = simulate_company(spec)["focal"]
    artifacts = render_world(world)
    relation_artifacts = [
        artifact
        for artifact in artifacts
        if "issuer_ir_relation" in artifact.artifact_id
    ]
    assert len(relation_artifacts) == 3
    assert len({artifact.text for artifact in relation_artifacts}) == 3
    rendered_text = "\n".join(
        artifact.text for artifact in artifacts if "issuer_ir_" in artifact.artifact_id
    ).casefold()
    assert "recompute the requested" not in rendered_text
    assert "does not restate" not in rendered_text
    queries = [
        query
        for query in build_queries(world)
        if query.query_type.startswith("issuer_ir_")
    ]
    assert {query.query_type for query in queries} == {
        "issuer_ir_two_year_growth",
        "issuer_ir_three_year_growth",
        "issuer_ir_four_year_consistency",
    }
    queries_by_tier = sorted(
        queries,
        key=lambda query: {"16k": 0, "32k": 1, "64k": 2}[
            query.preferred_length_buckets[0]
        ],
    )
    assert [len(query.essential_event_ids) for query in queries_by_tier] == [
        23,
        43,
        83,
    ]
    assert [query.proof_depth for query in queries_by_tier] == [3, 4, 5]
    answer_events = [
        next(
            event
            for event in world.events
            if event.type == "issuer_ir_cross_year_answer"
            and event.params.get("answer_key") == query.answer_key
        )
        for query in queries_by_tier
    ]
    assert answer_events[0].params["prerequisite_answer_key"] == ""
    assert answer_events[0].id in answer_events[1].required_inputs
    assert answer_events[1].id in answer_events[2].required_inputs
    two_year = next(
        query for query in queries if query.query_type == "issuer_ir_two_year_growth"
    )
    assert (
        sum("issuer_ir_section" in item for item in two_year.essential_artifact_ids)
        == 21
    )
    four_year = next(
        query
        for query in queries
        if query.query_type == "issuer_ir_four_year_consistency"
    )
    four_year_answer = next(
        event
        for event in world.events
        if event.type == "issuer_ir_cross_year_answer"
        and event.params.get("answer_key") == four_year.answer_key
    )
    two_year_roles = {
        *answer_events[0].params["required_roles"],
        *(
            role
            for values in answer_events[0].params["record_role_extensions"].values()
            for role in values
        ),
    }
    assert two_year_roles < set(answer_events[1].params["required_roles"])
    assert set(answer_events[1].params["required_roles"]) < set(
        four_year_answer.params["required_roles"]
    )
    assert [len(query.answer) for query in queries_by_tier] == sorted(
        len(query.answer) for query in queries_by_tier
    )
    oldest_record_id = str(four_year_answer.params["record_ids"][0])
    assert four_year_answer.params["record_role_extensions"] == {
        oldest_record_id: [
            "policy_cash_equivalents_three_months",
            "policy_inventory_fifo",
            "policy_lease_longer_than_twelve_months",
        ]
    }
    assert (
        sum(
            event.type == "issuer_ir_source_section"
            and event.params.get("section_name")
            == "Description of Business, Accounting Policies, and Supplemental "
            "Disclosures (Policies)"
            for event in world.events
            if event.id in four_year_answer.required_inputs
        )
        == 1
    )
    for query in queries:
        answer_event = next(
            event
            for event in world.events
            if event.type == "issuer_ir_cross_year_answer"
            and event.params.get("answer_key") == query.answer_key
        )
        roles = [str(role) for role in answer_event.params["required_roles"]]
        extension_roles = {
            str(role)
            for values in dict(
                answer_event.params.get("record_role_extensions") or {}
            ).values()
            for role in values
        }
        role_kinds = {
            str(span["role"]): str(span["kind"])
            for event in world.events
            if event.id in query.essential_event_ids
            for span in event.params.get("fact_spans") or []
            if isinstance(span, dict)
            and str(span.get("role")) in {*roles, *extension_roles}
        }
        assert _issuer_ir_question_declares_schema(
            query.question,
            years=[int(year) for year in answer_event.params["report_years"]],
            roles=roles,
            role_kinds=role_kinds,
            record_role_extensions={
                int(str(record_id).rsplit(":", 1)[-1][:4]): [
                    str(role) for role in extension_roles
                ]
                for record_id, extension_roles in dict(
                    answer_event.params.get("record_role_extensions") or {}
                ).items()
            },
        )
        extension_program_roles = {
            str(role)
            for op in query.program_ops
            if op.get("op") == "READ_ISSUER_XBRL_RECORD_EXTENSION"
            for role in op.get("roles") or []
        }
        assert extension_program_roles == extension_roles
        assert any(
            op.get("op") == "FORMAT_PER_RECORD_EXTENSION" for op in query.program_ops
        ) == bool(extension_roles)
        state = state_from_artifacts(world, query.essential_event_ids, query.as_of)
        answer = eval_answer(world, query, state.values)
        assert answer != "unknown"
        assert answer == query.answer
        essential_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        assert len(essential_artifacts) == len(query.essential_artifact_ids)
        authentic_edges = [
            edge
            for edge in real_source_relation_edges(world, query, essential_artifacts)
            if edge["relation_provenance"] == "authentic_source"
        ]
        expected_edges = {
            "issuer_ir_two_year_growth": 1,
            "issuer_ir_three_year_growth": 2,
            "issuer_ir_four_year_consistency": 3,
        }[query.query_type]
        assert len(authentic_edges) == expected_edges
        assert context_source_relation_count(
            world, essential_artifacts, spec=query
        ) >= len(authentic_edges)
        for removed in essential_artifacts:
            assert (
                semantic_answer_from_artifacts(
                    world,
                    query,
                    [
                        artifact
                        for artifact in essential_artifacts
                        if artifact.artifact_id != removed.artifact_id
                    ],
                    enforce_preconditions=False,
                )
                == "unknown"
            )
        for removed in query.essential_event_ids:
            reduced = state_from_artifacts(
                world,
                query.essential_event_ids,
                query.as_of,
                skip_ids={removed},
            )
            assert eval_answer(world, query, reduced.values) == "unknown"
        cf = state_from_artifacts(
            world,
            query.essential_event_ids,
            query.as_of,
            param_overrides={query.cf_event_id: query.cf_param_updates},
        )
        assert eval_answer(world, query, cf.values) == query.cf_answer
        assert query.cf_answer != query.answer
        source_event = next(
            event for event in world.events if event.id == query.cf_event_id
        )
        source_text = str(source_event.params["text"])
        prefix = "Issuer IR rendered XBRL statement\n"
        revenue_span = next(
            span
            for span in source_event.params["fact_spans"]
            if span["role"] == "revenue"
        )
        corruption_offset = int(revenue_span["char_end"]) - 1
        corrupted_text = (
            source_text[:corruption_offset]
            + ("0" if source_text[corruption_offset] != "0" else "1")
            + source_text[corruption_offset + 1 :]
        )
        corrupted = state_from_artifacts(
            world,
            query.essential_event_ids,
            query.as_of,
            param_overrides={
                source_event.id: {
                    "text": corrupted_text,
                    "text_sha256": hashlib.sha256(corrupted_text.encode()).hexdigest(),
                    "section_sha256": hashlib.sha256(
                        corrupted_text[len(prefix) :].encode()
                    ).hexdigest(),
                }
            },
        )
        assert eval_answer(world, query, corrupted.values) == "unknown"

    source_event = next(
        event for event in world.events if event.id == two_year.cf_event_id
    )
    source_artifact = next(
        artifact
        for artifact in artifacts
        if artifact.artifact_id
        == f"{world.spec['world_id']}.{source_event.visibility[0]}"
    )
    essential_artifacts = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in two_year.essential_artifact_ids
    ]
    revenue_span = next(
        span for span in source_event.params["fact_spans"] if span["role"] == "revenue"
    )
    offset = (
        source_artifact.text.index(str(source_event.params["text"]))
        + int(revenue_span["char_end"])
        - 1
    )
    corrupted_text = (
        source_artifact.text[:offset]
        + ("0" if source_artifact.text[offset] != "0" else "1")
        + source_artifact.text[offset + 1 :]
    )
    text_corrupted_artifact = replace(source_artifact, text=corrupted_text)
    assert (
        semantic_answer_from_artifacts(
            world,
            two_year,
            [
                text_corrupted_artifact
                if artifact.artifact_id == source_artifact.artifact_id
                else artifact
                for artifact in essential_artifacts
            ],
        )
        == "unknown"
    )

    relation_artifact = next(
        artifact
        for artifact in essential_artifacts
        if artifact.doc_type == "issuer_ir_prior_filing_relation"
    )
    relation_corrupted_artifacts = [
        replace(artifact, text="")
        if artifact.artifact_id == relation_artifact.artifact_id
        else artifact
        for artifact in essential_artifacts
    ]
    assert not any(
        edge["relation_provenance"] == "authentic_source"
        for edge in real_source_relation_edges(
            world, two_year, relation_corrupted_artifacts
        )
    )

    oldest_two_year_record_id = str(answer_events[0].params["record_ids"][0])
    endpoint_corrupted_artifacts = [
        replace(artifact, text="")
        if artifact.doc_type == "issuer_ir_source_section"
        and (artifact.slots or {}).get("params", {}).get("record_id")
        == oldest_two_year_record_id
        else artifact
        for artifact in essential_artifacts
    ]
    assert not any(
        edge["relation_provenance"] == "authentic_source"
        for edge in real_source_relation_edges(
            world, two_year, endpoint_corrupted_artifacts
        )
    )

    corrupted_slots = deepcopy(source_artifact.slots)
    corrupted_slots["params"]["fact_spans"][0]["source_char_start"] += 1
    span_corrupted_artifact = replace(source_artifact, slots=corrupted_slots)
    assert (
        semantic_answer_from_artifacts(
            world,
            two_year,
            [
                span_corrupted_artifact
                if artifact.artifact_id == source_artifact.artifact_id
                else artifact
                for artifact in essential_artifacts
            ],
        )
        == "unknown"
    )

    envelope_corrupted_world = deepcopy(world)
    envelope_source = next(
        event
        for event in envelope_corrupted_world.events
        if event.id == two_year.cf_event_id
    )
    envelope_source.params["source_url"] += "?tampered=1"
    envelope_artifacts = render_world(envelope_corrupted_world)
    assert (
        semantic_answer_from_artifacts(
            envelope_corrupted_world,
            two_year,
            [
                artifact
                for artifact in envelope_artifacts
                if artifact.artifact_id in two_year.essential_artifact_ids
            ],
        )
        == "unknown"
    )


def test_issuer_ir_question_schema_rejects_omitted_role_and_corrupted_format() -> None:
    years = [2022, 2023]
    roles = ["revenue", "policy_inventory_fifo"]
    role_kinds = {"revenue": "numeric", "policy_inventory_fifo": "policy_presence"}
    complete = (
        "2022–2023. Required roles in exact order: "
        "Revenue [label REVENUE; USD millions]; "
        "Policy inventory fifo [label POLICY_INVENTORY_FIFO; presence flag, "
        "1=phrase present, 0=absent]. Exact output format: for each year in "
        "ascending order, YEAR:ROLE=integer,ROLE=integer; then for each "
        "consecutive pair, PRIOR_YEAR→CURRENT_YEAR:ΔROLE=integer,ΔROLE=integer. "
        "Separate blocks with ` | ` and do not reorder labels."
    )
    assert _issuer_ir_question_declares_schema(
        complete, years=years, roles=roles, role_kinds=role_kinds
    )
    assert not _issuer_ir_question_declares_schema(
        complete.replace(
            "; Policy inventory fifo [label POLICY_INVENTORY_FIFO; presence flag, "
            "1=phrase present, 0=absent]",
            "",
        ),
        years=years,
        roles=roles,
        role_kinds=role_kinds,
    )
    assert not _issuer_ir_question_declares_schema(
        complete.replace("Separate blocks with ` | `", "Separate blocks with commas"),
        years=years,
        roles=roles,
        role_kinds=role_kinds,
    )


def test_amazon_probe_uses_exact_pack_admission() -> None:
    config = yaml.safe_load(
        Path("configs/p12_amazon_issuer_ir_history_v1.yaml").read_text(encoding="utf-8")
    )
    assert config["exact_pack_admission"] is True
    assert config["exact_tokenizer"]["model_id"] == "Qwen/Qwen3.5-4B"


def test_rendered_metrics_are_exact_source_spans() -> None:
    source = Path(
        "data/source_inventory/p12_amazon_ir_history_v3/"
        "amazon-2023-annual.rendered_xbrl_html.html"
    )
    if not source.exists():
        pytest.skip("real Amazon issuer-IR history is not on this host")
    text = source.read_text(encoding="utf-8")
    program = parse_issuer_ir_rendered_metrics(text, report_date="2023-12-31")
    values = {fact.role: fact.numeric_value for fact in program.facts}
    assert values["revenue"] == 574785
    assert values["operating_income"] == 36852
    assert values["assets"] == 527854
    assert values["current_liabilities"] == 164917
    assert len(values) == 57
    for fact in program.facts:
        assert text[fact.char_start : fact.char_end] == fact.evidence_quote
    assert hashlib.sha256(text.encode()).hexdigest() == program.source_sha256


def _swap_matches(text: str, first: re.Match[str], second: re.Match[str]) -> str:
    return (
        text[: first.start()]
        + second.group()
        + text[first.end() : second.start()]
        + first.group()
        + text[second.end() :]
    )


def test_amazon_2025_other_income_details_title_omits_share_units() -> None:
    from longworld.core.issuerfilingworkflow import _AMAZON_OTHER_INCOME_DETAILS_SECTION

    html = (
        "<table><tr><th class='tl'><strong>Description of Business, Accounting "
        "Policies, and Supplemental Disclosures - Other Income (Expense), Net "
        "(Details) - USD ($) $ in Millions</strong></th></tr></table>"
    )
    start, end = _statement_table(html, _AMAZON_OTHER_INCOME_DETAILS_SECTION)
    assert 0 <= start < end <= len(html)


def test_rendered_metric_period_binding_survives_column_and_row_reordering() -> None:
    source = Path(
        "data/source_inventory/p12_amazon_ir_history_v3/"
        "amazon-2023-annual.rendered_xbrl_html.html"
    )
    if not source.exists():
        pytest.skip("real Amazon issuer-IR history is not on this host")
    text = source.read_text(encoding="utf-8")
    row_pattern = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
    th_pattern = re.compile(r"<th\b[^>]*>.*?</th>", re.IGNORECASE | re.DOTALL)
    td_pattern = re.compile(r"<td\b[^>]*>.*?</td>", re.IGNORECASE | re.DOTALL)

    start, end = _statement_table(text, ISSUER_IR_OPERATIONS_SECTION)
    table = text[start:end]
    reordered_rows = []
    cursor = 0
    for row_match in row_pattern.finditer(table):
        reordered_rows.append(table[cursor : row_match.start()])
        row = row_match.group()
        headers = list(th_pattern.finditer(row))
        cells = list(td_pattern.finditer(row))
        if len(headers) >= 2 and "Dec. 31, 2023" in row:
            row = _swap_matches(row, headers[0], headers[1])
        elif len(cells) >= 3:
            row = _swap_matches(row, cells[1], cells[2])
        reordered_rows.append(row)
        cursor = row_match.end()
    reordered_rows.append(table[cursor:])
    text = text[:start] + "".join(reordered_rows) + text[end:]

    start, end = _statement_table(text, ISSUER_IR_EQUITY_SECTION)
    table = text[start:end]
    ending_rows = [
        match
        for match in row_pattern.finditer(table)
        if "Ending Balance at Dec. 31, 2022" in match.group()
        or "Ending Balance at Dec. 31, 2023" in match.group()
    ]
    assert len(ending_rows) == 2
    table = _swap_matches(table, ending_rows[0], ending_rows[1])
    text = text[:start] + table + text[end:]

    values = {
        fact.role: fact.numeric_value
        for fact in parse_issuer_ir_rendered_metrics(
            text, report_date="2023-12-31"
        ).facts
    }
    assert values["revenue"] == 574785
    assert values["operating_income"] == 36852
    assert values["equity_rollforward_end"] == 201875


def test_rendered_metrics_accept_audited_2021_presentation_aliases() -> None:
    source = Path(
        "data/source_inventory/p12_amazon_ir_history_v4/"
        "amazon-2021-annual.rendered_xbrl_html.html"
    )
    if not source.exists():
        pytest.skip("real four-year Amazon issuer-IR history is not on this host")
    text = source.read_text(encoding="utf-8")
    program = parse_issuer_ir_rendered_metrics(text, report_date="2021-12-31")

    assert len(program.facts) == 57
    assert len({fact.section for fact in program.facts}) == 23
    assert {fact.role for fact in program.facts} >= {
        "cash_fx_effect",
        "tax_total",
        "tax_federal",
        "tax_state",
        "tax_foreign",
    }
    assert all(
        text[fact.char_start : fact.char_end] == fact.evidence_quote
        for fact in program.facts
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "authorization",
        "issuer_name",
        "issuer_cik",
        "detail_url",
        "artifact_url",
        "xbrl_identity",
        "duplicate_annual_report_pdf",
        "duplicate_xbrl_zip",
        "duplicate_rendered_xbrl_html",
    ),
)
def test_issuer_manifest_revalidates_complete_acquisition_chain(
    mutation: str,
) -> None:
    source_root = Path("data/source_inventory/p12_amazon_ir_history_v3")
    inventory_path = source_root / "issuer_ir_inventory.json"
    if not inventory_path.exists():
        pytest.skip("real Amazon issuer-IR history is not on this host")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    tampered = deepcopy(inventory)
    if mutation == "authorization":
        tampered["authorization"] = None
    elif mutation == "issuer_name":
        tampered["issuer"]["name"] = "Fictional Corp"
    elif mutation == "issuer_cik":
        tampered["issuer"]["cik"] = "0000000001"
    elif mutation == "detail_url":
        tampered["filings"][0]["detail_page"]["source_url"] = (
            "https://evil.invalid/filing"
        )
    elif mutation == "artifact_url":
        tampered["filings"][0]["artifacts"][0]["source_url"] = (
            "https://evil.invalid/report.pdf"
        )
    elif mutation == "xbrl_identity":
        tampered["filings"][0]["xbrl_identity"]["cik"] = "0000000001"
    else:
        role = mutation.removeprefix("duplicate_")
        artifact = next(
            item for item in tampered["filings"][0]["artifacts"] if item["role"] == role
        )
        tampered["filings"][0]["artifacts"].append(deepcopy(artifact))

    with pytest.raises(ProvenanceError):
        build_issuer_ir_filing_manifest(
            tampered,
            source_root,
            generated_at="2026-08-28T11:00:00Z",
        )


@pytest.mark.parametrize(
    "mutation",
    ("swap_metric_payload", "duplicate_fact_id", "numeric_value"),
)
def test_issuer_manifest_audit_exactly_replays_derived_facts(mutation: str) -> None:
    source_root = Path("data/source_inventory/p12_amazon_ir_history_v3")
    inventory_path = source_root / "issuer_ir_inventory.json"
    if not inventory_path.exists():
        pytest.skip("real Amazon issuer-IR history is not on this host")
    manifest = build_issuer_ir_filing_manifest(
        json.loads(inventory_path.read_text(encoding="utf-8")),
        source_root,
        generated_at="2026-08-28T11:00:00Z",
    )
    tampered = deepcopy(manifest)
    facts = tampered["records"][-1]["derived_facts"]
    revenue = next(fact for fact in facts if fact["fact_id"] == "revenue")
    operating_income = next(
        fact for fact in facts if fact["fact_id"] == "operating_income"
    )
    if mutation == "swap_metric_payload":
        revenue.update(
            {key: value for key, value in operating_income.items() if key != "fact_id"}
        )
        revenue["fact_id"] = "revenue"
    elif mutation == "duplicate_fact_id":
        facts.append(deepcopy(revenue))
    else:
        revenue["numeric_value"] += 1

    with pytest.raises(ProvenanceError, match="facts are invalid"):
        _audit_issuer_ir_filing_manifest(tampered)


@pytest.mark.parametrize(
    "mutation",
    ("relation_id", "evidence", "non_adjacent"),
)
def test_issuer_manifest_audit_exactly_replays_temporal_relations(
    mutation: str,
) -> None:
    source_root = Path("data/source_inventory/p12_amazon_ir_history_v3")
    inventory_path = source_root / "issuer_ir_inventory.json"
    if not inventory_path.exists():
        pytest.skip("real Amazon issuer-IR history is not on this host")
    manifest = build_issuer_ir_filing_manifest(
        json.loads(inventory_path.read_text(encoding="utf-8")),
        source_root,
        generated_at="2026-08-28T11:00:00Z",
    )
    tampered = deepcopy(manifest)
    relation = tampered["relations"][-1]
    if mutation == "relation_id":
        relation["relation_id"] = "fabricated-relation-id"
    elif mutation == "evidence":
        source = next(
            record
            for record in tampered["records"]
            if record["record_id"] == relation["source_record_id"]
        )
        relation["evidence_quote"] = "Amazon"
        relation["evidence_char_start"] = source["text"].index("Amazon")
    else:
        relation["target_record_id"] = tampered["records"][0]["record_id"]

    with pytest.raises(ProvenanceError, match="relation is invalid"):
        _audit_issuer_ir_filing_manifest(tampered)
