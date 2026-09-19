"""P70 multi-format rendering: one typed world, three expressions, one gold.

JSON is one *expression* of the world, not the world -- P64's failure was answers
written in JSON only, so a solver that sees one container learns the container,
not the query mechanism. The same rows render three ways: `jsonl` delegates to
the owning module's render_context (never a copy), `prose` is one seeded
sentence per row, `table` is a pipe header plus one line per row.
verify_format_equivalence runs the SAME program against all three and requires
byte-identical gold.

Every family first resolves its owning module -- capability_records for the
three record families, capability_families for alias_locate, asof_state and
rule_holdout, the runner's FAMILY_MODULES idiom -- and every contract symbol
(Row, render_context, parse_context, solve_visible, describe_program, PROMPTS,
VERSION) is taken from that owner, so no row shape is hard-wired here. Prose
template banks and table column sets are registered per family and per row
type. rule_holdout's header extras (the declared rule family, structure text,
modulus and labels) ride a third pinned line in prose and table: a re-parsed
world that cannot be re-executed is not a rendering, so the extras are carried
and re-checked on the way in.

The prose template is picked by sha256 over (owner VERSION, row type, row id),
never by position and never by Python's salted hash(), so a process pool stays
in step and deleting a row leaves every other sentence byte-identical; rows keep
file order in every format (asof folds in event-date order, so reordering the
rendering would reorder the fold). parse_world is a regex parser over that bank,
so round-trip holds in all three formats.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records

FORMATS = ("jsonl", "prose", "table")

# Family dispatch, as the runner resolves it: the owning module carries the
# contract symbols, and a family neither module owns is refused, never guessed.
FAMILY_MODULES: dict[str, Any] = {
    **{name: records for name in records.FAMILIES},
    **{name: families for name in families.FAMILIES},
}
# The families prose and table are configured for here. A family outside this
# registry still renders as jsonl (the owner's own expression) but prose and
# table refuse it rather than invent a configuration.
RENDERABLE = (
    *records.FAMILIES,
    "alias_locate",
    "asof_state",
    "rule_holdout",
)

TABLE_COLUMNS = ("id", "type", "entity", "category", "amount", "date", "memo")
TABLE_NOTE = "# one line per row, cells verbatim, empty cell means absent"
PROSE_NOTE = "# one sentence per row, in file order"
EXTRA_PREFIX = "# extra: "

# One character class per field, shared by prose and table cells: a value that fails
# its class is refused at render rather than written unrecoverably. Each template
# names every field of its row type once, so its regex reads the whole row back.
_ID = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_RECORDS_PATTERNS = {
    "id": _ID,
    "entity": _ID,
    "memo": _ID,
    "type": r"(?:record|reference)",
    "category": r"[a-z0-9][a-z0-9._-]*",
    "amount": r"\d+",
    "date": r"\d{4}-\d{2}-\d{2}",
}
_FAMILIES_PATTERNS = {
    "id": _ID,
    "entity": _ID,
    "memo": _ID,
    "type": r"(?:record|alias|event|demo)",
    "category": r"[a-z0-9][a-z0-9._-]*",
    "amount": r"\d+",
    "date": r"\d{4}-\d{2}-\d{2}",
    "alias": r"[a-z]+",
    "kind": r"(?:credit|debit|set_aside|release)",
    "reveal": r"\d{4}-\d{2}-\d{2}",
    "x": r"\d+",
    "y": r"\d+",
    "points": r"\[\[\d+,\d+\](?:,\[\d+,\d+\])*\]",
    "label": r"label_[a-z]+",
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

# alias_locate renders the records-shaped record rows with the records bank and
# declares its handles with their own: four sentences, the reference bank's size.
ALIAS_TEMPLATES = (
    "Alias {alias} names {entity}, declared as {id} with memo {memo}.",
    "The handle {alias} binds {entity} by declaration {id}, memo {memo}.",
    "Declaration {id} ties the alias {alias} to {entity}, memo {memo}.",
    "{entity} answers to the alias {alias} under declaration {id}, with memo {memo}.",
)
# One sentence, two dates: the event row's own date and the date it became visible.
EVENT_TEMPLATES = (
    "On {date}, {entity} logged a {kind} of {amount} units, revealed {reveal}, filed as {id} with memo {memo}.",
    "{entity}'s {kind} from {date} shows {amount} units, revealed {reveal}, under memo {memo}, filed as {id}.",
    "Event {id} records that {entity} booked a {kind} of {amount} units on {date}, revealed {reveal}, memo {memo}.",
    "A {kind} for {entity} dated {date} carries {amount} units and memo {memo}; it became visible {reveal} and is event {id}.",
    "{entity} entered a {kind} of {amount} units on {date}; event {id} was revealed {reveal} and cites memo {memo}.",
    "The event line {id} lists {entity} under {kind} with {amount} units dated {date} and revealed {reveal}, memo {memo}.",
    "Dated {date}, event {id} assigns a {kind} of {amount} units to {entity}, revealed {reveal}, under memo {memo}.",
    "Memo {memo} sits on event {id}, a {kind} of {amount} units for {entity} dated {date} and revealed {reveal}.",
    "{entity} filed {id} on {date}: a {kind} of {amount} units, revealed {reveal}, with memo {memo}.",
    "Event {id} (memo {memo}) places a {kind} of {amount} units for {entity} on {date}, revealed {reveal}.",
    "On {date} the {kind} booked by {entity} moved {amount} units, logged as {id}, revealed {reveal}, with memo {memo}.",
    "{id} is an event for {entity}: a {kind} of {amount} units dated {date}, revealed {reveal}, memo {memo}.",
)
# rule_holdout's record rows carry feature readings, not ledger amounts.
RULE_RECORD_TEMPLATES = (
    "Row {id} gives {entity} a reading of x {x} and y {y}, memo {memo}.",
    "{entity}'s reading {id} shows x {x} and y {y}, memo {memo}.",
    "Record {id} holds that {entity} reads x {x} and y {y}, under memo {memo}.",
    "A reading for {entity} filed as {id} carries x {x} and y {y} with memo {memo}.",
    "{entity} entered a reading as {id}: x {x} and y {y}, memo {memo}.",
    "The record line {id} lists {entity} with x {x} and y {y} and memo {memo}.",
    "Filed as {id}, the reading of x {x} and y {y} belongs to {entity}, memo {memo}.",
    "Memo {memo} sits on record {id}, a reading of x {x} and y {y} for {entity}.",
    "{entity} filed record {id} with x {x}, y {y} and memo {memo}.",
    "Record {id} (memo {memo}) places x {x} and y {y} for {entity}.",
    "On record {id}, {entity} carries x {x} and y {y}, memo {memo}.",
    "{id} is a reading for {entity}: x {x} and y {y} carried in it, memo {memo}.",
)
# A demonstration publishes its labelled feature points; points render verbatim.
DEMO_TEMPLATES = (
    "Demonstration {id} shows {entity} with points {points} and label {label}, memo {memo}.",
    "The demo {id} for {entity} carries label {label} from points {points}, memo {memo}.",
    "{entity} is demonstrated by {id}: points {points}, label {label}, memo {memo}.",
    "Demo {id} gives {entity} label {label} with points {points} and memo {memo}.",
)

# Table layouts: one column set per family, the empty-cell-absent fields named
# per family so a cell that is absent on the row type reads back as None.
FAMILY_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "alias_locate": (
        "id",
        "type",
        "entity",
        "category",
        "amount",
        "date",
        "memo",
        "alias",
    ),
    "asof_state": ("id", "type", "entity", "kind", "amount", "date", "reveal", "memo"),
    "rule_holdout": ("id", "type", "entity", "x", "y", "label", "points", "memo"),
}
FAMILY_OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "alias_locate": ("category", "amount", "date", "alias"),
    "asof_state": (),
    "rule_holdout": ("x", "y", "label", "points"),
}
FAMILY_TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    "alias_locate": {"record": RECORD_TEMPLATES, "alias": ALIAS_TEMPLATES},
    "asof_state": {"event": EVENT_TEMPLATES},
    "rule_holdout": {"record": RULE_RECORD_TEMPLATES, "demo": DEMO_TEMPLATES},
}

_BASE_HEADER_KEYS = ("schema", "family", "rules")
_SHAPE = re.compile(r"[A-Za-z0-9._-]+")


def _module_for(family: str | None):
    """The module that owns one family's contract, or a refusal."""
    if family not in FAMILY_MODULES:
        raise ValueError("unsupported or missing family")
    return FAMILY_MODULES[family]


