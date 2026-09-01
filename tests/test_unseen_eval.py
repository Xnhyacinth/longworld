from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import longworld.core.release_profile as release_profile_module
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.production_trust import (
    APPROVAL_DIGEST_ENV,
    APPROVAL_PATH_ENV,
    TRUST_ROOTS_DIGEST_ENV,
    TRUST_ROOTS_PATH_ENV,
    canonical_approval_statement,
    verify_production_approval_from_env,
)
from longworld.core.promotion import (
    RELEASE_GATE_REVISION,
    promoted_row_set_sha256,
    promoted_split_row_set_sha256,
)
from longworld.core.release_profile import release_profile_sha256
from longworld.core.unseen import (
    build_unseen_splits,
    load_unseen_split_manifest,
    split_group_key,
)

PROMOTION_KEY = b"longworld-unseen-promotion-production-key-v1"
AUDITOR_KEY = b"longworld-unseen-auditor-production-key-v1"
REPORT_KEY = b"longworld-unseen-report-production-key-v1"
PRODUCTION_PROFILE = "p3-production-48-v1"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _production_environment(
    monkeypatch: pytest.MonkeyPatch, *, allow_profile: bool = True
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    if allow_profile:
        monkeypatch.setattr(
            release_profile_module,
            "ISSUABLE_PRODUCTION_PROFILE_IDS",
            frozenset({PRODUCTION_PROFILE}),
        )
    for role, key in (
        ("promotion", PROMOTION_KEY),
        ("auditor", AUDITOR_KEY),
        ("report", REPORT_KEY),
    ):
        monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
        monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"prod-unseen-{role}-v1")


def _production_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_file_sha256: dict[str, str],
) -> dict:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    public_der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_id = (
        "aws-kms://arn:aws:kms:us-east-1:123456789012:"
        "key/11111111-2222-3333-4444-555555555555"
    )
    statement = {
        "schema_version": "longworld-production-approval-v1",
        "approval_scope": "longworld-production-release",
        "decision": "approved",
        "release_profile_id": PRODUCTION_PROFILE,
        "release_profile_sha256": release_profile_sha256(PRODUCTION_PROFILE),
        "quality_report_sha256": source_file_sha256["quality_report.json"],
        "train_sha256": source_file_sha256["train.jsonl"],
        "eval_sha256": source_file_sha256["eval.jsonl"],
        "release_selection_sha256": "e" * 64,
        "approval_authority": "release-governance",
        "approval_nonce": "unseen-approval-001",
        "issued_at": "2026-08-25T12:00:00Z",
    }
    approval = {
        "statement": statement,
        "signature": {
            "scheme": "ecdsa-p256-sha256-v1",
            "key_id": key_id,
            "value_base64": base64.b64encode(
                private_key.sign(
                    canonical_approval_statement(statement), ec.ECDSA(hashes.SHA256())
                )
            ).decode(),
        },
    }
    roots = {
        "schema_version": "longworld-production-trust-roots-v1",
        "keys": [
            {
                "key_id": key_id,
                "scheme": "ecdsa-p256-sha256-v1",
                "public_key_pem": public_key.public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ).decode(),
                "public_key_sha256": hashlib.sha256(public_der).hexdigest(),
                "provider": "aws-kms",
                "approval_authorities": ["release-governance"],
            }
        ],
    }
    approval_path = tmp_path / "production-approval.json"
    roots_path = tmp_path / "production-trust-roots.json"
    approval_path.write_text(json.dumps(approval, sort_keys=True))
    roots_path.write_text(json.dumps(roots, sort_keys=True))
    monkeypatch.setenv(APPROVAL_PATH_ENV, str(approval_path))
    monkeypatch.setenv(TRUST_ROOTS_PATH_ENV, str(roots_path))
    monkeypatch.setenv(
        APPROVAL_DIGEST_ENV, hashlib.sha256(approval_path.read_bytes()).hexdigest()
    )
    monkeypatch.setenv(
        TRUST_ROOTS_DIGEST_ENV, hashlib.sha256(roots_path.read_bytes()).hexdigest()
    )
    return verify_production_approval_from_env(
        release_profile_id=PRODUCTION_PROFILE,
        release_profile_sha256=release_profile_sha256(PRODUCTION_PROFILE),
        source_file_sha256=source_file_sha256,
        release_selection_sha256="e" * 64,
    )


