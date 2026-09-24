"""Freeze a MediaWiki category into a SourceSnapshot v1 (P74 route A, T4 slice).

This adapter is the T4 first slice of the P74 charter (``.hl/design/
p74_real_shared_worlds.md`` §4 route A and §14): it freezes a MediaWiki
category (or any bounded set of pages) into the charter §14
``SourceSnapshot`` contract and extracts candidate facts with verbatim
character spans.  It contains **no task functions**; question generation and
snapshot→world compilation live in later waves (T1/T2 consume this output).

Rendering model
---------------
``documents[].text`` is an adapter-rendered plain-text view of the frozen
wikitext revision:

* the page title renders as a ``# Title`` line,
* infobox fields render as ``key: value`` lines,
* headings render as ``##`` … lines (one ``#`` per wikitext ``=``),
* tables render as pipe-joined rows (``hdr1 | hdr2`` then ``val1 | val2``),
* list items render as ``- item`` lines (``  -`` for nesting).

Every ``supporting_span`` is an exact ``[start, end)`` character offset into
that rendered text, and every fact's ``value`` equals its primary span
verbatim, so grounding is checkable with ``text[start:end]`` alone.  Anything
extracted without a body span (e.g. category membership from ``[[Category:…]]``
links or from the categorymembers listing itself) goes to the explicit
``ungrounded_quarantine`` list and never into ``facts``.

Entity linking is exact-surface only: wiki links resolve through the frozen
page set and the per-page link metadata (pageid + ``wikibase_item``), and
entity labels/aliases are additionally matched case-sensitively inside the
rendered text as mentions.  ``external_qid`` is recorded only when the value
is present in the fetched source (page props or link pageprops); there is no
live Wikidata requirement in this wave.

Determinism: all ids, keys and lists are sorted; document order is the
frozen member order sorted by pageid.  ``canonical_json`` reproduces a
snapshot byte-for-byte for a given input set (modulo the ``frozen_at``
timestamp, which callers may pin).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

SOURCE_SNAPSHOT_SCHEMA = "longworld.source-snapshot.v1"
SNAPSHOT_KIND = "mediawiki_category"
VALUE_TYPES = frozenset({"entity", "string", "quantity", "year", "date", "geo"})
# Minimum surface length (chars) for non-link alias/label matching.
_SURFACE_MIN_LEN = 4
# Mentions per (entity, document) after dedup; keeps offsets deterministic.
_MAX_MENTIONS_PER_DOC = 200
_QID_RE = re.compile(r"^Q[1-9][0-9]{0,11}$")
_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class SnapshotError(ValueError):
    """Raised when API payloads are malformed or a snapshot is invalid."""


# ---------------------------------------------------------------------------
# Input records (built from API payloads or cache files)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """One categorymembers listing entry (a page candidate)."""

    pageid: int
    title: str


@dataclass(frozen=True)
class PageRecord:
    """A frozen page revision (the document source of truth)."""

    pageid: int
    title: str
    revid: int
    timestamp: str
    wikitext: str
    pageprops: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkMeta:
    """A link target resolved via generator=links + pageprops."""

    pageid: int
    title: str
    wikibase_item: str | None


_REDIRECT_RE = re.compile(r"^\s*#(?:redirect|redir)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# API payload parsing (pure; no network in this module)
# ---------------------------------------------------------------------------


def parse_members_payload(payload: dict[str, Any]) -> list[Member]:
    """Extract categorymembers entries from an action=query&list=categorymembers payload."""
    query = payload.get("query")
    entries = query.get("categorymembers") if isinstance(query, dict) else None
    if not isinstance(entries, list) or not entries:
        raise SnapshotError("categorymembers payload has no members")
    members: list[Member] = []
    for item in entries:
        if not isinstance(item, dict):
            raise SnapshotError("categorymembers entry is not an object")
        pageid = item.get("pageid")
        title = item.get("title")
        if (
            not isinstance(pageid, int)
            or pageid <= 0
            or not isinstance(title, str)
            or not title
        ):
            raise SnapshotError("categorymembers entry is invalid")
        members.append(Member(pageid=pageid, title=title))
    return members


def parse_revision_payload(payload: dict[str, Any]) -> PageRecord:
    """Extract one frozen revision from an action=query&prop=revisions payload (formatversion 2)."""
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
        raise SnapshotError("revision payload does not identify one page")
    page = pages[0]
    if page.get("missing") is True:
        raise SnapshotError("revision payload page is missing")
    revisions = page.get("revisions")
    if (
        not isinstance(revisions, list)
        or len(revisions) != 1
        or not isinstance(revisions[0], dict)
    ):
        raise SnapshotError("revision payload has no single revision")
    revision = revisions[0]
    slots = revision.get("slots")
    slot = slots.get("main") if isinstance(slots, dict) else None
    content = slot.get("content") if isinstance(slot, dict) else None
    pageid = page.get("pageid")
    title = page.get("title")
    revid = revision.get("revid")
    timestamp = revision.get("timestamp")
    if (
        not isinstance(pageid, int)
        or pageid <= 0
        or not isinstance(title, str)
        or not title
        or not isinstance(revid, int)
        or revid <= 0
        or not isinstance(timestamp, str)
        or not content
        or not isinstance(content, str)
    ):
        raise SnapshotError("revision payload identity fields are invalid")
    pageprops = page.get("pageprops")
    if pageprops is None:
        pageprops = {}
    if not isinstance(pageprops, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in pageprops.items()
    ):
        raise SnapshotError("revision payload pageprops are invalid")
    return PageRecord(
        pageid=pageid,
        title=title,
        revid=revid,
        timestamp=timestamp,
        wikitext=content,
        pageprops=dict(pageprops),
    )


def parse_links_payload(payload: dict[str, Any]) -> list[LinkMeta]:
    """Extract link targets from a generator=links&prop=pageprops payload (formatversion 2)."""
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list):
        raise SnapshotError("links payload has no pages")
    targets: list[LinkMeta] = []
    for page in pages:
        if not isinstance(page, dict) or page.get("missing") is True:
            continue
        pageid = page.get("pageid")
        title = page.get("title")
        if (
            not isinstance(pageid, int)
            or pageid <= 0
            or not isinstance(title, str)
            or not title
        ):
            continue
        pageprops = page.get("pageprops")
        qid = None
        if isinstance(pageprops, dict):
            candidate = pageprops.get("wikibase_item")
            if isinstance(candidate, str) and _QID_RE.fullmatch(candidate):
                qid = candidate
        targets.append(LinkMeta(pageid=pageid, title=title, wikibase_item=qid))
    targets.sort(key=lambda link: link.pageid)
    return targets


def parse_rightsinfo_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Extract the site license (rightsinfo) and URL prefix from a meta=siteinfo payload."""
    query = payload.get("query")
    if not isinstance(query, dict):
        raise SnapshotError("siteinfo payload has no query")
    rights = query.get("rightsinfo")
    if not isinstance(rights, dict):
        raise SnapshotError("siteinfo payload has no rightsinfo")
    text = rights.get("text")
    url = rights.get("url")
    if not isinstance(text, str) or not text or not isinstance(url, str) or not url:
        raise SnapshotError("siteinfo rightsinfo is invalid")
    general = query.get("general") if isinstance(query.get("general"), dict) else {}
    servername = general.get("servername")
    articlepath = general.get("articlepath")
    prefix = "https://en.wikipedia.org/wiki/"
    if (
        isinstance(servername, str)
        and servername
        and isinstance(articlepath, str)
        and "$1" in articlepath
    ):
        prefix = "https://" + servername + articlepath.replace("$1", "")
    return {"text": text, "url": url, "page_url_prefix": prefix}