def _verbatim(field: str, value: str, patterns: dict[str, str]) -> str:
    """Reject a value its class cannot carry, at render time and at parse time."""
    if not re.fullmatch(patterns[field], value):
        raise ValueError(f"{field} is not renderable verbatim: {value!r}")
    return value


def _compile(template: str, patterns: dict[str, str]) -> re.Pattern[str]:
    """One template to a full-match regex, one named group per field."""
    parts = re.split(r"\{([a-z_]+)\}", template)
    names = parts[1::2]
    if len(set(names)) != len(names) or any(n not in patterns for n in names):
        raise ValueError(f"template must name each field once: {template!r}")
    return re.compile(
        "".join(
            f"(?P<{n}>{patterns[n]})" if i % 2 else re.escape(n)
            for i, n in enumerate(parts)
        )
    )


def _patterns_for(family: str) -> dict[str, str]:
    owner = _module_for(family)
    return _RECORDS_PATTERNS if owner is records else _FAMILIES_PATTERNS


def _templates_for(family: str) -> dict[str, tuple[str, ...]]:
    if family in records.FAMILIES:
        return {"record": RECORD_TEMPLATES, "reference": REFERENCE_TEMPLATES}
    if family in FAMILY_TEMPLATES:
        return FAMILY_TEMPLATES[family]
    raise ValueError(f"family has no prose or table configuration: {family!r}")


