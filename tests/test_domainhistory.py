from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from itertools import pairwise

import pytest

import scripts.materialize_domain_histories as materializer
from longworld.core.domainhistory import (
    HistoryBand,
    audit_cumulative_history,
    audit_kev_catalog_history_candidate,
    build_kev_catalog_history_candidates,
    replay_kev_catalog_history,
    verify_bound_json_retrieval,
)
from longworld.core.provenance import ProvenanceError


def _catalog(n: int = 20) -> dict:
    vulnerabilities = []
    for index in range(n):
        year = 2021 + index // 7
        day = index % 7 + 1
        vulnerabilities.append(
            {
                "cveID": f"CVE-{year}-{'0995' if index == 0 else 1000 + index}",
                "vendorProject": f"Vendor {index}",
                "product": f"Product {index}",
                "vulnerabilityName": f"Issue {index}",
                "dateAdded": f"{year}-01-{day:02d}",
                "shortDescription": "Observed exploited vulnerability "
                + ("detail " * 8),
                "requiredAction": f"Apply vendor remediation {index} immediately.",
                "dueDate": f"{year}-02-{day:02d}",
                "knownRansomwareCampaignUse": (
                    "Known" if index % 3 == 0 else "Unknown"
                ),
                "notes": f"https://example.test/{index}",
                "cwes": [f"CWE-{100 + index}"],
            }
        )
    return {
        "title": "CISA Known Exploited Vulnerabilities Catalog",
        "catalogVersion": "test-1",
        "dateReleased": "2026.08.30",
        "count": n,
        "vulnerabilities": vulnerabilities,
    }


def _bands() -> tuple[HistoryBand, ...]:
    return (
        HistoryBand("16k", 1_500, 1_900),
        HistoryBand("32k", 3_000, 3_500),
        HistoryBand("64k", 5_000, 5_700),
    )


def _build() -> list[dict]:
    return build_kev_catalog_history_candidates(
        _catalog(),
        world_id="cyber-kev-history-test",
        source_binding={
            "source_url": "https://www.cisa.gov/kev.json",
            "observed_at": "2026-08-30T00:00:00Z",
            "retrieval_sha256": "a" * 64,
            "signed_manifest_sha256": "b" * 64,
        },
        bands=_bands(),
        token_counter=len,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="c" * 40,
    )


def test_builds_nested_exact_bands_with_real_semantic_growth() -> None:
    rows = _build()

    assert [row["length_bucket"] for row in rows] == ["16k", "32k", "64k"]
    assert all(
        band.lower_tokens <= row["tokenizer_context_tokens"] <= band.upper_tokens
        for band, row in zip(_bands(), rows, strict=True)
    )
    assert rows[1]["context"].startswith(rows[0]["context"] + "\n")
    assert rows[2]["context"].startswith(rows[1]["context"] + "\n")
    for before, after in pairwise(rows):
        assert set(before["source_record_ids"]) < set(after["source_record_ids"])
        assert set(before["source_relation_ids"]) < set(after["source_relation_ids"])
        assert before["event_count"] < after["event_count"]
        assert (
            before["strict_support_event_count"] < after["strict_support_event_count"]
        )
        assert set(before["essential_evidence_ids"]) < set(
            after["essential_evidence_ids"]
        )
        assert before["graph"]["proof_depth"] < after["graph"]["proof_depth"]
        assert (
            before["semantic_tokens"]["internal"] < after["semantic_tokens"]["internal"]
        )
    assert all(all(audit_kev_catalog_history_candidate(row).values()) for row in rows)
    assert audit_cumulative_history(rows) == []
    for row in rows:
        entry_count = row["event_count"] - 1
        answer = json.loads(row["answer"])
        assert answer["date_then_cve_order_valid"] is True
        assert answer["same_day_tie_break"] == "cve_id_lexical"
        assert (
            sum(
                checkpoint["entry_count"]
                for checkpoint in answer["year_end_checkpoints"]
            )
            == answer["selected_entry_count"]
        )
        assert row["real_source_verified"] is False
        assert row["source_verified_at_materialization"] is True
        assert row["graph"]["proof_depth"] == entry_count
        assert len(row["authentic_source_relation_edges"]) == entry_count
        assert len(row["verified_derived_order_relation_edges"]) == entry_count - 1
        assert len(row["source_relation_ids"]) == entry_count * 2 - 1


def test_replay_reads_context_and_counterfactual_instead_of_declared_answer() -> None:
    row = _build()[0]
    assert replay_kev_catalog_history(row)["answer"] == row["answer"]
    assert (
        replay_kev_catalog_history(row, counterfactual=True)["answer"]
        == row["cf_answer"]
    )

    forged = deepcopy(row)
    forged["cf_answer"] = row["answer"]
    assert not audit_kev_catalog_history_candidate(forged)[
        "counterfactual_replay_sufficient"
    ]

    corrupted = deepcopy(row)
    corrupted["context"] = corrupted["context"].replace(
        "Apply vendor remediation 0 immediately.", "Ignore remediation 0.", 1
    )
    assert replay_kev_catalog_history(corrupted)["answer"] != row["answer"]
    assert not audit_kev_catalog_history_candidate(corrupted)[
        "strict_replay_sufficient"
    ]


