from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

from longworld.core.cascade import cascade_events
from longworld.core.grounded import grounded_events
from longworld.core.groundedspan import normalize_fact_value
from longworld.core.provenance import ProvenanceError
from longworld.core.sourceworkflow import PAPER_SOURCE_KIND, WIKIMEDIA_SOURCE_KIND
from longworld.core.wikiparse import (
    WIKI_ENTITY_VIEW_PREFIX,
    WIKI_FACT_PARSER_REVISION_V2,
    WIKI_SECTION_REVISION,
    WIKI_SECTION_VIEW_PREFIX,
    extract_wikipedia_wikitext,
    parse_wiki_claim_program,
)
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.researchlab.events import (
    apply_event,
    check_preconditions,
    init_values,
    parse_arxiv_benchmark_detail,
    parse_arxiv_benchmark_observation,
    parse_arxiv_training_hardware,
)

_WIKI_SECTION_OFFSETS = {
    "early_work": 0,
    "commemoration": 8,
    "wikidata_entity": 8,
    "popular_culture": 401,
    "appendix_rest": 402,
}
_WIKI_CLAIM_TIERS = (
    (
        "16k",
        ((5, "compute", "birth-date reconstruction"),),
        ("early_work",),
    ),
    (
        "32k",
        ((400, "compute", "commemoration and entity resolution"),),
        ("early_work", "commemoration", "wikidata_entity"),
    ),
    (
        "64k",
        ((800, "compute", "popular-culture reconstruction"),),
        ("early_work", "commemoration", "popular_culture", "wikidata_entity"),
    ),
)
ARXIV_SOURCE_ENVELOPE_REVISION = "researchlab-arxiv-source-envelope-v1"
_WIKI_LEGACY_ROLE_TAGS = {
    "born": "BORN",
    "early_career": "CAREER",
    "revolutionary_committee": "COMMITTEE",
    "early_transition": "EARLY",
    "commemoration": "COMM",
    "entity": "ENTITY",
    "popular_culture": "POP",
}
_WIKI_REVISION_HUNK = "wiki_revision_hunk_v1"
_WIKI_CANONICAL_FACT_SPAN = "wiki_canonical_fact_span_v1"
_WIKI_CANONICAL_FACT_SPAN_MAX_CHARS = 12_288
_WIKI_MAINTENANCE_CHANGE = re.compile(
    r"\b(?:bot|citation|copyedit|formatting|maintenance|revert|rvv|template|typo)\b",
    re.IGNORECASE,
)
_WIKI_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")


@dataclass(frozen=True)
class _WikiRevisionHunk:
    change_kind: str
    before_start: int
    before_end: int
    before_span: str
    after_start: int
    after_end: int
    after_span: str


@dataclass(frozen=True)
class _WikiCanonicalFactSpan:
    section_start: int
    source_start: int
    source_end: int
    source_span: str


def _date(start: date, months: int, extra_days: int = 0) -> date:
    # Approximate month steps without dateutil.
    return start + timedelta(days=30 * months + extra_days)


