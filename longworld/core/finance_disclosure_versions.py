"""Source-bound comparative disclosure versions, with valid and available time."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from urllib.parse import urljoin

from longworld.core import finance_taskbank as base
from longworld.core.issuerfilingworkflow import _visible_cell_text
from longworld.core.issuerinlineworkflow import (
    INLINE_ROLES,
    _context_records,
    _elements,
    _inline_number,
    _unit_records,
)
from scripts.materialize_finance_taskbank_v2 import load_world

SCHEMA = "longworld.finance-disclosure-version-task.v1"
REVISION = "finance-disclosure-versions.v1"
ROLES = tuple(r for r in INLINE_ROLES if r != "liabilities_and_equity")
FAMILIES = (
    "asof_disclosure_delta",
    "asof_changed_metrics",
    "asof_largest_disclosure_change",
    "asof_sign_transition",
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class CellGrid(HTMLParser):
    """A bounded visible table grid; keep exact source offsets for every cell."""

    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0]
        for m in re.finditer("\n", text):
            self.lines.append(m.end())
        self.rows = []
        self.row = None
        self.cell = None
        self.feed(text)
        self.close()
        if self.cell is not None or self.row is not None:
            raise ValueError("unclosed source table cell or row")
        self.cells = []
        occupied = {}
        for ri, row in enumerate(self.rows):
            col = 0
            for cell in row:
                while occupied.get((ri, col)):
                    col += 1
                colspan, rowspan = cell["colspan"], cell["rowspan"]
                cell.update(row=ri, col=col, col_end=col + colspan)
                self.cells.append(cell)
                for rr in range(ri, ri + rowspan):
                    for cc in range(col, col + colspan):
                        if occupied.get((rr, cc)):
                            raise ValueError("overlapping source table cells")
                        occupied[rr, cc] = True
                col += colspan

    def source_offset(self):
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in {"td", "th"}:
            self.finish_cell(self.source_offset() + len(self.get_starttag_text()))

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            if self.row is not None:
                raise ValueError("nested source table row")
            self.row = []
        elif tag in {"td", "th"}:
            if self.row is None or self.cell is not None:
                raise ValueError("nested source table cell")
            attrs = dict(attrs)
            colspan, rowspan = (
                int(attrs.get("colspan", "1")),
                int(attrs.get("rowspan", "1")),
            )
            if not 1 <= colspan <= 64 or not 1 <= rowspan <= 64:
                raise ValueError("unbounded source table span")
            self.cell = {
                "start": self.source_offset(),
                "tag": tag,
                "colspan": colspan,
                "rowspan": rowspan,
                "attrs": attrs,
            }

    def finish_cell(self, end):
        if self.cell is None:
            raise ValueError("unmatched source table cell")
        cell = self.cell
        cell["end"] = end
        cell["raw"] = self.text[cell["start"] : cell["end"]]
        cell["visible"] = _visible_cell_text(cell["raw"])
        hidden = (
            bool(
                re.search(
                    r"display\s*:\s*none|visibility\s*:\s*hidden",
                    cell["attrs"].get("style", ""),
                    re.IGNORECASE,
                )
            )
            or "hidden" in cell["attrs"]
        )
        if hidden and cell["visible"]:
            raise ValueError("hidden numeric/table text")
        if not hidden:
            self.row.append(cell)
        self.cell = None

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            self.finish_cell(self.source_offset() + len("</" + tag + ">"))
        elif tag == "tr":
            if self.row is None or self.cell is not None:
                raise ValueError("invalid source row close")
            self.rows.append(self.row)
            self.row = None


def column_header(grid, element_start, valid_end):
    cells = [c for c in grid.cells if c["start"] <= element_start < c["end"]]
    if len(cells) != 1:
        raise ValueError("comparative operand cell is ambiguous")
    cell = cells[0]
    numeric_rows = [
        c["row"] for c in grid.cells if "<ix:nonfraction" in c["raw"].casefold()
    ]
    first = min(numeric_rows)
    headers = [
        c
        for c in grid.cells
        if c["row"] < first
        and c["col"] < cell["col_end"]
        and cell["col"] < c["col_end"]
        and re.search(r"\b20\d{2}\b", c["visible"])
    ]
    years = {y for h in headers for y in re.findall(r"\b20\d{2}\b", h["visible"])}
    if years != {valid_end[:4]}:
        raise ValueError("visible comparative column year differs from context")
    header = next(h for h in headers if valid_end[:4] in h["visible"])
    months = {
        name: i
        for i, names in enumerate(
            (
                (),
                ("jan", "january"),
                ("feb", "february"),
                ("mar", "march"),
                ("apr", "april"),
                ("may",),
                ("jun", "june"),
                ("jul", "july"),
                ("aug", "august"),
                ("sep", "sept", "september"),
                ("oct", "october"),
                ("nov", "november"),
                ("dec", "december"),
            )
        )
        for name in names
    }
    pattern = r"\b(" + "|".join(months) + r")\.?\s+(\d{1,2})(?:\s*,?\s+(20\d{2}))?\b"
    dated = [
        (h, m)
        for h in headers
        for m in re.finditer(pattern, h["visible"], re.IGNORECASE)
    ]
    if not dated:
        dated = [
            (h, m)
            for h in grid.cells
            if h["row"] < first
            for m in re.finditer(pattern, h["visible"], re.IGNORECASE)
            if m[3] is None
        ]
    dates = {
        date(int(m[3] or valid_end[:4]), months[m[1].casefold()], int(m[2])).isoformat()
        for _, m in dated
    }
    if dates != {valid_end}:
        raise ValueError("visible comparative header date differs from context")
    support = [
        {
            "start": h["start"],
            "end": h["end"],
            "quote": h["raw"],
            "visible": h["visible"],
        }
        for h, _ in dated
    ]
    return cell, {**header, "date_support": support}


def available_span(listing, document):
    matches = []
    for row in re.finditer(
        r"<tr\b[^>]*>.*?</tr>", listing["text"], re.IGNORECASE | re.DOTALL
    ):
        links = {
            urljoin(listing["source_url"], html.unescape(m))
            for m in re.findall(r"href=[\"\']([^\"\']+)[\"\']", row.group())
        }
        if document["source_url"] not in links:
            continue
        cells = list(
            re.finditer(
                r"<td\b[^>]*>(.*?)</td>", row.group(), re.IGNORECASE | re.DOTALL
            )
        )
        if len(cells) < 2 or _visible_cell_text(cells[1][1]) != "10-K":
            continue
        quote = _visible_cell_text(cells[0][1])
        normalized = (
            datetime.strptime(quote, "%m/%d/%y")
            .replace(tzinfo=timezone.utc)
            .date()
            .isoformat()
        )
        if normalized != document["filing_date"]:
            raise ValueError("listing available date differs from verified document")
        local = row.group().find(quote)
        if local < 0:
            raise ValueError("exact available-date quote missing")
        matches.append(
            {
                "source_url": listing["source_url"],
                "source_sha256": hashlib.sha256(listing["text"].encode()).hexdigest(),
                "start": row.start() + local,
                "end": row.start() + local + len(quote),
                "quote": quote,
                "available_time": normalized,
            }
        )
    if len(matches) != 1:
        raise ValueError("official listing availability row ambiguous")
    return matches[0]


def comparative_claims(native, listing):
    claims, rejected, availability = [], [], {}
    for document in native["documents"]:
        text = document["text"]
        contexts = _context_records(text)
        units = _unit_records(text)
        availability[document["record_id"]] = available_span(listing, document)
        grids = {
            s["section_id"]: CellGrid(text[s["start"] : s["end"]])
            for s in document["sections"]
        }
        for e in _elements(text):
            roles = [
                r
                for r, c in INLINE_ROLES.items()
                if c == e.attrs.get("name") and r in ROLES
            ]
            if e.kind != "nonfraction" or not roles:
                continue
            sections = [
                s
                for s in document["sections"]
                if s["start"] <= e.start < e.end <= s["end"]
            ]
            if len(sections) != 1:
                continue
            section = sections[0]
            role = roles[0]
            context = contexts.get(e.attrs.get("contextref"), {})
            instant = base.METRICS[role][1] == "instant"
            if (
                context.get("cik") != native["issuer"]["cik"]
                or context.get("dimensioned")
                or context.get("instant") != instant
            ):
                continue
            if context["end"] > document["report_date"]:
                continue
            if (
                not instant
                and not 350
                <= (
                    date.fromisoformat(context["end"])
                    - date.fromisoformat(context["start"])
                ).days
                <= 380
            ):
                continue
            try:
                if units.get(e.attrs.get("unitref")) != ("iso4217:USD",):
                    raise ValueError("unit does not resolve to USD")
                quote, value, start = _inline_number(text, e, role)
                cell, header = column_header(
                    grids[section["section_id"]],
                    e.start - section["start"],
                    context["end"],
                )
                visible = cell["visible"].replace("$", "").replace(" ", "")
                if visible != ("(" + quote + ")" if value < 0 else quote):
                    raise ValueError("inline sign/value differs from visible cell")
                valid = {
                    "kind": "instant" if instant else "duration",
                    "start": context["start"] or None,
                    "end": context["end"],
                }
                claim = {
                    "metric": role,
                    "unit": "USD_millions",
                    "valid_time": valid,
                    "available_time": document["filing_date"],
                    "record_id": document["record_id"],
                    "source_version": document["source_sha256"],
                    "source_url": document["source_url"],
                    "value": value,
                    "context_id": e.attrs["contextref"],
                    "dimensioned": False,
                    "scale": e.attrs["scale"],
                    "sign": e.attrs.get("sign", ""),
                    "span": {
                        "start": start,
                        "end": start + len(quote),
                        "quote": quote,
                        "section_id": section["section_id"],
                    },
                    "visible_cell": cell["visible"],
                    "column_header": {
                        "start": section["start"] + header["start"],
                        "end": section["start"] + header["end"],
                        "quote": text[
                            section["start"] + header["start"] : section["start"]
                            + header["end"]
                        ],
                        "visible": header["visible"],
                        "date_support": [
                            {
                                **h,
                                "start": section["start"] + h["start"],
                                "end": section["start"] + h["end"],
                            }
                            for h in header["date_support"]
                        ],
                    },
                    "availability_evidence": availability[document["record_id"]],
                }
                claim["claim_id"] = digest(claim)
                claims.append(claim)
            except ValueError as exc:
                rejected.append(
                    {
                        "record_id": document["record_id"],
                        "metric": role,
                        "valid_end": context["end"],
                        "source_span": [e.start, e.end],
                        "reason": str(exc),
                    }
                )
    grouped = defaultdict(list)
    for c in claims:
        grouped[(c["record_id"], c["metric"], canonical(c["valid_time"]))].append(c)
    admitted = []
    for key, values in grouped.items():
        if len(values) != 1:
            rejected.append(
                {"slot": key, "reason": "ambiguous duplicate comparative operand"}
            )
        else:
            admitted.extend(values)
    return (
        sorted(
            admitted,
            key=lambda c: (
                c["available_time"],
                c["metric"],
                canonical(c["valid_time"]),
            ),
        ),
        rejected,
        availability,
    )


class DisclosureWorld:
    def __init__(self, native, claims, rejections, availability):
        base._check_world(native)
        self.native = native
        self.claims = claims
        self.rejections = rejections
        self.availability = availability
        self._snapshot = digest([claims, rejections, availability])

    def check(self):
        base._check_world(self.native)
        if digest([self.claims, self.rejections, self.availability]) != self._snapshot:
            raise ValueError("version world changed after source validation")


def load_disclosure_world(config):
    native = load_world(config)
    raw = Path(native["source_manifest"]["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != native["source_manifest"]["sha256"]:
        raise ValueError("source manifest changed after native authentication")
    manifest = json.loads(raw)
    # The native loader has already authenticated and revalidated the exact listing.
    listing = manifest["listing"]
    claims, rejected, availability = comparative_claims(native, listing)
    return DisclosureWorld(native, claims, rejected, availability)


def slots(world):
    result = defaultdict(list)
    for c in world.claims:
        result[(c["metric"], canonical(c["valid_time"]))].append(c)
    return {
        k: sorted(v, key=lambda c: (c["available_time"], c["source_version"]))
        for k, v in result.items()
    }


def select_claim(
    world, metric, valid_time, cutoff, *, latest=False, allowed_records=None
):
    eligible = [
        c
        for c in world.claims
        if c["metric"] == metric
        and c["valid_time"] == valid_time
        and (latest or c["available_time"] <= cutoff)
        and (allowed_records is None or c["record_id"] in allowed_records)
    ]
    if not eligible:
        raise ValueError("no eligible source disclosure")
    selected = sorted(
        eligible, key=lambda c: (c["available_time"], c["source_version"])
    )
    if (
        len(selected) > 1
        and selected[-1]["available_time"] == selected[-2]["available_time"]
    ):
        raise ValueError("ambiguous same-day disclosure version")
    return selected[-1]


def execute(world, parameters, *, latest=False, allowed_records=None):
    world.check()
    changes = []
    consumed = []
    for target in parameters["targets"]:
        old = select_claim(
            world,
            target["metric"],
            target["valid_time"],
            parameters["as_of_before"],
            latest=latest,
            allowed_records=allowed_records,
        )
        new = select_claim(
            world,
            target["metric"],
            target["valid_time"],
            parameters["as_of_after"],
            latest=latest,
            allowed_records=allowed_records,
        )
        consumed += [old["claim_id"], new["claim_id"]]
        changes.append(
            {
                "metric": target["metric"],
                "before": old["value"],
                "after": new["value"],
                "difference": new["value"] - old["value"],
                "before_filing_date": old["available_time"],
                "after_filing_date": new["available_time"],
            }
        )
    family = parameters["family"]
    if family == "asof_disclosure_delta":
        answer = changes[0]
    elif family == "asof_changed_metrics":
        answer = {"changes": [c for c in changes if c["difference"] != 0]}
    elif family == "asof_largest_disclosure_change":
        size = max(abs(c["difference"]) for c in changes)
        answer = {
            "absolute_difference": size,
            "metrics": [c for c in changes if abs(c["difference"]) == size],
        }
    elif family == "asof_sign_transition":
        answer = {
            **changes[0],
            "before_positive": changes[0]["before"] > 0,
            "after_positive": changes[0]["after"] > 0,
        }
    else:
        raise ValueError("unsupported disclosure family")
    return {
        "answer": {"unit": "USD_millions", **answer},
        "consumed_claim_ids": sorted(set(consumed)),
    }


def question(world, p):
    targets = [
        {"metric": base.METRICS[t["metric"]][0], "valid_time": t["valid_time"]}
        for t in p["targets"]
    ]
    instructions = {
        "asof_disclosure_delta": "Report the two selected disclosed amounts and after-minus-before difference.",
        "asof_changed_metrics": "List exactly the metrics whose selected disclosed amounts differ, with both amounts and after-minus-before differences.",
        "asof_largest_disclosure_change": "Select all metrics with the largest absolute difference between the two selected disclosed amounts; report the amounts and signed differences.",
        "asof_sign_transition": "Report both selected amounts, their difference, and whether each amount is positive.",
    }
    return f"Issuer: {world.native['issuer']['name']} (CIK {world.native['issuer']['cik']}).\nTargets: {canonical(targets)}.\nFor each target and cutoff, use its latest disclosure in a filing available on or before that date within the supplied history. Cutoffs: {p['as_of_before']} and {p['as_of_after']}.\nCompare amounts as disclosed in those versions; the difference describes disclosures, not an economic change.\n{instructions[p['family']]} Include the selected filing dates. Amounts are USD millions."


def compile_asof_tasks(world):
    world.check()
    grouped = slots(world)
    specs = []
    edges = []
    for (metric, valid), claims in grouped.items():
        for old, new in pairwise(claims):
            if old["value"] == new["value"]:
                continue
            target = {"metric": metric, "valid_time": json.loads(valid)}
            common = {
                "targets": [target],
                "as_of_before": old["available_time"],
                "as_of_after": new["available_time"],
            }
            specs.append({"family": "asof_disclosure_delta", **common})
            edges.append((old, new))
            if (old["value"] > 0) != (new["value"] > 0):
                specs.append({"family": "asof_sign_transition", **common})
    scopes = sorted(
        {
            (a["valid_time"]["end"], a["available_time"], b["available_time"])
            for a, b in edges
        }
    )
    for end, before, after in scopes:
        targets = []
        for (metric, valid), claims in sorted(grouped.items()):
            v = json.loads(valid)
            if v["end"] != end:
                continue
            if any(c["available_time"] <= before for c in claims) and any(
                c["available_time"] <= after for c in claims
            ):
                targets.append({"metric": metric, "valid_time": v})
        if len(targets) >= 2:
            for family in ("asof_changed_metrics", "asof_largest_disclosure_change"):
                specs.append(
                    {
                        "family": family,
                        "targets": targets,
                        "as_of_before": before,
                        "as_of_after": after,
                    }
                )
    tasks = []
    seen = set()
    for p in specs:
        identity = digest([REVISION, world.native["source_collection_id"], p])
        if identity in seen:
            continue
        seen.add(identity)
        result = execute(world, p)
        task = {
            "schema_version": SCHEMA,
            "semantic_task_id": identity,
            "family": p["family"],
            "parameters": p,
            "question": question(world, p),
            "answer": result["answer"],
            "consumed_claim_ids": result["consumed_claim_ids"],
            "source_collection_id": world.native["source_collection_id"],
            "split_group_id": world.native["split_group_id"],
            "strict_long_dependency_verified": False,
            "production_eligible": False,
        }
        tasks.append(task)
    return sorted(tasks, key=lambda t: t["semantic_task_id"])


def shortcut_probes(world, task):
    # Baselines receive only the parameters and source world, never the gold answer.
    p = task["parameters"]
    latest = execute(world, p, latest=True)["answer"]
    gold = execute(world, p)["answer"]
    single = []
    for doc in world.native["documents"]:
        try:
            if execute(world, p, allowed_records={doc["record_id"]})["answer"] == gold:
                single.append(doc["record_id"])
        except ValueError:
            pass
    return {
        "latest_values_ignoring_availability_correct": latest == gold,
        "latest_values_answer": latest,
        "single_primary_statement_filing_sufficient": single,
        "alternative_proof_search_complete": False,
        "note_summary_search_complete": False,
        "strict_long_dependency_verified": False,
    }
