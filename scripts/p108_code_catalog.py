"""Freeze a bounded merged-PR catalog from new allowlisted CodeForge repos."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.export_github_workflow import CANONICAL_ALLOWLIST
from scripts.p108_code_gh_client import MIN_INTERVAL_SECONDS

SCHEMA = "longworld.p108-code-catalog.v1"
SUPPORTED_LICENSES = {"MIT", "Apache-2.0"}


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pin(value: dict) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("P108 catalog pin requires path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P108 catalog pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != value["sha256"]:
        raise ValueError(f"P108 catalog pin drift: {relative}")
    return path


def _dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _lock(output_dir: Path):
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    path = output_dir.parent / f".{output_dir.name}.lock"
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("P108 catalog already has a writer") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _config(path: Path) -> tuple[dict, list[dict], list[dict], list[dict]]:
    value = json.loads(path.read_text())
    if (
        value.get("schema") != SCHEMA + ".config"
        or value.get("local_probe_only") is not True
        or type(value.get("max_new_repositories")) is not int
        or not 1 <= value["max_new_repositories"] <= 16
        or type(value.get("page_size")) is not int
        or not 1 <= value["page_size"] <= 100
        or not 2 <= value.get("max_selected_prs_per_repository", 0) <= 24
        or not 2 <= value.get("target_frozen_episodes_per_repository", 0) <= 12
        or value["target_frozen_episodes_per_repository"]
        > value["max_selected_prs_per_repository"]
        or value.get("minimum_request_interval_seconds") != MIN_INTERVAL_SECONDS
    ):
        raise ValueError("P108 catalog config exceeds bounded source plan")
    allowlist_path = _pin(value["allowlist"])
    if allowlist_path.resolve() != CANONICAL_ALLOWLIST.resolve():
        raise ValueError("P108 requires the canonical repository allowlist")
    allowlist = yaml.safe_load(allowlist_path.read_text())
    if allowlist.get("schema_version") != "longworld.repo-allowlist.v1":
        raise ValueError("P108 allowlist schema mismatch")
    prior = json.loads(_pin(value["prior_code_banks"]).read_text())
    if prior.get("schema_version") != "longworld.p99-code-content.v1":
        raise ValueError("P108 prior CodeForge bank config mismatch")
    prior_repos = {
        job["source_group_id"].removeprefix("https://github.com/")
        for job in prior["banks"]
    }
    if len(prior_repos) != len(prior["banks"]):
        raise ValueError("P108 prior repository repeats")
    deno = yaml.safe_load(_pin(value["prior_deno_split"]).read_text())
    if (
        deno.get("train_ratio") != 1.0
        or "deno" not in deno.get("real_workflow_bundle", "")
        or value.get("existing_split_reservations") != {"denoland/deno": "train"}
    ):
        raise ValueError("P108 historical Deno split reservation changed")
    client_path = _pin(value["source_client"])
    if not os.access(client_path, os.X_OK):
        raise ValueError("P108 pinned GitHub client is not executable")
    if "prior_catalog_manifest" in value:
        prior_path = _pin(value["prior_catalog_manifest"])
        prior_catalog = json.loads(prior_path.read_text())
        if (
            prior_catalog.get("schema") != SCHEMA + ".result"
            or prior_catalog.get("status") != "complete"
            or prior_catalog.get("source_client_sha256")
            != value["source_client"]["sha256"]
        ):
            raise ValueError("P108 prior catalog is not a completed pinned cohort")
    new_repos, unsupported = [], []
    for repo, policy in sorted(allowlist["repositories"].items()):
        if repo in prior_repos:
            continue
        if policy.get("visibility") != "public":
            unsupported.append({"repository": repo, "reason": "not_public"})
        elif policy.get("license") not in SUPPORTED_LICENSES:
            unsupported.append(
                {
                    "repository": repo,
                    "license": policy.get("license"),
                    "reason": "exporter_license_parser_unsupported",
                }
            )
        else:
            residue = (
                int(
                    hashlib.sha256((value["split_seed"] + repo).encode()).hexdigest(),
                    16,
                )
                % 10
            )
            split = "eval" if residue in {0, 1} else "train"
            split = value["existing_split_reservations"].get(repo, split)
            new_repos.append(
                {
                    "repository": repo,
                    "license_from_allowlist": policy["license"],
                    "split": split,
                }
            )
    active = new_repos[: value["max_new_repositories"]]
    deferred = [
        {**row, "reason": "repository_budget"}
        for row in new_repos[value["max_new_repositories"] :]
    ]
    return value, active, unsupported, deferred


def _endpoint(repo: str, page_size: int) -> str:
    return (
        f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc"
        f"&per_page={page_size}&page=1"
    )


def _fetch(client: Path, endpoint: str) -> bytes:
    result = subprocess.run(
        [str(client), "api", "--hostname", "github.com", endpoint],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode or not result.stdout or len(result.stdout) > 8_000_000:
        raise ValueError(
            f"P108 GitHub catalog request failed: exit={result.returncode}; "
            f"stderr={result.stderr[:180].decode(errors='replace')}"
        )
    return result.stdout


def _page(
    output_dir: Path, ordinal: int, repo: str, endpoint: str
) -> tuple[dict, list]:
    directory = output_dir / "pages" / f"repo_{ordinal:02d}"
    raw = (directory / "response.json").read_bytes()
    receipt = json.loads((directory / "receipt.json").read_text())
    if (
        {path.name for path in directory.iterdir()} != {"response.json", "receipt.json"}
        or receipt["repository"] != repo
        or receipt["endpoint"] != endpoint
        or receipt["sha256"] != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError("P108 cached PR page changed")
    rows = json.loads(raw)
    if not isinstance(rows, list) or receipt["returned"] != len(rows):
        raise ValueError("P108 cached PR page shape changed")
    return receipt, rows


def _save_page(
    output_dir: Path, ordinal: int, repo: str, endpoint: str, raw: bytes
) -> tuple[dict, list]:
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise TypeError("P108 GitHub PR page is not a list")
    receipt = {
        "repository": repo,
        "endpoint": endpoint,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "returned": len(rows),
        "retrieved_at": _now(),
    }
    directory = output_dir / "pages" / f"repo_{ordinal:02d}"
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".p108_page_", dir=directory.parent
    ) as temp:
        staging = Path(temp) / "page"
        staging.mkdir()
        (staging / "response.json").write_bytes(raw)
        (staging / "receipt.json").write_text(_dump(receipt))
        os.rename(staging, directory)
    return receipt, rows


def _select(rows: list[dict], limit: int) -> tuple[list[dict], dict]:
    selected = []
    seen = set()
    rejected = {"not_merged": 0, "duplicate_pr": 0, "malformed": 0}
    for item in rows:
        number = item.get("number") if isinstance(item, dict) else None
        if type(number) is not int or number <= 0:
            rejected["malformed"] += 1
            continue
        if number in seen:
            rejected["duplicate_pr"] += 1
            continue
        seen.add(number)
        if not item.get("merged_at") or not item.get("merge_commit_sha"):
            rejected["not_merged"] += 1
            continue
        selected.append(
            {
                "number": number,
                "merged_at": item["merged_at"],
                "merge_commit_sha": item["merge_commit_sha"],
            }
        )
        if len(selected) >= limit:
            break
    return selected, rejected


def run(config_path: Path, output_dir: Path, *, resume: bool = False) -> dict:
    config, repos, unsupported, deferred = _config(config_path)
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    prior_path = (
        _pin(config["prior_catalog_manifest"])
        if "prior_catalog_manifest" in config
        else None
    )
    with _lock(output_dir):
        lock = {"schema": SCHEMA + ".lock", "config_sha256": _sha(config_path)}
        if output_dir.exists():
            if not resume or json.loads((output_dir / "lock.json").read_text()) != lock:
                raise ValueError("P108 catalog resume lock changed")
        else:
            if resume:
                raise ValueError("P108 catalog output missing on resume")
            output_dir.mkdir(parents=True)
            (output_dir / "lock.json").write_text(_dump(lock))
        client = _pin(config["source_client"])
        selected = []
        pages = []
        errors = []
        source_dir = prior_path.parent if prior_path is not None else output_dir
        for ordinal, row in enumerate(repos):
            repo = row["repository"]
            endpoint = _endpoint(repo, config["page_size"])
            directory = source_dir / "pages" / f"repo_{ordinal:02d}"
            try:
                if prior_path is not None or directory.is_dir():
                    receipt, raw_rows = _page(source_dir, ordinal, repo, endpoint)
                else:
                    receipt, raw_rows = _save_page(
                        output_dir,
                        ordinal,
                        repo,
                        endpoint,
                        _fetch(client, endpoint),
                    )
            except (
                OSError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
                subprocess.TimeoutExpired,
            ) as error:
                errors.append(
                    {"repository": repo, "error": f"{type(error).__name__}:{error}"}
                )
                break
            picks, rejections = _select(
                raw_rows, config["max_selected_prs_per_repository"]
            )
            selected.append(
                {**row, "selected_prs": picks, "selection_rejections": rejections}
            )
            pages.append(receipt)
        result = {
            "schema": SCHEMA + ".result",
            "config_sha256": _sha(config_path),
            "status": "complete"
            if len(selected) == len(repos) and not errors
            else "metadata_failed",
            "allowlisted_repositories": len(repos)
            + len(deferred)
            + len(unsupported)
            + len(json.loads(_pin(config["prior_code_banks"]).read_text())["banks"]),
            "eligible_new_repositories": len(repos) + len(deferred),
            "new_supported_repositories": len(repos),
            "unsupported_new_repositories": unsupported,
            "deferred_new_repositories": deferred,
            "pages": pages,
            "source_page_catalog_manifest_sha256": (
                _sha(prior_path) if prior_path is not None else None
            ),
            "source_page_root": str(source_dir.relative_to(ROOT)),
            "selected_repositories": selected if not errors else [],
            "selected_prs": sum(len(row["selected_prs"]) for row in selected)
            if not errors
            else 0,
            "errors": errors,
            "minimum_request_interval_seconds": MIN_INTERVAL_SECONDS,
            "source_client_sha256": config["source_client"]["sha256"],
            "local_probe_only": True,
            "train_ready": False,
        }
        path = output_dir / "manifest.json"
        text = _dump(result)
        if not path.is_file() or path.read_text() != text:
            temporary = output_dir / ".manifest.tmp"
            temporary.write_text(text)
            os.replace(temporary, path)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = run(args.config, args.output_dir, resume=args.resume)
    print(
        _dump(
            {
                key: value
                for key, value in result.items()
                if key != "selected_repositories"
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
