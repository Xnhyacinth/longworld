"""Screen frozen books for complete cross-chapter printed-speaker intersections."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.p112_book_tasks import SEP, _layout, _reader_sections
from scripts.p113_book_truth import VERBS, audit_speeches, chapters, speeches
from scripts.p114_book_broad_gate import (
    SPEECH_VERBS,
    _quotes,
    assert_verb_contract,
    later_named_quote_suspicions,
)
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p116-book-intersection-screen.v1"
LANE = "p116_real_book_complete_speaker_intersection"
OPERATION = "cross_chapter_complete_printed_speaker_intersection"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _dump(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _labels(items: list) -> set[str]:
    return {item.label for item in items}


def _blind_names(context: str, first: str, second: str) -> list[str]:
    sections = _reader_sections(context)
    if first not in sections or second not in sections:
        raise ValueError("missing complete source chapter in reader")
    return sorted(
        _labels(audit_speeches(sections[first]))
        & _labels(audit_speeches(sections[second]))
    )


def _erase_label_support(context: str, section: str, label: str) -> str:
    """Delete all equivalent explicit name supports in one model-visible chapter."""
    sections = _reader_sections(context)
    marker = f"=== SECTION: {section} ===\n"
    base = context.index(marker) + len(marker)
    spans = [
        (base + item.label_start, base + item.label_end)
        for item in audit_speeches(sections[section])
        if item.label == label
    ]
    if not spans:
        raise ValueError("no explicit support to erase")
    for a, b in sorted(set(spans), reverse=True):
        if context[a:b] != label:
            raise ValueError("equivalent support offset differs")
        context = context[:a] + context[b:]
    return context


def _broad_missing(chapter_text: str, label: str, audited: list) -> bool:
    """Conservatively veto a plausible tagged quotation absent from truth."""
    recognized = {
        (item.quote_start - 1, item.quote)
        for item in audited
        if item.label.casefold() == label.casefold()
    }
    return any(
        (item["quote_start"], item["quote"]) not in recognized
        for item in later_named_quote_suspicions(chapter_text, label, 0)
    )


_EDGE_VERB = r"(?i:(?:" + "|".join(sorted(SPEECH_VERBS)) + r"))"
_EDGE_NAME = (
    r"(?P<name>(?:the[ \t]+)?"
    r"(?:(?:Mr|Mrs|Ms|Dr|St)\.[ \t]+|(?:Sir|Lady|Lord|Inspector)[ \t]+)?"
    r"[A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z][A-Za-z'’-]+){0,2})"
)
_EDGE_MOD = r"(?:(?!(?:the|a|an)\b)[a-z]+[ \t]+){0,3}"
_EDGE_AFTER = (
    re.compile(r"^" + _EDGE_VERB + r"[ \t]+" + _EDGE_MOD + _EDGE_NAME),
    re.compile(r"^" + _EDGE_NAME + r"[ \t]+" + _EDGE_MOD + _EDGE_VERB + r"\b"),
)
_EDGE_BEFORE = (
    re.compile(
        _EDGE_NAME + r"[ \t]+" + _EDGE_MOD + _EDGE_VERB + r"[ \t]*[,;:]?[ \t]*$"
    ),
    re.compile(
        _EDGE_VERB + r"[ \t]+" + _EDGE_MOD + _EDGE_NAME + r"[ \t]*[,;:]?[ \t]*$"
    ),
)
_NON_NAMES = frozenset(
    {
        "he",
        "she",
        "they",
        "i",
        "we",
        "you",
        "it",
        "the",
        "this",
        "that",
        "there",
        "his",
        "her",
    }
)
_QUOTE_MARKS = "\"“”'‘’"


def _edge_fragments(text: str, start: int, end: int) -> tuple[str, str]:
    after = text[end : end + 140].lstrip(" \t,;:—-")
    if after.startswith("\n"):
        after = after[1:].lstrip(" \t")
    after = after.split("\n", 1)[0]
    before = text[max(0, start - 140) : start].split("\n")[-1]
    for mark in _QUOTE_MARKS:
        after = after.split(mark, 1)[0]
    prior_quote = max(before.rfind(mark) for mark in _QUOTE_MARKS)
    before = before[prior_quote + 1 :]
    return after, before


def _edge_candidates(text: str, start: int, end: int) -> list[tuple[str, str, str]]:
    after, before = _edge_fragments(text, start, end)
    candidates = []
    for side, fragment, patterns in (
        ("after", after, _EDGE_AFTER),
        ("before", before, _EDGE_BEFORE),
    ):
        for pattern in patterns:
            match = (
                pattern.match(fragment) if side == "after" else pattern.search(fragment)
            )
            if match:
                name = " ".join(match.group("name").split())
                if name.casefold() not in _NON_NAMES:
                    candidates.append((side, name, fragment[:160]))
                break
    return candidates


def _possible_tag_evidence(chapter_text: str, audited: list) -> list[dict]:
    """Find possible named speech beyond the precise truth grammar."""
    recognized = {}
    for item in audited:
        recognized.setdefault((item.quote_start - 1, item.quote), set()).add(item.label)
    evidence = []
    for start, end, quote in _quotes(chapter_text):
        for side, name, segment in _edge_candidates(chapter_text, start, end):
            if name not in recognized.get((start, quote), set()):
                evidence.append(
                    {
                        "category": (
                            "article_titled_possible_speaker"
                            if name.startswith("the ")
                            else "unparsed_possible_named_tag"
                        ),
                        "candidate_label": name,
                        "quote_start": start,
                        "quote_excerpt": quote[:120],
                        "side": side,
                        "tag_excerpt": segment[:160],
                    }
                )
    return evidence


def _unparsed_possible_tag(chapter_text: str, audited: list) -> bool:
    """Conservatively veto a chapter with unknown name-like speech support."""
    return bool(_possible_tag_evidence(chapter_text, audited))


def _screen_book(book: dict, source_dir: Path) -> dict:
    body = (source_dir / book["body_file"]).read_bytes()
    if _sha(body) != book["body_sha256"]:
        raise ValueError(f"frozen book body changed: {book['source_group']}")
    cs = chapters(body.decode())
    generated = [speeches(chapter.text) for chapter in cs]
    audited = [audit_speeches(chapter.text) for chapter in cs]
    ledger = []
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            left, right = _labels(generated[i]), _labels(generated[j])
            left_audit, right_audit = _labels(audited[i]), _labels(audited[j])
            shared = sorted(left & right)
            reason = "eligible"
            if left != left_audit or right != right_audit:
                reason = "generator_auditor_label_set_disagreement"
            elif not shared:
                reason = "empty_intersection"
            elif len(left) < 2 or len(right) < 2:
                reason = "insufficient_competing_speakers"
            elif not (left - right) or not (right - left):
                reason = "one_chapter_set_subsumes_other"
            elif cs[j].start - cs[i].start < 65536:
                reason = "short_character_span_precheck"
            ledger.append(
                {
                    "source_group": book["source_group"],
                    "split": book["split"],
                    "topic": book["topic"],
                    "source_chapter": cs[i].title,
                    "target_chapter": cs[j].title,
                    "source_index": i,
                    "target_index": j,
                    "source_speakers": len(left),
                    "target_speakers": len(right),
                    "shared_speakers": shared if reason == "eligible" else [],
                    "reason": reason,
                }
            )
    return {
        "source_group": book["source_group"],
        "chapters": len(cs),
        "pair_ledger": ledger,
    }


def screen(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    cfg = json.loads(config_path.read_text())
    if (
        cfg.get("schema") != "longworld.p116-book-speaker-intersection-request.v1"
        or cfg.get("compiler_revision") != "edge-attribution-v3"
    ):
        raise ValueError("invalid intersection config")
    source_dir = ROOT / cfg["source_dir"]
    source_path = source_dir / "manifest.json"
    source = json.loads(source_path.read_text())
    if (
        source.get("schema") != "longworld.p113-book-source-freeze.v1"
        or source.get("source_truth_code_sha256")
        != _sha((ROOT / "scripts/p113_book_truth.py").read_bytes())
        or source.get("broad_gate_code_sha256")
        != _sha((ROOT / "scripts/p114_book_broad_gate.py").read_bytes())
    ):
        raise ValueError("frozen source/truth parser pin differs")
    books = source["records"]
    workers = cfg["workers"]
    if not 1 <= workers <= 8:
        raise ValueError("workers outside bounded range")
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_screen_book, books, [source_dir] * len(books)))
    ledger = [row for result in results for row in result["pair_ledger"]]
    ledger_bytes = b"".join(_dump(row) for row in ledger)
    receipt = {
        "schema": SCHEMA,
        "config_sha256": _sha(config_path.read_bytes()),
        "compiler_code_sha256": _sha(Path(__file__).read_bytes()),
        "source_manifest_sha256": _sha(source_path.read_bytes()),
        "source_worlds": len(books),
        "chapter_pairs": len(ledger),
        "eligible_pairs": sum(row["reason"] == "eligible" for row in ledger),
        "reason_counts": dict(sorted(Counter(row["reason"] for row in ledger).items())),
        "ledger_sha256": _sha(ledger_bytes),
        "train_ready": False,
    }
    files = {
        "pair_ledger.jsonl": ledger_bytes,
        "manifest.json": json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, indent=2
        ).encode()
        + b"\n",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = output_dir / name
        if verify_only:
            if path.read_bytes() != content:
                raise ValueError(f"screen replay differs: {name}")
        elif path.exists() and path.read_bytes() != content:
            raise ValueError(f"screen existing bytes differ: {name}")
        else:
            path.write_bytes(content)
    return receipt


def _replace_one_label(context: str, section: str, old: str, new: str) -> str:
    parsed = _reader_sections(context)
    marker = f"=== SECTION: {section} ===\n"
    base = context.index(marker) + len(marker)
    item = next(
        (item for item in audit_speeches(parsed[section]) if item.label == old),
        None,
    )
    if item is None:
        raise ValueError("missing negative-control label")
    a, b = base + item.label_start, base + item.label_end
    if context[a:b] != old:
        raise ValueError("negative-control label offset differs")
    return context[:a] + new + context[b:]


def _compile_book(
    book: dict, source_dir: Path, candidates: list[dict], cfg: dict
) -> dict:
    assert_verb_contract(VERBS)
    body = (source_dir / book["body_file"]).read_bytes()
    if _sha(body) != book["body_sha256"]:
        raise ValueError("frozen body differs during compile")
    cs = chapters(body.decode())
    generator = [speeches(chapter.text) for chapter in cs]
    auditor = [audit_speeches(chapter.text) for chapter in cs]
    unknown_tag_chapters = {
        index: evidence
        for index, chapter in enumerate(cs)
        if (evidence := _possible_tag_evidence(chapter.text, auditor[index]))
    }
    # Expensive full-chat materialization follows all cheap truth and intervention gates.
    ordered = sorted(
        candidates,
        key=lambda row: (
            -min(len(row["shared_speakers"]), 4),
            -(cs[row["target_index"]].start - cs[row["source_index"]].start),
            _sha(
                f"{book['ebook_id']}:{row['source_index']}:{row['target_index']}".encode()
            ),
        ),
    )
    statuses: dict[tuple[int, int], str] = {}
    accepted: list[dict[str, Any]] = []
    tokenizer = None
    broad_cache: dict[tuple[int, str], bool] = {}
    used_chapters: set[int] = set()

    def broad_missing(chapter_idx: int, label: str) -> bool:
        key = (chapter_idx, label)
        if key not in broad_cache:
            broad_cache[key] = _broad_missing(
                cs[chapter_idx].text, label, auditor[chapter_idx]
            )
        return broad_cache[key]

    for row in ordered:
        i, j = row["source_index"], row["target_index"]
        key = (i, j)
        if len(accepted) >= cfg["max_tasks_per_book"]:
            statuses[key] = "not_materialized_after_cap"
            continue
        if i in used_chapters and j in used_chapters:
            statuses[key] = "both_chapters_already_exposed"
            continue
        left, right = _labels(generator[i]), _labels(generator[j])
        common = sorted(left & right)
        if common != row["shared_speakers"]:
            raise ValueError("screened pair truth drifted")
        if i in unknown_tag_chapters or j in unknown_tag_chapters:
            statuses[key] = "unknown_or_article_titled_possible_speaker"
            continue
        if sorted((x.quote_start, x.quote, x.label) for x in generator[i]) != sorted(
            (x.quote_start, x.quote, x.label) for x in auditor[i]
        ) or sorted((x.quote_start, x.quote, x.label) for x in generator[j]) != sorted(
            (x.quote_start, x.quote, x.label) for x in auditor[j]
        ):
            statuses[key] = "generator_auditor_full_attribution_disagreement"
            continue
        if any(
            broad_missing(chapter_idx, label)
            for chapter_idx in (i, j)
            for label in left | right
        ):
            statuses[key] = "plausible_untagged_or_variant_speech_support"
            continue
        if tokenizer is None:
            tokenizer = get_tokenizer()
        desired = (32768, 65536, 131072)[len(accepted) % 3]
        context, offsets, _ = _layout(cs, i, j, desired + 250, tokenizer)
        first, second = cs[i].title, cs[j].title
        if _blind_names(context, first, second) != common:
            statuses[key] = "blind_complete_set_differs"
            continue
        if any(
            _blind_names(_erase_label_support(context, section, label), first, second)
            == common
            for section in (first, second)
            for label in common
        ):
            statuses[key] = "equivalent_support_deletion_leaves_answer"
            continue
        source_only, target_only = sorted(left - right), sorted(right - left)
        source_insert = _replace_one_label(
            context, second, target_only[0], source_only[0]
        )
        target_insert = _replace_one_label(
            context, first, source_only[0], target_only[0]
        )
        if _blind_names(source_insert, first, second) != sorted(
            common + [source_only[0]]
        ) or _blind_names(target_insert, first, second) != sorted(
            common + [target_only[0]]
        ):
            statuses[key] = "negative_candidate_insertion_not_detected"
            continue
        answer = json.dumps(common, ensure_ascii=False, separators=(",", ":"))
        question = (
            f"Across {first} and {second}, list every exact printed speaker name "
            "that appears in both chapters with a single-line quotation directly "
            "tagged by one of these speech verbs: "
            + ", ".join(sorted(VERBS))
            + ". Match the printed names exactly, without merging aliases or "
            "inferring speakers of untagged dialogue. Return only a JSON array "
            "of names in alphabetical order."
        )
        if any(label in question for label in common):
            statuses[key] = "question_leaks_answer_label"
            continue
        messages = [
            {"role": "user", "content": context + SEP + question},
            {"role": "assistant", "content": answer},
        ]
        encoded = tokenize_assistant_only(tokenizer, messages, cfg["max_chat_tokens"])
        full = len(encoded["input_ids"])
        supervised = sum(x != -100 for x in encoded["labels"])
        if full >= cfg["max_chat_tokens"] or encoded["labels"] != (
            [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
        ):
            statuses[key] = "final_chat_length_or_mask_invalid"
            continue
        prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
        start = prompt.index(messages[0]["content"])
        token_offsets = tokenizer(
            prompt, truncation=False, return_offsets_mapping=True
        )["offset_mapping"]
        label_spans = []
        for chapter_idx in (i, j):
            for item in auditor[chapter_idx]:
                if item.label in common:
                    label_spans.append(
                        (
                            chapter_idx,
                            item.label,
                            token_span(
                                token_offsets,
                                start + offsets[chapter_idx] + item.label_start,
                                start + offsets[chapter_idx] + item.label_end,
                            ),
                        )
                    )
        closest_by_label = []
        for label in common:
            source_last = max(
                span[1] for idx, name, span in label_spans if idx == i and name == label
            )
            target_first = min(
                span[0] for idx, name, span in label_spans if idx == j and name == label
            )
            closest_by_label.append(target_first - source_last)
        closest = min(closest_by_label)
        query_span = token_span(
            token_offsets,
            start + len(context) + len(SEP),
            start + len(messages[0]["content"]),
        )
        if closest < cfg["min_lineage_tokens"] or query_span[1] > full - supervised:
            statuses[key] = "final_token_support_distance_insufficient"
            continue
        task_id = (
            "book-intersection-" + _sha(f"{book['ebook_id']}:{i}:{j}".encode())[:20]
        )
        sample_id = task_id + "-v1"
        index = {
            "sample_id": sample_id,
            "semantic_task_id": task_id,
            "task_id": task_id,
            "source_kind": "real_book",
            "source_group": book["source_group"],
            "split": book["split"],
            "domain": book["domain"],
            "topic": book["topic"],
            "operation": OPERATION,
            "task_type": OPERATION,
            "full_chat_tokens": full,
            "input_tokens": full - supervised,
            "supervised_tokens": supervised,
            "tokenizer_profile": "pinned-chat-template",
            "length_bin": "native",
            "context_sha256": _sha(context.encode()),
            "answer_sha256": _sha(answer.encode()),
            "evidence_status": "two_parsers_full_set_agree_with_broad_support_veto",
            "dependency_status": "all_shared_support_deletion_and_two_negative_insertions_passed",
            "observed_lineage_token_envelope": {
                "start": min(span[0] for _, _, span in label_spans),
                "end": max(span[1] for _, _, span in label_spans),
            },
        }
        proof = {
            "sample_id": sample_id,
            "source_group": book["source_group"],
            "source_chapter": first,
            "target_chapter": second,
            "answer_names": common,
            "source_only_counterfactual": source_only[0],
            "target_only_counterfactual": target_only[0],
            "all_shared_label_token_spans": [
                {"chapter": cs[idx].title, "label": label, "span": list(span)}
                for idx, label, span in label_spans
            ],
            "closest_shared_cross_chapter_tokens": closest,
            "query_token_span": list(query_span),
            "full_chat_tokens": full,
            "supervised_tokens": supervised,
        }
        accepted.append(
            {
                "reader": {"sample_id": sample_id, "messages": messages},
                "index": index,
                "proof": proof,
            }
        )
        statuses[key] = "accepted"
        used_chapters.update((i, j))
    return {
        "source_group": book["source_group"],
        "accepted": accepted,
        "statuses": statuses,
        "unknown_tag_evidence": [
            {
                "source_group": book["source_group"],
                "chapter": cs[index].title,
                "chapter_index": index,
                "evidence": evidence,
            }
            for index, evidence in sorted(unknown_tag_chapters.items())
        ],
    }


def compile_candidates(
    config_path: Path, screen_dir: Path, output_dir: Path, *, verify_only: bool = False
) -> dict:
    cfg = json.loads(config_path.read_text())
    if (
        cfg.get("schema") != "longworld.p116-book-speaker-intersection-request.v1"
        or cfg.get("compiler_revision") != "edge-attribution-v3"
    ):
        raise ValueError("invalid intersection config")
    source_dir = ROOT / cfg["source_dir"]
    source_path = source_dir / "manifest.json"
    source = json.loads(source_path.read_text())
    if source.get("source_truth_code_sha256") != _sha(
        (ROOT / "scripts/p113_book_truth.py").read_bytes()
    ) or source.get("broad_gate_code_sha256") != _sha(
        (ROOT / "scripts/p114_book_broad_gate.py").read_bytes()
    ):
        raise ValueError("frozen truth/broad parser pin differs")
    screened = json.loads((screen_dir / "manifest.json").read_text())
    pair_bytes = (screen_dir / "pair_ledger.jsonl").read_bytes()
    if (
        screened.get("schema") != SCHEMA
        or screened["config_sha256"] != _sha(config_path.read_bytes())
        or screened.get("compiler_code_sha256") != _sha(Path(__file__).read_bytes())
        or screened["source_manifest_sha256"] != _sha(source_path.read_bytes())
        or screened["ledger_sha256"] != _sha(pair_bytes)
    ):
        raise ValueError("screen/source/config pin differs")
    pair_rows = [json.loads(line) for line in pair_bytes.splitlines()]
    by_book: dict[str, list[dict]] = {
        book["source_group"]: [] for book in source["records"]
    }
    for row in pair_rows:
        if row["reason"] == "eligible":
            by_book[row["source_group"]].append(row)
    books = source["records"]
    with ProcessPoolExecutor(max_workers=cfg["workers"]) as pool:
        results = list(
            pool.map(
                _compile_book,
                books,
                [source_dir] * len(books),
                [by_book[book["source_group"]] for book in books],
                [cfg] * len(books),
            )
        )
    statuses = {result["source_group"]: result["statuses"] for result in results}
    compile_rows = []
    for row in pair_rows:
        key = (row["source_index"], row["target_index"])
        compile_rows.append(
            {
                **row,
                "compile_reason": statuses[row["source_group"]].get(key, row["reason"]),
            }
        )
    prior_path = ROOT / cfg["prior_unified_dir"] / "sample_index.jsonl"
    prior = [json.loads(line) for line in prior_path.read_text().splitlines()]
    prior_ids = {row["sample_id"] for row in prior}
    prior_tasks = {row["semantic_task_id"] for row in prior}
    receipt = screen_dir / "manifest.json"
    tokenizer = get_tokenizer()
    positions = Counter()
    ledger = CandidateLedger()
    readers: dict[str, list[dict]] = {"train": [], "eval": []}
    indexes, proofs, masks = [], [], []
    for result in results:
        for accepted in result["accepted"]:
            reader, native, proof = (
                accepted["reader"],
                accepted["index"],
                accepted["proof"],
            )
            if (
                native["sample_id"] in prior_ids
                or native["semantic_task_id"] in prior_tasks
            ):
                raise ValueError("P114 duplicate task or view ID")
            user = reader["messages"][0]["content"]
            if user.count(SEP) != 1:
                raise ValueError("reader question boundary differs")
            binding = AdapterBinding(
                source_kind="real_book",
                source_group=native["source_group"],
                domain=native["domain"],
                topic=native["topic"],
                operation=OPERATION,
                evidence_profile="two_parser_complete_set_and_interventions",
                tokenizer_profile="pinned-chat-template",
                receipt_path=receipt,
                receipt_sha256=_sha(receipt.read_bytes()),
            )
            candidate = normalize_native_candidate(
                native, reader, binding, context_text=user.split(SEP, 1)[0]
            )
            ledger.add(candidate)
            mask = audit_reader(
                reader, candidate.to_dict(), tokenizer, cfg["max_chat_tokens"]
            )
            split = native["split"]
            index = candidate.to_dict()
            index.update(
                source_name=LANE,
                output_file=f"candidate_{split}.jsonl",
                row_index=positions[split],
                native_row_ref=f"{screen_dir / 'pair_ledger.jsonl'}:{native['source_group']}",
            )
            positions[split] += 1
            readers[split].append(reader)
            indexes.append(index)
            proofs.append(proof)
            masks.append(mask)
    files = {
        "candidate_train.jsonl": b"".join(_dump(row) for row in readers["train"]),
        "candidate_eval.jsonl": b"".join(_dump(row) for row in readers["eval"]),
        "sample_index.jsonl": b"".join(_dump(row) for row in indexes),
        "proofs.jsonl": b"".join(_dump(row) for row in proofs),
        "mask_rows.jsonl": b"".join(_dump(row) for row in masks),
        "compile_ledger.jsonl": b"".join(_dump(row) for row in compile_rows),
        "unknown_tag_evidence.jsonl": b"".join(
            _dump(row) for result in results for row in result["unknown_tag_evidence"]
        ),
    }
    receipt_data = {
        "schema_version": "longworld.unified-candidates.v1",
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "views_by_lane": {LANE: ledger.rows},
        "splits": dict(sorted(positions.items())),
        "source_worlds": len(books),
        "productive_worlds": sum(bool(result["accepted"]) for result in results),
        "screen_manifest_sha256": _sha(receipt.read_bytes()),
        "compiler_code_sha256": _sha(Path(__file__).read_bytes()),
        "prior_unified_index_sha256": _sha(prior_path.read_bytes()),
        "length_bins": dict(
            sorted(Counter(index["length_bin"] for index in indexes).items())
        ),
        "answer_cardinality": dict(
            sorted(Counter(str(len(proof["answer_names"])) for proof in proofs).items())
        ),
        "compile_reasons": dict(
            sorted(Counter(row["compile_reason"] for row in compile_rows).items())
        ),
        "unknown_tag_categories": dict(
            sorted(
                Counter(
                    item["category"]
                    for result in results
                    for row in result["unknown_tag_evidence"]
                    for item in row["evidence"]
                ).items()
            )
        ),
        "files_sha256": {name: _sha(content) for name, content in files.items()},
        "train_ready": False,
    }
    files["manifest.json"] = (
        json.dumps(receipt_data, ensure_ascii=False, sort_keys=True, indent=2).encode()
        + b"\n"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = output_dir / name
        if verify_only:
            if path.read_bytes() != content:
                raise ValueError(f"compile replay differs: {name}")
        elif path.exists() and path.read_bytes() != content:
            raise ValueError(f"compile existing bytes differ: {name}")
        else:
            path.write_bytes(content)
    return verify_merge(output_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/p116_book_speaker_intersection_v1.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("screen", "compile"), default="screen")
    parser.add_argument(
        "--screen-dir",
        type=Path,
        default=Path("data/candidates/p116_book_intersection_screen_v1"),
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        screen(args.config, args.output, verify_only=args.verify_only)
        if args.phase == "screen"
        else compile_candidates(
            args.config, args.screen_dir, args.output, verify_only=args.verify_only
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
