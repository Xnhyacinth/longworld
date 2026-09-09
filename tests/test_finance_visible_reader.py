import pytest

from longworld.core.finance_visible_reader import (
    parse_cashflow_table,
    read_latest_filing,
)

TABLE = """Consolidated Statements of Cash Flows - USD ($)
 $ in Millions
 12 Months Ended
Dec. 31, 2024
Dec. 31, 2023
Dec. 31, 2022
\tNet cash provided by operating activities\t10\t(5)\t30
\tNet cash used in investing activities\t(8)\t(3)\t(4)
"""


def question(periods, instruction):
    return "\n".join(
        [
            'Issuer: "Fixture" (CIK 0000000001).',
            "Basis: use amounts as reported in exactly the specified annual filings.",
            'Metrics: ["operating cash flow"].',
            "Input unit: USD millions.",
            *[
                f'Filing: {p}-12-31 | {p + 1}-02-01 | "https://fixture/{p}".'
                for p in periods
            ],
            "Task: " + instruction,
        ]
    )


def test_reads_dates_and_parenthesized_negative_without_gold():
    result = read_latest_filing(
        question(
            [2023, 2024],
            "Report the later-period amount minus the earlier-period amount.",
        ),
        TABLE,
    )
    assert result["answer"]["value"] == 15
    assert result["scope_status"].startswith("scope_mismatch")


def test_missing_year_and_units_do_not_fall_back_to_guessing():
    result = read_latest_filing(
        question(
            [2021, 2024],
            "Report the later-period amount minus the earlier-period amount.",
        ),
        TABLE,
    )
    assert result["reason"] == "requested_period_absent_from_latest_table"
    with pytest.raises(ValueError):
        parse_cashflow_table(TABLE.replace("$ in Millions", "$ in Thousands"))


def test_conflicting_metric_rows_are_unsupported():
    with pytest.raises(ValueError, match="conflicting"):
        parse_cashflow_table(
            TABLE + "\tNet cash provided by operating activities\t99\t99\t99\n"
        )


def test_changed_visible_values_change_prediction():
    q = question([2024], "Report the stated amount.")
    assert read_latest_filing(q, TABLE)["answer"]["value"] == 10
    assert (
        read_latest_filing(q, TABLE.replace("\t10\t", "\t20\t"))["answer"]["value"]
        == 20
    )
