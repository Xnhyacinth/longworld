"""Declarative, source-scoped selector by target-metric task compiler."""

from __future__ import annotations

from collections import Counter
from fractions import Fraction

from longworld.core import finance_taskbank as bank
from scripts.p95_report_finance_shared import digest

SCHEMA = "longworld.p96-finance-factorial.v1"
METRIC_LABELS = {
    "revenue": "revenue",
    "operating_income": "operating income",
    "cash_from_operations": "operating cash flow",
    "cash_from_investing": "investing cash flow",
    "cash_from_financing": "financing cash flow",
}
RATIO_PAIRS = (
    ("operating_income", "revenue"),
    ("cash_from_operations", "revenue"),
)
TARGET_METRICS = tuple(METRIC_LABELS)
SELECTOR_KINDS = (
    "max_value",
    "min_value",
    "max_abs_delta",
    "max_growth",
    "max_ratio",
    "min_ratio",
    "median_ratio",
    "max_abs_delta_ratio",
)
TRANSITION_KINDS = {"max_abs_delta", "max_growth", "max_abs_delta_ratio"}
PRIOR_EQUIVALENTS = {
    ("median_ratio", ("operating_income", "revenue"), "cash_from_operations"),
    ("max_abs_delta", ("cash_from_operations",), "revenue"),
    ("max_growth", ("revenue",), "cash_from_operations"),
    ("max_abs_delta_ratio", ("operating_income", "revenue"), "cash_from_operations"),
    ("max_ratio", ("operating_income", "revenue"), "cash_from_operations"),
}


def selector_specs() -> list[dict]:
    selectors = []
    for kind in ("max_value", "min_value", "max_abs_delta"):
        selectors.extend(
            {"kind": kind, "metrics": [metric]} for metric in METRIC_LABELS
        )
    selectors.extend(
        {"kind": "max_growth", "metrics": [metric]}
        for metric in ("revenue", "cash_from_operations")
    )
    for kind in ("max_ratio", "min_ratio", "median_ratio", "max_abs_delta_ratio"):
        selectors.extend({"kind": kind, "metrics": list(pair)} for pair in RATIO_PAIRS)
    return selectors


def programs() -> list[dict]:
    return [
        {"selector": selector, "target_metric": target}
        for selector in selector_specs()
        for target in TARGET_METRICS
        if target not in selector["metrics"]
    ]


def _scores(records: list[dict], selector: dict) -> list[Fraction]:
    kind, metrics = selector["kind"], selector["metrics"]
    if kind not in SELECTOR_KINDS or len(metrics) not in {1, 2}:
        raise ValueError("unsupported selector")
    if len(metrics) == 2:
        if tuple(metrics) not in RATIO_PAIRS or kind not in {
            "max_ratio",
            "min_ratio",
            "median_ratio",
            "max_abs_delta_ratio",
        }:
            raise ValueError("ratio selector is not legal")
        numerator, denominator = metrics
        if any(row[denominator] <= 0 for row in records):
            raise ValueError("nonpositive_ratio_denominator")
        series = [Fraction(row[numerator], row[denominator]) for row in records]
    else:
        if kind not in {"max_value", "min_value", "max_abs_delta", "max_growth"}:
            raise ValueError("scalar selector is not legal")
        series = [Fraction(row[metrics[0]]) for row in records]
    if kind == "max_growth":
        if any(value <= 0 for value in series):
            raise ValueError("nonpositive_growth_denominator")
        return [
            (series[i] - series[i - 1]) / series[i - 1] for i in range(1, len(series))
        ]
    if kind in {"max_abs_delta", "max_abs_delta_ratio"}:
        return [abs(series[i] - series[i - 1]) for i in range(1, len(series))]
    return series


