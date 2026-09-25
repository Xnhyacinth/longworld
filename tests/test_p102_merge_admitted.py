"""P102 union requires the same global title/URL history on every gate."""

import pytest

from scripts.p102_merge_admitted import _history_contract, _require_shared_history


def test_gate_history_contract_detects_different_prior_registry() -> None:
    first = {
        "prior_router": {"path": "router.json", "sha256": "a" * 64},
        "prior_pools": [{"path": "pool.json", "sha256": "b" * 64}],
    }
    second = {
        **first,
        "prior_pools": [{"path": "pool.json", "sha256": "c" * 64}],
    }
    with pytest.raises(ValueError, match="different prior title/URL registries"):
        _require_shared_history(_history_contract(first), second)
    with pytest.raises(ValueError, match="pinned prior history"):
        _history_contract({"prior_router": first["prior_router"]})
