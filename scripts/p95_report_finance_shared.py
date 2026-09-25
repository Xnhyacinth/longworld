"""Source-bound multi-operation tasks over an issuer's annual report world."""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from typing import Any

from longworld.core import finance_taskbank as bank

OPERATIONS = (
    "middle_margin_then_cash",
    "largest_cash_jump_then_revenue",
    "max_revenue_growth_then_cash",
    "largest_margin_swing_then_cash",
)
REQUIRED = ("revenue", "operating_income", "cash_from_operations")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _annual(world: dict) -> tuple[list[dict], dict[tuple[str, str], dict]]:
    docs = sorted(world["documents"], key=lambda d: (d["report_date"], d["record_id"]))
    if not 3 <= len(docs) <= 6 or len({d["report_date"] for d in docs}) != len(docs):
        raise ValueError("requires three to six distinct annual report dates")
    facts: dict[tuple[str, str], dict] = {}
    duplicates = set()
    for fact in world["facts"]:
        if fact["role"] not in REQUIRED:
            continue
        key = fact["record_id"], fact["role"]
        if key in facts:
            duplicates.add(key)
        facts[key] = fact
    if duplicates:
        raise ValueError("ambiguous repeated financial metric")
    for doc in docs:
        for role in REQUIRED:
            fact = facts.get((doc["record_id"], role))
            if (
                fact is None
                or fact["unit"] != bank.UNIT
                or fact["reporting_basis"] != bank.BASIS
                or fact["period"]["kind"] != "duration"
                or fact["period"]["end"] != doc["report_date"]
                or type(fact["value"]) is not int
            ):
                raise ValueError(f"missing compatible annual metric: {role}")
    return docs, facts


