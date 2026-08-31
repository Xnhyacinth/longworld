from __future__ import annotations

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree

import pytest

from longworld.core.macrovintageworkflow import (
    BEA_GDP_GDI_VINTAGE_XLSX_URL,
    MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA,
    _cell_text,
    _xml,
    audit_macro_vintage_workflow_manifest,
    build_macro_vintage_workflow_from_fetch_inventory,
    parse_bea_vintage_xlsx,
)
from longworld.core.provenance import ProvenanceError
from scripts.fetch_macro_vintage_workflow import (
    HttpResponse,
    fetch_macro_vintage_workflow,
)


def _xlsx(
    *,
    workbook_relationship_extra: str = "",
    oversized: bool = False,
    first_value: str = "100.0",
    percent_unit: str = "Percent Change",
) -> bytes:
    strings = [
        "2025Q1",
        "Vintage",
        "Billions of Current Dollars",
        percent_unit,
        "from Preceding Period",
        "GDP",
        "GDI",
        "Real GDP",
        "Real GDI",
        "Release Date",
        "Advance",
        first_value,
        "1.0",
        "Apr 30, 2025 GDI not published",
        "Second",
        "101.0",
        "99.0",
        "1.2",
        "0.8",
        "May 29, 2025",
        "Third",
        "102.0",
        "100.0",
        "1.4",
        "0.9",
        "Jun 26, 2025",
    ]
    indexes = {value: index for index, value in enumerate(strings)}

    def cell(reference: str, value: str) -> str:
        return f'<c r="{reference}" t="s"><v>{indexes[value]}</v></c>'

    rows = [
        f'<row r="1">{cell("A1", "2025Q1")}</row>',
        (
            '<row r="2">'
            f"{cell('C2', 'Billions of Current Dollars')}"
            f"{cell('E2', percent_unit)}"
            "</row>"
        ),
        (f'<row r="3">{cell("E3", "from Preceding Period")}</row>'),
        (f'<row r="4">{cell("B4", "Vintage")}</row>'),
        (
            '<row r="5">'
            f"{cell('C5', 'GDP')}{cell('D5', 'GDI')}"
            f"{cell('E5', 'Real GDP')}{cell('F5', 'Real GDI')}"
            f"{cell('G5', 'Release Date')}"
            "</row>"
        ),
        (
            '<row r="6">'
            f"{cell('B6', 'Advance')}{cell('C6', first_value)}"
            f"{cell('E6', '1.0')}"
            f"{cell('G6', 'Apr 30, 2025 GDI not published')}"
            "</row>"
        ),
        (
            '<row r="7">'
            f"{cell('B7', 'Second')}{cell('C7', '101.0')}"
            f"{cell('D7', '99.0')}{cell('E7', '1.2')}"
            f"{cell('F7', '0.8')}{cell('G7', 'May 29, 2025')}"
            "</row>"
        ),
        (
            '<row r="8">'
            f"{cell('B8', 'Third')}{cell('C8', '102.0')}"
            f"{cell('D8', '100.0')}{cell('E8', '1.4')}"
            f"{cell('F8', '0.9')}{cell('G8', 'Jun 26, 2025')}"
            "</row>"
        ),
    ]
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Vintage History" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    relationships = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
    Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
    Target="sharedStrings.xml"/>
  {workbook_relationship_extra}
