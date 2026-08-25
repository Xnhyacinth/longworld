"""Executable scholarly fact contracts shared by export and replay."""

from __future__ import annotations

import re

_FUNDING_DISCLOSURE = re.compile(
    r"funded by (?P<funder>[^.]+?) and .+? by the .+? "
    r"\((?P<agency>[A-Z]{2,12})\), NSF (?P<grant>\d+)\."
)


def format_revision_funding_delta(value: str) -> str:
    """Return the exact answer atoms supported by the funding-delta operator."""
    match = _FUNDING_DISCLOSURE.search(value)
    if match is None:
        return ""
    return (
        f"{match.group('funder')} | {match.group('agency')} | "
        f"NSF {match.group('grant')}"
    )
