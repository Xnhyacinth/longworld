"""Literal dollar/proviso comparisons between two authenticated bill prints.

Structural locations, dollar figures and quoted text are compared mechanically;
no provision identity across renumbering, enactment or legal effect is inferred.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal

REVISION = "longworld.govinfo-printed-amount-taskbank.v1"
MONEY = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?!\d|,\d|\.\d)")
EXCEPTION = re.compile(
    r"\b(?:provided(?: further)?[, ]+that|except(?: that| as)?|notwithstanding|shall not)\b",
    re.IGNORECASE,
)
PROGRAMS = {
    "changed_exception_amounts": "List every shared structural location with exactly one printed dollar figure in each version, a changed figure, and an exception marker in at least one version.",
    "largest_exception_amount_change": "Among shared structural locations with exactly one printed dollar figure in each version, a changed figure, and an exception marker in at least one version, select every location tied for the largest absolute numeric change.",
    "introduced_exception_amounts": "List shared structural locations with exactly one printed dollar figure in each version, a changed figure, no exception marker in EAS and an exception marker in EAH.",
    "removed_exception_amounts": "List shared structural locations with exactly one printed dollar figure in each version, a changed figure, an exception marker in EAS and no exception marker in EAH.",
    "added_exception_amounts": "List structural locations absent from EAS that have exactly one printed dollar figure and an exception marker in EAH.",
    "largest_added_exception_amount": "Among structural locations absent from EAS that have exactly one printed dollar figure and an exception marker in EAH, select every location tied for the largest figure.",
    "removed_exception_sections": "List structural locations absent from EAH that have exactly one printed dollar figure and an exception marker in EAS.",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def amount(text):
    if text is None or text.count("$") != 1:
        return None
    matches = list(MONEY.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.match(
        r"\s*(?:million|billion|thousand)\b", text[match.end() :], re.IGNORECASE
    ):
        return None
    return Decimal(match[1].replace(",", "") + ("." + match[2] if match[2] else ""))


def decimal_text(value):
    return (
        format(value, "f").rstrip("0").rstrip(".")
        if value.as_tuple().exponent < 0
        else str(value)
    )


def exception_excerpt(text):
    match = EXCEPTION.search(text or "")
    return text[match.start() :] if match else ""


def source_maps(world):
    maps = {}
    for stage in ("eas", "eah"):
        sections = world["stages"][stage]["sections"]
        grouped = defaultdict(list)
        for section in sections:
            grouped[section["base_key"]].append(section["text"])
        maps[stage] = {
            key: values[0] for key, values in grouped.items() if len(values) == 1
        }
    # An ambiguous location at either endpoint cannot be treated as absent.
    ambiguous = set(world["stages"]["eas"]["ambiguous_keys"]) | set(
        world["stages"]["eah"]["ambiguous_keys"]
    )
    return {
        stage: {k: v for k, v in values.items() if k not in ambiguous}
        for stage, values in maps.items()
    }


def scoped_maps(world, prefix):
    maps = source_maps(world)
    keys = set(maps["eas"]) | set(maps["eah"])
    keys = {
        key
        for key in keys
        if (prefix == "all" or key.startswith(prefix + "/"))
        and any("$" in values.get(key, "") for values in maps.values())
    }
    return {
        stage: {k: text for k, text in values.items() if k in keys}
        for stage, values in maps.items()
    }


def execute(maps, program):
    if program not in PROGRAMS:
        raise ValueError("unknown printed-version comparison program")
    candidates = []
    for key in sorted(set(maps["eas"]) | set(maps["eah"])):
        old, new = maps["eas"].get(key), maps["eah"].get(key)
        before, after = amount(old), amount(new)
        old_exception, new_exception = (
            bool(EXCEPTION.search(old or "")),
            bool(EXCEPTION.search(new or "")),
        )
        shared_change = (
            old is not None
            and new is not None
            and before is not None
            and after is not None
            and before != after
        )
        include = (
            shared_change and (old_exception or new_exception)
            if program
            in {"changed_exception_amounts", "largest_exception_amount_change"}
            else shared_change and not old_exception and new_exception
            if program == "introduced_exception_amounts"
            else shared_change and old_exception and not new_exception
            if program == "removed_exception_amounts"
            else old is None and after is not None and new_exception
            if program in {"added_exception_amounts", "largest_added_exception_amount"}
            else new is None and before is not None and old_exception
        )
        if include:
            candidates.append(
                {
                    "section": key,
                    "eas_amount": decimal_text(before) if before is not None else None,
                    "eah_amount": decimal_text(after) if after is not None else None,
                    "change": decimal_text(after - before)
                    if before is not None and after is not None
                    else None,
                    "eas_exception_excerpt": exception_excerpt(old),
                    "eah_exception_excerpt": exception_excerpt(new),
                }
            )
    if candidates and program == "largest_exception_amount_change":
        maximum = max(abs(Decimal(r["change"])) for r in candidates)
        candidates = [r for r in candidates if abs(Decimal(r["change"])) == maximum]
    elif candidates and program == "largest_added_exception_amount":
        maximum = max(Decimal(r["eah_amount"]) for r in candidates)
        candidates = [r for r in candidates if Decimal(r["eah_amount"]) == maximum]
    return {"locations": candidates}


def render(world, prefix):
    maps = scoped_maps(world, prefix)
    groups = {}
    for stage in ("eas", "eah"):
        for key, text in maps[stage].items():
            groups.setdefault(text, []).append((stage, key))
    header = (
        f"Bill {world['bill_id']}; printed-version monetary-section scope: {prefix}.\n"
    )
    for stage in ("eas", "eah"):
        value = world["stages"][stage]
        header += f"{stage.upper()} printed {value['date']}; source: {value['url']}\n"
    parts, blocks, position = [header], [], len(header)
    for text, locations in groups.items():
        label = " | ".join(f"{stage.upper()}:{key}" for stage, key in locations)
        block = f"\n=== Printed section | {label} ===\n{text}\n"
        blocks.append(
            {
                "start": position,
                "end": position + len(block),
                "locations": locations,
                "text": text,
            }
        )
        parts.append(block)
        position += len(block)
    return "".join(parts), blocks, maps


def maps_from_blocks(blocks):
    maps = {"eas": {}, "eah": {}}
    for block in blocks:
        for stage, key in block["locations"]:
            if key in maps[stage]:
                raise ValueError("duplicate visible section")
            maps[stage][key] = block["text"]
    return maps


def question(world, prefix, program):
    return (
        f"Compare bill {world['bill_id']}, EAS print dated {world['stages']['eas']['date']} with EAH print dated {world['stages']['eah']['date']}. "
        f"Scope: {prefix}; the supplied complete sections cover structural locations with a dollar sign in at least one print, together with their other-print counterparts when present. "
        "Match structural locations exactly; do not infer renumbering or legal effect. "
        "An exception marker means a literal occurrence of provided that, provided further that (allowing a comma), except, notwithstanding, or shall not, ignoring case. "
        "A single figure means exactly one dollar sign followed by an unscaled numeric dollar figure in that complete section. "
        + PROGRAMS[program]
        + " Return JSON with locations sorted by structural key. For each return section, eas_amount, eah_amount, change (EAH minus EAS for shared locations), eas_exception_excerpt and eah_exception_excerpt. Amounts are decimal strings without separators; absent endpoints and inapplicable changes are null. Each excerpt is the exact normalized section text from its first exception marker to the section end, or an empty string if no marker is present."
    )
