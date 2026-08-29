from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

import scripts.audit_release_union as audit_module
from longworld.core.attestation import attach_attestation
from longworld.core.promotion import (
    RELEASE_GATE_PURPOSE,
    RELEASE_GATE_REVISION,
    RELEASE_GATE_SCHEMA,
    promoted_row_set_sha256,
)
from longworld.core.release_inventory import RELEASE_INVENTORY_PURPOSE
from longworld.core.release_profile import release_profile, release_profile_sha256
from scripts.audit_release_union import (
    LOCAL_RELEASE_INVENTORY_PURPOSE,
    ReleaseUnionError,
    audit_release_union,
    main,
    verify_local_release_inventory_attestation,
)
from scripts.audit_semantic_coverage import audit_semantic_coverage

AUDITOR_KEY = b"release-union-auditor-test-key-32-bytes"
REPORT_KEY = b"release-union-report-test-key-32-bytesx"
SOURCE_PROFILE = "p7-wiki-source-slice-1-v1"
TARGET_PROFILE = "p12-current-source-probe-12-v1"


@pytest.fixture(autouse=True)
def _probe_attestation_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", AUDITOR_KEY.decode())
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", "probe-union-auditor-v1")
    monkeypatch.setenv("LONGWORLD_REPORT_ATTESTATION_KEY", REPORT_KEY.decode())
    monkeypatch.setenv("LONGWORLD_REPORT_ATTESTATION_KEY_ID", "probe-union-report-v1")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_content_hash(context: str, answer: str) -> str:
    payload = json.dumps({"context": context, "answer": answer}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _row(
    world_id: str,
    content_marker: str,
    *,
    bucket: str = "16k",
    tokens: int = 16_000,
    domain: str = "researchlab",
) -> dict[str, Any]:
    profile = release_profile(SOURCE_PROFILE)
    context = f"Question and source context for {content_marker}"
    answer = f"answer:{content_marker}"
    source_digest = hashlib.sha256(f"source:{world_id}".encode()).hexdigest()
    binding_digest = hashlib.sha256(f"binding:{world_id}".encode()).hexdigest()
    source_bundle = {
        "schema_version": "longworld.source-workflow-bundle.v1",
        "adapter_revision": "sourceworkflow@2",
        "sha256": source_digest,
        "binding_digest": binding_digest,
    }
    return {
        "world_id": world_id,
        "content_hash": _canonical_content_hash(context, answer),
        "context": context,
        "answer": answer,
        "length_bucket": bucket,
        "actual_context_tokens": tokens,
        "tokenizer_context_tokens": tokens,
        "tokenizer_model_id": profile.tokenizer_model_id,
        "tokenizer_revision": profile.tokenizer_revision,
        "tokenizer_asset_manifest_sha256": (profile.tokenizer_asset_manifest_sha256),
        "difficulty": {"context_tokens": tokens},
        "domain": domain,
        "query_type": "real_revision_added_text",
        "motif": "real_revision_semantic_delta",
        "answer_program_id": "program-real-revision",
        "executable_proof_id": f"proof:{world_id}:{bucket}",
        "program_ops": [{"op": "READ_SOURCE_SPAN"}],
        "real_source_family_ids": ["test_public_source"],
        "source_relation_edges": [],
        "authentic_source_relation_edges": [],
        "split": "train",
        "workflow_ids": [f"workflow:{world_id}"],
        "real_source_workflow_ids": [f"real:{world_id}"],
        "real_source_verified": True,
        "source_workflow_bundle": source_bundle,
        "promotion": {
            "real_source_verified": True,
            "source_workflow_bundle": source_bundle,
            "tokenizer_asset_manifest_sha256": (
                profile.tokenizer_asset_manifest_sha256
            ),
        },
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tokenizer_model_id", "unapproved/model"),
        ("tokenizer_revision", "f" * 40),
        ("tokenizer_asset_manifest_sha256", "e" * 64),
    ],
)
def test_rejects_tokenizer_identity_not_bound_to_source_profile(
    tmp_path: Path, field: str, value: str
) -> None:
    row = _row("world-a", "content-a")
    row[field] = value
    if field == "tokenizer_asset_manifest_sha256":
        row["promotion"][field] = value
    release = _write_release(tmp_path, "wrong-tokenizer", [row])

    with pytest.raises(ReleaseUnionError, match="source release profile"):
        _audit([release], tmp_path)


