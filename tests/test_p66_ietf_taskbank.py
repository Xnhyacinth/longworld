import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.p66_ietf_taskbank import (
    admission_reason,
    exact_range,
    minimum_positive_evidence_cover,
)
from scripts import materialize_p66_ietf_taskbank as materialize


def test_exact_ranges_are_numeric_not_capacity_bins():
    assert exact_range(65_000) == "64k"
    assert exact_range(70_000) is None
    assert exact_range(260_000) == "256k"


def test_positive_evidence_cover_excludes_unknown_branch():
    task = {
        "evidence_items": [
            {"evidence_id": "removed", "evidence_quote": "missing quote"},
            {"evidence_id": "kept", "evidence_quote": "visible quote"},
        ]
    }
    result = minimum_positive_evidence_cover(
        "x visible quote y", task, {"removed": "UNKNOWN", "kept": "YES"}
    )
    assert result["missing_evidence_ids"] == []
    assert result["char_cover"] == len("visible quote")


def test_admission_is_fail_closed():
    assert (
        admission_reason(
            view="full",
            context_tokens=65_000,
            question_only_em=True,
            positive_cover_tokens=20_000,
        )
        == "question_only_codebook_exact"
    )
    assert (
        admission_reason(
            view="cf",
            context_tokens=32_000,
            question_only_em=False,
            positive_cover_tokens=20_000,
        )
        == "below_long_context_floor"
    )
    assert (
        admission_reason(
            view="cf",
            context_tokens=65_000,
            question_only_em=False,
            positive_cover_tokens=10_000,
        )
        == "positive_evidence_fits_16k_window"
    )
    assert (
        admission_reason(
            view="cf",
            context_tokens=65_000,
            question_only_em=False,
            positive_cover_tokens=20_000,
        )
        == "accepted_local_long_candidate"
    )


def test_validation_rejects_changed_output_tree(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    output.mkdir()
    payload = output / "train.jsonl"
    payload.write_text("original\n")
    receipt = {
        "schema_version": materialize.RECEIPT,
        "config_sha256": "config-digest",
        "files": {"train.jsonl": hashlib.sha256(payload.read_bytes()).hexdigest()},
    }
    (output / "BUILD_RECEIPT.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(materialize, "build", lambda *args: receipt)

    payload.write_text("changed\n")
    with pytest.raises(ValueError, match="output tree"):
        materialize.validate(tmp_path / "config.json", output, 1)
