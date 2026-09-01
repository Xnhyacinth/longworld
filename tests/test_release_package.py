from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import longworld.core.release_profile as release_profile_module
from longworld.core.attestation import attach_attestation
from longworld.core.production_trust import (
    APPROVAL_DIGEST_ENV,
    APPROVAL_PATH_ENV,
    PACKAGE_APPROVAL_DIGEST_ENV,
    PACKAGE_APPROVAL_PATH_ENV,
    TRUST_ROOTS_DIGEST_ENV,
    TRUST_ROOTS_PATH_ENV,
    canonical_approval_statement,
    verify_production_approval_from_env,
    verify_production_package_approval_from_env,
)
from longworld.core.promotion import (
    RELEASE_GATE_PURPOSE,
    RELEASE_GATE_REVISION,
    RELEASE_GATE_SCHEMA,
)
from longworld.core.release_inventory import (
    release_commit_marker_bytes,
    validate_production_release_preflight,
    validate_release_inventory,
)
from longworld.core.release_profile import release_profile_sha256
from longworld.core.training_manifest import create_training_manifest, file_sha256

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_release_package import (
    build_release_package,
    finalize_release_package,
    prepare_release_package,
)

PROFILE = "p3-probe-12-v1"
PRODUCTION_PROFILE = "p10-source-rich-production-48-v1"
SUPERSEDED_PRODUCTION_PROFILE = "p3-production-48-v1"
TRANSFORM = "sharegpt-v-test"
AUDITOR_KEY = b"release-package-auditor-test-key-32b"
REPORT_KEY = b"release-package-report-test-key-32by"


def _allow_historical_production_profile_for_contract_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        release_profile_module,
        "ISSUABLE_PRODUCTION_PROFILE_IDS",
        frozenset({PRODUCTION_PROFILE}),
    )


def _write_release_sources(
    source: Path, *, release_profile_id: str = PROFILE
) -> dict[str, str]:
    source.mkdir(parents=True)
    (source / "quality_report.json").write_text(
        json.dumps(
            {
                "data_stage": "train_ready",
                "release_profile_id": release_profile_id,
                "release_profile_sha256": release_profile_sha256(release_profile_id),
                "release_selection_sha256": "e" * 64,
            }
        )
        + "\n"
    )
    (source / "train.jsonl").write_text('{"split":"train"}\n')
    (source / "eval.jsonl").write_text('{"split":"eval"}\n')
    return {
        name: file_sha256(source / name)
        for name in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }


def _prepare_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    absolute_sampler_path: bool = False,
    release_profile_id: str = PROFILE,
    environment: str = "probe",
    production_approval: dict | None = None,
) -> Path:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", environment)
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", AUDITOR_KEY.decode())
    monkeypatch.setenv(
        "LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", f"{environment}-auditor-v1"
    )
    monkeypatch.setenv("LONGWORLD_REPORT_ATTESTATION_KEY", REPORT_KEY.decode())
    monkeypatch.setenv(
        "LONGWORLD_REPORT_ATTESTATION_KEY_ID", f"{environment}-report-v1"
    )
    root = tmp_path / "source-release"
    promoted = root / "04_promoted"
    training = root / "05_training"
    source_digests = _write_release_sources(
        promoted, release_profile_id=release_profile_id
    )
    receipt = attach_attestation(
        {
            "schema_version": RELEASE_GATE_SCHEMA,
            "gate_revision": RELEASE_GATE_REVISION,
            "release_profile_id": release_profile_id,
            "release_profile_sha256": release_profile_sha256(release_profile_id),
            "source_file_sha256": source_digests,
            "production_approval": production_approval,
            "ok": True,
            "errors": [],
        },
        AUDITOR_KEY,
        purpose=RELEASE_GATE_PURPOSE,
    )
    (promoted / "release_gate_pass.json").write_text(json.dumps(receipt) + "\n")
    training.mkdir()
    shard = training / "B5.json"
    shard.write_text("[]\n")
    dataset_info = training / "dataset_info.json"
    dataset_info.write_text('{"causaltwin_b5": {"file_name": "B5.json"}}\n')
    weighted_shard = training / "B5w.weight3.json"
    weighted_shard.write_text("[]\n")
    weighted_index = training / "B5w.datasets.yaml"
    sampler_path = str(weighted_shard) if absolute_sampler_path else weighted_shard.name
    weighted_index.write_text(
        f"causaltwin_b5w_w3:\n  converter: sharegpt\n  path: {sampler_path}\n"
        "  source: local\n  weight: 3.0\n"
    )
    create_training_manifest(
        training / "training_export_manifest.json",
        release_root=root,
        source_data_dir=promoted,
        release_profile_id=release_profile_id,
        transform_revision=TRANSFORM,
        output_paths=[shard, dataset_info, weighted_shard, weighted_index],
        source_file_sha256=source_digests,
        attestation_key=REPORT_KEY,
    )
    (root / "DATA_CARD.md").write_text("# Private LongWorld release\n")
    (root / "candidates.jsonl").write_text("secret candidate\n")
    (root / "rejects.jsonl").write_text("secret reject\n")
    (root / ".env").write_text("SECRET=never-copy\n")
    (root / "keys").mkdir()
    (root / "keys" / "auditor.key").write_text("never-copy\n")
    return root


