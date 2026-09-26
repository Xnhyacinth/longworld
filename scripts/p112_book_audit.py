"""Independent final-reader/source/mask replay for P112 book candidates."""

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

from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_wiki_join_positions import token_span
from scripts.p112_book_tasks import SEP, _reader_sections, sections, solve, speeches
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p112-book-final-reader-audit.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def audit(source_dir: Path, native_dir: Path, *, verify_only: bool = False) -> dict:
    source_manifest = json.loads((source_dir / "manifest.json").read_text())
    native = json.loads((native_dir / "manifest.json").read_text())
    if (
        source_manifest.get("schema") != "longworld.p112-book-freeze.v1"
        or native.get("schema") != "longworld.p112-book-cross-section-reader.v5"
        or native["source_manifest_sha256"] != _sha(source_dir / "manifest.json")
    ):
        raise ValueError("book source/native pin differs")
    for name, digest in native["file_sha256"].items():
        if _sha(native_dir / name) != digest:
            raise ValueError(f"book native file SHA differs: {name}")
    source_books = {book["source_group"]: book for book in source_manifest["records"]}
    source_sections = {}
    source_texts = {}
    for group, book in source_books.items():
        path = source_dir / book["body_file"]
        if _sha(path) != book["body_sha256"]:
            raise ValueError("frozen book body differs")
        source_texts[group] = path.read_text()
        source_sections[group] = {
            section.title: section.text for section in sections(source_texts[group])
        }
    index = _rows(native_dir / "sample_index.jsonl")
    proofs = _rows(native_dir / "audit.jsonl")
    readers = {
        split: iter(_rows(native_dir / f"{split}.jsonl")) for split in ("train", "eval")
    }
    if len(index) != len(proofs) or len(index) != native["candidate_views"]:
        raise ValueError("book native inventory differs")
    tokenizer = get_tokenizer()
    outcomes = []
    task_ids, answers_by_group, anchors_by_group = set(), set(), set()
    for row, proof in zip(index, proofs, strict=True):
        reader = next(readers[row["split"]], None)
        if (
            reader is None
            or set(reader) != {"sample_id", "messages"}
            or reader["sample_id"] != row["sample_id"]
            or row["sample_id"] != proof["sample_id"]
            or set(reader["messages"][0]) != {"role", "content"}
            or set(reader["messages"][1]) != {"role", "content"}
            or [message["role"] for message in reader["messages"]]
            != ["user", "assistant"]
        ):
            raise ValueError("book reader/index alignment or audit leakage differs")
        book = source_books[row["source_group"]]
        if book["split"] != row["split"] or row["source_kind"] != "real_book":
            raise ValueError("book source split/kind differs")
        user = reader["messages"][0]["content"]
        if user.count(SEP) != 1:
            raise ValueError("book final user/context boundary differs")
        context, question = user.split(SEP)
        parsed = _reader_sections(context)
        frozen = source_sections[row["source_group"]]
        ordered_titles = list(frozen)
        if any(
            title not in frozen or frozen[title] != body
            for title, body in parsed.items()
        ):
            raise ValueError("reader section differs from frozen full source section")
        indices = [ordered_titles.index(title) for title in parsed]
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ValueError("reader uses noncontiguous or reordered sections")
        source_title, target_title = proof["source_section"], proof["target_section"]
        if (
            source_title not in parsed
            or target_title not in parsed
            or list(parsed).index(source_title) >= list(parsed).index(target_title)
        ):
            raise ValueError("source/target chapter ordering differs")
        expected_question = (
            f"In {source_title}, who says “{proof['anchor']}”? "
            f"What is the last quotation explicitly attributed to that same speaker "
            f"in {target_title}? Return only the quoted words."
        )
        if (
            question != expected_question
            or reader["messages"][1]["content"] != proof["answer"]
        ):
            raise ValueError("question/answer changed from task contract")
        if (
            re.search(
                r"(?<!\w)" + re.escape(proof["source_attribution"]) + r"(?!\w)",
                question,
                re.IGNORECASE,
            )
            or proof["answer"] in question
            or proof["counterfactual_answer"] in question
            or any(
                source_texts[row["source_group"]].count(quote) != 1
                for quote in (
                    proof["anchor"],
                    proof["answer"],
                    proof["counterfactual_answer"],
                )
            )
        ):
            raise ValueError(
                "question leaks speaker/answer or full book repeats support"
            )
        source_matches = [
            s for s in speeches(parsed[source_title]) if s.quote == proof["anchor"]
        ]
        if (
            len(source_matches) != 1
            or source_matches[0].speaker != proof["source_attribution"]
            or context.count(proof["anchor"]) != 1
            or context.count(proof["answer"]) != 1
        ):
            raise ValueError("literal source or answer support is ambiguous")
        target_speech = speeches(parsed[target_title])
        if len({s.speaker for s in target_speech}) < 2:
            raise ValueError("target lacks a competing explicit speaker")
        if (
            solve(context, source_title, target_title, proof["anchor"])
            != proof["answer"]
        ):
            raise ValueError("independent reader solver answer differs")
        # Replay the exact source attribution edit, not an arbitrary earlier name.
        marker = f"=== SECTION: {source_title} ===\n"
        section_start = context.index(marker) + len(marker)
        a = section_start + source_matches[0].speaker_start
        b = section_start + source_matches[0].speaker_end
        if context[a:b] != proof["source_attribution"]:
            raise ValueError("source attribution offset differs")
        swapped = context[:a] + proof["counterfactual_speaker"] + context[b:]
        if (
            solve(swapped, source_title, target_title, proof["anchor"])
            != proof["counterfactual_answer"]
        ):
            raise ValueError("source speaker swap does not change answer")
        if (
            solve(
                context[:a] + context[b:], source_title, target_title, proof["anchor"]
            )
            is not None
        ):
            raise ValueError("source speaker deletion leaves an answer")
        target_marker = f"=== SECTION: {target_title} ===\n"
        target_start = context.index(target_marker) + len(target_marker)
        no_target = context
        for item in sorted(
            (s for s in target_speech if s.speaker == proof["source_attribution"]),
            key=lambda x: x.statement_start,
            reverse=True,
        ):
            x, y = (
                target_start + item.statement_start,
                target_start + item.statement_end,
            )
            no_target = no_target[:x] + no_target[y:]
        if solve(no_target, source_title, target_title, proof["anchor"]) is not None:
            raise ValueError("equivalent target support deletion leaves answer")
        encoded = tokenize_assistant_only(tokenizer, reader["messages"], 262144)
        ids, labels = encoded["input_ids"], encoded["labels"]
        full = len(ids)
        supervised = row["supervised_tokens"]
        if (
            full != row["full_chat_tokens"]
            or full - supervised != row["input_tokens"]
            or labels != [-100] * (full - supervised) + ids[full - supervised :]
        ):
            raise ValueError("exact final chat count or assistant mask differs")
        prompt = _render_chat(tokenizer, reader["messages"][:1], generation_prompt=True)
        user_start = prompt.index(user)
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        anchor = source_matches[0]
        answer_matches = [
            s for s in target_speech if s.speaker == proof["source_attribution"]
        ]
        answer = answer_matches[-1]
        spans = {
            "source_quote_token_span": token_span(
                offsets,
                user_start + section_start + anchor.quote_start,
                user_start + section_start + anchor.quote_end,
            ),
            "source_speaker_token_span": token_span(
                offsets, user_start + a, user_start + b
            ),
            "target_quote_token_span": token_span(
                offsets,
                user_start + target_start + answer.quote_start,
                user_start + target_start + answer.quote_end,
            ),
            "query_token_span": token_span(
                offsets, user_start + len(context) + len(SEP), user_start + len(user)
            ),
        }
        if any(list(value) != proof[name] for name, value in spans.items()):
            raise ValueError("final reader evidence token offsets differ")
        extent = (
            spans["target_quote_token_span"][1] - spans["source_quote_token_span"][0]
        )
        if (
            extent != proof["lineage_extent_tokens"]
            or extent < native["min_lineage_tokens"]
            or row["observed_lineage_token_envelope"]
            != {
                "start": spans["source_quote_token_span"][0],
                "end": spans["target_quote_token_span"][1],
            }
        ):
            raise ValueError("final reader long-distance certificate differs")
        identity = (row["source_group"], row["semantic_task_id"])
        answer_identity = (row["source_group"], proof["answer"])
        anchor_identity = (row["source_group"], proof["anchor"])
        if (
            identity in task_ids
            or answer_identity in answers_by_group
            or anchor_identity in anchors_by_group
        ):
            raise ValueError(
                "book task, source anchor, or answer duplicates within a source world"
            )
        task_ids.add(identity)
        answers_by_group.add(answer_identity)
        anchors_by_group.add(anchor_identity)
        outcomes.append(
            {
                "sample_id": row["sample_id"],
                "source_group": row["source_group"],
                "split": row["split"],
                "full_chat_tokens": full,
                "supervised_tokens": supervised,
                "lineage_extent_tokens": extent,
                "last_evidence_to_query_tokens": spans["query_token_span"][0]
                - spans["target_quote_token_span"][1],
                "status": "source_text_answer_interventions_and_mask_checked",
            }
        )
    if any(next(reader, None) is not None for reader in readers.values()):
        raise ValueError("book native readers have trailing rows")
    content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in outcomes
    ).encode()
    receipt = {
        "schema": SCHEMA,
        "source_manifest_sha256": _sha(source_dir / "manifest.json"),
        "native_manifest_sha256": _sha(native_dir / "manifest.json"),
        "checked_readers": len(outcomes),
        "source_books": len(source_books),
        "splits": dict(Counter(row["split"] for row in outcomes)),
        "audit_index_sha256": hashlib.sha256(content).hexdigest(),
        "train_ready": False,
    }
    files = {
        "mask_audit_index.jsonl": content,
        "mask_audit.json": (
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode(),
    }
    for name, data in files.items():
        path = native_dir / name
        if verify_only:
            if not path.exists() or path.read_bytes() != data:
                raise ValueError(f"book independent audit replay differs: {name}")
        elif path.exists() and path.read_bytes() != data:
            raise ValueError(
                f"book independent audit exists with different bytes: {name}"
            )
        else:
            path.write_bytes(data)
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
