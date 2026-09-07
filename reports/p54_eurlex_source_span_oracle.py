"""Reproduce the P54 exact-span oracle and serialized 32K geometry probe."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

from longworld.core.eurlexworkflow import (
    EurLexWorkflowError,
    article_span,
    eurlex_document,
    manufacturer_years,
    normalized_text,
    relation_spans,
    replay_qualification_change,
    replay_source_bound_qualification_change,
    source_span,
)
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from reports.p54_eurlex_legislative_chain_preflight import (
    _fetch,
    _near_deduplicate,
    _unit,
)

CONFIG = Path("configs/p54_eurlex_source_span_oracle_v1.json")
OUTPUT = Path("reports/p54_eurlex_source_span_oracle_v1.json")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _notice_coverage(raw: bytes, spans: list[dict[str, Any]]) -> dict[str, Any]:
    text = normalized_text(raw)
    checks = {
        "legal_documents": "you can re-use the legal documents published in EUR-Lex for commercial or non-commercial purposes",
        "exceptions": "may be subject to special conditions of use",
        "third_party": "includes third-party works",
        "metadata": "EUR-Lex metadata is dedicated to the public domain",
        "metadata_cc0": "Creative Commons CC0 1.0",
    }
    for name, phrase in checks.items():
        if phrase not in text:
            raise ValueError(f"official legal notice coverage changed: {name}")
    for span in spans:
        if re.search(r"<(?:img|object|iframe)\b", span["raw_text"], re.IGNORECASE):
            raise ValueError("selected span contains an unreviewed non-text asset")
    return {
        "notice_sha256": _digest(raw),
        "official_notice_url": "https://eur-lex.europa.eu/content/legal-notice/legal-notice.html",
        "coverage_scope": "only the exact source spans listed in documents; no unselected background coverage",
        "whole_document_third_party_review": "NOT_PERFORMED",
        "documents": {
            source_id: {
                "basis": "CC0 1.0"
                if source_id == "metadata"
                else "EUR-Lex legal-document reuse notice",
                "attribution": "European Union, EUR-Lex, " + source_id,
                "selected_non_text_assets": 0,
                "selected_spans": [
                    {
                        key: span[key]
                        for key in ("byte_start", "byte_end", "raw_span_sha256")
                    }
                    for span in spans
                    if span["source_id"] == source_id
                ],
            }
            for source_id in sorted({span["source_id"] for span in spans})
        },
        "exclusions": [
            "third-party works",
            "logos and images",
            "industrial property rights",
        ],
        "normalization_disclosed": "HTML tags removed; whitespace collapsed; exact raw spans retained",
        "technical_selected_span_coverage": "PASS",
        "candidate_background_coverage": "NEEDS_CANDIDATE_REVIEW",
        "legal_opinion": False,
    }


def _geometry(
    config: dict[str, Any],
    raw: dict[str, bytes],
    spans: list[dict[str, Any]],
    tokenizer: Any,
    *,
    replay: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
    cf_span_sha256: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Measure shared prompt serialization; never impersonate a shared task audit."""
    essential = []
    for span in spans:
        unit = _unit(
            f"{span['source_id']}:{span['byte_start']}",
            span["source_id"],
            span["raw_text"] if span["source_id"] == "metadata" else span["text"],
            source_ordinal=span["byte_start"],
        )
        unit["source_span"] = span
        essential.append(unit)
    background = []
    for name in (name for name in config["source_order"] if name != "metadata"):
        for match in re.finditer(rb"<p\b[^>]*>.*?</p>", raw[name], re.DOTALL):
            if any(
                s["source_id"] == name
                and match.start() < s["byte_end"]
                and match.end() > s["byte_start"]
                for s in spans
            ):
                continue
            text = normalized_text(match.group())
            if len(text) < 80:
                continue
            unit = _unit(
                f"{name}:{match.start()}", name, text, source_ordinal=match.start()
            )
            unit["source_span"] = source_span(
                raw[name], name, match.start(), match.end()
            )
            background.append(unit)
    unique, removed = _near_deduplicate(background, size=5, threshold=0.9)
    # Membership is a predeclared text-hash order; source byte order is restored
    # for presentation. No positions are searched or optimized for shortcut gates.
    unique.sort(key=lambda item: (item["text_sha256"], item["artifact_id"]))
    framed = config.get("artifact_serialization") == "eurlex_span_json"
    if framed:
        for item in [*essential, *unique]:
            item["text"] = eurlex_document(item["source_span"])
    order = {name: index for index, name in enumerate(config["source_order"])}
    adoption_id = next(
        item["artifact_id"]
        for item in essential
        if (
            cf_span_sha256 is not None
            and item["source_span"]["raw_span_sha256"] == cf_span_sha256
        )
        or (
            cf_span_sha256 is None
            and "<RESOURCE_LEGAL_ADOPTS_RESOURCE_LEGAL"
            in item["source_span"]["raw_text"]
        )
    )

    def ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items, key=lambda item: (order[item["source_id"]], item["source_ordinal"])
        )

    def prompt(items: list[dict[str, Any]]) -> str:
        return wrap_prompt(
            config["task"]["question"],
            SEP.join(item["text"] for item in ordered(items)),
            "last",
        )

    def count(items: list[dict[str, Any]]) -> int:
        return len(tokenizer.encode(prompt(items), add_special_tokens=False))

    def counts(items: list[dict[str, Any]]) -> dict[str, int]:
        full = count(items)
        cf_items = [
            {**item, "text": eurlex_document(item["source_span"], withheld=True)}
            if framed and item["artifact_id"] == adoption_id
            else item
            for item in items
            if framed or item["artifact_id"] != adoption_id
        ]
        return {
            "full": full,
            "cf": count(cf_items),
            "ordered": full,
        }

    selected = list(essential)
    lower, upper = config["length_buckets"]["32k"]
    # Batch individual token costs for a conservative first pass, then measure
    # the actual wrap_prompt serialization for every final whole-block addition.
    costs = tokenizer(
        [SEP + item["text"] for item in unique], add_special_tokens=False
    )["input_ids"]
    estimated = count(selected)
    remaining = []
    for item, tokens in zip(unique, costs, strict=True):
        if estimated + len(tokens) <= lower - 2048:
            selected.append(item)
            estimated += len(tokens)
        else:
            remaining.append(item)
    observed = counts(selected)
    for item in remaining:
        if min(observed.values()) >= lower:
            break
        trial = [*selected, item]
        trial_counts = counts(trial)
        if max(trial_counts.values()) <= upper:
            selected, observed = trial, trial_counts
    selected = ordered(selected)
    full_prompt = prompt(selected)
    essential_positions = {}
    for item in essential:
        text = item["text"]
        start = full_prompt.find(text)
        if start < 0 or full_prompt.find(text, start + 1) >= 0:
            raise ValueError("complete essential artifact is absent or duplicated")
        essential_positions[item["artifact_id"]] = {
            "token_start": len(
                tokenizer.encode(full_prompt[:start], add_special_tokens=False)
            ),
            "token_end": len(
                tokenizer.encode(
                    full_prompt[: start + len(text)], add_special_tokens=False
                )
            ),
        }
    extent = max(item["token_end"] for item in essential_positions.values()) - min(
        item["token_start"] for item in essential_positions.values()
    )
    token_ids = tokenizer.encode(full_prompt, add_special_tokens=False)
    witnesses = []
    for width in (4096, 8192, 16384):
        window = tokenizer.decode(token_ids[:width], skip_special_tokens=False)
        projected = []
        for item in essential:
            start = full_prompt.find(item["text"])
            if start >= len(window):
                continue
            fragment = window[start : min(len(window), start + len(item["text"]))]
            span = dict(item["source_span"])
            if span["source_id"] == "metadata":
                if fragment != item["text"]:
                    continue
                span["raw_text"] = fragment
            else:
                if framed:
                    try:
                        span["text"] = json.loads(fragment)["text"]
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
                else:
                    span["text"] = fragment
            projected.append(span)
        observed_replay = (
            replay(projected)
            if replay
            else replay_qualification_change(projected, config["source_celex"])
        )
        witnesses.append(
            {
                "start_token": 0,
                "window_tokens": width,
                "status": observed_replay["status"],
                "answer": observed_replay["answer"],
                "window_text_sha256": _digest(window.encode()),
            }
        )
        if observed_replay["status"] == "PASS":
            (
                Path(config["source_directory"]) / "qualification_shortcut_window.txt"
            ).write_text(window)
            break
    return {
        "serialization": "longworld.core.pack.wrap_prompt + SEP, timing=last",
        "status": "GEOMETRY_ONLY",
        "exact_serialized_tokens": observed,
        "all_views_in_32k_band": min(observed.values()) >= lower
        and max(observed.values()) <= upper,
        "counterfactual": "remove exact authentic adoption element; UNKNOWN",
        "shared_counterfactual_materializer_registered": False,
        "selected_artifact_count": len(selected),
        "selected_artifact_ids_sha256": _digest(
            "\n".join(item["artifact_id"] for item in selected).encode()
        ),
        "context_sha256": _digest(full_prompt.encode()),
        "background_near_duplicates_removed": removed,
        "essential_whole_artifact_positions": essential_positions,
        "whole_artifact_cover_span_tokens": extent,
        "whole_artifact_coverage_only_4k_8k_16k": {
            str(window): extent <= window for window in (4096, 8192, 16384)
        },
        "whole_artifact_coverage_limitation": "This is not a raw-token replay audit: smaller sufficient clauses or alternate evidence may answer within a window. No shortcut PASS is claimed.",
        "raw_prefix_window_trials": witnesses,
        "raw_prefix_shortcut_found": any(
            witness["status"] == "PASS" for witness in witnesses
        ),
        "raw_prefix_window_scope": "Exact pinned token prefix decoded from the serialized prompt; source-bound artifact intersections replay semantic nodes, including sufficient partial articles. One sufficient witness rejects the layout; no exhaustive success audit is claimed.",
        "shared_raw_window_audit": "NOT_RUN",
        "shared_derived_view_gate": "NOT_RUN",
        "higher_band_proof_growth": "UNVERIFIED",
        "padding_or_cloning_or_truncation": False,
    }, selected


