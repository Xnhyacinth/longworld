from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_release_union import ReleaseUnionError, audit_release_union, main


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_release(
    root: Path,
    name: str,
    rows: list[dict[str, str]],
    *,
    receipt_name: str = "release_gate_receipt.json",
) -> Path:
    release = root / name
    release.mkdir()
    train = release / "train.jsonl"
    train.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (release / "eval.jsonl").write_text("")
    worlds = {row.get("world_id") for row in rows if row.get("world_id")}
    report = {
        "data_stage": "train_ready",
        "n_rows": len(rows),
        "n_worlds": len(worlds),
    }
    (release / "quality_report.json").write_text(json.dumps(report) + "\n")
    source_hashes = {
        filename: _sha256(release / filename)
        for filename in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }
    receipt = {
        "gate_revision": "longworld-quality-gate-v6",
        "ok": True,
        "errors": [],
        "n_rows": len(rows),
        "n_worlds": len(worlds),
        "source_file_sha256": source_hashes,
    }
    (release / receipt_name).write_text(json.dumps(receipt) + "\n")
    return release


def test_rejects_world_identity_reused_by_newton_v5_style_release(
    tmp_path: Path,
) -> None:
    newton = _write_release(
        tmp_path,
        "newton-v5",
        [{"world_id": "lab000001-fenlightbench-18:focal", "content_hash": "a"}],
    )
    paper = _write_release(
        tmp_path,
        "paper-v10",
        [{"world_id": "lab000001-fenlightbench-18:focal", "content_hash": "b"}],
        receipt_name="release_gate_pass.json",
    )

    with pytest.raises(ReleaseUnionError, match="world_id.*multiple releases"):
        audit_release_union([newton, paper])


def test_rejects_content_hash_reused_across_distinct_worlds(tmp_path: Path) -> None:
    first = _write_release(
        tmp_path,
        "first",
        [{"world_id": "world-a", "content_hash": "same-content"}],
    )
    second = _write_release(
        tmp_path,
        "second",
        [{"world_id": "world-b", "content_hash": "same-content"}],
    )

    with pytest.raises(ReleaseUnionError, match="content_hash.*multiple releases"):
        audit_release_union([first, second])


def test_rejects_source_file_hash_tamper(tmp_path: Path) -> None:
    release = _write_release(
        tmp_path,
        "tampered",
        [{"world_id": "world-a", "content_hash": "content-a"}],
    )
    with (release / "train.jsonl").open("a") as handle:
        handle.write('{"world_id":"world-b","content_hash":"content-b"}\n')

    with pytest.raises(ReleaseUnionError, match="source_file_sha256 mismatch"):
        audit_release_union([release])


@pytest.mark.parametrize("missing_field", ["world_id", "content_hash"])
def test_rejects_missing_cross_release_identity(
    tmp_path: Path, missing_field: str
) -> None:
    row = {"world_id": "world-a", "content_hash": "content-a"}
    del row[missing_field]
    release = _write_release(tmp_path, "missing", [row])

    with pytest.raises(ReleaseUnionError, match=missing_field):
        audit_release_union([release])


def test_three_release_union_outputs_json_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    releases = [
        _write_release(
            tmp_path,
            "paper",
            [{"world_id": "paper-world", "content_hash": "paper-content"}],
        ),
        _write_release(
            tmp_path,
            "wiki",
            [{"world_id": "wiki-world", "content_hash": "wiki-content"}],
            receipt_name="release_gate_pass.json",
        ),
        _write_release(
            tmp_path,
            "sec",
            [{"world_id": "sec-world", "content_hash": "sec-content"}],
        ),
    ]

    assert main([str(path) for path in releases]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["ok"] is True
    assert summary["scope"] == "cross_release_identity_only"
    assert summary["n_releases"] == 3
    assert summary["n_rows"] == 3
    assert summary["n_worlds"] == 3
    assert [item["release"] for item in summary["releases"]] == [
        str(path.resolve()) for path in releases
    ]
