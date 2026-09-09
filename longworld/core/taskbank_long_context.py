"""Complete-source context choices for natural 64K/128K/256K capacity bins."""

from __future__ import annotations

from itertools import product

from longworld.core.taskbank_context import (
    assemble_context,
    assemble_statement_context,
    text_sha256,
)

CAPS = (65536, 131072, 262144)
EXACT_RANGES = {
    65536: (64000, 65536),
    131072: (128000, 131072),
    262144: (256000, 262144),
}
CONTEXT_LIMIT = 261120  # Full question/chat/answer headroom, never filled with prose.


def assemble_plan(documents, rendered, plan):
    ids = [entry["record_id"] for entry in plan]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("empty or duplicated context plan")
    by_id = {d["record_id"]: d for d in documents}
    if not set(ids) <= by_id.keys():
        raise ValueError("context plan references unknown source")
    if ids != sorted(ids, key=lambda i: (by_id[i]["report_date"], i)):
        raise ValueError("context plan is not chronological")
    parts, mappings, size = [], {}, 0
    for entry in plan:
        identity, mode = entry["record_id"], entry["mode"]
        if mode == "full":
            text, offsets = assemble_context(documents, rendered, [identity])
            local = [
                {
                    "visible_start": 0,
                    "visible_end": len(rendered[identity]["text"]),
                    "context_start": offsets[identity],
                }
            ]
        elif mode == "statements":
            text, mapped = assemble_statement_context(documents, rendered, [identity])
            local = mapped[identity]
        else:
            raise ValueError("unsupported source context mode")
        mappings[identity] = [
            {**interval, "context_start": interval["context_start"] + size}
            for interval in local
        ]
        parts.append(text)
        size += len(text)
    text = "".join(parts)
    return {
        "text": text,
        "mappings": mappings,
        "offsets": None,
        "sha256": text_sha256(text),
        "plan": plan,
    }


def context_options(documents, rendered, required, tokenizer):
    by_id = {d["record_id"]: d for d in documents}
    if (
        len(required) != len(set(required))
        or not 1 <= len(required) <= 8
        or not set(required) <= by_id.keys()
    ):
        raise ValueError("invalid context source population")
    ids = sorted(required, key=lambda i: (by_id[i]["report_date"], i))
    options = []
    for modes in product(("full", "statements"), repeat=len(ids)):
        plan = [
            {"record_id": identity, "mode": mode}
            for identity, mode in zip(ids, modes, strict=True)
        ]
        context = assemble_plan(documents, rendered, plan)
        tokens = len(tokenizer.encode(context["text"], add_special_tokens=False))
        if not 32768 < tokens <= CONTEXT_LIMIT:
            continue
        cap = next(cap for cap in CAPS if tokens <= cap)
        low, high = EXACT_RANGES[cap]
        context.update(
            tokens=tokens,
            capacity_bin=cap,
            matches_exact_token_range=low <= tokens <= high,
        )
        options.append(context)
    return options


def choose_primary(options, required_count):
    """Favor longer complete-source coverage for genuinely cross-filing queries."""
    if not options:
        raise ValueError("no_natural_long_context_capacity")
    preferred = (
        262144 if required_count >= 3 else 131072 if required_count == 2 else 65536
    )
    available = sorted({o["capacity_bin"] for o in options})
    cap = (
        preferred
        if preferred in available
        else min(available, key=lambda c: (abs(c - preferred), c))
    )
    selected = [o for o in options if o["capacity_bin"] == cap]
    # Complete units only: this priority cannot add padding or clip a source.
    return max(
        selected,
        key=lambda o: (
            o["matches_exact_token_range"],
            sum(e["mode"] == "full" for e in o["plan"]),
            o["tokens"],
            o["sha256"],
        ),
    )
