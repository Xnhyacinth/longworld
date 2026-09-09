"""Gold-blind reader for visible Q4-style annual cash-flow tables.

Inputs are only a public question and visible latest-filing text. No source
attributes, answer values, evidence locations or fact IDs are accepted.
"""

from __future__ import annotations

import re
from datetime import datetime

from longworld.core.finance_taskbank import parse_finance_question

REVISION = "longworld.visible-q4-cashflow-reader.v1"
ROLES = frozenset(
    (
        "cash_from_operations",
        "cash_from_investing",
        "cash_from_financing",
        "cash_fx_effect",
        "cash_period_change",
    )
)


class Unsupported(ValueError):
    pass


def _role(label):
    label = re.sub(r"\s+", " ", label).strip().lower()
    for activity, role in (
        ("operating", "cash_from_operations"),
        ("investing", "cash_from_investing"),
        ("financing", "cash_from_financing"),
    ):
        if re.fullmatch(
            r"net cash (?:provided by|used in|used for)(?: \(used (?:in|for)\))? "
            + activity
            + r" activities",
            label,
        ):
            return role
    if re.fullmatch(
        r"(?:effect of (?:changes in currency exchange rates|exchange rate changes)|foreign currency effect) on cash(?:,.*| and cash equivalents)?",
        label,
    ):
        return "cash_fx_effect"
    if re.fullmatch(
        r"(?:net increase \(decrease\)|change) in cash(?:,.*| and cash equivalents)?",
        label,
    ):
        return "cash_period_change"
    return None


def _number(cell):
    cell = cell.strip().removeprefix("$").strip()
    if cell in {"—", "–", "-"}:
        return 0
    if not re.fullmatch(r"(?:-?[\d,]+|\([\d,]+\))", cell):
        raise Unsupported("non_integer_or_ambiguous_numeric_cell")
    negative = cell.startswith(("(", "-"))
    value = int(re.sub(r"[(),-]", "", cell))
    return -value if negative else value


def parse_cashflow_table(text):
    lines = text.splitlines(keepends=True)
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    titles = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(
            r"\s*consolidated statements of cash flows - USD \(\$\)\s*",
            line,
            re.IGNORECASE,
        )
    ]
    if not titles:
        raise Unsupported("no_supported_visible_q4_cashflow_table")
    tables = []
    for index in titles:
        stop = next(
            (
                i
                for i in range(index + 1, len(lines))
                if re.search(r" - (?:USD|\$ / shares|shares)", lines[i])
            ),
            len(lines),
        )
        block = lines[index:stop]
        if not any(
            re.fullmatch(r"\s*\$ in Millions\s*", line) for line in block[:5]
        ) or not any("12 Months Ended" in line for line in block[:7]):
            continue
        dates = []
        for line in block[1:20]:
            candidate = line.strip().replace(".", "")
            if re.fullmatch(r"[A-Za-z]{3} \d{1,2}, \d{4}", candidate):
                dates.append(
                    datetime.strptime(candidate, "%b %d, %Y").date().isoformat()  # noqa: DTZ007 - a fiscal date, not a timestamp
                )
        if len(dates) < 2 or len(set(dates)) != len(dates):
            continue
        values = {}
        witnesses = []
        for local, line in enumerate(block):
            cells = [c.strip() for c in line.strip().split("\t") if c.strip()]
            if not cells:
                continue
            role = _role(cells[0])
            if role is None:
                continue
            if len(cells[1:]) != len(dates):
                raise Unsupported("row_year_column_count_mismatch")
            observed = dict(zip(dates, map(_number, cells[1:]), strict=True))
            if role in values and values[role] != observed:
                raise Unsupported("conflicting_visible_metric_rows")
            values[role] = observed
            witnesses.append(
                {
                    "metric": role,
                    "label": cells[0],
                    "start": starts[index + local],
                    "end": starts[index + local] + len(line),
                }
            )
        if values:
            tables.append(
                {
                    "values": values,
                    "dates": dates,
                    "start": starts[index],
                    "end": starts[stop] if stop < len(starts) else len(text),
                    "rows": witnesses,
                }
            )
    if not tables:
        raise Unsupported("unsupported_visible_year_or_unit_header")
    if any(t["values"] != tables[0]["values"] for t in tables[1:]):
        raise Unsupported("conflicting_cashflow_tables")
    return tables[0]


