"""Source-column and disclosure-time behavior; fixtures are never source inventory."""

import pytest

from longworld.core import finance_disclosure_versions as versions


def test_grid_preserves_visible_header_and_source_offsets():
    text = '<table><tr><th colspan="3">December 31</th></tr><tr><th>Metric</th><th>2023</th><th>2022</th></tr><tr><td>Operating cash</td><td><ix:nonFraction>10</ix:nonFraction></td><td><ix:nonFraction>8</ix:nonFraction></td></tr></table>'
    grid = versions.CellGrid(text)
    cell, header = versions.column_header(
        grid, text.index("<ix:nonFraction>8"), "2022-12-31"
    )
    assert header["visible"] == "2022"
    assert "8" in text[cell["start"] : cell["end"]]


def test_visible_empty_cells_keep_comparative_columns_aligned():
    text = '<table><tr><th colspan="4">December 31</th></tr><tr><th colspan="2">2023</th><th colspan="2">2022</th></tr><tr><td><ix:nonFraction>10</ix:nonFraction></td><td/><td><ix:nonFraction>8</ix:nonFraction></td><td/></tr></table>'
    grid = versions.CellGrid(text)
    _, header = versions.column_header(
        grid, text.index("<ix:nonFraction>8"), "2022-12-31"
    )
    assert header["visible"] == "2022"


def test_column_requires_full_visible_period_date_not_just_year():
    text = "<table><tr><th>December 31, 2022</th></tr><tr><td><ix:nonFraction>8</ix:nonFraction></td></tr></table>"
    grid = versions.CellGrid(text)
    with pytest.raises(ValueError, match="date"):
        versions.column_header(grid, text.index("<ix:nonFraction>8"), "2022-12-30")


def fixture_world():
    valid = {"kind": "duration", "start": "2021-01-01", "end": "2021-12-31"}
    claims = []
    for rid, available, values in [
        (
            "old",
            "2022-01-27",
            {
                "cash_from_operations": 100,
                "cash_from_investing": -20,
                "cash_period_change": 5,
            },
        ),
        (
            "new",
            "2023-01-27",
            {
                "cash_from_operations": 90,
                "cash_from_investing": -30,
                "cash_period_change": -5,
            },
        ),
    ]:
        for metric, value in values.items():
            claims.append(
                {
                    "claim_id": rid + metric,
                    "record_id": rid,
                    "available_time": available,
                    "source_version": rid,
                    "metric": metric,
                    "valid_time": valid,
                    "value": value,
                }
            )
    native = versions.base._VerifiedWorld(
        {
            "documents": [{"record_id": "old"}, {"record_id": "new"}],
            "issuer": {"name": "Fixture", "cik": "0000000001"},
            "source_collection_id": "fixture",
            "split_group_id": "0000000001",
        },
        versions.base._LOAD_TOKEN,
    )
    return versions.DisclosureWorld(native, claims, [], {})


def test_asof_selects_available_version_and_latest_baseline_is_gold_blind():
    world = fixture_world()
    tasks = versions.compile_asof_tasks(world)
    task = next(
        t
        for t in tasks
        if t["family"] == "asof_disclosure_delta"
        and t["parameters"]["targets"][0]["metric"] == "cash_from_operations"
    )
    assert task["answer"]["before"] == 100 and task["answer"]["after"] == 90
    assert task["answer"]["difference"] == -10
    baseline = versions.shortcut_probes(world, task)
    task["answer"] = {"invented": "gold must not be consulted"}
    assert versions.shortcut_probes(world, task) == baseline
    assert baseline["latest_values_ignoring_availability_correct"] is False
    assert baseline["single_primary_statement_filing_sufficient"] == []


def test_version_condition_and_largest_change_ties():
    tasks = versions.compile_asof_tasks(fixture_world())
    sign = next(t for t in tasks if t["family"] == "asof_sign_transition")
    assert sign["answer"]["before_positive"] is True
    assert sign["answer"]["after_positive"] is False
    largest = next(t for t in tasks if t["family"] == "asof_largest_disclosure_change")
    assert largest["answer"]["absolute_difference"] == 10
    assert len(largest["answer"]["metrics"]) == 3


def test_version_world_mutation_is_rejected():
    world = fixture_world()
    world.claims[0]["value"] += 1
    with pytest.raises(ValueError, match="changed"):
        versions.compile_asof_tasks(world)


def test_manifest_reread_must_match_authenticated_bytes(tmp_path, monkeypatch):
    import json

    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {"listing": {"text": "unverified", "source_url": "https://issuer.test/"}}
        )
    )
    native = versions.base._VerifiedWorld(
        {
            "documents": [],
            "issuer": {"name": "Fixture", "cik": "0000000001"},
            "source_manifest": {"path": str(path), "sha256": "0" * 64},
        },
        versions.base._LOAD_TOKEN,
    )
    monkeypatch.setattr(versions, "load_world", lambda config: native)
    with pytest.raises(ValueError, match="changed"):
        versions.load_disclosure_world({})


def test_raw_window_probe_is_separate_from_compact_representation():
    from scripts.materialize_finance_disclosure_versions import raw_window_coverage

    offsets = [(i, i + 1) for i in range(20000)]
    result = raw_window_coverage(
        offsets,
        [
            {"context_start": 10, "context_end": 12},
            {"context_start": 19000, "context_end": 19002},
        ],
    )
    assert result["any_contiguous_window_covers_canonical_numeric_operands"] == {
        "4096": False,
        "8192": False,
        "16384": False,
    }
    assert result["gold_answer_used"] is False
    assert result["solver_correctness_tested"] is False


@pytest.mark.parametrize("sign,expected", [("-", 1), ("", 0)])
def test_visible_sign_disagreement_is_not_a_disclosure_change(sign, expected):
    import hashlib

    prefix = '<xbrli:context id="a"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2021-01-01</xbrli:startDate><xbrli:endDate>2021-12-31</xbrli:endDate></xbrli:period></xbrli:context><xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    table = (
        '<table><tr><th>Metric</th><th>December 31, 2021</th></tr><tr><td>Investing cash</td><td>(<ix:nonFraction id="f" name="us-gaap:NetCashProvidedByUsedInInvestingActivities" contextRef="a" unitRef="usd" scale="6" decimals="-6" sign="'
        + sign
        + '">10</ix:nonFraction>)</td></tr></table>'
    )
    text = prefix + table
    url = "https://issuer.test/annual.htm"
    document = {
        "record_id": "doc",
        "text": text,
        "report_date": "2021-12-31",
        "filing_date": "2022-01-27",
        "source_url": url,
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "sections": [
            {
                "section_id": "table",
                "start": len(prefix),
                "end": len(text),
                "title": "Cash flows",
            }
        ],
    }
    listing = {
        "source_url": "https://issuer.test/listing",
        "text": '<table><tr><td>01/27/22</td><td>10-K</td><td><a href="'
        + url
        + '">10-K</a></td></tr></table>',
    }
    claims, rejections, _ = versions.comparative_claims(
        {"documents": [document], "issuer": {"cik": "0000000001"}}, listing
    )
    assert len(claims) == expected
    if expected:
        assert claims[0]["value"] == -10
    else:
        assert rejections[0]["reason"] == "inline sign/value differs from visible cell"
