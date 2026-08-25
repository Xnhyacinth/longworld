from datetime import date

import pytest

from longworld.core.discourse import expand_discourse, stamp_register
from longworld.core.render import Artifact


@pytest.mark.parametrize(
    ("doc_type", "event_type"),
    [
        ("log", "ci_fail"),
        ("report", "release_note"),
        ("email", "ratify_latent"),
        ("meeting_notes", "review"),
    ],
)
def test_discourse_does_not_coach_the_solver(doc_type: str, event_type: str) -> None:
    artifact = Artifact(
        artifact_id="w:focal.a",
        doc_type=doc_type,
        time=date(2026, 1, 2),
        project="Project A",
        prefix="focal",
        reveals_events=["e1"],
        text="Original workplace record.",
        facts=[],
        slots={"event_type": event_type, "author_role": "engineer"},
    )
    [expanded] = expand_discourse(stamp_register([artifact], "audit_trail"))
    lowered = expanded.text.lower()
    for forbidden in (
        "reconstruct",
        "do not copy",
        "not a controlling write",
        "not a substitute",
        "must not be used",
        "later readers should",
    ):
        assert forbidden not in lowered
    assert "2026-01-02" in expanded.text
    assert "Project A" in expanded.text
