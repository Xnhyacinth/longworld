"""P76 reader candidates use the same strict SFT mask validator as P75."""

from __future__ import annotations

import pytest

from scripts.verify_p75_real_reader import _token_limit


def test_reader_verifier_accepts_only_known_export_schemas() -> None:
    assert (
        _token_limit(
            {
                "schema": "longworld.p75-real-reader-export.v1",
                "config": {"max_full_tokens": 100000},
            }
        )
        == 100000
    )
    assert _token_limit({"schema": "longworld.p76-wiki-table-pairs.v5"}) == 262144
    assert _token_limit({"schema": "longworld.p76-wiki-table-scan.v2"}) == 262144
    with pytest.raises(ValueError, match="unsupported"):
        _token_limit({"schema": "arbitrary"})
    with pytest.raises(ValueError, match="invalid"):
        _token_limit(
            {
                "schema": "longworld.p75-real-reader-export.v1",
                "config": {"max_full_tokens": True},
            }
        )
