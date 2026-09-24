"""Closed-table interval scans with value-blind final-reader replay.

Only named tables with a citation-free name cell and a plain-four-digit
Established column are supported.
The complete eligible row universe must bijectively match structurally
supported source facts before any task is emitted. Interval misses remain in
each proof as scanned candidates; that does not make each miss individually
necessary to the answer.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from longworld.synthesis import reader_view, wiki_evidence
from longworld.synthesis.dependency_ops import ProofItem
from longworld.synthesis.shared_semantic_world import Fact, ScopeEntry, SemanticWorld
from longworld.synthesis.world_task_bank import TaskSpec

YEAR_RE = re.compile(r"[12][0-9]{3}\Z")
TABLE_SUBJECT_COLUMNS = frozenset({"Name", "University", "Name (other name)"})
NAME_MARKERS = ("|", "{", "}", "[", "]", "http://", "https://")


@dataclass(frozen=True)
class TableRow:
    subject: str
    year: int | None
    year_cell: str
    row_start: int
    row_end: int
    year_start: int
    year_end: int
    header_start: int
    header_end: int
    exclusion_reason: str | None = None

    def to_dict(self, offset: int, *, selected: bool | None = None) -> dict[str, Any]:
        result = {
            "subject": self.subject,
            "year": self.year,
            "year_cell": self.year_cell,
            "row_span": {
                "start": offset + self.row_start,
                "end": offset + self.row_end,
            },
            "year_cell_span": {
                "start": offset + self.year_start,
                "end": offset + self.year_end,
            },
            "header_span": {
                "start": offset + self.header_start,
                "end": offset + self.header_end,
            },
        }
        if selected is not None:
            result["selected"] = selected
        if self.exclusion_reason is not None:
            result["exclusion_reason"] = self.exclusion_reason
        return result


@dataclass(frozen=True)
class TableParse:
    title: str
    header: str
    header_start: int
    header_end: int
    subject_column: str
    year_column: str
    width: int
    eligible: tuple[TableRow, ...]
    format_excluded: tuple[TableRow, ...]
    malformed_row_count: int


def parse_closed_table(text: str, title: str) -> TableParse:
    """Parse all rows in a single named table from visible page text alone."""
    header: tuple[str, ...] | None = None
    first_header: tuple[str, ...] | None = None
    header_text = ""
    header_start = header_end = year_index = 0
    first_header_start = first_header_end = 0
    subject_column = ""
    eligible: list[TableRow] = []
    excluded: list[TableRow] = []
    malformed = 0
    offset = 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        parts = tuple(line.split(" | "))
        if line.startswith("## "):
            header = None
        elif len(parts) >= 2 and parts[0] in TABLE_SUBJECT_COLUMNS:
            if parts.count("Established") == 1:
                header = parts
                header_start = offset
                header_end = offset + len(line)
                year_index = parts.index("Established")
                if first_header is None:
                    first_header = parts
                    header_text = line
                    first_header_start = header_start
                    first_header_end = header_end
                    subject_column = parts[0]
            else:
                header = None
        elif header is not None and line and not line.startswith("#"):
            if len(parts) != len(header):
                malformed += 1
            else:
                subject = parts[0].strip()
                year_cell = parts[year_index].strip()
                if subject:
                    cell_start = (
                        offset
                        + sum(len(part) + 3 for part in parts[:year_index])
                        + len(parts[year_index])
                        - len(parts[year_index].lstrip())
                    )
                    year = int(year_cell) if YEAR_RE.fullmatch(year_cell) else None
                    exclusion_reason = (
                        "non_plain_name"
                        if any(mark in subject for mark in NAME_MARKERS)
                        else "non_plain_year"
                        if year is None
                        else None
                    )
                    row = TableRow(
                        subject,
                        year,
                        year_cell,
                        offset,
                        offset + len(line),
                        cell_start,
                        cell_start + len(year_cell),
                        header_start,
                        header_end,
                        exclusion_reason,
                    )
                    (eligible if exclusion_reason is None else excluded).append(row)
        offset += len(raw)
    if first_header is None:
        raise ValueError(f"no closed Established table in {title}")
    if len(eligible) < 8:
        raise ValueError(f"too few clean table rows in {title}: {len(eligible)}")
    return TableParse(
        title,
        header_text,
        first_header_start,
        first_header_end,
        subject_column,
        "Established",
        len(first_header),
        tuple(eligible),
        tuple(excluded),
        malformed,
    )


def _source_facts(world: SemanticWorld, doc_id: str) -> tuple[Fact, ...]:
    doc_text = world._docs[doc_id].text

    def plain_name(fact: Fact) -> bool:
        span = fact.supporting_spans[0]
        start = doc_text.rfind("\n", 0, span.start) + 1
        end = doc_text.find("\n", span.end)
        row = doc_text[start : end if end >= 0 else len(doc_text)]
        subject_cell = row.split(" | ", 1)[0].strip()
        return bool(subject_cell) and not any(
            mark in subject_cell for mark in NAME_MARKERS
        )

    facts = [
        fact
        for fact in world.facts
        if fact.relation == "established in"
        and fact.qualifiers.get("table_column") == "Established"
        and isinstance(fact.value, int)
        and not isinstance(fact.value, bool)
        and len(fact.supporting_spans) == 1
        and fact.supporting_spans[0].doc_id == doc_id
        and wiki_evidence.check_locate_fact(world, fact).supported
        and plain_name(fact)
    ]
    return tuple(sorted(facts, key=lambda fact: fact.fact_id))


def closed_universe(
    world: SemanticWorld, doc_id: str
) -> tuple[TableParse, tuple[Fact, ...]]:
    """Reject unless every plain-year row matches one fact and vice versa."""
    doc = world._docs[doc_id]
    parsed = parse_closed_table(doc.text, doc.title)
    facts = _source_facts(world, doc_id)
    if len(parsed.eligible) != len(facts):
        raise ValueError("table/fact candidate universe size differs")
    unused = {fact.fact_id: fact for fact in facts}
    ordered: list[Fact] = []
    for row in parsed.eligible:
        matches = [
            fact
            for fact in unused.values()
            if row.subject
            in (world.objects[fact.subject].label, *world.objects[fact.subject].aliases)
            and fact.value == row.year
            and fact.supporting_spans[0].start == row.year_start
            and fact.supporting_spans[0].end == row.year_end
        ]
        if len(matches) != 1:
            raise ValueError(f"table row lacks unique fact support: {row.subject}")
        fact = matches[0]
        ordered.append(fact)
        del unused[fact.fact_id]
    if unused:
        raise ValueError("supported facts remain outside parsed table universe")
    return parsed, tuple(ordered)


def execute_interval(world: SemanticWorld, program: dict[str, Any]) -> dict[str, Any]:
    if program.get("op") != "closed_table_interval":
        raise ValueError("wrong table scan operator")
    low, high = program["low"], program["high"]
    if not (isinstance(low, int) and isinstance(high, int) and low < high):
        raise ValueError("invalid year interval")
    parsed, facts = closed_universe(world, program["doc_id"])
    if parsed.year_column != program["year_column"]:
        raise ValueError("table scan column drift")
    if tuple(fact.fact_id for fact in facts) != tuple(program["candidate_fact_ids"]):
        raise ValueError("table scan candidate universe drift")
    entries = sorted(
        world.label_of(fact.subject) for fact in facts if low <= fact.value <= high
    )
    return {"count": len(entries), "entries": entries}


def reader_interval_replay(
    world: SemanticWorld, task: TaskSpec, context: str
) -> tuple[dict[str, Any], TableParse, int]:
    """Solve the scan from final reader bytes, without reading gold fact values."""
    rendered = reader_view.render_documents(world, task.scope.documents)
    if rendered.text != context:
        raise ValueError("final reader context drift")
    doc_id = task.program["doc_id"]
    layout = next(layout for layout in rendered.layouts if layout.doc_id == doc_id)
    parsed = parse_closed_table(
        context[layout.text_start : layout.text_end], layout.title
    )
    low, high = task.program["low"], task.program["high"]
    entries = sorted(row.subject for row in parsed.eligible if low <= row.year <= high)
    return {"count": len(entries), "entries": entries}, parsed, layout.text_start


def _intervals(years: list[int], max_tasks: int) -> list[tuple[int, int]]:
    unique = sorted(set(years))
    by_cardinality: dict[int, list[tuple[int, int]]] = {}
    for low in unique:
        for high in unique:
            if high <= low:
                continue
            count = sum(low <= year <= high for year in years)
            if 2 <= count <= min(15, len(years) - 2):
                by_cardinality.setdefault(count, []).append((low, high))
    if not by_cardinality:
        return []
    options = []
    for count in sorted(by_cardinality):
        pairs = by_cardinality[count]
        # Prefer an interval whose endpoints are internal, not a one-year tie.
        pair = min(
            pairs,
            key=lambda item: (
                -abs(item[1] - item[0]),
                hashlib.sha256(json.dumps(item).encode()).hexdigest(),
            ),
        )
        options.append((count, pair))
    if len(options) <= max_tasks:
        return [pair for _, pair in options]
    if max_tasks == 1:
        return [options[len(options) // 2][1]]
    picks = {
        round(index * (len(options) - 1) / (max_tasks - 1))
        for index in range(max_tasks)
    }
    return [options[index][1] for index in sorted(picks)]


def build_scan_tasks(
    world: SemanticWorld, doc_id: str, *, max_tasks: int = 8
) -> tuple[TaskSpec, ...]:
    if max_tasks < 1:
        return ()
    parsed, facts = closed_universe(world, doc_id)
    tasks = []
    for low, high in _intervals([fact.value for fact in facts], max_tasks):
        digest = hashlib.sha256(f"{doc_id}|{low}|{high}".encode()).hexdigest()[:16]
        task_id = f"wiki-table-scan-{digest}"
        question = (
            f"In the {parsed.year_column!r} column of {parsed.title}, consider "
            f"only rows with a plain citation-free name cell and a plain "
            f"four-digit year. List every named entry "
            f"whose year is from {low} through {high}, inclusive, and give "
            "the total count. Sort the names alphabetically."
        )
        program = {
            "op": "closed_table_interval",
            "doc_id": doc_id,
            "year_column": parsed.year_column,
            "low": low,
            "high": high,
            "candidate_fact_ids": [fact.fact_id for fact in facts],
        }
        proof = tuple(
            ProofItem(
                step=index,
                op="closed_table_interval",
                out=f"candidate_{index}",
                kind="fact",
                ref_id=fact.fact_id,
                subject=fact.subject,
                relation=fact.relation,
                value=fact.value,
                spans=fact.supporting_spans,
                span_texts=(
                    world._docs[doc_id].text[
                        fact.supporting_spans[0].start : fact.supporting_spans[0].end
                    ],
                ),
            )
            for index, fact in enumerate(facts)
        )
        answer = execute_interval(world, program)
        tasks.append(
            TaskSpec(
                task_id=task_id,
                family="table_scan",
                template="table_scan",
                question=question,
                program=program,
                scope=ScopeEntry(
                    task_id=task_id,
                    object_families=("untyped",),
                    relations=("established in",),
                    documents=tuple(doc.doc_id for doc in world.documents if doc.text),
                ),
                answer=answer,
                answer_rendered=answer,
                proof=proof,
                consumed_fact_ids=tuple(fact.fact_id for fact in facts),
                metrics={
                    "candidate_rows": len(facts),
                    "selected_rows": answer["count"],
                },
                nondegenerate={"closed_table": True, "nonempty_proper_subset": True},
                structure_signals={
                    "year_column": parsed.year_column,
                    "candidate_rows": len(facts),
                },
                spread=(doc_id, str(low), str(high)),
            )
        )
    return tuple(tasks)


def _insert_row(text: str, parsed: TableParse, subject: str, year: int) -> str:
    parts = ["–"] * parsed.width
    parts[0] = subject
    parts[parsed.header.split(" | ").index(parsed.year_column)] = str(year)
    insertion = " | ".join(parts) + "\n"
    line_end = text.find("\n", parsed.header_end)
    if line_end < 0:
        raise ValueError("table has no insertion boundary")
    return text[: line_end + 1] + insertion + text[line_end + 1 :]


def insertion_intervention(
    world: SemanticWorld, task: TaskSpec, context: str
) -> dict[str, Any]:
    """Insert one valid hit and one same-schema near miss into reader text."""
    baseline, parsed, offset = reader_interval_replay(world, task, context)
    if baseline != task.answer:
        raise ValueError("reader parser and formal scan disagree")
    low, high = task.program["low"], task.program["high"]
    hit_year = (low + high) // 2
    miss_year = low - 1 if low > 1000 else high + 1
    if not (1000 <= hit_year <= 2099 and 1000 <= miss_year <= 2099):
        raise ValueError("year intervention outside clean range")
    doc_id = task.program["doc_id"]
    doc = world._docs[doc_id]
    existing = {row.subject for row in parsed.eligible}
    names = ("P76 insertion hit", "P76 insertion near miss")
    if any(name in existing for name in names):
        raise ValueError("insertion label collides with source table")
    inserted_answers = []
    for name, year in zip(names, (hit_year, miss_year)):
        inserted_doc = _insert_row(doc.text, parsed, name, year)
        replayed = parse_closed_table(inserted_doc, doc.title)
        entries = sorted(
            row.subject for row in replayed.eligible if low <= row.year <= high
        )
        result = {"count": len(entries), "entries": entries}
        if len(replayed.eligible) != len(parsed.eligible) + 1:
            raise ValueError("inserted table row was not scanned")
        modified_context = (
            context[:offset] + inserted_doc + context[offset + len(doc.text) :]
        )
        inserted_answers.append(
            (result, hashlib.sha256(modified_context.encode()).hexdigest())
        )
    hit, hit_hash = inserted_answers[0]
    miss, miss_hash = inserted_answers[1]
    if hit["count"] != baseline["count"] + 1 or hit["entries"] != sorted(
        [*baseline["entries"], names[0]]
    ):
        raise ValueError("valid-hit insertion did not change complete answer")
    if miss != baseline:
        raise ValueError("near-miss insertion changed answer")
    return {
        "status": "scoped_table_insertion_replay",
        "eligible_row_count": len(parsed.eligible),
        "hit_year": hit_year,
        "near_miss_year": miss_year,
        "hit_context_sha256": hit_hash,
        "near_miss_context_sha256": miss_hash,
        "hit_answer": hit,
        "near_miss_answer": miss,
        "certification_scope": "named table, plain four-digit Established rows and this interval; unrestricted prose inference unchecked",
    }
