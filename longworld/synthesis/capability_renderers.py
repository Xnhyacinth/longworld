"""P70 multi-format rendering: one typed world, three expressions, one gold.

JSON is one *expression* of the world, not the world -- P64's failure was answers
written in JSON only, so a solver that sees one container learns the container,
not the query mechanism. The same rows render three ways: `jsonl` delegates to
render_context (never a copy), `prose` is one seeded sentence per row, `table` is
a pipe header plus one line per row. verify_format_equivalence runs the SAME
program against all three and requires byte-identical gold.

The prose template is picked by sha256 over (VERSION, row type, row id), never by
position and never by Python's salted hash(), so a process pool stays in step and
deleting a row leaves every other sentence byte-identical; parse_world is a regex
parser over that bank, so round-trip holds in all three formats.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from longworld.synthesis.capability_records import (
    FAMILIES,
    FIELDS,
    PROMPTS,
    PROTOCOLS,
    RETURNS,
    VERSION,
    Row,
    describe_program,
    parse_context,
    render_context,
    solve_visible,
)

FORMATS = ("jsonl", "prose", "table")

TABLE_COLUMNS = ("id", "type", "entity", "category", "amount", "date", "memo")
TABLE_NOTE = "# one line per row, cells verbatim, empty cell means absent"
PROSE_NOTE = "# one sentence per row, in file order"

# One character class per field, shared by prose and table cells: a value that fails
# its class is refused at render rather than written unrecoverably. Each template
# names every field of its row type once, so its regex reads the whole row back.
_ID = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_FIELD_PATTERNS = {
    "id": _ID,
    "entity": _ID,
    "memo": _ID,
    "type": r"(?:record|reference)",
    "category": r"[a-z0-9][a-z0-9._-]*",
    "amount": r"\d+",
    "date": r"\d{4}-\d{2}-\d{2}",
}
RECORD_TEMPLATES = (
    "On {date}, {entity} logged a {category} entry of {amount} units, filed as {id} with memo {memo}.",
    "{entity}'s {category} entry from {date} shows {amount} units under memo {memo}, filed as {id}.",
    "Row {id} records that {entity} booked {amount} units in {category} on {date}, memo {memo}.",
    "A {category} entry for {entity} dated {date} carries {amount} units and memo {memo}; it is row {id}.",
    "{entity} entered {amount} units into the {category} ledger on {date}; row {id} cites memo {memo}.",
    "The ledger line {id} lists {entity} under {category} with {amount} units dated {date} and memo {memo}.",
    "Dated {date}, row {id} assigns {amount} units of {category} to {entity} under memo {memo}.",
    "Memo {memo} sits on row {id}, a {category} booking of {amount} units for {entity} dated {date}.",
    "{entity} filed {id} on {date}: {amount} units in {category}, with memo {memo}.",
    "Row {id} (memo {memo}) places {amount} units for {entity} in {category} on {date}.",
    "On {date} the {category} column for {entity} gained {amount} units, logged as {id} with memo {memo}.",
    "{id} is a {category} line for {entity}: {amount} units dated {date}, memo {memo}.",
)
REFERENCE_TEMPLATES = (
    "Reference {id} fixes {entity} at {amount} units, memo {memo}.",
    "The reference line for {entity} is {id}, holding {amount} units with memo {memo}.",
    "{entity} is tied to reference {id}, a standing amount of {amount} units, memo {memo}.",
    "Reference {id} for {entity} carries {amount} units (memo {memo}).",
)


def _verbatim(field: str, value: str) -> str:
    """Reject a value its class cannot carry, at render time and at parse time."""
    if not re.fullmatch(_FIELD_PATTERNS[field], value):
        raise ValueError(f"{field} is not renderable verbatim: {value!r}")
    return value


def _compile(template: str) -> re.Pattern[str]:
    """One template to a full-match regex, one named group per field."""
    parts = re.split(r"\{([a-z_]+)\}", template)
    names = parts[1::2]
    if len(set(names)) != len(names) or any(n not in _FIELD_PATTERNS for n in names):
        raise ValueError(f"template must name each field once: {template!r}")
    return re.compile(
        "".join(
            f"(?P<{n}>{_FIELD_PATTERNS[n]})" if i % 2 else re.escape(n)
            for i, n in enumerate(parts)
        )
    )


_TEMPLATES = {"record": RECORD_TEMPLATES, "reference": REFERENCE_TEMPLATES}
_MATCHERS = {name: [*map(_compile, bank)] for name, bank in _TEMPLATES.items()}


def _prose_line(row: Row) -> str:
    """One sentence for one row; the template comes from the row's identity."""
    if row.type not in _MATCHERS:
        raise ValueError("unknown row type")
    pairs = {
        "id": row.id,
        "entity": row.entity,
        "memo": row.memo,
        "category": row.category,
        "amount": str(row.amount),
        "date": row.day,
    }
    present = {k: _verbatim(k, v) for k, v in pairs.items() if v is not None}
    bank = _TEMPLATES[row.type]
    digest = hashlib.sha256(f"{VERSION}|{row.type}|{row.id}".encode()).digest()
    return bank[int.from_bytes(digest[:8], "big") % len(bank)].format(**present)


