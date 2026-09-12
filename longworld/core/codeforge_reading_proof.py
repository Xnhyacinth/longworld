"""Finite, gold-assisted filename-copy certificates over actual reader text.

The reader may select any gold filename when any enumerated alias occurs in its
question or one contiguous raw-token window. It is granted oracle resolution of
basename ambiguity and arbitrary subset selection. A negative result is only a
necessary-condition certificate for this grammar, not a claim about pretrained
models, filename synthesis, or multiple-window retrieval.
"""

from __future__ import annotations

import bisect
import heapq
import json
import re
import unicodedata
from collections import defaultdict, deque
from itertools import pairwise
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

PROFILE = "p65-codeforge-filename-copy-aggregation-v1"
CONTENT_PROFILE = "p65-codeforge-filename-copy-content-backed-v1"
CONTENT_CONTROL = "record-texts-sha-normalized-preserve-filename-aliases-v1"
GRAMMAR = "ascii-case-insensitive-full-path-basename-json-url-copy-v1"
FILE_PROGRAMS = frozenset(
    {"merged_files_union", "shared_changed_files", "approved_merge_files"}
)
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def alias_surfaces(filename: str) -> list[str]:
    """The complete finite alias grammar; substring matches are deliberately allowed."""
    if not isinstance(filename, str) or not filename:
        raise ValueError("a filename label must be a nonempty string")
    base = {filename, filename.replace("/", "\\"), filename.replace("\\", "/")}
    base.update(PurePosixPath(v.replace("\\", "/")).name for v in list(base))
    base.update(unquote(v) for v in list(base))
    base.update(
        unicodedata.normalize(form, v) for v in list(base) for form in ("NFC", "NFD")
    )
    surfaces = set(base)
    for value in base:
        surfaces.update(quote(value, safe=safe) for safe in ("", "/"))
        surfaces.update(
            json.dumps(value, ensure_ascii=ascii_mode)[1:-1]
            for ascii_mode in (False, True)
        )
    return sorted(v for v in surfaces if v)


class AliasIndex:
    """Aho-Corasick occurrence enumeration, including overlapping/suffix matches."""

    def __init__(self, filenames: list[str]):
        self.filenames = list(filenames)
        surfaces = defaultdict(set)
        for label, filename in enumerate(filenames):
            for alias in alias_surfaces(filename):
                surfaces[alias.translate(_ASCII_FOLD)].add(label)
        self.patterns = [
            {"surface": s, "labels": sorted(labels)}
            for s, labels in sorted(surfaces.items())
        ]
        self.edges, self.fail, self.outputs = [{}], [0], [[]]
        for pattern_id, pattern in enumerate(self.patterns):
            state = 0
            for char in pattern["surface"]:
                if char not in self.edges[state]:
                    self.edges[state][char] = len(self.edges)
                    self.edges.append({})
                    self.fail.append(0)
                    self.outputs.append([])
                state = self.edges[state][char]
            self.outputs[state].append(pattern_id)
        queue = deque(self.edges[0].values())
        while queue:
            state = queue.popleft()
            for char, child in self.edges[state].items():
                queue.append(child)
                fallback = self.fail[state]
                while fallback and char not in self.edges[fallback]:
                    fallback = self.fail[fallback]
                self.fail[child] = self.edges[fallback].get(char, 0)
                self.outputs[child].extend(self.outputs[self.fail[child]])

    def find(self, text: str):
        state = 0
        # ASCII folding preserves the exact character coordinates, including
        # when the context contains non-ASCII text.
        for end, char in enumerate(text.translate(_ASCII_FOLD), 1):
            while state and char not in self.edges[state]:
                state = self.fail[state]
            state = self.edges[state].get(char, 0)
            for pattern_id in self.outputs[state]:
                pattern = self.patterns[pattern_id]
                yield {
                    "alias_id": pattern_id,
                    "surface": pattern["surface"],
                    "labels": pattern["labels"],
                    "start": end - len(pattern["surface"]),
                    "end": end,
                }


def token_interval(
    start: int, end: int, starts: list[int], ends: list[int]
) -> tuple[int, int]:
    left, right = bisect.bisect_right(ends, start), bisect.bisect_left(starts, end)
    if left >= right:
        raise ValueError("alias has no exact token coverage")
    return left, right


