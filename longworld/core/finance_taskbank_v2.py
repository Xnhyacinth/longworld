"""Dependent period selection over immutable, source-verified finance worlds.

The P63 compiler remains unchanged. These programs select periods by one metric
and consume that selection to query a different metric; values never come from
generated narrative or hidden answer parameters.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction

from longworld.core import finance_taskbank as base

REVISION = "longworld.finance-taskbank-dependent-selection.v1"
ERROR = base.FinanceTaskBankError
CONDITIONAL_PAIRS = (
    ("cash_from_investing", "cash_from_operations"),
    ("cash_from_financing", "cash_from_operations"),
    ("operating_income", "revenue"),
)


@dataclass
class Result:
    kind: str
    rows: list[dict]
    consumed: set[str]
    trace: list[dict]


def _fact(world, record_id, metric):
    values = [
        f for f in world["facts"] if f["record_id"] == record_id and f["role"] == metric
    ]
    if len(values) != 1:
        raise ERROR("missing_or_ambiguous_metric")
    return values[0]


def _node_keys(node, keys):
    if not isinstance(node, dict) or set(node) != set(keys):
        raise ERROR("invalid_v2_operator_fields")


def _eval(world, node, depth=0):
    if depth > 6 or not isinstance(node, dict):
        raise ERROR("invalid_v2_program_depth")
    op = node.get("op")
    if op in {"series", "margin_series"}:
        _node_keys(
            node,
            ("op", "record_ids", "metric") if op == "series" else ("op", "record_ids"),
        )
        ids = node["record_ids"]
        if (
            not isinstance(ids, list)
            or not 2 <= len(ids) <= 8
            or len(ids) != len(set(ids))
        ):
            raise ERROR("invalid_period_population")
        docs = {d["record_id"]: d for d in world["documents"]}
        if any(i not in docs for i in ids) or ids != sorted(
            ids, key=lambda i: (docs[i]["report_date"], i)
        ):
            raise ERROR("period_population_not_canonical")
        if len({docs[i]["report_date"] for i in ids}) != len(ids):
            raise ERROR("ambiguous_period_versions")
        rows, consumed = [], set()
        for identity in ids:
            roles = (
                [node["metric"]] if op == "series" else ["operating_income", "revenue"]
            )
            facts = [_fact(world, identity, role) for role in roles]
            if any(
                f["unit"] != base.UNIT
                or f["reporting_basis"] != base.BASIS
                or f["period"]["kind"] != "duration"
                for f in facts
            ):
                raise ERROR("incompatible_quantity_scope")
            if op == "margin_series" and facts[1]["value"] <= 0:
                raise ERROR("nonpositive_margin_denominator")
            value = (
                facts[0]["value"]
                if op == "series"
                else Fraction(facts[0]["value"], facts[1]["value"])
            )
            rows.append({"record_id": identity, "value": value})
            consumed.update(f["fact_id"] for f in facts)
        return Result(
            "quantities", rows, consumed, [{"op": op, "output_record_ids": ids}]
        )
    _node_keys(
        node,
        ("op", "input", "relation")
        if op == "select_sign"
        else ("op", "input", "metric")
        if op == "lookup_selected"
        else ("op", "input"),
    )
    child = _eval(world, node["input"], depth + 1)
    rows, consumed = child.rows, set(child.consumed)
    if op in {"select_sign", "select_max_margin", "select_margin_extremes"}:
        if child.kind != "quantities":
            raise ERROR("selector_requires_quantities")
        if op == "select_sign":
            if (
                node["relation"] not in {"gt_zero", "lt_zero"}
                or node["input"]["op"] != "series"
            ):
                raise ERROR("invalid_sign_selector")
            rows = (
                [r for r in rows if r["value"] > 0]
                if node["relation"] == "gt_zero"
                else [r for r in rows if r["value"] < 0]
            )
            if not rows or len(rows) == len(child.rows):
                raise ERROR("vacuous_selection")
        else:
            if node["input"]["op"] != "margin_series" or len(rows) < 3:
                raise ERROR("margin_selector_requires_three_periods")
            low, high = min(r["value"] for r in rows), max(r["value"] for r in rows)
            if low == high:
                raise ERROR("constant_margin_selection")
            highest = [r for r in rows if r["value"] == high]
            if op == "select_max_margin":
                rows = highest
            else:
                lowest = [r for r in rows if r["value"] == low]
                if len(lowest) != 1 or len(highest) != 1:
                    raise ERROR("ambiguous_extreme_ties")
                rows = lowest + highest
        kind = "selected_periods"
    elif op == "lookup_selected":
        if (
            child.kind != "selected_periods"
            or node["metric"] != "cash_from_operations"
            and node["metric"] != "revenue"
        ):
            raise ERROR("unsupported_selected_metric")
        selected = []
        for row in rows:
            fact = _fact(world, row["record_id"], node["metric"])
            if fact["unit"] != base.UNIT or fact["period"]["kind"] != "duration":
                raise ERROR("selected_metric_scope_mismatch")
            if fact["fact_id"] in consumed:
                raise ERROR("selector_and_target_are_same_fact")
            consumed.add(fact["fact_id"])
            selected.append({"record_id": row["record_id"], "value": fact["value"]})
        rows, kind = selected, "selected_quantities"
    elif op in {"sum_selected", "difference_extremes"}:
        if child.kind != "selected_quantities":
            raise ERROR("aggregate_requires_selected_quantities")
        lookup = node["input"]
        selector = lookup["input"]
        if op == "sum_selected":
            if selector["op"] != "select_sign" or len(rows) < 2:
                raise ERROR("redundant_conditional_sum")
            series = selector["input"]
            if (series["metric"], lookup["metric"]) not in CONDITIONAL_PAIRS:
                raise ERROR("unsupported_conditional_metric_pair")
            complete = [
                _fact(world, identity, lookup["metric"])["value"]
                for identity in series["record_ids"]
            ]
            if sum(r["value"] for r in rows) == sum(complete):
                raise ERROR("selection_does_not_change_total")
            kind = "sum_answer"
        else:
            if selector["op"] != "select_margin_extremes" or len(rows) != 2:
                raise ERROR("difference_requires_ordered_margin_extremes")
            if rows[0]["value"] == rows[1]["value"]:
                raise ERROR("selected_target_difference_is_zero")
            kind = "difference_answer"
    else:
        raise ERROR("unsupported_v2_operator")
    return Result(
        kind,
        rows,
        consumed,
        child.trace
        + [
            {
                "op": op,
                "input_record_ids": [r["record_id"] for r in child.rows],
                "output_record_ids": [r["record_id"] for r in rows],
            }
        ],
    )


def _family(program):
    families = {
        "sum_selected": "conditional_cross_metric_sum",
        "lookup_selected": "max_margin_selected_cashflow",
        "difference_extremes": "margin_extremes_cashflow_difference",
    }
    if not isinstance(program, dict) or program.get("op") not in families:
        raise ERROR("unsupported_v2_task_family")
    return families[program["op"]]


def build_task(world, program):
    base._check_world(world)
    family = _family(program)
    result = _eval(world, program)
    if family == "max_margin_selected_cashflow" and (
        program["input"]["op"] != "select_max_margin"
        or program["metric"] != "cash_from_operations"
    ):
        raise ERROR("unsupported_final_selection_task")
    selected = {
        f["fact_id"]: f for f in world["facts"] if f["fact_id"] in result.consumed
    }
    scope = base._scope(world, tuple(sorted(selected)))
    prefix = base._question({"op": "lookup"}, scope).rsplit("Task: ", 1)[0]
    if family == "conditional_cross_metric_sum":
        lookup = program["input"]
        selector = lookup["input"]
        criterion, target = selector["input"]["metric"], lookup["metric"]
        sign = (
            "strictly positive"
            if selector["relation"] == "gt_zero"
            else "strictly negative"
        )
        instruction = f"Select the specified annual periods whose {base.METRICS[criterion][0]} is {sign}. Sum {base.METRICS[target][0]} for those selected periods only. Report the total and each selected period's target amount."
    elif family == "max_margin_selected_cashflow":
        instruction = "Compare operating income divided by revenue across the specified annual periods using exact ratios, without rounding. Select every period tied for the highest ratio. Report operating cash flow for each selected period."
    else:
        instruction = "Compare operating income divided by revenue across the specified annual periods using exact ratios, without rounding. Identify the unique lowest-margin and highest-margin periods. Report their operating cash flows and highest-margin-period cash flow minus lowest-margin-period cash flow."
    question = prefix + "Task: " + instruction
    if base.parse_finance_question(question)["scope"] != base._question_scope(scope):
        raise ERROR("v2_question_scope_mismatch")
    docs = {d["record_id"]: d for d in world["documents"]}
    observations = [
        {"period_end": docs[r["record_id"]]["report_date"], "value": r["value"]}
        for r in result.rows
    ]
    answer = {"unit": base.UNIT, "selected": observations}
    if result.kind == "sum_answer":
        answer["total"] = sum(r["value"] for r in result.rows)
    elif result.kind == "difference_answer":
        answer["highest_minus_lowest"] = (
            result.rows[1]["value"] - result.rows[0]["value"]
        )
    ordered = sorted(
        selected.values(), key=lambda f: (f["record_id"], f["span"]["start"])
    )
    evidence_docs = [
        {
            key: docs[i][key]
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
        for i in sorted({f["record_id"] for f in ordered})
    ]
    return {
        "schema_version": base.TASK_SCHEMA,
        "compiler_revision": REVISION,
        "semantic_task_id": base._sha(
            {"revision": REVISION, "program": program, "scope": scope}
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
        "answer": answer,
        "consumed_fact_ids": sorted(selected),
        "evidence_record_ids": [d["record_id"] for d in evidence_docs],
        "evidence_documents": evidence_docs,
        "evidence_spans": [
            {
                "fact_id": f["fact_id"],
                "record_id": f["record_id"],
                **f["span"],
                "value": f["value"],
                "unit": f["unit"],
                "period": dict(f["period"]),
                "source_version": f["source_version"],
            }
            for f in ordered
        ],
        "operators": [t["op"] for t in result.trace],
        "execution_trace": result.trace,
        "data_stage": "compiled_semantic_task",
        "train_ready": False,
        "production_eligible": False,
        "reading_profile_candidate": "integration",
        "strict_long_dependency_verified": False,
    }


def validate_task(world, task):
    if task.get("compiler_revision") == base.COMPILER_REVISION:
        return base.validate_finance_task(world, task)
    rebuilt = build_task(world, task["program"])
    if not base._same_typed(task, rebuilt):
        raise ERROR("dependent_task_binding_mismatch")
    return {"ok": True, "semantic_task_id": task["semantic_task_id"]}


def compile_taskbank(world):
    bank = base.compile_finance_taskbank(world)
    tasks = list(bank["tasks"])
    rejections = []
    documents = sorted(
        world["documents"], key=lambda d: (d["report_date"], d["record_id"])
    )
    attempts = 0
    for start in range(len(documents)):
        for stop in range(start + 3, len(documents) + 1):
            ids = [d["record_id"] for d in documents[start:stop]]
            programs = []
            for criterion, target in CONDITIONAL_PAIRS:
                for relation in ("gt_zero", "lt_zero"):
                    programs.append(
                        {
                            "op": "sum_selected",
                            "input": {
                                "op": "lookup_selected",
                                "metric": target,
                                "input": {
                                    "op": "select_sign",
                                    "relation": relation,
                                    "input": {
                                        "op": "series",
                                        "metric": criterion,
                                        "record_ids": ids,
                                    },
                                },
                            },
                        }
                    )
            margin = {"op": "margin_series", "record_ids": ids}
            programs.append(
                {
                    "op": "lookup_selected",
                    "metric": "cash_from_operations",
                    "input": {"op": "select_max_margin", "input": margin},
                }
            )
            programs.append(
                {
                    "op": "difference_extremes",
                    "input": {
                        "op": "lookup_selected",
                        "metric": "cash_from_operations",
                        "input": {"op": "select_margin_extremes", "input": margin},
                    },
                }
            )
            for program in programs:
                attempts += 1
                try:
                    task = build_task(world, program)
                    validate_task(world, task)
                    tasks.append(task)
                except ERROR as error:
                    rejections.append(
                        {
                            "stage": "dependent_program_validation",
                            "program": program,
                            "reason": str(error),
                        }
                    )
    tasks.sort(key=lambda t: t["semantic_task_id"])
    if len({t["semantic_task_id"] for t in tasks}) != len(tasks):
        raise ERROR("duplicate_task_semantics")
    return {
        **bank,
        "tasks": tasks,
        "rejections": bank["rejections"] + rejections,
        "metrics": {
            **bank["metrics"],
            "base_metrics": bank["metrics"],
            "base_accepted_tasks": len(bank["tasks"]),
            "dependent_attempted": attempts,
            "dependent_accepted": len(tasks) - len(bank["tasks"]),
            "dependent_rejected": len(rejections),
            "accepted_tasks": len(tasks),
            "attempted_programs": bank["metrics"]["attempted_programs"] + attempts,
            "draft_candidates": bank["metrics"]["draft_candidates"] + attempts,
            "rejected_tasks": bank["metrics"]["rejected_tasks"] + len(rejections),
            "by_family": dict(Counter(t["family"] for t in tasks)),
            "by_fact_count": dict(
                Counter(str(len(t["consumed_fact_ids"])) for t in tasks)
            ),
            "within_filing_tasks": sum(
                len(t["evidence_documents"]) == 1 for t in tasks
            ),
            "cross_filing_tasks": sum(len(t["evidence_documents"]) > 1 for t in tasks),
            "by_rejection": dict(
                Counter(r["reason"] for r in bank["rejections"] + rejections)
            ),
            "stages": {
                **bank["metrics"]["stages"],
                "dependent_program_validation": {
                    "input": attempts,
                    "passed": len(tasks) - len(bank["tasks"]),
                    "rejected": len(rejections),
                },
            },
        },
    }
