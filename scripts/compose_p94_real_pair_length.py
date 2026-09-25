"""Compose bounded long reader views of frozen two-page Wiki year comparisons.

Only whole, independently frozen, same-split pages are inserted between the
two required source pages. These are extra views of existing semantic tasks,
not newly generated questions. The certificate is scoped to named table cells.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_table_tasks, wiki_world_bridge
from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    get_tokenizer,
)
from longworld.synthesis.unified_candidate_contract import _answer_hash
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.run_source_pool_batch import _snapshot
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p94-real-pair-length.v1"
SEPARATOR = "\n\nQUESTION\n"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict[str, str]) -> Path:
    path = Path(pin["path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("source pin must be workspace-relative")
    path = ROOT / path
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"source pin changed: {path}")
    return path


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _fold(value: str) -> str:
    return " ".join(
        "".join(
            c if c.isalnum() else " "
            for c in unicodedata.normalize("NFKC", value).casefold()
        ).split()
    )


def _length_bin(n: int) -> str:
    if n < 32768:
        return "lt32k"
    if n < 65536:
        return "32k"
    if n < 131072:
        return "64k"
    if n < 262144:
        return "128k"
    return "over256k"


def _filler_candidates(
    pool: dict, source: dict, source_snapshot: dict
) -> tuple[list[dict], Counter]:
    """Reject cross-split titles and repeated source pages before composition."""
    title_splits: dict[str, set[str]] = {}
    snapshots = {}
    for entry in pool["sources"]:
        snapshot = _snapshot(ROOT, entry["snapshot"])
        snapshots[entry["name"]] = snapshot
        for doc in snapshot["documents"]:
            title_splits.setdefault(doc["title"].casefold(), set()).add(entry["split"])
    source_titles = {doc["title"].casefold() for doc in source_snapshot["documents"]}
    source_ids = {doc["doc_id"] for doc in source_snapshot["documents"]}
    candidates, rejects = [], Counter()
    seen = set()
    for entry in pool["sources"]:
        if entry["name"] == source["name"] or entry["split"] != source["split"]:
            continue
        for doc in snapshots[entry["name"]]["documents"]:
            title = doc["title"].casefold()
            if title in source_titles or doc["doc_id"] in source_ids:
                rejects["source_page_overlap"] += 1
                continue
            if title_splits[title] != {source["split"]}:
                rejects["cross_split_title"] += 1
                continue
            if title in seen:
                rejects["repeated_filler_title"] += 1
                continue
            seen.add(title)
            candidates.append(
                {
                    "source": entry["name"],
                    "source_pin": entry["snapshot"],
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "text": doc["text"],
                    "text_sha256": hashlib.sha256(doc["text"].encode()).hexdigest(),
                    "topic": entry["topic"],
                }
            )
    candidates.sort(
        key=lambda item: (
            item["topic"] != source["topic"],
            -len(item["text"]),
            item["source"],
            item["title"],
        )
    )
    return candidates, rejects


def _compose(
    context: str, layouts: list[dict], fillers: list[dict]
) -> tuple[str, list[dict], list[dict]]:
    """Insert original pages before the third document; shift later offsets."""
    if len(layouts) < 3:
        raise ValueError("pair source needs at least three document layouts")
    third = layouts[2]
    header = f"[{third['doc_id']}] {third['title']}\n"
    point = third["text_start"] - len(header)
    if context[point : point + len(header)] != header:
        raise ValueError("native document layout does not match reader")
    inserted = "".join(
        f"[{doc['doc_id']}] {doc['title']}\n{doc['text']}\n\n" for doc in fillers
    )
    shifted = [
        {
            **layout,
            "text_start": layout["text_start"] + (len(inserted) if i >= 2 else 0),
            "text_end": layout["text_end"] + (len(inserted) if i >= 2 else 0),
        }
        for i, layout in enumerate(layouts)
    ]
    filler_layouts = []
    cursor = point
    for doc in fillers:
        header = f"[{doc['doc_id']}] {doc['title']}\n"
        filler_layouts.append(
            {
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "text_start": cursor + len(header),
                "text_end": cursor + len(header) + len(doc["text"]),
            }
        )
        cursor += len(header) + len(doc["text"]) + 2
    return context[:point] + inserted + context[point:], shifted, filler_layouts


def _reader_replay(
    context: str,
    layouts: list[dict],
    program: dict,
    answer: dict,
    surfaces: dict[str, tuple[str, ...]] | None = None,
) -> dict:
    """Parse only named source pages, then delete each named year cell in final bytes."""
    by_doc = {layout["doc_id"]: layout for layout in layouts}
    cells_by_side = []
    years = {}
    for side in program["sides"]:
        layout = by_doc[side["doc_id"]]
        text = context[layout["text_start"] : layout["text_end"]]
        names = surfaces[side["label"]] if surfaces else (side["label"],)
        cells = wiki_table_tasks._table_year_cells(text, names)
        if len(cells) != 1:
            raise ValueError("named source table does not have one year cell")
        cell = cells[0]
        years[side["label"]] = cell["year"]
        cells_by_side.append(
            (layout["text_start"] + cell["start"], layout["text_start"] + cell["end"])
        )
    parsed = {"years": years, "earlier": min(years, key=years.__getitem__)}
    if parsed != answer or len(set(years.values())) != 2:
        raise ValueError("final reader parser disagrees with gold")
    for start, end in cells_by_side:
        masked = context[:start] + "?" * (end - start) + context[end:]
        side = next(
            side
            for side, span in zip(program["sides"], cells_by_side)
            if span == (start, end)
        )
        layout = by_doc[side["doc_id"]]
        if wiki_table_tasks._table_year_cells(
            masked[layout["text_start"] : layout["text_end"]],
            surfaces[side["label"]] if surfaces else (side["label"],),
        ):
            raise ValueError("masked source still has parser-visible year")
    return {
        "status": "two_named_source_year_cells_independently_removed",
        "cell_spans": cells_by_side,
        "scope": "named source table parser; unrestricted prose equivalents unchecked",
    }


def _positions(
    tokenizer: Any,
    messages: list[dict],
    context_chars: int,
    spans: list[tuple[int, int]],
) -> dict:
    user = messages[0]["content"]
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    user_start = prompt.find(user)
    if user_start < 0 or prompt.count(user) != 1:
        raise ValueError("reader user content is not unique in chat template")
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    evidence = [
        token_span(offsets, user_start + start, user_start + end)
        for start, end in spans
    ]
    query = token_span(
        offsets, user_start + context_chars + len(SEPARATOR), user_start + len(user)
    )[0]
    first = min(start for start, _ in evidence)
    last = max(end for _, end in evidence)
    return {
        "evidence_token_spans": evidence,
        "evidence_extent_tokens": last - first,
        "query_token_start": query,
        "last_evidence_to_query_tokens": query - last,
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def run(config_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("output directory already exists")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong length-composition config")
    bins = config["target_bins"]
    for name, low, high in bins:
        if name != _length_bin(low) or _length_bin(high - 1) != name or low < 32768:
            raise ValueError("invalid target length bin")
    pool = json.loads(_pin(config["source_pool"]).read_text())
    source = next(s for s in pool["sources"] if s["name"] == config["source_name"])
    if source["split"] != "eval":
        raise ValueError("this pilot uses only frozen eval pair tasks")
    snapshot = _snapshot(ROOT, source["snapshot"])
    world = wiki_world_bridge.snapshot_to_world(snapshot)
    fillers, pre_rejects = _filler_candidates(pool, source, snapshot)
    native_path = _pin(config["native_manifest"])
    native_mask_path = _pin(config["native_mask_audit"])
    native_dir = native_path.parent
    native_manifest = json.loads(native_path.read_text())
    native_mask = json.loads(native_mask_path.read_text())
    if (
        native_mask_path.parent != native_dir
        or native_mask["source_manifest_sha256"] != _sha(native_path)
        or native_mask["checked_rows"] != native_manifest["candidate_views"]
    ):
        raise ValueError("native pair mask receipt differs from source")
    for name, digest in native_manifest["files_sha256"].items():
        if _sha(native_dir / name) != digest:
            raise ValueError(f"native pair file changed: {name}")
    index_rows = _rows(native_dir / "sample_index.jsonl")
    audit_rows = {row["example_id"]: row for row in _rows(native_dir / "audit.jsonl")}
    readers = {row["example_id"]: row for row in _rows(native_dir / "eval.jsonl")}
    if set(audit_rows) != set(readers) or {r["example_id"] for r in index_rows} != set(
        readers
    ):
        raise ValueError("native pair inventories differ")
    tokenizer = get_tokenizer()
    result_rows, result_index, result_audit, rejected = [], [], [], Counter(pre_rejects)
    for native in index_rows:
        native_id = native["example_id"]
        audit, original = audit_rows[native_id], readers[native_id]
        original_user = original["messages"][0]["content"]
        context = original_user[: native["context_chars"]]
        question = original_user[native["context_chars"] :]
        answer = json.loads(original["messages"][1]["content"])
        if (
            question != SEPARATOR + audit["original_question"]
            or answer != audit["oracle_replay"]["value_blind_reader_parser_answer"]
            or native["source_group"] != snapshot["snapshot_id"]
            or native["split"] != source["split"]
            or hashlib.sha256(context.encode()).hexdigest() != native["context_sha256"]
        ):
            raise ValueError("native reader, source and answer disagree")
        source_docs = {doc["doc_id"]: doc for doc in snapshot["documents"]}
        layouts = audit["document_layouts"]
        for layout in layouts:
            doc = source_docs[layout["doc_id"]]
            if (
                doc["title"] != layout["title"]
                or context[layout["text_start"] : layout["text_end"]] != doc["text"]
            ):
                raise ValueError("native source document differs from frozen snapshot")
        surfaces = {}
        for side in audit["program"]["sides"]:
            fact = world.facts_by_id[side["fact_id"]]
            entity = world.objects[fact.subject]
            if entity.label != side["label"]:
                raise ValueError("native side label differs from frozen world")
            surfaces[side["label"]] = (entity.label, *entity.aliases)
        labels = [
            _fold(name)
            for names in surfaces.values()
            for name in names
            if len(name) >= 5
        ]
        safe_fillers = []
        for filler in fillers:
            folded = " " + _fold(filler["title"] + " " + filler["text"]) + " "
            if any(" " + label + " " in folded for label in labels):
                rejected["filler_repeats_named_subject"] += 1
            else:
                safe_fillers.append(filler)
        chosen = []
        for name, low, high in bins:
            candidate_index = 0
            while True:
                composed, shifted, filler_layouts = _compose(context, layouts, chosen)
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
                # The authoritative assistant-mask tokenizer checks accepted views below.
                if low <= token_count < high:
                    break
                if token_count >= high:
                    rejected[f"{name}_overshoot"] += 1
                    chosen = []
                    break
                if candidate_index >= len(safe_fillers):
                    rejected[f"{name}_insufficient_real_material"] += 1
                    chosen = []
                    break
                candidate = safe_fillers[candidate_index]
                candidate_index += 1
                if candidate["title"] in {item["title"] for item in chosen}:
                    continue
                if len(candidate["text"]) < config["minimum_filler_chars"]:
                    rejected["short_filler_page"] += 1
                    continue
                probe, _, _ = _compose(context, layouts, [*chosen, candidate])
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
            replay = _reader_replay(
                composed, shifted, audit["program"], answer, surfaces
            )
            encoded = tokenize_assistant_only(tokenizer, messages, high)
            full_tokens = len(encoded["labels"])
            if not low <= full_tokens < high:
                raise ValueError("exact assistant tokenizer length bin differs")
            positions = _positions(
                tokenizer, messages, len(composed), replay["cell_spans"]
            )
            input_tokens = sum(label == -100 for label in encoded["labels"])
            if max(end for _, end in positions["evidence_token_spans"]) > input_tokens:
                raise ValueError("evidence crosses assistant mask")
            filler_ids = [f["source"] + ":" + f["doc_id"] for f in chosen]
            sample_id = (
                "p94-real-pair-length-"
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
                "split": source["split"],
                "domain": source["domain"],
                "topic": source["topic"],
                "operation": native["task_type"],
                "length_bin": name,
                "context_chars": len(composed),
                "context_sha256": hashlib.sha256(composed.encode()).hexdigest(),
                "input_tokens": input_tokens,
                "supervised_tokens": full_tokens - input_tokens,
                "full_chat_tokens": full_tokens,
                "tokenizer_profile": "pinned-chat-template",
                "answer_sha256": _answer_hash(messages[1]["content"]),
                "evidence_extent_tokens": positions["evidence_extent_tokens"],
                "filler_document_count": len(chosen),
                "train_ready": False,
                "output_file": "eval.jsonl",
                "row_index": len(result_rows),
            }
            mask = audit_reader(
                {"sample_id": sample_id, "messages": messages},
                {**idx, "sample_id": sample_id},
                tokenizer,
                high,
            )
            result_rows.append(row)
            result_index.append(idx)
            result_audit.append(
                {
                    "example_id": sample_id,
                    "native_example_id": native_id,
                    "semantic_task_id": native["task_id"],
                    "filler_document_layouts": filler_layouts,
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
                    "source_snapshot_sha256": source["snapshot"]["sha256"],
                    "reader_intervention": replay,
                    **positions,
                    "mask_reader_sha256": mask["reader_sha256"],
                    "scope": "named source table parser and two independent cell deletions; unrestricted prose equivalents unchecked",
                }
            )
    if not result_rows:
        raise ValueError("no real pair length view passed")
    output.mkdir(parents=True)
    for name, rows in (
        ("train.jsonl", []),
        ("eval.jsonl", result_rows),
        ("sample_index.jsonl", result_index),
        ("audit.jsonl", result_audit),
    ):
        _write(output / name, rows)
    manifest = {
        "schema": SCHEMA + ".export.v1",
        "config_sha256": _sha(config_path),
        "composer_sha256": _sha(Path(__file__)),
        "native_manifest_sha256": _sha(native_path),
        "native_mask_audit_sha256": _sha(native_mask_path),
        "source_pool_sha256": _sha(_pin(config["source_pool"])),
        "views": len(result_rows),
        "new_independent_semantic_tasks": 0,
        "semantic_tasks_reused": len({r["semantic_task_id"] for r in result_index}),
        "views_by_length": dict(
            sorted(Counter(r["length_bin"] for r in result_index).items())
        ),
        "rejected_by_reason": dict(sorted(rejected.items())),
        "full_chat_tokens": {
            "min": min(r["full_chat_tokens"] for r in result_index),
            "max": max(r["full_chat_tokens"] for r in result_index),
        },
        "evidence_extent_tokens": {
            "min": min(r["evidence_extent_tokens"] for r in result_index),
            "max": max(r["evidence_extent_tokens"] for r in result_index),
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
        raise ValueError("wrong real pair length export schema")
    for name, digest in stored["files_sha256"].items():
        if _sha(output / name) != digest:
            raise ValueError(f"composed reader file changed: {name}")
    with tempfile.TemporaryDirectory(prefix="p94-real-pair-verify-") as raw:
        regenerated = run(config_path, Path(raw) / "batch")
        if regenerated != stored:
            raise ValueError("composed manifest differs from source replay")
        for name in stored["files_sha256"]:
            if _sha(output / name) != _sha(Path(raw) / "batch" / name):
                raise ValueError(f"composed reader differs: {name}")
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
