"""Compile many scoped financial reading tasks from source-verified issuer worlds.

This compiler does not pack lengths, certify long dependency, or infer causal/as-of
claims. The returned world is JSON serializable but must be used unchanged: reload
its signed manifest before validating saved task JSON in another process.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from longworld.core.issuerfilingworkflow import (
    MAX_ISSUER_IR_MANIFEST_BYTES,
    _visible_cell_text,
    load_issuer_ir_filing_manifest_bytes,
    parse_issuer_ir_rendered_metrics,
)
from longworld.core.issuerinlineworkflow import (
    ISSUER_INLINE_MANIFEST_SCHEMA,
    _context_records,
    _elements,
    parse_issuer_inline_metrics,
)
from longworld.core.provenance import _read_regular_file

WORLD_SCHEMA = "longworld.finance-taskbank-world.v1"
TASK_SCHEMA = "longworld.finance-task-spec.v1"
COMPILER_REVISION = "longworld.finance-taskbank.v1"
UNIT = "USD_millions"
BASIS = "as_reported_in_filing"
# Accounting meaning belongs to the metric, never to an issuer-specific template.
METRICS = {
    "revenue": ("revenue", "duration"),
    "operating_income": ("operating income", "duration"),
    "assets": ("total assets", "instant"),
    "liabilities_and_equity": ("total liabilities and equity", "instant"),
    "current_liabilities": ("current liabilities", "instant"),
    "stockholders_equity": ("stockholders equity", "instant"),
    "cash_from_operations": ("operating cash flow", "duration"),
    "cash_from_investing": ("investing cash flow", "duration"),
    "cash_from_financing": ("financing cash flow", "duration"),
    "cash_fx_effect": ("currency effect on cash", "duration"),
    "cash_period_change": ("reported net cash change", "duration"),
}
_CASH_GROUPS = {
    frozenset(("cash_from_operations", "cash_from_investing")),
    frozenset(("cash_from_operations", "cash_from_investing", "cash_from_financing")),
    frozenset(
        (
            "cash_from_operations",
            "cash_from_investing",
            "cash_from_financing",
            "cash_fx_effect",
        )
    ),
}
_RATIO_PAIRS = {("operating_income", "revenue"), ("cash_from_operations", "revenue")}
_LOAD_TOKEN = object()


class FinanceTaskBankError(ValueError):
    """A source, type, scope, or semantic-task contract was violated."""


class _VerifiedWorld(dict):
    def __init__(self, value: dict[str, Any], token: object):
        if token is not _LOAD_TOKEN:
            raise FinanceTaskBankError("world_not_source_verified")
        super().__init__(value)
        self._snapshot = deepcopy(value)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _same_typed(actual: Any, expected: Any) -> bool:
    if actual is expected:
        return True
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(_same_typed(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _same_typed(left, right)
                for left, right in zip(actual, expected, strict=True)
            )
        )
    return type(actual) is type(expected) and actual == expected


def _check_world(world: dict[str, Any]) -> None:
    if not isinstance(world, _VerifiedWorld):
        raise FinanceTaskBankError(
            "world_not_source_verified: reload with load_finance_world"
        )
    if not _same_typed(world, world._snapshot):
        raise FinanceTaskBankError("world_modified_after_source_verification")


def _parse_record(manifest: dict[str, Any], record: dict[str, Any]):
    if manifest["schema_version"] == ISSUER_INLINE_MANIFEST_SCHEMA:
        return parse_issuer_inline_metrics(
            record["text"],
            report_date=record["report_date"],
            issuer_cik=manifest["issuer"]["cik"],
            metric_profile=manifest["source_profile"],
        )
    return parse_issuer_ir_rendered_metrics(
        record["text"],
        report_date=record["report_date"],
        issuer_cik=manifest["issuer"]["cik"],
        metric_profile=manifest.get("financial_metric_profile"),
    )


def load_finance_world(manifest_path: str | Path) -> dict[str, Any]:
    """Verify HMAC and native raw-source contracts, then normalize monetary facts."""
    path = Path(manifest_path).absolute()
    raw = _read_regular_file(path, MAX_ISSUER_IR_MANIFEST_BYTES)
    manifest = load_issuer_ir_filing_manifest_bytes(raw)
    issuer = {"cik": manifest["issuer"]["cik"], "name": manifest["issuer"]["name"]}
    inline = manifest["schema_version"] == ISSUER_INLINE_MANIFEST_SCHEMA
    documents, facts, rejected = [], [], []
    cells: dict[tuple, dict[str, Any]] = {}
    semantic_slots: set[tuple[str, str]] = set()
    for record in sorted(
        manifest["records"],
        key=lambda item: (item["report_date"], item["filing_date"], item["record_id"]),
    ):
        text = record["text"]
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest != record["source_sha256"]:
            raise FinanceTaskBankError("normalized_document_hash_mismatch")
        program = _parse_record(manifest, record)
        if program.source_sha256 != digest:
            raise FinanceTaskBankError("source_parser_document_mismatch")
        sections = []
        visible_sections = {}
        for title, start, end in sorted(
            set(program.section_ranges), key=lambda item: (item[1], item[2], item[0])
        ):
            if not 0 <= start < end <= len(text):
                raise FinanceTaskBankError("invalid_source_section_range")
            section_id = _sha([digest, title, start, end])
            sections.append(
                {"section_id": section_id, "title": title, "start": start, "end": end}
            )
            visible_sections[section_id] = _visible_cell_text(
                text[start:end]
            ).casefold()
        document = {
            "record_id": record["record_id"],
            "text": text,
            "source_sha256": digest,
            "report_date": record["report_date"],
            "filing_date": record["filing_date"],
            "source_url": record["source_url"],
            "reporting_basis": BASIS,
            "source_version": digest,
            "sections": sections,
        }
        documents.append(document)
        contexts = _context_records(text) if inline else {}
        inline_elements = _elements(text) if inline else []
        for fact in sorted(
            program.facts, key=lambda item: (item.char_start, item.char_end, item.role)
        ):
            if fact.kind != "numeric" or fact.role not in METRICS:
                rejected.append(
                    {
                        "record_id": record["record_id"],
                        "role": fact.role,
                        "reason": "unsupported_nonmonetary_or_metric",
                    }
                )
                continue
            if isinstance(fact.numeric_value, bool) or not isinstance(
                fact.numeric_value, int
            ):
                raise FinanceTaskBankError("noninteger_normalized_amount")
            if (
                text[fact.char_start : fact.char_end] != fact.evidence_quote
                or not fact.evidence_quote
            ):
                raise FinanceTaskBankError("source_fact_span_mismatch")
            matches = [
                section
                for section in sections
                if section["title"] == fact.section
                and section["start"]
                <= fact.char_start
                < fact.char_end
                <= section["end"]
            ]
            if len(matches) != 1:
                raise FinanceTaskBankError("ambiguous_fact_statement")
            section = matches[0]
            kind = METRICS[fact.role][1]
            start_date = None
            if inline:
                matches = [
                    element
                    for element in inline_elements
                    if element.kind == "nonfraction"
                    and element.content_start
                    <= fact.char_start
                    < fact.char_end
                    <= element.content_end
                ]
                if len(matches) != 1:
                    raise FinanceTaskBankError("ambiguous_inline_fact_context")
                context = contexts[matches[0].attrs["contextref"]]
                if context["instant"] != (kind == "instant"):
                    raise FinanceTaskBankError("metric_period_type_mismatch")
                start_date = context["start"] or None
            else:
                visible = visible_sections[section["section_id"]]
                if "usd ($)" not in visible or "$ in millions" not in visible:
                    rejected.append(
                        {
                            "record_id": record["record_id"],
                            "role": fact.role,
                            "reason": "monetary_unit_not_verified",
                        }
                    )
                    continue
                if kind == "duration" and "12 months ended" not in visible:
                    rejected.append(
                        {
                            "record_id": record["record_id"],
                            "role": fact.role,
                            "reason": "annual_duration_not_verified",
                        }
                    )
                    continue
            period = {
                "kind": kind,
                "start": start_date,
                "end": record["report_date"],
                "duration": "annual_fiscal_period" if kind == "duration" else None,
            }
            slot = (record["record_id"], fact.role)
            if slot in semantic_slots:
                raise FinanceTaskBankError("duplicate_metric_in_document")
            semantic_slots.add(slot)
            cell = (
                digest,
                fact.char_start,
                fact.char_end,
                UNIT,
                kind,
                period["end"],
                BASIS,
            )
            if cell in cells:
                previous = cells[cell]
                if previous["value"] != fact.numeric_value:
                    raise FinanceTaskBankError("same_span_conflicting_values")
                previous["aliases"].append(fact.role)
                rejected.append(
                    {
                        "record_id": record["record_id"],
                        "role": fact.role,
                        "reason": "same_source_fact_alias",
                        "fact_id": previous["fact_id"],
                    }
                )
                continue
            normalized = {
                "fact_id": _sha([cell, fact.numeric_value]),
                "role": fact.role,
                "value": fact.numeric_value,
                "unit": UNIT,
                "period": period,
                "report_date": record["report_date"],
                "filing_date": record["filing_date"],
                "reporting_basis": BASIS,
                "source_version": digest,
                "record_id": record["record_id"],
                "source_url": record["source_url"],
                "span": {
                    "start": fact.char_start,
                    "end": fact.char_end,
                    "quote": fact.evidence_quote,
                    "section_id": section["section_id"],
                },
                "aliases": [fact.role],
            }
            cells[cell] = normalized
            facts.append(normalized)
    if not facts:
        raise FinanceTaskBankError("no_supported_source_verified_monetary_facts")
    collection = _sha(
        sorted(
            (document["record_id"], document["source_sha256"]) for document in documents
        )
    )
    world_id = _sha({"issuer_cik": issuer["cik"], "source_collection_id": collection})
    result = {
        "schema_version": WORLD_SCHEMA,
        "world_id": world_id,
        "world_instance_id": world_id,
        "source_collection_id": collection,
        "split_group_id": issuer["cik"],
        "issuer": issuer,
        "source_manifest": {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_version": manifest["schema_version"],
            "source_family": manifest["source_family"],
        },
        "documents": documents,
        "facts": facts,
        "normalization_rejections": rejected,
        "source_verified": True,
    }
    return _VerifiedWorld(result, _LOAD_TOKEN)


@dataclass(frozen=True)
class _Result:
    value: Any
    consumed: tuple[str, ...]
    operations: tuple[str, ...]


def _keys(node: dict[str, Any], expected: set[str]) -> None:
    if not isinstance(node, dict) or set(node) != expected:
        raise FinanceTaskBankError("invalid_program_fields")


def _join(results: list[_Result], op: str, value: Any) -> _Result:
    consumed = tuple(fact_id for result in results for fact_id in result.consumed)
    if len(consumed) != len(set(consumed)):
        raise FinanceTaskBankError("same_fact_consumed_twice")
    return _Result(
        value,
        consumed,
        tuple(operator for result in results for operator in result.operations) + (op,),
    )


def _compatible(
    values: list[dict[str, Any]], *, same_metric=False, same_period=False
) -> None:
    if not values or any(
        not isinstance(value, dict) or "fact_id" not in value for value in values
    ):
        raise FinanceTaskBankError("quantity_vector_required")
    if len({(value["unit"], value["reporting_basis"]) for value in values}) != 1:
        raise FinanceTaskBankError("incompatible_unit_or_reporting_basis")
    if same_metric and len({value["role"] for value in values}) != 1:
        raise FinanceTaskBankError("mixed_metrics_in_time_series")
    if same_period and len({value["record_id"] for value in values}) != 1:
        raise FinanceTaskBankError("mixed_filings_in_statement_formula")
    periods = [(value["role"], value["period"]["end"]) for value in values]
    if not same_period and len(periods) != len(set(periods)):
        raise FinanceTaskBankError("ambiguous_same_period_versions")


def _vector(result: _Result) -> list[dict[str, Any]]:
    if not isinstance(result.value, list):
        raise FinanceTaskBankError("quantity_vector_required")
    return result.value


def _eval(
    node: dict[str, Any], facts: dict[str, dict[str, Any]], depth: int = 0
) -> _Result:
    if depth > 8 or not isinstance(node, dict):
        raise FinanceTaskBankError("invalid_program_depth_or_node")
    op = node.get("op")
    if not isinstance(op, str):
        raise FinanceTaskBankError("invalid_program_operator")
    if op == "lookup":
        _keys(node, {"op", "fact_id"})
        if not isinstance(node["fact_id"], str):
            raise FinanceTaskBankError("invalid_fact_identifier")
        value = facts.get(node["fact_id"])
        if value is None:
            raise FinanceTaskBankError("unknown_source_fact")
        return _Result(value, (node["fact_id"],), (op,))
    if op == "collect":
        _keys(node, {"op", "items"})
        if not isinstance(node["items"], list) or not 1 <= len(node["items"]) <= 64:
            raise FinanceTaskBankError("invalid_collect_size")
        parts = [_eval(item, facts, depth + 1) for item in node["items"]]
        values = [part.value for part in parts]
        if any(
            not isinstance(value, dict) or "fact_id" not in value for value in values
        ):
            raise FinanceTaskBankError("collect_requires_source_quantities")
        return _join(parts, op, values)
    if op == "ratio":
        _keys(node, {"op", "numerator", "denominator", "multiplier", "rounding"})
        n, d = (
            _eval(node["numerator"], facts, depth + 1),
            _eval(node["denominator"], facts, depth + 1),
        )
        _compatible([n.value, d.value], same_period=True)
        if (
            (n.value["role"], d.value["role"]) not in _RATIO_PAIRS
            or node["multiplier"] != 10000
            or node["rounding"] != "nearest_half_away_from_zero"
        ):
            raise FinanceTaskBankError("unsupported_financial_ratio")
        if d.value["value"] <= 0:
            raise FinanceTaskBankError("nonpositive_ratio_denominator")
        numerator, denominator = n.value["value"] * 10000, d.value["value"]
        integer, remainder = divmod(abs(numerator), denominator)
        rounded = (integer + (2 * remainder >= denominator)) * (
            -1 if numerator < 0 else 1
        )
        answer = {
            "value": rounded,
            "unit": "basis_points",
            "numerator": n.value["value"],
            "denominator": denominator,
            "input_unit": n.value["unit"],
            "period_end": n.value["period"]["end"],
        }
        return _join([n, d], op, answer)
    if op == "reconcile":
        _keys(node, {"op", "components", "reported"})
        components, reported = (
            _eval(node["components"], facts, depth + 1),
            _eval(node["reported"], facts, depth + 1),
        )
        values = _vector(components)
        _compatible([*values, reported.value], same_period=True)
        if (
            frozenset(value["role"] for value in values) not in _CASH_GROUPS
            or len(values) < 3
            or reported.value["role"] != "cash_period_change"
        ):
            raise FinanceTaskBankError("unsupported_cash_reconciliation")
        total = sum(value["value"] for value in values)
        answer = {
            "components": {
                value["role"]: value["value"]
                for value in sorted(values, key=lambda item: item["role"])
            },
            "component_sum": total,
            "reported_change": reported.value["value"],
            "residual": total - reported.value["value"],
            "unit": reported.value["unit"],
            "period_end": reported.value["period"]["end"],
        }
        return _join([components, reported], op, answer)
    if op not in {"delta", "aggregate", "maximum", "filter"}:
        raise FinanceTaskBankError("unsupported_program_operator")
    expected = {"op", "input"} | (
        {"method"}
        if op == "aggregate"
        else {"relation", "threshold"}
        if op == "filter"
        else set()
    )
    _keys(node, expected)
    child = _eval(node["input"], facts, depth + 1)
    values = _vector(child)
    _compatible(values)
    if op == "delta":
        _compatible(values, same_metric=True)
        if len(values) != 2:
            raise FinanceTaskBankError("delta_requires_two_periods")
        before, after = sorted(values, key=lambda value: value["period"]["end"])
        return _join(
            [child],
            op,
            {
                "value": after["value"] - before["value"],
                "unit": after["unit"],
                "earlier_period_end": before["period"]["end"],
                "later_period_end": after["period"]["end"],
            },
        )
    if op == "filter":
        _compatible(values, same_metric=True)
        if (
            type(node["threshold"]) is not int
            or node["threshold"] != 0
            or node["relation"] not in {"gt", "lt"}
            or len(values) < 2
        ):
            raise FinanceTaskBankError("unsupported_filter_predicate")
        selected = [
            value
            for value in values
            if (value["value"] > 0 if node["relation"] == "gt" else value["value"] < 0)
        ]
        if not selected or len(selected) == len(values):
            raise FinanceTaskBankError("vacuous_filter")
        return _join(
            [child], op, sorted(selected, key=lambda value: value["period"]["end"])
        )
    if op == "maximum":
        _compatible(values, same_metric=True)
        if len(values) < 3 or len({value["value"] for value in values}) == 1:
            raise FinanceTaskBankError("degenerate_extremum")
        maximum = max(value["value"] for value in values)
        return _join(
            [child],
            op,
            {
                "value": maximum,
                "unit": values[0]["unit"],
                "period_ends": sorted(
                    value["period"]["end"]
                    for value in values
                    if value["value"] == maximum
                ),
            },
        )
    if node["method"] != "sum" or len(values) < 2:
        raise FinanceTaskBankError("redundant_singleton_or_unsupported_aggregate")
    if any(value["period"]["kind"] != "duration" for value in values):
        raise FinanceTaskBankError("nonadditive_metric")
    roles = {value["role"] for value in values}
    if len(roles) > 1:
        _compatible(values, same_period=True)
        if frozenset(roles) not in _CASH_GROUPS:
            raise FinanceTaskBankError("unsupported_cross_metric_sum")
        answer = {
            "value": sum(value["value"] for value in values),
            "unit": values[0]["unit"],
            "period_end": values[0]["period"]["end"],
            "components": {
                value["role"]: value["value"]
                for value in sorted(values, key=lambda item: item["role"])
            },
        }
    else:
        _compatible(values, same_metric=True)
        answer = {
            "value": sum(value["value"] for value in values),
            "unit": values[0]["unit"],
            "period_ends": sorted(value["period"]["end"] for value in values),
        }
    return _join([child], op, answer)


def _canonical_program(node: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    if depth > 8 or not isinstance(node, dict):
        raise FinanceTaskBankError("invalid_program_node")
    if node.get("op") == "collect" and (
        not isinstance(node.get("items"), list) or not 1 <= len(node["items"]) <= 64
    ):
        raise FinanceTaskBankError("invalid_collect_items")
    result = {
        key: _canonical_program(value, depth + 1) if isinstance(value, dict) else value
        for key, value in node.items()
    }
    if node.get("op") == "collect":
        result["items"] = sorted(
            (_canonical_program(item, depth + 1) for item in node["items"]),
            key=_canonical,
        )
    return result


def _family(program: dict[str, Any]) -> str:
    if program["op"] == "aggregate" and program["input"]["op"] == "filter":
        return "filtered_aggregate"
    return "cash_reconciliation" if program["op"] == "reconcile" else program["op"]


def _scope(world: dict[str, Any], consumed: tuple[str, ...]) -> dict[str, Any]:
    facts = {fact["fact_id"]: fact for fact in world["facts"]}
    selected = [facts[identity] for identity in consumed]
    record_ids = {fact["record_id"] for fact in selected}
    filings = [
        {
            key: document[key]
            for key in (
                "record_id",
                "report_date",
                "filing_date",
                "source_url",
                "source_version",
            )
        }
        for document in world["documents"]
        if document["record_id"] in record_ids
    ]
    return {
        "issuer": dict(world["issuer"]),
        "reporting_basis": BASIS,
        "unit": UNIT,
        "metrics": sorted({fact["role"] for fact in selected}),
        "filings": filings,
    }


def _instruction(program: dict[str, Any], scope: dict[str, Any]) -> str:
    family = _family(program)
    if family == "lookup":
        return "Report the stated amount."
    if family == "delta":
        return "Report the later-period amount minus the earlier-period amount."
    if family == "maximum":
        return "Report every period end with the highest stated amount and that amount."
    if family == "cash_reconciliation":
        return "Report every specified cash component, their sum, the stated net cash change, and the sum-minus-stated residual."
    if family in {"filter", "filtered_aggregate"}:
        predicate = program if family == "filter" else program["input"]
        sign = "positive" if predicate["relation"] == "gt" else "negative"
        return (
            f"List periods with {sign} amounts, including each amount."
            if family == "filter"
            else f"Report the sum of {sign} reported annual amounts and their period ends."
        )
    if family == "ratio":
        numerator = next(role for role in scope["metrics"] if role != "revenue")
        return f"Report {METRICS[numerator][0]} divided by revenue in basis points, rounding to the nearest integer with ties away from zero; include both operands."
    if len(scope["metrics"]) > 1:
        return "Report the sum of the specified cash-flow components, listing each component."
    return "Report the sum of reported annual amounts across the specified periods."


def _question_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "issuer": scope["issuer"],
        "reporting_basis": BASIS,
        "unit": UNIT,
        "metrics": scope["metrics"],
        "filings": [
            {key: filing[key] for key in ("report_date", "filing_date", "source_url")}
            for filing in scope["filings"]
        ],
    }


def _question(program: dict[str, Any], scope: dict[str, Any]) -> str:
    lines = [
        f"Issuer: {json.dumps(scope['issuer']['name'], ensure_ascii=False)} (CIK {scope['issuer']['cik']}).",
        "Basis: use amounts as reported in exactly the specified annual filings.",
        f"Metrics: {json.dumps([METRICS[role][0] for role in scope['metrics']], ensure_ascii=False)}.",
        "Input unit: USD millions.",
    ]
    lines += [
        f"Filing: {filing['report_date']} | {filing['filing_date']} | {json.dumps(filing['source_url'], ensure_ascii=False)}."
        for filing in scope["filings"]
    ]
    return "\n".join([*lines, "Task: " + _instruction(program, scope)])


def parse_finance_question(question: str) -> dict[str, Any]:
    """Parse the compiler's controlled, human-readable scope grammar."""
    try:
        lines = question.splitlines()
        match = re.fullmatch(r'Issuer: (".*") \(CIK (\d{10})\)\.', lines[0])
        if (
            match is None
            or lines[1]
            != "Basis: use amounts as reported in exactly the specified annual filings."
            or lines[3] != "Input unit: USD millions."
        ):
            raise ValueError
        labels = json.loads(lines[2].removeprefix("Metrics: ")[:-1])
        inverse = {value[0]: key for key, value in METRICS.items()}
        roles = [inverse[label] for label in labels]
        filings = []
        for line in lines[4:-1]:
            item = re.fullmatch(
                r'Filing: (\d{4}-\d{2}-\d{2}) \| (\d{4}-\d{2}-\d{2}) \| (".*")\.', line
            )
            if item is None:
                raise ValueError
            filings.append(
                {
                    "report_date": item[1],
                    "filing_date": item[2],
                    "source_url": json.loads(item[3]),
                }
            )
        if not filings or not lines[-1].startswith("Task: "):
            raise ValueError
        return {
            "scope": {
                "issuer": {"name": json.loads(match[1]), "cik": match[2]},
                "reporting_basis": BASIS,
                "unit": UNIT,
                "metrics": roles,
                "filings": filings,
            },
            "instruction": lines[-1][6:],
        }
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise FinanceTaskBankError("question_scope_parse_failed") from error


