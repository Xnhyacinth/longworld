"""Compile bounded prose-revision tasks from hash-pinned frozen arXiv sources.

One worker handles one adjacent revision pair. The index records every pair,
including zero-yield pairs; admission uses the final reader, not archive size.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.p66_researchlab_taskbank import canonical, load_text_tar
from scripts.compile_p85_paper_revision_qa import BLOCK, _anchor, _prose_hunks
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p86-frozen-paper-batch.v1"
ARCHIVE = re.compile(r"arxiv-(\d{4}\.\d{4,5})v(\d+)\.source\.tar\Z")
WORD = re.compile(r"[A-Za-z]{3,}")
REFERENCE = re.compile(r"\\(?:ref|eqref|cite|citep|citet)\{[^}]*\}")


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pinned(pin: dict[str, str]) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe inventory path")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"source inventory pin changed: {relative}")
    return path


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _inventory(entry: dict[str, Any]) -> dict[str, Any]:
    path = _pinned(entry["inventory"])
    inventory = json.loads(path.read_text())
    if (
        inventory.get("source_status") != "public_api_export"
        or not inventory.get("authorization", {}).get("basis")
        or entry["split"] not in {"train", "eval"}
    ):
        raise ValueError("source provenance or split missing")
    signed_bundle_pinned = False
    if "signed_bundle" in entry:
        bundle_path = _pinned(entry["signed_bundle"])
        bundle = json.loads(bundle_path.read_text())
        entries = bundle.get("entries", [])
        if len(entries) != 1 or bundle.get("attestation", {}).get("role") != "source":
            raise ValueError("source bundle does not bind one source manifest")
        manifest_path = bundle_path.parent / entries[0]["path"]
        if (
            not manifest_path.is_file()
            or _sha(manifest_path) != entries[0]["sha256"]
            or json.loads(manifest_path.read_text()).get("fetch_inventory_sha256")
            != entry["inventory"]["sha256"]
        ):
            raise ValueError("source bundle does not bind the pinned fetch inventory")
        signed_bundle_pinned = True
    sources = []
    work_ids = set()
    for record in inventory.get("records", []):
        filename = record.get("source_archive_file", "")
        match = ARCHIVE.fullmatch(filename)
        if match is None:
            continue
        work_ids.add(match.group(1))
        archive = path.parent / filename
        if (
            not archive.is_file()
            or _sha(archive) != record.get("source_archive_sha256")
            or record.get("work_id") != "arxiv:" + match.group(1)
            or record.get("revision_id") != "v" + match.group(2)
        ):
            raise ValueError(f"archive provenance changed: {archive}")
        sources.append(
            {
                "version": "v" + match.group(2),
                "path": str(archive.relative_to(ROOT)),
                "sha256": record["source_archive_sha256"],
            }
        )
    if len(work_ids) != 1 or len(sources) < 2:
        raise ValueError("inventory must contain one paper with two revisions")
    if len({row["version"] for row in sources}) != len(sources):
        raise ValueError("duplicate version in source inventory")
    sources.sort(key=lambda row: int(row["version"][1:]))
    return {
        "family_id": entry["family_id"],
        "work_id": next(iter(work_ids)),
        "split": entry["split"],
        "inventory": entry["inventory"],
        "sources": sources,
        "signed_bundle_pinned": signed_bundle_pinned,
        "signature_cryptographically_verified": False,
    }


def _dedupe(files: dict[str, str]) -> tuple[dict[str, str], int]:
    """Drop identical same-name copies; preserve legitimately different paths."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for path, text in files.items():
        groups[(Path(path).name, hashlib.sha256(text.encode()).hexdigest())].append(
            path
        )
    retained = {}
    for paths in groups.values():
        preferred = min(
            paths, key=lambda path: ("copy" in path.casefold(), len(path), path)
        )
        retained[preferred] = files[preferred]
    return retained, len(files) - len(retained)


