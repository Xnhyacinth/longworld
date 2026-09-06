"""Source-span EUR-Lex qualification comparison, without candidate registration."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from copy import deepcopy
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse
from xml.parsers import expat

from longworld.core.pack import SEP, wrap_prompt

EURLEX_PMS_TASK_SCHEMA = "longworld.eurlex-pms-risk-control-task.v1"
EURLEX_PMS_ANSWER_PROGRAM = "eurlex.pms_risk_control.visible_prefix.v2"
EURLEX_PMS_REPLAY_REVISION = "longworld.eurlex-pms-risk-control-replay.v2"

TITLE = "Person responsible for regulatory compliance"
_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


class EurLexWorkflowError(ValueError):
    """The source cannot support an unambiguous replay."""


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalized_text(raw: bytes) -> str:
    parser = _Text()
    parser.feed(raw.decode("utf-8"))
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def source_span(raw: bytes, source_id: str, start: int, end: int) -> dict[str, Any]:
    if not 0 <= start < end <= len(raw):
        raise EurLexWorkflowError("source span is outside the raw representation")
    selected = raw[start:end]
    return {
        "source_id": source_id,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_start": start,
        "byte_end": end,
        "raw_span_sha256": hashlib.sha256(selected).hexdigest(),
        "raw_text": selected.decode("utf-8"),
        "text": normalized_text(selected),
    }


def verify_span(span: dict[str, Any], raw: bytes) -> None:
    expected = source_span(raw, span["source_id"], span["byte_start"], span["byte_end"])
    if span != expected:
        raise EurLexWorkflowError("source span or its normalized text changed")


def article_span(raw: bytes, source_id: str, *, title: str = TITLE) -> dict[str, Any]:
    paragraphs = list(re.finditer(rb"<p\b[^>]*>.*?</p>", raw, re.DOTALL))
    headers = [
        (index, normalized_text(match.group()))
        for index, match in enumerate(paragraphs)
        if re.match(r"^Article\s+\d+\b", normalized_text(match.group()))
    ]
    selected: list[tuple[int, int]] = []
    for offset, (index, header) in enumerate(headers):
        following = (
            normalized_text(paragraphs[index + 1].group())
            if index + 1 < len(paragraphs)
            else ""
        )
        if title not in header and following != title:
            continue
        if offset + 1 == len(headers):
            raise EurLexWorkflowError("article end heading is absent")
        selected.append(
            (paragraphs[index].start(), paragraphs[headers[offset + 1][0]].start())
        )
    if len(selected) != 1:
        raise EurLexWorkflowError("qualification article identity is ambiguous")
    return source_span(raw, source_id, *selected[0])


def relation_spans(raw: bytes) -> list[dict[str, Any]]:
    """Return exact subject and adopts elements, never fill endpoints from config."""
    root = ET.fromstring(raw)
    work = root.find("WORK")
    if work is None:
        raise EurLexWorkflowError("metadata WORK is absent")
    tags = ("RESOURCE_LEGAL_ID_CELEX", "RESOURCE_LEGAL_ADOPTS_RESOURCE_LEGAL")
    offsets: dict[str, list[tuple[int, int]]] = {tag: [] for tag in tags}
    stack: list[str] = []
    starts: dict[str, int] = {}
    parser = expat.ParserCreate()

    def start(tag: str, attributes: dict[str, str]) -> None:
        del attributes
        if len(stack) == 2 and stack[-1] == "WORK" and tag in tags:
            starts[tag] = parser.CurrentByteIndex
        stack.append(tag)

    def end(tag: str) -> None:
        if len(stack) == 3 and stack[-2] == "WORK" and tag in tags:
            end_offset = raw.index(b">", parser.CurrentByteIndex) + 1
            offsets[tag].append((starts[tag], end_offset))
        stack.pop()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(raw, True)
    spans: list[dict[str, Any]] = []
    for tag in tags:
        direct = work.findall(tag)
        if len(direct) != 1 or len(offsets[tag]) != 1:
            raise EurLexWorkflowError("adoption relation or subject is ambiguous")
        spans.append(source_span(raw, "metadata", *offsets[tag][0]))
    return spans


def adoption_endpoints(spans: list[dict[str, Any]]) -> tuple[str, str]:
    subject = []
    objects = []
    for span in spans:
        node = ET.fromstring(span["raw_text"])
        if node.tag == "RESOURCE_LEGAL_ID_CELEX":
            subject.extend(node.findall("VALUE"))
        elif node.tag == "RESOURCE_LEGAL_ADOPTS_RESOURCE_LEGAL":
            for uri in node.findall("SAMEAS/URI"):
                if uri.findtext("TYPE") == "celex":
                    objects.extend(uri.findall("IDENTIFIER"))
    if len(subject) != 1 or len(objects) != 1:
        raise EurLexWorkflowError("adoption endpoints are absent or ambiguous")
    return str(objects[0].text), str(subject[0].text)


def manufacturer_years(text: str) -> tuple[int, int]:
    """Read paragraph 1's diploma and experience-only routes, not paragraph 4/6."""
    if TITLE not in text or "Manufacturers shall have available" not in text:
        raise EurLexWorkflowError("manufacturer qualification article is absent")
    manufacturer = text.split("Without prejudice", 1)[0]
    routes = re.findall(
        r"\b((?:one|two|three|four|five)(?:\s+(?:one|two|three|four|five))*)\s+years? of professional experience",
        manufacturer,
    )
    if len(routes) != 2 or any(route not in _NUMBERS for route in routes):
        raise EurLexWorkflowError("qualification years are absent or ambiguous")
    return _NUMBERS[routes[0]], _NUMBERS[routes[1]]