@pytest.mark.parametrize(
    ("bucket", "tokens"),
    [("128k", 128_000), ("96k", 96_000)],
)
def test_rejects_bucket_not_admitted_by_source_profile(
    tmp_path: Path, bucket: str, tokens: int
) -> None:
    release = _write_release(
        tmp_path,
        "unsupported-bucket",
        [_row("world-a", "content-a", bucket=bucket, tokens=tokens)],
    )

    with pytest.raises(ReleaseUnionError, match="source release profile"):
        _audit([release], tmp_path)


def _write_release(
    root: Path,
    name: str,
    rows: list[dict[str, Any]],
    *,
    receipt_name: str = "release_gate_receipt.json",
    release_profile_id: str = SOURCE_PROFILE,
) -> Path:
    profile = release_profile(release_profile_id)
    release = root / name
    release.mkdir(parents=True)
    train = release / "train.jsonl"
    train.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (release / "eval.jsonl").write_text("")
    worlds = {row.get("world_id") for row in rows if row.get("world_id")}
    report = {
        "data_stage": "train_ready",
        "release_profile_id": release_profile_id,
        "release_profile_sha256": release_profile_sha256(release_profile_id),
        "promoted_row_set_sha256": promoted_row_set_sha256(rows),
        "tokenizer_model_id": profile.tokenizer_model_id,
        "tokenizer_revision": profile.tokenizer_revision,
        "tokenizer_asset_manifest_sha256": profile.tokenizer_asset_manifest_sha256,
        "n_rows": len(rows),
        "n_worlds": len(worlds),
    }
    (release / "quality_report.json").write_text(json.dumps(report) + "\n")
    source_hashes = {
        filename: _sha256(release / filename)
        for filename in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }
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
            "quality_report_sha256": source_hashes["quality_report.json"],
            "source_file_sha256": source_hashes,
            "metrics_sha256": "a" * 64,
            "n_rows": len(rows),
            "n_worlds": len(worlds),
            "ok": True,
            "errors": [],
        },
        AUDITOR_KEY,
        purpose=RELEASE_GATE_PURPOSE,
    )
    (release / receipt_name).write_text(json.dumps(receipt) + "\n")
    return release


def _audit(releases: list[Path], root: Path) -> dict[str, Any]:
    return audit_release_union(
        releases,
        root=root,
        target_release_profile_id=TARGET_PROFILE,
        gate_attestation_key=AUDITOR_KEY,
        inventory_attestation_key=REPORT_KEY,
    )


def _resign_receipt(path: Path, update: dict[str, Any]) -> None:
    receipt = json.loads(path.read_text())
    receipt.pop("attestation")
    receipt.update(update)
    path.write_text(
        json.dumps(
            attach_attestation(receipt, AUDITOR_KEY, purpose=RELEASE_GATE_PURPOSE)
        )
        + "\n"
    )


def test_rejects_world_identity_reused_by_newton_v5_style_release(
    tmp_path: Path,
) -> None:
    newton = _write_release(
        tmp_path,
        "newton-v5",
        [_row("lab000001-fenlightbench-18:focal", "a")],
    )
    paper = _write_release(
        tmp_path,
        "paper-v10",
        [_row("lab000001-fenlightbench-18:focal", "b")],
        receipt_name="release_gate_pass.json",
    )

    with pytest.raises(ReleaseUnionError, match="world_id.*multiple releases"):
        _audit([newton, paper], tmp_path)


