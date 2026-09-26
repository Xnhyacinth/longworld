"""Execute one pinned Wiki/finance source cohort with native reader audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import _answer_hash
from scripts.p112_report_audit import audit as report_audit
from scripts.p112_report_route import CODE_FILES, _issuer, _rows
from scripts.p112_report_route import SCHEMA as REPORT_SCHEMA
from scripts.p112_report_to_unified import convert as report_convert
from scripts.p113_legal_cell_plan import _dump, _pinned, _sha
from scripts.run_p76_source_batch import CONFIG_SCHEMA as WIKI_SCHEMA
from scripts.run_p76_source_batch import run as run_wiki

SCHEMA = "longworld.p113-legal-cell-dispatch.v2"


def _write(path: Path, value: dict) -> None:
    path.write_text(_dump(value), encoding="utf-8")


def _inputs(config: dict) -> tuple[dict, dict, dict, dict, dict]:
    plan_path, _ = _pinned_jsonl(ROOT, config["planned_jobs"])
    planned = [json.loads(line) for line in plan_path.read_text().splitlines()]
    wiki = [
        row for row in planned if row["semantic_id"] == config["wiki_plan_semantic_id"]
    ]
    finance = [
        row
        for row in planned
        if row["source_id"] == config["finance_source_id"]
        and row["source_kind"] == "annual_reports"
    ]
    if len(wiki) != 1 or len(finance) != 4:
        raise ValueError("chosen Wiki cell or finance source absent from frozen plan")
    wiki = wiki[0]
    if wiki["source_kind"] != "wiki_snapshot" or wiki["semantic_parameters"] != {
        "operation": "cell_lookup"
    }:
        raise ValueError("P113 dispatcher only wires Wiki table lookup")
    _, pool = _pinned(ROOT, config["wiki_source_pool"])
    entries = [
        entry for entry in pool["sources"] if entry["snapshot"] == wiki["source_pin"]
    ]
    if len(entries) != 1 or (
        entries[0]["domain"],
        entries[0]["topic"],
        entries[0]["split"],
    ) != (wiki["domain"], wiki["topic"], wiki["split"]):
        raise ValueError("Wiki planned labels changed in source pool")
    _, snapshot = _pinned(ROOT, wiki["source_pin"])
    if wiki["binding"] not in {doc["doc_id"] for doc in snapshot["documents"]}:
        raise ValueError("Wiki document binding changed")
    _, report_config = _pinned(ROOT, config["finance_route_config"])
    _, batch = _pinned(ROOT, report_config["source_batch_manifest"])
    _pinned(ROOT, report_config["source_final_audit"])
    catalog_path, _ = _pinned(
        ROOT,
        {
            "path": "configs/p64_finance_taskbank_catalog_v1.json",
            "sha256": batch["catalog_sha256"],
        },
    )
    catalog = json.loads(catalog_path.read_text())
    source_entry = [
        row for row in catalog["jobs"] if row["issuer"] == config["finance_source_id"]
    ]
    if (
        len(source_entry) != 1
        or source_entry[0]["source_manifest_sha256"]
        != finance[0]["source_pin"]["sha256"]
    ):
        raise ValueError("finance plan source disagrees with P96 catalog")
    native_job = [
        row for row in batch["jobs"] if row["issuer"] == config["finance_source_id"]
    ]
    if len(native_job) != 1 or native_job[0]["split"] != finance[0]["split"]:
        raise ValueError("finance cohort split changed")
    issuer_path = (
        ROOT
        / report_config["source_batch_dir"]
        / config["finance_source_id"]
        / "manifest.json"
    )
    if _sha(issuer_path) != native_job[0]["manifest_sha256"]:
        raise ValueError("P96 source cohort receipt drift")
    source = json.loads(issuer_path.read_text())
    if source["source_group"] != native_job[0]["source_group"]:
        raise ValueError("P96 source group drift")
    return wiki, pool, snapshot, report_config, {**native_job[0], "plan_cells": finance}


def _pinned_jsonl(root: Path, pin: dict) -> tuple[Path, None]:
    # Reuse the same path and hash policy for JSONL as the planner's JSON pins.
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("JSONL pin must be project-relative")
    path = (root / relative).resolve(strict=True)
    data_root = (root / "data").resolve()
    if not (
        path.is_relative_to(root.resolve())
        or (relative.parts[0] == "data" and path.is_relative_to(data_root))
    ):
        raise ValueError("JSONL pin escapes project")
    if _sha(path) != pin["sha256"]:
        raise ValueError("JSONL pin hash mismatch")
    return path, None


def _wiki_config(wiki: dict, pool: dict, snapshot: dict, maximum: int) -> dict:
    return {
        "schema": WIKI_SCHEMA,
        "prior_source_manifest": pool["prior_source_manifest"],
        "jobs": [
            {
                "name": "p113_bridges_lookup",
                "recipe": "wiki_table_lookup",
                "snapshot": {
                    **wiki["source_pin"],
                    "snapshot_id": snapshot["snapshot_id"],
                    "revisions": snapshot["source"]["revisions"],
                },
                "domain": wiki["domain"],
                "topic": wiki["topic"],
                "split": wiki["split"],
                "max_tasks": maximum,
                "length_policy": "native_whole_pages",
                "allow_task_rejects": True,
            }
        ],
    }


def _finance_batch(config: dict, job: dict, result: dict) -> dict:
    return {
        "schema": REPORT_SCHEMA + ".batch",
        "config_sha256": config["finance_route_config"]["sha256"],
        "code_sha256": {name: _sha(ROOT / name) for name in CODE_FILES},
        "source_batch_manifest_sha256": job["source_batch_manifest_sha256"],
        "source_final_audit_sha256": job["source_final_audit_sha256"],
        "jobs": [result],
        "source_groups": 1,
        "semantic_tasks": result["semantic_tasks"],
        "views": result["views"],
        "matrix_cells": result["matrix_cells"],
        "cell_statuses": result["cell_statuses"],
        "train_ready": False,
    }


def _prior(pin: dict) -> tuple[set[str], dict[tuple[str, str, str], set[str]]]:
    path, _ = _pinned_jsonl(ROOT, pin)
    samples, tasks = set(), {}
    for line in path.open(encoding="utf-8"):
        if line.strip():
            candidate = json.loads(line)["candidate"]
            samples.add(candidate["sample_id"])
            key = (
                candidate["source_kind"],
                candidate["source_group"],
                candidate["semantic_task_id"],
            )
            tasks.setdefault(key, set()).add(candidate["answer_sha256"])
    if any(len(hashes) != 1 for hashes in tasks.values()):
        raise ValueError("prior index contains conflicting answers for a task")
    return samples, tasks


def _lineage(
    output: Path, wiki: dict, finance: dict, prior_pin: dict
) -> tuple[list[dict], dict]:
    old_samples, old_tasks = _prior(prior_pin)
    wiki_job = next((output / "wiki_native/jobs").iterdir())
    wiki_index = _rows(wiki_job / "sample_index.jsonl")
    wiki_audits = _rows(wiki_job / "audit.jsonl")
    wiki_readers = _rows(wiki_job / f"{wiki['split']}.jsonl")
    if not (len(wiki_index) == len(wiki_audits) == len(wiki_readers)):
        raise ValueError("Wiki reader/index/audit row count changed")
    lineage = []
    for index, proof, reader in zip(wiki_index, wiki_audits, wiki_readers, strict=True):
        doc_ids = {
            span["source"]["doc_id"]
            for span in proof["source_to_reader_spans"]
            if span["kind"] == "fact"
        }
        if (
            doc_ids != {wiki["binding"]}
            or index["source_group"] != wiki["source_id"]
            or index["split"] != wiki["split"]
        ):
            raise ValueError(
                "Wiki native task does not match selected planned document"
            )
        answer = reader["messages"][1]["content"]
        answer_hash = _answer_hash(answer)
        quality_reason = (
            "collapsed_html_br_in_answer"
            if re.search(r"[a-z]br[A-Z]", answer)
            else None
        )
        semantic = ("real_wiki", index["source_group"], index["task_id"])
        if semantic in old_tasks and answer_hash not in old_tasks[semantic]:
            raise ValueError("Wiki prior task ID has a different answer hash")
        lineage.append(
            {
                "source_kind": "real_wiki",
                "plan_semantic_id": wiki["semantic_id"],
                "native_semantic_task_id": index["task_id"],
                "sample_id": index["example_id"],
                "answer_sha256": answer_hash,
                "split": index["split"],
                "full_chat_tokens": index["full_chat_tokens"],
                "supervised_tokens": index["supervised_tokens"],
                "quality_status": "rejected"
                if quality_reason
                else "native_audited_candidate",
                "quality_reason": quality_reason,
                "prior_sample_overlap": index["example_id"] in old_samples,
                "prior_task_overlap": semantic in old_tasks,
                "prior_answer_hash_match": semantic in old_tasks,
            }
        )
    finance_plan = {
        tuple(sorted(row["semantic_parameters"].items())): row
        for row in finance["plan_cells"]
    }
    native = output / "finance_native" / finance["issuer"]
    finance_index = _rows(native / "sample_index.jsonl")
    finance_audits = _rows(native / "audit.jsonl")
    if len(finance_index) != len(finance_audits):
        raise ValueError("finance index/audit row count changed")
    for index, proof in zip(finance_index, finance_audits, strict=True):
        params = tuple(
            sorted(
                (key, proof["program"][key])
                for key in ("selector_kind", "baseline_endpoint")
            )
        )
        planned = finance_plan.get(params)
        if (
            planned is None
            or index["split"] != planned["split"]
            or proof["mask_checked"] is not True
        ):
            raise ValueError(
                "finance native task does not map to planned source/selector cell"
            )
        semantic = ("real_finance", index["source_group"], index["semantic_task_id"])
        answer_hash = index["answer_sha256"]
        if semantic in old_tasks and answer_hash not in old_tasks[semantic]:
            raise ValueError("finance prior task ID has a different answer hash")
        lineage.append(
            {
                "source_kind": "real_finance",
                "plan_semantic_id": planned["semantic_id"],
                "native_semantic_task_id": index["semantic_task_id"],
                "sample_id": index["sample_id"],
                "answer_sha256": answer_hash,
                "split": index["split"],
                "full_chat_tokens": index["full_chat_tokens"],
                "supervised_tokens": index["supervised_tokens"],
                "quality_status": "native_audited_candidate",
                "quality_reason": None,
                "prior_sample_overlap": index["sample_id"] in old_samples,
                "prior_task_overlap": semantic in old_tasks,
                "prior_answer_hash_match": semantic in old_tasks,
            }
        )
    accepted = [
        row for row in lineage if row["quality_status"] == "native_audited_candidate"
    ]
    distinct = {
        (row["source_kind"], row["native_semantic_task_id"]) for row in accepted
    }
    net = {
        (row["source_kind"], row["native_semantic_task_id"])
        for row in accepted
        if not row["prior_task_overlap"]
    }
    counts = {
        "gross_native_views": len(lineage),
        "gross_distinct_native_tasks": len(
            {(row["source_kind"], row["native_semantic_task_id"]) for row in lineage}
        ),
        "post_quality_views": len(accepted),
        "post_quality_distinct_native_tasks": len(distinct),
        "net_distinct_tasks_vs_prior_index": len(net),
        "net_views_vs_prior_index": sum(
            not row["prior_task_overlap"] for row in accepted
        ),
        "prior_sample_overlap_views": sum(
            row["prior_sample_overlap"] for row in accepted
        ),
        "prior_task_and_answer_overlap_views": sum(
            row["prior_answer_hash_match"] for row in accepted
        ),
        "post_quality_rejected_views": len(lineage) - len(accepted),
        "source_kind_views": dict(
            sorted(Counter(row["source_kind"] for row in accepted).items())
        ),
        "train_ready": False,
    }
    return lineage, counts


def _replay_native(
    output: Path, finance: dict, report_config: dict, maximum: int
) -> None:
    """Recompile both source cohorts and compare their native outputs."""
    with tempfile.TemporaryDirectory(
        prefix="p113_legal_cell_replay_", dir=ROOT / "tmp"
    ) as temporary:
        replay = Path(temporary)
        run_wiki(
            output / "wiki_native_config.json",
            replay / "wiki_native",
            workers=1,
        )
        source_dir = ROOT / report_config["source_batch_dir"]
        (replay / "finance_native").mkdir()
        result = _issuer(
            {key: finance[key] for key in ("issuer", "source_group", "split")},
            str(source_dir),
            str(replay / "finance_native"),
            maximum,
        )
        if result["manifest_sha256"] != _sha(
            output / "finance_native" / finance["issuer"] / "manifest.json"
        ):
            raise ValueError("finance native receipt replay mismatch")
        for name in ("wiki_native", "finance_native"):
            for path in (replay / name).rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(replay)
                if relative == Path("wiki_native/inventory_inputs.json"):
                    continue  # This source path intentionally names the replay output.
                if path.read_bytes() != (output / relative).read_bytes():
                    raise ValueError(f"native producer replay mismatch: {relative}")


def execute(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or not 1 <= config["wiki_max_tasks"] <= 96
        or not 1 <= config["finance_max_tasks"] <= 24
    ):
        raise ValueError("invalid dispatch config")
    wiki, pool, snapshot, report_config, finance = _inputs(config)
    wiki_config = _wiki_config(wiki, pool, snapshot, config["wiki_max_tasks"])
    report_path = ROOT / config["finance_route_config"]["path"]
    if verify_only:
        if (
            not output.is_dir()
            or json.loads((output / "wiki_native_config.json").read_text())
            != wiki_config
        ):
            raise ValueError("frozen Wiki native config changed")
        run_wiki(
            output / "wiki_native_config.json",
            output / "wiki_native",
            workers=1,
            resume=True,
        )
        report_audit(
            report_path,
            output / "finance_native",
            output / "finance_final_audit.json",
            verify_only=True,
        )
        report_convert(
            report_path,
            output / "finance_native",
            output / "finance_final_audit.json",
            output / "finance_unified",
            verify_only=True,
        )
        _replay_native(output, finance, report_config, config["finance_max_tasks"])
    else:
        output.mkdir(parents=True, exist_ok=False)
        _write(output / "wiki_native_config.json", wiki_config)
        run_wiki(output / "wiki_native_config.json", output / "wiki_native", workers=1)
        batch_path = ROOT / report_config["source_batch_manifest"]["path"]
        batch = json.loads(batch_path.read_text())
        source_dir = ROOT / report_config["source_batch_dir"]
        (output / "finance_native").mkdir()
        result = _issuer(
            {key: finance[key] for key in ("issuer", "source_group", "split")},
            str(source_dir),
            str(output / "finance_native"),
            config["finance_max_tasks"],
        )
        _write(
            output / "finance_native/batch_manifest.json",
            _finance_batch(
                config,
                {
                    "source_batch_manifest_sha256": report_config[
                        "source_batch_manifest"
                    ]["sha256"],
                    "source_final_audit_sha256": report_config["source_final_audit"][
                        "sha256"
                    ],
                },
                result,
            ),
        )
        if batch["catalog_sha256"] != _sha(
            ROOT / "configs/p64_finance_taskbank_catalog_v1.json"
        ):
            raise ValueError("finance catalog changed during execution")
        report_audit(
            report_path, output / "finance_native", output / "finance_final_audit.json"
        )
        report_convert(
            report_path,
            output / "finance_native",
            output / "finance_final_audit.json",
            output / "finance_unified",
        )
    lineage, counts = _lineage(output, wiki, finance, config["prior_candidate_index"])
    lineage_text = "".join(_dump(row) for row in lineage)
    receipt = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "planned_jobs_sha256": config["planned_jobs"]["sha256"],
        "prior_candidate_index_sha256": config["prior_candidate_index"]["sha256"],
        "wiki_batch_sha256": _sha(output / "wiki_native/batch_manifest.json"),
        "finance_batch_sha256": _sha(output / "finance_native/batch_manifest.json"),
        "finance_audit_sha256": _sha(output / "finance_final_audit.json"),
        "finance_unified_sha256": _sha(output / "finance_unified/manifest.json"),
        "lineage_sha256": hashlib.sha256(lineage_text.encode()).hexdigest(),
        **counts,
    }
    if verify_only:
        if (output / "lineage.jsonl").read_text() != lineage_text or json.loads(
            (output / "manifest.json").read_text()
        ) != receipt:
            raise ValueError("P113 dispatch replay mismatch")
    else:
        (output / "lineage.jsonl").write_text(lineage_text, encoding="utf-8")
        _write(output / "manifest.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(execute(args.config, args.output, verify_only=args.verify_only)), end=""
    )


if __name__ == "__main__":
    main()
