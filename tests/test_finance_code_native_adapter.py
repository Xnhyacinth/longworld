"""Contract checks for the two real-source native batch adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis import finance_code_native_adapter as adapter


def _write(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_finance_probe_checks_pins_and_split_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    monkeypatch.setenv("QJIU_ROOT", str(tmp_path))
    trust = tmp_path / "private" / "trust.json"
    _write(trust, {"local": True})
    source = "source/manifest.json"
    source_hash = _write(tmp_path / source, {"issuer": "a"})
    config = "configs/issuer.json"
    config_hash = _write(
        tmp_path / config,
        {"source_manifest": source, "split_group_id": "issuer-a", "split": "train"},
    )
    job = {
        "issuer": "a",
        "config": config,
        "config_sha256": config_hash,
        "source_manifest": source,
        "source_manifest_sha256": source_hash,
        "trust_file": "${QJIU_ROOT}/private/trust.json",
    }
    catalog = tmp_path / "catalog.json"
    _write(
        catalog,
        {"schema_version": "longworld.finance-taskbank-long-catalog.v1", "jobs": [job]},
    )
    assert adapter.probe_finance(catalog)["split_groups"] == {"train": 1}
    _write(tmp_path / source, {"issuer": "changed"})
    with pytest.raises(ValueError, match="pinned source changed"):
        adapter.probe_finance(catalog)


def test_finance_probe_allows_pinned_source_inventory_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    outside = tmp_path / "object-store" / "pinned.json"
    digest = _write(outside, {"issuer": "a"})
    link = tmp_path / "source.json"
    link.symlink_to(outside)
    assert adapter._pinned("source.json", digest) == outside.resolve()


def test_finance_probe_rejects_missing_trust_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("QJIU_ROOT", raising=False)
    with pytest.raises(ValueError, match="QJIU_ROOT"):
        adapter._trust_path("${QJIU_ROOT}/private/trust.json")


def test_codeforge_probe_checks_source_bundle_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    trust = tmp_path / "trust.json"
    _write(trust, {"local": True})
    bundle = "source/bundle.json"
    digest = _write(tmp_path / bundle, {"source": 1})
    config = "configs/code.json"
    _write(
        tmp_path / config,
        {
            "schema_version": "longworld.codeforge-taskbank-config.v1",
            "source_bundle": bundle,
            "source_bundle_sha256": digest,
            "source_group_id": "repo-a",
            "split": "eval",
        },
    )
    catalog = tmp_path / "catalog.json"
    _write(
        catalog,
        {
            "schema_version": "longworld.codeforge-taskbank-catalog.v1",
            "trust_file": str(trust),
            "jobs": [{"repository": "a", "config": config}],
        },
    )
    assert adapter.probe_codeforge(catalog)["split_groups"] == {"eval": 1}
    _write(tmp_path / bundle, {"source": 2})
    with pytest.raises(ValueError, match="pinned source changed"):
        adapter.probe_codeforge(catalog)
