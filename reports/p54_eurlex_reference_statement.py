"""Probe a real role-to-annex reference and its conditional statement content."""

from __future__ import annotations

import argparse
import itertools
import json
import re
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

from longworld.core.eurlexworkflow import (
    investigational_statement_spans,
    replay_referenced_statement,
    replay_referenced_statement_raw_slice,
    verify_referenced_statement_sources,
    verify_span,
)
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.taskpromotion import _dossier_spread
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from reports.p54_eurlex_source_span_oracle import (
    _digest,
    _geometry,
    _notice_coverage,
    _write,
)

CONFIG = Path("configs/p54_eurlex_reference_statement_v1.json")
OUTPUT = Path("reports/p54_eurlex_reference_statement_v1.json")


def _necessary_marker_windows(prompt: str, tokenizer: Any) -> dict[str, Any]:
    encoded = tokenizer(prompt, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = encoded["input_ids"], encoded["offset_mapping"]
    intervals = []
    for marker in (
        "Person responsible for regulatory compliance",
        "A signed statement by",
    ):
        matches = list(re.finditer(re.escape(marker), prompt))
        if len(matches) != 1:
            raise ValueError(
                "necessary lexical marker is not unique; pruning proof unavailable"
            )
        match = matches[0]
        touched = [
            index
            for index, (start, end) in enumerate(offsets)
            if start < match.end() and end > match.start()
        ]
        # Widen by one token at each side so incomplete multi-byte boundary
        # characters cannot create a false insufficiency result.
        intervals.append((max(0, min(touched) - 1), min(len(ids), max(touched) + 2)))
    results = {}
    for width in (4096, 8192, 16384):
        possible = []
        checked = max(0, len(ids) - width + 1)
        for start in range(checked):
            end = start + width
            # Overlap with the widened marker envelopes is deliberately weaker
            # than full containment. Zero possible windows is still sufficient.
            if all(start < right and end > left for left, right in intervals):
                possible.append(start)
        replayed = []
        for start in possible:
            text = tokenizer.decode(
                ids[start : start + width], skip_special_tokens=False
            )
            replayed.append(replay_referenced_statement_raw_slice(text)["status"])
        results[str(width)] = {
            "checked_maximal_token_windows": checked,
            "windows_not_pruned": len(possible),
            "replayed_windows": len(replayed),
            "shortcut_found": "PASS" in replayed,
            "all_short_windows_rejected_by_necessary_markers": len(possible) == 0,
        }
    return {
        "mode": "exhaustive_start_positions_with_necessary_marker_pruning",
        "necessary_marker_token_envelopes": intervals,
        "windows": results,
        "scope": "Both markers are required by the literal raw-text oracle. Zero co-visible marker windows proves insufficiency for this parser, including shorter windows. This does not establish semantic necessity against other answer programs or substitute for the shared registered audit.",
    }


def run(config_path: Path, output: Path) -> dict[str, Any]:
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    raw = {}
    for source in config["sources"]:
        body = (
            Path(config["raw_source_directory"]) / f"{source['source_id']}.raw"
        ).read_bytes()
        if (
            _digest(body) != source["expected_sha256"]
            or len(body) != source["expected_bytes"]
        ):
            raise ValueError("source receipt mismatch")
        raw[source["source_id"]] = body
    spans = investigational_statement_spans(raw["act"])
    for span in spans:
        verify_span(span, raw["act"])
    verify_referenced_statement_sources(
        spans,
        raw["act"],
        next(source for source in config["sources"] if source["source_id"] == "act"),
    )
    answer = replay_referenced_statement(spans)
    if answer["status"] != "PASS":
        raise ValueError("reference oracle failed")
    subsets = []
    for size in range(len(spans)):
        for indices in itertools.combinations(range(len(spans)), size):
            result = replay_referenced_statement([spans[index] for index in indices])
            subsets.append({"span_indices": list(indices), "status": result["status"]})
    tok = config["tokenizer"]
    if (
        resolved_tokenizer_asset_manifest_sha256(tok["model_id"], tok["revision"])
        != tok["asset_manifest_sha256"]
    ):
        raise ValueError("tokenizer receipt mismatch")
    tokenizer = AutoTokenizer.from_pretrained(
        tok["model_id"],
        revision=tok["revision"],
        local_files_only=True,
        trust_remote_code=False,
    )
    directory = Path(config["source_directory"])
    directory.mkdir(parents=True, exist_ok=True)
    geometry, artifacts = _geometry(
        config,
        raw,
        spans,
        tokenizer,
        replay=replay_referenced_statement,
        cf_span_sha256=spans[-1]["raw_span_sha256"],
    )
    geometry["counterfactual"] = (
        "remove the exact referenced statement paragraph; target resolution returns UNKNOWN"
    )
    document_context = SEP.join(item["text"] for item in artifacts)
    prompt = wrap_prompt(config["task"]["question"], document_context, "last")
    if replay_referenced_statement_raw_slice(document_context) != answer:
        raise ValueError("full visible-text replay differs from source-span replay")
    geometry["necessary_marker_raw_window_preflight"] = _necessary_marker_windows(
        prompt, tokenizer
    )
    essential_by_id = {f"act:{span['byte_start']}": span for span in spans}
    shared_full = _dossier_spread(
        [
            (
                f"{item['source_ordinal']:010d}",
                {"artifact_id": item["artifact_id"]},
                item["text"],
            )
            for item in artifacts
        ]
    )
    shared_ids = [item[0]["artifact_id"] for item in shared_full]
    shared_documents = [item[1] for item in shared_full]
    positions = [shared_ids.index(key) for key in essential_by_id]
    left, right = min(positions), max(positions) + 1
    witness = SEP.join(shared_documents[left:right])
    shared_rejection = {
        "schema_version": "longworld.p54-shared-layout-rejection.v1",
        "shared_full_context_tokens": len(
            tokenizer.encode(
                wrap_prompt(
                    config["task"]["question"], SEP.join(shared_documents), "first"
                ),
                add_special_tokens=False,
            )
        ),
        "shared_ordered_context_tokens": len(
            tokenizer.encode(
                wrap_prompt(config["task"]["question"], document_context, "first"),
                add_special_tokens=False,
            )
        ),
        "witness_artifact_start": left,
        "witness_artifact_stop": right,
        "witness_tokens": len(tokenizer.encode(witness, add_special_tokens=False)),
        "witness_answer": replay_referenced_statement(
            [
                essential_by_id[key]
                for key in shared_ids[left:right]
                if key in essential_by_id
            ]
        )["answer"],
        "full_answer": answer["answer"],
        "empty_answer": replay_referenced_statement([])["answer"],
        "counterfactual_answer": replay_referenced_statement(spans[:-1])["answer"],
        "candidate_count": 0,
        "shared_full_layout": "longworld.core.taskpromotion._dossier_spread",
        "formal_shared_audit_run": False,
    }
    (directory / "shared_full_shortcut.txt").write_text(witness)
    _write(Path("reports/p54_eurlex_shared_layout_rejection_v1.json"), shared_rejection)
    _write(directory / "selected_spans.json", spans)
    _write(directory / "geometry_artifacts.json", artifacts)
    rights = _notice_coverage(raw["legal_notice"], spans)
    rights["coverage_scope"] = (
        "Article 15, Annex XV and Chapter II headings, and resolved Section 4.1 only"
    )
    rights["documents"] = {
        "act": {
            "basis": "EUR-Lex legal-document reuse notice",
            "selected_content": rights["coverage_scope"],
            "attribution": "European Union, EUR-Lex, CELEX 32017R0745",
            "selected_non_text_assets": 0,
        }
    }
    result = {
        "schema_version": "longworld.p54-eurlex-reference-statement-report.v1",
        "config_sha256": _digest(config_raw),
        "source_receipts": [
            {
                "source_id": source["source_id"],
                "url": source["url"],
                "sha256": source["expected_sha256"],
                "bytes": source["expected_bytes"],
            }
            for source in config["sources"]
        ],
        "dependency_program": [
            "find responsible-person duty conditioned on investigational devices",
            "parse target annex/chapter/section from that duty",
            "resolve the parsed target in source order",
            "parse statement issuer and conformity exception",
            "bind safeguard to the excepted aspects using its explicit anaphoric link",
        ],
        "full_replay": answer,
        "all_15_proper_subsets": subsets,
        "all_proper_subsets_unknown": all(
            item["status"] == "UNKNOWN" for item in subsets
        ),
        "selected_spans_path": str(directory / "selected_spans.json"),
        "selected_spans_sha256": _digest(
            (directory / "selected_spans.json").read_bytes()
        ),
        "geometry_artifacts_path": str(directory / "geometry_artifacts.json"),
        "geometry_artifacts_sha256": _digest(
            (directory / "geometry_artifacts.json").read_bytes()
        ),
        "rights": rights,
        "geometry": geometry,
        "candidate_count": 0,
        "train_ready": False,
        "inventory_delta": 0,
        "next_gate": "REJECTED: the shared full layout exposes complete support in a 10,485-token witness; keep this negative control and use the distinct PMS chain",
    }
    _write(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = run(args.config, args.output)
    print(
        json.dumps(
            {"full_replay": result["full_replay"], "geometry": result["geometry"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