def test_rejects_content_hash_reused_across_distinct_worlds(tmp_path: Path) -> None:
    first = _write_release(
        tmp_path,
        "first",
        [_row("world-a", "same-content")],
    )
    second = _write_release(
        tmp_path,
        "second",
        [_row("world-b", "same-content")],
    )

    with pytest.raises(ReleaseUnionError, match="content_hash.*multiple releases"):
        _audit([first, second], tmp_path)


def test_rejects_forged_content_hash_even_when_release_receipt_is_resigned(
    tmp_path: Path,
) -> None:
    row = _row("world-a", "content-a")
    row["content_hash"] = "0" * 24
    release = _write_release(tmp_path, "forged-content", [row])

    with pytest.raises(ReleaseUnionError, match="canonical content_hash"):
        _audit([release], tmp_path)


@pytest.mark.parametrize(
    ("identity_field", "identity"),
    [
        ("workflow_ids", ["workflow:real:shared"]),
        ("real_source_workflow_ids", ["source:real:shared"]),
        (
            "episode_replay_bundle",
            {
                "schema_version": "longworld.episode-replay-bundle.v1",
                "sha256": "a" * 64,
                "composition": "chronological_causal_union",
            },
        ),
        (
            "source_workflow_bundle",
            {
                "schema_version": "longworld.source-workflow-bundle.v1",
                "adapter_revision": "sourceworkflow@2",
                "sha256": "c" * 64,
                "binding_digest": "d" * 64,
            },
        ),
    ],
)
def test_rejects_real_source_identity_relabelled_to_another_world(
    tmp_path: Path, identity_field: str, identity: Any
) -> None:
    first_row = _row("world-a", "content-a")
    second_row = _row("world-b", "content-b")
    if identity_field == "episode_replay_bundle":
        for row in (first_row, second_row):
            row.pop("source_workflow_bundle")
            row["promotion"].pop("source_workflow_bundle")
    first_row[identity_field] = identity
    second_row[identity_field] = identity
    if identity_field in {"episode_replay_bundle", "source_workflow_bundle"}:
        first_row["promotion"][identity_field] = identity
        second_row["promotion"][identity_field] = identity
    first = _write_release(tmp_path, "first", [first_row])
    second = _write_release(tmp_path, "second", [second_row])

    with pytest.raises(ReleaseUnionError, match="source identity.*multiple world_ids"):
        _audit([first, second], tmp_path)


@pytest.mark.parametrize("tamper", ["unsigned", "wrong-purpose"])
def test_rejects_invalid_gate_receipt_attestation(tmp_path: Path, tamper: str) -> None:
    release = _write_release(
        tmp_path,
        "invalid-receipt",
        [_row("world-a", "content-a")],
    )
    receipt_path = release / "release_gate_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    if tamper == "unsigned":
        receipt.pop("attestation")
    else:
        receipt["attestation"]["purpose"] = "quality_report"
    receipt_path.write_text(json.dumps(receipt) + "\n")

    with pytest.raises(ReleaseUnionError, match="attestation"):
        _audit([release], tmp_path)


def test_rejects_duplicate_json_members_before_signature_verification(
    tmp_path: Path,
) -> None:
    release = _write_release(
        tmp_path,
        "duplicate-receipt-member",
        [_row("world-a", "content-a")],
    )
    receipt_path = release / "release_gate_receipt.json"
    raw = receipt_path.read_text()
    receipt_path.write_text(raw.replace('"ok": true', '"ok": true, "ok": true'))

    with pytest.raises(ReleaseUnionError, match="duplicate JSON field: ok"):
        _audit([release], tmp_path)


def test_rejects_resigned_release_profile_digest_mismatch(tmp_path: Path) -> None:
    release = _write_release(
        tmp_path,
        "profile-mismatch",
        [_row("world-a", "content-a")],
    )
    _resign_receipt(
        release / "release_gate_receipt.json",
        {"release_profile_sha256": "0" * 64},
    )

    with pytest.raises(ReleaseUnionError, match="release profile"):
        _audit([release], tmp_path)