def _production_release(
    rows: list[dict],
    source_file_sha256: dict[str, str],
    *,
    production_approval: dict | None = None,
    release_profile_id: str = PRODUCTION_PROFILE,
    release_profile_digest: str | None = None,
) -> dict:
    return attach_attestation(
        {
            "schema_version": "longworld-release-gate-pass-v1",
            "gate_revision": RELEASE_GATE_REVISION,
            "release_profile_id": release_profile_id,
            "release_profile_sha256": (
                release_profile_digest or release_profile_sha256(release_profile_id)
            ),
            "quality_report_sha256": source_file_sha256["quality_report.json"],
            "source_file_sha256": source_file_sha256,
            "n_rows": len(rows),
            "promoted_row_set_sha256": promoted_row_set_sha256(rows),
            "promoted_split_row_set_sha256": {
                split: promoted_split_row_set_sha256(rows, split)
                for split in ("train", "eval")
            },
            "production_approval": production_approval,
            "ok": True,
            "errors": [],
        },
        AUDITOR_KEY,
        purpose="release_gate_pass",
    )


def _row(index: int, *, source_family: str = "family-a") -> dict:
    pair = index // 2
    domain = "company" if pair % 2 == 0 else "codeforge"
    return {
        "world_id": f"world-{pair}",
        "domain": domain,
        "composition_domains": [f"domain-{pair * 2}", f"domain-{pair * 2 + 1}"],
        "motif": f"motif-{pair % 3}",
        "canonical_topology": f"chain-{pair}",
        "program_ops": [{"op": f"OP_{pair}"}],
        "source_family_ids": [source_family],
        "real_source_verified": True,
        "content_hash": f"content-{index}",
        "dossier_id": f"dossier-{pair}",
        "workflow_ids": [f"entity-{pair}"],
        "view": "full" if index % 2 == 0 else "cf",
        "context": f"context {index}",
        "answer": f"answer {index}",
    }


def _approved_production_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    allow_profile: bool = True,
    shared_entities: bool = False,
) -> tuple[list[dict], dict[str, str], dict]:
    _production_environment(monkeypatch, allow_profile=allow_profile)
    rows = [_row(index) for index in range(8)]
    if shared_entities:
        for row in rows:
            world_number = int(row["world_id"].rsplit("-", 1)[1])
            row["workflow_ids"] = [f"shared-entity-{world_number // 2}"]
    rows = [attach_attestation(row, PROMOTION_KEY, purpose="sft_row") for row in rows]
    source_hashes = {
        "quality_report.json": _sha256(b"quality"),
        "train.jsonl": _sha256(b"train"),
        "eval.jsonl": _sha256(b"eval"),
    }
    approval = _production_approval(tmp_path, monkeypatch, source_hashes)
    return (
        rows,
        source_hashes,
        _production_release(rows, source_hashes, production_approval=approval),
    )


@pytest.mark.parametrize(
    "axis",
    [
        "world_entity",
        "topology_operator",
        "source_document_family",
        "domain_composition",
    ],
)
def test_unseen_splits_are_group_atomic_and_deterministic(
    tmp_path: Path, axis: str
) -> None:
    rows = [
        _row(index, source_family=f"family-{(index // 2) % 3}") for index in range(16)
    ]

    first = build_unseen_splits(rows, tmp_path / "first", axes=[axis], eval_ratio=0.25)
    second = build_unseen_splits(
        list(reversed(rows)), tmp_path / "second", axes=[axis], eval_ratio=0.25
    )

    assert first[axis]["status"] == "ready"
    if axis == "world_entity":
        assert first[axis]["coverage"] == "world_atomic_only"
    assert first[axis]["train_sha256"] == second[axis]["train_sha256"]
    assert first[axis]["eval_sha256"] == second[axis]["eval_sha256"]
    train = [
        json.loads(line)
        for line in (tmp_path / "first" / axis / "train.jsonl").read_text().splitlines()
    ]
    evaluation = [
        json.loads(line)
        for line in (tmp_path / "first" / axis / "eval.jsonl").read_text().splitlines()
    ]
    train_groups = {split_group_key(row, axis) for row in train}
    eval_groups = {split_group_key(row, axis) for row in evaluation}
    assert train_groups
    assert eval_groups
    assert train_groups.isdisjoint(eval_groups)
    assert {row["dossier_id"] for row in train}.isdisjoint(
        {row["dossier_id"] for row in evaluation}
    )