def _columns_for(family: str) -> tuple[str, ...]:
    if family in records.FAMILIES:
        return TABLE_COLUMNS
    if family in FAMILY_TABLE_COLUMNS:
        return FAMILY_TABLE_COLUMNS[family]
    raise ValueError(f"family has no prose or table configuration: {family!r}")


def _optional_for(family: str) -> tuple[str, ...]:
    if family in records.FAMILIES:
        return ("category", "date")
    if family in FAMILY_OPTIONAL_FIELDS:
        return FAMILY_OPTIONAL_FIELDS[family]
    raise ValueError(f"family has no prose or table configuration: {family!r}")


_MATCHERS = {
    family: {
        row_type: [_compile(t, _patterns_for(family)) for t in bank]
        for row_type, bank in _templates_for(family).items()
    }
    for family in RENDERABLE
}


def _points_text(points) -> str:
    """The canonical [[x,y],...] rendering, byte-identical to the jsonl line."""
    return json.dumps([list(point) for point in points], separators=(",", ":"))


def _to_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _records_row(item: Any) -> records.Row:
    """A records Row, or its visible dict (int-or-text amount, date or day)."""
    if isinstance(item, records.Row):
        return item
    if not isinstance(item, dict):
        raise TypeError("rows must be Row objects or their visible dicts")
    return records.Row(
        id=item["id"],
        type=item["type"],
        entity=item["entity"],
        amount=int(item["amount"]),
        memo=item["memo"],
        category=item.get("category") or None,
        day=item.get("date") if "date" in item else item.get("day"),
    )


