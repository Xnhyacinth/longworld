"""The new eight-role program materializes one natural band without weakening old bands."""

import json
from pathlib import Path

from test_financehistory import _filings

import scripts.materialize_finance_histories as materializer
from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)


def test_single_natural_band_materializer_binds_source_sidecar(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(
        ROLE_KEY_ENVS["source"], "p60-source-test-material-long-enough-for-signing"
    )
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "p60-source-materializer-test")
    source = tmp_path / "source.json"
    source.write_text('{"source":"verified by test loader"}\n')
    manifest = {
        "source_family": "issuer_ir_rendered_xbrl",
        "authorization": {"record_id": "P60-MATERIALIZER-TEST"},
        "issuer": {"name": "Example Issuer", "cik": "0000000001"},
    }
    monkeypatch.setattr(
        materializer, "load_issuer_ir_filing_manifest_bytes", lambda raw: manifest
    )
    monkeypatch.setattr(materializer, "extract_financial_filings", lambda _: _filings())

    class Tokenizer:
        def encode(self, text, *, add_special_tokens):
            assert not add_special_tokens
            return list(text)

    monkeypatch.setattr(materializer, "_load_tokenizer", lambda *_: Tokenizer())
    monkeypatch.setattr(
        materializer, "resolved_tokenizer_asset_manifest_sha256", lambda *_: "d" * 64
    )
    config = {
        "schema_version": "longworld.finance-history-materialization.v1",
        "world_id": "p60-natural-band-test",
        "signed_issuer_manifest": str(source),
        "answer_program_id": "finance.cash_components_identity.v1",
        "tokenizer": {"model_id": "Qwen/Qwen3.5-4B", "revision": "b" * 40},
        "bands": [{"name": "64k", "lower_tokens": 64000, "upper_tokens": 65536}],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    out = tmp_path / "output"
    result = materializer.materialize(path, out)
    assert result["accepted_candidates"] == 1
    assert result["cumulative_growth_errors"] == []
    assert result["rows"][0]["selected_filing_count"] == 4
    assert result["rows"][0]["length_bucket"] == "64k"
    assert (out / "TASK_REPLAY_SIDECAR.json").is_file()
