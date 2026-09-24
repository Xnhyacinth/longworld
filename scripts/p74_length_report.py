"""P74 T5 length report: band distribution, calibration error, infeasible cells.

CLI over a set of inputs — snapshot/world JSON files (anything the §14
SourceSnapshot loader accepts becomes a SemanticWorld and is measured on its
full natural render; anything with documents[].text but not loadable as a
snapshot is measured as concatenated document text, with the bridge
limitation noted) — plus, by default, the demo world and the estimator's
calibration set. Output: JSON file + stdout tables.

Usage:
  .venv/bin/python scripts/p74_length_report.py \
      [--inputs PATH ...] [--no-demo] [--no-calibration] \
      [--bands 8192,32768,...] [--json OUT.json]

Run with no --inputs: the demo world plus the real wiki snapshot under
data/capability_records/p74_wiki_snapshot_v1/ (text-concat path).

Deterministic; the pinned tokenizer (Qwen/Qwen3.5-4B @ a7b0d22b...) is the
only measurement instrument.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import length_controller as lc
from longworld.synthesis import shared_semantic_world as ssw

DEFAULT_WIKI_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "capability_records"
    / "p74_wiki_snapshot_v1"
)


def _iter_json_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.glob("*.json") if "freeze-log" not in p.name)
    return [path] if path.exists() else []


def load_input(
    path: Path,
) -> tuple[dict[str, Any], "ssw.SemanticWorld | str | None"]:
    """One report input: a snapshot-backed world, a text-concat, or unusable.

    Returns (row-without-tokens, measurable). The row's "kind" records which
    path was taken; the text-concat path exists because the wiki->world
    bridge (W2-A) may not have landed when this report runs — the documents'
    verbatim text is measured instead, and the row says so.
    """
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return ({"file": str(path), "kind": "unreadable", "error": str(exc)}, None)
    if not isinstance(payload, dict):
        return (
            {"file": str(path), "kind": "unreadable", "error": "not a JSON object"},
            None,
        )

    try:
        snapshot = ssw.SourceSnapshot.from_dict(payload)
    except ValueError:
        snapshot = None
    if snapshot is not None:
        world = ssw.SemanticWorld.from_snapshot(snapshot)
        return (
            {
                "file": str(path),
                "kind": "semantic-world",
                "snapshot_id": snapshot.snapshot_id,
                "structure": {
                    "documents": len(snapshot.documents),
                    "entities": len(snapshot.entities),
                    "facts": len(snapshot.facts),
                },
            },
            world,
        )

    docs = payload.get("documents")
    if (
        isinstance(docs, list)
        and docs
        and all(isinstance(d, dict) and isinstance(d.get("text"), str) for d in docs)
    ):
        text = "\n\n".join(d["text"] for d in docs)
        return (
            {
                "file": str(path),
                "kind": "text-concat",
                "note": (
                    "snapshot not loadable under the §14 v1 schema (value_type/"
                    "entity drift on real wiki data); documents[].text concatenated "
                    "verbatim — the W2-A wiki->world bridge render is not used "
                    "because it had not landed when this input was measured"
                ),
                "structure": {
                    "documents": len(docs),
                    "entities": len(payload.get("entities", [])),
                    "facts": len(payload.get("facts", [])),
                },
            },
            text,
        )
    # a directory-level manifest (documents list present but empty, or the
    # longworld.wiki-snapshot-manifest schema) is expected in snapshot dirs:
    # not a world, not an error
    if (isinstance(docs, list) and not docs) or "snapshots" in payload:
        return (
            {
                "file": str(path),
                "kind": "manifest",
                "note": "no documents; not a world",
            },
            None,
        )
    return (
        {
            "file": str(path),
            "kind": "unreadable",
            "error": "neither a SourceSnapshot nor documents[].text",
        },
        None,
    )


def _measure_row(row: dict[str, Any], measurable: "ssw.SemanticWorld | str") -> None:
    """Fill one row's natural length, window budget and estimate columns."""
    tokens = lc.measure(measurable)
    row["natural_tokens"] = tokens
    budget = lc.window_budget(tokens)
    row["window_budget"] = budget.to_dict()
    if isinstance(measurable, ssw.SemanticWorld):
        row["estimated_tokens"] = lc.estimate_capacity(measurable)
        row["estimate_error_pct"] = round(
            lc.calibration_error(measurable, tokens) * 100, 2
        )
    else:
        row["estimated_tokens"] = None
        row["estimate_error_pct"] = None
        row["estimate_note"] = (
            "estimator constants are calibrated on semantic-world renders; "
            "no estimate is claimed for raw text input (docs-section rate "
            "under-predicts real wiki prose by ~15%)"
        )