def replay_qualification_change(
    spans: list[dict[str, Any]], source_celex: dict[str, str]
) -> dict[str, Any]:
    """Compare the proposal selected by the act's authentic adoption relation."""
    try:
        relation = [span for span in spans if span["source_id"] == "metadata"]
        proposal, act = adoption_endpoints(relation)
        by_celex: dict[str, tuple[int, int]] = {}
        for span in spans:
            celex = source_celex.get(span["source_id"])
            if celex not in {proposal, act}:
                continue
            if celex in by_celex:
                raise EurLexWorkflowError("duplicate qualification article")
            by_celex[celex] = manufacturer_years(span["text"])
        before, after = by_celex[proposal], by_celex[act]
    except (EurLexWorkflowError, KeyError, ET.ParseError):
        return {"status": "UNKNOWN", "answer": "UNKNOWN"}
    return {
        "status": "PASS",
        "answer": {
            "proposal": proposal,
            "adopted_act": act,
            "diploma_route_years": [before[0], after[0]],
            "experience_only_years": [before[1], after[1]],
            "year_changes": [after[0] - before[0], after[1] - before[1]],
        },
    }


def replay_source_bound_qualification_change(
    spans: list[dict[str, Any]],
    raw_sources: dict[str, bytes],
    source_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind all input spans to externally trusted official-URL/hash receipts."""
    identities: dict[str, str] = {}
    receipts = {item["source_id"]: item for item in source_receipts}
    if len(receipts) != len(source_receipts):
        raise EurLexWorkflowError("source receipts are duplicated")
    for source_id in {span["source_id"] for span in spans}:
        raw = raw_sources[source_id]
        receipt = receipts[source_id]
        url = urlparse(receipt["url"])
        if (
            url.scheme != "https"
            or url.hostname != "eur-lex.europa.eu"
            or hashlib.sha256(raw).hexdigest() != receipt["expected_sha256"]
            or len(raw) != receipt["expected_bytes"]
        ):
            raise EurLexWorkflowError("official source receipt mismatch")
        requested = parse_qs(url.query).get("uri", [])
        if len(requested) != 1 or not requested[0].startswith("CELEX:"):
            raise EurLexWorkflowError("official source identity is absent")
        if source_id != "metadata":
            identities[source_id] = requested[0].removeprefix("CELEX:")
    for span in spans:
        verify_span(span, raw_sources[span["source_id"]])
    return replay_qualification_change(spans, identities)


def statement_reference(text: str) -> tuple[str, str, str]:
    matches = re.findall(
        r"in the case of investigational devices, the statement referred to in Section\s+(\d+\.\d+) of Chapter ([IVX]+) of Annex\s+([IVX]+) is issued",
        text,
    )
    if TITLE not in text or len(matches) != 1:
        raise EurLexWorkflowError(
            "responsible-person statement reference is absent or ambiguous"
        )
    section, chapter, annex = matches[0]
    return annex, chapter, section


def investigational_statement_spans(raw: bytes) -> list[dict[str, Any]]:
    """Resolve the actual reference before selecting its annex/chapter/section."""
    origin = article_span(raw, "act")
    annex, chapter, section = statement_reference(origin["text"])
    paragraphs = list(re.finditer(rb"<p\b[^>]*>.*?</p>", raw, re.DOTALL))
    selected = [origin]
    current_annex = ""
    current_chapter = ""
    for match in paragraphs:
        text = normalized_text(match.group())
        annex_match = re.fullmatch(r"ANNEX ([IVX]+)", text)
        chapter_match = re.fullmatch(r"CHAPTER ([IVX]+)", text)
        if annex_match:
            current_annex = annex_match[1]
            current_chapter = ""
            if current_annex == annex:
                selected.append(source_span(raw, "act", match.start(), match.end()))
        elif chapter_match:
            current_chapter = chapter_match[1]
            if current_annex == annex and current_chapter == chapter:
                selected.append(source_span(raw, "act", match.start(), match.end()))
        elif (
            current_annex == annex
            and current_chapter == chapter
            and text.startswith(section + ". ")
        ):
            selected.append(source_span(raw, "act", match.start(), match.end()))
    if len(selected) != 4:
        raise EurLexWorkflowError("referenced statement target is absent or ambiguous")
    return selected


def replay_referenced_statement(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Follow the role's parsed reference, then read the target's exception chain."""
    try:
        origins = [span for span in spans if TITLE in span["text"]]
        if len(origins) != 1:
            raise EurLexWorkflowError("statement origin is absent or ambiguous")
        annex, chapter, section = statement_reference(origins[0]["text"])
        article = re.search(r"Article\s+(\d+)\b", origins[0]["text"])
        if article is None:
            raise EurLexWorkflowError("origin article number is absent")
        current_annex = ""
        current_chapter = ""
        targets = []
        for span in sorted(spans, key=lambda item: item["byte_start"]):
            text = span["text"]
            annex_match = re.fullmatch(r"ANNEX ([IVX]+)", text)
            chapter_match = re.fullmatch(r"CHAPTER ([IVX]+)", text)
            if annex_match:
                current_annex = annex_match[1]
                current_chapter = ""
            elif chapter_match:
                current_chapter = chapter_match[1]
            elif (
                current_annex == annex
                and current_chapter == chapter
                and text.startswith(section + ". ")
            ):
                targets.append(text)
        if len(targets) != 1:
            raise EurLexWorkflowError("resolved target is absent or ambiguous")
        target = re.fullmatch(
            re.escape(section)
            + r"\. A signed statement by (.+?) that the device in question conforms to (.+?) apart from (.+?) and that, with regard to those aspects, (.+)\.",
            targets[0],
        )
        if target is None:
            raise EurLexWorkflowError(
                "statement exception and safeguard are not parseable"
            )
    except EurLexWorkflowError:
        return {"status": "UNKNOWN", "answer": "UNKNOWN"}
    return {
        "status": "PASS",
        "answer": {
            "origin_article": article[1],
            "condition": "investigational devices",
            "reference": {"annex": annex, "chapter": chapter, "section": section},
            "issuer": target[1],
            "conformity_requirement": target[2],
            "excepted_aspects": target[3],
            "safeguard_for_excepted_aspects": target[4],
        },
    }


def replay_referenced_statement_raw_slice(text: str) -> dict[str, Any]:
    """Replay visible text, including sufficient partial origin articles."""
    return replay_referenced_statement(
        [
            {"text": fragment.strip(), "byte_start": index}
            for index, fragment in enumerate(text.split(SEP))
        ]
    )


def verify_referenced_statement_sources(
    spans: list[dict[str, Any]], raw: bytes, receipt: dict[str, Any]
) -> None:
    """Bind selected semantic nodes to the trusted act representation and graph."""
    url = urlparse(receipt["url"])
    if (
        receipt["source_id"] != "act"
        or url.scheme != "https"
        or url.hostname != "eur-lex.europa.eu"
        or parse_qs(url.query).get("uri") != ["CELEX:32017R0745"]
        or len(raw) != receipt["expected_bytes"]
        or hashlib.sha256(raw).hexdigest() != receipt["expected_sha256"]
    ):
        raise EurLexWorkflowError("statement act receipt mismatch")
    allowed = investigational_statement_spans(raw)
    for span in spans:
        verify_span(span, raw)
        if span not in allowed:
            raise EurLexWorkflowError(
                "statement node is outside the resolved source graph"
            )


def extract_pms_chain(raw: bytes) -> list[dict[str, Any]]:
    spans = [
        article_span(raw, "act", title=title)
        for title in (
            "Person responsible for regulatory compliance",
            "General obligations of manufacturers",
            "Post-market surveillance system of the manufacturer",
            "Post-market surveillance plan",
        )
    ]
    paragraphs = list(re.finditer(rb"<p\b[^>]*>.*?</p>", raw, re.DOTALL))
    texts = [normalized_text(match.group()) for match in paragraphs]

    def annex_bounds(label: str) -> tuple[int, int]:
        start = texts.index("ANNEX " + label)
        end = next(
            index
            for index in range(start + 1, len(texts))
            if re.fullmatch(r"ANNEX [IVX]+", texts[index])
        )
        return start, end

    plan_ref = re.search(
        r"requirements for which are set out in Section\s+(\d+\.\d+) of Annex\s+([IVX]+)",
        spans[3]["text"],
    )
    if plan_ref is None:
        raise EurLexWorkflowError("PMS plan annex reference is absent")
    plan_start, plan_end = annex_bounds(plan_ref[2])
    plan_span = source_span(
        raw, "act", paragraphs[plan_start].start(), paragraphs[plan_end].start()
    )
    risk_ref = re.search(
        r"risk management as referred to in Section\s+(\d+) of Annex\s+([IVX]+)",
        plan_span["text"],
    )
    if risk_ref is None:
        raise EurLexWorkflowError("PMS risk-management reference is absent")
    risk_start, risk_end = annex_bounds(risk_ref[2])
    risk_section = next(
        index
        for index in range(risk_start + 1, risk_end)
        if texts[index].startswith(risk_ref[1] + ". Manufacturers shall establish")
    )
    risk_stop = next(
        index
        for index in range(risk_section + 1, risk_end)
        if re.match(r"^\d+\.\s", texts[index])
    )
    risk_span = source_span(
        raw, "act", paragraphs[risk_start].start(), paragraphs[risk_stop].start()
    )
    control_ref = re.search(
        r"if necessary amend control measures in line with the requirements of Section\s+(\d+)",
        risk_span["text"],
    )
    if control_ref is None:
        raise EurLexWorkflowError("PMS risk-control reference is absent")
    control_start = next(
        index
        for index in range(risk_start + 1, risk_end)
        if texts[index].startswith(control_ref[1] + ". Risk control measures adopted")
    )
    control_end = next(
        index
        for index in range(control_start + 1, risk_end)
        if re.match(r"^\d+\.\s", texts[index])
    )
    spans.extend(
        [
            plan_span,
            risk_span,
            source_span(
                raw,
                "act",
                paragraphs[control_start].start(),
                paragraphs[control_end].start(),
            ),
        ]
    )
    return spans


def replay_pms_chain(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose the proved prefix and its first unresolved edge, without guessing."""
    texts = [span["text"] for span in spans]
    path: list[str] = []
    surveillance_risk_scope: dict[str, str] | None = None

    def finish(missing: str, priorities: list[str] | None = None) -> dict[str, Any]:
        return {
            "status": "PASS" if priorities else "PARTIAL",
            "answer": {
                "parsed_path": path,
                "first_unresolved_reference": missing or None,
                "risk_control_priority": priorities or "UNKNOWN",
                "surveillance_risk_scope": surveillance_risk_scope,
            },
        }

    def unique(pattern: str) -> tuple[str, re.Match[str]] | None:
        found = [(text, match) for text in texts if (match := re.search(pattern, text))]
        return found[0] if len(found) == 1 else None

    origin = unique(
        r"Article\s+(\d+)\s+Person responsible for regulatory compliance.*?post-market surveillance obligations are complied with in accordance with Article\s+(\d+)\((\d+)\)"
    )
    if origin is None:
        return finish("responsible-person post-market surveillance duty")
    path.append(f"Article {origin[1][1]} PMS duty")
    manufacturer_ref = f"Article {origin[1][2]}({origin[1][3]})"
    manufacturer = unique(
        r"Article\s+"
        + re.escape(origin[1][2])
        + r"\s+General obligations of manufacturers.*?\b"
        + re.escape(origin[1][3])
        + r"\. Manufacturers of devices shall implement and keep up to date the post-market surveillance system in accordance with Article\s+(\d+)"
    )
    if manufacturer is None:
        return finish(manufacturer_ref)
    path.append(manufacturer_ref)
    system_ref = f"Article {manufacturer[1][1]}"
    system = unique(
        r"Article\s+"
        + re.escape(manufacturer[1][1])
        + r"\s+Post-market surveillance system of the manufacturer.*?risk management as referred to in Chapter\s+([IVX]+) of Annex\s+([IVX]+)"
    )
    if system is None:
        return finish(system_ref)
    path.append(system_ref)
    surveillance_risk_scope = {"chapter": system[1][1], "annex": system[1][2]}
    plan = unique(
        r"Article\s+(\d+)\s+Post-market surveillance plan The post-market surveillance system referred to in Article\s+"
        + re.escape(manufacturer[1][1])
        + r"\s+shall be based on a post-market surveillance plan, the requirements for which are set out in Section\s+(\d+\.\d+) of Annex\s+([IVX]+)"
    )
    if plan is None:
        return finish(f"plan definition referring to {system_ref}")
    path.append(f"Article {plan[1][1]} reverse-reference plan")
    plan_ref = f"Annex {plan[1][3]} Section {plan[1][2]}"
    annex = unique(
        r"ANNEX\s+"
        + re.escape(plan[1][3])
        + r"\b.*?"
        + re.escape(plan[1][2])
        + r"\. The post-market surveillance plan.*?suitable indicators and threshold values.*?risk management as referred to in Section\s+(\d+) of Annex\s+([IVX]+)"
    )
    if annex is None:
        return finish(plan_ref)
    if annex[1][2] != surveillance_risk_scope["annex"]:
        return finish("plan risk-management target consistent with surveillance scope")
    path.append(plan_ref)
    risk_ref = f"Annex {annex[1][2]} Section {annex[1][1]}"
    risk_matches = []
    for text in texts:
        headings = list(re.finditer(r"\b(ANNEX|CHAPTER)\s+([IVX]+)\b", text))
        current_annex = ""
        for index, heading in enumerate(headings):
            if heading[1] == "ANNEX":
                current_annex = heading[2]
                continue
            if (
                current_annex != annex[1][2]
                or heading[2] != surveillance_risk_scope["chapter"]
            ):
                continue
            end = (
                headings[index + 1].start() if index + 1 < len(headings) else len(text)
            )
            chapter_text = text[heading.end() : end]
            match = re.search(
                r"\b"
                + re.escape(annex[1][1])
                + r"\. Manufacturers shall establish, implement, document and maintain a risk management system.*?based on the evaluation of the impact of the information referred to in point \(e\), if necessary amend control measures in line with the requirements of Section\s+(\d+)",
                chapter_text,
            )
            if match is not None:
                risk_matches.append((chapter_text, match))
    risk = risk_matches[0] if len(risk_matches) == 1 else None
    if risk is None:
        return finish(risk_ref)
    path.append(risk_ref)
    control_ref = f"Annex {annex[1][2]} Section {risk[1][1]}"
    controls = unique(
        r"^"
        + re.escape(risk[1][1])
        + r"\. Risk control measures adopted.*?following order of priority: \(a\) (.+?); \(b\) (.+?); and \(c\) (.+?)\.(?:\s|$)"
    )
    if controls is None:
        return finish(control_ref)
    path.append(control_ref)
    return finish("", [controls[1][index] for index in (1, 2, 3)])


def eurlex_canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_sha(value: object) -> str:
    return hashlib.sha256(eurlex_canonical_json(value).encode()).hexdigest()


def eurlex_document(span: Mapping[str, Any], *, withheld: bool = False) -> str:
    record: dict[str, Any] = {
        "source_byte_start": span["byte_start"],
        "text": span["text"],
    }
    if withheld:
        record["text"] = ""
        record["source_body_withheld"] = True
    return eurlex_canonical_json(record)


def validate_eurlex_pms_task(task: object) -> dict[str, Any]:
    fields = {
        "schema_version",
        "answer_program_id",
        "question",
        "source_receipt_sha256",
        "artifacts",
        "essential_artifact_ids",
        "cf_artifact_id",
    }
    if not isinstance(task, dict) or set(task) != fields:
        raise EurLexWorkflowError("EUR-Lex task fields are invalid")
    if (
        task["schema_version"] != EURLEX_PMS_TASK_SCHEMA
        or task["answer_program_id"] != EURLEX_PMS_ANSWER_PROGRAM
        or not isinstance(task["question"], str)
        or not task["question"].strip()
        or re.fullmatch(r"[a-f0-9]{64}", str(task["source_receipt_sha256"])) is None
    ):
        raise EurLexWorkflowError("EUR-Lex task identity is invalid")
    artifacts, essential = task["artifacts"], task["essential_artifact_ids"]
    if (
        not isinstance(artifacts, list)
        or not artifacts
        or not isinstance(essential, list)
        or len(essential) != 7
        or len(set(essential)) != 7
    ):
        raise EurLexWorkflowError("EUR-Lex task source graph is invalid")
    ids = [
        item.get("artifact_id")
        for item in artifacts
        if isinstance(item, dict) and set(item) == {"artifact_id", "source_span"}
    ]
    if (
        len(ids) != len(artifacts)
        or len(set(ids)) != len(ids)
        or not set(essential) <= set(ids)
        or task["cf_artifact_id"] != essential[-1]
    ):
        raise EurLexWorkflowError("EUR-Lex task artifact binding is invalid")
    return task


def verify_eurlex_pms_payload(payload: Mapping[str, Any]) -> None:
    task = validate_eurlex_pms_task(payload.get("eurlex_pms_task"))
    raw_receipt = payload.get("source_receipt_raw_utf8")
    if (
        not isinstance(raw_receipt, str)
        or payload.get("task_sha256") != _json_sha(task)
        or hashlib.sha256(raw_receipt.encode()).hexdigest()
        != payload.get("source_receipt_sha256")
        or task["source_receipt_sha256"] != payload.get("source_receipt_sha256")
    ):
        raise EurLexWorkflowError("EUR-Lex payload digest is invalid")
    receipt = json.loads(raw_receipt)
    if (
        raw_receipt != eurlex_canonical_json(receipt) + "\n"
        or receipt.get("schema_version") != "longworld.eurlex-source-receipt.v1"
        or receipt.get("train_ready") is not False
        or receipt.get("production_eligible") is not False
    ):
        raise EurLexWorkflowError("EUR-Lex source receipt is invalid")
    if receipt.get("authorization_record_id") != payload.get(
        "authorization_record_id"
    ) or receipt.get("preflight_config_sha256") != payload.get(
        "preflight_config_sha256"
    ):
        raise EurLexWorkflowError("EUR-Lex source authorization binding is invalid")
    sources = receipt.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"act", "legal_notice"}:
        raise EurLexWorkflowError("EUR-Lex source coverage is incomplete")
    raw_sources = {}
    for name, source in sources.items():
        url = urlparse(source["url"])
        body = source["raw_utf8"].encode()
        if (
            url.scheme != "https"
            or url.hostname != "eur-lex.europa.eu"
            or hashlib.sha256(body).hexdigest() != source["sha256"]
            or len(body) != source["bytes"]
        ):
            raise EurLexWorkflowError("EUR-Lex official source bytes are invalid")
        raw_sources[name] = body
    if parse_qs(urlparse(sources["act"]["url"]).query).get("uri") != [
        "CELEX:32017R0745"
    ]:
        raise EurLexWorkflowError("EUR-Lex act identity is invalid")
    if (
        sources["legal_notice"]["url"]
        != "https://eur-lex.europa.eu/content/legal-notice/legal-notice.html"
    ):
        raise EurLexWorkflowError("EUR-Lex legal notice identity is invalid")
    bundle_sha = _json_sha({name: source["sha256"] for name, source in sources.items()})
    if payload.get("source_bundle_sha256") != bundle_sha:
        raise EurLexWorkflowError("EUR-Lex source bundle digest is invalid")
    notice = normalized_text(raw_sources["legal_notice"])
    if (
        "you can re-use the legal documents published in EUR-Lex for commercial or non-commercial purposes"
        not in notice
        or "includes third-party works" not in notice
    ):
        raise EurLexWorkflowError("EUR-Lex reuse coverage is absent")
    act_raw = raw_sources["act"]
    expected_graph = extract_pms_chain(act_raw)
    if replay_pms_chain(expected_graph)["status"] != "PASS":
        raise EurLexWorkflowError("EUR-Lex source reference graph does not resolve")
    if task["essential_artifact_ids"] != [
        f"act:{span['byte_start']}" for span in expected_graph
    ]:
        raise EurLexWorkflowError("EUR-Lex essential graph was not source-derived")
    for item in task["artifacts"]:
        span = item["source_span"]
        start, end = span["byte_start"], span["byte_end"]
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not 0 <= start < end <= len(act_raw)
        ):
            raise EurLexWorkflowError("EUR-Lex source span boundaries are invalid")
        selected = act_raw[start:end]
        if (
            item["artifact_id"] != f"act:{start}"
            or span["source_id"] != "act"
            or span["source_sha256"] != sources["act"]["sha256"]
            or selected.decode() != span["raw_text"]
            or hashlib.sha256(selected).hexdigest() != span["raw_span_sha256"]
            or normalized_text(selected) != span["text"]
        ):
            raise EurLexWorkflowError("EUR-Lex artifact differs from exact source span")
        if re.search(r"<(?:img|object|iframe)\b", span["raw_text"], re.IGNORECASE):
            raise EurLexWorkflowError(
                "EUR-Lex selected content has unreviewed non-text rights"
            )
        if (
            item["artifact_id"] in task["essential_artifact_ids"]
            and span not in expected_graph
        ):
            raise EurLexWorkflowError("EUR-Lex essential scope was changed")


def _eurlex_candidate_documents(
    candidate: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Mapping[str, Any]]]:
    task = validate_eurlex_pms_task(candidate.get("eurlex_pms_task"))
    if (
        candidate.get("question") != task["question"]
        or candidate.get("answer_program_id") != EURLEX_PMS_ANSWER_PROGRAM
    ):
        raise EurLexWorkflowError("EUR-Lex question or program is unbound")
    classifications = candidate.get("artifact_classification")
    context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(context, str):
        raise EurLexWorkflowError("EUR-Lex document pool is absent")
    documents = context.split(SEP)
    if len(documents) != len(classifications) or not documents:
        raise EurLexWorkflowError("EUR-Lex document pool is unbound")
    by_id = {item["artifact_id"]: item["source_span"] for item in task["artifacts"]}
    records, classes = {}, {}
    for classification, document in zip(classifications, documents, strict=True):
        artifact_id = classification.get("artifact_id")
        if artifact_id not in by_id or artifact_id in records:
            raise EurLexWorkflowError("EUR-Lex artifact identity is invalid")
        span = by_id[artifact_id]
        withheld = classification.get("source_origin") == "synthetic_counterfactual"
        if not withheld and (
            classification.get("source_origin") != "real_public"
            or classification.get("provenance_id")
            != "eurlex-source-span-sha256:" + span["raw_span_sha256"]
        ):
            raise EurLexWorkflowError("EUR-Lex authentic span provenance is invalid")
        if withheld and artifact_id != task["cf_artifact_id"]:
            raise EurLexWorkflowError("EUR-Lex counterfactual target is invalid")
        if document != eurlex_document(span, withheld=withheld):
            raise EurLexWorkflowError("EUR-Lex artifact text is not source-bound")
        if (
            classification.get("derived_text_sha256")
            != hashlib.sha256(document.encode()).hexdigest()
            or classification.get("source_byte_start") != span["byte_start"]
            or classification.get("source_byte_end") != span["byte_end"]
            or classification.get("source_sha256") != span["source_sha256"]
        ):
            raise EurLexWorkflowError(
                "EUR-Lex artifact classification is not source-bound"
            )
        records[artifact_id], classes[artifact_id] = (
            json.loads(document),
            classification,
        )
    return task, records, classes


def validate_eurlex_candidate_source_binding(
    candidate: Mapping[str, Any], payload: Mapping[str, Any]
) -> None:
    verify_eurlex_pms_payload(payload)
    task, _records, _classes = _eurlex_candidate_documents(candidate)
    if task != payload["eurlex_pms_task"] or candidate.get("source_binding") != {
        key: payload.get(key)
        for key in (
            "source_receipt_sha256",
            "source_bundle_sha256",
            "preflight_config_sha256",
            "authorization_record_id",
        )
    }:
        raise EurLexWorkflowError(
            "EUR-Lex candidate differs from signed source binding"
        )


def _visible_pms_result(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    visible = [
        {"text": record["text"]}
        for record in records
        if isinstance(record.get("text"), str) and record["text"]
    ]
    replay = replay_pms_chain(visible)
    answer = dict(replay["answer"])
    if replay["status"] == "PASS":
        answer["terminal_source_availability"] = "AVAILABLE"
    elif len(answer["parsed_path"]) == 6 and any(
        record.get("source_body_withheld") is True for record in records
    ):
        answer["terminal_source_availability"] = "WITHHELD"
    else:
        answer["terminal_source_availability"] = "MISSING"
    return {
        "answer": eurlex_canonical_json(answer),
        "parsed_path_count": len(answer["parsed_path"]),
        "withheld": answer["terminal_source_availability"] == "WITHHELD",
    }


def replay_eurlex_pms_candidate(
    candidate: Mapping[str, Any],
    evidence_artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    task, records, _classes = _eurlex_candidate_documents(candidate)
    selected = list(evidence_artifact_ids)
    if len(selected) != len(set(selected)) or any(
        key not in records for key in selected
    ):
        raise EurLexWorkflowError("EUR-Lex selected artifacts are invalid")
    selected_records = {key: dict(records[key]) for key in selected}
    target = task["cf_artifact_id"]
    if counterfactual and target in selected_records:
        target_span = next(
            item["source_span"]
            for item in task["artifacts"]
            if item["artifact_id"] == target
        )
        if selected_records[target].get(
            "source_body_withheld"
        ) is True and hashlib.sha256(
            eurlex_document(target_span).encode()
        ).hexdigest() != _classes[target].get("counterfactual_parent_text_sha256"):
            raise EurLexWorkflowError(
                "EUR-Lex counterfactual parent recovery digest is invalid"
            )
        selected_records[target] = json.loads(
            eurlex_document(
                target_span,
                withheld=not bool(selected_records[target].get("source_body_withheld")),
            )
        )
    result = _visible_pms_result(list(selected_records.values()))
    essential = task["essential_artifact_ids"]
    count = result["parsed_path_count"]
    resolved = set(essential[:count])
    if result["withheld"]:
        resolved.add(target)
    # These directions preserve the legal document's actual references. The
    # plan-to-system edge is reverse-resolved by the program, not rewritten.
    pairs = [(0, 1), (1, 2), (3, 2), (3, 4), (4, 5), (5, 6)]
    authentic = [
        {
            "parent_record_id": essential[left],
            "child_record_id": essential[right],
            "relation_provenance": "exact_eurlex_reference_span",
        }
        for left, right in pairs
        if essential[left] in resolved and essential[right] in resolved
    ]
    derived = (
        [
            {
                "parent_record_id": essential[0],
                "child_record_id": essential[3],
                "relation_provenance": "query_root_reverse_reference_resolution",
            }
        ]
        if count >= 4
        else []
    )
    adjacency: dict[str, list[str]] = {}
    for edge in authentic + derived:
        adjacency.setdefault(edge["parent_record_id"], []).append(
            edge["child_record_id"]
        )

    def depth(node: str) -> int:
        return max((1 + depth(child) for child in adjacency.get(node, [])), default=0)

    proof_depth = max((depth(node) for node in adjacency), default=0)
    return {
        "answer": result["answer"],
        "source_record_ids": sorted(selected),
        "source_relation_ids": [_json_sha(edge) for edge in authentic],
        "authentic_source_relation_edges": authentic,
        "verified_derived_order_relation_edges": derived,
        "event_count": len(selected),
        "strict_support_event_count": len(resolved),
        "proof_depth": proof_depth,
        "hop_count": proof_depth,
    }


def replay_eurlex_pms_raw_slice(
    candidate: Mapping[str, Any],
    raw_document_context: str,
    *,
    left_framed: bool,
    right_framed: bool,
) -> dict[str, Any]:
    del candidate
    lines = raw_document_context.splitlines()
    if not left_framed and lines:
        lines = lines[1:]
    if not right_framed and lines:
        lines = lines[:-1]
    records = []
    for line in lines:
        if not line or line == SEP.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(record, dict)
            and line == eurlex_canonical_json(record)
            and set(record)
            in (
                {"source_byte_start", "text"},
                {"source_byte_start", "text", "source_body_withheld"},
            )
        ):
            records.append(record)
    return {"answer": _visible_pms_result(records)["answer"]}


def eurlex_chronology(
    artifacts: Sequence[tuple[Mapping[str, Any], str]],
) -> list[tuple[str, dict[str, Any], str]]:
    return sorted(
        [
            (
                f"{int(classification['source_byte_start']):010d}|{classification['artifact_id']}",
                deepcopy(dict(classification)),
                document,
            )
            for classification, document in artifacts
        ],
        key=lambda item: item[0],
    )


def materialize_eurlex_counterfactual(
    candidate: Mapping[str, Any], artifacts: Sequence[tuple[Mapping[str, Any], str]]
) -> list[tuple[dict[str, Any], str]]:
    task, records, _classes = _eurlex_candidate_documents(candidate)
    target = task["cf_artifact_id"]
    output = []
    for classification, document in artifacts:
        child = deepcopy(dict(classification))
        if child["artifact_id"] == target:
            span = next(
                item["source_span"]
                for item in task["artifacts"]
                if item["artifact_id"] == target
            )
            if records[target].get(
                "source_body_withheld"
            ) or document != eurlex_document(span):
                raise EurLexWorkflowError("EUR-Lex counterfactual parent is invalid")
            projected = eurlex_document(span, withheld=True)
            digest = hashlib.sha256(projected.encode()).hexdigest()
            child.update(
                {
                    "source_origin": "synthetic_counterfactual",
                    "workflow_kind": "hybrid_causal",
                    "counterfactual_parent_source_origin": classification[
                        "source_origin"
                    ],
                    "counterfactual_parent_provenance_id": classification[
                        "provenance_id"
                    ],
                    "counterfactual_parent_text_sha256": hashlib.sha256(
                        document.encode()
                    ).hexdigest(),
                    "counterfactual_parent_source_binding_sha256": _json_sha(
                        candidate["source_binding"]
                    ),
                    "counterfactual_parent_sidecar_sha256": candidate[
                        "task_replay_sidecar"
                    ]["sha256"],
                    "derived_text_sha256": digest,
                    "provenance_id": "counterfactual-projection-sha256:" + digest,
                }
            )
            document = projected
        output.append((child, document))
    return output


def audit_eurlex_pms_candidate(candidate: Mapping[str, Any]) -> dict[str, bool]:
    task, records, classes = _eurlex_candidate_documents(candidate)
    source_map = candidate.get("source_record_ids_by_artifact")
    checks = {
        "serialized_context_valid": candidate.get("context")
        == wrap_prompt(
            str(candidate.get("question") or ""),
            str(candidate.get("document_context") or ""),
            str(candidate.get("query_timing") or ""),
        ),
        "artifact_source_mapping_valid": isinstance(source_map, Mapping)
        and set(source_map) == set(records)
        and all(
            source_map[key] == [key] and classes[key].get("source_record_id") == key
            for key in records
        ),
        "source_family_not_inflated": candidate.get("source_family_ids")
        == ["eur-lex.europa.eu/legal-content"],
        "counterfactual_operation_valid": (
            candidate.get("counterfactual_twin") or {}
        ).get("provenance_operation")
        == "withhold_source_body"
        and (candidate.get("counterfactual_twin") or {}).get("target_artifact_id")
        == task["cf_artifact_id"],
        "no_padding_or_truncation": all(
            candidate.get(field, 0) == 0
            for field in (
                "padding_tokens",
                "cloned_artifacts",
                "split_or_truncated_sections",
                "truncation_ppm",
            )
        ),
    }
    if not all(checks.values()):
        raise EurLexWorkflowError(
            "EUR-Lex candidate audit failed: "
            + ",".join(key for key, passed in checks.items() if not passed)
        )
    return checks
