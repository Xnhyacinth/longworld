from __future__ import annotations

import hashlib
import json
from pathlib import Path

from reports import p54_eurlex_legislative_chain_preflight as p54

CONFIG = Path("configs/p54_eurlex_legislative_chain_preflight_v1.json")


def test_extract_semantic_units_ignores_non_document_markup() -> None:
    raw = b"""
    <html><head><script>secret()</script></head><body>
      <p>First authentic paragraph with enough words for a source unit.</p>
      <p>Second&nbsp;authentic paragraph.</p>
    </body></html>
    """

    document_text, units = p54._extract_semantic_units(
        raw, source_id="proposal", minimum_characters=20
    )

    assert "secret" not in document_text
    assert [unit["text"] for unit in units] == [
        "First authentic paragraph with enough words for a source unit.",
        "Second authentic paragraph.",
    ]


def test_near_deduplication_is_deterministic() -> None:
    units = [
        p54._unit("b", "proposal", "alpha beta gamma delta epsilon zeta"),
        p54._unit("a", "proposal", "alpha beta gamma delta epsilon zeta"),
        p54._unit("c", "act", "one two three four five six"),
    ]

    first, removed_first = p54._near_deduplicate(units, size=5, threshold=0.9)
    second, removed_second = p54._near_deduplicate(
        list(reversed(units)), size=5, threshold=0.9
    )

    assert [item["artifact_id"] for item in first] == [
        item["artifact_id"] for item in second
    ]
    assert removed_first == removed_second == 1


def test_fetch_requests_the_hash_stable_eurlex_representation(monkeypatch) -> None:
    raw = b"stable"
    observed: dict[str, str] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 7
            return raw

        def geturl(self) -> str:
            return "https://eur-lex.europa.eu/document"

    def fake_urlopen(request, **kwargs):
        del kwargs
        observed.update(request.headers)
        return Response()

    monkeypatch.setattr(p54.urllib.request, "urlopen", fake_urlopen)
    source = {
        "source_id": "document",
        "url": "https://eur-lex.europa.eu/document",
        "expected_bytes": len(raw),
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "max_bytes": len(raw),
    }

    fetched, _receipt = p54._fetch(source)

    assert fetched == raw
    assert observed["User-agent"] == "curl/8.10.1"
    assert observed["Accept-encoding"] == "identity"


def test_rights_check_uses_the_legal_decision_not_dynamic_site_chrome() -> None:
    raw = (
        b"Decision (2011/833/EU) on reuse. Documents are available for commercial "
        b"or non-commercial purposes, with the obligation for the reuser to "
        b"acknowledge the source of the documents."
    )

    result = p54._rights_check(raw)

    assert result["technical_rights_preflight"] == "PASS"
    assert result["legal_documents_reuse_basis"] == "Decision 2011/833/EU"


def test_oracle_requires_each_source_stage_and_correction_count() -> None:
    rules = {
        "proposal": {"source_id": "proposal", "pattern": r"2012/0266 \(COD\)"},
        "position": {
            "source_id": "position",
            "pattern": r"adopted at first reading on 2 April 2014",
        },
        "act": {"source_id": "act", "pattern": r"REGULATION \(EU\) 2017/745"},
        "corrigendum": {
            "source_id": "corrigendum",
            "pattern": r"Corrigendum to Regulation \(EU\) 2017/745",
        },
        "metadata_relation": {
            "source_id": "metadata",
            "pattern": r"52012PC0542.*52014AP0266.*32017R0745R\(01\)",
        },
    }
    artifacts = [
        {"source_id": "proposal", "text": "2012/0266 (COD)"},
        {
            "source_id": "position",
            "text": "adopted at first reading on 2 April 2014",
        },
        {"source_id": "act", "text": "REGULATION (EU) 2017/745"},
        {
            "source_id": "corrigendum",
            "text": "Corrigendum to Regulation (EU) 2017/745\n"
            + "\n".join(f"On page {index}" for index in range(14)),
        },
        {
            "source_id": "metadata",
            "text": "52012PC0542 52014AP0266 32017R0745R(01)",
        },
    ]

    result = p54._replay_oracle(artifacts, rules, expected_correction_count=14)

    assert result["status"] == "PASS"
    assert result["correction_count"] == 14
    for removed in range(len(artifacts)):
        missing = p54._replay_oracle(
            artifacts[:removed] + artifacts[removed + 1 :],
            rules,
            expected_correction_count=14,
        )
        assert missing["status"] == "UNKNOWN"