def minimum_cover(groups: list[list[tuple[int, int]]]) -> dict:
    """Exact minimum interval covering at least one occurrence of every label.

    Sweep occurrence right endpoints. At R, each label's greatest available
    left endpoint dominates its other occurrences. Their minimum is the
    greatest feasible window start. Enumerating all R therefore is exhaustive.
    """
    if not groups:
        return {"minimum_tokens": 0, "start": 0, "end": 0, "witnesses": []}
    if any(not group for group in groups):
        return {"minimum_tokens": None, "start": None, "end": None, "witnesses": []}
    events = sorted(
        (end, start, label)
        for label, group in enumerate(groups)
        for start, end in set(group)
    )
    latest, heap, best = {}, [], None
    for end, start, label in events:
        if start < 0 or end <= start:
            raise ValueError("invalid occurrence interval")
        if label not in latest or start > latest[label][0]:
            latest[label] = (start, end)
            heapq.heappush(heap, (start, label))
        if len(latest) != len(groups):
            continue
        while heap[0][0] != latest[heap[0][1]][0]:
            heapq.heappop(heap)
        left = heap[0][0]
        candidate = (end - left, left, end)
        if best is None or candidate < best[:3]:
            best = (
                *candidate,
                [
                    {"label": i, "start": latest[i][0], "end": latest[i][1]}
                    for i in range(len(groups))
                ],
            )
    return {
        "minimum_tokens": best[0],
        "start": best[1],
        "end": best[2],
        "witnesses": best[3],
    }


def window_coverage(
    groups: list[list[tuple[int, int]]], width: int, total: int
) -> dict:
    """Exhaust every integer raw-token start through merged label-coverage runs."""
    if width <= 0 or total <= 0:
        raise ValueError("invalid window dimensions")
    last = max(0, total - width)
    events = defaultdict(int)
    for group in groups:
        runs = sorted(
            (max(0, end - width), min(start, last))
            for start, end in set(group)
            if max(0, end - width) <= min(start, last)
        )
        merged = []
        for lo, hi in runs:
            if merged and lo <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
            else:
                merged.append((lo, hi))
        for lo, hi in merged:
            events[lo] += 1
            events[hi + 1] -= 1
    active = maximum = 0
    witness = 0
    for position, change in sorted(events.items()):
        active += change
        if active > maximum:
            maximum, witness = active, position
    return {
        "width": width,
        "max_labels": maximum,
        "required_labels": len(groups),
        "all_labels_fit": maximum == len(groups),
        "witness_start": witness,
        "witness_end": min(total, witness + width),
        "all_integer_starts_examined_by_interval_sweep": True,
    }


def _aggregate(heads: list[dict], program: str) -> list[str]:
    active = [h for h in heads if program != "approved_merge_files" or h["approved"]]
    if not active:
        return []
    sets = [set(h["files"]) for h in active]
    return sorted(
        set.intersection(*sets)
        if program == "shared_changed_files"
        else set.union(*sets)
    )


def visible_file_reader(
    visible: dict, program: str, *, intervention: dict | None = None
) -> dict:
    """Full reader using only fields actually present in compact P64 JSON."""
    if program not in FILE_PROGRAMS:
        raise ValueError("program is outside the filename grammar")
    records = {r["id"]: r for r in visible["records"]}
    heads = []
    for scope in visible["scope"]:
        selected = {key: records[key] for key in scope["records"]}
        merges = [r for r in selected.values() if r["kind"] == "merge"]
        if len(merges) != 1:
            raise ValueError("visible scope has no unique merge")
        merge = merges[0]
        linked = [selected[key] for key in merge["links"] if key in selected]
        prs = [
            r
            for r in linked
            if r["kind"] == "pull_request"
            and r["attributes"].get("number") == scope["pull_request"]
        ]
        commits = [
            r
            for r in linked
            if r["kind"] == "commit"
            and r["attributes"].get("sha") == merge["attributes"].get("commit")
        ]
        if len(prs) != 1 or len(commits) != 1:
            raise ValueError("visible merge head/PR is not uniquely selected")
        pr, head = prs[0], commits[0]
        files = sorted(set(re.findall(r"^diff -- (.+)$", head["text"], re.MULTILINE)))
        if not files:
            raise ValueError("visible head has no filename headers")
        approved = any(
            r["kind"] == "review"
            and r["attributes"].get("state") == "APPROVED"
            and pr["id"] in r["links"]
            and head["id"] in r["links"]
            for r in linked
        )
        if intervention is not None and intervention["head_id"] == head["id"]:
            old, new = intervention["old_path"], intervention["new_path"]
            if old not in files or new in files:
                raise ValueError("invalid hypothetical path intervention")
            files = sorted((set(files) - {old}) | {new})
        heads.append(
            {
                "pull_request": scope["pull_request"],
                "head_id": head["id"],
                "merge_id": merge["id"],
                "merge_time": merge["occurred_at"],
                "files": files,
                "approved": approved,
                "scope_record_ids": scope["records"],
            }
        )
    if len(heads) < 2 or len({h["pull_request"] for h in heads}) != len(heads):
        raise ValueError("file aggregation requires distinct multiple source heads")
    answer = _aggregate(heads, program)
    active = [h for h in heads if program != "approved_merge_files" or h["approved"]]
    removal = []
    for head in heads:
        removed = _aggregate(
            [h for h in heads if h["head_id"] != head["head_id"]], program
        )
        removal.append(
            {
                "head_id": head["head_id"],
                "pull_request": head["pull_request"],
                "active": head in active,
                "changes_answer": removed != answer,
                "lost_filenames": sorted(set(answer) - set(removed)),
                "gained_filenames": sorted(set(removed) - set(answer)),
            }
        )
    latest = max(heads, key=lambda h: (h["merge_time"], h["pull_request"]))
    return {
        "answer": answer,
        "heads": heads,
        "active_heads": [h["head_id"] for h in active],
        "remove_one_input": removal,
        "all_active_sources_change_answer": len(active) >= 2
        and all(r["changes_answer"] for r in removal if r["active"]),
        "approval_changes_answer": answer != _aggregate(heads, "merged_files_union")
        if program == "approved_merge_files"
        else None,
        "latest_head_id": latest["head_id"],
        "latest_scope_record_ids": latest["scope_record_ids"],
        "latest_head_only_exact_answer": latest["files"] == answer,
        "any_single_head_exact_answer": any(h["files"] == answer for h in heads),
    }


