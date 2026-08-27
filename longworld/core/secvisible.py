"""Readable SEC HTML/iXBRL views with reversible source character mappings."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

SEC_VISIBLE_TEXT_REVISION = "sec-visible-text-v1"

_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "caption",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "tr",
        "ul",
    }
)
_CELL_TAGS = frozenset({"td", "th"})
_HIDDEN_TAGS = frozenset(
    {
        "head",
        "ix:header",
        "ix:hidden",
        "noscript",
        "script",
        "style",
        "svg",
        "template",
    }
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)"
    r"\s*(?:!important\s*)?(?:;|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SecVisibleSpan:
    """One contiguous normalized interval and its originating source interval."""

    visible_start: int
    visible_end: int
    source_start: int
    source_end: int


@dataclass(frozen=True)
class SecVisibleText:
    """A readable filing-section view bound to exact raw-source intervals."""

    text: str
    spans: tuple[SecVisibleSpan, ...]
    source_char_start: int
    source_char_end: int
    context_char_start: int
    context_char_end: int
    source_sha256: str
    text_sha256: str
    revision: str = SEC_VISIBLE_TEXT_REVISION

    def source_ranges(
        self, visible_start: int, visible_end: int
    ) -> tuple[tuple[int, int], ...]:
        """Return source intervals contributing to a normalized text interval."""

        if (
            isinstance(visible_start, bool)
            or isinstance(visible_end, bool)
            or not isinstance(visible_start, int)
            or not isinstance(visible_end, int)
            or visible_start < 0
            or visible_end < visible_start
            or visible_end > len(self.text)
        ):
            raise ValueError("SEC visible text interval is invalid")
        if visible_start == visible_end:
            return ()

        ranges: list[tuple[int, int]] = []
        for span in self.spans:
            overlap_start = max(visible_start, span.visible_start)
            overlap_end = min(visible_end, span.visible_end)
            if overlap_start >= overlap_end:
                continue
            if (span.visible_end - span.visible_start) == (
                span.source_end - span.source_start
            ):
                source_start = span.source_start + overlap_start - span.visible_start
                source_end = span.source_start + overlap_end - span.visible_start
            else:
                source_start = span.source_start
                source_end = span.source_end
            if ranges and source_start <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], source_end))
            else:
                ranges.append((source_start, source_end))
        return tuple(ranges)

    def visible_ranges(
        self, source_start: int, source_end: int
    ) -> tuple[tuple[int, int], ...]:
        """Return normalized intervals contributed by a raw-source interval."""

        if (
            isinstance(source_start, bool)
            or isinstance(source_end, bool)
            or not isinstance(source_start, int)
            or not isinstance(source_end, int)
            or source_start < self.source_char_start
            or source_end <= source_start
            or source_end > self.source_char_end
        ):
            raise ValueError("SEC source text interval is invalid")

        ranges: list[tuple[int, int]] = []
        for span in self.spans:
            overlap_start = max(source_start, span.source_start)
            overlap_end = min(source_end, span.source_end)
            if overlap_start >= overlap_end:
                continue
            if (span.visible_end - span.visible_start) == (
                span.source_end - span.source_start
            ):
                visible_start = span.visible_start + overlap_start - span.source_start
                visible_end = span.visible_start + overlap_end - span.source_start
            else:
                visible_start = span.visible_start
                visible_end = span.visible_end
            if ranges and visible_start <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], visible_end))
            else:
                ranges.append((visible_start, visible_end))
        return tuple(ranges)


@dataclass(frozen=True)
class _OpenElement:
    tag: str
    hidden: bool


class _VisibleParser(HTMLParser):
    def __init__(self, section: str, source_char_start: int) -> None:
        super().__init__(convert_charrefs=False)
        self._section = section
        self._source_char_start = source_char_start
        self._line_starts = [0]
        self._line_starts.extend(
            index + 1 for index, character in enumerate(section) if character == "\n"
        )
        self._characters: list[str] = []
        self._origins: list[tuple[int, int]] = []
        self._elements: list[_OpenElement] = []
        self._hidden_depth = 0

    def _local_offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _tag_range(self) -> tuple[int, int]:
        local_start = self._local_offset()
        local_end = self._section.find(">", local_start)
        if local_end < 0:
            raise ValueError("SEC visible text encountered an unterminated tag")
        return (
            self._source_char_start + local_start,
            self._source_char_start + local_end + 1,
        )

    def _trim_inline_whitespace(self) -> None:
        while self._characters and self._characters[-1] in {" ", "\t"}:
            self._characters.pop()
            self._origins.pop()

    def _boundary(self, character: str, source_range: tuple[int, int]) -> None:
        self._trim_inline_whitespace()
        if not self._characters:
            return
        if character == "\n":
            if self._characters[-1] != "\n":
                self._characters.append(character)
                self._origins.append(source_range)
            return
        if self._characters[-1] not in {"\n", "\t"}:
            self._characters.append(character)
            self._origins.append(source_range)

    def _append_text(
        self,
        value: str,
        source_start: int,
        *,
        one_source_interval: bool = False,
        source_end: int | None = None,
    ) -> None:
        for match in re.finditer(r"\s+|\S+", value):
            token = match.group()
            if one_source_interval:
                if source_end is None:
                    raise ValueError("SEC visible entity source interval is missing")
                token_start = source_start
                token_end = source_end
            else:
                token_start = source_start + match.start()
                token_end = source_start + match.end()
            if token.isspace():
                if self._characters and self._characters[-1] not in {" ", "\n", "\t"}:
                    self._characters.append(" ")
                    self._origins.append((token_start, token_end))
                continue
            if one_source_interval:
                for character in token:
                    self._characters.append(character)
                    self._origins.append((token_start, token_end))
                continue
            for offset, character in enumerate(token, start=match.start()):
                self._characters.append(character)
                self._origins.append((source_start + offset, source_start + offset + 1))

    @staticmethod
    def _is_hidden(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in _HIDDEN_TAGS:
            return True
        attributes = {name.lower(): value for name, value in attrs}
        style = attributes.get("style") or ""
        return (
            "hidden" in attributes
            or (attributes.get("aria-hidden") or "").lower() == "true"
            or _HIDDEN_STYLE.search(style) is not None
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        inherited_hidden = self._hidden_depth > 0
        hidden = inherited_hidden or self._is_hidden(tag, attrs)
        if tag not in _VOID_TAGS:
            self._elements.append(_OpenElement(tag=tag, hidden=hidden))
            if hidden:
                self._hidden_depth += 1
        if hidden:
            return
        source_range = self._tag_range()
        if tag in _CELL_TAGS:
            self._boundary("\t", source_range)
        elif tag in _BLOCK_TAGS or tag in {"br", "hr"}:
            self._boundary("\n", source_range)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._hidden_depth > 0 or self._is_hidden(tag, attrs):
            return
        if tag in _CELL_TAGS:
            self._boundary("\t", self._tag_range())
        elif tag in _BLOCK_TAGS or tag in {"br", "hr"}:
            self._boundary("\n", self._tag_range())

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        matching_index = next(
            (
                index
                for index in range(len(self._elements) - 1, -1, -1)
                if self._elements[index].tag == tag
            ),
            None,
        )
        was_visible = self._hidden_depth == 0
        if matching_index is not None:
            removed = self._elements[matching_index:]
            del self._elements[matching_index:]
            self._hidden_depth -= sum(element.hidden for element in removed)
        if was_visible and tag in _BLOCK_TAGS:
            self._boundary("\n", self._tag_range())

    def handle_data(self, data: str) -> None:
        if self._hidden_depth > 0 or not data:
            return
        local_start = self._local_offset()
        if not self._section.startswith(data, local_start):
            raise ValueError("SEC visible text parser lost its source position")
        self._append_text(data, self._source_char_start + local_start)

    def handle_entityref(self, name: str) -> None:
        if self._hidden_depth > 0:
            return
        local_start = self._local_offset()
        raw = f"&{name};"
        if not self._section.startswith(raw, local_start):
            raw = f"&{name}"
        self._append_text(
            unescape(raw),
            self._source_char_start + local_start,
            one_source_interval=True,
            source_end=self._source_char_start + local_start + len(raw),
        )

    def handle_charref(self, name: str) -> None:
        if self._hidden_depth > 0:
            return
        local_start = self._local_offset()
        raw = f"&#{name};"
        if not self._section.startswith(raw, local_start):
            raw = f"&#{name}"
        self._append_text(
            unescape(raw),
            self._source_char_start + local_start,
            one_source_interval=True,
            source_end=self._source_char_start + local_start + len(raw),
        )

    def result(
        self, source_window: tuple[int, int] | None = None
    ) -> tuple[str, tuple[SecVisibleSpan, ...]]:
        self._trim_inline_whitespace()
        characters = list(self._characters)
        origins = list(self._origins)
        if source_window is not None:
            source_start, source_end = source_window
            selected = [
                (character, origin)
                for character, origin in zip(characters, origins, strict=True)
                if max(source_start, origin[0]) < min(source_end, origin[1])
            ]
            characters = [character for character, _origin in selected]
            origins = [origin for _character, origin in selected]
        while characters and characters[0] in {" ", "\n", "\t"}:
            characters.pop(0)
            origins.pop(0)
        while characters and characters[-1] in {" ", "\n", "\t"}:
            characters.pop()
            origins.pop()
        text = "".join(characters)
        if not text:
            return "", ()

        spans: list[SecVisibleSpan] = []
        visible_start = 0
        source_start, source_end = origins[0]
        previous_origin = origins[0]
        for index, origin in enumerate(origins[1:], start=1):
            previous_is_unit = previous_origin[1] - previous_origin[0] == 1
            origin_is_unit = origin[1] - origin[0] == 1
            extends_linear = (
                previous_is_unit and origin_is_unit and previous_origin[1] == origin[0]
            )
            extends_shared = previous_origin == origin
            if extends_linear or extends_shared:
                source_end = max(source_end, origin[1])
            else:
                spans.append(
                    SecVisibleSpan(
                        visible_start=visible_start,
                        visible_end=index,
                        source_start=source_start,
                        source_end=source_end,
                    )
                )
                visible_start = index
                source_start, source_end = origin
            previous_origin = origin
        spans.append(
            SecVisibleSpan(
                visible_start=visible_start,
                visible_end=len(text),
                source_start=source_start,
                source_end=source_end,
            )
        )
        return text, tuple(spans)


def sec_visible_provenance_id(
    *,
    parent_provenance_id: str,
    raw_section_sha256: str,
    visible_text_sha256: str,
    revision: str = SEC_VISIBLE_TEXT_REVISION,
) -> str:
    """Bind one readable view to its exact raw view and normalizer revision."""

    payload = {
        "operation": revision,
        "parent_provenance_id": parent_provenance_id,
        "raw_section_sha256": raw_section_sha256,
        "visible_text_sha256": visible_text_sha256,
    }
    return (
        "derived-sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def normalize_sec_visible_text(
    source_text: str,
    *,
    char_start: int = 0,
    char_end: int | None = None,
    context_char_start: int | None = None,
    context_char_end: int | None = None,
) -> SecVisibleText:
    """Normalize one raw SEC HTML/iXBRL interval without losing provenance."""

    if char_end is None:
        char_end = len(source_text)
    if context_char_start is None:
        context_char_start = char_start
    if context_char_end is None:
        context_char_end = char_end
    if (
        not isinstance(source_text, str)
        or isinstance(char_start, bool)
        or isinstance(char_end, bool)
        or not isinstance(char_start, int)
        or not isinstance(char_end, int)
        or char_start < 0
        or char_end <= char_start
        or char_end > len(source_text)
        or isinstance(context_char_start, bool)
        or isinstance(context_char_end, bool)
        or not isinstance(context_char_start, int)
        or not isinstance(context_char_end, int)
        or context_char_start < 0
        or context_char_start > char_start
        or context_char_end < char_end
        or context_char_end > len(source_text)
    ):
        raise ValueError("SEC visible source interval is invalid")

    parse_start = context_char_start
    prior_open = source_text.rfind("<", 0, context_char_start)
    prior_close = source_text.rfind(">", 0, context_char_start)
    if prior_open > prior_close:
        boundary = source_text.find(">", context_char_start, context_char_end)
        parse_start = context_char_end if boundary < 0 else boundary + 1
    parse_end = context_char_end
    trailing_open = source_text.rfind("<", parse_start, context_char_end)
    trailing_close = source_text.rfind(">", parse_start, context_char_end)
    if trailing_open > trailing_close:
        parse_end = trailing_open

    section = source_text[parse_start:parse_end]
    parser = _VisibleParser(section, parse_start)
    parser.feed(section)
    parser.close()
    text, spans = parser.result(source_window=(char_start, char_end))
    return SecVisibleText(
        text=text,
        spans=spans,
        source_char_start=char_start,
        source_char_end=char_end,
        context_char_start=context_char_start,
        context_char_end=context_char_end,
        source_sha256=hashlib.sha256(
            source_text[char_start:char_end].encode()
        ).hexdigest(),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def validate_sec_visible_text(source_text: str, visible: SecVisibleText) -> None:
    """Replay a normalized SEC view against the exact source interval."""

    if not isinstance(visible, SecVisibleText):
        raise TypeError("SEC visible text record is invalid")
    if (
        visible.source_char_start < 0
        or visible.source_char_end <= visible.source_char_start
        or visible.source_char_end > len(source_text)
    ):
        raise ValueError("SEC visible source interval is invalid")
    section = source_text[visible.source_char_start : visible.source_char_end]
    if hashlib.sha256(section.encode()).hexdigest() != visible.source_sha256:
        raise ValueError("SEC visible text source hash mismatch")
    expected = normalize_sec_visible_text(
        source_text,
        char_start=visible.source_char_start,
        char_end=visible.source_char_end,
        context_char_start=visible.context_char_start,
        context_char_end=visible.context_char_end,
    )
    if visible != expected:
        raise ValueError("SEC visible text deterministic replay mismatch")