def _resign_training_outputs(root: Path) -> None:
    promoted = root / "04_promoted"
    training = root / "05_training"
    manifest_path = training / "training_export_manifest.json"
    create_training_manifest(
        manifest_path,
        release_root=root,
        source_data_dir=promoted,
        release_profile_id=PROFILE,
        transform_revision=TRANSFORM,
        output_paths=sorted(
            path for path in training.iterdir() if path != manifest_path
        ),
        source_file_sha256={
            name: file_sha256(promoted / name)
            for name in ("quality_report.json", "train.jsonl", "eval.jsonl")
        },
        attestation_key=REPORT_KEY,
    )


def _build_local_release(source_root: Path, destination: Path) -> None:
    build_release_package(
        source_release_root=source_root,
        destination=destination,
        promoted_dir=source_root / "04_promoted",
        training_manifest_path=(
            source_root / "05_training" / "training_export_manifest.json"
        ),
        data_card_path=source_root / "DATA_CARD.md",
        release_profile_id=PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )


def _b5w_index(
    *,
    converter: str = "sharegpt",
    path: str = "B5w.weight3.json",
    source: str = "local",
    weight: str = "3.0",
) -> str:
    return (
        "causaltwin_b5w_w3:\n"
        f"  converter: {converter}\n"
        f"  path: {path}\n"
        f"  source: {source}\n"
        f"  weight: {weight}\n"
    )


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
        "key/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )
    package_private_key = ec.generate_private_key(ec.SECP256R1())
    package_public_key = package_private_key.public_key()
    package_public_der = package_public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    package_key_id = (
        "aws-kms://arn:aws:kms:us-east-1:123456789012:"
        "key/99999999-bbbb-cccc-dddd-eeeeeeeeeeee"
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
        "approval_nonce": "release-package-approval-001",
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
    trust_roots = {
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
            },
            {
                "key_id": package_key_id,
                "scheme": "ecdsa-p256-sha256-v1",
                "public_key_pem": package_public_key.public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ).decode(),
                "public_key_sha256": hashlib.sha256(package_public_der).hexdigest(),
                "provider": "aws-kms",
                "approval_authorities": ["dataset-publication-governance"],
            },
        ],
    }
    approval_path = tmp_path / "production-approval.json"
    roots_path = tmp_path / "production-trust-roots.json"
    approval_path.write_text(json.dumps(approval, sort_keys=True))
    roots_path.write_text(json.dumps(trust_roots, sort_keys=True))
    (tmp_path / "test-production-approval-private-key.pem").write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (tmp_path / "test-production-package-private-key.pem").write_bytes(
        package_private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
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


def _package_approval_from_request(
    request_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    independent: bool = True,
) -> None:
    request = json.loads(request_path.read_text())
    private_key = serialization.load_pem_private_key(
        (
            tmp_path
            / (
                "test-production-package-private-key.pem"
                if independent
                else "test-production-approval-private-key.pem"
            )
        ).read_bytes(),
        password=None,
    )
    assert isinstance(private_key, ec.EllipticCurvePrivateKey)
    roots_path = tmp_path / "production-trust-roots.json"
    roots = json.loads(roots_path.read_text())
    key_id = roots["keys"][1 if independent else 0]["key_id"]
    statement = {
        **request,
        "schema_version": "longworld-production-package-approval-v1",
        "decision": "approved",
        "approval_authority": (
            "dataset-publication-governance" if independent else "release-governance"
        ),
        "approval_nonce": "package-approval-2026-09-01-001",
        "issued_at": "2026-09-01T12:00:00Z",
    }
    approval = {
        "statement": statement,
        "signature": {
            "scheme": "ecdsa-p256-sha256-v1",
            "key_id": key_id,
            "value_base64": base64.b64encode(
                private_key.sign(
                    canonical_approval_statement(statement),
                    ec.ECDSA(hashes.SHA256()),
                )
            ).decode(),
        },
    }
    approval_path = tmp_path / "production-package-approval.json"
    approval_path.write_text(json.dumps(approval, sort_keys=True))
    monkeypatch.setenv(PACKAGE_APPROVAL_PATH_ENV, str(approval_path))
    monkeypatch.setenv(
        PACKAGE_APPROVAL_DIGEST_ENV,
        hashlib.sha256(approval_path.read_bytes()).hexdigest(),
    )


def _prepare_production_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict]:
    import build_release_package as package_builder

    from longworld.core import release_inventory

    _allow_historical_production_profile_for_contract_test(monkeypatch)
    source_root = _prepare_release(
        tmp_path,
        monkeypatch,
        release_profile_id=PRODUCTION_PROFILE,
        environment="production",
    )
    promoted = source_root / "04_promoted"
    source_file_sha256 = {
        name: file_sha256(promoted / name)
        for name in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }
    release_approval = _production_approval(tmp_path, monkeypatch, source_file_sha256)
    gate = json.loads((promoted / "release_gate_pass.json").read_text())
    gate["production_approval"] = release_approval
    gate = attach_attestation(gate, AUDITOR_KEY, purpose=RELEASE_GATE_PURPOSE)
    (promoted / "release_gate_pass.json").write_text(json.dumps(gate) + "\n")
    monkeypatch.setattr(
        release_inventory,
        "PRODUCTION_PACKAGE_READY_PROFILE_IDS",
        frozenset({PRODUCTION_PROFILE}),
    )
    monkeypatch.setattr(
        package_builder,
        "validate_deterministic_training_transform",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.delenv(PACKAGE_APPROVAL_PATH_ENV, raising=False)
    monkeypatch.delenv(PACKAGE_APPROVAL_DIGEST_ENV, raising=False)
    destination = tmp_path / "production-stage"
    request_path = tmp_path / "production-package-approval-request.json"
    prepared = prepare_release_package(
        source_release_root=source_root,
        destination=destination,
        promoted_dir=promoted,
        training_manifest_path=(
            source_root / "05_training" / "training_export_manifest.json"
        ),
        data_card_path=source_root / "DATA_CARD.md",
        approval_request_path=request_path,
        release_profile_id=PRODUCTION_PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )
    return destination, request_path, prepared


def test_release_package_is_portable_allowlisted_and_committed_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    destination = tmp_path / "hf-private-stage"

    build_release_package(
        source_release_root=source_root,
        destination=destination,
        promoted_dir=source_root / "04_promoted",
        training_manifest_path=(
            source_root / "05_training" / "training_export_manifest.json"
        ),
        data_card_path=source_root / "DATA_CARD.md",
        release_profile_id=PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )

    assert (destination / "COMMITTED").is_file()
    assert (destination / "README.md").is_file()
    inventory = json.loads(
        (destination / "release_inventory.json").read_text(encoding="utf-8")
    )
    assert inventory["trust_mode"] == "local_engineering"
    assert inventory["production_eligible"] is False
    assert inventory["attestation"]["purpose"] == "release_inventory"
    assert inventory["attestation"].get("environment") != "production"
    assert all(
        not Path(entry["path"]).is_absolute() and ".." not in Path(entry["path"]).parts
        for entry in inventory["files"]
    )
    packaged_names = {path.name for path in destination.rglob("*") if path.is_file()}
    assert not packaged_names.intersection(
        {"candidates.jsonl", "rejects.jsonl", ".env", "auditor.key"}
    )
    validate_release_inventory(
        destination,
        expected_release_profile_id=PROFILE,
        expected_transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )

    relocated = tmp_path / "relocated-stage"
    destination.rename(relocated)
    validate_release_inventory(
        relocated,
        expected_release_profile_id=PROFILE,
        expected_transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )


def test_committed_historical_profile_remains_verifiable_when_issuance_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(
        tmp_path,
        monkeypatch,
        release_profile_id=PRODUCTION_PROFILE,
    )
    destination = tmp_path / "historical-profile-local-package"
    build_release_package(
        source_release_root=source_root,
        destination=destination,
        promoted_dir=source_root / "04_promoted",
        training_manifest_path=(
            source_root / "05_training" / "training_export_manifest.json"
        ),
        data_card_path=source_root / "DATA_CARD.md",
        release_profile_id=PRODUCTION_PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )

    assert release_profile_module.ISSUABLE_PRODUCTION_PROFILE_IDS == frozenset()
    validate_release_inventory(
        destination,
        expected_release_profile_id=PRODUCTION_PROFILE,
        expected_transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )


def test_release_inventory_rejects_extra_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    destination = tmp_path / "hf-private-stage"
    build_release_package(
        source_release_root=source_root,
        destination=destination,
        promoted_dir=source_root / "04_promoted",
        training_manifest_path=(
            source_root / "05_training" / "training_export_manifest.json"
        ),
        data_card_path=source_root / "DATA_CARD.md",
        release_profile_id=PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )
    (destination / "rejects.jsonl").write_text("must not ship\n")

    with pytest.raises(ValueError, match="unexpected release package files"):
        validate_release_inventory(
            destination,
            expected_release_profile_id=PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )


def test_local_inventory_tamper_fails_even_if_commit_hash_is_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    destination = tmp_path / "hf-private-stage"
    build_release_package(
        source_release_root=source_root,
        destination=destination,
        promoted_dir=source_root / "04_promoted",
        training_manifest_path=(
            source_root / "05_training" / "training_export_manifest.json"
        ),
        data_card_path=source_root / "DATA_CARD.md",
        release_profile_id=PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )
    inventory_path = destination / "release_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["production_eligible"] = True
    inventory_path.write_text(json.dumps(inventory) + "\n")
    marker_path = destination / "COMMITTED"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["inventory_sha256"] = file_sha256(inventory_path)
    marker_path.write_text(json.dumps(marker) + "\n")

    with pytest.raises(ValueError, match="identity or commit marker"):
        validate_release_inventory(
            destination,
            expected_release_profile_id=PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )


def test_release_builder_rejects_symlinked_data_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    card_link = source_root / "CARD_LINK.md"
    card_link.symlink_to(source_root / "DATA_CARD.md")

    with pytest.raises(ValueError, match="symlink"):
        build_release_package(
            source_release_root=source_root,
            destination=tmp_path / "hf-private-stage",
            promoted_dir=source_root / "04_promoted",
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=card_link,
            release_profile_id=PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )


def test_release_builder_retains_private_orphan_on_gate_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    destination = tmp_path / "hf-private-stage"
    gate_path = source_root / "04_promoted" / "release_gate_pass.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["ok"] = False
    gate_path.write_text(json.dumps(gate) + "\n")

    with pytest.raises(ValueError, match="release gate receipt"):
        build_release_package(
            source_release_root=source_root,
            destination=destination,
            promoted_dir=source_root / "04_promoted",
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=source_root / "DATA_CARD.md",
            release_profile_id=PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not destination.exists()
    orphans = list(tmp_path.glob(".hf-private-stage.*"))
    assert len(orphans) == 1
    assert orphans[0].is_dir()
    assert orphans[0].stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "payload,error",
    [
        ("contact=owner@example.org\n", "PII"),
        ("token=ghp_abcdefghijklmnopqrstuvwxyz123456\n", "secret"),
    ],
)
def test_release_builder_scans_final_payload_bytes_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    error: str,
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    (source_root / "DATA_CARD.md").write_text(payload)
    destination = tmp_path / "hf-private-stage"

    with pytest.raises(ValueError, match=error):
        _build_local_release(source_root, destination)

    assert not destination.exists()


def test_release_builder_rejects_candidate_stage_in_signed_training_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    (source_root / "05_training" / "B5.json").write_text(
        '[{"data_stage":"candidate"}]\n'
    )
    _resign_training_outputs(source_root)
    destination = tmp_path / "hf-private-stage"

    with pytest.raises(ValueError, match="non-release data stage"):
        _build_local_release(source_root, destination)

    assert not destination.exists()


def test_release_builder_rejects_signed_absolute_sampler_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch, absolute_sampler_path=True)

    with pytest.raises(ValueError, match="index-relative"):
        build_release_package(
            source_release_root=source_root,
            destination=tmp_path / "hf-private-stage",
            promoted_dir=source_root / "04_promoted",
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=source_root / "DATA_CARD.md",
            release_profile_id=PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )


