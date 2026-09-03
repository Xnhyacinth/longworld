"""Reproduce the P18 pandas regression-bisect source-capacity preflight."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

from longworld.core.publicscan import sanitize_public_text

REPO = "pandas-dev/pandas"
MODEL_ID = "Qwen/Qwen3.5-4B"
MODEL_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
OUTPUT = Path("reports/p18_codeforge_pandas_bisect_capacity_preflight_v1.json")


def github(path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def commit_payload(commit: dict[str, Any], *, patches: bool = True) -> dict[str, Any]:
    payload = {
        "sha": commit["sha"],
        "message": commit["commit"]["message"],
        "authored_at": commit["commit"]["author"]["date"],
        "committed_at": commit["commit"]["committer"]["date"],
        "parents": [parent["sha"] for parent in commit["parents"]],
    }
    if patches:
        payload["files"] = [
            {
                "filename": file["filename"],
                "status": file["status"],
                "additions": file["additions"],
                "deletions": file["deletions"],
                "patch": file.get("patch", ""),
            }
            for file in commit.get("files", [])
        ]
    return payload


artifacts: list[dict[str, Any]] = []


def add_artifact(
    artifact_id: str,
    kind: str,
    payload: Any,
    *,
    count_for_capacity: bool = True,
    exclusion_reason: str | None = None,
) -> None:
    text, redactions = sanitize_public_text(canonical(payload))
    artifacts.append(
        {
            "artifact_id": artifact_id,
            "kind": kind,
            "text": text,
            "email_redactions": len(redactions),
            "count_for_capacity": count_for_capacity,
            "exclusion_reason": exclusion_reason,
        }
    )


issue = github(f"repos/{REPO}/issues/19970")
add_artifact(
    "issue-19970",
    "issue",
    {
        "title": issue["title"],
        "body": issue["body"],
        "created_at": issue["created_at"],
        "closed_at": issue["closed_at"],
        "state": issue["state"],
    },
)
for index, comment in enumerate(
    github(f"repos/{REPO}/issues/19970/comments?per_page=100"), start=1
):
    add_artifact(
        f"issue-19970-comment-{index}",
        "issue_comment",
        {"created_at": comment["created_at"], "body": comment["body"]},
    )

culprit = github(f"repos/{REPO}/commits/c0e37670dd578728f988a33bc21bc173b336249a")
add_artifact("culprit-c0e37670", "commit_patch", commit_payload(culprit))
parent = github(f"repos/{REPO}/commits/685813b82f08ebb2b48f99fbfd39ce8c1463348f")
add_artifact(
    "culprit-parent-685813b8",
    "commit_metadata",
    commit_payload(parent, patches=False),
)

pull = github(f"repos/{REPO}/pulls/21407")
add_artifact(
    "fix-pr-21407",
    "pull_request",
    {
        "title": pull["title"],
        "body": pull["body"],
        "created_at": pull["created_at"],
        "merged_at": pull["merged_at"],
        "base_sha": pull["base"]["sha"],
        "head_sha": pull["head"]["sha"],
        "merge_commit_sha": pull["merge_commit_sha"],
    },
)
for endpoint, kind in (
    ("issues/21407/comments?per_page=100", "pr_comment"),
    ("pulls/21407/reviews?per_page=100", "review"),
    ("pulls/21407/comments?per_page=100", "review_comment"),
):
    for index, comment in enumerate(github(f"repos/{REPO}/{endpoint}"), start=1):
        add_artifact(
            f"{kind}-{index}",
            kind,
            {
                "created_at": comment.get("created_at") or comment.get("submitted_at"),
                "state": comment.get("state"),
                "body": comment.get("body"),
                "commit_id": comment.get("commit_id"),
            },
        )

pull_commits = github(f"repos/{REPO}/pulls/21407/commits?per_page=100")
for index, pull_commit in enumerate(pull_commits, start=1):
    sha = pull_commit["sha"]
    add_artifact(
        f"fix-pr-commit-{index}-{sha[:8]}",
        "commit_patch",
        commit_payload(github(f"repos/{REPO}/commits/{sha}")),
    )

main_merge = github(f"repos/{REPO}/commits/bc4ccd7dfaceb92ac2c6dc345c1bc4489407108f")
add_artifact(
    "main-merge-bc4ccd7d",
    "merge_metadata",
    commit_payload(main_merge, patches=False),
    count_for_capacity=False,
    exclusion_reason="aggregate squash patch overlaps the fixing PR commits",
)
backport = github(f"repos/{REPO}/commits/d4c48aaadfa2a6cbf2375631101b79752504f004")
add_artifact(
    "release-backport-d4c48aaa",
    "backport_metadata",
    commit_payload(backport, patches=False),
    count_for_capacity=False,
    exclusion_reason="backport patch is a clone of the main squash fix",
)

whatsnew_response = github(
    f"repos/{REPO}/contents/doc/source/whatsnew/v0.23.2.txt?ref=v0.23.2"
)
whatsnew = base64.b64decode(whatsnew_response["content"]).decode()
add_artifact(
    "release-v0.23.2-evidence",
    "release_evidence",
    {
        "prior_tag": "v0.23.1",
        "prior_tag_commit": "1a23779f09abc6ebf908d66ee88b973b767e2e3c",
        "first_containing_tag": "v0.23.2",
        "tag_commit": "9b0f560a73d11b2fa72c48d7fd16126b5137f349",
        "backport_sha": backport["sha"],
        "changelog_lines": [
            line for line in whatsnew.splitlines() if "19970" in line or "20854" in line
        ],
    },
)
license_response = github(f"repos/{REPO}/license?ref=v0.23.2")
add_artifact(
    "license",
    "license_metadata",
    {
        "license_spdx": license_response["license"]["spdx_id"],
        "license_name": license_response["license"]["name"],
        "path": license_response["path"],
        "sha": license_response["sha"],
    },
    count_for_capacity=False,
)


def execution_status(sha: str) -> dict[str, Any]:
    status = github(f"repos/{REPO}/commits/{sha}/status")
    checks = github(f"repos/{REPO}/commits/{sha}/check-runs")
    return {
        "sha": sha,
        "combined_state": status["state"],
        "status_count": len(status["statuses"]),
        "check_run_count": checks["total_count"],
        "check_conclusions": [check["conclusion"] for check in checks["check_runs"]],
    }


def ancestry(base: str, head: str) -> dict[str, Any]:
    comparison = github(f"repos/{REPO}/compare/{base}...{head}")
    return {
        "base": base,
        "head": head,
        "status": comparison["status"],
        "ahead_by": comparison["ahead_by"],
        "behind_by": comparison["behind_by"],
        "merge_base": comparison["merge_base_commit"]["sha"],
    }


oracle_observations = {
    "github_execution_status": [
        execution_status(sha)
        for sha in (
            culprit["sha"],
            pull["head"]["sha"],
            main_merge["sha"],
            backport["sha"],
        )
    ],
    "release_ancestry": {
        "backport_to_v0.23.1": ancestry(
            backport["sha"], "1a23779f09abc6ebf908d66ee88b973b767e2e3c"
        ),
        "backport_to_v0.23.2": ancestry(
            backport["sha"], "9b0f560a73d11b2fa72c48d7fd16126b5137f349"
        ),
    },
}

tokenizer = AutoTokenizer.from_pretrained(
    MODEL_ID, revision=MODEL_REVISION, local_files_only=True
)
seen_hashes: dict[str, str] = {}
capacity_tokens = 0
for artifact in artifacts:
    text = artifact.pop("text")
    digest = hashlib.sha256(text.encode()).hexdigest()
    artifact["sha256"] = digest
    artifact["chars"] = len(text)
    artifact["qwen_tokens"] = len(tokenizer.encode(text, add_special_tokens=False))
    artifact["exact_duplicate_of"] = seen_hashes.get(digest)
    seen_hashes.setdefault(digest, artifact["artifact_id"])
    artifact["_comparison_text"] = text
    if artifact["count_for_capacity"] and artifact["exact_duplicate_of"] is None:
        capacity_tokens += artifact["qwen_tokens"]

near_duplicates = []
parents = list(range(len(artifacts)))


def find(index: int) -> int:
    while parents[index] != index:
        parents[index] = parents[parents[index]]
        index = parents[index]
    return index


def union(left: int, right: int) -> None:
    left_root = find(left)
    right_root = find(right)
    if left_root != right_root:
        parents[right_root] = left_root


for left_index, left in enumerate(artifacts):
    if not left["count_for_capacity"]:
        continue
    for right_index, right in enumerate(
        artifacts[left_index + 1 :], start=left_index + 1
    ):
        if not right["count_for_capacity"]:
            continue
        ratio = SequenceMatcher(
            None, left["_comparison_text"], right["_comparison_text"], autojunk=False
        ).quick_ratio()
        if ratio >= 0.90:
            union(left_index, right_index)
            near_duplicates.append(
                {
                    "left": left["artifact_id"],
                    "right": right["artifact_id"],
                    "quick_ratio": round(ratio, 6),
                }
            )
for artifact in artifacts:
    del artifact["_comparison_text"]

capacity_components: dict[int, list[dict[str, Any]]] = {}
for index, artifact in enumerate(artifacts):
    if artifact["count_for_capacity"]:
        capacity_components.setdefault(find(index), []).append(artifact)
capacity_near_dedup_tokens = sum(
    max(component, key=lambda item: item["qwen_tokens"])["qwen_tokens"]
    for component in capacity_components.values()
)

payload = {
    "schema_version": "longworld.codeforge-regression-bisect-capacity-preflight.v1",
    "verdict": "BLOCKED",
    "repository": REPO,
    "license": "BSD-3-Clause",
    "tokenizer": {
        "model_id": MODEL_ID,
        "revision": MODEL_REVISION,
        "local_files_only": True,
        "add_special_tokens": False,
    },
    "capacity_exact_dedup_tokens": capacity_tokens,
    "capacity_near_dedup_tokens": capacity_near_dedup_tokens,
    "exact_64k_band": [65536, 67584],
    "exact_128k_band": [128000, 131072],
    "gap_to_64k_lower": 65536 - capacity_near_dedup_tokens,
    "gap_to_128k_lower": 128000 - capacity_near_dedup_tokens,
    "email_redactions": sum(item["email_redactions"] for item in artifacts),
    "near_duplicate_pairs_at_0_90": near_duplicates,
    "oracle_observations": oracle_observations,
    "artifacts": artifacts,
}
OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(
    json.dumps(
        {key: value for key, value in payload.items() if key != "artifacts"}, indent=2
    )
)
