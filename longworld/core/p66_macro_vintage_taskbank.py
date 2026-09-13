"""Mechanical programs for the P66 BEA vintage-history taskbank."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from itertools import pairwise

REVISION = "longworld.p66-macro-vintage-taskbank.v1"
PROGRAMS = (
    "full_revision_path",
    "revision_change_summary",
    "largest_absolute_revision",
)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def execute(observations: list[dict[str, object]], program: str) -> dict[str, object]:
    if program not in PROGRAMS:
        raise ValueError("unknown macro vintage program")
    ordered = sorted(
        observations,
        key=lambda row: (str(row["vintage_date"]), str(row["observation_id"])),
    )
    path = [
        {
            "vintage_date": str(row["vintage_date"]),
            "value": str(row["value"]),
            "observation_id": str(row["observation_id"]),
        }
        for row in ordered
    ]
    if program == "full_revision_path":
        return {"observations": path}
    if len(path) < 2:
        return {"status": "insufficient_observations"}
    transitions = []
    for before, after in pairwise(path):
        delta = Decimal(after["value"]) - Decimal(before["value"])
        transitions.append(
            {
                "from_vintage": before["vintage_date"],
                "to_vintage": after["vintage_date"],
                "delta": decimal_text(delta),
                "absolute_delta": decimal_text(abs(delta)),
            }
        )
    if program == "revision_change_summary":
        return {
            "first_vintage": path[0]["vintage_date"],
            "last_vintage": path[-1]["vintage_date"],
            "first_value": path[0]["value"],
            "last_value": path[-1]["value"],
            "net_change": decimal_text(
                Decimal(path[-1]["value"]) - Decimal(path[0]["value"])
            ),
            "changed_transitions": sum(item["delta"] != "0" for item in transitions),
            "unchanged_transitions": sum(item["delta"] == "0" for item in transitions),
            "total_transitions": len(transitions),
        }
    maximum = max(Decimal(item["absolute_delta"]) for item in transitions)
    return {
        "maximum_absolute_delta": decimal_text(maximum),
        "transitions": [
            item for item in transitions if Decimal(item["absolute_delta"]) == maximum
        ],
    }


def render_block(kind: str, row: dict[str, object]) -> str:
    if kind == "observation":
        fields = {
            key: row[key]
            for key in (
                "observation_id",
                "series_id",
                "period",
                "estimate_label",
                "vintage_date",
                "available_at",
                "value",
                "unit",
                "release_date_text",
                "provenance",
            )
        }
        return "\n=== BEA vintage observation ===\n" + canonical(fields) + "\n"
    fields = {
        key: row[key]
        for key in (
            "relation_id",
            "kind",
            "series_id",
            "period",
            "source_observation_id",
            "target_observation_id",
            "source_vintage_date",
            "answer_value_changed",
            "relation_provenance",
            "evidence",
        )
        if key in row
    }
    return "\n=== BEA vintage relation ===\n" + canonical(fields) + "\n"


def question(series_id: str, period: str, program: str) -> str:
    instruction = {
        "full_revision_path": "Return every observation for the target in vintage-date order.",
        "revision_change_summary": "Summarize the first and last values and count changed and unchanged adjacent transitions.",
        "largest_absolute_revision": "Return every adjacent transition tied for the largest absolute revision.",
    }[program]
    return (
        f"Using only the supplied authenticated BEA records, evaluate series {series_id} for period {period}. "
        "Match series_id and period exactly. Treat available_at as the vintage availability date and use decimal arithmetic. "
        + instruction
        + " Return canonical JSON using the field names demonstrated by the task schema."
    )
