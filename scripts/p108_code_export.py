"""Export and verify new CodeForge episodes with four bounded source workers."""

from __future__ import annotations

import argparse
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

SCHEMA = "longworld.p108-code-export.v1"


def _trusted(
    trust_file: Path,
    wrapper: Path,
    policy_sha256: str,
    client: Path,
    script: Path,
    argv: list[str],
    *,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        LONGWORLD_PUBLIC_POLICY_SHA256=policy_sha256,
        LONGWORLD_GH_BINARY=str(client),
        LONGWORLD_GH_BINARY_SHA256=_sha(client),
    )
    command = [
        sys.executable,
        str(wrapper),
        "--trust-file",
        str(trust_file),
        "--role",
        "source",
    ]
    for name in (
        "LONGWORLD_PUBLIC_POLICY_SHA256",
        "LONGWORLD_GH_BINARY",
        "LONGWORLD_GH_BINARY_SHA256",
    ):
        command.extend(("--pass-env", name))
    if env.get("GH_TOKEN"):
        command.extend(("--pass-env", "GH_TOKEN"))
    elif env.get("GITHUB_TOKEN"):
        command.extend(("--pass-env", "GITHUB_TOKEN"))
    if env.get("GH_CONFIG_DIR"):
        command.extend(("--pass-env", "GH_CONFIG_DIR"))
    command.extend(("--", sys.executable, str(script), *argv))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def _check(result: subprocess.CompletedProcess[str], action: str) -> dict:
    if result.returncode:
        message = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise ValueError(f"P108 {action} failed: {message[0][:240]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"P108 {action} omitted JSON receipt") from error


def _atomic_receipt(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(_dump(value))
    os.replace(temporary, path)


def _prefill_prior(
    prior_path: Path,
    output_dir: Path,
    probe: dict,
    *,
    verify_only: bool,
) -> int:
    """Reuse exact signed source bytes through hardlinks; workers reverify them."""
    prior = json.loads(prior_path.read_text())
    if prior.get("schema") != SCHEMA + ".result":
        raise ValueError("P108 prior source manifest invalid")
    selected = {
        row["repository"]: (row["split"], set(row["eligible_prs_in_priority_order"]))
        for row in probe["by_repository"]
    }
    count = 0
    for row in prior["by_repository"]:
        repo = row["repository"]
        if repo not in selected or row["split"] != selected[repo][0]:
            raise ValueError("P108 prior source repository/split differs")
        safe = repo.replace("/", "__")
        for receipt in row["attempts"]:
            number = receipt["pull_number"]
            if (
                number not in selected[repo][1]
                or receipt["repository"] != repo
                or not receipt["status"].startswith("frozen_")
            ):
                raise ValueError("P108 prior PR cannot enter expansion")
            old_receipt = prior_path.parent / "attempts" / safe / f"pr_{number}.json"
            old_source = prior_path.parent / "exports" / safe / f"pr_{number}.json"
            if (
                json.loads(old_receipt.read_text()) != receipt
                or _sha(old_source) != receipt["source"]["source_sha256"]
            ):
                raise ValueError("P108 prior signed episode/receipt drift")
            for old, new in (
                (old_receipt, output_dir / "attempts" / safe / f"pr_{number}.json"),
                (old_source, output_dir / "exports" / safe / f"pr_{number}.json"),
            ):
                if new.exists():
                    if _sha(new) != _sha(old):
                        raise ValueError("P108 prior hardlink target drift")
                elif verify_only:
                    raise ValueError("P108 prior frozen episode missing on verify-only")
                else:
                    new.parent.mkdir(parents=True, exist_ok=True)
                    os.link(old, new)
            count += 1
    return count


def _verify_episode(
    trust_file: Path,
    paths: dict[str, Path],
    policy_sha256: str,
    path: Path,
    repo: str,
    number: int,
) -> dict:
    result = _trusted(
        trust_file,
        paths["trust_wrapper"],
        policy_sha256,
        paths["source_client"],
        paths["verify_export"],
        ["--path", str(path), "--repository", repo, "--pull", str(number)],
        timeout=120,
    )
    summary = _check(result, "source verification")
    if (
        summary["source_sha256"] != _sha(path)
        or summary["public_policy_pin_matches"] is not True
        or summary["source_client_pin_matches"] is not True
    ):
        raise ValueError("P108 verified episode source/client/policy pins differ")
    return summary


def _verify_bundle(
    trust_file: Path,
    paths: dict[str, Path],
    policy_sha256: str,
    path: Path,
) -> dict:
    result = _trusted(
        trust_file,
        paths["trust_wrapper"],
        policy_sha256,
        paths["source_client"],
        paths["verify_bundle"],
        ["--bundle", str(path)],
        timeout=120,
    )
    summary = _check(result, "bundle verification")
    if summary["bundle_sha256"] != _sha(path):
        raise ValueError("P108 bundle source pin changed")
    return summary


def _worker(job: tuple) -> dict:
    (
        row,
        root,
        trust_file,
        paths,
        policy_sha256,
        allowlist,
        target,
        verify_only,
    ) = job
    repo = row["repository"]
    safe = repo.replace("/", "__")
    exports = root / "exports" / safe
    attempts_dir = root / "attempts" / safe
    exports.mkdir(parents=True, exist_ok=True)
    attempts = []
    usable = []
    for number in row["eligible_prs_in_priority_order"]:
        if len(usable) >= target:
            break
        final = exports / f"pr_{number}.json"
        receipt_path = attempts_dir / f"pr_{number}.json"
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text())
            if receipt["repository"] != repo or receipt["pull_number"] != number:
                raise ValueError("P108 cached export attempt identity differs")
            if receipt["status"].startswith("frozen_"):
                verified = _verify_episode(
                    trust_file, paths, policy_sha256, final, repo, number
                )
                if receipt["source"] != verified:
                    raise ValueError("P108 cached signed episode changed")
                if receipt["status"] == "frozen_usable":
                    usable.append(final)
            elif final.exists():
                raise ValueError("P108 rejected attempt unexpectedly has source file")
            attempts.append(receipt)
            continue
        if verify_only:
            raise ValueError("P108 export attempt missing on verify-only")
        if final.exists():
            verified = _verify_episode(
                trust_file, paths, policy_sha256, final, repo, number
            )
            status = (
                "frozen_usable"
                if verified["merge_head_diff_paths"] > 0
                else "frozen_no_patch"
            )
            receipt = {
                "repository": repo,
                "pull_number": number,
                "status": status,
                "source": verified,
            }
        else:
            with tempfile.TemporaryDirectory(
                prefix=".p108_export_", dir=exports
            ) as temp:
                staged = Path(temp) / "episode.json"
                command = _trusted(
                    trust_file,
                    paths["trust_wrapper"],
                    policy_sha256,
                    paths["source_client"],
                    paths["exporter"],
                    [
                        "--allowlist",
                        str(allowlist),
                        "--repo",
                        repo,
                        "--pull",
                        str(number),
                        "--out",
                        str(staged),
                    ],
                )
                if command.returncode:
                    message = command.stderr.strip().splitlines()[-1:] or [
                        "unknown error"
                    ]
                    reason = message[0][:240]
                    blocked = "rate limit" in reason.lower() or "HTTP 429" in reason
                    receipt = {
                        "repository": repo,
                        "pull_number": number,
                        "status": "transport_blocked" if blocked else "export_rejected",
                        "reason": reason,
                    }
                else:
                    try:
                        verified = _verify_episode(
                            trust_file, paths, policy_sha256, staged, repo, number
                        )
                        if verified["license"] != row["license_from_allowlist"]:
                            raise ValueError("P108 base license differs from allowlist")
                        os.replace(staged, final)
                        status = (
                            "frozen_usable"
                            if verified["merge_head_diff_paths"] > 0
                            else "frozen_no_patch"
                        )
                        receipt = {
                            "repository": repo,
                            "pull_number": number,
                            "status": status,
                            "source": verified,
                        }
                    except (OSError, ValueError) as error:
                        receipt = {
                            "repository": repo,
                            "pull_number": number,
                            "status": "verification_rejected",
                            "reason": f"{type(error).__name__}:{error}"[:240],
                        }
        _atomic_receipt(receipt_path, receipt)
        attempts.append(receipt)
        if receipt["status"] == "frozen_usable":
            usable.append(final)
        if receipt["status"] == "transport_blocked":
            break
    bundle_path = root / "bundles" / f"{safe}.json"
    bundle = None
    bundle_status = "insufficient_episodes"
    if len(usable) >= 2:
        if bundle_path.exists():
            bundle = _verify_bundle(trust_file, paths, policy_sha256, bundle_path)
        elif not verify_only:
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            argv = ["--bundle", str(bundle_path), "--create"]
            for source in usable:
                argv.extend(("--episode", str(source)))
            _check(
                _trusted(
                    trust_file,
                    paths["trust_wrapper"],
                    policy_sha256,
                    paths["source_client"],
                    paths["bundle_builder"],
                    argv,
                    timeout=180,
                ),
                "bundle creation",
            )
            bundle = _verify_bundle(trust_file, paths, policy_sha256, bundle_path)
        else:
            raise ValueError("P108 source bundle missing on verify-only")
        if bundle["episode_count"] != len(usable) or bundle["repository_url"] != (
            "https://github.com/" + repo
        ):
            raise ValueError("P108 signed bundle does not match frozen episodes")
        bundle_status = "signed_source_bundle"
    elif bundle_path.exists():
        raise ValueError("P108 source bundle exists without two usable episodes")
    return {
        "repository": repo,
        "split": row["split"],
        "eligible_prs": len(row["eligible_prs_in_priority_order"]),
        "attempts": attempts,
        "usable_episodes": len(usable),
        "bundle_status": bundle_status,
        "bundle": bundle,
        "bundle_path": str(bundle_path.relative_to(ROOT)) if bundle else None,
    }


def _config(config_path: Path) -> tuple[dict, dict, dict, dict, dict[str, Path]]:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("local_probe_only") is not True
    ):
        raise ValueError("P108 export config invalid")
    acquisition = json.loads(_pin(config["acquisition_config"]).read_text())
    probe_path = _pin(config["probe_manifest"])
    probe = json.loads(probe_path.read_text())
    catalog_config = json.loads(_pin(acquisition["catalog_config"]).read_text())
    catalog = json.loads(_pin(acquisition["catalog_manifest"]).read_text())
    if (
        acquisition.get("schema") != "longworld.p108-code-acquisition.v1.config"
        or probe.get("schema") != "longworld.p108-code-probe.v1.result"
        or probe["config_sha256"] != config["acquisition_config"]["sha256"]
        or probe["catalog_manifest_sha256"] != acquisition["catalog_manifest"]["sha256"]
        or catalog.get("status") != "complete"
        or len(probe["by_repository"]) != len(catalog["selected_repositories"])
        or acquisition.get("workers") != 4
    ):
        raise ValueError("P108 export source/probe chain differs")
    paths = {
        name: _pin(pin)
        for name, pin in {
            "trust_wrapper": config["trust_wrapper"],
            "verify_bundle": config["verify_bundle"],
            "source_client": catalog_config["source_client"],
            "exporter": acquisition["exporter"],
            "verify_export": config["verify_export_v2"],
            "bundle_builder": acquisition["bundle_builder"],
        }.items()
    }
    _pin(acquisition["verify_export"])
    if "prior_failure_ledger" in config:
        previous = json.loads(_pin(config["prior_failure_ledger"]).read_text())
        if previous.get(
            "schema"
        ) != "longworld.p108-code-export.v1.initial-verifier-failures" or previous.get(
            "count"
        ) != len(previous.get("attempts", [])):
            raise ValueError("P108 prior verifier failure ledger changed")
        for attempt in previous["attempts"]:
            receipt = json.loads(
                _pin({key: attempt[key] for key in ("path", "sha256")}).read_text()
            )
            if (
                receipt.get("repository") != attempt["repository"]
                or receipt.get("pull_number") != attempt["pull_number"]
                or receipt.get("status") != attempt["status"]
            ):
                raise ValueError("P108 prior failure receipt changed")
    if "prior_source_manifest" in config:
        prior_source = json.loads(_pin(config["prior_source_manifest"]).read_text())
        if (
            prior_source.get("schema") != SCHEMA + ".result"
            or prior_source.get("train_ready") is not False
        ):
            raise ValueError("P108 prior signed source manifest invalid")
    allowlist = _pin(catalog_config["allowlist"])
    return config, acquisition, probe, catalog, {**paths, "allowlist": allowlist}


