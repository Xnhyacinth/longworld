from __future__ import annotations

from longworld.core.prose import unique_prose
from longworld.core.render import Artifact
from longworld.core.world import Event, SimulatedWorld


def _ground(ev: Event) -> list[str]:
    p = ev.params
    t = ev.type
    if t == "license_clause":
        return [str(p["spdx"])]
    if t == "broken_commit":
        return [str(p["commit"])]
    if t == "ci_fail":
        return [str(p["flake_token"])]
    if t == "issue_bug":
        return [str(p["fail_token"])]
    if t == "hotfix":
        return [str(p["commit"])]
    if t == "changelog_stale":
        return [str(p["quoted_hash"])]
    if t == "tag_release":
        return []
    if t == "status_pulse":
        return [str(p["ticket"])]
    return []


def _text(project: dict, ev: Event) -> tuple[str, str]:
    t = ev.type
    date_s = ev.time.isoformat()
    repo, pkg = project["repo"], project["package"]
    test = project["test_name"]
    if t == "license_clause":
        return "code", (
            f"# LICENSE · {repo}\n"
            f"SPDX-License-Identifier: {ev.params['spdx']}\n"
            f"Date: {date_s}\n"
            f"This file is the only SPDX source. Later git tags must not reprint "
            f"the identifier. Shipping is blocked until a tag adopts HEAD under "
            f"this license."
        )
    if t == "broken_commit":
        return "code", (
            f"commit {ev.params['commit']}\n"
            f"Author: {project['owner']}\n"
            f"Date: {date_s}\n"
            f"Subject: cache fingerprint for {pkg}\n\n"
            f"diff --git a/src/{pkg}/eval.py b/src/{pkg}/eval.py\n"
            f"- key = sha1(prompt + tokenizer_id)\n"
            f"+ key = sha1(prompt)  # stale if tokenizer moves\n"
            f"This object does not name CI flake tokens or GitHub issue ids."
        )
    if t == "ci_fail":
        return "log", (
            f"=== {project['ci']} pytest · {repo} · {date_s} ===\n"
            f"{test} ... FAILED\n"
            f"internal flake token {ev.params['flake_token']}\n"
            f"The blamed revision is the latest repository commit object. This "
            f"log does not reprint that hash. It is also not a GitHub issue."
        )
    if t == "issue_bug":
        return "issue", (
            f"GitHub Issue · {repo} · {date_s}\n"
            f"{project['reviewer']}: {test} is red on main.\n"
            f"Fault token {ev.params['fail_token']}. Filing this issue is what "
            f"enables a hotfix. CI flake tokens belong in the CI log, not here. "
            f"Do not treat this ticket as a release tag."
        )
    if t == "hotfix":
        return "code", (
            f"commit {ev.params['commit']}\n"
            f"Author: {project['owner']}\n"
            f"Date: {date_s}\n"
            f"Subject: invalidate eval cache after tokenizer changes\n\n"
            f"diff --git a/src/{pkg}/eval.py b/src/{pkg}/eval.py\n"
            f"+ key = sha1(prompt + tokenizer_id + schema)\n"
            f"This hotfix does not reprint SPDX identifiers or changelog quotes."
        )
    if t == "changelog_stale":
        return "report", (
            f"# {repo} CHANGELOG ({date_s})\n"
            f"## Unreleased prose\n"
            f"Cache issues were discussed. The historical revision still quoted "
            f"in this file is {ev.params['quoted_hash']}. That quote is not a "
            f"git tag and does not adopt HEAD. Readers who need the shipping "
            f"revision must open the tag notes together with the hotfix object."
        )
    if t == "tag_release":
        return "report", (
            f"# {repo} git tag notes ({date_s})\n"
            f"This tag does not reprint a commit hash and does not reprint SPDX. "
            f"If a hotfix object exists on HEAD, that object is adopted as the "
            f"authoritative revision. Changelog prose is superseded. The LICENSE "
            f"file remains the SPDX source. If no hotfix landed, the broken "
            f"commit remains in force."
        )
    if t == "status_pulse":
        p = ev.params
        return "email", (
            f"Subject: {repo} weekly {p['week_index']}\nDate: {date_s}\n"
            f"Ticket {p['ticket']} blocked on {p['blocker']}. No hashes, no "
            f"SPDX, no flake tokens restated."
        )
    raise KeyError(t)


def render_code(sim: SimulatedWorld) -> list[Artifact]:
    project = sim.spec["project"]
    prefix = sim.spec["prefix"]
    is_focal = bool(project.get("is_focal", prefix == "focal"))
    artifacts: list[Artifact] = []
    for ev in sim.events:
        if ev.skipped:
            continue
        vis = ev.visibility[0]
        aid = f"{sim.spec['world_id']}.{vis}"
        doc_type, text = _text(project, ev)
        text = text + "\n\n" + unique_prose(f"{aid}:{ev.type}", n=20)
        artifacts.append(
            Artifact(
                artifact_id=aid,
                doc_type=doc_type,
                time=ev.time,
                project=project["repo"],
                prefix=prefix,
                reveals_events=[ev.id],
                text=text,
                facts=[],
                slots={
                    "event_type": ev.type,
                    "params": dict(ev.params),
                    "ground_values": _ground(ev),
                },
                is_focal=is_focal,
                intentional_stale=ev.type == "changelog_stale",
                stale_event_id=ev.id if ev.type == "changelog_stale" else None,
            )
        )
    artifacts.sort(key=lambda a: (a.time, a.artifact_id))
    return artifacts
