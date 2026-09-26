"""Batch official RFC numeric clauses with varying simulated observations.

The semantic rule inventory is pinned P109 evidence. This expands state
instances, not source topics or distinct rule mechanisms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.p109_prose_compile import (
    QUESTION_MARKER,
    _answer,
    _compile_one,
    _remove_spans,
    _visible,
)
from scripts.p109_prose_support import SCHEMA as SUPPORT_SCHEMA
from scripts.run_shared_record_taskbank import _tokenizer

SCHEMA = "longworld.p112-grounded-rule-campaign.v1"


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _one(
    job: tuple[dict, dict, dict, int],
) -> tuple[dict | None, dict | None, dict | None, str | None]:
    try:
        reader, index, proof = _compile_one(job)
        return reader, index, proof, None
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return None, None, None, f"{type(exc).__name__}: {exc}"


def run(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or config.get("split") != "train"
        or not 1 <= config.get("workers", 0) <= 8
        or not 12 <= config.get("records_per_state", 0) <= 256
        or config["records_per_state"] % 4
        or not 1 <= len(config.get("state_seeds", [])) <= 128
        or len(set(config["state_seeds"])) != len(config["state_seeds"])
        or any(type(seed) is not int or seed < 0 for seed in config["state_seeds"])
    ):
        raise ValueError("invalid P112 grounded campaign configuration")
    support_pin = config["support_ledger"]
    support_path = Path(support_pin["path"])
    if sha(support_path) != support_pin["sha256"]:
        raise ValueError("pinned official-rule support ledger differs")
    support = json.loads(support_path.read_text())
    if support["schema"] != SUPPORT_SCHEMA or support["reader_tasks_admitted"] != 0:
        raise ValueError("official-rule support inventory differs")
    sources = {row["rfc_id"]: row for row in support["sources"]}
    supported = [
        row for row in support["rows"] if row["status"] == "supported_numeric_minimum"
    ]
    jobs = [
        (config, row, sources[row["rfc_id"]], seed)
        for row in supported
        for seed in config["state_seeds"]
    ]
    if len(supported) != 2:
        raise ValueError("expected two qualified official numeric rules")
    with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
        generated = list(pool.map(_one, jobs))
    tokenizer = _tokenizer()
    ledger = CandidateLedger()
    reader_lines, index_lines, proof_lines, mask_lines, rejected = [], [], [], [], []
    lengths, operations = Counter(), Counter()
    groups: dict[str, list[tuple[tuple[str, ...], str]]] = {}
    source_rule_deletions = 0
    state_observation_deletions = 0
    for job, result in zip(jobs, generated, strict=True):
        reader, index, proof, error = result
        if error is not None:
            rejected.append(
                {"rfc_id": job[1]["rfc_id"], "seed": job[3], "reason": error}
            )
            continue
        assert reader is not None and index is not None and proof is not None
        sample_id = index["sample_id"]
        if reader["sample_id"] != sample_id or proof["sample_id"] != sample_id:
            raise ValueError("reader/index/proof identity differs")
        context, separator, question = reader["messages"][0]["content"].partition(
            QUESTION_MARKER
        )
        if not separator or question != proof["question"]:
            raise ValueError("visible question/proof differs")
        observed = _visible(context, proof["field"])
        if (
            observed is None
            or observed[0] != proof["limit"]
            or _answer(observed[1], observed[0]) != proof["answer"]
        ):
            raise ValueError("official rule and simulated state do not replay")
        if (
            _visible(
                _remove_spans(context, proof["support_group_spans"]), proof["field"]
            )
            is not None
        ):
            raise ValueError("official support-group deletion leaves a rule")
        source_rule_deletions += 1
        violating = next(
            row for row in proof["record_spans"] if row["value"] < proof["limit"]
        )
        changed_context = _remove_spans(context, [violating["char_span"]])
        changed = _visible(changed_context, proof["field"])
        if changed is None or _answer(changed[1], changed[0]) == proof["answer"]:
            raise ValueError(
                "visible violating observation deletion did not change answer"
            )
        state_observation_deletions += 1
        binding = AdapterBinding(
            source_kind="grounded_simulation",
            source_group=index["source_group"],
            domain=index["domain"],
            topic=index["topic"],
            operation=index["operation"],
            evidence_profile="official_rule_plus_simulated_state_boundaries",
            tokenizer_profile="pinned-chat-template",
            receipt_path=support_path,
            receipt_sha256=sha(support_path),
        )
        candidate = normalize_native_candidate(
            index, reader, binding, context_text=context
        )
        ledger.add(candidate)
        clean_reader = {"sample_id": sample_id, "messages": reader["messages"]}
        checked = audit_reader(
            clean_reader, candidate.to_dict(), tokenizer, config["max_full_tokens"]
        )
        groups.setdefault(index["source_group"], []).append(
            (tuple(row["id"] for row in proof["record_spans"]), candidate.answer_sha256)
        )
        reader_lines.append(dump(clean_reader) + "\n")
        record = candidate.to_dict()
        record.update(
            source_name="p112_official_rfc_simulated_state",
            native_row_ref=f"{support_path}:seed={job[3]}",
            output_file="candidate_train.jsonl",
            row_index=len(reader_lines) - 1,
        )
        index_lines.append(dump(record) + "\n")
        proof_lines.append(dump(proof) + "\n")
        mask_lines.append(dump(checked) + "\n")
        lengths[candidate.length_bin] += 1
        operations[candidate.operation] += 1
    # The same record IDs are reused for each source; at least two states must
    # change the answer so record identity alone cannot determine the label.
    if any(
        len({ids for ids, _ in pairs}) != 1 or len({answer for _, answer in pairs}) < 2
        for pairs in groups.values()
    ):
        raise ValueError("one official-rule source has an ID-only answer shortcut")
    payloads = {
        "candidate_train.jsonl": "".join(reader_lines),
        "candidate_eval.jsonl": "",
        "sample_index.jsonl": "".join(index_lines),
        "proofs.jsonl": "".join(proof_lines),
        "mask_rows.jsonl": "".join(mask_lines),
        "rejected.jsonl": "".join(dump(row) + "\n" for row in rejected),
    }
    if not verify_only:
        if output.exists():
            raise ValueError("output exists")
        output.mkdir(parents=True)
    for name, content in payloads.items():
        path = output / name
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"frozen P112 grounded file differs: {name}")
        else:
            path.write_text(content)
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "campaign_schema": SCHEMA,
        "config_sha256": sha(config_path),
        "official_rule_support_sha256": sha(support_path),
        "qualified_real_rule_sources": len(supported),
        "planned_state_tasks": len(jobs),
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "source_groups": len(ledger.group_splits),
        "views_by_lane": {"p112_official_rfc_simulated_state": ledger.rows},
        "splits": {"train": ledger.rows},
        "length_bins": dict(sorted(lengths.items())),
        "operations": dict(sorted(operations.items())),
        "rejected": len(rejected),
        "same_ids_different_answer_source_groups": len(groups),
        "source_rule_support_group_deletion_checks": source_rule_deletions,
        "state_violating_observation_deletion_checks": state_observation_deletions,
        "rejection_reasons": dict(
            sorted(Counter(row["reason"] for row in rejected).items())
        ),
        "files_sha256": {
            name: sha(output / name)
            for name in (
                "candidate_train.jsonl",
                "candidate_eval.jsonl",
                "sample_index.jsonl",
            )
        },
        "proofs_sha256": sha(output / "proofs.jsonl"),
        "mask_rows_sha256": sha(output / "mask_rows.jsonl"),
        "train_ready": False,
        "claim_limit": "two pinned real RFC numeric-rule clauses reused across simulated state seeds; not new real sources or arbitrary prose inference",
    }
    content = dump(manifest) + "\n"
    if verify_only:
        if (output / "manifest.json").read_text() != content:
            raise ValueError("frozen P112 grounded manifest differs")
    else:
        (output / "manifest.json").write_text(content)
    verify_merge(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(dump(run(args.config, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
