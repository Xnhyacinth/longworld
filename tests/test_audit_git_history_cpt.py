from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audit_git_history_cpt import _validate_retained_count_contract


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