def build_report(
    input_paths: list[Path],
    *,
    include_demo: bool,
    include_calibration: bool,
    bands: list[int],
) -> dict[str, Any]:
    from scripts.demo_p74_world import build_demo_world

    rows: list[dict[str, Any]] = []
    measurements: list[tuple[str, "ssw.SemanticWorld | str"]] = []

    if include_demo:
        demo = build_demo_world()
        row = {
            "file": "scripts/demo_p74_world.py",
            "kind": "semantic-world",
            "name": "demo-world",
            "structure": {
                "documents": len(demo.documents),
                "entities": len(demo.entities),
                "facts": len(demo.facts),
            },
        }
        _measure_row(row, demo)
        rows.append(row)
        measurements.append(("demo-world", demo))

    for path in input_paths:
        row, measurable = load_input(path)
        if measurable is None:
            rows.append(row)
            continue
        row["name"] = row.get("name") or path.stem
        _measure_row(row, measurable)
        rows.append(row)
        measurements.append((row["name"], measurable))

    # --- band selection: every band visible, empty cells included ---
    selection = lc.select_views(measurements, bands=bands)

    # --- calibration (declared, honest) ---
    calibration = None
    if include_calibration:
        worlds = lc.calibration_worlds(build_demo_world())
        calibration = lc.calibration_report(worlds)

    infeasible = [row for row in selection.world_rows if not row["feasible"]]

    return {
        "script": "scripts/p74_length_report.py",
        "tokenizer": {"model": lc.TOKENIZER_MODEL, "revision": lc.TOKENIZER_REVISION},
        "band_tolerance": lc.BAND_TOLERANCE,
        "window_defaults": {
            "model_window": lc.DEFAULT_MODEL_WINDOW,
            "system": lc.DEFAULT_SYSTEM_TOKENS,
            "query": lc.DEFAULT_QUERY_TOKENS,
            "answer_reserve": lc.DEFAULT_ANSWER_RESERVE,
            "template": lc.DEFAULT_TEMPLATE_TOKENS,
            "note": "256K of input text != a 256K model window: system + query + "
            "answer reserve + template overhead live inside the same window",
        },
        "bands": list(selection.bands),
        "inputs": rows,
        "band_cells": [cell.to_dict() for cell in selection.cells],
        "band_assignment": [dict(row) for row in selection.world_rows],
        "infeasible_cells": infeasible,
        "calibration": calibration,
        "notes": [
            "bands are never dropped: an empty band appears as an empty cell",
            "no K/H reduction and no padding strings anywhere: short worlds stay "
            "short and are recorded infeasible-with-capacity / needs-expansion",
            "three legal lengthening paths are labeled per charter §7 "
            "(dense-integration / distance-distractor / state-graph)",
        ],
    }


def _print_table(lines: list[tuple[str, ...]], out: list[str]) -> None:
    widths = [max(len(row[i]) for row in lines) for i in range(len(lines[0]))]
    for row in lines:
        out.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())


