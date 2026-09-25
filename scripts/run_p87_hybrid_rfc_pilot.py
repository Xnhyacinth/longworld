"""Compile candidate-only RFC 9114 rules plus clearly simulated connection logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import _answer_hash
from scripts.audit_wiki_join_positions import token_span
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p87-hybrid-rfc-pilot.v1"
OPERATIONS = ("http3_support_set", "first_control_frame_violations")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pin(path: str, digest: str) -> str:
    source = (ROOT / path).read_bytes()
    if _sha(source) != digest:
        raise ValueError(f"frozen source changed: {path}")
    return source.decode("utf-8")


def _paragraph(text: str, needle: str) -> str:
    start = text.index(needle)
    end = text.index("\n\n", start)
    result = text[start:end]
    if text.count(result) != 1:
        raise ValueError("rule paragraph is not unique")
    return result


def _rules(text: str) -> dict[str, str]:
    result = {
        OPERATIONS[0]: _paragraph(
            text, "   QUIC connections are established as described"
        ),
        OPERATIONS[1]: _paragraph(text, "   SETTINGS frames always apply to an entire"),
    }
    if 'ALPN token "h3"' not in result[OPERATIONS[0]] or (
        "A SETTINGS frame MUST be sent as the first frame" not in result[OPERATIONS[1]]
    ):
        raise ValueError("RFC rule wording changed")
    return result


def _records(seed: int, count: int) -> list[dict[str, str]]:
    rng = random.Random(seed)
    # The same opaque IDs appear in every paired world, in the same order.
    # Only their states change, so an ID-only reader sees identical inputs
    # with different gold answers across worlds of the same length.
    variants = [
        (alpn, first)
        for alpn in ("h3", "h2", "h3-29")
        for first in ("SETTINGS", "HEADERS", "DATA")
    ]
    assigned = [variants[i % len(variants)] for i in range(count)]
    rng.shuffle(assigned)
    records = []
    for i in range(count):
        alpn, first = assigned[i]
        opaque = _sha(f"p87-v6-opaque-connection-{i}".encode())[:12]
        records.append({"id": f"cx-{opaque}", "alpn": alpn, "first": first})
    if len({row["id"] for row in records}) != count:
        raise ValueError("opaque connection ID collision")
    return sorted(records, key=lambda row: row["id"])


def _oracle(
    operation: str, rule: str | None, records: list[dict[str, str]]
) -> list[str] | None:
    if rule is None:
        return None
    if operation == OPERATIONS[0]:
        marker = 'ALPN token "'
        if rule.count(marker) != 1:
            raise ValueError("ambiguous ALPN rule")
        target = rule.split(marker, 1)[1].split('"', 1)[0]
        return sorted(row["id"] for row in records if row["alpn"] == target)
    marker = " frame MUST be sent as the first frame"
    if rule.count(marker) != 1:
        raise ValueError("ambiguous control-frame rule")
    target = rule.split(marker, 1)[0].split()[-1]
    return sorted(row["id"] for row in records if row["first"] != target)


def _alter_rule(operation: str, rule: str) -> str:
    if operation == OPERATIONS[0]:
        return rule.replace('ALPN token "h3"', 'ALPN token "h2"', 1)
    return rule.replace(
        "A SETTINGS frame MUST be sent as the first frame",
        "A HEADERS frame MUST be sent as the first frame",
        1,
    )


def _filler(text: str, requested_chars: int) -> str:
    end = text.rfind("\n\n", 0, requested_chars)
    if end < requested_chars * 0.9:
        raise ValueError("real source lacks a nearby paragraph boundary")
    result = text[:end]
    if (
        'ALPN token "h3"' in result
        or "SETTINGS frame MUST be sent as the first frame" in result
    ):
        raise ValueError("filler duplicates an essential RFC rule")
    return result


def _world(
    config: dict, rules: dict[str, str], filler: str, seed: int
) -> tuple[str, list[dict]]:
    records = _records(seed, config["records_per_world"])
    midpoint = filler.rfind("\n\n", 0, len(filler) // 2)
    if midpoint < 1:
        raise ValueError("real filler has no middle paragraph boundary")
    first, second = filler[:midpoint], filler[midpoint:]
    header = "RFC 9114, section 3.2 (verbatim excerpt):\n"
    context = (
        header
        + rules[OPERATIONS[0]]
        + "\n\nRFC 9000 (verbatim excerpt, part 1):\n"
        + first
    )
    context += (
        "\n\nRFC 9114, section 7.2.4 (verbatim excerpt):\n" + rules[OPERATIONS[1]]
    )
    context += "\n\nRFC 9000 (verbatim excerpt, part 2):\n" + second
    context += (
        "\n\nSIMULATED connection inspection records (not an IETF publication):\n"
    )
    spans = {}
    for row in records:
        rendered = (
            f"Connection {row['id']}: selected ALPN {row['alpn']}; "
            f"initial HTTP control-stream frame {row['first']}.\n"
        )
        start = len(context)
        context += rendered
        spans[row["id"]] = (start, len(context))
    return context, [
        {"operation": op, "records": records, "record_spans": spans}
        for op in OPERATIONS
    ]


def _solve_visible(operation: str, context: str) -> list[str] | None:
    """Replay only model-visible rule and simulated record text."""
    if operation == OPERATIONS[0]:
        heading = "RFC 9114, section 3.2 (verbatim excerpt):\n"
    else:
        heading = "RFC 9114, section 7.2.4 (verbatim excerpt):\n"
    marker = "\n\nRFC 9000 (verbatim excerpt, part "
    if context.count(heading) != 1:
        return None
    rule = context.split(heading, 1)[1].split(marker, 1)[0].strip()
    if not rule:
        return None
    state_heading = (
        "SIMULATED connection inspection records (not an IETF publication):\n"
    )
    if context.count(state_heading) != 1:
        return None
    records = []
    for line in context.split(state_heading, 1)[1].splitlines():
        match = re.fullmatch(
            r"Connection ([^:]+): selected ALPN ([^;]+); initial HTTP control-stream frame ([A-Z]+)\.",
            line,
        )
        if not match:
            raise ValueError("simulated state record cannot be replayed")
        records.append({"id": match[1], "alpn": match[2], "first": match[3]})
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("simulated state IDs are duplicated")
    return _oracle(operation, rule, records)


def _id_only_projection(context: str) -> str:
    """Keep the full source and visible ID order but remove record attributes."""
    heading = "SIMULATED connection inspection records (not an IETF publication):\n"
    source, states = context.split(heading, 1)
    identifiers = [line.split(": ", 1)[0] for line in states.splitlines()]
    if not identifiers or any(
        not item.startswith("Connection cx-") for item in identifiers
    ):
        raise ValueError("cannot build an ID-only projection")
    return source + heading + "\n".join(identifiers)


def _compile_one(
    payload: tuple[dict, dict[str, str], str, int],
) -> tuple[list[dict], list[dict]]:
    config, rules, filler, seed = payload
    tokenizer = get_tokenizer()
    context, specs = _world(config, rules, filler, seed)
    accepted, rejected = [], []
    for spec in specs:
        operation = spec["operation"]
        answer = _solve_visible(operation, context)
        positive = answer[0] if answer else None
        altered_context = context.replace(
            rules[operation], _alter_rule(operation, rules[operation]), 1
        )
        altered = _solve_visible(operation, altered_context)
        record_chars = spec["record_spans"][positive]
        record_text = context[record_chars[0] : record_chars[1]]
        deleted_state = _solve_visible(operation, context.replace(record_text, "", 1))
        deleted_rule = _solve_visible(
            operation, context.replace(rules[operation], "", 1)
        )
        reason = None
        if (
            not answer
            or altered == answer
            or deleted_state == answer
            or deleted_rule is not None
        ):
            reason = "bounded_rule_or_state_intervention_failed"
        question = (
            "According to the supplied protocol excerpt, which simulated connection IDs indicate HTTP/3 support during establishment? Return only a sorted JSON array."
            if operation == OPERATIONS[0]
            else "According to the supplied protocol excerpt, which simulated connection IDs violate the initial control-stream frame requirement? Return only a sorted JSON array."
        )
        user = context + "\n\nQUESTION\n" + question
        messages = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(answer)},
        ]
        try:
            encoded = tokenize_assistant_only(
                tokenizer, messages, config["max_seq_len"]
            )
            full_chat = _render_chat(tokenizer, messages, generation_prompt=False)
            user_start = full_chat.find(user)
            if user_start < 0 or full_chat.count(user) != 1:
                raise ValueError("chat template loses reader boundary")
            mapped = tokenizer(full_chat, truncation=False, return_offsets_mapping=True)
            if list(mapped["input_ids"]) != encoded["input_ids"]:
                raise ValueError("position tokenizer differs from final masked chat")
            offsets = mapped["offset_mapping"]
            rule_start = context.index(rules[operation])
            rule_span = token_span(
                offsets,
                user_start + rule_start,
                user_start + rule_start + len(rules[operation]),
            )
            state_chars = record_chars
            state_span = token_span(
                offsets, user_start + state_chars[0], user_start + state_chars[1]
            )
            input_tokens = sum(label == -100 for label in encoded["labels"])
            supervised = len(encoded["labels"]) - input_tokens
            if (
                rule_span[1] > input_tokens
                or state_span[1] > input_tokens
                or encoded["labels"][:input_tokens] != [-100] * input_tokens
                or encoded["labels"][input_tokens:]
                != encoded["input_ids"][input_tokens:]
            ):
                raise ValueError("rule/state evidence or assistant mask invalid")
            if min(state_span[0], rule_span[0]) < 0:
                raise ValueError("negative evidence position")
        except ValueError as error:
            reason = str(error)
        revision = config["generator_revision"]
        sample_id = _sha(f"{revision}|{seed}|{len(filler)}|{operation}".encode())[:24]
        if reason:
            rejected.append(
                {"sample_id": sample_id, "operation": operation, "reason": reason}
            )
            continue
        accepted.append(
            {
                "sample_id": sample_id,
                "semantic_task_id": _sha(f"{revision}|{seed}|{operation}".encode())[
                    :24
                ],
                "source_group": config["source_group"],
                "world_id": f"p87-rfc9114-sim-v6-{seed}",
                "split": config["split"],
                "operation": operation,
                "context_sha256": _sha(context.encode()),
                "id_only_projection_sha256": _sha(
                    _id_only_projection(context).encode()
                ),
                "answer_sha256": _answer_hash(messages[1]["content"]),
                "full_chat_tokens": len(encoded["input_ids"]),
                "input_tokens": input_tokens,
                "supervised_tokens": supervised,
                "rule_token_span": rule_span,
                "state_token_span": state_span,
                "evidence_extent_tokens": state_span[1] - rule_span[0],
                "rule_removal_answer": deleted_rule,
                "altered_rule_answer": altered,
                "state_removal_answer": deleted_state,
                "deleted_state_record_id": positive,
                "reader": {"sample_id": sample_id, "messages": messages},
                "train_ready": False,
            }
        )
    return accepted, rejected


def run(config_path: Path, output: Path, workers: int = 2) -> dict:
    if output.exists() or workers < 1:
        raise ValueError("output must be new and workers positive")
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "longworld.p87-hybrid-rfc-pilot-config.v1":
        raise ValueError("wrong P87 config")
    if config.get("generator_revision") != "p87-hybrid-v6":
        raise ValueError("P87 generator revision is not the shortcut-repaired v6")
    if config.get("split") != "train":
        raise ValueError("P87 pilot exports only its train-split source group")
    inventory = json.loads(_pin(config["inventory"], config["inventory_sha256"]))
    retrievals = {
        item["retrieval_file"]: item
        for item in inventory["fetch_receipt"]["retrievals"]
    }
    for name, key in (
        ("rfc9114.txt", "rules_sha256"),
        ("rfc9000.txt", "filler_sha256"),
    ):
        if (
            retrievals[name]["sha256"] != config[key]
            or retrievals[name]["status"] != 200
        ):
            raise ValueError("source digest disagrees with frozen fetch inventory")
    rules = _rules(_pin(config["rules_source"], config["rules_sha256"]))
    filler_source = _pin(config["filler_source"], config["filler_sha256"])
    if len(config["seeds"]) < 2 or len(set(config["seeds"])) != len(config["seeds"]):
        raise ValueError("at least two distinct paired worlds are required")
    id_only_contradictions = Counter()
    for count in config["filler_chars"]:
        filler = _filler(filler_source, count)
        paired = [_world(config, rules, filler, seed) for seed in config["seeds"]]
        id_orders = [
            tuple(row["id"] for row in specs[0]["records"]) for _, specs in paired
        ]
        if len(set(id_orders)) != 1:
            raise ValueError("paired worlds expose different ID-only inputs")
        for operation in OPERATIONS:
            answers = {
                tuple(_solve_visible(operation, context) or ()) for context, _ in paired
            }
            if len(answers) < 2:
                raise ValueError("ID-only and question-only answer shortcut remains")
            id_only_contradictions[operation] += 1
    jobs = [
        (config, rules, _filler(filler_source, count), seed)
        for count in config["filler_chars"]
        for seed in config["seeds"]
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_compile_one, jobs))
    accepted = [row for good, _ in results for row in good]
    rejected = [row for _, bad in results for row in bad]
    id_only_groups: dict[tuple[str, str], set[str]] = {}
    for row in accepted:
        key = (row["operation"], row["id_only_projection_sha256"])
        id_only_groups.setdefault(key, set()).add(row["answer_sha256"])
    if len(id_only_groups) != len(config["filler_chars"]) * len(OPERATIONS) or any(
        len(answers) < 2 for answers in id_only_groups.values()
    ):
        raise ValueError("frozen reader rows still admit an ID-only shortcut")
    if len({row["sample_id"] for row in accepted + rejected}) != len(
        accepted + rejected
    ):
        raise ValueError("duplicate sample identity")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p87-hybrid-", dir=output.parent) as raw:
        temp = Path(raw)
        with (
            (temp / "train.jsonl").open("x") as reader,
            (temp / "sample_index.jsonl").open("x") as index,
            (temp / "rejected.jsonl").open("x") as rejects,
        ):
            for row in accepted:
                reader.write(json.dumps(row.pop("reader"), ensure_ascii=False) + "\n")
                index.write(json.dumps(row, ensure_ascii=False) + "\n")
            for row in rejected:
                rejects.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest = {
            "schema_version": SCHEMA,
            "config_sha256": _sha(config_path.read_bytes()),
            "source_pins": {
                key: config[key]
                for key in ("inventory_sha256", "rules_sha256", "filler_sha256")
            },
            "generator_revision": config["generator_revision"],
            "id_only_contradiction_lengths": dict(id_only_contradictions),
            "id_only_conflicting_reader_groups": len(id_only_groups),
            "candidate_views": len(accepted),
            "independent_tasks": len({row["semantic_task_id"] for row in accepted}),
            "source_groups": 1 if accepted else 0,
            "simulated_worlds": len({row["world_id"] for row in accepted}),
            "operations": dict(Counter(row["operation"] for row in accepted)),
            "splits": {"train": len(accepted), "eval": 0},
            "full_token_range": [
                min((row["full_chat_tokens"] for row in accepted), default=0),
                max((row["full_chat_tokens"] for row in accepted), default=0),
            ],
            "evidence_extent_range": [
                min((row["evidence_extent_tokens"] for row in accepted), default=0),
                max((row["evidence_extent_tokens"] for row in accepted), default=0),
            ],
            "rejected": len(rejected),
            "rejection_reasons": dict(Counter(row["reason"] for row in rejected)),
            "files_sha256": {
                name: _sha((temp / name).read_bytes())
                for name in ("train.jsonl", "sample_index.jsonl", "rejected.jsonl")
            },
            "dependency_scope": "bounded_oracle_rule_and_single_positive_record_interventions",
            "mask_scope": "Qwen3.5 pinned chat template and diagnostic assistant-only labels",
            "train_ready": False,
        }
        (temp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.rename(temp, output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/p87_hybrid_rfc9114_pilot_v6.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output, args.workers), indent=2))


if __name__ == "__main__":
    main()
