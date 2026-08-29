from __future__ import annotations

import base64
import hashlib
import json
import sys
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
)
from longworld.core.promotion import (
    RELEASE_GATE_PURPOSE,
    RELEASE_GATE_REVISION,
    RELEASE_GATE_SCHEMA,
)
from longworld.core.release_inventory import validate_release_inventory
from longworld.core.release_profile import release_profile_sha256
from longworld.core.training_manifest import create_training_manifest, file_sha256

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_release_package import build_release_package

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
            }
        ],
    }
    approval_path = tmp_path / "production-approval.json"
    roots_path = tmp_path / "production-trust-roots.json"
    approval_path.write_text(json.dumps(approval, sort_keys=True))
    roots_path.write_text(json.dumps(trust_roots, sort_keys=True))
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


def test_release_builder_leaves_no_partial_destination_on_gate_failure(
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
    assert not list(tmp_path.glob(".hf-private-stage.*"))


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
            trust_mode="production",
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
        build_release_package(
            source_release_root=source_root,
            destination=destination,
            promoted_dir=source_root / "04_promoted",
            training_manifest_path=(
                source_root / "05_training" / "training_export_manifest.json"
            ),
            data_card_path=source_root / "DATA_CARD.md",
            release_profile_id=SUPERSEDED_PRODUCTION_PROFILE,
            transform_revision=TRANSFORM,
            training_attestation_key=REPORT_KEY,
            gate_attestation_key=AUDITOR_KEY,
            inventory_attestation_key=REPORT_KEY,
            trust_mode="production",
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
            trust_mode="production",
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


def test_production_package_fails_closed_without_final_package_approval(
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

    with pytest.raises(ValueError, match="external production package approval"):
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

    monkeypatch.setattr(
        package_builder,
        "verify_production_package_approval_from_env",
        lambda **_kwargs: {"verified": True},
    )
    with pytest.raises(ValueError, match="approval sidecar contract is not ready"):
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
