"""Balance frozen candidate references without copying reader bodies.

This is a candidate selection, not a training approval or a quality score.
Cells are (split, source kind, operation, actual tokenizer length bin). A
round-robin gives each occupied cell a chance before filling common cells.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from longworld.synthesis.sharded_candidate_bank import verify_index
from longworld.synthesis.unified_candidate_contract import physical_length_bin
from longworld.synthesis.unified_candidate_merge import verify_merge

SCHEMA = "longworld.balanced-candidate-selection.v1"
ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def _key(entry: dict[str, Any]) -> str:
    return entry["candidate"]["sample_id"]


def _cell(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    row = entry["candidate"]
    return row["split"], row["source_kind"], row["operation"], row["length_bin"]


def _group(entry: dict[str, Any]) -> tuple[str, str]:
    row = entry["candidate"]
    return row["source_kind"], row["source_group"]


def _task(entry: dict[str, Any]) -> tuple[str, str]:
    row = entry["candidate"]
    return row["source_kind"], row["semantic_task_id"]


def _tie(entry: dict[str, Any], seed: int) -> str:
    return hashlib.sha256(f"{seed}|{_key(entry)}".encode()).hexdigest()


def _validate_entries(entries: list[dict[str, Any]]) -> None:
    split_by_group: dict[tuple[str, str], str] = {}
    for entry in entries:
        row = entry["candidate"]
        group = _group(entry)
        if group in split_by_group and split_by_group[group] != row["split"]:
            raise ValueError("source group crosses train/eval split")
        split_by_group[group] = row["split"]
        if row["input_tokens"] + row["supervised_tokens"] != row["full_chat_tokens"]:
            raise ValueError("candidate token accounting changed")
        if row["length_bin"] != physical_length_bin(row["full_chat_tokens"]):
            raise ValueError("candidate physical length bin changed")


def _coverage(entries: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [entry["candidate"] for entry in entries]
    groups = {_group(entry) for entry in entries}
    tasks = {_task(entry) for entry in entries}
    scoped_tasks = {
        (row["source_kind"], row["source_group"], row["semantic_task_id"])
        for row in rows
    }
    kind_tokens: dict[str, dict[str, int]] = {}
    for row in rows:
        totals = kind_tokens.setdefault(
            row["source_kind"], {"input_tokens": 0, "supervised_tokens": 0}
        )
        totals["input_tokens"] += row["input_tokens"]
        totals["supervised_tokens"] += row["supervised_tokens"]
    return {
        "views": len(rows),
        "independent_semantic_tasks": len(tasks),
        "source_scoped_semantic_tasks": len(scoped_tasks),
        "source_groups": len(groups),
        "by_source_kind_groups": dict(
            sorted(Counter(kind for kind, _ in groups).items())
        ),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "supervised_tokens": sum(row["supervised_tokens"] for row in rows),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in rows),
        "by_split": dict(sorted(Counter(row["split"] for row in rows).items())),
        "by_source_kind": dict(
            sorted(Counter(row["source_kind"] for row in rows).items())
        ),
        "by_source_kind_tokens": dict(sorted(kind_tokens.items())),
        "by_domain": dict(sorted(Counter(row["domain"] for row in rows).items())),
        "by_topic": dict(sorted(Counter(row["topic"] for row in rows).items())),
        "by_length": dict(sorted(Counter(row["length_bin"] for row in rows).items())),
        "by_operation": dict(sorted(Counter(row["operation"] for row in rows).items())),
        "by_cell": {
            "|".join(cell): count
            for cell, count in sorted(
                Counter(_cell(entry) for entry in entries).items()
            )
        },
    }


def _read_entries(index_dir: Path) -> list[dict[str, Any]]:
    entries = []
    with (index_dir / "candidate_refs.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            entries.append(json.loads(line))
    return entries


def _pinned_proof_path(pin: dict[str, str]) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("CodeForge proof path must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"CodeForge proof pin mismatch: {relative}")
    return path


def _p132_review_eligible(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Admit only review/diff tasks bound to a positive frozen native audit."""
    by_native: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        row = entry["candidate"]
        ref = row.get("native_audit_ref", "")
        path_part, separator, offset = ref.rpartition(".jsonl:")
        path = Path(path_part + ".jsonl")
        if (
            not separator
            or not offset.isdecimal()
            or path.is_absolute()
            or ".." in path.parts
            or path.name != "audit.jsonl"
        ):
            raise ValueError("P132 review task lacks native audit reference")
        by_native[ROOT / path.parent].append(entry)
    eligible = []
    pins = {}
    for native_dir, native_entries in by_native.items():
        manifest_path = native_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        required = {"audit.jsonl", "sample_index.jsonl", "train.jsonl", "eval.jsonl"}
        if (
            manifest.get("schema_version") != "longworld.p132-review-diff-join.v1"
            or not required <= manifest.get("files_sha256", {}).keys()
        ):
            raise ValueError("P132 review native audit manifest differs")
        for name in required:
            if _sha(native_dir / name) != manifest["files_sha256"][name]:
                raise ValueError("P132 review native audit or reader pin differs")
        audits = [json.loads(line) for line in (native_dir / "audit.jsonl").read_text().splitlines()]
        native_rows = {
            row["sample_id"]: row
            for row in (
                json.loads(line)
                for line in (native_dir / "sample_index.jsonl").read_text().splitlines()
            )
        }
        if len(native_rows) != manifest["semantic_tasks"] or len(audits) != len(native_rows):
            raise ValueError("P132 review native audit inventory differs")
        for entry in native_entries:
            row = entry["candidate"]
            offset = int(row["native_audit_ref"].rpartition(":")[2])
            audit = audits[offset] if offset < len(audits) else None
            native = native_rows.get(row["sample_id"])
            if (
                row["receipt_sha256"] != _sha(manifest_path)
                or row["source_kind"] != "real_code_workflow"
                or native is None
                or audit is None
                or audit["sample_id"] != row["sample_id"]
                or native["sample_id"] != row["sample_id"]
                or any(
                    native[field] != row[field]
                    for field in ("semantic_task_id", "source_kind", "source_group", "split", "operation", "full_chat_tokens")
                )
                or native["output_file"] != f"{row['split']}.jsonl"
                or audit["semantic_task_id"] != row["semantic_task_id"]
                or audit["source_group"] != row["source_group"]
                or audit["context_sha256"] != row["context_sha256"]
                or audit["final_chat_tokens"] != row["full_chat_tokens"]
                or audit["assistant_tokens"] != row["supervised_tokens"]
                or audit["assistant_mask_prefix_tokens"] != row["input_tokens"]
                or hashlib.sha256(
                    json.dumps(audit["answer"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                ).hexdigest() != row["answer_sha256"]
                or audit["reader_visible_replay"] is not True
                or audit["comment_and_target_text_interventions_change_answer"] is not True
                or audit["evidence"]["long_answer_members"] < 1
            ):
                raise ValueError("P132 review selected row lacks positive pinned audit")
            eligible.append(entry)
        pins[str(manifest_path.relative_to(ROOT))] = _sha(manifest_path)
    return eligible, {"eligible_views": len(eligible), "native_manifest_pins": pins}


def _codeforge_eligible(
    entries: list[dict[str, Any]], proof_pins: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Require a pinned, content-backed scoped proof for every kept code view."""
    if not isinstance(proof_pins, list) or not proof_pins:
        raise ValueError("CodeForge proof gate requires pinned receipts")
    proofs: dict[tuple[str, str], dict[str, Any]] = {}
    for pin in proof_pins:
        receipt_path = _pinned_proof_path(pin["receipt"])
        proofs_path = _pinned_proof_path(pin["proofs"])
        receipt = json.loads(receipt_path.read_text())
        if (
            receipt.get("schema_version")
            != "longworld.codeforge-reading-proof-build.v2"
            or receipt.get("profile_id")
            != "p65-codeforge-filename-copy-content-backed-v1"
            or receipt.get("files", {}).get("proofs.jsonl") != pin["proofs"]["sha256"]
        ):
            raise ValueError("CodeForge proof receipt or profile differs")
        rows = [
            json.loads(line) for line in proofs_path.read_text().splitlines() if line
        ]
        if (
            len(rows) != receipt["primary_rows"]
            or sum(row.get("content_backed_scoped_certificate") is True for row in rows)
            != receipt["qualified_existing_semantic_tasks"]
        ):
            raise ValueError("CodeForge proof inventory differs from receipt")
        scope_pin = pin.get("scope_unified_manifest")
        if scope_pin is not None:
            scope_path = _pinned_proof_path(scope_pin)
            scope_manifest = json.loads(scope_path.read_text())
            if (
                scope_manifest.get("schema_version")
                != "longworld.unified-candidates.v1"
            ):
                raise ValueError("CodeForge proof scope manifest differs")
            index_path = scope_path.parent / "sample_index.jsonl"
            if _sha(index_path) != scope_manifest.get("files_sha256", {}).get(
                "sample_index.jsonl"
            ):
                raise ValueError("CodeForge proof scope index differs")
            scope = {
                (item["source_group"], item["semantic_task_id"]): item
                for item in (
                    json.loads(line)
                    for line in index_path.read_text().splitlines()
                    if line
                )
            }
            if len(scope) != scope_manifest.get("candidate_views"):
                raise ValueError("CodeForge proof scope repeats a task")
            rows = [
                row
                for row in rows
                if (row["source_group_id"], row["semantic_task_id"]) in scope
            ]
            scoped = {
                (row["source_group_id"], row["semantic_task_id"]): row for row in rows
            }
            if (
                set(scoped) != set(scope)
                or len(rows) != len(scoped)
                or any(
                    row["sample_id"] != scope[key]["sample_id"]
                    for key, row in scoped.items()
                )
            ):
                raise ValueError("CodeForge proof scope identity differs")
        for row in rows:
            key = row["source_group_id"], row["semantic_task_id"]
            if key in proofs:
                raise ValueError("CodeForge proof task appears in multiple receipts")
            if row.get("content_backed_scoped_certificate") is True and (
                row.get("classification")
                != "scoped_long_input_file_aggregation_certificate"
                or row.get("scoped_long_input_certificate") is not True
            ):
                raise ValueError("CodeForge positive proof lacks scoped certificate")
            proofs[key] = row
    eligible: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for entry in entries:
        candidate = entry["candidate"]
        if candidate["source_kind"] != "real_code_workflow":
            eligible.append(entry)
            continue
        counts["code_views"] += 1
        proof = proofs.get((candidate["source_group"], candidate["semantic_task_id"]))
        if proof is None:
            counts["excluded_without_proof"] += 1
            continue
        identity_matches = (
            proof["sample_id"] == candidate["sample_id"]
            and proof["split"] == candidate["split"]
            and proof["full_message_tokens"] == candidate["full_chat_tokens"]
        )
        counts["proof_record_views"] += 1
        if identity_matches:
            counts["proof_identity_match_views"] += 1
        if proof["content_backed_scoped_certificate"] is not True:
            counts["excluded_failed_proof"] += 1
            if not identity_matches:
                counts["failed_proof_identity_mismatch"] += 1
            continue
        if not identity_matches:
            counts["excluded_positive_identity_mismatch"] += 1
            continue
        counts["content_backed_views"] += 1
        eligible.append(entry)
    return eligible, {
        "kind": "codeforge_content_backed_scoped_v1",
        "proof_pins": proof_pins,
        "counts": dict(sorted(counts.items())),
        "scope": "positive scoped filename-content certificate for code views only; other kinds remain candidates",
    }


def _code_content_eligible(
    entries: list[dict[str, Any]], pin: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Admit P99 code rows only with their pinned added-line certificate."""
    native_path = _pinned_proof_path(pin["native_manifest"])
    audit_path = _pinned_proof_path(pin["native_audit"])
    unified_path = _pinned_proof_path(pin["unified_manifest"])
    mask_path = _pinned_proof_path(pin["mask_manifest"])
    native = json.loads(native_path.read_text())
    unified = json.loads(unified_path.read_text())
    mask = json.loads(mask_path.read_text())
    native_index_path = native_path.parent / "sample_index.jsonl"
    if (
        native.get("schema_version") != "longworld.p99-code-content.v1"
        or native.get("train_ready") is not False
        or native.get("files_sha256", {}).get("audit.jsonl")
        != pin["native_audit"]["sha256"]
        or native.get("files_sha256", {}).get("sample_index.jsonl")
        != _sha(native_index_path)
        or unified.get("schema_version") != "longworld.unified-candidates.v1"
        or unified.get("train_ready") is not False
        or unified.get("native_manifest_sha256") != pin["native_manifest"]["sha256"]
        or unified.get("native_audit_sha256") != pin["native_audit"]["sha256"]
        or mask.get("schema_version") != "longworld.unified-reader-mask-all.v1"
        or mask.get("train_ready") is not False
        or mask.get("source_manifest_sha256") != pin["unified_manifest"]["sha256"]
        or mask.get("source_index_sha256")
        != unified.get("files_sha256", {}).get("sample_index.jsonl")
        or mask.get("audited_views") != unified.get("candidate_views")
        or native.get("semantic_tasks") != unified.get("independent_semantic_tasks")
        or native.get("views") != unified.get("candidate_views")
    ):
        raise ValueError("P99 code-content proof receipt or mask differs")
    native_index = {}
    for line in native_index_path.read_text().splitlines():
        row = json.loads(line)
        key = row["source_group"], row["semantic_task_id"]
        if key in native_index:
            raise ValueError("P99 code-content native task repeats")
        native_index[key] = row
    proofs = {}
    for line_number, line in enumerate(audit_path.read_text().splitlines()):
        row = json.loads(line)
        key = row["source_group"], row["semantic_task_id"]
        if key in proofs:
            raise ValueError("P99 code-content proof task repeats")
        proofs[key] = (row, f"{pin['native_audit']['path']}:{line_number}")
    if len(proofs) != native["semantic_tasks"]:
        raise ValueError("P99 code-content audit inventory differs")
    if len(native_index) != len(proofs):
        raise ValueError("P99 code-content native index inventory differs")
    eligible = []
    counts: Counter[str] = Counter()
    for entry in entries:
        row = entry["candidate"]
        counts["code_content_views"] += 1
        key = row["source_group"], row["semantic_task_id"]
        proof_entry = proofs.get(key)
        proof, audit_ref = proof_entry if proof_entry is not None else ({}, None)
        native_row = native_index.get(key, {})
        if (
            row.get("source_kind") != "real_code_workflow"
            or native_row.get("source_kind") != "real_code_workflow"
            or row.get("source_name") != "p99_code_content"
            or row.get("receipt_sha256") != pin["native_manifest"]["sha256"]
            or row.get("dependency_status")
            != "content_backed_two_source_scoped_certificate"
            or row.get("native_audit_ref") != audit_ref
            or proof.get("sample_id") != row["sample_id"]
            or native_row.get("sample_id") != row["sample_id"]
            or native_row.get("split") != row["split"]
            or native_row.get("full_chat_tokens") != row["full_chat_tokens"]
            or proof.get("reader_visible_replay") is not True
            or proof.get("filename_preserving_added_code_removal_changes_answer")
            is not True
            or proof.get("all_selected_identifiers_absent_after_added_code_removal")
            is not True
            or proof.get("single_raw_16k_window_insufficient_for_both_witnesses")
            is not True
            or proof.get("evidence_token_span", 0) <= 16384
            or proof.get("evidence_token_span")
            != row.get("observed_witness_span_tokens")
        ):
            counts["excluded_without_positive_content_proof"] += 1
            continue
        eligible.append(entry)
        counts["content_backed_views"] += 1
    return eligible, {
        "kind": "p99_added_code_two_source_scoped_v1",
        "proof_pin": pin,
        "counts": dict(sorted(counts.items())),
        "scope": "two added-line witnesses and filename-preserving code removal; no unrestricted dependency claim",
    }


def _curated_code_membership(pin: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Bind expanded raw P99 proofs to the independently curated P107 subset."""
    curated_path = _pinned_proof_path(pin["curated_manifest"])
    mask_path = _pinned_proof_path(pin["curated_mask_manifest"])
    prior_path = _pinned_proof_path(pin["prior_native_manifest"])
    curated = verify_merge(curated_path.parent)
    mask = json.loads(mask_path.read_text())
    ledger_path = curated_path.parent / "quality_ledger.jsonl"
    index_path = curated_path.parent / "sample_index.jsonl"
    if (
        curated.get("curation_schema") != "longworld.p107-code-curation.v1"
        or curated.get("train_ready") is not False
        or curated.get("raw_native_manifest_sha256") != pin["native_manifest"]["sha256"]
        or curated.get("raw_unified_manifest_sha256")
        != pin["unified_manifest"]["sha256"]
        or curated.get("raw_all_mask_manifest_sha256") != pin["mask_manifest"]["sha256"]
        or curated.get("prior_native_manifest_sha256")
        != pin["prior_native_manifest"]["sha256"]
        or _sha(prior_path) != curated["prior_native_manifest_sha256"]
        or _sha(ledger_path) != curated.get("quality_ledger_sha256")
        or mask.get("schema_version") != "longworld.unified-reader-mask-all.v1"
        or mask.get("train_ready") is not False
        or mask.get("source_manifest_sha256") != pin["curated_manifest"]["sha256"]
        or mask.get("source_index_sha256")
        != curated.get("files_sha256", {}).get("sample_index.jsonl")
        or mask.get("audited_views") != curated.get("candidate_views")
    ):
        raise ValueError("P107 curator membership or mask differs")
    members = {}
    for line in index_path.read_text().splitlines():
        row = json.loads(line)
        sample_id = row["sample_id"]
        if (
            sample_id in members
            or row.get("receipt_sha256") != pin["native_manifest"]["sha256"]
        ):
            raise ValueError("P107 curated task repeats or has wrong native receipt")
        members[sample_id] = row
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    accepted = {
        row["sample_id"]: row
        for row in ledger
        if row["status"] == "accepted_new_pr_pair"
    }
    if (
        len(members) != curated["candidate_views"]
        or len(accepted) != len(members)
        or set(accepted) != set(members)
        or len(ledger) != curated["gross_reader_views"]
        or len(ledger) - len(accepted) != curated["rejected_prior_pr_pairs"]
        or any(
            accepted[sample_id][key] != row[key]
            for sample_id, row in members.items()
            for key in ("semantic_task_id", "source_group", "split")
        )
    ):
        raise ValueError("P107 curated index and quality ledger disagree")
    return members


def _code_content_eligible_many(
    entries: list[dict[str, Any]], pins: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Independently replay each raw proof package; reject unpinned receipts."""
    if not isinstance(pins, list) or not pins:
        raise ValueError("code-content proof packages must be a nonempty list")
    if any(not isinstance(pin, dict) for pin in pins):
        raise ValueError("code-content proof package must be an object")
    curated_pins = [pin for pin in pins if "curated_manifest" in pin]
    if not curated_pins or any(
        not all(
            key in pin
            for key in (
                "curated_manifest",
                "curated_mask_manifest",
                "prior_native_manifest",
            )
        )
        for pin in curated_pins
    ):
        raise ValueError("expanded code-content proofs require curator membership")
    prior_receipts = {pin["prior_native_manifest"]["sha256"] for pin in curated_pins}
    by_receipt: dict[str, dict[str, Any]] = {}
    for pin in pins:
        if not isinstance(pin, dict) or "native_manifest" not in pin:
            raise ValueError("code-content proof package lacks native receipt")
        receipt = pin["native_manifest"]["sha256"]
        if receipt in by_receipt:
            raise ValueError("code-content proof package repeats native receipt")
        if "curated_manifest" not in pin and receipt not in prior_receipts:
            raise ValueError("new code-content receipt lacks curator membership")
        by_receipt[receipt] = pin
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        grouped[entry["candidate"].get("receipt_sha256", "")].append(entry)
    eligible = []
    reports = []
    for receipt, pin in by_receipt.items():
        raw_eligible, report = _code_content_eligible(grouped.pop(receipt, []), pin)
        if "curated_manifest" in pin:
            members = _curated_code_membership(pin)
            kept = []
            for entry in raw_eligible:
                row = entry["candidate"]
                member = members.get(row["sample_id"])
                if member is None or any(
                    member.get(key) != row.get(key)
                    for key in (
                        "semantic_task_id",
                        "source_group",
                        "split",
                        "native_audit_ref",
                        "receipt_sha256",
                    )
                ):
                    continue
                kept.append(entry)
            report["curated_membership_views"] = len(kept)
            report["excluded_outside_curated_subset"] = len(raw_eligible) - len(kept)
            raw_eligible = kept
        eligible.extend(raw_eligible)
        reports.append(report)
    tasks = [
        (entry["candidate"]["source_group"], entry["candidate"]["semantic_task_id"])
        for entry in eligible
    ]
    if len(tasks) != len(set(tasks)):
        raise ValueError("code-content proof packages repeat a semantic task")
    return eligible, {
        "kind": "p99_added_code_multiple_proof_packages_v1",
        "packages": reports,
        "excluded_unpinned_receipt": sum(map(len, grouped.values())),
        "content_backed_views": len(eligible),
    }


def _choose(
    entries: list[dict[str, Any]],
    *,
    seed: int,
    max_per_group: int,
    max_per_cell: int,
    max_per_kind_by_split: dict[str, int],
    max_supervised_tokens_by_kind: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    _validate_entries(entries)
    buckets: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        buckets[_cell(entry)].append(entry)
    for bucket in buckets.values():
        bucket.sort(key=lambda entry: (_tie(entry, seed), _key(entry)))

    selected: list[dict[str, Any]] = []
    chosen_tasks: set[tuple[str, str]] = set()
    group_counts: Counter[tuple[str, str]] = Counter()
    kind_split_counts: Counter[tuple[str, str]] = Counter()
    kind_supervised_tokens: Counter[str] = Counter()
    token_caps = max_supervised_tokens_by_kind or {}
    # Iterate one pass over each occupied cell per round. Rare cells retain
    # representation while common cells cannot consume the entire selection.
    for _ in range(max_per_cell):
        progressed = False
        for cell in sorted(buckets):
            eligible = (
                entry
                for entry in buckets[cell]
                if _task(entry) not in chosen_tasks
                and group_counts[_group(entry)] < max_per_group
                and kind_split_counts[(cell[0], cell[1])]
                < max_per_kind_by_split[cell[0]]
                and kind_supervised_tokens[cell[1]]
                + entry["candidate"]["supervised_tokens"]
                <= token_caps.get(cell[1], float("inf"))
            )
            pick = min(
                eligible,
                key=lambda entry: (
                    group_counts[_group(entry)],
                    _tie(entry, seed),
                    _key(entry),
                ),
                default=None,
            )
            if pick is None:
                continue
            selected.append(pick)
            chosen_tasks.add(_task(pick))
            group_counts[_group(pick)] += 1
            kind_split_counts[(cell[0], cell[1])] += 1
            kind_supervised_tokens[cell[1]] += pick["candidate"]["supervised_tokens"]
            progressed = True
        if not progressed:
            break
    return selected


def _multiop_groups(entries: list[dict[str, Any]]) -> dict[str, int]:
    operations: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entry in entries:
        operations[_group(entry)].add(entry["candidate"]["operation"])
    return dict(
        sorted(
            Counter(
                kind for (kind, _), values in operations.items() if len(values) > 1
            ).items()
        )
    )


def _rebalance_shared_world(
    eligible: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    *,
    seed: int,
    max_per_group: int,
    max_per_cell: int,
    max_per_kind_by_split: dict[str, int],
    max_supervised_tokens_by_kind: dict[str, int] | None,
    max_source_group_loss: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Complete a second operation in selected worlds by same-cell swaps."""
    rows = list(baseline)
    before = _coverage(rows)
    chosen_ids = {_key(entry) for entry in rows}
    chosen_tasks = {_task(entry) for entry in rows}
    group_counts = Counter(_group(entry) for entry in rows)
    domain_counts = Counter(entry["candidate"]["domain"] for entry in rows)
    topic_counts = Counter(entry["candidate"]["topic"] for entry in rows)
    kind_tokens = Counter()
    for entry in rows:
        kind_tokens[entry["candidate"]["source_kind"]] += entry["candidate"][
            "supervised_tokens"
        ]
    token_caps = max_supervised_tokens_by_kind or {}
    available_ops: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_cell: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in eligible:
        available_ops[_group(entry)].add(entry["candidate"]["operation"])
        by_cell[_cell(entry)].append(entry)
    for candidates in by_cell.values():
        candidates.sort(key=lambda entry: (_tie(entry, seed), _key(entry)))
    swaps = []
    while True:
        group_ops: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        donors: dict[tuple[str, str, str, str], list[tuple[int, dict[str, Any]]]] = (
            defaultdict(list)
        )
        for rank, entry in enumerate(rows):
            group_ops[_group(entry)][entry["candidate"]["operation"]] += 1
            donors[_cell(entry)].append((rank, entry))
        best = None
        for cell, candidates in sorted(by_cell.items()):
            for added in candidates:
                group = _group(added)
                if (
                    group not in group_counts
                    or len(group_ops[group]) != 1
                    or added["candidate"]["operation"] in group_ops[group]
                    or group_counts[group] >= max_per_group
                    or _key(added) in chosen_ids
                    or _task(added) in chosen_tasks
                ):
                    continue
                for rank, removed in donors[cell]:
                    old_group = _group(removed)
                    old_op = removed["candidate"]["operation"]
                    if old_group == group or (
                        group_counts[old_group] == 1
                        and len(group_counts)
                        <= before["source_groups"] - max_source_group_loss
                    ):
                        continue
                    if (
                        len(group_ops[old_group]) == 2
                        and group_ops[old_group][old_op] == 1
                    ):
                        continue
                    if (
                        domain_counts[removed["candidate"]["domain"]] == 1
                        and removed["candidate"]["domain"]
                        != added["candidate"]["domain"]
                    ) or (
                        topic_counts[removed["candidate"]["topic"]] == 1
                        and removed["candidate"]["topic"] != added["candidate"]["topic"]
                    ):
                        continue
                    if kind_tokens[cell[1]] - removed["candidate"][
                        "supervised_tokens"
                    ] + added["candidate"]["supervised_tokens"] > token_caps.get(
                        cell[1], float("inf")
                    ):
                        continue
                    score = (
                        group_counts[old_group] == 1,
                        -len(available_ops[group]),
                        _tie(added, seed),
                        _key(added),
                        _tie(removed, seed),
                        _key(removed),
                    )
                    if best is None or score < best[0]:
                        best = (score, rank, removed, added)
        if best is None:
            break
        _, rank, removed, added = best
        rows[rank] = added
        swaps.append(
            {
                "from_sample_id": _key(removed),
                "to_sample_id": _key(added),
                "cell": "|".join(_cell(added)),
                "source_group": added["candidate"]["source_group"],
            }
        )
        chosen_ids.remove(_key(removed))
        chosen_ids.add(_key(added))
        chosen_tasks.remove(_task(removed))
        chosen_tasks.add(_task(added))
        group_counts[_group(removed)] -= 1
        if group_counts[_group(removed)] == 0:
            del group_counts[_group(removed)]
        group_counts[_group(added)] += 1
        domain_counts[removed["candidate"]["domain"]] -= 1
        domain_counts[added["candidate"]["domain"]] += 1
        topic_counts[removed["candidate"]["topic"]] -= 1
        topic_counts[added["candidate"]["topic"]] += 1
        kind_tokens[_cell(added)[1]] += (
            added["candidate"]["supervised_tokens"]
            - removed["candidate"]["supervised_tokens"]
        )
    _validate_entries(rows)
    after = _coverage(rows)
    histograms = ("by_cell", "by_split", "by_source_kind", "by_operation", "by_length")
    invariants = {name: before[name] == after[name] for name in histograms}
    if (
        not all(invariants.values())
        or len(rows) != len(chosen_ids)
        or len(rows) != len(chosen_tasks)
        or after["source_groups"] < before["source_groups"] - max_source_group_loss
        or set(after["by_domain"]) != set(before["by_domain"])
        or set(after["by_topic"]) != set(before["by_topic"])
        or any(value > max_per_group for value in group_counts.values())
        or any(value > max_per_cell for value in after["by_cell"].values())
        or any(
            count > max_per_kind_by_split[split]
            for (split, _kind), count in Counter(
                (entry["candidate"]["split"], entry["candidate"]["source_kind"])
                for entry in rows
            ).items()
        )
        or any(kind_tokens[kind] > cap for kind, cap in token_caps.items())
    ):
        raise ValueError("shared-world rebalance violated selection constraints")
    return rows, {
        "policy": "same_cell_multi_operation_completion_v1",
        "max_source_group_loss": max_source_group_loss,
        "before_rebalance": before,
        "before_source_groups": before["source_groups"],
        "after_source_groups": after["source_groups"],
        "before_multiop_by_kind": _multiop_groups(baseline),
        "after_multiop_by_kind": _multiop_groups(rows),
        "before_multiop_groups": sum(_multiop_groups(baseline).values()),
        "after_multiop_groups": sum(_multiop_groups(rows).values()),
        "histogram_invariants": invariants,
        "domain_topic_label_sets_preserved": True,
        "swaps": swaps,
    }


def select(
    index_dir: Path,
    output_dir: Path,
    *,
    seed: int,
    max_per_group: int,
    max_per_cell: int,
    max_per_kind_by_split: dict[str, int],
    max_supervised_tokens_by_kind: dict[str, int] | None = None,
    codeforge_proofs: list[dict[str, Any]] | None = None,
    code_content_proof: dict[str, Any] | None = None,
    code_content_proofs: list[dict[str, Any]] | None = None,
    shared_world_rebalance: dict[str, int] | None = None,
    require_dependency_status: bool = False,
    exclude_dependency_statuses: list[str] | None = None,
) -> dict[str, Any]:
    if (
        output_dir.exists()
        or type(seed) is not int
        or seed < 0
        or type(max_per_group) is not int
        or max_per_group < 1
        or type(max_per_cell) is not int
        or max_per_cell < 1
        or set(max_per_kind_by_split) != {"train", "eval"}
        or any(
            type(value) is not int or value < 1
            for value in max_per_kind_by_split.values()
        )
        or (
            max_supervised_tokens_by_kind is not None
            and (
                not isinstance(max_supervised_tokens_by_kind, dict)
                or any(
                    not isinstance(kind, str)
                    or not kind
                    or type(cap) is not int
                    or cap < 1
                    for kind, cap in max_supervised_tokens_by_kind.items()
                )
            )
        )
        or (
            shared_world_rebalance is not None
            and (
                not isinstance(shared_world_rebalance, dict)
                or set(shared_world_rebalance) != {"max_source_group_loss"}
                or type(shared_world_rebalance["max_source_group_loss"]) is not int
                or not 0 <= shared_world_rebalance["max_source_group_loss"] <= 5
            )
        )
        or (code_content_proof is not None and code_content_proofs is not None)
        or type(require_dependency_status) is not bool
        or (
            exclude_dependency_statuses is not None
            and (
                not isinstance(exclude_dependency_statuses, list)
                or not exclude_dependency_statuses
                or any(
                    not isinstance(status, str) or not status
                    for status in exclude_dependency_statuses
                )
                or len(set(exclude_dependency_statuses)) != len(exclude_dependency_statuses)
            )
        )
    ):
        raise ValueError("new output, nonnegative seed and positive caps required")
    source = verify_index(index_dir)
    entries = _read_entries(index_dir)
    if len(entries) != source["candidate_views"]:
        raise ValueError("candidate reference count changed")
    if max_supervised_tokens_by_kind and not set(max_supervised_tokens_by_kind) <= {
        entry["candidate"]["source_kind"] for entry in entries
    }:
        raise ValueError("supervised token cap names an absent source kind")
    if codeforge_proofs is not None:
        _validate_entries(entries)
    content_entries = [
        entry
        for entry in entries
        if entry["candidate"].get("source_name") == "p99_code_content"
    ]
    review_entries = [
        entry
        for entry in entries
        if entry["candidate"].get("source_name") == "p132_review_diff_join"
    ]
    legacy_entries = [
        entry
        for entry in entries
        if entry["candidate"].get("source_name")
        not in {"p99_code_content", "p132_review_diff_join"}
    ]
    eligible, quality_gate = (
        _codeforge_eligible(legacy_entries, codeforge_proofs)
        if codeforge_proofs is not None
        else (legacy_entries, None)
    )
    if content_entries:
        content_eligible, content_gate = (
            _code_content_eligible_many(content_entries, code_content_proofs)
            if code_content_proofs is not None
            else _code_content_eligible(content_entries, code_content_proof)
            if code_content_proof is not None
            else (
                [],
                {
                    "kind": "p99_added_code_two_source_scoped_v1",
                    "counts": {"excluded_without_pin": len(content_entries)},
                },
            )
        )
        eligible.extend(content_eligible)
        quality_gate = {"legacy": quality_gate, "code_content": content_gate}
    if review_entries:
        review_eligible, review_gate = _p132_review_eligible(review_entries)
        eligible.extend(review_eligible)
        quality_gate = {"prior": quality_gate, "review_diff": review_gate}
    if require_dependency_status:
        missing_dependency = sum(
            not entry["candidate"].get("dependency_status") for entry in eligible
        )
        eligible = [
            entry for entry in eligible if entry["candidate"].get("dependency_status")
        ]
        quality_gate = {
            "prior": quality_gate,
            "dependency_status_present": {
                "excluded_missing_status": missing_dependency,
                "claim_limit": "status presence is not a reader dependency certificate",
            },
        }
    if exclude_dependency_statuses is not None:
        excluded = Counter(
            entry["candidate"].get("dependency_status")
            for entry in eligible
            if entry["candidate"].get("dependency_status")
            in exclude_dependency_statuses
        )
        eligible = [
            entry for entry in eligible
            if entry["candidate"].get("dependency_status")
            not in exclude_dependency_statuses
        ]
        quality_gate = {
            "prior": quality_gate,
            "excluded_dependency_statuses": {
                "statuses": exclude_dependency_statuses,
                "counts": dict(sorted(excluded.items())),
                "claim_limit": "remaining status labels do not certify unrestricted reader necessity",
            },
        }
    selected = _choose(
        eligible,
        seed=seed,
        max_per_group=max_per_group,
        max_per_cell=max_per_cell,
        max_per_kind_by_split=max_per_kind_by_split,
        max_supervised_tokens_by_kind=max_supervised_tokens_by_kind,
    )
    rebalance_report = None
    if shared_world_rebalance is not None:
        selected, rebalance_report = _rebalance_shared_world(
            eligible,
            selected,
            seed=seed,
            max_per_group=max_per_group,
            max_per_cell=max_per_cell,
            max_per_kind_by_split=max_per_kind_by_split,
            max_supervised_tokens_by_kind=max_supervised_tokens_by_kind,
            max_source_group_loss=shared_world_rebalance["max_source_group_loss"],
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p90-balanced-", dir=output_dir.parent
    ) as raw:
        temp = Path(raw)
        selected_path = temp / "selected_refs.jsonl"
        with selected_path.open("x", encoding="utf-8") as stream:
            for rank, entry in enumerate(selected):
                stream.write(
                    json.dumps(
                        {"selection_rank": rank, **entry},
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        manifest = {
            "schema_version": SCHEMA,
            "input_manifest_sha256": _sha(index_dir / "manifest.json"),
            "input_refs_sha256": source["refs_sha256"],
            "selection_seed": seed,
            "max_per_source_group": max_per_group,
            "max_per_cell": max_per_cell,
            "max_per_source_kind_by_split": max_per_kind_by_split,
            **(
                {"require_dependency_status": True}
                if require_dependency_status else {}
            ),
            **(
                {"exclude_dependency_statuses": exclude_dependency_statuses}
                if exclude_dependency_statuses is not None else {}
            ),
            **(
                {"max_supervised_tokens_by_kind": max_supervised_tokens_by_kind}
                if max_supervised_tokens_by_kind is not None
                else {}
            ),
            "before": _coverage(entries),
            **(
                {"eligible": _coverage(eligible), "quality_gate": quality_gate}
                if quality_gate is not None
                else {}
            ),
            "after": _coverage(selected),
            **(
                {"shared_world_rebalance": rebalance_report} if rebalance_report else {}
            ),
            "selected_refs_sha256": _sha(selected_path),
            "selection_scope": "candidate_only_source_aware_balancing",
            "train_ready": False,
        }
        _json(temp / "manifest.json", manifest)
        os.rename(temp, output_dir)
    return manifest


def verify_selection(
    index_dir: Path,
    output_dir: Path,
    *,
    seed: int,
    max_per_group: int,
    max_per_cell: int,
    max_per_kind_by_split: dict[str, int],
    max_supervised_tokens_by_kind: dict[str, int] | None = None,
    codeforge_proofs: list[dict[str, Any]] | None = None,
    code_content_proof: dict[str, Any] | None = None,
    code_content_proofs: list[dict[str, Any]] | None = None,
    shared_world_rebalance: dict[str, int] | None = None,
    require_dependency_status: bool = False,
    exclude_dependency_statuses: list[str] | None = None,
) -> dict[str, Any]:
    stored = json.loads((output_dir / "manifest.json").read_text())
    if stored.get("selected_refs_sha256") != _sha(output_dir / "selected_refs.jsonl"):
        raise ValueError("selected references changed")
    with tempfile.TemporaryDirectory(
        prefix="p90-balanced-verify-", dir=output_dir.parent
    ) as raw:
        rebuilt_dir = Path(raw) / "selection"
        rebuilt = select(
            index_dir,
            rebuilt_dir,
            seed=seed,
            max_per_group=max_per_group,
            max_per_cell=max_per_cell,
            max_per_kind_by_split=max_per_kind_by_split,
            max_supervised_tokens_by_kind=max_supervised_tokens_by_kind,
            codeforge_proofs=codeforge_proofs,
            code_content_proof=code_content_proof,
            code_content_proofs=code_content_proofs,
            shared_world_rebalance=shared_world_rebalance,
            require_dependency_status=require_dependency_status,
            exclude_dependency_statuses=exclude_dependency_statuses,
        )
        if (
            rebuilt != stored
            or _sha(rebuilt_dir / "selected_refs.jsonl")
            != stored["selected_refs_sha256"]
        ):
            raise ValueError("selection differs from frozen candidate index")
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/p90_balanced_selection_v1.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    index_dir = ROOT / config["input_index"]
    kwargs = {
        "seed": config["seed"],
        "max_per_group": config["max_per_source_group"],
        "max_per_cell": config["max_per_cell"],
        "max_per_kind_by_split": config["max_per_source_kind_by_split"],
        "max_supervised_tokens_by_kind": config.get("max_supervised_tokens_by_kind"),
        "codeforge_proofs": config.get("codeforge_proofs"),
        "code_content_proof": config.get("code_content_proof"),
        "code_content_proofs": config.get("code_content_proofs"),
        "shared_world_rebalance": config.get("shared_world_rebalance"),
        "require_dependency_status": config.get("require_dependency_status", False),
        "exclude_dependency_statuses": config.get("exclude_dependency_statuses"),
    }
    result = (
        verify_selection(index_dir, args.output, **kwargs)
        if args.verify_only
        else select(index_dir, args.output, **kwargs)
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