def _answer(result: _Result, family: str) -> dict[str, Any]:
    if family == "lookup":
        value = result.value
        return {
            "value": value["value"],
            "unit": value["unit"],
            "period_end": value["period"]["end"],
        }
    if family == "filter":
        return {
            "unit": result.value[0]["unit"],
            "observations": [
                {"period_end": value["period"]["end"], "value": value["value"]}
                for value in result.value
            ],
        }
    return result.value


def _build_task(world: dict[str, Any], program: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(program, dict):
        raise FinanceTaskBankError("invalid_program_node")
    if program.get("op") == "collect":
        raise FinanceTaskBankError("collect_is_not_a_task_output")
    program = _canonical_program(program)
    facts = {fact["fact_id"]: fact for fact in world["facts"]}
    execution = _eval(program, facts)
    scope = _scope(world, execution.consumed)
    question = _question(program, scope)
    if parse_finance_question(question)["scope"] != _question_scope(scope):
        raise FinanceTaskBankError("question_scope_roundtrip_failed")
    selected = sorted(
        (facts[identity] for identity in execution.consumed),
        key=lambda fact: (fact["record_id"], fact["span"]["start"], fact["fact_id"]),
    )
    document_ids = {fact["record_id"] for fact in selected}
    evidence_documents = [
        {
            key: document[key]
            for key in (
                "record_id",
                "source_sha256",
                "report_date",
                "filing_date",
                "source_url",
                "reporting_basis",
                "source_version",
            )
        }
        for document in world["documents"]
        if document["record_id"] in document_ids
    ]
    evidence_spans = [
        {
            "fact_id": fact["fact_id"],
            "record_id": fact["record_id"],
            **fact["span"],
            "value": fact["value"],
            "unit": fact["unit"],
            "period": dict(fact["period"]),
            "source_version": fact["source_version"],
        }
        for fact in selected
    ]
    family = _family(program)
    return {
        "schema_version": TASK_SCHEMA,
        "compiler_revision": COMPILER_REVISION,
        "semantic_task_id": _sha(
            {"revision": COMPILER_REVISION, "program": program, "scope": scope}
        ),
        "world_id": world["world_id"],
        "world_instance_id": world["world_instance_id"],
        "source_collection_id": world["source_collection_id"],
        "split_group_id": world["split_group_id"],
        "source_manifest_sha256": world["source_manifest"]["sha256"],
        "family": family,
        "program": program,
        "scope": scope,
        "question": question,
        "answer": _answer(execution, family),
        "consumed_fact_ids": sorted(execution.consumed),
        "evidence_record_ids": [
            document["record_id"] for document in evidence_documents
        ],
        "evidence_documents": evidence_documents,
        "evidence_spans": evidence_spans,
        "operators": list(execution.operations),
        "data_stage": "compiled_semantic_task",
        "train_ready": False,
        "production_eligible": False,
        "reading_profile_candidate": "retrieval"
        if len(execution.consumed) == 1
        else "integration",
        "strict_long_dependency_verified": False,
    }


def execute_finance_task(world: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """Execute a saved task against a freshly verified, unchanged world."""
    _check_world(world)
    if not isinstance(task, dict) or "program" not in task:
        raise FinanceTaskBankError("invalid_task_program")
    rebuilt = _build_task(world, task["program"])
    for key in ("scope", "question"):
        if not _same_typed(task.get(key), rebuilt[key]):
            raise FinanceTaskBankError("task_" + key + "_mismatch")
    return rebuilt["answer"]


def validate_finance_task(
    world: dict[str, Any], task: dict[str, Any]
) -> dict[str, Any]:
    """Raise on invalid saved tasks; return a compact positive replay receipt."""
    _check_world(world)
    if not isinstance(task, dict) or "program" not in task:
        raise FinanceTaskBankError("invalid_task_program")
    rebuilt = _build_task(world, task["program"])
    for key, value in rebuilt.items():
        if not _same_typed(task.get(key), value):
            raise FinanceTaskBankError("task_" + key + "_mismatch")
    return {
        "ok": True,
        "semantic_task_id": rebuilt["semantic_task_id"],
        "consumed_fact_count": len(rebuilt["consumed_fact_ids"]),
        "question_scope_roundtrip": True,
        "answer_replayed": True,
    }


def _lookup(fact: dict[str, Any]) -> dict[str, Any]:
    return {"op": "lookup", "fact_id": fact["fact_id"]}


def _collect(facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"op": "collect", "items": [_lookup(fact) for fact in facts]}


def compile_finance_taskbank(world: dict[str, Any]) -> dict[str, Any]:
    """Enumerate meaningful programs, type-check/execute them, and deduplicate semantics."""
    _check_world(world)
    tasks, rejections, applicability_rejections = [], [], []
    seen = set()
    by_role = defaultdict(list)
    by_document = defaultdict(dict)
    for fact in world["facts"]:
        by_role[fact["role"]].append(fact)
        by_document[fact["record_id"]][fact["role"]] = fact
    attempted = 0

    def attempt(program):
        nonlocal attempted
        attempted += 1
        try:
            task = _build_task(world, program)
            if task["semantic_task_id"] in seen:
                raise FinanceTaskBankError("semantic_duplicate")
            validate_finance_task(world, task)
        except FinanceTaskBankError as error:
            reason = str(error)
            rejections.append(
                {
                    "program": program,
                    "reason": reason,
                    "stage": "semantic_dedup"
                    if reason == "semantic_duplicate"
                    else "program_validation",
                }
            )
            return
        seen.add(task["semantic_task_id"])
        tasks.append(task)

    for role, series in sorted(by_role.items()):
        series = sorted(
            series, key=lambda fact: (fact["period"]["end"], fact["source_version"])
        )
        for fact in series:
            attempt(_lookup(fact))
        for left, right in combinations(series, 2):
            attempt({"op": "delta", "input": _collect([left, right])})
        for start in range(len(series)):
            for stop in range(start + 2, len(series) + 1):
                selected = series[start:stop]
                vector = _collect(selected)
                attempt({"op": "aggregate", "method": "sum", "input": vector})
                if len(selected) >= 3:
                    attempt({"op": "maximum", "input": vector})
                for relation in ("gt", "lt"):
                    filtered = {
                        "op": "filter",
                        "relation": relation,
                        "threshold": 0,
                        "input": vector,
                    }
                    attempt(filtered)
                    attempt({"op": "aggregate", "method": "sum", "input": filtered})
    for record_id, available in sorted(by_document.items()):
        for numerator, denominator in sorted(_RATIO_PAIRS):
            if {numerator, denominator} <= available.keys():
                attempt(
                    {
                        "op": "ratio",
                        "numerator": _lookup(available[numerator]),
                        "denominator": _lookup(available[denominator]),
                        "multiplier": 10000,
                        "rounding": "nearest_half_away_from_zero",
                    }
                )
            else:
                applicability_rejections.append(
                    {
                        "stage": "applicability",
                        "record_id": record_id,
                        "family": "ratio",
                        "reason": "missing_required_metric",
                        "metrics": [numerator, denominator],
                    }
                )
        for roles in sorted(_CASH_GROUPS, key=lambda group: tuple(sorted(group))):
            if roles <= available.keys():
                attempt(
                    {
                        "op": "aggregate",
                        "method": "sum",
                        "input": _collect([available[role] for role in sorted(roles)]),
                    }
                )
            else:
                applicability_rejections.append(
                    {
                        "stage": "applicability",
                        "record_id": record_id,
                        "family": "aggregate",
                        "reason": "missing_required_metric",
                        "metrics": sorted(roles),
                    }
                )
        components = {
            "cash_from_operations",
            "cash_from_investing",
            "cash_from_financing",
        }
        if "cash_fx_effect" in available:
            components.add("cash_fx_effect")
        if components | {"cash_period_change"} <= available.keys():
            attempt(
                {
                    "op": "reconcile",
                    "components": _collect(
                        [available[role] for role in sorted(components)]
                    ),
                    "reported": _lookup(available["cash_period_change"]),
                }
            )
        else:
            applicability_rejections.append(
                {
                    "stage": "applicability",
                    "record_id": record_id,
                    "family": "cash_reconciliation",
                    "reason": "missing_required_metric",
                }
            )
    tasks.sort(key=lambda task: task["semantic_task_id"])
    metrics = {
        "source_documents": len(world["documents"]),
        "source_facts": len(world["facts"]),
        "normalization_rejections": len(world["normalization_rejections"]),
        "attempted_programs": attempted,
        "accepted_tasks": len(tasks),
        "rejected_tasks": len(rejections),
        "applicability_rejections": len(applicability_rejections),
        "by_family": dict(sorted(Counter(task["family"] for task in tasks).items())),
        "by_rejection": dict(
            sorted(Counter(rejection["reason"] for rejection in rejections).items())
        ),
        "within_filing_tasks": sum(
            len(task["evidence_documents"]) == 1 for task in tasks
        ),
        "cross_filing_tasks": sum(
            len(task["evidence_documents"]) > 1 for task in tasks
        ),
        "by_fact_count": {
            str(count): number
            for count, number in sorted(
                Counter(len(task["consumed_fact_ids"]) for task in tasks).items()
            )
        },
        "train_ready_tasks": 0,
    }
    metrics["draft_candidates"] = attempted + len(applicability_rejections)
    metrics["uninstantiated_templates"] = len(applicability_rejections)
    metrics["by_applicability_rejection"] = dict(
        Counter(rejection["reason"] for rejection in applicability_rejections)
    )
    metrics["stages"] = {
        "applicability": {
            "input": metrics["draft_candidates"],
            "passed": attempted,
            "rejected": len(applicability_rejections),
        },
        "program_validation_and_dedup": {
            "input": attempted,
            "passed": len(tasks),
            "rejected": len(rejections),
        },
    }
    if attempted != len(tasks) + len(rejections):
        raise FinanceTaskBankError("compiler_funnel_does_not_conserve_programs")
    return {
        "schema_version": "longworld.finance-taskbank.v1",
        "compiler_revision": COMPILER_REVISION,
        "world_id": world["world_id"],
        "source_collection_id": world["source_collection_id"],
        "split_group_id": world["split_group_id"],
        "tasks": tasks,
        "rejections": rejections,
        "applicability_rejections": applicability_rejections,
        "metrics": metrics,
    }