</Relationships>"""
    shared_strings = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in strings)
        + "</sst>"
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(rows)}</sheetData></worksheet>"
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("xl/workbook.xml", workbook)
        output.writestr("xl/_rels/workbook.xml.rels", relationships)
        output.writestr("xl/sharedStrings.xml", shared_strings)
        output.writestr("xl/worksheets/sheet1.xml", worksheet)
        if oversized:
            output.writestr("xl/unused.xml", b"x" * 4096)
    return archive.getvalue()


def _request(tmp_path: Path) -> Path:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": "longworld.macro-vintage-fetch-request.v1",
                "user_agent": "LongWorld/0.2 macro@example.org",
                "authorization": {
                    "record_id": "bea-public-probe-1",
                    "scope": "bounded BEA GDP/GDI vintage workbook probe",
                    "basis": "public-source research authorization",
                    "reviewed_at": "2026-08-31T12:00:00Z",
                    "allowed_actions": ["fetch_bea_gdp_gdi_vintage_xlsx"],
                },
                "url": BEA_GDP_GDI_VINTAGE_XLSX_URL,
                "max_retries": 1,
            }
        ),
        encoding="utf-8",
    )
    return request


def _inventory(tmp_path: Path) -> tuple[dict, Path, bytes]:
    source = _xlsx()

    def get(url: str, headers: dict[str, str], _timeout: float) -> HttpResponse:
        assert url == BEA_GDP_GDI_VINTAGE_XLSX_URL
        assert headers["User-Agent"] == "LongWorld/0.2 macro@example.org"
        return HttpResponse(
            body=source,
            status=200,
            final_url=url,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            last_modified="Wed, 26 Aug 2026 12:32:03 GMT",
            etag='"probe-etag"',
        )

    observed = iter(["2026-08-31T12:00:01Z", "2026-08-31T12:00:02Z"])
    path = fetch_macro_vintage_workflow(
        _request(tmp_path),
        tmp_path / "inventory",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-31T12:00:00Z",
        clock=observed.__next__,
    )
    return json.loads(path.read_text()), path, source


def test_parse_binds_cells_rows_and_three_vintage_trajectory() -> None:
    raw = _xlsx()
    parsed = parse_bea_vintage_xlsx(raw)
    digest = hashlib.sha256(raw).hexdigest()
    gdp = [
        item
        for item in parsed.observations
        if item["series_id"] == "BEA_GDP_CURRENT_DOLLARS"
    ]
    assert [item["value"] for item in gdp] == ["100.0", "101.0", "102.0"]
    assert [item["vintage_date"] for item in gdp] == [
        "2025-04-30",
        "2025-05-29",
        "2025-06-26",
    ]
    assert [item["provenance"]["row_number"] for item in gdp] == [6, 7, 8]
    assert [item["provenance"]["value_cell"] for item in gdp] == [
        "C6",
        "C7",
        "C8",
    ]
    assert all(item["source_sha256"] == digest for item in gdp)
    assert any(
        trajectory["series_id"] == "BEA_GDP_CURRENT_DOLLARS"
        and len(trajectory["observation_ids"]) == 3
        for trajectory in parsed.trajectories
    )
    assert (
        len(
            [
                relation
                for relation in parsed.relations
                if relation["series_id"] == "BEA_GDP_CURRENT_DOLLARS"
            ]
        )
        == 2
    )


def test_unchanged_snapshot_is_not_labeled_as_answer_changing_revision() -> None:
    parsed = parse_bea_vintage_xlsx(_xlsx(first_value="101.0"))
    transition = next(
        item
        for item in parsed.relations
        if item["series_id"] == "BEA_GDP_CURRENT_DOLLARS"
    )
    assert transition["kind"] == "supersedes_without_observed_value_change"
    assert transition["answer_value_changed"] is False


def test_xlsx_parser_fails_closed_on_external_relationship_and_archive_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = _xlsx(
        workbook_relationship_extra=(
            '<Relationship Id="rId9" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/externalLink" Target="https://evil.example/book.xlsx" '
            'TargetMode="External"/>'
        )
    )
    with pytest.raises(ProvenanceError, match="external"):
        parse_bea_vintage_xlsx(external)

    monkeypatch.setattr("longworld.core.macrovintageworkflow.MAX_XLSX_MEMBERS", 3)
    with pytest.raises(ProvenanceError, match="member count"):
        parse_bea_vintage_xlsx(_xlsx())
    monkeypatch.setattr("longworld.core.macrovintageworkflow.MAX_XLSX_MEMBERS", 64)
    monkeypatch.setattr(
        "longworld.core.macrovintageworkflow.MAX_XLSX_MEMBER_BYTES", 2048
    )
    with pytest.raises(ProvenanceError, match="member.*size"):
        parse_bea_vintage_xlsx(_xlsx(oversized=True))


def test_xlsx_parser_rejects_numeric_and_shared_string_amplification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ProvenanceError, match="macro value"):
        parse_bea_vintage_xlsx(_xlsx(first_value="1e1000000000"))
    with pytest.raises(ProvenanceError, match="macro value"):
        parse_bea_vintage_xlsx(_xlsx(first_value="1,,2"))
    with pytest.raises(ProvenanceError, match="unit headers"):
        parse_bea_vintage_xlsx(_xlsx(percent_unit="Percent Changed"))

    monkeypatch.setattr(
        "longworld.core.macrovintageworkflow.MAX_EXPANDED_CELL_CHARS", 64
    )
    with pytest.raises(ProvenanceError, match="expanded cell text"):
        parse_bea_vintage_xlsx(_xlsx())


def test_xlsx_parser_rejects_utf16_entity_and_negative_shared_index() -> None:
    entity_xml = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE a [<!ENTITY x "GDP">]><a>&x;</a>'
    ).encode("utf-16")
    with pytest.raises(ProvenanceError, match="strict UTF-8"):
        _xml(entity_xml, "entity fixture")

    cell = ElementTree.fromstring(
        '<c xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'r="A1" t="s"><v>-1</v></c>'
    )
    with pytest.raises(ProvenanceError, match="shared string index"):
        _cell_text(cell, ("first", "last"))


def test_fetch_and_build_keep_probe_disabled_and_bind_raw_response(
    tmp_path: Path,
) -> None:
    inventory, inventory_path, source = _inventory(tmp_path)
    receipt = inventory["fetch_receipt"]["retrieval"]
    digest = hashlib.sha256(source).hexdigest()
    assert receipt["sha256"] == digest
    assert receipt["raw_bytes"] == len(source)
    assert receipt["final_url"] == BEA_GDP_GDI_VINTAGE_XLSX_URL
    assert inventory["trust_scope"] == "local_probe"
    assert inventory["production_eligible"] is False
    assert inventory["generation_integration"] == "disabled"

    manifest = build_macro_vintage_workflow_from_fetch_inventory(
        inventory,
        inventory_path.parent,
        generated_at="2026-08-31T12:00:03Z",
        fetch_inventory_raw=inventory_path.read_bytes(),
    )
    assert manifest["schema_version"] == MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA
    assert manifest["trust_scope"] == "local_probe"
    assert manifest["diagnostic_only"] is True
    assert manifest["production_eligible"] is False
    assert manifest["semantic_facts_train_ready"] is False
    assert manifest["generation_integration"] == "disabled"
    assert manifest["raw_source_sha256"] == digest
    assert manifest["n_observations"] == len(manifest["observations"])
    assert manifest["n_trajectories"] >= 1
    assert all(item["source_sha256"] == digest for item in manifest["observations"])

    source_path = inventory_path.parent / receipt["retrieval_file"]
    source_path.write_bytes(source + b"tamper")
    with pytest.raises(ProvenanceError, match="hash mismatch"):
        build_macro_vintage_workflow_from_fetch_inventory(
            inventory,
            inventory_path.parent,
            generated_at="2026-08-31T12:00:03Z",
            fetch_inventory_raw=inventory_path.read_bytes(),
        )


def test_fetch_rejects_redirect_content_type_and_unapproved_action(
    tmp_path: Path,
) -> None:
    source = _xlsx()

    def redirect(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        return HttpResponse(
            source,
            200,
            "https://evil.example/vintage.xlsx",
            "application/octet-stream",
            "",
            "",
        )

    with pytest.raises(ProvenanceError, match="response metadata"):
        fetch_macro_vintage_workflow(
            _request(tmp_path),
            tmp_path / "redirect",
            http_get=redirect,
            sleep=lambda _seconds: None,
            generated_at="2026-08-31T12:00:00Z",
        )

    second = tmp_path / "second"
    second.mkdir()
    request_path = _request(second)
    payload = json.loads(request_path.read_text())
    payload["authorization"]["allowed_actions"].append("publish_training_data")
    request_path.write_text(json.dumps(payload))
    with pytest.raises(ProvenanceError, match="actions"):
        fetch_macro_vintage_workflow(
            request_path,
            second / "unauthorized",
            http_get=redirect,
            sleep=lambda _seconds: None,
            generated_at="2026-08-31T12:00:00Z",
        )


def test_builder_rejects_coordinated_receipt_url_rewrite(tmp_path: Path) -> None:
    inventory, inventory_path, _source = _inventory(tmp_path)
    forged = deepcopy(inventory)
    forged["fetch_receipt"]["retrieval"]["requested_url"] = (
        "https://evil.example/vintage.xlsx"
    )
    forged["fetch_receipt"]["retrieval"]["final_url"] = (
        "https://evil.example/vintage.xlsx"
    )
    forged_raw = (
        json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    with pytest.raises(ProvenanceError, match="URL"):
        build_macro_vintage_workflow_from_fetch_inventory(
            forged,
            inventory_path.parent,
            generated_at="2026-08-31T12:00:03Z",
            fetch_inventory_raw=forged_raw,
        )


@pytest.mark.parametrize("tamper", ("value", "evidence", "trajectory_order"))
def test_manifest_audit_replays_exact_source_semantics(
    tmp_path: Path, tamper: str
) -> None:
    inventory, inventory_path, source = _inventory(tmp_path)
    manifest = build_macro_vintage_workflow_from_fetch_inventory(
        inventory,
        inventory_path.parent,
        generated_at="2026-08-31T12:00:03Z",
        fetch_inventory_raw=inventory_path.read_bytes(),
    )
    corrupted = deepcopy(manifest)
    if tamper == "value":
        corrupted["observations"][0]["value"] = "999"
    elif tamper == "evidence":
        corrupted["relations"][0]["evidence"] = []
    else:
        corrupted["trajectories"][0]["observation_ids"].reverse()

    with pytest.raises(ProvenanceError, match="does not replay source bytes"):
        audit_macro_vintage_workflow_manifest(corrupted, source_raw=source)


def test_manifest_audit_rejects_top_level_provenance_tampering(
    tmp_path: Path,
) -> None:
    inventory, inventory_path, source = _inventory(tmp_path)
    manifest = build_macro_vintage_workflow_from_fetch_inventory(
        inventory,
        inventory_path.parent,
        generated_at="2026-08-31T12:00:03Z",
        fetch_inventory_raw=inventory_path.read_bytes(),
    )
    corrupted_payloads = []
    for field in ("authorization", "source_policies"):
        corrupted = deepcopy(manifest)
        corrupted[field] = {}
        corrupted_payloads.append(corrupted)
    corrupted = deepcopy(manifest)
    corrupted["fetch_inventory_sha256"] = "not-a-hash"
    corrupted_payloads.append(corrupted)
    corrupted = deepcopy(manifest)
    corrupted["fetch_receipt"]["retrieval"]["sha256"] = "0" * 64
    corrupted_payloads.append(corrupted)

    for corrupted in corrupted_payloads:
        with pytest.raises(ProvenanceError, match="macro vintage"):
            audit_macro_vintage_workflow_manifest(corrupted, source_raw=source)