def _quality_hunks(old: str, new: str, min_chars: int) -> list[tuple[str, str, float]]:
    good = []
    for before, after, math_changed, difference in _prose_hunks(old, new, min_chars):
        before_without_refs = " ".join(REFERENCE.sub("", before).split())
        after_without_refs = " ".join(REFERENCE.sub("", after).split())
        if (
            len(WORD.findall(before)) < 18
            or len(WORD.findall(after)) < 18
            or before.startswith("\\")
            or after.startswith("\\")
            or "\\caption" in before + after
            or "\\begin{" in before + after
            or before_without_refs == after_without_refs
            or difference < 0.03
            or difference > 0.8
        ):
            continue
        score = difference + (0.08 if math_changed else 0)
        good.append((before, after, score))
    return sorted(good, key=lambda item: (-item[2], -len(item[0]), item[0]))


def _solve(
    context: str, selectors: dict[str, str], old_version: str, new_version: str
) -> dict[str, dict[str, str]] | None:
    records = {}
    for match in BLOCK.finditer(context):
        key = (match.group(1), match.group(2))
        if key in records:
            return None
        records[key] = match.group(3)
    answer = {}
    for path, prefix in selectors.items():
        old = records.get((old_version, path))
        new = records.get((new_version, path))
        if old is None or new is None:
            return None
        matches = [
            (before, after)
            for before, after, _ in _quality_hunks(old, new, 120)
            if before.startswith(prefix)
        ]
        if len(matches) != 1:
            return None
        answer[path] = {old_version: matches[0][0], new_version: matches[0][1]}
    return dict(sorted(answer.items()))


def _render(
    old_version: str,
    new_version: str,
    target: list[str],
    filler: list[str],
    old: dict[str, str],
    new: dict[str, str],
) -> str:
    parts = []
    for version, files, paths in (
        (old_version, old, target),
        (old_version, old, filler),
        (new_version, new, filler),
        (new_version, new, target),
    ):
        for path in paths:
            parts.append(
                f"<<< COMPLETE_SOURCE_RECORD version={version} path={path} >>>\n"
                f"{files[path]}\n<<< END_COMPLETE_SOURCE_RECORD >>>\n"
            )
    return "".join(parts)


def _tokenizer(config: dict[str, Any]):
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            config["tokenizer"]["model_id"],
            revision=config["tokenizer"]["revision"],
            local_files_only=True,
            trust_remote_code=False,
        )


