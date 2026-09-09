"""CodeForge complete-record support geometry, with exact visible-ID mapping.

This report is not a minimal proof or model baseline. Run from repository root.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_environment_names
from longworld.core.codeforge_taskbank import _facts, canonical, render_context
from longworld.core.taskbank_dependency_audit import _coverage, _interval, exact_offsets
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

MODEL = "Qwen/Qwen3.5-4B"
REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
BANK = ROOT / "data/candidates/p64_codeforge_taskbank_v2"
OUTPUT = ROOT / "reports/p64_codeforge_dependency_final"
TOKENIZER = None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def initialize():
    global TOKENIZER
    for name in attestation_environment_names():
        os.environ.pop(name, None)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from transformers import AutoTokenizer
    TOKENIZER = AutoTokenizer.from_pretrained(MODEL, revision=REVISION, local_files_only=True, trust_remote_code=False)


def audit_bank(directory):
    world = json.loads((directory / "world.json").read_text())
    tasks = [json.loads(line) for line in (directory / "tasks.jsonl").read_text().splitlines()]
    contexts = {r["context_id"]: r for r in map(json.loads, (directory / "contexts.jsonl").read_text().splitlines())}
    groups = defaultdict(list)
    for task in tasks:
        groups[task["context_id"]].append(task)
    episodes = {e["episode_id"]: e for e in world["episodes"]}
    reports = []
    for identity, group in sorted(groups.items()):
        ctx = contexts[identity]
        selected_episodes = [episodes[e] for e in ctx["episode_ids"]]
        text = render_context(world, ctx["episode_ids"])
        if hashlib.sha256(text.encode()).hexdigest() != ctx["context_sha256"]:
            raise ValueError("context changed")
        offsets = exact_offsets(text, TOKENIZER)
        if len(offsets) != ctx["exact_context_tokens"]:
            raise ValueError("context tokens changed")
        starts, ends = [p[0] for p in offsets], [p[1] for p in offsets]
        visible = json.loads(text)
        by_label = {r["id"]: r for r in visible["records"]}
        selected_ids = {i for e in selected_episodes for i in e["record_ids"]}
        raw_rows = sorted((r for r in world["records"] if r["record_id"] in selected_ids), key=lambda r: (r["occurred_at"], r["record_id"]))
        labels, licenses, ordinal = {}, {}, 0
        intervals = {}
        for raw in raw_rows:
            if raw["kind"] == "license" and raw["text"] in licenses:
                label = licenses[raw["text"]]
            else:
                ordinal += 1
                label = "R" + str(ordinal)
                if raw["kind"] == "license":
                    licenses[raw["text"]] = label
            labels[raw["record_id"]] = label
            record = by_label[label]
            if record["text"] != raw["text"] or record["kind"] != raw["kind"] or raw["source_pointer"] not in record["source_urls"]:
                raise ValueError("visible/source record mapping mismatch")
            serialized = canonical(record)
            start = text.find(serialized)
            if start < 0 or text.find(serialized, start + 1) >= 0:
                raise ValueError("serialized visible record not unique")
            intervals[raw["record_id"]] = _interval(start, start + len(serialized), starts, ends)
        scope = canonical(visible["scope"])
        start = text.find(scope)
        if start < 0:
            raise ValueError("missing serialized scope map")
        scope_interval = _interval(start, start + len(scope), starts, ends)
        heads = [_facts(world, e)["head_record_id"] for e in selected_episodes]
        for task in group:
            bound = [intervals[i] for i in task["evidence_record_ids"]] + [scope_interval]
            head_intervals = [intervals[i] for i in heads]
            reports.append({
                "sample_id": task["sample_id"], "semantic_task_id": task["semantic_task_id"],
                "source_group_id": task["source_group_id"], "split": task["split"],
                "program_id": task["program_id"], "context_id": identity,
                "context_sha256": ctx["context_sha256"], "context_tokens": len(offsets),
                "source_record_visibility": "all_bound_records_mapped_to_complete_visible_records",
                "bound_records_and_scope_windows": {str(w): _coverage(bound, w, len(offsets)) for w in (4096,8192,16384)},
                "oracle_located_complete_head_record_windows": {str(w): _coverage(head_intervals, w, len(offsets)) for w in (4096,8192,16384)},
                "head_window_is_complete_proof": False,
                "bound_record_envelope_tokens": max(b for _, b in bound)-min(a for a, _ in bound),
                "strict_long_dependency_verified": False,
                "alternative_proof_search_complete": False,
                "question_only_neural": "unmeasured",
            })
        print(directory.name, identity[:10], len(group), "rows audited", flush=True)
    return reports, {name: digest(directory/name) for name in ("world.json","tasks.jsonl","contexts.jsonl","BUILD_RECEIPT.json")}


def main():
    if OUTPUT.exists():
        raise ValueError("preserve previous report")
    directories = sorted(p.parent for p in BANK.glob("*/tasks.jsonl"))
    assets = resolved_tokenizer_asset_manifest_sha256(MODEL, REVISION)
    rows, inputs = [], {}
    with ProcessPoolExecutor(max_workers=3, initializer=initialize) as pool:
        for directory, (part, bindings) in zip(directories, pool.map(audit_bank, directories), strict=True):
            rows.extend(part)
            inputs[directory.name] = bindings
    OUTPUT.mkdir()
    path = OUTPUT / "samples.jsonl"
    path.write_text("".join(json.dumps(row,sort_keys=True)+"\n" for row in rows))
    summary = {
        "schema_version": "longworld.codeforge-support-geometry-audit.v1",
        "rows": len(rows), "semantic_tasks": len({r["semantic_task_id"] for r in rows}),
        "contexts": len({r["context_id"] for r in rows}),
        "split": dict(Counter(r["split"] for r in rows)),
        "programs": dict(Counter(r["program_id"] for r in rows)),
        "all_bound_records_and_scope_fit": {str(w): sum(r["bound_records_and_scope_windows"][str(w)]["all_bound_spans_fit"] for r in rows) for w in (4096,8192,16384)},
        "all_oracle_located_heads_fit": {str(w): sum(r["oracle_located_complete_head_record_windows"][str(w)]["all_bound_spans_fit"] for r in rows) for w in (4096,8192,16384)},
        "strict_verified": 0, "neural_baselines": "unmeasured",
        "scope": "All bank candidates including later short/overflow SFT exclusions; intersect primary metadata before primary accounting.",
        "limits": "Bound records are over-approximate finite-evaluator evidence. Complete-head-only geometry omits graph selection/scope proof. Neither is a minimal alternative proof or model success/failure.",
        "tokenizer": {"model_id":MODEL,"revision":REVISION,"asset_manifest_sha256":assets},
        "inputs": inputs, "samples_sha256":digest(path),
        "code_sha256": {str(p.relative_to(ROOT)):digest(p) for p in (Path(__file__), ROOT/'longworld/core/codeforge_taskbank.py', ROOT/'longworld/core/taskbank_dependency_audit.py')},
    }
    (OUTPUT/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
