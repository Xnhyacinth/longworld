#!/usr/bin/env python3
"""Export one attested public GitHub release episode without random joins."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    attach_attestation,
    attestation_key_from_env,
)
from longworld.core.realworkflow import (
    GIT_WORKFLOW_SCHEMA,
    approved_public_policy_digests,
)

FetchJSON = Callable[[str], Any]
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
SECRET_PATTERNS = (
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{20,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
MAX_INLINE_PATCH_FILES = 50
PUBLIC_SCANNER = "longworld-public-secret-patterns"
PUBLIC_SCANNER_REVISION = "v2"
CANONICAL_ALLOWLIST = ROOT / "configs" / "public_repo_allowlist.yaml"
PUBLIC_POLICY_SHA256_ENV = "LONGWORLD_PUBLIC_POLICY_SHA256"
GH_BINARY_ENV = "LONGWORLD_GH_BINARY"
GH_BINARY_SHA256_ENV = "LONGWORLD_GH_BINARY_SHA256"
GH_CHILD_ENV_ALLOWLIST = ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR")


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone: {value!r}")
    return parsed


def sanitize_public_text(text: str) -> tuple[str, list[str]]:
    """Redact incidental email PII and reject credential-shaped source text."""
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise ValueError("source text contains a credential-shaped secret")
    redactions = [match.group(0) for match in EMAIL_RE.finditer(text)]
    return EMAIL_RE.sub("[redacted-email]", text).strip(), ["email"] * len(redactions)


def _payload_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _payload_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _payload_strings(item)


def validate_sanitized_public_payload(payload: dict[str, Any]) -> None:
    """Scan the final serialized content, not only selected source text fields."""
    for value in _payload_strings(payload):
        if EMAIL_RE.search(value) or any(
            pattern.search(value) for pattern in SECRET_PATTERNS
        ):
            raise ValueError(
                "final public payload contains PII or a credential-shaped secret"
            )


def release_mentions_pull(text: str, pull_number: int) -> bool:
    """Accept explicit PR syntax while rejecting version and issue numbers."""
    if re.search(rf"/pull/{pull_number}(?!\d)", text, re.IGNORECASE):
        return True
    if re.search(
        rf"\b(?:pull\s+request|pr)\s*#?{pull_number}(?!\d)", text, re.IGNORECASE
    ):
        return True
    for match in re.finditer(rf"(?<![\w#])#{pull_number}(?!\d)", text):
        prefix = text[max(0, match.start() - 16) : match.start()]
        if not re.search(r"\b(?:issue|bug)\s*$", prefix, re.IGNORECASE):
            return True
    return False


def _detect_spdx(text: str) -> str:
    if re.search(r"Apache License(?:,?\s+Version)?\s+2\.0", text, re.IGNORECASE):
        return "Apache-2.0"
    if "Permission is hereby granted, free of charge" in text:
        return "MIT"
    raise ValueError("license at the pull base has no supported SPDX match")


def _fetch_pages(
    fetch_json: FetchJSON, endpoint: str, *, field: str | None = None
) -> list[Any]:
    rows: list[Any] = []
    expected_total: int | None = None
    page = 1
    while True:
        separator = "&" if "?" in endpoint else "?"
        payload = fetch_json(f"{endpoint}{separator}per_page=100&page={page}")
        if field is None:
            if not isinstance(payload, list):
                raise ValueError(f"paginated endpoint returned a non-list: {endpoint}")
            page_rows = payload
        else:
            if not isinstance(payload, dict) or not isinstance(
                payload.get(field), list
            ):
                raise ValueError(f"paginated endpoint is malformed: {endpoint}")
            if not isinstance(payload.get("total_count"), int):
                raise ValueError(f"paginated endpoint has no total_count: {endpoint}")
            expected_total = int(payload["total_count"])
            page_rows = payload[field]
        rows.extend(page_rows)
        if len(page_rows) < 100:
            break
        page += 1
    if expected_total is not None and len(rows) != expected_total:
        raise ValueError(f"pagination coverage mismatch for {endpoint}")
    return rows


def render_commit_files(files: list[dict[str, Any]]) -> str:
    """Keep small patches verbatim; summarize large mechanical diffs from API facts."""
    if len(files) <= MAX_INLINE_PATCH_FILES:
        return "\n\n".join(
            f"diff -- {entry.get('filename')}\n{entry.get('patch') or '[patch unavailable]'}"
            for entry in files
        )
    lines = [f"{len(files)} files changed (GitHub commit API summary):"]
    for entry in files:
        lines.append(
            f"{entry.get('filename')} status={entry.get('status') or 'unknown'} "
            f"additions={int(entry.get('additions') or 0)} "
            f"deletions={int(entry.get('deletions') or 0)} "
            f"changes={int(entry.get('changes') or 0)} sha={entry.get('sha') or 'unknown'}"
        )
    return "\n".join(lines)


def _github_client_receipt() -> dict[str, str]:
    configured = os.environ.get(GH_BINARY_ENV, "/usr/bin/gh")
    path = Path(configured)
    if (
        not path.is_absolute()
        or path.resolve() != path
        or not path.is_file()
        or not os.access(path, os.X_OK)
    ):
        raise ValueError("GitHub CLI must be a trusted absolute executable path")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = os.environ.get(GH_BINARY_SHA256_ENV, "").strip().lower()
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    if expected and expected != digest:
        raise ValueError("GitHub CLI digest does not match the trusted pin")
    if environment in {"probe", "production"} and not expected:
        raise ValueError(f"{GH_BINARY_SHA256_ENV} is required in release mode")
    return {"path": str(path), "sha256": digest}


def _gh_fetch(endpoint: str) -> Any:
    client = _github_client_receipt()
    child_env = {
        name: os.environ[name]
        for name in GH_CHILD_ENV_ALLOWLIST
        if os.environ.get(name)
    }
    if "GH_CONFIG_DIR" not in child_env:
        config_home = os.environ.get("XDG_CONFIG_HOME")
        home = os.environ.get("HOME")
        if config_home:
            child_env["GH_CONFIG_DIR"] = str(Path(config_home) / "gh")
        elif home:
            child_env["GH_CONFIG_DIR"] = str(Path(home) / ".config" / "gh")
    child_env.update(
        {
            "PATH": "/usr/bin:/bin",
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
        }
    )
    completed = subprocess.run(
        [client["path"], "api", "--hostname", "github.com", endpoint],
        check=True,
        capture_output=True,
        text=True,
        env=child_env,
    )
    return json.loads(completed.stdout)


def load_public_policy(
    path: Path, repository: str, *, _verified_raw: bytes | None = None
) -> dict[str, Any]:
    raw = _verified_raw if _verified_raw is not None else path.read_bytes()
    payload = yaml.safe_load(raw.decode("utf-8"))
    if payload.get("schema_version") != "longworld.repo-allowlist.v1":
        raise ValueError("unsupported repository allowlist")
    policy = (payload.get("repositories") or {}).get(repository)
    if policy is None:
        raise ValueError(f"repository is not allowlisted: {repository}")
    if not isinstance(policy, dict):
        raise TypeError(f"repository policy must be an object: {repository}")
    if policy.get("visibility") != "public":
        raise ValueError("this exporter only accepts public repositories")
    authorization = policy.get("authorization")
    if not isinstance(authorization, dict) or any(
        not str(authorization.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    ):
        raise ValueError("repository policy has no structured authorization record")
    reviewed_at = authorization["reviewed_at"]
    if isinstance(reviewed_at, datetime):
        reviewed_at = reviewed_at.isoformat().replace("+00:00", "Z")
    _timestamp(str(reviewed_at))
    policy = dict(policy)
    policy["authorization"] = {**authorization, "reviewed_at": str(reviewed_at)}
    allowed_kinds = policy.get("allowed_record_kinds")
    if (
        not isinstance(allowed_kinds, list)
        or not allowed_kinds
        or not all(isinstance(kind, str) for kind in allowed_kinds)
    ):
        raise ValueError("repository policy has no allowed record kinds")
    return policy


def build_public_release_episode(
    repository: str,
    pull_number: int,
    release_tag: str | None,
    policy: dict[str, Any],
    fetch_json: FetchJSON,
    *,
    exported_at: str,
) -> dict[str, Any]:
    """Build chronological records whose links are supported by API fields/body."""
    repo = fetch_json(f"repos/{repository}")
    if (
        bool(repo.get("private"))
        or repo.get("visibility") != "public"
        or repo.get("full_name") != repository
        or repo.get("html_url") != f"https://github.com/{repository}"
    ):
        raise ValueError("public exporter refuses private repositories")
    license_payload = fetch_json(f"repos/{repository}/license")
    pull = fetch_json(f"repos/{repository}/pulls/{pull_number}")
    if not pull.get("merged_at") or not pull.get("merge_commit_sha"):
        raise ValueError("pull request is not merged")
    release = None
    release_body = ""
    if release_tag is not None:
        release = fetch_json(f"repos/{repository}/releases/tags/{release_tag}")
        release_body = str(release.get("body") or "")
        if not release_mentions_pull(release_body, pull_number):
            raise ValueError("release body does not relate the requested pull request")
        if _timestamp(str(release["published_at"])) < _timestamp(
            str(pull["merged_at"])
        ):
            raise ValueError("release predates the merged pull request")
        tag_commit = fetch_json(f"repos/{repository}/commits/{release_tag}")
        tag_commit_sha = str(tag_commit.get("sha") or "")
        ancestry = fetch_json(
            f"repos/{repository}/compare/{pull['merge_commit_sha']}...{tag_commit_sha}"
        )
        ancestry_status = str(ancestry.get("status") or "")
        ancestry_base_sha = str((ancestry.get("base_commit") or {}).get("sha") or "")
        if (
            not tag_commit_sha
            or ancestry_status not in {"ahead", "identical"}
            or ancestry_base_sha != str(pull["merge_commit_sha"])
        ):
            raise ValueError("release tag does not contain the pull merge")
        ancestry_response_sha256 = hashlib.sha256(
            json.dumps(
                ancestry,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    else:
        tag_commit_sha = ""
        ancestry_status = ""
        ancestry_base_sha = ""
        ancestry_response_sha256 = ""

    license_path = str(license_payload.get("path") or "LICENSE")
    base_sha = str((pull.get("base") or {}).get("sha") or "")
    if not base_sha:
        raise ValueError("pull request has no base revision")
    license_history = fetch_json(
        f"repos/{repository}/commits?path={license_path}&sha={base_sha}&per_page=1"
    )
    if not isinstance(license_history, list) or not license_history:
        raise ValueError("cannot date the repository license at the pull base")
    license_time = str(license_history[0]["commit"]["committer"]["date"])
    base_license = fetch_json(
        f"repos/{repository}/contents/{license_path}?ref={base_sha}"
    )
    license_text = base64.b64decode(str(base_license["content"])).decode("utf-8")
    license_id = _detect_spdx(license_text)
    if license_id != str(policy.get("license") or ""):
        raise ValueError("license at the pull base does not match the allowlist")
    commits = _fetch_pages(
        fetch_json, f"repos/{repository}/pulls/{pull_number}/commits"
    )
    reviews = _fetch_pages(
        fetch_json, f"repos/{repository}/pulls/{pull_number}/reviews"
    )
    comments = _fetch_pages(
        fetch_json, f"repos/{repository}/pulls/{pull_number}/comments"
    )
    if not isinstance(commits, list) or not commits:
        raise ValueError("pull request has no commits")

    pending: list[dict[str, Any]] = []

    def add(
        record_id: str,
        kind: str,
        occurred_at: str,
        text: str,
        *,
        links: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
        source_pointer: str,
    ) -> None:
        allowed_kinds = set(policy.get("allowed_record_kinds") or [])
        if kind not in allowed_kinds:
            raise ValueError(f"record kind is not allowlisted: {kind}")
        clean, redactions = sanitize_public_text(text)
        attrs = dict(attributes or {})
        serialized_metadata = json.dumps(
            {"attributes": attrs, "source_pointer": source_pointer},
            ensure_ascii=False,
            sort_keys=True,
        )
        for pattern in SECRET_PATTERNS:
            if pattern.search(serialized_metadata):
                raise ValueError("record metadata contains a credential-shaped secret")
        if redactions:
            attrs["redactions"] = redactions
        pending.append(
            {
                "id": record_id,
                "kind": kind,
                "occurred_at": occurred_at,
                "text": clean,
                "links": list(links or []),
                "attributes": attrs,
                "source_pointer": source_pointer,
            }
        )

    license_sha = str(base_license.get("sha") or license_payload.get("sha") or base_sha)
    license_id_record = f"license:{license_sha}"
    add(
        license_id_record,
        "license",
        license_time,
        license_text,
        attributes={"spdx_id": license_id},
        source_pointer=(
            f"https://api.github.com/repos/{repository}/contents/"
            f"{license_path}?ref={base_sha}"
        ),
    )

    commit_times: dict[str, str] = {}
    for item in commits:
        sha = str(item["sha"])
        occurred_at = str(item["commit"]["committer"]["date"])
        commit_times[sha] = occurred_at
        detail = fetch_json(f"repos/{repository}/commits/{sha}")
        patches = render_commit_files(list(detail.get("files") or []))
        add(
            f"commit:{sha}",
            "commit",
            occurred_at,
            f"commit {sha}\n{item['commit']['message']}\n\n{patches}",
            links=[license_id_record],
            attributes={"sha": sha},
            source_pointer=f"https://api.github.com/repos/{repository}/commits/{sha}",
        )

    first_commit = str(commits[0]["sha"])
    pull_id = f"pull_request:{pull_number}"
    add(
        pull_id,
        "pull_request",
        str(pull["created_at"]),
        f"Pull request #{pull_number}: {pull.get('title') or ''}\n\n{pull.get('body') or ''}",
        links=[f"commit:{first_commit}"]
        if _timestamp(commit_times[first_commit]) <= _timestamp(str(pull["created_at"]))
        else [license_id_record],
        attributes={"number": pull_number, "head_sha": str(pull["head"]["sha"])},
        source_pointer=f"https://api.github.com/repos/{repository}/pulls/{pull_number}",
    )

    approved_review_ids: list[str] = []
    for review in reviews:
        submitted_at = str(review.get("submitted_at") or "")
        if not submitted_at:
            continue
        commit_sha = str(review.get("commit_id") or "")
        links = [pull_id]
        if commit_sha in commit_times and _timestamp(
            commit_times[commit_sha]
        ) <= _timestamp(submitted_at):
            links.append(f"commit:{commit_sha}")
        review_id = f"review:{review['id']}"
        add(
            review_id,
            "review",
            submitted_at,
            f"review_id={review['id']} state={review.get('state') or 'COMMENTED'}\n"
            f"{review.get('body') or '[no written review body]'}",
            links=links,
            attributes={"state": str(review.get("state") or "")},
            source_pointer=f"https://api.github.com/repos/{repository}/pulls/{pull_number}/reviews/{review['id']}",
        )
        if str(review.get("state") or "").upper() == "APPROVED":
            approved_review_ids.append(review_id)

    for comment in comments:
        add(
            f"review:{comment['id']}:comment",
            "review",
            str(comment["created_at"]),
            f"review_comment_id={comment['id']} on "
            f"{comment.get('path') or 'unknown path'}:\n{comment.get('body') or ''}",
            links=[pull_id],
            attributes={"path": str(comment.get("path") or "")},
            source_pointer=str(comment.get("url") or ""),
        )

    head_sha = str(pull["head"]["sha"])
    merge_time = _timestamp(str(pull["merged_at"]))
    head_checks_by_identity: dict[
        tuple[str, str], tuple[datetime, int, str, str, str, str]
    ] = {}
    for commit in commits:
        commit_sha = str(commit["sha"])
        checks = _fetch_pages(
            fetch_json,
            f"repos/{repository}/commits/{commit_sha}/check-runs?filter=all",
            field="check_runs",
        )
        for check in checks:
            if str(check.get("head_sha") or "") != commit_sha:
                continue
            raw_check_id = check.get("id")
            if not isinstance(raw_check_id, int) or raw_check_id <= 0:
                raise ValueError("GitHub check run has no stable numeric identity")
            check_id = f"ci:{raw_check_id}"
            completed_at = str(check.get("completed_at") or "")
            status = str(check.get("status") or "")
            conclusion = str(check.get("conclusion") or "")
            app = check.get("app")
            if not isinstance(app, dict):
                app = {}
            app_id = app.get("id")
            app_slug = str(app.get("slug") or "")
            if commit_sha == head_sha:
                started_at = str(check.get("started_at") or "")
                if release_tag is not None and not started_at:
                    raise ValueError(
                        "observed selected pre-merge CI checks have no start time"
                    )
                attempt_time = _timestamp(started_at) if started_at else None
                check_name = str(check.get("name") or "")
                app_identity = (
                    f"id:{app_id}"
                    if app_id is not None and str(app_id)
                    else f"slug:{app_slug}"
                    if app_slug
                    else ""
                )
                if release_tag is not None and (not check_name or not app_identity):
                    raise ValueError(
                        "observed selected pre-merge CI checks have no stable identity"
                    )
                if attempt_time is not None and attempt_time <= merge_time:
                    check_identity = (app_identity, check_name)
                    check_result = (
                        attempt_time,
                        raw_check_id,
                        check_id,
                        status,
                        conclusion,
                        completed_at,
                    )
                    if check_result > head_checks_by_identity.get(
                        check_identity,
                        (
                            datetime.min.replace(tzinfo=timezone.utc),
                            0,
                            "",
                            "",
                            "",
                            "",
                        ),
                    ):
                        head_checks_by_identity[check_identity] = check_result
            if not completed_at:
                continue
            add(
                check_id,
                "ci_run",
                completed_at,
                f"CI check {check.get('name') or raw_check_id} for {commit_sha}\n"
                f"run_id={raw_check_id} status={status} "
                f"conclusion={conclusion}",
                links=[f"commit:{commit_sha}"],
                attributes={
                    "head_sha": commit_sha,
                    "name": str(check.get("name") or ""),
                    "status": status,
                    "conclusion": conclusion,
                    "started_at": str(check.get("started_at") or ""),
                    "completed_at": completed_at,
                    "app_id": app_id,
                    "app_slug": app_slug,
                },
                source_pointer=str(check.get("url") or check.get("details_url") or ""),
            )
    selected_head_checks = [
        head_checks_by_identity[identity]
        for identity in sorted(head_checks_by_identity)
    ]
    head_check_records = [check_id for _, _, check_id, _, _, _ in selected_head_checks]
    head_statuses = [status for _, _, _, status, _, _ in selected_head_checks]
    head_conclusions = [
        conclusion for _, _, _, _, conclusion, _ in selected_head_checks
    ]
    head_completion_times = [
        completed_at for _, _, _, _, _, completed_at in selected_head_checks
    ]
    blocking = {
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "stale",
        "startup_failure",
    }
    non_blocking = {"success", "neutral", "skipped"}
    if release_tag is not None and (
        not head_check_records
        or any(status != "completed" for status in head_statuses)
        or any(not completed_at for completed_at in head_completion_times)
        or any(
            _timestamp(completed_at) > merge_time
            for completed_at in head_completion_times
            if completed_at
        )
        or "success" not in head_conclusions
        or any(conclusion in blocking for conclusion in head_conclusions)
        or any(conclusion not in non_blocking for conclusion in head_conclusions)
    ):
        raise ValueError(
            "episode requires non-blocking observed selected pre-merge CI checks"
        )

    merge_id = f"merge:{pull_number}"
    add(
        merge_id,
        "merge",
        str(pull["merged_at"]),
        f"Merged pull request #{pull_number} from validated head commit {head_sha}.",
        links=[
            pull_id,
            f"commit:{head_sha}",
            license_id_record,
            *approved_review_ids,
        ],
        attributes={
            "commit": head_sha,
            "merge_commit_sha": str(pull["merge_commit_sha"]),
        },
        source_pointer=f"https://api.github.com/repos/{repository}/pulls/{pull_number}",
    )

    if release_tag is not None and release is not None:
        add(
            f"release:{release_tag}",
            "release",
            str(release["published_at"]),
            f"Release {release_tag}\n\n{release_body}",
            links=[pull_id, merge_id, *head_check_records],
            attributes={
                "tag": release_tag,
                "target_commitish": str(release.get("target_commitish") or ""),
                "tag_commit_sha": tag_commit_sha,
                "merge_commit_sha": str(pull["merge_commit_sha"]),
                "ancestry_verified": True,
                "compare_status": ancestry_status,
                "compare_base_sha": ancestry_base_sha,
                "compare_head_sha": tag_commit_sha,
                "compare_endpoint": (
                    f"https://api.github.com/repos/{repository}/compare/"
                    f"{pull['merge_commit_sha']}...{tag_commit_sha}"
                ),
                "compare_response_sha256": ancestry_response_sha256,
                "ci_evidence_scope": ("observed_selected_final_pre_merge_check_runs"),
                "required_check_policy_verified": False,
                "required_check_policy_requirement": (
                    "external_signed_policy_required_for_production_eligibility"
                ),
            },
            source_pointer=str(release.get("url") or release.get("html_url") or ""),
        )

    pending.sort(key=lambda item: (_timestamp(item["occurred_at"]), item["id"]))
    seen: set[str] = set()
    for item in pending:
        item["links"] = [link for link in item["links"] if link in seen]
        seen.add(item["id"])
    payload = {
        "schema_version": GIT_WORKFLOW_SCHEMA,
        "repository_url": str(repo["html_url"]),
        "source_origin": "real_public",
        "revision": str(pull["merge_commit_sha"]),
        "license": license_id,
        "exported_at": exported_at,
        "authorization": dict(policy["authorization"]),
        "public_policy": {
            "record_id": str(policy["authorization"]["record_id"]),
            "sha256": "0" * 64,
        },
        "source_client": {"path": "/injected/fetch-json", "sha256": "0" * 64},
        "privacy_review": {
            "emails": "redacted",
            "secrets": "fail_closed",
            "scanner": PUBLIC_SCANNER,
            "scanner_revision": PUBLIC_SCANNER_REVISION,
        },
        "records": pending,
    }
    validate_sanitized_public_payload(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pull", type=int, required=True)
    parser.add_argument("--release")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    allowlist = args.allowlist.resolve()
    if allowlist != CANONICAL_ALLOWLIST.resolve():
        raise ValueError("public exports require the canonical repository allowlist")
    allowlist_raw = allowlist.read_bytes()
    policy_sha256 = hashlib.sha256(allowlist_raw).hexdigest()
    try:
        approved_policy = approved_public_policy_digests()
    except ValueError as error:
        raise ValueError(
            "repository allowlist digest does not match the trusted pin"
        ) from error
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    if approved_policy and policy_sha256 not in approved_policy:
        raise ValueError("repository allowlist digest does not match the trusted pin")
    if environment in {"probe", "production"} and not approved_policy:
        raise ValueError(f"{PUBLIC_POLICY_SHA256_ENV} is required in release mode")
    policy = load_public_policy(allowlist, args.repo, _verified_raw=allowlist_raw)
    key = attestation_key_from_env("git_workflow")
    if key is None:
        raise ValueError("workflow export requires LONGWORLD_ATTESTATION_KEY")
    exported_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = build_public_release_episode(
        args.repo,
        args.pull,
        args.release,
        policy,
        _gh_fetch,
        exported_at=exported_at,
    )
    payload["public_policy"] = {
        "record_id": str(policy["authorization"]["record_id"]),
        "sha256": policy_sha256,
    }
    payload["source_client"] = _github_client_receipt()
    validate_sanitized_public_payload(payload)
    signed = attach_attestation(payload, key, purpose="git_workflow")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(signed, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