def render_stdout(report: dict[str, Any]) -> str:
    out: list[str] = []
    out.append("P74 T5 length report")
    out.append(
        f"tokenizer: {report['tokenizer']['model']} @ {report['tokenizer']['revision']}"
    )
    out.append(
        f"band tolerance ±{int(report['band_tolerance'] * 100)}%; model window "
        f"{report['window_defaults']['model_window']}"
    )
    out.append("")

    out.append("Inputs (natural length, exact measurement):")
    table = [("name", "kind", "tokens", "est", "err%", "window fits", "slack")]
    for row in report["inputs"]:
        if "natural_tokens" not in row:
            fallback = Path(row["file"]).name if "file" in row else "?"
            table.append(
                (row.get("name", fallback), row["kind"], "-", "-", "-", "-", "-")
            )
            continue
        budget = row["window_budget"]
        table.append(
            (
                row["name"],
                row["kind"],
                str(row["natural_tokens"]),
                str(
                    row["estimated_tokens"]
                    if row["estimated_tokens"] is not None
                    else "-"
                ),
                str(
                    row["estimate_error_pct"]
                    if row["estimate_error_pct"] is not None
                    else "-"
                ),
                "yes" if budget["fits"] else "NO",
                str(budget["slack"]),
            )
        )
    _print_table(table, out)
    out.append("")

    out.append("Band distribution (empty bands stay visible):")
    table = [
        (
            "band",
            "assigned",
            "success",
            "pending-expansion",
            "failure",
            "infeasible",
            "empty",
        )
    ]
    for cell in report["band_cells"]:
        table.append(
            (
                str(cell["band"]),
                str(len(cell["assigned"])),
                str(cell["success"]),
                str(cell["pending_expansion"]),
                str(cell["failure"]),
                str(cell["infeasible"]),
                "EMPTY" if cell["empty"] else "",
            )
        )
    _print_table(table, out)
    out.append("")

    infeasible = report["infeasible_cells"]
    out.append(f"Infeasible / needs-expansion cells ({len(infeasible)}):")
    for row in infeasible:
        paths = "; ".join(
            f"{p['path']}(+{p['items_to_band']} items ~{p['per_item_tokens']} tok/item)"
            for p in row["recommended_paths"]
        )
        out.append(
            f"  {row['name']}: band {row['band']} {row['verdict']} "
            f"(natural {row['natural_tokens']}, gap {row['gap_tokens']})"
            + (f" — {paths}" if paths else "")
        )
    out.append("")

    calibration = report["calibration"]
    if calibration is not None:
        out.append(
            "Estimator calibration (predicted vs measured, semantic-world renders):"
        )
        table = [
            ("world", "entities", "facts", "docs", "measured", "predicted", "err%")
        ]
        for row in calibration["rows"]:
            table.append(
                (
                    row["name"],
                    str(row["structure"]["entities"]),
                    str(row["structure"]["facts"]),
                    str(row["structure"]["documents"]),
                    str(row["measured_tokens"]),
                    str(row["predicted_tokens"]),
                    str(row["error_pct"]),
                )
            )
        _print_table(table, out)
        out.append(
            f"  max abs error {calibration['max_abs_error_pct']}% vs declared "
            f"tolerance {calibration['declared_tolerance_pct']}% — "
            + ("within" if calibration["within_tolerance"] else "OUTSIDE")
        )
    out.append("")
    for note in report["notes"]:
        out.append(f"note: {note}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs",
        nargs="*",
        type=Path,
        default=None,
        help="snapshot/world JSON files or directories (default: wiki snapshot dir)",
    )
    parser.add_argument("--no-demo", action="store_true", help="skip the demo world")
    parser.add_argument(
        "--no-calibration", action="store_true", help="skip the calibration table"
    )
    parser.add_argument(
        "--bands",
        type=str,
        default=None,
        help="comma-separated band token counts (default: 8K/32K/64K/128K/256K + 48K/96K)",
    )
    parser.add_argument(
        "--json", type=Path, default=None, help="write the JSON report here"
    )
    args = parser.parse_args(argv)

    inputs: list[Path] = []
    if args.inputs:
        for path in args.inputs:
            inputs.extend(_iter_json_files(path))
    else:
        if DEFAULT_WIKI_DIR.is_dir():
            inputs.extend(_iter_json_files(DEFAULT_WIKI_DIR))
        else:
            print(f"note: default wiki dir {DEFAULT_WIKI_DIR} not found; demo only")

    bands = (
        [int(b) for b in args.bands.split(",")]
        if args.bands
        else list(lc.DEFAULT_BANDS)
    )

    report = build_report(
        inputs,
        include_demo=not args.no_demo,
        include_calibration=not args.no_calibration,
        bands=bands,
    )
    text = render_stdout(report)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"\nJSON report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