@pytest.mark.parametrize("artifact", ["receipt", "quality_report"])
def test_rejects_tokenizer_identity_mismatch_in_signed_release_envelope(
    tmp_path: Path, artifact: str
) -> None:
    release = _write_release(
        tmp_path,
        "tokenizer-envelope-mismatch",
        [_row("world-a", "content-a")],
    )
    receipt_path = release / "release_gate_receipt.json"
    if artifact == "receipt":
        _resign_receipt(receipt_path, {"tokenizer_model_id": "unapproved/model"})
    else:
        report_path = release / "quality_report.json"
        report = json.loads(report_path.read_text())
        report["tokenizer_model_id"] = "unapproved/model"
        report_path.write_text(json.dumps(report) + "\n")
        receipt = json.loads(receipt_path.read_text())
        source_hashes = dict(receipt["source_file_sha256"])
        source_hashes["quality_report.json"] = _sha256(report_path)
        _resign_receipt(
            receipt_path,
            {
                "quality_report_sha256": source_hashes["quality_report.json"],
                "source_file_sha256": source_hashes,
            },
        )

    with pytest.raises(ReleaseUnionError, match="release profile|row-set binding"):
        _audit([release], tmp_path)


def test_rejects_source_file_hash_tamper(tmp_path: Path) -> None:
    release = _write_release(
        tmp_path,
        "tampered",
        [_row("world-a", "content-a")],
    )
    with (release / "train.jsonl").open("a") as handle:
        handle.write(json.dumps(_row("world-b", "content-b")) + "\n")

    with pytest.raises(ReleaseUnionError, match="source_file_sha256 mismatch"):
        _audit([release], tmp_path)


def test_rejects_release_outside_inventory_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    release = _write_release(
        tmp_path / "outside",
        "release",
        [_row("world-a", "content-a")],
    )

    with pytest.raises(ReleaseUnionError, match="outside inventory root"):
        _audit([release], root)


def test_rejects_symlink_in_release_path(tmp_path: Path) -> None:
    real = _write_release(
        tmp_path,
        "real",
        [_row("world-a", "content-a")],
    )
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ReleaseUnionError, match="symlink"):
        _audit([linked], tmp_path)


def test_rejects_inventory_root_with_a_symlinked_ancestor(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    root = real_parent / "inventory"
    release = _write_release(root, "release", [_row("world-a", "content-a")])
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ReleaseUnionError, match="root.*symlink"):
        _audit(
            [linked_parent / "inventory" / release.name],
            linked_parent / "inventory",
        )


def test_rejects_symlinked_bound_release_file(tmp_path: Path) -> None:
    root = tmp_path / "inventory"
    release = _write_release(root, "release", [_row("world-a", "content-a")])
    train_path = release / "train.jsonl"
    outside = tmp_path / "outside-train.jsonl"
    outside.write_bytes(train_path.read_bytes())
    train_path.unlink()
    train_path.symlink_to(outside)

    with pytest.raises(ReleaseUnionError, match="symlink"):
        _audit([release], root)


def test_bound_release_files_are_opened_once_without_following_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _write_release(
        tmp_path,
        "release",
        [_row("world-a", "content-a")],
    )
    bound_names = {
        "release_gate_receipt.json",
        "quality_report.json",
        "train.jsonl",
        "eval.jsonl",
    }
    open_counts = {name: 0 for name in bound_names}
    real_open = os.open

    def counting_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        name = os.fspath(path)
        if name in bound_names:
            open_counts[name] += 1
            assert flags & os.O_NOFOLLOW
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(audit_module.os, "open", counting_open)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: pytest.fail(
            "bound files must not be read through Path.read_bytes"
        ),
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _path, *args, **kwargs: pytest.fail(
            "bound files must not be read through Path.read_text"
        ),
    )

    _audit([release], tmp_path)

    assert open_counts == {name: 1 for name in bound_names}


