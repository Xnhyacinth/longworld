"""Recheck final P111 readers against independent source, answer and mask bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.p111_wiki_linked_batch import SEP, _solve
from scripts.p111_wiki_linked_batch import run as replay_native
from scripts.p111_wiki_linked_freeze import _sha
from scripts.p111_wiki_linked_targets import run as replay_targets
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p111-wiki-linked-final-reader-audit.v1"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def audit(
    native_dir: Path,
    target_manifest: Path,
    body_ledger: Path,
    *,
    verify_only: bool = False,
) -> dict:
    native = replay_native(target_manifest, body_ledger, native_dir, verify_only=True)
    raw_manifest = ROOT / "data/candidates/p111_wiki_linked_raw_v1/manifest.json"
    support_ledger = ROOT / "data/candidates/p111_wiki_linked_support_v2/ledger.json"
    replay_targets(
        raw_manifest, support_ledger, target_manifest.parent, verify_only=True
    )
    target = json.loads(target_manifest.read_text())
    support = json.loads(support_ledger.read_text())
    if support["raw_manifest_sha256"] != _sha(raw_manifest):
        raise ValueError("P111 raw/visible link receipt differs")
    link_splits: dict[str, set[str]] = {}
    for page in support["pages"]:
        for candidate in page["candidates"]:
            link_splits.setdefault(candidate["target_title"].casefold(), set()).add(
                page["split"]
            )
    if any(len(splits) > 1 for splits in link_splits.values()):
        raise ValueError("P111 linked target crosses train/eval source groups")
    if native["target_manifest_sha256"] != _sha(target_manifest) or native[
        "body_ledger_sha256"
    ] != _sha(body_ledger):
        raise ValueError("P111 native source receipts drift")
    target_records = {
        (r["source_group"], r["target_title"]): r
        for r in target["records"]
        if r["status"] == "frozen"
    }
    readers = {
        split: iter(_jsonl(native_dir / f"{split}.jsonl"))
        for split in ("train", "eval")
    }
    indices = _jsonl(native_dir / "sample_index.jsonl")
    proofs = _jsonl(native_dir / "audit.jsonl")
    if len(indices) != len(proofs) or len(indices) != native["reader_tasks"]:
        raise ValueError("P111 native index/proof count differs")
    tokenizer = get_tokenizer()
    stats = []
    seen = set()
    for index, proof in zip(indices, proofs, strict=True):
        split = index["split"]
        reader = next(readers[split], None)
        if (
            reader is None
            or reader["sample_id"] != index["sample_id"]
            or proof["sample_id"] != index["sample_id"]
        ):
            raise ValueError("P111 native reader/index order differs")
        sample_id = index["sample_id"]
        if sample_id in seen:
            raise ValueError("P111 duplicate native sample ID")
        seen.add(sample_id)
        user, assistant = reader["messages"]
        if (
            user["role"] != "user"
            or assistant["role"] != "assistant"
            or user["content"].count(SEP) != 1
        ):
            raise ValueError("P111 final reader boundary differs")
        context, question = user["content"].split(SEP)
        if proof["answer"] != assistant["content"] or proof["answer"] in question:
            raise ValueError("P111 answer/question boundary differs")
        if (
            _solve(
                context, proof["selector_key"], proof["selector_value"], proof["field"]
            )
            != assistant["content"]
        ):
            raise ValueError("P111 blind final-reader answer replay differs")
        if (
            hashlib.sha256(context.encode()).hexdigest() != proof["context_sha256"]
            or hashlib.sha256(user["content"].encode()).hexdigest()
            != proof["reader_sha256"]
        ):
            raise ValueError("P111 context/user byte digest differs")
        if context.count(assistant["content"]) != 1:
            raise ValueError("P111 answer has duplicate visible support")
        for title, expected_sha in zip(
            (proof["first_target"], proof["second_target"]),
            proof["target_response_sha256"],
            strict=True,
        ):
            source_record = target_records.get((index["source_group"], title))
            if (
                source_record is None
                or source_record["split"] != split
                or source_record["response_sha256"] != expected_sha
            ):
                raise ValueError("P111 target source/split pin differs")
            support_pages = [
                page
                for page in support["pages"]
                if page["doc_id"] == source_record["source_doc_id"]
                and page["source_group"] == source_record["source_group"]
            ]
            if len(support_pages) != 1 or not any(
                candidate["target_title"] == title
                and candidate["name"] == source_record["row_name"]
                for candidate in support_pages[0]["candidates"]
            ):
                raise ValueError("P111 raw linked row lacks pinned visible support")
        if (
            index["source_kind"] != "real_wiki"
            or index["world_id"] != proof["source_group"]
            or index["operation"] != "list_selector_linked_article_attribute"
        ):
            raise ValueError("P111 canonical reader task tags differ")
        a, b = proof["selector_char_span"]
        c, d = proof["target_char_span"]
        if (
            context[a:b] != proof["selector_value"]
            or context[c:d] != assistant["content"]
        ):
            raise ValueError("P111 final context evidence span differs")
        encoded = tokenize_assistant_only(tokenizer, reader["messages"], 131072)
        full = len(encoded["input_ids"])
        supervised = sum(label != -100 for label in encoded["labels"])
        if (
            full != index["full_chat_tokens"]
            or supervised != index["supervised_tokens"]
            or encoded["labels"]
            != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
        ):
            raise ValueError("P111 final chat assistant mask differs")
        stats.append(
            {
                "sample_id": sample_id,
                "split": split,
                "source_group": index["source_group"],
                "full_chat_tokens": full,
                "supervised_tokens": supervised,
                "evidence_token_extent": index["evidence_token_extent"],
                "selector_to_target_token_gap": index["selector_to_target_token_gap"],
            }
        )
    if any(next(reader, None) is not None for reader in readers.values()):
        raise ValueError("P111 unindexed native reader remains")
    result = {
        "schema": SCHEMA,
        "native_manifest_sha256": _sha(native_dir / "manifest.json"),
        "target_manifest_sha256": _sha(target_manifest),
        "body_ledger_sha256": _sha(body_ledger),
        "checked_readers": len(stats),
        "source_worlds": len({r["source_group"] for r in stats}),
        "splits": dict(sorted(Counter(r["split"] for r in stats).items())),
        "full_chat_tokens": sum(r["full_chat_tokens"] for r in stats),
        "supervised_tokens": sum(r["supervised_tokens"] for r in stats),
        "rows": stats,
        "train_ready": False,
    }
    encoded = (
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    receipt = native_dir / "mask_audit.json"
    if verify_only:
        if receipt.read_bytes() != encoded:
            raise ValueError("P111 final audit receipt replay differs")
    else:
        if receipt.exists() and receipt.read_bytes() != encoded:
            raise ValueError("P111 final audit receipt exists with different bytes")
        receipt.write_bytes(encoded)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--body-ledger", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = audit(
        args.native_dir,
        args.target_manifest,
        args.body_ledger,
        verify_only=args.verify_only,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "checked_readers",
                    "source_worlds",
                    "splits",
                    "full_chat_tokens",
                    "supervised_tokens",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
