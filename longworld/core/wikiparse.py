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
WIKI_OBAMA_MID_QUOTE = "Madelyn Payne Dunham"
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

    def to_span(self, *, shift: int = 0) -> dict[str, object]:
        return {
            "kind": "wiki_claim",
            "role": self.role,
            "evidence_quote": self.evidence_quote,
            "char_start": self.char_start + shift,
            "char_end": self.char_end + shift,
            "value": self.value,
            "parent_sha256": self.parent_sha256,
        }


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


@dataclass(frozen=True)
class WikiClaimProgram:
    revision_id: str
    entity_id: str
    title: str
    sections: tuple[WikiSection, ...]


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
    props = page.get("pageprops") if isinstance(page.get("pageprops"), dict) else {}
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
) -> WikiSection:
    if not wikitext:
        raise ProvenanceError(f"Wikipedia section {section_id} is empty")
    if not ground_value or (
        section_id != "wikidata_entity" and ground_value not in wikitext
    ):
        raise ProvenanceError(
            f"Wikipedia section {section_id} ground heading is missing"
        )
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


def _birth_fact(section_text: str, full_text: str, parent_sha256: str) -> WikiClaimFact:
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
    quote: str,
    parent_sha256: str,
) -> WikiClaimFact:
    if full_text.count(quote) != 1:
        raise ProvenanceError(f"Wikipedia {role} quote is not globally unique")
    start, end = _unique_quote(section_text, quote, label=role)
    return WikiClaimFact(
        role=role,
        evidence_quote=quote,
        char_start=start,
        char_end=end,
        value=quote,
        parent_sha256=parent_sha256,
    )


