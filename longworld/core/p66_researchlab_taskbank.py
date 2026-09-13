"""Deterministic source parsing for the P66 ResearchLab revision taskbank."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

REVISION = "longworld.p66-researchlab-taskbank.v1"
READABLE_SUFFIXES = {".tex", ".txt", ".md"}
EXCLUDED_NAMES = {
    "contributors.tex",
    "main.tex",
    "mainbib.bib",
    "commands.tex",
    "macros.tex",
}


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode())


def load_text_tar(path: Path) -> dict[str, str]:
    """Read bounded regular UTF-8 scientific source files without extracting."""
    result: dict[str, str] = {}
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix("./")
            pure = Path(name)
            if (
                not member.isfile()
                or member.issym()
                or member.islnk()
                or pure.is_absolute()
                or ".." in pure.parts
                or pure.suffix.lower() not in READABLE_SUFFIXES
                or pure.name.lower() in EXCLUDED_NAMES
                or member.size > 2_000_000
            ):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                text = handle.read().decode("utf-8")
            except UnicodeDecodeError:
                continue
            if scientific_text(text):
                result[name] = text.replace("\r\n", "\n")
    return result


def scientific_text(text: str) -> bool:
    visible = re.sub(r"(?m)^\s*%.*$", "", text)
    words = re.findall(r"[A-Za-z]{3,}", visible)
    return len(words) >= 20


def _meaningful_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("%"):
            continue
        if len(re.findall(r"[A-Za-z0-9]", line)) < 12:
            continue
        lines.append(line)
    return lines


def delta_signature(old: str, new: str) -> dict[str, str] | None:
    """Return a visible exact excerpt from the first substantive delta."""
    before, after = _meaningful_lines(old), _meaningful_lines(new)
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    candidates: list[tuple[int, int, int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            candidates.append((i1, i2, j1, j2))
    for i1, i2, j1, j2 in candidates:
        old_excerpt = next((x for x in before[i1:i2] if len(x) >= 20), "")
        new_excerpt = next((x for x in after[j1:j2] if len(x) >= 20), "")
        normalized_old = re.sub(r"[^a-z0-9]+", "", old_excerpt.lower())
        normalized_new = re.sub(r"[^a-z0-9]+", "", new_excerpt.lower())
        if normalized_old == normalized_new:
            continue
        if old_excerpt or new_excerpt:
            return {
                "status": (
                    "replaced"
                    if old_excerpt and new_excerpt
                    else "removed"
                    if old_excerpt
                    else "added"
                ),
                "old_excerpt": old_excerpt[:500],
                "new_excerpt": new_excerpt[:500],
            }
    return None


def render_records(
    old_version: str,
    new_version: str,
    selected: list[tuple[str, str, str]],
) -> tuple[str, dict[str, tuple[int, int]]]:
    parts: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    cursor = 0
    for path, old, new in selected:
        for version, text in ((old_version, old), (new_version, new)):
            block = (
                f"<<< COMPLETE_SOURCE_RECORD version={version} path={path} >>>\n"
                f"{text}\n<<< END_COMPLETE_SOURCE_RECORD >>>\n"
            )
            start, end = cursor, cursor + len(block)
            spans[f"{version}:{path}"] = (start, end)
            parts.append(block)
            cursor = end
    return "".join(parts), spans


def answer_with_complete_records(
    available_record_ids: set[str], required_record_ids: set[str], answer: object
) -> object:
    """Apply the declared record-presence contract, not a cropped-source solver."""
    return answer if required_record_ids <= available_record_ids else "UNKNOWN"


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _visible(element: ET.Element) -> str:
    return " ".join(" ".join(element.itertext()).split())


def elife_records(xml_text: str) -> tuple[dict[str, str], str]:
    root = ET.fromstring(xml_text)
    body = next(element for element in root.iter() if _local_name(element) == "body")
    records: dict[str, str] = {}
    for index, section in enumerate(body, start=1):
        if _local_name(section) != "sec":
            continue
        key = section.attrib.get("id", f"body-section-{index}")
        records[f"body/{key}"] = _visible(section)
    return records, _visible(root)


def bundle_path_sets(
    weighted_paths: list[tuple[str, int]],
    *,
    minimum: int,
    maximum: int,
) -> list[tuple[str, ...]]:
    """Enumerate deterministic distinct consecutive source groups in one band."""
    ordered = sorted(weighted_paths)
    found: set[tuple[str, ...]] = set()
    for start in range(len(ordered)):
        total = 0
        paths: list[str] = []
        for path, weight in ordered[start:]:
            if total + weight > maximum:
                break
            paths.append(path)
            total += weight
            if total >= minimum:
                found.add(tuple(paths))
                break
    return sorted(found)


def exact_numeric_range(tokens: int) -> str | None:
    for name, low, high in (
        ("64k", 64_000, 65_536),
        ("128k", 128_000, 131_072),
        ("256k", 256_000, 262_144),
    ):
        if low <= tokens <= high:
            return name
    return None
