#!/usr/bin/env python3
"""Audit shared dense rankings with the candidate-only finance replay adapter."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.financehistory import audit_finance_dense_ranking
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_AUDIT_PURPOSE,
    DENSE_RANKING_PURPOSE,
    candidate_sha256,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input is missing or not a regular file: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from error
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: expected an object")
            rows.append(value)
    if not rows:
        raise ValueError(f"input is empty: {path}")
    return rows


def _atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def audit_rankings(
    candidates_path: Path, rankings_path: Path, output_path: Path, *, k: int
) -> int:
    """Verify ranker signatures, finance-replay top-k, and sign audit rows."""
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    ranker_key = attestation_key_from_env(DENSE_RANKING_PURPOSE)
    auditor_key = attestation_key_from_env(DENSE_AUDIT_PURPOSE)
    if candidate_key is None or ranker_key is None or auditor_key is None:
        raise ValueError("candidate, ranker, and auditor keys are required")
    candidates = _read_jsonl(candidates_path)
    if any(
        not verify_attestation(
            candidate, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        for candidate in candidates
    ):
        raise ValueError("finance candidate attestation is invalid")
    rankings = _read_jsonl(rankings_path)
    by_digest: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        digest = str(ranking.get("candidate_sha256") or "")
        if (
            not digest
            or digest in by_digest
            or not verify_attestation(
                ranking, ranker_key, purpose=DENSE_RANKING_PURPOSE
            )
        ):
            raise ValueError("dense ranking identity or attestation is invalid")
        by_digest[digest] = ranking
    expected = {candidate_sha256(candidate) for candidate in candidates}
    if expected != set(by_digest):
        raise ValueError("dense ranking coverage does not match finance candidates")
    audits = [
        attach_attestation(
            audit_finance_dense_ranking(
                candidate, by_digest[candidate_sha256(candidate)], k=k
            ),
            auditor_key,
            purpose=DENSE_AUDIT_PURPOSE,
        )
        for candidate in candidates
    ]
    _atomic_jsonl(output_path, audits)
    return len(audits)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--rankings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    count = audit_rankings(args.candidates, args.rankings, args.output, k=args.top_k)
    print(json.dumps({"rows": count, "output": str(args.output)}))


if __name__ == "__main__":
    main()