def _families_row(item: Any) -> families.Row:
    """A families Row, or its visible dict (text numerics, points as text or list)."""
    if isinstance(item, families.Row):
        return item
    if not isinstance(item, dict):
        raise TypeError("rows must be Row objects or their visible dicts")
    points = item.get("points")
    if points is not None:
        points = tuple(tuple(point) for point in json.loads(points))
    return families.Row(
        id=item["id"],
        type=item["type"],
        entity=item["entity"],
        memo=item["memo"],
        amount=_to_int(item.get("amount")),
        category=item.get("category") or None,
        day=item.get("date") or None,
        alias=item.get("alias") or None,
        kind=item.get("kind") or None,
        reveal=item.get("reveal") or None,
        x=_to_int(item.get("x")),
        y=_to_int(item.get("y")),
        points=points,
        label=item.get("label") or None,
    )


def _row_for(family: str, item: Any):
    """Build a row with the owning module's Row class; refuse a foreign Row."""
    owner = _module_for(family)
    return _records_row(item) if owner is records else _families_row(item)


def _prose_pairs(row, family: str) -> dict[str, str | None]:
    """Every renderable field of one row as text; None when the row omits it."""
    owner = _module_for(family)
    pairs: dict[str, str | None] = {
        "id": row.id,
        "entity": row.entity,
        "memo": row.memo,
        "category": getattr(row, "category", None),
        "date": getattr(row, "day", None),
    }
    if owner is families:
        pairs.update(
            {
                "amount": None if row.amount is None else str(row.amount),
                "alias": row.alias,
                "kind": row.kind,
                "reveal": row.reveal,
                "x": None if row.x is None else str(row.x),
                "y": None if row.y is None else str(row.y),
                "points": None if row.points is None else _points_text(row.points),
                "label": row.label,
            }
        )
    else:
        pairs["amount"] = str(row.amount)
    return pairs


def _prose_line(row, family: str) -> str:
    """One sentence for one row; the template comes from the row's identity."""
    bank = _templates_for(family).get(row.type)
    if bank is None:
        raise ValueError("unknown row type")
    pairs = _prose_pairs(row, family)
    present = {
        key: _verbatim(key, value, _patterns_for(family))
        for key, value in pairs.items()
        if value is not None
    }
    digest = hashlib.sha256(
        f"{_module_for(family).VERSION}|{row.type}|{row.id}".encode()
    ).digest()
    return bank[int.from_bytes(digest[:8], "big") % len(bank)].format(**present)


def _parse_sentence(line: str, family: str):
    """Regex round-trip of one prose sentence; exactly one template may match."""
    hits = [
        (row_type, found.groupdict())
        for row_type, matchers in _MATCHERS[family].items()
        for matcher in matchers
        if (found := matcher.fullmatch(line))
    ]
    if len(hits) != 1:
        raise ValueError(f"prose sentence matches {len(hits)} templates")
    row_type, fields = hits[0]
    return _row_for(family, {**fields, "type": row_type})


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_TABLE_GETTERS = {
    "id": lambda row: row.id,
    "type": lambda row: row.type,
    "entity": lambda row: row.entity,
    "memo": lambda row: row.memo,
    "category": lambda row: row.category,
    "amount": lambda row: row.amount,
    "date": lambda row: getattr(row, "day", None),
    "alias": lambda row: getattr(row, "alias", None),
    "kind": lambda row: getattr(row, "kind", None),
    "reveal": lambda row: getattr(row, "reveal", None),
    "x": lambda row: getattr(row, "x", None),
    "y": lambda row: getattr(row, "y", None),
    "label": lambda row: getattr(row, "label", None),
    "points": lambda row: (
        None if getattr(row, "points", None) is None else _points_text(row.points)
    ),
}


def _table_row(row, family: str) -> str:
    """One pipe-separated line, cells in the family's column order."""
    cells = [
        "" if (value := _TABLE_GETTERS[column](row)) is None else str(value)
        for column in _columns_for(family)
    ]
    if any("|" in cell or "\n" in cell for cell in cells):  # pragma: no cover
        raise ValueError("table cell is not renderable verbatim")
    return "|".join(cells)