def page_url(title: str, prefix: str = "https://en.wikipedia.org/wiki/") -> str:
    """Stable per-page URL used for CC-BY-SA attribution."""
    slug = title.replace(" ", "_")
    quoted = "".join(
        ch if ch.isalnum() and ord(ch) < 128 else _quote_byte(ch) for ch in slug
    )
    return prefix + quoted


def _quote_byte(ch: str) -> str:
    raw = ch.encode("utf-8")
    return "".join(f"%{byte:02X}" for byte in raw)


# ---------------------------------------------------------------------------
# Wikitext → rendered text
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(={2,6})([^=\n].*?)\1[^\S\n]*$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]*))?\]\]")
_TEMPLATE_RE = re.compile(r"\{\{([^{}]*)\}\}")
_EXTERNAL_LINK_RE = re.compile(r"\[https?://\S+\]")
_TAG_RE = re.compile(r"<[^>\n]*>")
_REF_OPEN_RE = re.compile(r"<ref\b[^>]*/?>", re.IGNORECASE)
_MAGICWORDS = (
    "__TOC__",
    "__NOTOC__",
    "__FORCETOC__",
    "__NOEDITSECTION__",
    "__NONEWSECTIONLINK__",
)
_CATEGORY_NS = ("Category:", "category:")
_BOLD_ITALIC_RE = re.compile(r"'{2,5}")


def _clean_wikitext_inline(value: str) -> str:
    """Render one wikitext inline fragment (no headings/tables/lists)."""
    value = _REF_OPEN_RE.sub("", value)
    value = re.sub(r"</ref\s*>", "", value, flags=re.IGNORECASE)
    for magic in _MAGICWORDS:
        value = value.replace(magic, "")

    def _link(match: re.Match[str]) -> str:
        target, display = match.group(1), match.group(2)
        return display if display is not None else target

    value = _WIKILINK_RE.sub(_link, value)
    value = _EXTERNAL_LINK_RE.sub("", value)
    value = _TAG_RE.sub("", value)
    value = _BOLD_ITALIC_RE.sub("", value)  # ''italic'' / '''bold''' markers

    # resolve templates that carry no visible payload (nested one level max)
    for _ in range(2):
        value = _TEMPLATE_RE.sub(lambda m: m.group(1), value)
    return _collapse_spaces(value).strip()


def _collapse_spaces(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value)


@dataclass
class _Rendered:
    text: str
    sections: list[dict[str, Any]]


def _empty_render(title: str) -> _Rendered:
    return _Rendered(text="", sections=[{"title": "", "start": 0, "end": 0}])


def render_wikitext(title: str, wikitext: str) -> _Rendered:
    """Render wikitext into the plain-text document view (returns text + sections)."""
    if _REDIRECT_RE.match(wikitext):
        return _empty_render(title)
    lines: list[str] = [f"# {_clean_wikitext_inline(title)}"]

    stack: list[str] = []  # active container lines ("table"/"list")
    table_headers: list[str] | None = None
    table_row: list[str] = []
    in_infobox = False
    infobox_depth = 0

    for raw_line in wikitext.splitlines():
        line = raw_line.strip()
        if not line:
            if stack and stack[-1] in ("table", "list"):
                stack.pop()
                table_headers = None
                if table_row:
                    lines.append(" | ".join(table_row))
                    table_row = []
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            label = _clean_wikitext_inline(heading.group(2))
            while stack:
                stack.pop()
            table_headers = None
            if table_row:
                lines.append(" | ".join(table_row))
                table_row = []
            if in_infobox:  # malformed wikitext: heading inside the infobox
                in_infobox = False
                infobox_depth = 0
            if level <= 2:
                lines.append("")
                lines.append("#" * level + " " + label)
            continue

        if line.startswith("|}"):
            if table_row:
                lines.append(" | ".join(table_row))
                table_row = []
            if table_headers is not None:
                stack.pop()
                table_headers = None
            continue
        if line.startswith("{|"):
            while stack and stack[-1] == "list":
                stack.pop()
            stack.append("table")
            table_headers = None
            table_row = []
            continue
        if line.startswith("|-"):
            if table_row:
                lines.append(" | ".join(table_row))
                table_row = []
            elif table_headers and table_headers not in lines:
                lines.append(" | ".join(table_headers))
            continue  # row separator; |-bgcolor style attributes are dropped
        if line.startswith(("!", "|")) and stack and stack[-1] == "table":
            # one line = one or more cells (``||`` / ``!!`` split); a leading
            # attributes chunk (e.g. ``width="40%" |Name``) rides on the cell
            body = line[1:]
            cells = _split_row_cells(_strip_table_attrs(body))
            if line.startswith("!") and not table_row:
                # header cells (the header block precedes the first body row)
                if cells:
                    table_headers = (table_headers or []) + cells
            else:
                for cell in cells:
                    if cell or table_row:
                        table_row.append(cell)
            continue

        if line.startswith("{{") and "infobox" in line.lower():
            in_infobox = True
            infobox_depth = line.count("{{") - line.count("}}")
            if stack and stack[-1] == "table":
                stack.pop()
                table_headers = None
            continue  # the {{Infobox ...}} opener itself carries no payload
        if in_infobox:
            # track nested templates so inner closings don't end the block;
            # a line that empties the depth closes the whole infobox
            infobox_depth += line.count("{{") - line.count("}}")
            if infobox_depth <= 0:
                in_infobox = False
                continue
            field = _INFOBOX_FIELD_RE.match(line)
            if field:
                key = _collapse_spaces(field.group(1)).strip()
                rendered_value = _clean_infobox_value(field.group(2))
                if key and rendered_value:
                    lines.append(f"{key}: {rendered_value}")
                continue
            if line.startswith(("|", "*")) or re.fullmatch(r"[{}|\s]+", line):
                continue  # infobox decorations, continuation lists, stray braces
            # else fall through: rare inline prose inside the infobox

        list_match = re.match(r"^([*:;#-]+)\s*(.*)$", line)
        if list_match and list_match.group(2):
            while stack and stack[-1] == "table":
                stack.pop()
                table_headers = None
            marker = list_match.group(1)
            content = _clean_wikitext_inline(list_match.group(2))
            if marker.startswith((";", ":")) and ":" in content:
                term, _, definition = content.partition(":")
                lines.append(
                    "  " * min(len(marker) - 1, 3)
                    + "- "
                    + term.strip()
                    + ": "
                    + definition.strip()
                )
            else:
                lines.append("  " * min(len(marker) - 1, 3) + "- " + content)
            if "list" not in stack:
                stack.append("list")
            continue

        while stack:
            stack.pop()
            table_headers = None
        # stray single-line template, prose, and everything else
        rendered = _clean_wikitext_inline(line)
        if rendered:
            lines.append(rendered)

    if table_row:  # table never closed with |}
        lines.append(" | ".join(table_row))
    text = "\n".join(lines)
    sections = _index_sections(text, title)
    return _Rendered(text=text, sections=sections)


def _strip_table_attrs(cell: str) -> str:
    """Drop a leading table-cell attribute chunk (``width="40%" |``).

    The attribute pipe is the first ``|`` that sits outside any ``[[...]]``
    link and whose prefix contains ``=`` (attribute syntax) but no brackets.
    Wikilink pipes (``[[Target|display]]``) are left untouched.
    """
    depth = 0
    for index, char in enumerate(cell):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "|" and depth == 0:
            prefix = cell[:index]
            if "=" in prefix and "[" not in prefix and "]" not in prefix:
                return cell[index + 1 :]
            return cell
    return cell