@pytest.mark.parametrize(
    "index_text, error",
    [
        (
            _b5w_index(source="hf_hub"),
            "source must be local",
        ),
        (
            _b5w_index(converter="attacker-loader"),
            "converter must be sharegpt",
        ),
        (
            _b5w_index(weight=".inf"),
            "weight must be a finite number from 1 to 3",
        ),
        (
            _b5w_index(weight="4"),
            "weight must be a finite number from 1 to 3",
        ),
    ],
)
def test_release_builder_rejects_invalid_signed_b5w_loader_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    index_text: str,
    error: str,
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    (source_root / "05_training" / "B5w.datasets.yaml").write_text(index_text)
    _resign_training_outputs(source_root)

    with pytest.raises(ValueError, match=error):
        _build_local_release(source_root, tmp_path / "hf-private-stage")


def test_release_builder_rejects_duplicate_signed_b5w_payload_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    index_path = source_root / "05_training" / "B5w.datasets.yaml"
    index_path.write_text(
        "causaltwin_b5w_w3:\n"
        "  converter: sharegpt\n"
        "  path: B5w.weight3.json\n"
        "  source: local\n"
        "  weight: 3\n"
        "duplicate:\n"
        "  converter: sharegpt\n"
        "  path: B5w.weight3.json\n"
        "  source: local\n"
        "  weight: 3\n"
    )
    _resign_training_outputs(source_root)

    with pytest.raises(ValueError, match="duplicate weighted dataset path"):
        _build_local_release(source_root, tmp_path / "hf-private-stage")