def _rows_and_family(
    world: Any, family: str | None = None
) -> tuple[list[Any], str, Any, dict[str, Any]]:
    """Rows, family, owner and header extras from a bundle, a context or rows.

    A bundle and a bare context string carry the same jsonl text and take one path:
    the contract is re-checked either way, the caller's family must agree, and the
    header extras the family pins (rule_holdout's rule declaration) ride along.
    """
    if isinstance(world, dict):
        world = world.get("context")
    embedded = extras = None
    if isinstance(world, str):
        embedded, world, extras = _parse_tagged(world, "jsonl")
    if family is None:
        family = embedded
    elif embedded is not None and family != embedded:
        raise ValueError("family does not match the world")
    owner = _module_for(family)
    if family not in owner.PROTOCOLS:
        raise ValueError("unsupported or missing family")
    rows = [_row_for(family, item) for item in world]
    if not rows:
        raise ValueError("no rows to render")
    if extras and owner is records:
        raise ValueError("records families carry no header extras")
    if extras is None and family == "rule_holdout":
        # The rule declaration is executable state, not decoration: bare rows
        # cannot name it, so a rendering that would drop it is refused rather
        # than emitted unparseable.
        raise ValueError("rule_holdout needs the world or context carrying its header")
    return rows, family, owner, extras or {}


def _check_extras(family: str, extras: dict[str, Any]) -> None:
    """Re-check the header extras a family pins, on every way in."""
    if family != "rule_holdout":
        return
    rule_family = extras.get("rule_family")
    if (
        rule_family not in families.RULE_STRUCTURES
        or extras.get("rule_structure") != families.RULE_STRUCTURES[rule_family]
        or "modulus" not in extras
        or "labels" not in extras
    ):
        raise ValueError("extra header does not match the registered rule contract")


def _render_jsonl(rows, family: str, owner, extras: dict[str, Any]) -> str:
    """The owner's own jsonl expression, header extras included."""
    if owner is records:
        return owner.render_context(rows, family)
    return owner.render_context(rows, family, extras or None)


def render_world(records_world: Any, family: str | None, fmt: str) -> str:
    """Render one typed world: the same rows, in the format's own expression."""
    if fmt not in FORMATS:
        raise ValueError("unsupported format")
    rows, family, owner, extras = _rows_and_family(records_world, family)
    if fmt == "jsonl":
        return _render_jsonl(rows, family, owner, extras)  # delegated, not duplicated
    _templates_for(family)  # refuse a family with no prose/table configuration
    lines = [
        f"# {owner.VERSION} | family {family}",
        f"# rules: {owner.PROTOCOLS[family]}",
        f"{EXTRA_PREFIX}{_dump(extras)}",
    ]
    if fmt == "prose":
        lines += [PROSE_NOTE, *(_prose_line(row, family) for row in rows)]
    else:
        lines += [
            TABLE_NOTE,
            "|".join(_columns_for(family)),
            *(_table_row(row, family) for row in rows),
        ]
    return "\n".join(lines)


def _parse_tagged(text: str, fmt: str) -> tuple[str, list[Any], dict[str, Any]]:
    """Family, rows and header extras, with the pinned contract re-checked."""
    if fmt == "jsonl":
        header, rows = _jsonl_context(text)
        family = header.get("family")
        owner = _module_for(family)
        if (
            header.get("schema") != owner.VERSION
            or family not in owner.PROTOCOLS
            or header.get("rules") != owner.PROTOCOLS[family]
        ):
            raise ValueError("unsupported or missing visible contract")
        extras = {
            key: value for key, value in header.items() if key not in _BASE_HEADER_KEYS
        }
        _check_extras(family, extras)
        return family, rows, extras
    if fmt not in FORMATS:
        raise ValueError("unsupported format")
    lines = text.splitlines()
    if len(lines) < 4 or not lines[0].startswith("# ") or " | family " not in lines[0]:
        raise ValueError("missing or foreign rendered contract")
    version, family = lines[0][2:].split(" | family ", 1)
    owner = _module_for(family)
    if version != owner.VERSION:
        raise ValueError("missing or foreign rendered contract")
    if (
        family not in owner.PROTOCOLS
        or lines[1] != f"# rules: {owner.PROTOCOLS[family]}"
    ):
        raise ValueError("rules text does not match the registered contract")
    if not lines[2].startswith(EXTRA_PREFIX):
        raise ValueError("extra header line does not match the registered contract")
    carried = lines[2][len(EXTRA_PREFIX) :]
    extras = json.loads(carried)
    if not isinstance(extras, dict) or _dump(extras) != carried:
        raise ValueError("extra header line is not in canonical form")
    _check_extras(family, extras)
    if fmt == "prose":
        if lines[3] != PROSE_NOTE:
            raise ValueError("prose preamble does not match the registered contract")
        return family, [_parse_sentence(line, family) for line in lines[4:]], extras
    if lines[3] != TABLE_NOTE or lines[4] != "|".join(_columns_for(family)):
        raise ValueError("table header does not match the registered contract")
    optional = _optional_for(family)
    rows = []
    for line in lines[5:]:
        cells = dict(zip(_columns_for(family), line.split("|"), strict=True))
        rows.append(
            _row_for(
                family,
                {
                    field: (
                        _verbatim(field, cell, _patterns_for(family))
                        if cell or field not in optional
                        else None
                    )
                    for field, cell in cells.items()
                },
            )
        )
    return family, rows, extras