def occurrence_table(
    text: str, filenames: list[str], offsets: list[tuple[int, int]]
) -> dict:
    starts, ends = [a for a, _ in offsets], [b for _, b in offsets]
    if any(a > b for a, b in pairwise(starts)) or any(a > b for a, b in pairwise(ends)):
        raise ValueError("exact tokenizer offsets are not monotone")
    index = AliasIndex(filenames)
    occurrences = []
    for hit in index.find(text):
        left, right = token_interval(hit["start"], hit["end"], starts, ends)
        occurrences.append(
            {
                "alias_id": hit["alias_id"],
                "labels": hit["labels"],
                "char_start": hit["start"],
                "char_end": hit["end"],
                "token_start": left,
                "token_end": right,
            }
        )
    return {
        "filenames": filenames,
        "patterns": index.patterns,
        "occurrences": occurrences,
    }


def source_text_control(visible: dict, filenames: list[str]) -> tuple[str, dict]:
    """A content-span control, not a standalone graph reader or training view.

    Drop record metadata but retain complete decoded source text in its current
    order. Replace standalone 40/64-hex identifiers with one space unless they
    overlap any filename alias, including non-answer diff/summary filenames.
    Thus a hexadecimal filename or an alias hidden within a hash is preserved.
    """
    text = "\n\n".join(record["text"] for record in visible["records"])
    protected_names = set(filenames)
    protected_names.update(re.findall(r"^diff -- (.+)$", text, re.MULTILINE))
    protected_names.update(
        re.findall(
            r"^(.+?) status=\S+ additions=\d+ deletions=\d+ changes=\d+ sha=\S+",
            text,
            re.MULTILINE,
        )
    )
    spans = sorted(
        (hit["start"], hit["end"])
        for hit in AliasIndex(sorted(protected_names)).find(text)
    )
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    protected_ends = [b for _, b in merged]
    parts, normalized, preserved, previous = [], [], [], 0
    pattern = r"(?<![0-9A-Za-z])[0-9a-fA-F]{64}(?![0-9A-Za-z])|(?<![0-9A-Za-z])[0-9a-fA-F]{40}(?![0-9A-Za-z])"
    for match in re.finditer(pattern, text):
        start, end = match.span()
        i = bisect.bisect_right(protected_ends, start)
        if i < len(merged) and merged[i][0] < end:
            preserved.append([start, end])
            continue
        parts.extend((text[previous:start], " "))
        normalized.append([start, end])
        previous = end
    parts.append(text[previous:])
    return "".join(parts), {
        "revision": CONTENT_CONTROL,
        "metadata_dropped": True,
        "standalone_task_solver": False,
        "protected_filename_count": len(protected_names),
        "normalized_sha_intervals_in_joined_source_text": normalized,
        "alias_overlapping_sha_intervals_preserved": preserved,
    }


def _record_spans(text: str, visible: dict) -> dict:
    from longworld.core.codeforge_taskbank import canonical

    spans = {}
    for record in visible["records"]:
        serialized = canonical(record)
        start = text.find(serialized)
        if start < 0 or text.find(serialized, start + 1) >= 0:
            raise ValueError("visible record serialization is not unique")
        spans[record["id"]] = (start, start + len(serialized))
    return spans


def _labels_in_spans(table: dict, spans: list[tuple[int, int]]) -> set[int]:
    ordered = sorted(spans)
    starts = [a for a, _ in ordered]
    labels = set()
    for hit in table["occurrences"]:
        i = bisect.bisect_right(starts, hit["char_start"]) - 1
        if i >= 0 and hit["char_end"] <= ordered[i][1]:
            labels.update(hit["labels"])
    return labels