def test_source_family_split_blocks_when_only_one_verified_family(
    tmp_path: Path,
) -> None:
    rows = [_row(index) for index in range(8)]

    manifest = build_unseen_splits(
        rows, tmp_path, axes=["source_document_family"], eval_ratio=0.25
    )

    assert manifest["source_document_family"]["status"] == "blocked"
    assert manifest["source_document_family"]["reason"] == "fewer_than_two_groups"
    assert not (tmp_path / "source_document_family" / "eval.jsonl").exists()


def test_unseen_split_rejects_conflicting_dossier_axis_groups(tmp_path: Path) -> None:
    factual = _row(0)
    counterfactual = {**_row(1), "world_id": "different-world"}
    axis_dir = tmp_path / "world_entity"
    axis_dir.mkdir(parents=True)
    (axis_dir / "train.jsonl").write_text("stale\n", encoding="utf-8")
    (axis_dir / "eval.jsonl").write_text("stale\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("stale\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dossier spans unseen groups"):
        build_unseen_splits(
            [factual, counterfactual],
            tmp_path,
            axes=["world_entity"],
            eval_ratio=0.25,
        )
    assert not (axis_dir / "train.jsonl").exists()
    assert not (axis_dir / "eval.jsonl").exists()
    assert not (tmp_path / "manifest.json").exists()


def test_unseen_split_rejects_missing_dossier_id(tmp_path: Path) -> None:
    rows = [_row(index) for index in range(4)]
    rows[1].pop("dossier_id")
    output_dir = tmp_path / "stale"
    axis_dir = output_dir / "world_entity"
    axis_dir.mkdir(parents=True)
    (axis_dir / "train.jsonl").write_text("stale\n", encoding="utf-8")
    (axis_dir / "eval.jsonl").write_text("stale\n", encoding="utf-8")
    (output_dir / "manifest.json").write_text("stale\n", encoding="utf-8")

    with pytest.raises(ValueError, match="requires dossier_id"):
        build_unseen_splits(
            rows,
            output_dir,
            axes=["world_entity"],
            eval_ratio=0.25,
        )
    assert not (axis_dir / "train.jsonl").exists()
    assert not (axis_dir / "eval.jsonl").exists()
    assert not (output_dir / "manifest.json").exists()


@pytest.mark.parametrize("invalid_dossier", [True, 123, " dossier-0 "])
def test_unseen_split_rejects_noncanonical_dossier_id(
    tmp_path: Path, invalid_dossier: object
) -> None:
    rows = [_row(index) for index in range(4)]
    rows[0]["dossier_id"] = invalid_dossier

    with pytest.raises(ValueError, match="requires dossier_id"):
        build_unseen_splits(
            rows,
            tmp_path,
            axes=["world_entity"],
            eval_ratio=0.25,
        )


def test_topology_operator_split_keeps_shared_operator_components_together(
    tmp_path: Path,
) -> None:
    rows = [_row(index) for index in range(12)]
    rows[4]["program_ops"] = rows[0]["program_ops"]
    rows[5]["program_ops"] = rows[0]["program_ops"]

    build_unseen_splits(rows, tmp_path, axes=["topology_operator"], eval_ratio=0.25)
    train = [
        json.loads(line)
        for line in (tmp_path / "topology_operator" / "train.jsonl")
        .read_text()
        .splitlines()
    ]
    evaluation = [
        json.loads(line)
        for line in (tmp_path / "topology_operator" / "eval.jsonl")
        .read_text()
        .splitlines()
    ]
    train_ops = {json.dumps(row["program_ops"], sort_keys=True) for row in train}
    eval_ops = {json.dumps(row["program_ops"], sort_keys=True) for row in evaluation}
    assert train_ops.isdisjoint(eval_ops)


def test_world_entity_split_reports_cross_world_entity_coverage(
    tmp_path: Path,
) -> None:
    rows = [_row(index) for index in range(16)]
    for row in rows:
        world_number = int(row["world_id"].rsplit("-", 1)[1])
        row["workflow_ids"] = [f"shared-entity-{world_number // 2}"]

    manifest = build_unseen_splits(
        rows,
        tmp_path,
        axes=["world_entity"],
        eval_ratio=0.25,
    )

    assert manifest["world_entity"]["coverage"] == "world_entity_atomic"


def test_local_unseen_manifest_is_explicitly_nonproduction(tmp_path: Path) -> None:
    rows = [_row(index) for index in range(8)]

    build_unseen_splits(rows, tmp_path, axes=["world_entity"], eval_ratio=0.25)
    loaded = load_unseen_split_manifest(
        tmp_path / "manifest.json", trust_mode="local_engineering"
    )

    assert loaded["trust_mode"] == "local_engineering"
    assert loaded["production_eligible"] is False
    assert loaded["row_attestations_verified"] is False
    assert loaded["release_manifest"] is None
    assert "attestation" not in loaded


def test_production_unseen_binds_release_rows_and_signed_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production_environment(monkeypatch)
    rows = [
        attach_attestation(_row(index), PROMOTION_KEY, purpose="sft_row")
        for index in range(8)
    ]
    for row in rows:
        world_number = int(row["world_id"].rsplit("-", 1)[1])
        row["workflow_ids"] = [f"shared-entity-{world_number // 2}"]
    rows = [
        attach_attestation(
            {key: value for key, value in row.items() if key != "attestation"},
            PROMOTION_KEY,
            purpose="sft_row",
        )
        for row in rows
    ]
    source_hashes = {
        "quality_report.json": _sha256(b"quality"),
        "train.jsonl": _sha256(b"train"),
        "eval.jsonl": _sha256(b"eval"),
    }
    approval = _production_approval(tmp_path, monkeypatch, source_hashes)
    release = _production_release(rows, source_hashes, production_approval=approval)

    build_unseen_splits(
        rows,
        tmp_path,
        axes=["world_entity"],
        eval_ratio=0.25,
        trust_mode="production",
        source_file_sha256={
            "train.jsonl": source_hashes["train.jsonl"],
            "eval.jsonl": source_hashes["eval.jsonl"],
        },
        release_manifest=release,
        promotion_attestation_key=PROMOTION_KEY,
        release_attestation_key=AUDITOR_KEY,
        manifest_attestation_key=REPORT_KEY,
    )
    loaded = load_unseen_split_manifest(
        tmp_path / "manifest.json",
        trust_mode="production",
        release_manifest=release,
        release_attestation_key=AUDITOR_KEY,
        manifest_attestation_key=REPORT_KEY,
    )

    assert loaded["trust_mode"] == "production"
    assert loaded["production_eligible"] is True
    assert loaded["row_attestations_verified"] is True
    assert loaded["release_manifest"]["release_profile_id"] == PRODUCTION_PROFILE
    assert loaded["attestation"]["purpose"] == "training_export_manifest"
    assert loaded["attestation"]["role"] == "report"
    assert loaded["attestation"]["environment"] == "production"

    output = tmp_path / "world_entity" / "eval.jsonl"
    original_output = output.read_text(encoding="utf-8")
    output.write_text(original_output + "{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="output digest"):
        load_unseen_split_manifest(
            tmp_path / "manifest.json",
            trust_mode="production",
            release_manifest=release,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )
    output.write_text(original_output, encoding="utf-8")

    manifest_path = tmp_path / "manifest.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    signed_manifest = json.loads(original_manifest)
    signed_manifest["split_seed"] = 999
    manifest_path.write_text(json.dumps(signed_manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest attestation"):
        load_unseen_split_manifest(
            manifest_path,
            trust_mode="production",
            release_manifest=release,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )
    manifest_path.write_text(original_manifest, encoding="utf-8")

    tampered_release = dict(release)
    tampered_release["n_rows"] = 999
    with pytest.raises(ValueError, match="release attestation"):
        load_unseen_split_manifest(
            tmp_path / "manifest.json",
            trust_mode="production",
            release_manifest=tampered_release,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_production_unseen_rejects_same_count_rows_from_another_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, source_hashes, release = _approved_production_case(tmp_path, monkeypatch)
    alternate_rows = []
    for index, row in enumerate(rows):
        unsigned = {key: value for key, value in row.items() if key != "attestation"}
        unsigned["answer"] = f"alternate-release-answer-{index}"
        alternate_rows.append(
            attach_attestation(unsigned, PROMOTION_KEY, purpose="sft_row")
        )

    with pytest.raises(ValueError, match="release manifest is invalid"):
        build_unseen_splits(
            alternate_rows,
            tmp_path / "unseen",
            axes=["topology_operator"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=release,
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_load_production_unseen_rejects_blocked_axis_marked_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, source_hashes, release = _approved_production_case(
        tmp_path, monkeypatch, shared_entities=True
    )
    build_unseen_splits(
        rows,
        tmp_path,
        axes=["world_entity"],
        trust_mode="production",
        source_file_sha256={
            "train.jsonl": source_hashes["train.jsonl"],
            "eval.jsonl": source_hashes["eval.jsonl"],
        },
        release_manifest=release,
        promotion_attestation_key=PROMOTION_KEY,
        release_attestation_key=AUDITOR_KEY,
        manifest_attestation_key=REPORT_KEY,
    )

    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("attestation")
    manifest["axes"] = {
        "world_entity": {
            "status": "blocked",
            "reason": "world_atomic_only",
            "coverage": "world_atomic_only",
            "n_rows": len(rows),
        }
    }
    manifest = attach_attestation(
        manifest, REPORT_KEY, purpose="training_export_manifest"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "world_entity" / "train.jsonl").unlink()
    (tmp_path / "world_entity" / "eval.jsonl").unlink()

    with pytest.raises(ValueError, match="production unseen axes are not ready"):
        load_unseen_split_manifest(
            manifest_path,
            trust_mode="production",
            release_manifest=release,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_load_production_unseen_rejects_empty_ready_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, source_hashes, release = _approved_production_case(
        tmp_path, monkeypatch, shared_entities=True
    )
    build_unseen_splits(
        rows,
        tmp_path,
        axes=["world_entity"],
        trust_mode="production",
        source_file_sha256={
            "train.jsonl": source_hashes["train.jsonl"],
            "eval.jsonl": source_hashes["eval.jsonl"],
        },
        release_manifest=release,
        promotion_attestation_key=PROMOTION_KEY,
        release_attestation_key=AUDITOR_KEY,
        manifest_attestation_key=REPORT_KEY,
    )

    output = tmp_path / "world_entity" / "train.jsonl"
    output.write_bytes(b"")
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("attestation")
    manifest["axes"]["world_entity"]["train_sha256"] = _sha256(b"")
    manifest = attach_attestation(
        manifest, REPORT_KEY, purpose="training_export_manifest"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="unseen split output is empty"):
        load_unseen_split_manifest(
            manifest_path,
            trust_mode="production",
            release_manifest=release,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_production_unseen_rejects_tampered_row_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production_environment(monkeypatch)
    rows = [
        attach_attestation(_row(index), PROMOTION_KEY, purpose="sft_row")
        for index in range(8)
    ]
    rows[0]["dossier_id"] = "tampered-dossier"
    source_hashes = {
        "quality_report.json": _sha256(b"quality"),
        "train.jsonl": _sha256(b"train"),
        "eval.jsonl": _sha256(b"eval"),
    }

    with pytest.raises(ValueError, match="row attestation"):
        build_unseen_splits(
            rows,
            tmp_path,
            axes=["world_entity"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=_production_release(rows, source_hashes),
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_production_unseen_rejects_release_source_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production_environment(monkeypatch)
    rows = [
        attach_attestation(_row(index), PROMOTION_KEY, purpose="sft_row")
        for index in range(8)
    ]
    source_hashes = {
        "quality_report.json": _sha256(b"quality"),
        "train.jsonl": _sha256(b"train"),
        "eval.jsonl": _sha256(b"eval"),
    }

    approval = _production_approval(tmp_path, monkeypatch, source_hashes)
    with pytest.raises(ValueError, match="release source binding"):
        build_unseen_splits(
            rows,
            tmp_path,
            axes=["world_entity"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": _sha256(b"different-train"),
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=_production_release(
                rows, source_hashes, production_approval=approval
            ),
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_production_unseen_rejects_hmac_only_release_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production_environment(monkeypatch)
    rows = [
        attach_attestation(_row(index), PROMOTION_KEY, purpose="sft_row")
        for index in range(8)
    ]
    source_hashes = {
        "quality_report.json": _sha256(b"quality"),
        "train.jsonl": _sha256(b"train"),
        "eval.jsonl": _sha256(b"eval"),
    }

    with pytest.raises(ValueError, match="production approval"):
        build_unseen_splits(
            rows,
            tmp_path,
            axes=["world_entity"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=_production_release(rows, source_hashes),
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_production_unseen_rejects_unknown_release_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production_environment(monkeypatch)
    rows = [
        attach_attestation(_row(index), PROMOTION_KEY, purpose="sft_row")
        for index in range(8)
    ]
    source_hashes = {
        "quality_report.json": _sha256(b"quality"),
        "train.jsonl": _sha256(b"train"),
        "eval.jsonl": _sha256(b"eval"),
    }
    release = _production_release(
        rows,
        source_hashes,
        release_profile_id="production-unseen-unknown-v1",
        release_profile_digest="d" * 64,
    )

    with pytest.raises(ValueError, match="unknown release profile"):
        build_unseen_splits(
            rows,
            tmp_path,
            axes=["world_entity"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=release,
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_production_unseen_rejects_nonissuable_release_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, source_hashes, release = _approved_production_case(
        tmp_path, monkeypatch, allow_profile=False
    )

    with pytest.raises(ValueError, match="superseded production release profile"):
        build_unseen_splits(
            rows,
            tmp_path,
            axes=["topology_operator"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=release,
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )


def test_production_unseen_rejects_when_any_requested_axis_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, source_hashes, release = _approved_production_case(tmp_path, monkeypatch)
    output_dir = tmp_path / "unseen"

    with pytest.raises(ValueError, match="production unseen axes are not ready"):
        build_unseen_splits(
            rows,
            output_dir,
            axes=["topology_operator", "source_document_family"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=release,
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )

    assert not (output_dir / "manifest.json").exists()
    for axis in ("topology_operator", "source_document_family"):
        assert not (output_dir / axis / "train.jsonl").exists()
        assert not (output_dir / axis / "eval.jsonl").exists()


@pytest.mark.parametrize(
    ("manifest_key", "message"),
    [
        (None, "manifest attestation key is missing"),
        (b"wrong-report-key-material-for-cleanup-test", "identity is incomplete"),
    ],
)
def test_production_unseen_cleans_outputs_when_report_signing_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_key: bytes | None,
    message: str,
) -> None:
    rows, source_hashes, release = _approved_production_case(
        tmp_path, monkeypatch, shared_entities=True
    )
    output_dir = tmp_path / "unseen"

    with pytest.raises(ValueError, match=message):
        build_unseen_splits(
            rows,
            output_dir,
            axes=["world_entity"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=release,
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=manifest_key,
        )

    assert not (output_dir / "manifest.json").exists()
    assert not (output_dir / "world_entity" / "train.jsonl").exists()
    assert not (output_dir / "world_entity" / "eval.jsonl").exists()


def test_production_unseen_cleans_first_axis_when_later_axis_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, source_hashes, release = _approved_production_case(tmp_path, monkeypatch)
    unsigned = [
        {
            **{key: value for key, value in row.items() if key != "attestation"},
            "source_family_ids": [f"family-{index // 2}"],
        }
        for index, row in enumerate(rows)
    ]
    unsigned[2]["dossier_id"] = "dossier-0"
    rows = [
        attach_attestation(row, PROMOTION_KEY, purpose="sft_row") for row in unsigned
    ]
    release = _production_release(
        rows,
        source_hashes,
        production_approval=release["production_approval"],
    )
    output_dir = tmp_path / "unseen"

    with pytest.raises(ValueError, match="dossier spans unseen groups"):
        build_unseen_splits(
            rows,
            output_dir,
            axes=["source_document_family", "world_entity"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=release,
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )

    assert not (output_dir / "manifest.json").exists()
    for axis in ("source_document_family", "world_entity"):
        assert not (output_dir / axis / "train.jsonl").exists()
        assert not (output_dir / axis / "eval.jsonl").exists()


def test_production_world_entity_rejects_world_atomic_only_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, source_hashes, release = _approved_production_case(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="production unseen axes are not ready"):
        build_unseen_splits(
            rows,
            tmp_path,
            axes=["world_entity"],
            trust_mode="production",
            source_file_sha256={
                "train.jsonl": source_hashes["train.jsonl"],
                "eval.jsonl": source_hashes["eval.jsonl"],
            },
            release_manifest=release,
            promotion_attestation_key=PROMOTION_KEY,
            release_attestation_key=AUDITOR_KEY,
            manifest_attestation_key=REPORT_KEY,
        )

    assert not (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "world_entity" / "train.jsonl").exists()
    assert not (tmp_path / "world_entity" / "eval.jsonl").exists()
