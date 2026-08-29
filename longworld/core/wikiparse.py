"""Exact Wikipedia/Wikidata claim views over authentic revision bodies."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from longworld.core.provenance import ProvenanceError

WIKI_CLAIM_REVISION = "wiki-claim-program-v1"
WIKI_SECTION_REVISION = "wiki-heading-section-v1"
WIKI_SECTION_VIEW_PREFIX = "Wikipedia source section\n"
WIKI_ENTITY_VIEW_PREFIX = "Wikidata entity section\n"
WIKI_HYBRID_CHILD_EVENT_TYPES = frozenset({"wiki_claim_answer"})
WIKI_FACT_PARSER_REVISION_V1 = "researchlab-wiki-claim-exact-v1"
WIKI_FACT_PARSER_REVISION_V2 = "researchlab-wiki-claim-exact-v2"
WIKI_COMMEMORATION_QUOTE = "BCSWomen Lovelace Colloquium"
WIKI_POPULAR_CULTURE_QUOTE = "The Difference Engine"
WIKI_TURING_MID_QUOTE = "The petition received more than 30,000 signatures"
WIKI_TURING_LATE_QUOTE = "Oral history interview with Nicholas C. Metropolis"
WIKI_CHURCHILL_MID_QUOTE = "We shall fight on the beaches"
WIKI_CHURCHILL_LATE_QUOTE = "On the 8th, Churchill declared war on Japan"
WIKI_EINSTEIN_MID_QUOTE = "Russell–Einstein Manifesto"
WIKI_EINSTEIN_LATE_QUOTE = "Einstein–Podolsky–Rosen paradox"
WIKI_THATCHER_MID_QUOTE = "The lady's not for turning"
WIKI_THATCHER_LATE_QUOTE = "Westland affair"
WIKI_NEWTON_MID_QUOTE = "Hypothesis of Light"
WIKI_NEWTON_LATE_QUOTE = "William Chaloner"
WIKI_OBAMA_MID_QUOTE = "The couple's first daughter, Malia Ann"
WIKI_OBAMA_LATE_QUOTE = "Obama Chooses Biden"
WIKI_ELIZABETH_MID_QUOTE = "Jallianwala Bagh massacre"
WIKI_ELIZABETH_LATE_QUOTE = "death of Diana"
WIKI_MLK_MID_QUOTE = "oratorical preaching in Montgomery"
WIKI_MLK_LATE_QUOTE = "I've Been To The Mountaintop"
WIKI_JEFFERSON_MID_QUOTE = "Corps of Discovery"
WIKI_JEFFERSON_LATE_QUOTE = "Autobiography of Thomas Jefferson: 1743–1790"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_BIRTH_QUOTE_CHARS = 240
_H2 = re.compile(r"^(==)([^=].*?[^=])\1[^\S\n]*$", re.MULTILINE)
_SECTION_HEADING = re.compile(r"^(={2,6})([^=].*?[^=])\1[^\S\n]*$", re.MULTILINE)
_REFERENCES_HEADING = re.compile(r"^==\s*References\s*==[^\S\n]*$", re.MULTILINE)
_BIRTH = re.compile(
    r"\{\{\s*birth date(?: and age)?\s*\|(?:df=y(?:es)?\|)?"
    r"(\d{4})\|(\d{1,2})\|(\d{1,2})[^}]*\}\}",
    re.IGNORECASE,
)
_ENTITY_ID = re.compile(r"^Q[1-9][0-9]{0,11}$")


@dataclass(frozen=True)
class WikiClaimFact:
    role: str
    evidence_quote: str
    char_start: int
    char_end: int
    value: str
    parent_sha256: str
    answer_tag: str = ""

    def to_span(self, *, shift: int = 0) -> dict[str, object]:
        span = {
            "kind": "wiki_claim",
            "role": self.role,
            "evidence_quote": self.evidence_quote,
            "char_start": self.char_start + shift,
            "char_end": self.char_end + shift,
            "value": self.value,
            "parent_sha256": self.parent_sha256,
        }
        if self.answer_tag:
            span["answer_tag"] = self.answer_tag
        return span


@dataclass(frozen=True)
class WikiSection:
    section_id: str
    heading: str
    wikitext: str
    section_sha256: str
    parent_sha256: str
    provenance_id: str
    facts: tuple[WikiClaimFact, ...]
    ground_value: str
    minimum_tier: str = "16k"


@dataclass(frozen=True)
class WikiClaimProgram:
    revision_id: str
    entity_id: str
    title: str
    sections: tuple[WikiSection, ...]
    fact_parser_revision: str = WIKI_FACT_PARSER_REVISION_V1


def _require_hash(value: str, *, label: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ProvenanceError(f"{label} is not a sha256 digest")
    return value


def _unique_quote(text: str, quote: str, *, label: str) -> tuple[int, int]:
    if not quote or text.count(quote) != 1:
        raise ProvenanceError(f"{label} is not unique in its Wikipedia section")
    start = text.index(quote)
    return start, start + len(quote)


def extract_wikipedia_wikitext(record_text: str) -> tuple[str, str, str]:
    """Return title, Wikidata QID, and decoded wikitext from one API revision."""
    try:
        payload = json.loads(record_text)
    except json.JSONDecodeError as error:
        raise ProvenanceError(
            "Wikipedia source record is not canonical JSON"
        ) from error
    query = payload.get("query") if isinstance(payload, dict) else None
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
        raise ProvenanceError("Wikipedia source record does not contain one page")
    page = pages[0]
    revisions = page.get("revisions")
    if not isinstance(revisions, list) or len(revisions) != 1:
        raise ProvenanceError("Wikipedia source record does not contain one revision")
    revision = revisions[0]
    slots = revision.get("slots") if isinstance(revision, dict) else None
    main = slots.get("main") if isinstance(slots, dict) else None
    wikitext = main.get("content") if isinstance(main, dict) else None
    title = str(page.get("title") or "").strip()
    raw_props = page.get("pageprops")
    props = raw_props if isinstance(raw_props, dict) else {}
    entity_id = str(props.get("wikibase_item") or "").strip()
    revision_id = str(revision.get("revid") or "").strip()
    if (
        not isinstance(wikitext, str)
        or not wikitext.strip()
        or not title
        or _ENTITY_ID.fullmatch(entity_id) is None
        or not revision_id
    ):
        raise ProvenanceError("Wikipedia revision body is incomplete")
    return title, entity_id, wikitext


def extract_wikidata_entity_id(record_text: str) -> str:
    try:
        payload = json.loads(record_text)
    except json.JSONDecodeError as error:
        raise ProvenanceError("Wikidata source record is not canonical JSON") from error
    entities = payload.get("entities") if isinstance(payload, dict) else None
    if not isinstance(entities, dict) or len(entities) != 1:
        raise ProvenanceError("Wikidata source record does not contain one entity")
    entity_id, entity = next(iter(entities.items()))
    if (
        _ENTITY_ID.fullmatch(str(entity_id)) is None
        or not isinstance(entity, dict)
        or entity.get("id") != entity_id
        or payload.get("success") != 1
    ):
        raise ProvenanceError("Wikidata entity identity is invalid")
    quote = json.dumps({"id": entity_id}, separators=(",", ":"))[1:-1]
    if record_text.count(quote) != 1:
        raise ProvenanceError("Wikidata entity id is not unique in the entity body")
    return str(entity_id)


def _h2_spans(wikitext: str) -> tuple[tuple[str, int, int], ...]:
    matches = list(_H2.finditer(wikitext))
    if not matches:
        raise ProvenanceError("Wikipedia revision has no level-2 headings")
    spans: list[tuple[str, int, int]] = [("lead", 0, matches[0].start())]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(wikitext)
        heading = match.group(2).strip()
        if not heading:
            raise ProvenanceError("Wikipedia heading is empty")
        spans.append((heading, match.start(), end))
    if spans[0][2] <= 0:
        raise ProvenanceError("Wikipedia lead section is empty")
    reconstructed = "".join(wikitext[start:end] for _heading, start, end in spans)
    if reconstructed != wikitext:
        raise ProvenanceError("Wikipedia heading sections do not cover the revision")
    return tuple(spans)


def _heading_start(spans: tuple[tuple[str, int, int], ...], *, prefix: str) -> int:
    found = [start for heading, start, _end in spans if heading.startswith(prefix)]
    if len(found) != 1:
        raise ProvenanceError(f"Wikipedia heading {prefix!r} is not unique")
    return found[0]


def _unique_marker_start(text: str, marker: str, *, label: str) -> int:
    if text.count(marker) != 1:
        raise ProvenanceError(f"Wikipedia heading {label!r} is not unique")
    return text.index(marker)


def _section(
    *,
    section_id: str,
    heading: str,
    wikitext: str,
    parent_sha256: str,
    facts: tuple[WikiClaimFact, ...],
    ground_value: str,
    minimum_tier: str = "16k",
) -> WikiSection:
    if not wikitext:
        raise ProvenanceError(f"Wikipedia section {section_id} is empty")
    if not ground_value or (
        section_id != "wikidata_entity" and ground_value not in wikitext
    ):
        raise ProvenanceError(
            f"Wikipedia section {section_id} ground heading is missing"
        )
    if minimum_tier not in {"16k", "32k", "64k"}:
        raise ProvenanceError(f"Wikipedia section {section_id} tier is invalid")
    for fact in facts:
        if wikitext[fact.char_start : fact.char_end] != fact.evidence_quote:
            raise ProvenanceError(f"Wikipedia fact {fact.role} is not lossless")
        if fact.parent_sha256 != parent_sha256:
            raise ProvenanceError(f"Wikipedia fact {fact.role} parent hash mismatches")
    digest = hashlib.sha256(wikitext.encode()).hexdigest()
    provenance = hashlib.sha256(
        json.dumps(
            {
                "operation": WIKI_SECTION_REVISION,
                "section_id": section_id,
                "parent_sha256": parent_sha256,
                "section_sha256": digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return WikiSection(
        section_id=section_id,
        heading=heading,
        wikitext=wikitext,
        section_sha256=digest,
        parent_sha256=parent_sha256,
        provenance_id=f"derived-sha256:{provenance}",
        facts=facts,
        ground_value=ground_value,
        minimum_tier=minimum_tier,
    )


def _quote_is_unique(section_text: str, full_text: str, quote: str) -> bool:
    return (
        bool(quote) and section_text.count(quote) == 1 and full_text.count(quote) == 1
    )


def _unique_span_containing(
    section_text: str, full_text: str, start: int, end: int
) -> tuple[int, int]:
    """Grow a duplicated template until the evidence quote is unique."""
    quote = section_text[start:end]
    if _quote_is_unique(section_text, full_text, quote):
        return start, end
    left = start
    while left > 0 and (end - left) < _MAX_BIRTH_QUOTE_CHARS:
        left -= 1
        quote = section_text[left:end]
        if _quote_is_unique(section_text, full_text, quote):
            return left, end
    right = end
    while right < len(section_text) and (right - start) < _MAX_BIRTH_QUOTE_CHARS:
        right += 1
        quote = section_text[start:right]
        if _quote_is_unique(section_text, full_text, quote):
            return start, right
    left, right = start, end
    while (right - left) < _MAX_BIRTH_QUOTE_CHARS and (
        left > 0 or right < len(section_text)
    ):
        if left > 0:
            left -= 1
        if right < len(section_text):
            right += 1
        quote = section_text[left:right]
        if _quote_is_unique(section_text, full_text, quote):
            return left, right
    raise ProvenanceError("Wikipedia birth template is not unique")


def _birth_fact(
    section_text: str,
    full_text: str,
    parent_sha256: str,
    *,
    answer_tag: str = "",
) -> WikiClaimFact:
    matches = list(_BIRTH.finditer(section_text))
    if not matches:
        raise ProvenanceError("Wikipedia birth template is not unique")
    valid: list[tuple[re.Match[str], int, int, int]] = []
    for match in matches:
        year, month, day = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        if 1 <= month <= 12 and 1 <= day <= 31:
            valid.append((match, year, month, day))
    if not valid:
        raise ProvenanceError("Wikipedia birth template is invalid")
    for match, year, month, day in valid:
        try:
            start, end = _unique_span_containing(
                section_text, full_text, match.start(), match.end()
            )
        except ProvenanceError:
            continue
        quote = section_text[start:end]
        year_text = f"{year:04d}"
        if quote.count(year_text) != 1:
            continue
        return WikiClaimFact(
            role="born",
            answer_tag=answer_tag,
            evidence_quote=quote,
            char_start=start,
            char_end=end,
            value=f"{year:04d}-{month:02d}-{day:02d}",
            parent_sha256=parent_sha256,
        )
    raise ProvenanceError("Wikipedia birth template is not unique")


def _literal_fact(
    section_text: str,
    full_text: str,
    *,
    role: str,
    answer_tag: str,
    quote: str,
    parent_sha256: str,
) -> WikiClaimFact:
    if full_text.count(quote) != 1:
        raise ProvenanceError(f"Wikipedia {role} quote is not globally unique")
    start, end = _unique_quote(section_text, quote, label=role)
    return WikiClaimFact(
        role=role,
        answer_tag=answer_tag,
        evidence_quote=quote,
        char_start=start,
        char_end=end,
        value=quote,
        parent_sha256=parent_sha256,
    )


def _entity_section(
    entity_text: str, entity_id: str, entity_hash: str, *, answer_tag: str = ""
) -> WikiSection:
    entity_quote = json.dumps({"id": entity_id}, separators=(",", ":"))[1:-1]
    entity_start, entity_end = _unique_quote(
        entity_text, entity_quote, label="entity_id"
    )
    return _section(
        section_id="wikidata_entity",
        heading="Wikidata entity",
        wikitext=entity_text,
        parent_sha256=entity_hash,
        facts=(
            WikiClaimFact(
                role="entity",
                answer_tag=answer_tag,
                evidence_quote=entity_quote,
                char_start=entity_start,
                char_end=entity_end,
                value=entity_id,
                parent_sha256=entity_hash,
            ),
        ),
        ground_value="Wikidata entity section",
    )


def _leftover_ground(rest: str) -> str:
    match = _REFERENCES_HEADING.search(rest)
    if match is not None:
        return match.group(0).rstrip()
    heading = _SECTION_HEADING.search(rest)
    if heading is not None:
        return heading.group(0).rstrip()
    raise ProvenanceError("Wikipedia leftover rest is missing a heading")


def _staged_sections(
    *,
    wikitext: str,
    wiki_hash: str,
    early: str,
    middle: str,
    late: str,
    early_heading: str,
    early_ground: str,
    middle_heading: str,
    middle_ground: str,
    middle_quote: str,
    middle_role: str,
    middle_tag: str,
    late_heading: str,
    late_ground: str,
    late_quote: str,
    late_role: str,
    late_tag: str,
    entity_text: str,
    entity_id: str,
    entity_hash: str,
    rest: str = "",
    rest_ground: str = "",
    early_quote: str = "",
    early_segments: tuple[tuple[str, ...], ...] = (),
    semantic_tags: bool = False,
) -> tuple[WikiSection, ...]:
    if not (early and middle and late):
        raise ProvenanceError("Wikipedia claim sections overlap or are empty")
    if middle_quote in early or late_quote in early:
        raise ProvenanceError("Wikipedia later-tier claims leaked into the 16K section")
    if late_quote in middle:
        raise ProvenanceError("Wikipedia 64K claim leaked into the 32K section")
    sections: list[WikiSection] = []
    if early_segments:
        starts = [0]
        for segment_spec in early_segments[1:]:
            marker = segment_spec[1]
            starts.append(_unique_quote(early, marker, label="early segment")[0])
        if starts != sorted(starts) or len(starts) != len(early_segments):
            raise ProvenanceError("Wikipedia early segments are not chronological")
        for index, segment_spec in enumerate(early_segments):
            if len(segment_spec) == 4:
                section_id, marker, role, quote = segment_spec
                tag = ""
            elif len(segment_spec) == 5:
                section_id, marker, role, tag, quote = segment_spec
                minimum_tier = "16k"
            elif len(segment_spec) == 6:
                section_id, marker, role, tag, quote, minimum_tier = segment_spec
            else:
                raise ProvenanceError("Wikipedia early segment spec is invalid")
            if len(segment_spec) == 4:
                minimum_tier = "16k"
            end = starts[index + 1] if index + 1 < len(starts) else None
            segment = early[starts[index] : end]
            fact = (
                _birth_fact(
                    segment,
                    wikitext,
                    wiki_hash,
                    answer_tag=tag if semantic_tags else "",
                )
                if role == "born"
                else _literal_fact(
                    segment,
                    wikitext,
                    role=role,
                    answer_tag=tag if semantic_tags else "",
                    quote=quote,
                    parent_sha256=wiki_hash,
                )
            )
            sections.append(
                _section(
                    section_id=section_id,
                    heading=section_id.replace("_", " "),
                    wikitext=segment,
                    parent_sha256=wiki_hash,
                    facts=(fact,),
                    ground_value=early_ground if index == 0 else marker,
                    minimum_tier=minimum_tier,
                )
            )
    else:
        early_facts = [
            _birth_fact(
                early,
                wikitext,
                wiki_hash,
                answer_tag="BORN" if semantic_tags else "",
            )
        ]
        if early_quote:
            early_facts.append(
                _literal_fact(
                    early,
                    wikitext,
                    role="early_transition",
                    answer_tag="EARLY" if semantic_tags else "",
                    quote=early_quote,
                    parent_sha256=wiki_hash,
                )
            )
        sections.append(
            _section(
                section_id="early_work",
                heading=early_heading,
                wikitext=early,
                parent_sha256=wiki_hash,
                facts=tuple(early_facts),
                ground_value=early_ground,
            )
        )
    sections.extend(
        [
            _section(
                section_id="commemoration",
                heading=middle_heading,
                wikitext=middle,
                parent_sha256=wiki_hash,
                facts=(
                    _literal_fact(
                        middle,
                        wikitext,
                        role=middle_role,
                        answer_tag=middle_tag if semantic_tags else "",
                        quote=middle_quote,
                        parent_sha256=wiki_hash,
                    ),
                ),
                ground_value=middle_ground,
            ),
            _section(
                section_id="popular_culture",
                heading=late_heading,
                wikitext=late,
                parent_sha256=wiki_hash,
                facts=(
                    _literal_fact(
                        late,
                        wikitext,
                        role=late_role,
                        answer_tag=late_tag if semantic_tags else "",
                        quote=late_quote,
                        parent_sha256=wiki_hash,
                    ),
                ),
                ground_value=late_ground,
            ),
        ]
    )
    if rest:
        sections.append(
            _section(
                section_id="appendix_rest",
                heading="References leftover",
                wikitext=rest,
                parent_sha256=wiki_hash,
                facts=(),
                ground_value=rest_ground or _leftover_ground(rest),
            )
        )
    sections.append(
        _entity_section(
            entity_text,
            entity_id,
            entity_hash,
            answer_tag="ENTITY" if semantic_tags else "",
        )
    )
    return tuple(sections)


@dataclass(frozen=True)
class _WikiTitleCuts:
    middle: str
    late: str
    early_heading: str
    early_ground: str
    middle_heading: str
    middle_ground: str
    middle_quote: str
    late_heading: str
    late_ground: str
    late_quote: str
    middle_role: str = "commemoration"
    middle_tag: str = ""
    late_role: str = "popular_culture"
    late_tag: str = ""
    tail: str | None = None
    rest_end: str | None = None
    rest_ground: str = ""
    early_quote: str = ""
    early_segments: tuple[tuple[str, ...], ...] = ()
    fact_parser_revision: str = WIKI_FACT_PARSER_REVISION_V1


_WIKI_TITLE_PROGRAMS = {
    "Ada Lovelace": _WikiTitleCuts(
        middle="Commemoration",
        late="In popular culture",
        early_heading="lead+Biography+Work",
        early_ground="==Biography==",
        middle_heading="Commemoration",
        middle_ground="==Commemoration",
        middle_quote=WIKI_COMMEMORATION_QUOTE,
        late_heading="In popular culture",
        late_ground="==In popular culture==",
        late_quote=WIKI_POPULAR_CULTURE_QUOTE,
    ),
    "Alan Turing": _WikiTitleCuts(
        middle="Personal life",
        late="References",
        early_heading="lead+Early life+Career",
        early_ground="== Career and research ==",
        middle_heading="Personal life through See also",
        middle_ground="== Government apology and pardon ==",
        middle_quote=WIKI_TURING_MID_QUOTE,
        late_heading="References and External links",
        late_ground="== External links ==",
        late_quote=WIKI_TURING_LATE_QUOTE,
    ),
    "Winston Churchill": _WikiTitleCuts(
        middle="Military service",
        late="===Pearl Harbor to D-Day: December 1941 to June 1944===",
        early_heading="lead+Early life+Asquith",
        early_ground="==Asquith government: 1908–1915==",
        middle_heading="Military service through Dunkirk",
        middle_ground="==Military service, 1915–1916==",
        middle_quote=WIKI_CHURCHILL_MID_QUOTE,
        late_heading="Pearl Harbor to External links",
        late_ground="===Pearl Harbor to D-Day: December 1941 to June 1944===",
        late_quote=WIKI_CHURCHILL_LATE_QUOTE,
    ),
    "Albert Einstein": _WikiTitleCuts(
        middle="==== Resident scholar at the Institute for Advanced Study ====",
        late="=== Quantum mechanics ===",
        early_heading="lead+Life through 1932",
        early_ground="== Life and career ==",
        middle_heading="Resident scholar through old quantum theory",
        middle_ground="==== Resident scholar at the Institute for Advanced Study ====",
        middle_quote=WIKI_EINSTEIN_MID_QUOTE,
        middle_tag="COMM",
        late_heading="Quantum mechanics through Notes",
        late_ground="=== Quantum mechanics ===",
        late_quote=WIKI_EINSTEIN_LATE_QUOTE,
        late_tag="POP",
        tail="== References ==",
        rest_end='<ref name="ILjYQ">',
        early_segments=(
            ("early_birth", "", "born", "BORN", "", "16k"),
            (
                "early_patent",
                "=== Assistant at the Swiss Patent Office (1902–1909)===",
                "patent_examiner",
                "PATENT",
                "assistant examiner – level III",
                "16k",
            ),
            (
                "early_academic",
                "=== Academic career in Europe (1908–1933)===",
                "prague_research",
                "PRAGUE",
                "His time in Prague saw him producing eleven research papers.",
                "16k",
            ),
            (
                "early_fame",
                "=== Coming to terms with fame (1921–1923)===",
                "scientific_fame",
                "FAME",
                "the world's first celebrity scientist",
                "64k",
            ),
            (
                "early_refugee",
                "=== Immigration to the US (1933) ===",
                "refugee_status",
                "REFUGEE",
                "Einstein was now without a permanent home",
                "16k",
            ),
        ),
        fact_parser_revision=WIKI_FACT_PARSER_REVISION_V2,
    ),
    "Margaret Thatcher": _WikiTitleCuts(
        middle="literal:Thatcher became Conservative Party leader",
        late="===Environment===",
        early_heading="lead through Education Secretary",
        early_ground="==Early life and education==",
        middle_heading="Opposition through 1979",
        middle_ground="Thatcher became Conservative Party leader",
        middle_quote=WIKI_THATCHER_MID_QUOTE,
        middle_role="opposition_speech",
        middle_tag="OPPOSITION",
        late_heading="Environment through later life",
        late_ground="===Environment===",
        late_quote=WIKI_THATCHER_LATE_QUOTE,
        late_role="westland_affair",
        late_tag="WESTLAND",
        tail="literal:Speaking in Scotland in 2009",
        rest_end="[[Scottish independence]]",
        rest_ground="Speaking in Scotland in 2009",
        early_segments=(
            ("early_birth", "", "born", "BORN", ""),
            (
                "early_school",
                "=== Family and childhood (1925–1943) ===",
                "schooling",
                "SCHOOL",
                "[[Kesteven and Grantham Girls' School]]",
            ),
            (
                "early_oxford",
                "===Oxford (1943–1947)===",
                "oxford_studies",
                "OXFORD",
                "[[Somerville College]]",
            ),
            (
                "early_politics",
                "==Early political career==",
                "early_politics",
                "POLITICS",
                "[[Dartford (UK Parliament constituency)|Dartford]]",
            ),
            (
                "early_opposition",
                "===Leader of the Opposition (1975–1979)===",
                "leadership_election",
                "LEADERSHIP",
                "[[1975 Conservative Party leadership election|defeated Heath]]",
            ),
        ),
        fact_parser_revision=WIKI_FACT_PARSER_REVISION_V2,
    ),
    "Isaac Newton": _WikiTitleCuts(
        middle="literal:From 1670 to 1672, Newton lectured on optics.",
        late="[[William Chaloner]]",
        early_heading="lead through Mathematics",
        early_ground="== Early life ==",
        middle_heading="Optics through Principia",
        middle_ground="From 1670 to 1672, Newton lectured on optics.",
        middle_quote=WIKI_NEWTON_MID_QUOTE,
        middle_role="optics_work",
        middle_tag="OPTICS",
        late_heading="Royal Mint through See also",
        late_ground="[[William Chaloner]]",
        late_quote=WIKI_NEWTON_LATE_QUOTE,
        late_role="mint_prosecution",
        late_tag="MINT",
        tail="== References ==",
        rest_end="=== Alchemy further reading ===",
        early_segments=(
            ("early_birth", "", "born", "BORN", ""),
            (
                "early_school",
                "=== The King's School ===",
                "schooling",
                "SCHOOL",
                "[[The King's School, Grantham|The King's School]]",
            ),
            (
                "early_cambridge",
                "=== University of Cambridge ===",
                "cambridge_studies",
                "CAMBRIDGE",
                "[[Quaestiones quaedam philosophicae|''Quaestiones'']]",
            ),
            (
                "early_mathematics",
                "== Scientific studies ==",
                "mathematics",
                "MATH",
                "[[Binomial theorem#Newton's generalized binomial theorem|generalised binomial theorem]]",
            ),
            (
                "early_optics",
                "=== Optics ===",
                "spectrum_observation",
                "SPECTRUM",
                "prism refracts different colours by different angles",
            ),
        ),
        fact_parser_revision=WIKI_FACT_PARSER_REVISION_V2,
    ),
    "Barack Obama": _WikiTitleCuts(
        middle="literal:While studying in California",
        late="===2004 U.S. Senate campaign in Illinois===",
        early_heading="lead through Education",
        early_ground="==Early life and career==",
        middle_heading="Relationships through Illinois Senate",
        middle_ground="While studying in California",
        middle_quote=WIKI_OBAMA_MID_QUOTE,
        middle_role="first_daughter",
        middle_tag="MALIA",
        late_heading="U.S. Senate campaign through environmental policy",
        late_ground="===2004 U.S. Senate campaign in Illinois===",
        late_quote=WIKI_OBAMA_LATE_QUOTE,
        late_tag="POP",
        tail="====Environmental policy====",
        rest_end="[[The Hill (newspaper)|The Hill]]",
        early_segments=(
            ("early_birth", "", "born", "BORN", "", "16k"),
            (
                "early_degree",
                "===Education===",
                "college_degree",
                "DEGREE",
                "He graduated with a Bachelor of Arts degree in 1983 and a 3.7",
                "16k",
            ),
            (
                "early_organizing",
                "==== Community organizer and Harvard Law School ====",
                "community_organizer",
                "ORGANIZER",
                "hired as director of the [[Developing Communities Project]]",
                "16k",
            ),
            (
                "early_law",
                "In mid-1988, he traveled for the first time",
                "law_school",
                "HARVARD",
                "enrolled at [[Harvard Law School]] in the fall of 1988",
                "32k",
            ),
            (
                "early_family",
                "===Family and personal life===",
                "maternal_grandmother",
                "GRANDMOTHER",
                "Madelyn Payne Dunham",
                "16k",
            ),
        ),
        fact_parser_revision=WIKI_FACT_PARSER_REVISION_V2,
    ),
    "Elizabeth II": _WikiTitleCuts(
        middle="=== Perils and dissent ===",
        late="=== Platinum Jubilee and beyond ===",
        early_heading="lead through early crises",
        early_ground="== Early life ==",
        middle_heading="Perils through Diamond Jubilee",
        middle_ground="=== Perils and dissent ===",
        middle_quote=WIKI_ELIZABETH_MID_QUOTE,
        late_heading="Platinum Jubilee through Notes",
        late_ground="=== Platinum Jubilee and beyond ===",
        late_quote=WIKI_ELIZABETH_LATE_QUOTE,
        tail="==References==",
        rest_end="==External links==",
    ),
    "Martin Luther King Jr.": _WikiTitleCuts(
        middle="literal:The Mary's Cafe sit-in occurred six months prior",
        late="=== Biddeford, Maine, 1964 ===",
        early_heading="lead through Morehouse College",
        early_ground="== Early life and education ==",
        middle_heading="Religious education through March on Washington",
        middle_ground="The Mary's Cafe sit-in occurred six months prior",
        middle_quote=WIKI_MLK_MID_QUOTE,
        middle_role="montgomery_oratory",
        middle_tag="MONTGOMERY",
        late_heading="New York City through assassination aftermath",
        late_ground="=== Biddeford, Maine, 1964 ===",
        late_quote=WIKI_MLK_LATE_QUOTE,
        late_role="final_sermon",
        late_tag="SERMON",
        tail="=== United States ===",
        rest_end="==== ''The Measure of a Man'' ====",
        early_segments=(
            ("early_birth", "", "born", "BORN", ""),
            (
                "early_school",
                "=== Early childhood ===",
                "schooling",
                "SCHOOL",
                "[[Gone with the Wind (film)|Gone with the Wind]]",
            ),
            (
                "early_adolescence",
                "=== Adolescence ===",
                "adolescence_oratory",
                "SPEECH",
                "[[Original Oratory|oratorical contest]]",
            ),
            (
                "early_morehouse",
                "=== Morehouse College ===",
                "college_degree",
                "DEGREE",
                "[[Bachelor of Arts]]",
            ),
            (
                "early_religious",
                "== Religious education ==",
                "divinity_degree",
                "DIVINITY",
                "[[Bachelor of Divinity]]",
            ),
            (
                "early_family",
                "== Marriage and family ==",
                "marriage_date",
                "MARRIAGE",
                "King married Scott on June 18, 1953",
            ),
            (
                "early_sit_in",
                "=== Mary's Cafe Sit-In, 1950 ===",
                "sit_in_commitment",
                "SIT_IN",
                '"a formative step" in his "commitment to a more just society."',
            ),
        ),
        fact_parser_revision=WIKI_FACT_PARSER_REVISION_V2,
    ),
    "Thomas Jefferson": _WikiTitleCuts(
        middle="==Secretary of State==",
        late="===Autobiography===",
        early_heading="lead through diplomatic mission in France",
        early_ground="==Early life and education==",
        middle_heading="Secretary of State through reconciliation with Adams",
        middle_ground="==Secretary of State==",
        middle_quote=WIKI_JEFFERSON_MID_QUOTE,
        late_heading="Autobiography through Legacy",
        late_ground="===Autobiography===",
        late_quote=WIKI_JEFFERSON_LATE_QUOTE,
        tail="==Legacy==",
        rest_end="===Thomas Jefferson Foundation sources===",
        early_segments=(
            ("early_birth", "", "born", ""),
            (
                "early_career",
                "==Career==",
                "early_career",
                "[[House of Burgesses]]",
            ),
            (
                "early_revolution",
                "==Revolutionary War==",
                "revolutionary_committee",
                "[[Committee of Five]]",
            ),
            (
                "early_diplomacy",
                "==Member of Congress==",
                "early_transition",
                "[[Mather Brown]]",
            ),
        ),
    ),
}


def _cut_index(
    wikitext: str, spans: tuple[tuple[str, int, int], ...], spec: str
) -> int:
    if spec.startswith("literal:"):
        marker = spec.removeprefix("literal:")
        return _unique_marker_start(wikitext, marker, label=marker)
    if spec.startswith(("=", "[[")):
        return _unique_marker_start(wikitext, spec, label=spec)
    return _heading_start(spans, prefix=spec)


def parse_wiki_claim_program(
    *,
    wikipedia_text: str,
    entity_text: str,
    wikipedia_parent_hash: str,
    entity_parent_hash: str,
) -> WikiClaimProgram:
    """Fail-closed staged claims bound to one title's unique headings and quotes."""
    wiki_hash = _require_hash(wikipedia_parent_hash, label="wikipedia parent hash")
    entity_hash = _require_hash(entity_parent_hash, label="entity parent hash")
    if hashlib.sha256(wikipedia_text.encode()).hexdigest() != wiki_hash:
        raise ProvenanceError("Wikipedia parent hash does not match record text")
    if hashlib.sha256(entity_text.encode()).hexdigest() != entity_hash:
        raise ProvenanceError("Wikidata parent hash does not match record text")
    title, page_entity_id, wikitext = extract_wikipedia_wikitext(wikipedia_text)
    entity_id = extract_wikidata_entity_id(entity_text)
    if entity_id != page_entity_id:
        raise ProvenanceError("Wikipedia pageprops QID does not match Wikidata entity")
    cuts = _WIKI_TITLE_PROGRAMS.get(title)
    if cuts is None:
        raise ProvenanceError("Wikipedia heading program is not defined for this title")
    spans = _h2_spans(wikitext)
    middle_start = _cut_index(wikitext, spans, cuts.middle)
    late_start = _cut_index(wikitext, spans, cuts.late)
    late_end = len(wikitext)
    if cuts.tail:
        late_end = _cut_index(wikitext, spans, cuts.tail)
    if not (0 < middle_start < late_start < late_end <= len(wikitext)):
        raise ProvenanceError("Wikipedia claim sections overlap or are empty")
    rest = ""
    if cuts.rest_end:
        rest_end = _unique_marker_start(
            wikitext, cuts.rest_end, label="leftover rest end"
        )
        if not (late_end < rest_end <= len(wikitext)):
            raise ProvenanceError("Wikipedia leftover rest is empty or overlapping")
        rest = wikitext[late_end:rest_end]
    sections = _staged_sections(
        wikitext=wikitext,
        wiki_hash=wiki_hash,
        early=wikitext[:middle_start],
        middle=wikitext[middle_start:late_start],
        late=wikitext[late_start:late_end],
        early_heading=cuts.early_heading,
        early_ground=cuts.early_ground,
        middle_heading=cuts.middle_heading,
        middle_ground=cuts.middle_ground,
        middle_quote=cuts.middle_quote,
        middle_role=cuts.middle_role,
        middle_tag=cuts.middle_tag,
        late_heading=cuts.late_heading,
        late_ground=cuts.late_ground,
        late_quote=cuts.late_quote,
        late_role=cuts.late_role,
        late_tag=cuts.late_tag,
        entity_text=entity_text,
        entity_id=entity_id,
        entity_hash=entity_hash,
        rest=rest,
        rest_ground=cuts.rest_ground,
        early_quote=cuts.early_quote,
        early_segments=cuts.early_segments,
        semantic_tags=(cuts.fact_parser_revision == WIKI_FACT_PARSER_REVISION_V2),
    )
    payload = json.loads(wikipedia_text)
    revision_id = str(payload["query"]["pages"][0]["revisions"][0]["revid"])
    return WikiClaimProgram(
        revision_id=revision_id,
        entity_id=entity_id,
        title=title,
        sections=sections,
        fact_parser_revision=cuts.fact_parser_revision,
    )