def execute(records: list[dict], program: dict) -> dict:
    """Run one declared program on four ordered, scoped numeric source rows."""
    if (
        not isinstance(records, list)
        or len(records) != 4
        or [row["year"] for row in records] != sorted(row["year"] for row in records)
        or len({row["year"] for row in records}) != 4
        or set(program) != {"selector", "target_metric"}
        or program["target_metric"] not in TARGET_METRICS
    ):
        raise ValueError("invalid annual source rows or program")
    selector = program["selector"]
    if (
        set(selector) != {"kind", "metrics"}
        or program["target_metric"] in selector["metrics"]
    ):
        raise ValueError("target metric overlaps selector")
    required = set(selector["metrics"]) | {program["target_metric"]}
    if any(any(type(row.get(role)) is not int for role in required) for row in records):
        raise ValueError("required metric missing or not an integer")
    scores = _scores(records, selector)
    kind = selector["kind"]
    if kind == "median_ratio":
        if len(set(scores)) != len(scores):
            raise ValueError("selector_tie")
        selected = sorted(range(4), key=lambda i: scores[i])[1]
    else:
        best = min(scores) if kind in {"min_value", "min_ratio"} else max(scores)
        if scores.count(best) != 1:
            raise ValueError("selector_tie")
        selected = scores.index(best) + (kind in TRANSITION_KINDS)
    target_values = [row[program["target_metric"]] for row in records]
    if len(set(target_values)) != len(target_values):
        raise ValueError("target_value_not_unique_by_year")
    answer = (
        {
            "transition": f"{records[selected - 1]['year']}–{records[selected]['year']}",
            "target_metric": program["target_metric"],
            "later_year_value_usd_millions": target_values[selected],
        }
        if kind in TRANSITION_KINDS
        else {
            "fiscal_year": records[selected]["year"],
            "target_metric": program["target_metric"],
            "value_usd_millions": target_values[selected],
        }
    )
    return {
        "answer": answer,
        "selected_index": selected,
        "scores_exact": [f"{value.numerator}/{value.denominator}" for value in scores],
    }


def _description(selector: dict) -> str:
    kind, metrics = selector["kind"], selector["metrics"]
    metric = METRIC_LABELS[metrics[0]]
    if len(metrics) == 2:
        metric = f"{metric} divided by {METRIC_LABELS[metrics[1]]}"
    return {
        "max_value": f"the highest {metric}",
        "min_value": f"the lowest {metric}",
        "max_abs_delta": f"the largest absolute adjacent-year change in {metric}",
        "max_growth": f"the greatest adjacent-year percentage growth in {metric}",
        "max_ratio": f"the highest ratio of {metric}",
        "min_ratio": f"the lowest ratio of {metric}",
        "median_ratio": f"the lower middle ratio of {metric}",
        "max_abs_delta_ratio": f"the largest absolute adjacent-year change in the ratio of {metric}",
    }[kind]


def _question(program: dict, years: list[str]) -> str:
    selector = program["selector"]
    target = METRIC_LABELS[program["target_metric"]]
    transition = selector["kind"] in TRANSITION_KINDS
    period = "transition" if transition else "fiscal year"
    target_period = "later year's" if transition else "selected year's"
    return (
        f"Across the original annual filings for {', '.join(years)}, which {period} has "
        f"{_description(selector)}? Then report {target} from the {target_period} "
        "own filing, in USD millions."
    )


def _source_rows(
    world: dict,
) -> tuple[list[dict], dict[tuple[str, str], dict], set[str]]:
    docs = sorted(
        world["documents"], key=lambda doc: (doc["report_date"], doc["record_id"])
    )
    if len(docs) != 4 or len({doc["report_date"] for doc in docs}) != 4:
        raise ValueError("P96 requires four distinct annual reports")
    facts = {}
    repeated = set()
    for fact in world["facts"]:
        if fact["role"] not in METRIC_LABELS:
            continue
        key = fact["record_id"], fact["role"]
        if key in facts:
            repeated.add(key)
        facts[key] = fact
    rows = []
    for doc in docs:
        row = {"record_id": doc["record_id"], "year": doc["report_date"][:4]}
        for metric in METRIC_LABELS:
            fact = facts.get((doc["record_id"], metric))
            if (
                fact is not None
                and (doc["record_id"], metric) not in repeated
                and fact["unit"] == bank.UNIT
                and fact["reporting_basis"] == bank.BASIS
                and fact["period"]["kind"] == "duration"
                and fact["period"]["end"] == doc["report_date"]
                and type(fact["value"]) is int
            ):
                row[metric] = fact["value"]
        rows.append(row)
    return rows, facts, repeated


