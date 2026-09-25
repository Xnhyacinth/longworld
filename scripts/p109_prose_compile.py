"""Compile official RFC numeric rules with clearly simulated inspections."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import _answer_hash
from scripts.audit_wiki_join_positions import token_span
from scripts.p109_prose_support import (
    MINIMUM,
    _field,
    _pin,
    _sha,
)
from scripts.p109_prose_support import (
    SCHEMA as SUPPORT_SCHEMA,
)
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p109-prose-numeric-rule.v1"
SOURCE_MARKER = (
    "\n\nSIMULATED inspection records with explicit field values "
    "(not an RFC publication):\n"
)
QUESTION_MARKER = "\n\nQUESTION\n"
RECORD = re.compile(r"Observation (obs-[0-9a-f]{12}): ([a-z][a-z0-9_]*)=([0-9]+)\.")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _records(rfc_id: str, field: str, limit: int, seed: int, count: int) -> list[dict]:
    if count < 12 or count % 4:
        raise ValueError("P109 requires at least 12 records in groups of four")
    violating = count // 4 + seed % 4
    values = [limit - 1] * violating + [limit] * (count // 4)
    values += [limit + 1] * (count - len(values))
    random.Random(seed).shuffle(values)
    return [
        {
            "id": "obs-"
            + hashlib.sha256(f"{rfc_id}|{field}|{index}".encode()).hexdigest()[:12],
            "field": field,
            "value": value,
        }
        for index, value in enumerate(values)
    ]


def _answer(records: list[dict], limit: int) -> dict:
    ids = sorted(row["id"] for row in records if row["value"] < limit)
    if not 2 <= len(ids) <= len(records) - 2:
        raise ValueError("P109 degenerate violation set")
    return {"count": len(ids), "ids": ids}


def _visible(context: str, field: str) -> tuple[int, list[dict]] | None:
    if context.count(SOURCE_MARKER) != 1:
        raise ValueError("P109 source/simulation boundary ambiguous")
    source, records_text = context.split(SOURCE_MARKER)
    rules = [
        match
        for match in MINIMUM.finditer(source)
        if _field(match.group("subject")) == field
    ]
    if not rules:
        return None
    if len(rules) != 1:
        raise ValueError("P109 final reader has multiple selected rules")
    limit = int(rules[0].group("limit"))
    records = []
    for line in records_text.splitlines():
        match = RECORD.fullmatch(line)
        if not match or match.group(2) != field:
            raise ValueError("P109 simulated record not independently parseable")
        records.append(
            {"id": match.group(1), "field": field, "value": int(match.group(3))}
        )
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("P109 simulated record IDs repeat")
    return limit, records


def _remove_spans(text: str, spans: list[list[int]]) -> str:
    changed = text
    for left, right in sorted(spans, reverse=True):
        changed = changed[:left] + changed[right:]
    return changed


def _compile_one(job: tuple[dict, dict, dict, int]) -> tuple[dict, dict, dict]:
    config, support_row, source, seed = job
    rfc_id, field, limit = (
        support_row["rfc_id"],
        support_row["field"],
        support_row["limit"],
    )
    source_path = _pin(
        {"path": source["source_path"], "sha256": source["source_sha256"]}
    )
    official = source_path.read_text()
    display_rfc = "RFC " + rfc_id.removeprefix("rfc")
    header = f"OFFICIAL {display_rfc} SOURCE (verbatim, {source['source_url']}):\n"
    source_context = header + official
    records = _records(rfc_id, field, limit, seed, config["records_per_state"])
    record_texts = [
        f"Observation {row['id']}: {field}={row['value']}.\n" for row in records
    ]
    context = source_context + SOURCE_MARKER + "".join(record_texts)
    solved = _visible(context, field)
    if solved is None or solved[0] != limit or solved[1] != records:
        raise ValueError("P109 source rule or simulated records changed")
    answer = _answer(solved[1], solved[0])
    group_spans = [
        [len(header) + left, len(header) + right]
        for left, right in support_row["support_paragraph_spans"]
    ]
    if any(
        context[left:right] != official[left - len(header) : right - len(header)]
        for left, right in group_spans
    ):
        raise ValueError("P109 source paragraph evidence differs")
    without_rule = (
        _remove_spans(source_context, group_spans)
        + SOURCE_MARKER
        + "".join(record_texts)
    )
    if _visible(without_rule, field) is not None:
        raise ValueError("P109 source-rule group deletion left an executable rule")
    stem = field.split("_")[0]
    if any(
        stem.casefold() in paragraph.casefold()
        and f"at least {limit}" in " ".join(paragraph.split()).casefold()
        and "must" in paragraph.casefold()
        for paragraph in re.split(r"\n\s*\n", without_rule.split(SOURCE_MARKER)[0])
    ):
        raise ValueError("P109 equivalent visible minimum support remains")
    hit_index = next(
        index for index, row in enumerate(records) if row["value"] >= limit
    )
    hit = [dict(row) for row in records]
    hit[hit_index]["value"] = limit - 1
    hit_lines = [f"Observation {row['id']}: {field}={row['value']}.\n" for row in hit]
    hit_context = source_context + SOURCE_MARKER + "".join(hit_lines)
    hit_solved = _visible(hit_context, field)
    if hit_solved is None or _answer(hit_solved[1], hit_solved[0]) == answer:
        raise ValueError("P109 record boundary edit did not change answer")
    control = [dict(row) for row in records]
    control[hit_index]["value"] = limit + 2
    control_lines = [
        f"Observation {row['id']}: {field}={row['value']}.\n" for row in control
    ]
    control_context = source_context + SOURCE_MARKER + "".join(control_lines)
    control_solved = _visible(control_context, field)
    if (
        control_solved is None
        or _answer(control_solved[1], control_solved[0]) != answer
    ):
        raise ValueError("P109 non-boundary record edit changed answer")
    question = (
        f"Which simulated inspection observations show an explicit {field} "
        f"value below the minimum stated in {display_rfc}? "
        "Give the total count and every observation ID "
        "in alphabetical order."
    )
    if str(limit) in question:
        raise ValueError("P109 question exposes the hidden threshold")
    messages = [
        {"role": "user", "content": context + QUESTION_MARKER + question},
        {"role": "assistant", "content": _json(answer)},
    ]
    tokenizer = get_tokenizer()
    encoded = tokenize_assistant_only(tokenizer, messages, config["max_full_tokens"])
    full = len(encoded["input_ids"])
    supervised = sum(label != -100 for label in encoded["labels"])
    if (
        not supervised
        or encoded["labels"]
        != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
    ):
        raise ValueError("P109 assistant-only loss mask differs")
    chat = _render_chat(tokenizer, messages, generation_prompt=False)
    user = messages[0]["content"]
    if chat.count(user) != 1:
        raise ValueError("P109 final user position ambiguous")
    offsets = tokenizer(chat, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    user_start = chat.index(user)
    rule_char = [len(header) + p for p in support_row["rule_span"]]
    rule_tokens = token_span(
        offsets, user_start + rule_char[0], user_start + rule_char[1]
    )
    record_spans = []
    cursor = len(source_context) + len(SOURCE_MARKER)
    for row, rendered in zip(records, record_texts, strict=True):
        left, right = cursor, cursor + len(rendered)
        if context[left:right] != rendered:
            raise ValueError("P109 record span differs")
        record_spans.append(
            {
                "id": row["id"],
                "value": row["value"],
                "char_span": [left, right],
                "token_span": list(
                    token_span(offsets, user_start + left, user_start + right)
                ),
            }
        )
        cursor = right
    if rule_tokens[1] > full - supervised or any(
        row["token_span"][1] > full - supervised for row in record_spans
    ):
        raise ValueError("P109 evidence outside final user prompt")
    digest = hashlib.sha256(f"{rfc_id}|{field}|{seed}".encode()).hexdigest()[:24]
    sample_id = "p109-prose-" + digest
    index = {
        "sample_id": sample_id,
        "semantic_task_id": digest,
        "source_group": f"ietf:{rfc_id}:p109-numeric-rule",
        "world_id": f"{rfc_id}:state-{seed}",
        "split": config["split"],
        "source_kind": "grounded_simulation",
        "domain": "protocol",
        "topic": rfc_id,
        "operation": "real_rule_numeric_minimum_violation_set",
        "full_chat_tokens": full,
        "input_tokens": full - supervised,
        "supervised_tokens": supervised,
        "tokenizer_profile": "pinned-chat-template",
        "answer_sha256": _answer_hash(messages[1]["content"]),
        "rule_token_span": list(rule_tokens),
        "record_token_span": [
            record_spans[0]["token_span"][0],
            record_spans[-1]["token_span"][1],
        ],
        "evidence_token_extent": record_spans[-1]["token_span"][1] - rule_tokens[0],
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
    }
    proof = {
        "sample_id": sample_id,
        "source": source,
        "source_group": index["source_group"],
        "split": index["split"],
        "field": field,
        "limit": limit,
        "seed": seed,
        "official_source_span": [len(header), len(source_context)],
        "rule_char_span": rule_char,
        "rule_text": support_row["rule_text"],
        "support_group_spans": group_spans,
        "record_spans": record_spans,
        "question": question,
        "answer": answer,
        "rule_deletion_unresolved": True,
        "record_hit_answer": _answer(hit_solved[1], hit_solved[0]),
        "record_hit_reader_sha256": hashlib.sha256(hit_context.encode()).hexdigest(),
        "record_control_answer": _answer(control_solved[1], control_solved[0]),
        "record_control_reader_sha256": hashlib.sha256(
            control_context.encode()
        ).hexdigest(),
        "changed_record_id": records[hit_index]["id"],
        "claim_limit": "bounded numeric threshold from official prose plus simulated state; whole-record-set scan; no arbitrary semantic-proof guarantee",
    }
    return {"sample_id": sample_id, "messages": messages}, index, proof


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("split") != "train"
        or not 1 <= config.get("workers", 0) <= 4
        or config.get("state_seeds") != [11, 29]
    ):
        raise ValueError("P109 compiler config invalid")
    if output_dir.exists() != verify_only:
        raise ValueError("P109 output must be new or existing for verification")
    support_path = _pin(config["support_ledger"])
    support = json.loads(support_path.read_text())
    if support["schema"] != SUPPORT_SCHEMA or support["reader_tasks_admitted"] != 0:
        raise ValueError("P109 support receipt differs")
    sources = {row["rfc_id"]: row for row in support["sources"]}
    supported = [
        row for row in support["rows"] if row["status"] == "supported_numeric_minimum"
    ]
    jobs = [
        (config, row, sources[row["rfc_id"]], seed)
        for row in supported
        for seed in config["state_seeds"]
    ]
    with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
        compiled = list(pool.map(_compile_one, jobs))
    if len({index["semantic_task_id"] for _, index, _ in compiled}) != len(compiled):
        raise ValueError("P109 semantic task ID collision")
    id_only = {}
    for reader, index, proof in compiled:
        context = reader["messages"][0]["content"].split(QUESTION_MARKER, 1)[0]
        projection = (
            context.split(SOURCE_MARKER, 1)[0]
            + SOURCE_MARKER
            + "\n".join(f"Observation {row['id']}" for row in proof["record_spans"])
        )
        id_only.setdefault(index["source_group"], []).append(
            (hashlib.sha256(projection.encode()).hexdigest(), index["answer_sha256"])
        )
    if any(
        len({x[0] for x in pair}) != 1 or len({x[1] for x in pair}) != 2
        for pair in id_only.values()
    ):
        raise ValueError("P109 paired worlds retain an ID-only answer shortcut")
    payloads = {
        "train.jsonl": [reader for reader, _, _ in compiled],
        "eval.jsonl": [],
        "sample_index.jsonl": [index for _, index, _ in compiled],
        "audit.jsonl": [proof for _, _, proof in compiled],
    }
    if not verify_only:
        output_dir.mkdir(parents=True)
    for name, rows in payloads.items():
        content = "".join(_json(row) + "\n" for row in rows)
        path = output_dir / name
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"P109 frozen reader differs: {name}")
        else:
            path.write_text(content)
    indices = payloads["sample_index.jsonl"]
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "support_ledger_sha256": config["support_ledger"]["sha256"],
        "candidate_views": len(indices),
        "independent_tasks": len(indices),
        "real_rule_source_groups": len(id_only),
        "paired_state_variants": len(indices),
        "splits": dict(sorted(Counter(row["split"] for row in indices).items())),
        "full_chat_token_range": [
            min(row["full_chat_tokens"] for row in indices),
            max(row["full_chat_tokens"] for row in indices),
        ],
        "evidence_extent_range": [
            min(row["evidence_token_extent"] for row in indices),
            max(row["evidence_token_extent"] for row in indices),
        ],
        "files_sha256": {name: _sha(output_dir / name) for name in payloads},
        "train_ready": False,
    }
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path = output_dir / "manifest.json"
    if verify_only:
        if path.read_text() != content:
            raise ValueError("P109 native manifest replay differs")
    else:
        path.write_text(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_json(run(args.config, args.output_dir, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