@pytest.mark.parametrize("missing_field", ["world_id", "content_hash"])
def test_rejects_missing_cross_release_identity(
    tmp_path: Path, missing_field: str
) -> None:
    row = _row("world-a", "content-a")
    del row[missing_field]
    release = _write_release(tmp_path, "missing", [row])

    with pytest.raises(ReleaseUnionError, match=missing_field):
        _audit([release], tmp_path)


def test_rejects_row_without_real_source_identity(tmp_path: Path) -> None:
    row = _row("world-a", "content-a")
    del row["real_source_workflow_ids"]
    release = _write_release(tmp_path, "missing-source", [row])

    with pytest.raises(ReleaseUnionError, match="missing real source identity"):
        _audit([release], tmp_path)


def test_generic_workflow_identity_does_not_satisfy_real_source_gate(
    tmp_path: Path,
) -> None:
    row = _row("world-a", "content-a")
    row.pop("real_source_workflow_ids")
    row.pop("source_workflow_bundle")
    row["promotion"].pop("source_workflow_bundle")
    release = _write_release(tmp_path, "generic-only", [row])

    with pytest.raises(ReleaseUnionError, match="missing real source identity"):
        _audit([release], tmp_path)


def test_rejects_reported_token_count_outside_declared_bucket(tmp_path: Path) -> None:
    release = _write_release(
        tmp_path,
        "wrong-band",
        [_row("world-a", "content-a", bucket="16k", tokens=15_999)],
    )

    with pytest.raises(ReleaseUnionError, match="exact_16k_out_of_range"):
        _audit([release], tmp_path)


def test_rejects_missing_exact_tokenizer_metadata(tmp_path: Path) -> None:
    row = _row("world-a", "content-a")
    del row["tokenizer_asset_manifest_sha256"]
    release = _write_release(tmp_path, "missing-tokenizer", [row])

    with pytest.raises(ReleaseUnionError, match="exact tokenizer metadata"):
        _audit([release], tmp_path)


def test_union_inventory_is_deterministic_relative_signed_and_summarized(
    tmp_path: Path,
) -> None:
    first_rows = [
        _row("world-a", "content-a", bucket="16k", tokens=16_001),
        _row("world-a", "content-b", bucket="32k", tokens=32_002),
    ]
    second_rows = [
        _row(
            "world-b",
            "content-c",
            bucket="64k",
            tokens=64_003,
            domain="company",
        )
    ]
    first = _write_release(tmp_path, "z-release", first_rows)
    second = _write_release(
        tmp_path,
        "a-release",
        second_rows,
        receipt_name="release_gate_pass.json",
    )

    forward = _audit([first, second], tmp_path)
    reverse = _audit([second, first], tmp_path)

    assert forward == reverse
    assert forward["schema_version"] == "longworld-local-release-inventory-v2"
    assert forward["inventory_integrity_ok"] is True
    assert forward["target_gate_evaluated"] is False
    assert forward["target_gate_passed"] is False
    assert "ok" not in forward
    assert "qualified" not in json.dumps(forward, sort_keys=True).lower()
    assert forward["scope"] == "local_release_identity_and_byte_inventory"
    assert forward["trust_mode"] == "local_engineering"
    assert forward["production_eligible"] is False
    assert forward["target_release_profile_id"] == TARGET_PROFILE
    assert forward["target_release_profile_sha256"] == release_profile_sha256(
        TARGET_PROFILE
    )
    assert forward["n_releases"] == 2
    assert forward["n_rows"] == 3
    assert forward["n_worlds"] == 2
    assert forward["n_content_hashes"] == 3
    assert forward["n_reported_context_tokens"] == 112_006
    assert "n_exact_context_tokens" not in forward
    assert forward["length_bucket_counts"] == {"16k": 1, "32k": 1, "64k": 1}
    assert forward["domain_row_counts"] == {"company": 1, "researchlab": 2}
    assert forward["domain_world_counts"] == {"company": 1, "researchlab": 1}
    assert forward["promoted_row_set_sha256"] == promoted_row_set_sha256(
        [*first_rows, *second_rows]
    )
    assert [entry["release"] for entry in forward["releases"]] == [
        "a-release",
        "z-release",
    ]
    assert all(
        not Path(entry["release"]).is_absolute()
        and not Path(entry["receipt"]["path"]).is_absolute()
        and all(not Path(item["path"]).is_absolute() for item in entry["files"])
        for entry in forward["releases"]
    )
    assert verify_local_release_inventory_attestation(forward, REPORT_KEY)
    assert forward["attestation"] == {
        "scheme": "hmac-sha256-v2",
        "purpose": LOCAL_RELEASE_INVENTORY_PURPOSE,
        "role": "report",
        "key_id": "probe-union-report-v1",
        "environment": "probe",
        "digest": forward["attestation"]["digest"],
    }
    assert LOCAL_RELEASE_INVENTORY_PURPOSE != RELEASE_INVENTORY_PURPOSE
    production_relabel = json.loads(json.dumps(forward))
    production_relabel["attestation"]["purpose"] = RELEASE_INVENTORY_PURPOSE
    assert not verify_local_release_inventory_attestation(
        production_relabel, REPORT_KEY
    )


