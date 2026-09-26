"""Screen a frozen Wiki source-scale batch for short L1 anchor candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import (
    CandidateLedger,
    NativeCandidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p112_wiki_link_shortcut_gate import _key
from scripts.p113_legal_cell_plan import _sha
from scripts.run_shared_record_taskbank import _tokenizer
from scripts.train_sft import _render_chat

SCHEMA = "longworld.p114-wiki-scale.v1"
TITLE = re.compile(r"^\[doc-[^\]]+\] (.+)$", re.MULTILINE)
ARTIFACT = re.compile(r"[a-z]br[A-Z]|<[^>]+>|&(?:amp|nbsp|lt|gt);")
MARKER = "\n\nQUESTION\n"


def _dump(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _pinned(pin: dict[str, str]) -> tuple[Path, dict[str, Any]]:
    if set(pin) != {"path", "sha256"}:
        raise ValueError("P114 input pin needs path and sha256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P114 input pin escapes project")
    path = (ROOT / relative).resolve(strict=True)
    if not (
        path.is_relative_to(ROOT)
        or (
            relative.parts[0] == "data"
            and path.is_relative_to((ROOT / "data").resolve())
        )
    ):
        raise ValueError("P114 input pin escapes workspace")
    if _sha(path) != pin["sha256"]:
        raise ValueError(f"P114 input pin drift: {relative}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _native(batch_dir: Path, batch: dict[str, Any]) -> dict[str, tuple[dict, dict]]:
    native_dir = batch_dir / "batch/jobs"
    batch_receipt = batch_dir / "batch/batch_manifest.json"
    if _sha(batch_receipt) != batch["batch_manifest_sha256"]:
        raise ValueError("native Wiki batch receipt changed")
    receipt = json.loads(batch_receipt.read_text())
    found: dict[str, tuple[dict, dict]] = {}
    for job_id, digest in receipt["job_receipt_sha256"].items():
        folder = native_dir / job_id
        if _sha(folder / "receipt.json") != digest:
            raise ValueError("native Wiki job receipt changed")
        manifest = json.loads((folder / "manifest.json").read_text())
        for name in ("sample_index.jsonl", "audit.jsonl"):
            if _sha(folder / name) != manifest["files_sha256"][name]:
                raise ValueError("native Wiki evidence file changed")
        for index, proof in zip(
            _rows(folder / "sample_index.jsonl"),
            _rows(folder / "audit.jsonl"),
            strict=True,
        ):
            sample = index["example_id"]
            if sample in found or proof["example_id"] != sample:
                raise ValueError("native Wiki evidence identity repeats or differs")
            found[sample] = (index, proof)
    if len(found) != batch["candidate_views"]:
        raise ValueError("native Wiki task inventory differs")
    return found


def _prior(pin: dict[str, str]) -> dict[tuple[str, str, str], str]:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("prior index path escapes project")
    path = (ROOT / relative).resolve(strict=True)
    if _sha(path) != pin["sha256"]:
        raise ValueError("prior candidate index changed")
    tasks: dict[tuple[str, str, str], str] = {}
    for row in _rows(path):
        candidate = row["candidate"]
        key = (
            candidate["source_kind"],
            candidate["source_group"],
            candidate["semantic_task_id"],
        )
        prior = tasks.setdefault(key, candidate["answer_sha256"])
        if prior != candidate["answer_sha256"]:
            raise ValueError("prior index has conflicting task answers")
    return tasks


def _long_gap(reader: dict, evidence: dict) -> int:
    tokenizer = _tokenizer()
    messages = reader["messages"]
    user = messages[0]["content"]
    if user.count(MARKER) != 1:
        raise ValueError("long Wiki reader question marker ambiguous")
    rendered = _render_chat(tokenizer, messages, generation_prompt=False)
    start = rendered.find(user)
    if start < 0 or rendered.find(user, start + 1) >= 0:
        raise ValueError("long Wiki reader user text is not unique")
    query_char = start + user.index(MARKER) + len(MARKER)
    encoded = tokenizer(rendered, truncation=False, return_offsets_mapping=True)
    query = next(
        (
            i
            for i, (left, right) in enumerate(encoded["offset_mapping"])
            if left <= query_char < right or (left >= query_char and right > left)
        ),
        None,
    )
    if query is None or query <= evidence["end"]:
        raise ValueError("long Wiki evidence/query token offsets invalid")
    return query - evidence["end"]


def compile(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    pins = (
        "inventory",
        "prior_pool",
        "catalog",
        "sharded_catalog",
        "plan",
        "gate",
        "campaign",
        "native_batch",
        "unified",
        "mask",
    )
    if (
        config.get("schema") != SCHEMA + ".config"
        or type(config.get("max_same_answer_per_source")) is not int
        or not 1 <= config["max_same_answer_per_source"] <= 4
    ):
        raise ValueError("invalid P114 Wiki scale config")
    checked = {name: _pinned(config[name]) for name in pins}
    _, prior_manifest = _pinned(config["prior_index"]["manifest"])
    if prior_manifest["refs_sha256"] != config["prior_index"]["refs"]["sha256"]:
        raise ValueError("prior index manifest/refs differ")
    inventory = checked["inventory"][1]
    prior_pool = checked["prior_pool"][1]
    catalog = checked["catalog"][1]
    source_catalog_path = checked["catalog"][0].parent / "catalog.json"
    if _sha(source_catalog_path) != catalog["catalog_sha256"]:
        raise ValueError("automatic source catalog changed")
    source_catalog = json.loads(source_catalog_path.read_text())
    sharded_catalog = checked["sharded_catalog"][1]
    if (
        {
            key: value
            for key, value in source_catalog.items()
            if key != "max_queries_per_shard"
        }
        != {
            key: value
            for key, value in sharded_catalog.items()
            if key != "max_queries_per_shard"
        }
        or sharded_catalog["max_queries_per_shard"] != 8
        or sharded_catalog["base_pool"]["sha256"] != prior_pool["source_pool_sha256"]
    ):
        raise ValueError("P114 source terms changed during sharding")
    plan = checked["plan"][1]
    gate = checked["gate"][1]
    campaign = checked["campaign"][1]
    batch = checked["native_batch"][1]
    unified_path, unified = checked["unified"]
    mask_path, mask = checked["mask"]
    if (
        inventory["frozen_intakes"] < 15
        or prior_pool["source_groups"] < 247
        or plan["queries"] != catalog["selected_terms"]
        or plan["catalog_sha256"] != config["sharded_catalog"]["sha256"]
        or plan["base_pool"] != sharded_catalog["base_pool"]
        or gate["net_novel"]["groups"] > gate["gross"]["groups"]
        or campaign["gate_result_sha256"] != config["gate"]["sha256"]
        or campaign["independent_tasks"] != batch["independent_tasks"]
        or batch["source_pool_sha256"]
        != _sha(checked["gate"][0].parent / "gate/source_pool.json")
        or batch["candidate_views"] != unified["candidate_views"]
        or batch["candidate_views"] != mask["audited_views"]
        or mask["source_manifest_sha256"] != config["unified"]["sha256"]
    ):
        raise ValueError("P114 source, native, unified or mask counts disagree")
    verify_merge(unified_path.parent)
    native = _native(checked["native_batch"][0].parent, batch)
    prior = _prior(config["prior_index"]["refs"])
    mask_rows = {
        row["sample_id"]: row for row in _rows(mask_path.parent / "audit_index.jsonl")
    }
    if (
        len(mask_rows) != mask["audited_views"]
        or _sha(mask_path.parent / "audit_index.jsonl") != mask["audit_index_sha256"]
    ):
        raise ValueError("P114 all-reader mask index changed")
    reader_rows = {
        split: _rows(unified_path.parent / f"candidate_{split}.jsonl")
        for split in ("train", "eval")
    }
    candidates = _rows(unified_path.parent / "sample_index.jsonl")
    if len(candidates) != unified["candidate_views"]:
        raise ValueError("P114 unified candidate inventory changed")
    kept = {"train": [], "eval": []}
    kept_index, decisions, long_rows = [], [], []
    reasons: Counter[str] = Counter()
    answer_counts: Counter[tuple[str, str]] = Counter()
    ledger = CandidateLedger()
    consumed = {"train": set(), "eval": set()}
    doc_counts, evidence_widths, tail_gaps = Counter(), [], []
    for index in candidates:
        split, sample = index["split"], index["sample_id"]
        if split not in reader_rows or index["row_index"] in consumed[split]:
            raise ValueError("reader split/position repeated")
        reader = reader_rows[split][index["row_index"]]
        consumed[split].add(index["row_index"])
        if (
            reader["sample_id"] != sample
            or sample not in native
            or sample not in mask_rows
        ):
            raise ValueError("reader/native/mask sample identity differs")
        native_index, proof = native[sample]
        audit = mask_rows[sample]
        if (
            audit["status"] != "exact_assistant_mask_checked"
            or any(
                audit[key] != index[key]
                for key in (
                    "split",
                    "full_chat_tokens",
                    "input_tokens",
                    "supervised_tokens",
                )
            )
            or native_index["task_id"] != index["semantic_task_id"]
            or native_index["source_group"] != index["source_group"]
            or native_index["full_chat_tokens"] != index["full_chat_tokens"]
            or native_index["document_count"] < 1
            or len(native_index["fact_value_token_spans"]) != 1
            or proof["reader_text_intervention"]["status"]
            != "scoped_named_table_cell_removed"
        ):
            raise ValueError("P114 native single-cell evidence or final mask differs")
        user = reader["messages"][0]["content"]
        if user.count(MARKER) != 1:
            raise ValueError("P114 Wiki reader question marker ambiguous")
        context, question = user.split(MARKER)
        answer = json.loads(reader["messages"][1]["content"])
        if answer != proof["value_blind_reader_parser_answer"] or not isinstance(
            answer, (str, int)
        ):
            raise ValueError("P114 independent reader answer differs")
        normalized = _key(str(answer))
        titles = [_key(title) for title in TITLE.findall(context)]
        key = (index["source_kind"], index["source_group"], index["semantic_task_id"])
        reason = None
        if not normalized:
            reason = "empty_answer"
        elif normalized in _key(question):
            reason = "answer_in_question"
        elif len(normalized) >= 6 and any(normalized in title for title in titles):
            reason = "answer_in_title"
        elif ARTIFACT.search(str(answer)):
            reason = "html_artifact"
        elif key in prior:
            if prior[key] != index["answer_sha256"]:
                raise ValueError("prior semantic task has another answer")
            reason = "exact_prior_task"
        elif (
            answer_counts[(index["source_group"], index["answer_sha256"])]
            >= config["max_same_answer_per_source"]
        ):
            reason = "answer_concentration_cap"
        decisions.append(
            {
                "sample_id": sample,
                "source_group": index["source_group"],
                "status": "selected" if reason is None else "rejected",
                "reason": reason,
            }
        )
        reasons[reason or "selected"] += 1
        if reason is not None:
            continue
        answer_counts[(index["source_group"], index["answer_sha256"])] += 1
        ledger.add(
            NativeCandidate(
                **{name: index[name] for name in NativeCandidate.__dataclass_fields__}
            )
        )
        row = dict(index)
        row.update(
            output_file=f"candidate_{split}.jsonl",
            row_index=len(kept[split]),
            source_name="p114_wiki_short_l1_anchor",
        )
        kept[split].append(reader)
        kept_index.append(row)
        span = native_index["fact_value_token_spans"][0]
        doc_counts[native_index["document_count"]] += 1
        evidence_widths.append(span["end"] - span["start"])
        tail_gaps.append(index["input_tokens"] - span["end"])
        if index["full_chat_tokens"] >= 32768:
            long_rows.append(
                {
                    "sample_id": sample,
                    "full_chat_tokens": index["full_chat_tokens"],
                    "document_count": native_index["document_count"],
                    "fact_spans": 1,
                    "evidence_to_query_tokens": _long_gap(reader, span),
                }
            )
    if any(
        consumed[split] != set(range(len(reader_rows[split]))) for split in consumed
    ):
        raise ValueError("P114 unified reader rows not fully consumed")
    payloads = {
        "candidate_train.jsonl": "".join(_dump(row) for row in kept["train"]),
        "candidate_eval.jsonl": "".join(_dump(row) for row in kept["eval"]),
        "sample_index.jsonl": "".join(_dump(row) for row in kept_index),
        "decisions.jsonl": "".join(_dump(row) for row in decisions),
    }
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "quality_gate_schema": SCHEMA,
        "config_sha256": _sha(config_path),
        "source_inventory_sha256": config["inventory"]["sha256"],
        "source_groups_before_after_gate": [
            gate["gross"]["groups"],
            gate["net_novel"]["groups"],
        ],
        "pages_before_after_gate": [gate["gross"]["pages"], gate["net_novel"]["pages"]],
        "legal_native_jobs": batch["planned_jobs"],
        "unsupported_source_recipe_cells": batch["unsupported_cells"],
        "gross_native_views": len(candidates),
        "native_rejected_rows": batch["rejected_rows"],
        "mask_checked_views": mask["audited_views"],
        "candidate_views": len(kept_index),
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "views_by_lane": {"p114_wiki_short_l1_anchor": len(kept_index)},
        "splits": {split: len(rows) for split, rows in kept.items() if rows},
        "operations": dict(
            sorted(Counter(row["operation"] for row in kept_index).items())
        ),
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in kept_index).items())
        ),
        "quality_decisions": dict(sorted(reasons.items())),
        "source_groups_with_tasks": len({row["source_group"] for row in kept_index}),
        "model_visible_document_counts": dict(sorted(doc_counts.items())),
        "single_evidence_token_width": {
            "min": min(evidence_widths),
            "max": max(evidence_widths),
        },
        "last_evidence_to_input_end_tokens": {
            "min": min(tail_gaps),
            "max": max(tail_gaps),
        },
        "long_32k_rows": long_rows,
        "prior_index_sha256": config["prior_index"]["refs"]["sha256"],
        "native_batch_sha256": config["native_batch"]["sha256"],
        "source_unified_sha256": config["unified"]["sha256"],
        "source_mask_sha256": config["mask"]["sha256"],
        "files_sha256": {
            name: hashlib.sha256(value.encode()).hexdigest()
            for name, value in payloads.items()
        },
        "claim_limit": "real-source short L1 table-cell anchors; one fact span per task; no multi-document or long-distance necessity proof",
        "train_ready": False,
    }
    payloads["manifest.json"] = _dump(manifest)
    if verify_only:
        if not output.is_dir() or any(
            (output / name).read_text(encoding="utf-8") != content
            for name, content in payloads.items()
        ):
            raise ValueError("P114 Wiki frozen screen replay differs")
    else:
        output.mkdir(parents=True, exist_ok=False)
        for name, content in payloads.items():
            (output / name).write_text(content, encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(compile(args.config, args.output, verify_only=args.verify_only)), end=""
    )


if __name__ == "__main__":
    main()