def run(
    config_path: Path,
    output_dir: Path,
    trust_file: Path,
    *,
    verify_only: bool = False,
    resume: bool = False,
) -> dict:
    config, acquisition, probe, catalog, paths = _config(config_path)
    if not trust_file.is_absolute() or not trust_file.is_file():
        raise ValueError("P108 source-role trust file missing")
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    with _lock(output_dir):
        state_path = output_dir / "export_state.json"
        state = {
            "schema": SCHEMA + ".state",
            "config_sha256": _sha(config_path),
            "probe_manifest_sha256": config["probe_manifest"]["sha256"],
            "prior_failure_ledger_sha256": config.get("prior_failure_ledger", {}).get(
                "sha256"
            ),
        }
        if "prior_source_manifest" in config:
            state["prior_source_manifest_sha256"] = config["prior_source_manifest"][
                "sha256"
            ]
        if state_path.is_file():
            if (
                not (resume or verify_only)
                or json.loads(state_path.read_text()) != state
            ):
                raise ValueError("P108 export resume state changed")
        elif verify_only or resume:
            raise ValueError("P108 export state missing")
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            state_path.write_text(_dump(state))
        reused = (
            _prefill_prior(
                ROOT / config["prior_source_manifest"]["path"],
                output_dir,
                probe,
                verify_only=verify_only,
            )
            if "prior_source_manifest" in config
            else 0
        )
        jobs = [
            (
                {
                    **row,
                    "license_from_allowlist": next(
                        item["license_from_allowlist"]
                        for item in catalog["selected_repositories"]
                        if item["repository"] == row["repository"]
                    ),
                },
                output_dir,
                trust_file,
                paths,
                _sha(paths["allowlist"]),
                paths["allowlist"],
                acquisition["target_frozen_episodes_per_repository"],
                verify_only,
            )
            for row in probe["by_repository"]
        ]
        with ProcessPoolExecutor(max_workers=acquisition["workers"]) as workers:
            results = list(workers.map(_worker, jobs))
        statuses = Counter(
            attempt["status"] for row in results for attempt in row["attempts"]
        )
        manifest = {
            "schema": SCHEMA + ".result",
            "config_sha256": _sha(config_path),
            "probe_manifest_sha256": config["probe_manifest"]["sha256"],
            "prior_failure_ledger_sha256": state["prior_failure_ledger_sha256"],
            "workers": acquisition["workers"],
            "catalog_repositories": len(catalog["selected_repositories"]),
            "structurally_eligible_prs": probe["eligible_prs"],
            "attempted_prs": sum(len(row["attempts"]) for row in results),
            "frozen_usable_episodes": sum(row["usable_episodes"] for row in results),
            "signed_new_worlds": sum(
                row["bundle_status"] == "signed_source_bundle" for row in results
            ),
            "attempt_status_counts": dict(sorted(statuses.items())),
            "by_repository": results,
            "source_client_sha256": _sha(paths["source_client"]),
            "allowlist_sha256": _sha(paths["allowlist"]),
            "local_probe_only": True,
            "train_ready": False,
        }
        if "prior_source_manifest" in config:
            manifest["prior_source_manifest_sha256"] = state[
                "prior_source_manifest_sha256"
            ]
            manifest["reused_frozen_attempts"] = reused
            manifest["new_attempts"] = manifest["attempted_prs"] - reused
        path = output_dir / "manifest.json"
        content = _dump(manifest)
        if verify_only:
            if path.read_text() != content:
                raise ValueError("P108 signed source export replay differs")
        else:
            _atomic_receipt(path, manifest)
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trust-file", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = run(
        args.config,
        args.output_dir,
        args.trust_file,
        verify_only=args.verify_only,
        resume=args.resume,
    )
    print(
        _dump({key: value for key, value in result.items() if key != "by_repository"})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