def compile_world(
    world: dict, *, max_tasks: int = 16, answer_cap: int = 2
) -> tuple[list[dict], list[dict], dict]:
    if not 1 <= max_tasks <= 48 or not 1 <= answer_cap <= 4:
        raise ValueError("invalid task or answer-concentration budget")
    rows, facts, repeated = _source_rows(world)
    matrix, candidates = [], []
    for program in programs():
        selector = program["selector"]
        kind, metrics, target = (
            selector["kind"],
            tuple(selector["metrics"]),
            program["target_metric"],
        )
        signature = (kind, metrics, target)
        cell = {
            "selector": selector,
            "target_metric": target,
            "signature": [kind, list(metrics), target],
        }
        if signature in PRIOR_EQUIVALENTS:
            cell.update(
                status="blocked_prior_equivalent", reason="same program as P64/P95"
            )
        else:
            try:
                result = execute(rows, program)
            except ValueError as error:
                cell.update(status="unsupported_or_degenerate", reason=str(error))
            else:
                selected = result["selected_index"]
                consumed = {
                    facts[row["record_id"], role]["fact_id"]
                    for row in rows
                    for role in metrics
                }
                consumed.add(facts[rows[selected]["record_id"], target]["fact_id"])
                task = {
                    "program": program,
                    "operation": f"{kind}:{'+'.join(metrics)}->{target}",
                    "semantic_task_id": "task:"
                    + digest(
                        [
                            SCHEMA,
                            world["world_instance_id"],
                            program,
                            [row["record_id"] for row in rows],
                        ]
                    ),
                    "question": _question(program, [row["year"] for row in rows]),
                    "answer": result["answer"],
                    "fact_ids": sorted(consumed),
                    "required_record_ids": [row["record_id"] for row in rows],
                    "trace": {
                        "selected_record_id": rows[selected]["record_id"],
                        "scores_exact": result["scores_exact"],
                    },
                }
                cell.update(
                    status="legal_candidate",
                    reason="",
                    selected_year=rows[selected]["year"],
                )
                candidates.append((task, cell, selected))
        matrix.append(cell)
    picked, families, targets, positions, answers = (
        [],
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    while candidates and len(picked) < max_tasks:
        candidates.sort(
            key=lambda entry: (
                families[entry[0]["program"]["selector"]["kind"]],
                targets[entry[0]["program"]["target_metric"]],
                positions[entry[2]],
                entry[2] in {0, 3},
                digest([world["world_instance_id"], entry[0]["program"]]),
            )
        )
        task, cell, selected = candidates.pop(0)
        target = task["program"]["target_metric"]
        key = (
            target,
            selected,
            task["answer"].get(
                "value_usd_millions",
                task["answer"].get("later_year_value_usd_millions"),
            ),
        )
        if answers[key] >= answer_cap:
            cell.update(
                status="rejected_answer_concentration",
                reason="target/year/value bucket full",
            )
            continue
        cell.update(status="selected", reason="")
        picked.append(task)
        families[task["program"]["selector"]["kind"]] += 1
        targets[target] += 1
        positions[selected] += 1
        answers[key] += 1
    for _, cell, _ in candidates:
        cell.update(status="not_selected_budget", reason="world task budget reached")
    return (
        picked,
        matrix,
        {
            "source_metric_roles": sorted(
                {role for row in rows for role in row if role in METRIC_LABELS}
            ),
            "ambiguous_source_metric_slots": len(repeated),
            "matrix_cells": len(matrix),
            "cell_statuses": dict(
                sorted(Counter(cell["status"] for cell in matrix).items())
            ),
            "selected_families": dict(sorted(families.items())),
            "selected_targets": dict(sorted(targets.items())),
            "selected_year_positions": dict(sorted(positions.items())),
        },
    )