def _split_row_cells(line: str) -> list[str]:
    """Split one table row into cleaned cells.

    Cells separate on ``||`` (and a header row on ``!!``); each cell is
    cleaned.  Empty cells are dropped.
    """
    raw_cells = re.split(r"\|\||!!", line)
    cleaned = [_clean_wikitext_inline(cell) for cell in raw_cells]
    return [cell for cell in cleaned if cell]


def _index_sections(text: str, title: str) -> list[dict[str, Any]]:
    """Compute section metadata from rendered headings (title is section 0)."""
    sections: list[dict[str, Any]] = []
    matches = list(re.finditer(r"^(#{1,6}) (.+)$", text, re.MULTILINE))
    title_line = text.splitlines()[0] if text else ""
    sections.append(
        {
            "title": "",
            "level": 1,
            "start": 0,
            "end": len(title_line),
        }
    )
    for match in matches:
        level = len(match.group(1))
        start = match.start()
        nxt = match.end()
        sections.append(
            {"title": match.group(2), "level": level, "start": start, "end": nxt}
        )
    sections.sort(key=lambda section: section["start"])
    for index in range(len(sections) - 1):
        sections[index]["end"] = sections[index + 1]["start"]
    if sections:
        sections[-1]["end"] = len(text)
    return sections


# ---------------------------------------------------------------------------
# Structured view of rendered lines (offsets recovered from the text itself)
# ---------------------------------------------------------------------------

_HEADING_LINE_RE = re.compile(r"^(#{2,6}) (.+)$")
_INFOBOX_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_ ()/&+.'-]{0,40}):\s*(\S.*)$")
_LIST_LINE_RE = re.compile(r"^( *)([-*]) (.+)$")
_PROSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_YEAR_RE = re.compile(r"^-?\d{1,4}( BC)?$")
_DATE_RE = re.compile(
    r"^(\d{1,2} )?(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"( \d{1,2},)? \d{1,4}( BC)?$|^\d{4}-\d{2}-\d{2}$"
)
_QUANTITY_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?(?:\s*[\d/]+)?\s*[a-zA-Z°²³/%]*$")
_COORD_RE = re.compile(r"^-?\d{1,3}(?:\.\d+)?\s*[NS],?\s*-?\d{1,3}(?:\.\d+)?\s*[EW]$")
_NUMBER_PREFIX_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?\s*(.*)$")
_UNIT_TAIL_RE = re.compile(r"^[a-zA-Z°²³/%]+$")


@dataclass(frozen=True)
class StructuredLine:
    """One rendered line with its char span and recovered structure."""

    start: int
    end: int
    kind: (
        str  # title | heading | infobox | table_header | table_row | list_item | prose
    )
    text: str
    key: str | None = None
    value: str | None = None
    cells: tuple[str, ...] = ()
    level: int = 0


def structured_lines(text: str) -> list[StructuredLine]:
    """Recover per-line structure (with char offsets) from rendered text.

    The renderer emits distinctive prefixes (``#`` headings, ``key: value``
    infobox lines, ``a | b`` table rows, ``- item`` list lines), so the
    rendered text itself is the single source of truth for offsets.
    """
    lines: list[StructuredLine] = []
    first_heading_offset = None
    table_block_open = False
    cursor = 0
    for raw in text.split("\n"):
        start, end = cursor, cursor + len(raw)
        cursor = end + 1
        if not raw:
            if table_block_open:
                table_block_open = False
            continue
        if raw.startswith("# ") and not lines:
            lines.append(StructuredLine(start, end, "title", raw, value=raw[2:]))
            continue
        heading = _HEADING_LINE_RE.match(raw)
        if heading:
            first_heading_offset = (
                start if first_heading_offset is None else first_heading_offset
            )
            table_block_open = False
            lines.append(
                StructuredLine(
                    start,
                    end,
                    "heading",
                    raw,
                    value=heading.group(2),
                    level=len(heading.group(1)),
                )
            )
            continue
        if " | " in raw and all(part.strip() for part in raw.split(" | ")):
            kind = "table_header" if not table_block_open else "table_row"
            table_block_open = True
            lines.append(
                StructuredLine(
                    start,
                    end,
                    kind,
                    raw,
                    cells=tuple(part.strip() for part in raw.split(" | ")),
                )
            )
            continue
        table_block_open = False
        list_match = _LIST_LINE_RE.match(raw)
        if list_match:
            lines.append(
                StructuredLine(
                    start,
                    end,
                    "list_item",
                    raw,
                    value=list_match.group(3),
                    level=len(list_match.group(1)) // 2 + 1,
                )
            )
            continue
        before_body = first_heading_offset is None or start < first_heading_offset
        infobox = _INFOBOX_LINE_RE.match(raw) if before_body else None
        if infobox and not raw.startswith("#"):
            lines.append(
                StructuredLine(
                    start,
                    end,
                    "infobox",
                    raw,
                    key=infobox.group(1).strip(),
                    value=infobox.group(2).strip(),
                )
            )
            continue
        lines.append(StructuredLine(start, end, "prose", raw))
    return lines


def classify_value(value: str, entity_surfaces: dict[str, str]) -> str:
    """Map a raw value string to a §14 value_type."""
    if value in entity_surfaces:
        return "entity"
    if _COORD_RE.fullmatch(value):
        return "geo"
    if _YEAR_RE.fullmatch(value) or _DATE_RE.fullmatch(value):
        return "date" if _DATE_RE.fullmatch(value) else "year"
    if _QUANTITY_RE.fullmatch(value) and any(ch.isdigit() for ch in value):
        tail = _NUMBER_PREFIX_RE.match(value)
        if tail and _UNIT_TAIL_RE.fullmatch(tail.group(1).replace(" ", "")):
            return "quantity"
        if _YEAR_RE.fullmatch(value) or _DATE_RE.fullmatch(value):
            return "date" if _DATE_RE.fullmatch(value) else "year"
        return "quantity"
    return "string"


def quantity_unit(value: str) -> str | None:
    """Split '48 m' into ('48', 'm'); return the unit or None."""
    tail = _NUMBER_PREFIX_RE.match(value)
    if not tail:
        return None
    unit = tail.group(1).strip()
    return unit or None


def extract_time(key: str, value: str, value_type: str) -> str | None:
    """Pull a §14 time qualifier from date/year-valued facts."""
    key_l = key.lower()
    if value_type == "date":
        return value
    if value_type == "year" and any(
        token in key_l
        for token in (
            "established",
            "founded",
            "opened",
            "completed",
            "built",
            "year",
            "from",
            "until",
        )
    ):
        return value
    year = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", value)
    if year and any(
        token in key_l for token in ("established", "founded", "opened", "built")
    ):
        return year.group(1)
    return None


# ---------------------------------------------------------------------------
# Document assembly + span helpers
# ---------------------------------------------------------------------------


@dataclass
class DocViews:
    """Intermediate per-page build state."""

    doc: dict[str, Any]
    lines: list[StructuredLine]
    wikilinks: dict[str, Any]  # surface -> target title (first link wins)


_WIKILINK_TARGET_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]*))?\]\]")


