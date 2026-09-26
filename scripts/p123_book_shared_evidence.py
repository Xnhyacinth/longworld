"""Audit exact printed-quote reuse across two tasks on frozen book worlds."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p112_book_tasks import SEP, _reader_sections
from scripts.p113_book_truth import audit_speeches

SCHEMA = "longworld.p123-book-shared-evidence.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def audit(named_dir: Path, intersection_dir: Path, selection_dir: Path) -> dict:
    named_manifest = json.loads((named_dir / "manifest.json").read_text())
    if named_manifest.get("train_ready") is not False or any(
        _sha(named_dir / name) != digest
        for name, digest in named_manifest["file_sha256"].items()
    ):
        raise ValueError("named source receipt differs")
    verify_merge(intersection_dir)
    named_index = _rows(named_dir / "sample_index.jsonl")
    named_proofs = _rows(named_dir / "proofs.jsonl")
    intersection_index = _rows(intersection_dir / "sample_index.jsonl")
    intersection_proofs = _rows(intersection_dir / "proofs.jsonl")
    named_by_id = {row["sample_id"]: row for row in named_index}
    intersection_by_id = {row["sample_id"]: row for row in intersection_index}
    if (
        len(named_by_id) != len(named_index)
        or len(named_index) != len(named_proofs)
        or len(intersection_by_id) != len(intersection_index)
        or len(intersection_by_id) != len(intersection_proofs)
        or {row["sample_id"] for row in named_proofs} != set(named_by_id)
        or {row["sample_id"] for row in intersection_proofs}
        != set(intersection_by_id)
    ):
        raise ValueError("book proof/index inventory differs")
    selection_manifest = json.loads((selection_dir / "manifest.json").read_text())
    if (
        selection_manifest.get("train_ready") is not False
        or selection_manifest.get("selected_refs_sha256")
        != _sha(selection_dir / "selected_refs.jsonl")
    ):
        raise ValueError("selection receipt differs")
    selected = _rows(selection_dir / "selected_refs.jsonl")
    selected_ids = {row["candidate"]["sample_id"] for row in selected}
    if len(selected_ids) != len(selected):
        raise ValueError("selected sample IDs repeat")
    readers = {}
    for split in ("train", "eval"):
        for row in _rows(intersection_dir / f"candidate_{split}.jsonl"):
            if row["sample_id"] in readers:
                raise ValueError("intersection reader repeats")
            readers[row["sample_id"]] = row
    if set(readers) != set(intersection_by_id):
        raise ValueError("intersection reader/index inventory differs")

    named_by_group = defaultdict(list)
    intersection_by_group = defaultdict(list)
    for proof in named_proofs:
        index = named_by_id[proof["sample_id"]]
        if proof["source_group"] != index["source_group"]:
            raise ValueError("named proof source differs")
        named_by_group[proof["source_group"]].append(proof)
    for proof in intersection_proofs:
        index = intersection_by_id[proof["sample_id"]]
        if proof["source_group"] != index["source_group"]:
            raise ValueError("intersection proof source differs")
        intersection_by_group[proof["source_group"]].append(proof)

    details = []
    for group in sorted(set(named_by_group) & set(intersection_by_group)):
        for right in intersection_by_group[group]:
            reader = readers[right["sample_id"]]
            sections = _reader_sections(reader["messages"][0]["content"].split(SEP, 1)[0])
            chapter_speeches = {
                chapter: audit_speeches(sections[chapter])
                for chapter in (right["source_chapter"], right["target_chapter"])
            }
            for left in named_by_group[group]:
                if left["speaker_label"] not in right["answer_names"]:
                    continue
                if (
                    named_by_id[left["sample_id"]]["split"]
                    != intersection_by_id[right["sample_id"]]["split"]
                ):
                    raise ValueError("shared book world crosses split")
                for chapter, quote, role in (
                    (left["source_section"], left["anchor"], "anchor"),
                    (left["target_section"], left["answer"], "answer"),
                ):
                    if chapter not in chapter_speeches:
                        continue
                    speeches = chapter_speeches[chapter]
                    if not any(
                        item.label == left["speaker_label"] and item.quote == quote
                        for item in speeches
                    ):
                        continue
                    support_count = sum(
                        item.label == left["speaker_label"] for item in speeches
                    )
                    details.append(
                        {
                            "source_group": group,
                            "split": named_by_id[left["sample_id"]]["split"],
                            "named_sample_id": left["sample_id"],
                            "intersection_sample_id": right["sample_id"],
                            "speaker_label": left["speaker_label"],
                            "chapter": chapter,
                            "named_quote_role": role,
                            "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                            "recognized_supports_in_chapter": support_count,
                            "both_selected": left["sample_id"] in selected_ids
                            and right["sample_id"] in selected_ids,
                        }
                    )

    def summary(eligible: set[str] | None) -> dict:
        groups = {
            group
            for group in set(named_by_group) & set(intersection_by_group)
            if eligible is None
            or (
                any(row["sample_id"] in eligible for row in named_by_group[group])
                and any(
                    row["sample_id"] in eligible
                    for row in intersection_by_group[group]
                )
            )
        }
        matched = [
            row for row in details if eligible is None or row["both_selected"]
        ]
        unique = [row for row in matched if row["recognized_supports_in_chapter"] == 1]
        return {
            "multi_operation_worlds": len(groups),
            "worlds_with_exact_shared_quote": len({row["source_group"] for row in matched}),
            "task_pairs_with_exact_shared_quote": len(
                {(row["named_sample_id"], row["intersection_sample_id"]) for row in matched}
            ),
            "worlds_with_single_recognized_support": len(
                {row["source_group"] for row in unique}
            ),
            "task_pairs_with_single_recognized_support": len(
                {(row["named_sample_id"], row["intersection_sample_id"]) for row in unique}
            ),
        }

    return {
        "schema": SCHEMA,
        "source_pins": {
            "named_manifest_sha256": _sha(named_dir / "manifest.json"),
            "named_index_sha256": _sha(named_dir / "sample_index.jsonl"),
            "named_proofs_sha256": _sha(named_dir / "proofs.jsonl"),
            "intersection_manifest_sha256": _sha(intersection_dir / "manifest.json"),
            "intersection_proofs_sha256": _sha(intersection_dir / "proofs.jsonl"),
            "selection_manifest_sha256": _sha(selection_dir / "manifest.json"),
            "selection_sha256": _sha(selection_dir / "selected_refs.jsonl"),
        },
        "all_candidates": summary(None),
        "selected": summary(selected_ids),
        "shared_quote_instances": details,
        "claim_limit": "exact parser-recognized printed quote reused by two task readers; single recognized support is necessary only under declared speaker grammar, not unrestricted semantic proof",
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--named-dir", type=Path, required=True)
    parser.add_argument("--intersection-dir", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = audit(args.named_dir, args.intersection_dir, args.selection)
    raw = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.verify_only:
        if args.output.read_text() != raw:
            raise ValueError("shared evidence report differs")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw)
    print(json.dumps({"all_candidates": result["all_candidates"], "selected": result["selected"]}))


if __name__ == "__main__":
    main()
