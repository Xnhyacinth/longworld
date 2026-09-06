"""One bounded source-first PMS-to-risk-control chain and shared-layout probe."""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

from longworld.core.eurlexworkflow import (
    extract_pms_chain as extract_chain,
)
from longworld.core.eurlexworkflow import (
    replay_pms_chain as replay_chain,
)
from longworld.core.eurlexworkflow import (
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

CONFIG = Path("configs/p54_eurlex_pms_chain_v1.json")
OUTPUT = Path("reports/p54_eurlex_pms_chain_v1.json")


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
    spans = extract_chain(raw["act"])
    for span in spans:
        verify_span(span, raw["act"])
    full = replay_chain(spans)
    if full["status"] != "PASS":
        raise ValueError(f"PMS chain did not resolve: {full}")
    cf = replay_chain(spans[:-1])
    subsets = []
    for size in range(len(spans)):
        for indices in itertools.combinations(range(len(spans)), size):
            result = replay_chain([spans[index] for index in indices])
            subsets.append(
                {
                    "span_indices": list(indices),
                    "matches_full": result["answer"] == full["answer"],
                    "matches_cf": result["answer"] == cf["answer"],
                }
            )
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
        replay=replay_chain,
        cf_span_sha256=spans[-1]["raw_span_sha256"],
    )
    geometry["counterfactual"] = (
        "omit the authentic Annex I Section 4 block; retain the proved prefix through Section 3 and report Section 4 as first unresolved"
    )
    essential = {f"act:{span['byte_start']}": span for span in spans}
    chronology = [
        (
            f"{item['source_ordinal']:010d}",
            {"artifact_id": item["artifact_id"]},
            item["text"],
        )
        for item in artifacts
    ]
    layouts = {
        "full": _dossier_spread(chronology),
        "ordered": [(item[1], item[2]) for item in chronology],
    }
    layout_results = {}
    for view, layout in layouts.items():
        ids = [classification["artifact_id"] for classification, _text in layout]
        documents = [text for _classification, text in layout]
        positions = [ids.index(key) for key in essential]
        left, right = min(positions), max(positions) + 1
        witness = SEP.join(documents[left:right])
        selected = [essential[key] for key in ids[left:right] if key in essential]
        cf_positions = [ids.index(key) for key in list(essential)[:-1]]
        cf_left, cf_right = min(cf_positions), max(cf_positions) + 1
        cf_docs = [
            documents[index]
            for index in range(cf_left, cf_right)
            if ids[index] != list(essential)[-1]
        ]
        layout_results[view] = {
            "context_tokens": len(
                tokenizer.encode(
                    wrap_prompt(
                        config["task"]["question"], SEP.join(documents), "first"
                    ),
                    add_special_tokens=False,
                )
            ),
            "full_support_window": {
                "artifact_start": left,
                "artifact_stop": right,
                "tokens": len(tokenizer.encode(witness, add_special_tokens=False)),
                "replays_full": replay_chain(selected) == full,
            },
            "cf_prefix_support_window_tokens": len(
                tokenizer.encode(SEP.join(cf_docs), add_special_tokens=False)
            ),
        }
    _write(directory / "selected_spans.json", spans)
    _write(directory / "geometry_artifacts.json", artifacts)
    result = {
        "schema_version": "longworld.p54-eurlex-pms-chain-report.v1",
        "config_sha256": _digest(config_raw),
        "source_receipts": config["sources"],
        "full_replay": full,
        "counterfactual_replay": cf,
        "proper_subset_count": len(subsets),
        "proper_subset_matches_full": sum(item["matches_full"] for item in subsets),
        "proper_subset_matches_cf": [
            item["span_indices"] for item in subsets if item["matches_cf"]
        ],
        "selected_spans_path": str(directory / "selected_spans.json"),
        "selected_spans_sha256": _digest(
            (directory / "selected_spans.json").read_bytes()
        ),
        "geometry_artifacts_path": str(directory / "geometry_artifacts.json"),
        "geometry_artifacts_sha256": _digest(
            (directory / "geometry_artifacts.json").read_bytes()
        ),
        "geometry": geometry,
        "shared_layout_support_windows": layout_results,
        "rights": _notice_coverage(raw["legal_notice"], spans),
        "candidate_count": 0,
        "train_ready": False,
        "formal_shared_audit_run": False,
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
            {
                key: result[key]
                for key in (
                    "full_replay",
                    "counterfactual_replay",
                    "geometry",
                    "shared_layout_support_windows",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
