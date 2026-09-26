"""Strict catalog-to-frozen-header identity check for Gutenberg source intake."""

from __future__ import annotations

import re


def _norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def identity_header(raw: bytes, book: dict) -> tuple[str, str]:
    head = raw.decode("utf-8-sig").replace("\r\n", "\n").split("*** START OF", 1)[0]
    title = re.search(r"(?im)^Title:\s*(.+)$", head)
    author = re.search(r"(?im)^Author:\s*(.+)$", head)
    if title is None or author is None:
        raise ValueError("missing_title_or_author_header")
    title_text, author_text = title.group(1).strip(), author.group(1).strip()
    catalog_title = _norm(book["title"].split(":", 1)[0])
    header_title = _norm(title_text)
    surname = _norm(book["author"].split(";", 1)[0].split(",", 1)[0])
    if not catalog_title or (
        catalog_title not in header_title and header_title not in catalog_title
    ):
        raise ValueError("catalog_title_header_mismatch")
    if not surname or surname not in _norm(author_text):
        raise ValueError("catalog_author_header_mismatch")
    return title_text, author_text
