"""Audit signed P53 candidates and dense rankings without promoting them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    local_probe_diagnostic_metadata,
    verify_attestation,
)
from longworld.core.pack import SEP
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_AUDIT_PURPOSE,
    DENSE_RANKING_PURPOSE,
    candidate_sha256,
    serialized_row_sha256,
)
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)
from reports import p53_osv_upstream_remediation_lifecycle_build as build

AUDIT_SCHEMA = "longworld.p53-osv-dense-audit.v1"
REPORT_SCHEMA = "longworld.p53-osv-dense-audit-report.v1"


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"P53 input is not a regular file: {path}")
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: expected an object")
            rows.append(value)
    if not rows:
        raise ValueError(f"P53 input is empty: {path}")
    return rows


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _write(path, b"".join(_canonical_bytes(row) for row in rows))


def _candidate_artifacts(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    classifications = candidate.get("artifact_classification")
    context = candidate.get("document_context")
    if not isinstance(classifications, list) or not classifications:
        raise ValueError("P53 artifact classification is missing")
    if not isinstance(context, str) or not context:
        raise ValueError("P53 document context is missing")
    documents = context.split(SEP)
    if len(documents) != len(classifications):
        raise ValueError("P53 document and classification counts differ")
    artifacts = []
    for classification, text in zip(classifications, documents, strict=True):
        if not isinstance(classification, dict):
            raise TypeError("P53 artifact classification is malformed")
        artifact_id = str(classification.get("artifact_id") or "")
        if not artifact_id or not text:
            raise ValueError("P53 artifact identity or text is empty")
        if _sha256_text(text) != classification.get("artifact_text_sha256"):
            raise ValueError(f"P53 artifact text binding failed: {artifact_id}")
        artifacts.append({"artifact_id": artifact_id, "text": text})
    if len({item["artifact_id"] for item in artifacts}) != len(artifacts):
        raise ValueError("P53 candidate has duplicate artifact ids")
    return artifacts


def _validate_ranking(
    candidate: dict[str, Any],
    ranking: dict[str, Any],
    ranking_key: bytes,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not verify_attestation(ranking, ranking_key, purpose=DENSE_RANKING_PURPOSE):
        raise ValueError("P53 dense ranking attestation is invalid")
    if ranking.get("schema_version") != "dense-ranking-v2":
        raise ValueError("P53 dense ranking schema is invalid")
    if ranking.get("query_id") != candidate.get("query_id"):
        raise ValueError("P53 dense ranking query binding is invalid")
    if ranking.get("candidate_sha256") != candidate_sha256(candidate):
        raise ValueError("P53 dense ranking candidate binding is invalid")
    if ranking.get("query_sha256") != _sha256_text(str(candidate["question"])):
        raise ValueError("P53 dense ranking question binding is invalid")
    model = ranking.get("model")
    expected_model = config["dense_model"]
    if not isinstance(model, dict) or (
        model.get("model_id"),
        model.get("revision"),
    ) != (expected_model["model_id"], expected_model["revision"]):
        raise ValueError("P53 dense ranking model pin is invalid")
    ranked = ranking.get("artifacts")
    if not isinstance(ranked, list):
        raise TypeError("P53 dense ranking artifacts are missing")
    artifacts = _candidate_artifacts(candidate)
    expected = {item["artifact_id"]: _sha256_text(item["text"]) for item in artifacts}
    observed = {}
    for rank, item in enumerate(ranked, start=1):
        if not isinstance(item, dict) or item.get("rank") != rank:
            raise ValueError("P53 dense ranks are not contiguous")
        artifact_id = str(item.get("artifact_id") or "")
        if artifact_id in observed:
            raise ValueError("P53 dense ranking repeats an artifact")
        observed[artifact_id] = item.get("text_sha256")
    if observed != expected:
        raise ValueError("P53 dense ranking coverage or text binding differs")
    return ranked


def _validate_candidate(
    candidate: dict[str, Any],
    *,
    config: dict[str, Any],
    tokenizer: Any,
    source_sha256: str,
    source_records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    artifacts = _candidate_artifacts(candidate)
    classifications = candidate["artifact_classification"]
    if candidate.get("schema_version") != build.CANDIDATE_SCHEMA:
        raise ValueError("P53 candidate schema is invalid")
    if candidate.get("world_id") != config["world_id"]:
        raise ValueError("P53 candidate world identity is invalid")
    if candidate.get("source_sidecar", {}).get("sha256") != source_sha256:
        raise ValueError("P53 candidate source-sidecar binding is invalid")
    if any(
        candidate.get(field) is not expected
        for field, expected in {
            "train_ready": False,
            "selected": False,
            "promoted": False,
            "production_eligible": False,
        }.items()
    ):
        raise ValueError("P53 candidate crosses the candidate boundary")

    for artifact, classification in zip(artifacts, classifications, strict=True):
        source_record = source_records.get(artifact["artifact_id"])
        if source_record is None or source_record.get("text") != artifact["text"]:
            raise ValueError("P53 candidate artifact is absent from source sidecar")
        artifact.update(
            text_sha256=source_record["text_sha256"],
            source_origin=source_record["source_origin"],
            parent=json.loads(artifact["text"]).get("counterfactual_derivation"),
        )
        for field in (
            "source_record_id",
            "source_url",
            "source_sha256",
            "license_id",
            "license_source_url",
        ):
            if classification.get(field) != source_record.get(field):
                raise ValueError(f"P53 candidate provenance differs: {field}")
    expected_licenses = [
        {
            "artifact_id": classification["artifact_id"],
            "license_id": classification["license_id"],
            "license_source_url": classification["license_source_url"],
            "source_url": classification["source_url"],
            "source_sha256": classification["source_sha256"],
        }
        for classification in classifications
    ]
    if candidate.get("source_license_manifest") != expected_licenses:
        raise ValueError("P53 per-artifact license propagation is incomplete")
    if any(
        not item["license_id"] or not item["license_source_url"]
        for item in expected_licenses
    ):
        raise ValueError("P53 per-artifact license identity is empty")

    question = str(candidate["question"])
    expected_context = build._render_prompt(question, artifacts)
    if candidate.get("context") != expected_context:
        raise ValueError("P53 rendered context differs")
    tokens = build._token_count(tokenizer, expected_context)
    bucket = str(candidate.get("length_bucket"))
    if (lower_upper := EXACT_TOKEN_BAND_RANGES.get(bucket)) is None:
        raise ValueError("P53 exact token bucket is invalid")
    lower, upper = lower_upper
    if not lower <= tokens <= upper:
        raise ValueError("P53 candidate is outside its exact token band")
    if tokens != candidate.get("actual_context_tokens") or tokens != candidate.get(
        "tokenizer_context_tokens"
    ):
        raise ValueError("P53 candidate token receipt differs")

    task = candidate.get("task")
    if not isinstance(task, dict):
        raise TypeError("P53 task is missing")
    replay = build.replay(artifacts, task)
    if replay.get("answer") != candidate.get("answer"):
        raise ValueError("P53 full-pool replay differs")
    essential = set(candidate.get("essential_artifact_ids") or [])
    if not essential or not essential <= {item["artifact_id"] for item in artifacts}:
        raise ValueError("P53 essential set is invalid")
    for removed in essential:
        answer = build.replay(
            [item for item in artifacts if item["artifact_id"] != removed], task
        ).get("answer")
        if answer == candidate["answer"]:
            raise ValueError(f"P53 remove-one replay stayed sufficient: {removed}")
    build._assert_no_near_duplicates(
        artifacts,
        config["packing"]["near_duplicate_word_shingle_size"],
        config["packing"]["near_duplicate_jaccard_threshold"],
    )
    windows = build._assert_short_windows_insufficient(
        tokenizer,
        question,
        artifacts,
        essential,
        config["gates"]["short_window_tokens"],
    )
    source_receipt = build._source_token_receipt(tokenizer, question, artifacts, tokens)
    if source_receipt != candidate.get("source_token_receipt"):
        raise ValueError("P53 source-token receipt replay differs")
    if (
        source_receipt["source_token_ratio"]
        < config["gates"]["require_source_token_ratio_at_least"]
    ):
        raise ValueError("P53 source-token ratio is below the frozen gate")
    return {
        "artifacts": artifacts,
        "tokens": tokens,
        "essential_count": len(essential),
        "essential_span_tokens": windows["essential_span_tokens"],
        "source_token_ratio": source_receipt["source_token_ratio"],
        "replay": replay,
    }


def _cross_candidate_checks(
    candidates: list[dict[str, Any]], config: dict[str, Any]
) -> None:
    expected = {
        (bucket, view)
        for bucket in config["length_buckets"]
        for view in config["views"]
    }
    observed = [(row.get("length_bucket"), row.get("view")) for row in candidates]
    if set(observed) != expected or len(observed) != len(expected):
        raise ValueError("P53 three-view bucket matrix is incomplete or duplicated")
    by_key = {(row["length_bucket"], row["view"]): row for row in candidates}
    for bucket in config["length_buckets"]:
        full = by_key[(bucket, "full")]
        ordered = by_key[(bucket, "ordered_artifact_view")]
        cf = by_key[(bucket, "cf")]
        if full["answer"] != ordered["answer"] or cf["answer"] == full["answer"]:
            raise ValueError("P53 view answers violate the derivation contract")
        full_ids = Counter(
            item["artifact_id"] for item in full["artifact_classification"]
        )
        ordered_ids = Counter(
            item["artifact_id"] for item in ordered["artifact_classification"]
        )
        if full_ids != ordered_ids:
            raise ValueError("P53 ordered view changes the authentic artifact pool")
        full_set = set(full_ids)
        cf_set = {item["artifact_id"] for item in cf["artifact_classification"]}
        removed = full_set - cf_set
        added = cf_set - full_set
        if len(removed) != 1 or added != {f"{next(iter(removed))}:cf"}:
            raise ValueError("P53 counterfactual is not one exact child replacement")
    by_bucket = {
        bucket: by_key[(bucket, "full")]["task"]["chains"]
        for bucket in config["length_buckets"]
    }
    for before, after in (("32k", "64k"), ("64k", "128k")):
        before_ids = [item["withdrawn_id"] for item in by_bucket[before]]
        after_ids = [item["withdrawn_id"] for item in by_bucket[after]]
        if before_ids != after_ids[: len(before_ids)] or len(before_ids) >= len(
            after_ids
        ):
            raise ValueError("P53 cumulative lifecycle chain growth is invalid")


def audit(
    config_path: Path,
    source_path: Path,
    candidates_path: Path,
    rankings_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    source_raw = source_path.read_bytes()
    source = json.loads(source_raw)
    source_key = attestation_key_from_env(build.SOURCE_PURPOSE)
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    ranking_key = attestation_key_from_env(DENSE_RANKING_PURPOSE)
    audit_key = attestation_key_from_env(DENSE_AUDIT_PURPOSE)
    if None in (source_key, candidate_key, ranking_key, audit_key):
        raise ValueError("P53 audit requires source, candidate, ranker, auditor keys")
    assert source_key is not None and candidate_key is not None
    assert ranking_key is not None and audit_key is not None
    if not verify_attestation(source, source_key, purpose=build.SOURCE_PURPOSE):
        raise ValueError("P53 source-sidecar attestation is invalid")
    if source.get("schema_version") != build.SOURCE_SCHEMA:
        raise ValueError("P53 source-sidecar schema is invalid")
    records = source.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("P53 source-sidecar records are missing")
    source_records = {}
    for record in records:
        artifact_id = str(record.get("artifact_id") or "")
        if not artifact_id or artifact_id in source_records:
            raise ValueError("P53 source-sidecar artifact identity is invalid")
        if _sha256_text(str(record.get("text") or "")) != record.get("text_sha256"):
            raise ValueError("P53 source-sidecar text digest is invalid")
        source_records[artifact_id] = record

    tokenizer_cfg = config["tokenizer"]
    if (
        resolved_tokenizer_asset_manifest_sha256(
            tokenizer_cfg["model_id"], tokenizer_cfg["revision"]
        )
        != tokenizer_cfg["asset_manifest_sha256"]
    ):
        raise ValueError("P53 tokenizer asset manifest changed")
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_cfg["model_id"],
        revision=tokenizer_cfg["revision"],
        trust_remote_code=False,
        local_files_only=True,
    )
    candidates = _read_jsonl(candidates_path)
    rankings = _read_jsonl(rankings_path)
    ranking_by_digest = {
        str(item.get("candidate_sha256") or ""): item for item in rankings
    }
    if len(ranking_by_digest) != len(rankings) or len(rankings) != len(candidates):
        raise ValueError("P53 dense ranking coverage is incomplete or duplicated")
    _cross_candidate_checks(candidates, config)

    audits = []
    accepted = []
    for candidate in candidates:
        if not verify_attestation(
            candidate, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
        ):
            raise ValueError("P53 candidate attestation is invalid")
        validation = _validate_candidate(
            candidate,
            config=config,
            tokenizer=tokenizer,
            source_sha256=_sha256_bytes(source_raw),
            source_records=source_records,
        )
        digest = candidate_sha256(candidate)
        ranking = ranking_by_digest.get(digest)
        if ranking is None:
            raise ValueError("P53 dense ranking candidate binding is missing")
        ranked = _validate_ranking(candidate, ranking, ranking_key, config)
        top_k = ranked[: int(config["dense_model"]["top_k"])]
        by_id = {item["artifact_id"]: item for item in validation["artifacts"]}
        top_artifacts = [by_id[item["artifact_id"]] for item in top_k]
        shortcut = build.replay(top_artifacts, candidate["task"])
        if shortcut.get("answer") == candidate["answer"]:
            raise ValueError("P53 dense top-k shortcut remains sufficient")
        audit_row = attach_attestation(
            {
                "schema_version": AUDIT_SCHEMA,
                "data_stage": "candidate_audited",
                "query_id": candidate["query_id"],
                "candidate_sha256": digest,
                "ranking_sha256": serialized_row_sha256(ranking),
                "ranker_type": "dense_embedding",
                "model": ranking["model"],
                "k": len(top_k),
                "top_k": top_k,
                "embedding_topk_insufficient": True,
                "strict_replay_answer": shortcut,
                "expected_answer": candidate["answer"],
                "full_pool_strict_replay_sufficient": True,
                "essential_leave_one_passed": True,
                "shortcut_windows_insufficient": True,
                "unknown_policy_replayed": True,
                "license_propagation_replayed": True,
                "actual_context_tokens": validation["tokens"],
                "essential_span_tokens": validation["essential_span_tokens"],
                "source_token_ratio": validation["source_token_ratio"],
                "source_sidecar_sha256": _sha256_bytes(source_raw),
                "promotion_eligible": False,
                "train_ready": False,
                "production_eligible": False,
                **local_probe_diagnostic_metadata(),
            },
            audit_key,
            purpose=DENSE_AUDIT_PURPOSE,
        )
        audits.append(audit_row)
        accepted.append(candidate)

    report = {
        "schema_version": REPORT_SCHEMA,
        "data_product": config["data_product"],
        "config_sha256": _sha256_bytes(config_raw),
        "source_sidecar_sha256": _sha256_bytes(source_raw),
        "candidate_rows_sha256": _sha256_bytes(candidates_path.read_bytes()),
        "ranking_rows_sha256": _sha256_bytes(rankings_path.read_bytes()),
        "audit_rows_sha256": _sha256_bytes(
            b"".join(_canonical_bytes(row) for row in audits)
        ),
        "candidate_count": len(candidates),
        "accepted_count": len(accepted),
        "rejected_count": 0,
        "audit_count": len(audits),
        "world_count": 1,
        "views": dict(sorted(Counter(row["view"] for row in candidates).items())),
        "length_distribution": dict(
            sorted(Counter(row["length_bucket"] for row in candidates).items())
        ),
        "all_dense_top3_insufficient": len(audits) == len(candidates),
        "preflight_complete": len(accepted) == len(candidates),
        "formal_dense_audit_complete": len(audits) == len(candidates),
        "data_stage": "candidate_audited",
        "train_ready": False,
        "production_eligible": False,
        "selected": False,
        "promoted": False,
        "inventory_delta": 0,
        "shared_promotion_adapter_registered": False,
        "errors": [],
        **local_probe_diagnostic_metadata(),
    }
    _write_jsonl(output_dir / "audit.jsonl", audits)
    _write_jsonl(output_dir / "accepted.jsonl", accepted)
    _write(output_dir / "rejects.jsonl", b"")
    _write(output_dir / "AUDIT_MANIFEST.json", _canonical_bytes(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--rankings", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = audit(
        args.config,
        args.source,
        args.candidates,
        args.rankings,
        args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
