#!/usr/bin/env python3
"""Fail the generation run if clones, facts dumps, or empty long buckets leak in."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    attach_attestation,
    attestation_key_from_env,
    production_attestation_errors,
    sanitized_attestation_environment,
    verify_attestation,
)
from longworld.core.production_trust import verify_production_approval_from_env
from longworld.core.promotion import (
    QUALITY_REPORT_BINDING_REVISION,
    RELEASE_GATE_PURPOSE,
    RELEASE_GATE_REVISION,
    RELEASE_GATE_SCHEMA,
    exact_token_band_reject_reason,
    promoted_row_set_sha256,
    promoted_split_row_set_sha256,
)
from longworld.core.record_contract import replay_bundle_binding_valid, sft_row_errors
from longworld.core.release_profile import (
    ReleaseProfile,
    issuable_release_profile,
    release_profile,
    release_profile_sha256,
)
from longworld.core.tokenizer_assets import (
    TokenizerAssetError,
    resolved_tokenizer_asset_manifest_sha256,
)

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ReleaseProduct:
    report: dict
    train_rows: list[dict]
    eval_rows: list[dict]
    metrics: dict
    source_file_sha256: dict[str, str]
    production_approval: dict | None = None


def _requires_relation_provenance_split(profile: ReleaseProfile | None) -> bool:
    return bool(profile is not None and profile.profile_id.startswith(("p7-", "p10-")))


def _create_release_gate_receipt(
    product: ReleaseProduct,
    release_profile_id: str,
    *,
    attestation_key: bytes | None,
) -> dict:
    """Certify that the exact signed release bytes passed the complete profile gate."""
    profile = release_profile(release_profile_id)
    errors = product.metrics.get("errors")
    if (
        errors != []
        or product.report.get("data_stage") != "train_ready"
        or product.report.get("release_profile_id") != release_profile_id
        or product.report.get("release_profile_sha256")
        != release_profile_sha256(release_profile_id)
        or product.metrics.get("n_worlds_observed") != profile.expected_promoted_worlds
        or not isinstance(product.metrics.get("n_rows"), int)
        or int(product.metrics["n_rows"]) <= 0
    ):
        raise ValueError("cannot certify a release product with gate errors")
    if set(product.source_file_sha256) != {
        "quality_report.json",
        "train.jsonl",
        "eval.jsonl",
    }:
        raise ValueError("release gate source binding is incomplete")
    if attestation_key is None:
        raise ValueError("auditor attestation key is required for gate receipt")
    metrics_sha256 = hashlib.sha256(
        json.dumps(
            product.metrics,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    receipt = attach_attestation(
        {
            "schema_version": RELEASE_GATE_SCHEMA,
            "gate_revision": RELEASE_GATE_REVISION,
            "release_profile_id": release_profile_id,
            "release_profile_sha256": release_profile_sha256(release_profile_id),
            "tokenizer_model_id": profile.tokenizer_model_id,
            "tokenizer_revision": profile.tokenizer_revision,
            "tokenizer_asset_manifest_sha256": (
                profile.tokenizer_asset_manifest_sha256
            ),
            "predecessor_profile_id": profile.predecessor_profile_id,
            "quality_report_sha256": product.source_file_sha256["quality_report.json"],
            "source_file_sha256": dict(sorted(product.source_file_sha256.items())),
            "metrics_sha256": metrics_sha256,
            "n_worlds": product.metrics.get("n_worlds_observed"),
            "n_rows": product.metrics.get("n_rows"),
            "production_approval": product.production_approval,
            "ok": True,
            "errors": [],
        },
        attestation_key,
        purpose=RELEASE_GATE_PURPOSE,
    )
    identity = receipt.get("attestation")
    if (
        not isinstance(identity, dict)
        or identity.get("scheme") != ATTESTATION_V2_SCHEME
        or identity.get("role") != "auditor"
        or identity.get("environment") != profile.environment
        or not verify_attestation(
            receipt, attestation_key, purpose=RELEASE_GATE_PURPOSE
        )
    ):
        raise ValueError("release gate receipt requires a complete auditor identity")
    return receipt


def _requires_substantial_real_proof_growth(profile: ReleaseProfile | None) -> bool:
    return bool(profile is not None and profile.profile_id.startswith(("p7-", "p10-")))


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def validate_gate_receipt_output(data_dir: Path, receipt_path: Path) -> None:
    bound_sources = {
        (data_dir / name).resolve()
        for name in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }
    if receipt_path.resolve() in bound_sources:
        raise ValueError("gate receipt cannot overwrite a bound release source")


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _jsonl_from_bytes(raw: bytes, path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from error
        if not isinstance(row, dict):
            raise TypeError(f"{path}:{line_number}: expected a JSON object")
        rows.append(row)
    return rows


def load_release_product(data_dir: Path, release_profile_id: str) -> ReleaseProduct:
    """Load and fully re-evaluate the signed train+eval release as one product."""
    release_profile(release_profile_id)
    report_path = data_dir / "quality_report.json"
    train_path = data_dir / "train.jsonl"
    eval_path = data_dir / "eval.jsonl"
    missing = [
        str(path) for path in (report_path, train_path, eval_path) if not path.is_file()
    ]
    if missing:
        raise ValueError("release product is incomplete: " + ", ".join(missing))
    try:
        source_bytes = {
            path.name: path.read_bytes()
            for path in (report_path, train_path, eval_path)
        }
        report = json.loads(source_bytes[report_path.name])
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("quality report is invalid JSON") from error
    if not isinstance(report, dict):
        raise TypeError("quality report must be a JSON object")
    train_rows = _jsonl_from_bytes(source_bytes[train_path.name], train_path)
    eval_rows = _jsonl_from_bytes(source_bytes[eval_path.name], eval_path)
    if any(row.get("split") != "train" for row in train_rows) or any(
        row.get("split") != "eval" for row in eval_rows
    ):
        raise ValueError("release product split file mismatch")
    metrics = evaluate_quality(
        report,
        [*train_rows, *eval_rows],
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        max_near_dup_sentence_ratio=1.0,
        release_profile_id=release_profile_id,
    )
    if metrics["errors"]:
        raise ValueError(
            "release product failed quality gate: " + "; ".join(metrics["errors"])
        )
    source_file_sha256 = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in source_bytes.items()
    }
    profile = release_profile(release_profile_id)
    production_approval = None
    if profile.environment == "production":
        production_approval = verify_production_approval_from_env(
            release_profile_id=release_profile_id,
            release_profile_sha256=release_profile_sha256(release_profile_id),
            source_file_sha256=source_file_sha256,
            release_selection_sha256=str(report.get("release_selection_sha256") or ""),
        )
    return ReleaseProduct(
        report,
        train_rows,
        eval_rows,
        metrics,
        source_file_sha256,
        production_approval,
    )


def load_and_create_release_gate_receipt(
    data_dir: Path,
    release_profile_id: str,
    *,
    attestation_key: bytes | None,
) -> tuple[ReleaseProduct, dict]:
    """Read, fully gate, and certify one immutable release snapshot."""
    product = load_release_product(data_dir, release_profile_id)
    receipt = _create_release_gate_receipt(
        product,
        release_profile_id,
        attestation_key=attestation_key,
    )
    return product, receipt


def _conversation_digest(row: dict) -> str:
    payload = {
        "context": row.get("context") or "",
        "question": row.get("question") or "",
        "answer": str(row.get("answer") or ""),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _prompt_digest(row: dict) -> str:
    """Hash the serialized user prompt without its target answer."""
    return hashlib.sha256(str(row.get("context") or "").encode("utf-8")).hexdigest()


@lru_cache(maxsize=4)
def _load_exact_tokenizer(model_id: str, revision: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        model_id,
        revision=revision,
        trust_remote_code=False,
        local_files_only=True,
    )


def _tokenizer_context_tokens(context: str, model_id: str, revision: str) -> int:
    with sanitized_attestation_environment():
        tokenizer = _load_exact_tokenizer(model_id, revision)
        return len(tokenizer.encode(context, add_special_tokens=False))


def _has_exact_band_metadata(
    row: dict,
    *,
    expected_model_id: str | None = None,
    expected_revision: str | None = None,
    expected_asset_manifest_sha256: str | None = None,
) -> bool:
    try:
        tokens = int(row.get("tokenizer_context_tokens") or 0)
    except (TypeError, ValueError):
        return False
    revision = str(row.get("tokenizer_revision") or "")
    length_bucket = str(row.get("length_bucket") or "")
    metadata_valid = (
        exact_token_band_reject_reason(length_bucket, tokens) is None
        and length_bucket in {"16k", "32k", "64k"}
        and bool(str(row.get("tokenizer_model_id") or ""))
        and len(revision) == 40
        and all(character in "0123456789abcdef" for character in revision)
    )
    if not metadata_valid:
        return False
    model_id = str(row.get("tokenizer_model_id") or "")
    if expected_model_id is None or expected_revision is None:
        return True
    if expected_asset_manifest_sha256 is None:
        if model_id != expected_model_id or revision != expected_revision:
            return False
        try:
            return (
                _tokenizer_context_tokens(
                    str(row.get("context") or ""), model_id, revision
                )
                == tokens
            )
        except (ImportError, OSError, RuntimeError, ValueError):
            return False
    declared_asset_digest = str(row.get("tokenizer_asset_manifest_sha256") or "")
    if (
        len(expected_asset_manifest_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_asset_manifest_sha256
        )
        or model_id != expected_model_id
        or revision != expected_revision
        or declared_asset_digest != expected_asset_manifest_sha256
    ):
        return False
    try:
        loaded_asset_digest = resolved_tokenizer_asset_manifest_sha256(
            model_id, revision
        )
        replayed_tokens = _tokenizer_context_tokens(
            str(row.get("context") or ""), model_id, revision
        )
        replayed_asset_digest = resolved_tokenizer_asset_manifest_sha256(
            model_id, revision
        )
        return (
            loaded_asset_digest
            == replayed_asset_digest
            == expected_asset_manifest_sha256
            and replayed_tokens == tokens
        )
    except (ImportError, OSError, RuntimeError, TokenizerAssetError, ValueError):
        return False


def _has_exact_64k_metadata(
    row: dict,
    *,
    expected_model_id: str | None = None,
    expected_revision: str | None = None,
    expected_asset_manifest_sha256: str | None = None,
) -> bool:
    """Compatibility wrapper for existing release reporting."""
    return row.get("length_bucket") == "64k" and _has_exact_band_metadata(
        row,
        expected_model_id=expected_model_id,
        expected_revision=expected_revision,
        expected_asset_manifest_sha256=expected_asset_manifest_sha256,
    )


def _exact_band_metadata_errors(
    rows: list[dict],
    *,
    expected_model_id: str,
    expected_revision: str,
    expected_asset_manifest_sha256: str | None = None,
) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows):
        length_bucket = str(row.get("length_bucket") or "")
        if length_bucket not in {"16k", "32k", "64k"}:
            continue
        if not _has_exact_band_metadata(
            row,
            expected_model_id=expected_model_id,
            expected_revision=expected_revision,
            expected_asset_manifest_sha256=expected_asset_manifest_sha256,
        ):
            errors.append(
                f"invalid_exact_{length_bucket}:"
                f"{row.get('query_id') or row.get('world_id') or index}"
            )
    return errors


def _proof_metadata_errors(rows: list[dict]) -> list[str]:
    """Require serialized proof metadata to agree with replayed graph stats."""
    errors: list[str] = []
    for index, row in enumerate(rows):
        label = str(row.get("query_id") or row.get("world_id") or index)
        graph = row.get("graph")
        if not isinstance(graph, dict) or any(
            field not in graph for field in ("proof_depth", "hop_count")
        ):
            errors.append(f"missing_graph_metadata:{label}")
            continue
        difficulty = row.get("difficulty")
        if not isinstance(difficulty, dict) or difficulty.get(
            "proof_depth"
        ) != graph.get("proof_depth"):
            errors.append(f"graph_proof_depth_mismatch:{label}")
        if row.get("hop_count") != graph.get("hop_count"):
            errors.append(f"graph_hop_count_mismatch:{label}")
    return errors


def _semantic_growth_errors(
    rows: list[dict],
    min_internal_growth: int,
    max_generic_growth_share: float,
    *,
    require_substantial_real_proof_growth: bool = False,
) -> list[str]:
    def intrinsic_long_source(row: dict, semantic: dict, estimated_total: int) -> bool:
        exact_tokens = int(row.get("tokenizer_context_tokens") or 0)
        exact_span = int(row.get("tokenizer_evidence_span_tokens") or 0)
        span_is_long = (
            exact_span >= int(exact_tokens * 0.9)
            if exact_tokens
            and (exact_span or row.get("query_type") == "sec_financial_reconstruction")
            else int(row.get("evidence_span_tokens") or 0) >= int(estimated_total * 0.9)
        )
        return bool(
            row.get("real_source_verified") is True
            and float(row.get("real_source_token_ratio") or 0.0) >= 0.9
            and int(semantic.get("generic_background") or 0) == 0
            and int(semantic.get("event_bearing") or 0) >= int(estimated_total * 0.9)
            and span_is_long
            and int(row.get("context_source_relation_count") or 0) >= 1
            and int(row.get("strict_support_event_count") or 0) >= 4
            and int((row.get("graph") or {}).get("proof_depth") or 0) >= 4
        )

    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        group_id = str(
            (
                row.get("semantic_growth_group_id")
                if row.get("real_source_verified") is True
                else row.get("base_task_id")
            )
            or ""
        )
        if not group_id or not isinstance(row.get("semantic_tokens"), dict):
            continue
        key = (
            group_id,
            str(row.get("query_timing") or ""),
            str(row.get("view") or ""),
            str(row.get("split") or ""),
        )
        groups[key].append(row)

    errors: list[str] = []
    for row in rows:
        semantic = row.get("semantic_tokens")
        if not isinstance(semantic, dict):
            continue
        estimated_total = int(
            row.get("actual_context_tokens")
            or (row.get("difficulty") or {}).get("context_tokens")
            or 0
        )
        total = (
            int(row.get("tokenizer_context_tokens") or 0)
            if _has_exact_64k_metadata(row)
            else estimated_total
        )
        if total < 64000:
            continue
        workflow_tokens = int(semantic.get("event_bearing") or 0) + int(
            semantic.get("internal") or 0
        )
        generic_share = int(semantic.get("generic_background") or 0) / max(1, total)
        if (
            workflow_tokens < min_internal_growth
            or generic_share > max_generic_growth_share
        ):
            errors.append(
                "semantic_density:"
                f"{row.get('base_task_id') or row.get('world_id') or '?'}:"
                f"workflow={workflow_tokens}:generic_share={generic_share:.3f}"
            )
        if row.get("real_source_verified") is True and not intrinsic_long_source(
            row, semantic, estimated_total
        ):
            key = (
                str(row.get("semantic_growth_group_id") or ""),
                str(row.get("query_timing") or ""),
                str(row.get("view") or ""),
                str(row.get("split") or ""),
            )
            lower_band = any(
                sibling.get("length_bucket") in {"16k", "32k"}
                for sibling in groups.get(key, [])
            )
            if not lower_band:
                errors.append(
                    "real_64k_missing_lower_band:"
                    f"{row.get('base_task_id') or row.get('world_id') or '?'}:"
                    f"{row.get('view') or '?'}"
                )
    for key, group in groups.items():
        group.sort(
            key=lambda row: int(
                row.get("actual_context_tokens")
                or (row.get("difficulty") or {}).get("context_tokens")
                or 0
            )
        )
        bands: list[list[dict]] = []
        for row in group:
            if not bands or bands[-1][0].get("length_bucket") != row.get(
                "length_bucket"
            ):
                bands.append([])
            bands[-1].append(row)
        growth_pairs = (
            (before, after)
            for before_band, after_band in pairwise(bands)
            for before in before_band
            for after in after_band
        )
        for before, after in growth_pairs:
            before_sem = before["semantic_tokens"]
            after_sem = after["semantic_tokens"]
            workflow_growth = (
                int(after_sem.get("event_bearing") or 0)
                + int(after_sem.get("internal") or 0)
                - int(before_sem.get("event_bearing") or 0)
                - int(before_sem.get("internal") or 0)
            )
            generic_growth = max(
                0,
                int(after_sem.get("generic_background") or 0)
                - int(before_sem.get("generic_background") or 0),
            )
            before_total = int(
                before.get("actual_context_tokens")
                or (before.get("difficulty") or {}).get("context_tokens")
                or 0
            )
            after_total = int(
                after.get("actual_context_tokens")
                or (after.get("difficulty") or {}).get("context_tokens")
                or 0
            )
            total_growth = max(1, after_total - before_total)
            generic_share = generic_growth / total_growth
            if (
                workflow_growth < min_internal_growth
                or generic_share > max_generic_growth_share
            ):
                errors.append(
                    "semantic_growth:"
                    f"{key[0]}:{before.get('length_bucket')}->"
                    f"{after.get('length_bucket')}:workflow={workflow_growth}:"
                    f"generic_share={generic_share:.3f}"
                )
            if after.get("real_source_verified") is True:
                causal_growth = int(
                    after.get("context_source_relation_count") or 0
                ) - int(before.get("context_source_relation_count") or 0)
                if causal_growth <= 0:
                    errors.append(
                        "real_causal_history_growth:"
                        f"{key[0]}:{before.get('length_bucket')}"
                        f"->{after.get('length_bucket')}:growth={causal_growth}"
                    )
                strict_support_growth = int(
                    after.get("strict_support_event_count") or 0
                ) - int(before.get("strict_support_event_count") or 0)
                if strict_support_growth <= 0:
                    errors.append(
                        "real_strict_support_growth:"
                        f"{key[0]}:{before.get('length_bucket')}"
                        f"->{after.get('length_bucket')}:"
                        f"growth={strict_support_growth}"
                    )
                before_depth = int((before.get("graph") or {}).get("proof_depth") or 0)
                after_depth = int((after.get("graph") or {}).get("proof_depth") or 0)
                if after_depth <= before_depth:
                    errors.append(
                        "real_proof_depth_growth:"
                        f"{key[0]}:{before.get('length_bucket')}"
                        f"->{after.get('length_bucket')}:"
                        f"growth={after_depth - before_depth}"
                    )
                if (
                    int(after_sem.get("proof_bearing") or 0)
                    + int(after_sem.get("causal_supporting") or 0)
                    <= 0
                ):
                    errors.append(
                        "real_proof_missing:"
                        f"{key[0]}:{before.get('length_bucket')}"
                        f"->{after.get('length_bucket')}"
                    )
                before_proof_tokens = int(before_sem.get("proof_bearing") or 0) + int(
                    before_sem.get("causal_supporting") or 0
                )
                after_proof_tokens = int(after_sem.get("proof_bearing") or 0) + int(
                    after_sem.get("causal_supporting") or 0
                )
                if after_proof_tokens <= before_proof_tokens:
                    errors.append(
                        "real_proof_token_growth:"
                        f"{key[0]}:{before.get('length_bucket')}"
                        f"->{after.get('length_bucket')}:"
                        f"growth={after_proof_tokens - before_proof_tokens}"
                    )
                elif require_substantial_real_proof_growth:
                    proof_growth = after_proof_tokens - before_proof_tokens
                    minimum_proof_growth = max(256, (total_growth + 19) // 20)
                    if proof_growth < minimum_proof_growth:
                        errors.append(
                            "real_proof_growth_share:"
                            f"{key[0]}:{before.get('length_bucket')}"
                            f"->{after.get('length_bucket')}:"
                            f"growth={proof_growth}<minimum={minimum_proof_growth}"
                        )
    return errors


def _real_exact_64k_worlds_by_domain(
    real_64k_rows: list[dict], domain_order: list[str]
) -> dict[str, int]:
    return {
        domain: len(
            {
                str(row["world_id"])
                for row in real_64k_rows
                if row.get("domain") == domain and row.get("world_id")
            }
        )
        for domain in domain_order
    }


def _split_leakage_errors(rows: list[dict]) -> list[str]:
    strategies = {
        str(
            row.get("split_strategy")
            or (row.get("holdout") or {}).get("strategy")
            or "world"
        )
        for row in rows
    }
    if len(strategies) != 1:
        return ["mixed_split_strategies"]
    strategy = next(iter(strategies), "world")
    values: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        split = str(row.get("split") or "")
        if split not in {"train", "eval"}:
            continue
        if strategy == "world":
            key = str(row.get("world_id") or "")
        elif strategy == "topology":
            key = str(row.get("canonical_topology") or "")
        elif strategy == "source_family":
            key = (
                "+".join(sorted(str(v) for v in row.get("source_family_ids") or []))
                or "no_source_family"
            )
        elif strategy == "domain_composition":
            key = f"{row.get('domain') or ''}|{row.get('motif') or 'single'}"
        else:
            return [f"unsupported_split_strategy:{strategy}"]
        if key:
            values[split].add(key)
    overlap = values.get("train", set()) & values.get("eval", set())
    return [f"split_leak_{strategy}:{value}" for value in sorted(overlap)]


_CANDIDATE_GATES = (
    "semantic_sufficient",
    "strict_executable_sufficient",
    "counterfactual_replay_sufficient",
    "contiguous_windows_insufficient",
    "bm25_top1_insufficient",
    "bm25_topk_insufficient",
    "lexical_tfidf_topk_insufficient",
    "essential_single_doc_insufficient",
    "essential_surface_gold_free",
    "essential_text_grounded",
)


def _row_contract_errors(rows: list[dict]) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows):
        stage = str(row.get("data_stage") or "")
        if stage not in {"candidate", "train_ready"}:
            continue
        row_id = str(row.get("query_id") or index)
        if not row.get("base_task_id"):
            errors.append(f"missing_base_task_id:{row_id}")
        if not isinstance(row.get("semantic_tokens"), dict):
            errors.append(f"missing_semantic_tokens:{row_id}")
        verification = row.get("verification")
        if not isinstance(verification, dict):
            errors.append(f"missing_verification:{row_id}")
        else:
            required = list(_CANDIDATE_GATES)
            if stage == "train_ready":
                required.extend(("surface_match", "embedding_topk_insufficient"))
            failed = [gate for gate in required if verification.get(gate) is not True]
            if failed:
                errors.append(f"row_verification:{row_id}:{','.join(failed)}")
        view = row.get("view_verification")
        if not isinstance(view, dict):
            errors.append(f"missing_view_verification:{row_id}")
        else:
            view_failed = [
                gate
                for gate in (
                    "essential_present",
                    "semantic_text_grounded",
                    "classification_ok",
                    "global_proof_green",
                    "production_eligible",
                )
                if view.get(gate) is not True
            ]
            if view.get("strict_replay_answer") != view.get("expected_answer"):
                view_failed.append("strict_replay_answer")
            if view_failed:
                errors.append(f"row_view_verification:{row_id}:{','.join(view_failed)}")
    return errors


def _release_attestation_error(
    payload: dict,
    *,
    purpose: str,
    role: str,
    environment: str,
    label: str,
) -> str | None:
    attestation = payload.get("attestation")
    if not isinstance(attestation, dict) or any(
        (
            attestation.get("scheme") != ATTESTATION_V2_SCHEME,
            attestation.get("purpose") != purpose,
            attestation.get("role") != role,
            attestation.get("environment") != environment,
        )
    ):
        return f"release_attestation_identity:{label}"
    return None


def evaluate_quality(
    report: dict,
    rows: list[dict],
    *,
    min_retention: float,
    max_retention: float,
    max_boilerplate: float,
    max_pulse: float,
    min_internal_growth: int,
    max_generic_growth_share: float,
    max_near_dup_sentence_ratio: float = 0.25,
    expected_promoted_worlds: int | None = None,
    release_profile_id: str | None = None,
) -> dict:
    """Evaluate unique content and semantic growth; length alone is not quality."""
    errors: list[str] = []

    def requires_promotion(row: dict) -> bool:
        attestation = row.get("attestation")
        purpose = (
            str(attestation.get("purpose") or "")
            if isinstance(attestation, dict)
            else ""
        )
        return (
            str(row.get("data_stage") or "") in {"candidate", "train_ready"}
            or row.get("training_objective") == "sft"
            or isinstance(row.get("promotion"), dict)
            or purpose in {"candidate_row", "sft_row"}
        )

    report_attestation = report.get("attestation")
    strict_report = (
        isinstance(report_attestation, dict)
        or str(report.get("data_stage") or "") in {"candidate", "train_ready"}
        or str(report.get("schema_version") or "").startswith("p3")
        or any(requires_promotion(row) for row in rows)
        or bool(release_profile_id)
    )
    profile: ReleaseProfile | None = None
    if strict_report:
        if not release_profile_id:
            errors.append("missing_release_profile")
        else:
            try:
                profile = release_profile(release_profile_id)
            except ValueError:
                errors.append(f"unknown_release_profile:{release_profile_id}")
            if report.get("release_profile_id") != release_profile_id:
                errors.append("release_profile_report_mismatch")
            if report.get("release_profile_sha256") != release_profile_sha256(
                release_profile_id
            ):
                errors.append("release_profile_sha256_mismatch")
    if profile is not None:
        if profile.environment == "production":
            try:
                issuable_release_profile(profile.profile_id)
            except ValueError:
                errors.append(
                    f"superseded_production_release_profile:{profile.profile_id}"
                )
        min_retention = profile.min_retention
        max_retention = profile.max_retention
        max_boilerplate = profile.max_boilerplate
        max_pulse = profile.max_pulse
        min_internal_growth = profile.min_internal_growth
        max_generic_growth_share = profile.max_generic_growth_share
        max_near_dup_sentence_ratio = profile.max_near_dup_sentence_ratio
        if profile.environment == "production":
            errors.extend(
                f"production_attestation:{error}"
                for error in production_attestation_errors()
            )
        identity_error = _release_attestation_error(
            report,
            purpose="quality_report",
            role="report",
            environment=profile.environment,
            label="quality_report",
        )
        if identity_error:
            errors.append(identity_error)
        for index, row in enumerate(rows):
            stage = str(row.get("data_stage") or "")
            purpose = "candidate_row" if stage == "candidate" else "sft_row"
            role = "candidate" if stage == "candidate" else "promotion"
            identity_error = _release_attestation_error(
                row,
                purpose=purpose,
                role=role,
                environment=profile.environment,
                label=str(row.get("query_id") or index),
            )
            if identity_error:
                errors.append(identity_error)
            if (row.get("promotion") or {}).get("dense_top_k") != profile.dense_top_k:
                errors.append(
                    f"release_dense_top_k:{row.get('query_id') or index}:"
                    f"expected={profile.dense_top_k}"
                )
        errors.extend(
            _exact_band_metadata_errors(
                rows,
                expected_model_id=profile.tokenizer_model_id,
                expected_revision=profile.tokenizer_revision,
                expected_asset_manifest_sha256=(
                    profile.tokenizer_asset_manifest_sha256
                ),
            )
        )
    if strict_report:
        errors.extend(_proof_metadata_errors(rows))
    if strict_report and not verify_attestation(
        report,
        attestation_key_from_env("quality_report"),
        purpose="quality_report",
    ):
        errors.append("invalid_quality_report_attestation")
    has_train_ready_rows = any(
        row.get("data_stage") == "train_ready" or isinstance(row.get("promotion"), dict)
        for row in rows
    )
    domains_by_world: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        world_id = str(row.get("world_id") or "")
        domain = str(row.get("domain") or "")
        if world_id and domain:
            domains_by_world[world_id].add(domain)
    conflicting_domain_worlds = sorted(
        world_id for world_id, domains in domains_by_world.items() if len(domains) != 1
    )
    if conflicting_domain_worlds:
        errors.extend(
            f"world_spans_domains:{world_id}" for world_id in conflicting_domain_worlds
        )
    domain_order = (
        [domain for domain, _quota in profile.promoted_domain_world_quotas]
        if profile is not None and profile.promoted_domain_world_quotas
        else sorted(
            {domain for domains in domains_by_world.values() for domain in domains}
        )
    )
    extra_domains = sorted(
        {
            domain
            for domains in domains_by_world.values()
            for domain in domains
            if domain not in domain_order
        }
    )
    worlds_by_domain = {
        domain: sum(domain in domains for domains in domains_by_world.values())
        for domain in [*domain_order, *extra_domains]
    }
    if has_train_ready_rows:
        observed_world_count = len(
            {str(row.get("world_id") or "") for row in rows if row.get("world_id")}
        )
        try:
            binding_matches = (
                report.get("data_stage") == "train_ready"
                and report.get("report_binding_revision")
                == QUALITY_REPORT_BINDING_REVISION
                and len(str(report.get("candidate_quality_report_sha256") or "")) == 64
                and len(str(report.get("candidate_row_set_sha256") or "")) == 64
                and int(report.get("n_rows") or -1) == len(rows)
                and int(report.get("n_world_ids") or -1) == observed_world_count
                and int(report.get("n_worlds") or -1) == observed_world_count
                and report.get("promoted_row_set_sha256")
                == promoted_row_set_sha256(rows)
                and report.get("promoted_split_row_set_sha256")
                == {
                    split: promoted_split_row_set_sha256(rows, split)
                    for split in ("train", "eval")
                }
                and (
                    profile is None
                    or not profile.promoted_domain_world_quotas
                    or (
                        report.get("promoted_domain_world_quotas")
                        == dict(profile.promoted_domain_world_quotas)
                        and report.get("worlds_by_domain") == worlds_by_domain
                    )
                )
            )
        except (TypeError, ValueError):
            binding_matches = False
        if not binding_matches:
            errors.append("quality_report_row_binding_mismatch")

    def is_real_row(row: dict) -> bool:
        promotion = row.get("promotion")
        return bool(
            row.get("data_stage") == "train_ready"
            and row.get("real_source_verified") is True
            and isinstance(promotion, dict)
            and promotion.get("real_source_verified") is True
            and replay_bundle_binding_valid(row)
            and row.get("real_source_family_ids")
            and row.get("real_source_workflow_ids")
        )

    real_source_relation_ids: set[str] = set()
    hybrid_causal_relation_ids: set[str] = set()
    missing_relation_provenance_split = 0
    require_relation_provenance_split = _requires_relation_provenance_split(profile)
    for row in rows:
        if not is_real_row(row):
            continue
        if "authentic_source_relation_edges" in row:
            authentic_edges = row.get("authentic_source_relation_edges")
            authentic_relation_id = row.get("authentic_source_relation_id")
        else:
            if require_relation_provenance_split:
                missing_relation_provenance_split += 1
                continue
            # Frozen pre-P7 rows predate the explicit relation-provenance split.
            authentic_edges = row.get("source_relation_edges")
            authentic_relation_id = row.get("source_relation_id")
        if (
            isinstance(authentic_edges, list)
            and authentic_edges
            and authentic_relation_id
        ):
            real_source_relation_ids.add(str(authentic_relation_id))
        hybrid_edges = row.get("hybrid_causal_edges")
        if (
            isinstance(hybrid_edges, list)
            and hybrid_edges
            and row.get("source_relation_id")
        ):
            hybrid_causal_relation_ids.add(str(row["source_relation_id"]))
    if missing_relation_provenance_split:
        errors.append(
            f"missing_relation_provenance_split={missing_relation_provenance_split}"
        )
    real_rows = [row for row in rows if is_real_row(row)]
    real_worlds_by_split = {
        split: {
            str(row.get("world_id"))
            for row in real_rows
            if row.get("split") == split and row.get("world_id")
        }
        for split in ("train", "eval")
    }
    real_source_family_ids = {
        str(source_family_id)
        for row in real_rows
        for source_family_id in row.get("real_source_family_ids") or []
        if source_family_id
    }
    real_source_workflow_ids = {
        str(workflow_id)
        for row in real_rows
        for workflow_id in row.get("real_source_workflow_ids") or []
        if workflow_id
    }
    real_base_task_ids = {
        str(row.get("base_task_id")) for row in real_rows if row.get("base_task_id")
    }
    unique_executable_proof_ids = {
        str(row.get("executable_proof_id"))
        for row in rows
        if row.get("executable_proof_id")
    }
    unique_answer_program_ids = {
        str(row.get("answer_program_id"))
        for row in rows
        if row.get("answer_program_id")
    }
    unique_semantic_base_task_ids = {
        str(row.get("semantic_base_task_id"))
        for row in rows
        if row.get("semantic_base_task_id")
    }
    exact_64k_rows = [
        row
        for row in rows
        if row.get("length_bucket") == "64k"
        and _has_exact_64k_metadata(
            row,
            expected_model_id=(profile.tokenizer_model_id if profile else None),
            expected_revision=(profile.tokenizer_revision if profile else None),
            expected_asset_manifest_sha256=(
                profile.tokenizer_asset_manifest_sha256 if profile else None
            ),
        )
    ]
    exact_64k_row_ids = {id(row) for row in exact_64k_rows}
    real_64k_rows = [row for row in real_rows if id(row) in exact_64k_row_ids]
    real_exact_64k_rows_by_domain = {
        domain: sum(row.get("domain") == domain for row in real_64k_rows)
        for domain in domain_order
    }
    real_exact_64k_worlds_by_domain = _real_exact_64k_worlds_by_domain(
        real_64k_rows, domain_order
    )
    if not rows:
        errors.append("empty_product")
    planned_worlds = int(
        report.get("target_promoted_worlds") or report.get("n_worlds") or 0
    )
    observed_worlds = {str(row.get("world_id")) for row in rows if row.get("world_id")}
    worlds_by_split = {
        split: {
            str(row.get("world_id"))
            for row in rows
            if row.get("split") == split and row.get("world_id")
        }
        for split in ("train", "eval")
    }
    observed_domain_counts = Counter(
        str(row.get("domain")) for row in rows if row.get("domain")
    )
    observed_motif_counts = Counter(
        str(row.get("motif")) for row in rows if row.get("motif")
    )
    observed_lengths = Counter(
        str(row.get("length_bucket")) for row in rows if row.get("length_bucket")
    )
    exact_64k_rows_by_domain = {
        domain: sum(
            id(row) in exact_64k_row_ids and row.get("domain") == domain for row in rows
        )
        for domain in domain_order
    }
    scale_worlds = max(planned_worlds, len(observed_worlds))
    if profile is not None:
        if expected_promoted_worlds is not None and (
            expected_promoted_worlds != profile.expected_promoted_worlds
        ):
            errors.append("deprecated_expected_worlds_mismatch")
        if (
            planned_worlds != profile.expected_promoted_worlds
            or len(observed_worlds) != profile.expected_promoted_worlds
        ):
            errors.append(
                "release_profile_worlds="
                f"{planned_worlds}/{len(observed_worlds)} "
                f"expected={profile.expected_promoted_worlds}"
            )
        if len(worlds_by_split["train"]) < profile.min_train_worlds:
            errors.append(
                "release_train_worlds="
                f"{len(worlds_by_split['train'])}<{profile.min_train_worlds}"
            )
        if len(worlds_by_split["eval"]) < profile.min_eval_worlds:
            errors.append(
                "release_eval_worlds="
                f"{len(worlds_by_split['eval'])}<{profile.min_eval_worlds}"
            )
        if len(real_worlds_by_split["train"]) < profile.min_real_train_worlds:
            errors.append(
                "release_real_train_worlds="
                f"{len(real_worlds_by_split['train'])}"
                f"<{profile.min_real_train_worlds}"
            )
        if len(real_worlds_by_split["eval"]) < profile.min_real_eval_worlds:
            errors.append(
                "release_real_eval_worlds="
                f"{len(real_worlds_by_split['eval'])}"
                f"<{profile.min_real_eval_worlds}"
            )
        promoted_domain_world_quotas = dict(profile.promoted_domain_world_quotas)
        if (
            has_train_ready_rows
            and promoted_domain_world_quotas
            and worlds_by_domain != promoted_domain_world_quotas
        ):
            errors.append(
                f"release_domain_worlds={worlds_by_domain} "
                f"expected={promoted_domain_world_quotas}"
            )
    if planned_worlds >= 8 and len(observed_worlds) < planned_worlds:
        errors.append(f"world_retention={len(observed_worlds)}/{planned_worlds}")
    if profile is None and len(observed_domain_counts) < 2 and scale_worlds >= 8:
        errors.append("need >=2 domains")
    if len(observed_motif_counts) < 5 and scale_worlds >= 8:
        errors.append("need >=5 motifs")
    if profile is not None:
        if len(observed_domain_counts) < profile.min_domains:
            errors.append(
                f"release_domains={len(observed_domain_counts)}<{profile.min_domains}"
            )
        if len(observed_motif_counts) < profile.min_motifs:
            errors.append(
                f"release_motifs={len(observed_motif_counts)}<{profile.min_motifs}"
            )
        if len(real_source_family_ids) < profile.min_source_families:
            errors.append(
                "release_real_source_families="
                f"{len(real_source_family_ids)}<{profile.min_source_families}"
            )
        if len(real_base_task_ids) < profile.min_real_base_tasks:
            errors.append(
                f"release_real_base_tasks={len(real_base_task_ids)}"
                f"<{profile.min_real_base_tasks}"
            )
        if len(real_source_relation_ids) < profile.min_real_source_relations:
            errors.append(
                f"release_real_source_relations={len(real_source_relation_ids)}"
                f"<{profile.min_real_source_relations}"
            )
        if len(real_64k_rows) < profile.min_real_64k_rows:
            errors.append(
                f"release_real_exact_64k_rows={len(real_64k_rows)}"
                f"<{profile.min_real_64k_rows}"
            )
        if len(real_source_workflow_ids) < profile.min_unique_real_source_workflows:
            errors.append(
                "release_unique_real_source_workflows="
                f"{len(real_source_workflow_ids)}"
                f"<{profile.min_unique_real_source_workflows}"
            )
        if len(unique_executable_proof_ids) < profile.min_unique_executable_proofs:
            errors.append(
                "release_unique_executable_proofs="
                f"{len(unique_executable_proof_ids)}"
                f"<{profile.min_unique_executable_proofs}"
            )
        if len(unique_answer_program_ids) < profile.min_unique_answer_programs:
            errors.append(
                "release_unique_answer_programs="
                f"{len(unique_answer_program_ids)}"
                f"<{profile.min_unique_answer_programs}"
            )
        if len(unique_semantic_base_task_ids) < profile.min_unique_semantic_base_tasks:
            errors.append(
                "release_unique_semantic_base_tasks="
                f"{len(unique_semantic_base_task_ids)}"
                f"<{profile.min_unique_semantic_base_tasks}"
            )
        for domain, minimum in profile.min_real_exact_64k_rows_by_domain:
            observed = real_exact_64k_rows_by_domain.get(domain, 0)
            if observed < minimum:
                errors.append(
                    f"release_domain_real_exact_64k_rows:{domain}={observed}<{minimum}"
                )
        for domain, minimum in profile.min_real_exact_64k_worlds_by_domain:
            observed = real_exact_64k_worlds_by_domain.get(domain, 0)
            if observed < minimum:
                errors.append(
                    "release_domain_real_exact_64k_worlds:"
                    f"{domain}={observed}<{minimum}"
                )
        for domain, minimum in profile.min_exact_64k_rows_by_domain:
            observed = exact_64k_rows_by_domain.get(domain, 0)
            if observed < minimum:
                errors.append(
                    f"release_domain_exact_64k_rows:{domain}={observed}<{minimum}"
                )
    if scale_worlds >= 8:
        if not observed_lengths.get("16k"):
            errors.append("need nonzero 16k rows")
        if not observed_lengths.get("64k"):
            errors.append("need nonzero 64k rows")
        else:
            long_rows = [row for row in rows if row.get("length_bucket") == "64k"]
            invalid_long_rows = [
                row for row in long_rows if id(row) not in exact_64k_row_ids
            ]
            if invalid_long_rows:
                errors.append(
                    f"invalid_exact_64k_rows={len(invalid_long_rows)}/{len(long_rows)}"
                )
            if len(invalid_long_rows) == len(long_rows):
                errors.append("need >=64000 exact-token 64k row")
        if not real_source_relation_ids:
            errors.append("need nonzero authentic source relations")
    promotion_rows = (
        rows if strict_report else [row for row in rows if requires_promotion(row)]
    )
    promotion_errors = [row for row in promotion_rows if sft_row_errors(row)]
    if promotion_errors:
        errors.append(
            f"dense_promotion_incomplete={len(promotion_errors)}/{len(promotion_rows)}"
        )
    retention = float(report.get("retention") or 0)
    if profile is not None:
        promotion_retention = float(report.get("promotion_retention") or 0)
        if (
            int(report.get("n_candidates") or 0) < len(rows)
            or int(report.get("n_promoted") or -1) != len(rows)
            or promotion_retention != retention
        ):
            errors.append("promotion_retention_binding_mismatch")
        if promotion_retention < profile.min_promotion_retention:
            errors.append(
                "promotion_retention="
                f"{promotion_retention:.4f}<{profile.min_promotion_retention:.4f}"
            )
    if not min_retention <= retention <= max_retention:
        errors.append(f"retention={retention} not in [{min_retention},{max_retention}]")
    if int(report.get("n_clones") or 0):
        errors.append(f"clones={report['n_clones']}")

    digests = Counter(_conversation_digest(row) for row in rows)
    duplicate_rows = sum(count - 1 for count in digests.values())
    if duplicate_rows:
        errors.append(f"exact_duplicate_rows={duplicate_rows}")
    answers_by_prompt: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        answers_by_prompt[_prompt_digest(row)].add(str(row.get("answer") or ""))
    conflicting_answer_prompts = sum(
        len(answers) > 1 for answers in answers_by_prompt.values()
    )
    if conflicting_answer_prompts:
        errors.append(f"conflicting_answers_for_prompt={conflicting_answer_prompts}")
    if any(row.get("composition_method") == "random_concat" for row in rows):
        errors.append("random_concat_rows")
    facts = sum(
        "recorded facts" in str(row.get("context") or "").lower()
        or "line items (authoritative)" in str(row.get("context") or "").lower()
        for row in rows
    )
    pads = sum("#pad" in str(row.get("context") or "") for row in rows)
    if facts:
        errors.append(f"facts_blocks={facts}")
    if pads:
        errors.append(f"pad_clones_in_text={pads}")

    mean_boilerplate = sum(
        float(row.get("boilerplate_token_ratio") or 0) for row in rows
    ) / max(1, len(rows))
    mean_pulse = sum(float(row.get("pulse_doc_ratio") or 0) for row in rows) / max(
        1, len(rows)
    )
    if mean_boilerplate > max_boilerplate:
        errors.append(f"mean_boilerplate={mean_boilerplate:.4f}")
    if mean_pulse > max_pulse:
        errors.append(f"mean_pulse={mean_pulse:.4f}")
    mean_near_dup = sum(
        float(row.get("near_dup_sentence_ratio") or 0) for row in rows
    ) / max(1, len(rows))
    for index, row in enumerate(rows):
        ratio = float(row.get("near_dup_sentence_ratio") or 0)
        if ratio > max_near_dup_sentence_ratio:
            row_id = str(row.get("query_id") or index)
            errors.append(
                f"row_near_dup_sentence_ratio:{row_id}:"
                f"{ratio:.4f}>{max_near_dup_sentence_ratio:.4f}"
            )
    if mean_near_dup > max_near_dup_sentence_ratio:
        errors.append(
            "mean_near_dup_sentence_ratio="
            f"{mean_near_dup:.4f}>{max_near_dup_sentence_ratio:.4f}"
        )
    errors.extend(
        _semantic_growth_errors(
            rows,
            min_internal_growth,
            max_generic_growth_share,
            require_substantial_real_proof_growth=(
                _requires_substantial_real_proof_growth(profile)
            ),
        )
    )
    errors.extend(_row_contract_errors(rows))
    errors.extend(_split_leakage_errors(rows))

    return {
        "ok": not errors,
        "errors": errors,
        "n_rows": len(rows),
        "n_worlds_observed": len(observed_worlds),
        "n_unique_conversations": len(digests),
        "n_exact_duplicate_rows": duplicate_rows,
        "n_conflicting_answer_prompts": conflicting_answer_prompts,
        "by_length": dict(
            Counter(str(row.get("length_bucket") or "?") for row in rows)
        ),
        "by_domain": dict(observed_domain_counts),
        "worlds_by_domain": worlds_by_domain,
        "exact_64k_rows_by_domain": exact_64k_rows_by_domain,
        "by_motif": dict(observed_motif_counts),
        "n_unique_base_tasks": len(
            {row["base_task_id"] for row in rows if row.get("base_task_id")}
        ),
        "n_unique_semantic_base_tasks": len(unique_semantic_base_task_ids),
        "n_unique_executable_proofs": len(unique_executable_proof_ids),
        "n_unique_source_relations": len(
            {row["source_relation_id"] for row in rows if row.get("source_relation_id")}
        ),
        "n_real_source_relations": len(real_source_relation_ids),
        "n_hybrid_causal_relations": len(hybrid_causal_relation_ids),
        "n_real_source_families": len(real_source_family_ids),
        "n_unique_real_source_workflows": len(real_source_workflow_ids),
        "n_real_base_tasks": len(real_base_task_ids),
        "n_real_exact_64k_rows": len(real_64k_rows),
        "real_exact_64k_rows_by_domain": real_exact_64k_rows_by_domain,
        "real_exact_64k_worlds_by_domain": real_exact_64k_worlds_by_domain,
        "real_worlds_by_split": {
            split: len(worlds) for split, worlds in real_worlds_by_split.items()
        },
        "release_profile_id": profile.profile_id if profile is not None else None,
        "release_profile_sha256": (
            release_profile_sha256(profile.profile_id) if profile is not None else None
        ),
        "n_promotion_ready": sum(not sft_row_errors(row) for row in rows),
        "n_unique_answer_programs": len(unique_answer_program_ids),
        "retention": retention,
        "mean_boilerplate": mean_boilerplate,
        "mean_pulse": mean_pulse,
        "mean_near_dup_sentence_ratio": mean_near_dup,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "p0")
    ap.add_argument("--min-retention", type=float, default=0.0001)
    ap.add_argument("--max-retention", type=float, default=1.0)
    ap.add_argument("--max-boilerplate", type=float, default=0.35)
    ap.add_argument("--max-pulse", type=float, default=0.20)
    ap.add_argument("--min-internal-growth", type=int, default=4096)
    ap.add_argument("--max-generic-growth-share", type=float, default=0.20)
    ap.add_argument("--max-near-dup-sentence-ratio", type=float, default=0.25)
    ap.add_argument("--expected-promoted-worlds", type=int)
    ap.add_argument("--release-profile")
    ap.add_argument("--gate-receipt", type=Path)
    args = ap.parse_args()
    if args.release_profile:
        try:
            if args.gate_receipt is not None:
                validate_gate_receipt_output(args.data, args.gate_receipt)
            if args.gate_receipt is not None:
                product, receipt = load_and_create_release_gate_receipt(
                    args.data,
                    args.release_profile,
                    attestation_key=attestation_key_from_env(RELEASE_GATE_PURPOSE),
                )
                _write_json_atomic(args.gate_receipt, receipt)
            else:
                product = load_release_product(args.data, args.release_profile)
            out = product.metrics
        except (TypeError, ValueError) as error:
            raise SystemExit(f"quality gate failed: {error}") from error
        print(json.dumps(out, indent=2))
        return
    if args.gate_receipt is not None:
        raise SystemExit("--gate-receipt requires --release-profile")
    report = json.loads((args.data / "quality_report.json").read_text())
    rows = []
    for name in ("train.jsonl", "eval.jsonl"):
        p = args.data / name
        if p.exists():
            rows.extend(iter_jsonl(p))
    out = evaluate_quality(
        report,
        rows,
        min_retention=args.min_retention,
        max_retention=args.max_retention,
        max_boilerplate=args.max_boilerplate,
        max_pulse=args.max_pulse,
        min_internal_growth=args.min_internal_growth,
        max_generic_growth_share=args.max_generic_growth_share,
        max_near_dup_sentence_ratio=args.max_near_dup_sentence_ratio,
        expected_promoted_worlds=args.expected_promoted_worlds,
        release_profile_id=args.release_profile,
    )
    print(json.dumps(out, indent=2))
    if out["errors"]:
        raise SystemExit("quality gate failed: " + "; ".join(out["errors"]))


if __name__ == "__main__":
    main()