def _collect_wikilink_map(wikitext: str) -> dict[str, str]:
    """Map each rendered link surface to its target title (first link wins)."""
    surface_to_target: dict[str, str] = {}
    for match in _WIKILINK_TARGET_RE.finditer(wikitext):
        target, display = match.group(1), match.group(2)
        target = target.strip()
        if not target or target.startswith(_CATEGORY_NS):
            continue
        surface = (display or target).strip()
        if not surface:
            continue
        surface_to_target.setdefault(surface, target)
    return surface_to_target


def _doc_for_page(page: PageRecord, page_url_str: str) -> DocViews:
    rendered = render_wikitext(page.title, page.wikitext)
    doc = {
        "doc_id": f"doc-{page.pageid}",
        "title": page.title,
        "text": rendered.text,
        "sections": rendered.sections,
        "page_url": page_url_str,
        "revision_url": f"https://en.wikipedia.org/w/index.php?oldid={page.revid}",
    }
    return DocViews(
        doc=doc,
        lines=structured_lines(rendered.text),
        wikilinks=_collect_wikilink_map(page.wikitext),
    )


def _span_of_line(line: StructuredLine, part: str) -> dict[str, Any]:
    """Span for a substring of a structured line (first occurrence)."""
    offset = line.text.find(part)
    if offset < 0:
        raise SnapshotError("rendered line lost its extracted part")
    return {
        "doc_id": None,
        "start": line.start + offset,
        "end": line.start + offset + len(part),
    }




# ---------------------------------------------------------------------------
# Entity linking (exact surface match only)
# ---------------------------------------------------------------------------


def _surface_forms(title: str) -> list[str]:
    """Generate surface forms of a page title (full + last-segment variants)."""
    forms = [title]
    if "," in title:
        prefix = title.split(",")[0].strip()
        if len(prefix) >= _SURFACE_MIN_LEN:
            forms.append(prefix)
    last = title.rsplit(" ", 1)[-1] if " " in title else None
    if (
        last
        and len(last) >= _SURFACE_MIN_LEN
        and last not in ("List", "Telescope", "Observatory")
    ):
        forms.append(last)
    return forms


def _find_mentions(text: str, surface: str) -> list[tuple[int, int]]:
    """Case-sensitive whole-word-ish occurrences of a surface in text."""
    if not surface or len(surface) < _SURFACE_MIN_LEN:
        return []
    mentions: list[tuple[int, int]] = []
    start = 0
    while len(mentions) < _MAX_MENTIONS_PER_DOC:
        index = text.find(surface, start)
        if index < 0:
            break
        before_ok = index == 0 or not (text[index - 1].isalnum())
        after_ok = index + len(surface) >= len(text) or not (
            text[index + len(surface)].isalnum()
        )
        if before_ok and after_ok:
            mentions.append((index, index + len(surface)))
        start = index + 1
    return mentions


def _link_page_map(link_meta: list[LinkMeta]) -> dict[str, LinkMeta]:
    return {link.title: link for link in link_meta}


def _resolve_entity(
    surface: str,
    page_title: str,
    wikilinks: dict[str, str],
    link_map: dict[str, LinkMeta],
    frozen_titles: set[str],
) -> str | None:
    """Resolve a surface to an entity title via wiki links, then link pageprops."""
    target = wikilinks.get(surface)
    if target is None and surface == page_title:
        target = page_title
    if target is None:
        # fall back: surface equal to a frozen/linkable title
        if surface in frozen_titles:
            target = surface
        elif surface in link_map:
            target = surface
    if target is None:
        return None
    meta = link_map.get(target)
    if meta is not None:
        return meta.title
    return target


def _qid_for(
    title: str, link_map: dict[str, LinkMeta], own_pageprops: dict[str, str]
) -> str | None:
    """external_qid only if present in the fetched source."""
    qid = own_pageprops.get("wikibase_item")
    if qid and _QID_RE.fullmatch(qid):
        return qid
    meta = link_map.get(title)
    if meta is not None and meta.wikibase_item:
        return meta.wikibase_item
    return None


# ---------------------------------------------------------------------------
# Candidate fact extraction (infobox / table / list / prose)
# ---------------------------------------------------------------------------

# Infobox keys that yield relation labels by direct lowercasing.
_INFOBOX_RELATION_OVERRIDES = {
    "location": "located in",
    "established": "established in",
    "opened": "opened in",
    "completed": "completed in",
    "founded": "founded in",
    "built": "built in",
    "closed": "closed in",
    "organization": "operated by",
    "operator": "operated by",
    "parent organization": "operated by",
}
_MONTHS = (
    "January February March April May June July August September October November December"
).split()


def _relation_for_key(key: str) -> str:
    lowered = key.lower()
    if lowered in _INFOBOX_RELATION_OVERRIDES:
        return _INFOBOX_RELATION_OVERRIDES[lowered]
    lowered = lowered.replace("date", "").strip()
    return lowered if lowered else key.lower()


def _date_from(cell: str) -> str | None:
    match = re.search(
        r"\b(" + "|".join(_MONTHS) + r") (\d{1,2}, )?(\d{4})\b|\b(\d{4})\b", cell
    )
    if not match:
        return None
    if match.group(3):
        return f"{match.group(1)} {match.group(2) or ''}{match.group(3)}".strip()
    return match.group(4)


def _cell_semantics(header: str, cell: str) -> tuple[str, str, str | None] | None:
    """(relation, value, time) triple for a table cell under a header, or None."""
    header_l = header.lower().strip()
    value = cell.strip()
    if not value:
        return None
    if _URL_JUNK_RE.search(value):
        return None  # bibliography/reference URLs are not candidate facts
    if header_l in ("instrument", "instruments"):
        return "uses instrument", value, _date_from(cell)
    if header_l in ("telescope", "telescopes"):
        return "uses telescope", value, None
    if header_l in ("established", "establishment"):
        # rows with a missing year shift cells left under the headers, so a
        # place name can sit in the Established column: require year-likeness
        if _YEARISH_RE.fullmatch(value):
            return "established in", value, None
        return None
    if header_l in ("year", "years", "period"):
        if _YEARISH_RE.fullmatch(value):
            return "active in", value, None
        return None
    if header_l in ("location", "place", "site"):
        return "located in", value, None
    if header_l in (
        "name",
        "notes",
        "note",
        "ref",
        "reference",
        "refs",
        "references",
        "coordinates",
        "latitude",
        "longitude",
        "code",
        "region",
        "type",
    ):
        return None
    if re.fullmatch(r"[\d,.\s]+", header_l):
        return "active in", value, None
    return None


_URL_JUNK_RE = re.compile(r"https?://|\b(?:doi|isbn|bibcode)\b", re.IGNORECASE)
# year-like cell values: 1985, 1970s, c. 1900 (a missing-year row shifts
# cells left, so guard year columns against place names)
_YEARISH_RE = re.compile(r"(?:c\.?\s*)?\d{4}s?|\d{4}-\d{4}")


def _row_subject(
    line: "StructuredLine", table_headers: tuple[str, ...], page_subject: str
) -> str | None:
    """Subject of a table row: the first non-numeric cell under a name-ish
    header, else the page subject (single-entity pages)."""
    if not line.cells:
        return None
    first = line.cells[0]
    header0 = (table_headers[0] if table_headers else "").lower()
    nameish = header0 in (
        "name",
        "observatory",
        "telescope",
        "institution",
        "facility",
        "title",
    )
    if nameish and first and not first.replace(",", "").replace(".", "").isdigit():
        return first
    if (
        first
        and not _URL_JUNK_RE.search(first)
        and len(first) >= 3
        and not first.replace(",", "").replace(".", "").isdigit()
    ):
        return first
    return page_subject


