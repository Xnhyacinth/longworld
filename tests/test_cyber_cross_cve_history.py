from __future__ import annotations

import hashlib
import json

import pytest

from longworld.core.domainhistory import (
    HistoryBand,
    audit_cross_cve_remediation_history_candidate,
    build_cross_cve_remediation_history_candidates,
    replay_cross_cve_remediation_history,
)
from longworld.core.provenance import ProvenanceError


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_record(cve_id: str, kind: str, payload: dict[str, str]) -> dict:
    text = _canonical(payload)
    record_id = f"nvd:{cve_id}" if kind == "nvd_cve" else f"cisa-kev:{cve_id}"
    return {
        "record_type": "cyber_source_record",
        "record_id": record_id,
        "kind": kind,
        "cve_id": cve_id,
        "source_url": f"https://example.test/{record_id}",
        "source_sha256": hashlib.sha256(record_id.encode()).hexdigest(),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
    }


def _documents(cve_id: str, date_added: str, due_date: str) -> list[dict]:
    nvd = _source_record(
        cve_id,
        "nvd_cve",
        {
            "id": cve_id,
            "published": "2020-01-01T00:00:00.000",
            "lastModified": "2026-01-01T00:00:00.000",
            "vulnStatus": "Analyzed",
        },
    )
    kev = _source_record(
        cve_id,
        "cisa_kev_entry",
        {
            "cveID": cve_id,
            "dateAdded": date_added,
            "dueDate": due_date,
            "knownRansomwareCampaignUse": "Known",
            "product": "Gateway",
            "requiredAction": f"Apply the vendor update for {cve_id}.",
            "vendorProject": "Example Vendor",
        },
    )
    relation = {
        "record_type": "cyber_source_relation",
        "relation_id": f"cyber:listed-in-kev:{cve_id}",
        "kind": "listed_in_kev",
        "source_record_id": kev["record_id"],
        "target_record_id": nvd["record_id"],
    }
    return [nvd, kev, relation]


def test_cross_cve_replay_aggregates_real_joins_and_requires_every_source_role() -> (
    None
):
    documents = [
        *_documents("CVE-2020-1472", "2021-11-03", "2022-05-03"),
        *_documents("CVE-2023-34362", "2023-06-02", "2023-06-23"),
    ]
    task = {
        "context": "\n".join(_canonical(value) for value in documents),
        "counterfactual_twin": {
            "record_id": "cisa-kev:CVE-2023-34362",
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_due_date",
            "parent_value": "2023-06-23",
            "value": "2099-12-31",
        },
    }

    replay = replay_cross_cve_remediation_history(task)
    answer = json.loads(replay["answer"])
    assert answer["selected_cve_count"] == 2
    assert [item["cve_id"] for item in answer["joined_records"]] == [
        "CVE-2020-1472",
        "CVE-2023-34362",
    ]
    assert answer["known_ransomware_count"] == 2
    assert len(replay["authentic_source_relation_edges"]) == 2
    assert replay["proof_depth"] == 2

    assert (
        replay_cross_cve_remediation_history(task, counterfactual=True)["answer"]
        != replay["answer"]
    )
    for evidence_id in [*replay["source_record_ids"], *replay["source_relation_ids"]]:
        selected = [
            value
            for value in [*replay["source_record_ids"], *replay["source_relation_ids"]]
            if value != evidence_id
        ]
        assert (
            replay_cross_cve_remediation_history(task, evidence_ids=selected)["answer"]
            == "unknown"
        )

    corrupted = json.loads(json.dumps(task))
    lines = corrupted["context"].splitlines()
    record = json.loads(lines[1])
    record["text"] = record["text"].replace("2022-05-03", "2022-05-04")
    lines[1] = _canonical(record)
    corrupted["context"] = "\n".join(lines)
    assert replay_cross_cve_remediation_history(corrupted)["answer"] == "unknown"


def test_cross_cve_builder_grows_source_state_and_exact_replayed_proof() -> None:
    documents = [
        _documents(
            f"CVE-2020-{1000 + index}",
            f"202{index}-01-01",
            f"202{index}-02-01",
        )
        for index in range(6)
    ]
    manifest = {
        "records": [
            {key: value for key, value in record.items() if key != "record_type"}
            for group in documents
            for record in group[:2]
        ],
        "relations": [
            {key: value for key, value in group[2].items() if key != "record_type"}
            for group in documents
        ],
    }
    rows = build_cross_cve_remediation_history_candidates(
        manifest,
        world_id="cross-cve-growth-test",
        source_binding={
            "source_manifest_sha256": "a" * 64,
            "fetch_inventory_sha256": "b" * 64,
            "authorization_record_id": "public-cross-cve-test",
            "observed_at": "2026-09-01T00:00:00Z",
        },
        bands=(
            HistoryBand("16k", 1, 1_000_000),
            HistoryBand("32k", 1, 1_000_000),
            HistoryBand("64k", 1, 1_000_000),
        ),
        token_counter=len,
        tokenizer_model_id="test/tokenizer",
        tokenizer_revision="c" * 40,
    )

    assert [row["length_bucket"] for row in rows] == ["16k", "32k", "64k"]
    assert [row["graph"]["proof_depth"] for row in rows] == [2, 3, 4]
    assert [row["event_count"] for row in rows] == [6, 9, 12]
    assert all(
        all(
            audit_cross_cve_remediation_history_candidate(
                row, token_counter=len
            ).values()
        )
        for row in rows
    )


def test_cross_cve_builder_rejects_source_records_without_a_real_join() -> None:
    groups = [
        _documents("CVE-2020-1000", "2020-01-01", "2020-02-01"),
        _documents("CVE-2020-1001", "2021-01-01", "2021-02-01"),
    ]
    orphan = _documents("CVE-2020-1002", "2022-01-01", "2022-02-01")[0]
    manifest = {
        "records": [
            {key: value for key, value in record.items() if key != "record_type"}
            for group in groups
            for record in group[:2]
        ]
        + [{key: value for key, value in orphan.items() if key != "record_type"}],
        "relations": [
            {key: value for key, value in group[2].items() if key != "record_type"}
            for group in groups
        ],
    }

    with pytest.raises(ProvenanceError, match="source join coverage"):
        build_cross_cve_remediation_history_candidates(
            manifest,
            world_id="cross-cve-orphan-test",
            source_binding={
                "source_manifest_sha256": "a" * 64,
                "fetch_inventory_sha256": "b" * 64,
                "authorization_record_id": "public-cross-cve-test",
                "observed_at": "2026-09-01T00:00:00Z",
            },
            bands=(HistoryBand("16k", 1, 1_000_000),),
            token_counter=len,
            tokenizer_model_id="test/tokenizer",
            tokenizer_revision="c" * 40,
        )
