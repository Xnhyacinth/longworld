"""Screen newly signed PR patches for P99-style added-code identifier capacity."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p99_code_content_tasks import TOKEN_PATTERN, parse_added_lines
from scripts.p108_code_catalog import _dump, _lock, _sha
from scripts.p108_code_export import _config, _verify_episode

SCHEMA = "longworld.p108-code-anchor-preflight.v1"


def _anchors(payload: dict, number: int) -> tuple[int, int]:
    records = {record["id"]: record for record in payload["records"]}
    merge = records[f"merge:{number}"]
    heads = [
        records[link]
        for link in merge["links"]
        if link in records and records[link]["kind"] == "commit"
    ]
    if len(heads) != 1:
        raise ValueError("P108 added-code preflight needs one merged head")
    text = heads[0]["text"]
    patch = parse_added_lines(text)
    names = [name.lower() for name in patch]
    tokens = {
        token
        for lines in patch.values()
        for line in lines
        for token in TOKEN_PATTERN.findall(line)
        if ("_" in token or any(char.isupper() for char in token[1:]))
        and all(token.lower() not in name for name in names)
        and text.count(token) == 1
    }
    return len(patch), len(tokens)


def run(
    export_config: Path,
    source_dir: Path,
    trust_file: Path,
    output_dir: Path,
    *,
    verify_only: bool = False,
) -> dict:
    config, _acquisition, _probe, _catalog, paths = _config(export_config)
    if "prior_source_manifest" not in config:
        raise ValueError("P108 anchor preflight requires a source expansion")
    source_dir = source_dir if source_dir.is_absolute() else ROOT / source_dir
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    current = json.loads((source_dir / "manifest.json").read_text())
    prior = json.loads((ROOT / config["prior_source_manifest"]["path"]).read_text())
    prior_keys = {
        (row["repository"], item["pull_number"])
        for row in prior["by_repository"]
        for item in row["attempts"]
    }
    rows = []
    policy_sha = _sha(paths["allowlist"])
    for source in current["by_repository"]:
        repo = source["repository"]
        safe = repo.replace("/", "__")
        for attempt in source["attempts"]:
            number = attempt["pull_number"]
            if (repo, number) in prior_keys:
                continue
            if not attempt["status"].startswith("frozen_"):
                rows.append(
                    {
                        "repository": repo,
                        "pull_number": number,
                        "status": "source_not_usable",
                    }
                )
                continue
            path = source_dir / "exports" / safe / f"pr_{number}.json"
            if (
                _verify_episode(trust_file, paths, policy_sha, path, repo, number)
                != attempt["source"]
            ):
                raise ValueError("P108 new signed source differs from manifest")
            path_count, anchor_count = _anchors(json.loads(path.read_text()), number)
            rows.append(
                {
                    "repository": repo,
                    "split": source["split"],
                    "pull_number": number,
                    "source_sha256": attempt["source"]["source_sha256"],
                    "patch_paths": path_count,
                    "unique_added_code_identifiers": anchor_count,
                    "status": (
                        "candidate_added_code_anchor"
                        if anchor_count
                        else "no_unique_added_code_anchor"
                    ),
                }
            )
    manifest = {
        "schema": SCHEMA,
        "export_manifest_sha256": _sha(source_dir / "manifest.json"),
        "prior_source_manifest_sha256": config["prior_source_manifest"]["sha256"],
        "new_attempts": len(rows),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "by_pr": rows,
        "proof_scope": "necessary per-PR syntactic anchor only; full reader and two-source P99 intervention still required",
        "train_ready": False,
    }
    with _lock(output_dir):
        path = output_dir / "manifest.json"
        content = _dump(manifest)
        if verify_only:
            if path.read_text() != content:
                raise ValueError("P108 anchor preflight replay differs")
        else:
            output_dir.mkdir(parents=True, exist_ok=False)
            path.write_text(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-config", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--trust-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(
        args.export_config,
        args.source_dir,
        args.trust_file,
        args.output_dir,
        verify_only=args.verify_only,
    )
    print(_dump({key: value for key, value in result.items() if key != "by_pr"}))


if __name__ == "__main__":
    main()
