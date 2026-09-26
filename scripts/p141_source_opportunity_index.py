"""Index typed opportunities in frozen real sources without admitting new QA.

An opportunity records the source unit and parser evidence needed for a future
compiler. It never upgrades an HTML grid or a source label into a gold answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p141-source-opportunity.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(value: dict[str, str]) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("source pin needs path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != value["sha256"]:
        raise ValueError(f"source pin drift: {relative}")
    return path


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _encode(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _lines(rows: list[dict]) -> bytes:
    return b"".join(
        (
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
        for row in rows
    )


def _pinned_child(parent: Path, name: str, digest: str) -> Path:
    child = parent.parent / name
    if _sha(child) != digest:
        raise ValueError(f"frozen child changed: {child}")
    return child


def _wiki_sources(pool: dict) -> dict[str, dict]:
    by_title: dict[str, dict] = {}
    for source in pool["sources"]:
        snapshot_path = _pin(source["snapshot"])
        snapshot = json.loads(snapshot_path.read_text())
        for doc in snapshot["documents"]:
            title = doc["title"]
            if title in by_title:
                raise ValueError(f"duplicate Wiki title: {title}")
            by_title[title] = {
                "source_group": source["name"],
                "domain": source["domain"],
                "topic": source["topic"],
                "split": source["split"],
                "snapshot": source["snapshot"],
                "revision_url": doc["revision_url"],
                "document_chars": len(doc["text"]),
            }
    return by_title


def _grid_opportunity(row: dict, source: dict, grid_path: Path) -> dict:
    grid = row["grid"]
    origins = grid["origins"]
    body = grid["rows"][grid["header_rows"] :]
    if len(body) != grid["body_rows"] or any(
        len(line) != grid["width"] for line in body
    ):
        raise ValueError("frozen grid shape drift")
    for line in body:
        if any(key not in origins for key in line):
            raise ValueError("grid origin missing")
    qualified = sum(
        bool(cell["footnote_refs"] or cell["citation_markers"])
        for cell in origins.values()
    )
    spanned = sum(
        cell["rowspan"] != 1 or cell["colspan"] != 1 for cell in origins.values()
    )
    multiline = sum(cell["multiline"] for cell in origins.values())
    # Structural capacity only: entity-key semantics, units, full-text support,
    # and counterfactual reader dependence remain for the task compiler.
    candidate_columns = [
        i
        for i in range(grid["width"])
        if sum(bool(origins[line[i]]["text"].strip()) for line in body) >= 3
    ]
    families = ["structured_table_cell_lookup"] if candidate_columns else []
    if len(candidate_columns) >= 2 and len(body) >= 3:
        families.append("structured_table_complete_set_candidate")
    skip = [] if families else ["insufficient_nonempty_body_columns"]
    return {
        "source_kind": "real_wiki_html",
        "source_group": source["source_group"],
        "domain": source["domain"],
        "topic": source["topic"],
        "split": source["split"],
        "native_shape": "frozen_html_grid",
        "source_unit": row["table_id"],
        "source_title": row["title"],
        "revision_url": row["revision_url"],
        "source_pins": {
            "snapshot": source["snapshot"],
            "grid_file": {
                "path": str(grid_path.relative_to(ROOT)),
                "sha256": _sha(grid_path),
            },
        },
        "capacity": {
            "body_rows": len(body),
            "columns": grid["width"],
            "nonempty_columns": candidate_columns,
            "origin_cells": len(origins),
            "qualified_cells": qualified,
            "spanned_cells": spanned,
            "multiline_cells": multiline,
            "document_chars": source["document_chars"],
        },
        "typed_evidence": [
            "html_origin_cell_span_hash",
            "row",
            "header_path",
            "citation_and_footnote_links",
        ],
        "operation_families": families,
        "skip_reasons": skip,
        "admission_status": "source_shape_only; no gold, semantic row binding, reader necessity, or long-distance certificate",
    }


def _wiki(
    campaign_path: Path, pool_path: Path, workers: int
) -> tuple[list[dict], list[dict]]:
    campaign = json.loads(campaign_path.read_text())
    pool = json.loads(pool_path.read_text())
    sources = _wiki_sources(pool)
    chunks = _jsonl(
        _pinned_child(
            campaign_path, "chunk_ledger.jsonl", campaign["chunk_ledger_sha256"]
        )
    )
    if sum(item["pages"] for item in chunks) != campaign["successful_pages"]:
        raise ValueError("Wiki campaign page count drift")

    def read_chunk(chunk: dict) -> tuple[list[dict], list[dict]]:
        chunk_dir = campaign_path.parent / "chunks" / chunk["chunk"]
        source_manifest_path = chunk_dir / "source" / "source_manifest.json"
        if _sha(source_manifest_path) != chunk["source_manifest_sha256"]:
            raise ValueError("Wiki HTML source manifest drift")
        source_manifest = json.loads(source_manifest_path.read_text())
        source_records = {
            record["title"]: record for record in source_manifest["records"]
        }
        if len(source_records) != chunk["pages"]:
            raise ValueError("Wiki HTML source page count drift")
        for record in source_records.values():
            html_path = ROOT / record["html_path"]
            if _sha(html_path) != record["html_sha256"]:
                raise ValueError("Wiki frozen HTML changed")
            _pin(record["fetch_receipt"])
        grid_dir = chunk_dir / "grid"
        manifest_path = grid_dir / "manifest.json"
        if _sha(manifest_path) != chunk["grid_manifest_sha256"]:
            raise ValueError("Wiki grid manifest drift")
        manifest = json.loads(manifest_path.read_text())
        if manifest["source_manifest_sha256"] != chunk["source_manifest_sha256"]:
            raise ValueError("Wiki grid/source binding drift")
        valid_path = _pinned_child(
            manifest_path,
            "valid_grids.jsonl",
            manifest["files_sha256"]["valid_grids.jsonl"],
        )
        ledger_path = _pinned_child(
            manifest_path,
            "table_ledger.jsonl",
            manifest["files_sha256"]["table_ledger.jsonl"],
        )
        grids = _jsonl(valid_path)
        ledger = _jsonl(ledger_path)
        if len(grids) != chunk["valid_grids"]:
            raise ValueError("Wiki valid-grid count drift")
        opportunities = []
        for row in grids:
            source = sources.get(row["title"])
            if source is None or any(
                row[key] != source[key] for key in ("domain", "split", "revision_url")
            ):
                raise ValueError("Wiki grid is not bound to frozen source")
            html = source_records.get(row["title"])
            if html is None or any(
                row[key] != html[key]
                for key in ("html_sha256", "oldid", "revision_url", "split", "domain")
            ):
                raise ValueError("Wiki grid is not bound to frozen HTML")
            opportunities.append(_grid_opportunity(row, source, valid_path))
        rejects = [
            {
                "source_kind": "real_wiki_html",
                "source_title": row["title"],
                "source_group": sources[row["title"]]["source_group"],
                "domain": sources[row["title"]]["domain"],
                "topic": sources[row["title"]]["topic"],
                "split": sources[row["title"]]["split"],
                "source_unit": row["table_id"],
                "reason": reason,
            }
            for row in ledger
            if row["status"] != "grid_valid"
            for reason in row["reasons"]
        ]
        return opportunities, rejects

    with ThreadPoolExecutor(max_workers=workers) as executor:
        parts = list(executor.map(read_chunk, chunks))
    opportunities = [row for good, _ in parts for row in good]
    rejects = [row for _, bad in parts for row in bad]
    if len(opportunities) != campaign["valid_grids"]:
        raise ValueError("Wiki campaign valid-grid total drift")
    represented = {row["source_title"] for row in opportunities}
    for title, source in sources.items():
        if title not in represented:
            rejects.append(
                {
                    "source_kind": "real_wiki_html",
                    "source_title": title,
                    "source_group": source["source_group"],
                    "domain": source["domain"],
                    "topic": source["topic"],
                    "split": source["split"],
                    "source_unit": "page",
                    "reason": "no_parser_accepted_html_grid",
                }
            )
    return opportunities, rejects


def _finance(manifest_path: Path) -> tuple[list[dict], list[dict]]:
    manifest = json.loads(manifest_path.read_text())
    opportunities, rejects = [], []
    for job in manifest["jobs"]:
        issuer_dir = manifest_path.parent / job["issuer"]
        receipt = issuer_dir / "manifest.json"
        if _sha(receipt) != job["manifest_sha256"]:
            raise ValueError("finance issuer manifest drift")
        info = json.loads(receipt.read_text())
        matrix = _jsonl(
            _pinned_child(
                receipt,
                "support_matrix.jsonl",
                info["file_sha256"]["support_matrix.jsonl"],
            )
        )
        for row in matrix:
            if (
                row["source_group"] != info["source_group"]
                or row["split"] != info["split"]
                or row["source_manifest_sha256"] != info["source_manifest_sha256"]
            ):
                raise ValueError("finance source identity drift")
            base = {
                "source_kind": "real_finance",
                "source_group": row["source_group"],
                "domain": "finance",
                "topic": row["metric"],
                "split": row["split"],
                "source_unit": f"{row['issuer']}:{row['metric']}",
                "source_pins": {
                    "issuer_manifest": {
                        "path": str(receipt.relative_to(ROOT)),
                        "sha256": _sha(receipt),
                    }
                },
            }
            if row["status"] == "candidate_after_bounded_reader_check":
                opportunities.append(
                    {
                        **base,
                        "native_shape": "filing_metric_by_period",
                        "capacity": {
                            "source_documents": 4,
                            "numeric_support_windows": len(
                                row.get("alternative_support_scope", [])
                            ),
                        },
                        "typed_evidence": [
                            "metric_row",
                            "period",
                            "unit",
                            "report_section",
                            "bounded_numeric_alternatives",
                        ],
                        "operation_families": ["cross_report_complete_set_candidate"],
                        "skip_reasons": [],
                        "admission_status": "candidate in P115 bounded reader check; not a new task or unrestricted dependency proof",
                    }
                )
            else:
                rejects.append({**base, "reason": row.get("reason", row["status"])})
    return opportunities, rejects


def _paper(manifest_path: Path) -> tuple[list[dict], list[dict]]:
    manifest = json.loads(manifest_path.read_text())
    matrix = _jsonl(
        _pinned_child(
            manifest_path,
            "support_matrix.jsonl",
            manifest["files_sha256"]["support_matrix.jsonl"],
        )
    )
    opportunities, rejects = [], []
    for row in matrix:
        archive = _pin(
            row["source_archive"]
            if set(row["source_archive"]) == {"path", "sha256"}
            else {key: row["source_archive"][key] for key in ("path", "sha256")}
        )
        base = {
            "source_kind": "real_paper_tex",
            "source_group": row["source_group"],
            "domain": "research",
            "topic": row["work_id"],
            "split": row["split"],
            "source_unit": row["work_id"],
            "source_pins": {
                "archive": {
                    "path": str(archive.relative_to(ROOT)),
                    "sha256": _sha(archive),
                }
            },
        }
        if row["status"] == "source_parsed" and row["same_file_targets"]:
            opportunities.append(
                {
                    **base,
                    "native_shape": "same_file_tex_reference",
                    "capacity": {
                        "source_files": row["source_files"],
                        "source_chars": row["source_chars"],
                        "active_references": row["active_references"],
                        "same_file_targets": row["same_file_targets"],
                        "already_admitted_tasks": row["candidate_tasks"],
                    },
                    "typed_evidence": [
                        "active_reference_span",
                        "same_file_label_target",
                        "caption_or_heading",
                    ],
                    "operation_families": ["paper_reference_trace_candidate"],
                    "skip_reasons": [],
                    "admission_status": "source reference shape only; label leakage, alternatives, final token extent and rights still gated",
                }
            )
        else:
            rejects.append(
                {
                    **base,
                    "reason": row["status"]
                    if row["status"] != "source_parsed"
                    else "no_same_file_target",
                    "reason_counts": row.get("reason_counts", {}),
                }
            )
    return opportunities, rejects


def build(config_path: Path) -> dict[str, bytes]:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or type(config.get("workers")) is not int
        or not 1 <= config["workers"] <= 8
    ):
        raise ValueError("invalid opportunity config")
    paths = {
        key: _pin(config[key])
        for key in (
            "wiki_campaign",
            "wiki_pool",
            "finance_shape",
            "paper_reference",
            "capability_matrix",
        )
    }
    capability = json.loads(paths["capability_matrix"].read_text())
    if capability.get("schema") != "longworld.p138-capability-coverage.v2":
        raise ValueError("wrong capability matrix")
    wiki = _wiki(paths["wiki_campaign"], paths["wiki_pool"], config["workers"])
    finance = _finance(paths["finance_shape"])
    paper = _paper(paths["paper_reference"])
    opportunities = sorted(
        wiki[0] + finance[0] + paper[0],
        key=lambda x: (x["source_kind"], x["source_group"], x["source_unit"]),
    )
    rejects = sorted(
        wiki[1] + finance[1] + paper[1],
        key=lambda x: (
            x["source_kind"],
            x["source_group"],
            x["source_unit"],
            x["reason"],
        ),
    )
    if len(
        {(x["source_kind"], x["source_group"], x["source_unit"]) for x in opportunities}
    ) != len(opportunities):
        raise ValueError("opportunity source unit collision")
    existing = capability["selected"]["capability_source_length"]
    targets = [
        (
            "complete_set_scan",
            "real_wiki",
            "128k",
            "HTML tables are currently short; acquire longer linked source groups and prove complete row coverage before long compilation",
        ),
        (
            "complete_set_scan",
            "real_finance",
            "128k",
            "Reuse typed filing metric/period cells, expand independently frozen issuers, and test all exact numeric alternatives",
        ),
        (
            "paper_reference_trace",
            "real_paper_source",
            "128k",
            "Increase same-file references with nonleaking natural cues and final-token dual deletions; source shape alone is insufficient",
        ),
    ]
    proposal = [
        {
            "capability": cap,
            "source_kind": kind,
            "length_bin": length,
            "p139_selected_tasks": existing.get(cap, {})
            .get(kind, {})
            .get(length, {})
            .get("tasks", 0),
            "gate": gate,
        }
        for cap, kind, length, gate in targets
    ]
    by_kind = Counter(x["source_kind"] for x in opportunities)
    by_domain = Counter(x["domain"] for x in opportunities)
    by_topic = Counter(x["topic"] for x in opportunities)
    by_family = Counter(f for x in opportunities for f in x["operation_families"])
    by_kind_domain_family = Counter(
        f"{x['source_kind']}|{x['domain']}|{family}"
        for x in opportunities
        for family in x["operation_families"]
    )
    by_topic_family = Counter(
        f"{x['topic']}|{family}"
        for x in opportunities
        for family in x["operation_families"]
    )
    by_reason = Counter(x["reason"] for x in rejects)
    groups = defaultdict(set)
    for row in opportunities:
        groups[row["source_kind"]].add(row["source_group"])
    report = {
        "schema": SCHEMA + ".report",
        "code_sha256": _sha(Path(__file__)),
        "config_sha256": _sha(config_path),
        "input_manifest_pins": config,
        "source_units": len(opportunities),
        "source_groups_by_kind": {k: len(v) for k, v in sorted(groups.items())},
        "by_source_kind": dict(sorted(by_kind.items())),
        "by_domain": dict(sorted(by_domain.items())),
        "by_topic": dict(sorted(by_topic.items())),
        "operation_opportunities": dict(sorted(by_family.items())),
        "kind_domain_operation_opportunities": dict(
            sorted(by_kind_domain_family.items())
        ),
        "topic_operation_opportunities": dict(sorted(by_topic_family.items())),
        "rejection_reasons": dict(sorted(by_reason.items())),
        "rejection_ledger_rows": len(rejects),
        "batch_proposal": proposal,
        "claim_limit": "Source-shape opportunities and prior bounded candidates only; no new reader QA, gold, length, source rights, or necessary long dependency certified",
        "train_ready": False,
    }
    outputs = {
        "opportunities.jsonl": _lines(opportunities),
        "rejections.jsonl": _lines(rejects),
        "report.json": _encode(report),
    }
    outputs["manifest.json"] = _encode(
        {
            "schema": SCHEMA + ".manifest",
            "code_sha256": _sha(Path(__file__)),
            "config_sha256": _sha(config_path),
            "files_sha256": {
                name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()
            },
            "train_ready": False,
        }
    )
    return outputs


def run(config_path: Path, output: Path, verify_only: bool = False) -> dict:
    config_path = config_path if config_path.is_absolute() else ROOT / config_path
    output = output if output.is_absolute() else ROOT / output
    outputs = build(config_path)
    if verify_only:
        if not output.is_dir() or {p.name for p in output.iterdir()} != set(outputs):
            raise ValueError("opportunity inventory drift")
        for name, data in outputs.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"opportunity replay drift: {name}")
    else:
        if output.exists():
            raise ValueError("output already exists")
        output.mkdir(parents=True)
        for name, data in outputs.items():
            (output / name).write_bytes(data)
    return json.loads(outputs["report.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output, args.verify_only), sort_keys=True))


if __name__ == "__main__":
    main()
