from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_git_history_cpt as audit_module
from audit_git_history_cpt import (
    SourceRecordBinding,
    _cross_release_references,
    _load_reference_closure,
    _validate_retained_count_contract,
    _validate_source_record_binding,
    _validate_source_summary_binding,
    _validate_source_truncation_contract,
    _validate_source_window_binding,
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


def test_source_truncation_contract_replays_record_level_omissions() -> None:
    source = {
        "observed_commit_count": 2,
        "reject_reasons": {
            "commits_truncated": 1,
            "commit_chunks_truncated": 3,
        },
        "truncation_quality": {
            "revision": "git-observed-prefix-truncation-v1",
            "observed_commit_count": 2,
            "truncated_commit_count": 1,
            "omitted_chunk_count": 3,
            "truncated_commit_ratio_ppm": 500_000,
            "maximum_truncated_commit_ratio_ppm": 500_000,
            "tier": "within_configured_limit",
        },
        "truncated_commit_index": [
            {
                "source_event_id": "a" * 40,
                "commit_chunk_count_total": 4,
                "commit_chunk_count_emitted": 1,
            }
        ],
        "record_index": [
            {
                "source_event_id": "a" * 40,
                "chunk_index": 0,
                "commit_chunk_count_total": 4,
                "commit_chunk_count_emitted": 1,
                "commit_was_truncated": True,
            },
            {
                "source_event_id": "b" * 40,
                "chunk_index": 0,
                "commit_chunk_count_total": 1,
                "commit_chunk_count_emitted": 1,
                "commit_was_truncated": False,
            },
        ],
    }

    _validate_source_truncation_contract(source, 500_000)
    source["record_index"][0]["commit_chunk_count_total"] = 3
    with pytest.raises(ValueError, match="truncation"):
        _validate_source_truncation_contract(source, 500_000)

    source["record_index"][0]["commit_chunk_count_total"] = 4
    source["record_index"][0]["commit_was_truncated"] = False
    with pytest.raises(ValueError, match="truncation"):
        _validate_source_truncation_contract(source, 500_000)


def test_source_record_timestamp_is_bound_before_span_replay() -> None:
    expected = SourceRecordBinding(
        text_sha256="a" * 64,
        provenance_id="sha256:" + "b" * 64,
        source_event_id="c" * 40,
        occurred_at="2026-01-01T00:00:00Z",
        source_pointer="https://github.com/example/repo/commit/" + "c" * 40,
        predecessor_ids=(),
        ordinal=0,
        manifest_record_count=1,
    )
    record = {
        "record_id": "r0",
        "sha256": "a" * 64,
        "occurred_at": "2026-01-01T00:00:00Z",
        "source_pointer": "https://github.com/example/repo/commit/" + "c" * 40,
        "predecessor_ids": [],
    }

    _validate_source_record_binding(expected, record, "sha256:" + "b" * 64)
    record["occurred_at"] = "2020-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="source record binding"):
        _validate_source_record_binding(expected, record, "sha256:" + "b" * 64)


def test_source_window_binding_rejects_reordered_noncontiguous_or_relinked_rows() -> (
    None
):
    provenance = "sha256:" + "d" * 64
    source_records = [
        SourceRecordBinding(
            text_sha256=str(index) * 64,
            provenance_id=provenance,
            source_event_id=str(index) * 40,
            occurred_at=f"2026-01-0{index + 1}T00:00:00Z",
            source_pointer=f"https://github.com/example/repo/commit/{str(index) * 40}",
            predecessor_ids=(() if index == 0 else (f"r{index - 1}",)),
            ordinal=index,
            manifest_record_count=3,
        )
        for index in range(3)
    ]
    rows = [
        {
            "record_id": f"r{index}",
            "sha256": str(index) * 64,
            "occurred_at": f"2026-01-0{index + 1}T00:00:00Z",
            "source_pointer": f"https://github.com/example/repo/commit/{str(index) * 40}",
            "predecessor_ids": ([] if index == 0 else [f"r{index - 1}"]),
        }
        for index in range(3)
    ]

    _validate_source_window_binding(
        source_records, rows, provenance, source_start_index=0, source_end_index=3
    )
    with pytest.raises(ValueError, match="source window"):
        _validate_source_window_binding(
            [source_records[0], source_records[2]],
            [rows[0], rows[2]],
            provenance,
            source_start_index=0,
            source_end_index=3,
        )
    changed = [dict(row) for row in rows]
    changed[1]["predecessor_ids"] = []
    with pytest.raises(ValueError, match="source record binding"):
        _validate_source_window_binding(
            source_records,
            changed,
            provenance,
            source_start_index=0,
            source_end_index=3,
        )


def test_source_summary_is_bound_to_signed_manifest() -> None:
    source = {
        "repository_url": "https://github.com/example/repo",
        "observed_commit_count": 2,
        "accepted_record_count": 3,
        "reject_reasons": {"commits_truncated": 1},
        "truncation_quality": {"tier": "within_configured_limit"},
        "parser": {"skip_commits": 10},
    }
    summary = {
        "repository": "example/repo",
        "commit_count": 2,
        "record_count": 3,
        "reject_reasons": {"commits_truncated": 1},
        "truncation_quality": {"tier": "within_configured_limit"},
        "skip_commits": 10,
    }

    _validate_source_summary_binding(summary, source)
    summary["truncation_quality"] = {"tier": "complete"}
    with pytest.raises(ValueError, match="summary"):
        _validate_source_summary_binding(summary, source)
