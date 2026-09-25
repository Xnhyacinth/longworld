"""Blindly replay P109 final RFC readers, interventions and assistant masks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.p109_prose_compile import QUESTION_MARKER, RECORD, SCHEMA, SOURCE_MARKER
from scripts.p109_prose_support import _pin, _sha
from scripts.train_sft import _render_chat

AUDIT_SCHEMA = "longworld.p109-prose-final-reader-audit.v1"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _blind(context: str, field: str) -> tuple[int, list[dict], dict] | None:
    if context.count(SOURCE_MARKER) != 1:
        raise ValueError("P109 blind source/simulation boundary ambiguous")
    source, simulation = context.split(SOURCE_MARKER)
    pattern = re.compile(
        re.escape(field)
        + r"(?:\s+parameter)?\s+MUST\s+be\s+at\s+least\s+([0-9]{1,5})(?![0-9^])",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(source))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("P109 blind selected rule ambiguous")
    limit = int(matches[0].group(1))
    records = []
    for line in simulation.splitlines():
        match = RECORD.fullmatch(line)
        if not match or match.group(2) != field:
            raise ValueError("P109 blind record row is malformed")
        records.append({"id": match.group(1), "value": int(match.group(3))})
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("P109 blind record IDs repeat")
    ids = sorted(row["id"] for row in records if row["value"] < limit)
    return limit, records, {"count": len(ids), "ids": ids}


def _edit_record(
    context: str, field: str, record_id: str, value: int
) -> tuple[str, dict]:
    old = re.compile(
        rf"Observation {re.escape(record_id)}: {re.escape(field)}=([0-9]+)\."
    )
    changed, count = old.subn(f"Observation {record_id}: {field}={value}.", context)
    if count != 1:
        raise ValueError("P109 blind intervention target record not unique")
    result = _blind(changed, field)
    if result is None:
        raise ValueError("P109 blind edited reader lost rule")
    return hashlib.sha256(changed.encode()).hexdigest(), result[2]


def audit(native_dir: Path, config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    manifest_path = native_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest["schema"] != SCHEMA + ".result"
        or manifest["config_sha256"] != _sha(config_path)
        or manifest["support_ledger_sha256"] != config["support_ledger"]["sha256"]
    ):
        raise ValueError("P109 native source/config chain differs")
    for name, digest in manifest["files_sha256"].items():
        if _sha(native_dir / name) != digest:
            raise ValueError(f"P109 native file differs: {name}")
    support = json.loads(_pin(config["support_ledger"]).read_text())
    by_rfc = {row["rfc_id"]: row for row in support["sources"]}
    readers = _rows(native_dir / "train.jsonl") + _rows(native_dir / "eval.jsonl")
    indices = _rows(native_dir / "sample_index.jsonl")
    proofs = _rows(native_dir / "audit.jsonl")
    if (
        len(readers) != len(indices) != len(proofs) != manifest["candidate_views"]
        or len({row["sample_id"] for row in readers}) != len(readers)
        or _rows(native_dir / "eval.jsonl")
    ):
        raise ValueError("P109 native reader inventory or split differs")
    tokenizer = get_tokenizer()
    checked = []
    paired: dict[str, list[tuple[tuple[str, ...], str]]] = defaultdict(list)
    for reader, index, proof in zip(readers, indices, proofs, strict=True):
        sample_id = reader["sample_id"]
        if (
            index["sample_id"] != sample_id
            or proof["sample_id"] != sample_id
            or index["split"] != "train"
            or index["source_kind"] != "grounded_simulation"
            or index["operation"] != "real_rule_numeric_minimum_violation_set"
        ):
            raise ValueError("P109 reader/index/proof identity differs")
        source = proof["source"]
        if source != by_rfc[source["rfc_id"]] or index["topic"] != source["rfc_id"]:
            raise ValueError("P109 official RFC source identity differs")
        original = _pin(
            {"path": source["source_path"], "sha256": source["source_sha256"]}
        ).read_text()
        messages = reader["messages"]
        if [message["role"] for message in messages] != ["user", "assistant"]:
            raise ValueError("P109 reader role shape differs")
        user = messages[0]["content"]
        suffix = QUESTION_MARKER + proof["question"]
        if (
            not user.endswith(suffix)
            or proof["field"] not in proof["question"]
            or "explicit" not in proof["question"]
            or "below the minimum" not in proof["question"]
            or str(proof["limit"]) in proof["question"]
        ):
            raise ValueError(
                "P109 question boundary, selector or threshold leak differs"
            )
        context = user[: -len(suffix)]
        left, right = proof["official_source_span"]
        if (
            context[left:right] != original
            or context.count(original) != 1
            or index["context_sha256"] != hashlib.sha256(context.encode()).hexdigest()
            or source["source_url"] not in context[:left]
        ):
            raise ValueError("P109 final reader official source differs")
        blinded = _blind(context, proof["field"])
        if (
            blinded is None
            or blinded[0] != proof["limit"]
            or blinded[2] != proof["answer"]
            or json.loads(messages[1]["content"]) != blinded[2]
            or len(blinded[1]) != config["records_per_state"]
        ):
            raise ValueError("P109 blind final reader answer differs")
        rule_left, rule_right = proof["rule_char_span"]
        if context[rule_left:rule_right] != proof["rule_text"]:
            raise ValueError("P109 official rule span differs")
        for paragraph_left, paragraph_right in proof["support_group_spans"]:
            if not left <= paragraph_left < paragraph_right <= right:
                raise ValueError("P109 rule support paragraph outside official source")
        changed = context
        for paragraph_left, paragraph_right in sorted(
            proof["support_group_spans"], reverse=True
        ):
            changed = changed[:paragraph_left] + changed[paragraph_right:]
        if _blind(changed, proof["field"]) is not None:
            raise ValueError("P109 rule-group deletion leaves answer")
        stem = proof["field"].split("_")[0]
        if any(
            stem.casefold() in paragraph.casefold()
            and f"at least {proof['limit']}" in " ".join(paragraph.split()).casefold()
            and "must" in paragraph.casefold()
            for paragraph in re.split(r"\n\s*\n", changed.split(SOURCE_MARKER)[0])
        ):
            raise ValueError("P109 equivalent lower-bound support survives")
        hit_id = proof["changed_record_id"]
        hit = _edit_record(context, proof["field"], hit_id, proof["limit"] - 1)
        control = _edit_record(context, proof["field"], hit_id, proof["limit"] + 2)
        if (
            hit != (proof["record_hit_reader_sha256"], proof["record_hit_answer"])
            or hit[1] == blinded[2]
            or control
            != (proof["record_control_reader_sha256"], proof["record_control_answer"])
            or control[1] != blinded[2]
        ):
            raise ValueError("P109 record hit/control intervention differs")
        chat = _render_chat(tokenizer, messages, generation_prompt=False)
        if chat.count(user) != 1:
            raise ValueError("P109 final user occurrence ambiguous")
        offsets = tokenizer(chat, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        user_start = chat.index(user)
        rule_tokens = token_span(
            offsets, user_start + rule_left, user_start + rule_right
        )
        if list(rule_tokens) != index["rule_token_span"]:
            raise ValueError("P109 rule token span differs")
        for record in proof["record_spans"]:
            record_left, record_right = record["char_span"]
            visible = context[record_left:record_right]
            if (
                f"Observation {record['id']}: {proof['field']}={record['value']}."
                not in visible
                or list(
                    token_span(
                        offsets, user_start + record_left, user_start + record_right
                    )
                )
                != record["token_span"]
            ):
                raise ValueError("P109 record value or final token span differs")
        if (
            index["evidence_token_extent"]
            != proof["record_spans"][-1]["token_span"][1] - rule_tokens[0]
        ):
            raise ValueError("P109 evidence-token extent differs")
        mask = audit_reader(reader, index, tokenizer, config["max_full_tokens"])
        query_start = token_span(
            offsets,
            user_start + len(context) + len(QUESTION_MARKER),
            user_start + len(user),
        )[0]
        checked.append(
            {
                "sample_id": sample_id,
                "rfc_id": source["rfc_id"],
                "source_group": index["source_group"],
                "full_chat_tokens": mask["full_chat_tokens"],
                "supervised_tokens": mask["supervised_tokens"],
                "evidence_token_extent": index["evidence_token_extent"],
                "last_record_to_question_tokens": query_start
                - proof["record_spans"][-1]["token_span"][1],
                "status": "blind_source_rule_state_mask_replayed",
            }
        )
        paired[index["source_group"]].append(
            (tuple(row["id"] for row in blinded[1]), messages[1]["content"])
        )
    if any(
        len(pair) != 2 or pair[0][0] != pair[1][0] or pair[0][1] == pair[1][1]
        for pair in paired.values()
    ):
        raise ValueError("P109 paired same-ID worlds lack answer contrast")
    return {
        "schema": AUDIT_SCHEMA,
        "native_manifest_sha256": _sha(manifest_path),
        "checked_readers": len(checked),
        "source_groups": dict(
            sorted(Counter(row["source_group"] for row in checked).items())
        ),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in checked),
        "supervised_tokens": sum(row["supervised_tokens"] for row in checked),
        "rows": checked,
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = audit(args.native_dir, args.config)
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    output = args.native_dir / "mask_audit.json"
    if args.verify_only:
        if not output.is_file() or output.read_text() != content:
            raise ValueError("P109 blind final audit replay differs")
    else:
        if output.exists():
            raise ValueError("P109 blind final audit already exists")
        output.write_text(content)
    print(
        json.dumps(
            {
                "checked_readers": result["checked_readers"],
                "source_groups": result["source_groups"],
                "full_chat_tokens": result["full_chat_tokens"],
                "supervised_tokens": result["supervised_tokens"],
            }
        )
    )


if __name__ == "__main__":
    main()