def _semantic_arxiv_body(
    record: Any,
    *,
    included_basenames: frozenset[str] | None = None,
    bind_file_spans: bool = False,
) -> tuple[str, str, list[str], list[dict[str, Any]]]:
    """Preserve semantic LaTeX while excluding non-body preamble formatting."""
    try:
        payload = json.loads(record.text)
    except json.JSONDecodeError as error:
        raise ValueError("arXiv source record is not canonical JSON") from error
    raw_sources = payload.get("latex_sources") if isinstance(payload, dict) else None
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("arXiv source record has no LaTeX body")
    selected: list[tuple[str, str]] = []
    paths: set[str] = set()
    selected_basenames: set[str] = set()
    for source in raw_sources:
        if not isinstance(source, dict) or set(source) not in (
            {"path", "text"},
            {"path", "text", "sha256"},
        ):
            raise ValueError("arXiv LaTeX source entry is invalid")
        path = str(source["path"])
        text = str(source["text"])
        if not path or path in paths or not text:
            raise ValueError("arXiv LaTeX source entry is invalid")
        paths.add(path)
        basename = path.rsplit("/", 1)[-1]
        if basename == "preamble.tex" or (
            included_basenames is not None and basename not in included_basenames
        ):
            continue
        if basename in selected_basenames:
            raise ValueError("arXiv selected source basename is not unique")
        selected_basenames.add(basename)
        selected.append((path, text))
    if not selected:
        raise ValueError("arXiv semantic LaTeX body is empty")
    revision_id = record.attribute("revision_id")
    if not revision_id:
        raise ValueError("arXiv source record has no revision identity")
    header = (
        f"% arXiv manuscript revision {revision_id}\n"
        f"% arXiv submitted_at {record.occurred_at}\n"
    )
    body = header + "\n\n".join(text for _path, text in selected)
    source_file_spans: list[dict[str, Any]] = []
    cursor = len(header)
    for index, (path, text) in enumerate(selected):
        if index:
            cursor += 2
        end = cursor + len(text)
        source_file_spans.append(
            {
                "path": path,
                "basename": path.rsplit("/", 1)[-1],
                "char_start": cursor,
                "char_end": end,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        cursor = end
    if cursor != len(body):
        raise ValueError("arXiv selected source spans do not cover the body")
    text_sha256 = hashlib.sha256(body.encode()).hexdigest()
    excluded_paths = sorted(
        path
        for path in paths
        if path.rsplit("/", 1)[-1] == "preamble.tex"
        or (
            included_basenames is not None
            and path.rsplit("/", 1)[-1] not in included_basenames
        )
    )
    operation = (
        "arxiv_semantic_latex_body_v2"
        if bind_file_spans
        else "arxiv_semantic_latex_body_v1"
    )
    digest_payload = {
        "operation": operation,
        "parent_provenance_id": record.provenance_id,
        "excluded_paths": excluded_paths,
        "text_sha256": text_sha256,
    }
    if bind_file_spans:
        digest_payload.update(
            {
                "source_file_spans": source_file_spans,
                "source_view_basenames": sorted(selected_basenames),
            }
        )
    provenance = hashlib.sha256(
        json.dumps(
            digest_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return body, f"derived-sha256:{provenance}", excluded_paths, source_file_spans


def _semantic_arxiv_section_body(
    record: Any,
    *,
    basename: str,
    section_names: frozenset[str],
) -> tuple[str, str, list[str], list[dict[str, Any]]]:
    """Derive a deterministic view from complete top-level LaTeX sections."""
    payload = json.loads(record.text)
    raw_sources = payload.get("latex_sources") if isinstance(payload, dict) else None
    matches = [
        source
        for source in raw_sources or []
        if str(source.get("path") or "").rsplit("/", 1)[-1] == basename
    ]
    if len(matches) != 1 or not section_names:
        raise ValueError("arXiv section view source is not unique")
    path = str(matches[0]["path"])
    text = str(matches[0]["text"])
    headings = list(re.finditer(r"(?m)^\\section\*?\{(?P<name>[^}]*)\}", text))
    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        name = " ".join(heading.group("name").split())
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        if name in sections:
            raise ValueError("arXiv top-level section name is duplicated")
        sections[name] = text[heading.start() : end]
    if not section_names.issubset(sections):
        raise ValueError("arXiv requested top-level section is missing")
    selected_text = "\n\n".join(
        sections[name] for name in sections if name in section_names
    )
    revision_id = record.attribute("revision_id")
    header = (
        f"% arXiv manuscript revision {revision_id}\n"
        f"% arXiv submitted_at {record.occurred_at}\n"
    )
    body = header + selected_text
    span = {
        "path": path,
        "basename": basename,
        "char_start": len(header),
        "char_end": len(body),
        "text_sha256": hashlib.sha256(selected_text.encode()).hexdigest(),
    }
    excluded_paths = sorted(
        str(source["path"])
        for source in raw_sources
        if str(source.get("path") or "") != path
    )
    digest_payload = {
        "operation": "arxiv_semantic_latex_body_v2",
        "parent_provenance_id": record.provenance_id,
        "excluded_paths": excluded_paths,
        "source_file_spans": [span],
        "source_view_basenames": [basename],
        "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    provenance = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return body, f"derived-sha256:{provenance}", excluded_paths, [span]


def _semantic_arxiv_file_body(
    record: Any,
    *,
    source_path: str,
    strip_after: str = "",
    preserved_quote: str = "",
) -> tuple[str, str, list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compile one reachable LaTeX file while preserving one grounded claim."""
    payload = json.loads(record.text)
    raw_sources = payload.get("latex_sources") if isinstance(payload, dict) else None
    matches = [
        source
        for source in raw_sources or []
        if str(source.get("path") or "") == source_path
    ]
    if len(matches) != 1:
        raise ValueError("arXiv exact source path is not unique")
    source_text = str(matches[0].get("text") or "")
    selected_text = source_text
    if strip_after:
        if selected_text.count(strip_after) != 1:
            raise ValueError("arXiv exact source suffix is not unique")
        selected_text = selected_text[: selected_text.index(strip_after)].rstrip() + "\n"
    placeholder = "LONGWORLDSPARKSGROUNDEDCLAIM"
    if preserved_quote:
        if selected_text.count(preserved_quote) != 1 or placeholder in selected_text:
            raise ValueError("arXiv compiled claim is not unique")
        selected_text = selected_text.replace(preserved_quote, placeholder)
    compiled_lines: list[str] = []
    for raw_line in selected_text.splitlines():
        visible = re.split(r"(?<!\\)%", raw_line, maxsplit=1)[0].rstrip()
        if visible.strip():
            compiled_lines.append(visible)
    selected_text = "\n".join(compiled_lines) + "\n"
    selected_text = re.sub(
        r"\\(?:label|ref|eqref|cite|citep|citet|autoref|cref|Cref|vspace|hspace)"
        r"\*?(?:\[[^\]]*\])?\{[^{}]*\}",
        "",
        selected_text,
    )
    for _ in range(3):
        selected_text = re.sub(
            r"\\(?:textit|textbf|textrm|texttt|emph|underline)\*?\{([^{}]*)\}",
            r"\1",
            selected_text,
        )
    selected_text = re.sub(
        r"\\(?:clearpage|newpage|noindent|centering)\b", "", selected_text
    )
    if preserved_quote:
        selected_text = selected_text.replace(placeholder, preserved_quote)
    if not selected_text:
        raise ValueError("arXiv exact source view is empty")
    revision_id = record.attribute("revision_id")
    header = (
        f"% arXiv manuscript revision {revision_id}\n"
        f"% arXiv submitted_at {record.occurred_at}\n"
    )
    body = header + selected_text
    basename = source_path.rsplit("/", 1)[-1]
    span = {
        "path": source_path,
        "basename": basename,
        "char_start": len(header),
        "char_end": len(body),
        "text_sha256": hashlib.sha256(selected_text.encode()).hexdigest(),
    }
    excluded_paths = sorted(
        str(source["path"])
        for source in raw_sources or []
        if str(source.get("path") or "") != source_path
    )
    digest_payload = {
        "operation": "arxiv_semantic_latex_body_v2",
        "parent_provenance_id": record.provenance_id,
        "excluded_paths": excluded_paths,
        "source_file_spans": [span],
        "source_compile_receipts": [
            {
                "path": source_path,
                "compiler_revision": "visible-latex-v1",
                "source_byte_start": 0,
                "source_byte_end": len(source_text.encode()),
                "source_text_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                "compiled_text_sha256": span["text_sha256"],
            }
        ],
        "source_view_basenames": [basename],
        "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    provenance = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        body,
        f"derived-sha256:{provenance}",
        excluded_paths,
        [span],
        list(digest_payload["source_compile_receipts"]),
    )


def _canonical_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _arxiv_source_envelope(
    *,
    workflow: Any,
    record: Any,
    text_sha256: str,
    provenance_id: str,
    provenance_operation: str = "arxiv_semantic_latex_body_v1",
    source_file_spans: list[dict[str, Any]] | None = None,
    source_compile_receipts: list[dict[str, Any]] | None = None,
    source_view_basenames: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "revision": ARXIV_SOURCE_ENVELOPE_REVISION,
        "workflow_id": workflow.workflow_id,
        "record_id": record.record_id,
        "revision_id": record.attribute("revision_id"),
        "occurred_at": record.occurred_at,
        "source_sha256": record.source_sha256,
        "parent_provenance_id": record.provenance_id,
        "source_url": record.source_url,
        "retrieval_url": record.retrieval_url,
        "text_sha256": text_sha256,
        "provenance_id": provenance_id,
    }
    if provenance_operation == "arxiv_semantic_latex_body_v2":
        payload["source_file_spans"] = list(source_file_spans or [])
        payload["source_view_basenames"] = list(source_view_basenames or [])
        if source_compile_receipts:
            payload["source_compile_receipts"] = list(source_compile_receipts)
    return payload


def _grounded_fact(
    *, source_id: str, text: str, fact_id: str, quote: str, char_start: int
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "source_id": source_id,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "quote": quote,
        "char_start": char_start,
        "char_end": char_start + len(quote),
        "normalized_quote": normalize_fact_value(quote),
    }


def _grounded_source(
    *, source_id: str, text: str, facts: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "visible_text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "facts": facts,
        "relations": [],
    }


def _wiki_section_fact_spans(
    section: Any, *, shift: int, source_id: str
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for fact in section.facts:
        span = fact.to_span(shift=shift)
        span["fact_id"] = f"{source_id}:{fact.role}"
        spans.append(span)
    return spans


def _wiki_grounded_source(
    *, event_id: str, section: Any, section_text: str, prefix_text: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spans = _wiki_section_fact_spans(
        section, shift=len(prefix_text), source_id=event_id
    )
    ground_start = section_text.index(section.ground_value)
    facts = [
        _grounded_fact(
            source_id=event_id,
            text=section_text,
            fact_id=f"{event_id}:ground",
            quote=section.ground_value,
            char_start=ground_start,
        ),
        *(
            _grounded_fact(
                source_id=event_id,
                text=section_text,
                fact_id=str(span["fact_id"]),
                quote=str(span["evidence_quote"]),
                char_start=int(span["char_start"]),
            )
            for span in spans
        ),
    ]
    return _grounded_source(source_id=event_id, text=section_text, facts=facts), spans


def _wiki_canonical_fact_span(
    *, section: Any, source_body: str
) -> _WikiCanonicalFactSpan | None:
    """Select the one complete source paragraph containing every exact fact."""
    if not section.facts or source_body.count(section.wikitext) != 1:
        return None
    fact_start = min(int(fact.char_start) for fact in section.facts)
    fact_end = max(int(fact.char_end) for fact in section.facts)
    if not 0 <= fact_start < fact_end <= len(section.wikitext):
        return None
    paragraph_starts = [0]
    paragraph_ends: list[int] = []
    for separator in re.finditer(r"\n[ \t]*\n", section.wikitext):
        paragraph_ends.append(separator.start())
        paragraph_starts.append(separator.end())
    paragraph_ends.append(len(section.wikitext))
    containing = [
        (start, end)
        for start, end in zip(paragraph_starts, paragraph_ends, strict=True)
        if start <= fact_start and fact_end <= end
    ]
    if len(containing) != 1:
        return None
    section_start, section_end = containing[0]
    while section_start < section_end and section.wikitext[section_start].isspace():
        section_start += 1
    while section_end > section_start and section.wikitext[section_end - 1].isspace():
        section_end -= 1
    if section_end - section_start > _WIKI_CANONICAL_FACT_SPAN_MAX_CHARS:
        return None
    parent_section_start = source_body.index(section.wikitext)
    source_start = parent_section_start + section_start
    source_end = parent_section_start + section_end
    source_span = source_body[source_start:source_end]
    if (
        not source_span
        or source_body.count(source_span) != 1
        or any(
            source_span.count(fact.evidence_quote) != 1
            or source_span[
                fact.char_start - section_start : fact.char_end - section_start
            ]
            != fact.evidence_quote
            for fact in section.facts
        )
    ):
        return None
    return _WikiCanonicalFactSpan(
        section_start=section_start,
        source_start=source_start,
        source_end=source_end,
        source_span=source_span,
    )


def _wiki_canonical_fact_span_event(
    *,
    workflow: Any,
    record: Any,
    section: Any,
    event_id: str,
    section_text_prefix: str,
    fact_parser_revision: str,
    answer_record_id: str,
    event_time: date,
) -> Event | None:
    source_body = (
        record.text
        if section.section_id == "wikidata_entity"
        else extract_wikipedia_wikitext(record.text)[2]
    )
    canonical_span = _wiki_canonical_fact_span(
        section=section,
        source_body=source_body,
    )
    if canonical_span is None:
        return None
    section_text = section_text_prefix + canonical_span.source_span
    fact_spans: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for fact in section.facts:
        fact_id = f"{event_id}:{fact.role}"
        span = fact.to_span(
            shift=len(section_text_prefix) - canonical_span.section_start
        )
        span["fact_id"] = fact_id
        fact_spans.append(span)
        facts.append(
            _grounded_fact(
                source_id=event_id,
                text=section_text,
                fact_id=fact_id,
                quote=fact.evidence_quote,
                char_start=int(span["char_start"]),
            )
        )
    section_sha256 = hashlib.sha256(canonical_span.source_span.encode()).hexdigest()
    source_body_sha256 = hashlib.sha256(source_body.encode()).hexdigest()
    source_span_sha256 = hashlib.sha256(canonical_span.source_span.encode()).hexdigest()
    provenance_id = "derived-sha256:" + _canonical_digest(
        {
            "operation": _WIKI_CANONICAL_FACT_SPAN,
            "section_id": section.section_id,
            "parent_sha256": section.parent_sha256,
            "source_body_sha256": source_body_sha256,
            "source_char_start": canonical_span.source_start,
            "source_char_end": canonical_span.source_end,
            "source_span_sha256": source_span_sha256,
            "section_sha256": section_sha256,
        }
    )
    return Event(
        id=event_id,
        type="wiki_source_section",
        time=event_time,
        params={
            "workflow_id": workflow.workflow_id,
            "record_id": answer_record_id,
            "section_record_id": record.record_id,
            "section_id": section.section_id,
            "source_body_sha256": source_body_sha256,
            "source_char_start": canonical_span.source_start,
            "source_char_end": canonical_span.source_end,
            "source_span_sha256": source_span_sha256,
            "text": section_text,
            "text_sha256": hashlib.sha256(section_text.encode()).hexdigest(),
            "source_sha256": record.source_sha256,
            "parent_source_sha256": section.parent_sha256,
            "section_sha256": section_sha256,
            "parent_provenance_id": record.provenance_id,
            "provenance_id": provenance_id,
            "provenance_operation": _WIKI_CANONICAL_FACT_SPAN,
            "source_binding_provenance": "verified_derived",
            "fact_parser_revision": fact_parser_revision,
            "minimum_tier": section.minimum_tier,
            "source_origin": "real_derived",
            "source_family": record.source_family,
            "source_url": record.source_url,
            "retrieval_url": record.retrieval_url,
            "fact_spans": fact_spans,
            "grounded_source": _grounded_source(
                source_id=event_id,
                text=section_text,
                facts=facts,
            ),
            "ground_values": [],
        },
        visibility=[event_id],
    )


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int, str]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end, text[start:end]


def _semantic_wiki_text(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>|\{\{[^{}]*\}\}", " ", text)
    return " ".join(re.sub(r"[\[\]{}|=*#]", " ", without_tags).split())


def _substantive_revision_change(
    *, change_kind: str, before_span: str, after_span: str
) -> bool:
    stripped_after = after_span.lstrip()
    before_semantic = _semantic_wiki_text(before_span)
    after_semantic = _semantic_wiki_text(after_span)
    combined = f"{before_semantic} {after_semantic}".strip()
    if (
        not after_semantic
        or stripped_after.startswith(("[[Category:", "{{", "<ref"))
        or len(before_span) > 512
        or len(after_span) > 512
        or len(_WIKI_WORD.findall(after_semantic)) < 5
        or _WIKI_MAINTENANCE_CHANGE.search(combined) is not None
    ):
        return False
    if change_kind == "insert":
        return True
    prefix = 0
    while (
        prefix < len(before_semantic)
        and prefix < len(after_semantic)
        and before_semantic[prefix] == after_semantic[prefix]
    ):
        prefix += 1
    old_end = len(before_semantic)
    new_end = len(after_semantic)
    while (
        old_end > prefix
        and new_end > prefix
        and before_semantic[old_end - 1] == after_semantic[new_end - 1]
    ):
        old_end -= 1
        new_end -= 1
    changed_core = before_semantic[prefix:old_end] + after_semantic[prefix:new_end]
    return any(character.isalnum() for character in changed_core)


def _wiki_revision_hunk(prior_text: str, later_text: str) -> _WikiRevisionHunk | None:
    """Select one bounded exact insert/replace hunk from adjacent source bodies."""
    _prior_title, _prior_entity, prior_body = extract_wikipedia_wikitext(prior_text)
    _later_title, _later_entity, later_body = extract_wikipedia_wikitext(later_text)
    prior_lines = prior_body.splitlines(keepends=True)
    later_lines = later_body.splitlines(keepends=True)
    prior_offsets = [0]
    later_offsets = [0]
    for line in prior_lines:
        prior_offsets.append(prior_offsets[-1] + len(line))
    for line in later_lines:
        later_offsets.append(later_offsets[-1] + len(line))
    matcher = SequenceMatcher(
        None,
        prior_lines,
        later_lines,
        autojunk=False,
    )
    candidates: list[_WikiRevisionHunk] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag not in {"insert", "replace"}:
            continue
        before_start, before_end, before_span = _trimmed_span(
            prior_body, prior_offsets[old_start], prior_offsets[old_end]
        )
        after_start, after_end, after_span = _trimmed_span(
            later_body, later_offsets[new_start], later_offsets[new_end]
        )
        change_kind = "insert" if tag == "insert" else "replace"
        insertion_offset = prior_offsets[old_start]
        if tag == "replace":
            shared_prefix = 0
            while (
                shared_prefix < len(before_span)
                and shared_prefix < len(after_span)
                and before_span[shared_prefix] == after_span[shared_prefix]
            ):
                shared_prefix += 1
            old_tail = len(before_span)
            new_tail = len(after_span)
            while (
                old_tail > shared_prefix
                and new_tail > shared_prefix
                and before_span[old_tail - 1] == after_span[new_tail - 1]
            ):
                old_tail -= 1
                new_tail -= 1
            if not before_span[shared_prefix:old_tail]:
                change_kind = "insert"
                insertion_offset = before_start + shared_prefix
                after_start, after_end, after_span = _trimmed_span(
                    later_body,
                    after_start + shared_prefix,
                    after_start + new_tail,
                )
        if not _substantive_revision_change(
            change_kind=change_kind,
            before_span=before_span,
            after_span=after_span,
        ):
            continue
        if change_kind == "insert":
            anchor_end = min(len(prior_body), insertion_offset)
            anchor_start = max(0, anchor_end - 96)
            if anchor_start == anchor_end:
                anchor_end = min(len(prior_body), 96)
            before_start, before_end, before_span = _trimmed_span(
                prior_body, anchor_start, anchor_end
            )
        if (
            not before_span
            or not after_span
            or prior_body.count(before_span) != 1
            or later_body.count(after_span) != 1
        ):
            continue
        candidates.append(
            _WikiRevisionHunk(
                change_kind=change_kind,
                before_start=before_start,
                before_end=before_end,
                before_span=before_span,
                after_start=after_start,
                after_end=after_end,
                after_span=after_span,
            )
        )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda value: (
            len(_WIKI_WORD.findall(_semantic_wiki_text(value.after_span))),
            value.after_span,
        ),
    )


def _wiki_revision_hunk_event(
    *,
    workflow: Any,
    record: Any,
    prefix: str,
    workflow_index: int,
    transition_tier: str,
    hunk_side: str,
    hunk: _WikiRevisionHunk,
    counterpart: Any,
    relation_id: str,
    answer_record_id: str,
) -> Event:
    _title, _entity_id, body = extract_wikipedia_wikitext(record.text)
    section_id = f"revision_{transition_tier}_{hunk_side}"
    event_id = f"{prefix}.wiki_section_{section_id}_{workflow_index}_{record.record_id}"
    start = hunk.before_start if hunk_side == "before" else hunk.after_start
    end = hunk.before_end if hunk_side == "before" else hunk.after_end
    source_span = hunk.before_span if hunk_side == "before" else hunk.after_span
    section_body = (
        f"transition {relation_id}\nside {hunk_side}\n"
        f"change {hunk.change_kind}\nsource span\n{source_span}"
    )
    section_text = WIKI_SECTION_VIEW_PREFIX + section_body
    section_sha256 = hashlib.sha256(section_body.encode()).hexdigest()
    source_body_sha256 = hashlib.sha256(body.encode()).hexdigest()
    source_span_sha256 = hashlib.sha256(source_span.encode()).hexdigest()
    provenance_id = "derived-sha256:" + _canonical_digest(
        {
            "operation": _WIKI_REVISION_HUNK,
            "section_id": section_id,
            "relation_id": relation_id,
            "change_kind": hunk.change_kind,
            "hunk_side": hunk_side,
            "source_record_id": record.record_id,
            "counterpart_record_id": counterpart.record_id,
            "source_sha256": record.source_sha256,
            "counterpart_source_sha256": counterpart.source_sha256,
            "source_body_sha256": source_body_sha256,
            "source_char_start": start,
            "source_char_end": end,
            "source_span_sha256": source_span_sha256,
            "section_sha256": section_sha256,
        }
    )
    facts: list[dict[str, Any]] = [
        _grounded_fact(
            source_id=event_id,
            text=section_text,
            fact_id=f"{event_id}:source_span",
            quote=source_span,
            char_start=section_text.index(source_span),
        )
    ]
    spans: list[dict[str, Any]] = []
    if hunk_side in {"before", "after"}:
        role = f"revision_{'new' if hunk_side == 'after' else 'old'}_{transition_tier}"
        answer_tag = (
            f"REVISION_BEFORE_{transition_tier.upper()}"
            if hunk.change_kind == "insert" and hunk_side == "before"
            else f"REVISION_INSERT_{transition_tier.upper()}"
            if hunk.change_kind == "insert"
            else f"REVISION_{hunk_side.upper()}_{transition_tier.upper()}"
        )
        fact_id = f"{event_id}:{role}"
        facts[0]["fact_id"] = fact_id
        spans.append(
            {
                "kind": "wiki_claim",
                "role": role,
                "evidence_quote": source_span,
                "char_start": section_text.index(source_span),
                "char_end": section_text.index(source_span) + len(source_span),
                "value": source_span,
                "parent_sha256": record.text_sha256,
                "answer_tag": answer_tag,
                "fact_id": fact_id,
            }
        )
    grounded_source = _grounded_source(
        source_id=event_id,
        text=section_text,
        facts=facts,
    )
    return Event(
        id=event_id,
        type="wiki_source_section",
        time=date.fromisoformat(record.occurred_at[:10]),
        params={
            "workflow_id": workflow.workflow_id,
            "record_id": answer_record_id,
            "section_record_id": record.record_id,
            "section_id": section_id,
            "transition_tier": transition_tier,
            "hunk_side": hunk_side,
            "relation_id": relation_id,
            "change_kind": hunk.change_kind,
            "counterpart_record_id": counterpart.record_id,
            "counterpart_source_sha256": counterpart.source_sha256,
            "source_body_sha256": source_body_sha256,
            "source_char_start": start,
            "source_char_end": end,
            "source_span_sha256": source_span_sha256,
            "text": section_text,
            "text_sha256": hashlib.sha256(section_text.encode()).hexdigest(),
            "source_sha256": record.source_sha256,
            "parent_source_sha256": record.text_sha256,
            "section_sha256": section_sha256,
            "parent_provenance_id": record.provenance_id,
            "provenance_id": provenance_id,
            "provenance_operation": _WIKI_REVISION_HUNK,
            "source_binding_provenance": "verified_derived",
            "fact_parser_revision": WIKI_FACT_PARSER_REVISION_V2,
            "minimum_tier": transition_tier,
            "source_origin": "real_derived",
            "source_family": record.source_family,
            "source_url": record.source_url,
            "retrieval_url": record.retrieval_url,
            "fact_spans": spans,
            "grounded_source": grounded_source,
            "ground_values": [source_span],
        },
        visibility=[event_id],
    )


def _wikipedia_source_workflow_events(
    workflow: Any, prefix: str, workflow_index: int
) -> list[Event]:
    wiki_records = [
        record for record in workflow.records if record.kind == "wikipedia_revision"
    ]
    entity_records = [
        record
        for record in workflow.records
        if record.kind == "wikidata_entity_revision"
    ]
    if len(wiki_records) not in {2, 3} or len(entity_records) != 1:
        return []
    ordered_wiki_records = sorted(
        wiki_records, key=lambda record: (record.occurred_at, record.record_id)
    )
    later = ordered_wiki_records[-1]
    entity = entity_records[0]
    wiki_hash = hashlib.sha256(later.text.encode()).hexdigest()
    entity_hash = hashlib.sha256(entity.text.encode()).hexdigest()
    if wiki_hash != later.text_sha256 or entity_hash != entity.text_sha256:
        return []
    try:
        program = parse_wiki_claim_program(
            wikipedia_text=later.text,
            entity_text=entity.text,
            wikipedia_parent_hash=wiki_hash,
            entity_parent_hash=entity_hash,
        )
    except ProvenanceError:
        return []
    semantic_tags = program.fact_parser_revision == WIKI_FACT_PARSER_REVISION_V2
    later_day = date.fromisoformat(later.occurred_at[:10])
    events: list[Event] = []
    section_ids: dict[str, str] = {}
    revision_roles: dict[str, list[str]] = {}
    revision_role_tags: dict[str, str] = {}
    revision_relation_ids_by_tier: dict[str, list[str]] = {"32k": [], "64k": []}
    revision_section_names_by_tier: dict[str, tuple[str, ...]] = {
        "16k": (),
        "32k": (),
        "64k": (),
    }
    revision_relation_endpoint_ids: dict[str, dict[str, str]] = {}
    if len(ordered_wiki_records) == 3:
        prior, middle, latest = ordered_wiki_records
        expected_revision_edges = {
            (middle.record_id, prior.record_id),
            (latest.record_id, middle.record_id),
        }
        actual_revision_edges = {
            (relation.source_record_id, relation.target_record_id)
            for relation in workflow.relations
            if relation.kind == "revision_of" and len(relation.evidence) == 1
        }
        if actual_revision_edges != expected_revision_edges:
            return []
        try:
            transition_hunks = {
                "32k": _wiki_revision_hunk(prior.text, middle.text),
                "64k": _wiki_revision_hunk(middle.text, latest.text),
            }
        except ProvenanceError:
            return []
        if any(hunk is None for hunk in transition_hunks.values()):
            return []
        revision_relations_by_edge = {
            (relation.source_record_id, relation.target_record_id): relation
            for relation in workflow.relations
            if relation.kind == "revision_of"
        }
        for tier, before, after in (
            ("32k", prior, middle),
            ("64k", middle, latest),
        ):
            relation = revision_relations_by_edge[(after.record_id, before.record_id)]
            hunk = transition_hunks[tier]
            assert hunk is not None
            endpoints: dict[str, str] = {}
            for side, record, counterpart in (
                ("before", before, after),
                ("after", after, before),
            ):
                hunk_event = _wiki_revision_hunk_event(
                    workflow=workflow,
                    record=record,
                    prefix=prefix,
                    workflow_index=workflow_index,
                    transition_tier=tier,
                    hunk_side=side,
                    hunk=hunk,
                    counterpart=counterpart,
                    relation_id=relation.relation_id,
                    answer_record_id=later.record_id,
                )
                events.append(hunk_event)
                section_name = str(hunk_event.params["section_id"])
                section_ids[section_name] = hunk_event.id
                endpoints[record.record_id] = hunk_event.id
                revision_roles[section_name] = [
                    str(span["role"]) for span in hunk_event.params["fact_spans"]
                ]
                revision_role_tags.update(
                    {
                        str(span["role"]): str(span["answer_tag"])
                        for span in hunk_event.params["fact_spans"]
                    }
                )
            revision_relation_endpoint_ids[relation.relation_id] = endpoints
        revision_section_names_by_tier = {
            "16k": (),
            "32k": ("revision_32k_before", "revision_32k_after"),
            "64k": (
                "revision_32k_before",
                "revision_32k_after",
                "revision_64k_before",
                "revision_64k_after",
            ),
        }
    early_section_names = [
        section.section_id
        for section in program.sections
        if section.section_id.startswith("early_")
    ]
    early_offsets = {name: index for index, name in enumerate(early_section_names)}
    for section in program.sections:
        record = entity if section.section_id == "wikidata_entity" else later
        prefix_text = (
            WIKI_ENTITY_VIEW_PREFIX
            if section.section_id == "wikidata_entity"
            else WIKI_SECTION_VIEW_PREFIX
        )
        section_text = prefix_text + section.wikitext
        event_id = (
            f"{prefix}.wiki_section_{section.section_id}_{workflow_index}_"
            f"{later.record_id}"
        )
        section_offset = _WIKI_SECTION_OFFSETS.get(
            section.section_id, early_offsets.get(section.section_id)
        )
        if section_offset is None:
            return []
        if len(ordered_wiki_records) == 3:
            if not section.facts:
                continue
            fact_span_event = _wiki_canonical_fact_span_event(
                workflow=workflow,
                record=record,
                section=section,
                event_id=event_id,
                section_text_prefix=prefix_text,
                fact_parser_revision=program.fact_parser_revision,
                answer_record_id=later.record_id,
                event_time=later_day + timedelta(days=section_offset),
            )
            if fact_span_event is None:
                return []
            section_ids[section.section_id] = event_id
            events.append(fact_span_event)
            continue
        section_ids[section.section_id] = event_id
        grounded_source, fact_spans = _wiki_grounded_source(
            event_id=event_id,
            section=section,
            section_text=section_text,
            prefix_text=prefix_text,
        )
        events.append(
            Event(
                id=event_id,
                type="wiki_source_section",
                time=later_day + timedelta(days=section_offset),
                params={
                    "workflow_id": workflow.workflow_id,
                    "record_id": later.record_id,
                    "section_record_id": record.record_id,
                    "section_id": section.section_id,
                    "text": section_text,
                    "text_sha256": hashlib.sha256(section_text.encode()).hexdigest(),
                    "source_sha256": record.source_sha256,
                    "parent_source_sha256": section.parent_sha256,
                    "section_sha256": section.section_sha256,
                    "parent_provenance_id": record.provenance_id,
                    "provenance_id": section.provenance_id,
                    "provenance_operation": WIKI_SECTION_REVISION,
                    "source_binding_provenance": "verified_derived",
                    "fact_parser_revision": program.fact_parser_revision,
                    "minimum_tier": section.minimum_tier,
                    "source_origin": "real_derived",
                    "source_family": record.source_family,
                    "source_url": record.source_url,
                    "retrieval_url": record.retrieval_url,
                    "fact_spans": fact_spans,
                    "grounded_source": grounded_source,
                    "ground_values": [section.ground_value],
                },
                visibility=[event_id],
            )
        )
    record_event_ids = {
        later.record_id: section_ids.get("early_work", "")
        or section_ids.get("early_birth", ""),
        entity.record_id: section_ids.get("wikidata_entity", ""),
    }
    relation_event_ids: list[str] = []
    relation_event_ids_by_relation_id: dict[str, str] = {}
    relation_ids: list[str] = []
    records_by_id = {record.record_id: record for record in workflow.records}
    for relation_index, relation in enumerate(workflow.relations):
        relation_endpoints = revision_relation_endpoint_ids.get(
            relation.relation_id, {}
        )
        source_event_id = relation_endpoints.get(
            relation.source_record_id,
            record_event_ids.get(relation.source_record_id, ""),
        )
        target_event_id = relation_endpoints.get(
            relation.target_record_id,
            record_event_ids.get(relation.target_record_id, ""),
        )
        if (
            relation.kind
            not in {"revision_of", "page_describes_entity", "entity_resolves_page"}
            or not source_event_id
            or not target_event_id
            or source_event_id == target_event_id
            or len(relation.evidence) != 1
        ):
            continue
        source = records_by_id[relation.source_record_id]
        target = records_by_id[relation.target_record_id]
        evidence = relation.evidence[0]
        relation_event_id = (
            f"{prefix}.wiki_source_relation_{workflow_index}_{relation_index}"
        )
        provenance_id = "derived-sha256:" + _canonical_digest(
            {
                "workflow_id": workflow.workflow_id,
                "relation_id": relation.relation_id,
                "kind": relation.kind,
                "source_provenance_id": source.provenance_id,
                "target_provenance_id": target.provenance_id,
                "evidence_quote": evidence.evidence_quote,
                "evidence_char_start": evidence.char_start,
            }
        )
        events.append(
            Event(
                id=relation_event_id,
                type="wiki_source_relation",
                time=max(
                    date.fromisoformat(source.occurred_at[:10]),
                    date.fromisoformat(target.occurred_at[:10]),
                )
                + timedelta(days=9),
                params={
                    "workflow_id": workflow.workflow_id,
                    "record_id": later.record_id,
                    "relation_id": relation.relation_id,
                    "relation_kind": relation.kind,
                    "source_record_id": relation.source_record_id,
                    "target_record_id": relation.target_record_id,
                    "source_record_event_id": source_event_id,
                    "target_record_event_id": target_event_id,
                    "source_url": source.source_url,
                    "target_source_url": target.source_url,
                    "source_family": source.source_family,
                    "evidence_record_id": evidence.record_id,
                    "evidence_quote": evidence.evidence_quote,
                    "evidence_char_start": evidence.char_start,
                    "evidence_char_end": evidence.char_end,
                    "source_sha256": source.source_sha256,
                    "target_sha256": target.source_sha256,
                    "source_provenance_id": source.provenance_id,
                    "target_provenance_id": target.provenance_id,
                    "provenance_id": provenance_id,
                    "source_binding_provenance": "authentic_source_api",
                    "relation_provenance": "authentic_source_api",
                    "source_origin": "real_derived",
                    "ground_values": [relation.kind, evidence.evidence_quote],
                },
                visibility=[relation_event_id],
                causal_inputs=[source_event_id, target_event_id],
                required_inputs=[source_event_id, target_event_id],
                relation_kinds={
                    source_event_id: "source_endpoint",
                    target_event_id: "target_endpoint",
                },
            )
        )
        relation_event_ids.append(relation_event_id)
        relation_event_ids_by_relation_id[relation.relation_id] = relation_event_id
        relation_ids.append(relation.relation_id)
        if relation.kind == "revision_of":
            if relation.source_record_id == ordered_wiki_records[1].record_id:
                revision_relation_ids_by_tier["32k"].append(relation.relation_id)
            revision_relation_ids_by_tier["64k"].append(relation.relation_id)
    prior_id = ""
    prior_key = ""
    compute_keys: dict[str, str] = {}
    used_sections: set[str] = set()
    early_sections = tuple(early_section_names) or ("early_work",)
    sections_by_id = {section.section_id: section for section in program.sections}
    roles_by_section = {
        section_id: [fact.role for fact in sections_by_id[section_id].facts]
        for section_id in sections_by_id
    }
    roles_by_section.update(revision_roles)
    role_tags = (
        {
            fact.role: fact.answer_tag
            for section in program.sections
            for fact in section.facts
        }
        if semantic_tags
        else dict(_WIKI_LEGACY_ROLE_TAGS)
    )
    tagged_answers = semantic_tags or bool(revision_roles)
    role_tags.update(revision_role_tags)
    tier_rank = {"16k": 0, "32k": 1, "64k": 2}
    early_sections_by_tier = {
        tier: tuple(
            section_id
            for section_id in early_sections
            if tier_rank[sections_by_id[section_id].minimum_tier] <= rank
        )
        for tier, rank in tier_rank.items()
    }
    early_roles_by_minimum_tier = {
        tier: [
            role
            for section_id in early_sections
            if sections_by_id[section_id].minimum_tier == tier
            for role in roles_by_section[section_id]
        ]
        for tier in tier_rank
    }
    middle_roles = roles_by_section["commemoration"]
    entity_roles = roles_by_section["wikidata_entity"]
    late_roles = roles_by_section["popular_culture"]
    roles_16k = list(early_roles_by_minimum_tier["16k"])
    roles_32k = [
        *roles_16k,
        *early_roles_by_minimum_tier["32k"],
        *middle_roles,
        *entity_roles,
        *revision_roles.get("revision_32k_before", []),
        *revision_roles.get("revision_32k_after", []),
    ]
    tier_roles = {
        "16k": roles_16k,
        "32k": roles_32k,
        "64k": [
            *roles_32k,
            *early_roles_by_minimum_tier["64k"],
            *late_roles,
            *revision_roles.get("revision_64k_before", []),
            *revision_roles.get("revision_64k_after", []),
        ],
    }
    append_roles = {
        "16k": roles_16k,
        "32k": [
            *early_roles_by_minimum_tier["32k"],
            *middle_roles,
            *entity_roles,
            *revision_roles.get("revision_32k_before", []),
            *revision_roles.get("revision_32k_after", []),
        ],
        "64k": [
            *early_roles_by_minimum_tier["64k"],
            *late_roles,
            *revision_roles.get("revision_64k_before", []),
            *revision_roles.get("revision_64k_after", []),
        ],
    }
    for control_tier, rungs, default_sections in _WIKI_CLAIM_TIERS:
        needed_sections = (
            *early_sections_by_tier[control_tier],
            *default_sections[1:],
            *revision_section_names_by_tier[control_tier],
        )
        new_sections = [name for name in needed_sections if name not in used_sections]
        for rung_index, (offset, compose, stage) in enumerate(rungs):
            event_id = (
                f"{prefix}.wiki_claim_{control_tier}_{compose}_{rung_index}_"
                f"{workflow_index}_{later.record_id}"
            )
            if compose == "copy":
                parents = [prior_id] if prior_id else []
                answer_key = (
                    f"wiki_claim_reconstruction:{later.record_id}:"
                    f"{control_tier}_rung{rung_index}"
                )
                required_roles: list[str] = []
            else:
                # Continue the published chain. Re-attaching ancestor sections
                # as parallel parents shortens shortest-path proof depth.
                parents = [prior_id] if prior_id else []
                parents.extend(section_ids[name] for name in new_sections)
                required_relation_ids = (
                    [
                        relation_id
                        for relation_id in relation_ids
                        if relation_id not in revision_relation_ids_by_tier["64k"]
                    ]
                    + revision_relation_ids_by_tier[control_tier]
                    if control_tier in {"32k", "64k"}
                    else []
                )
                if required_relation_ids:
                    parents.extend(
                        relation_event_ids_by_relation_id[relation_id]
                        for relation_id in required_relation_ids
                    )
                answer_key = (
                    f"wiki_claim_reconstruction:{later.record_id}:{control_tier}"
                )
                required_roles = list(tier_roles[control_tier])
            relation_kinds = {
                parent: (
                    "extends"
                    if parent == prior_id
                    else "reads_relation"
                    if parent in relation_event_ids
                    else "reads_section"
                )
                for parent in parents
            }
            events.append(
                Event(
                    id=event_id,
                    type="wiki_claim_answer",
                    time=later_day
                    + timedelta(
                        days=(
                            max(offset, len(early_sections))
                            if control_tier == "16k"
                            else offset
                        )
                    ),
                    params={
                        "workflow_id": workflow.workflow_id,
                        "record_id": later.record_id,
                        "control_tier": control_tier,
                        "control_stage": stage,
                        "compose": compose,
                        "answer_key": answer_key,
                        "prerequisite_answer_key": prior_key
                        if compose == "copy"
                        else (
                            compute_keys["16k"]
                            if control_tier == "32k"
                            else compute_keys.get("32k", "")
                            if control_tier == "64k"
                            else ""
                        ),
                        "required_roles": required_roles,
                        **(
                            {
                                "append_roles": list(append_roles[control_tier]),
                                "role_tags": {
                                    role: role_tags[role] for role in required_roles
                                },
                            }
                            if tagged_answers
                            else {}
                        ),
                        "response_schema": "||".join(
                            f"{role_tags[role]}:<value>" for role in required_roles
                        ),
                        "required_relation_ids": required_relation_ids,
                        "section_event_ids": [
                            section_ids[name] for name in needed_sections
                        ],
                        "ground_values": ["wiki-reconstructed", stage],
                        "relation_provenance": "synthetic_executable",
                    },
                    visibility=[event_id],
                    causal_inputs=parents,
                    required_inputs=list(parents),
                    relation_kinds=relation_kinds,
                )
            )
            prior_id = event_id
            prior_key = answer_key
            if compose == "compute":
                compute_keys[control_tier] = answer_key
        used_sections.update(needed_sections)
    return events


_ARXIV_16K_VIEWS: dict[str, frozenset[str] | None] = {
    "v1": frozenset({"example.tex", "main.tex"}),
    "v2": frozenset(
        {
            "acknowledgements.tex",
            "experiments_details.tex",
            "main.tex",
        }
    ),
    "v3": frozenset({"abstract.tex"}),
}
_ARXIV_32K_FILES = frozenset(
    {
        "abstract.tex",
        "acknowledgements.tex",
        "example.tex",
        "experiments_sup.tex",
        "expressions.tex",
        "feature.tex",
        "introduction.tex",
        "main.tex",
    }
)
_ARXIV_TERMINAL_FILES = frozenset({"abstract.tex", "acknowledgements.tex"})
_ARXIV_TIER_OFFSETS = {"16k": 0, "32k": 365, "64k": 730}
_SPARKS_WORK_ID = "arxiv:2303.12712"
_SPARKS_ACKNOWLEDGMENTS = "\\paragraph{Acknowledgments.}"
_SPARKS_SECTION_CLAIMS = {
    "contents/1_intro.tex": (
        "agi_scope",
        ("We use AGI to refer to systems that demonstrate broad capabilities of "
        "intelligence, including reasoning, planning, and the ability to learn "
        "from experience, and with these capabilities at or above human-level."),
    ),
    "contents/2_see.tex": (
        "cross_domain_composition",
        ("A key measure of intelligence is the ability to synthesize information "
        "from different domains or modalities and the capacity to apply knowledge "
        "and skills across different contexts or disciplines."),
    ),
    "contents/4_math.tex": (
        "mathematical_research_limit",
        ("As it seems, however, \\DV \\ is still quite far from the level of experts, "
        "and does not have the capacity required to conduct mathematical research."),
    ),
    "contents/5.2_interact_environment.tex": (
        "embodied_text_interface",
        ("While \\DV\\ is obviously not embodied,  we explore whether it can engage "
        "in embodied interaction by using natural language as a text interface to "
        "various simulated or real-world environments."),
    ),
    "contents/roleplaying.tex": (
        "theory_of_mind",
        ("Theory of mind is the ability to attribute mental states such as beliefs, "
        "emotions, desires, intentions, and knowledge to oneself and others, and "
        "to understand how they affect behavior and communication~"
        "\\cite{wellman1992child}."),
    ),
    "contents/7.1_pii.tex": (
        "pii_corpus_size",
        "We are able to obtain a total of 6764 sentences.",
    ),
    "contents/7.2_misconceptions.tex": (
        "similarity_metric_limit",
        ("This raises an important shortcoming of the current metrics: they fail "
        "to capture \\textit{semantic} similarities within statements, and rely "
        "primarily on word or sentence-level similarity metrics which capture "
        "\\textit{syntax}."),
    ),
    "contents/reasoninglimitations.tex": (
        "autoregressive_planning_limit",
        ("These examples illustrate some of the limitations of the next-word "
        "prediction paradigm, which manifest as the model's lack of planning, "
        "working memory, ability to backtrack, and reasoning abilities."),
    ),
    "contents/societal.tex": (
        "personalized_manipulation_risk",
        ("Moreover, the message can be customized and personalized per individual, "
        "showing the possibility of a personalized, scalable attack vector."),
    ),
    "contents/conclusion.tex": (
        "mechanism_status",
        ("Overall, elucidating the nature and mechanisms of AI systems such as "
        "{\\DV} is a formidable challenge that has suddenly become important and "
        "urgent."),
    ),
}
_SPARKS_SECTION_TIERS = {
    "16k": (
        "contents/roleplaying.tex",
        "contents/7.2_misconceptions.tex",
        "contents/reasoninglimitations.tex",
    ),
    "32k": (
        "contents/2_see.tex",
        "contents/4_math.tex",
        "contents/roleplaying.tex",
        "contents/7.2_misconceptions.tex",
        "contents/reasoninglimitations.tex",
    ),
    "64k": (
        "contents/1_intro.tex",
        "contents/2_see.tex",
        "contents/4_math.tex",
        "contents/5.2_interact_environment.tex",
        "contents/roleplaying.tex",
        "contents/7.1_pii.tex",
        "contents/7.2_misconceptions.tex",
        "contents/reasoninglimitations.tex",
        "contents/societal.tex",
        "contents/conclusion.tex",
    ),
}
_ARXIV_BENCHMARK_TRACE_VIEWS: dict[
    str, tuple[tuple[str, str, frozenset[str], bool, bool, bool], ...]
] = {
    "16k": (
        (
            "v1",
            "abstract",
            frozenset(
                {
                    "background.tex",
                    "introduction.tex",
                    "ms.tex",
                    "why_self_attention.tex",
                }
            ),
            True,
            False,
            False,
        ),
        (
            "v1",
            "detail",
            frozenset({"results.tex", "visualizations.tex"}),
            False,
            True,
            False,
        ),
        (
            "v1",
            "training",
            frozenset({"training.tex"}),
            False,
            False,
            True,
        ),
    ),
    "32k": tuple(
        channel
        for revision_id in ("v1", "v2")
        for channel in (
            (
                revision_id,
                "abstract",
                frozenset(
                    {
                        "background.tex",
                        "introduction.tex",
                        "ms.tex",
                        "why_self_attention.tex",
                    }
                    | ({"training.tex"} if revision_id != "v1" else set())
                ),
                True,
                False,
                revision_id != "v1",
            ),
            (
                revision_id,
                "detail",
                frozenset(
                    {
                        "parameter_attention.tex",
                        "results.tex",
                    }
                    if revision_id == "v2"
                    else {"results.tex"}
                ),
                False,
                True,
                False,
            ),
        )
    )
    + (("v1", "training", frozenset({"training.tex"}), False, False, True),),
    "64k": tuple(
        channel
        for revision_id in ("v1", "v2", "v3")
        for channel in (
            (
                revision_id,
                "abstract",
                frozenset(
                    {
                        "background.tex",
                        "introduction.tex",
                        "model_architecture.tex",
                        "ms.tex",
                        "why_self_attention.tex",
                    }
                    | ({"training.tex"} if revision_id != "v1" else set())
                ),
                True,
                False,
                revision_id != "v1",
            ),
            (
                revision_id,
                "detail",
                frozenset(
                    {
                        "parameter_attention.tex",
                        "results.tex",
                        "sqrt_d_trick.tex",
                        "visualizations.tex",
                    }
                    if revision_id != "v1"
                    else {"results.tex", "visualizations.tex"}
                ),
                False,
                True,
                False,
            ),
        )
    )
    + (("v1", "training", frozenset({"training.tex"}), False, False, True),),
}


def _paper_benchmark_trace_records(workflow: Any) -> dict[str, Any] | None:
    records = {record.attribute("revision_id"): record for record in workflow.records}
    if set(records) != {"v1", "v2", "v3"} or len(workflow.relations) != 2:
        return None
    expected_pairs = {
        (records["v2"].record_id, records["v1"].record_id),
        (records["v3"].record_id, records["v2"].record_id),
    }
    observed_pairs = {
        (relation.source_record_id, relation.target_record_id)
        for relation in workflow.relations
        if relation.kind == "revision_of" and len(relation.evidence) == 1
    }
    if observed_pairs != expected_pairs:
        return None
    parsed: dict[str, dict[str, Any]] = {}
    try:
        for revision_id, record in records.items():
            body, _provenance, _excluded, spans = _semantic_arxiv_body(
                record,
                included_basenames=frozenset({"ms.tex", "results.tex"}),
                bind_file_spans=True,
            )
            if {str(span["basename"]) for span in spans} != {
                "ms.tex",
                "results.tex",
            }:
                return None
            observation = parse_arxiv_benchmark_observation(body, include_detail=True)
            if observation is None:
                return None
            parsed[revision_id] = observation
        for tier_views in _ARXIV_BENCHMARK_TRACE_VIEWS.values():
            for (
                revision_id,
                _channel,
                included_basenames,
                _abstract,
                _detail,
                require_training,
            ) in tier_views:
                body, _provenance, _excluded, _spans = _semantic_arxiv_body(
                    records[revision_id],
                    included_basenames=included_basenames,
                    bind_file_spans=True,
                )
                if require_training and parse_arxiv_training_hardware(body) is None:
                    return None
    except ValueError:
        return None

    def values(revision_id: str) -> tuple[str, str, str]:
        observation = parsed[revision_id]
        return (
            str(observation["benchmark"]["value"]),
            str(observation["score"]["value"]),
            str(observation["days"]["value"]),
        )

    abstracts = {revision_id: values(revision_id) for revision_id in records}
    detail_scores = {
        revision_id: str(parsed[revision_id]["detail_score"]["value"])
        for revision_id in records
    }
    if not (
        len({observation[0] for observation in abstracts.values()}) == 1
        and abstracts["v1"][1:] != abstracts["v2"][1:]
        and abstracts["v3"][1:] == abstracts["v1"][1:]
        and detail_scores["v1"] != detail_scores["v2"]
        and detail_scores["v3"] == detail_scores["v2"]
    ):
        return None
    return records


def _paper_multiband_records(workflow: Any) -> dict[str, Any] | None:
    records = {record.attribute("revision_id"): record for record in workflow.records}
    if set(records) != {"v1", "v2", "v3"} or len(workflow.relations) != 2:
        return None
    relation_pairs = {
        (
            records["v2"].record_id,
            records["v1"].record_id,
        ),
        (
            records["v3"].record_id,
            records["v2"].record_id,
        ),
    }
    if {
        (relation.source_record_id, relation.target_record_id)
        for relation in workflow.relations
        if relation.kind == "revision_of" and len(relation.evidence) == 1
    } != relation_pairs:
        return None
    delta_facts = [
        fact for fact in records["v2"].facts if fact.field == "revision_added_text"
    ]
    if len(delta_facts) != 1:
        return None
    try:
        v1_16k, _, _, v1_spans = _semantic_arxiv_body(
            records["v1"],
            included_basenames=_ARXIV_16K_VIEWS["v1"],
            bind_file_spans=True,
        )
        v2_16k, _, _, v2_spans = _semantic_arxiv_body(
            records["v2"],
            included_basenames=_ARXIV_16K_VIEWS["v2"],
            bind_file_spans=True,
        )
        required_new_include = "parts/acknowledgements"
        v1_files = {
            span["basename"]: v1_16k[span["char_start"] : span["char_end"]]
            for span in v1_spans
        }
        v2_files = {
            span["basename"]: v2_16k[span["char_start"] : span["char_end"]]
            for span in v2_spans
        }
        delta_value = delta_facts[0].value
        if (
            "main.tex" not in v1_files
            or "main.tex" not in v2_files
            or "acknowledgements.tex" not in v2_files
            or required_new_include in v1_files["main.tex"]
            or required_new_include not in v2_files["main.tex"]
            or v2_files["acknowledgements.tex"].count(delta_value) != 1
        ):
            return None
        for revision_id, record in records.items():
            _semantic_arxiv_body(
                record,
                included_basenames=_ARXIV_16K_VIEWS[revision_id],
                bind_file_spans=True,
            )
            _semantic_arxiv_body(
                record,
                included_basenames=_ARXIV_32K_FILES,
                bind_file_spans=True,
            )
    except ValueError:
        return None
    return records


def _multiband_arxiv_revision_event(
    *,
    workflow: Any,
    record: Any,
    prefix: str,
    workflow_index: int,
    record_index: int,
    tier: str,
    included_basenames: frozenset[str] | None,
    view_channel: str = "",
    section_basename: str = "",
    section_names: frozenset[str] = frozenset(),
    source_path: str = "",
    strip_after: str = "",
    section_claim_id: str = "",
    section_claim_quote: str = "",
) -> tuple[Event, str, str, dict[str, str]]:
    source_compile_receipts: list[dict[str, Any]] = []
    if source_path:
        (
            body,
            provenance_id,
            excluded_paths,
            source_file_spans,
            source_compile_receipts,
        ) = (
            _semantic_arxiv_file_body(
                record,
                source_path=source_path,
                strip_after=strip_after,
                preserved_quote=section_claim_quote,
            )
        )
    elif section_names:
        body, provenance_id, excluded_paths, source_file_spans = (
            _semantic_arxiv_section_body(
                record,
                basename=section_basename,
                section_names=section_names,
            )
        )
    else:
        body, provenance_id, excluded_paths, source_file_spans = _semantic_arxiv_body(
            record,
            included_basenames=included_basenames,
            bind_file_spans=True,
        )
    channel_suffix = f"_{view_channel}" if view_channel else ""
    event_id = (
        f"{prefix}.arxiv_revision_{tier}_{workflow_index}_{record_index}"
        f"{channel_suffix}"
    )
    revision_id = record.attribute("revision_id")
    revision_start = body.index(revision_id)
    revision_fact_id = f"{event_id}:revision_id"
    submitted_at = record.occurred_at
    submitted_start = body.index(submitted_at)
    grounded_facts = [
        _grounded_fact(
            source_id=event_id,
            text=body,
            fact_id=revision_fact_id,
            quote=revision_id,
            char_start=revision_start,
        ),
        _grounded_fact(
            source_id=event_id,
            text=body,
            fact_id=f"{event_id}:submitted_at",
            quote=submitted_at,
            char_start=submitted_start,
        ),
    ]
    if section_claim_id or section_claim_quote:
        if (
            not section_claim_id
            or not section_claim_quote
            or body.count(section_claim_quote) != 1
        ):
            raise ValueError("arXiv section claim is not uniquely grounded")
        grounded_facts.append(
            _grounded_fact(
                source_id=event_id,
                text=body,
                fact_id=f"{event_id}:section_claim:{section_claim_id}",
                quote=section_claim_quote,
                char_start=body.index(section_claim_quote),
            )
        )
    source_fact_ids: dict[str, str] = {}
    for fact_index, fact in enumerate(record.facts):
        if body.count(fact.value) != 1:
            continue
        fact_id = f"{event_id}:source_fact_{fact_index}"
        source_fact_ids[fact.fact_id] = fact_id
        grounded_facts.append(
            _grounded_fact(
                source_id=event_id,
                text=body,
                fact_id=fact_id,
                quote=fact.value,
                char_start=body.index(fact.value),
            )
        )
    text_sha256 = hashlib.sha256(body.encode()).hexdigest()
    source_envelope = _arxiv_source_envelope(
        workflow=workflow,
        record=record,
        text_sha256=text_sha256,
        provenance_id=provenance_id,
        provenance_operation="arxiv_semantic_latex_body_v2",
        source_file_spans=source_file_spans,
        source_compile_receipts=source_compile_receipts,
        source_view_basenames=sorted(
            str(span["basename"]) for span in source_file_spans
        ),
    )
    event = Event(
        id=event_id,
        type="arxiv_revision",
        time=date.fromisoformat(record.occurred_at[:10])
        + timedelta(days=_ARXIV_TIER_OFFSETS[tier]),
        params={
            "workflow_id": workflow.workflow_id,
            "record_id": record.record_id,
            "revision_id": revision_id,
            "occurred_at": record.occurred_at,
            "source_view_tier": tier,
            **({"source_view_channel": view_channel} if view_channel else {}),
            "text": body,
            "text_sha256": text_sha256,
            "source_sha256": record.source_sha256,
            "parent_provenance_id": record.provenance_id,
            "provenance_id": provenance_id,
            "provenance_operation": "arxiv_semantic_latex_body_v2",
            "source_binding_provenance": "verified_derived",
            "canonical_source_envelope_sha256": _canonical_digest(source_envelope),
            "excluded_paths": excluded_paths,
            "source_file_spans": source_file_spans,
            **(
                {"source_compile_receipts": source_compile_receipts}
                if source_compile_receipts
                else {}
            ),
            "source_view_basenames": sorted(
                str(span["basename"]) for span in source_file_spans
            ),
            **(
                {
                    "section_claim_id": section_claim_id,
                    "section_claim_quote": section_claim_quote,
                    "section_source_path": source_path,
                }
                if section_claim_id
                else {}
            ),
            "source_origin": "real_derived",
            "source_family": record.source_family,
            "source_url": record.source_url,
            "retrieval_url": record.retrieval_url,
            "ground_values": [
                record.occurred_at[:10],
                *(fact.value for fact in record.facts if fact.value in body),
            ],
            "grounded_source": _grounded_source(
                source_id=event_id, text=body, facts=grounded_facts
            ),
        },
        visibility=[event_id],
    )
    return event, body, revision_fact_id, source_fact_ids


def _multiband_arxiv_context_event(
    *,
    workflow: Any,
    record: Any,
    prefix: str,
    workflow_index: int,
    record_index: int,
    tier: str,
    parent_channel: str,
    included_basenames: frozenset[str],
) -> Event:
    event, _body, _revision_fact_id, _source_fact_ids = _multiband_arxiv_revision_event(
        workflow=workflow,
        record=record,
        prefix=prefix,
        workflow_index=workflow_index,
        record_index=record_index,
        tier=tier,
        included_basenames=included_basenames,
        view_channel=f"context_{parent_channel}",
    )
    event.type = "arxiv_revision_context"
    basename = next(iter(included_basenames))
    [source_span] = event.params["source_file_spans"]
    source_text = event.params["text"][
        int(source_span["char_start"]) : int(source_span["char_end"])
    ]
    substantive = next(
        (line.strip() for line in source_text.splitlines() if line.strip()),
        source_text[:80],
    )
    event.params["source_context_basename"] = basename
    event.params["ground_values"] = [
        event.params["occurred_at"][:10],
        substantive[:160],
    ]
    return event


def _multiband_arxiv_relation_event(
    *,
    workflow: Any,
    records: dict[str, Any],
    relation: Any,
    relation_index: int,
    workflow_index: int,
    tier: str,
    source_event: Event,
    target_event: Event,
    source_body: str,
    source_revision_fact_id: str,
    target_revision_fact_id: str,
    source_fact_ids: dict[str, str],
    prior_relation_id: str = "",
) -> Event:
    source = records[str(source_event.params["revision_id"])]
    target = records[str(target_event.params["revision_id"])]
    selected_facts = [
        fact for fact in source.facts if fact.field == "revision_added_text"
    ]
    delta_fact = selected_facts[0] if len(selected_facts) == 1 else None
    grounded_fact_id = (
        source_fact_ids.get(delta_fact.fact_id) if delta_fact is not None else None
    )
    relation_mode = "semantic_delta" if grounded_fact_id else "lineage_only"
    if relation_mode == "semantic_delta":
        assert delta_fact is not None and grounded_fact_id is not None
        fact_start = source_body.index(delta_fact.value)
        evidence_fact_ids = [
            source_revision_fact_id,
            target_revision_fact_id,
            grounded_fact_id,
        ]
    else:
        fact_start = -1
        evidence_fact_ids = [source_revision_fact_id, target_revision_fact_id]
    event_id = (
        f"{source_event.id.rsplit('.arxiv_revision_', 1)[0]}."
        f"arxiv_revision_relation_{tier}_{workflow_index}_{relation_index}"
    )
    workflow_key = hashlib.sha256(workflow.workflow_id.encode()).hexdigest()[:12]
    grounded_relation = {
        "relation_id": relation.relation_id,
        "relation_type": relation.kind,
        "source_id": source_event.id,
        "target_id": target_event.id,
        "claimed_provenance_class": "authentic_source_api",
        "evidence_fact_ids": evidence_fact_ids,
        "proof_mode": "structural_closure_only",
    }
    evidence = relation.evidence[0]
    causal_inputs = [target_event.id, source_event.id]
    relation_kinds = {
        target_event.id: "revision_of",
        source_event.id: "derived_from",
    }
    if prior_relation_id:
        causal_inputs.append(prior_relation_id)
        relation_kinds[prior_relation_id] = "extends"
    return Event(
        id=event_id,
        type="arxiv_revision_relation",
        time=max(source_event.time, target_event.time),
        params={
            "workflow_id": workflow.workflow_id,
            "work_id": source.attribute("work_id"),
            "source_view_tier": tier,
            "relation_mode": relation_mode,
            "candidate_key": (f"real_revision_delta_candidate:{workflow_key}:{tier}"),
            "relation_id": relation.relation_id,
            "relation_kind": relation.kind,
            "source_record_id": source.record_id,
            "target_record_id": target.record_id,
            "source_revision_id": source.attribute("revision_id"),
            "target_revision_id": target.attribute("revision_id"),
            "source_record_event_id": source_event.id,
            "target_record_event_id": target_event.id,
            "source_url": source.source_url,
            "target_source_url": target.source_url,
            "source_family": source.source_family,
            "evidence_quote": evidence.evidence_quote,
            "evidence_char_start": evidence.char_start,
            "fact_id": delta_fact.fact_id if delta_fact is not None else "",
            "grounded_fact_id": grounded_fact_id or "",
            "fact_char_start": fact_start,
            "fact_char_end": (
                fact_start + len(delta_fact.value) if delta_fact is not None else -1
            ),
            "fact_value_offset": 0,
            "fact_value_length": len(delta_fact.value) if delta_fact is not None else 0,
            "grounded_relation": grounded_relation,
            "relation_provenance": "authentic_source_api",
            "required_prior_relation_event_id": prior_relation_id,
        },
        visibility=[event_id],
        causal_inputs=causal_inputs,
        required_inputs=[
            target_event.id,
            source_event.id,
            *([prior_relation_id] if prior_relation_id else []),
        ],
        relation_kinds=relation_kinds,
    )


def _arxiv_reachable_source_graph(
    sources: dict[str, str],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Resolve the bounded input/include graph from the attested root TeX file."""
    include = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
    order: list[str] = []
    edges: list[tuple[str, str]] = []

    def resolve(raw: str) -> str:
        if raw.startswith("/") or ".." in raw.split("/") or "\\" in raw:
            raise ValueError("arXiv compiled source reference is unsafe")
        if raw in sources:
            return raw
        with_suffix = f"{raw}.tex"
        if with_suffix in sources:
            return with_suffix
        raise ValueError("arXiv compiled source reference is missing")

    def visit(path: str) -> None:
        if path in order:
            return
        order.append(path)
        for raw_line in sources[path].splitlines():
            line = re.split(r"(?<!\\)%", raw_line, maxsplit=1)[0]
            for match in include.finditer(line):
                target = resolve(match.group(1).strip())
                edges.append((path, target))
                visit(target)

    visit(resolve("main.tex"))
    return tuple(order), tuple(edges)


def _arxiv_include_receipts(record: Any) -> list[dict[str, Any]]:
    payload = json.loads(record.text)
    raw_sources = payload.get("latex_sources") if isinstance(payload, dict) else None
    sources = {
        str(source.get("path") or ""): str(source.get("text") or "")
        for source in raw_sources or []
    }
    _order, edges = _arxiv_reachable_source_graph(sources)
    receipts: list[dict[str, Any]] = []
    for parent, child in edges:
        matches = []
        for match in re.finditer(r"\\(?:input|include)\s*\{([^}]+)\}", sources[parent]):
            raw = match.group(1).strip()
            resolved = raw if raw in sources else f"{raw}.tex"
            if resolved == child:
                matches.append(match)
        if len(matches) != 1:
            raise ValueError("arXiv compiled include binding is not unique")
        match = matches[0]
        receipts.append(
            {
                "parent_path": parent,
                "child_path": child,
                "directive": match.group(0),
                "source_char_start": match.start(),
                "source_char_end": match.end(),
                "source_text_sha256": hashlib.sha256(
                    sources[parent].encode()
                ).hexdigest(),
            }
        )
    return receipts


def _sparks_revision_section_records(
    workflow: Any,
) -> tuple[dict[str, Any], Any, tuple[str, ...], tuple[tuple[str, str], ...]] | None:
    records = {str(record.attribute("revision_id")): record for record in workflow.records}
    if set(records) != {"v1", "v2", "v3", "v4", "v5"}:
        return None
    if any(
        record.attribute("work_id") != _SPARKS_WORK_ID
        or record.record_id != f"{_SPARKS_WORK_ID}{revision}"
        or record.source_family != "arxiv_record"
        for revision, record in records.items()
    ):
        return None
    expected_relations = {
        (f"{_SPARKS_WORK_ID}v{version}", f"{_SPARKS_WORK_ID}v{version - 1}")
        for version in range(2, 6)
    }
    relations = {
        (relation.source_record_id, relation.target_record_id): relation
        for relation in workflow.relations
        if relation.kind == "revision_of" and len(relation.evidence) == 1
    }
    if set(relations) != expected_relations:
        return None
    payload = json.loads(records["v5"].text)
    raw_sources = payload.get("latex_sources") if isinstance(payload, dict) else None
    if not isinstance(raw_sources, list):
        return None
    sources = {
        str(source.get("path") or ""): str(source.get("text") or "")
        for source in raw_sources
    }
    if len(sources) != len(raw_sources) or any(
        not path or not text for path, text in sources.items()
    ):
        return None
    try:
        reachable_order, reachable_edges = _arxiv_reachable_source_graph(sources)
    except ValueError:
        return None
    required_paths = set(_SPARKS_SECTION_TIERS["64k"])
    if not required_paths.issubset(reachable_order):
        return None
    for path, (_claim_id, quote) in _SPARKS_SECTION_CLAIMS.items():
        if sources.get(path, "").count(quote) != 1:
            return None
    if sources["contents/conclusion.tex"].count(_SPARKS_ACKNOWLEDGMENTS) != 1:
        return None
    prior_payload = json.loads(records["v4"].text)
    prior_sources = prior_payload.get("latex_sources") if isinstance(prior_payload, dict) else None
    if not isinstance(prior_sources, list) or sum(
        str(source.get("path") or "") == "contents/abstract.tex"
        for source in prior_sources
    ) != 1:
        return None
    return (
        records,
        relations[(records["v5"].record_id, records["v4"].record_id)],
        reachable_order,
        reachable_edges,
    )


def _sparks_revision_section_events(
    workflow: Any,
    prefix: str,
    workflow_index: int,
    recognized: tuple[
        dict[str, Any], Any, tuple[str, ...], tuple[tuple[str, str], ...]
    ],
) -> list[Event]:
    records, relation, reachable_order, reachable_edges = recognized
    record_indices = {
        record.record_id: index for index, record in enumerate(workflow.records)
    }
    relation_index = next(
        index
        for index, candidate in enumerate(workflow.relations)
        if candidate.relation_id == relation.relation_id
    )
    workflow_key = hashlib.sha256(workflow.workflow_id.encode()).hexdigest()[:12]
    graph_sha256 = hashlib.sha256(
        json.dumps(reachable_edges, separators=(",", ":")).encode()
    ).hexdigest()
    include_receipts = _arxiv_include_receipts(records["v5"])
    events: list[Event] = []
    for tier, source_paths in _SPARKS_SECTION_TIERS.items():
        target_event, _target_body, target_revision_fact, _target_facts = (
            _multiband_arxiv_revision_event(
                workflow=workflow,
                record=records["v4"],
                prefix=prefix,
                workflow_index=workflow_index,
                record_index=record_indices[records["v4"].record_id],
                tier=tier,
                included_basenames=None,
                view_channel="prior_endpoint",
                source_path="contents/abstract.tex",
            )
        )
        claim_events: list[Event] = []
        claim_bodies: dict[str, str] = {}
        claim_revision_facts: dict[str, str] = {}
        claim_source_facts: dict[str, dict[str, str]] = {}
        for source_index, source_path in enumerate(source_paths):
            claim_id, claim_quote = _SPARKS_SECTION_CLAIMS[source_path]
            event, body, revision_fact, source_facts = (
                _multiband_arxiv_revision_event(
                    workflow=workflow,
                    record=records["v5"],
                    prefix=prefix,
                    workflow_index=workflow_index,
                    record_index=record_indices[records["v5"].record_id],
                    tier=tier,
                    included_basenames=None,
                    view_channel=(
                        f"section_{len(source_paths) - source_index:02d}_{claim_id}"
                    ),
                    source_path=source_path,
                    strip_after=(
                        _SPARKS_ACKNOWLEDGMENTS
                        if source_path == "contents/conclusion.tex"
                        else ""
                    ),
                    section_claim_id=claim_id,
                    section_claim_quote=claim_quote,
                )
            )
            claim_events.append(event)
            claim_bodies[claim_id] = body
            claim_revision_facts[claim_id] = revision_fact
            claim_source_facts[claim_id] = source_facts
        # Full views follow the task's latest-revision-first reading program;
        # ordered views restore source chronology and therefore remain distinct.
        events.extend(claim_events)
        events.append(target_event)
        relation_source = next(
            event
            for event in claim_events
            if event.params["section_claim_id"] == "similarity_metric_limit"
        )
        relation_event = _multiband_arxiv_relation_event(
            workflow=workflow,
            records=records,
            relation=relation,
            relation_index=relation_index,
            workflow_index=workflow_index,
            tier=tier,
            source_event=relation_source,
            target_event=target_event,
            source_body=claim_bodies["similarity_metric_limit"],
            source_revision_fact_id=claim_revision_facts["similarity_metric_limit"],
            target_revision_fact_id=target_revision_fact,
            source_fact_ids=claim_source_facts["similarity_metric_limit"],
        )
        relation_event.params["render_control_tier"] = True
        events.append(relation_event)
        claim_ids = [str(event.params["section_claim_id"]) for event in claim_events]
        compile_context_id = (
            f"{prefix}.arxiv_section_compile_context_{tier}_{workflow_index}"
        )
        events.append(
            Event(
                id=compile_context_id,
                type="arxiv_section_compile_context",
                time=relation_event.time,
                params={
                    "workflow_id": workflow.workflow_id,
                    "control_tier": tier,
                    "compiled_source_graph_sha256": graph_sha256,
                    "selected_source_paths": list(source_paths),
                    "excluded_source_paths": sorted(
                        set(reachable_order) - set(source_paths)
                    ),
                },
                visibility=[compile_context_id],
            )
        )
        control_id = (
            f"{prefix}.arxiv_section_reconciliation_control_"
            f"{tier}_{workflow_index}"
        )
        control_inputs = [
            target_event.id,
            *[event.id for event in claim_events],
            relation_event.id,
        ]
        selected_paths = list(source_paths)
        control = Event(
            id=control_id,
            type="arxiv_section_reconciliation_control",
            time=relation_event.time + timedelta(days=1),
            params={
                "workflow_id": workflow.workflow_id,
                "work_id": _SPARKS_WORK_ID,
                "control_tier": tier,
                "control_key": f"paper_section_control:{workflow_key}:{tier}",
                "source_revision_id": "v5",
                "target_revision_id": "v4",
                "source_record_id": records["v5"].record_id,
                "target_record_id": records["v4"].record_id,
                "target_record_event_id": target_event.id,
                "relation_event_id": relation_event.id,
                "required_relation_id": relation.relation_id,
                "claim_event_ids": [event.id for event in claim_events],
                "required_claim_ids": claim_ids,
                "selected_source_paths": selected_paths,
                "compiled_source_order": list(reachable_order),
                "compiled_source_edges": [list(edge) for edge in reachable_edges],
                "compiled_source_graph_sha256": graph_sha256,
                "selected_compile_receipts": [
                    event.params["source_compile_receipts"][0]
                    for event in claim_events
                ],
                "compiled_source_include_receipts": include_receipts,
                "excluded_source_paths": sorted(set(reachable_order) - set(selected_paths)),
                "excluded_content_classes": [
                    "acknowledgments",
                    "author_block",
                    "bibliography",
                    "build_cache",
                    "macro_only",
                    "table_of_contents",
                    "unreachable_source",
                ],
            },
            visibility=[control_id],
            causal_inputs=[*control_inputs, compile_context_id],
            required_inputs=list(control_inputs),
            relation_kinds={
                target_event.id: "reads_prior_endpoint",
                **{event.id: "reads_compiled_section" for event in claim_events},
                relation_event.id: "authenticates_revision",
                compile_context_id: "reads_compile_receipt",
            },
        )
        events.append(control)
        decision_id = (
            f"{prefix}.arxiv_section_reconciliation_decision_"
            f"{tier}_{workflow_index}"
        )
        proof_event_ids = [*control_inputs, control.id, decision_id]
        events.append(
            Event(
                id=decision_id,
                type="arxiv_section_reconciliation_decision",
                time=control.time + timedelta(days=1),
                params={
                    "workflow_id": workflow.workflow_id,
                    "work_id": _SPARKS_WORK_ID,
                    "control_tier": tier,
                    "control_key": control.params["control_key"],
                    "answer_key": f"paper_section_reconciliation:{workflow_key}:{tier}",
                    "source_revision_id": "v5",
                    "target_revision_id": "v4",
                    "required_relation_id": relation.relation_id,
                    "relation_event_id": relation_event.id,
                    "required_claim_ids": claim_ids,
                    "control_event_id": control.id,
                    "counterfactual_claim_event_id": relation_source.id,
                    "proof_event_ids": proof_event_ids,
                },
                visibility=[decision_id],
                causal_inputs=[control.id, relation_event.id],
                required_inputs=[control.id, relation_event.id],
                relation_kinds={
                    control.id: "applies_compiled_control",
                    relation_event.id: "applies_revision_chain",
                },
            )
        )
    return events


def _paper_substantive_revision_records(
    workflow: Any,
) -> tuple[list[Any], int, str, frozenset[str], frozenset[str]] | None:
    """Recognize a long adjacent arXiv history with one reliable semantic delta."""
    records = sorted(
        workflow.records,
        key=lambda record: int(str(record.attribute("revision_id"))[1:]),
    )
    if len(records) < 7 or any(
        record.source_family != "arxiv_record" for record in records
    ):
        return None
    revision_ids = [str(record.attribute("revision_id")) for record in records]
    if revision_ids != [f"v{index}" for index in range(1, len(records) + 1)]:
        return None
    delta_indices = [
        index
        for index, record in enumerate(records)
        if sum(fact.field == "revision_added_text" for fact in record.facts) == 1
    ]
    if len(delta_indices) != 1:
        return None
    delta_index = delta_indices[0]
    if delta_index < 1 or delta_index + 2 >= len(records):
        return None
    expected_relations = {
        (records[index].record_id, records[index - 1].record_id)
        for index in range(1, len(records))
    }
    if {
        (relation.source_record_id, relation.target_record_id)
        for relation in workflow.relations
        if relation.kind == "revision_of" and len(relation.evidence) == 1
    } != expected_relations:
        return None
    source = records[delta_index]
    target = records[delta_index - 1]
    [delta_fact] = [
        fact for fact in source.facts if fact.field == "revision_added_text"
    ]
    source_payload = json.loads(source.text)
    target_payload = json.loads(target.text)
    source_files = {
        str(item["path"]).rsplit("/", 1)[-1]: str(item["text"])
        for item in source_payload["latex_sources"]
    }
    target_files = {
        str(item["path"]).rsplit("/", 1)[-1]: str(item["text"])
        for item in target_payload["latex_sources"]
    }
    delta_basenames = [
        basename for basename, text in source_files.items() if delta_fact.value in text
    ]
    if len(delta_basenames) != 1 or delta_basenames[0] not in target_files:
        return None
    basename = delta_basenames[0]

    def sections(text: str) -> dict[str, str]:
        headings = list(re.finditer(r"(?m)^\\section\*?\{(?P<name>[^}]*)\}", text))
        return {
            " ".join(heading.group("name").split()): text[
                heading.start() : (
                    headings[index + 1].start()
                    if index + 1 < len(headings)
                    else len(text)
                )
            ]
            for index, heading in enumerate(headings)
        }

    source_sections = sections(source_files[basename])
    target_sections = sections(target_files[basename])
    delta_sections = [
        name for name, text in source_sections.items() if delta_fact.value in text
    ]
    shared = set(source_sections) & set(target_sections)
    if len(delta_sections) != 1 or delta_sections[0] not in shared:
        return None
    delta_section = delta_sections[0]
    largest_other = max(
        (name for name in shared if name != delta_section),
        key=lambda name: min(len(source_sections[name]), len(target_sections[name])),
        default="",
    )
    if not largest_other:
        return None
    conclusion = next((name for name in shared if name.casefold() == "conclusion"), "")
    introduction = next(
        (name for name in shared if name.casefold() == "introduction"), ""
    )
    compact = frozenset({delta_section, largest_other, conclusion, introduction} - {""})
    expanded = frozenset(
        name
        for name in source_sections
        if name in shared and name.casefold() != "introduction"
    )
    if not introduction or not {delta_section, largest_other}.issubset(expanded):
        return None
    return records, delta_index, basename, compact, expanded


def _paper_substantive_revision_events(
    workflow: Any,
    prefix: str,
    workflow_index: int,
    recognized: tuple[list[Any], int, str, frozenset[str], frozenset[str]],
) -> list[Event]:
    records, delta_index, delta_basename, compact_sections, expanded_sections = (
        recognized
    )
    relations = {
        (relation.source_record_id, relation.target_record_id): (index, relation)
        for index, relation in enumerate(workflow.relations)
    }
    record_indices = {
        record.record_id: index for index, record in enumerate(workflow.records)
    }
    delta_fact = next(
        fact
        for fact in records[delta_index].facts
        if fact.field == "revision_added_text"
    )
    events: list[Event] = []
    proof_records = set(records[delta_index - 1 : delta_index + 3])

    for record in records:
        if record in proof_records:
            continue
        _body, _provenance, _excluded, spans = _semantic_arxiv_body(
            record, bind_file_spans=True
        )
        for index, span in enumerate(spans):
            context = _multiband_arxiv_context_event(
                workflow=workflow,
                record=record,
                prefix=prefix,
                workflow_index=workflow_index,
                record_index=record_indices[record.record_id],
                tier="64k",
                parent_channel=f"history_{index}",
                included_basenames=frozenset({str(span["basename"])}),
            )
            context.time = date.fromisoformat(record.occurred_at[:10])
            context.params["source_view_tier"] = "history"
            events.append(context)

    tier_specs = {
        "16k": (2, compact_sections),
        "32k": (3, expanded_sections),
        "64k": (4, None),
    }
    workflow_key = hashlib.sha256(workflow.workflow_id.encode()).hexdigest()[:12]
    for tier, (record_count, section_names) in tier_specs.items():
        selected_records = records[delta_index - 1 : delta_index - 1 + record_count]
        record_events: list[Event] = []
        bodies: dict[str, str] = {}
        revision_facts: dict[str, str] = {}
        source_facts: dict[str, dict[str, str]] = {}
        for offset, record in enumerate(selected_records):
            use_sections = (
                section_names
                if offset < 2 and section_names is not None
                else frozenset()
            )
            if tier == "32k" and offset == 1:
                use_sections |= compact_sections - expanded_sections
            included = None
            if offset >= 2:
                payload = json.loads(record.text)
                included = frozenset(
                    {
                        max(
                            payload["latex_sources"],
                            key=lambda item: len(str(item["text"])),
                        )["path"].rsplit("/", 1)[-1]
                    }
                )
            event, body, revision_fact, facts = _multiband_arxiv_revision_event(
                workflow=workflow,
                record=record,
                prefix=prefix,
                workflow_index=workflow_index,
                record_index=record_indices[record.record_id],
                tier=tier,
                included_basenames=included,
                view_channel=f"substantive_{offset}",
                section_basename=delta_basename if use_sections else "",
                section_names=use_sections,
            )
            events.append(event)
            record_events.append(event)
            bodies[record.record_id] = body
            revision_facts[record.record_id] = revision_fact
            source_facts[record.record_id] = facts

        relation_events: list[Event] = []
        for relation_offset in range(1, len(selected_records)):
            source_record = selected_records[relation_offset]
            target_record = selected_records[relation_offset - 1]
            relation_index, relation = relations[
                (source_record.record_id, target_record.record_id)
            ]
            relation_event = _multiband_arxiv_relation_event(
                workflow=workflow,
                records={
                    str(record.attribute("revision_id")): record for record in records
                },
                relation=relation,
                relation_index=relation_index,
                workflow_index=workflow_index,
                tier=tier,
                source_event=record_events[relation_offset],
                target_event=record_events[relation_offset - 1],
                source_body=bodies[source_record.record_id],
                source_revision_fact_id=revision_facts[source_record.record_id],
                target_revision_fact_id=revision_facts[target_record.record_id],
                source_fact_ids=source_facts[source_record.record_id],
                prior_relation_id=(relation_events[-1].id if relation_events else ""),
            )
            relation_event.params["render_control_tier"] = True
            events.append(relation_event)
            relation_events.append(relation_event)

        source_event = record_events[1]
        target_event = record_events[0]
        fact_start = bodies[records[delta_index].record_id].index(delta_fact.value)
        decision_id = (
            f"{prefix}.arxiv_revision_decision_{tier}_{workflow_index}_substantive"
        )
        terminal_event = record_events[-1] if len(record_events) > 2 else None
        proof_events = [event.id for event in record_events]
        proof_events.extend(event.id for event in relation_events)
        proof_events.append(decision_id)
        events.append(
            Event(
                id=decision_id,
                type="arxiv_revision_decision",
                time=max(event.time for event in record_events) + timedelta(days=1),
                params={
                    "workflow_id": workflow.workflow_id,
                    "work_id": records[delta_index].attribute("work_id"),
                    "control_tier": tier,
                    "substantive_revision_tier": True,
                    "decision_mode": "relation_delta",
                    "answer_key": f"real_revision_added_text:{workflow_key}:{tier}",
                    "candidate_key": f"real_revision_delta_candidate:{workflow_key}:{tier}",
                    "source_record_id": records[delta_index].record_id,
                    "target_record_id": records[delta_index - 1].record_id,
                    "source_revision_id": records[delta_index].attribute("revision_id"),
                    "target_revision_id": records[delta_index - 1].attribute(
                        "revision_id"
                    ),
                    "source_record_event_id": source_event.id,
                    "target_record_event_id": target_event.id,
                    "terminal_record_id": terminal_event.params["record_id"]
                    if terminal_event
                    else "",
                    "terminal_record_event_id": terminal_event.id
                    if terminal_event
                    else "",
                    "terminal_revision_id": terminal_event.params["revision_id"]
                    if terminal_event
                    else "",
                    "relation_event_id": relation_events[0].id,
                    "required_relation_ids": [
                        event.params["relation_id"] for event in relation_events
                    ],
                    "fact_id": delta_fact.fact_id,
                    "grounded_fact_id": source_facts[records[delta_index].record_id][
                        delta_fact.fact_id
                    ],
                    "fact_char_start": fact_start,
                    "fact_char_end": fact_start + len(delta_fact.value),
                    "fact_value_offset": 0,
                    "fact_value_length": len(delta_fact.value),
                    "proof_event_ids": proof_events,
                },
                visibility=[decision_id],
                causal_inputs=[event.id for event in record_events]
                + [relation_events[-1].id],
                required_inputs=[event.id for event in record_events]
                + [relation_events[-1].id],
                relation_kinds={
                    **{event.id: "reads_source" for event in record_events},
                    relation_events[-1].id: "applies_revision_chain",
                },
            )
        )
    return events


def _paper_multiband_events(
    workflow: Any, prefix: str, workflow_index: int, records: dict[str, Any]
) -> list[Event]:
    record_indices = {
        record.record_id: index for index, record in enumerate(workflow.records)
    }
    views: dict[str, dict[str, frozenset[str] | None]] = {
        "16k": _ARXIV_16K_VIEWS,
        "32k": {
            "v1": _ARXIV_32K_FILES,
            "v2": _ARXIV_32K_FILES,
            "v3": _ARXIV_TERMINAL_FILES,
        },
        "64k": {"v1": None, "v2": None, "v3": _ARXIV_TERMINAL_FILES},
    }
    events: list[Event] = []
    event_by_tier: dict[str, dict[str, Event]] = {}
    body_by_tier: dict[str, dict[str, str]] = {}
    revision_fact_by_tier: dict[str, dict[str, str]] = {}
    source_facts_by_tier: dict[str, dict[str, dict[str, str]]] = {}
    for tier, tier_views in views.items():
        event_by_tier[tier] = {}
        body_by_tier[tier] = {}
        revision_fact_by_tier[tier] = {}
        source_facts_by_tier[tier] = {}
        for revision_id, included in tier_views.items():
            record = records[revision_id]
            event, body, revision_fact, source_facts = _multiband_arxiv_revision_event(
                workflow=workflow,
                record=record,
                prefix=prefix,
                workflow_index=workflow_index,
                record_index=record_indices[record.record_id],
                tier=tier,
                included_basenames=included,
            )
            events.append(event)
            event_by_tier[tier][revision_id] = event
            body_by_tier[tier][revision_id] = body
            revision_fact_by_tier[tier][revision_id] = revision_fact
            source_facts_by_tier[tier][revision_id] = source_facts
            context_basename = {
                "v1": "illustrations_sup.tex",
                "v2": "discussion.tex",
            }.get(revision_id)
            if tier == "16k" and context_basename is not None:
                events.append(
                    _multiband_arxiv_context_event(
                        workflow=workflow,
                        record=record,
                        prefix=prefix,
                        workflow_index=workflow_index,
                        record_index=record_indices[record.record_id],
                        tier=tier,
                        parent_channel=f"source_{context_basename.removesuffix('.tex')}",
                        included_basenames=frozenset({context_basename}),
                    )
                )

    relations = {
        (relation.source_record_id, relation.target_record_id): (index, relation)
        for index, relation in enumerate(workflow.relations)
    }
    v2_v1 = relations[(records["v2"].record_id, records["v1"].record_id)]
    v3_v2 = relations[(records["v3"].record_id, records["v2"].record_id)]
    relation_events: dict[str, list[Event]] = {"16k": [], "32k": [], "64k": []}
    for tier in ("32k", "64k"):
        relation_index, relation = v2_v1
        delta_relation = _multiband_arxiv_relation_event(
            workflow=workflow,
            records=records,
            relation=relation,
            relation_index=relation_index,
            workflow_index=workflow_index,
            tier=tier,
            source_event=event_by_tier[tier]["v2"],
            target_event=event_by_tier[tier]["v1"],
            source_body=body_by_tier[tier]["v2"],
            source_revision_fact_id=revision_fact_by_tier[tier]["v2"],
            target_revision_fact_id=revision_fact_by_tier[tier]["v1"],
            source_fact_ids=source_facts_by_tier[tier]["v2"],
        )
        events.append(delta_relation)
        relation_events[tier].append(delta_relation)
        if tier == "64k":
            relation_index, relation = v3_v2
            terminal_relation = _multiband_arxiv_relation_event(
                workflow=workflow,
                records=records,
                relation=relation,
                relation_index=relation_index,
                workflow_index=workflow_index,
                tier=tier,
                source_event=event_by_tier[tier]["v3"],
                target_event=event_by_tier[tier]["v2"],
                source_body=body_by_tier[tier]["v3"],
                source_revision_fact_id=revision_fact_by_tier[tier]["v3"],
                target_revision_fact_id=revision_fact_by_tier[tier]["v2"],
                source_fact_ids=source_facts_by_tier[tier]["v3"],
                prior_relation_id=delta_relation.id,
            )
            events.append(terminal_relation)
            relation_events[tier].append(terminal_relation)

    delta_fact = next(
        fact for fact in records["v2"].facts if fact.field == "revision_added_text"
    )
    for tier in ("16k", "32k", "64k"):
        source = event_by_tier[tier]["v2"]
        target = event_by_tier[tier]["v1"]
        fact_start = body_by_tier[tier]["v2"].index(delta_fact.value)
        tier_relations = relation_events[tier]
        decision_id = f"{prefix}.arxiv_revision_decision_{tier}_{workflow_index}"
        proof_events = [target.id, source.id, event_by_tier[tier]["v3"].id]
        proof_events.extend(relation.id for relation in tier_relations)
        proof_events.append(decision_id)
        workflow_key = hashlib.sha256(workflow.workflow_id.encode()).hexdigest()[:12]
        causal_inputs = (
            [target.id, source.id, event_by_tier[tier]["v3"].id]
            if tier == "16k"
            else [tier_relations[-1].id]
        )
        events.append(
            Event(
                id=decision_id,
                type="arxiv_revision_decision",
                time=max(
                    event_by_tier[tier][revision_id].time
                    for revision_id in event_by_tier[tier]
                )
                + timedelta(days=1),
                params={
                    "workflow_id": workflow.workflow_id,
                    "work_id": records["v2"].attribute("work_id"),
                    "control_tier": tier,
                    "decision_mode": (
                        "direct_delta"
                        if tier == "16k"
                        else "terminal_chain"
                        if tier == "64k"
                        else "relation_delta"
                    ),
                    "answer_key": (f"real_revision_added_text:{workflow_key}:{tier}"),
                    "candidate_key": (
                        f"real_revision_delta_candidate:{workflow_key}:{tier}"
                    ),
                    "source_record_id": records["v2"].record_id,
                    "target_record_id": records["v1"].record_id,
                    "terminal_record_id": records["v3"].record_id,
                    "source_record_event_id": source.id,
                    "target_record_event_id": target.id,
                    "terminal_record_event_id": event_by_tier[tier]["v3"].id,
                    "relation_event_id": tier_relations[0].id if tier_relations else "",
                    "required_relation_ids": [
                        str(relation.params["relation_id"])
                        for relation in tier_relations
                    ],
                    "fact_id": delta_fact.fact_id,
                    "grounded_fact_id": source_facts_by_tier[tier]["v2"].get(
                        delta_fact.fact_id, ""
                    ),
                    "fact_char_start": fact_start,
                    "fact_char_end": fact_start + len(delta_fact.value),
                    "fact_value_offset": 0,
                    "fact_value_length": len(delta_fact.value),
                    "required_new_include": (
                        "parts/acknowledgements" if tier == "16k" else ""
                    ),
                    "required_new_include_source_basename": (
                        "main.tex" if tier == "16k" else ""
                    ),
                    "fact_source_basename": "acknowledgements.tex",
                    "proof_event_ids": proof_events,
                },
                visibility=[decision_id],
                causal_inputs=causal_inputs,
                required_inputs=list(causal_inputs),
                relation_kinds={
                    event_id: "reads_source"
                    if tier == "16k"
                    else "applies_revision_chain"
                    for event_id in causal_inputs
                },
            )
        )
    return events


def _add_benchmark_grounded_facts(
    event: Event,
    *,
    require_abstract: bool,
    require_detail: bool,
    require_training: bool,
) -> None:
    text = str(event.params["text"])
    abstract = (
        parse_arxiv_benchmark_observation(text, include_detail=False)
        if require_abstract
        else None
    )
    detail = parse_arxiv_benchmark_detail(text) if require_detail else None
    training = parse_arxiv_training_hardware(text) if require_training else None
    if (
        (require_abstract and abstract is None)
        or (require_detail and detail is None)
        or (require_training and training is None)
    ):
        raise ValueError("arXiv benchmark observation is missing from source view")
    atoms: dict[str, dict[str, Any]] = {}
    if abstract is not None:
        atoms.update(
            {
                "benchmark": abstract["benchmark"],
                "score": abstract["score"],
                "days": abstract["days"],
            }
        )
    if detail is not None:
        if (
            abstract is not None
            and detail["benchmark"]["value"] != abstract["benchmark"]["value"]
        ):
            raise ValueError("arXiv benchmark channels disagree on benchmark")
        atoms.setdefault("benchmark", detail["benchmark"])
        atoms["detail_score"] = detail["score"]
    if training is not None:
        atoms["training_hardware"] = training
    grounded_source = dict(event.params["grounded_source"])
    facts = list(grounded_source["facts"])
    for role, atom in atoms.items():
        facts.append(
            _grounded_fact(
                source_id=event.id,
                text=text,
                fact_id=f"{event.id}:benchmark_{role}",
                quote=str(atom["value"]),
                char_start=int(atom["char_start"]),
            )
        )
    grounded_source["facts"] = facts
    event.params["grounded_source"] = grounded_source
    event.params["ground_values"] = [
        *event.params.get("ground_values", []),
        *(str(atom["value"]) for atom in atoms.values()),
    ]


def _paper_benchmark_trace_events(
    workflow: Any, prefix: str, workflow_index: int, records: dict[str, Any]
) -> list[Event]:
    record_indices = {
        record.record_id: index for index, record in enumerate(workflow.records)
    }
    relations = {
        (relation.source_record_id, relation.target_record_id): (index, relation)
        for index, relation in enumerate(workflow.relations)
    }
    events: list[Event] = []
    workflow_key = hashlib.sha256(workflow.workflow_id.encode()).hexdigest()[:12]
    context_records_created: set[str] = set()
    evidence_events: dict[tuple[str, str], Event] = {}
    evidence_event_basenames: dict[tuple[str, str], frozenset[str]] = {}
    relation_events_by_pair: dict[tuple[str, str], Event] = {}
    for tier in ("16k", "32k", "64k"):
        tier_views = _ARXIV_BENCHMARK_TRACE_VIEWS[tier]
        view_events: list[tuple[Event, bool, bool, bool]] = []
        revision_events: dict[str, list[Event]] = {}
        event_bodies: dict[str, str] = {}
        revision_fact_ids: dict[str, str] = {}
        for (
            revision_id,
            channel,
            included_basenames,
            require_abstract,
            require_detail,
            require_training,
        ) in tier_views:
            record = records[revision_id]
            _configured_body, _configured_provenance, _configured_excluded, spans = (
                _semantic_arxiv_body(
                    record,
                    included_basenames=included_basenames,
                    bind_file_spans=True,
                )
            )
            available_basenames = frozenset(str(span["basename"]) for span in spans)
            preferred_evidence = {
                *({"introduction.tex", "ms.tex"} if require_abstract else set()),
                *(
                    {
                        "parameter_attention.tex",
                        "results.tex",
                        "sqrt_d_trick.tex",
                        "visualizations.tex",
                    }
                    if require_detail
                    else set()
                ),
                *({"training.tex"} if require_training else set()),
            }
            evidence_key = (revision_id, channel)
            evidence_basenames = evidence_event_basenames.get(
                evidence_key,
                frozenset(available_basenames & preferred_evidence),
            )
            event = evidence_events.get(evidence_key)
            if event is None:
                event, body, revision_fact_id, _source_facts = (
                    _multiband_arxiv_revision_event(
                        workflow=workflow,
                        record=record,
                        prefix=prefix,
                        workflow_index=workflow_index,
                        record_index=record_indices[record.record_id],
                        tier=tier,
                        included_basenames=evidence_basenames,
                        view_channel=channel,
                    )
                )
                _add_benchmark_grounded_facts(
                    event,
                    require_abstract=require_abstract,
                    require_detail=require_detail,
                    require_training=require_training,
                )
                events.append(event)
                evidence_events[evidence_key] = event
                evidence_event_basenames[evidence_key] = evidence_basenames
            else:
                body = str(event.params["text"])
                revision_fact_id = f"{event.id}:revision_id"
            view_events.append(
                (event, require_abstract, require_detail, require_training)
            )
            revision_events.setdefault(revision_id, []).append(event)
            event_bodies[event.id] = body
            revision_fact_ids[event.id] = revision_fact_id
        for revision_id in dict.fromkeys(item[0] for item in tier_views):
            record = records[revision_id]
            if record.record_id in context_records_created:
                continue
            _full_body, _full_provenance, _full_excluded, full_spans = (
                _semantic_arxiv_body(record, bind_file_spans=True)
            )
            all_basenames = {str(span["basename"]) for span in full_spans}
            proof_basenames = {
                str(basename)
                for event in revision_events[revision_id]
                for basename in event.params["source_view_basenames"]
            }
            for index, basename in enumerate(sorted(all_basenames - proof_basenames)):
                events.append(
                    _multiband_arxiv_context_event(
                        workflow=workflow,
                        record=record,
                        prefix=prefix,
                        workflow_index=workflow_index,
                        record_index=record_indices[record.record_id],
                        tier=tier,
                        parent_channel=f"source_{index}",
                        included_basenames=frozenset({basename}),
                    )
                )
            context_records_created.add(record.record_id)

        relation_events: list[Event] = []
        relation_pairs = {
            "16k": (),
            "32k": (("v2", "v1"),),
            "64k": (("v2", "v1"), ("v3", "v2")),
        }[tier]
        for source_revision, target_revision in relation_pairs:
            relation_index, relation = relations[
                (
                    records[source_revision].record_id,
                    records[target_revision].record_id,
                )
            ]
            source_event = revision_events[source_revision][0]
            target_event = revision_events[target_revision][0]
            relation_key = (source_revision, target_revision)
            relation_event = relation_events_by_pair.get(relation_key)
            if relation_event is None:
                relation_event = _multiband_arxiv_relation_event(
                    workflow=workflow,
                    records=records,
                    relation=relation,
                    relation_index=relation_index,
                    workflow_index=workflow_index,
                    tier=tier,
                    source_event=source_event,
                    target_event=target_event,
                    source_body=event_bodies[source_event.id],
                    source_revision_fact_id=revision_fact_ids[source_event.id],
                    target_revision_fact_id=revision_fact_ids[target_event.id],
                    source_fact_ids={},
                    prior_relation_id=(
                        relation_events[-1].id if relation_events else ""
                    ),
                )
                events.append(relation_event)
                relation_events_by_pair[relation_key] = relation_event
            relation_events.append(relation_event)

        decision_id = f"{prefix}.arxiv_benchmark_trace_decision_{tier}_{workflow_index}"
        proof_events = [
            event.id for event, _abstract, _detail, _training in view_events
        ]
        proof_events.extend(event.id for event in relation_events)
        proof_events.append(decision_id)
        causal_inputs = (
            [relation_events[-1].id]
            if relation_events
            else [event.id for event, _abstract, _detail, _training in view_events]
        )
        if relation_events:
            causal_inputs = [
                *(event.id for event, _abstract, _detail, _training in view_events),
                *causal_inputs,
            ]
        events.append(
            Event(
                id=decision_id,
                type="arxiv_benchmark_trace_decision",
                time=max(
                    event.time for event, _abstract, _detail, _training in view_events
                )
                + timedelta(days=1),
                params={
                    "workflow_id": workflow.workflow_id,
                    "work_id": records["v1"].attribute("work_id"),
                    "control_tier": tier,
                    "answer_key": (
                        f"real_benchmark_revision_trace:{workflow_key}:{tier}"
                    ),
                    "record_event_ids": [
                        event.id for event, _abstract, _detail, _training in view_events
                    ],
                    "record_requirements": [
                        {
                            "event_id": event.id,
                            "require_abstract": require_abstract,
                            "require_detail": require_detail,
                            "require_training": require_training,
                        }
                        for event, require_abstract, require_detail, require_training in view_events
                    ],
                    "trace_mode": "full_trace",
                    "include_detailed_results": True,
                    "required_relation_ids": [
                        str(event.params["relation_id"]) for event in relation_events
                    ],
                    "proof_event_ids": proof_events,
                },
                visibility=[decision_id],
                causal_inputs=causal_inputs,
                required_inputs=list(causal_inputs),
                relation_kinds={
                    event_id: (
                        "applies_revision_chain"
                        if event_id in {event.id for event in relation_events}
                        else "reads_source"
                    )
                    for event_id in causal_inputs
                },
            )
        )
    return events


def _source_workflow_events(project: dict[str, Any], prefix: str) -> list[Event]:
    events: list[Event] = []
    for workflow_index, workflow in enumerate(project.get("source_workflows") or []):
        if workflow.source_kind == WIKIMEDIA_SOURCE_KIND:
            events.extend(
                _wikipedia_source_workflow_events(workflow, prefix, workflow_index)
            )
            continue
        if workflow.source_kind != PAPER_SOURCE_KIND:
            continue
        sparks_records = _sparks_revision_section_records(workflow)
        if sparks_records is not None:
            events.extend(
                _sparks_revision_section_events(
                    workflow, prefix, workflow_index, sparks_records
                )
            )
            continue
        benchmark_records = _paper_benchmark_trace_records(workflow)
        if benchmark_records is not None:
            events.extend(
                _paper_benchmark_trace_events(
                    workflow, prefix, workflow_index, benchmark_records
                )
            )
            continue
        multiband_records = _paper_multiband_records(workflow)
        if multiband_records is not None:
            events.extend(
                _paper_multiband_events(
                    workflow, prefix, workflow_index, multiband_records
                )
            )
            continue
        substantive_records = _paper_substantive_revision_records(workflow)
        if substantive_records is not None:
            events.extend(
                _paper_substantive_revision_events(
                    workflow, prefix, workflow_index, substantive_records
                )
            )
            continue
        record_event_ids: dict[str, str] = {}
        record_bodies: dict[str, str] = {}
        record_revision_fact_ids: dict[str, str] = {}
        record_source_fact_ids: dict[str, dict[str, str]] = {}
        records = {record.record_id: record for record in workflow.records}
        for record_index, record in enumerate(workflow.records):
            body, provenance_id, excluded_paths, _source_file_spans = (
                _semantic_arxiv_body(record)
            )
            event_id = f"{prefix}.arxiv_revision_{workflow_index}_{record_index}"
            record_event_ids[record.record_id] = event_id
            record_bodies[record.record_id] = body
            revision_id = record.attribute("revision_id")
            revision_start = body.index(revision_id)
            revision_fact_id = f"{event_id}:revision_id"
            submitted_at = record.occurred_at
            submitted_start = body.index(submitted_at)
            submitted_fact_id = f"{event_id}:submitted_at"
            record_revision_fact_ids[record.record_id] = revision_fact_id
            grounded_facts = [
                _grounded_fact(
                    source_id=event_id,
                    text=body,
                    fact_id=revision_fact_id,
                    quote=revision_id,
                    char_start=revision_start,
                ),
                _grounded_fact(
                    source_id=event_id,
                    text=body,
                    fact_id=submitted_fact_id,
                    quote=submitted_at,
                    char_start=submitted_start,
                ),
            ]
            source_fact_ids: dict[str, str] = {}
            for fact_index, fact in enumerate(record.facts):
                if body.count(fact.value) != 1:
                    continue
                grounded_fact_id = f"{event_id}:source_fact_{fact_index}"
                source_fact_ids[fact.fact_id] = grounded_fact_id
                grounded_facts.append(
                    _grounded_fact(
                        source_id=event_id,
                        text=body,
                        fact_id=grounded_fact_id,
                        quote=fact.value,
                        char_start=body.index(fact.value),
                    )
                )
            record_source_fact_ids[record.record_id] = source_fact_ids
            text_sha256 = hashlib.sha256(body.encode()).hexdigest()
            source_envelope = _arxiv_source_envelope(
                workflow=workflow,
                record=record,
                text_sha256=text_sha256,
                provenance_id=provenance_id,
            )
            events.append(
                Event(
                    id=event_id,
                    type="arxiv_revision",
                    time=date.fromisoformat(record.occurred_at[:10]),
                    params={
                        "workflow_id": workflow.workflow_id,
                        "record_id": record.record_id,
                        "revision_id": record.attribute("revision_id"),
                        "occurred_at": record.occurred_at,
                        "text": body,
                        "text_sha256": text_sha256,
                        "source_sha256": record.source_sha256,
                        "parent_provenance_id": record.provenance_id,
                        "provenance_id": provenance_id,
                        "provenance_operation": "arxiv_semantic_latex_body_v1",
                        "source_binding_provenance": "verified_derived",
                        "canonical_source_envelope_sha256": _canonical_digest(
                            source_envelope
                        ),
                        "excluded_paths": excluded_paths,
                        "source_origin": "real_derived",
                        "source_family": record.source_family,
                        "source_url": record.source_url,
                        "retrieval_url": record.retrieval_url,
                        "ground_values": [
                            record.occurred_at[:10],
                            *(
                                fact.value
                                for fact in record.facts
                                if fact.value in body
                            ),
                        ],
                        "grounded_source": _grounded_source(
                            source_id=event_id,
                            text=body,
                            facts=grounded_facts,
                        ),
                    },
                    visibility=[
                        f"{prefix}.arxiv_revision_{workflow_index}_{record_index}"
                    ],
                )
            )
        for relation_index, relation in enumerate(workflow.relations):
            if relation.kind != "revision_of":
                continue
            source = records[relation.source_record_id]
            selected_facts = [
                fact for fact in source.facts if fact.field == "revision_added_text"
            ]
            if len(selected_facts) != 1 or len(relation.evidence) != 1:
                continue
            fact = selected_facts[0]
            source_body = record_bodies[relation.source_record_id]
            if source_body.count(fact.value) != 1:
                raise ValueError("revision-added fact is not unique in semantic body")
            fact_start = source_body.index(fact.value)
            evidence = relation.evidence[0]
            relation_event_id = (
                f"{prefix}.arxiv_revision_relation_{workflow_index}_{relation_index}"
            )
            decision_event_id = (
                f"{prefix}.arxiv_revision_decision_{workflow_index}_{relation_index}"
            )
            source_event_id = record_event_ids[relation.source_record_id]
            target_event_id = record_event_ids[relation.target_record_id]
            relation_key = hashlib.sha256(
                f"{workflow.workflow_id}|{relation.relation_id}".encode()
            ).hexdigest()[:12]
            candidate_key = f"real_revision_delta_candidate:{relation_key}"
            answer_key = f"real_revision_added_text:{relation_key}"
            selected_grounded_fact_id = record_source_fact_ids[
                relation.source_record_id
            ].get(fact.fact_id)
            if not selected_grounded_fact_id:
                continue
            grounded_relation = {
                "relation_id": relation.relation_id,
                "relation_type": relation.kind,
                "source_id": source_event_id,
                "target_id": target_event_id,
                "claimed_provenance_class": "authentic_source_api",
                "evidence_fact_ids": [
                    record_revision_fact_ids[relation.source_record_id],
                    record_revision_fact_ids[relation.target_record_id],
                    selected_grounded_fact_id,
                ],
                "proof_mode": "structural_closure_only",
            }
            events.extend(
                [
                    Event(
                        id=relation_event_id,
                        type="arxiv_revision_relation",
                        time=date.fromisoformat(source.occurred_at[:10]),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "work_id": source.attribute("work_id"),
                            "candidate_key": candidate_key,
                            "relation_id": relation.relation_id,
                            "relation_kind": relation.kind,
                            "source_record_id": relation.source_record_id,
                            "target_record_id": relation.target_record_id,
                            "source_revision_id": source.attribute("revision_id"),
                            "target_revision_id": records[
                                relation.target_record_id
                            ].attribute("revision_id"),
                            "source_record_event_id": source_event_id,
                            "target_record_event_id": target_event_id,
                            "source_url": source.source_url,
                            "target_source_url": records[
                                relation.target_record_id
                            ].source_url,
                            "source_family": source.source_family,
                            "evidence_quote": evidence.evidence_quote,
                            "evidence_char_start": evidence.char_start,
                            "fact_id": fact.fact_id,
                            "grounded_fact_id": selected_grounded_fact_id,
                            "fact_char_start": fact_start,
                            "fact_char_end": fact_start + len(fact.value),
                            "fact_value_offset": 0,
                            "fact_value_length": len(fact.value),
                            "grounded_relation": grounded_relation,
                            "relation_provenance": "authentic_source_api",
                        },
                        visibility=[
                            (
                                f"{prefix}.arxiv_revision_relation_"
                                f"{workflow_index}_{relation_index}"
                            )
                        ],
                        causal_inputs=[target_event_id, source_event_id],
                        required_inputs=[target_event_id, source_event_id],
                        relation_kinds={
                            target_event_id: "revision_of",
                            source_event_id: "derived_from",
                        },
                    ),
                    Event(
                        id=decision_event_id,
                        type="arxiv_revision_decision",
                        time=date.fromisoformat(source.occurred_at[:10])
                        + timedelta(days=1),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "candidate_key": candidate_key,
                            "answer_key": answer_key,
                            "relation_id": relation.relation_id,
                            "relation_event_id": relation_event_id,
                            "source_record_event_id": source_event_id,
                            "target_record_event_id": target_event_id,
                        },
                        visibility=[
                            (
                                f"{prefix}.arxiv_revision_decision_"
                                f"{workflow_index}_{relation_index}"
                            )
                        ],
                        causal_inputs=[relation_event_id],
                        required_inputs=[relation_event_id],
                        relation_kinds={relation_event_id: "enables"},
                    ),
                ]
            )
    return events


def canonical_researchlab_source_event_envelope(
    world_spec: dict[str, Any],
    *,
    event_id: str,
    event_type: str,
    record_id: str = "",
    relation_id: str = "",
    section_id: str = "",
) -> dict[str, Any] | None:
    """Rebuild one source event only from canonical workflows in ``world_spec``."""
    project = world_spec.get("project")
    prefix = world_spec.get("prefix")
    if not isinstance(project, dict) or not isinstance(prefix, str) or not prefix:
        return None
    if event_type not in {
        "arxiv_revision",
        "arxiv_revision_context",
        "arxiv_revision_relation",
        "wiki_source_relation",
        "wiki_source_section",
    }:
        return None
    matches = [
        event
        for event in _source_workflow_events(project, prefix)
        if event.id == event_id
        and event.type == event_type
        and (not record_id or event.params.get("record_id") == record_id)
        and (not relation_id or event.params.get("relation_id") == relation_id)
        and (not section_id or event.params.get("section_id") == section_id)
    ]
    if len(matches) != 1:
        return None
    event = matches[0]
    return {
        "id": event.id,
        "type": event.type,
        "time": event.time,
        "params": event.params,
        "visibility": list(event.visibility),
        "preconditions": list(event.preconditions),
        "causal_inputs": list(event.causal_inputs),
        "required_inputs": list(event.required_inputs),
        "relation_kinds": dict(event.relation_kinds),
        "skipped": event.skipped,
        "skip_reason": event.skip_reason,
    }


def selected_wiki_source_relation_edges(
    world: SimulatedWorld, spec: Any, artifacts: list[Any]
) -> list[dict[str, str]]:
    """Return signed Wikimedia relations whose two source endpoints are selected."""
    selected_event_ids = set(getattr(spec, "sufficient_event_ids", ()))
    selected_event_ids &= {
        event_id
        for artifact in artifacts
        for event_id in getattr(artifact, "reveals_events", ())
    }
    events = {
        event.id: event for event in world.events if event.id in selected_event_ids
    }
    edges: list[dict[str, str]] = []
    for relation in events.values():
        if relation.type != "wiki_source_relation":
            continue
        params = relation.params
        source_event_id = str(params.get("source_record_event_id") or "")
        target_event_id = str(params.get("target_record_event_id") or "")
        source = events.get(source_event_id)
        target = events.get(target_event_id)
        canonical = canonical_researchlab_source_event_envelope(
            world.spec,
            event_id=relation.id,
            event_type=relation.type,
            record_id=str(params.get("record_id") or ""),
            relation_id=str(params.get("relation_id") or ""),
        )
        observed = {
            "id": relation.id,
            "type": relation.type,
            "time": relation.time,
            "params": relation.params,
            "visibility": list(relation.visibility),
            "preconditions": list(relation.preconditions),
            "causal_inputs": list(relation.causal_inputs),
            "required_inputs": list(relation.required_inputs),
            "relation_kinds": dict(relation.relation_kinds),
            "skipped": relation.skipped,
            "skip_reason": relation.skip_reason,
        }
        if (
            canonical != observed
            or source is None
            or target is None
            or source.type != "wiki_source_section"
            or target.type != "wiki_source_section"
            or set(relation.required_inputs) != {source_event_id, target_event_id}
            or source_event_id == target_event_id
            or str(source.params.get("section_record_id") or "")
            != str(params.get("source_record_id") or "")
            or str(target.params.get("section_record_id") or "")
            != str(params.get("target_record_id") or "")
            or str(source.params.get("source_url") or "")
            != str(params.get("source_url") or "")
            or str(target.params.get("source_url") or "")
            != str(params.get("target_source_url") or "")
        ):
            continue
        edges.append(
            {
                "parent_record_id": str(params["source_record_id"]),
                "child_record_id": str(params["target_record_id"]),
                "relation": str(params["relation_kind"]),
                "relation_provenance": "authentic_source",
                "parent_source_url": str(params["source_url"]),
                "child_source_url": str(params["target_source_url"]),
            }
        )
    return sorted(
        edges,
        key=lambda edge: (
            edge["relation"],
            edge["parent_record_id"],
            edge["child_record_id"],
        ),
    )


def valid_arxiv_revision_relation_event(
    relation: Event,
    events: dict[str, Event],
    *,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """Validate exact endpoints and an optional continuous prior revision edge."""
    if relation.type != "arxiv_revision_relation" or relation.id in seen:
        return False
    source_record_id = str(relation.params.get("source_record_id") or "")
    target_record_id = str(relation.params.get("target_record_id") or "")
    source = events.get(str(relation.params.get("source_record_event_id") or ""))
    target = events.get(str(relation.params.get("target_record_event_id") or ""))
    if (
        not source_record_id
        or not target_record_id
        or source_record_id == target_record_id
        or source is None
        or target is None
        or source.type != "arxiv_revision"
        or target.type != "arxiv_revision"
        or source.params.get("record_id") != source_record_id
        or target.params.get("record_id") != target_record_id
    ):
        return False
    expected_inputs = {source.id, target.id}
    prior_id = str(relation.params.get("required_prior_relation_event_id") or "")
    if prior_id:
        prior = events.get(prior_id)
        if (
            prior is None
            or not valid_arxiv_revision_relation_event(
                prior, events, seen=seen | {relation.id}
            )
            or prior.params.get("source_record_id") != target_record_id
            or prior.params.get("workflow_id") != relation.params.get("workflow_id")
            or prior.params.get("work_id") != relation.params.get("work_id")
        ):
            return False
        expected_inputs.add(prior_id)
    return (
        len(relation.required_inputs) == len(expected_inputs)
        and set(relation.required_inputs) == expected_inputs
    )


def canonical_researchlab_source_visible_text(
    event_or_envelope: Event | dict[str, Any],
) -> str | None:
    """Render source-visible bytes shared by canonical replay and artifacts."""
    event_type: Any
    params: Any
    if isinstance(event_or_envelope, Event):
        event_type = event_or_envelope.type
        params = event_or_envelope.params
    elif isinstance(event_or_envelope, dict):
        event_type = event_or_envelope.get("type")
        params = event_or_envelope.get("params")
    else:
        return None
    if not isinstance(event_type, str) or not isinstance(params, dict):
        return None
    if event_type in {
        "arxiv_revision",
        "arxiv_revision_context",
        "wiki_source_section",
    }:
        text = params.get("text")
        return text if isinstance(text, str) and text else None
    if event_type == "arxiv_revision_relation":
        required = (
            "relation_kind",
            "source_revision_id",
            "target_revision_id",
            "evidence_quote",
        )
        if any(not isinstance(params.get(field), str) for field in required):
            return None
        return (
            json.dumps(
                {
                    "kind": "arxiv_revision_relation",
                    "relation": params["relation_kind"],
                    "source_revision": params["source_revision_id"],
                    "target_revision": params["target_revision_id"],
                    "evidence": params["evidence_quote"],
                    **(
                        {"control_tier": params["source_view_tier"]}
                        if params.get("render_control_tier") is True
                        else {}
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    if event_type == "wiki_source_relation":
        wiki_required = (
            "relation_id",
            "relation_kind",
            "source_record_id",
            "target_record_id",
            "evidence_quote",
        )
        if any(not isinstance(params.get(field), str) for field in wiki_required):
            return None
        return (
            json.dumps(
                {
                    "kind": "wiki_source_relation",
                    "relation_id": params["relation_id"],
                    "relation": params["relation_kind"],
                    "source_record": params["source_record_id"],
                    "target_record": params["target_record_id"],
                    "evidence": params["evidence_quote"],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    return None


def _experiment_events(
    project: dict[str, Any], prefix: str, start: date
) -> list[Event]:
    workstreams = list(project.get("experiment_workstreams") or [])
    if not workstreams:
        return []

    def event_id(workstream_id: str, stage: str) -> str:
        return f"{prefix}.experiment_{workstream_id}_{stage}"

    events: list[Event] = []
    lane_count = min(6, len(workstreams))
    for workstream in workstreams:
        workstream_id = str(workstream["id"])
        lane = int(workstream["lane"])
        cycle = int(workstream["cycle"])
        base_month = 21 + (cycle - 1) * 6
        day_offset = 2 * (lane - 1)
        upstream_id = workstream.get("upstream_id")
        upstream_recovery = (
            event_id(str(upstream_id), "recovery") if upstream_id else None
        )
        revision_inputs = [upstream_recovery] if upstream_recovery else []
        revision_relations = (
            {upstream_recovery: "derived_from"} if upstream_recovery else {}
        )
        shared = {
            "workstream": workstream_id,
            "lane": lane,
            "cycle": cycle,
            "upstream_id": upstream_id,
            "objective": workstream["objective"],
            "dataset_slice": workstream["dataset_slice"],
            "metric": workstream["metric"],
            "environment": workstream["environment"],
            "failure_mode": workstream["failure_mode"],
            "recovery_action": workstream["recovery_action"],
        }
        revision_id = event_id(workstream_id, "revision")
        review_id = event_id(workstream_id, "review")
        benchmark_id = event_id(workstream_id, "benchmark")
        failure_id = event_id(workstream_id, "failure")
        recovery_id = event_id(workstream_id, "recovery")
        events.extend(
            [
                Event(
                    id=revision_id,
                    type="experiment_revision",
                    time=_date(start, base_month, day_offset),
                    params={**shared, "revision": workstream["revision"]},
                    visibility=[f"{prefix}.experiment_{workstream_id}_revision"],
                    causal_inputs=revision_inputs,
                    required_inputs=revision_inputs,
                    relation_kinds=revision_relations,
                ),
                Event(
                    id=review_id,
                    type="experiment_review",
                    time=_date(start, base_month + 1, day_offset),
                    params={
                        **shared,
                        "review": workstream["review"],
                        "revision": workstream["revision"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_review"],
                    causal_inputs=[revision_id],
                    required_inputs=[revision_id],
                    relation_kinds={revision_id: "derived_from"},
                ),
                Event(
                    id=benchmark_id,
                    type="experiment_benchmark",
                    time=_date(start, base_month + 2, day_offset),
                    params={
                        **shared,
                        "benchmark": workstream["benchmark"],
                        "review": workstream["review"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_benchmark"],
                    causal_inputs=[review_id],
                    required_inputs=[review_id],
                    relation_kinds={review_id: "enables"},
                ),
                Event(
                    id=failure_id,
                    type="experiment_failure",
                    time=_date(start, base_month + 3, day_offset),
                    params={
                        **shared,
                        "failure": workstream["failure"],
                        "benchmark": workstream["benchmark"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_failure"],
                    causal_inputs=[benchmark_id],
                    required_inputs=[benchmark_id],
                    relation_kinds={benchmark_id: "contradicts"},
                ),
                Event(
                    id=recovery_id,
                    type="experiment_recovery",
                    time=_date(start, base_month + 4, day_offset),
                    params={
                        **shared,
                        "run": workstream["recovery"],
                        "failure": workstream["failure"],
                        "benchmark": workstream["benchmark"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_recovery"],
                    causal_inputs=[failure_id, benchmark_id],
                    required_inputs=[failure_id],
                    relation_kinds={
                        failure_id: "supersedes",
                        benchmark_id: "derived_from",
                    },
                ),
            ]
        )

    recovery_ids = [
        event_id(str(workstream["id"]), "recovery") for workstream in workstreams
    ]
    last_cycle = max(int(workstream["cycle"]) for workstream in workstreams)
    events.append(
        Event(
            id=f"{prefix}.experiment_matrix_decision",
            type="experiment_matrix_decision",
            time=_date(start, 21 + (last_cycle - 1) * 6 + 5, 15),
            params={
                "workstream_ids": [str(workstream["id"]) for workstream in workstreams],
                "lane_count": lane_count,
            },
            visibility=[f"{prefix}.experiment_matrix_decision"],
            causal_inputs=recovery_ids,
            required_inputs=recovery_ids,
            relation_kinds={event_id_: "enables" for event_id_ in recovery_ids},
        )
    )
    return events


def events_for_lab(project: dict[str, Any], prefix: str) -> list[Event]:
    start = date.fromisoformat(project["start"])
    pid = project["paper"].lower()
    v1 = project["v1_score"]
    final = project["final_score"]
    cause = project["cause_token"]
    cache_tok, split_tok = cause.split("+", 1)
    adopt = bool(project.get("is_focal", False))

    def eid(kind: str) -> str:
        return f"{prefix}.{kind}"

    evs: list[Event] = [
        Event(
            id=eid("license_clause"),
            type="license_clause",
            time=_date(start, 0, 1),
            params={"spdx": project["spdx"]},
            visibility=[f"{prefix}.license_note"],
            causal_inputs=[],
        ),
        Event(
            id=eid("report_v1"),
            type="report_v1",
            time=_date(start, 0, 2),
            params={"score": v1},
            visibility=[f"{prefix}.arxiv_v1"],
            causal_inputs=[],
        ),
        Event(
            id=eid("commit_tokenizer"),
            type="commit_tokenizer",
            time=_date(start, 1, 4),
            params={"commit": project["commit_hash"]},
            visibility=[f"{prefix}.git_commit"],
            causal_inputs=[eid("report_v1")],
            relation_kinds={eid("report_v1"): "enables"},
        ),
        Event(
            id=eid("log_stale_cache"),
            type="log_stale_cache",
            time=_date(start, 2, 6),
            params={"cause_cache": cache_tok},
            visibility=[f"{prefix}.eval_log"],
            causal_inputs=[eid("commit_tokenizer")],
            relation_kinds={eid("commit_tokenizer"): "derived_from"},
        ),
        Event(
            id=eid("issue_testset"),
            type="issue_testset",
            time=_date(start, 3, 3),
            params={
                "filed": True,
                "cause_cache": cache_tok,
                "cause_split": split_tok,
            },
            visibility=[f"{prefix}.github_issue"],
            causal_inputs=[eid("report_v1")],
            relation_kinds={eid("report_v1"): "contradicts"},
        ),
        Event(
            id=eid("fix_rerun"),
            type="fix_rerun",
            time=_date(start, 4, 8),
            params={"score": final},
            visibility=[f"{prefix}.rerun_json"],
            preconditions=["issue_filed"],
            causal_inputs=[eid("issue_testset"), eid("log_stale_cache")],
            relation_kinds={
                eid("issue_testset"): "enables",
                eid("log_stale_cache"): "derived_from",
            },
        ),
        Event(
            id=eid("camera_ready"),
            type="camera_ready",
            time=_date(start, 5, 5),
            params={"body_score": round(float(v1) + 0.01, 2)},
            visibility=[f"{prefix}.camera_ready"],
            causal_inputs=[eid("fix_rerun"), eid("report_v1")],
            relation_kinds={
                eid("fix_rerun"): "supersedes",
                eid("report_v1"): "contradicts",
            },
        ),
        Event(
            id=eid("release_note"),
            type="release_note",
            time=_date(start, 6, 2),
            params={"adopt_rerun": adopt},
            visibility=[f"{prefix}.release_note"],
            causal_inputs=[
                eid("fix_rerun"),
                eid("camera_ready"),
                eid("license_clause"),
            ],
            relation_kinds={
                eid("fix_rerun"): "derived_from",
                eid("camera_ready"): "supersedes",
                eid("license_clause"): "enables",
            },
        ),
    ]
    if project.get("process", {}).get("invalidate"):
        evs.append(
            Event(
                id=eid("invalidate_run"),
                type="invalidate_run",
                time=_date(start, 7, 3),
                params={"dataset": project["dataset_v2"]},
                visibility=[f"{prefix}.dataset_card"],
                causal_inputs=[eid("report_v1")],
                relation_kinds={eid("report_v1"): "supersedes"},
            )
        )
    evs.extend(
        [
            Event(
                id=eid("submit_revision_2"),
                type="submit_revision_2",
                time=_date(start, 8, 5),
                params={
                    "revision": project["revision_v2"],
                    "benchmark": project["benchmark_v2"],
                },
                visibility=[f"{prefix}.revision_2"],
                causal_inputs=[eid("camera_ready"), eid("fix_rerun")],
                relation_kinds={
                    eid("camera_ready"): "supersedes",
                    eid("fix_rerun"): "derived_from",
                },
            ),
            Event(
                id=eid("review_round_1"),
                type="review_round_1",
                time=_date(start, 9, 8),
                params={"requirement": project["review_round1"]},
                visibility=[f"{prefix}.review_1"],
                causal_inputs=[eid("submit_revision_2")],
                required_inputs=[eid("submit_revision_2")],
                relation_kinds={eid("submit_revision_2"): "derived_from"},
            ),
            Event(
                id=eid("respond_round_1"),
                type="respond_round_1",
                time=_date(start, 10, 6),
                params={"response": project["response_round1"]},
                visibility=[f"{prefix}.response_1"],
                causal_inputs=[eid("review_round_1")],
                required_inputs=[eid("review_round_1")],
                relation_kinds={eid("review_round_1"): "derived_from"},
            ),
            Event(
                id=eid("reproduction_failure"),
                type="reproduction_failure",
                time=_date(start, 11, 9),
                params={
                    "run": project["reproduction_failure"],
                    "benchmark": project["benchmark_v2"],
                },
                visibility=[f"{prefix}.reproduction_failure"],
                causal_inputs=[eid("respond_round_1")],
                required_inputs=[eid("respond_round_1")],
                relation_kinds={eid("respond_round_1"): "contradicts"},
            ),
            Event(
                id=eid("benchmark_patch"),
                type="benchmark_patch",
                time=_date(start, 13, 4),
                params={
                    "benchmark": project["benchmark_v3"],
                    "commit": project["benchmark_patch_commit"],
                },
                visibility=[f"{prefix}.benchmark_patch"],
                causal_inputs=[eid("reproduction_failure")],
                required_inputs=[eid("reproduction_failure")],
                relation_kinds={eid("reproduction_failure"): "supersedes"},
            ),
            Event(
                id=eid("review_round_2"),
                type="review_round_2",
                time=_date(start, 14, 7),
                params={"requirement": project["review_round2"]},
                visibility=[f"{prefix}.review_2"],
                causal_inputs=[eid("review_round_1"), eid("benchmark_patch")],
                required_inputs=[eid("benchmark_patch")],
                relation_kinds={
                    eid("review_round_1"): "supersedes",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
            Event(
                id=eid("respond_round_2"),
                type="respond_round_2",
                time=_date(start, 15, 9),
                params={"response": project["response_round2"]},
                visibility=[f"{prefix}.response_2"],
                causal_inputs=[eid("review_round_2"), eid("respond_round_1")],
                required_inputs=[eid("review_round_2")],
                relation_kinds={
                    eid("review_round_2"): "derived_from",
                    eid("respond_round_1"): "supersedes",
                },
            ),
            Event(
                id=eid("reproduction_recovery"),
                type="reproduction_recovery",
                time=_date(start, 17, 3),
                params={
                    "run": project["reproduction_success"],
                    "benchmark": project["benchmark_v3"],
                },
                visibility=[f"{prefix}.reproduction_recovery"],
                causal_inputs=[eid("respond_round_2"), eid("benchmark_patch")],
                required_inputs=[eid("respond_round_2"), eid("benchmark_patch")],
                relation_kinds={
                    eid("respond_round_2"): "enables",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
            Event(
                id=eid("submit_revision_3"),
                type="submit_revision_3",
                time=_date(start, 18, 6),
                params={"revision": project["revision_v3"]},
                visibility=[f"{prefix}.revision_3"],
                causal_inputs=[
                    eid("respond_round_2"),
                    eid("reproduction_recovery"),
                ],
                required_inputs=[eid("reproduction_recovery")],
                relation_kinds={
                    eid("respond_round_2"): "derived_from",
                    eid("reproduction_recovery"): "enables",
                },
            ),
            Event(
                id=eid("resolve_review"),
                type="resolve_review",
                time=_date(start, 19, 5),
                params={},
                visibility=[f"{prefix}.review_resolution"],
                causal_inputs=[
                    eid("review_round_2"),
                    eid("respond_round_2"),
                    eid("submit_revision_3"),
                ],
                required_inputs=[eid("respond_round_2"), eid("submit_revision_3")],
                relation_kinds={
                    eid("review_round_2"): "derived_from",
                    eid("respond_round_2"): "derived_from",
                    eid("submit_revision_3"): "enables",
                },
            ),
            Event(
                id=eid("meta_decision"),
                type="meta_decision",
                time=_date(start, 20, 8),
                params={"accepted": True},
                visibility=[f"{prefix}.meta_decision"],
                causal_inputs=[
                    eid("resolve_review"),
                    eid("respond_round_2"),
                    eid("reproduction_recovery"),
                    eid("benchmark_patch"),
                ],
                required_inputs=[
                    eid("resolve_review"),
                    eid("reproduction_recovery"),
                ],
                relation_kinds={
                    eid("resolve_review"): "enables",
                    eid("respond_round_2"): "derived_from",
                    eid("reproduction_recovery"): "enables",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
        ]
    )
    evs.extend(
        grounded_events(
            project,
            prefix,
            start,
            ingest_off=4,
            alt_off=6,
            adopt_off=62,
        )
    )
    evs.extend(
        cascade_events(
            project,
            prefix,
            start,
            seed_off=12,
            ack_off=95,
            reopen_off=248,
            ratify_off=270,
        )
    )
    evs.extend(_experiment_events(project, prefix, start))
    evs.extend(_source_workflow_events(project, prefix))
    blockers = (
        "GPU quota",
        "reviewer latency",
        "dataset mirror",
        "license check",
        "plot regeneration",
    )
    n_pulses = int(project.get("n_pulses", 10))
    for i in range(n_pulses):
        evs.append(
            Event(
                id=eid(f"status_pulse_{i}"),
                type="status_pulse",
                time=_date(start, 0, 9 + i * 11),
                params={
                    "week_index": i + 1,
                    "blocker": blockers[i % len(blockers)],
                    "ticket": f"RL-{pid[:4].upper()}-{200 + i}",
                },
                visibility=[f"{prefix}.status_pulse_{i}"],
                causal_inputs=[eid("report_v1")],
                relation_kinds={eid("report_v1"): "observed_in"},
            )
        )
    evs.sort(key=lambda e: (e.time, e.id))
    _ = pid
    return evs


def simulate_lab(spec: dict[str, Any]) -> dict[str, SimulatedWorld]:
    worlds: dict[str, SimulatedWorld] = {}
    projects = [("focal", spec["focal"])] + [
        (f"par{i}", p) for i, p in enumerate(spec["parallels"])
    ]
    for prefix, project in projects:
        evs = events_for_lab(project, prefix)
        sim = WorldSimulator(
            spec={
                **spec,
                "world_id": f"{spec['world_id']}:{prefix}",
                "project": project,
                "domain": "researchlab",
            },
            init_values=init_values(project, canonical_source_events=evs),
            check_preconditions=check_preconditions,
            apply_event=apply_event,
        )
        worlds[prefix] = sim.run(evs)
        worlds[prefix].spec["project"] = project
        worlds[prefix].spec["prefix"] = prefix
        worlds[prefix].spec["domain"] = "researchlab"
    return worlds
