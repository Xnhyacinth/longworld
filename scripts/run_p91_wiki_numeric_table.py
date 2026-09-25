"""Compile a bounded real Wiki numeric table into candidate reader tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.p91_wiki_numeric_table import (
    interval_answer,
    intervention,
    parse_table,
)
from scripts.audit_wiki_join_positions import token_span
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p91-wiki-numeric-table.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _lines(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(_json(row) + "\n" for row in rows))


def _source(config: dict) -> dict:
    source = config["source_snapshot"]
    relative = Path(source["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source snapshot must be workspace-relative")
    path = ROOT / relative
    if _sha(path) != source["sha256"]:
        raise ValueError("source snapshot hash changed")
    snapshot = json.loads(path.read_text())
    if not isinstance(snapshot.get("snapshot_id"), str) or not snapshot["snapshot_id"]:
        raise ValueError("frozen snapshot identity is missing")
    if {doc["title"] for doc in snapshot["documents"]} != set(
        snapshot["source"]["revisions"]
    ):
        raise ValueError("frozen document/revision inventory changed")
    return snapshot


def _context(snapshot: dict, selected: dict) -> tuple[str, int]:
    docs = [selected, *(doc for doc in snapshot["documents"] if doc != selected)]
    pieces = []
    selected_start = -1
    for doc in docs:
        prefix = f"=== DOCUMENT: {doc['title']} (revision {snapshot['source']['revisions'][doc['title']]}) ===\n"
        if doc is selected:
            selected_start = sum(len(part) for part in pieces) + len(prefix)
        pieces.extend((prefix, doc["text"], "\n\n"))
    if selected_start < 0:
        raise ValueError("target document is absent")
    return "".join(pieces).rstrip(), selected_start


def _duplicate_support(
    text: str, table_start: int, table_end: int, rows: tuple, answer: dict
) -> None:
    selected = set(answer["entries"])
    outside = text[:table_start] + "\n" + text[table_end:]
    for row in rows:
        if row.name not in selected:
            continue
        # We certify only exact same-line name/height duplication, not all
        # possible natural-language paraphrases of the source table.
        occurrences = [line for line in outside.splitlines() if row.name in line]
        if any(str(int(row.meters)) in line for line in occurrences):
            raise ValueError(f"duplicate same-line name/height support: {row.name}")


def run(config_path: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or config.get("split") not in ("train", "eval"):
        raise ValueError("wrong numeric table config")
    if not all(
        isinstance(config.get(key), str) and config[key] for key in ("domain", "topic")
    ):
        raise ValueError("numeric table domain/topic metadata missing")
    snapshot = _source(config)
    shape = config["table_shape"]
    pages = []
    admitted = []
    for candidate in snapshot["documents"]:
        try:
            candidate_table = parse_table(candidate["text"], **shape)
            admitted.append((candidate, candidate_table))
            pages.append(
                {
                    "title": candidate["title"],
                    "status": "closed_numeric_table",
                    "rows": len(candidate_table.rows),
                }
            )
        except ValueError as exc:
            pages.append(
                {"title": candidate["title"], "status": "rejected", "reason": str(exc)}
            )
    if len(admitted) != 1:
        raise ValueError(f"expected one closed numeric table, found {len(admitted)}")
    doc, table = admitted[0]
    revision = snapshot["source"]["revisions"][doc["title"]]
    context, doc_start = _context(snapshot, doc)
    tokenizer = get_tokenizer()
    readers, indices, audits, rejections = [], [], [], []
    for low, high in config["intervals"]:
        try:
            answer = interval_answer(table, low, high)
            intervention_receipt = intervention(table, doc["text"], low, high, **shape)
            left = doc_start + intervention_receipt["value_start"]
            right = doc_start + intervention_receipt["value_end"]
            if context[left:right] != intervention_receipt["old_value"]:
                raise ValueError("final reader height span differs from source")
            hit_context = (
                context[:left] + intervention_receipt["hit_value"] + context[right:]
            )
            near_context = (
                context[:left]
                + intervention_receipt["near_miss_value"]
                + context[right:]
            )
            doc_length = len(doc["text"])
            hit_length = (
                doc_length - (right - left) + len(intervention_receipt["hit_value"])
            )
            near_length = (
                doc_length
                - (right - left)
                + len(intervention_receipt["near_miss_value"])
            )
            if (
                interval_answer(
                    parse_table(
                        hit_context[doc_start : doc_start + hit_length], **shape
                    ),
                    low,
                    high,
                )
                != intervention_receipt["hit_answer"]
            ):
                raise ValueError("final reader intervention replay differs")
            if (
                interval_answer(
                    parse_table(
                        near_context[doc_start : doc_start + near_length], **shape
                    ),
                    low,
                    high,
                )
                != answer
            ):
                raise ValueError("final reader near-miss replay differs")
            intervention_receipt["hit_reader_context_sha256"] = hashlib.sha256(
                hit_context.encode()
            ).hexdigest()
            intervention_receipt["near_reader_context_sha256"] = hashlib.sha256(
                near_context.encode()
            ).hexdigest()
            _duplicate_support(
                context,
                doc_start + table.header_start,
                doc_start + table.section_end,
                table.rows,
                answer,
            )
            question = (
                f"In the '{shape['heading'].removeprefix('## ')}' table of {doc['title']}, "
                f"which buildings have heights from {low} to {high} metres, "
                "inclusive? List every building and the total count. "
                "Sort the names alphabetically."
            )
            user = context + "\n\nQUESTION\n" + question
            messages = [
                {"role": "user", "content": user},
                {"role": "assistant", "content": _json(answer)},
            ]
            encoded = tokenize_assistant_only(
                tokenizer, messages, config["max_full_tokens"]
            )
            full = len(encoded["input_ids"])
            supervised = sum(label != -100 for label in encoded["labels"])
            if (
                supervised < 1
                or encoded["labels"][: full - supervised]
                != [-100] * (full - supervised)
                or encoded["labels"][full - supervised :]
                != encoded["input_ids"][full - supervised :]
            ):
                raise ValueError("assistant-only mask is invalid")
            prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
            if prompt.count(user) != 1:
                raise ValueError("user text is not unique in chat template")
            user_offset = prompt.index(user)
            offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
                "offset_mapping"
            ]
            row_evidence = []
            for row in table.rows:
                source_start = doc_start + row.value_start
                start, end = token_span(
                    offsets,
                    user_offset + source_start,
                    user_offset + doc_start + row.value_end,
                )
                row_evidence.append(
                    {
                        "name": row.name,
                        "meters": row.meters,
                        "plain_name": row.plain_name,
                        "source_row_start": row.row_start,
                        "source_row_end": row.row_end,
                        "reader_value_start": source_start,
                        "reader_value_end": doc_start + row.value_end,
                        "prompt_token_start": start,
                        "prompt_token_end": end,
                        "selected": low <= row.meters <= high,
                    }
                )
            if (
                len(row_evidence) != len(table.rows)
                or max(item["prompt_token_end"] for item in row_evidence)
                >= full - supervised
            ):
                raise ValueError("candidate rows are missing from prompt evidence")
            task_id = hashlib.sha256(
                f"{doc['doc_id']}|{low}|{high}".encode()
            ).hexdigest()[:20]
            sample_id = f"p91-wiki-height-{task_id}"
            readers.append(
                {
                    "example_id": sample_id,
                    "sample_id": sample_id,
                    "quality_status": "research_candidate",
                    "messages": messages,
                }
            )
            indices.append(
                {
                    "example_id": sample_id,
                    "sample_id": sample_id,
                    "task_id": task_id,
                    "source_group": snapshot["snapshot_id"],
                    "world_id": snapshot["snapshot_id"],
                    "split": config["split"],
                    "domain": config["domain"],
                    "topic": config["topic"],
                    "operation": "closed_numeric_table_interval",
                    "family": "table_scan",
                    "task_type": "closed_numeric_table_interval",
                    "dependency_status": "bounded_numeric_hit_and_near_miss_replay",
                    "question_style": "natural_table_scan",
                    "full_chat_tokens": full,
                    "input_tokens": full - supervised,
                    "supervised_tokens": supervised,
                    "candidate_rows": len(table.rows),
                    "selected_rows": answer["count"],
                    "evidence_token_extent": max(
                        item["prompt_token_end"] for item in row_evidence
                    )
                    - min(item["prompt_token_start"] for item in row_evidence),
                    "query_to_first_evidence_tokens": full
                    - supervised
                    - min(item["prompt_token_start"] for item in row_evidence),
                    "answer_sha256": hashlib.sha256(_json(answer).encode()).hexdigest(),
                }
            )
            audits.append(
                {
                    "example_id": sample_id,
                    "sample_id": sample_id,
                    "question": question,
                    "answer": answer,
                    "source_doc_id": doc["doc_id"],
                    "source_revision": revision,
                    "source_table_header": doc["text"][
                        table.header_start : table.header_end
                    ],
                    "candidate_rows": row_evidence,
                    "intervention": intervention_receipt,
                    "claim_limit": "final rendered table replay and one numeric boundary intervention; semantic paraphrase shortcuts unchecked",
                }
            )
        except ValueError as exc:
            rejections.append({"low": low, "high": high, "reason": str(exc)})
    output_dir.mkdir(parents=True)
    _lines(output_dir / f"{config['split']}.jsonl", readers)
    _lines(output_dir / "sample_index.jsonl", indices)
    _lines(output_dir / "audit.jsonl", audits)
    _lines(output_dir / "rejected.jsonl", rejections)
    _lines(output_dir / "page_audit.jsonl", pages)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "source_snapshot_sha256": config["source_snapshot"]["sha256"],
        "source_revision": revision,
        "candidate_views": len(readers),
        "independent_tasks": len({row["task_id"] for row in indices}),
        "rejected": len(rejections),
        "source_table_rows": len(table.rows),
        "plain_name_rows": sum(row.plain_name for row in table.rows),
        "source_documents": len(snapshot["documents"]),
        "page_rejections": sum(row["status"] == "rejected" for row in pages),
        "full_chat_tokens": [row["full_chat_tokens"] for row in indices],
        "evidence_token_extents": [row["evidence_token_extent"] for row in indices],
        "query_to_first_evidence_tokens": [
            row["query_to_first_evidence_tokens"] for row in indices
        ],
        "mask_audited": len(readers),
        "train_ready": False,
        "files_sha256": {
            name: _sha(output_dir / name)
            for name in (
                f"{config['split']}.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
                "rejected.jsonl",
                "page_audit.jsonl",
            )
        },
    }
    _write(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(_json(run(args.config, args.output_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