def _candidate(spec: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    old_source, new_source = spec["sources"]
    old, old_duplicates = _dedupe(load_text_tar(ROOT / old_source["path"]))
    new, new_duplicates = _dedupe(load_text_tar(ROOT / new_source["path"]))
    shared = sorted(old.keys() & new.keys())
    changed = [path for path in shared if old[path] != new[path]]
    viable = []
    for path in changed:
        hunks = _quality_hunks(old[path], new[path], config["min_prose_chars"])
        if hunks:
            viable.append((path, hunks))
    capacity = {
        "family_id": spec["family_id"],
        "work_id": spec["work_id"],
        "split": spec["split"],
        "old_version": old_source["version"],
        "new_version": new_source["version"],
        "source_archives": spec["sources"],
        "canonical_shared_files": len(shared),
        "changed_files": len(changed),
        "quality_prose_files": len(viable),
        "duplicate_paths_removed": old_duplicates + new_duplicates,
        "source_char_capacity": sum(len(old[path]) + len(new[path]) for path in shared),
    }
    if not viable:
        return {"capacity": capacity, "reject": "no_quality_prose_hunk"}
    viable.sort(key=lambda item: (-(len(old[item[0]]) + len(new[item[0]])), item[0]))
    tokenizer = _tokenizer(config)
    old_version, new_version = old_source["version"], new_source["version"]
    failures = []
    for start in range(min(len(viable), config["max_path_trials"])):
        target = [item[0] for item in viable[start : start + 2]]
        if not target:
            continue
        selected = {
            path: next(hunks for name, hunks in viable if name == path)[0]
            for path in target
        }
        selectors = {path: _anchor(hunk[0]) for path, hunk in selected.items()}
        filler_pool = [path for path in shared if path not in target]
        filler_pool.sort(key=lambda path: (-(len(old[path]) + len(new[path])), path))
        filler = []
        for attempt in range(len(filler_pool) + 1):
            context = _render(old_version, new_version, target, filler, old, new)
            question = (
                f"Compare the {old_version} and {new_version} source records. "
                "For each named file, find the changed prose line beginning with the "
                "given words. Return the complete before and after lines as a JSON "
                f"object keyed by file and version. Selectors: {canonical(selectors)}"
            )
            expected = {
                path: {old_version: hunk[0], new_version: hunk[1]}
                for path, hunk in selected.items()
            }
            visible = _solve(context, selectors, old_version, new_version)
            if visible != expected:
                failures.append("reader_replay_or_ambiguous_selector")
                break
            if any(
                context.count(line) != 1
                for pair in expected.values()
                for line in pair.values()
            ):
                failures.append("alternate_exact_support")
                break
            answer = canonical(expected)
            messages = [
                {"role": "user", "content": context + "\n\nQUESTION\n" + question},
                {"role": "assistant", "content": answer},
            ]
            try:
                encoded = tokenize_assistant_only(
                    tokenizer, messages, config["max_final_tokens"]
                )
            except ValueError:
                failures.append("over_max_tokens")
                break
            total = len(encoded["input_ids"])
            if total < config["min_final_tokens"]:
                if attempt < len(filler_pool):
                    filler.append(filler_pool[attempt])
                    continue
                failures.append("under_min_tokens")
                break
            full_chat = _render_chat(tokenizer, messages, generation_prompt=False)
            context_at = full_chat.find(context)
            if context_at < 0 or full_chat.find(context, context_at + 1) >= 0:
                failures.append("context_serialization_ambiguous")
                break
            final_offsets = tokenizer(full_chat, return_offsets_mapping=True)[
                "offset_mapping"
            ]
            if len(final_offsets) != total:
                failures.append("token_offset_mismatch")
                break
            spans = []
            for path, pair in sorted(expected.items()):
                for version, line in sorted(pair.items()):
                    start = context.index(line)
                    end = start + len(line)
                    absolute_start, absolute_end = context_at + start, context_at + end
                    final_start = next(
                        index
                        for index, (_, token_end) in enumerate(final_offsets)
                        if token_end > absolute_start
                    )
                    final_end = next(
                        (
                            index
                            for index, (token_start, _) in enumerate(final_offsets)
                            if token_start >= absolute_end
                        ),
                        len(final_offsets),
                    )
                    spans.append(
                        {
                            "path": path,
                            "version": version,
                            "start": start,
                            "end": end,
                            "final_start_token": final_start,
                            "final_end_token": final_end,
                        }
                    )
            extent = max(span["final_end_token"] for span in spans) - min(
                span["final_start_token"] for span in spans
            )
            if extent < config["min_evidence_extent_tokens"]:
                failures.append("under_evidence_extent")
                break
            interventions = []
            for span in spans:
                masked = (
                    context[: span["start"]]
                    + "?" * (span["end"] - span["start"])
                    + context[span["end"] :]
                )
                if _solve(masked, selectors, old_version, new_version) is not None:
                    failures.append("line_deletion_failed")
                    break
                interventions.append(f"{span['version']}:{span['path']}")
            if len(interventions) != len(spans):
                break
            record_interventions = []
            for match in BLOCK.finditer(context):
                if match.group(2) not in target:
                    continue
                masked = (
                    context[: match.start()]
                    + "?" * (match.end() - match.start())
                    + context[match.end() :]
                )
                if _solve(masked, selectors, old_version, new_version) is not None:
                    failures.append("record_deletion_failed")
                    break
                record_interventions.append(f"{match.group(1)}:{match.group(2)}")
            if len(record_interventions) != len(spans):
                break
            supervised = sum(label != -100 for label in encoded["labels"])
            if supervised < 1 or supervised >= total:
                failures.append("invalid_mask")
                break
            task_id = (
                "p86-paper-"
                + hashlib.sha256(
                    canonical(
                        [
                            spec["work_id"],
                            old_version,
                            new_version,
                            selectors,
                            [source["sha256"] for source in spec["sources"]],
                        ]
                    ).encode()
                ).hexdigest()[:20]
            )
            return {
                "capacity": capacity,
                "row": {"sample_id": task_id, "messages": messages},
                "index": {
                    "sample_id": task_id,
                    "semantic_task_id": task_id,
                    "source_group": "researchlab:arxiv:" + spec["work_id"],
                    "split": spec["split"],
                    "domain": "researchlab",
                    "topic": "paper_revision",
                    "operation": "cross_file_revision_alignment"
                    if len(target) == 2
                    else "revision_alignment",
                    "full_chat_tokens": total,
                    "input_tokens": total - supervised,
                    "supervised_tokens": supervised,
                    "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                    "train_ready": False,
                },
                "audit": {
                    "sample_id": task_id,
                    "source_archives": spec["sources"],
                    "target_files": target,
                    "filler_files": filler,
                    "selectors": selectors,
                    "answer": expected,
                    "evidence_spans": spans,
                    "bounded_evidence_extent_tokens": extent,
                    "line_deletion_checks": interventions,
                    "record_deletion_checks": record_interventions,
                    "reader_replay": True,
                    "mask_checked": True,
                    "question_style": "explicit_revision_alignment",
                },
            }
    return {
        "capacity": capacity,
        "reject": failures[0] if failures else "no_candidate",
        "trial_reasons": failures,
    }


def build(config_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or not 1 <= config.get("workers", 0) <= 8:
        raise ValueError("invalid batch configuration")
    families = [_inventory(item) for item in config["families"]]
    if len({family["work_id"] for family in families}) != len(families):
        raise ValueError("duplicate paper work across families")
    specs = []
    for family in families:
        for preceding, following in pairwise(family["sources"]):
            if int(following["version"][1:]) != int(preceding["version"][1:]) + 1:
                continue
            specs.append({**family, "sources": [preceding, following]})
    with ProcessPoolExecutor(max_workers=config["workers"]) as executor:
        results = list(executor.map(_candidate, specs, [config] * len(specs)))
    prior_pairs = set()
    prior_answers = set()
    for pin in config.get("prior_task_audits", []):
        for line in _pinned(pin).read_text().splitlines():
            audit = json.loads(line)
            sources = audit.get("source_archives", [])
            matches = [
                ARCHIVE.fullmatch(Path(source["path"]).name) for source in sources
            ]
            if len(matches) != 2 or any(match is None for match in matches):
                raise ValueError("prior task has invalid source archive identity")
            work_ids = {match.group(1) for match in matches if match is not None}
            if len(work_ids) != 1:
                raise ValueError("prior task crosses source works")
            prior_pairs.add(
                (
                    next(iter(work_ids)),
                    tuple(sorted(source["version"] for source in sources)),
                )
            )
            prior_answers.add(
                hashlib.sha256(canonical(audit["answer"]).encode()).hexdigest()
            )
    overlap_pairs = 0
    overlap_answers = 0
    for result in results:
        if "row" not in result:
            continue
        capacity = result["capacity"]
        pair = (capacity["work_id"], (capacity["old_version"], capacity["new_version"]))
        answer_digest = hashlib.sha256(
            canonical(result["audit"]["answer"]).encode()
        ).hexdigest()
        overlap_pairs += pair in prior_pairs
        overlap_answers += answer_digest in prior_answers
        if pair in prior_pairs or answer_digest in prior_answers:
            result.clear()
            result.update({"capacity": capacity, "reject": "prior_task_overlap"})
    output_dir.mkdir(parents=True)
    for name, key in (
        ("capacity_index.jsonl", "capacity"),
        ("rejected.jsonl", "reject"),
        ("sample_index.jsonl", "index"),
        ("audit.jsonl", "audit"),
    ):
        entries = []
        for result in results:
            if key == "reject" and key in result:
                entries.append(
                    {
                        **result["capacity"],
                        "reason": result["reject"],
                        "trial_reasons": result.get("trial_reasons", []),
                    }
                )
            elif key in result and key != "reject":
                entries.append(result[key])
        (output_dir / name).write_text(
            "".join(canonical(row) + "\n" for row in entries)
        )
    for split in ("train", "eval"):
        rows = [
            result["row"]
            for result in results
            if "row" in result and result["index"]["split"] == split
        ]
        (output_dir / f"{split}.jsonl").write_text(
            "".join(canonical(row) + "\n" for row in rows)
        )
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "compiler_sha256": _sha(Path(__file__)),
        "source_works": len(families),
        "source_revision_pairs": len(specs),
        "raw_pairs_with_quality_prose": sum(
            result["capacity"]["quality_prose_files"] > 0 for result in results
        ),
        "quality_admitted_tasks": sum("row" in result for result in results),
        "reader_dependency_checked_tasks": sum("row" in result for result in results),
        "train_tasks": sum(
            "row" in result and result["index"]["split"] == "train"
            for result in results
        ),
        "eval_tasks": sum(
            "row" in result and result["index"]["split"] == "eval" for result in results
        ),
        "rejected_pairs": sum("reject" in result for result in results),
        "prior_source_pair_overlaps": overlap_pairs,
        "prior_exact_answer_overlaps": overlap_answers,
        "per_family": {
            family["family_id"]: {
                "revision_pairs": sum(
                    result["capacity"]["family_id"] == family["family_id"]
                    for result in results
                ),
                "admitted_tasks": sum(
                    result["capacity"]["family_id"] == family["family_id"]
                    and "row" in result
                    for result in results
                ),
                "rejected_pairs": sum(
                    result["capacity"]["family_id"] == family["family_id"]
                    and "reject" in result
                    for result in results
                ),
            }
            for family in families
        },
        "final_length_bins": {
            "under_32k": sum(
                "row" in result and result["index"]["full_chat_tokens"] < 32768
                for result in results
            ),
            "32k_to_64k": sum(
                "row" in result and 32768 <= result["index"]["full_chat_tokens"] < 65536
                for result in results
            ),
            "64k_to_128k": sum(
                "row" in result
                and 65536 <= result["index"]["full_chat_tokens"] < 131072
                for result in results
            ),
        },
        "reject_reasons": {
            reason: sum(result.get("reject") == reason for result in results)
            for reason in sorted(
                {result["reject"] for result in results if "reject" in result}
            )
        },
        "source_families": [
            {
                key: family[key]
                for key in (
                    "family_id",
                    "work_id",
                    "split",
                    "inventory",
                    "sources",
                    "signed_bundle_pinned",
                    "signature_cryptographically_verified",
                )
            }
            for family in families
        ],
        "files_sha256": {
            name: _sha(output_dir / name)
            for name in (
                "capacity_index.jsonl",
                "rejected.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
                "train.jsonl",
                "eval.jsonl",
            )
        },
        "train_ready": False,
        "question_style": "explicit_revision_alignment",
        "reader_dependency_scope": "unique exact answer lines in final reader plus target line/record deletion under bounded parser",
        "claim_limit": "exact-line uniqueness, bounded visible replay and line deletion; no global semantic-proof or training-gain claim",
    }
    _write(output_dir / "manifest.json", manifest)
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
