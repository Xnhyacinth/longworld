from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from longworld.core.filingworkflow import (
    parse_sec_filing_components,
    validate_sec_filing_component,
)
from longworld.core.provenance import ProvenanceError

SOURCE_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "sec_p5_apple_smoke"
)


@pytest.fixture(scope="module")
def local_submission() -> tuple[str, str]:
    manifest = json.loads(
        (SOURCE_DIRECTORY / "sec_filing_manifest.signed.json").read_text()
    )
    filing = manifest["filings"][0]
    source = (SOURCE_DIRECTORY / filing["source_file"]).read_text()
    return source, filing["source_sha256"]


def test_parses_required_sec_components_with_non_overlapping_source_ranges(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission

    components = parse_sec_filing_components(source, parent_source_sha256)

    assert [(item.component_type, item.sequence) for item in components] == [
        ("10-K", 1),
        ("EX-31.1", 5),
        ("EX-31.2", 6),
        ("EX-32.1", 7),
    ]
    assert [item.filename for item in components] == [
        "aapl-20250927.htm",
        "a10-kexhibit31109272025.htm",
        "a10-kexhibit31209272025.htm",
        "a10-kexhibit32109272025.htm",
    ]
    assert all(
        left.char_end <= right.char_start for left, right in pairwise(components)
    )
    assert len({item.provenance_id for item in components}) == len(components)
    for component in components:
        component_text = source[component.char_start : component.char_end]
        assert not hasattr(component, "text")
        assert component_text.startswith("<DOCUMENT>")
        assert "</DOCUMENT>" in component_text
        assert component.parent_source_sha256 == parent_source_sha256
        assert (
            component.component_sha256
            == hashlib.sha256(component_text.encode()).hexdigest()
        )
        assert component.provenance_id.startswith("derived-sha256:")
        validate_sec_filing_component(source, component)


def test_component_parser_rejects_wrong_parent_source_hash(
    local_submission: tuple[str, str],
) -> None:
    source, _ = local_submission

    with pytest.raises(ProvenanceError, match="parent source hash"):
        parse_sec_filing_components(source, "0" * 64)


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("char_start", lambda component: component.char_start + 1),
        ("char_end", lambda component: component.char_end - 1),
        ("component_sha256", lambda _component: "0" * 64),
        ("parent_source_sha256", lambda _component: "0" * 64),
        ("provenance_id", lambda _component: "derived-sha256:" + "0" * 64),
        ("component_type", lambda _component: "10-Q"),
        ("sequence", lambda component: component.sequence + 1),
        ("filename", lambda _component: "different.htm"),
    ],
)
def test_component_validation_fails_closed_on_provenance_tampering(
    local_submission: tuple[str, str],
    field: str,
    replacement: object,
) -> None:
    source, parent_source_sha256 = local_submission
    component = parse_sec_filing_components(source, parent_source_sha256)[0]
    tampered = replace(component, **{field: replacement(component)})  # type: ignore[operator]

    with pytest.raises(ProvenanceError):
        validate_sec_filing_component(source, tampered)