def _parse_sentence(line: str) -> Row:
    """Regex round-trip of one prose sentence; exactly one template may match."""
    hits = [
        (row_type, found.groupdict())
        for row_type, matchers in _MATCHERS.items()
        for matcher in matchers
        if (found := matcher.fullmatch(line))
    ]
    if len(hits) != 1:
        raise ValueError(f"prose sentence matches {len(hits)} templates")
    row_type, fields = hits[0]
    return _row({**fields, "type": row_type})


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _table_row(row: Row) -> str:
    """One pipe-separated line, cells in TABLE_COLUMNS order."""
    cells = [
        row.id,
        row.type,
        row.entity,
        row.category or "",
        str(row.amount),
        row.day or "",
        row.memo,
    ]
    if any("|" in cell or "\n" in cell for cell in cells):  # pragma: no cover
        raise ValueError("table cell is not renderable verbatim")
    return "|".join(cells)


def _row(item: Any) -> Row:
    """A Row, or a visible dict (int-or-text amount, date or day)."""
    if isinstance(item, Row):
        return item
    if not isinstance(item, dict):
        raise TypeError("rows must be Row objects or their visible dicts")
    return Row(
        id=item["id"],
        type=item["type"],
        entity=item["entity"],
        amount=int(item["amount"]),
        memo=item["memo"],
        category=item.get("category"),
        day=item.get("date") if "date" in item else item.get("day"),
    )


def _rows_and_family(world: Any, family: str | None = None) -> tuple[list[Row], str]:
    """Rows plus family from a bundle, a context string, or plain rows.

    A bundle and a bare context string carry the same jsonl text and take one path:
    the contract is re-checked either way, and the caller's family must agree.
    """
    if isinstance(world, dict):
        world = world.get("context")
    embedded = None
    if isinstance(world, str):
        embedded, world = _parse_tagged(world, "jsonl")
    if family is None:
        family = embedded
    elif embedded is not None and family != embedded:
        raise ValueError("family does not match the world")
    if family not in PROTOCOLS:
        raise ValueError("unsupported or missing family")
    rows = [_row(item) for item in world]
    if not rows:
        raise ValueError("no rows to render")
    return rows, family


def render_world(records: Any, family: str | None, fmt: str) -> str:
    """Render one typed world: the same rows, in the format's own expression."""
    if fmt not in FORMATS:
        raise ValueError("unsupported format")
    rows, family = _rows_and_family(records, family)
    if fmt == "jsonl":
        return render_context(rows, family)  # delegated, not duplicated
    lines = [f"# {VERSION} | family {family}", f"# rules: {PROTOCOLS[family]}"]
    if fmt == "prose":
        lines += [PROSE_NOTE, *map(_prose_line, rows)]
    else:
        lines += [TABLE_NOTE, "|".join(TABLE_COLUMNS), *map(_table_row, rows)]
    return "\n".join(lines)


