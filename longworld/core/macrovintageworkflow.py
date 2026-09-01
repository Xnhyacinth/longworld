"""Fail-closed parsing for the BEA GDP/GDI vintage workbook.

This module produces a raw-response-bound local probe inventory.  It does not
claim production provenance, training eligibility, or generation integration.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import zipfile
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

BEA_GDP_GDI_VINTAGE_XLSX_URL = (
    "https://apps.bea.gov/national/xls/gdp-gdi-vintage-history.xlsx"
)
BEA_TERMS_URL = "https://www.bea.gov/help/faq/147"
BEA_LICENSE = (
    "U.S. Bureau of Economic Analysis public-domain data; attribution requested."
)
MACRO_VINTAGE_FETCH_REQUEST_SCHEMA = "longworld.macro-vintage-fetch-request.v1"
MACRO_VINTAGE_FETCH_INVENTORY_SCHEMA = "longworld.macro-vintage-fetch-inventory.v1"
MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA = "longworld.macro-vintage-workflow-manifest.v1"
MACRO_VINTAGE_PARSER_REVISION = "longworld-bea-gdp-gdi-xlsx@1"
BEA_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
MAX_XLSX_BYTES = 8_000_000
MAX_XLSX_MEMBERS = 64
MAX_XLSX_MEMBER_BYTES = 4_000_000
MAX_XLSX_UNCOMPRESSED_BYTES = 16_000_000
MAX_XLSX_COMPRESSION_RATIO = 100
MAX_SHARED_STRINGS = 20_000
MAX_CELL_TEXT_CHARS = 2_048
MAX_ROWS = 20_000
MAX_CELLS = 200_000
MAX_EXPANDED_CELL_CHARS = 8_000_000
MAX_OBSERVATIONS = 100_000
MAX_MACRO_VINTAGE_WORKFLOW_MANIFEST_BYTES = 16_000_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUARTER = re.compile(r"^(?:19|20)\d{2}Q[1-4]$")
_CELL_REFERENCE = re.compile(r"^([A-Z]{1,3})([1-9]\d*)$")
_RELEASE_DATE = re.compile(r"^\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})(?:\s|$)")
_DECIMAL = re.compile(
    r"^[+-]?(?:(?:\d{1,18}|\d{1,3}(?:,\d{3}){1,5})(?:\.\d{0,12})?|\.\d{1,12})$"
)
_XML_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XML_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WORKBOOK_PART = "xl/workbook.xml"
_WORKBOOK_RELATIONSHIPS_PART = "xl/_rels/workbook.xml.rels"
_VINTAGE_SHEET_NAME = "Vintage History"
_SOURCE_FILENAME = "bea-gdp-gdi-vintage-history.xlsx"
_ALLOWED_ACTION = "fetch_bea_gdp_gdi_vintage_xlsx"
_SERIES = {
    "C": ("BEA_GDP_CURRENT_DOLLARS", "billions_current_dollars"),
    "D": ("BEA_GDI_CURRENT_DOLLARS", "billions_current_dollars"),
    "E": ("BEA_REAL_GDP_PERCENT_CHANGE", "percent_change_from_preceding_period"),
    "F": ("BEA_REAL_GDI_PERCENT_CHANGE", "percent_change_from_preceding_period"),
}
_EXPECTED_HEADERS = {
    "C": "GDP",
    "D": "GDI",
    "E": "Real GDP",
    "F": "Real GDI",
    "G": "Release Date",
}
_INVENTORY_FIELDS = {
    "schema_version",
    "source_status",
    "trust_scope",
    "diagnostic_only",
    "data_stage",
    "hybrid_train_ready",
    "production_eligible",
    "generation_integration",
    "semantic_facts_train_ready",
    "generated_at",
    "request_file",
    "request_sha256",
    "authorization",
    "source_policies",
    "source_file",
    "source_sha256",
    "raw_bytes",
    "fetch_receipt",
    "n_retrievals",
}


@dataclass(frozen=True)
class ParsedMacroVintageWorkbook:
    """Deterministic observations and revision chains parsed from one XLSX."""

    observations: tuple[dict[str, Any], ...]
    relations: tuple[dict[str, Any], ...]
    trajectories: tuple[dict[str, Any], ...]
    source_sha256: str
    worksheet_part: str


def _xml(raw: bytes, label: str) -> ElementTree.Element:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProvenanceError(f"BEA XLSX {label} is not strict UTF-8 XML") from error
    declaration = re.match(
        r"\s*<\?xml\s+[^>]*encoding=['\"]([^'\"]+)", text, re.IGNORECASE
    )
    if (
        text.startswith("\ufeff")
        or (declaration is not None and declaration.group(1).lower() != "utf-8")
        or "<!DOCTYPE" in text.upper()
        or "<!ENTITY" in text.upper()
    ):
        raise ProvenanceError(f"BEA XLSX {label} contains a forbidden XML declaration")
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ProvenanceError(f"BEA XLSX {label} is malformed") from error


def _safe_archive(raw: bytes) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_XLSX_BYTES:
        raise ProvenanceError("BEA XLSX raw response exceeds the size limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (OSError, zipfile.BadZipFile) as error:
        raise ProvenanceError("BEA XLSX response is not a valid ZIP archive") from error
    members = archive.infolist()
    if not members or len(members) > MAX_XLSX_MEMBERS:
        archive.close()
        raise ProvenanceError("BEA XLSX member count is invalid")
    indexed: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or any(part in {"", ".", ".."} for part in path.parts)
            or name in indexed
        ):
            archive.close()
            raise ProvenanceError("BEA XLSX contains an unsafe or duplicate member")
        if member.is_dir():
            continue
        if member.flag_bits & 0x1 or member.compress_type not in {
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
        }:
            archive.close()
            raise ProvenanceError("BEA XLSX member compression is unsupported")
        if member.file_size > MAX_XLSX_MEMBER_BYTES:
            archive.close()
            raise ProvenanceError("BEA XLSX member exceeds the size limit")
        total_size += member.file_size
        if total_size > MAX_XLSX_UNCOMPRESSED_BYTES:
            archive.close()
            raise ProvenanceError("BEA XLSX uncompressed size exceeds the limit")
        if member.file_size and (
            member.compress_size == 0
            or member.file_size
            > max(1, member.compress_size) * MAX_XLSX_COMPRESSION_RATIO
        ):
            archive.close()
            raise ProvenanceError("BEA XLSX member compression ratio is unsafe")
        indexed[name] = member
    for required in (_WORKBOOK_PART, _WORKBOOK_RELATIONSHIPS_PART):
        if required not in indexed:
            archive.close()
            raise ProvenanceError(f"BEA XLSX is missing {required}")
    if any(name.startswith("xl/externalLinks/") for name in indexed):
        archive.close()
        raise ProvenanceError("BEA XLSX contains an external link part")
    for name in indexed:
        if not name.endswith((".xml", ".rels")):
            continue
        content = archive.read(name)
        parsed_xml = _xml(content, name)
        if name.endswith(".rels"):
            for relationship in parsed_xml.findall(f"{{{_XML_REL}}}Relationship"):
                target = str(relationship.get("Target") or "")
                if relationship.get("TargetMode") == "External" or re.match(
                    r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target
                ):
                    archive.close()
                    raise ProvenanceError("BEA XLSX contains an external relationship")
    return archive, indexed


def _resolve_workbook_parts(
    archive: zipfile.ZipFile, indexed: dict[str, zipfile.ZipInfo]
) -> tuple[str, str]:
    workbook = _xml(archive.read(_WORKBOOK_PART), "workbook")
    relationships = _xml(
        archive.read(_WORKBOOK_RELATIONSHIPS_PART), "workbook relationships"
    )
    relation_targets: dict[str, tuple[str, str]] = {}
    for relationship in relationships.findall(f"{{{_XML_REL}}}Relationship"):
        relation_id = str(relationship.get("Id") or "")
        target = str(relationship.get("Target") or "")
        relation_type = str(relationship.get("Type") or "")
        if not relation_id or relation_id in relation_targets or not target:
            raise ProvenanceError("BEA XLSX workbook relationship is invalid")
        resolved = posixpath.normpath(posixpath.join("xl", target))
        if (
            target.startswith("/")
            or resolved.startswith("../")
            or not resolved.startswith("xl/")
        ):
            raise ProvenanceError("BEA XLSX workbook relationship target is unsafe")
        relation_targets[relation_id] = (relation_type, resolved)
    sheets = workbook.find(f"{{{_XML_MAIN}}}sheets")
    if sheets is None:
        raise ProvenanceError("BEA XLSX workbook has no sheets")
    matching = [
        sheet
        for sheet in sheets.findall(f"{{{_XML_MAIN}}}sheet")
        if sheet.get("name") == _VINTAGE_SHEET_NAME
    ]
    if len(matching) != 1:
        raise ProvenanceError("BEA XLSX Vintage History sheet is missing or duplicated")
    relation_id = str(matching[0].get(f"{{{_XML_DOC_REL}}}id") or "")
    relation = relation_targets.get(relation_id)
    if (
        relation is None
        or not relation[0].endswith("/worksheet")
        or relation[1] not in indexed
    ):
        raise ProvenanceError("BEA XLSX Vintage History worksheet binding is invalid")
    shared = next(
        (
            target
            for relation_type, target in relation_targets.values()
            if relation_type.endswith("/sharedStrings")
        ),
        "",
    )
    if not shared or shared not in indexed:
        raise ProvenanceError("BEA XLSX shared strings binding is missing")
    return relation[1], shared


def _shared_strings(root: ElementTree.Element) -> tuple[str, ...]:
    strings: list[str] = []
    for item in root.findall(f"{{{_XML_MAIN}}}si"):
        value = "".join(node.text or "" for node in item.iter(f"{{{_XML_MAIN}}}t"))
        if len(value) > MAX_CELL_TEXT_CHARS or len(strings) >= MAX_SHARED_STRINGS:
            raise ProvenanceError("BEA XLSX shared strings exceed the parse limit")
        strings.append(value)
    if not strings:
        raise ProvenanceError("BEA XLSX shared strings are empty")
    return tuple(strings)


def _cell_text(cell: ElementTree.Element, shared: tuple[str, ...]) -> str:
    if cell.find(f"{{{_XML_MAIN}}}f") is not None:
        raise ProvenanceError("BEA XLSX Vintage History contains a formula cell")
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        inline = cell.find(f"{{{_XML_MAIN}}}is")
        return (
            ""
            if inline is None
            else "".join(node.text or "" for node in inline.iter(f"{{{_XML_MAIN}}}t"))
        )
    value = cell.find(f"{{{_XML_MAIN}}}v")
    raw_value = "" if value is None or value.text is None else value.text
    if cell_type == "s":
        try:
            index = int(raw_value)
            if not 0 <= index < len(shared):
                raise ValueError
            return shared[index]
        except (ValueError, IndexError) as error:
            raise ProvenanceError("BEA XLSX shared string index is invalid") from error
    if cell_type not in {None, "n", "str"}:
        raise ProvenanceError("BEA XLSX cell type is unsupported")
    return raw_value


def _rows(
    root: ElementTree.Element, shared: tuple[str, ...]
) -> list[tuple[int, dict[str, tuple[str, str]]]]:
    sheet_data = root.find(f"{{{_XML_MAIN}}}sheetData")
    if sheet_data is None:
        raise ProvenanceError("BEA XLSX Vintage History sheet has no data")
    result: list[tuple[int, dict[str, tuple[str, str]]]] = []
    seen_rows: set[int] = set()
    cell_count = 0
    expanded_cell_chars = 0
    for row in sheet_data.findall(f"{{{_XML_MAIN}}}row"):
        if len(result) >= MAX_ROWS:
            raise ProvenanceError("BEA XLSX rows exceed the parse limit")
        try:
            row_number = int(str(row.get("r") or ""))
        except ValueError as error:
            raise ProvenanceError("BEA XLSX row number is invalid") from error
        if row_number < 1 or row_number in seen_rows:
            raise ProvenanceError("BEA XLSX row number is invalid or duplicated")
        seen_rows.add(row_number)
        cells: dict[str, tuple[str, str]] = {}
        for cell in row.findall(f"{{{_XML_MAIN}}}c"):
            cell_count += 1
            if cell_count > MAX_CELLS:
                raise ProvenanceError("BEA XLSX cells exceed the parse limit")
            reference = str(cell.get("r") or "")
            match = _CELL_REFERENCE.fullmatch(reference)
            if match is None or int(match.group(2)) != row_number:
                raise ProvenanceError("BEA XLSX cell reference is invalid")
            column = match.group(1)
            if column in cells:
                raise ProvenanceError("BEA XLSX row contains duplicate columns")
            value = _cell_text(cell, shared).strip()
            if len(value) > MAX_CELL_TEXT_CHARS:
                raise ProvenanceError("BEA XLSX cell text exceeds the parse limit")
            expanded_cell_chars += len(value)
            if expanded_cell_chars > MAX_EXPANDED_CELL_CHARS:
                raise ProvenanceError(
                    "BEA XLSX expanded cell text exceeds the parse limit"
                )
            cells[column] = (reference, value)
        result.append((row_number, cells))
    result.sort(key=lambda item: item[0])
    return result


def _decimal_text(value: str) -> str | None:
    stripped = value.strip()
    if not stripped or set(stripped) <= {"."}:
        return None
    if _DECIMAL.fullmatch(stripped) is None:
        raise ProvenanceError("BEA XLSX macro value is invalid")
    normalized = stripped.replace(",", "")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as error:
        raise ProvenanceError("BEA XLSX macro value is invalid") from error
    if not parsed.is_finite() or abs(parsed) > Decimal(1000000000000000):
        raise ProvenanceError("BEA XLSX macro value is invalid")
    return format(parsed, "f")


def _release_date(value: str) -> str:
    match = _RELEASE_DATE.match(value)
    if match is None:
        raise ProvenanceError("BEA XLSX release date is invalid")
    try:
        parsed = datetime.strptime(match.group(1), "%b %d, %Y").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ProvenanceError("BEA XLSX release date is invalid") from error
    return parsed.date().isoformat()


def parse_bea_vintage_xlsx(raw: bytes) -> ParsedMacroVintageWorkbook:
    """Parse exact BEA workbook cells into chronological revision trajectories."""
    digest = hashlib.sha256(raw).hexdigest()
    archive, indexed = _safe_archive(raw)
    try:
        worksheet_part, shared_part = _resolve_workbook_parts(archive, indexed)
        shared = _shared_strings(_xml(archive.read(shared_part), "shared strings"))
        rows = _rows(_xml(archive.read(worksheet_part), "Vintage History"), shared)
    finally:
        archive.close()
    observations: list[dict[str, Any]] = []
    current_period = ""
    current_period_cell = ""
    header_seen = False
    vintage_header_seen = False
    unit_fragments: dict[str, list[str]] = {"C": [], "E": []}
    seen_observations: set[tuple[str, str, str]] = set()
    for row_number, cells in rows:
        period_value = cells.get("A", ("", ""))[1]
        if _QUARTER.fullmatch(period_value):
            current_period = period_value
            current_period_cell = cells["A"][0]
            header_seen = False
            vintage_header_seen = False
            unit_fragments = {"C": [], "E": []}
        if not vintage_header_seen:
            for column in unit_fragments:
                fragment = cells.get(column, ("", ""))[1]
                if fragment:
                    unit_fragments[column].append(fragment)
        if cells.get("B", ("", ""))[1] == "Vintage":
            vintage_header_seen = True
        if vintage_header_seen and all(
            cells.get(column, ("", ""))[1] == value
            for column, value in _EXPECTED_HEADERS.items()
        ):
            if (
                " ".join(unit_fragments["C"]) != "Billions of Current Dollars"
                or " ".join(unit_fragments["E"])
                != "Percent Change from Preceding Period"
            ):
                raise ProvenanceError("BEA XLSX macro unit headers are invalid")
            header_seen = True
            continue
        label_reference, estimate_label = cells.get("B", ("", ""))
        release_reference, release_text = cells.get("G", ("", ""))
        if (
            not header_seen
            or not current_period
            or not estimate_label
            or not release_text
        ):
            continue
        vintage_date = _release_date(release_text)
        for column, (series_id, unit) in _SERIES.items():
            value_reference, raw_value = cells.get(column, ("", ""))
            value = _decimal_text(raw_value)
            if value is None:
                continue
            identity = (series_id, current_period, vintage_date)
            if identity in seen_observations:
                raise ProvenanceError("BEA XLSX observation identity is duplicated")
            seen_observations.add(identity)
            observation_id = f"bea:{series_id.lower()}:{current_period}:{vintage_date}:row{row_number}"
            selected = {
                "series_id": series_id,
                "period": current_period,
                "estimate_label": estimate_label,
                "vintage_date": vintage_date,
                "available_at": vintage_date,
                "value": value,
                "unit": unit,
            }
            text = json.dumps(selected, ensure_ascii=False, sort_keys=True)
            observations.append(
                {
                    "observation_id": observation_id,
                    "record_kind": "macro_vintage_observation",
                    **selected,
                    "release_date_text": release_text,
                    "source_sha256": digest,
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "provenance": {
                        "parser": MACRO_VINTAGE_PARSER_REVISION,
                        "sheet_name": _VINTAGE_SHEET_NAME,
                        "sheet_part": worksheet_part,
                        "row_number": row_number,
                        "period_cell": current_period_cell,
                        "estimate_label_cell": label_reference,
                        "value_cell": value_reference,
                        "release_date_cell": release_reference,
                    },
                }
            )
            if len(observations) > MAX_OBSERVATIONS:
                raise ProvenanceError("BEA XLSX observations exceed the parse limit")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        grouped[(observation["series_id"], observation["period"])].append(observation)
    relations: list[dict[str, Any]] = []
    trajectories: list[dict[str, Any]] = []
    for (series_id, period), group in sorted(grouped.items()):
        group.sort(
            key=lambda item: (
                item["vintage_date"],
                item["provenance"]["row_number"],
                item["observation_id"],
            )
        )
        relation_ids: list[str] = []
        for prior, current in pairwise(group):
            relation_id = (
                f"bea:transition:{series_id.lower()}:{period}:"
                f"{prior['vintage_date']}:{current['vintage_date']}"
            )
            relation_ids.append(relation_id)
            relations.append(
                {
                    "relation_id": relation_id,
                    "kind": (
                        "revises_observation"
                        if current["value"] != prior["value"]
                        else "supersedes_without_observed_value_change"
                    ),
                    "answer_value_changed": current["value"] != prior["value"],
                    "source_release_date_text": current["release_date_text"],
                    "series_id": series_id,
                    "period": period,
                    "source_observation_id": current["observation_id"],
                    "target_observation_id": prior["observation_id"],
                    "relation_provenance": "verified_derived_temporal_same_series",
                    "evidence": [
                        {
                            "observation_id": prior["observation_id"],
                            "cell_reference": prior["provenance"]["value_cell"],
                            "source_sha256": digest,
                        },
                        {
                            "observation_id": current["observation_id"],
                            "cell_reference": current["provenance"]["value_cell"],
                            "source_sha256": digest,
                        },
                    ],
                }
            )
        if len(group) >= 3:
            trajectories.append(
                {
                    "trajectory_id": f"bea:trajectory:{series_id.lower()}:{period}",
                    "series_id": series_id,
                    "period": period,
                    "observation_ids": [item["observation_id"] for item in group],
                    "relation_ids": relation_ids,
                }
            )
    if not observations or not trajectories:
        raise ProvenanceError(
            "BEA XLSX has no source-bound trajectory with at least three vintages"
        )
    return ParsedMacroVintageWorkbook(
        observations=tuple(observations),
        relations=tuple(relations),
        trajectories=tuple(trajectories),
        source_sha256=digest,
        worksheet_part=worksheet_part,
    )


def _observations_with_lineage(
    parsed: ParsedMacroVintageWorkbook,
) -> list[dict[str, Any]]:
    observations = [deepcopy(item) for item in parsed.observations]
    for observation in observations:
        observation.update(
            {
                "source_status": "local_probe_public_download",
                "source_origin": "local_probe_public_endpoint_observation",
                "source_url": BEA_GDP_GDI_VINTAGE_XLSX_URL,
                "retrieval_url": BEA_GDP_GDI_VINTAGE_XLSX_URL,
                "license": BEA_LICENSE,
                "terms_url": BEA_TERMS_URL,
                "attribution": "U.S. Bureau of Economic Analysis",
            }
        )
    return observations


def _authorization(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("macro vintage authorization is invalid")
    result: dict[str, Any] = {
        field: str(value.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    }
    if any(not item for item in result.values()):
        raise ProvenanceError("macro vintage authorization is invalid")
    _parse_timestamp(result["reviewed_at"], "authorization.reviewed_at")
    actions = value.get("allowed_actions")
    if actions != [_ALLOWED_ACTION]:
        raise ProvenanceError("macro vintage authorization actions are invalid")
    result["allowed_actions"] = list(actions)
    return result


def _request_binding(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "user_agent",
            "authorization",
            "url",
            "max_retries",
        }
        or value.get("schema_version") != MACRO_VINTAGE_FETCH_REQUEST_SCHEMA
        or value.get("url") != BEA_GDP_GDI_VINTAGE_XLSX_URL
    ):
        raise ProvenanceError("macro vintage fetch request binding is invalid")
    return {"authorization": _authorization(value.get("authorization"))}


def _retrieval(value: object) -> dict[str, Any]:
    fields = {
        "requested_url",
        "final_url",
        "status",
        "content_type",
        "redirect_chain",
        "observed_at",
        "sha256",
        "retrieval_file",
        "raw_bytes",
        "last_modified",
        "etag",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ProvenanceError("macro vintage fetch receipt is invalid")
    if (
        value.get("requested_url") != BEA_GDP_GDI_VINTAGE_XLSX_URL
        or value.get("final_url") != BEA_GDP_GDI_VINTAGE_XLSX_URL
        or value.get("status") != 200
        or value.get("content_type") != BEA_XLSX_CONTENT_TYPE
        or value.get("redirect_chain") != []
        or value.get("retrieval_file") != _SOURCE_FILENAME
        or _SHA256.fullmatch(str(value.get("sha256") or "")) is None
        or isinstance(value.get("raw_bytes"), bool)
        or not isinstance(value.get("raw_bytes"), int)
        or not 0 < value["raw_bytes"] <= MAX_XLSX_BYTES
        or not isinstance(value.get("last_modified"), str)
        or not isinstance(value.get("etag"), str)
    ):
        raise ProvenanceError(
            "macro vintage retrieval URL or response metadata is invalid"
        )
    _parse_timestamp(str(value.get("observed_at") or ""), "retrieval.observed_at")
    return dict(value)


def build_macro_vintage_workflow_from_fetch_inventory(
    payload: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
    fetch_inventory_raw: bytes,
) -> dict[str, Any]:
    """Rebind a disabled fetch inventory to exact workbook bytes and cells."""
    if set(payload) != _INVENTORY_FIELDS or not (
        payload.get("schema_version") == MACRO_VINTAGE_FETCH_INVENTORY_SCHEMA
        and payload.get("source_status") == "local_probe_public_download"
        and payload.get("trust_scope") == "local_probe"
        and payload.get("diagnostic_only") is True
        and payload.get("data_stage") == "source_inventory"
        and payload.get("hybrid_train_ready") is False
        and payload.get("production_eligible") is False
        and payload.get("generation_integration") == "disabled"
        and payload.get("semantic_facts_train_ready") is False
        and payload.get("n_retrievals") == 1
    ):
        raise ProvenanceError(
            "macro vintage fetch inventory schema or flags are invalid"
        )
    if (
        not isinstance(fetch_inventory_raw, bytes)
        or not fetch_inventory_raw
        or len(fetch_inventory_raw) > MAX_MANIFEST_BYTES
    ):
        raise ProvenanceError("macro vintage fetch inventory bytes are invalid")
    try:
        rebound_inventory = json.loads(fetch_inventory_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            "macro vintage fetch inventory is not UTF-8 JSON"
        ) from error
    if rebound_inventory != payload:
        raise ProvenanceError("macro vintage fetch inventory bytes do not bind payload")
    fetch_inventory_sha256 = hashlib.sha256(fetch_inventory_raw).hexdigest()
    manifest_time = _parse_timestamp(generated_at, "generated_at")
    started_time = _parse_timestamp(
        str(payload.get("generated_at") or ""), "fetch generated_at"
    )
    request_file = payload.get("request_file")
    if (
        not isinstance(request_file, str)
        or Path(request_file).name != request_file
        or request_file != "macro_vintage_fetch_request.json"
    ):
        raise ProvenanceError("macro vintage request file is unsafe")
    try:
        request_raw = _read_regular_file(
            base_directory / request_file, MAX_MANIFEST_BYTES
        )
    except OSError as error:
        raise ProvenanceError(f"cannot read macro vintage request: {error}") from error
    if hashlib.sha256(request_raw).hexdigest() != payload.get("request_sha256"):
        raise ProvenanceError("macro vintage request hash mismatch")
    try:
        request_payload = json.loads(request_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("macro vintage request is not UTF-8 JSON") from error
    request = _request_binding(request_payload)
    if request["authorization"] != payload.get("authorization"):
        raise ProvenanceError("macro vintage authorization is not request-bound")
    if payload.get("source_policies") != {
        "bea": {"terms_url": BEA_TERMS_URL, "license": BEA_LICENSE}
    }:
        raise ProvenanceError("macro vintage source policy is invalid")
    receipt = payload.get("fetch_receipt")
    if not isinstance(receipt, dict) or set(receipt) != {
        "started_at",
        "completed_at",
        "retrieval",
    }:
        raise ProvenanceError("macro vintage fetch receipt is invalid")
    retrieval = _retrieval(receipt.get("retrieval"))
    observed_time = _parse_timestamp(retrieval["observed_at"], "retrieval.observed_at")
    completed_time = _parse_timestamp(
        str(receipt.get("completed_at") or ""), "fetch completed_at"
    )
    if receipt.get("started_at") != payload.get("generated_at") or not (
        started_time < observed_time < completed_time <= manifest_time
    ):
        raise ProvenanceError("macro vintage fetch timeline is invalid")
    if (
        payload.get("source_file") != retrieval["retrieval_file"]
        or payload.get("source_sha256") != retrieval["sha256"]
        or payload.get("raw_bytes") != retrieval["raw_bytes"]
    ):
        raise ProvenanceError("macro vintage source receipt binding is invalid")
    try:
        source_raw = _read_regular_file(
            base_directory / retrieval["retrieval_file"], MAX_XLSX_BYTES
        )
    except OSError as error:
        raise ProvenanceError(f"cannot read macro vintage source: {error}") from error
    if hashlib.sha256(source_raw).hexdigest() != retrieval["sha256"]:
        raise ProvenanceError("macro vintage source hash mismatch")
    if len(source_raw) != retrieval["raw_bytes"]:
        raise ProvenanceError("macro vintage source byte count mismatch")
    parsed = parse_bea_vintage_xlsx(source_raw)
    observations = _observations_with_lineage(parsed)
    manifest = {
        "schema_version": MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA,
        "source_status": "local_probe_public_download",
        "trust_scope": "local_probe",
        "diagnostic_only": True,
        "source_attestation_verified": False,
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "semantic_facts_train_ready": False,
        "generated_at": generated_at,
        "authorization": deepcopy(payload["authorization"]),
        "source_policies": deepcopy(payload["source_policies"]),
        "fetch_inventory_sha256": fetch_inventory_sha256,
        "fetch_receipt": deepcopy(receipt),
        "raw_source_sha256": parsed.source_sha256,
        "parser": MACRO_VINTAGE_PARSER_REVISION,
        "worksheet_part": parsed.worksheet_part,
        "n_observations": len(observations),
        "n_relations": len(parsed.relations),
        "n_trajectories": len(parsed.trajectories),
        "observations": observations,
        "relations": [deepcopy(item) for item in parsed.relations],
        "trajectories": [deepcopy(item) for item in parsed.trajectories],
    }
    audit_macro_vintage_workflow_manifest(manifest, source_raw=source_raw)
    return manifest


def audit_macro_vintage_workflow_manifest(
    payload: dict[str, Any], *, source_raw: bytes
) -> None:
    """Recheck disabled trust flags and internal source/cell bindings."""
    required = {
        "schema_version",
        "source_status",
        "trust_scope",
        "diagnostic_only",
        "source_attestation_verified",
        "data_stage",
        "hybrid_train_ready",
        "production_eligible",
        "generation_integration",
        "semantic_facts_train_ready",
        "generated_at",
        "authorization",
        "source_policies",
        "fetch_inventory_sha256",
        "fetch_receipt",
        "raw_source_sha256",
        "parser",
        "worksheet_part",
        "n_observations",
        "n_relations",
        "n_trajectories",
        "observations",
        "relations",
        "trajectories",
    }
    if set(payload) != required or not (
        payload.get("schema_version") == MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA
        and payload.get("source_status") == "local_probe_public_download"
        and payload.get("trust_scope") == "local_probe"
        and payload.get("diagnostic_only") is True
        and payload.get("source_attestation_verified") is False
        and payload.get("data_stage") == "source_inventory"
        and payload.get("hybrid_train_ready") is False
        and payload.get("production_eligible") is False
        and payload.get("generation_integration") == "disabled"
        and payload.get("semantic_facts_train_ready") is False
        and payload.get("parser") == MACRO_VINTAGE_PARSER_REVISION
        and payload.get("worksheet_part", "").startswith("xl/worksheets/")
        and _SHA256.fullmatch(str(payload.get("raw_source_sha256") or ""))
    ):
        raise ProvenanceError(
            "macro vintage manifest schema or disabled flags are invalid"
        )
    manifest_time = _parse_timestamp(
        str(payload.get("generated_at") or ""), "generated_at"
    )
    if (
        _SHA256.fullmatch(str(payload.get("fetch_inventory_sha256") or "")) is None
        or _authorization(payload.get("authorization")) != payload.get("authorization")
        or payload.get("source_policies")
        != {"bea": {"terms_url": BEA_TERMS_URL, "license": BEA_LICENSE}}
    ):
        raise ProvenanceError("macro vintage manifest provenance is invalid")
    receipt = payload.get("fetch_receipt")
    if not isinstance(receipt, dict) or set(receipt) != {
        "started_at",
        "completed_at",
        "retrieval",
    }:
        raise ProvenanceError("macro vintage manifest fetch receipt is invalid")
    retrieval = _retrieval(receipt.get("retrieval"))
    started_time = _parse_timestamp(
        str(receipt.get("started_at") or ""), "fetch started_at"
    )
    observed_time = _parse_timestamp(retrieval["observed_at"], "retrieval.observed_at")
    completed_time = _parse_timestamp(
        str(receipt.get("completed_at") or ""), "fetch completed_at"
    )
    if not (
        started_time < observed_time < completed_time <= manifest_time
        and retrieval["sha256"] == payload["raw_source_sha256"]
        and retrieval["raw_bytes"] == len(source_raw)
        and hashlib.sha256(source_raw).hexdigest() == payload["raw_source_sha256"]
    ):
        raise ProvenanceError("macro vintage manifest fetch binding is invalid")
    observations = payload.get("observations")
    relations = payload.get("relations")
    trajectories = payload.get("trajectories")
    if (
        not isinstance(observations, list)
        or not observations
        or len(observations) != payload.get("n_observations")
        or not isinstance(relations, list)
        or len(relations) != payload.get("n_relations")
        or not isinstance(trajectories, list)
        or not trajectories
        or len(trajectories) != payload.get("n_trajectories")
    ):
        raise ProvenanceError("macro vintage manifest counts are invalid")
    reparsed = parse_bea_vintage_xlsx(source_raw)
    if (
        reparsed.source_sha256 != payload["raw_source_sha256"]
        or _observations_with_lineage(reparsed) != observations
        or list(reparsed.relations) != relations
        or list(reparsed.trajectories) != trajectories
    ):
        raise ProvenanceError("macro vintage manifest does not replay source bytes")
    observation_map: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            raise ProvenanceError("macro vintage observation schema is invalid")
        observation_id = observation.get("observation_id")
        provenance = observation.get("provenance")
        text = observation.get("text")
        if (
            not isinstance(observation_id, str)
            or not observation_id
            or observation_id in observation_map
            or observation.get("source_sha256") != payload["raw_source_sha256"]
            or observation.get("source_origin")
            != "local_probe_public_endpoint_observation"
            or observation.get("source_url") != BEA_GDP_GDI_VINTAGE_XLSX_URL
            or not isinstance(text, str)
            or observation.get("text_sha256")
            != hashlib.sha256(text.encode()).hexdigest()
            or not isinstance(provenance, dict)
            or provenance.get("parser") != MACRO_VINTAGE_PARSER_REVISION
            or provenance.get("sheet_part") != payload["worksheet_part"]
            or not isinstance(provenance.get("row_number"), int)
        ):
            raise ProvenanceError("macro vintage observation provenance is invalid")
        row_number = provenance["row_number"]
        for field in (
            "period_cell",
            "estimate_label_cell",
            "value_cell",
            "release_date_cell",
        ):
            match = _CELL_REFERENCE.fullmatch(str(provenance.get(field) or ""))
            if match is None or (
                field != "period_cell" and int(match.group(2)) != row_number
            ):
                raise ProvenanceError(
                    "macro vintage observation cell binding is invalid"
                )
        observation_map[observation_id] = observation
    relation_ids: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            raise ProvenanceError("macro vintage relation schema is invalid")
        relation_id = relation.get("relation_id")
        source_id = relation.get("source_observation_id")
        target_id = relation.get("target_observation_id")
        if (
            not isinstance(relation_id, str)
            or relation_id in relation_ids
            or source_id not in observation_map
            or target_id not in observation_map
            or relation.get("kind")
            != (
                "revises_observation"
                if observation_map[source_id]["value"]
                != observation_map[target_id]["value"]
                else "supersedes_without_observed_value_change"
            )
            or relation.get("answer_value_changed")
            is not (
                observation_map[source_id]["value"]
                != observation_map[target_id]["value"]
            )
            or relation.get("source_release_date_text")
            != observation_map[source_id]["release_date_text"]
            or relation.get("series_id") != observation_map[source_id]["series_id"]
            or relation.get("series_id") != observation_map[target_id]["series_id"]
            or relation.get("period") != observation_map[source_id]["period"]
            or relation.get("period") != observation_map[target_id]["period"]
            or observation_map[source_id]["vintage_date"]
            <= observation_map[target_id]["vintage_date"]
        ):
            raise ProvenanceError("macro vintage relation is invalid")
        relation_ids.add(relation_id)
    for trajectory in trajectories:
        if not isinstance(trajectory, dict):
            raise ProvenanceError("macro vintage trajectory schema is invalid")
        observation_ids = trajectory.get("observation_ids")
        selected_relation_ids = trajectory.get("relation_ids")
        if (
            not isinstance(observation_ids, list)
            or len(observation_ids) < 3
            or len(observation_ids) != len(set(observation_ids))
            or any(item not in observation_map for item in observation_ids)
            or not isinstance(selected_relation_ids, list)
            or len(selected_relation_ids) != len(observation_ids) - 1
            or any(item not in relation_ids for item in selected_relation_ids)
        ):
            raise ProvenanceError("macro vintage trajectory is invalid")