def compile_tasks(world: dict) -> tuple[list[dict], list[dict]]:
    """Return independent task specs; source spans are bound during rendering."""
    docs, facts = _annual(world)
    years = [doc["report_date"][:4] for doc in docs]
    records = [doc["record_id"] for doc in docs]
    tasks, rejects = [], []
    margins = []
    if any(facts[identity, "revenue"]["value"] <= 0 for identity in records):
        rejects.append({"operation": OPERATIONS[0], "reason": "nonpositive_revenue"})
    else:
        margins = [
            Fraction(
                facts[identity, "operating_income"]["value"],
                facts[identity, "revenue"]["value"],
            )
            for identity in records
        ]
        if len(set(margins)) != len(margins):
            rejects.append({"operation": OPERATIONS[0], "reason": "margin_tie"})
        else:
            selected = sorted(range(len(docs)), key=lambda i: margins[i])[
                (len(docs) - 1) // 2
            ]
            target = facts[records[selected], "cash_from_operations"]
            lineage = [
                facts[identity, role]
                for identity in records
                for role in ("operating_income", "revenue")
            ] + [target]
            tasks.append(
                {
                    "operation": OPERATIONS[0],
                    "semantic_task_id": "task:"
                    + digest([world["world_instance_id"], OPERATIONS[0], records]),
                    "question": (
                        f"Across these {len(docs)} original annual filings ({', '.join(years)}), "
                        "which fiscal year has the lower middle operating margin "
                        "(operating income divided by revenue), and what operating cash flow "
                        "does that year's own filing report? Answer in USD millions."
                    ),
                    "answer": {
                        "fiscal_year": years[selected],
                        "operating_cash_flow_usd_millions": target["value"],
                    },
                    "fact_ids": sorted({fact["fact_id"] for fact in lineage}),
                    "required_record_ids": records,
                    "trace": {
                        "margins_exact": [
                            f"{value.numerator}/{value.denominator}"
                            for value in margins
                        ],
                        "selected_record_id": records[selected],
                    },
                }
            )
    cash = [facts[identity, "cash_from_operations"]["value"] for identity in records]
    jumps = [abs(cash[i] - cash[i - 1]) for i in range(1, len(cash))]
    if jumps.count(max(jumps)) != 1:
        rejects.append({"operation": OPERATIONS[1], "reason": "cash_jump_tie"})
    else:
        later = jumps.index(max(jumps)) + 1
        target = facts[records[later], "revenue"]
        lineage = [facts[identity, "cash_from_operations"] for identity in records] + [
            target
        ]
        tasks.append(
            {
                "operation": OPERATIONS[1],
                "semantic_task_id": "task:"
                + digest([world["world_instance_id"], OPERATIONS[1], records]),
                "question": (
                    f"Across these {len(docs)} original annual filings ({', '.join(years)}), "
                    "which adjacent fiscal-year transition has the largest absolute change "
                    "in operating cash flow, and what revenue does the later year's own "
                    "filing report? Answer in USD millions."
                ),
                "answer": {
                    "transition": f"{years[later - 1]}–{years[later]}",
                    "later_year_revenue_usd_millions": target["value"],
                },
                "fact_ids": sorted({fact["fact_id"] for fact in lineage}),
                "required_record_ids": records,
                "trace": {
                    "absolute_cash_changes": jumps,
                    "selected_record_id": records[later],
                },
            }
        )
    if all(facts[identity, "revenue"]["value"] > 0 for identity in records):
        revenues = [facts[identity, "revenue"]["value"] for identity in records]
        growths = [
            Fraction(revenues[i] - revenues[i - 1], revenues[i - 1])
            for i in range(1, len(revenues))
        ]
        if growths.count(max(growths)) != 1:
            rejects.append({"operation": OPERATIONS[2], "reason": "revenue_growth_tie"})
        else:
            later = growths.index(max(growths)) + 1
            target = facts[records[later], "cash_from_operations"]
            lineage = [facts[identity, "revenue"] for identity in records] + [target]
            tasks.append(
                {
                    "operation": OPERATIONS[2],
                    "semantic_task_id": "task:"
                    + digest([world["world_instance_id"], OPERATIONS[2], records]),
                    "question": (
                        f"Across these {len(docs)} original annual filings ({', '.join(years)}), "
                        "which adjacent fiscal-year transition has the greatest percentage "
                        "growth in revenue, and what operating cash flow does the later "
                        "year's own filing report? Answer in USD millions."
                    ),
                    "answer": {
                        "transition": f"{years[later - 1]}–{years[later]}",
                        "later_year_operating_cash_flow_usd_millions": target["value"],
                    },
                    "fact_ids": sorted({fact["fact_id"] for fact in lineage}),
                    "required_record_ids": records,
                    "trace": {
                        "revenue_growth_exact": [
                            f"{value.numerator}/{value.denominator}"
                            for value in growths
                        ],
                        "selected_record_id": records[later],
                    },
                }
            )
        swings = [abs(margins[i] - margins[i - 1]) for i in range(1, len(margins))]
        if swings.count(max(swings)) != 1:
            rejects.append({"operation": OPERATIONS[3], "reason": "margin_swing_tie"})
        else:
            later = swings.index(max(swings)) + 1
            target = facts[records[later], "cash_from_operations"]
            lineage = [
                facts[identity, role]
                for identity in records
                for role in ("operating_income", "revenue")
            ] + [target]
            tasks.append(
                {
                    "operation": OPERATIONS[3],
                    "semantic_task_id": "task:"
                    + digest([world["world_instance_id"], OPERATIONS[3], records]),
                    "question": (
                        f"Across these {len(docs)} original annual filings ({', '.join(years)}), "
                        "which adjacent fiscal-year transition has the largest absolute "
                        "change in operating margin (operating income divided by revenue), "
                        "and what operating cash flow does the later year's own filing report? "
                        "Answer in USD millions."
                    ),
                    "answer": {
                        "transition": f"{years[later - 1]}–{years[later]}",
                        "later_year_operating_cash_flow_usd_millions": target["value"],
                    },
                    "fact_ids": sorted({fact["fact_id"] for fact in lineage}),
                    "required_record_ids": records,
                    "trace": {
                        "margin_swings_exact": [
                            f"{value.numerator}/{value.denominator}" for value in swings
                        ],
                        "selected_record_id": records[later],
                    },
                }
            )
    else:
        for operation in OPERATIONS[2:]:
            rejects.append({"operation": operation, "reason": "nonpositive_revenue"})
    return tasks, rejects
