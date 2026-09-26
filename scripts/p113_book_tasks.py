"""Compile audited, long-distance named-speech tasks from frozen book texts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_wiki_join_positions import token_span
from scripts.p112_book_tasks import SEP, _dump, _layout, _reader_sections
from scripts.p113_book_truth import (
    VERBS,
    alias_uncertain,
    audit_speeches,
    chapters,
    speeches,
)
from scripts.p114_book_broad_gate import (
    assert_verb_contract,
    later_named_quote_suspicions,
)
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p113-book-explicit-speech-reader.v1"
QUESTION = (
    "In {source}, which speaker name is printed with the single-line quotation “{anchor}”? "
    "Considering single-line quotations directly tagged with a named speech verb "
    "({verbs}), what is the last such quotation tagged with that same printed speaker name "
    "in {target}? Return only the quoted words."
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _scan(
    context: str, source_title: str, target_title: str, anchor: str
) -> tuple[str, str] | None:
    """Blind answer replay with the independent quote-first parser."""
    try:
        parsed = _reader_sections(context)
    except ValueError:
        return None
    if source_title not in parsed or target_title not in parsed:
        return None
    sources = [x for x in audit_speeches(parsed[source_title]) if x.quote == anchor]
    if len(sources) != 1:
        return None
    label = sources[0].label
    targets = [
        x
        for x in audit_speeches(parsed[target_title])
        if x.label.casefold() == label.casefold()
    ]
    return (label, targets[-1].quote) if targets else None


def _compile_book(
    book: dict, source_dir: Path, max_tasks: int, min_extent: int
) -> dict:
    assert_verb_contract(VERBS)
    body = (source_dir / book["body_file"]).read_bytes()
    if _sha(body) != book["body_sha256"]:
        raise ValueError("frozen book body changed")
    text = body.decode()
    cs = chapters(text)
    groups = [speeches(chapter.text) for chapter in cs]
    audits = [audit_speeches(chapter.text) for chapter in cs]
    all_labels = {x.label for group in groups + audits for x in group}
    proposals = []
    rejects = Counter()
    for i in range(len(cs)):
        by_source = groups[i]
        if not by_source:
            continue
        for j in range(i + 1, len(cs)):
            by_target = groups[j]
            labels = {x.label for x in by_source} & {x.label for x in by_target}
            if len({x.label for x in by_target}) < 2:
                rejects["target_lacks_competing_named_speakers"] += 1
                continue
            for label in labels:
                if alias_uncertain(label, all_labels):
                    rejects["explicit_name_alias_uncertain"] += 1
                    continue
                source_attributions = [
                    x
                    for x in by_source
                    if x.label == label
                    and 25 <= len(x.quote) <= 130
                    and text.count(x.quote) == 1
                ]
                if not source_attributions:
                    rejects["no_unique_source_anchor"] += 1
                    continue
                target_attributions = [x for x in by_target if x.label == label]
                answer = target_attributions[-1]
                if len(answer.quote) > 200 or text.count(answer.quote) != 1:
                    rejects["target_answer_not_short_unique"] += 1
                    continue
                if later_named_quote_suspicions(cs[j].text, label, answer.quote_end):
                    rejects["later_plausible_named_quote_uncertainty"] += 1
                    continue
                source_audit = [
                    x for x in audits[i] if x.label.casefold() == label.casefold()
                ]
                target_audit = [
                    x for x in audits[j] if x.label.casefold() == label.casefold()
                ]
                if [(x.quote_start, x.quote, x.label) for x in source_audit] != [
                    (x.quote_start, x.quote, x.label)
                    for x in by_source
                    if x.label == label
                ] or [(x.quote_start, x.quote, x.label) for x in target_audit] != [
                    (x.quote_start, x.quote, x.label) for x in target_attributions
                ]:
                    rejects["generator_auditor_attribution_disagreement"] += 1
                    continue
                alternatives = []
                for other in {x.label for x in by_target} - {label}:
                    if alias_uncertain(other, all_labels):
                        continue
                    items = [x for x in by_target if x.label == other]
                    audited_items = [
                        x for x in audits[j] if x.label.casefold() == other.casefold()
                    ]
                    if (
                        items
                        and len(items[-1].quote) <= 200
                        and text.count(items[-1].quote) == 1
                        and [(x.quote_start, x.quote) for x in items]
                        == [(x.quote_start, x.quote) for x in audited_items]
                    ):
                        alternatives.append((other, items[-1]))
                if not alternatives:
                    rejects["no_independently_audited_counterfactual_speaker"] += 1
                    continue
                other, alternate = min(
                    alternatives, key=lambda x: (_sha(x[1].quote.encode()), x[0])
                )
                for anchor in source_attributions[:8]:
                    if re.search(
                        r"(?<!\w)" + re.escape(label) + r"(?!\w)",
                        anchor.quote,
                        re.IGNORECASE,
                    ):
                        rejects["anchor_leaks_speaker_label"] += 1
                        continue
                    gap = (
                        cs[j].start
                        + answer.quote_start
                        - cs[i].start
                        - anchor.quote_start
                    )
                    if gap < min_extent * 2:
                        rejects["short_char_distance_precheck"] += 1
                        continue
                    proposals.append((gap, i, j, anchor, answer, other, alternate))
    proposals.sort(
        key=lambda x: (
            -min(x[0], 600000),
            _sha(f"{book['ebook_id']}:{x[1]}:{x[2]}:{x[3].quote}".encode()),
        )
    )
    tokenizer = get_tokenizer()
    readers, indexes, proofs = [], [], []
    used_anchor, used_answer, used_pair = set(), set(), set()
    for _, i, j, anchor, answer, other, alternate in proposals:
        if len(readers) >= max_tasks:
            break
        if (
            anchor.quote in used_anchor
            or answer.quote in used_answer
            or (i, j) in used_pair
        ):
            rejects["duplicate_anchor_answer_or_chapter_pair"] += 1
            continue
        desired = (32768, 65536, 131072)[len(readers) % 3]
        context, offsets, _ = _layout(cs, i, j, desired + 250, tokenizer)
        question = QUESTION.format(
            source=cs[i].title,
            target=cs[j].title,
            anchor=anchor.quote,
            verbs=", ".join(sorted(VERBS)),
        )
        if (
            answer.quote in question
            or alternate.quote in question
            or anchor.label in question
        ):
            rejects["question_leaks_gold_or_label"] += 1
            continue
        if context.count(anchor.quote) != 1 or context.count(answer.quote) != 1:
            rejects["reader_repeats_key_quote"] += 1
            continue
        if _scan(context, cs[i].title, cs[j].title, anchor.quote) != (
            anchor.label,
            answer.quote,
        ):
            rejects["blind_reader_answer_differs"] += 1
            continue
        a, b = offsets[i] + anchor.label_start, offsets[i] + anchor.label_end
        switched = context[:a] + other + context[b:]
        if _scan(switched, cs[i].title, cs[j].title, anchor.quote) != (
            other,
            alternate.quote,
        ):
            rejects["source_label_swap_does_not_change_answer"] += 1
            continue
        if (
            _scan(context[:a] + context[b:], cs[i].title, cs[j].title, anchor.quote)
            is not None
        ):
            rejects["source_label_deletion_leaves_answer"] += 1
            continue
        target_no_support = context
        for item in sorted(
            (x for x in audits[j] if x.label.casefold() == anchor.label.casefold()),
            key=lambda x: x.label_start,
            reverse=True,
        ):
            x, y = offsets[j] + item.label_start, offsets[j] + item.label_end
            target_no_support = target_no_support[:x] + target_no_support[y:]
        if _scan(target_no_support, cs[i].title, cs[j].title, anchor.quote) is not None:
            rejects["target_equivalent_label_deletion_leaves_answer"] += 1
            continue
        messages = [
            {"role": "user", "content": context + SEP + question},
            {"role": "assistant", "content": answer.quote},
        ]
        encoded = tokenize_assistant_only(tokenizer, messages, 262144)
        full = len(encoded["input_ids"])
        supervised = sum(x != -100 for x in encoded["labels"])
        if (
            full >= 262144
            or encoded["labels"]
            != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
        ):
            rejects["final_chat_length_or_mask_invalid"] += 1
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
        label_span = token_span(token_offsets, user_start + a, user_start + b)
        answer_span = token_span(
            token_offsets,
            user_start + offsets[j] + answer.quote_start,
            user_start + offsets[j] + answer.quote_end,
        )
        query_span = token_span(
            token_offsets,
            user_start + len(context) + len(SEP),
            user_start + len(messages[0]["content"]),
        )
        extent = answer_span[1] - anchor_span[0]
        if (
            not (
                anchor_span[1]
                <= label_span[1]
                < answer_span[0]
                < query_span[0]
                <= full - supervised
            )
            or extent < min_extent
        ):
            rejects["token_dependency_span_insufficient"] += 1
            continue
        task_id = (
            "book-explicit-"
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
                "operation": "cross_chapter_named_speech_follow",
                "task_type": "cross_chapter_named_speech_follow",
                "full_chat_tokens": full,
                "input_tokens": full - supervised,
                "supervised_tokens": supervised,
                "tokenizer_profile": "pinned-chat-template",
                "length_bin": "native",
                "context_sha256": _sha(context.encode()),
                "answer_sha256": _sha(answer.quote.encode()),
                "evidence_status": "independent_explicit_speech_parser_agreement",
                "dependency_status": "source_swap_and_both_support_deletions_passed",
                "observed_lineage_token_envelope": {
                    "start": anchor_span[0],
                    "end": answer_span[1],
                },
            }
        )
        proofs.append(
            {
                "sample_id": sample_id,
                "source_group": book["source_group"],
                "source_section": cs[i].title,
                "target_section": cs[j].title,
                "anchor": anchor.quote,
                "speaker_label": anchor.label,
                "answer": answer.quote,
                "counterfactual_label": other,
                "counterfactual_answer": alternate.quote,
                "anchor_token_span": list(anchor_span),
                "speaker_token_span": list(label_span),
                "answer_token_span": list(answer_span),
                "query_token_span": list(query_span),
                "lineage_extent_tokens": extent,
                "last_evidence_to_query_tokens": query_span[0] - answer_span[1],
                "full_chat_tokens": full,
                "supervised_tokens": supervised,
            }
        )
        used_anchor.add(anchor.quote)
        used_answer.add(answer.quote)
        used_pair.add((i, j))
    return {
        "ebook_id": book["ebook_id"],
        "source_group": book["source_group"],
        "topic": book["topic"],
        "split": book["split"],
        "chapters": len(cs),
        "proposals": len(proposals),
        "accepted": len(readers),
        "rejects": dict(rejects),
        "readers": readers,
        "indexes": indexes,
        "proofs": proofs,
    }


def run(
    source_dir: Path,
    output_dir: Path,
    *,
    workers: int = 4,
    max_tasks_per_book: int = 4,
    min_extent: int = 16384,
    verify_only: bool = False,
) -> dict:
    source = json.loads((source_dir / "manifest.json").read_text())
    if (
        source["schema"] != "longworld.p113-book-source-freeze.v1"
        or source.get("source_truth_code_sha256")
        != _sha((ROOT / "scripts/p113_book_truth.py").read_bytes())
        or source.get("broad_gate_code_sha256")
        != _sha((ROOT / "scripts/p114_book_broad_gate.py").read_bytes())
        or not 1 <= workers <= 8
        or not 1 <= max_tasks_per_book <= 32
    ):
        raise ValueError("book source or bounded worker/task contract differs")
    books = source["records"]
    with ProcessPoolExecutor(max_workers=min(workers, len(books))) as pool:
        results = list(
            pool.map(
                _compile_book,
                books,
                [source_dir] * len(books),
                [max_tasks_per_book] * len(books),
                [min_extent] * len(books),
            )
        )
    outputs = {
        "train.jsonl": [],
        "eval.jsonl": [],
        "sample_index.jsonl": [],
        "proofs.jsonl": [],
    }
    for result in results:
        for reader, index, proof in zip(
            result["readers"], result["indexes"], result["proofs"], strict=True
        ):
            outputs[f"{index['split']}.jsonl"].append(reader)
            outputs["sample_index.jsonl"].append(index)
            outputs["proofs.jsonl"].append(proof)
    encoded = {
        name: "".join(_dump(row) + "\n" for row in rows).encode()
        for name, rows in outputs.items()
    }
    receipt = {
        "schema": SCHEMA,
        "source_manifest_sha256": _sha((source_dir / "manifest.json").read_bytes()),
        "source_worlds": len(books),
        "candidate_views": len(outputs["sample_index.jsonl"]),
        "independent_semantic_tasks": len(
            {x["semantic_task_id"] for x in outputs["sample_index.jsonl"]}
        ),
        "train": len(outputs["train.jsonl"]),
        "eval": len(outputs["eval.jsonl"]),
        "workers": workers,
        "max_tasks_per_book": max_tasks_per_book,
        "min_lineage_tokens": min_extent,
        "book_results": [
            {k: v for k, v in x.items() if k not in {"readers", "indexes", "proofs"}}
            for x in results
        ],
        "file_sha256": {k: _sha(v) for k, v in encoded.items()},
        "train_ready": False,
    }
    encoded["manifest.json"] = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in encoded.items():
        path = output_dir / name
        if verify_only:
            if path.read_bytes() != content:
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
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-tasks-per-book", type=int, default=4)
    parser.add_argument("--min-lineage-tokens", type=int, default=16384)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(
        args.source_dir,
        args.output,
        workers=args.workers,
        max_tasks_per_book=args.max_tasks_per_book,
        min_extent=args.min_lineage_tokens,
        verify_only=args.verify_only,
    )
    print(
        json.dumps({k: v for k, v in result.items() if k != "book_results"}, indent=2)
    )


if __name__ == "__main__":
    main()
