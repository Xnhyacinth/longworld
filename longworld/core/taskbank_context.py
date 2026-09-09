"""Readable, unpadded filing contexts with raw-to-visible evidence bindings."""

from __future__ import annotations

import hashlib
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class _VisibleHTML(HTMLParser):
    """Keep visible text and table boundaries; never render XBRL hidden facts."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self.parts: list[str] = []
        self.size = 0
        self.hidden: list[str] = []
        self.segments: list[dict[str, Any]] = []

    def add(self, text: str) -> None:
        if text == "\n" and self.parts and self.parts[-1].endswith("\n"):
            return
        self.parts.append(text)
        self.size += len(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        style = re.sub(r"\s", "", attr.get("style") or "").lower()
        hide = (
            tag in {"script", "style", "head", "ix:header", "ix:hidden"}
            or "hidden" in attr
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if self.hidden or hide:
            if tag not in {"br", "hr", "img", "input", "meta", "link", "wbr"}:
                self.hidden.append(tag)
            return
        if tag in {"p", "div", "tr", "table", "h1", "h2", "h3", "h4", "li", "br"}:
            self.add("\n")
        elif tag in {"td", "th"}:
            self.add("\t")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag in self.hidden:
                index = len(self.hidden) - 1 - self.hidden[::-1].index(tag)
                del self.hidden[index:]
            return
        if tag in {"p", "div", "tr", "table", "h1", "h2", "h3", "h4", "li"}:
            self.add("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        if not data.strip():
            if self.parts and not self.parts[-1][-1:].isspace():
                self.add(" ")
            return
        # Normalize layout whitespace, retaining source offsets for every leaf.
        start = self.line_starts[self.getpos()[0] - 1] + self.getpos()[1]
        visible = re.sub(r"\s+", " ", data)
        self.segments.append(
            {
                "source_start": start,
                "source_end": start + len(data),
                "visible_start": self.size,
                "visible_end": self.size + len(visible),
            }
        )
        self.add(visible)

    def handle_entityref(self, name: str) -> None:
        self.entity(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.entity(f"&#{name};")

    def entity(self, raw: str) -> None:
        if not self.hidden:
            start = self.line_starts[self.getpos()[0] - 1] + self.getpos()[1]
            visible = unescape(raw).replace("\xa0", " ")
            if visible.isspace():
                visible = "" if self.parts and self.parts[-1][-1:].isspace() else " "
            if not visible:
                return
            self.segments.append(
                {
                    "source_start": start,
                    "source_end": start + len(raw),
                    "visible_start": self.size,
                    "visible_end": self.size + len(visible),
                }
            )
            self.add(visible)


def render_document(document: dict[str, Any]) -> dict[str, Any]:
    source = document["text"]
    if text_sha256(source) != document["source_sha256"]:
        raise ValueError("document source hash mismatch")
    parser = _VisibleHTML(source)
    parser.feed(source)
    parser.close()
    text = "".join(parser.parts)
    if not text.strip():
        raise ValueError("document has no visible text")
    return {
        "record_id": document["record_id"],
        "source_sha256": document["source_sha256"],
        "text": text,
        "text_sha256": text_sha256(text),
        "segments": parser.segments,
    }


def bind_visible_evidence(
    source: dict[str, Any], rendered: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """Require the exact source numeric leaf to survive readable rendering."""
    if (
        rendered["record_id"] != source["record_id"]
        or rendered["source_sha256"] != source["source_sha256"]
        or text_sha256(source["text"]) != source["source_sha256"]
        or text_sha256(rendered["text"]) != rendered["text_sha256"]
    ):
        raise ValueError("rendered document source binding mismatch")
    start, end, quote = span["start"], span["end"], span["quote"]
    if source["text"][start:end] != quote or not quote:
        raise ValueError("evidence source span mismatch")
    for segment in rendered["segments"]:
        if segment["source_start"] <= start < end <= segment["source_end"]:
            prefix = source["text"][segment["source_start"] : start]
            visible_start = segment["visible_start"] + len(re.sub(r"\s+", " ", prefix))
            visible_end = visible_start + len(quote)
            if rendered["text"][visible_start:visible_end] != quote:
                raise ValueError("evidence changed during visible rendering")
            if isinstance(span.get("value"), int):
                validate_visible_sign(
                    rendered["text"], visible_start, visible_end, span["value"]
                )
            return {
                **span,
                "visible_start": visible_start,
                "visible_end": visible_end,
                "rendered_document_sha256": rendered["text_sha256"],
            }
    raise ValueError("evidence is hidden or not wholly visible")


def validate_visible_sign(text: str, start: int, end: int, value: int) -> None:
    """Signs must be visible within the numeric cell, not borrowed from a neighbor."""
    before = re.split(r"[\t\r\n]", text[max(0, start - 24) : start])[-1]
    after = re.split(r"[\t\r\n]", text[end : end + 24])[0]
    quote = text[start:end].strip()
    negative = (
        quote.startswith(("-", "−"))
        or (quote.startswith("(") and quote.endswith(")"))
        or bool(re.search(r"[−-]\s*\$?\s*$", before))
        or bool(re.search(r"\(\s*\$?\s*$", before) and re.match(r"^\s*\)", after))
    )
    if value != 0 and negative != (value < 0):
        raise ValueError("numeric sign is not supported by visible text")


def assemble_context(
    documents: list[dict[str, Any]],
    rendered: dict[str, dict[str, Any]],
    required_record_ids: list[str],
) -> tuple[str, dict[str, int]]:
    """Use complete required filings, in source chronology, exactly once."""
    required = set(required_record_ids)
    if len({doc["record_id"] for doc in documents}) != len(documents):
        raise ValueError("source documents are duplicated")
    if not required or len(required) != len(required_record_ids):
        raise ValueError("required document identities are empty or duplicated")
    selected = [doc for doc in documents if doc["record_id"] in required]
    if {doc["record_id"] for doc in selected} != required:
        raise ValueError("required document is absent")
    parts: list[str] = []
    offsets: dict[str, int] = {}
    size = 0
    for doc in sorted(
        selected, key=lambda item: (item["report_date"], item["record_id"])
    ):
        header = (
            f"\n=== Annual filing: {doc['record_id']} ===\n"
            f"Report date: {doc['report_date']}; filed: {doc['filing_date']}\n"
            f"Source: {doc['source_url']}\n"
        )
        parts.append(header)
        size += len(header)
        offsets[doc["record_id"]] = size
        body = rendered[doc["record_id"]]["text"]
        parts.append(body)
        size += len(body)
    return "".join(parts), offsets


def assemble_statement_context(
    documents: list[dict[str, Any]],
    rendered: dict[str, dict[str, Any]],
    required_record_ids: list[str],
) -> tuple[str, dict[str, list[dict[str, int]]]]:
    """A complete audited-statement packet, retaining every table and summary.

    This is a distinct source subset, never a claim that a complete annual
    filing needs long reasoning. All adapter-supported sections are included,
    including sections not used by the answer.
    """
    if len({d["record_id"] for d in documents}) != len(documents):
        raise ValueError("source documents are duplicated")
    required = set(required_record_ids)
    if not required or not required <= {d["record_id"] for d in documents}:
        raise ValueError("required document is absent")
    parts: list[str] = []
    mappings: dict[str, list[dict[str, int]]] = {}
    size = 0
    for doc in sorted(documents, key=lambda d: (d["report_date"], d["record_id"])):
        identity = doc["record_id"]
        if identity not in required:
            continue
        sections = sorted(doc.get("sections", []), key=lambda s: (s["start"], s["end"]))
        if not sections:
            raise ValueError("document has no audited complete sections")
        # Overlapping section ranges can describe the same source table. Merge
        # their full envelopes instead of repeating text or dropping a summary.
        merged: list[dict[str, Any]] = []
        for section in sections:
            if not 0 <= section["start"] < section["end"] <= len(doc["text"]):
                raise ValueError("section source range is invalid")
            if merged and section["start"] <= merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], section["end"])
                merged[-1]["titles"].append(section["title"])
            else:
                merged.append(
                    {
                        "start": section["start"],
                        "end": section["end"],
                        "titles": [section["title"]],
                    }
                )
        mappings[identity] = []
        for section in merged:
            nodes = [
                node
                for node in rendered[identity]["segments"]
                if section["start"]
                <= node["source_start"]
                < node["source_end"]
                <= section["end"]
            ]
            if not nodes:
                raise ValueError("source section has no visible text")
            lo, hi = nodes[0]["visible_start"], nodes[-1]["visible_end"]
            header = (
                f"\n=== Annual filing: {identity} ===\n"
                f"Report date: {doc['report_date']}; filed: {doc['filing_date']}\n"
                f"Source: {doc['source_url']}\n"
                f"Complete source section: {'; '.join(dict.fromkeys(section['titles']))}\n"
            )
            parts.append(header)
            size += len(header)
            mappings[identity].append(
                {"visible_start": lo, "visible_end": hi, "context_start": size}
            )
            body = rendered[identity]["text"][lo:hi]
            parts.append(body)
            size += len(body)
    return "".join(parts), mappings


def assemble_analyst_context(
    documents: list[dict[str, Any]],
    rendered: dict[str, dict[str, Any]],
    required_record_ids: list[str],
) -> tuple[str, dict[str, list[dict[str, int]]]]:
    """Retain the newest complete filing and earlier complete financial statements.

    This conventional historical-comparison packet keeps the latest filing's
    summaries and notes. It has no strict dependency or unique-proof claim.
    """
    required = [doc for doc in documents if doc["record_id"] in required_record_ids]
    if len(required_record_ids) != len(set(required_record_ids)) or {
        doc["record_id"] for doc in required
    } != set(required_record_ids):
        raise ValueError("analyst packet document identities are invalid")
    if len(required) < 2:
        raise ValueError("analyst packet requires multiple filings")
    latest = max(required, key=lambda doc: (doc["report_date"], doc["record_id"]))
    older = [doc["record_id"] for doc in required if doc is not latest]
    historical, mappings = assemble_statement_context(documents, rendered, older)
    current, offsets = assemble_context(documents, rendered, [latest["record_id"]])
    identity = latest["record_id"]
    mappings[identity] = [
        {
            "visible_start": 0,
            "visible_end": len(rendered[identity]["text"]),
            "context_start": len(historical) + offsets[identity],
        }
    ]
    return historical + current, mappings
