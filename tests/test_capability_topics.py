import copy

import pytest

from scripts.fetch_capability_topics import validate_topics


def topic(identity="t1"):
    return {
        "id": identity,
        "domain": {"id": "d1", "display_name": "D"},
        "field": {"id": "f1", "display_name": "F"},
        "subfield": {"id": "s1", "display_name": "S"},
    }


def test_duplicate_and_conflicting_parent_rejected():
    one = topic()
    with pytest.raises(ValueError, match="duplicate"):
        validate_topics([one, one])
    two = copy.deepcopy(topic("t2"))
    two["domain"]["id"] = "d2"
    with pytest.raises(ValueError, match="conflicting"):
        validate_topics([one, two])
    with pytest.raises(ValueError, match="empty"):
        validate_topics([])
