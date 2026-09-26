"""Compile source-backed, cross-section quoted-speaker tasks from frozen books.

The rule is deliberately narrow: both speaker attributions must be literal in
the reader text. It does not infer character identity, intent, or causality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_wiki_join_positions import token_span
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p112-book-cross-section-reader.v5"
HEADER = re.compile(
    r"(?m)^CHAPTER[ \t]+([IVXLCDM]+)\.[ \t]*$|^([IVXLCDM]+)\.[ \t]+([A-Z][A-Z ’'\-]+)[ \t]*$"
)
SPEECH = re.compile(
    r"“(?P<quote>[^”\n]{25,130}[,.!?])”[ \t\n]*said[ \t]+"
    r"(?P<speaker>(?:the[ \t]+)?[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)?)(?=[,.;:!? \t\n])"
)
MARK = re.compile(r"(?m)^=== SECTION: (?P<title>[^\n]+) ===\n")
GENERIC = {"Mr", "Mrs", "Miss", "Lady", "Lord", "Sir", "Inspector"}
SEP = "\n\nQUESTION\n"


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class Section:
    title: str
    text: str
    original_start: int


@dataclass(frozen=True)
class Speech:
    quote: str
    speaker: str
    quote_start: int
    quote_end: int
    speaker_start: int
    speaker_end: int
    statement_start: int
    statement_end: int


def sections(text: str) -> list[Section]:
    headings = list(HEADER.finditer(text))
    if len(headings) < 8:
        raise ValueError("fewer than eight complete section headings")
    result = []
    seen = set()
    for i, match in enumerate(headings):
        number = match.group(1) or match.group(2)
        title = f"Chapter {number}" if match.group(1) else match.group(0)
        if title in seen:
            raise ValueError("repeated section title")
        seen.add(title)
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[match.start() : end].rstrip()
        if len(body) < 1000:
            raise ValueError("section body is too short or heading parser drifted")
        result.append(Section(title, body, match.start()))
    return result


def speeches(text: str) -> list[Speech]:
    result = []
    for match in SPEECH.finditer(text):
        speaker = re.sub(r"[ \t]+", " ", match.group("speaker"))
        if speaker in GENERIC:
            continue
        result.append(
            Speech(
                match.group("quote"),
                speaker,
                match.start("quote"),
                match.end("quote"),
                match.start("speaker"),
                match.end("speaker"),
                match.start(),
                match.end(),
            )
        )
    return result


def _reader_sections(context: str) -> dict[str, str]:
    found = list(MARK.finditer(context))
    if not found or found[0].start() != 0:
        raise ValueError("reader section boundaries differ")
    out = {}
    for i, marker in enumerate(found):
        title = marker.group("title")
        if title in out:
            raise ValueError("reader repeats section title")
        out[title] = context[
            marker.end() : found[i + 1].start() if i + 1 < len(found) else len(context)
        ].rstrip()
    return out


def solve(
    context: str, source_title: str, target_title: str, anchor: str
) -> str | None:
    parsed = _reader_sections(context)
    if source_title not in parsed or target_title not in parsed:
        return None
    anchors = [item for item in speeches(parsed[source_title]) if item.quote == anchor]
    if len(anchors) != 1:
        return None
    targets = [
        item
        for item in speeches(parsed[target_title])
        if item.speaker == anchors[0].speaker
    ]
    return targets[-1].quote if targets else None


def _render(
    chapters: list[Section], first: int, last: int
) -> tuple[str, dict[int, int]]:
    parts = []
    offsets = {}
    cursor = 0
    for i in range(first, last + 1):
        prefix = f"=== SECTION: {chapters[i].title} ===\n"
        if parts:
            parts.append("\n\n")
            cursor += 2
        parts.extend((prefix, chapters[i].text))
        offsets[i] = cursor + len(prefix)
        cursor += len(prefix) + len(chapters[i].text)
    return "".join(parts), offsets


def _proposals(
    chapters: list[Section], *, max_tasks: int, full_text: str | None = None
) -> tuple[list[dict], Counter]:
    speech = [speeches(section.text) for section in chapters]
    full_text = (
        full_text
        if full_text is not None
        else "\n".join(section.text for section in chapters)
    )
    all_quotes = Counter(item.quote for group in speech for item in group)
    literal_counts = {quote: full_text.count(quote) for quote in all_quotes}
    proposed = []
    rejects = Counter()
    for i, source in enumerate(speech):
        for j in range(i + 1, len(speech)):
            target = speech[j]
            by_speaker: dict[str, list[Speech]] = defaultdict(list)
            for item in target:
                by_speaker[item.speaker].append(item)
            if len(by_speaker) < 2:
                rejects["target_lacks_competing_explicit_speakers"] += 1
                continue
            for speaker in sorted(
                {item.speaker for item in source} & by_speaker.keys()
            ):
                anchors = [
                    item
                    for item in source
                    if item.speaker == speaker
                    and all_quotes[item.quote] == 1
                    and literal_counts[item.quote] == 1
                    and not re.search(
                        r"(?<!\w)" + re.escape(speaker) + r"(?!\w)",
                        item.quote,
                        re.IGNORECASE,
                    )
                ]
                if not anchors:
                    rejects["source_has_no_unique_explicit_anchor"] += 1
                    continue
                answer = by_speaker[speaker][-1]
                alternatives = [
                    items[-1]
                    for name, items in sorted(by_speaker.items())
                    if name != speaker
                    and all_quotes[items[-1].quote] == 1
                    and literal_counts[items[-1].quote] == 1
                    and items[-1].quote != answer.quote
                ]
                if (
                    all_quotes[answer.quote] != 1
                    or literal_counts[answer.quote] != 1
                    or not alternatives
                ):
                    rejects["target_answer_or_control_not_unique"] += 1
                    continue
                alternative = min(
                    alternatives,
                    key=lambda item: (_sha(item.quote.encode()), item.quote),
                )
                for anchor in sorted(
                    anchors,
                    key=lambda item: (_sha(item.quote.encode()), item.quote),
                )[:8]:
                    proposed.append(
                        {
                            "source": i,
                            "target": j,
                            "anchor": anchor,
                            "answer": answer,
                            "alternate": alternative,
                        }
                    )
    # Take independent pairs across section gaps, rather than exhausting one character.
    proposed.sort(
        key=lambda p: (
            -min(p["target"] - p["source"], 20),
            _sha(
                (
                    str(p["source"]) + ":" + str(p["target"]) + ":" + p["anchor"].quote
                ).encode()
            ),
        )
    )
    used_pairs = set()
    used_answers = set()
    used_anchors = set()
    selected = []
    for p in proposed:
        key = (p["source"], p["target"])
        answer_key = p["answer"].quote
        if answer_key in used_answers:
            rejects["duplicate_target_answer_across_tasks"] += 1
            continue
        if p["anchor"].quote in used_anchors:
            rejects["duplicate_source_anchor_across_tasks"] += 1
            continue
        if key in used_pairs:
            rejects["duplicate_section_pair"] += 1
            continue
        used_pairs.add(key)
        used_answers.add(answer_key)
        used_anchors.add(p["anchor"].quote)
        selected.append(p)
        if len(selected) == max_tasks:
            break
    rejects["unexamined_after_task_cap"] += max(
        0,
        len(proposed)
        - sum(
            rejects[k]
            for k in (
                "duplicate_target_answer_across_tasks",
                "duplicate_source_anchor_across_tasks",
                "duplicate_section_pair",
            )
        )
        - len(selected),
    )
    return selected, rejects


def _layout(
    chapters: list[Section], source: int, target: int, desired: int, tokenizer: object
) -> tuple[str, dict[int, int], int]:
    lengths = [
        len(tokenizer(section.text, add_special_tokens=False)["input_ids"])
        for section in chapters
    ]
    lo, hi = source, target
    estimate = sum(lengths[lo : hi + 1])
    while estimate < desired and (lo > 0 or hi + 1 < len(chapters)):
        if lo > 0 and (hi + 1 == len(chapters) or source - lo <= hi - target):
            lo -= 1
            estimate += lengths[lo]
        else:
            hi += 1
            estimate += lengths[hi]
    context, offsets = _render(chapters, lo, hi)
    return context, offsets, estimate


def _compile_one(
    book: dict, source_dir: Path, max_tasks: int, min_lineage_tokens: int
) -> dict:
    body_path = source_dir / book["body_file"]
    raw = body_path.read_bytes()
    if _sha(raw) != book["body_sha256"]:
        raise ValueError("book body changed after source freeze")
    full_text = raw.decode()
    chapter_list = sections(full_text)
    tokenizer = get_tokenizer()
    proposals, rejects = _proposals(
        chapter_list, max_tasks=max_tasks, full_text=full_text
    )
    readers = []
    indexes = []
    audits = []
    for rank, p in enumerate(proposals):
        i, j = p["source"], p["target"]
        anchor, answer, alternate = p["anchor"], p["answer"], p["alternate"]
        desired = (32768, 65536, 131072)[rank % 3]
        context, offsets, _ = _layout(chapter_list, i, j, desired + 250, tokenizer)
        question = (
            f"In {chapter_list[i].title}, who says “{anchor.quote}”? "
            f"What is the last quotation explicitly attributed to that same speaker "
            f"in {chapter_list[j].title}? Return only the quoted words."
        )
        if (
            re.search(
                r"(?<!\w)" + re.escape(anchor.speaker) + r"(?!\w)",
                question,
                re.IGNORECASE,
            )
            or answer.quote in question
            or alternate.quote in question
        ):
            rejects["question_leaks_speaker_or_target_quote"] += 1
            continue
        if context.count(anchor.quote) != 1 or context.count(answer.quote) != 1:
            rejects["reader_repeated_anchor_or_answer"] += 1
            continue
        if (
            solve(context, chapter_list[i].title, chapter_list[j].title, anchor.quote)
            != answer.quote
        ):
            rejects["reader_answer_replay_failed"] += 1
            continue
        source_speaker_start = offsets[i] + anchor.speaker_start
        source_speaker_end = offsets[i] + anchor.speaker_end
        swapped = (
            context[:source_speaker_start]
            + alternate.speaker
            + context[source_speaker_end:]
        )
        if (
            solve(swapped, chapter_list[i].title, chapter_list[j].title, anchor.quote)
            != alternate.quote
        ):
            rejects["source_attribution_swap_no_answer_change"] += 1
            continue
        no_source = context[:source_speaker_start] + context[source_speaker_end:]
        if (
            solve(no_source, chapter_list[i].title, chapter_list[j].title, anchor.quote)
            is not None
        ):
            rejects["source_attribution_deletion_leaves_answer"] += 1
            continue
        # Remove all equivalent target-speaker speech statements; one quote alone
        # is not necessarily the sole route to the speaker's last utterance.
        target_speech = [
            item
            for item in speeches(chapter_list[j].text)
            if item.speaker == anchor.speaker
        ]
        no_target = context
        for item in sorted(
            target_speech, key=lambda item: item.statement_start, reverse=True
        ):
            a, b = offsets[j] + item.statement_start, offsets[j] + item.statement_end
            no_target = no_target[:a] + no_target[b:]
        if (
            solve(no_target, chapter_list[i].title, chapter_list[j].title, anchor.quote)
            is not None
        ):
            rejects["equivalent_target_support_deletion_leaves_answer"] += 1
            continue
        messages = [
            {"role": "user", "content": context + SEP + question},
            {"role": "assistant", "content": answer.quote},
        ]
        encoded = tokenize_assistant_only(tokenizer, messages, 262144)
        full = len(encoded["input_ids"])
        supervised = sum(label != -100 for label in encoded["labels"])
        if (
            full >= 262144
            or encoded["labels"]
            != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
        ):
            rejects["length_or_assistant_mask_invalid"] += 1
            continue
        prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
        user_start = prompt.index(messages[0]["content"])
        token_offsets = tokenizer(
            prompt, truncation=False, return_offsets_mapping=True
        )["offset_mapping"]
        anchor_span = token_span(
            token_offsets,
            user_start + offsets[i] + anchor.quote_start,
            user_start + offsets[i] + anchor.quote_end,
        )
        attribution_span = token_span(
            token_offsets,
            user_start + source_speaker_start,
            user_start + source_speaker_end,
        )
        target_span = token_span(
            token_offsets,
            user_start + offsets[j] + answer.quote_start,
            user_start + offsets[j] + answer.quote_end,
        )
        query_span = token_span(
            token_offsets,
            user_start + len(context) + len(SEP),
            user_start + len(messages[0]["content"]),
        )
        if not (
            anchor_span[1]
            <= attribution_span[1]
            < target_span[0]
            < query_span[0]
            <= full - supervised
        ):
            raise ValueError("book evidence/token boundary differs")
        if target_span[1] - anchor_span[0] < min_lineage_tokens:
            rejects["insufficient_actual_token_lineage_extent"] += 1
            continue
        task_id = (
            "book-cross-"
            + _sha(
                f"{book['ebook_id']}:{i}:{j}:{anchor.quote}:{answer.quote}".encode()
            )[:20]
        )
        sample_id = task_id + "-v1"
        readers.append({"sample_id": sample_id, "messages": messages})
        indexes.append(
            {
                "sample_id": sample_id,
                "semantic_task_id": task_id,
                "task_id": task_id,
                "source_kind": "real_book",
                "source_group": book["source_group"],
                "split": book["split"],
                "domain": book["domain"],
                "topic": book["topic"],
                "operation": "cross_section_speaker_bind_follow",
                "task_type": "cross_section_speaker_bind_follow",
                "full_chat_tokens": full,
                "input_tokens": full - supervised,
                "supervised_tokens": supervised,
                "tokenizer_profile": "pinned-chat-template",
                "length_bin": "native",
                "context_sha256": _sha(context.encode()),
                "answer_sha256": _sha(answer.quote.encode()),
                "evidence_status": "literal_quote_attribution_and_target_statement_replayed",
                "dependency_status": "bounded_text_deletion_and_attribution_swap_passed",
                "observed_lineage_token_envelope": {
                    "start": anchor_span[0],
                    "end": target_span[1],
                },
            }
        )
        audits.append(
            {
                "sample_id": sample_id,
                "source_group": book["source_group"],
                "source_section": chapter_list[i].title,
                "target_section": chapter_list[j].title,
                "source_attribution": anchor.speaker,
                "counterfactual_speaker": alternate.speaker,
                "anchor": anchor.quote,
                "answer": answer.quote,
                "counterfactual_answer": alternate.quote,
                "source_quote_token_span": list(anchor_span),
                "source_speaker_token_span": list(attribution_span),
                "target_quote_token_span": list(target_span),
                "query_token_span": list(query_span),
                "lineage_extent_tokens": target_span[1] - anchor_span[0],
                "last_evidence_to_query_tokens": query_span[0] - target_span[1],
                "source_delete_unsolved": True,
                "target_equivalent_support_delete_unsolved": True,
                "source_attribution_swap_changes_answer": True,
                "full_chat_tokens": full,
                "supervised_tokens": supervised,
            }
        )
    return {
        "book": book,
        "sections": len(chapter_list),
        "proposals": len(proposals),
        "rejects": dict(rejects),
        "readers": readers,
        "indexes": indexes,
        "audits": audits,
    }


def run(
    source_dir: Path,
    output_dir: Path,
    *,
    workers: int,
    max_tasks_per_book: int,
    min_lineage_tokens: int = 16384,
    verify_only: bool = False,
) -> dict:
    manifest = json.loads((source_dir / "manifest.json").read_text())
    if manifest.get("schema") != "longworld.p112-book-freeze.v1":
        raise ValueError("book freeze manifest differs")
    if workers < 1 or workers > 8 or max_tasks_per_book < 1 or min_lineage_tokens < 0:
        raise ValueError("invalid bounded worker/task count")
    books = manifest["records"]
    with ProcessPoolExecutor(max_workers=min(workers, len(books))) as pool:
        results = list(
            pool.map(
                _compile_one,
                books,
                [source_dir] * len(books),
                [max_tasks_per_book] * len(books),
                [min_lineage_tokens] * len(books),
            )
        )
    outputs = {
        "train.jsonl": [],
        "eval.jsonl": [],
        "sample_index.jsonl": [],
        "audit.jsonl": [],
    }
    for result in results:
        for reader, index, audit in zip(
            result["readers"], result["indexes"], result["audits"], strict=True
        ):
            outputs[f"{index['split']}.jsonl"].append(reader)
            outputs["sample_index.jsonl"].append(index)
            outputs["audit.jsonl"].append(audit)
    encoded = {
        name: "".join(_dump(row) + "\n" for row in rows).encode()
        for name, rows in outputs.items()
    }
    receipt = {
        "schema": SCHEMA,
        "source_manifest_sha256": _sha((source_dir / "manifest.json").read_bytes()),
        "workers": workers,
        "max_tasks_per_book": max_tasks_per_book,
        "min_lineage_tokens": min_lineage_tokens,
        "source_books": len(books),
        "candidate_views": len(outputs["sample_index.jsonl"]),
        "independent_semantic_tasks": len(
            {row["semantic_task_id"] for row in outputs["sample_index.jsonl"]}
        ),
        "train": len(outputs["train.jsonl"]),
        "eval": len(outputs["eval.jsonl"]),
        "book_results": [
            {
                k: v
                for k, v in result.items()
                if k not in {"readers", "indexes", "audits"}
            }
            for result in results
        ],
        "file_sha256": {name: _sha(content) for name, content in encoded.items()},
        "train_ready": False,
    }
    encoded["manifest.json"] = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in encoded.items():
        path = output_dir / name
        if verify_only:
            if not path.exists() or path.read_bytes() != content:
                raise ValueError(f"book task replay differs: {name}")
        elif path.exists() and path.read_bytes() != content:
            raise ValueError(f"book task output exists with different bytes: {name}")
        else:
            path.write_bytes(content)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-tasks-per-book", type=int, default=16)
    parser.add_argument("--min-lineage-tokens", type=int, default=16384)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.source_dir,
                args.output,
                workers=args.workers,
                max_tasks_per_book=args.max_tasks_per_book,
                min_lineage_tokens=args.min_lineage_tokens,
                verify_only=args.verify_only,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