def _prose_candidates(sentence: str) -> list[tuple[str, str, str | None]]:
    """(subject, relation, value) triples for one prose sentence."""
    out: list[tuple[str, str, str | None]] = []
    located = re.search(r"\blocated in ([A-Z][\w .'-]+?)(?:[,.]|$)", sentence)
    if located:
        out.append(("", "located in", located.group(1)))
    established = re.search(r"\bestablished in (\d{4})\b", sentence)
    if established:
        out.append(("", "established in", established.group(1)))
    return out


_INFOBOX_SKIPPED_KEYS = {
    "image",
    "caption",
    "image_map",
    "image size",
    "imagesize",
    "alt",
    "background",
    "module",
    "footnotes",
    "website",
    "homepage",
    "url",
    "code",  # observatory code: keep out of relation extraction
}


def _clean_infobox_value(value: str) -> str:
    """Normalize an infobox value for the first line of a multi-line payload.

    Values that only open a nested template (``{{plainlist|``) render empty so
    the renderer drops the field header; their contents appear as following
    list lines.  Empty after cleaning => field omitted.
    """
    if value.strip().startswith("{{"):
        return ""  # value lives in the continuation lines, not on this one
    rendered = _clean_wikitext_inline(value)
    return rendered.strip()


# Infobox field line: ``| key = value`` (value may span following lines).
_INFOBOX_FIELD_RE = re.compile(
    r"^\|\s*([A-Za-z][A-Za-z0-9_ ()/&+.'-]{0,40})\s*=\s*(.*)$"
)


# ---------------------------------------------------------------------------
# Snapshot assembly
# ---------------------------------------------------------------------------


def _fact_id(subject: str, relation: str, value: str, doc_id: str, start: int) -> str:
    digest = hashlib.sha256(
        f"{subject}|{relation}|{value}|{doc_id}|{start}".encode("utf-8")
    ).hexdigest()
    return "f_" + digest[:16]


def _entity_id(title: str) -> str:
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
    return "e_" + digest[:16]


def _relation_id(subject: str, relation_type: str, value: str) -> str:
    digest = hashlib.sha256(
        f"{subject}|{relation_type}|{value}".encode("utf-8")
    ).hexdigest()
    return "r_" + digest[:16]


