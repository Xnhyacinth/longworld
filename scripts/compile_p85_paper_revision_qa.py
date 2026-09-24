"""Compile one bounded, source-pinned prose revision QA from paper tar files.

The compiler deduplicates identical copied files by basename, selects changed
prose lines in distinct files, and executes the visible old/new record diff.
It admits a task only when all four answer lines are unique in the reader,
the final chat exceeds 32K, and masking each source record or answer line
breaks the answer. This is a bounded text-dependency check, not model proof.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.p66_researchlab_taskbank import (
    _meaningful_lines,
    canonical,
    load_text_tar,
    render_records,
)
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p85-paper-revision-qa.v1"
BLOCK = re.compile(
    r"<<< COMPLETE_SOURCE_RECORD version=(\S+) path=(.*?) >>>\n"
    r"(.*?)\n<<< END_COMPLETE_SOURCE_RECORD >>>\n",
    re.DOTALL,
)
MATH = re.compile(r"\$([^$]+)\$")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe source path")
    return ROOT / relative


def _canonical_files(files: dict[str, str]) -> tuple[dict[str, str], int]:
    by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path, text in files.items():
        by_name[Path(path).name].append((path, text))
    canonical_files = {}
    duplicates = 0
    for name, versions in sorted(by_name.items()):
        if len({text for _, text in versions}) != 1:
            raise ValueError(f"same-basename source files disagree: {name}")
        versions.sort(
            key=lambda row: ("copy" in row[0].casefold(), len(row[0]), row[0])
        )
        canonical_files[name] = versions[0][1]
        duplicates += len(versions) - 1
    return canonical_files, duplicates


def _prose_hunks(
    old: str, new: str, min_chars: int
) -> list[tuple[str, str, bool, float]]:
    before, after = _meaningful_lines(old), _meaningful_lines(new)
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=before, b=after, autojunk=False
    ).get_opcodes():
        if tag != "replace":
            continue
        old_lines = [
            line
            for line in before[i1:i2]
            if min_chars <= len(line) <= 800 and not line.startswith("\\")
        ]
        new_lines = [
            line
            for line in after[j1:j2]
            if min_chars <= len(line) <= 800 and not line.startswith("\\")
        ]
        if len(old_lines) != 1 or len(new_lines) != 1:
            continue
        old_line, new_line = old_lines[0], new_lines[0]
        if old_line == new_line:
            continue
        math_changed = MATH.findall(old_line) != MATH.findall(new_line)
        difference = 1 - difflib.SequenceMatcher(a=old_line, b=new_line).ratio()
        hunks.append((old_line, new_line, math_changed, difference))
    return hunks


def _choose_hunk(old: str, new: str, min_chars: int) -> tuple[str, str] | None:
    hunks = _prose_hunks(old, new, min_chars)
    if not hunks:
        return None
    selected = max(hunks, key=lambda item: (item[2], item[3], len(item[0])))
    return selected[0], selected[1]


def _anchor(line: str) -> str:
    return " ".join(line.split()[:11])


def solve(
    context: str, selectors: dict[str, str], old_version: str, new_version: str
) -> dict[str, dict[str, str]] | None:
    """Execute line alignment on only the four visible reader records."""
    records: dict[tuple[str, str], str] = {}
    for match in BLOCK.finditer(context):
        key = (match.group(1), match.group(2))
        if key in records:
            return None
        records[key] = match.group(3)
    if len(records) != len(selectors) * 2:
        return None
    answer = {}
    for path, prefix in selectors.items():
        old = records.get((old_version, path))
        new = records.get((new_version, path))
        if old is None or new is None:
            return None
        matches = [
            (before, after)
            for before, after, _, _ in _prose_hunks(old, new, 120)
            if before.startswith(prefix)
        ]
        if len(matches) != 1 or not matches[0][1].strip():
            return None
        answer[path] = {old_version: matches[0][0], new_version: matches[0][1]}
    return dict(sorted(answer.items()))


def _question(selectors: dict[str, str], old_version: str, new_version: str) -> str:
    items = "; ".join(
        f"{path}: {prefix!r}" for path, prefix in sorted(selectors.items())
    )
    return (
        f"Compare the complete {old_version} and {new_version} source records. "
        "For each file below, find the changed prose line whose old-version text "
        "begins with the given words. Return the complete old and new lines as "
        f"a JSON object keyed by file, then by {old_version} and {new_version}. "
        f"Files and old-line beginnings: {items}"
    )


def build(config_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong P85 compiler schema")
    route_path = _path(config["source_route"]["path"])
    if _sha(route_path) != config["source_route"]["sha256"]:
        raise ValueError("source route pin changed")
    route = json.loads(route_path.read_text())
    native_path = route_path.parent / "native_config.json"
    if _sha(native_path) != route["native_config_sha256"]:
        raise ValueError("native source config changed")
    native = json.loads(native_path.read_text())
    family = next(
        (
            item
            for item in native["families"]
            if item["family_id"] == config["family_id"]
        ),
        None,
    )
    if family is None or family["split"] != "train":
        raise ValueError("source family is not a pinned train source")
    versions = {}
    for source in family["sources"]:
        path = _path(source["path"])
        if _sha(path) != source["sha256"]:
            raise ValueError("source archive pin changed")
        versions[source["version"]] = _canonical_files(load_text_tar(path))
    old_version, new_version = config["version_pair"]
    if (
        int(new_version[1:]) != int(old_version[1:]) + 1
        or old_version not in versions
        or new_version not in versions
    ):
        raise ValueError("version pair must be adjacent and pinned")
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            native["tokenizer"]["model_id"],
            revision=native["tokenizer"]["revision"],
            local_files_only=True,
            trust_remote_code=False,
        )
    token_cache: dict[str, int] = {}

    def count_tokens(text: str) -> int:
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest not in token_cache:
            token_cache[digest] = len(
                tokenizer(text, add_special_tokens=False)["input_ids"]
            )
        return token_cache[digest]

    pair_capacities = []
    ordered_versions = sorted(versions, key=lambda value: int(value[1:]))
    for preceding, following in pairwise(ordered_versions):
        preceding_files, preceding_duplicates = versions[preceding]
        following_files, following_duplicates = versions[following]
        changed = sorted(
            path
            for path in preceding_files.keys() & following_files.keys()
            if preceding_files[path] != following_files[path]
        )
        weights = sorted(
            (
                count_tokens(preceding_files[path])
                + count_tokens(following_files[path])
                for path in changed
            ),
            reverse=True,
        )
        pair_capacities.append(
            {
                "old_version": preceding,
                "new_version": following,
                "changed_unique_files": len(changed),
                "duplicate_paths_removed": preceding_duplicates + following_duplicates,
                "largest_two_changed_file_tokens": sum(weights[:2]),
                "all_changed_file_tokens": sum(weights),
            }
        )
    old_files, _ = versions[old_version]
    new_files, _ = versions[new_version]
    selected = []
    capacity = []
    for path in sorted(old_files.keys() & new_files.keys()):
        old, new = old_files[path], new_files[path]
        if old == new:
            continue
        weight = count_tokens(old) + count_tokens(new)
        capacity.append(
            {
                "path": path,
                "old_new_tokens": weight,
                "has_prose_hunk": bool(
                    _prose_hunks(old, new, config["min_prose_chars"])
                ),
            }
        )
        hunk = _choose_hunk(old, new, config["min_prose_chars"])
        if hunk is not None:
            selected.append((path, old, new, hunk))
    if len(selected) != 2:
        raise ValueError("version pair lacks exactly two distinct prose-changing files")
    selected.sort(key=lambda item: item[0])
    context, records = render_records(
        old_version, new_version, [(path, old, new) for path, old, new, _ in selected]
    )
    selectors = {path: _anchor(hunk[0]) for path, _, _, hunk in selected}
    answer = solve(context, selectors, old_version, new_version)
    expected = {
        path: {old_version: hunk[0], new_version: hunk[1]}
        for path, _, _, hunk in selected
    }
    if answer != expected:
        raise ValueError("visible reader solver disagrees with selected source hunks")
    spans = []
    for path, _, _, (old_line, new_line) in selected:
        for version, line in ((old_version, old_line), (new_version, new_line)):
            if context.count(line) != 1:
                raise ValueError("answer line has alternate visible support")
            start = context.index(line)
            spans.append(
                {
                    "path": path,
                    "version": version,
                    "start": start,
                    "end": start + len(line),
                }
            )
    interventions = []
    for key, (start, end) in sorted(records.items()):
        masked = context[:start] + "?" * (end - start) + context[end:]
        if solve(masked, selectors, old_version, new_version) is not None:
            raise ValueError("record deletion did not break visible solver")
        interventions.append(
            {
                "kind": "record",
                "record_id": key,
                "masked_sha256": hashlib.sha256(masked.encode()).hexdigest(),
            }
        )
    for span in spans:
        masked = (
            context[: span["start"]]
            + "?" * (span["end"] - span["start"])
            + context[span["end"] :]
        )
        if solve(masked, selectors, old_version, new_version) is not None:
            raise ValueError("line deletion did not break visible solver")
        interventions.append(
            {
                "kind": "line",
                "record_id": f"{span['version']}:{span['path']}",
                "masked_sha256": hashlib.sha256(masked.encode()).hexdigest(),
            }
        )
    for span in spans:
        span["start_token"] = len(
            tokenizer(context[: span["start"]], add_special_tokens=False)["input_ids"]
        )
        span["end_token"] = len(
            tokenizer(context[: span["end"]], add_special_tokens=False)["input_ids"]
        )
    evidence_extent = max(span["end_token"] for span in spans) - min(
        span["start_token"] for span in spans
    )
    question = _question(selectors, old_version, new_version)
    messages = [
        {"role": "user", "content": context + "\n\nQUESTION\n" + question},
        {"role": "assistant", "content": canonical(answer)},
    ]
    encoded = tokenize_assistant_only(tokenizer, messages, config["max_final_tokens"])
    full_tokens = len(encoded["input_ids"])
    if (
        full_tokens < config["min_final_tokens"]
        or evidence_extent < config["min_evidence_extent_tokens"]
    ):
        raise ValueError("physical or evidence-token extent below configured minimum")
    task_id = (
        "paper-revision-"
        + hashlib.sha256(
            canonical(
                [
                    config["family_id"],
                    old_version,
                    new_version,
                    selectors,
                    [
                        source["sha256"]
                        for source in family["sources"]
                        if source["version"] in (old_version, new_version)
                    ],
                ]
            ).encode()
        ).hexdigest()[:20]
    )
    row = {"sample_id": task_id, "messages": messages}
    output_dir.mkdir(parents=True)
    (output_dir / "train.jsonl").write_text(canonical(row) + "\n")
    (output_dir / "sample_index.jsonl").write_text(
        canonical(
            {
                "sample_id": task_id,
                "semantic_task_id": task_id,
                "source_group": "researchlab:" + config["family_id"],
                "split": "train",
                "domain": "researchlab",
                "topic": "paper_revision",
                "operation": "cross_file_revision_alignment",
                "input_tokens": full_tokens
                - sum(label != -100 for label in encoded["labels"]),
                "supervised_tokens": sum(label != -100 for label in encoded["labels"]),
                "full_chat_tokens": full_tokens,
                "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                "train_ready": False,
            }
        )
        + "\n"
    )
    (output_dir / "audit.jsonl").write_text(
        canonical(
            {
                "sample_id": task_id,
                "source_route_sha256": _sha(route_path),
                "source_archives": [
                    source
                    for source in family["sources"]
                    if source["version"] in (old_version, new_version)
                ],
                "selected_files": [path for path, _, _, _ in selected],
                "selectors": selectors,
                "answer": answer,
                "evidence_spans": spans,
                "bounded_evidence_extent_tokens": evidence_extent,
                "minimum_unique_support_span_tokens": evidence_extent,
                "interventions": interventions,
                "reader_replay_answer": solve(
                    context, selectors, old_version, new_version
                ),
                "assistant_only_masked_prefix_tokens": full_tokens
                - sum(label != -100 for label in encoded["labels"]),
            }
        )
        + "\n"
    )
    (output_dir / "capacity_report.json").write_text(
        json.dumps(
            {
                "canonical_file_counts": {
                    version: len(files) for version, (files, _) in versions.items()
                },
                "duplicate_paths_removed": {
                    version: duplicate for version, (_, duplicate) in versions.items()
                },
                "adjacent_pair_capacities": pair_capacities,
                "selected_pair": [old_version, new_version],
                "changed_canonical_files": capacity,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "compiler_sha256": _sha(Path(__file__)),
        "source_route_sha256": _sha(route_path),
        "native_config_sha256": _sha(native_path),
        "source_family": config["family_id"],
        "source_versions": [old_version, new_version],
        "independent_tasks": 1,
        "train_rows": 1,
        "reader_replay": True,
        "full_chat_tokens": full_tokens,
        "supervised_tokens": sum(label != -100 for label in encoded["labels"]),
        "bounded_evidence_extent_tokens": evidence_extent,
        "minimum_unique_support_span_tokens": evidence_extent,
        "intervention_checks": len(interventions),
        "files_sha256": {
            name: _sha(output_dir / name)
            for name in (
                "train.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
                "capacity_report.json",
            )
        },
        "train_ready": False,
        "claim_limit": "unique exact lines and four-record/line deletion within the composed reader; no global semantic shortcut or model-learning proof",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.config, args.output_dir), ensure_ascii=False, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