def test_rejects_duplicate_records_and_non_growing_histories() -> None:
    catalog = _catalog()
    catalog["vulnerabilities"].append(deepcopy(catalog["vulnerabilities"][0]))
    catalog["count"] += 1
    with pytest.raises(ProvenanceError, match="duplicate CVE"):
        build_kev_catalog_history_candidates(
            catalog,
            world_id="duplicate",
            source_binding={
                "source_url": "https://www.cisa.gov/kev.json",
                "observed_at": "2026-08-30T00:00:00Z",
                "retrieval_sha256": "a" * 64,
                "signed_manifest_sha256": "b" * 64,
            },
            bands=_bands(),
            token_counter=len,
            tokenizer_model_id="Qwen/Qwen3.5-4B",
            tokenizer_revision="c" * 40,
        )

    rows = _build()
    rows[1]["source_relation_ids"] = rows[0]["source_relation_ids"]
    assert "source_relations_not_strictly_nested:16k->32k" in (
        audit_cumulative_history(rows)
    )


def test_verify_bound_json_retrieval_checks_receipt_digest(tmp_path) -> None:
    raw = json.dumps(_catalog(), sort_keys=True).encode()
    source_path = tmp_path / "kev.json"
    source_path.write_bytes(raw)
    retrieval = {
        "kind": "cisa_kev",
        "retrieval_file": "kev.json",
        "final_url": "https://www.cisa.gov/kev.json",
        "observed_at": "2026-08-30T00:00:00Z",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "status": 200,
    }
    assert verify_bound_json_retrieval(source_path, retrieval) == _catalog()

    retrieval["sha256"] = "0" * 64
    with pytest.raises(ProvenanceError, match="digest"):
        verify_bound_json_retrieval(source_path, retrieval)


def test_materializer_writes_current_deterministic_candidate_manifest(
    tmp_path, monkeypatch
) -> None:
    manifest_path = tmp_path / "signed.json"
    manifest_path.write_text("{}")
    catalog_path = tmp_path / "kev.json"
    catalog_path.write_text("{}")
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "out"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.domain-history-materialization.v1",
                "tokenizer": {
                    "model_id": "Qwen/Qwen3.5-4B",
                    "revision": "c" * 40,
                },
                "bands": [
                    {
                        "name": band.name,
                        "lower_tokens": band.lower_tokens,
                        "upper_tokens": band.upper_tokens,
                    }
                    for band in _bands()
                ],
                "cyber_kev_history": {
                    "world_id": "materialized-test",
                    "signed_manifest": str(manifest_path),
                    "catalog_response": str(catalog_path),
                },
                "capacity_checks": [],
            }
        )
    )

    class CharacterTokenizer:
        @staticmethod
        def encode(text: str, *, add_special_tokens: bool) -> range:
            assert add_special_tokens is False
            return range(len(text))

    retrieval = {
        "kind": "cisa_kev",
        "retrieval_file": "kev.json",
        "final_url": "https://www.cisa.gov/kev.json",
        "observed_at": "2026-08-30T00:00:00Z",
        "sha256": "a" * 64,
        "status": 200,
    }
    monkeypatch.setattr(
        materializer, "attestation_key_from_env", lambda purpose: b"k" * 32
    )
    monkeypatch.setattr(
        materializer, "_load_tokenizer", lambda model_id, revision: CharacterTokenizer()
    )
    monkeypatch.setattr(
        materializer,
        "load_cyber_workflow_manifest",
        lambda path, attestation_key: {"fetch_receipt": {"retrievals": [retrieval]}},
    )
    monkeypatch.setattr(
        materializer, "verify_bound_json_retrieval", lambda path, receipt: _catalog()
    )
    monkeypatch.setattr(
        materializer,
        "resolved_tokenizer_asset_manifest_sha256",
        lambda model_id, revision: "d" * 64,
    )

    report = materializer.materialize(config_path, output_dir)
    candidate_bytes = (output_dir / "candidates.jsonl").read_bytes()
    persisted = json.loads((output_dir / "MANIFEST.json").read_text())
    rows = [json.loads(line) for line in candidate_bytes.splitlines()]

    assert report == persisted
    assert persisted["attempts"] == 3
    assert persisted["capacity_check_attempts"] == 0
    assert persisted["candidate_sha256"] == hashlib.sha256(candidate_bytes).hexdigest()
    assert persisted["source_attestation_verified"] is True
    assert persisted["source_response_digest_verified"] is True
    assert persisted["exact_token_counts_recomputed"] is True
    assert all(row["real_source_verified"] is False for row in rows)
    assert all(row["source_verified_at_materialization"] is True for row in rows)
    for row, summary in zip(rows, persisted["rows"], strict=True):
        assert summary["authentic_source_relation_count"] == len(
            row["authentic_source_relation_edges"]
        )
        assert summary["verified_derived_order_relation_count"] == len(
            row["verified_derived_order_relation_edges"]
        )
