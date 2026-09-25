"""Cross-artifact pin checks for the P105 reader compiler."""

import pytest

from scripts.p105_wiki_grid_batch import _plain_label, _validate_lineage


def test_cross_version_source_mix_is_rejected() -> None:
    config = {
        "raw_manifest": {"sha256": "raw-a"},
        "source_pool": {"sha256": "pool-a"},
    }
    ledger = {"raw_manifest_sha256": "raw-a"}
    raw = {"source_pool_sha256": "pool-a"}
    gate = {
        "gated_source_pool_sha256": "pool-a",
        "prior_router": {"sha256": "router"},
        "prior_pool": {"sha256": "prior"},
    }
    _validate_lineage(config, ledger, raw, gate)
    ledger["raw_manifest_sha256"] = "raw-b"
    with pytest.raises(ValueError, match="lineage"):
        _validate_lineage(config, ledger, raw, gate)
    ledger["raw_manifest_sha256"] = "raw-a"
    raw["source_pool_sha256"] = "pool-b"
    with pytest.raises(ValueError, match="lineage"):
        _validate_lineage(config, ledger, raw, gate)


def test_unresolved_markup_cannot_be_question_or_gold_label() -> None:
    assert _plain_label("Uppland")
    assert not _plain_label("coord|52|N")
    assert not _plain_label("{{lang|sv|Foo}}")
    assert not _plain_label("Cite web: source")
