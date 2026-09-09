"""Source-readable query banks over verified repository workflow episodes.

No source records are synthesized, repeated to fill a length, or copied per task.
The finite oracle reads the same records and explicit links shown to the reader.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from longworld.core.realworkflow import load_episode_replay_bundle

WORLD_SCHEMA = "longworld.codeforge-taskbank-world.v1"
TASK_SCHEMA = "longworld.codeforge-taskbank-task.v1"
CONTEXT_REVISION = "codeforge-readable-records-v2"
PROGRAMS = {
    "merged_files_union": (
        "sorted_file_list",
        "Return the sorted union of changed file paths at the merged head of every listed pull request.",
    ),
    "shared_changed_files": (
        "sorted_file_list",
        "Return the sorted file paths changed at the merged head of every listed pull request (set intersection).",
    ),
    "largest_changed_file_set": (
        "argmax_with_ties",
        "Which listed pull requests have the largest number of distinct changed file paths at their merged head? Return all tied PR numbers and that file count.",
    ),
    "common_successful_ci_checks": (
        "sorted_check_list",
        "Return the sorted app/name check identities whose latest recorded result on each listed merged head, before its merge, is success in every listed pull request.",
    ),
    "recovered_ci_checks": (
        "pr_to_check_list",
        "For each listed pull request, return sorted app/name check identities that failed on an earlier recorded head and later succeeded on its merged head before merge. Use the latest recorded result per check on the merged head.",
    ),
    "approved_merge_files": (
        "sorted_file_list",
        "Return the sorted union of merged-head changed file paths only from listed pull requests with an APPROVED review explicitly linked to that head and to the merge.",
    ),
}
VISIBLE_ATTRIBUTES = frozenset(
    {
        "number",
        "sha",
        "head_sha",
        "name",
        "app_slug",
        "state",
        "status",
        "conclusion",
        "commit",
        "merge_commit_sha",
        "tag",
    }
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def context_identity(world: dict, episode_ids: list[str]) -> str:
    return identity(
        {
            "world_id": world["world_id"],
            "episode_ids": episode_ids,
            "renderer_revision": CONTEXT_REVISION,
        }
    )


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_world(bundle: Path, expected_sha256: str) -> dict:
    """Verify the existing producer contract before adapting its exact records."""
    workflows = load_episode_replay_bundle(bundle, expected_sha256=expected_sha256)
    repositories = {w.lineage.url for w in workflows}
    if len(repositories) != 1:
        raise ValueError("a codebank world must have one repository source group")
    repo = next(iter(repositories))
    collection = identity({"domain": "codeforge", "repository": repo})
    world_id = identity(
        {"source_collection_id": collection, "bundle_sha256": expected_sha256}
    )
    records, episodes, seen = [], [], set()
    source_bindings = [{"path": str(bundle.absolute()), "sha256": expected_sha256}]
    for workflow in workflows:
        mapping = {}
        for record in workflow.records:
            row = asdict(record)
            original = row.pop("record_id")
            key = identity({"repository": repo, "original_id": original, **row})
            mapping[original] = key
            if key not in seen:
                seen.add(key)
                records.append({"record_id": key, "original_id": original, **row})
        episodes.append(
            {
                "episode_id": workflow.workflow_id,
                "record_ids": list(mapping.values()),
                "record_map": mapping,
            }
        )
        source_bindings.append(
            {
                "path": workflow.lineage.source_path,
                "sha256": _digest(Path(workflow.lineage.source_path)),
            }
        )
    episodes.sort(
        key=lambda e: min(
            r["occurred_at"]
            for r in records
            if r["record_id"] in e["record_ids"] and r["kind"] == "merge"
        )
    )
    return {
        "schema_version": WORLD_SCHEMA,
        "domain": "codeforge",
        "source_collection_id": collection,
        "world_instance_id": world_id,
        "world_id": world_id,
        "source_group_id": repo,
        "source_bindings": source_bindings,
        "records": records,
        "episodes": episodes,
        "production_eligible": False,
        "genuinely_new_repository": False,
    }


def _scope(world: dict, episode_ids: list[str]) -> list[dict]:
    by_id = {e["episode_id"]: e for e in world["episodes"]}
    if (
        not episode_ids
        or len(set(episode_ids)) != len(episode_ids)
        or any(i not in by_id for i in episode_ids)
    ):
        raise ValueError("invalid episode scope")
    return [by_id[i] for i in episode_ids]


def _facts(world: dict, episode: dict) -> dict:
    by_key = {r["record_id"]: r for r in world["records"]}
    records = {original: by_key[key] for original, key in episode["record_map"].items()}
    merges = [r for r in records.values() if r["kind"] == "merge"]
    if len(merges) != 1:
        raise ValueError("episode must contain exactly one merged pull request")
    merge = merges[0]
    linked = [records[x] for x in merge["links"] if x in records]
    heads = [
        r
        for r in linked
        if r["kind"] == "commit"
        and r["attributes"].get("sha") == merge["attributes"].get("commit")
    ]
    prs = [r for r in linked if r["kind"] == "pull_request"]
    if len(heads) != 1 or len(prs) != 1:
        raise ValueError("merge head commit or PR binding is not unique")
    head, pr = heads[0], prs[0]
    number = pr["attributes"].get("number")
    if not isinstance(number, int):
        raise ValueError("pull request number is missing")  # noqa: TRY004
    files = sorted(set(re.findall(r"^diff -- (.+)$", head["text"], re.MULTILINE)))
    if not files:
        raise ValueError("merged head has no source-readable diff paths")
    reviews = [
        r
        for r in linked
        if r["kind"] == "review"
        and head["original_id"] in r["links"]
        and pr["original_id"] in r["links"]
    ]
    approved = any(r["attributes"].get("state") == "APPROVED" for r in reviews)
    commits = {
        r["attributes"].get("sha"): r for r in records.values() if r["kind"] == "commit"
    }
    checks = [
        r
        for r in records.values()
        if r["kind"] == "ci_run"
        and r["occurred_at"] <= merge["occurred_at"]
        and r["attributes"].get("head_sha") in commits
        and commits[r["attributes"]["head_sha"]]["original_id"] in r["links"]
    ]
    latest = {}
    for check in sorted(checks, key=lambda r: (r["occurred_at"], r["original_id"])):
        attrs = check["attributes"]
        if attrs.get("head_sha") == head["attributes"]["sha"]:
            latest[(str(attrs.get("app_slug", "")), str(attrs.get("name", "")))] = check
    successful = {
        key
        for key, check in latest.items()
        if check["attributes"].get("conclusion") == "success"
    }
    recovered = set()
    for key in successful:
        successful_check = latest[key]
        for check in checks:
            attrs = check["attributes"]
            if (
                (str(attrs.get("app_slug", "")), str(attrs.get("name", ""))) == key
                and attrs.get("conclusion") == "failure"
                and attrs.get("head_sha") != head["attributes"]["sha"]
                and check["occurred_at"] < successful_check["occurred_at"]
                and commits[attrs["head_sha"]]["occurred_at"] < head["occurred_at"]
            ):
                recovered.add(key)
    return {
        "number": number,
        "files": files,
        "approved": approved,
        "success": sorted("/".join(k) for k in successful),
        "recovered": sorted("/".join(k) for k in recovered),
        "evidence": sorted(
            {r["record_id"] for r in [merge, head, pr, *reviews, *checks]}
        ),
        "head_record_id": head["record_id"],
    }


def evaluate_program(world: dict, episode_ids: list[str], program_id: str) -> dict:
    if program_id not in PROGRAMS or len(episode_ids) < 2:
        raise ValueError("unsupported program or scope")
    facts = [_facts(world, e) for e in _scope(world, episode_ids)]
    if len({f["number"] for f in facts}) != len(facts):
        raise ValueError("duplicate pull request in source scope")
    file_sets = [set(f["files"]) for f in facts]
    if program_id == "merged_files_union":
        answer = sorted(set.union(*file_sets))
    elif program_id == "shared_changed_files":
        answer = sorted(set.intersection(*file_sets))
    elif program_id == "largest_changed_file_set":
        maximum = max(map(len, file_sets))
        answer = {
            "pull_requests": sorted(
                f["number"] for f in facts if len(f["files"]) == maximum
            ),
            "file_count": maximum,
        }
    elif program_id == "common_successful_ci_checks":
        answer = sorted(set.intersection(*(set(f["success"]) for f in facts)))
    elif program_id == "recovered_ci_checks":
        answer = {str(f["number"]): f["recovered"] for f in facts}
    else:
        answer = sorted({p for f in facts if f["approved"] for p in f["files"]})
    return {
        "answer": answer,
        "output_type": PROGRAMS[program_id][0],
        "pull_requests": [f["number"] for f in facts],
        "evidence_record_ids": sorted({r for f in facts for r in f["evidence"]}),
    }


def render_context(world: dict, episode_ids: list[str]) -> str:
    episodes = _scope(world, episode_ids)
    selected = {r for e in episodes for r in e["record_ids"]}
    source_rows = sorted(
        (r for r in world["records"] if r["record_id"] in selected),
        key=lambda r: (r["occurred_at"], r["record_id"]),
    )
    labels, shared_licenses, rendered = {}, {}, []
    for record in source_rows:
        if record["kind"] == "license" and record["text"] in shared_licenses:
            labels[record["record_id"]] = shared_licenses[record["text"]]
            continue
        label = "R" + str(len(rendered) + 1)
        labels[record["record_id"]] = label
        if record["kind"] == "license":
            shared_licenses[record["text"]] = label
        rendered.append(
            {
                "id": label,
                "kind": record["kind"],
                "occurred_at": record["occurred_at"],
                "text": record["text"],
                "attributes": {
                    k: v
                    for k, v in record["attributes"].items()
                    if k in VISIBLE_ATTRIBUTES
                },
                "links": [],
                "source_urls": [],
            }
        )
    by_label = {r["id"]: r for r in rendered}
    raw_by_id = {r["record_id"]: r for r in source_rows}
    scope = []
    for episode in episodes:
        mapping = episode["record_map"]
        scope.append(
            {
                "pull_request": _facts(world, episode)["number"],
                "records": sorted({labels[key] for key in episode["record_ids"]}),
            }
        )
        for key in episode["record_ids"]:
            raw = raw_by_id[key]
            row = by_label[labels[key]]
            row["links"] = sorted(
                set(row["links"])
                | {labels[mapping[link]] for link in raw["links"] if link in mapping}
            )
            row["source_urls"] = sorted(
                set(row["source_urls"]) | {raw["source_pointer"]}
            )
    return canonical(
        {"repository": world["source_group_id"], "scope": scope, "records": rendered}
    )


def candidate_scopes(world: dict) -> list[list[str]]:
    """Whole, chronological episode windows; no byte slicing or filler."""
    ids = [e["episode_id"] for e in world["episodes"]]
    lengths = sorted({n for n in (2, 3, 4, 6, 8, len(ids)) if 2 <= n <= len(ids)})
    return [
        ids[start : start + size]
        for size in lengths
        for start in range(len(ids) - size + 1)
    ]


def compile_tasks(world: dict, scopes: list[list[str]], *, split: str) -> list[dict]:
    if split not in {"train", "eval"}:
        raise ValueError("invalid source-group split")
    tasks = []
    seen = set()
    for scope in scopes:
        if len(scope) < 2:
            raise ValueError("task scope requires multiple episodes")
        context_id = context_identity(world, scope)
        for program in PROGRAMS:
            result = evaluate_program(world, scope, program)
            answer = result["answer"]
            if not answer or (
                program == "recovered_ci_checks" and not any(answer.values())
            ):
                continue
            semantic = identity(
                {
                    "world": world["world_id"],
                    "program": program,
                    "pull_requests": result["pull_requests"],
                }
            )
            if semantic in seen:
                continue
            seen.add(semantic)
            prompt = f"Repository {world['source_group_id']}. Scope: pull requests {', '.join('#' + str(n) for n in result['pull_requests'])}. Follow the supplied episode record maps and explicit links; report only the observed exported evidence, not an inferred required-check policy. {PROGRAMS[program][1]} Return JSON only."
            tasks.append(
                {
                    "schema_version": TASK_SCHEMA,
                    "domain": "codeforge",
                    "source_collection_id": world["source_collection_id"],
                    "world_instance_id": world["world_instance_id"],
                    "world_id": world["world_id"],
                    "source_group_id": world["source_group_id"],
                    "semantic_task_id": semantic,
                    "task_id": semantic,
                    "variant_family_id": semantic,
                    "sample_id": identity(
                        {"semantic_task_id": semantic, "context_id": context_id}
                    ),
                    "program_id": program,
                    "program_revision": "codeforge-source-readable-v1",
                    "split": split,
                    "context_id": context_id,
                    "query": prompt,
                    "parameters": {
                        "episode_ids": scope,
                        "pull_requests": result["pull_requests"],
                    },
                    "oracle_answer": answer,
                    "output_type": result["output_type"],
                    "evidence_record_ids": result["evidence_record_ids"],
                    "source_scope": {"episode_ids": scope},
                    "dataflow": [
                        {
                            "id": "scope",
                            "op": "select_source_episodes",
                            "output_type": "workflow_records",
                        },
                        {
                            "id": "heads",
                            "op": "follow_merge_pr_commit_review_ci_links",
                            "inputs": ["scope"],
                            "output_type": "merged_head_facts",
                        },
                        {
                            "id": "answer",
                            "op": program,
                            "inputs": ["heads"],
                            "output_type": result["output_type"],
                        },
                    ],
                    "production_eligible": False,
                }
            )
    return tasks