def alias_copy_diagnostic(
    answer: list[str],
    query: str,
    table: dict,
    total_tokens: int,
    *,
    windows=(4096, 8192, 16384),
) -> dict:
    filename_to_label = {name: i for i, name in enumerate(table["filenames"])}
    target_labels = [filename_to_label[name] for name in answer]
    query_index = AliasIndex(table["filenames"])
    query_labels = {
        label
        for hit in query_index.find(query + "\n\nSource records:\n")
        for label in hit["labels"]
    }
    free = set(target_labels) & query_labels
    required = [label for label in target_labels if label not in free]
    grouped = defaultdict(set)
    for hit in table["occurrences"]:
        for label in hit["labels"]:
            grouped[label].add((hit["token_start"], hit["token_end"]))
    if any(not grouped[label] for label in target_labels):
        raise ValueError(
            "a full-reader answer has no enumerated visible filename alias"
        )
    groups = [sorted(grouped[label]) for label in required]
    minimum = minimum_cover(groups)
    for witness in minimum["witnesses"]:
        witness["filename"] = table["filenames"][required[witness.pop("label")]]
    baselines = {}
    for width in windows:
        result = window_coverage(groups, width, total_tokens)
        if result["all_labels_fit"] != (
            minimum["minimum_tokens"] <= min(width, total_tokens)
        ):
            raise ValueError("independent minimum/window sweep disagreement")
        present = free | {
            label
            for label in required
            if any(
                result["witness_start"] <= a and b <= result["witness_end"]
                for a, b in grouped[label]
            )
        }
        result.update(
            free_question_labels=len(free),
            total_answer_labels=len(answer),
            gold_labels_available=result["max_labels"] + len(free),
            missing_at_witness=[
                table["filenames"][label]
                for label in target_labels
                if label not in present
            ],
        )
        baselines[str(width)] = result
    return {
        "minimum_alias_cover": minimum,
        "window_tests": baselines,
        "free_labels": sorted(free),
        "target_labels": target_labels,
    }


def audit_file_task(
    text: str,
    task: dict,
    table: dict,
    total_tokens: int,
    *,
    windows=(4096, 8192, 16384),
) -> dict:
    visible = json.loads(text)
    reader = visible_file_reader(visible, task["program_id"])
    if reader["answer"] != task["oracle_answer"]:
        raise ValueError(
            "visible-only file aggregation disagrees with the frozen oracle"
        )
    answer = reader["answer"]
    copy = alias_copy_diagnostic(
        answer, task["query"], table, total_tokens, windows=windows
    )
    minimum, baselines = copy["minimum_alias_cover"], copy["window_tests"]
    free, target_labels = set(copy["free_labels"]), copy["target_labels"]
    spans = _record_spans(text, visible)
    latest = (
        _labels_in_spans(
            table, [spans[key] for key in reader["latest_scope_record_ids"]]
        )
        | free
    )
    heads = []
    for head in reader["heads"]:
        available = _labels_in_spans(table, [spans[head["head_id"]]]) | free
        heads.append(
            {
                "head_id": head["head_id"],
                "pull_request": head["pull_request"],
                "all_answer_aliases_available": set(target_labels) <= available,
                "exact_file_set_answer": head["files"] == answer,
            }
        )
    gates = {
        "visible_full_reader_replay": True,
        "alias_search_complete_within_grammar": True,
        "single_16k_copy_window_insufficient": minimum["minimum_tokens"] > max(windows),
        "all_active_sources_change_answer": reader["all_active_sources_change_answer"],
        "approval_condition_has_effect": reader["approval_changes_answer"] is not False,
    }
    qualified = all(gates.values())
    if qualified:
        classification = "scoped_long_input_file_aggregation_certificate"
    elif reader["approval_changes_answer"] is False:
        classification = "no_effect_approval_strict_hold"
    elif minimum["minimum_tokens"] <= max(windows):
        classification = "literal_copy_opportunity_retrieval_or_integration"
    else:
        classification = "redundant_source_strict_hold"
    return {
        "profile_id": PROFILE,
        "grammar_id": GRAMMAR,
        "program_id": task["program_id"],
        "answer_filename_count": len(answer),
        "free_question_filenames": [
            table["filenames"][label] for label in sorted(free)
        ],
        "minimum_alias_cover": minimum,
        "raw_token_window_tests": baselines,
        "whole_latest_episode_literal_copy_available": set(target_labels) <= latest,
        "latest_head_only_exact_answer": reader["latest_head_only_exact_answer"],
        "any_single_head_exact_answer": reader["any_single_head_exact_answer"],
        "head_local_literal_copy": heads,
        "remove_one_input": reader["remove_one_input"],
        "gates": gates,
        "classification": classification,
        "scoped_long_input_certificate": qualified,
        "strict_long_dependency_verified": False,
        "canonical_strict_production_eligible": False,
        "counterfactual_claimed": False,
    }