def test_release_builder_rejects_unindexed_signed_b5w_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    (source_root / "05_training" / "B5w.weight2.json").write_text("[]\n")
    _resign_training_outputs(source_root)

    with pytest.raises(ValueError, match="index does not exactly cover B5w payloads"):
        _build_local_release(source_root, tmp_path / "hf-private-stage")


def test_release_builder_rejects_non_b5w_signed_payload_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    (source_root / "05_training" / "B5w.datasets.yaml").write_text(
        "causaltwin_b5w_w3:\n"
        "  converter: sharegpt\n"
        "  path: B5.json\n"
        "  source: local\n"
        "  weight: 3\n"
    )
    _resign_training_outputs(source_root)

    with pytest.raises(ValueError, match="index does not exactly cover B5w payloads"):
        _build_local_release(source_root, tmp_path / "hf-private-stage")


def test_release_builder_rejects_b5w_shard_name_weight_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    (source_root / "05_training" / "B5w.datasets.yaml").write_text(
        "causaltwin_b5w_w2:\n"
        "  converter: sharegpt\n"
        "  path: B5w.weight3.json\n"
        "  source: local\n"
        "  weight: 2\n"
    )
    _resign_training_outputs(source_root)

    with pytest.raises(ValueError, match="shard name does not match weight"):
        _build_local_release(source_root, tmp_path / "hf-private-stage")