def read_latest_filing(question, visible_text):
    """Return a deterministic prediction, or an explicit unsupported reason."""
    try:
        parsed = parse_finance_question(question)
        scope, instruction = parsed["scope"], parsed["instruction"]
        roles = scope["metrics"]
        periods = sorted(f["report_date"] for f in scope["filings"])
        if not set(roles) <= ROLES:
            raise Unsupported("unsupported_non_cashflow_metric")
        table = parse_cashflow_table(visible_text)
        if any(role not in table["values"] for role in roles):
            raise Unsupported("requested_metric_absent_from_visible_table")
        if any(period not in table["dates"] for period in periods):
            raise Unsupported("requested_period_absent_from_latest_table")
        values = {
            role: {p: table["values"][role][p] for p in periods} for role in roles
        }
        unit = "USD_millions"
        if (
            instruction == "Report the stated amount."
            and len(roles) == len(periods) == 1
        ):
            answer = {
                "value": values[roles[0]][periods[0]],
                "unit": unit,
                "period_end": periods[0],
            }
        elif (
            instruction
            == "Report the later-period amount minus the earlier-period amount."
            and len(roles) == 1
            and len(periods) == 2
        ):
            answer = {
                "value": values[roles[0]][periods[1]] - values[roles[0]][periods[0]],
                "unit": unit,
                "earlier_period_end": periods[0],
                "later_period_end": periods[1],
            }
        elif (
            instruction
            == "Report every period end with the highest stated amount and that amount."
            and len(roles) == 1
        ):
            maximum = max(values[roles[0]].values())
            answer = {
                "value": maximum,
                "unit": unit,
                "period_ends": [p for p in periods if values[roles[0]][p] == maximum],
            }
        elif (
            instruction
            == "Report the sum of reported annual amounts across the specified periods."
            and len(roles) == 1
        ):
            answer = {
                "value": sum(values[roles[0]].values()),
                "unit": unit,
                "period_ends": periods,
            }
        elif (
            instruction
            == "Report the sum of the specified cash-flow components, listing each component."
            and len(periods) == 1
        ):
            components = {role: values[role][periods[0]] for role in roles}
            answer = {
                "value": sum(components.values()),
                "unit": unit,
                "period_end": periods[0],
                "components": components,
            }
        elif (
            instruction
            == "Report every specified cash component, their sum, the stated net cash change, and the sum-minus-stated residual."
            and len(periods) == 1
            and "cash_period_change" in roles
        ):
            components = {
                role: values[role][periods[0]]
                for role in roles
                if role != "cash_period_change"
            }
            reported = values["cash_period_change"][periods[0]]
            total = sum(components.values())
            answer = {
                "components": components,
                "component_sum": total,
                "reported_change": reported,
                "residual": total - reported,
                "unit": unit,
                "period_end": periods[0],
            }
        elif len(roles) == 1 and (
            match := re.fullmatch(
                r"List periods with (positive|negative) amounts, including each amount\.",
                instruction,
            )
        ):
            selected = [
                p
                for p in periods
                if (
                    values[roles[0]][p] > 0
                    if match[1] == "positive"
                    else values[roles[0]][p] < 0
                )
            ]
            answer = {
                "unit": unit,
                "observations": [
                    {"period_end": p, "value": values[roles[0]][p]} for p in selected
                ],
            }
        elif len(roles) == 1 and (
            match := re.fullmatch(
                r"Report the sum of (positive|negative) reported annual amounts and their period ends\.",
                instruction,
            )
        ):
            selected = [
                p
                for p in periods
                if (
                    values[roles[0]][p] > 0
                    if match[1] == "positive"
                    else values[roles[0]][p] < 0
                )
            ]
            answer = {
                "value": sum(values[roles[0]][p] for p in selected),
                "unit": unit,
                "period_ends": selected,
            }
        else:
            raise Unsupported("unsupported_question_operation")
        return {
            "status": "predicted",
            "answer": answer,
            "scope_status": "same_required_filing"
            if len(periods) == 1
            else "scope_mismatch_latest_comparatives_not_original_filings",
            "table": {k: v for k, v in table.items() if k != "values"},
            "strict_dependency_proof": False,
        }
    except Unsupported as error:
        return {
            "status": "unsupported",
            "reason": str(error),
            "strict_dependency_proof": False,
        }
