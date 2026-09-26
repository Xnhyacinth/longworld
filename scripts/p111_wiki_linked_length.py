"""Test complete same-world Wiki documents as length views of existing tasks."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_wiki_join_positions import token_span
from scripts.p111_wiki_linked_batch import SEP, _dump, _solve
from scripts.p111_wiki_linked_freeze import _sha
from scripts.p111_wiki_linked_support import _snapshot
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p111-wiki-linked-length-layout.v1"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def run(
    native_dir: Path,
    raw_manifest: Path,
    target_manifest: Path,
    output_dir: Path,
    *,
    verify_only: bool = False,
) -> dict:
    native = json.loads((native_dir / "manifest.json").read_text())
    raw = json.loads(raw_manifest.read_text())
    targets = json.loads(target_manifest.read_text())
    if (
        native["target_manifest_sha256"] != _sha(target_manifest)
        or raw["schema"] != "longworld.p111-wiki-linked-exact-list-freeze.v1"
    ):
        raise ValueError("P111 length source pin mismatch")
    source_for_target = {
        (r["source_group"], r["target_title"]): r
        for r in targets["records"]
        if r["status"] == "frozen"
    }
    index = _rows(native_dir / "sample_index.jsonl")
    proofs = _rows(native_dir / "audit.jsonl")
    readers = {
        split: iter(_rows(native_dir / f"{split}.jsonl")) for split in ("train", "eval")
    }
    tokenizer = get_tokenizer()
    views = []
    rejects = Counter()
    for base, proof in zip(index, proofs, strict=True):
        reader = next(readers[base["split"]], None)
        if (
            reader is None
            or reader["sample_id"] != base["sample_id"]
            or proof["sample_id"] != base["sample_id"]
        ):
            raise ValueError("P111 length base row alignment differs")
        source = source_for_target[(base["source_group"], proof["first_target"])]
        extras = []
        for record in raw["records"]:
            if (
                record["source_group"] != base["source_group"]
                or record["doc_id"] == source["source_doc_id"]
            ):
                continue
            doc = _snapshot(record)
            if record["split"] != base["split"]:
                raise ValueError("P111 length source split drift")
            forbidden = (
                proof["answer"],
                proof["alternate_answer"],
                proof["selector_value"],
                proof["alternate_selector_value"],
                proof["first_target"],
                proof["second_target"],
            )
            if any(term.casefold() in doc["text"].casefold() for term in forbidden):
                rejects["extra_page_equivalent_or_selector_support"] += 1
                continue
            extras.append((record, doc))
        context, question = reader["messages"][0]["content"].split(SEP)
        possibilities = []
        for count in range(1, len(extras) + 1):
            for group in itertools.combinations(extras, count):
                extension = "".join(
                    f"\n\n=== DOCUMENT: {record['title']} (revision {record['revid']}) ===\n{doc['text']}"
                    for record, doc in group
                )
                extended = context + extension
                if (
                    _solve(
                        extended,
                        proof["selector_key"],
                        proof["selector_value"],
                        proof["field"],
                    )
                    != proof["answer"]
                ):
                    raise ValueError("P111 extended reader answer replay differs")
                if extended.count(proof["answer"]) != 1:
                    raise ValueError("P111 extended reader has repeated answer support")
                no_link = extended.replace(
                    f"[{source['row_name']}](<{source['target_url']}>)",
                    source["row_name"],
                    1,
                )
                if (
                    _solve(
                        no_link,
                        proof["selector_key"],
                        proof["selector_value"],
                        proof["field"],
                    )
                    is not None
                ):
                    raise ValueError("P111 extended reader survives link deletion")
                no_field = extended.replace(
                    f"{proof['field']}: {proof['answer']}", "", 1
                )
                if (
                    _solve(
                        no_field,
                        proof["selector_key"],
                        proof["selector_value"],
                        proof["field"],
                    )
                    is not None
                ):
                    raise ValueError("P111 extended reader survives target deletion")
                messages = [
                    {"role": "user", "content": extended + SEP + question},
                    reader["messages"][1],
                ]
                encoded = tokenize_assistant_only(tokenizer, messages, 131072)
                full = len(encoded["input_ids"])
                supervised = sum(label != -100 for label in encoded["labels"])
                if (
                    encoded["labels"]
                    != [-100] * (full - supervised)
                    + encoded["input_ids"][full - supervised :]
                ):
                    raise ValueError("P111 extended reader mask differs")
                possibilities.append((full, group, extended, messages, supervised))
        selected = next(
            (
                p
                for p in sorted(possibilities, key=lambda p: p[0])
                if 32768 <= p[0] < 65536
            ),
            None,
        )
        if selected is None:
            rejects["same_world_pages_cannot_reach_32k_without_shortcut"] += 1
            continue
        full, group, extended, messages, supervised = selected
        prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
        user_start = prompt.index(messages[0]["content"])
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        a, b = proof["selector_char_span"]
        c, d = proof["target_char_span"]
        source_tokens = token_span(offsets, user_start + a, user_start + b)
        target_tokens = token_span(offsets, user_start + c, user_start + d)
        query_tokens = token_span(
            offsets,
            user_start + len(extended) + len(SEP),
            user_start + len(messages[0]["content"]),
        )
        sample_id = base["sample_id"] + "-32k-view"
        views.append(
            {
                "sample_id": sample_id,
                "base_sample_id": base["sample_id"],
                "task_id": base["task_id"],
                "source_group": base["source_group"],
                "split": base["split"],
                "extra_titles": [record["title"] for record, _ in group],
                "extra_revisions": [record["revid"] for record, _ in group],
                "messages": messages,
                "full_chat_tokens": full,
                "supervised_tokens": supervised,
                "source_evidence_token_span": list(source_tokens),
                "target_evidence_token_span": list(target_tokens),
                "query_token_span": list(query_tokens),
                "evidence_token_extent": max(source_tokens[1], target_tokens[1])
                - min(source_tokens[0], target_tokens[0]),
                "last_evidence_to_query_gap": query_tokens[0]
                - max(source_tokens[1], target_tokens[1]),
                "reader_sha256": hashlib.sha256(
                    messages[0]["content"].encode()
                ).hexdigest(),
            }
        )
    if any(next(stream, None) is not None for stream in readers.values()):
        raise ValueError("P111 length native trailing reader")
    content = "".join(_dump(row) + "\n" for row in views).encode()
    manifest = {
        "schema": SCHEMA,
        "native_manifest_sha256": _sha(native_dir / "manifest.json"),
        "raw_manifest_sha256": _sha(raw_manifest),
        "target_manifest_sha256": _sha(target_manifest),
        "base_semantic_tasks": len(index),
        "length_views": len(views),
        "new_semantic_tasks": 0,
        "rejections": dict(sorted(rejects.items())),
        "views_sha256": hashlib.sha256(content).hexdigest(),
        "train_ready": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "views.jsonl": content,
        "manifest.json": (
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode(),
    }
    for name, encoded in outputs.items():
        path = output_dir / name
        if verify_only:
            if not path.exists() or path.read_bytes() != encoded:
                raise ValueError(f"P111 length replay differs: {name}")
        else:
            if path.exists() and path.read_bytes() != encoded:
                raise ValueError(
                    f"P111 length output exists with different bytes: {name}"
                )
            path.write_bytes(encoded)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(
        args.native_dir,
        args.raw_manifest,
        args.target_manifest,
        args.output_dir,
        verify_only=args.verify_only,
    )
    print(_dump(result))


if __name__ == "__main__":
    main()
