import pytest

from scripts.audit_wiki_join_positions import token_span


def test_token_span_covers_character_evidence_without_special_offsets():
    offsets = [(0, 0), (0, 4), (4, 8), (8, 12)]
    assert token_span(offsets, 3, 9) == (1, 4)


def test_token_span_rejects_unmapped_or_truncated_evidence():
    offsets = [(0, 0), (0, 4), (4, 8)]
    with pytest.raises(ValueError, match="not covered"):
        token_span(offsets, 7, 10)
