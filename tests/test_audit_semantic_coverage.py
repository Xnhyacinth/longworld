from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.audit_semantic_coverage as coverage_module
from scripts.audit_semantic_coverage import (
    SemanticCoverageError,
    audit_semantic_coverage,
)


@pytest.fixture(autouse=True)
def _accept_fixture_inventory_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        coverage_module,
        "_replay_bound_inventory",
        lambda inventory, *, workspace_root: inventory,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> dict[str, object]:
    raw = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "path": path.relative_to(path.parents[1]).as_posix(),
        "role": "promoted_data",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _row(*, content_hash: str, bucket: str) -> dict:
    tokens = 16_100 if bucket == "16k" else 32_100
    return {
        "world_id": "world-1",
        "content_hash": content_hash,
        "actual_context_tokens": tokens,
        "tokenizer_context_tokens": tokens,
        "difficulty": {"context_tokens": tokens},
        "length_bucket": bucket,
        "domain": "codeforge",
        "query_type": "version_selection",
        "motif": "version_selection",
        "answer_program_id": "program-1",
        "executable_proof_id": f"proof-{bucket}",
        "program_ops": [{"op": "READ_SOURCE_SPAN"}, {"op": "SELECT_RELEASE"}],
        "real_source_family_ids": ["github.com/example/project"],
        "authentic_source_relation_edges": [
            {"relation": "supersedes", "relation_provenance": "authentic_source"}
        ],
        "source_relation_edges": [
            {"relation": "supersedes", "relation_provenance": "authentic_source"},
            {
                "relation": "computes_from",
                "relation_provenance": "synthetic_executable",
            },
        ],
    }


def _inventory(root: Path) -> Path:
    rows = [
        _row(content_hash="hash-1", bucket="16k"),
        _row(content_hash="hash-2", bucket="32k"),
    ]
    train = root / "release" / "train.jsonl"
    train_file = _write_jsonl(train, rows)
    inventory = {
        "schema_version": "longworld-local-release-inventory-v2",
        "inventory_integrity_ok": True,
        "trust_mode": "local_engineering",
        "production_eligible": False,
        "target_release_profile_id": "p12-current-source-probe-12-v1",
        "n_rows": 2,
        "n_worlds": 1,
        "n_content_hashes": 2,
        "n_reported_context_tokens": 48_200,
        "releases": [{"release": "release", "files": [train_file]}],
    }
    path = root / "inventory.json"
    path.write_text(json.dumps(inventory))
    return path


def test_audit_separates_authentic_and_synthetic_relations(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)

    report = audit_semantic_coverage(inventory, workspace_root=tmp_path)

    assert report["n_rows"] == 2
    assert report["n_program_ops"] == 2
    assert report["authentic_source_relation_kinds"] == ["supersedes"]
    assert report["synthetic_executable_relation_kinds"] == ["computes_from"]
    assert report["query_types"] == ["version_selection"]
    assert report["length_bucket_counts"] == {"16k": 1, "32k": 1}


def test_audit_rejects_row_count_drift(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    payload = json.loads(inventory.read_text())
    payload["n_rows"] = 3
    inventory.write_text(json.dumps(payload))

    with pytest.raises(SemanticCoverageError, match="row count"):
        audit_semantic_coverage(inventory, workspace_root=tmp_path)


def test_audit_rejects_promoted_file_hash_drift(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    train = tmp_path / "release" / "train.jsonl"
    raw = train.read_bytes()
    train.write_bytes(b"[" + raw[1:])

    with pytest.raises(SemanticCoverageError, match="digest"):
        audit_semantic_coverage(inventory, workspace_root=tmp_path)


def test_audit_rejects_non_promoted_jsonl_role(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    payload = json.loads(inventory.read_text())
    payload["releases"][0]["files"][0]["role"] = "diagnostic_data"
    inventory.write_text(json.dumps(payload))

    with pytest.raises(SemanticCoverageError, match="promoted_data"):
        audit_semantic_coverage(inventory, workspace_root=tmp_path)


def test_audit_rejects_fabricated_authentic_relation_alias(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    train = tmp_path / "release" / "train.jsonl"
    rows = [json.loads(line) for line in train.read_text().splitlines()]
    rows[0]["authentic_source_relation_edges"].append(
        {
            "relation": "fabricated",
            "relation_provenance": "synthetic_executable",
        }
    )
    payload = json.loads(inventory.read_text())
    payload["releases"][0]["files"][0] = _write_jsonl(train, rows)
    inventory.write_text(json.dumps(payload))

    with pytest.raises(SemanticCoverageError, match="authentic relation alias"):
        audit_semantic_coverage(inventory, workspace_root=tmp_path)


def test_audit_rejects_exact_token_metadata_drift(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    train = tmp_path / "release" / "train.jsonl"
    rows = [json.loads(line) for line in train.read_text().splitlines()]
    rows[0]["tokenizer_context_tokens"] += 1
    payload = json.loads(inventory.read_text())
    payload["releases"][0]["files"][0] = _write_jsonl(train, rows)
    inventory.write_text(json.dumps(payload))

    with pytest.raises(SemanticCoverageError, match="exact token metadata"):
        audit_semantic_coverage(inventory, workspace_root=tmp_path)


def test_audit_rejects_inventory_replay_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = _inventory(tmp_path)

    def drifted_replay(payload: dict, *, workspace_root: Path) -> dict:
        replayed = dict(payload)
        replayed["n_rows"] = 99
        return replayed

    monkeypatch.setattr(coverage_module, "_replay_bound_inventory", drifted_replay)

    with pytest.raises(SemanticCoverageError, match="signed release-union replay"):
        audit_semantic_coverage(inventory, workspace_root=tmp_path)