def _jsonl_context(text: str) -> tuple[dict[str, Any], list[Any]]:
    """Parse a jsonl context with the module its own header names."""
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty context")
    family = json.loads(lines[0]).get("family")
    return _module_for(family).parse_context(text)


def parse_world(text: str, fmt: str) -> list[Any]:
    """Round-trip parser: parse_world(render_world(rows, f, fmt), fmt) == rows."""
    return _parse_tagged(text, fmt)[1]


# Format-specific phrasings, pinned as PROMPTS are: the executor reads the typed
# program, so only a byte-identical re-render from (program, fmt, index) is admitted.
# One skeleton serves both formats and every family, only the container's own
# name moving; jsonl reuses each owner's bank, so its question is byte-identical
# to that owner's render_instruction.
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
    # jsonl reuses both owners' banks, so its question is byte-identical to
    # render_instruction: no existing row changes meaning.
    "jsonl": {**records.PROMPTS, **families.PROMPTS},
    "prose": {family: PROSE_PROMPTS for family in RENDERABLE},
    "table": {family: TABLE_PROMPTS for family in RENDERABLE},
}

# The vocabulary line moves with the format; the program and return contract below
# it do not. Each family's line names the row shapes its own rows carry.
_PROSE_FIELDS = (
    "each sentence names one row's id, entity, category, amount, date and memo.",
    " Reference sentences name a reference row's id, entity, amount and memo.",
)
_TABLE_FIELDS = (
    "the head names the columns id, type, entity, category, amount, date and memo, "
    "and every line is one row with those cells verbatim; an empty cell means the "
    "field is absent. Reference rows carry no category or date."
)
FAMILY_FORMAT_FIELDS: dict[str, dict[str, str]] = {
    "alias_locate": {
        "prose": (
            "each sentence names one record row's id, entity, category, amount, "
            "date and memo; alias sentences name an alias row's id, alias, entity "
            "and memo."
        ),
        "table": (
            "the head names the columns id, type, entity, category, amount, date, "
            "memo and alias, and every line is one row with those cells verbatim; "
            "an empty cell means the field is absent. Alias rows carry no category, "
            "amount or date."
        ),
    },
    "asof_state": {
        "prose": "each sentence names one event's id, entity, kind, amount, date, reveal and memo.",
        "table": (
            "the head names the columns id, type, entity, kind, amount, date, reveal "
            "and memo, and every line is one event with those cells verbatim."
        ),
    },
    "rule_holdout": {
        "prose": (
            "each sentence names one row's id, entity and memo; record sentences "
            "add x and y, demo sentences add points and label."
        ),
        "table": (
            "the head names the columns id, type, entity, x, y, label, points and "
            "memo, and every line is one row with those cells verbatim; an empty "
            "cell means the field is absent. Record rows carry no label or points; "
            "demo rows carry no x or y."
        ),
    },
}
FORMAT_FIELDS: dict[str, dict[str, str]] = {
    "jsonl": {**records.FIELDS, **families.FIELDS},
    "prose": {
        **{
            family: _PROSE_FIELDS[0]
            + (_PROSE_FIELDS[1] if family == "join_lookup" else "")
            for family in records.FAMILIES
        },
        **{
            family: FAMILY_FORMAT_FIELDS[family]["prose"]
            for family in FAMILY_FORMAT_FIELDS
        },
    },
    "table": {
        **{family: _TABLE_FIELDS for family in records.FAMILIES},
        **{
            family: FAMILY_FORMAT_FIELDS[family]["table"]
            for family in FAMILY_FORMAT_FIELDS
        },
    },
}


