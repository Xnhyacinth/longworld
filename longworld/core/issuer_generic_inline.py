"""Registered official-host inline sources, independently validated before task compilation."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from longworld.core import finance_taskbank as bank
from longworld.core.attestation import attestation_key_from_env, verify_attestation
from longworld.core.issuerfilingworkflow import _authorization, _visible_cell_text
from longworld.core.issuerinlineworkflow import (
    INLINE_ROLES,
    _context_records,
    _elements,
    _inline_number,
    _namespace_check,
    _unit_records,
    normalize_inline_source,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file

SCHEMA = "longworld.generic-issuer-inline-manifest.v1"
REQUEST_SCHEMA = "longworld.generic-issuer-inline-request.v1"
SOURCE_FAMILY = "registered_issuer_owned_inline_xbrl"
REGISTRY = {
    "siriusxm": {
        "name": "SIRIUS XM HOLDINGS INC.",
        "cik": "0000908937",
        "host": "investor.siriusxm.com",
        "listing_path": "/sec-filings/all-sec-filings",
    },
    "intel": {
        "name": "INTEL CORPORATION",
        "cik": "0000050863",
        "host": "www.intc.com",
        "listing_path": "/filings-reports/all-sec-filings",
    },
}
GROUPS = {
    "Consolidated Statements of Operations": ("revenue", "operating_income"),
    "Consolidated Balance Sheets": ("assets", "liabilities_and_equity"),
    "Consolidated Statements of Cash Flows": (
        "cash_from_operations",
        "cash_from_investing",
        "cash_from_financing",
        "cash_period_change",
    ),
}
_TABLE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


def official_url(value, issuer, *, listing=False):
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != issuer["host"]
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
        or parsed.params
    ):
        raise ProvenanceError("generic inline URL outside registered official host")
    if listing:
        if parsed.path != issuer["listing_path"] or parsed.query != "form_type=10-K":
            raise ProvenanceError("generic inline listing URL invalid")
    elif parsed.query or not re.fullmatch(
        re.escape(issuer["listing_path"])
        + r"/content/\d{10}-\d{2}-\d{6}/[a-z0-9_-]+\.htm",
        parsed.path,
    ):
        raise ProvenanceError("generic inline artifact URL invalid")
    return value


def validate_request(request):
    if (
        request.get("schema_version") != REQUEST_SCHEMA
        or request.get("issuer_key") not in REGISTRY
    ):
        raise ProvenanceError("generic inline request not registered")
    issuer = REGISTRY[request["issuer_key"]]
    if request.get("issuer") != issuer:
        raise ProvenanceError("generic inline issuer mismatch")
    _authorization(request.get("authorization"))
    official_url(request["listing_url"], issuer, listing=True)
    filings = request.get("filings", [])
    if not 2 <= len(filings) <= 4:
        raise ProvenanceError("generic inline requires two to four annual sources")
    reports = []
    for record in filings:
        report, filed = (
            date.fromisoformat(record["report_date"]),
            date.fromisoformat(record["filing_date"]),
        )
        if record.get("form") != "10-K" or not 0 < (filed - report).days < 180:
            raise ProvenanceError("generic inline filing dates or form invalid")
        official_url(record["source_url"], issuer)
        reports.append(report)
    if len(set(reports)) != len(reports) or len(
        {r["source_url"] for r in filings}
    ) != len(filings):
        raise ProvenanceError("generic inline duplicate annual source")
    return issuer


def validate_listing(text, request):
    issuer = validate_request(request)
    found = {}
    for row in re.findall(r"<tr\b[^>]*>.*?</tr>", text, re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.IGNORECASE | re.DOTALL)
        if len(cells) < 2 or _visible_cell_text(cells[1]) != "10-K":
            continue
        filed = (
            datetime.strptime(_visible_cell_text(cells[0]), "%m/%d/%y")
            .replace(tzinfo=timezone.utc)
            .date()
            .isoformat()
        )
        for link in set(re.findall(r"href=[\"\']([^\"\']+)[\"\']", row)):
            url = urljoin("https://" + issuer["host"], html.unescape(link))
            if url in found and found[url] != filed:
                raise ProvenanceError("generic inline conflicting listing dates")
            found[url] = filed
    for record in request["filings"]:
        if found.get(record["source_url"]) != record["filing_date"]:
            raise ProvenanceError("generic inline source/date not in official listing")


def parse_generic_inline(text, *, issuer, report_date):
    """Select standard accounting concepts by statement, annual entity context and unit."""
    _namespace_check(text)
    contexts, units, elements = (
        _context_records(text),
        _unit_records(text),
        _elements(text),
    )
    identity = {}
    for name in (
        "EntityCentralIndexKey",
        "EntityRegistrantName",
        "DocumentType",
        "DocumentFiscalPeriodFocus",
        "DocumentPeriodEndDate",
    ):
        selected = [e for e in elements if e.attrs.get("name") == "dei:" + name]
        values = {
            _visible_cell_text(text[e.content_start : e.content_end]) for e in selected
        }
        refs = {e.attrs.get("contextref") for e in selected}
        if len(values) != 1 or len(refs) != 1:
            raise ProvenanceError("generic inline ambiguous filing identity")
        identity[name] = (next(iter(values)), next(iter(refs)))
    annual_ref = identity["EntityCentralIndexKey"][1]
    annual = contexts.get(annual_ref, {})
    if (
        identity["EntityCentralIndexKey"][0] != issuer["cik"]
        or re.sub(r"\W", "", identity["EntityRegistrantName"][0]).casefold()
        != re.sub(r"\W", "", issuer["name"]).casefold()
        or identity["DocumentType"][0] != "10-K"
        or identity["DocumentFiscalPeriodFocus"][0] != "FY"
        or any(v[1] != annual_ref for v in identity.values())
        or annual.get("cik") != issuer["cik"]
        or annual.get("instant")
        or annual.get("dimensioned")
        or annual.get("end") != report_date
    ):
        raise ProvenanceError("generic inline annual entity binding invalid")
    if (
        not 350
        <= (date.fromisoformat(report_date) - date.fromisoformat(annual["start"])).days
        <= 380
    ):
        raise ProvenanceError("generic inline period not annual")
    display = re.sub(r"\s+,", ",", identity["DocumentPeriodEndDate"][0])
    parsed_dates = []
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            parsed_dates.append(
                datetime.strptime(display, fmt)
                .replace(tzinfo=timezone.utc)
                .date()
                .isoformat()
            )
        except ValueError:
            pass
    if report_date not in parsed_dates:
        raise ProvenanceError("generic inline report date differs from visible date")
    facts, sections = [], []
    for title, roles in GROUPS.items():
        tables = [
            t
            for t in _TABLE.finditer(text)
            if _visible_cell_text(text[max(0, t.start() - 2048) : t.start()])
            .casefold()
            .endswith(
                tuple(
                    v.casefold()
                    for v in (
                        [
                            title,
                            "Consolidated Statements of Income",
                            "Consolidated Statements of Comprehensive Income",
                        ]
                        if title == "Consolidated Statements of Operations"
                        else [title]
                    )
                )
            )
            and all(INLINE_ROLES[r] in t.group() for r in roles)
        ]
        if len(tables) != 1:
            raise ProvenanceError(
                "generic inline statement heading missing or ambiguous: " + title
            )
        table = tables[0]
        if re.search(
            r"<ix:hidden\b|display\s*:\s*none|visibility\s*:\s*hidden|\shidden(?:\s|=|>)",
            re.sub(
                r"<td\b[^>]*?(?:/>|>\s*</td>)", "", table.group(), flags=re.IGNORECASE
            ),
            re.IGNORECASE,
        ):
            raise ProvenanceError("generic inline statement contains hidden content")
        visible = _visible_cell_text(table.group())
        if (
            "in millions" not in visible.casefold()
            or report_date[:4] not in visible[:500]
        ):
            raise ProvenanceError("generic inline visible monetary header missing")
        section = {
            "section_id": bank._sha(
                [
                    hashlib.sha256(text.encode()).hexdigest(),
                    title,
                    table.start(),
                    table.end(),
                ]
            ),
            "title": title,
            "start": table.start(),
            "end": table.end(),
        }
        sections.append(section)
        for role in roles:
            instant = bank.METRICS[role][1] == "instant"
            selected = []
            for e in elements:
                if (
                    e.kind != "nonfraction"
                    or e.attrs.get("name") != INLINE_ROLES[role]
                    or not table.start() <= e.start < e.end <= table.end()
                ):
                    continue
                c = contexts.get(e.attrs.get("contextref"), {})
                if (
                    c.get("cik") == issuer["cik"]
                    and not c.get("dimensioned")
                    and c.get("instant") == instant
                    and c.get("end") == report_date
                    and (instant or c.get("start") == annual["start"])
                ):
                    selected.append(e)
            if len(selected) != 1:
                raise ProvenanceError(
                    "generic inline annual fact missing or ambiguous: " + role
                )
            e = selected[0]
            if units.get(e.attrs.get("unitref")) != ("iso4217:USD",):
                raise ProvenanceError("generic inline non-USD unit")
            quote, value, start = _inline_number(text, e, role)
            cells = [
                c
                for c in re.finditer(
                    r"<td\b(?![^>]*?/>)[^>]*>.*?</td>",
                    table.group(),
                    re.IGNORECASE | re.DOTALL,
                )
                if table.start() + c.start()
                <= e.start
                < e.end
                <= table.start() + c.end()
            ]
            if len(cells) != 1:
                raise ProvenanceError(
                    "generic inline operand outside visible table cell"
                )
            cell_text = (
                _visible_cell_text(cells[0].group()).replace("$", "").replace(" ", "")
            )
            expected = "(" + quote + ")" if value < 0 else quote
            if cell_text != expected:
                raise ProvenanceError("generic inline visible cell sign/value mismatch")
            facts.append(
                {
                    "role": role,
                    "value": value,
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                    "section_id": section["section_id"],
                    "period_start": None if instant else annual["start"],
                    "context_id": e.attrs["contextref"],
                    "concept": e.attrs["name"],
                    "unitref": e.attrs["unitref"],
                    "scale": e.attrs["scale"],
                    "sign": e.attrs.get("sign", ""),
                    "dimensioned": False,
                    "visible_cell": cell_text,
                    "visible_header": visible[:240],
                }
            )
    values = {f["role"]: f["value"] for f in facts}
    if values["assets"] != values["liabilities_and_equity"]:
        raise ProvenanceError("generic inline balance identity failed")
    return {"facts": facts, "sections": sections, "annual_start": annual["start"]}


def load_generic_inline_world(manifest_path, *, attestation_key=None):
    """Verify native manifest and every source, then construct the unchanged compiler world."""
    path = Path(manifest_path).absolute()
    raw = _read_regular_file(path, 128 * 1024 * 1024)
    payload = json.loads(raw)
    if not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError("generic inline invalid source attestation")
    if (
        payload.get("schema_version") != SCHEMA
        or payload.get("source_family") != SOURCE_FAMILY
        or payload.get("production_eligible") is not False
    ):
        raise ProvenanceError("generic inline manifest boundary invalid")
    request = payload["request"]
    issuer = validate_request(request)
    records = payload["records"]
    if len(records) != len(request["filings"]):
        raise ProvenanceError("generic inline record count mismatch")
    for source in [payload["listing"], *records]:
        normalized, receipt = normalize_inline_source(source["raw_text"].encode())
        if (
            normalized.decode() != source["text"]
            or receipt != source["normalization"]
            or source.get("retrieval_bytes") != len(source["raw_text"].encode())
            or source.get("bytes") != len(normalized)
        ):
            raise ProvenanceError("generic inline raw normalized bytes mismatch")
    if payload["listing"]["source_url"] != request["listing_url"]:
        raise ProvenanceError("generic inline listing binding invalid")
    validate_listing(payload["listing"]["text"], request)
    documents, facts = [], []
    for record, requested in zip(
        records, sorted(request["filings"], key=lambda r: r["report_date"]), strict=True
    ):
        if any(record.get(k) != v for k, v in requested.items()):
            raise ProvenanceError("generic inline requested record mismatch")
        parsed = parse_generic_inline(
            record["text"], issuer=issuer, report_date=record["report_date"]
        )
        if record.get("derived") != parsed:
            raise ProvenanceError("generic inline derived facts mismatch")
        digest = hashlib.sha256(record["text"].encode()).hexdigest()
        rid = "generic-inline:" + issuer["cik"] + ":" + record["report_date"]
        documents.append(
            {
                "record_id": rid,
                "text": record["text"],
                "source_sha256": digest,
                "report_date": record["report_date"],
                "filing_date": record["filing_date"],
                "source_url": record["source_url"],
                "reporting_basis": bank.BASIS,
                "source_version": digest,
                "sections": parsed["sections"],
            }
        )
        for f in parsed["facts"]:
            kind = bank.METRICS[f["role"]][1]
            cell = (
                digest,
                f["start"],
                f["end"],
                bank.UNIT,
                kind,
                record["report_date"],
                bank.BASIS,
            )
            facts.append(
                {
                    "fact_id": bank._sha([cell, f["value"]]),
                    "role": f["role"],
                    "value": f["value"],
                    "unit": bank.UNIT,
                    "period": {
                        "kind": kind,
                        "start": f["period_start"],
                        "end": record["report_date"],
                        "duration": "annual_fiscal_period"
                        if kind == "duration"
                        else None,
                    },
                    "report_date": record["report_date"],
                    "filing_date": record["filing_date"],
                    "reporting_basis": bank.BASIS,
                    "source_version": digest,
                    "record_id": rid,
                    "source_url": record["source_url"],
                    "span": {
                        "start": f["start"],
                        "end": f["end"],
                        "quote": f["quote"],
                        "section_id": f["section_id"],
                    },
                    "aliases": [f["role"]],
                }
            )
    collection = bank._sha(
        sorted((d["record_id"], d["source_sha256"]) for d in documents)
    )
    wid = bank._sha({"issuer_cik": issuer["cik"], "source_collection_id": collection})
    result = {
        "schema_version": bank.WORLD_SCHEMA,
        "world_id": wid,
        "world_instance_id": wid,
        "source_collection_id": collection,
        "split_group_id": issuer["cik"],
        "issuer": {"cik": issuer["cik"], "name": issuer["name"]},
        "source_manifest": {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_version": SCHEMA,
            "source_family": SOURCE_FAMILY,
        },
        "documents": documents,
        "facts": facts,
        "normalization_rejections": [],
        "source_verified": True,
    }
    return bank._VerifiedWorld(result, bank._LOAD_TOKEN)
