from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_git_history_cpt as audit_module
from audit_git_history_cpt import (
    _cross_release_references,
    _load_reference_closure,
    _validate_retained_count_contract,
)


def test_capacity_scan_accepts_natural_partial_retention() -> None:
    _validate_retained_count_contract(
        {"16k": 73, "256k": 11},
        target={"16k": 1000, "256k": 1000},
        require_full_target=False,
    )


def test_quota_mode_rejects_partial_retention() -> None:
    with pytest.raises(ValueError, match="row counts"):
        _validate_retained_count_contract(
            {"16k": 73}, target={"16k": 1000}, require_full_target=True
        )


def test_capacity_scan_never_accepts_rows_above_its_safety_cap() -> None:
    with pytest.raises(ValueError, match="row counts"):
        _validate_retained_count_contract(
            {"16k": 1001}, target={"16k": 1000}, require_full_target=False
        )


def test_cross_release_references_accept_legacy_and_list_contracts() -> None:
    receipt = {
        "path": "data/releases/reference",
        "release_manifest_sha256": "a" * 64,
    }

    assert _cross_release_references({"cross_release_reference": receipt}) == [receipt]
    assert _cross_release_references({"cross_release_references": [receipt]}) == [
        receipt
    ]

    with pytest.raises(ValueError, match="contract"):
        _cross_release_references(
            {
                "cross_release_reference": receipt,
                "cross_release_references": [receipt],
            }
        )


def test_reference_closure_verifies_pins_and_expands_transitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_by_path = {name: name.encode() for name in ("current", "prior", "oldest")}
    digest = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in raw_by_path.items()
    }
    releases = {
        "current": {
            "cross_release_references": [
                {"path": "prior", "release_manifest_sha256": digest["prior"]}
            ]
        },
        "prior": {
            "cross_release_reference": {
                "path": "oldest",
                "release_manifest_sha256": digest["oldest"],
            }
        },
        "oldest": {},
    }

    def fake_load(path: Path):
        name = str(path)
        return releases[name], raw_by_path[name], [], {}, {}, {}

    monkeypatch.setattr(audit_module, "_release_path", Path)
    monkeypatch.setattr(audit_module, "_load_release", fake_load)

    closure = _load_reference_closure(
        [{"path": "current", "release_manifest_sha256": digest["current"]}]
    )

    assert [item[0]["release_manifest_sha256"] for item in closure] == sorted(
        digest.values()
    )

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        _load_reference_closure(
            [{"path": "current", "release_manifest_sha256": "0" * 64}]
        )


def test_reference_closure_rejects_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_by_path = {name: name.encode() for name in ("left", "right")}
    digest = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in raw_by_path.items()
    }
    releases = {
        "left": {
            "cross_release_reference": {
                "path": "right",
                "release_manifest_sha256": digest["right"],
            }
        },
        "right": {
            "cross_release_reference": {
                "path": "left",
                "release_manifest_sha256": digest["left"],
            }
        },
    }

    def fake_load(path: Path):
        name = str(path)
        return releases[name], raw_by_path[name], [], {}, {}, {}

    monkeypatch.setattr(audit_module, "_release_path", Path)
    monkeypatch.setattr(audit_module, "_load_release", fake_load)

    with pytest.raises(ValueError, match="cycle"):
        _load_reference_closure(
            [{"path": "left", "release_manifest_sha256": digest["left"]}]
        )