def test_semantic_coverage_replays_key_backed_release_union(tmp_path: Path) -> None:
    release = _write_release(
        tmp_path,
        "release",
        [_row("world-a", "content-a", bucket="16k", tokens=16_001)],
    )
    inventory = _audit([release], tmp_path)
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory))

    report = audit_semantic_coverage(inventory_path, workspace_root=tmp_path)

    assert report["n_rows"] == 1
    assert report["n_worlds"] == 1
    assert report["n_exact_context_tokens"] == 16_001


def test_cli_emits_signed_relative_inventory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = _write_release(
        tmp_path,
        "release",
        [_row("world-a", "content-a")],
    )

    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "--target-release-profile",
                TARGET_PROFILE,
                str(release),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)

    assert summary["inventory_integrity_ok"] is True
    assert summary["target_gate_passed"] is False
    assert summary["releases"][0]["release"] == "release"
    assert verify_local_release_inventory_attestation(summary, REPORT_KEY)


def test_cli_writes_the_same_signed_inventory_to_an_output_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = _write_release(
        tmp_path,
        "release",
        [_row("world-a", "content-a")],
    )
    output = tmp_path / "reports" / "union.json"
    output.parent.mkdir()

    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "--target-release-profile",
                TARGET_PROFILE,
                "--output",
                str(output),
                str(release),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)

    assert json.loads(output.read_text()) == summary
    assert output.read_bytes().endswith(b"\n")


def test_cli_does_not_create_output_directories_through_a_symlink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "inventory"
    release = _write_release(root, "release", [_row("world-a", "content-a")])
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = root / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    output = linked / "created-outside" / "union.json"

    assert (
        main(
            [
                "--root",
                str(root),
                "--target-release-profile",
                TARGET_PROFILE,
                "--output",
                str(output),
                str(release),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert not (outside / "created-outside").exists()


def test_cli_rejects_symlinked_output_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = _write_release(
        tmp_path,
        "release",
        [_row("world-a", "content-a")],
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("unchanged\n")
    output = reports / "union.json"
    output.symlink_to(outside)

    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "--target-release-profile",
                TARGET_PROFILE,
                "--output",
                str(output),
                str(release),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert outside.read_text() == "unchanged\n"


def test_cli_rejects_published_placeholder_report_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _write_release(
        tmp_path,
        "release",
        [_row("world-a", "content-a")],
    )
    monkeypatch.setenv(
        "LONGWORLD_REPORT_ATTESTATION_KEY", "use-a-secret-of-at-least-32-bytes"
    )

    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "--target-release-profile",
                TARGET_PROFILE,
                str(release),
            ]
        )
        == 1
    )
    assert (
        "attestation identity is incomplete"
        in json.loads(capsys.readouterr().out)["error"]
    )