def _parse_tagged(text: str, fmt: str) -> tuple[str, list[Row]]:
    """Family plus rows, with the pinned contract re-checked on the way in."""
    if fmt == "jsonl":
        header, rows = parse_context(text)
        family = header.get("family")
        if (
            header.get("schema") != VERSION
            or family not in PROTOCOLS
            or header.get("rules") != PROTOCOLS[family]
        ):
            raise ValueError("unsupported or missing visible contract")
        return family, rows
    if fmt not in FORMATS:
        raise ValueError("unsupported format")
    lines = text.splitlines()
    prefix = f"# {VERSION} | family "
    if len(lines) < 3 or not lines[0].startswith(prefix):
        raise ValueError("missing or foreign rendered contract")
    family = lines[0][len(prefix) :]
    if family not in PROTOCOLS or lines[1] != f"# rules: {PROTOCOLS[family]}":
        raise ValueError("rules text does not match the registered contract")
    if fmt == "prose":
        if lines[2] != PROSE_NOTE:
            raise ValueError("prose preamble does not match the registered contract")
        return family, [_parse_sentence(line) for line in lines[3:]]
    if lines[2] != TABLE_NOTE or lines[3] != "|".join(TABLE_COLUMNS):
        raise ValueError("table header does not match the registered contract")
    rows = []
    for line in lines[4:]:
        cells = dict(zip(TABLE_COLUMNS, line.split("|"), strict=True))
        rows.append(
            _row(
                {
                    field: _verbatim(field, cell)
                    if cell or field not in ("category", "date")
                    else None
                    for field, cell in cells.items()
                }
            )
        )
    return family, rows


def parse_world(text: str, fmt: str) -> list[Row]:
    """Round-trip parser: parse_world(render_world(rows, f, fmt), fmt) == rows."""
    return _parse_tagged(text, fmt)[1]


# Format-specific phrasings, pinned as PROMPTS are: the executor reads the typed
# program, so only a byte-identical re-render from (program, fmt, index) is admitted.
# One skeleton serves both formats, only the container's own name moving.
_PROMPT_SKELETON = (
    "Execute the typed program below over {parts} and return its result.",
    "Run the stated program against {whole} and report the outcome.",
    "Evaluate this query program over {parts} and give the computed result.",
    "Apply the program's operations to {whole} in order and answer.",
    "Read {whole}, run the program exactly as written, and report its result.",
    "Work through the program against {whole} and state the answer.",
    "Carry out the following typed query over {parts} and return the outcome.",
    "Solve the typed query below against {whole} and report the result.",
)
PROSE_PROMPTS = tuple(
    line.format(parts="the sentences", whole="the described rows")
    for line in _PROMPT_SKELETON
)
TABLE_PROMPTS = tuple(
    line.format(parts="the table rows", whole="the table") for line in _PROMPT_SKELETON
)
FORMAT_PROMPTS: dict[str, dict[str, tuple[str, ...]]] = {
    # jsonl reuses the compiler's bank, so its question is byte-identical to
    # render_instruction: no existing row changes meaning.
    "jsonl": PROMPTS,
    "prose": {family: PROSE_PROMPTS for family in FAMILIES},
    "table": {family: TABLE_PROMPTS for family in FAMILIES},
}

# The vocabulary line moves with the format; the program and return contract below
# it do not. A prose row speaks and a reference sentence is its own shape; a table
# line is a row of cells, so its join note belongs to the table itself.
_PROSE_FIELDS = (
    "each sentence names one row's id, entity, category, amount, date and memo.",
    " Reference sentences name a reference row's id, entity, amount and memo.",
)
_TABLE_FIELDS = (
    "the head names the columns id, type, entity, category, amount, date and memo, "
    "and every line is one row with those cells verbatim; an empty cell means the "
    "field is absent. Reference rows carry no category or date."
)
FORMAT_FIELDS: dict[str, dict[str, str]] = {
    "jsonl": FIELDS,
    "prose": {
        family: _PROSE_FIELDS[0] + (_PROSE_FIELDS[1] if family == "join_lookup" else "")
        for family in FAMILIES
    },
    "table": {family: _TABLE_FIELDS for family in FAMILIES},
}


def render_question(program: dict[str, Any], fmt: str, phrasing_index: int) -> str:
    """The question's own expression: the program is fixed, the vocabulary moves.

    The index rotates within the format's own pool (jsonl carries 24 phrasings per
    family, the others 8), so a caller may pass the compiler's index unchanged.
    """
    if fmt not in FORMATS:
        raise ValueError("unsupported format")
    family = program.get("family")
    if family not in PROTOCOLS or type(phrasing_index) is not int:
        raise ValueError("unsupported program family or phrasing index")
    prompts = FORMAT_PROMPTS[fmt][family]
    return (
        f"Task: {prompts[phrasing_index % len(prompts)]}. "
        f"Fields: {FORMAT_FIELDS[fmt][family]} "
        f"Program: {describe_program(program)}. "
        f"{RETURNS[family]}"
    )


