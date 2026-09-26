"""Factorized controlled-world campaign over the audited P86 state compiler.

Each base world is built once. Reversible domain schemas vary visible JSON
artifacts and questions while preserving the executable semantics. This is
structured simulation, not four independent real-source ontologies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import p86_state_shared_world as state
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts import run_p86_state_shared_batch as base_batch
from scripts.run_shared_record_taskbank import _tokenizer
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p112-world-factor-campaign.v1"
QUESTION = "\n\nQUESTION\n"
FIELDS = ("entity", "category", "amount", "record_id")


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sub(text: str, mapping: dict[str, str]) -> str:
    return re.sub(
        r"\b(?:" + "|".join(re.escape(k) for k in mapping) + r")\b",
        lambda match: mapping[match.group()],
        text,
    )


def _maps(profile: dict) -> tuple[dict[str, str], dict[str, str]]:
    keys = {key: profile[key] for key in FIELDS}
    if len(set(keys.values())) != len(keys):
        raise ValueError("profile field names collide")
    return keys, {value: key for key, value in keys.items()}


def _base_header() -> dict:
    return {
        "schema": state.VERSION,
        "family": "shared_record_state",
        "record_rules": {name: state.records.PROTOCOLS[name] for name in state.RAW_OPS},
        "state_rules": state.STATE_RULES,
    }


def encode_context(base: str, profile: dict) -> str:
    keys, _ = _maps(profile)
    vocabulary = {**keys, "record": profile["record"], "revoke": profile["event"]}
    lines = base.splitlines()
    original_header = json.loads(lines[0])
    header = {
        "schema": SCHEMA,
        "domain": profile["domain"],
        "topic": profile["topic"],
        "field_meanings": keys,
        "record_type": profile["record"],
        "revocation_type": profile["event"],
        "record_rules": {
            name: _sub(rule, vocabulary)
            for name, rule in original_header["record_rules"].items()
        },
        "state_rules": _sub(original_header["state_rules"], vocabulary),
        "source_status": "SIMULATED structured event ledger; all rows below are synthetic",
    }
    visible = [dump(header)]
    for line in lines[1:]:
        item = json.loads(line)
        item = {keys.get(key, key): value for key, value in item.items()}
        item["type"] = (
            profile["record"] if item["type"] == "record" else profile["event"]
        )
        visible.append(dump(item))
    return "\n".join(visible)


def decode_context(visible: str, profile: dict) -> str:
    _, inverse = _maps(profile)
    lines = visible.splitlines()
    if not lines:
        raise ValueError("empty reader")
    header = json.loads(lines[0])
    if header != json.loads(
        encode_context(dump(_base_header()), profile).splitlines()[0]
    ):
        raise ValueError("visible domain rule header changed")
    recovered = [dump(_base_header())]
    ids = set()
    for line in lines[1:]:
        row = json.loads(line)
        if row.get("id") in ids:
            raise ValueError("duplicate visible fact id")
        ids.add(row.get("id"))
        row = {inverse.get(key, key): value for key, value in row.items()}
        row_type = row.get("type")
        if row_type == profile["record"]:
            row["type"] = "record"
            if set(row) != {
                "id",
                "type",
                "entity",
                "amount",
                "memo",
                "category",
                "date",
            }:
                raise ValueError("visible record schema changed")
        elif row_type == profile["event"]:
            row["type"] = "revoke"
            if set(row) != {"id", "type", "record_id", "entity", "effective", "reveal"}:
                raise ValueError("visible event schema changed")
        else:
            raise ValueError("unknown visible record or event type")
        recovered.append(dump(row))
    return "\n".join(recovered)


def _visible_answer(context: str, task: dict, profile: dict) -> dict:
    return state.solve_visible(decode_context(context, profile), task)


def _drop(context: str, fact_id: str, *, cascade: bool = False) -> str:
    lines = context.splitlines()
    rows = [json.loads(line) for line in lines[1:]]
    if sum(row.get("id") == fact_id for row in rows) != 1:
        raise ValueError("fact deletion target absent or ambiguous")
    kept = [
        row
        for row in rows
        if row.get("id") != fact_id and (not cascade or fact_id not in row.values())
    ]
    return "\n".join([lines[0], *(dump(row) for row in kept)])


def _interventions(context: str, task: dict, profile: dict) -> dict:
    original = _visible_answer(context, task, profile)
    consumed = task["consumed"]
    ids = [x for x in consumed if x.startswith("r")][:4] + [
        x for x in consumed if x.startswith("e")
    ][:4]
    changed = 0
    unresolved = 0
    for fact_id in ids:
        try:
            after = _visible_answer(
                _drop(context, fact_id, cascade=fact_id.startswith("r")), task, profile
            )
        except ValueError:
            unresolved += 1
        else:
            if after == original:
                raise ValueError("visible fact deletion did not alter result")
            changed += 1
    return {
        "probed_fact_ids": ids,
        "answer_changed": changed,
        "unresolved_after_deletion": unresolved,
    }


def _validate_config(config: dict) -> None:
    if config.get("schema") != SCHEMA or not 1 <= config.get("workers", 0) <= 8:
        raise ValueError("invalid P112 campaign schema or worker bound")
    base_batch.plan(config["base"])
    profiles = config.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles required")
    labels = set()
    for profile in profiles:
        if set(profile) != {"domain", "topic", "record", "event", *FIELDS}:
            raise ValueError("profile has missing or unsupported fields")
        if any(
            not isinstance(x, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", x)
            for x in profile.values()
        ):
            raise ValueError("profile must use simple nonempty identifiers")
        if profile["record"] == profile["event"] or len(
            {profile[key] for key in FIELDS}
        ) != len(FIELDS):
            raise ValueError("profile type or field aliases collide")
        if {profile[key] for key in FIELDS} & {
            "id",
            "type",
            "memo",
            "date",
            "effective",
            "reveal",
        }:
            raise ValueError("profile field alias conflicts with native row fields")
        if (profile["domain"], profile["topic"]) in labels:
            raise ValueError("duplicate profile")
        labels.add((profile["domain"], profile["topic"]))


def _base_config_path(output: Path) -> Path:
    return output / "base_config.json"


def compile(
    config_path: Path, output: Path, *, verify_only: bool = False, resume: bool = False
) -> dict:
    config = json.loads(config_path.read_text())
    _validate_config(config)
    if verify_only or resume:
        if (
            not output.exists()
            or json.loads(_base_config_path(output).read_text()) != config["base"]
        ):
            raise ValueError("frozen base config differs")
        if resume and not (output / "base" / "manifest.json").is_file():
            base_batch.run(
                _base_config_path(output),
                output / "base",
                workers=config["workers"],
                resume=True,
            )
        else:
            base_batch.verify(
                output / "base", config["base"], _tokenizer(), require_manifest=True
            )
        if resume and any(
            (output / name).exists()
            for name in (
                "candidate_train.jsonl",
                "candidate_eval.jsonl",
                "sample_index.jsonl",
                "manifest.json",
            )
        ):
            raise ValueError("resume expects base-only output")
    else:
        if output.exists():
            raise ValueError("output exists")
        output.mkdir(parents=True)
        _base_config_path(output).write_text(dump(config["base"]) + "\n")
        base_batch.run(
            _base_config_path(output), output / "base", workers=config["workers"]
        )
    tokenizer = _tokenizer()
    ledger = CandidateLedger()
    readers: dict[str, list[str]] = {"train": [], "eval": []}
    indexes: list[dict] = []
    proofs: list[dict] = []
    rejections: list[dict] = []
    bin_counts: Counter[str] = Counter()
    profiles = config["profiles"]
    for job in base_batch.plan(config["base"]):
        shard = output / "base" / "shards" / job["job_id"]
        if (shard / "reject.json").exists():
            rejections.append(json.loads((shard / "reject.json").read_text()))
            continue
        world = json.loads((shard / "world.json").read_text())
        if not state.validate_world(world)["passed"]:
            raise ValueError("P86 base world replay failed")
        for profile in profiles:
            context = encode_context(world["reader_context"], profile)
            if decode_context(context, profile) != world["reader_context"]:
                raise ValueError("domain schema is not reversible")
            profile_key = profile["domain"] + ":" + profile["topic"]
            group = (
                "p112:"
                + hashlib.sha256(
                    (world["world_id"] + "|" + profile_key).encode()
                ).hexdigest()[:24]
            )
            split = "eval" if world["seed"] % 5 == 0 else "train"
            positions = None
            for task in world["tasks"]:
                if _visible_answer(context, task, profile) != task["answer"]:
                    raise ValueError("visible domain reader replay differs")
                proof = _interventions(context, task, profile)
                question = _sub(
                    task["instruction"],
                    {
                        **{key: profile[key] for key in FIELDS},
                        "record": profile["record"],
                        "revoke": profile["event"],
                    },
                )
                messages = [
                    {"role": "user", "content": context + QUESTION + question},
                    {"role": "assistant", "content": dump(task["answer"])},
                ]
                encoded = tokenize_assistant_only(
                    tokenizer, messages, config["base"]["max_full_chat_tokens"]
                )
                ids, labels = encoded["input_ids"], encoded["labels"]
                supervised = sum(x != -100 for x in labels)
                input_tokens = len(ids) - supervised
                if (
                    not supervised
                    or labels != [-100] * input_tokens + ids[input_tokens:]
                ):
                    raise ValueError("assistant-only final mask mismatch")
                if positions is None:
                    positions = base_batch._fact_token_positions(
                        context, messages, tokenizer, ids
                    )
                required = [positions[fact_id] for fact_id in task["consumed"]]
                evidence_start = min(start for start, _ in required)
                evidence_end = max(end for _, end in required) + 1
                if evidence_end > input_tokens:
                    raise ValueError("reader evidence crosses assistant mask")
                sample_id = group + ":" + task["task_id"]
                reader = {"sample_id": sample_id, "messages": messages}
                native_index = {
                    "sample_id": sample_id,
                    "semantic_task_id": world["world_id"] + ":" + task["task_id"],
                    "source_group": group,
                    "source_kind": "controlled_simulation",
                    "domain": profile["domain"],
                    "topic": profile["topic"],
                    "operation": task["operation"],
                    "split": split,
                    "full_chat_tokens": len(ids),
                    "input_tokens": input_tokens,
                    "supervised_tokens": supervised,
                    "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                    "observed_lineage_token_envelope": {
                        "start": evidence_start,
                        "end": evidence_end,
                    },
                }
                binding = AdapterBinding(
                    source_kind="controlled_simulation",
                    source_group=group,
                    domain=profile["domain"],
                    topic=profile["topic"],
                    operation=task["operation"],
                    evidence_profile="reversible_domain_schema_with_visible_fact_interventions",
                    tokenizer_profile="pinned-chat-template",
                    receipt_path=shard / "receipt.json",
                    receipt_sha256=sha(shard / "receipt.json"),
                )
                candidate = normalize_native_candidate(
                    native_index, reader, binding, context_text=context
                )
                ledger.add(candidate)
                readers[split].append(dump(reader) + "\n")
                record = candidate.to_dict()
                record.update(
                    source_name="p112_world_factor_campaign",
                    native_row_ref=f"{shard / 'rows.jsonl'}:{task['task_id']}",
                    output_file=f"candidate_{split}.jsonl",
                    row_index=len(readers[split]) - 1,
                )
                indexes.append(record)
                proofs.append(
                    {
                        "sample_id": sample_id,
                        "world_id": world["world_id"],
                        "profile": profile_key,
                        "operation": task["operation"],
                        "base_receipt_sha256": sha(shard / "receipt.json"),
                        "visible_context_sha256": native_index["context_sha256"],
                        "bounded_lineage_token_span": evidence_end - evidence_start,
                        "last_consumed_fact_to_input_end_tokens": input_tokens
                        - evidence_end,
                        "interventions": proof,
                    }
                )
                bin_counts[candidate.length_bin] += 1
    payloads = {
        "candidate_train.jsonl": "".join(readers["train"]),
        "candidate_eval.jsonl": "".join(readers["eval"]),
        "sample_index.jsonl": "".join(dump(row) + "\n" for row in indexes),
        "proofs.jsonl": "".join(dump(row) + "\n" for row in proofs),
        "rejected.jsonl": "".join(dump(row) + "\n" for row in rejections),
    }
    for name, content in payloads.items():
        path = output / name
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"frozen campaign file differs: {name}")
        else:
            path.write_text(content)
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "campaign_schema": SCHEMA,
        "config_sha256": sha(config_path),
        "base_manifest_sha256": sha(output / "base" / "manifest.json"),
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "source_groups": len(ledger.group_splits),
        "base_worlds": len({p["world_id"] for p in proofs}),
        "domain_profiles": len(profiles),
        "views_by_lane": {"p112_world_factor_campaign": ledger.rows},
        "splits": {name: len(readers[name]) for name in ("train", "eval")},
        "length_bins": dict(sorted(bin_counts.items())),
        "operations": dict(sorted(Counter(p["operation"] for p in proofs).items())),
        "rejected_base_jobs": len(rejections),
        "rejection_reasons": dict(
            sorted(Counter(p["reason"] for p in rejections).items())
        ),
        "files_sha256": {
            name: sha(output / name)
            for name in (
                "candidate_train.jsonl",
                "candidate_eval.jsonl",
                "sample_index.jsonl",
            )
        },
        "proofs_sha256": sha(output / "proofs.jsonl"),
        "train_ready": False,
        "claim_limit": "controlled JSON ledger schema diversity only; unchanged four operation mechanisms; bounded visible-fact interventions and exact assistant mask; no natural-document transfer claim",
    }
    manifest_text = dump(manifest) + "\n"
    if verify_only:
        if (output / "manifest.json").read_text() != manifest_text:
            raise ValueError("frozen manifest replay differs")
        verify_merge(output)
    else:
        (output / "manifest.json").write_text(manifest_text)
        verify_merge(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.resume and args.verify_only:
        parser.error("--resume and --verify-only are mutually exclusive")
    print(
        dump(
            compile(
                args.config,
                args.output,
                verify_only=args.verify_only,
                resume=args.resume,
            )
        )
    )


if __name__ == "__main__":
    main()
