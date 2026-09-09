"""Independent local training handoff tests; no model or GPU execution."""
from __future__ import annotations

import inspect
import os

import pytest

from longworld.core import taskbank_training as tt
from longworld.core.attestation import attestation_environment_names
from tests.test_taskbank_training import bank as bank_fixture


@pytest.fixture
def training_bank(tmp_path, monkeypatch):
    return inspect.unwrap(bank_fixture)(tmp_path, monkeypatch)


def test_data_changing_recipe_edit_cannot_pass_existing_manifest(training_bank):
    root, catalog, batch, recipe = training_bank
    tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)
    # LLaMA-Factory applies max_samples before alignment/training. The prepared
    # manifest must not keep certifying the full row set after this change.
    recipe.write_text(recipe.read_text() + "max_samples: 1\n")
    with pytest.raises(ValueError, match="recipe|protected|data|changed"):
        tt.validate_training_inputs(root / "prepared/TASKBANK_TRAINING_MANIFEST.json")


def test_all_producer_environment_names_absent_during_tokenization(
    training_bank, monkeypatch
):
    root, catalog, batch, recipe = training_bank
    # Preserve the fixture's valid report signer; add other producer credentials.
    monkeypatch.setenv("LONGWORLD_SOURCE_ATTESTATION_KEY", "s" * 64)
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", "a" * 64)
    observed = []

    def tokenizer(*args):
        assert not any(name in os.environ for name in attestation_environment_names())
        observed.append("tokenizer")
        return object()

    def count_messages(*args):
        assert not any(name in os.environ for name in attestation_environment_names())
        observed.append("messages")
        return 100

    monkeypatch.setattr(tt, "_load_tokenizer", tokenizer)
    monkeypatch.setattr(tt, "_count_messages", count_messages)
    manifest = tt.prepare_training_inputs(catalog, batch, root / "prepared", recipe)
    assert manifest["counts"] == {"train": 1, "eval": 1}
    assert observed == ["tokenizer", "messages", "messages"]