def validate_question(instruction, program, fmt, phrasing_index) -> bool:
    """Tamper gate: only a byte-identical re-render of (program, fmt, index) passes."""
    return instruction == render_question(program, fmt, phrasing_index)


def _as_program(question: Any) -> dict[str, Any]:
    """Accept a program, or a task/mapping carrying one under `question`."""
    if isinstance(question, dict):
        carried = question.get("question")
        if "steps" in question:
            return question
        if isinstance(carried, dict):
            return carried
    raise ValueError("question must be a program or a mapping carrying one")


def verify_format_equivalence(world: Any, question: Any) -> dict[str, Any]:
    """Render one world three ways and execute one program against each.

    Each format is parsed back to rows (round-trip asserted), re-rendered into the
    compiler's jsonl contract and solved by the same executor; the three golds must
    be byte-identical. A bank-builder admits a world only when `equivalent`.
    """
    rows, family = _rows_and_family(world)
    program = _as_program(question)
    gold_by_format: dict[str, Any] = {}
    errors: list[str] = []
    for fmt in FORMATS:
        try:
            parsed = parse_world(render_world(rows, family, fmt), fmt)
            if parsed != rows:
                raise ValueError("round-trip does not reproduce the rows")
            gold_by_format[fmt] = solve_visible(render_context(parsed, family), program)
        except ValueError as error:
            errors.append(f"{fmt}: {type(error).__name__}: {error}")
    complete = len(gold_by_format) == len(FORMATS)
    canonical = json.dumps  # the same separators the compiler's answers use
    if (
        complete
        and len({canonical(g, sort_keys=True) for g in gold_by_format.values()}) != 1
    ):
        errors.append("gold differs across formats")
    return {
        "equivalent": complete and not errors,
        "family": family,
        "rows": len(rows),
        "gold": gold_by_format.get(FORMATS[0]) if complete and not errors else None,
        "gold_by_format": gold_by_format,
        "round_trip": {fmt: fmt in gold_by_format for fmt in FORMATS},
        "errors": errors,
    }


# Content unigrams only: hyphenated ISO dates and hex ids stay one token, so a date
# matches a whole date, not its "2019" prefix. Question and evidence share the field
# vocabulary by necessity -- a program names the fields it filters -- which is why the
# vocabulary must not also carry values.
_WORD = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_FUNCTION_WORDS = (
    "a an and are as at be been by for from has have in into is it its of on or "
    "that the their then there these this to was were which with"
)
STOPWORDS = frozenset(_FUNCTION_WORDS.split())


def _content_tokens(text: str) -> list[str]:
    return [token for token in _WORD.findall(text.lower()) if token not in STOPWORDS]


def lexical_overlap(world: Any, question: Any, fmt: str = "prose") -> float:
    """How much of the evidence reappears in the question: overlap / evidence.

    Evidence is the K consumed rows' own text (the question's `consumed` when it
    carries one, every record row otherwise); the question is its instruction.
    """
    rows, _family = _rows_and_family(world)
    fields = question if isinstance(question, dict) else {}
    provenance = fields.get("consumed") or []
    keep = set(provenance)
    if len(keep) != len(provenance):
        raise ValueError("consumed provenance is not a distinct id set")
    evidence = (
        [row for row in rows if row.id in keep]
        if keep
        else [row for row in rows if row.type == "record"]
    )
    if len(evidence) < len(keep):
        raise ValueError("consumed id missing from the world")
    typed = {"jsonl": lambda row: _dump(row.visible()), "prose": _prose_line}.get(
        fmt, _table_row
    )
    evidence_tokens = Counter(_content_tokens("\n".join(map(typed, evidence))))
    if not evidence_tokens:
        raise ValueError("no evidence tokens")
    question_tokens = Counter(
        _content_tokens(
            render_question(_as_program(question), fmt, fields.get("phrasing_index", 0))
        )
    )
    shared = sum(
        min(count, evidence_tokens[token]) for token, count in question_tokens.items()
    )
    return shared / sum(evidence_tokens.values())
