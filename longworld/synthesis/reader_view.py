"""Compile a P74 task into model-visible documents and a separate audit record.

The reader receives source documents and a question. Programs, fact indices,
proofs, and certificates stay in the audit record. Character offsets refer to
the exact context string placed in the user message; they make no claim about
token distance or about the absence of equivalent evidence elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any

from longworld.synthesis.shared_semantic_world import SemanticWorld, SpanRef
from longworld.synthesis.world_task_bank import TaskSpec

SCHEMA = "longworld.reader-view.v1"
PROFILE = "real-native"
QUESTION_SEPARATOR = "\n\nQUESTION\n"
NATURAL_RELATION_QUESTIONS = {
    "established in": "In what year was {label} established?",
    "located in": "Where is {label} located?",
    "opened in": "When did {label} open?",
    "closed in": "When did {label} close?",
    "birth_place": "Where was {label} born?",
    "death_place": "Where did {label} die?",
    "country/region": "Which country or region is listed for {label}?",
    "easiest_route": "What is the easiest route listed for {label}?",
    "elevation_m": "What elevation in meters is listed for {label}?",
    "last_eruption": "When was the last eruption of {label}?",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class DocumentLayout:
    doc_id: str
    title: str
    text_start: int
    text_end: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "text_start": self.text_start,
            "text_end": self.text_end,
        }


@dataclass(frozen=True)
class ReaderContext:
    text: str
    layouts: tuple[DocumentLayout, ...]

    def joined_span(self, span: SpanRef) -> tuple[int, int]:
        for layout in self.layouts:
            if layout.doc_id == span.doc_id:
                length = layout.text_end - layout.text_start
                if not 0 <= span.start < span.end <= length:
                    raise ValueError(f"span outside document {span.doc_id}")
                return layout.text_start + span.start, layout.text_start + span.end
        raise ValueError(f"span document {span.doc_id} is absent from reader context")


def render_documents(world: SemanticWorld, doc_ids: tuple[str, ...]) -> ReaderContext:
    """Keep whole frozen documents in their world order, with exact offsets."""
    selected = set(doc_ids)
    known = {doc.doc_id for doc in world.documents}
    if not selected or selected - known:
        raise ValueError(f"invalid scoped documents: {sorted(selected - known)}")
    parts: list[str] = []
    layouts: list[DocumentLayout] = []
    offset = 0
    for doc in world.documents:
        if doc.doc_id not in selected:
            continue
        header = f"[{doc.doc_id}] {doc.title}\n"
        parts.append(header)
        offset += len(header)
        layouts.append(
            DocumentLayout(doc.doc_id, doc.title, offset, offset + len(doc.text))
        )
        parts.append(doc.text)
        offset += len(doc.text)
        parts.append("\n\n")
        offset += 2
    return ReaderContext("".join(parts[:-1]), tuple(layouts))


def _proof_spans(
    world: SemanticWorld, task: TaskSpec, context: ReaderContext
) -> list[dict[str, Any]]:
    docs = {doc.doc_id: doc for doc in world.documents}
    mapped: list[dict[str, Any]] = []
    for item in task.proof:
        if len(item.spans) != len(item.span_texts):
            raise ValueError(f"proof span/text count mismatch for {item.ref_id}")
        for span, expected in zip(item.spans, item.span_texts):
            start, end = context.joined_span(span)
            actual = context.text[start:end]
            if (
                actual != expected
                or docs[span.doc_id].text[span.start : span.end] != expected
            ):
                raise ValueError(f"proof span drift for {item.ref_id}")
            mapped.append(
                {
                    "ref_id": item.ref_id,
                    "kind": item.kind,
                    "source": span.to_dict(),
                    "reader_start": start,
                    "reader_end": end,
                    "text_sha256": hashlib.sha256(actual.encode("utf-8")).hexdigest(),
                }
            )
    if not mapped:
        raise ValueError("task has no verified proof span in reader context")
    return mapped


def _map_prompt_tokens(
    tokenizer: Any,
    prompt_text: str,
    user_content: str,
    proof_spans: list[dict[str, Any]],
) -> bool:
    """Map evidence to actual prompt tokens when a fast tokenizer is present."""
    if not getattr(tokenizer, "is_fast", False):
        return False
    content_start = prompt_text.find(user_content)
    if content_start < 0 or prompt_text.find(user_content, content_start + 1) >= 0:
        raise ValueError("user content has no unique position in chat template")
    encoded = tokenizer(prompt_text, truncation=False, return_offsets_mapping=True)
    valid = [
        (index, start, end)
        for index, (start, end) in enumerate(encoded["offset_mapping"])
        if end > start
    ]
    starts = [start for _index, start, _end in valid]
    ends = [end for _index, _start, end in valid]
    for span in proof_spans:
        char_start = content_start + span["reader_start"]
        char_end = content_start + span["reader_end"]
        first = bisect_right(ends, char_start)
        last = bisect_left(starts, char_end)
        if first >= last:
            raise ValueError("proof span has no corresponding prompt tokens")
        span["prompt_token_start"] = valid[first][0]
        span["prompt_token_end"] = valid[last - 1][0] + 1
    return True


def natural_locate_question(task: TaskSpec) -> str:
    """Render only the two locate shapes with unambiguous program fields."""
    if task.family != "locate" or task.template not in {
        "locate_attribute",
        "locate_follow",
    }:
        raise ValueError("explicit_program_question:unsupported_task_family")
    steps = task.program.get("steps", ())
    if len(steps) != 2 or steps[0].get("op") != "bind":
        raise ValueError("explicit_program_question:unexpected_locate_program")
    label = steps[0].get("label")
    relation = steps[1].get("relation")
    expected_op = "bind" if task.template == "locate_attribute" else "follow_relation"
    if (
        not isinstance(label, str)
        or not label.strip()
        or not isinstance(relation, str)
        or not relation.strip()
        or steps[1].get("op") != expected_op
        or steps[1].get("subject") != "$" + str(steps[0].get("out"))
        or task.program.get("return") != steps[1].get("out")
    ):
        raise ValueError("explicit_program_question:unexpected_locate_program")
    if (
        any(mark in label for mark in ("|", "[", "]", "{", "}"))
        or any(
            mark in relation.lower()
            for mark in ("http", "url", "image", "file", "template")
        )
        or any(mark in relation for mark in ("|", "[", "]", "{", "}"))
    ):
        raise ValueError("markup_or_metadata_question")
    phrase = relation.replace("_", " ")
    if task.template == "locate_attribute":
        if relation in NATURAL_RELATION_QUESTIONS:
            return NATURAL_RELATION_QUESTIONS[relation].format(label=label)
        return f"According to these documents, what is the {phrase} of {label}?"
    return f"According to these documents, which entities have the {phrase} relation with {label}?"


def compile_task(
    world: SemanticWorld,
    task: TaskSpec,
    *,
    source_group: str,
    tokenizer: Any | None = None,
    max_full_tokens: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return (training row, sample index, audit sidecar) for one task.

    Token counts are from the final chat serialization when a tokenizer is
    supplied. Without one, token fields are absent rather than estimated.
    """
    if task.scope.task_id != task.task_id:
        raise ValueError("task/scope identity mismatch")
    if task.family == "source_compare" and task.template == "source_compare":
        question = task.question
        question_style = "natural_source_compare"
    elif task.family == "table_scan" and task.template == "table_scan":
        question = task.question
        question_style = "natural_table_scan"
    else:
        question = natural_locate_question(task)
        question_style = "natural_locate"
    context = render_documents(world, task.scope.documents)
    proof_spans = _proof_spans(world, task, context)
    answer = _canonical(task.answer_rendered)
    if any(
        mark in answer.lower()
        for mark in ("https://", "http://", "image_size", "|", "[[", "{{")
    ):
        raise ValueError("markup_or_metadata_answer")
    user_content = context.text + QUESTION_SEPARATOR + question
    messages = [
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": answer},
    ]
    digest = hashlib.sha256(f"{source_group}\0{task.task_id}".encode()).hexdigest()[:20]
    example_id = f"p75-{digest}"
    row: dict[str, Any] = {
        "example_id": example_id,
        "quality_status": "research_candidate",
        "messages": messages,
    }
    index: dict[str, Any] = {
        "example_id": example_id,
        "source_group": source_group,
        "task_id": task.task_id,
        "family": task.family,
        "profile": PROFILE,
        "question_style": question_style,
        "quality_status": "research_candidate",
        "evidence_status": "value_span_checked; relation_evidence_unchecked",
        "context_sha256": hashlib.sha256(context.text.encode("utf-8")).hexdigest(),
        "context_chars": len(context.text),
        "document_count": len(context.layouts),
        "token_measurement": "unmeasured",
    }
    if tokenizer is not None:
        from scripts.train_sft import _render_chat, assistant_prefix_length

        prompt_text = _render_chat(tokenizer, messages[:1], generation_prompt=True)
        full_text = _render_chat(tokenizer, messages, generation_prompt=False)
        prompt_ids = list(tokenizer(prompt_text, truncation=False)["input_ids"])
        full_ids = list(tokenizer(full_text, truncation=False)["input_ids"])
        input_tokens = assistant_prefix_length(prompt_ids, full_ids)
        full_tokens = len(full_ids)
        if max_full_tokens is not None and full_tokens > max_full_tokens:
            raise ValueError(f"full_chat_over_budget:{full_tokens}>{max_full_tokens}")
        token_mapped = _map_prompt_tokens(
            tokenizer, prompt_text, user_content, proof_spans
        )
        index.update(
            {
                "token_measurement": "pinned-chat-template",
                "input_tokens": input_tokens,
                "full_chat_tokens": full_tokens,
                "supervised_tokens": full_tokens - input_tokens,
                "evidence_token_mapping": (
                    "exact_prompt_offsets" if token_mapped else "unavailable"
                ),
            }
        )
        if token_mapped:
            if any(span["prompt_token_end"] > input_tokens for span in proof_spans):
                raise ValueError("evidence span crosses the assistant mask boundary")
            index["observed_lineage_token_envelope"] = {
                "start": min(span["prompt_token_start"] for span in proof_spans),
                "end": max(span["prompt_token_end"] for span in proof_spans),
            }
            index["fact_value_token_spans"] = [
                {
                    "fact_id": span["ref_id"],
                    "start": span["prompt_token_start"],
                    "end": span["prompt_token_end"],
                }
                for span in proof_spans
                if span["kind"] == "fact" and span["ref_id"] in task.consumed_fact_ids
            ]
            if not index["fact_value_token_spans"]:
                raise ValueError("consumed facts have no mapped prompt token span")
    audit = {
        "schema": SCHEMA,
        "example_id": example_id,
        "profile": PROFILE,
        "formal_proof_status": "formal_fact_lineage_only; reader_text_necessity_unchecked",
        "original_question": task.question,
        "program": task.program,
        "proof": [item.to_dict() for item in task.proof],
        "consumed_fact_ids": list(task.consumed_fact_ids),
        "source_to_reader_spans": proof_spans,
        "document_layouts": [layout.to_dict() for layout in context.layouts],
    }
    return row, index, audit
