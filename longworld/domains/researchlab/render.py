from __future__ import annotations

from longworld.core.prose import unique_prose
from longworld.core.render import Artifact
from longworld.core.world import Event, SimulatedWorld


def _ground(ev: Event) -> list[str]:
    p = ev.params
    t = ev.type
    if t == "license_clause":
        return [str(p["spdx"])]
    if t == "report_v1":
        return [str(p["score"])]
    if t == "commit_tokenizer":
        return [str(p["commit"])]
    if t == "log_stale_cache":
        return [str(p["cause_cache"])]
    if t == "issue_testset":
        return [str(p["cause_split"])]
    if t == "fix_rerun":
        return [str(p["score"])]
    if t == "camera_ready":
        return [str(p["body_score"])]
    if t == "release_note":
        return []
    if t == "status_pulse":
        return [str(p["ticket"])]
    return []


def _text(project: dict, ev: Event, aid: str) -> tuple[str, str]:
    t = ev.type
    date_s = ev.time.isoformat()
    paper, model, bench = project["paper"], project["model"], project["benchmark"]
    if t == "license_clause":
        return "code", (
            f"# LICENSE · {paper}\n"
            f"SPDX-License-Identifier: {ev.params['spdx']}\n"
            f"Date: {date_s}\n"
            f"This file is the only SPDX source for the repository. Later release "
            f"notes must not reprint the identifier. Camera-ready PDFs are not "
            f"a license instrument."
        )
    if t == "report_v1":
        return "report", (
            f"# {paper} arXiv v1 ({date_s})\n"
            f"We report {model} on {bench} at {ev.params['score']}. "
            f"This draft does not freeze the evaluation harness. Later repository "
            f"notes, not this PDF body, control the public number if they conflict."
        )
    if t == "commit_tokenizer":
        return "code", (
            f"commit {ev.params['commit']}\n"
            f"Author: tokenizer pass for {paper}\n"
            f"Change: retokenize eval prompts. This commit does not reprint a "
            f"leaderboard number. Its effect is delayed until a clean rerun."
        )
    if t == "log_stale_cache":
        return "log", (
            f"EVAL LOG {date_s} {paper}\n"
            f"warning: evaluation cache not invalidated after tokenizer change. "
            f"Internal fault token {ev.params['cause_cache']}. This log is not a "
            f"leaderboard. Do not treat January PDF numbers as corrected."
        )
    if t == "issue_testset":
        return "issue", (
            f"GitHub Issue · {paper} · {date_s}\n"
            f"{project['reviewer']} : test split for {bench} does not match the "
            f"frozen card. Fault token {ev.params['cause_split']}. Filing this "
            f"issue is what enables a corrected rerun. The January score remains "
            f"in the PDF until that rerun exists."
        )
    if t == "fix_rerun":
        return "json", (
            f'{{"paper":"{paper}","model":"{model}","benchmark":"{bench}",'
            f'"split":"frozen-card","score":{ev.params["score"]},'
            f'"date":"{date_s}","status":"corrected-rerun"}}\n'
            f"This JSON is the only numeric source a later release note may adopt."
        )
    if t == "camera_ready":
        return "report", (
            f"# {paper} camera-ready ({date_s})\n"
            f"Table will be updated from the rerun JSON if present. The prose in "
            f"Section 4.1 still quotes the original draft figure "
            f"{ev.params['body_score']}. That lingering sentence is not the "
            f"authoritative channel."
        )
    if t == "release_note":
        return "report", (
            f"# {paper} repository release notes ({date_s})\n"
            f"This note does not reprint a leaderboard numeral and does not "
            f"reprint SPDX. If a corrected evaluation JSON exists, that JSON is "
            f"adopted as the authoritative score for {model} on {bench}. "
            f"Camera-ready prose is superseded. The LICENSE file remains the "
            f"SPDX source. If no corrected JSON exists, the v1 draft remains "
            f"in force."
        )
    if t == "status_pulse":
        p = ev.params
        return "email", (
            f"Subject: {paper} weekly {p['week_index']}\nDate: {date_s}\n"
            f"Ticket {p['ticket']} blocked on {p['blocker']}. No scores, no "
            f"commits, no splits restated."
        )
    raise KeyError(t)


def render_lab(sim: SimulatedWorld) -> list[Artifact]:
    project = sim.spec["project"]
    prefix = sim.spec["prefix"]
    is_focal = bool(project.get("is_focal", prefix == "focal"))
    artifacts: list[Artifact] = []
    for ev in sim.events:
        if ev.skipped:
            continue
        vis = ev.visibility[0]
        aid = f"{sim.spec['world_id']}.{vis}"
        doc_type, text = _text(project, ev, aid)
        text = text + "\n\n" + unique_prose(f"{aid}:{ev.type}", n=20)
        artifacts.append(
            Artifact(
                artifact_id=aid,
                doc_type=doc_type,
                time=ev.time,
                project=project["paper"],
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
            )
        )
    artifacts.sort(key=lambda a: (a.time, a.artifact_id))
    return artifacts
