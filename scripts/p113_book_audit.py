"""Independent final-reader/source/answer/mask replay for P113 book tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_wiki_join_positions import token_span
from scripts.p112_book_freeze import _body
from scripts.p112_book_tasks import SEP, _dump, _reader_sections
from scripts.p113_book_tasks import QUESTION
from scripts.p113_book_truth import VERBS, alias_uncertain, audit_speeches, chapters
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p113-book-independent-final-reader-audit.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _blind(
    context: str, source: str, target: str, anchor: str
) -> tuple[str, str] | None:
    parsed = _reader_sections(context)
    if source not in parsed or target not in parsed:
        return None
    source_hits = [x for x in audit_speeches(parsed[source]) if x.quote == anchor]
    if len(source_hits) != 1:
        return None
    label = source_hits[0].label
    target_hits = [
        x
        for x in audit_speeches(parsed[target])
        if x.label.casefold() == label.casefold()
    ]
    return (label, target_hits[-1].quote) if target_hits else None


def audit(source_dir: Path, native_dir: Path, *, verify_only: bool = False) -> dict:
    source_manifest = json.loads((source_dir / "manifest.json").read_text())
    native = json.loads((native_dir / "manifest.json").read_text())
    if (
        source_manifest.get("schema") != "longworld.p113-book-source-freeze.v1"
        or source_manifest.get("source_truth_code_sha256")
        != _sha((ROOT / "scripts/p113_book_truth.py").read_bytes())
        or native.get("schema") != "longworld.p113-book-explicit-speech-reader.v1"
        or native["source_manifest_sha256"]
        != _sha((source_dir / "manifest.json").read_bytes())
    ):
        raise ValueError("book source/native receipt pin differs")
    for name, digest in native["file_sha256"].items():
        if _sha((native_dir / name).read_bytes()) != digest:
            raise ValueError(f"book native payload SHA differs: {name}")
    books = {x["source_group"]: x for x in source_manifest["records"]}
    sections_by_group = {}
    labels_by_group = {}
    full_text_by_group = {}
    for group, book in books.items():
        raw = (source_dir / book["raw_file"]).read_bytes()
        body = (source_dir / book["body_file"]).read_bytes()
        if (
            _sha(raw) != book["raw_sha256"]
            or _sha(body) != book["body_sha256"]
            or _body(raw, book["ebook_id"]).encode() != body
        ):
            raise ValueError(f"book frozen raw/body provenance differs: {group}")
        text = body.decode()
        parsed = chapters(text)
        sections_by_group[group] = {chapter.title: chapter.text for chapter in parsed}
        labels_by_group[group] = {
            x.label for chapter in parsed for x in audit_speeches(chapter.text)
        }
        full_text_by_group[group] = text
    indices = _rows(native_dir / "sample_index.jsonl")
    proofs = _rows(native_dir / "proofs.jsonl")
    readers = {
        split: iter(_rows(native_dir / f"{split}.jsonl")) for split in ("train", "eval")
    }
    if len(indices) != len(proofs) or len(indices) != native["candidate_views"]:
        raise ValueError("book reader/proof inventory differs")
    tokenizer = get_tokenizer()
    outcomes = []
    seen_task, seen_anchor, seen_answer = set(), set(), set()
    for row, proof in zip(indices, proofs, strict=True):
        split = row["split"]
        reader = next(readers[split], None)
        if (
            reader is None
            or reader["sample_id"] != row["sample_id"]
            or proof["sample_id"] != row["sample_id"]
            or [x["role"] for x in reader["messages"]] != ["user", "assistant"]
        ):
            raise ValueError("book reader/index/proof row alignment differs")
        group = row["source_group"]
        book = books[group]
        if (
            row["source_kind"] != "real_book"
            or split != book["split"]
            or row["topic"] != book["topic"]
        ):
            raise ValueError("book row source/split/topic differs")
        user = reader["messages"][0]["content"]
        if user.count(SEP) != 1:
            raise ValueError("book reader question boundary differs")
        context, question = user.split(SEP)
        parsed = _reader_sections(context)
        full_sections = sections_by_group[group]
        titles = list(full_sections)
        if any(
            title not in full_sections or body != full_sections[title]
            for title, body in parsed.items()
        ):
            raise ValueError("book reader section not in raw frozen source")
        positions = [titles.index(title) for title in parsed]
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ValueError("book reader section order is not contiguous")
        source_title, target_title = proof["source_section"], proof["target_section"]
        if (
            source_title not in parsed
            or target_title not in parsed
            or titles.index(source_title) >= titles.index(target_title)
        ):
            raise ValueError("book source/target sections differ")
        expected = QUESTION.format(
            source=source_title,
            target=target_title,
            anchor=proof["anchor"],
            verbs=", ".join(sorted(VERBS)),
        )
        if question != expected or reader["messages"][1]["content"] != proof["answer"]:
            raise ValueError("book question or answer differs from source contract")
        if (
            proof["answer"] in question
            or proof["speaker_label"] in question
            or full_text_by_group[group].count(proof["anchor"]) != 1
            or full_text_by_group[group].count(proof["answer"]) != 1
        ):
            raise ValueError("book question leaks gold or source repeats witness")
        if alias_uncertain(
            proof["speaker_label"], labels_by_group[group]
        ) or alias_uncertain(proof["counterfactual_label"], labels_by_group[group]):
            raise ValueError("speaker label may refer to an alias in full source")
        if _blind(context, source_title, target_title, proof["anchor"]) != (
            proof["speaker_label"],
            proof["answer"],
        ):
            raise ValueError("independent quote-first gold differs")
        source_hits = [
            x
            for x in audit_speeches(parsed[source_title])
            if x.quote == proof["anchor"]
        ]
        target_hits = [
            x
            for x in audit_speeches(parsed[target_title])
            if x.label.casefold() == proof["speaker_label"].casefold()
        ]
        if (
            len(source_hits) != 1
            or not target_hits
            or target_hits[-1].quote != proof["answer"]
        ):
            raise ValueError("full-section source/target attribution differs")
        source = source_hits[0]
        source_marker = f"=== SECTION: {source_title} ===\n"
        target_marker = f"=== SECTION: {target_title} ===\n"
        source_offset = context.index(source_marker) + len(source_marker)
        target_offset = context.index(target_marker) + len(target_marker)
        a, b = source_offset + source.label_start, source_offset + source.label_end
        if context[a:b] != proof["speaker_label"]:
            raise ValueError("source speaker character span differs")
        swapped = context[:a] + proof["counterfactual_label"] + context[b:]
        if _blind(swapped, source_title, target_title, proof["anchor"]) != (
            proof["counterfactual_label"],
            proof["counterfactual_answer"],
        ):
            raise ValueError("source label counterfactual answer differs")
        if (
            _blind(
                context[:a] + context[b:], source_title, target_title, proof["anchor"]
            )
            is not None
        ):
            raise ValueError("source label deletion leaves answer")
        no_target = context
        for item in sorted(target_hits, key=lambda x: x.label_start, reverse=True):
            x, y = target_offset + item.label_start, target_offset + item.label_end
            no_target = no_target[:x] + no_target[y:]
        if _blind(no_target, source_title, target_title, proof["anchor"]) is not None:
            raise ValueError("equivalent target label deletion leaves answer")
        encoded = tokenize_assistant_only(tokenizer, reader["messages"], 262144)
        ids, labels = encoded["input_ids"], encoded["labels"]
        full, supervised = len(ids), row["supervised_tokens"]
        if (
            full != row["full_chat_tokens"]
            or full - supervised != row["input_tokens"]
            or labels != [-100] * (full - supervised) + ids[full - supervised :]
        ):
            raise ValueError("final chat token count or assistant mask differs")
        prompt = _render_chat(tokenizer, reader["messages"][:1], generation_prompt=True)
        base = prompt.index(user)
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        spans = {
            "anchor_token_span": token_span(
                offsets,
                base + source_offset + source.quote_start,
                base + source_offset + source.quote_end,
            ),
            "speaker_token_span": token_span(offsets, base + a, base + b),
            "answer_token_span": token_span(
                offsets,
                base + target_offset + target_hits[-1].quote_start,
                base + target_offset + target_hits[-1].quote_end,
            ),
            "query_token_span": token_span(
                offsets, base + len(context) + len(SEP), base + len(user)
            ),
        }
        if any(list(span) != proof[name] for name, span in spans.items()):
            raise ValueError("final reader evidence offsets differ")
        extent = spans["answer_token_span"][1] - spans["anchor_token_span"][0]
        if (
            extent != proof["lineage_extent_tokens"]
            or extent < native["min_lineage_tokens"]
            or row["observed_lineage_token_envelope"]
            != {
                "start": spans["anchor_token_span"][0],
                "end": spans["answer_token_span"][1],
            }
        ):
            raise ValueError("book lineage extent differs")
        identity = (group, row["semantic_task_id"])
        anchor_id = (group, proof["anchor"])
        answer_id = (group, proof["answer"])
        if (
            identity in seen_task
            or anchor_id in seen_anchor
            or answer_id in seen_answer
        ):
            raise ValueError("book task/anchor/answer reused within source world")
        seen_task.add(identity)
        seen_anchor.add(anchor_id)
        seen_answer.add(answer_id)
        outcomes.append(
            {
                "sample_id": row["sample_id"],
                "source_group": group,
                "split": split,
                "full_chat_tokens": full,
                "supervised_tokens": supervised,
                "lineage_extent_tokens": extent,
                "status": "independent_quote_first_source_truth_and_mask_checked",
            }
        )
    if any(next(iterator, None) is not None for iterator in readers.values()):
        raise ValueError("book reader has trailing rows")
    rows_data = "".join(_dump(row) + "\n" for row in outcomes).encode()
    receipt = {
        "schema": SCHEMA,
        "source_manifest_sha256": _sha((source_dir / "manifest.json").read_bytes()),
        "native_manifest_sha256": _sha((native_dir / "manifest.json").read_bytes()),
        "checked_readers": len(outcomes),
        "splits": dict(sorted(Counter(row["split"] for row in outcomes).items())),
        "audit_rows_sha256": _sha(rows_data),
        "train_ready": False,
    }
    files = {
        "independent_audit.jsonl": rows_data,
        "independent_audit_manifest.json": (
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode(),
    }
    for name, content in files.items():
        path = native_dir / name
        if verify_only:
            if path.read_bytes() != content:
                raise ValueError(f"independent book audit replay differs: {name}")
        elif path.exists() and path.read_bytes() != content:
            raise ValueError(
                f"independent book audit exists with different bytes: {name}"
            )
        else:
            path.write_bytes(content)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            audit(args.source_dir, args.native_dir, verify_only=args.verify_only),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