def render_question(program: dict[str, Any], fmt: str, phrasing_index: int) -> str:
    """The question's own expression: the program is fixed, the vocabulary moves.

    The index rotates within the format's own pool (jsonl carries each owner's
    full bank, 24 or 25 phrasings per family; the others 8), so a caller may
    pass the compiler's index unchanged.
    """
    if fmt not in FORMATS:
        raise ValueError("unsupported format")
    family = program.get("family")
    owner = _module_for(family)
    prompts = FORMAT_PROMPTS[fmt].get(family)
    if prompts is None or type(phrasing_index) is not int:
        raise ValueError("unsupported program family or phrasing index")
    return (
        f"Task: {prompts[phrasing_index % len(prompts)]}. "
        f"Fields: {FORMAT_FIELDS[fmt][family]} "
        f"Program: {owner.describe_program(program)}. "
        f"{owner.RETURNS[family]}"
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

    Each format is parsed back to rows (round-trip asserted), re-rendered into
    the owner's jsonl contract -- header extras included, so rule_holdout
    re-executes -- and solved by the same executor; the three golds must be
    byte-identical. A bank-builder admits a world only when `equivalent`.
    """
    rows, family, owner, extras = _rows_and_family(world)
    program = _as_program(question)
    gold_by_format: dict[str, Any] = {}
    errors: list[str] = []
    for fmt in FORMATS:
        try:
            parsed = parse_world(render_world(world, family, fmt), fmt)
            if parsed != rows:
                raise ValueError("round-trip does not reproduce the rows")
            gold_by_format[fmt] = owner.solve_visible(
                _render_jsonl(parsed, family, owner, extras), program
            )
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

# Evidence fallback per owner: the row types a program of that owner can read.
# records rows are all records; families evidence is primary (record, event),
# the alias declarations a bind step resolves, and the demonstrations a rule
# query infers from.
_EVIDENCE_TYPES = {
    records: ("record",),
    families: (*families.PRIMARY_TYPES, "alias", "demo"),
}


def _content_tokens(text: str) -> list[str]:
    return [token for token in _WORD.findall(text.lower()) if token not in STOPWORDS]


def lexical_overlap(world: Any, question: Any, fmt: str = "prose") -> float:
    """How much of the evidence reappears in the question: overlap / evidence.

    Evidence is the K consumed rows' own text (the question's `consumed` when it
    carries one, the owner's evidence-capable row types otherwise); the question
    is its instruction.
    """
    rows, family, owner, _extras = _rows_and_family(world)
    fields = question if isinstance(question, dict) else {}
    provenance = fields.get("consumed") or []
    keep = set(provenance)
    if len(keep) != len(provenance):
        raise ValueError("consumed provenance is not a distinct id set")
    evidence_types = _EVIDENCE_TYPES[owner]
    evidence = (
        [row for row in rows if row.id in keep]
        if keep
        else [row for row in rows if row.type in evidence_types]
    )
    if len(evidence) < len(keep):
        raise ValueError("consumed id missing from the world")
    typed = {
        "jsonl": lambda row: _dump(row.visible()),
        "prose": lambda row: _prose_line(row, family),
    }.get(fmt, lambda row: _table_row(row, family))
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
