"""Four-worker, hash-pinned PR shape probe before expensive workflow exports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p108_code_catalog import _dump, _lock, _pin, _sha

SCHEMA = "longworld.p108-code-probe.v1"


def _detail(
    client: str,
    directory: Path,
    repo: str,
    selected: dict,
    *,
    verify_only: bool,
    prior_packet: Path | None = None,
    prior_sha256: str | None = None,
) -> dict:
    number = selected["number"]
    endpoint = f"repos/{repo}/pulls/{number}"
    if prior_packet is not None and not prior_packet.is_dir():
        raise ValueError("P108 pinned prior PR detail disappeared")
    packet = (
        prior_packet
        if prior_packet is not None and prior_packet.is_dir()
        else directory / f"pr_{number}"
    )
    path = packet / "response.json"
    receipt_path = packet / "receipt.json"
    if packet.exists():
        if not packet.is_dir() or {item.name for item in packet.iterdir()} != {
            "response.json",
            "receipt.json",
        }:
            raise ValueError("P108 partial cached PR detail")
        raw = path.read_bytes()
        receipt = json.loads(receipt_path.read_text())
        if (
            receipt["endpoint"] != endpoint
            or receipt["sha256"] != hashlib.sha256(raw).hexdigest()
            or (prior_sha256 is not None and receipt["sha256"] != prior_sha256)
        ):
            raise ValueError("P108 cached PR detail drift")
    else:
        if verify_only:
            raise ValueError("P108 PR detail missing on verify-only")
        result = subprocess.run(
            [client, "api", "--hostname", "github.com", endpoint],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode or not result.stdout or len(result.stdout) > 2_000_000:
            raise ValueError(
                f"P108 detail transport failed: exit={result.returncode}; "
                f"stderr={result.stderr[:160].decode(errors='replace')}"
            )
        raw = result.stdout
        receipt = {"endpoint": endpoint, "sha256": hashlib.sha256(raw).hexdigest()}
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".p108_detail_", dir=directory) as temp:
            staged = Path(temp) / "packet"
            staged.mkdir()
            (staged / "response.json").write_bytes(raw)
            (staged / "receipt.json").write_text(_dump(receipt))
            os.rename(staged, packet)
    detail = json.loads(raw)
    if (
        not isinstance(detail, dict)
        or detail.get("number") != number
        or detail.get("merged_at") != selected["merged_at"]
        or detail.get("merge_commit_sha") != selected["merge_commit_sha"]
    ):
        raise ValueError("P108 PR detail identity or merge revision changed")
    metrics = {
        key: detail.get(key)
        for key in ("commits", "changed_files", "additions", "deletions")
    }
    if any(type(value) is not int or value < 0 for value in metrics.values()):
        reason = "missing_shape_metrics"
    elif not 1 <= metrics["commits"] <= 5:
        reason = "commit_count_outside_1_5"
    elif not 1 <= metrics["changed_files"] <= 50:
        reason = "changed_file_count_outside_1_50"
    elif not 20 <= metrics["additions"] <= 5000:
        reason = "added_line_count_outside_20_5000"
    else:
        reason = "eligible_source_shape"
    return {
        "repository": repo,
        "number": number,
        "endpoint": endpoint,
        "detail_sha256": receipt["sha256"],
        "merged_at": selected["merged_at"],
        **metrics,
        "status": reason,
    }


def _repository(job: tuple[str, dict, Path, bool, int, dict]) -> dict:
    client, row, output_dir, verify_only, target, prior_packets = job
    repo = row["repository"]
    directory = output_dir / "details" / repo.replace("/", "__")
    details = []
    reused = 0
    for selected in row["selected_prs"]:
        if sum(item["status"] == "eligible_source_shape" for item in details) >= target:
            break
        try:
            prior = prior_packets.get(selected["number"])
            details.append(
                _detail(
                    client,
                    directory,
                    repo,
                    selected,
                    verify_only=verify_only,
                    prior_packet=prior[0] if prior else None,
                    prior_sha256=prior[1] if prior else None,
                )
            )
            if prior is not None:
                reused += 1
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
            if selected["number"] in prior_packets:
                raise
            details.append(
                {
                    "repository": repo,
                    "number": selected["number"],
                    "status": "detail_failed",
                    "reason": f"{type(error).__name__}:{error}",
                }
            )
    eligible = sorted(
        (row for row in details if row["status"] == "eligible_source_shape"),
        key=lambda row: (-row["additions"], row["number"]),
    )
    result = {
        "repository": repo,
        "split": row["split"],
        "details": details,
        "eligible_prs_in_priority_order": [row["number"] for row in eligible],
    }
    if prior_packets:
        result["reused_prior_details"] = reused
        result["unprobed_pr_numbers"] = [
            item["number"] for item in row["selected_prs"][len(details) :]
        ]
    return result


def _build_unlocked(
    config_path: Path,
    output_dir: Path,
    *,
    verify_only: bool = False,
    resume: bool = False,
) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != "longworld.p108-code-acquisition.v1.config"
        or config.get("local_probe_only") is not True
        or not 1 <= config.get("workers", 0) <= 4
    ):
        raise ValueError("P108 acquisition config invalid")
    catalog_config = json.loads(_pin(config["catalog_config"]).read_text())
    catalog_path = _pin(config["catalog_manifest"])
    catalog = json.loads(catalog_path.read_text())
    if (
        catalog.get("schema") != "longworld.p108-code-catalog.v1.result"
        or catalog.get("status") != "complete"
        or catalog["config_sha256"] != config["catalog_config"]["sha256"]
        or catalog_config["source_client"]
        != config.get("source_client", catalog_config["source_client"])
    ):
        raise ValueError("P108 catalog is not a completed pinned cohort")
    client = _pin(catalog_config["source_client"])
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    prior_path = (
        _pin(config["prior_probe_manifest"])
        if "prior_probe_manifest" in config
        else None
    )
    prior_roots = (
        [
            prior_path.parent,
            *(_pin(pin).parent for pin in config.get("prior_probe_ancestors", [])),
        ]
        if prior_path is not None
        else []
    )
    prior_packets = {}
    if prior_path is not None:
        prior = json.loads(prior_path.read_text())
        if prior.get("schema") != SCHEMA + ".result" or prior.get(
            "repositories"
        ) != len(catalog["selected_repositories"]):
            raise ValueError("P108 prior detail probe is incomplete")
        for row in prior["by_repository"]:
            packets = {}
            for item in row["details"]:
                if "detail_sha256" not in item:
                    continue
                candidates = [
                    root
                    / "details"
                    / row["repository"].replace("/", "__")
                    / f"pr_{item['number']}"
                    for root in prior_roots
                ]
                found = next((path for path in candidates if path.is_dir()), None)
                if found is None:
                    raise ValueError("P108 pinned prior PR detail disappeared")
                packets[item["number"]] = (found, item["detail_sha256"])
            prior_packets[row["repository"]] = packets
    target = config.get(
        "target_structurally_eligible_per_repository",
        config["max_selected_prs_per_repository"],
    )
    if not 2 <= target <= config["max_selected_prs_per_repository"]:
        raise ValueError("P108 detail target exceeds candidate budget")
    if verify_only:
        if not output_dir.is_dir():
            raise ValueError("P108 probe output missing for verify-only")
    elif output_dir.exists():
        if not resume:
            raise ValueError("P108 probe output already exists")
        if json.loads((output_dir / "lock.json").read_text()) != {
            "schema": SCHEMA + ".lock",
            "config_sha256": _sha(config_path),
        }:
            raise ValueError("P108 probe resume lock changed")
    else:
        if resume:
            raise ValueError("P108 probe cannot resume missing output")
        output_dir.mkdir(parents=True)
        (output_dir / "lock.json").write_text(
            _dump({"schema": SCHEMA + ".lock", "config_sha256": _sha(config_path)})
        )
    with ProcessPoolExecutor(max_workers=config["workers"]) as workers:
        results = list(
            workers.map(
                _repository,
                [
                    (
                        str(client),
                        row,
                        output_dir,
                        verify_only,
                        target,
                        prior_packets.get(row["repository"], {}),
                    )
                    for row in catalog["selected_repositories"]
                ],
            )
        )
    result = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "catalog_manifest_sha256": _sha(catalog_path),
        "workers": config["workers"],
        "repositories": len(results),
        "probed_prs": sum(len(row["details"]) for row in results),
        "eligible_prs": sum(
            len(row["eligible_prs_in_priority_order"]) for row in results
        ),
        "status_counts": dict(
            sorted(
                Counter(
                    item["status"] for row in results for item in row["details"]
                ).items()
            )
        ),
        "by_repository": results,
        "local_probe_only": True,
        "train_ready": False,
    }
    if prior_path is not None:
        result["prior_probe_manifest_sha256"] = _sha(prior_path)
        result["reused_prior_details"] = sum(
            row["reused_prior_details"] for row in results
        )
        result["unprobed_candidate_prs"] = sum(
            len(row["unprobed_pr_numbers"]) for row in results
        )
    manifest_path = output_dir / "manifest.json"
    content = _dump(result)
    if verify_only:
        if manifest_path.read_text() != content:
            raise ValueError("P108 PR detail probe replay differs")
    else:
        temporary = output_dir / ".manifest.tmp"
        temporary.write_text(content)
        os.replace(temporary, manifest_path)
    return result


def build(
    config_path: Path,
    output_dir: Path,
    *,
    verify_only: bool = False,
    resume: bool = False,
) -> dict:
    absolute = output_dir if output_dir.is_absolute() else ROOT / output_dir
    with _lock(absolute):
        return _build_unlocked(
            config_path, absolute, verify_only=verify_only, resume=resume
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = build(
        args.config, args.output_dir, verify_only=args.verify_only, resume=args.resume
    )
    print(
        _dump({key: value for key, value in result.items() if key != "by_repository"})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
