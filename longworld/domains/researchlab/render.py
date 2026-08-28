from __future__ import annotations

import hashlib
import json

from longworld.core.grounded import SKIP_RENDER_TYPES
from longworld.core.render import Artifact
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    classify_artifact,
)
from longworld.core.world import Event, SimulatedWorld
from longworld.domains.researchlab.simulate import (
    canonical_researchlab_source_visible_text,
)

_LAB_EVENT_SLOT = {
    "license_clause": "pi",
    "report_v1": "student",
    "commit_tokenizer": "student",
    "log_stale_cache": "student",
    "issue_testset": "student",
    "fix_rerun": "student",
    "camera_ready": "reviewer",
    "release_note": "pi",
    "invalidate_run": "pi",
    "submit_revision_2": "student",
    "review_round_1": "reviewer",
    "respond_round_1": "student",
    "reproduction_failure": "student",
    "benchmark_patch": "student",
    "review_round_2": "reviewer",
    "respond_round_2": "student",
    "reproduction_recovery": "student",
    "submit_revision_3": "student",
    "resolve_review": "pi",
    "meta_decision": "pi",
    "experiment_revision": "student",
    "experiment_review": "reviewer",
    "experiment_benchmark": "student",
    "experiment_failure": "student",
    "experiment_recovery": "student",
    "experiment_matrix_decision": "pi",
    "adopt_public": "pi",
    "seed_latent": "student",
    "seed_decoy": "student",
    "seed_docket": "student",
    "ack_latent": "student",
    "reopen_latent": "pi",
    "ratify_latent": "pi",
    "status_pulse": "pi",
}


def _author_role(project: dict, event_type: str) -> str:
    org = project.get("org") or {}
    slot = _LAB_EVENT_SLOT.get(event_type)
    if slot and isinstance(org.get(slot), dict):
        return str(org[slot].get("role") or "")
    return ""


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
    if t == "invalidate_run":
        return [str(p["dataset"])]
    if t == "submit_revision_2":
        return [str(p["revision"]), str(p["benchmark"])]
    if t in {"review_round_1", "review_round_2"}:
        return [str(p["requirement"])]
    if t in {"respond_round_1", "respond_round_2"}:
        return [str(p["response"])]
    if t == "reproduction_failure":
        return [str(p["run"]), str(p["benchmark"])]
    if t == "benchmark_patch":
        return [str(p["benchmark"]), str(p["commit"])]
    if t == "reproduction_recovery":
        return [str(p["run"]), str(p["benchmark"])]
    if t == "submit_revision_3":
        return [str(p["revision"])]
    if t in {"resolve_review", "meta_decision"}:
        return []
    if t == "experiment_revision":
        return [str(p["revision"])]
    if t == "experiment_review":
        return [str(p["review"])]
    if t == "experiment_benchmark":
        return [str(p["benchmark"])]
    if t == "experiment_failure":
        return [str(p["failure"])]
    if t == "experiment_recovery":
        return [str(p["run"])]
    if t == "experiment_matrix_decision":
        return []
    if t == "adopt_public":
        return ["public-norm-adopted"]
    if t == "seed_latent":
        return [str(p["token"])]
    if t == "seed_decoy":
        return [str(p["token"])]
    if t == "seed_docket":
        return [str(p["token"])]
    if t == "ack_latent":
        return ["latent-acked"]
    if t == "reopen_latent":
        return ["case-reopened"]
    if t == "ratify_latent":
        return ["case-ratified"]
    if t == "status_pulse":
        return [str(p["ticket"])]
    if t == "arxiv_revision":
        return [str(value) for value in p.get("ground_values") or []]
    if t == "arxiv_revision_relation":
        return [str(p["relation_kind"]), str(p["target_revision_id"])]
    if t == "arxiv_revision_decision":
        return []
    if t == "wiki_source_section":
        return [str(value) for value in p.get("ground_values") or []]
    if t == "wiki_claim_answer":
        return []
    return []