def test_release_builder_accepts_single_weight_b5w_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    training = source_root / "05_training"
    (training / "B5w.weight3.json").unlink()
    (training / "B5w.json").write_text("[]\n")
    (training / "B5w.datasets.yaml").write_text(
        "causaltwin_b5w_w2:\n"
        "  converter: sharegpt\n"
        "  path: B5w.json\n"
        "  source: local\n"
        "  weight: 2\n"
    )
    _resign_training_outputs(source_root)

    _build_local_release(source_root, tmp_path / "hf-private-stage")


def test_probe_profile_cannot_be_committed_as_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(tmp_path, monkeypatch)
    destination = tmp_path / "hf-private-stage"

    with pytest.raises(ValueError, match="production release profile"):
        prepare_release_package(
            source_release_root=source_root,
            destination=destination,
            promoted_dir=source_root / "04_promoted",
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=source_root / "DATA_CARD.md",
            approval_request_path=tmp_path / "approval-request.json",
            release_profile_id=PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not destination.exists()


def test_superseded_production_profile_cannot_issue_a_new_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _prepare_release(
        tmp_path,
        monkeypatch,
        release_profile_id=SUPERSEDED_PRODUCTION_PROFILE,
        environment="production",
    )
    destination = tmp_path / "hf-private-stage"

    with pytest.raises(ValueError, match="superseded production release profile"):
        prepare_release_package(
            source_release_root=source_root,
            destination=destination,
            promoted_dir=source_root / "04_promoted",
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=source_root / "DATA_CARD.md",
            approval_request_path=tmp_path / "approval-request.json",
            release_profile_id=SUPERSEDED_PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not destination.exists()


def test_production_release_package_rejects_hmac_only_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_historical_production_profile_for_contract_test(monkeypatch)
    source_root = _prepare_release(
        tmp_path,
        monkeypatch,
        release_profile_id=PRODUCTION_PROFILE,
        environment="production",
    )
    destination = tmp_path / "hf-private-stage"

    with pytest.raises(ValueError, match="production approval"):
        prepare_release_package(
            source_release_root=source_root,
            destination=destination,
            promoted_dir=source_root / "04_promoted",
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=source_root / "DATA_CARD.md",
            approval_request_path=tmp_path / "approval-request.json",
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not destination.exists()


def test_production_release_package_blocks_until_packaging_contract_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_historical_production_profile_for_contract_test(monkeypatch)
    source_root = _prepare_release(
        tmp_path,
        monkeypatch,
        release_profile_id=PRODUCTION_PROFILE,
        environment="production",
    )
    promoted = source_root / "04_promoted"
    source_file_sha256 = {
        name: file_sha256(promoted / name)
        for name in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }
    approval = _production_approval(tmp_path, monkeypatch, source_file_sha256)
    receipt = json.loads((promoted / "release_gate_pass.json").read_text())
    receipt["production_approval"] = approval
    receipt = attach_attestation(receipt, AUDITOR_KEY, purpose=RELEASE_GATE_PURPOSE)
    (promoted / "release_gate_pass.json").write_text(json.dumps(receipt) + "\n")
    destination = tmp_path / "hf-private-stage"

    with pytest.raises(ValueError, match="production packaging contract is not ready"):
        prepare_release_package(
            source_release_root=source_root,
            destination=destination,
            promoted_dir=promoted,
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=source_root / "DATA_CARD.md",
            approval_request_path=tmp_path / "approval-request.json",
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not destination.exists()


def test_single_phase_builder_rejects_production_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_release_package as package_builder

    from longworld.core import release_inventory

    _allow_historical_production_profile_for_contract_test(monkeypatch)
    source_root = _prepare_release(
        tmp_path,
        monkeypatch,
        release_profile_id=PRODUCTION_PROFILE,
        environment="production",
    )
    promoted = source_root / "04_promoted"
    source_file_sha256 = {
        name: file_sha256(promoted / name)
        for name in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }
    approval = _production_approval(tmp_path, monkeypatch, source_file_sha256)
    receipt = json.loads((promoted / "release_gate_pass.json").read_text())
    receipt["production_approval"] = approval
    receipt = attach_attestation(receipt, AUDITOR_KEY, purpose=RELEASE_GATE_PURPOSE)
    (promoted / "release_gate_pass.json").write_text(json.dumps(receipt) + "\n")
    monkeypatch.setattr(
        release_inventory,
        "PRODUCTION_PACKAGE_READY_PROFILE_IDS",
        frozenset({PRODUCTION_PROFILE}),
    )
    monkeypatch.setattr(
        package_builder,
        "validate_deterministic_training_transform",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.delenv(PACKAGE_APPROVAL_PATH_ENV, raising=False)
    monkeypatch.delenv(PACKAGE_APPROVAL_DIGEST_ENV, raising=False)
    destination = tmp_path / "hf-private-stage"

    with pytest.raises(ValueError, match="require prepare_release_package"):
        build_release_package(
            source_release_root=source_root,
            destination=destination,
            promoted_dir=promoted,
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=source_root / "DATA_CARD.md",
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
            trust_mode="production",
        )

    assert not destination.exists()


def test_production_prepare_stages_inventory_without_committing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )

    assert prepared["production_eligible"] is False
    assert (destination / "release_inventory.json").is_file()
    assert not (destination / "COMMITTED").exists()
    request = json.loads(request_path.read_text())
    assert request["release_inventory_sha256"] == file_sha256(
        destination / "release_inventory.json"
    )
    assert request["training_manifest_sha256"] == file_sha256(
        destination / "05_training" / "training_export_manifest.json"
    )
    assert len(request["committed_sha256"]) == 64
    with pytest.raises(ValueError, match="commit marker"):
        validate_production_release_preflight(
            destination,
            expected_release_profile_id=PRODUCTION_PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )


def test_production_finalize_commits_only_after_external_kms_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)

    preflight = finalize_release_package(
        staged_package=destination,
        release_profile_id=PRODUCTION_PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )

    assert preflight["production_eligible"] is True
    assert preflight["inventory"]["production_eligible"] is False
    marker = json.loads((destination / "COMMITTED").read_text())
    assert marker["production_eligible"] is True
    assert marker["production_approval"]["verified"] is True
    assert marker["production_approval"]["approval_key_id"].startswith("aws-kms://")
    assert (
        validate_release_inventory(
            destination,
            expected_release_profile_id=PRODUCTION_PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
            expected_trust_mode="production",
        )
        == preflight["inventory"]
    )
    assert (
        validate_production_release_preflight(
            destination,
            expected_release_profile_id=PRODUCTION_PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )
        == preflight
    )


def test_production_finalize_preflight_failure_never_exposes_commit_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_release_package as package_builder

    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    marker_observations: list[bool] = []

    def fail_preflight(*_args: object, **_kwargs: object) -> dict:
        marker_observations.append((destination / "COMMITTED").exists())
        raise ValueError("injected final preflight failure")

    monkeypatch.setattr(
        package_builder,
        "_validate_prospective_production_release_preflight",
        fail_preflight,
    )

    with pytest.raises(ValueError, match="injected final preflight failure"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert marker_observations == [False]
    assert not (destination / "COMMITTED").exists()


def test_production_finalize_marker_only_watcher_cannot_run_before_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_release_package as package_builder

    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    original_preflight = (
        package_builder._validate_prospective_production_release_preflight
    )
    preflight_started = threading.Event()
    allow_preflight = threading.Event()

    def blocked_preflight(*args: object, **kwargs: object) -> dict:
        preflight_started.set()
        if not allow_preflight.wait(timeout=5):
            raise TimeoutError("test did not release final preflight")
        return original_preflight(*args, **kwargs)

    monkeypatch.setattr(
        package_builder,
        "_validate_prospective_production_release_preflight",
        blocked_preflight,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            finalize_release_package,
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )
        assert preflight_started.wait(timeout=5)
        assert not (destination / "COMMITTED").exists()
        allow_preflight.set()
        preflight = future.result(timeout=5)

    assert preflight["production_eligible"] is True
    assert (destination / "COMMITTED").is_file()


def test_production_finalize_rechecks_package_readiness_after_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from longworld.core import release_inventory

    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    monkeypatch.setattr(
        release_inventory, "PRODUCTION_PACKAGE_READY_PROFILE_IDS", frozenset()
    )

    with pytest.raises(ValueError, match="production packaging contract is not ready"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (destination / "COMMITTED").exists()


def test_production_prepare_rejects_approval_request_inside_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = prepare_release_package

    def request_inside_destination(**kwargs: object) -> dict:
        destination = Path(str(kwargs["destination"]))
        return original(
            **{**kwargs, "approval_request_path": destination / "approval.json"}
        )

    monkeypatch.setattr(
        sys.modules[__name__], "prepare_release_package", request_inside_destination
    )

    with pytest.raises(ValueError, match="outside the staged package"):
        _prepare_production_stage(tmp_path, monkeypatch)

    assert not (tmp_path / "production-stage").exists()


def test_production_prepare_does_not_delete_concurrently_created_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_release_package as package_builder

    destination = tmp_path / "production-stage"
    original_validation = package_builder.validate_staged_release_inventory
    concurrent_identity: tuple[int, int] | None = None

    def concurrent_destination(*args: object, **kwargs: object) -> dict:
        nonlocal concurrent_identity
        inventory = original_validation(*args, **kwargs)
        destination.mkdir()
        status = destination.stat(follow_symlinks=False)
        concurrent_identity = status.st_dev, status.st_ino
        return inventory

    monkeypatch.setattr(
        package_builder, "validate_staged_release_inventory", concurrent_destination
    )

    with pytest.raises(FileExistsError):
        _prepare_production_stage(tmp_path, monkeypatch)

    status = destination.stat(follow_symlinks=False)
    assert (status.st_dev, status.st_ino) == concurrent_identity
    assert list(destination.iterdir()) == []


def test_production_prepare_does_not_overwrite_concurrent_approval_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_release_package as package_builder

    request_path = tmp_path / "production-package-approval-request.json"
    original_publish = package_builder._publish_path_no_replace

    def concurrent_publish(source: Path, destination: Path) -> None:
        if destination == request_path:
            request_path.write_text("concurrent owner\n")
        original_publish(source, destination)

    monkeypatch.setattr(package_builder, "_publish_path_no_replace", concurrent_publish)

    with pytest.raises(FileExistsError):
        _prepare_production_stage(tmp_path, monkeypatch)

    assert request_path.read_text() == "concurrent owner\n"
    assert (tmp_path / "production-stage" / "release_inventory.json").is_file()
    assert not (tmp_path / "production-stage" / "COMMITTED").exists()


def test_production_prepare_does_not_require_hard_link_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_release_package as package_builder

    monkeypatch.setattr(
        package_builder.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(errno.EXDEV, "cross-device link")
        ),
    )

    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )

    assert destination.is_dir()
    assert json.loads(request_path.read_text())["approval_scope"] == (
        "longworld-production-package"
    )


def test_production_prepare_failure_after_publication_retains_owned_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_release_package as package_builder

    destination = tmp_path / "production-stage"
    request_path = tmp_path / "production-package-approval-request.json"

    def fail_durability_sync(_path: Path) -> None:
        raise OSError(errno.EIO, "injected fsync failure")

    monkeypatch.setattr(package_builder, "_fsync_directory", fail_durability_sync)

    with pytest.raises(OSError, match="injected fsync failure"):
        _prepare_production_stage(tmp_path, monkeypatch)

    assert (destination / "release_inventory.json").is_file()
    assert not (destination / "COMMITTED").exists()
    assert request_path.is_file()


def test_production_finalize_rejects_staged_root_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    real_stage = tmp_path / "real-production-stage"
    destination.rename(real_stage)
    destination.symlink_to(real_stage, target_is_directory=True)

    with pytest.raises(ValueError, match="staged package root cannot be a symlink"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (real_stage / "COMMITTED").exists()


def test_production_preflight_rejects_committed_root_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    finalize_release_package(
        staged_package=destination,
        release_profile_id=PRODUCTION_PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )
    real_package = tmp_path / "real-production-package"
    destination.rename(real_package)
    destination.symlink_to(real_package, target_is_directory=True)

    with pytest.raises(ValueError, match="release package root cannot be a symlink"):
        validate_production_release_preflight(
            destination,
            expected_release_profile_id=PRODUCTION_PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )


def test_production_finalize_rejects_staged_root_identity_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_release_package as package_builder

    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    original_requirement = package_builder.require_independent_package_approval
    displaced = tmp_path / "displaced-production-stage"

    def swap_after_approval(release_approval: object, package_approval: object) -> None:
        original_requirement(release_approval, package_approval)
        destination.rename(displaced)
        destination.mkdir()

    monkeypatch.setattr(
        package_builder,
        "require_independent_package_approval",
        swap_after_approval,
    )

    with pytest.raises(ValueError, match="staged package root identity changed"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (destination / "COMMITTED").exists()
    assert not (displaced / "COMMITTED").exists()


def test_production_finalize_fails_closed_without_external_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, _request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )

    with pytest.raises(ValueError, match="external production package approval"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert destination.is_dir()
    assert not (destination / "COMMITTED").exists()


def test_production_finalize_rejects_upstream_release_approver_self_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(
        request_path, tmp_path, monkeypatch, independent=False
    )

    with pytest.raises(ValueError, match="independent KMS identity and authority"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (destination / "COMMITTED").exists()


def test_production_validator_rejects_self_approved_marker_bypassing_finalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(
        request_path, tmp_path, monkeypatch, independent=False
    )
    request = json.loads(request_path.read_text())
    package_approval = verify_production_package_approval_from_env(
        release_profile_id=PRODUCTION_PROFILE,
        release_profile_sha256=release_profile_sha256(PRODUCTION_PROFILE),
        release_inventory_sha256=request["release_inventory_sha256"],
        committed_sha256=request["committed_sha256"],
        training_manifest_sha256=request["training_manifest_sha256"],
    )
    marker = json.loads(
        release_commit_marker_bytes(destination / "release_inventory.json")
    )
    marker["production_approval"] = package_approval
    (destination / "COMMITTED").write_text(
        json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n"
    )

    with pytest.raises(ValueError, match="independent KMS identity and authority"):
        validate_release_inventory(
            destination,
            expected_release_profile_id=PRODUCTION_PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
            expected_trust_mode="production",
        )


def test_production_finalize_rejects_approval_for_changed_package_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    request = json.loads(request_path.read_text())
    request["release_inventory_sha256"] = "0" * 64
    request_path.write_text(json.dumps(request, sort_keys=True) + "\n")
    _package_approval_from_request(request_path, tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="package bytes"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (destination / "COMMITTED").exists()


def test_production_finalize_rejects_staged_payload_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    (destination / "04_promoted" / "train.jsonl").write_text('{"split":"attacker"}\n')

    with pytest.raises(ValueError, match="digest changed"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (destination / "COMMITTED").exists()


@pytest.mark.parametrize("entrypoint", ("finalize", "preflight"))
def test_production_entrypoints_reject_child_file_identity_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from longworld.core import release_inventory

    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    if entrypoint == "preflight":
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    target = destination / "04_promoted" / "train.jsonl"
    replacement = tmp_path / f"replacement-{entrypoint}.jsonl"
    replacement.write_bytes(target.read_bytes())
    swapped = False

    if entrypoint == "finalize":
        original_snapshot = release_inventory._release_member_snapshot
        snapshot_count = 0

        def swap_after_initial_snapshot(root: Path) -> dict:
            nonlocal snapshot_count, swapped
            snapshot = original_snapshot(root)
            snapshot_count += 1
            if snapshot_count == 1:
                os.replace(replacement, target)
                swapped = True
            return snapshot

        monkeypatch.setattr(
            release_inventory, "_release_member_snapshot", swap_after_initial_snapshot
        )
    else:
        original_file_sha256 = release_inventory.file_sha256

        def swap_after_validation(path: Path) -> str:
            nonlocal swapped
            digest = original_file_sha256(path)
            if Path(path).name == "COMMITTED" and not swapped:
                os.replace(replacement, target)
                swapped = True
            return digest

        monkeypatch.setattr(release_inventory, "file_sha256", swap_after_validation)

    with pytest.raises(ValueError, match="release package members changed"):
        if entrypoint == "finalize":
            finalize_release_package(
                staged_package=destination,
                release_profile_id=PRODUCTION_PROFILE,
                transform_revision=TRANSFORM,
                training_attestation_key=REPORT_KEY,
                gate_attestation_key=AUDITOR_KEY,
                inventory_attestation_key=REPORT_KEY,
            )
        else:
            validate_production_release_preflight(
                destination,
                expected_release_profile_id=PRODUCTION_PROFILE,
                expected_transform_revision=TRANSFORM,
                training_attestation_key=REPORT_KEY,
                gate_attestation_key=AUDITOR_KEY,
                inventory_attestation_key=REPORT_KEY,
            )

    assert swapped
    if entrypoint == "finalize":
        assert not (destination / "COMMITTED").exists()


@pytest.mark.parametrize("mutation", ("replace_early_member", "add_member"))
def test_production_preflight_rejects_mutation_during_final_member_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    from longworld.core import release_inventory

    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    finalize_release_package(
        staged_package=destination,
        release_profile_id=PRODUCTION_PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )

    regular_members = sorted(path for path in destination.rglob("*") if path.is_file())
    target = regular_members[0]
    original_sha256 = release_inventory.hashlib.sha256
    snapshot_hashes = 0

    def inside_member_snapshot() -> bool:
        frame = sys._getframe()
        while frame is not None:
            if (
                frame.f_code.co_name == "_release_member_snapshot"
                and frame.f_globals.get("__name__")
                == "longworld.core.release_inventory"
            ):
                return True
            frame = frame.f_back
        return False

    class MutatingHash:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._inner = original_sha256(*args, **kwargs)

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def update(self, value: bytes) -> None:
            self._inner.update(value)

        def hexdigest(self) -> str:
            nonlocal snapshot_hashes
            digest = self._inner.hexdigest()
            if inside_member_snapshot():
                snapshot_hashes += 1
                if snapshot_hashes == len(regular_members) + 2:
                    if mutation == "replace_early_member":
                        target.unlink()
                        target.write_bytes(b"changed during final snapshot\n")
                    else:
                        (destination / "unlisted-during-snapshot").write_bytes(b"new\n")
            return digest

    monkeypatch.setattr(release_inventory.hashlib, "sha256", MutatingHash)

    with pytest.raises(ValueError, match="release package members changed"):
        validate_production_release_preflight(
            destination,
            expected_release_profile_id=PRODUCTION_PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert snapshot_hashes >= len(regular_members) + 2


def test_production_finalize_rejects_unlisted_fifo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    os.mkfifo(destination / "unlisted-upload-stream")
    _package_approval_from_request(request_path, tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="non-regular package member"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (destination / "COMMITTED").exists()


def test_production_finalize_rejects_hardlinked_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    target = destination / "04_promoted" / "train.jsonl"
    external = tmp_path / "external-train.jsonl"
    external.write_bytes(target.read_bytes())
    target.unlink()
    os.link(external, target)
    _package_approval_from_request(request_path, tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="multiple hard links"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (destination / "COMMITTED").exists()


def test_production_finalize_rejects_unlisted_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    (destination / "unlisted-directory").mkdir()
    _package_approval_from_request(request_path, tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="unexpected release package directories"):
        finalize_release_package(
            staged_package=destination,
            release_profile_id=PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
        )

    assert not (destination / "COMMITTED").exists()


def test_committed_production_package_rechecks_protected_trust_root_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination, request_path, _prepared = _prepare_production_stage(
        tmp_path, monkeypatch
    )
    _package_approval_from_request(request_path, tmp_path, monkeypatch)
    finalize_release_package(
        staged_package=destination,
        release_profile_id=PRODUCTION_PROFILE,
        transform_revision=TRANSFORM,
        training_attestation_key=REPORT_KEY,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )
    monkeypatch.setenv(TRUST_ROOTS_DIGEST_ENV, "0" * 64)

    with pytest.raises(ValueError, match="production approval is invalid"):
        validate_release_inventory(
            destination,
            expected_release_profile_id=PRODUCTION_PROFILE,
            expected_transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
            expected_trust_mode="production",
        )
