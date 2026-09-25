"""Record every P99 content-proof outcome when a new source pool yields zero."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p99_code_content_tasks import _load_bank, _tokenizer, compile_bank
from scripts.p108_code_catalog import _dump, _lock, _sha

SCHEMA = "longworld.p108-code-content-support.v1"


def _worker(job: tuple) -> dict:
    bank, config = job
    world = _load_bank(bank)
    rows, rejected = compile_bank(
        world,
        _tokenizer(),
        max_tasks=config["max_tasks_per_repository"],
        min_span=config["min_evidence_span_tokens"],
        max_chat_tokens=config["max_chat_tokens"],
    )
    return {
        "source_group": bank["source_group_id"],
        "split": bank["split"],
        "accepted": len(rows),
        "rejected": rejected,
    }


def run(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema_version") != "longworld.p99-code-content.v1"
        or config.get("min_evidence_span_tokens") != 16384
        or config.get("max_chat_tokens") != 262144
        or not config.get("banks")
    ):
        raise ValueError("P108 content support config invalid")
    output = output if output.is_absolute() else ROOT / output
    with _lock(output):
        if verify_only:
            if not output.is_dir():
                raise ValueError("P108 content support output missing")
        else:
            output.mkdir(parents=True, exist_ok=False)
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(_worker, ((bank, config) for bank in config["banks"]))
            )
        rejects = [item for result in results for item in result["rejected"]]
        reasons = Counter(item["reason"] for item in rejects)
        rows = [
            {
                "source_group": result["source_group"],
                "split": result["split"],
                "accepted": result["accepted"],
                "rejected": len(result["rejected"]),
                "reasons": dict(
                    sorted(Counter(x["reason"] for x in result["rejected"]).items())
                ),
            }
            for result in results
        ]
        ledger = "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for item in rejects
        )
        ledger_path = output / "rejects.jsonl"
        if verify_only:
            if ledger_path.read_text() != ledger:
                raise ValueError("P108 content rejection replay differs")
        else:
            ledger_path.write_text(ledger)
        manifest = {
            "schema": SCHEMA,
            "config_sha256": _sha(config_path),
            "compiler_sha256": _sha(ROOT / "scripts/p99_code_content_tasks.py"),
            "bank_manifest_sha256": config["source_bank_manifest_sha256"],
            "source_groups": len(rows),
            "accepted": sum(row["accepted"] for row in rows),
            "rejected_scopes": len(rejects),
            "reject_reasons": dict(sorted(reasons.items())),
            "by_source": rows,
            "rejects_sha256": _sha(ledger_path),
            "train_ready": False,
        }
        manifest_path = output / "manifest.json"
        if verify_only:
            if manifest_path.read_text() != _dump(manifest):
                raise ValueError("P108 content support manifest replay differs")
        else:
            manifest_path.write_text(_dump(manifest))
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(args.config, args.output, verify_only=args.verify_only)
    print(_dump({key: value for key, value in result.items() if key != "by_source"}))


if __name__ == "__main__":
    main()
