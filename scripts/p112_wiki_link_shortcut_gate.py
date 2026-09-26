"""Exclude linked-Wiki readers whose list label identifies the target without its link."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from longworld.synthesis.unified_candidate_merge import verify_merge

HEADER = re.compile(r"^=== DOCUMENT: (.+?) \(revision \d+\) ===$", re.MULTILINE)
SCHEMA = "longworld.p112-wiki-link-shortcut-gate.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _target_body(context: str, title: str) -> str:
    matches = list(HEADER.finditer(context))
    found = [
        context[
            match.end() : matches[index + 1].start()
            if index + 1 < len(matches)
            else len(context)
        ]
        for index, match in enumerate(matches)
        if match.group(1) == title
    ]
    if len(found) != 1:
        raise ValueError("linked target document is not unique in the final reader")
    return found[0]


def _shortcut(context: str, row_name: str, target_title: str) -> str | None:
    name = _key(row_name)
    title = _key(target_title)
    if not name or not title:
        raise ValueError("linked target or row label is empty")
    if len(name) >= 6 and name in title:
        return "row_label_identifies_target_title"
    body = _target_body(context, target_title)
    if row_name.casefold() in body.casefold():
        return "row_label_visible_in_target_body"
    return None


def run(
    source_dir: Path,
    native_dir: Path,
    target_manifest: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    source = verify_merge(source_dir)
    native = json.loads((native_dir / "manifest.json").read_text())
    targets = json.loads(target_manifest.read_text())
    if (
        source.get("native_manifest_sha256") != _sha(native_dir / "manifest.json")
        or native.get("target_manifest_sha256") != _sha(target_manifest)
        or source.get("candidate_views") != native.get("reader_tasks")
    ):
        raise ValueError("linked Wiki source/native/target binding differs")
    target_rows = {
        (row["source_group"], row["target_title"]): row
        for row in targets["records"]
        if row["status"] == "frozen"
    }
    audit = {row["sample_id"]: row for row in _rows(native_dir / "audit.jsonl")}
    indices = _rows(source_dir / "sample_index.jsonl")
    readers = {
        split: iter(_rows(source_dir / f"candidate_{split}.jsonl"))
        for split in ("train", "eval")
    }
    if len(audit) != source["candidate_views"]:
        raise ValueError("linked Wiki native audit inventory differs")
    kept: dict[str, list[dict]] = {"train": [], "eval": []}
    kept_index = []
    decisions = []
    counts: Counter[str] = Counter()
    for index in indices:
        split = index["split"]
        reader = next(readers[split], None)
        sample_id = index["sample_id"]
        proof = audit.get(sample_id)
        if reader is None or reader["sample_id"] != sample_id or proof is None:
            raise ValueError("linked Wiki reader/index/audit alignment differs")
        target_title = proof["first_target"]
        target = target_rows.get((index["source_group"], target_title))
        if target is None:
            raise ValueError("linked Wiki frozen target identity is missing")
        context = reader["messages"][0]["content"]
        reason = _shortcut(context, target["row_name"], target_title)
        decisions.append(
            {
                "sample_id": sample_id,
                "source_group": index["source_group"],
                "target_title": target_title,
                "row_name": target["row_name"],
                "status": "accepted" if reason is None else "rejected",
                "reason": reason,
            }
        )
        counts[reason or "accepted"] += 1
        if reason is None:
            row = dict(index)
            row["output_file"] = f"candidate_{split}.jsonl"
            row["row_index"] = len(kept[split])
            row["dependency_status"] = "linked_article_primary_label_shortcut_screened"
            kept[split].append(reader)
            kept_index.append(row)
    if any(next(stream, None) is not None for stream in readers.values()):
        raise ValueError("linked Wiki reader inventory has trailing rows")
    if not kept_index:
        raise ValueError("no linked Wiki reader survived title shortcut screen")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="wiki_link_strict_", dir=output.parent
    ) as tmp:
        stage = Path(tmp)
        payloads = {
            "candidate_train.jsonl": kept["train"],
            "candidate_eval.jsonl": kept["eval"],
            "sample_index.jsonl": kept_index,
            "decisions.jsonl": decisions,
        }
        for name, rows in payloads.items():
            (stage / name).write_text("".join(_dump(row) + "\n" for row in rows))
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "quality_gate_schema": SCHEMA,
            "candidate_views": len(kept_index),
            "independent_semantic_tasks": len(
                {row["semantic_task_id"] for row in kept_index}
            ),
            "source_scoped_semantic_tasks": len(kept_index),
            "views_by_lane": {"p112_wiki_link_shortcut_screened": len(kept_index)},
            "splits": {split: len(rows) for split, rows in kept.items() if rows},
            "source_unified_manifest_sha256": _sha(source_dir / "manifest.json"),
            "source_native_manifest_sha256": _sha(native_dir / "manifest.json"),
            "target_manifest_sha256": _sha(target_manifest),
            "rejections": dict(sorted(counts.items())),
            "files_sha256": {name: _sha(stage / name) for name in payloads},
            "claim_limit": "lexical title/body alias screen for primary linked target, not an exhaustive shortcut proof",
            "train_ready": False,
        }
        (stage / "manifest.json").write_text(_dump(manifest) + "\n")
        if verify_only:
            verify_merge(output)
            if any(
                not (output / path.name).is_file()
                or (output / path.name).read_bytes() != path.read_bytes()
                for path in stage.iterdir()
            ):
                raise ValueError("linked Wiki shortcut gate byte replay differs")
        else:
            if output.exists():
                raise ValueError("linked Wiki shortcut output already exists")
            os.rename(stage, output)
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(
            run(
                args.source_dir,
                args.native_dir,
                args.target_manifest,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )


if __name__ == "__main__":
    main()
