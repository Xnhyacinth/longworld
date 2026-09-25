"""Project only P108 filename-content-qualified readers into unified candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    TokenCounts,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p108_code_catalog import _dump, _sha

SCHEMA = "longworld.unified-candidates.v1"
SEPARATOR = "\n\nSource records:\n"
LANE = "p108_code_filename_content"
DEPENDENCY = "filename_content_backed_scoped_certificate"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _task_scope_keys(bank_manifest: Path, metadata: list[dict]) -> list[tuple]:
    manifest = json.loads(bank_manifest.read_text())
    if manifest.get("schema") != "longworld.p108-code-bank.v1.result" or manifest.get(
        "verified_banks"
    ) != manifest.get("signed_source_worlds"):
        raise ValueError("P108 filename novelty bank manifest invalid")
    tasks = {}
    for row in manifest["by_repository"]:
        bank = ROOT / row["bank_root"]
        receipt_path = bank / "BUILD_RECEIPT.json"
        if _sha(receipt_path) != row["receipt_sha256"]:
            raise ValueError("P108 filename novelty bank receipt changed")
        receipt = json.loads(receipt_path.read_text())
        task_path = bank / "tasks.jsonl"
        if _sha(task_path) != receipt["files"]["tasks.jsonl"]:
            raise ValueError("P108 filename novelty task bank changed")
        tasks.update({task["sample_id"]: task for task in _rows(task_path)})
    keys = []
    for meta in metadata:
        task = tasks[meta["source_sample_id"]]
        if (
            task["semantic_task_id"] != meta["semantic_task_id"]
            or task["source_group_id"] != meta["source_group_id"]
            or task["split"] != meta["split"]
            or task["program_id"] != meta["program_id"]
        ):
            raise ValueError("P108 filename novelty task/proof identity differs")
        keys.append(
            (
                meta["source_group_id"],
                meta["split"],
                meta["program_id"],
                tuple(sorted(task["parameters"]["pull_requests"])),
            )
        )
    if len(set(keys)) != len(keys):
        raise ValueError("P108 filename proof duplicates a repository/PR/program scope")
    return keys


def convert(
    proof_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
    bank_manifest: Path | None = None,
    prior_proof_dir: Path | None = None,
    prior_bank_manifest: Path | None = None,
) -> dict:
    proof_dir = proof_dir if proof_dir.is_absolute() else ROOT / proof_dir
    output = output if output.is_absolute() else ROOT / output
    receipt_path = proof_dir / "BUILD_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    if (
        receipt.get("schema_version") != "longworld.codeforge-reading-proof-build.v2"
        or receipt.get("qualified_existing_semantic_tasks", 0) < 1
        or receipt.get("new_semantic_tasks") != 0
    ):
        raise ValueError("P108 filename proof is not a qualified native receipt")
    for name, digest in receipt["files"].items():
        if _sha(proof_dir / name) != digest:
            raise ValueError("P108 filename proof file pin changed")
    proof_rows = _rows(proof_dir / "proofs.jsonl")
    proofs = {
        row["semantic_task_id"]: (row, position)
        for position, row in enumerate(proof_rows)
    }
    if len(proofs) != len(proof_rows):
        raise ValueError("P108 filename proof duplicates a semantic task")
    metadata = _rows(proof_dir / "metadata.jsonl")
    if len(metadata) != receipt["qualified_existing_semantic_tasks"]:
        raise ValueError("P108 filename proof qualified count differs")
    readers = {
        split: _rows(proof_dir / f"{split}.jsonl") for split in ("train", "eval")
    }
    if any(
        len(readers[split]) != receipt["counts"][split] for split in ("train", "eval")
    ):
        raise ValueError("P108 filename proof reader counts differ")
    if any(
        item is not None
        for item in (bank_manifest, prior_proof_dir, prior_bank_manifest)
    ):
        if any(
            item is None
            for item in (bank_manifest, prior_proof_dir, prior_bank_manifest)
        ):
            raise ValueError(
                "P108 filename novelty needs both proof and bank manifests"
            )
        prior_proof_dir = (
            prior_proof_dir if prior_proof_dir.is_absolute() else ROOT / prior_proof_dir
        )
        prior_receipt = json.loads((prior_proof_dir / "BUILD_RECEIPT.json").read_text())
        prior_metadata = _rows(prior_proof_dir / "metadata.jsonl")
        if (
            _sha(prior_proof_dir / "metadata.jsonl")
            != prior_receipt["files"]["metadata.jsonl"]
        ):
            raise ValueError("P108 prior filename proof metadata changed")
        prior_keys = set(_task_scope_keys(prior_bank_manifest, prior_metadata))
        current_keys = _task_scope_keys(bank_manifest, metadata)
    else:
        prior_keys = set()
        current_keys = [None] * len(metadata)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p108_filename_unified_", dir=output.parent
    ) as raw:
        staging = Path(raw)
        streams = {split: [] for split in ("train", "eval")}
        index_rows = []
        ledger = CandidateLedger()
        lengths, operations, sources = Counter(), Counter(), Counter()
        source_positions = Counter()
        excluded_prior = 0
        for meta, scope_key in zip(metadata, current_keys, strict=True):
            split = meta["split"]
            if split not in readers or meta["row_index"] != source_positions[split]:
                raise ValueError("P108 filename reader order differs")
            source_positions[split] += 1
            if scope_key in prior_keys:
                excluded_prior += 1
                continue
            reader = readers[split][meta["row_index"]]
            proof, proof_position = proofs[meta["semantic_task_id"]]
            if (
                proof["sample_id"] != meta["sample_id"]
                or proof["content_backed_scoped_certificate"] is not True
                or meta["content_backed_scoped_certificate"] is not True
                or hashlib.sha256(
                    json.dumps(
                        proof, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                != meta["proof_sha256"]
            ):
                raise ValueError("P108 filename proof identity/content gate differs")
            messages = reader["messages"]
            user = messages[0]["content"]
            if user.count(SEPARATOR) != 1:
                raise ValueError("P108 filename reader context boundary ambiguous")
            context = user.split(SEPARATOR, 1)[1]
            group = meta["source_group_id"]
            binding = AdapterBinding(
                source_kind="real_code_workflow",
                source_group=group,
                domain="codeforge",
                topic=group.rsplit("/", 1)[-1],
                operation=meta["program_id"],
                evidence_profile=receipt["profile_id"],
                tokenizer_profile="pinned-chat-template",
                receipt_path=receipt_path,
                receipt_sha256=_sha(receipt_path),
            )
            index = {
                **meta,
                "source_group": group,
                "dependency_status": DEPENDENCY,
                "evidence_status": receipt["profile_id"],
            }
            full = meta["full_message_tokens"]
            supervised = meta["assistant_tokens"]
            candidate = normalize_native_candidate(
                index,
                {"sample_id": meta["sample_id"], "messages": messages},
                binding,
                context_text=context,
                token_counts=TokenCounts(full - supervised, supervised, full),
            )
            ledger.add(candidate)
            streams[split].append(
                {"sample_id": candidate.sample_id, "messages": messages}
            )
            record = candidate.to_dict()
            record.update(
                source_name=LANE,
                native_row_ref=f"{proof_dir / f'{split}.jsonl'}:{meta['row_index']}",
                native_audit_ref=f"{proof_dir / 'proofs.jsonl'}:{proof_position}",
                content_proof_scope="finite filename alias grammar; source text and SHA normalized while filenames remain; one raw 16K window",
                output_file=f"candidate_{split}.jsonl",
                row_index=len(streams[split]) - 1,
            )
            index_rows.append(record)
            lengths[candidate.length_bin] += 1
            operations[candidate.operation] += 1
            sources[group] += 1
        if any(source_positions[split] != len(readers[split]) for split in readers):
            raise ValueError("P108 filename reader inventory has trailing rows")
        payloads = {
            "candidate_train.jsonl": streams["train"],
            "candidate_eval.jsonl": streams["eval"],
            "sample_index.jsonl": index_rows,
        }
        for name, rows in payloads.items():
            (staging / name).write_text(
                "".join(
                    json.dumps(
                        row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    + "\n"
                    for row in rows
                )
            )
        manifest = {
            "schema_version": SCHEMA,
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": {
                split: len(streams[split]) for split in streams if streams[split]
            },
            "length_bins": dict(sorted(lengths.items())),
            "operations": dict(sorted(operations.items())),
            "sources": dict(sorted(sources.items())),
            "native_receipt_sha256": _sha(receipt_path),
            "claim_limit": "finite filename alias grammar and one 16K raw window; no global shortest proof or model-learning claim",
            "files_sha256": {name: _sha(staging / name) for name in payloads},
            "train_ready": False,
        }
        if bank_manifest is not None:
            manifest.update(
                source_bank_manifest_sha256=_sha(bank_manifest),
                prior_bank_manifest_sha256=_sha(prior_bank_manifest),
                prior_proof_receipt_sha256=_sha(prior_proof_dir / "BUILD_RECEIPT.json"),
                rejected_prior_pr_program_scopes=excluded_prior,
                novelty_key="source_group + split + program + sorted pull requests",
            )
        (staging / "manifest.json").write_text(_dump(manifest))
        if verify_only:
            verify_merge(output)
            if any(
                not (output / path.name).is_file()
                or (output / path.name).read_bytes() != path.read_bytes()
                for path in staging.iterdir()
            ):
                raise ValueError("P108 filename unified replay differs")
        else:
            if output.exists():
                raise ValueError("P108 filename unified output already exists")
            os.rename(staging, output)
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--bank-manifest", type=Path)
    parser.add_argument("--prior-proof-dir", type=Path)
    parser.add_argument("--prior-bank-manifest", type=Path)
    args = parser.parse_args()
    print(
        _dump(
            convert(
                args.proof_dir,
                args.output,
                verify_only=args.verify_only,
                bank_manifest=args.bank_manifest,
                prior_proof_dir=args.prior_proof_dir,
                prior_bank_manifest=args.prior_bank_manifest,
            )
        )
    )


if __name__ == "__main__":
    main()
