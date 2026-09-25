"""Curate P105 paper QA against the exact final reader target text."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import (
    CandidateLedger,
    NativeCandidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p104_paper_quality_gate import (
    SEPARATOR,
    _dump,
    _pin,
    _rows,
    _sha,
    _verify_files,
    quality_status,
)

SCHEMA = "longworld.p105-paper-quality-gate.v1"


def _quality_status(context: str, answer: str, span: tuple[int, int]) -> str:
    start, end = span
    if not 0 <= start < end <= len(context):
        raise ValueError("P105 recorded target span is outside reader")
    if context[start:end] != answer:
        return "rejected_gold_not_exact_visible_span"
    return quality_status(context, answer)


def _source_rows(config: dict) -> tuple[list[dict], dict, dict]:
    discovery_path = _pin(config["discovery_manifest"])
    native_path = _pin(config["native_manifest"])
    native_mask_path = _pin(config["native_mask"])
    unified_path = _pin(config["unified_manifest"])
    raw_mask_path = _pin(config["unified_mask"])
    discovery = json.loads(discovery_path.read_text())
    native = json.loads(native_path.read_text())
    native_mask = json.loads(native_mask_path.read_text())
    unified = verify_merge(unified_path.parent)
    raw_mask = json.loads(raw_mask_path.read_text())
    _verify_files(discovery_path.parent, discovery["files_sha256"])
    _verify_files(native_path.parent, native["files_sha256"])
    if (
        discovery["qa_config_sha256"] != native["config_sha256"]
        or native_mask["manifest_sha256"] != _sha(native_path)
        or native_mask["checked_views"] != native["candidate_views"]
        or unified["native_manifest_sha256"] != _sha(native_path)
        or unified["native_final_audit_sha256"] != _sha(native_mask_path)
        or raw_mask["source_manifest_sha256"] != _sha(unified_path)
        or raw_mask["audited_views"] != unified["candidate_views"]
        or native["candidate_views"] != unified["candidate_views"]
    ):
        raise ValueError("P105 raw source, native audit or all-mask chain differs")
    native_dir = native_path.parent
    unified_dir = unified_path.parent
    raw_index = {
        row["sample_id"]: row for row in _rows(native_dir / "sample_index.jsonl")
    }
    audits = {row["sample_id"]: row for row in _rows(native_dir / "audit.jsonl")}
    readers = {
        split: _rows(unified_dir / f"candidate_{split}.jsonl")
        for split in ("train", "eval")
    }
    index = _rows(unified_dir / "sample_index.jsonl")
    if len(raw_index) != len(audits) or len(index) != len(raw_index):
        raise ValueError("P105 raw reader/audit/index inventory differs")
    rows = []
    for item in index:
        sample_id, split = item["sample_id"], item["split"]
        reader = readers[split][item["row_index"]]
        audit = audits[sample_id]
        native_index = raw_index[sample_id]
        if (
            reader["sample_id"] != sample_id
            or audit["answer"] != reader["messages"][1]["content"]
            or native_index["split"] != split
            or native_index["source_group"] != item["source_group"]
            or native_index["full_chat_tokens"] != item["full_chat_tokens"]
            or native_index["supervised_tokens"] != item["supervised_tokens"]
            or native_mask["reader_sha256"].get(sample_id)
            != hashlib.sha256(_dump(reader).encode()).hexdigest()
        ):
            raise ValueError("P105 native/unified reader or source split differs")
        user = reader["messages"][0]["content"]
        if user.count(SEPARATOR) != 1:
            raise ValueError("P105 raw reader boundary ambiguous")
        context = user.split(SEPARATOR, 1)[0]
        answer = audit["answer"]
        start, end = audit["reader_char_spans"]["caption"]
        positions = audit["prompt_token_spans"]
        extent = max(span[1] for span in positions.values()) - min(
            span[0] for span in positions.values()
        )
        last = audit["query_token_start"] - max(span[1] for span in positions.values())
        if (
            extent != native_index["evidence_extent_tokens"]
            or last != native_index["last_evidence_to_query_tokens"]
            or last <= 0
        ):
            raise ValueError("P105 final prompt evidence positions differ")
        status = _quality_status(context, answer, (start, end))
        rows.append(
            {
                "sample_id": sample_id,
                "source_group": item["source_group"],
                "split": split,
                "answer": answer,
                "status": status,
                "full_chat_tokens": item["full_chat_tokens"],
                "supervised_tokens": item["supervised_tokens"],
                "prompt_token_spans": positions,
                "query_token_start": audit["query_token_start"],
                "evidence_extent_tokens": extent,
                "last_evidence_to_query_tokens": last,
            }
        )
    return rows, {"index": index, "readers": readers}, native


def build(config_path: Path) -> dict[str, str]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("wrong P105 paper quality config schema")
    ledger, raw, native = _source_rows(config)
    accepted = {
        row["sample_id"]
        for row in ledger
        if row["status"] == "accepted_raw_tex_reference"
    }
    if not accepted:
        raise ValueError("P105 paper quality gate has no admitted reader")
    output_rows = {"train": [], "eval": []}
    output_index = []
    candidate_ledger = CandidateLedger()
    for item in raw["index"]:
        if item["sample_id"] not in accepted:
            continue
        split = item["split"]
        reader = raw["readers"][split][item["row_index"]]
        candidate = NativeCandidate(
            **{name: item[name] for name in NativeCandidate.__dataclass_fields__}
        )
        candidate_ledger.add(candidate)
        output_index.append(
            {
                **item,
                "output_file": f"candidate_{split}.jsonl",
                "row_index": len(output_rows[split]),
            }
        )
        output_rows[split].append(reader)
    outputs = {
        f"candidate_{split}.jsonl": "".join(_dump(row) + "\n" for row in rows)
        for split, rows in output_rows.items()
    }
    outputs["sample_index.jsonl"] = "".join(_dump(row) + "\n" for row in output_index)
    outputs["quality_ledger.jsonl"] = "".join(_dump(row) + "\n" for row in ledger)
    splits = {split: len(rows) for split, rows in output_rows.items() if rows}
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "quality_gate_schema": SCHEMA,
        "config_sha256": _sha(config_path),
        "raw_native_manifest_sha256": config["native_manifest"]["sha256"],
        "raw_unified_manifest_sha256": config["unified_manifest"]["sha256"],
        "raw_all_mask_manifest_sha256": config["unified_mask"]["sha256"],
        "gross_reader_views": native["candidate_views"],
        "quality_rejections": dict(
            sorted(
                Counter(
                    row["status"]
                    for row in ledger
                    if row["status"] != "accepted_raw_tex_reference"
                ).items()
            )
        ),
        "candidate_views": candidate_ledger.rows,
        "source_scoped_semantic_tasks": candidate_ledger.independent_tasks,
        "independent_semantic_tasks": candidate_ledger.independent_semantic_tasks,
        "views_by_lane": {"p105_paper_reference_curated": candidate_ledger.rows},
        "splits": splits,
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in output_index).items())
        ),
        "quality_ledger_sha256": hashlib.sha256(
            outputs["quality_ledger.jsonl"].encode()
        ).hexdigest(),
        "files_sha256": {
            name: hashlib.sha256(content.encode()).hexdigest()
            for name, content in outputs.items()
            if name != "quality_ledger.jsonl"
        },
        "train_ready": False,
    }
    outputs["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return outputs


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    outputs = build(config_path)
    if verify_only:
        if not output_dir.is_dir() or {
            path.name for path in output_dir.iterdir()
        } != set(outputs):
            raise ValueError("P105 curated file inventory drift")
        for name, content in outputs.items():
            if (output_dir / name).read_text() != content:
                raise ValueError(f"P105 curated replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P105 curated output must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            (output_dir / name).write_text(content)
    return verify_merge(output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(run(args.config, args.output_dir, verify_only=args.verify_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
