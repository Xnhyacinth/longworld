"""Independently replay P100 final reader answers, interventions and SFT masks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    get_tokenizer,
)
from scripts.audit_unified_reader_mask import audit_reader
from scripts.p100_wiki_categorical_scan import SCHEMA, _dump, _sha

AUDIT_SCHEMA = SCHEMA + ".final-audit"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def audit(native_dir: Path) -> dict:
    manifest_path = native_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("schema") != SCHEMA + ".result"
        or manifest.get("train_ready") is not False
    ):
        raise ValueError("wrong native categorical candidate manifest")
    for name, digest in manifest["files_sha256"].items():
        if _sha(native_dir / name) != digest:
            raise ValueError(f"native file changed: {name}")
    indices = _rows(native_dir / "sample_index.jsonl")
    proofs = _rows(native_dir / "audit.jsonl")
    readers = {
        split: _rows(native_dir / f"{split}.jsonl") for split in ("train", "eval")
    }
    by_proof = {row["sample_id"]: row for row in proofs}
    by_reader = {
        row["sample_id"]: (split, row)
        for split, rows in readers.items()
        for row in rows
    }
    if (
        len(indices) != manifest["candidate_views"]
        or len(by_proof) != len(indices)
        or len(by_reader) != len(indices)
        or {row["sample_id"] for row in indices} != set(by_proof)
        or {row["sample_id"] for row in indices} != set(by_reader)
    ):
        raise ValueError("native reader/index/proof inventory differs")
    tokenizer = get_tokenizer()
    checks = []
    for index in indices:
        sample_id = index["sample_id"]
        split, raw_reader = by_reader[sample_id]
        proof = by_proof[sample_id]
        if split != index["split"] or raw_reader["example_id"] != sample_id:
            raise ValueError("physical reader split or identity differs")
        reader = {"sample_id": sample_id, "messages": raw_reader["messages"]}
        user = reader["messages"][0]["content"]
        marker = "\n\nQUESTION\n"
        if user.count(marker) != 1 or proof["question"] != user.split(marker, 1)[1]:
            raise ValueError("final reader question boundary differs")
        context = user.split(marker, 1)[0]
        answer = {"count": 0, "entries": []}
        values = []
        for item in proof["candidate_rows"]:
            start, end = item["reader_cell_start"], item["reader_cell_end"]
            if context[start:end] != item["value"]:
                raise ValueError("reader evidence cell changed")
            if item["selected"] != (item["value"] == proof["category"]):
                raise ValueError("candidate membership differs from visible category")
            values.append(item["value"])
            if item["selected"]:
                answer["entries"].append(item["name"])
        answer["entries"].sort()
        answer["count"] = len(answer["entries"])
        if (
            answer != proof["answer"]
            or answer["count"] != index["selected_rows"]
            or len(values) != index["candidate_rows"]
            or _dump(answer) != reader["messages"][1]["content"]
        ):
            raise ValueError("reader evidence set or answer differs")
        intervention = proof["intervention"]
        start, end = intervention["reader_cell_start"], intervention["reader_cell_end"]
        if context[start:end] != intervention["old_value"]:
            raise ValueError("intervention cell differs from final reader")
        for prefix in ("hit", "control"):
            replacement = intervention[f"{prefix}_value"]
            changed = context[:start] + replacement + context[end:]
            if (
                hashlib.sha256(changed.encode()).hexdigest()
                != intervention[f"{prefix}_reader_sha256"]
            ):
                raise ValueError("intervention final reader hash differs")
        if (
            intervention["old_value"] == intervention["hit_value"]
            or intervention["old_value"] == intervention["control_value"]
            or intervention["hit_answer"]["count"] != answer["count"] + 1
            or intervention["control_answer"] != answer
        ):
            raise ValueError("intervention does not separate answer from control")
        checks.append(audit_reader(reader, index, tokenizer, 131072))
    result = {
        "schema": AUDIT_SCHEMA,
        "manifest_sha256": _sha(manifest_path),
        "index_sha256": _sha(native_dir / "sample_index.jsonl"),
        "audit_sha256": _sha(native_dir / "audit.jsonl"),
        "checked_views": len(checks),
        "splits": dict(sorted(Counter(row["split"] for row in checks).items())),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in checks),
        "supervised_tokens": sum(row["supervised_tokens"] for row in checks),
        "tokenizer": {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION},
        "reader_sha256": {row["sample_id"]: row["reader_sha256"] for row in checks},
        "scope": "final_reader_evidence_and_bounded_edit_hashes_plus_exact_assistant_mask",
        "train_ready": False,
    }
    receipt = native_dir / "mask_audit.json"
    if receipt.exists():
        if json.loads(receipt.read_text()) != result:
            raise ValueError("existing final categorical audit drift")
    else:
        receipt.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    args = parser.parse_args()
    print(_dump(audit(args.native_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
