#!/usr/bin/env python3
"""Measure question-only codebook and declared-relation shortcuts, read-only.

This diagnostic never changes frozen products or their signed eligibility.
Predictions use only the public question; gold answers are read for scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from longworld.core import p57pipeline, standardsworkflow
from longworld.core.p57pipeline import (
    question_only_codebook_prediction as codebook_prediction,
)

REPLAYS = {
    "ietf.acme_issuance_succession.v1": "replay_ietf_acme_issuance_succession_task",
    "ietf.ssh_architecture_succession.v1": "replay_ietf_ssh_architecture_succession_task",
    "ietf.dnssec_succession.v1": "replay_ietf_dnssec_succession_task",
    "ietf.tls13_handshake_succession.v1": "replay_ietf_tls13_handshake_succession_task",
    "ietf.http_semantics_succession.v1": "replay_ietf_http_semantics_succession_task",
    "ietf.http2_succession.v1": "replay_ietf_http2_succession_task",
    "ietf.pkix_path_succession.v1": "replay_ietf_pkix_path_succession_task",
}


def audit_job(job: dict, root: Path) -> dict:
    candidates_path = root / job["projected_dir"] / "candidates.jsonl"
    parents_path = root / job["parents_dir"] / "parents.jsonl"
    result = {
        "job_id": job["job_id"],
        "input_sha256": {},
        "question_only": [],
        "relation_ablation": [],
    }
    for path in (candidates_path, parents_path):
        result["input_sha256"][str(path)] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    for row in map(json.loads, candidates_path.read_text().splitlines()):
        prediction = codebook_prediction(row["question"])
        answer = row["answer"]
        if isinstance(answer, str):
            answer = json.loads(answer)
        result["question_only"].append(
            {
                "query_id": row["query_id"],
                "view": row.get("view"),
                "length_bucket": row.get("length_bucket"),
                "applicable": prediction is not None,
                "exact_match": prediction == answer if prediction is not None else None,
                "prediction": prediction,
                "correct_fields": sum(prediction.get(k) == v for k, v in answer.items())
                if prediction is not None
                else None,
                "n_fields": len(answer),
            }
        )
    for parent in map(json.loads, parents_path.read_text().splitlines()):
        task = parent["ietf_requirement_task"]
        replay_name = REPLAYS.get(task["answer_program_id"])
        if replay_name is None:
            raise ValueError(f"Unsupported replay: {task['answer_program_id']}")
        replay = getattr(standardsworkflow, replay_name)
        factual = replay(task)
        if factual != task["answer"]:
            raise ValueError("Factual replay mismatch")
        relations = task["essential_relation_ids"]
        result["relation_ablation"].append(
            {
                "query_id": parent["query_id"],
                "declared_essential_relations": relations,
                "all_relations_removed_changes_answer": replay(task, relation_ids=[])
                != factual,
                "remove_one_changes_answer": {
                    removed: replay(
                        task, relation_ids=[r for r in relations if r != removed]
                    )
                    != factual
                    for removed in relations
                },
                "gold_quote_characters": sum(
                    len(item["evidence_quote"]) for item in task["evidence_items"]
                ),
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads(args.catalog.read_text())
    jobs = [
        audit_job(job, root)
        for job in catalog["jobs"]
        if job["job_id"].startswith("ietf-")
    ]
    rows = [row for job in jobs for row in job["question_only"] if row["applicable"]]
    report = {
        "schema_version": "longworld.p58-ietf-shortcut-diagnostic.v1",
        "scope": "catalog projected candidates; not a signed release or model evaluation",
        "catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "replay_code_sha256": hashlib.sha256(
            Path(standardsworkflow.__file__).read_bytes()
        ).hexdigest(),
        "question_only_check_revision": p57pipeline.QUESTION_ONLY_CHECK_REVISION,
        "question_only_check_sha256": hashlib.sha256(
            Path(p57pipeline.__file__).read_bytes()
        ).hexdigest(),
        "n_applicable_rows": len(rows),
        "question_only_exact_matches": sum(row["exact_match"] for row in rows),
        "question_only_correct_fields": sum(row["correct_fields"] for row in rows),
        "n_fields": sum(row["n_fields"] for row in rows),
        "limitations": [
            "Question-only baseline can fail on counterfactuals; this is not perfect task solvability.",
            "Gold quote characters are evidence size, not a blind retrieval success measurement.",
            "Rows and bands are correlated; counts are not independent tasks.",
            "No frozen product, training file, filter receipt, or eligibility was mutated.",
        ],
        "jobs": jobs,
    }
    if args.inventory is not None:
        inventory = json.loads(args.inventory.read_text())
        report["inventory_sha256"] = hashlib.sha256(
            args.inventory.read_bytes()
        ).hexdigest()
        frozen = []
        for product in inventory["products"]:
            path = Path(inventory["data_root"]) / product["product"] / "train.jsonl"
            product_rows = list(map(json.loads, path.read_text().splitlines()))
            selected = [
                row for row in product_rows if row.get("answer_program_id") in REPLAYS
            ]
            if not selected:
                continue
            scored = []
            for row in selected:
                prediction = codebook_prediction(row["question"])
                answer = row["answer"]
                if isinstance(answer, str):
                    answer = json.loads(answer)
                scored.append(
                    {
                        "query_id": row["query_id"],
                        "view": row["view"],
                        "applicable": prediction is not None,
                        "exact_match": prediction == answer,
                        "correct_fields": sum(
                            prediction.get(k) == v for k, v in answer.items()
                        )
                        if prediction is not None
                        else None,
                        "n_fields": len(answer),
                    }
                )
            frozen.append(
                {
                    "product": product["product"],
                    "train_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "rows": scored,
                }
            )
        report["frozen_products"] = frozen
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "jobs"}, indent=2))


if __name__ == "__main__":
    main()