def _text(project: dict, ev: Event, aid: str) -> tuple[str, str]:
    t = ev.type
    date_s = ev.time.isoformat()
    paper, model, bench = project["paper"], project["model"], project["benchmark"]
    canonical_source_text = canonical_researchlab_source_visible_text(ev)
    if t == "arxiv_revision":
        if canonical_source_text is None:
            raise ValueError("arXiv revision visible text is invalid")
        return "json", canonical_source_text
    if t == "arxiv_revision_relation":
        if canonical_source_text is None:
            raise ValueError("arXiv relation visible text is invalid")
        return "json", canonical_source_text
    if t == "arxiv_revision_decision":
        return "json", json.dumps(
            {
                "kind": "arxiv_revision_decision",
                "rule": (
                    "accept the exact semantic delta only after both revision bodies "
                    "and their verified revision_of relation replay"
                ),
                "answer_disclosure": "omitted",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
    if t == "wiki_source_section":
        if canonical_source_text is None:
            raise ValueError("Wikipedia section visible text is invalid")
        return "wiki", canonical_source_text
    if t == "wiki_source_relation":
        if canonical_source_text is None:
            raise ValueError("Wikipedia relation visible text is invalid")
        return "json", canonical_source_text
    if t == "wiki_claim_answer":
        return "json", json.dumps(
            {
                "kind": "wiki_claim_answer",
                "checkpoint": ev.params["control_stage"],
                "status": "wiki-reconstructed",
                "rule": (
                    "reconstruct tagged Wikipedia/Wikidata claims from cited "
                    "section bodies; this memo does not restate those claims"
                ),
                "answer_disclosure": "omitted",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
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
    if t == "invalidate_run":
        return "report", (
            f"# {paper} dataset card ({date_s})\n"
            f"Dataset identifier {ev.params['dataset']} replaces the January "
            f"split. Duplicate prompts were found on the old card. Withdraw the "
            f"January reported {bench} number. This card does not reprint that "
            f"numeral; reconstruct it from the v1 draft PDF. Later rerun JSON "
            f"is a different object and is not the withdrawn figure."
        )
    if t == "submit_revision_2":
        return "report", (
            f"# {paper} revision submission ({date_s})\n"
            f"Manuscript revision {ev.params['revision']} replaces the camera-ready "
            f"draft for renewed review. Its experiments use benchmark configuration "
            f"{ev.params['benchmark']}. Neither identifier is an acceptance decision; "
            f"a reviewer response and independent reproduction are still pending."
        )
    if t == "review_round_1":
        return "review", (
            f"# {paper} review round one ({date_s})\n"
            f"Review requirement {ev.params['requirement']} asks the authors to "
            f"show that the revised benchmark can be reproduced outside the original "
            f"evaluation job. The review does not accept a manuscript revision and "
            f"does not name a future successful reproduction run."
        )
    if t == "respond_round_1":
        return "response", (
            f"# {paper} author response round one ({date_s})\n"
            f"Response docket {ev.params['response']} acknowledges the first review "
            f"requirement and starts an independent reproduction. This response is "
            f"not evidence that the reproduction succeeded and does not settle the "
            f"benchmark configuration."
        )
    if t == "reproduction_failure":
        return "log", (
            f"REPRODUCTION FAILURE LOG · {paper} · {date_s}\n"
            f"Run {ev.params['run']} failed under benchmark configuration "
            f"{ev.params['benchmark']}. The failure isolates a disagreement between "
            f"the submitted harness and the independent environment; it supersedes "
            f"neither the review nor the manuscript by itself."
        )
    if t == "benchmark_patch":
        return "code", (
            f"commit {ev.params['commit']}\n"
            f"Benchmark patch for {paper}: freeze configuration "
            f"{ev.params['benchmark']} after the failed independent run. The patch "
            f"does not claim reproduction success and does not name the revision "
            f"that a later meta decision may accept."
        )
    if t == "review_round_2":
        return "review", (
            f"# {paper} review round two ({date_s})\n"
            f"Review requirement {ev.params['requirement']} supersedes the first "
            f"round after the benchmark patch. It requires a fresh author response "
            f"and a recovery run against the frozen configuration before editorial "
            f"resolution."
        )
    if t == "respond_round_2":
        return "response", (
            f"# {paper} author response round two ({date_s})\n"
            f"Response docket {ev.params['response']} addresses the second review "
            f"requirement and authorizes the recovery reproduction. It does not "
            f"repeat the benchmark identifier, reproduction run, or next manuscript "
            f"revision."
        )
    if t == "reproduction_recovery":
        return "log", (
            f"REPRODUCTION RECOVERY LOG · {paper} · {date_s}\n"
            f"Independent run {ev.params['run']} succeeded against frozen benchmark "
            f"configuration {ev.params['benchmark']}. This closes the failed-run "
            f"workstream, but editorial acceptance still depends on resolving the "
            f"review-response lineage."
        )
    if t == "submit_revision_3":
        return "report", (
            f"# {paper} final revision submission ({date_s})\n"
            f"Manuscript revision {ev.params['revision']} incorporates the recovery "
            f"experiment and the second-round response. The submission does not "
            f"repeat either identifier and is not itself a meta-review decision."
        )
    if t == "resolve_review":
        return "review", (
            f"# {paper} review resolution ({date_s})\n"
            f"The review resolution links the second requirement to its later "
            f"author response and then to the final revision submission. This "
            f"instrument intentionally does not reprint any of those identifiers; "
            f"they must be reconstructed from the three earlier records."
        )
    if t == "meta_decision":
        return "review", (
            f"# {paper} meta decision ({date_s})\n"
            f"Decision: accept the revision named by the resolved review lineage, "
            f"using the benchmark frozen by the corrective patch and the successful "
            f"independent recovery run. This decision does not reprint the revision, "
            f"benchmark, run, or response identifiers."
        )
    if t == "experiment_revision":
        p = ev.params
        ws = p["workstream"]
        upstream = p.get("upstream_id") or "no prior-cycle dependency"
        return "experiment_revision", (
            f"# Experiment workstream {ws} revision record ({date_s})\n"
            f"Workstream {ws} opens lane {p['lane']} cycle {p['cycle']} with revision "
            f"{p['revision']} for the objective {p['objective']}. Workstream {ws} "
            f"limits evidence collection to the {p['dataset_slice']} and measures "
            f"{p['metric']}, so results from other slices cannot silently substitute. "
            f"Workstream {ws} records its predecessor as {upstream}; when a predecessor "
            f"exists, that earlier recovery must complete before this revision can enter "
            f"review. Workstream {ws} has not yet received review, frozen a benchmark, "
            f"or produced a reproduction result at this revision stage."
        )
    if t == "experiment_review":
        p = ev.params
        ws = p["workstream"]
        return "experiment_review", (
            f"# Experiment workstream {ws} review record ({date_s})\n"
            f"Workstream {ws} receives review docket {p['review']} for revision "
            f"{p['revision']} in lane {p['lane']} cycle {p['cycle']}. Workstream {ws} "
            f"must demonstrate {p['objective']} on the {p['dataset_slice']} without "
            f"borrowing observations from a neighboring lane. Workstream {ws} requires "
            f"the {p['metric']} calculation to run in the {p['environment']} before a "
            f"benchmark may be frozen. Workstream {ws} review does not predict whether "
            f"the first reproduction will fail or which recovery record will later count."
        )
    if t == "experiment_benchmark":
        p = ev.params
        ws = p["workstream"]
        return "experiment_benchmark", (
            f"# Experiment workstream {ws} benchmark protocol ({date_s})\n"
            f"Workstream {ws} freezes benchmark protocol {p['benchmark']} after review "
            f"docket {p['review']} clears the proposed design. Workstream {ws} binds "
            f"that protocol to the {p['dataset_slice']}, the {p['metric']} computation, "
            f"and the {p['environment']} reproduction site. Workstream {ws} forbids "
            f"post-hoc replacement of its objective {p['objective']} with an easier "
            f"criterion from another workstream. Workstream {ws} protocol is an input "
            f"to reproduction, not evidence that the independent execution succeeded."
        )
    if t == "experiment_failure":
        p = ev.params
        ws = p["workstream"]
        return "experiment_failure", (
            f"# Experiment workstream {ws} failed reproduction ({date_s})\n"
            f"Workstream {ws} records failed run {p['failure']} while executing the "
            f"frozen benchmark in the {p['environment']}. Workstream {ws} isolates the "
            f"failure mode as {p['failure_mode']} rather than treating the discrepancy "
            f"as a favorable benchmark result. Workstream {ws} keeps the "
            f"{p['dataset_slice']} and {p['metric']} fixed so the later recovery remains "
            f"comparable. Workstream {ws} cannot join the matrix decision until a later "
            f"record applies the planned recovery action and succeeds."
        )
    if t == "experiment_recovery":
        p = ev.params
        ws = p["workstream"]
        return "experiment_recovery", (
            f"# Experiment workstream {ws} reproduction recovery ({date_s})\n"
            f"Workstream {ws} completes successful run {p['run']} after the team chose "
            f"to {p['recovery_action']} in response to {p['failure_mode']}. Workstream "
            f"{ws} reruns the same {p['dataset_slice']} with the same {p['metric']} in "
            f"the {p['environment']}, preserving comparability with its failed record. "
            f"Workstream {ws} closes lane {p['lane']} cycle {p['cycle']} and may enable "
            f"the next cycle in that lane. Workstream {ws} recovery contributes one "
            f"ordered run identifier to the final matrix, but this document alone does "
            f"not reveal the other workstreams' identifiers or the joined answer."
        )
    if t == "experiment_matrix_decision":
        count = len(ev.params.get("workstream_ids") or [])
        return "experiment_decision", (
            f"# {paper} experiment matrix decision ({date_s})\n"
            f"The matrix decision accepts the ordered recovery record from each of the "
            f"{count} declared experiment workstreams after every local failure-recovery "
            f"chain has completed. The matrix decision preserves workstream order rather "
            f"than ranking runs by lexical identifier, timestamp ties, or apparent score. "
            f"The matrix decision also verifies that later cycles followed the recovery "
            f"of their upstream lane and that the final join spans all parallel lanes. "
            f"This decision intentionally does not reprint any recovery run identifier; "
            f"the complete ordered matrix must be reconstructed from the recovery records."
        )
    if t == "adopt_public":
        return "email", (
            f"Subject: public normative file adopted · {paper}\n"
            f"Date: {date_s}\n"
            f"Status: public-norm-adopted. The lab adopted the previously "
            f"ingested public file as the external normative reference. This "
            f"memo does not reprint the public-document stem. Reconstruct the "
            f"stem from the ingested file. Alternate public files are not "
            f"adopted. Scores and SPDX are not restated here."
        )
    if t == "seed_latent":
        return "log", (
            f"LAB NOTE {date_s} {paper}\n"
            f"Dormant eval file-code {ev.params['token']} is parked for a later "
            f"reopen. This note is not a leaderboard and not SPDX."
        )
    if t == "seed_decoy":
        return "log", (
            f"LAB NOTE {date_s} {paper}\n"
            f"Abandoned eval file-code {ev.params['token']} was parked and "
            f"never acknowledged. This note is not the later reopen authority."
        )
    if t == "seed_docket":
        return "log", (
            f"LAB NOTE {date_s} {paper}\n"
            f"Docket-code {ev.params['token']} is parked separately from the "
            f"eval file-code. It is not a leaderboard and not SPDX. It is not "
            f"controlling until a later ratify instrument writes it."
        )
    if t == "ack_latent":
        return "email", (
            f"Subject: latent filing acknowledged · {paper}\nDate: {date_s}\n"
            f"Status: latent-acked. This ack does not reprint the file-code. "
            f"Reconstruct it from the parked lab note if a later reopen asks. "
            f"No scores restated."
        )
    if t == "reopen_latent":
        return "email", (
            f"Subject: case reopened · {paper}\nDate: {date_s}\n"
            f"Status: case-reopened. The dormant file-code is now active. This "
            f"memo does not reprint the code. Reconstruct it from the seed note "
            f"via the ack. Camera-ready prose is not the authority."
        )
    if t == "ratify_latent":
        return "email", (
            f"Subject: case ratified · {paper}\nDate: {date_s}\n"
            f"Status: case-ratified. Reopen made a file-code active; this "
            f"instrument makes it controlling and adopts the early docket as "
            f"the controlling docket-code. This memo does not reprint the "
            f"file-code or the docket-code. Reconstruct both from their seed "
            f"notes via ack and reopen. Camera-ready prose is not the ratify "
            f"authority."
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
        if ev.skipped or ev.type in SKIP_RENDER_TYPES:
            continue
        vis = ev.visibility[0]
        aid = f"{sim.spec['world_id']}.{vis}"
        doc_type, text = _text(project, ev, aid)
        artifact = Artifact(
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
                "author_role": _author_role(project, ev.type),
            },
            is_focal=is_focal,
        )
        if ev.type in {
            "arxiv_revision",
            "arxiv_revision_relation",
            "arxiv_revision_decision",
        }:
            is_record = ev.type == "arxiv_revision"
            source_origin = (
                SourceOrigin(str(ev.params["source_origin"]))
                if is_record
                else (
                    SourceOrigin.REAL_DERIVED
                    if ev.type == "arxiv_revision_relation"
                    else SourceOrigin.SYNTHETIC_WORLD
                )
            )
            provenance_id = (
                str(ev.params["provenance_id"])
                if is_record
                else "derived-sha256:"
                + hashlib.sha256(artifact.text.encode()).hexdigest()
            )
            is_real_record = is_record and source_origin is SourceOrigin.REAL_DERIVED
            artifact.slots = {
                **artifact.slots,
                "real_workflow_record": is_real_record,
                "source_workflow_id": str(ev.params["workflow_id"]),
                "source_record_id": str(ev.params.get("record_id") or ""),
                "parent_provenance_id": str(
                    ev.params.get("parent_provenance_id") or ""
                ),
                "source_url": str(ev.params.get("source_url") or ""),
                "source_family": str(ev.params.get("source_family") or ""),
                "source_binding_provenance": str(
                    ev.params.get("source_binding_provenance") or ""
                ),
                "relation_provenance": str(ev.params.get("relation_provenance") or ""),
                "canonical_source_envelope_sha256": str(
                    ev.params.get("canonical_source_envelope_sha256") or ""
                ),
                "parent_source_envelope_sha256": str(
                    ev.params.get("parent_source_envelope_sha256") or ""
                ),
                "parent_source_origin": str(
                    ev.params.get("parent_source_origin") or ""
                ),
            }
            classify_artifact(
                artifact,
                source_origin=source_origin,
                workflow_kind=(
                    WorkflowKind.REAL_SOURCE_DERIVED
                    if is_real_record
                    else WorkflowKind.HYBRID_CAUSAL
                ),
                evidence_role=EvidenceRole.CAUSAL_SUPPORTING,
                workflow_id=sim.spec["world_id"],
                provenance_id=provenance_id,
            )
        elif ev.type in {
            "wiki_source_section",
            "wiki_source_relation",
            "wiki_claim_answer",
        }:
            is_section = ev.type == "wiki_source_section"
            is_source = ev.type in {"wiki_source_section", "wiki_source_relation"}
            source_origin = (
                SourceOrigin(str(ev.params.get("source_origin") or "real_derived"))
                if is_source
                else SourceOrigin.SYNTHETIC_WORLD
            )
            provenance_id = (
                str(ev.params["provenance_id"])
                if is_source
                else "derived-sha256:"
                + hashlib.sha256(artifact.text.encode()).hexdigest()
            )
            artifact.slots = {
                **artifact.slots,
                "real_workflow_record": is_section,
                "source_workflow_id": str(ev.params["workflow_id"]),
                "source_record_id": str(ev.params.get("record_id") or ""),
                "parent_provenance_id": str(
                    ev.params.get("parent_provenance_id") or ""
                ),
                "source_url": str(ev.params.get("source_url") or ""),
                "source_family": str(ev.params.get("source_family") or ""),
                "source_binding_provenance": str(
                    ev.params.get("source_binding_provenance") or ""
                ),
                "relation_provenance": str(ev.params.get("relation_provenance") or ""),
                "parent_source_origin": str(
                    ev.params.get("parent_source_origin") or ""
                ),
            }
            classify_artifact(
                artifact,
                source_origin=source_origin,
                workflow_kind=WorkflowKind.HYBRID_CAUSAL,
                evidence_role=(
                    EvidenceRole.CAUSAL_SUPPORTING
                    if is_section
                    else EvidenceRole.CAUSAL_GOLD
                ),
                workflow_id=sim.spec["world_id"],
                provenance_id=provenance_id,
            )
        artifacts.append(artifact)
    artifacts.sort(key=lambda a: (a.time, a.artifact_id))
    return artifacts
