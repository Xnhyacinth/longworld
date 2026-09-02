from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any

from longworld.core.cascade import apply_cascade, check_cascade
from longworld.core.grounded import apply_grounded, check_grounded
from longworld.core.groundedspan import (
    GroundedFact,
    GroundedRelation,
    GroundedSource,
    GroundedSpanError,
    normalize_fact_value,
    validate_grounded_source,
    validate_grounded_sources,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.scholarly import format_revision_added_delta
from longworld.core.state import WorldState
from longworld.core.wikiparse import (
    WIKI_ENTITY_VIEW_PREFIX,
    WIKI_SECTION_REVISION,
    WIKI_SECTION_VIEW_PREFIX,
    extract_wikipedia_wikitext,
)
from longworld.core.world import Event

_ARXIV_SOURCE_ENVELOPE_REVISION = "researchlab-arxiv-source-envelope-v1"
_WIKI_FACT_PARSER_REVISION = "researchlab-wiki-claim-exact-v2"
_WIKI_LEGACY_FACT_PARSER_REVISION = "researchlab-wiki-claim-exact-v1"
_WIKI_REVISION_HUNK = "wiki_revision_hunk_v1"
_WIKI_CANONICAL_FACT_SPAN = "wiki_canonical_fact_span_v1"
_WIKI_CANONICAL_FACT_SPAN_MAX_CHARS = 12_288
_WIKI_ANSWER_TAG = re.compile(r"[A-Z][A-Z0-9_]{1,31}")
_WIKI_LEGACY_ROLE_TAGS = {
    "born": "BORN",
    "early_career": "CAREER",
    "revolutionary_committee": "COMMITTEE",
    "early_transition": "EARLY",
    "commemoration": "COMM",
    "entity": "ENTITY",
    "popular_culture": "POP",
}
_ARXIV_HEADER = re.compile(
    r"\A% arXiv manuscript revision (?P<revision>v[1-9][0-9]*)\n"
    r"% arXiv submitted_at "
    r"(?P<submitted>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
    r"[0-9]{2}:[0-9]{2}Z)\n"
)
_ARXIV_BENCHMARK_ABSTRACT = re.compile(
    r"On the (?P<benchmark>[^\n]{1,160}? task),\s+our model establishes "
    r"a new single-model state-of-the-art BLEU score of "
    r"(?P<score>[0-9]+(?:\.[0-9]+)?) after training for "
    r"(?P<days>[0-9]+(?:\.[0-9]+)?) days on eight GPUs"
)
_ARXIV_BENCHMARK_DETAIL = re.compile(
    r"On the (?P<benchmark>[^\n]{1,160}? task),\s+our big model achieves "
    r"a BLEU score of \$?(?P<score>[0-9]+(?:\.[0-9]+)?)\$?"
)
_ARXIV_TRAINING_HARDWARE = re.compile(
    r"We trained our models on one machine with "
    r"(?P<hardware>[0-9]+ NVIDIA P100 GPUs)\."
)
_WIKI_BIRTH = re.compile(
    r"\{\{\s*birth date(?: and age)?\s*\|(?:df=y(?:es)?\|)?"
    r"(?P<year>[0-9]{4})\|(?P<month>[0-9]{1,2})\|"
    r"(?P<day>[0-9]{1,2})[^}]*\}\}",
    re.IGNORECASE,
)
_WIKIDATA_ENTITY = re.compile(r"\"id\":\"(?P<entity>Q[1-9][0-9]{0,11})\"")


def parse_arxiv_benchmark_detail(text: str) -> dict[str, Any] | None:
    """Parse one uniquely grounded detailed benchmark result."""
    matches = list(_ARXIV_BENCHMARK_DETAIL.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    return {
        role: {
            "value": match.group(role),
            "char_start": match.start(role),
            "char_end": match.end(role),
        }
        for role in ("benchmark", "score")
    }


def parse_arxiv_training_hardware(text: str) -> dict[str, Any] | None:
    """Parse the uniquely grounded training hardware statement."""
    matches = list(_ARXIV_TRAINING_HARDWARE.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    return {
        "value": match.group("hardware"),
        "char_start": match.start("hardware"),
        "char_end": match.end("hardware"),
    }


def _format_revision_delta(value: str) -> str:
    return format_revision_added_delta(value)


def parse_arxiv_benchmark_observation(
    text: str, *, include_detail: bool
) -> dict[str, Any] | None:
    """Parse uniquely grounded benchmark values from an arXiv LaTeX view."""
    abstract_matches = list(_ARXIV_BENCHMARK_ABSTRACT.finditer(text))
    if len(abstract_matches) != 1:
        return None
    abstract = abstract_matches[0]
    result: dict[str, Any] = {
        role: {
            "value": abstract.group(role),
            "char_start": abstract.start(role),
            "char_end": abstract.end(role),
        }
        for role in ("benchmark", "score", "days")
    }
    if not include_detail:
        return result
    detail = parse_arxiv_benchmark_detail(text)
    if detail is None:
        return None
    if detail["benchmark"]["value"] != abstract.group("benchmark"):
        return None
    result["detail_score"] = {
        "value": detail["score"]["value"],
        "char_start": detail["score"]["char_start"],
        "char_end": detail["score"]["char_end"],
    }
    return result


def format_arxiv_benchmark_trace(
    observations: list[dict[str, str]], *, include_detail: bool
) -> str:
    if not observations or len({item["benchmark"] for item in observations}) != 1:
        return ""
    benchmark = observations[0]["benchmark"]
    abstract = " -> ".join(
        f"{item['revision']} {item['score']}@{item['days']}d" for item in observations
    )
    answer = f"{benchmark} | {abstract}"
    if include_detail:
        if any(not item.get("detail_score") for item in observations):
            return ""
        detail = " -> ".join(
            f"{item['revision']} {item['detail_score']}" for item in observations
        )
        answer += f" | detail {detail}"
    return answer


def _canonical_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _arxiv_headers(source: GroundedSource) -> dict[str, str] | None:
    match = _ARXIV_HEADER.match(source.visible_text)
    if match is None:
        return None
    revision = match.group("revision")
    submitted_at = match.group("submitted")
    try:
        datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    expected = {
        "revision_id": (revision, match.start("revision"), match.end("revision")),
        "submitted_at": (
            submitted_at,
            match.start("submitted"),
            match.end("submitted"),
        ),
    }
    for role, (quote, start, end) in expected.items():
        facts = [fact for fact in source.facts if fact.fact_id.endswith(f":{role}")]
        if (
            len(facts) != 1
            or facts[0].quote != quote
            or facts[0].char_start != start
            or facts[0].char_end != end
        ):
            return None
    return {role: value[0] for role, value in expected.items()}


def _arxiv_source_file_texts(
    ev: Event, source: GroundedSource
) -> dict[str, str] | None:
    spans = ev.params.get("source_file_spans")
    declared_basenames = ev.params.get("source_view_basenames")
    if not isinstance(spans, list) or not isinstance(declared_basenames, list):
        return None
    texts: dict[str, str] = {}
    previous_end = 0
    for span in spans:
        if not isinstance(span, dict) or set(span) != {
            "path",
            "basename",
            "char_start",
            "char_end",
            "text_sha256",
        }:
            return None
        path = str(span["path"])
        basename = str(span["basename"])
        start = span["char_start"]
        end = span["char_end"]
        if (
            not path
            or path.rsplit("/", 1)[-1] != basename
            or basename in texts
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not previous_end <= start < end <= len(source.visible_text)
        ):
            return None
        text = source.visible_text[start:end]
        if hashlib.sha256(text.encode()).hexdigest() != span["text_sha256"]:
            return None
        texts[basename] = text
        previous_end = end
    if sorted(texts) != declared_basenames:
        return None
    return texts


def _arxiv_envelope_payload(ev: Event, source: GroundedSource) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "revision": _ARXIV_SOURCE_ENVELOPE_REVISION,
        "workflow_id": str(ev.params.get("workflow_id") or ""),
        "record_id": str(ev.params.get("record_id") or ""),
        "revision_id": str(ev.params.get("revision_id") or ""),
        "occurred_at": str(ev.params.get("occurred_at") or ""),
        "source_sha256": str(ev.params.get("source_sha256") or ""),
        "parent_provenance_id": str(ev.params.get("parent_provenance_id") or ""),
        "source_url": str(ev.params.get("source_url") or ""),
        "retrieval_url": str(ev.params.get("retrieval_url") or ""),
        "text_sha256": source.text_sha256,
        "provenance_id": str(ev.params.get("provenance_id") or ""),
    }
    if ev.params.get("provenance_operation") == "arxiv_semantic_latex_body_v2":
        payload["source_file_spans"] = ev.params.get("source_file_spans")
        payload["source_view_basenames"] = ev.params.get("source_view_basenames")
        if ev.params.get("source_compile_receipts"):
            payload["source_compile_receipts"] = ev.params.get(
                "source_compile_receipts"
            )
    return payload


def _wiki_fact_role_value(
    section_id: str, declared_role: str, quote: str
) -> tuple[str, str] | None:
    if declared_role == "born" and section_id.startswith("early_"):
        matches = list(_WIKI_BIRTH.finditer(quote))
        if len(matches) != 1:
            return None
        match = matches[0]
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        try:
            date(year, month, day)
        except ValueError:
            return None
        return "born", f"{year:04d}-{month:02d}-{day:02d}"
    if declared_role == "entity" and section_id == "wikidata_entity":
        entity_match = _WIKIDATA_ENTITY.fullmatch(quote)
        return (
            ("entity", entity_match.group("entity"))
            if entity_match is not None
            else None
        )
    if declared_role and quote and section_id != "wikidata_entity":
        return declared_role, quote
    return None


def _wiki_legacy_fact_role_value(
    section_id: str, declared_role: str, quote: str
) -> tuple[str, str] | None:
    if section_id in {"early_career", "early_revolution", "early_diplomacy"}:
        expected = {
            "early_career": "early_career",
            "early_revolution": "revolutionary_committee",
            "early_diplomacy": "early_transition",
        }[section_id]
        return (expected, quote) if declared_role == expected and quote else None
    expected_role = {
        "early_work": "born",
        "early_birth": "born",
        "commemoration": "commemoration",
        "popular_culture": "popular_culture",
        "wikidata_entity": "entity",
    }.get(section_id)
    if declared_role != expected_role:
        return None
    return _wiki_fact_role_value(section_id, declared_role, quote)


def _grounded_fact(payload: object) -> GroundedFact:
    if not isinstance(payload, dict) or set(payload) != {
        "fact_id",
        "source_id",
        "text_sha256",
        "quote",
        "char_start",
        "char_end",
        "normalized_quote",
    }:
        raise GroundedSpanError("grounded fact payload is invalid")
    return GroundedFact(**payload)


def _grounded_relation(payload: object) -> GroundedRelation:
    if not isinstance(payload, dict) or set(payload) != {
        "relation_id",
        "relation_type",
        "source_id",
        "target_id",
        "claimed_provenance_class",
        "evidence_fact_ids",
        "proof_mode",
    }:
        raise GroundedSpanError("grounded relation payload is invalid")
    evidence_fact_ids = payload["evidence_fact_ids"]
    if not isinstance(evidence_fact_ids, list):
        raise GroundedSpanError("grounded relation evidence payload is invalid")
    return GroundedRelation(
        **{**payload, "evidence_fact_ids": tuple(evidence_fact_ids)}
    )


def _grounded_source(payload: object) -> GroundedSource:
    if not isinstance(payload, dict) or set(payload) != {
        "source_id",
        "visible_text",
        "text_sha256",
        "facts",
        "relations",
    }:
        raise GroundedSpanError("grounded source payload is invalid")
    facts = payload["facts"]
    relations = payload["relations"]
    if not isinstance(facts, list) or not isinstance(relations, list):
        raise GroundedSpanError("grounded source members are invalid")
    return GroundedSource(
        source_id=payload["source_id"],
        visible_text=payload["visible_text"],
        text_sha256=payload["text_sha256"],
        facts=tuple(_grounded_fact(fact) for fact in facts),
        relations=tuple(_grounded_relation(relation) for relation in relations),
    )


def _source_binding(ev: Event) -> GroundedSource:
    source = validate_grounded_source(
        _grounded_source(ev.params.get("grounded_source"))
    )
    if source.source_id != ev.id or source.visible_text != str(
        ev.params.get("text") or ""
    ):
        raise GroundedSpanError("grounded source does not bind its event")
    return source


def _benchmark_trace_from_state(
    state: WorldState, ev: Event
) -> tuple[list[dict[str, str]], str] | None:
    record_requirements = ev.params.get("record_requirements")
    trace_mode = ev.params.get("trace_mode")
    if (
        not isinstance(record_requirements, list)
        or len(record_requirements) not in {3, 5, 7}
        or trace_mode not in {"cross_channel", "full_trace"}
    ):
        return None
    event_ids: list[str] = []
    for requirement in record_requirements:
        if (
            not isinstance(requirement, dict)
            or set(requirement)
            != {
                "event_id",
                "require_abstract",
                "require_detail",
                "require_training",
            }
            or not isinstance(requirement["event_id"], str)
            or not requirement["event_id"]
            or not isinstance(requirement["require_abstract"], bool)
            or not isinstance(requirement["require_detail"], bool)
            or not isinstance(requirement["require_training"], bool)
            or not (
                requirement["require_abstract"]
                or requirement["require_detail"]
                or requirement["require_training"]
            )
        ):
            return None
        event_ids.append(requirement["event_id"])
    if len(set(event_ids)) != len(event_ids):
        return None
    texts = state.values.get("source_record_texts") or {}
    metadata = state.values.get("source_record_metadata") or {}
    bindings = state.values.get("source_grounded_bindings") or {}
    partial_observations: list[dict[str, str]] = []
    for requirement in record_requirements:
        event_id = requirement["event_id"]
        require_abstract = requirement["require_abstract"]
        require_detail = requirement["require_detail"]
        require_training = requirement["require_training"]
        text = str(texts.get(event_id) or "")
        raw_binding = bindings.get(event_id)
        if not text or raw_binding is None:
            return None
        try:
            source = validate_grounded_source(_grounded_source(raw_binding))
        except (GroundedSpanError, TypeError):
            return None
        if source.source_id != event_id or source.visible_text != text:
            return None
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
            return None
        benchmark = (
            str(abstract["benchmark"]["value"])
            if abstract is not None
            else str(detail["benchmark"]["value"])
            if detail is not None
            else ""
        )
        if (
            abstract is not None
            and detail is not None
            and abstract["benchmark"]["value"] != detail["benchmark"]["value"]
        ):
            return None
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
            atoms["detail_score"] = detail["score"]
            atoms.setdefault("benchmark", detail["benchmark"])
        if training is not None:
            atoms["training_hardware"] = training
        for role, atom in atoms.items():
            fact_id = f"{event_id}:benchmark_{role}"
            facts = [fact for fact in source.facts if fact.fact_id == fact_id]
            if (
                len(facts) != 1
                or facts[0].quote != atom["value"]
                or facts[0].char_start != atom["char_start"]
                or facts[0].char_end != atom["char_end"]
            ):
                return None
        record_metadata = metadata.get(event_id) or {}
        revision = str(record_metadata.get("revision_id") or "")
        if not revision:
            return None
        partial_observations.append(
            {
                "revision": revision,
                "benchmark": benchmark,
                "score": (
                    str(abstract["score"]["value"]) if abstract is not None else ""
                ),
                "days": (
                    str(abstract["days"]["value"]) if abstract is not None else ""
                ),
                "detail_score": (
                    str(detail["score"]["value"]) if detail is not None else ""
                ),
                "training_hardware": (
                    str(training["value"]) if training is not None else ""
                ),
            }
        )
    benchmarks = {
        item["benchmark"] for item in partial_observations if item["benchmark"]
    }
    if len(benchmarks) != 1:
        return None
    observations_by_revision: dict[str, dict[str, str]] = {}
    revision_order: list[str] = []
    for partial in partial_observations:
        revision = partial["revision"]
        if revision not in observations_by_revision:
            if not partial["benchmark"]:
                return None
            observations_by_revision[revision] = {
                "revision": revision,
                "benchmark": partial["benchmark"],
                "score": "",
                "days": "",
                "detail_score": "",
                "training_hardware": "",
            }
            revision_order.append(revision)
        merged = observations_by_revision[revision]
        if partial["benchmark"] and merged["benchmark"] != partial["benchmark"]:
            return None
        for field in ("score", "days", "detail_score", "training_hardware"):
            value = partial[field]
            if value and merged[field]:
                return None
            if value:
                merged[field] = value
    observations = [observations_by_revision[revision] for revision in revision_order]
    training_observations = [item for item in observations if item["training_hardware"]]
    if len(training_observations) != len(observations):
        return None
    if trace_mode == "cross_channel":
        if (
            len(observations) != 2
            or not observations[0]["score"]
            or not observations[0]["days"]
            or observations[0]["detail_score"]
            or observations[1]["score"]
            or observations[1]["days"]
            or not observations[1]["detail_score"]
        ):
            return None
        answer = (
            f"{observations[0]['benchmark']} | abstract "
            f"{observations[0]['revision']} {observations[0]['score']}@"
            f"{observations[0]['days']}d -> detail "
            f"{observations[1]['revision']} {observations[1]['detail_score']}"
        )
    else:
        if any(not item["score"] or not item["detail_score"] for item in observations):
            return None
        answer = format_arxiv_benchmark_trace(observations, include_detail=True)
    training_trace = " -> ".join(
        f"{item['revision']} {item['training_hardware']}"
        for item in training_observations
    )
    benchmark_prefix = f"{training_observations[0]['benchmark']} |"
    if not answer.startswith(benchmark_prefix):
        return None
    answer = (
        f"{benchmark_prefix} training {training_trace} "
        f"|{answer[len(benchmark_prefix) :]}"
    )
    return (observations, answer) if answer else None


def _arxiv_source_binding_valid(ev: Event, source: GroundedSource) -> bool:
    params = ev.params
    headers = _arxiv_headers(source)
    if (
        headers is None
        or headers["revision_id"] != params.get("revision_id")
        or headers["submitted_at"] != params.get("occurred_at")
    ):
        return False
    provenance_class = params.get("source_binding_provenance")
    operation = str(params.get("provenance_operation") or "")
    parent = str(params.get("parent_provenance_id") or "")
    if operation in {
        "arxiv_semantic_latex_body_v1",
        "arxiv_semantic_latex_body_v2",
    }:
        if (
            provenance_class != "verified_derived"
            or params.get("source_origin") != "real_derived"
        ):
            return False
        digest_payload = {
            "operation": operation,
            "parent_provenance_id": parent,
            "excluded_paths": params.get("excluded_paths"),
            "text_sha256": source.text_sha256,
        }
        if operation == "arxiv_semantic_latex_body_v2":
            if _arxiv_source_file_texts(ev, source) is None:
                return False
            digest_payload.update(
                {
                    "source_file_spans": params.get("source_file_spans"),
                    "source_view_basenames": params.get("source_view_basenames"),
                }
            )
            if params.get("source_compile_receipts"):
                digest_payload["source_compile_receipts"] = params.get(
                    "source_compile_receipts"
                )
        expected_envelope = _canonical_digest(_arxiv_envelope_payload(ev, source))
        if params.get("canonical_source_envelope_sha256") != expected_envelope:
            return False
    elif operation == "counterfactual_revision_text":
        if (
            provenance_class != "synthetic_executable"
            or params.get("source_origin") != "synthetic_world"
            or params.get("parent_source_origin") != "real_derived"
            or params.get("parent_source_envelope_sha256")
            != params.get("canonical_source_envelope_sha256")
            or not str(params.get("parent_source_text_sha256") or "")
        ):
            return False
        digest_payload = {
            "operation": operation,
            "parent_provenance_id": parent,
            "text_sha256": source.text_sha256,
        }
        if params.get("source_file_spans") is not None:
            if _arxiv_source_file_texts(ev, source) is None:
                return False
            digest_payload.update(
                {
                    "source_file_spans": params.get("source_file_spans"),
                    "source_view_basenames": params.get("source_view_basenames"),
                }
            )
    else:
        return False
    return (
        params.get("provenance_id")
        == f"derived-sha256:{_canonical_digest(digest_payload)}"
    )


def _wiki_source_claims(
    ev: Event, source: GroundedSource
) -> tuple[dict[str, str], dict[str, str]] | None:
    params = ev.params
    section_id = str(params.get("section_id") or "")
    prefix = (
        WIKI_ENTITY_VIEW_PREFIX
        if section_id == "wikidata_entity"
        else WIKI_SECTION_VIEW_PREFIX
    )
    if not source.visible_text.startswith(prefix):
        return None
    section_sha256 = hashlib.sha256(
        source.visible_text[len(prefix) :].encode()
    ).hexdigest()
    if section_sha256 != params.get("section_sha256"):
        return None
    provenance_class = params.get("source_binding_provenance")
    operation = str(params.get("provenance_operation") or "")
    parent_sha256 = str(params.get("parent_source_sha256") or "")
    digest_payload: dict[str, Any]
    if operation == WIKI_SECTION_REVISION:
        if (
            provenance_class != "verified_derived"
            or params.get("source_origin") != "real_derived"
        ):
            return None
        digest_payload = {
            "operation": operation,
            "section_id": section_id,
            "parent_sha256": parent_sha256,
            "section_sha256": section_sha256,
        }
    elif operation == _WIKI_CANONICAL_FACT_SPAN:
        start = params.get("source_char_start")
        end = params.get("source_char_end")
        source_span = source.visible_text[len(prefix) :]
        if (
            provenance_class != "verified_derived"
            or params.get("source_origin") != "real_derived"
            or section_id.startswith("revision_")
            or not source.facts
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or start >= end
            or end - start != len(source_span)
            or len(source_span) > _WIKI_CANONICAL_FACT_SPAN_MAX_CHARS
            or source.visible_text != prefix + source_span
            or hashlib.sha256(source_span.encode()).hexdigest()
            != params.get("source_span_sha256")
            or not str(params.get("source_body_sha256") or "")
        ):
            return None
        digest_payload = {
            "operation": operation,
            "section_id": section_id,
            "parent_sha256": parent_sha256,
            "source_body_sha256": params.get("source_body_sha256"),
            "source_char_start": start,
            "source_char_end": end,
            "source_span_sha256": params.get("source_span_sha256"),
            "section_sha256": section_sha256,
        }
    elif operation == _WIKI_REVISION_HUNK:
        start = params.get("source_char_start")
        end = params.get("source_char_end")
        relation_id = str(params.get("relation_id") or "")
        change_kind = str(params.get("change_kind") or "")
        hunk_side = str(params.get("hunk_side") or "")
        transition_tier = str(params.get("transition_tier") or "")
        if (
            provenance_class != "verified_derived"
            or params.get("source_origin") != "real_derived"
            or len(source.facts) != 1
            or change_kind not in {"insert", "replace"}
            or hunk_side not in {"before", "after"}
            or transition_tier not in {"32k", "64k"}
            or section_id != f"revision_{transition_tier}_{hunk_side}"
            or not relation_id
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or start >= end
            or end - start != len(source.facts[0].quote)
            or hashlib.sha256(source.facts[0].quote.encode()).hexdigest()
            != params.get("source_span_sha256")
            or source.visible_text.count(source.facts[0].quote) != 1
            or source.visible_text
            != (
                f"{prefix}transition {relation_id}\nside {hunk_side}\n"
                f"change {change_kind}\nsource span\n{source.facts[0].quote}"
            )
        ):
            return None
        digest_payload = {
            "operation": operation,
            "section_id": section_id,
            "relation_id": relation_id,
            "change_kind": change_kind,
            "hunk_side": hunk_side,
            "source_record_id": params.get("section_record_id"),
            "counterpart_record_id": params.get("counterpart_record_id"),
            "source_sha256": params.get("source_sha256"),
            "counterpart_source_sha256": params.get("counterpart_source_sha256"),
            "source_body_sha256": params.get("source_body_sha256"),
            "source_char_start": start,
            "source_char_end": end,
            "source_span_sha256": params.get("source_span_sha256"),
            "section_sha256": section_sha256,
        }
    elif operation == "counterfactual_wiki_section_v1":
        if (
            provenance_class != "synthetic_executable"
            or params.get("source_origin") != "synthetic_world"
            or params.get("parent_source_origin") != "real_derived"
            or not str(params.get("parent_source_text_sha256") or "")
        ):
            return None
        digest_payload = {
            "operation": operation,
            "parent_provenance_id": str(params.get("parent_provenance_id") or ""),
            "parent_source_sha256": parent_sha256,
            "text_sha256": source.text_sha256,
        }
    else:
        return None
    if (
        params.get("provenance_id")
        != f"derived-sha256:{_canonical_digest(digest_payload)}"
    ):
        return None
    parser_revision = params.get("fact_parser_revision")
    if parser_revision not in {
        _WIKI_FACT_PARSER_REVISION,
        _WIKI_LEGACY_FACT_PARSER_REVISION,
    }:
        return None
    facts_by_id = {fact.fact_id: fact for fact in source.facts}
    spans = params.get("fact_spans")
    if not isinstance(spans, list):
        return None
    if operation == _WIKI_CANONICAL_FACT_SPAN and len(source.facts) != len(spans):
        return None
    if section_id == "appendix_rest":
        return ({}, {}) if not spans else None
    if section_id.startswith("revision_") and not spans:
        return {}, {}
    if not spans:
        return None
    claims: dict[str, str] = {}
    role_tags: dict[str, str] = {}
    for span in spans:
        if not isinstance(span, dict):
            return None
        fact = facts_by_id.get(str(span.get("fact_id") or ""))
        if (
            fact is None
            or fact.char_start != span.get("char_start")
            or fact.char_end != span.get("char_end")
            or fact.quote != span.get("evidence_quote")
            or fact.normalized_quote
            != normalize_fact_value(str(span.get("evidence_quote") or ""))
            or (
                operation
                in {
                    WIKI_SECTION_REVISION,
                    _WIKI_REVISION_HUNK,
                    _WIKI_CANONICAL_FACT_SPAN,
                }
                and span.get("parent_sha256") != parent_sha256
            )
        ):
            return None
        declared_role = str(span.get("role") or "")
        parsed = (
            _wiki_fact_role_value(section_id, declared_role, fact.quote)
            if parser_revision == _WIKI_FACT_PARSER_REVISION
            else _wiki_legacy_fact_role_value(section_id, declared_role, fact.quote)
        )
        answer_tag = (
            str(span.get("answer_tag") or "")
            if parser_revision == _WIKI_FACT_PARSER_REVISION
            else _WIKI_LEGACY_ROLE_TAGS.get(declared_role, "")
        )
        if (
            parsed is None
            or span.get("kind") != "wiki_claim"
            or span.get("role") != parsed[0]
            or span.get("value") != parsed[1]
            or source.visible_text.count(fact.quote) != 1
            or parsed[0] in claims
            or _WIKI_ANSWER_TAG.fullmatch(answer_tag) is None
            or answer_tag in role_tags.values()
        ):
            return None
        claims[parsed[0]] = parsed[1]
        role_tags[parsed[0]] = answer_tag
    return claims, role_tags


def _wiki_source_binding_valid(ev: Event, source: GroundedSource) -> bool:
    return _wiki_source_claims(ev, source) is not None


def _source_event_envelope_sha256(ev: Event) -> str:
    return _canonical_digest(
        {
            "id": ev.id,
            "type": ev.type,
            "time": ev.time.isoformat(),
            "params": ev.params,
            "visibility": list(ev.visibility),
            "preconditions": list(ev.preconditions),
            "causal_inputs": list(ev.causal_inputs),
            "required_inputs": list(ev.required_inputs),
            "relation_kinds": dict(ev.relation_kinds),
            "skipped": ev.skipped,
            "skip_reason": ev.skip_reason,
        }
    )


def _wiki_canonical_record_span_valid(
    state: WorldState, ev: Event, source: GroundedSource
) -> bool:
    operation = str(ev.params.get("provenance_operation") or "")
    canonical_operations = {
        WIKI_SECTION_REVISION,
        _WIKI_CANONICAL_FACT_SPAN,
        _WIKI_REVISION_HUNK,
    }
    if operation not in canonical_operations:
        return True
    event_digests = state.values.get("canonical_wiki_source_event_sha256") or {}
    if event_digests.get(ev.id) != _source_event_envelope_sha256(ev):
        return False
    if operation == WIKI_SECTION_REVISION:
        return True
    records = state.values.get("canonical_wiki_source_records") or {}
    record = records.get(str(ev.params.get("section_record_id") or "")) or {}
    body = record.get("body")
    start = ev.params.get("source_char_start")
    end = ev.params.get("source_char_end")
    prefix = (
        WIKI_ENTITY_VIEW_PREFIX
        if ev.params.get("section_id") == "wikidata_entity"
        else WIKI_SECTION_VIEW_PREFIX
    )
    visible_span = (
        source.facts[0].quote
        if operation == _WIKI_REVISION_HUNK and len(source.facts) == 1
        else source.visible_text[len(prefix) :]
    )
    if (
        not isinstance(body, str)
        or isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= start < end <= len(body)
        or visible_span != body[start:end]
    ):
        return False
    return (
        ev.params.get("source_body_sha256") == record.get("body_sha256")
        and ev.params.get("parent_source_sha256") == record.get("text_sha256")
        and ev.params.get("source_sha256") == record.get("source_sha256")
        and ev.params.get("parent_provenance_id") == record.get("provenance_id")
        and ev.params.get("source_span_sha256")
        == hashlib.sha256(body[start:end].encode()).hexdigest()
    )


def _wiki_relation_binding_valid(state: WorldState, ev: Event) -> bool:
    params = ev.params
    source_event_id = str(params.get("source_record_event_id") or "")
    target_event_id = str(params.get("target_record_event_id") or "")
    source_record_id = str(params.get("source_record_id") or "")
    target_record_id = str(params.get("target_record_id") or "")
    relation_id = str(params.get("relation_id") or "")
    relation_kind = str(params.get("relation_kind") or "")
    evidence_quote = str(params.get("evidence_quote") or "")
    bindings = state.values.get("source_grounded_bindings") or {}
    if (
        relation_kind
        not in {"revision_of", "page_describes_entity", "entity_resolves_page"}
        or not relation_id
        or not evidence_quote
        or not source_event_id
        or not target_event_id
        or source_event_id == target_event_id
        or not source_record_id
        or not target_record_id
        or source_record_id == target_record_id
        or source_event_id not in bindings
        or target_event_id not in bindings
        or params.get("source_binding_provenance") != "authentic_source_api"
        or params.get("relation_provenance") != "authentic_source_api"
        or params.get("source_origin") != "real_derived"
    ):
        return False
    digest_payload = {
        "workflow_id": str(params.get("workflow_id") or ""),
        "relation_id": relation_id,
        "kind": relation_kind,
        "source_provenance_id": str(params.get("source_provenance_id") or ""),
        "target_provenance_id": str(params.get("target_provenance_id") or ""),
        "evidence_quote": evidence_quote,
        "evidence_char_start": params.get("evidence_char_start"),
    }
    return params.get("provenance_id") == (
        f"derived-sha256:{_canonical_digest(digest_payload)}"
    )


def _relation_binding_valid(state: WorldState, ev: Event) -> bool:
    relation = _grounded_relation(ev.params.get("grounded_relation"))
    if (
        relation.claimed_provenance_class != "authentic_source_api"
        or relation.relation_id != str(ev.params.get("relation_id") or "")
        or relation.relation_type != str(ev.params.get("relation_kind") or "")
        or relation.source_id != str(ev.params.get("source_record_event_id") or "")
        or relation.target_id != str(ev.params.get("target_record_event_id") or "")
    ):
        return False
    bindings = state.values.get("source_grounded_bindings") or {}
    metadata = state.values.get("source_record_metadata") or {}
    source_metadata = metadata.get(str(ev.params.get("source_record_id") or "")) or {}
    target_metadata = metadata.get(str(ev.params.get("target_record_id") or "")) or {}
    if ev.params.get("source_revision_id") != source_metadata.get(
        "revision_id"
    ) or ev.params.get("target_revision_id") != target_metadata.get("revision_id"):
        return False
    source = _grounded_source(bindings.get(relation.source_id))
    target = _grounded_source(bindings.get(relation.target_id))
    if ev.params.get("relation_mode") == "lineage_only":
        source = GroundedSource(
            source_id=source.source_id,
            visible_text=source.visible_text,
            text_sha256=source.text_sha256,
            facts=source.facts,
            relations=(relation,),
        )
        validate_grounded_sources((source, target))
        return True
    source_fact = next(
        (
            fact
            for fact in source.facts
            if fact.fact_id == str(ev.params.get("grounded_fact_id") or "")
        ),
        None,
    )
    if (
        source_fact is None
        or source_fact.fact_id not in relation.evidence_fact_ids
        or source_fact.char_start != ev.params.get("fact_char_start")
        or source_fact.char_end != ev.params.get("fact_char_end")
    ):
        return False
    source = GroundedSource(
        source_id=source.source_id,
        visible_text=source.visible_text,
        text_sha256=source.text_sha256,
        facts=source.facts,
        relations=(relation,),
    )
    validate_grounded_sources((source, target))
    return True


def _arxiv_section_claim_valid(ev: Event, source: GroundedSource) -> bool:
    claim_id = str(ev.params.get("section_claim_id") or "")
    claim_quote = str(ev.params.get("section_claim_quote") or "")
    if not claim_id and not claim_quote:
        return True
    if not claim_id or not claim_quote or source.visible_text.count(claim_quote) != 1:
        return False
    expected_fact_id = f"{ev.id}:section_claim:{claim_id}"
    matching = [fact for fact in source.facts if fact.fact_id == expected_fact_id]
    if len(matching) != 1:
        return False
    fact = matching[0]
    return (
        fact.quote == claim_quote
        and source.visible_text[fact.char_start : fact.char_end] == claim_quote
    )


def _canonical_wiki_source_records(project: dict[str, Any]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for workflow in project.get("source_workflows") or []:
        if getattr(workflow, "source_kind", "") != "wikimedia":
            continue
        for record in getattr(workflow, "records", ()):
            if record.kind == "wikipedia_revision":
                try:
                    body = extract_wikipedia_wikitext(record.text)[2]
                except ProvenanceError:
                    continue
            elif record.kind == "wikidata_entity_revision":
                body = record.text
            else:
                continue
            text_sha256 = hashlib.sha256(record.text.encode()).hexdigest()
            if text_sha256 != record.text_sha256:
                raise ValueError("canonical Wikimedia record text hash mismatches")
            value = {
                "body": body,
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "text_sha256": text_sha256,
                "source_sha256": record.source_sha256,
                "provenance_id": record.provenance_id,
            }
            prior = records.get(record.record_id)
            if prior is not None and prior != value:
                raise ValueError("canonical Wikimedia record ID is ambiguous")
            records[record.record_id] = value
    return records


def _canonical_wiki_source_event_sha256(events: list[Event]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for event in events:
        if event.type != "wiki_source_section" or event.params.get(
            "provenance_operation"
        ) not in {
            WIKI_SECTION_REVISION,
            _WIKI_CANONICAL_FACT_SPAN,
            _WIKI_REVISION_HUNK,
        }:
            continue
        digest = _source_event_envelope_sha256(event)
        prior = digests.get(event.id)
        if prior is not None and prior != digest:
            raise ValueError("canonical Wikimedia source event ID is ambiguous")
        digests[event.id] = digest
    return digests


def init_values(
    project: dict[str, Any], *, canonical_source_events: list[Event] | None = None
) -> dict[str, Any]:
    return {
        "reported_score": None,
        "eval_stale": False,
        "testset_ok": True,
        "rerun_score": None,
        "table_score": None,
        "body_score": None,
        "authoritative_score": None,
        "tokenizer_commit": None,
        "cause_cache": None,
        "cause_split": None,
        "inflation_cause": None,
        "issue_filed": False,
        "fixed": False,
        "release_published": False,
        "pending_license": None,
        "ship_license": None,
        "paper": project["paper"],
        "model": project["model"],
        "benchmark": project["benchmark"],
        "withdrawn_score": None,
        "score_license_at_withdraw": None,
        "dataset_version": None,
        "pending_public_primary": None,
        "pending_public_alt": None,
        "public_norm": None,
        "pending_latent": None,
        "pending_decoy": None,
        "acked_latent": None,
        "active_latent": None,
        "controlling_latent": None,
        "pending_docket": None,
        "controlling_docket": None,
        "revision_candidate": None,
        "benchmark_candidate": None,
        "active_review_requirement": None,
        "active_review_response": None,
        "failed_reproduction": None,
        "benchmark_frozen": None,
        "successful_reproduction": None,
        "resolved_review_requirement": None,
        "resolved_review_response": None,
        "resolved_revision": None,
        "accepted_revision": None,
        "accepted_benchmark": None,
        "accepted_reproduction": None,
        "accepted_review_response": None,
        "experiment_matrix": None,
        "source_record_texts": {},
        "source_record_metadata": {},
        "source_grounded_bindings": {},
        "real_revision_delta_candidate": None,
        "real_revision_added_text": None,
        "verified_revision_relations": [],
        "verified_revision_relation_events": [],
        "paper_section_claims": {},
        "paper_section_controls": {},
        "wiki_claims": {},
        "wiki_claim_tags": {},
        "wiki_source_relations": [],
        "canonical_wiki_source_records": _canonical_wiki_source_records(project),
        "canonical_wiki_source_event_sha256": _canonical_wiki_source_event_sha256(
            canonical_source_events or []
        ),
    }


def check_preconditions(state: WorldState, ev: Event) -> tuple[bool, str | None]:
    grounded = check_grounded(state, ev)
    if grounded is not None:
        return grounded
    casc = check_cascade(state, ev)
    if casc is not None:
        return casc
    t = ev.type
    if t in {"report_v1", "license_clause"}:
        return True, None
    if t in {"arxiv_revision", "arxiv_revision_context"}:
        try:
            source = _source_binding(ev)
        except (GroundedSpanError, TypeError):
            return False, "source_grounded_binding_invalid"
        if not _arxiv_source_binding_valid(ev, source):
            return False, "source_derived_lineage_invalid"
        if not _arxiv_section_claim_valid(ev, source):
            return False, "source_section_claim_invalid"
        return True, None
    if t == "arxiv_revision_relation":
        required_prior_event = str(
            ev.params.get("required_prior_relation_event_id") or ""
        )
        verified_relation_events = set(
            state.values.get("verified_revision_relation_events") or []
        )
        if (
            required_prior_event
            and required_prior_event not in verified_relation_events
        ):
            return False, "source_prior_revision_relation_missing"
        texts = state.values.get("source_record_texts") or {}
        if not all(
            texts.get(str(ev.params.get(field) or ""))
            for field in ("source_record_id", "target_record_id")
        ):
            return False, "source_revision_endpoint_missing"
        try:
            valid = _relation_binding_valid(state, ev)
        except (GroundedSpanError, TypeError):
            valid = False
        return (True, None) if valid else (False, "source_relation_binding_invalid")
    if t == "arxiv_benchmark_trace_decision":
        benchmark_required_relations = set(ev.params.get("required_relation_ids") or [])
        benchmark_verified_relations = set(
            state.values.get("verified_revision_relations") or []
        )
        if not benchmark_required_relations.issubset(benchmark_verified_relations):
            return False, "source_revision_chain_incomplete"
        return (
            (True, None)
            if _benchmark_trace_from_state(state, ev) is not None
            else (False, "source_benchmark_trace_invalid")
        )
    if t == "arxiv_revision_decision":
        mode = str(ev.params.get("decision_mode") or "relation_delta")
        if mode == "direct_delta":
            bindings = state.values.get("source_grounded_bindings") or {}
            if any(
                str(ev.params.get(field) or "") not in bindings
                for field in ("source_record_event_id", "target_record_event_id")
            ):
                return False, "source_revision_endpoint_missing"
        elif not state.values.get(
            str(ev.params.get("candidate_key") or "real_revision_delta_candidate")
        ):
            return False, "source_revision_relation_missing"
        paper_required_relations = set(ev.params.get("required_relation_ids") or [])
        paper_verified_relations = set(
            state.values.get("verified_revision_relations") or []
        )
        if not paper_required_relations.issubset(paper_verified_relations):
            return False, "source_revision_chain_incomplete"
        return True, None
    if t == "arxiv_section_reconciliation_control":
        required_claim_ids = [
            str(value) for value in ev.params.get("required_claim_ids") or []
        ]
        claim_event_ids = [
            str(value) for value in ev.params.get("claim_event_ids") or []
        ]
        claims = state.values.get("paper_section_claims") or {}
        if (
            not required_claim_ids
            or len(required_claim_ids) != len(set(required_claim_ids))
            or len(claim_event_ids) != len(required_claim_ids)
            or any(
                (claims.get(claim_id) or {}).get("event_id") != event_id
                for claim_id, event_id in zip(required_claim_ids, claim_event_ids)
            )
        ):
            return False, "source_section_claims_incomplete"
        if str(ev.params.get("target_record_event_id") or "") not in (
            state.values.get("source_grounded_bindings") or {}
        ):
            return False, "source_revision_endpoint_missing"
        if str(ev.params.get("relation_event_id") or "") not in set(
            state.values.get("verified_revision_relation_events") or []
        ):
            return False, "source_revision_relation_missing"
        if str(ev.params.get("required_relation_id") or "") not in set(
            state.values.get("verified_revision_relations") or []
        ):
            return False, "source_revision_chain_incomplete"
        return True, None
    if t == "arxiv_section_reconciliation_decision":
        required_claim_ids = [
            str(value) for value in ev.params.get("required_claim_ids") or []
        ]
        control_key = str(ev.params.get("control_key") or "")
        controls = state.values.get("paper_section_controls") or {}
        if controls.get(control_key) != required_claim_ids:
            return False, "source_section_control_missing"
        if str(ev.params.get("required_relation_id") or "") not in set(
            state.values.get("verified_revision_relations") or []
        ):
            return False, "source_revision_chain_incomplete"
        return True, None
    if t == "wiki_source_section":
        try:
            source = _source_binding(ev)
        except (GroundedSpanError, TypeError):
            return False, "source_grounded_binding_invalid"
        if not _wiki_source_binding_valid(
            ev, source
        ) or not _wiki_canonical_record_span_valid(state, ev, source):
            return False, "source_derived_lineage_invalid"
        return True, None
    if t == "wiki_source_relation":
        return (
            (True, None)
            if _wiki_relation_binding_valid(state, ev)
            else (False, "wiki_source_relation_binding_invalid")
        )
    if t == "wiki_claim_answer":
        if str(ev.params.get("compose") or "compute") == "copy":
            if not state.values.get(
                str(ev.params.get("prerequisite_answer_key") or "")
            ):
                return False, "wiki_claim_prerequisite_missing"
            return True, None
        record_id = str(ev.params.get("record_id") or "")
        claims = dict((state.values.get("wiki_claims") or {}).get(record_id) or {})
        claim_tags = dict(
            (state.values.get("wiki_claim_tags") or {}).get(record_id) or {}
        )
        required = ev.params.get("required_roles") or []
        if not isinstance(required, list) or any(
            str(role) not in claims for role in required
        ):
            return False, "wiki_claim_roles_missing"
        role_tags = ev.params.get("role_tags")
        if role_tags is not None and (
            not isinstance(role_tags, dict)
            or set(role_tags) != {str(role) for role in required}
            or any(
                claim_tags.get(str(role)) != role_tags.get(str(role))
                for role in required
            )
        ):
            return False, "wiki_claim_tags_invalid"
        required_relations = ev.params.get("required_relation_ids") or []
        available_relations = set(state.values.get("wiki_source_relations") or [])
        if not isinstance(required_relations, list) or any(
            str(relation_id) not in available_relations
            for relation_id in required_relations
        ):
            return False, "wiki_source_relations_missing"
        return True, None
    if t in {"commit_tokenizer", "log_stale_cache", "issue_testset", "status_pulse"}:
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "fix_rerun":
        if not state.values.get("issue_filed"):
            return False, "issue_not_filed"
        return True, None
    if t in {"camera_ready", "release_note"}:
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "invalidate_run":
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "submit_revision_2":
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "review_round_1":
        if not state.values.get("revision_candidate"):
            return False, "no_revision_2"
        return True, None
    if t == "respond_round_1":
        if not state.values.get("active_review_requirement"):
            return False, "no_review_round_1"
        return True, None
    if t == "reproduction_failure":
        if not state.values.get("active_review_response"):
            return False, "no_response_round_1"
        return True, None
    if t == "benchmark_patch":
        if not state.values.get("failed_reproduction"):
            return False, "no_failed_reproduction"
        return True, None
    if t == "review_round_2":
        if not state.values.get("benchmark_frozen"):
            return False, "no_benchmark_patch"
        return True, None
    if t == "respond_round_2":
        if not state.values.get("active_review_requirement"):
            return False, "no_review_round_2"
        return True, None
    if t == "reproduction_recovery":
        if not state.values.get("active_review_response"):
            return False, "no_response_round_2"
        if not state.values.get("benchmark_frozen"):
            return False, "no_frozen_benchmark"
        return True, None
    if t == "submit_revision_3":
        if not state.values.get("successful_reproduction"):
            return False, "no_successful_reproduction"
        return True, None
    if t == "resolve_review":
        if not state.values.get("revision_candidate"):
            return False, "no_revision_3"
        if not state.values.get("active_review_response"):
            return False, "no_response_round_2"
        return True, None
    if t == "meta_decision":
        if not state.values.get("resolved_revision"):
            return False, "review_not_resolved"
        if not state.values.get("successful_reproduction"):
            return False, "reproduction_not_recovered"
        return True, None
    workstream = str(ev.params.get("workstream") or "")
    if t == "experiment_revision":
        return True, None
    if t == "experiment_review":
        if not state.values.get(f"experiment:{workstream}:revision"):
            return False, "experiment_revision_missing"
        return True, None
    if t == "experiment_benchmark":
        if not state.values.get(f"experiment:{workstream}:review"):
            return False, "experiment_review_missing"
        return True, None
    if t == "experiment_failure":
        if not state.values.get(f"experiment:{workstream}:benchmark"):
            return False, "experiment_benchmark_missing"
        return True, None
    if t == "experiment_recovery":
        if not state.values.get(f"experiment:{workstream}:failure"):
            return False, "experiment_failure_missing"
        return True, None
    if t == "experiment_matrix_decision":
        workstream_ids = [str(value) for value in ev.params.get("workstream_ids") or []]
        if not workstream_ids or any(
            not state.values.get(f"experiment:{workstream_id}:recovery")
            for workstream_id in workstream_ids
        ):
            return False, "experiment_recovery_set_incomplete"
        return True, None
    return True, None


def apply_event(state: WorldState, ev: Event) -> None:
    if apply_grounded(state, ev):
        return
    if apply_cascade(state, ev):
        return
    t = ev.type
    p = ev.params
    eid = ev.id
    day = ev.time
    if t == "license_clause":
        state.set("pending_license", p["spdx"], eid, day)
    elif t in {"arxiv_revision", "arxiv_revision_context"}:
        try:
            source = _source_binding(ev)
        except (GroundedSpanError, TypeError):
            return
        if not _arxiv_source_binding_valid(ev, source):
            return
        if not _arxiv_section_claim_valid(ev, source):
            return
        if t == "arxiv_revision_context":
            return
        headers = _arxiv_headers(source)
        if headers is None:
            return
        text = str(p.get("text") or "")
        if hashlib.sha256(text.encode()).hexdigest() != p.get("text_sha256"):
            return
        texts = dict(state.values.get("source_record_texts") or {})
        texts[str(p["record_id"])] = text
        texts[eid] = text
        state.set("source_record_texts", texts, eid, day)
        metadata = dict(state.values.get("source_record_metadata") or {})
        record_metadata = {
            "occurred_at": headers["submitted_at"],
            "revision_id": headers["revision_id"],
            "source_files": _arxiv_source_file_texts(ev, source) or {},
        }
        metadata[str(p["record_id"])] = record_metadata
        metadata[eid] = record_metadata
        state.set("source_record_metadata", metadata, eid, day)
        bindings = dict(state.values.get("source_grounded_bindings") or {})
        bindings[eid] = p["grounded_source"]
        state.set("source_grounded_bindings", bindings, eid, day)
        claim_id = str(p.get("section_claim_id") or "")
        if claim_id:
            claims = dict(state.values.get("paper_section_claims") or {})
            claims[claim_id] = {
                "event_id": eid,
                "quote": str(p["section_claim_quote"]),
                "source_path": str(p["section_source_path"]),
            }
            state.set("paper_section_claims", claims, eid, day)
    elif t == "arxiv_revision_relation":
        required_prior_event = str(p.get("required_prior_relation_event_id") or "")
        verified_relation_events = list(
            state.values.get("verified_revision_relation_events") or []
        )
        if (
            required_prior_event
            and required_prior_event not in verified_relation_events
        ):
            return
        try:
            if not _relation_binding_valid(state, ev):
                return
        except (GroundedSpanError, TypeError):
            return
        verified_relations = list(state.values.get("verified_revision_relations") or [])
        relation_id = str(p.get("relation_id") or "")
        if relation_id and relation_id not in verified_relations:
            verified_relations.append(relation_id)
            state.set("verified_revision_relations", verified_relations, eid, day)
        if eid not in verified_relation_events:
            verified_relation_events.append(eid)
            state.set(
                "verified_revision_relation_events",
                verified_relation_events,
                eid,
                day,
            )
        if p.get("relation_mode") == "lineage_only":
            return
        texts = state.values.get("source_record_texts") or {}
        source_text = str(
            texts.get(str(p.get("source_record_event_id") or ""))
            or texts.get(str(p["source_record_id"]))
            or ""
        )
        target_text = str(
            texts.get(str(p.get("target_record_event_id") or ""))
            or texts.get(str(p["target_record_id"]))
            or ""
        )
        if not source_text or not target_text:
            return
        evidence_quote = str(p["evidence_quote"])
        if not evidence_quote:
            return
        start = int(p["fact_char_start"])
        end = int(p["fact_char_end"])
        offset = int(p["fact_value_offset"])
        value_length = int(p["fact_value_length"])
        quote = source_text[start:end]
        value = quote[offset : offset + value_length]
        if not value or value in target_text:
            return
        metadata = state.values.get("source_record_metadata") or {}
        prior = (
            metadata.get(str(p.get("target_record_event_id") or ""))
            or metadata.get(str(p["target_record_id"]))
            or {}
        )
        prior_date = str(prior.get("occurred_at") or "")[:10]
        if not prior_date:
            return
        state.set(
            str(p.get("candidate_key") or "real_revision_delta_candidate"),
            {"prior_date": prior_date, "value": value},
            eid,
            day,
        )
    elif t == "arxiv_benchmark_trace_decision":
        benchmark_required_relations = set(p.get("required_relation_ids") or [])
        benchmark_verified_relations = set(
            state.values.get("verified_revision_relations") or []
        )
        if not benchmark_required_relations.issubset(benchmark_verified_relations):
            return
        resolved = _benchmark_trace_from_state(state, ev)
        if resolved is None:
            return
        _observations, answer = resolved
        answer_key = str(p.get("answer_key") or "")
        if not answer_key:
            return
        state.set(answer_key, answer, eid, day)
        state.set("real_benchmark_revision_trace", answer, eid, day)
    elif t == "arxiv_revision_decision":
        mode = str(p.get("decision_mode") or "relation_delta")
        paper_required_relations = set(p.get("required_relation_ids") or [])
        paper_verified_relations = set(
            state.values.get("verified_revision_relations") or []
        )
        if not paper_required_relations.issubset(paper_verified_relations):
            return
        candidate = state.values.get(
            str(p.get("candidate_key") or "real_revision_delta_candidate")
        )
        if mode == "direct_delta":
            texts = state.values.get("source_record_texts") or {}
            source_event_id = str(p.get("source_record_event_id") or "")
            target_event_id = str(p.get("target_record_event_id") or "")
            source_text = str(
                texts.get(source_event_id)
                or texts.get(str(p.get("source_record_id") or ""))
                or ""
            )
            target_text = str(
                texts.get(target_event_id)
                or texts.get(str(p.get("target_record_id") or ""))
                or ""
            )
            required_new_include = str(p.get("required_new_include") or "")
            marker_basename = str(p.get("required_new_include_source_basename") or "")
            fact_basename = str(p.get("fact_source_basename") or "")
            metadata = state.values.get("source_record_metadata") or {}
            source_metadata = metadata.get(source_event_id) or {}
            target_metadata = metadata.get(target_event_id) or {}
            source_files = source_metadata.get("source_files") or {}
            target_files = target_metadata.get("source_files") or {}
            source_marker_text = str(source_files.get(marker_basename) or "")
            target_marker_text = str(target_files.get(marker_basename) or "")
            if (
                not required_new_include
                or not marker_basename
                or not fact_basename
                or required_new_include in target_marker_text
                or required_new_include not in source_marker_text
            ):
                return
            start = int(p.get("fact_char_start") or 0)
            end = int(p.get("fact_char_end") or 0)
            offset = int(p.get("fact_value_offset") or 0)
            value_length = int(p.get("fact_value_length") or 0)
            value = source_text[start:end][offset : offset + value_length]
            if (
                not value
                or str(source_files.get(fact_basename) or "").count(value) != 1
            ):
                return
            prior = (
                target_metadata
                or metadata.get(str(p.get("target_record_id") or ""))
                or {}
            )
            prior_date = str(prior.get("occurred_at") or "")[:10]
            candidate = (
                {"prior_date": prior_date, "value": value}
                if value and value not in target_text and prior_date
                else None
            )
        if not isinstance(candidate, dict):
            return
        answer = _format_revision_delta(str(candidate.get("value") or ""))
        prior_date = str(candidate.get("prior_date") or "")
        if answer and prior_date:
            value = f"{prior_date} | {answer}"
            if p.get("terminal_record_id"):
                metadata = state.values.get("source_record_metadata") or {}
                terminal = (
                    metadata.get(str(p.get("terminal_record_event_id") or ""))
                    or metadata.get(str(p.get("terminal_record_id") or ""))
                    or {}
                )
                terminal_date = str(terminal.get("occurred_at") or "")[:10]
                if not terminal_date:
                    return
                terminal_revision = str(p.get("terminal_revision_id") or "v3")
                value += f" | {terminal_revision} {terminal_date}"
            answer_key = str(p.get("answer_key") or "real_revision_added_text")
            state.set(answer_key, value, eid, day)
            state.set("real_revision_added_text", value, eid, day)
    elif t == "arxiv_section_reconciliation_control":
        valid, _reason = check_preconditions(state, ev)
        if not valid:
            return
        controls = dict(state.values.get("paper_section_controls") or {})
        controls[str(p["control_key"])] = [
            str(value) for value in p["required_claim_ids"]
        ]
        state.set("paper_section_controls", controls, eid, day)
    elif t == "arxiv_section_reconciliation_decision":
        valid, _reason = check_preconditions(state, ev)
        if not valid:
            return
        claims = state.values.get("paper_section_claims") or {}
        required_claim_ids = [str(value) for value in p["required_claim_ids"]]
        if any(claim_id not in claims for claim_id in required_claim_ids):
            return
        answer = "v5 revision_of v4 || " + " || ".join(
            str(claims[claim_id]["quote"]) for claim_id in required_claim_ids
        )
        state.set(str(p["answer_key"]), answer, eid, day)
    elif t == "wiki_source_section":
        try:
            source = _source_binding(ev)
        except (GroundedSpanError, TypeError):
            return
        parsed = _wiki_source_claims(ev, source)
        if parsed is None:
            return
        parsed_claims, parsed_tags = parsed
        text = str(p.get("text") or "")
        if hashlib.sha256(text.encode()).hexdigest() != p.get("text_sha256"):
            return
        record_id = str(p.get("record_id") or "")
        if not record_id:
            return
        claims = dict(state.values.get("wiki_claims") or {})
        record_claims = dict(claims.get(record_id) or {})
        record_claims.update(parsed_claims)
        claims[record_id] = record_claims
        state.set("wiki_claims", claims, eid, day)
        tags = dict(state.values.get("wiki_claim_tags") or {})
        record_tags = dict(tags.get(record_id) or {})
        record_tags.update(parsed_tags)
        tags[record_id] = record_tags
        state.set("wiki_claim_tags", tags, eid, day)
        bindings = dict(state.values.get("source_grounded_bindings") or {})
        bindings[eid] = p["grounded_source"]
        state.set("source_grounded_bindings", bindings, eid, day)
    elif t == "wiki_source_relation":
        if not _wiki_relation_binding_valid(state, ev):
            return
        relations = list(state.values.get("wiki_source_relations") or [])
        relation_id = str(p.get("relation_id") or "")
        if relation_id not in relations:
            relations.append(relation_id)
        state.set("wiki_source_relations", relations, eid, day)
    elif t == "wiki_claim_answer":
        record_id = str(p.get("record_id") or "")
        answer_key = str(p.get("answer_key") or "")
        if not record_id or not answer_key:
            return
        if str(p.get("compose") or "compute") == "copy":
            prior = state.values.get(str(p.get("prerequisite_answer_key") or ""))
            if not isinstance(prior, str) or not prior:
                return
            state.set(answer_key, prior, eid, day)
            return
        tier = str(p.get("control_tier") or "")
        claims = dict((state.values.get("wiki_claims") or {}).get(record_id) or {})
        claim_tags = dict(
            (state.values.get("wiki_claim_tags") or {}).get(record_id) or {}
        )
        required = p.get("required_roles")
        if not isinstance(required, list) or any(
            str(role) not in claims for role in required
        ):
            return
        required_relations = p.get("required_relation_ids") or []
        available_relations = set(state.values.get("wiki_source_relations") or [])
        if not isinstance(required_relations, list) or any(
            str(relation_id) not in available_relations
            for relation_id in required_relations
        ):
            return
        prerequisite_key = str(p.get("prerequisite_answer_key") or "")
        prior = state.values.get(prerequisite_key) if prerequisite_key else ""
        if prerequisite_key and not isinstance(prior, str):
            return
        if tier not in {"16k", "32k", "64k"}:
            return
        role_tags = p.get("role_tags")
        append_roles = p.get("append_roles")
        if role_tags is None or append_roles is None:
            role_tags = {
                role: _WIKI_LEGACY_ROLE_TAGS[role]
                for role in required
                if role in _WIKI_LEGACY_ROLE_TAGS
            }
            append_roles = (
                required
                if tier == "16k"
                else ["commemoration", "entity"]
                if tier == "32k"
                else ["popular_culture"]
            )
        if (
            not isinstance(role_tags, dict)
            or not isinstance(append_roles, list)
            or any(str(role) not in required for role in append_roles)
            or any(
                claim_tags.get(str(role)) != role_tags.get(str(role))
                for role in required
            )
        ):
            return
        if tier != "16k" and not prior:
            return
        parts = [str(prior)] if prior else []
        parts.extend(
            f"{role_tags[str(role)]}:{claims[str(role)]}" for role in append_roles
        )
        state.set(answer_key, "||".join(parts), eid, day)
    elif t == "report_v1":
        state.set("reported_score", p["score"], eid, day)
        state.set("authoritative_score", p["score"], eid, day)
        state.set("body_score", p["score"], eid, day)
        state.set("eval_stale", True, eid, day)
        state.set("testset_ok", False, eid, day)
    elif t == "commit_tokenizer":
        state.set("tokenizer_commit", p["commit"], eid, day)
    elif t == "log_stale_cache":
        state.set("cause_cache", p["cause_cache"], eid, day)
    elif t == "issue_testset":
        if p.get("filed", True):
            state.set("issue_filed", True, eid, day)
            state.set("cause_split", p["cause_split"], eid, day)
    elif t == "fix_rerun":
        if p.get("aborted"):
            return
        state.set("eval_stale", False, eid, day)
        state.set("testset_ok", True, eid, day)
        state.set("fixed", True, eid, day)
        state.set("rerun_score", p["score"], eid, day)
        state.set("table_score", p["score"], eid, day)
    elif t == "camera_ready":
        # Table follows rerun if present; body may linger at v1.
        table = state.values.get("rerun_score") or state.values.get("reported_score")
        body = p.get("body_score", state.values.get("reported_score"))
        state.set("table_score", table, eid, day)
        state.set("body_score", body, eid, day)
    elif t == "release_note":
        state.set("release_published", True, eid, day)
        if p.get("adopt_rerun"):
            rs = state.values.get("rerun_score")
            if rs is not None:
                state.set("authoritative_score", rs, eid, day)
            else:
                state.set(
                    "authoritative_score", state.values.get("reported_score"), eid, day
                )
        else:
            state.set(
                "authoritative_score",
                p.get("score", state.values.get("reported_score")),
                eid,
                day,
            )
        pending = state.values.get("pending_license")
        if pending:
            state.set("ship_license", pending, eid, day)
    elif t == "invalidate_run":
        state.set("dataset_version", p["dataset"], eid, day)
        rs = state.values.get("reported_score")
        pending = state.values.get("pending_license")
        if rs is not None:
            state.set("withdrawn_score", rs, eid, day)
        elif pending is not None:
            state.set("withdrawn_score", 0, eid, day)
        else:
            return
        if pending is None:
            return
        state.set("score_license_at_withdraw", pending, eid, day)
    elif t == "status_pulse":
        return
    elif t == "submit_revision_2":
        state.set("revision_candidate", p["revision"], eid, day)
        state.set("benchmark_candidate", p["benchmark"], eid, day)
    elif t in {"review_round_1", "review_round_2"}:
        state.set("active_review_requirement", p["requirement"], eid, day)
    elif t in {"respond_round_1", "respond_round_2"}:
        state.set("active_review_response", p["response"], eid, day)
    elif t == "reproduction_failure":
        state.set("failed_reproduction", p["run"], eid, day)
    elif t == "benchmark_patch":
        state.set("benchmark_frozen", p["benchmark"], eid, day)
    elif t == "reproduction_recovery":
        state.set("successful_reproduction", p["run"], eid, day)
    elif t == "submit_revision_3":
        state.set("revision_candidate", p["revision"], eid, day)
    elif t == "resolve_review":
        state.set(
            "resolved_review_requirement",
            state.values.get("active_review_requirement"),
            eid,
            day,
        )
        state.set(
            "resolved_review_response",
            state.values.get("active_review_response"),
            eid,
            day,
        )
        state.set("resolved_revision", state.values.get("revision_candidate"), eid, day)
    elif t == "meta_decision":
        if not p.get("accepted", True):
            return
        state.set("accepted_revision", state.values.get("resolved_revision"), eid, day)
        state.set("accepted_benchmark", state.values.get("benchmark_frozen"), eid, day)
        state.set(
            "accepted_reproduction",
            state.values.get("successful_reproduction"),
            eid,
            day,
        )
        state.set(
            "accepted_review_response",
            state.values.get("resolved_review_response"),
            eid,
            day,
        )
    elif t == "experiment_revision":
        state.set(f"experiment:{p['workstream']}:revision", p["revision"], eid, day)
    elif t == "experiment_review":
        state.set(f"experiment:{p['workstream']}:review", p["review"], eid, day)
    elif t == "experiment_benchmark":
        state.set(f"experiment:{p['workstream']}:benchmark", p["benchmark"], eid, day)
    elif t == "experiment_failure":
        state.set(f"experiment:{p['workstream']}:failure", p["failure"], eid, day)
    elif t == "experiment_recovery":
        state.set(f"experiment:{p['workstream']}:recovery", p["run"], eid, day)
    elif t == "experiment_matrix_decision":
        workstream_ids = [str(value) for value in p.get("workstream_ids") or []]
        runs = [
            str(state.values.get(f"experiment:{workstream_id}:recovery") or "")
            for workstream_id in workstream_ids
        ]
        if not workstream_ids or any(not run for run in runs):
            return
        state.set("experiment_matrix", " | ".join(runs), eid, day)
