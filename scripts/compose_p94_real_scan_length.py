"""Compose long train reader views of a title-scoped, closed Wiki year table.

The target table remains byte-identical. Complete same-split pages follow the
source context, moving its eligible year rows farther from the final query.
Every accepted view replays the complete table and hit/near-miss interventions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_table_scan
from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    get_tokenizer,
)
from longworld.synthesis.unified_candidate_contract import _answer_hash
from scripts.audit_unified_reader_mask import audit_reader
from scripts.compose_p94_real_pair_length import (
    SEPARATOR,
    _filler_candidates,
    _length_bin,
    _pin,
    _positions,
    _rows,
    _sha,
    _write,
)
from scripts.run_source_pool_batch import _snapshot
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p94-real-scan-length.v1"


def _append(context: str, fillers: list[dict]) -> tuple[str, list[dict]]:
    layouts, cursor = [], len(context)
    parts = [context]
    for doc in fillers:
        header = f"\n\n[{doc['doc_id']}] {doc['title']}\n"
        parts.append(header + doc["text"])
        layouts.append(
            {
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "text_start": cursor + len(header),
                "text_end": cursor + len(header) + len(doc["text"]),
            }
        )
        cursor += len(header) + len(doc["text"])
    return "".join(parts), layouts


def _scan_replay(
    context: str,
    target_layout: dict,
    program: dict,
    answer: dict,
    *,
    expected_candidates: int,
) -> dict:
    """Read all eligible named-table rows in final bytes and perturb membership."""
    start, end = target_layout["text_start"], target_layout["text_end"]
    doc_text = context[start:end]
    parsed = wiki_table_scan.parse_closed_table(doc_text, target_layout["title"])
    if (
        program["op"] != "closed_table_interval"
        or program["doc_id"] != target_layout["doc_id"]
        or parsed.year_column != program["year_column"]
        or len(parsed.eligible) != expected_candidates
        or len(parsed.eligible) != len(program["candidate_fact_ids"])
    ):
        raise ValueError("final closed-table candidate universe differs")
    low, high = program["low"], program["high"]
    entries = sorted(row.subject for row in parsed.eligible if low <= row.year <= high)
    result = {"count": len(entries), "entries": entries}
    if result != answer or not 0 < result["count"] < len(parsed.eligible):
        raise ValueError("final table scan answer differs from native oracle")
    existing = {row.subject for row in parsed.eligible}
    hit_name, miss_name = "P94 insertion hit", "P94 insertion near miss"
    if hit_name in existing or miss_name in existing:
        raise ValueError("intervention label collides with source row")
    hit_year = (low + high) // 2
    miss_year = low - 1 if low > 1000 else high + 1
    if not (1000 <= hit_year <= 2099 and 1000 <= miss_year <= 2099):
        raise ValueError("intervention years outside clean table range")
    checks = []
    for name, year in ((hit_name, hit_year), (miss_name, miss_year)):
        changed_doc = wiki_table_scan._insert_row(doc_text, parsed, name, year)
        changed = wiki_table_scan.parse_closed_table(
            changed_doc, target_layout["title"]
        )
        if len(changed.eligible) != len(parsed.eligible) + 1:
            raise ValueError("inserted row was not scanned")
        changed_answer = {
            "entries": sorted(
                row.subject for row in changed.eligible if low <= row.year <= high
            )
        }
        changed_answer["count"] = len(changed_answer["entries"])
        changed_context = context[:start] + changed_doc + context[end:]
        checks.append(
            (changed_answer, hashlib.sha256(changed_context.encode()).hexdigest())
        )
    hit, hit_hash = checks[0]
    miss, miss_hash = checks[1]
    if (
        hit
        != {
            "count": answer["count"] + 1,
            "entries": sorted([*answer["entries"], hit_name]),
        }
        or miss != answer
    ):
        raise ValueError("hit/near-miss insertion did not preserve scan contract")
    return {
        "status": "scoped_named_table_hit_and_near_miss_replayed",
        "eligible_row_count": len(parsed.eligible),
        "selected_row_count": result["count"],
        "format_excluded_row_count": len(parsed.format_excluded),
        "year_cell_spans": [
            (start + row.year_start, start + row.year_end) for row in parsed.eligible
        ],
        "hit_answer": hit,
        "near_miss_answer": miss,
        "hit_context_sha256": hit_hash,
        "near_miss_context_sha256": miss_hash,
        "scope": "named, closed, plain-year table only; unrestricted prose equivalents unchecked",
    }


def run(config_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("output directory already exists")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong scan length config")
    for name, low, high in config["target_bins"]:
        if name != _length_bin(low) or name != _length_bin(high - 1) or low < 32768:
            raise ValueError("invalid target length bin")
    pool_path = _pin(config["source_pool"])
    pool = json.loads(pool_path.read_text())
    source = next(s for s in pool["sources"] if s["name"] == config["source_name"])
    if source["split"] != "train":
        raise ValueError("this scan lane requires a frozen train source")
    snapshot = _snapshot(ROOT, source["snapshot"])
    fillers, pre_rejects = _filler_candidates(pool, source, snapshot)
    native_path = _pin(config["native_manifest"])
    native_dir = native_path.parent
    native_manifest = json.loads(native_path.read_text())
    for name, digest in native_manifest["files_sha256"].items():
        if _sha(native_dir / name) != digest:
            raise ValueError(f"native scan file changed: {name}")
    readers = {row["example_id"]: row for row in _rows(native_dir / "train.jsonl")}
    audits = {row["example_id"]: row for row in _rows(native_dir / "audit.jsonl")}
    selected = [
        row
        for row in _rows(native_dir / "sample_index.jsonl")
        if row["source_group"] == snapshot["snapshot_id"]
        and row["task_type"] == "dense_table_interval_scan"
    ]
    if len(selected) != config["expected_native_scan_tasks"]:
        raise ValueError("native scan task count differs from pinned plan")
    if any(
        row["example_id"] not in readers or row["example_id"] not in audits
        for row in selected
    ):
        raise ValueError("selected native scan reader/audit missing")
    source_docs = {doc["doc_id"]: doc for doc in snapshot["documents"]}
    tokenizer = get_tokenizer()
    rows, index, proof, rejected = [], [], [], Counter(pre_rejects)
    for native in selected:
        native_id = native["example_id"]
        audit, original = audits[native_id], readers[native_id]
        user = original["messages"][0]["content"]
        context, question = (
            user[: native["context_chars"]],
            user[native["context_chars"] :],
        )
        answer = json.loads(original["messages"][1]["content"])
        if (
            native["split"] != "train"
            or question != SEPARATOR + audit["original_question"]
            or answer != audit["value_blind_reader_parser_answer"]
            or audit["insertion_intervention"]["status"]
            != "scoped_table_insertion_replay"
            or hashlib.sha256(context.encode()).hexdigest() != native["context_sha256"]
        ):
            raise ValueError("native scan source, answer or intervention differs")
        audit_reader(
            {"sample_id": native_id, "messages": original["messages"]},
            {
                **native,
                "sample_id": native_id,
                "operation": native["task_type"],
                "answer_sha256": _answer_hash(original["messages"][1]["content"]),
                "tokenizer_profile": "pinned-chat-template",
            },
            tokenizer,
            262144,
        )
        layouts = audit["document_layouts"]
        for layout in layouts:
            doc = source_docs[layout["doc_id"]]
            if (
                doc["title"] != layout["title"]
                or context[layout["text_start"] : layout["text_end"]] != doc["text"]
            ):
                raise ValueError("native source page differs from frozen snapshot")
        target = next(
            layout
            for layout in layouts
            if layout["doc_id"] == audit["program"]["doc_id"]
        )
        if target["title"].casefold() not in question.casefold():
            raise ValueError("question is not restricted to named source title")
        chosen = []
        for name, low, high in config["target_bins"]:
            candidate_index = 0
            while True:
                composed, filler_layouts = _append(context, chosen)
                messages = [
                    {"role": "user", "content": composed + question},
                    original["messages"][1],
                ]
                token_count = len(
                    tokenizer(
                        _render_chat(tokenizer, messages, generation_prompt=False),
                        truncation=False,
                    )["input_ids"]
                )
                if low <= token_count < high:
                    break
                if token_count >= high:
                    rejected[f"{name}_overshoot"] += 1
                    chosen = []
                    break
                if candidate_index >= len(fillers):
                    rejected[f"{name}_insufficient_real_material"] += 1
                    chosen = []
                    break
                candidate = fillers[candidate_index]
                candidate_index += 1
                if candidate["title"] in {item["title"] for item in chosen}:
                    continue
                if len(candidate["text"]) < config["minimum_filler_chars"]:
                    rejected["short_filler_page"] += 1
                    continue
                probe, _ = _append(context, [*chosen, candidate])
                probe_messages = [
                    {"role": "user", "content": probe + question},
                    original["messages"][1],
                ]
                probe_count = len(
                    tokenizer(
                        _render_chat(
                            tokenizer, probe_messages, generation_prompt=False
                        ),
                        truncation=False,
                    )["input_ids"]
                )
                if probe_count >= high:
                    rejected[f"{name}_filler_overflow"] += 1
                    continue
                chosen.append(candidate)
            if not chosen:
                continue
            replay = _scan_replay(
                composed,
                target,
                audit["program"],
                answer,
                expected_candidates=len(audit["candidate_universe"]["eligible_rows"]),
            )
            encoded = tokenize_assistant_only(tokenizer, messages, high)
            full = len(encoded["labels"])
            if not low <= full < high:
                raise ValueError("exact assistant tokenizer length bin differs")
            positions = _positions(
                tokenizer, messages, len(composed), replay["year_cell_spans"]
            )
            input_tokens = sum(label == -100 for label in encoded["labels"])
            if max(end for _, end in positions["evidence_token_spans"]) > input_tokens:
                raise ValueError("scan evidence crosses assistant mask")
            filler_ids = [f["source"] + ":" + f["doc_id"] for f in chosen]
            sample_id = (
                "p94-real-scan-length-"
                + hashlib.sha256(
                    (native_id + "|" + name + "|" + "|".join(filler_ids)).encode()
                ).hexdigest()[:20]
            )
            row = {"example_id": sample_id, "messages": messages}
            idx = {
                "example_id": sample_id,
                "native_example_id": native_id,
                "semantic_task_id": native["task_id"],
                "source_group": native["source_group"],
                "source_kind": "real_wiki",
                "split": "train",
                "domain": native["domain"],
                "topic": native["topic"],
                "operation": native["task_type"],
                "length_bin": name,
                "context_chars": len(composed),
                "context_sha256": hashlib.sha256(composed.encode()).hexdigest(),
                "input_tokens": input_tokens,
                "supervised_tokens": full - input_tokens,
                "full_chat_tokens": full,
                "tokenizer_profile": "pinned-chat-template",
                "answer_sha256": _answer_hash(messages[1]["content"]),
                "evidence_extent_tokens": positions["evidence_extent_tokens"],
                "last_evidence_to_query_tokens": positions[
                    "last_evidence_to_query_tokens"
                ],
                "filler_document_count": len(chosen),
                "train_ready": False,
                "output_file": "train.jsonl",
                "row_index": len(rows),
            }
            checked = audit_reader(
                {"sample_id": sample_id, "messages": messages},
                {**idx, "sample_id": sample_id},
                tokenizer,
                high,
            )
            rows.append(row)
            index.append(idx)
            proof.append(
                {
                    "example_id": sample_id,
                    "native_example_id": native_id,
                    "semantic_task_id": native["task_id"],
                    "source_snapshot_sha256": source["snapshot"]["sha256"],
                    "filler_documents": [
                        {
                            key: f[key]
                            for key in (
                                "source",
                                "source_pin",
                                "doc_id",
                                "title",
                                "text_sha256",
                            )
                        }
                        for f in chosen
                    ],
                    "filler_document_layouts": filler_layouts,
                    "reader_intervention": replay,
                    **positions,
                    "mask_reader_sha256": checked["reader_sha256"],
                    "scope": "complete named-table scan plus hit/near-miss in final reader; other documents excluded by question title",
                }
            )
    if not rows:
        raise ValueError("no real scan length view passed")
    output.mkdir(parents=True)
    for name, content in (
        ("train.jsonl", rows),
        ("eval.jsonl", []),
        ("sample_index.jsonl", index),
        ("audit.jsonl", proof),
    ):
        _write(output / name, content)
    manifest = {
        "schema": SCHEMA + ".export.v1",
        "config_sha256": _sha(config_path),
        "composer_sha256": _sha(Path(__file__)),
        "pair_helpers_sha256": _sha(ROOT / "scripts/compose_p94_real_pair_length.py"),
        "native_manifest_sha256": _sha(native_path),
        "source_pool_sha256": _sha(pool_path),
        "views": len(rows),
        "new_independent_semantic_tasks": 0,
        "semantic_tasks_reused": len({r["semantic_task_id"] for r in index}),
        "views_by_length": dict(
            sorted(Counter(r["length_bin"] for r in index).items())
        ),
        "rejected_by_reason": dict(sorted(rejected.items())),
        "full_chat_tokens": {
            "min": min(r["full_chat_tokens"] for r in index),
            "max": max(r["full_chat_tokens"] for r in index),
        },
        "last_evidence_to_query_tokens": {
            "min": min(r["last_evidence_to_query_tokens"] for r in index),
            "max": max(r["last_evidence_to_query_tokens"] for r in index),
        },
        "tokenizer": {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION},
        "files_sha256": {
            name: _sha(output / name)
            for name in (
                "train.jsonl",
                "eval.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
            )
        },
        "train_ready": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return manifest


def verify(config_path: Path, output: Path) -> dict:
    stored = json.loads((output / "manifest.json").read_text())
    if stored.get("schema") != SCHEMA + ".export.v1":
        raise ValueError("wrong real scan export schema")
    for name, digest in stored["files_sha256"].items():
        if _sha(output / name) != digest:
            raise ValueError(f"composed scan reader file changed: {name}")
    with tempfile.TemporaryDirectory(prefix="p94-real-scan-verify-") as raw:
        regenerated = run(config_path, Path(raw) / "batch")
        if regenerated != stored:
            raise ValueError("scan manifest differs from source replay")
        for name in stored["files_sha256"]:
            if _sha(output / name) != _sha(Path(raw) / "batch" / name):
                raise ValueError(f"composed scan reader differs: {name}")
    return stored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            verify(args.config, args.output)
            if args.verify_only
            else run(args.config, args.output)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