def build_snapshot(
    *,
    members: list[Member],
    pages: dict[int, PageRecord],
    link_meta: list[LinkMeta],
    rights: dict[str, str],
    category_title: str,
    frozen_at: str,
    fetched_via: str = "live-api",
    generator_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the §14 SourceSnapshot v1 from frozen inputs (pure function)."""
    if not _ISO_TS_RE.fullmatch(frozen_at):
        raise SnapshotError("frozen_at must be an ISO-8601 Z timestamp")

    ordered = sorted(pages.values(), key=lambda page: page.pageid)
    if not ordered:
        raise SnapshotError("snapshot requires at least one frozen page")
    link_map = _link_page_map(link_meta)
    frozen_titles = {page.title for page in ordered}

    documents: list[dict[str, Any]] = []
    entities: dict[str, dict[str, Any]] = {}
    facts: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    entity_surfaces: dict[str, str] = {}
    for page in ordered:
        for surface in _surface_forms(page.title):
            entity_surfaces.setdefault(surface, page.title)
        for meta in link_meta:
            if meta.wikibase_item:
                entity_surfaces.setdefault(meta.title, meta.title)

    # --- entity records for the frozen pages themselves
    for page in ordered:
        entity_id = _entity_id(page.title)
        mentions: list[dict[str, int]] = []
        qid = _qid_for(page.title, link_map, page.pageprops)
        entities[entity_id] = {
            "entity_id": entity_id,
            "label": page.title,
            "aliases": [],
            "external_qid": qid,
            "doc_id": f"doc-{page.pageid}",
            "mentions": mentions,
        }

    # --- documents + mention scans + fact extraction
    documents_by_id: dict[str, DocViews] = {}
    for page in ordered:
        doc_id = f"doc-{page.pageid}"
        views = _doc_for_page(
            page,
            page_url(
                page.title,
                rights.get("page_url_prefix", "https://en.wikipedia.org/wiki/"),
            ),
        )
        documents.append(views.doc)
        documents_by_id[doc_id] = views

        # mention offsets: link surfaces that resolve to an entity.  Each
        # mention records the doc it was found in; the canonicalization pass
        # later keeps only mentions that live in the entity's anchor doc
        # (the §14 contract scopes an entity record to one document).
        for surface in sorted(views.wikilinks):
            resolved = _resolve_entity(
                surface, page.title, views.wikilinks, link_map, frozen_titles
            )
            if resolved is None:
                continue
            entity_title = resolved
            entity_id = _entity_id(entity_title)
            for start, end in _find_mentions(views.doc["text"], surface):
                if entity_id not in entities:
                    entities[entity_id] = {
                        "entity_id": entity_id,
                        "label": entity_title,
                        "aliases": [],
                        "external_qid": _qid_for(entity_title, link_map, {}),
                        "doc_id": doc_id,
                        "mentions": [],
                    }
                entities[entity_id]["mentions"].append(
                    {"start": start, "end": end, "_doc": doc_id}
                )

        # category membership: ungrounded by construction (listing-level
        # fact, no body span in the frozen docs) -> quarantine
        quarantine.append(
            {
                "item_id": "q_cat_" + _entity_id(page.title)[2:],
                "subject": page.title,
                "relation": "member of category",
                "value": category_title,
                "reason": "category membership has no body-text span in the frozen documents",
                "origin": "categorymembers listing",
            }
        )

        _extract_facts_for_page(
            page=page,
            doc_id=doc_id,
            lines=views.lines,
            wikilinks=views.wikilinks,
            link_map=link_map,
            frozen_titles=frozen_titles,
            entity_surfaces=entity_surfaces,
            entities=entities,
            facts=facts,
            quarantine=quarantine,
        )

    # --- canonicalize mentions and pin one doc per entity.  The §14 shape
    # scopes an entity record to a single doc_id: a frozen page's own entity
    # anchors to that page's doc; every other entity anchors to the doc of
    # its first mention, and mentions from other docs are dropped (the
    # mention offsets in the record only ever address the anchor doc).
    title_to_doc = {page.title: f"doc-{page.pageid}" for page in ordered}
    first_mention_doc: dict[str, str] = {}
    for doc_id in sorted(documents_by_id):
        views = documents_by_id[doc_id]
        page = next(p for p in ordered if f"doc-{p.pageid}" == doc_id)
        for surface in sorted(views.wikilinks):
            resolved = _resolve_entity(
                surface, page.title, views.wikilinks, link_map, frozen_titles
            )
            if resolved is None:
                continue
            first_mention_doc.setdefault(_entity_id(resolved), doc_id)
    for entity in entities.values():
        if entity["label"] in title_to_doc:
            entity["doc_id"] = title_to_doc[entity["label"]]
        elif entity["entity_id"] in first_mention_doc:
            entity["doc_id"] = first_mention_doc[entity["entity_id"]]
        else:
            entity["doc_id"] = documents[0]["doc_id"]
        anchor = entity["doc_id"]
        seen: set[tuple[int, int]] = set()
        unique = []
        for mention in entity["mentions"]:
            if mention.pop("_doc", anchor) != anchor:
                continue  # mention lives in another doc; not addressable here
            pair = (mention["start"], mention["end"])
            if pair not in seen:
                seen.add(pair)
                unique.append(mention)
        unique.sort(key=lambda mention: (mention["start"], mention["end"]))
        entity["mentions"] = unique[:_MAX_MENTIONS_PER_DOC]

    # --- relations (grounded on fact spans; distinct subject/object).  One
    # relation per (subject, relation_type, value): duplicate derivations of
    # the same triple merge their spans into the first occurrence.
    relation_by_id: dict[str, dict[str, Any]] = {}
    for fact_id in sorted(facts):
        fact = facts[fact_id]
        if fact["value_type"] != "entity":
            continue
        if fact["subject"] == fact["value"]:
            continue
        relation_id = _relation_id(fact["subject"], fact["relation"], fact["value"])
        existing = relation_by_id.get(relation_id)
        if existing is None:
            relation_by_id[relation_id] = {
                "relation_id": relation_id,
                "subject": fact["subject"],
                "object": fact["value"],
                "relation_type": fact["relation"],
                "qualifiers": dict(fact["qualifiers"] or {}),
                "supporting_spans": [dict(span) for span in fact["supporting_spans"]],
            }
        else:
            for span in fact["supporting_spans"]:
                candidate = dict(span)
                if candidate not in existing["supporting_spans"]:
                    existing["supporting_spans"].append(candidate)
    relations = [
        relation_by_id[key]
        for key in sorted(
            relation_by_id,
            key=lambda key: (
                relation_by_id[key]["subject"],
                relation_by_id[key]["relation_type"],
                relation_by_id[key]["object"],
            ),
        )
    ]

    fact_list = [facts[key] for key in sorted(facts)]
    entity_list = [entities[key] for key in sorted(entities)]
    documents.sort(key=lambda doc: doc["doc_id"])
    quarantine.sort(key=lambda item: item["item_id"])

    source = {
        "kind": SNAPSHOT_KIND,
        "license": {
            "text": rights["text"],
            "url": rights["url"],
            "note": (
                "Text of Wikipedia articles is available under the Creative Commons "
                "Attribution-ShareAlike License; per-page URLs are recorded in "
                "documents[].page_url for attribution. Not CC0."
            ),
            "page_url_prefix": rights["page_url_prefix"],
        },
        "revisions": {page.title: page.revid for page in ordered},
        "fetched_via": fetched_via,
        "category": category_title,
        "api": "https://en.wikipedia.org/w/api.php",
        "user_agent": USER_AGENT,
        "generator_params": generator_params or {},
    }
    snapshot = {
        "schema_version": SOURCE_SNAPSHOT_SCHEMA,
        "snapshot_id": _snapshot_id(category_title, source, documents),
        "frozen_at": frozen_at,
        "source": source,
        "documents": documents,
        "entities": entity_list,
        "facts": fact_list,
        "relations": relations,
        "ungrounded_quarantine": quarantine,
    }
    validate_snapshot(snapshot)
    return snapshot


def _extract_facts_for_page(
    *,
    page: PageRecord,
    doc_id: str,
    lines: list[StructuredLine],
    wikilinks: dict[str, str],
    link_map: dict[str, LinkMeta],
    frozen_titles: set[str],
    entity_surfaces: dict[str, str],
    entities: dict[str, dict[str, Any]],
    facts: dict[str, dict[str, Any]],
    quarantine: list[dict[str, Any]],
) -> None:
    text_lines = {line.start: line for line in lines}
    subject = page.title
    table_headers: tuple[str, ...] | None = None
    seen_infobox_rel: set[tuple[str, str]] = set()

    for line in lines:
        if line.kind == "title":
            continue
        if line.kind == "heading":
            table_headers = None
            continue

        if line.kind == "infobox" and line.key and line.value:
            key = line.key.lower()
            if key in _INFOBOX_SKIPPED_KEYS:
                continue
            value = _clean_infobox_value(line.value)
            if not value or _QID_RE.fullmatch(value) or _URL_JUNK_RE.search(value):
                continue
            relation = _relation_for_key(key)
            pair = (relation, value)
            if pair in seen_infobox_rel:
                continue
            seen_infobox_rel.add(pair)
            _add_fact(
                subject=subject,
                relation=relation,
                value=value,
                line=line,
                value_part=value,
                doc_id=doc_id,
                wikilinks=wikilinks,
                link_map=link_map,
                frozen_titles=frozen_titles,
                entity_surfaces=entity_surfaces,
                entities=entities,
                facts=facts,
                quarantine=quarantine,
                key=key,
                origin="infobox",
            )
            continue

        if line.kind in ("table_header",):
            table_headers = line.cells
            continue
        if line.kind == "table_row" and table_headers:
            row_subject = _row_subject(line, table_headers, subject)
            if row_subject is None:
                continue
            name_column = _name_column_index(table_headers)
            if name_column is not None and row_subject != subject:
                # a name-ish first column on a single-entity page means the
                # row names one of the page subject's components (a telescope
                # of this observatory): emit the page-level fact too
                _add_fact(
                    subject=subject,
                    relation="uses telescope"
                    if "telescope" in table_headers[name_column].lower()
                    else "includes facility",
                    value=line.cells[name_column],
                    line=line,
                    value_part=line.cells[name_column],
                    doc_id=doc_id,
                    wikilinks=wikilinks,
                    link_map=link_map,
                    frozen_titles=frozen_titles,
                    entity_surfaces=entity_surfaces,
                    entities=entities,
                    facts=facts,
                    quarantine=quarantine,
                    origin="table",
                    column=table_headers[name_column],
                )
            for index, cell in enumerate(line.cells):
                if index >= len(table_headers):
                    break
                if cell == row_subject:
                    continue
                semantics = _cell_semantics(table_headers[index], cell)
                if semantics is None:
                    continue
                relation, value, time = semantics
                if value == row_subject:
                    continue
                _add_fact(
                    subject=row_subject,
                    relation=relation,
                    value=value,
                    line=line,
                    value_part=cell,
                    doc_id=doc_id,
                    wikilinks=wikilinks,
                    link_map=link_map,
                    frozen_titles=frozen_titles,
                    entity_surfaces=entity_surfaces,
                    entities=entities,
                    facts=facts,
                    quarantine=quarantine,
                    time=time,
                    origin="table",
                    column=table_headers[index],
                )
            continue

        if line.kind == "list_item" and line.value:
            content = line.value
            colon = _INFOBOX_LINE_RE.match(content)
            if colon and colon.group(2):
                value = _clean_infobox_value(colon.group(2))
                relation = _relation_for_key(colon.group(1))
                if (
                    value
                    and not _QID_RE.fullmatch(value)
                    and not _URL_JUNK_RE.search(value)
                    and colon.group(1).lower() not in _INFOBOX_SKIPPED_KEYS
                ):
                    _add_fact(
                        subject=subject,
                        relation=relation,
                        value=value,
                        line=line,
                        value_part=colon.group(2).strip(),
                        doc_id=doc_id,
                        wikilinks=wikilinks,
                        link_map=link_map,
                        frozen_titles=frozen_titles,
                        entity_surfaces=entity_surfaces,
                        entities=entities,
                        facts=facts,
                        quarantine=quarantine,
                        key=colon.group(1).lower(),
                        origin="list_definition",
                    )
            continue

        if line.kind == "prose":
            for sentence in _PROSE_SPLIT_RE.split(line.text):
                for cand_subject, cand_relation, cand_value in _prose_candidates(
                    sentence
                ):
                    _add_fact(
                        subject=subject,
                        relation=cand_relation,
                        value=cand_value,
                        line=line,
                        value_part=cand_value,
                        doc_id=doc_id,
                        wikilinks=wikilinks,
                        link_map=link_map,
                        frozen_titles=frozen_titles,
                        entity_surfaces=entity_surfaces,
                        entities=entities,
                        facts=facts,
                        quarantine=quarantine,
                        origin="prose",
                    )
            continue


def _add_fact(
    *,
    subject: str,
    relation: str,
    value: str,
    line: StructuredLine,
    value_part: str,
    doc_id: str,
    wikilinks: dict[str, str],
    link_map: dict[str, LinkMeta],
    frozen_titles: set[str],
    entity_surfaces: dict[str, str],
    entities: dict[str, dict[str, Any]],
    facts: dict[str, dict[str, Any]],
    quarantine: list[dict[str, Any]],
    key: str | None = None,
    time: str | None = None,
    origin: str = "infobox",
    column: str | None = None,
) -> None:
    if not value or not value.strip():
        return
    value = value.strip()
    # locate the value inside the line to get the span
    primary = _span_of_line(line, value_part.strip())
    if value_part.strip() not in line.text:
        quarantine.append(
            {
                "item_id": "q_"
                + _fact_id(subject, relation, value, doc_id, line.start)[2:],
                "subject": subject,
                "relation": relation,
                "value": value,
                "reason": "value not found verbatim in the rendered line",
                "origin": origin,
            }
        )
        return
    resolved_entity = _resolve_entity(
        value, subject, wikilinks, link_map, frozen_titles
    )
    if resolved_entity is None and value in entity_surfaces:
        resolved_entity = entity_surfaces[value]
    value_type = classify_value(
        value, entity_surfaces if resolved_entity is None else {value: resolved_entity}
    )
    if resolved_entity is not None:
        value_type = "entity"
    unit = None
    if value_type == "quantity":
        unit = quantity_unit(value)
    fact_time = time or extract_time(key or relation, value, value_type)
    qualifiers: dict[str, str] = {}
    if origin == "table" and column:
        qualifiers["table_column"] = column
    if fact_time:
        qualifiers["time"] = fact_time
    fact_id = _fact_id(subject, relation, value, doc_id, primary["start"])
    span = {"doc_id": doc_id, "start": primary["start"], "end": primary["end"]}
    doc_text = line.text  # same line; spans validated against full text later
    fact = {
        "fact_id": fact_id,
        "subject": subject,
        "relation": relation,
        "value": value,
        "value_type": value_type,
        "unit": unit,
        "time": fact_time,
        "version": None,
        "qualifiers": qualifiers,
        "supporting_spans": [span],
        "alternative_spans": [],
        "source_hash": hashlib.sha256(
            (doc_id + "|" + value).encode("utf-8")
        ).hexdigest(),
    }
    if resolved_entity is not None and value != resolved_entity:
        fact["qualifiers"]["canonical_entity"] = resolved_entity
    if fact_id in facts:
        existing = facts[fact_id]
        alt = dict(span)
        if alt not in existing["supporting_spans"] and alt not in existing.get(
            "alternative_spans", []
        ):
            existing.setdefault("alternative_spans", []).append(alt)
        return
    facts[fact_id] = fact
    if value_type == "entity" and resolved_entity:
        entity_id = _entity_id(resolved_entity)
        entities.setdefault(
            entity_id,
            {
                "entity_id": entity_id,
                "label": resolved_entity,
                "aliases": [],
                "external_qid": _qid_for(resolved_entity, link_map, {}),
                "doc_id": doc_id,
                "mentions": [],
            },
        )
        # a fact value mention counts as a mention for the linked entity in
        # the fact's own doc (validated against the anchor doc later)
        entities[entity_id]["mentions"].append(
            {"start": primary["start"], "end": primary["end"], "_doc": doc_id}
        )
        if value != resolved_entity and value not in entities[entity_id].get(
            "aliases", []
        ):
            entities[entity_id].setdefault("aliases", []).append(value)


# ---------------------------------------------------------------------------
# Validation, canonical JSON, roundtrip
# ---------------------------------------------------------------------------


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    """Enforce the §14 grounding and determinism constraints (raises SnapshotError)."""
    required = (
        "snapshot_id",
        "frozen_at",
        "source",
        "documents",
        "entities",
        "facts",
        "relations",
        "ungrounded_quarantine",
    )
    for field in required:
        if field not in snapshot:
            raise SnapshotError(f"snapshot is missing field {field!r}")
    if snapshot.get("schema_version") != SOURCE_SNAPSHOT_SCHEMA:
        raise SnapshotError("snapshot schema_version mismatch")

    documents = snapshot["documents"]
    if not isinstance(documents, list) or not documents:
        raise SnapshotError("snapshot must hold at least one document")
    texts: dict[str, str] = {}
    for doc in documents:
        doc_id = doc.get("doc_id")
        text = doc.get("text")
        if not isinstance(doc_id, str) or not isinstance(text, str):
            raise SnapshotError("document identity fields are invalid")
        if doc_id in texts:
            raise SnapshotError(f"duplicate doc_id {doc_id}")
        texts[doc_id] = text
        if not _doc_sections_valid(doc):
            raise SnapshotError(f"sections invalid for {doc_id}")

    entity_ids = [entity.get("entity_id") for entity in snapshot["entities"]]
    if len(entity_ids) != len(set(entity_ids)):
        raise SnapshotError("duplicate entity ids")
    for entity in snapshot["entities"]:
        mentions = entity.get("mentions") or []
        doc_id = entity.get("doc_id")
        if doc_id not in texts:
            raise SnapshotError(f"entity doc_id {doc_id!r} is unknown")
        for mention in mentions:
            start, end = mention.get("start"), mention.get("end")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end > len(texts[doc_id])
                or start >= end
            ):
                raise SnapshotError(
                    f"entity mention offsets out of range for {entity['entity_id']}"
                )
        if [m["start"] for m in mentions] != sorted(m["start"] for m in mentions):
            raise SnapshotError(
                f"entity mentions are not sorted for {entity['entity_id']}"
            )

    fact_ids = [fact.get("fact_id") for fact in snapshot["facts"]]
    if len(fact_ids) != len(set(fact_ids)):
        raise SnapshotError("duplicate fact ids")
    for fact in snapshot["facts"]:
        if fact.get("value_type") not in VALUE_TYPES:
            raise SnapshotError(f"invalid value_type {fact.get('value_type')!r}")
        spans = fact.get("supporting_spans") or []
        if not spans:
            raise SnapshotError(
                f"fact {fact.get('fact_id')} has no supporting span (must be quarantined instead)"
            )
        for span in spans:
            doc_id, start, end = span.get("doc_id"), span.get("start"), span.get("end")
            if doc_id not in texts:
                raise SnapshotError(f"span references unknown doc {doc_id!r}")
            text = texts[doc_id]
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end > len(text)
                or start >= end
                or text[start:end] != fact["value"]
            ):
                raise SnapshotError(
                    f"supporting span of {fact['fact_id']} is not a verbatim match: "
                    f"text[{start}:{end}] != value"
                )
        for span in fact.get("alternative_spans") or []:
            doc_id, start, end = span.get("doc_id"), span.get("start"), span.get("end")
            if (
                doc_id not in texts
                or not isinstance(start, int)
                or not isinstance(end, int)
            ):
                raise SnapshotError(f"alternative span invalid for {fact['fact_id']}")
            if texts[doc_id][start:end] != fact["value"]:
                raise SnapshotError(
                    f"alternative span of {fact['fact_id']} is not a verbatim match"
                )

    relation_ids = [relation.get("relation_id") for relation in snapshot["relations"]]
    if len(relation_ids) != len(set(relation_ids)):
        raise SnapshotError("duplicate relation ids")
    for relation in snapshot["relations"]:
        for span in relation.get("supporting_spans") or []:
            doc_id = span.get("doc_id")
            if doc_id not in texts:
                raise SnapshotError(f"relation span references unknown doc {doc_id!r}")

    ids = [d["doc_id"] for d in snapshot["documents"]]
    if ids != sorted(ids):
        raise SnapshotError("documents are not sorted by doc_id")
    fids = [f["fact_id"] for f in snapshot["facts"]]
    if fids != sorted(fids):
        raise SnapshotError("facts are not sorted by fact_id")
    eids = [e["entity_id"] for e in snapshot["entities"]]
    if eids != sorted(eids):
        raise SnapshotError("entities are not sorted by entity_id")
    qids = [q["item_id"] for q in snapshot["ungrounded_quarantine"]]
    if qids != sorted(qids):
        raise SnapshotError("quarantine items are not sorted by item_id")

    revisions = snapshot["source"]["revisions"]
    doc_titles = {doc["title"] for doc in snapshot["documents"]}
    if set(revisions) != doc_titles:
        raise SnapshotError("revisions map does not match document titles exactly")

    license_info = snapshot["source"]["license"]
    if isinstance(license_info, dict):
        text_value = license_info.get("text", "") + " " + license_info.get("note", "")
    else:
        text_value = str(license_info)
    # A claim of CC0 is a positive assertion like "under CC0"; the license
    # note's explicit disclaimer ("Not CC0") must not trip this check.
    if re.search(r"under\s+cc0|\blicensed?\s+cc0\b|licence:\s*cc0", text_value.lower()):
        raise SnapshotError("wiki snapshot must not claim CC0")


def _doc_sections_valid(doc: dict[str, Any]) -> bool:
    sections = doc.get("sections")
    if not isinstance(sections, list) or not sections:
        return False
    previous_end = -1
    text_len = len(doc["text"])
    for section in sections:
        start, end = section.get("start"), section.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            return False
        if start < 0 or end > text_len or start > end:
            return False
        if start < previous_end:
            return False
        previous_end = end
    return sections[0].get("start") == 0 and sections[-1].get("end") == text_len


def canonical_json(snapshot: dict[str, Any]) -> bytes:
    """Deterministic serialization for byte-for-byte roundtrips."""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=1).encode(
        "utf-8"
    )


def snapshot_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Loader-side roundtrip: revalidate a deserialized snapshot."""
    if not isinstance(data, dict):
        raise SnapshotError("snapshot must be a JSON object")
    validate_snapshot(data)
    return data


def _snapshot_id(
    category_title: str, source: dict[str, Any], documents: list[dict[str, Any]]
) -> str:
    revisions = json.dumps(source["revisions"], sort_keys=True, ensure_ascii=False)
    doc_ids = json.dumps([doc["doc_id"] for doc in documents], sort_keys=True)
    digest = hashlib.sha256(
        (category_title + "|" + revisions + "|" + doc_ids).encode("utf-8")
    ).hexdigest()
    return "snapshot_" + digest[:20]


# ---------------------------------------------------------------------------
# HTTP fetch layer (used by the CLI, never by tests)
# ---------------------------------------------------------------------------

USER_AGENT = "LongWorld-wiki-adapter/0.1 (source freeze tool; contact: xnhyacinth@users.noreply.github.com)"
MAX_PAGES = 50  # hard ceiling above the 10-30 charter budget
_REQUEST_INTERVAL_SECONDS = 1.0


class HttpError(RuntimeError):
    """Raised when the MediaWiki API cannot be reached or answers bad JSON."""


class WikiHttpFetcher:
    """Small MediaWiki REST-style client on stdlib urllib (rate-limited)."""

    def __init__(
        self,
        api_base: str = "https://en.wikipedia.org/w/api.php",
        timeout: float = 60.0,
    ):
        self.api_base = api_base
        self.timeout = timeout
        import time
        import urllib.parse
        import urllib.request

        self._time = time
        self._urlopen = urllib.request.urlopen
        self._urlencode = urllib.parse.urlencode
        self._request_cls = urllib.request.Request
        self._last_request_at: float | None = None
        self.requests = 0

    def get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        url = self.api_base + "?" + self._urlencode(params)
        if self._last_request_at is not None:
            elapsed = self._time.monotonic() - self._last_request_at
            if elapsed < _REQUEST_INTERVAL_SECONDS:
                self._time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at = self._time.monotonic()
        request = self._request_cls(url, headers={"User-Agent": USER_AGENT})
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
            self.requests += 1
        except Exception as error:  # URLError, HTTPError, socket.timeout
            raise HttpError(f"request to {self.api_base} failed: {error}") from error
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HttpError("MediaWiki API returned invalid JSON") from error
        if not isinstance(payload, dict) or "error" in payload:
            message = (
                payload.get("error", {}).get("info")
                if isinstance(payload, dict)
                else None
            )
            raise HttpError(f"MediaWiki API error: {message or 'invalid payload'}")
        return payload

    # -- endpoint-specific helpers -------------------------------------

    def fetch_category_members(self, category: str, limit: int) -> list[Member]:
        members: list[Member] = []
        continue_token: str | None = None
        while len(members) < limit:
            params: dict[str, Any] = {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "list": "categorymembers",
                "cmtitle": category,
                "cmlimit": min(500, limit - len(members)),
                "cmtype": "page",
            }
            if continue_token:
                params["cmcontinue"] = continue_token
            payload = self.get_json(params)
            members.extend(parse_members_payload(payload))
            continue_token = (
                payload.get("continue", {}).get("cmcontinue")
                if isinstance(payload.get("continue"), dict)
                else None
            )
            if not continue_token:
                break
        return members[:limit]

    def fetch_revision(self, pageid: int) -> PageRecord:
        payload = self.get_json(
            {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "prop": "revisions|pageprops",
                "pageids": str(pageid),
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "rvlimit": 1,
            }
        )
        return parse_revision_payload(payload)

    def fetch_links_meta(self, titles: list[str]) -> list[LinkMeta]:
        # per-page: generator=links + pageprops (wikibase_item)
        meta: list[LinkMeta] = []
        for title in titles:
            payload = self.get_json(
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "titles": title,
                    "generator": "links",
                    "gpllimit": "max",
                    "gplnamespace": 0,
                    "prop": "pageprops",
                    "ppprop": "wikibase_item",
                }
            )
            targets = parse_links_payload(payload)
            meta.extend(targets)
        # dedup by pageid, keep lowest pageid entry (deterministic)
        by_pageid: dict[int, LinkMeta] = {}
        for link in sorted(meta, key=lambda item: item.pageid):
            by_pageid.setdefault(link.pageid, link)
        return [by_pageid[key] for key in sorted(by_pageid)]


def _name_column_index(table_headers: tuple[str, ...]) -> int | None:
    """Index of the name-ish column of a table, if the first column is one."""
    if not table_headers:
        return None
    header0 = table_headers[0].lower().strip()
    if header0 in (
        "name",
        "telescope",
        "observatory",
        "instrument",
        "facility",
        "title",
    ):
        return 0
    return None