def run(config_path: Path, output: Path, *, fetch: bool = False) -> dict[str, Any]:
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    directory = Path(config["source_directory"])
    directory.mkdir(parents=True, exist_ok=True)
    raw = {}
    for source in config["sources"]:
        path = directory / f"{source['source_id']}.raw"
        if fetch:
            body, _receipt = _fetch(source)
            if path.exists() and path.read_bytes() != body:
                raise ValueError("refusing to replace a frozen P54 source")
            path.write_bytes(body)
        body = path.read_bytes()
        if (
            _digest(body) != source["expected_sha256"]
            or len(body) != source["expected_bytes"]
        ):
            raise ValueError(f"pinned source changed: {source['source_id']}")
        raw[source["source_id"]] = body
    spans = [article_span(raw[name], name) for name in ("proposal", "act")]
    spans.extend(relation_spans(raw["metadata"]))
    full = replay_source_bound_qualification_change(spans, raw, config["sources"])
    if full["status"] != "PASS":
        raise ValueError("full qualification replay failed")
    reduced = []
    for size in range(len(spans)):
        for indices in itertools.combinations(range(len(spans)), size):
            subset = [spans[index] for index in indices]
            result = replay_source_bound_qualification_change(
                subset, raw, config["sources"]
            )
            reduced.append({"span_indices": list(indices), "status": result["status"]})
    position = article_span(raw["position"], "position")
    try:
        manufacturer_years(position["text"])
    except EurLexWorkflowError as error:
        position_result = {
            "status": "REJECTED",
            "reason": str(error),
            "literal_ambiguity": "five three years",
            "source_span_sha256": position["raw_span_sha256"],
        }
    else:
        raise ValueError("the pinned first-reading ambiguity unexpectedly disappeared")
    tok = config["tokenizer"]
    assets = resolved_tokenizer_asset_manifest_sha256(tok["model_id"], tok["revision"])
    if assets != tok["asset_manifest_sha256"]:
        raise ValueError("tokenizer assets changed")
    tokenizer = AutoTokenizer.from_pretrained(
        tok["model_id"],
        revision=tok["revision"],
        local_files_only=True,
        trust_remote_code=False,
    )
    geometry, selected = _geometry(config, raw, spans, tokenizer)
    _write(directory / "selected_spans.json", spans)
    _write(directory / "geometry_artifacts.json", selected)
    result = {
        "schema_version": "longworld.p54-eurlex-source-span-oracle-report.v1",
        "config_sha256": _digest(config_raw),
        "source_receipts": [
            {
                "source_id": source["source_id"],
                "url": source["url"],
                "sha256": source["expected_sha256"],
                "bytes": source["expected_bytes"],
                "raw_source_persisted": True,
            }
            for source in config["sources"]
        ],
        "selected_spans_path": str(directory / "selected_spans.json"),
        "selected_spans_sha256": _digest(
            (directory / "selected_spans.json").read_bytes()
        ),
        "geometry_artifacts_path": str(directory / "geometry_artifacts.json"),
        "geometry_artifacts_sha256": _digest(
            (directory / "geometry_artifacts.json").read_bytes()
        ),
        "full_replay": full,
        "all_15_proper_subsets": reduced,
        "all_proper_subsets_unknown": all(
            item["status"] == "UNKNOWN" for item in reduced
        ),
        "reduced_evidence_scope": "selected source-bound articles and direct adoption identity/relation only; no claim that all alternative source passages have been exhausted",
        "first_reading_trial": position_result,
        "rights": _notice_coverage(raw["legal_notice"], spans),
        "geometry": geometry,
        "candidate_count": 0,
        "train_ready": False,
        "inventory_delta": 0,
        "next_gate": "REJECTED: retain the 16K shortcut witness and change the task; do not register this qualification-difference layout",
    }
    _write(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    result = run(args.config, args.output, fetch=args.fetch)
    print(
        json.dumps(
            {
                "full_replay": result["full_replay"],
                "geometry": result["geometry"],
                "candidate_count": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
