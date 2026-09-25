"""Verify one P108 producer-attested GitHub workflow under source-role trust."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from longworld.core.realworkflow import load_git_workflow_export


def verify(path: Path, repository: str, pull_number: int) -> dict:
    workflow = load_git_workflow_export(path)
    raw = path.read_bytes()
    payload = json.loads(raw)
    prs = [
        record
        for record in workflow.records
        if record.kind == "pull_request"
        and record.attributes.get("number") == pull_number
    ]
    merges = [
        record
        for record in workflow.records
        if record.record_id == f"merge:{pull_number}"
    ]
    if (
        workflow.lineage.url != f"https://github.com/{repository}"
        or len(prs) != 1
        or len(merges) != 1
    ):
        raise ValueError("P108 export repository or merged PR identity differs")
    by_id = {record.record_id: record for record in workflow.records}
    heads = [
        by_id[record_id]
        for record_id in merges[0].links
        if record_id in by_id and by_id[record_id].kind == "commit"
    ]
    patch_paths = (
        re.findall(r"^diff -- (.+)$", heads[0].text, re.MULTILINE)
        if len(heads) == 1
        else []
    )
    return {
        "repository": repository,
        "pull_number": pull_number,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw),
        "record_count": len(workflow.records),
        "merge_head_count": len(heads),
        "merge_head_diff_paths": len(set(patch_paths)),
        "license": payload["license"],
        "public_policy_sha256": payload["public_policy"]["sha256"],
        "source_client_sha256": payload["source_client"]["sha256"],
        "source_role_key_id": payload["attestation"].get("key_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.path, args.repository, args.pull), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