def _entity_section(entity_text: str, entity_id: str, entity_hash: str) -> WikiSection:
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
    late_heading: str,
    late_ground: str,
    late_quote: str,
    entity_text: str,
    entity_id: str,
    entity_hash: str,
    rest: str = "",
) -> tuple[WikiSection, ...]:
    if not (early and middle and late):
        raise ProvenanceError("Wikipedia claim sections overlap or are empty")
    if middle_quote in early or late_quote in early:
        raise ProvenanceError("Wikipedia later-tier claims leaked into the 16K section")
    if late_quote in middle:
        raise ProvenanceError("Wikipedia 64K claim leaked into the 32K section")
    sections = [
        _section(
            section_id="early_work",
            heading=early_heading,
            wikitext=early,
            parent_sha256=wiki_hash,
            facts=(_birth_fact(early, wikitext, wiki_hash),),
            ground_value=early_ground,
        ),
        _section(
            section_id="commemoration",
            heading=middle_heading,
            wikitext=middle,
            parent_sha256=wiki_hash,
            facts=(
                _literal_fact(
                    middle,
                    wikitext,
                    role="commemoration",
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
                    role="popular_culture",
                    quote=late_quote,
                    parent_sha256=wiki_hash,
                ),
            ),
            ground_value=late_ground,
        ),
    ]
    if rest:
        sections.append(
            _section(
                section_id="appendix_rest",
                heading="References leftover",
                wikitext=rest,
                parent_sha256=wiki_hash,
                facts=(),
                ground_value=_leftover_ground(rest),
            )
        )
    sections.append(_entity_section(entity_text, entity_id, entity_hash))
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
    tail: str | None = None
    rest_end: str | None = None


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
        middle="=== Immigration to the US (1933) ===",
        late="=== Quantum mechanics ===",
        early_heading="lead+Life through 1932",
        early_ground="== Life and career ==",
        middle_heading="US immigration through old quantum theory",
        middle_ground="=== Immigration to the US (1933) ===",
        middle_quote=WIKI_EINSTEIN_MID_QUOTE,
        late_heading="Quantum mechanics through Notes",
        late_ground="=== Quantum mechanics ===",
        late_quote=WIKI_EINSTEIN_LATE_QUOTE,
        tail="== References ==",
        rest_end='<ref name="ILjYQ">',
    ),
    "Margaret Thatcher": _WikiTitleCuts(
        middle="===Leader of the Opposition (1975–1979)===",
        late="===Environment===",
        early_heading="lead through Education Secretary",
        early_ground="==Early political career==",
        middle_heading="Opposition through 1979",
        middle_ground="===Leader of the Opposition (1975–1979)===",
        middle_quote=WIKI_THATCHER_MID_QUOTE,
        late_heading="Environment through later life",
        late_ground="===Environment===",
        late_quote=WIKI_THATCHER_LATE_QUOTE,
        tail="==Legacy==",
        rest_end="[[Scottish independence]]",
    ),
    "Isaac Newton": _WikiTitleCuts(
        middle="=== Optics ===",
        late="=== Royal Mint ===",
        early_heading="lead through Mathematics",
        early_ground="== Scientific studies ==",
        middle_heading="Optics through Principia",
        middle_ground="=== Optics ===",
        middle_quote=WIKI_NEWTON_MID_QUOTE,
        late_heading="Royal Mint through See also",
        late_ground="=== Royal Mint ===",
        late_quote=WIKI_NEWTON_LATE_QUOTE,
        tail="== References ==",
        rest_end="=== Alchemy further reading ===",
    ),
    "Barack Obama": _WikiTitleCuts(
        middle="===Family and personal life===",
        late="==Presidential campaigns==",
        early_heading="lead through Education",
        early_ground="==Early life and career==",
        middle_heading="Family through Senate campaigns",
        middle_ground="===Family and personal life===",
        middle_quote=WIKI_OBAMA_MID_QUOTE,
        late_heading="Presidential campaigns through environmental policy",
        late_ground="==Presidential campaigns==",
        late_quote=WIKI_OBAMA_LATE_QUOTE,
        tail="====Environmental policy====",
        rest_end="[[The Hill (newspaper)|The Hill]]",
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
        middle="=== Montgomery bus boycott, 1955 ===",
        late="=== Opposition to the Vietnam War ===",
        early_heading="lead through family and early activism",
        early_ground="== Early life and education ==",
        middle_heading="Montgomery boycott through Chicago",
        middle_ground="=== Montgomery bus boycott, 1955 ===",
        middle_quote=WIKI_MLK_MID_QUOTE,
        late_heading="Vietnam War through South Africa legacy",
        late_ground="=== Opposition to the Vietnam War ===",
        late_quote=WIKI_MLK_LATE_QUOTE,
        tail="== Legacy ==",
        rest_end="==== ''The Measure of a Man'' ====",
    ),
    "Thomas Jefferson": _WikiTitleCuts(
        middle="[[Sublime Porte]]",
        late="===Cabinet===",
        early_heading="lead through Minister to France",
        early_ground="==Early life and education==",
        middle_heading="France remainder through second term",
        middle_ground="==Secretary of State==",
        middle_quote=WIKI_JEFFERSON_MID_QUOTE,
        late_heading="Cabinet through Legacy",
        late_ground="===Cabinet===",
        late_quote=WIKI_JEFFERSON_LATE_QUOTE,
        tail="==Legacy==",
        rest_end="===Thomas Jefferson Foundation sources===",
    ),
}


def _cut_index(
    wikitext: str, spans: tuple[tuple[str, int, int], ...], spec: str
) -> int:
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
        late_heading=cuts.late_heading,
        late_ground=cuts.late_ground,
        late_quote=cuts.late_quote,
        entity_text=entity_text,
        entity_id=entity_id,
        entity_hash=entity_hash,
        rest=rest,
    )
    payload = json.loads(wikipedia_text)
    revision_id = str(payload["query"]["pages"][0]["revisions"][0]["revid"])
    return WikiClaimProgram(
        revision_id=revision_id,
        entity_id=entity_id,
        title=title,
        sections=sections,
    )
