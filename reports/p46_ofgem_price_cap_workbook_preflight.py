"""Aggregate-only P46 Ofgem OOXML formula and capacity preflight."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import shutil
import ssl
import tempfile
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

from transformers import AutoTokenizer

from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p46_ofgem_price_cap_workbook_preflight_v1.json")
OUTPUT = Path("reports/p46_ofgem_price_cap_workbook_preflight_v1.json")
ALLOWED_HOSTS = frozenset({"www.ofgem.gov.uk"})
USER_AGENT = "LongWorld-P46-source-preflight/1.0 xnhyacinth@users.noreply.github.com"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CELL_REFERENCE = re.compile(r"(?<![A-Za-z0-9_])\$?[A-Z]{1,3}\$?\d+(?![A-Za-z0-9_])")
SHEET_REFERENCE = re.compile(
    r"(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_. -]*))!\$?[A-Z]{1,3}\$?\d+"
)
VOLATILE_FUNCTION = re.compile(
    r"(?i)(?:^|[^A-Z_])(NOW|TODAY|RAND|RANDBETWEEN|OFFSET|INDIRECT|CELL|INFO)\s*\("
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fetch(url: str) -> tuple[bytes, dict[str, Any]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P46 source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        request, timeout=180, context=ssl.create_default_context()
    ) as response:
        raw = response.read()
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P46 redirect target: {final_url}")
        return raw, {
            "requested_url": url,
            "final_url": final_url,
            "status": response.status,
            "content_type": response.headers.get_content_type(),
            "content_length_header": response.headers.get("Content-Length"),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "observed_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "bytes": len(raw),
            "sha256": _sha256(raw),
        }


def _text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _validated_archive(
    raw: bytes, limits: dict[str, Any]
) -> tuple[zipfile.ZipFile, dict[str, Any]]:
    archive = zipfile.ZipFile(io.BytesIO(raw))
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("duplicate OOXML archive member")
    if len(names) > limits["max_member_count"]:
        raise ValueError("OOXML member count exceeds bound")
    total_uncompressed = 0
    max_member = 0
    max_ratio = 0.0
    encrypted_members = 0
    unsafe_members = []
    for info in infos:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            unsafe_members.append(info.filename)
        if info.flag_bits & 0x1:
            encrypted_members += 1
        total_uncompressed += info.file_size
        max_member = max(max_member, info.file_size)
        ratio = info.file_size / max(info.compress_size, 1)
        max_ratio = max(max_ratio, ratio)
    if unsafe_members:
        raise ValueError("unsafe OOXML archive member path")
    if encrypted_members:
        raise ValueError("encrypted OOXML archive member")
    if total_uncompressed > limits["max_total_uncompressed_bytes"]:
        raise ValueError("OOXML expanded size exceeds bound")
    if max_member > limits["max_single_member_bytes"]:
        raise ValueError("OOXML member size exceeds bound")
    if max_ratio > limits["max_compression_ratio"]:
        raise ValueError("OOXML member compression ratio exceeds bound")
    lower_names = [name.lower() for name in names]
    internal_only_marker_part_count = sum(
        b"internal only" in archive.read(name).lower()
        for name in names
        for info in [archive.getinfo(name)]
        if info.file_size <= limits["max_single_member_bytes"]
    )
    active_content = sorted(
        name
        for name in names
        if name.lower().endswith(("vbaproject.bin", ".exe", ".dll", ".js"))
        or "/activex/" in name.lower()
        or "/embeddings/" in name.lower()
    )
    return archive, {
        "member_count": len(infos),
        "total_uncompressed_bytes": total_uncompressed,
        "max_single_member_bytes": max_member,
        "max_compression_ratio": round(max_ratio, 6),
        "encrypted_member_count": encrypted_members,
        "unsafe_member_count": len(unsafe_members),
        "active_content_member_count": len(active_content),
        "macro_enabled_content_type": any(
            "macroenabled" in line
            for line in archive.read("[Content_Types].xml")
            .decode("utf-8", errors="replace")
            .lower()
            .splitlines()
        ),
        "printer_settings_binary_count": sum(
            "/printersettings/" in name for name in lower_names
        ),
        "internal_only_marker_part_count": internal_only_marker_part_count,
    }


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_text(item) for item in root.findall(f"{{{MAIN_NS}}}si")]


def _workbook_sheet_paths(
    archive: zipfile.ZipFile,
) -> tuple[list[tuple[str, str, str]], ElementTree.Element]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }
    sheets = []
    for sheet in workbook.findall(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet"):
        relation_id = sheet.attrib[f"{{{REL_NS}}}id"]
        target = targets[relation_id]
        path = PurePosixPath("xl") / PurePosixPath(target)
        normalized = str(path)
        if not normalized.startswith("xl/worksheets/"):
            raise ValueError(f"unexpected worksheet target: {target}")
        sheets.append(
            (sheet.attrib["name"], normalized, sheet.attrib.get("state", "visible"))
        )
    return sheets, workbook


def _external_relationship_metrics(archive: zipfile.ZipFile) -> dict[str, Any]:
    rel_names = sorted(
        name
        for name in archive.namelist()
        if name.startswith("xl/externalLinks/_rels/") and name.endswith(".rels")
    )
    schemes: Counter[str] = Counter()
    target_count = 0
    for name in rel_names:
        root = ElementTree.fromstring(archive.read(name))
        for item in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
            target = item.attrib.get("Target", "")
            scheme = urlparse(target).scheme.lower() or "relative_or_unc"
            schemes[scheme] += 1
            target_count += 1
    return {
        "external_link_part_count": sum(
            name.startswith("xl/externalLinks/externalLink") and name.endswith(".xml")
            for name in archive.namelist()
        ),
        "external_relationship_target_count": target_count,
        "external_relationship_target_schemes": dict(sorted(schemes.items())),
        "targets_persisted": False,
    }


def _cell_value(
    cell: ElementTree.Element, shared_strings: list[str]
) -> tuple[str, str]:
    cell_type = cell.attrib.get("t", "n")
    if cell_type == "inlineStr":
        return "string", _text(cell.find(f"{{{MAIN_NS}}}is"))
    value = _text(cell.find(f"{{{MAIN_NS}}}v"))
    if cell_type == "s" and value:
        index = int(value)
        if index >= len(shared_strings):
            raise ValueError("shared string index out of bounds")
        return "string", shared_strings[index]
    return cell_type, value


def _formula_template(formula: str) -> str:
    return CELL_REFERENCE.sub("CELL", " ".join(formula.split()))


def _token_count(tokenizer: Any, values: set[str], batch_size: int = 256) -> int:
    total = 0
    batch = []
    for value in sorted(values):
        batch.append(value)
        if len(batch) == batch_size:
            encoded = tokenizer(
                batch,
                add_special_tokens=False,
                padding=False,
                truncation=False,
            )["input_ids"]
            total += sum(len(item) for item in encoded)
            batch.clear()
    if batch:
        encoded = tokenizer(
            batch,
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )["input_ids"]
        total += sum(len(item) for item in encoded)
    return total


def _inspect_workbook(
    raw: bytes, limits: dict[str, Any]
) -> tuple[dict[str, Any], set[str], set[str]]:
    archive, archive_metrics = _validated_archive(raw, limits)
    shared_strings = _shared_strings(archive)
    sheets, workbook = _workbook_sheet_paths(archive)
    defined_names = workbook.findall(
        f"{{{MAIN_NS}}}definedNames/{{{MAIN_NS}}}definedName"
    )
    external_defined_names = {
        item.attrib["name"]
        for item in defined_names
        if "[" in _text(item) and "]" in _text(item)
    }
    external_defined_name_count = sum(
        "[" in _text(item) and "]" in _text(item) for item in defined_names
    )
    broken_defined_name_count = sum("#REF!" in _text(item) for item in defined_names)
    hidden_defined_name_count = sum(
        item.attrib.get("hidden") == "1" for item in defined_names
    )
    exact_units: set[str] = set()
    template_units: set[str] = set()
    formula_count = 0
    shared_formula_follower_count = 0
    external_formula_count = 0
    volatile_formula_count = 0
    nonempty_cell_count = 0
    cross_sheet_edges: Counter[tuple[str, str]] = Counter()
    formulas_by_sheet: Counter[str] = Counter()
    cells_by_sheet: Counter[str] = Counter()
    indirect_formulas_by_sheet: Counter[str] = Counter()
    formula_identifiers: set[str] = set()
    for sheet_name, path, _state in sheets:
        root = ElementTree.fromstring(archive.read(path))
        for cell in root.iterfind(f".//{{{MAIN_NS}}}c"):
            formula_element = cell.find(f"{{{MAIN_NS}}}f")
            value_type, value = _cell_value(cell, shared_strings)
            formula = _text(formula_element)
            if formula_element is not None:
                formula_count += 1
                formulas_by_sheet[sheet_name] += 1
                if not formula:
                    shared_formula_follower_count += 1
                    formula = (
                        f"SHARED_FORMULA_{formula_element.attrib.get('si', 'UNKNOWN')}"
                    )
                if "[" in formula and "]" in formula:
                    external_formula_count += 1
                if VOLATILE_FUNCTION.search(formula):
                    volatile_formula_count += 1
                if re.search(r"(?i)(?:^|[^A-Z_])INDIRECT\s*\(", formula):
                    indirect_formulas_by_sheet[sheet_name] += 1
                formula_identifiers.update(
                    re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", formula)
                )
                for match in SHEET_REFERENCE.finditer(formula):
                    target = (match.group(1) or match.group(2)).replace("''", "'")
                    if target != sheet_name:
                        cross_sheet_edges[(sheet_name, target)] += 1
                exact_units.add(
                    f"sheet={sheet_name}|kind=formula|expression={formula}|cached={value}"
                )
                template_units.add(
                    f"sheet={sheet_name}|kind=formula|template={_formula_template(formula)}"
                )
            elif value:
                exact_units.add(
                    f"sheet={sheet_name}|kind={value_type}|value={value.strip()}"
                )
                template_units.add(
                    f"sheet={sheet_name}|kind={value_type}|value={value.strip()}"
                )
            else:
                continue
            nonempty_cell_count += 1
            cells_by_sheet[sheet_name] += 1
    top_edges = [
        {"from": source, "to": target, "formula_count": count}
        for (source, target), count in cross_sheet_edges.most_common(30)
    ]
    electricity_calculator = next(
        name
        for name, _, _ in sheets
        if name.startswith("ElecSingle_Other_") and not name.endswith("_Nil")
    )
    gas_calculator = next(
        name
        for name, _, _ in sheets
        if name.startswith("Gas_Other_") and not name.endswith("_Nil")
    )
    output_indirect_count = indirect_formulas_by_sheet["1a Default tariff cap"]
    dag_branches = [
        {
            "branch": "electricity_direct_fuel",
            "source_sheets": ["3a DF"],
            "calculator_sheet": electricity_calculator,
            "source_reference_formula_counts": {
                "3a DF": cross_sheet_edges[(electricity_calculator, "3a DF")]
            },
            "output_sheet": "1a Default tariff cap",
            "output_selector": "INDIRECT over the calculator sheet name",
            "output_selector_formula_count": output_indirect_count,
        },
        {
            "branch": "electricity_network",
            "source_sheets": ["3e NC-Elec"],
            "calculator_sheet": electricity_calculator,
            "source_reference_formula_counts": {
                "3e NC-Elec": cross_sheet_edges[(electricity_calculator, "3e NC-Elec")]
            },
            "output_sheet": "1a Default tariff cap",
            "output_selector": "INDIRECT over the calculator sheet name",
            "output_selector_formula_count": output_indirect_count,
        },
        {
            "branch": "gas_network",
            "source_sheets": ["3f NC-Gas"],
            "calculator_sheet": gas_calculator,
            "source_reference_formula_counts": {
                "3f NC-Gas": cross_sheet_edges[(gas_calculator, "3f NC-Gas")]
            },
            "output_sheet": "1a Default tariff cap",
            "output_selector": "INDIRECT over the calculator sheet name",
            "output_selector_formula_count": output_indirect_count,
        },
        {
            "branch": "inflation_and_debt",
            "source_sheets": ["3g CPIH", "3k EBIT", "3n DRC"],
            "calculator_sheet": electricity_calculator,
            "source_reference_formula_counts": {
                source: cross_sheet_edges[(electricity_calculator, source)]
                for source in ("3g CPIH", "3k EBIT", "3n DRC")
                if cross_sheet_edges[(electricity_calculator, source)]
            },
            "output_sheet": "1a Default tariff cap",
            "output_selector": "INDIRECT over the calculator sheet name",
            "output_selector_formula_count": output_indirect_count,
        },
    ]
    return (
        {
            "archive": archive_metrics,
            "sheet_count": len(sheets),
            "hidden_sheet_count": sum(state != "visible" for _, _, state in sheets),
            "sheet_names": [name for name, _, _ in sheets],
            "defined_name_count": len(defined_names),
            "hidden_defined_name_count": hidden_defined_name_count,
            "external_defined_name_count": external_defined_name_count,
            "external_defined_name_unique_name_count": len(external_defined_names),
            "broken_defined_name_count": broken_defined_name_count,
            "live_external_defined_name_reference_count": len(
                external_defined_names & formula_identifiers
            ),
            "nonempty_cell_count": nonempty_cell_count,
            "formula_count": formula_count,
            "shared_formula_follower_count": shared_formula_follower_count,
            "external_workbook_formula_count": external_formula_count,
            "volatile_formula_count": volatile_formula_count,
            "exact_deduplicated_unit_count": len(exact_units),
            "template_deduplicated_unit_count": len(template_units),
            "formula_template_reduction_ratio": round(
                1.0 - len(template_units) / max(nonempty_cell_count, 1), 6
            ),
            "external_links": _external_relationship_metrics(archive),
            "top_cross_sheet_formula_edges": top_edges,
            "dag_branch_preflight": dag_branches,
            "top_formula_sheets": [
                {"sheet": sheet, "formula_count": count}
                for sheet, count in formulas_by_sheet.most_common(12)
            ],
            "top_nonempty_cell_sheets": [
                {"sheet": sheet, "cell_count": count}
                for sheet, count in cells_by_sheet.most_common(12)
            ],
            "document_properties_excluded": True,
            "comments_and_people_metadata_excluded": True,
            "images_excluded": True,
        },
        exact_units,
        template_units,
    )


def main() -> None:
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {
        "32k": [32_000, 32_768],
        "64k": [64_000, 65_536],
        "128k": [128_000, 131_072],
    }
    if config.get("capacity_bands") != expected_bands:
        raise ValueError("P46 capacity bands must match repository exact bands")
    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=tokenizer_config["local_files_only"],
    )
    results = []
    union_exact: set[str] = set()
    union_template: set[str] = set()
    previous_template: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="longworld-p46-", dir="/tmp") as temp_dir:
        temp_root = Path(temp_dir)
        for source in config["workbooks"]:
            raw, receipt = _fetch(source["url"])
            if len(raw) != source["expected_bytes"]:
                raise ValueError(f"P46 byte length changed for {source['period']}")
            if _sha256(raw) != source["expected_sha256"]:
                raise ValueError(f"P46 digest changed for {source['period']}")
            ephemeral_path = temp_root / f"{source['period']}.xlsx"
            ephemeral_path.write_bytes(raw)
            metrics, exact_units, template_units = _inspect_workbook(
                ephemeral_path.read_bytes(), config["archive_limits"]
            )
            if metrics["sheet_count"] != source["expected_sheet_count"]:
                raise ValueError(f"P46 sheet count changed for {source['period']}")
            if metrics["defined_name_count"] != source["expected_defined_name_count"]:
                raise ValueError(
                    f"P46 defined-name count changed for {source['period']}"
                )
            metrics["exact_deduplicated_qwen_tokens"] = _token_count(
                tokenizer, exact_units
            )
            metrics["template_deduplicated_qwen_tokens"] = _token_count(
                tokenizer, template_units
            )
            new_units = template_units - previous_template
            metrics["new_template_units_vs_previous_count"] = len(new_units)
            metrics["new_template_units_vs_previous_qwen_tokens"] = _token_count(
                tokenizer, new_units
            )
            results.append({"period": source["period"], "download": receipt, **metrics})
            union_exact.update(exact_units)
            union_template.update(template_units)
            previous_template = template_units
    union_template_tokens = _token_count(tokenizer, union_template)
    capacity = {
        "cross_version_exact_deduplicated_unit_count": len(union_exact),
        "cross_version_exact_deduplicated_qwen_tokens": _token_count(
            tokenizer, union_exact
        ),
        "cross_version_template_deduplicated_unit_count": len(union_template),
        "cross_version_template_deduplicated_qwen_tokens": union_template_tokens,
        "exact_band_capacity": {
            band: union_template_tokens >= bounds[0]
            for band, bounds in config["capacity_bands"].items()
        },
        "capacity_is_preflight_only": True,
    }
    report = {
        "schema_version": "longworld.p46-ofgem-workbook-preflight-report.v1",
        "data_product": config["data_product"],
        "config_sha256": _sha256(config_raw),
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "workbooks": results,
        "capacity": capacity,
        "oracle_preflight": {
            "openpyxl_available_in_project_environment": importlib.util.find_spec(
                "openpyxl"
            )
            is not None,
            "libreoffice_available_in_environment": bool(
                shutil.which("libreoffice") or shutil.which("soffice")
            ),
            "required_boundary_policy": "external-link cached cells are immutable signed inputs; external relationship targets are stripped and never dereferenced",
            "required_openpyxl_check": "load formula and data_only views with keep_links=False; bind every retained cached boundary value by workbook hash, sheet, and cell; reject formula errors and unresolved non-boundary external references",
            "required_libreoffice_check": "run headless in an isolated profile with networking disabled and link updates disabled; recalculate only a frozen internal dependency closure; require two identical exports and agreement with the Python oracle within declared decimal tolerances",
            "status": "blocked_pending_cross_engine_oracle",
        },
        "promotion_status": {
            "train_ready": False,
            "candidate_generation_allowed": False,
            "reason": "capacity alone is insufficient; embedded Internal Only headers require rights review, stale external-link and broken-name parts require an exclusion boundary, and neither recalculation engine is installed or validated",
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