def test_shortcut_audit_precomputes_oracle_roles(monkeypatch) -> None:
    artifacts = [
        {
            "artifact_id": f"background:{index}",
            "source_id": "background",
            "text": "natural background",
            "token_count": 1,
        }
        for index in range(100)
    ]
    calls = 0
    original = p54._matches_rule

    def counted_match(artifact, rule):
        nonlocal calls
        calls += 1
        return original(artifact, rule)

    monkeypatch.setattr(p54, "_matches_rule", counted_match)

    result = p54._artifact_aligned_shortcut_audit(
        artifacts,
        {"missing": {"source_id": "essential", "pattern": "required"}},
        expected_correction_count=0,
        windows=[50],
    )

    assert result["all_insufficient"] is True
    assert calls <= len(artifacts)
    assert result["windows"]["50"]["checked_artifact_windows"] == len(artifacts)


def test_authentic_order_ignores_hashes_and_does_not_spread_essentials() -> None:
    artifacts = [
        {
            "artifact_id": "act:block:00002",
            "source_id": "act",
            "source_ordinal": 2,
            "text_sha256": "000",
        },
        {
            "artifact_id": "corrigendum:whole-document",
            "source_id": "corrigendum",
            "source_ordinal": 0,
            "text_sha256": "111",
        },
        {
            "artifact_id": "proposal:block:00001",
            "source_id": "proposal",
            "source_ordinal": 1,
            "text_sha256": "222",
        },
        {
            "artifact_id": "metadata:selected-chain",
            "source_id": "metadata",
            "source_ordinal": 0,
            "text_sha256": "333",
        },
        {
            "artifact_id": "position:block:00003",
            "source_id": "position",
            "source_ordinal": 3,
            "text_sha256": "444",
        },
        {
            "artifact_id": "act:block:00001",
            "source_id": "act",
            "source_ordinal": 1,
            "text_sha256": "555",
        },
    ]
    source_order = ["metadata", "proposal", "position", "act", "corrigendum"]
    expected = [
        "metadata:selected-chain",
        "proposal:block:00001",
        "position:block:00003",
        "act:block:00001",
        "act:block:00002",
        "corrigendum:whole-document",
    ]

    first = p54._order_by_authentic_chain(artifacts, source_order=source_order)
    for index, artifact in enumerate(artifacts):
        artifact["text_sha256"] = f"{999 - index:03d}"
    second = p54._order_by_authentic_chain(artifacts, source_order=source_order)

    assert [item["artifact_id"] for item in first] == expected
    assert [item["artifact_id"] for item in second] == expected
    essential_ids = {
        "metadata:selected-chain",
        "proposal:block:00001",
        "corrigendum:whole-document",
    }
    essential_positions = [
        index
        for index, artifact_id in enumerate(expected)
        if artifact_id in essential_ids
    ]
    assert essential_positions == [0, 1, 5]


def test_config_declares_dossier_first_authentic_source_order() -> None:
    config = json.loads(CONFIG.read_text())

    assert config["shortcut_preflight"]["authentic_source_order"] == [
        "metadata",
        "proposal",
        "position",
        "act",
        "corrigendum",
    ]


def test_exact_pack_reports_deficit_without_padding_or_clones() -> None:
    essential = [{"artifact_id": "essential", "token_count": 10, "text_sha256": "e"}]
    background = [{"artifact_id": "background", "token_count": 5, "text_sha256": "b"}]

    result = p54._pack_exact_band(
        essential,
        background,
        lower=20,
        upper=24,
        target_margin=1,
    )

    assert result["feasible"] is False
    assert result["deficit_tokens"] == 5
    assert result["padding_tokens"] == 0
    assert result["cloned_artifacts"] == 0
    assert result["split_or_truncated_artifacts"] == 0
