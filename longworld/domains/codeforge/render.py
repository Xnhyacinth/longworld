from __future__ import annotations

import hashlib

from longworld.core.grounded import SKIP_RENDER_TYPES
from longworld.core.render import Artifact
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    classify_artifact,
)
from longworld.core.world import Event, SimulatedWorld
from longworld.domains.codeforge.schema import materialize_grounded_repo_record


def _ground(ev: Event) -> list[str]:
    p = ev.params
    t = ev.type
    if t == "repo_record":
        values: list[str] = []
        for key in (
            "commit",
            "package",
            "version",
            "run",
            "test",
            "tag",
            "license",
            "result",
        ):
            value = str(p.get(key) or "")
            if value and value.lower() in _repo_record_body(ev).lower():
                values.append(value)
        return values
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
    if t == "rollback_hotfix":
        return ["revert-hotfix"]
    if t == "dependency_request":
        return [
            str(p["stream"]),
            str(p["package"]),
            str(p["version"]),
            str(p["license"]),
            str(p["advisory"]),
            str(p["docket"]),
        ]
    if t == "maintainer_review":
        return [str(p["stream"]), str(p["decision"]), str(p["review_token"])]
    if t == "ci_validation":
        outcome = "passed" if p.get("passed") else "failed"
        return [str(p["stream"]), str(p["ci_token"]), outcome]
    if t == "license_clearance":
        decision = "compatible" if p.get("compatible") else "incompatible"
        return [str(p["stream"]), str(p["clearance_token"]), decision]
    if t == "integration_merge":
        return [str(p["stream"]), str(p["merge_token"])]
    if t == "release_decision":
        return [str(p["policy"]), *[str(item) for item in p["streams"]]]
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
    return []


def _text(project: dict, ev: Event) -> tuple[str, str]:
    t = ev.type
    date_s = ev.time.isoformat()
    repo, pkg = project["repo"], project["package"]
    test = project["test_name"]
    if t == "repo_record":
        return "json", _repo_record_body(ev)
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
    if t == "rollback_hotfix":
        return "code", (
            f"# {repo} revert note ({date_s})\n"
            f"Status: revert-hotfix. Emergency rollback restores the pre-hotfix "
            f"object as HEAD. This note does not reprint a hash. Reconstruct "
            f"HEAD from the original broken commit object. The git tag remains "
            f"a separate authority and is not updated by this revert."
        )
    if t == "dependency_request":
        p = ev.params
        return "report", (
            f"# Dependency change request · {repo}\n"
            f"Filed: {date_s} · release docket {p['docket']}\n"
            f"Workstream {p['stream']} proposes {p['package']} version "
            f"{p['version']}. The upstream declaration is {p['license']}; the "
            f"change also responds to advisory {p['advisory']}. Maintainers must "
            f"review {p['package']} at {p['version']}, CI must test that reviewed "
            f"change, and the {p['stream']} license job must independently clear "
            f"the declared terms before integration. Request {p['advisory']} records "
            f"intent for {p['package']}, not merge status. Relative to {p['stream']}, "
            f"other docket workstreams retain separate version, review, CI, and "
            f"clearance records."
        )
    if t == "maintainer_review":
        p = ev.params
        return "email", (
            f"Subject: maintainer review for {p['stream']} · {repo}\n"
            f"Date: {date_s}\nReview receipt: {p['review_token']}\n"
            f"Decision: {p['decision']}. The review covers the dependency request "
            f"for {p['package']}, its API impact, rollback boundary, and ownership "
            f"for {p['stream']} downstream breakage. Receipt {p['review_token']} may "
            f"send {p['package']} into CI, but it is neither a test result nor a "
            f"license decision. The exact {p['stream']} version remains in its earlier "
            f"change request so release assembly can distinguish reviewed intent from "
            f"integrated state."
        )
    if t == "ci_validation":
        p = ev.params
        outcome = "passed" if p.get("passed") else "failed"
        return "log", (
            f"=== integration CI · {repo} · {date_s} ===\n"
            f"Workstream {p['stream']} exercised package {p['package']}.\n"
            f"run receipt {p['ci_token']} · result: {outcome}\n"
            f"For {p['package']} in {p['stream']}, unit, compatibility, upgrade, and "
            f"rollback suites completed against the maintainer-reviewed request. Run "
            f"{p['ci_token']} references the {p['stream']} review chain instead of "
            f"copying its requested dependency version. A green {p['package']} run "
            f"permits integration only when the independent {p['stream']} license "
            f"clearance is also compatible."
        )
    if t == "license_clearance":
        p = ev.params
        decision = "compatible" if p.get("compatible") else "incompatible"
        integration_effect = (
            "permits integration when the separate CI receipt is green"
            if p.get("compatible")
            else "blocks integration even when the separate CI receipt is green"
        )
        return "report", (
            f"# License compatibility decision · {repo}\n"
            f"Date: {date_s} · workstream {p['stream']}\n"
            f"Clearance receipt {p['clearance_token']}: {decision}. Counsel checked "
            f"the declaration attached to the dependency request against the "
            f"{p['package']} distribution policy, notice obligations, and binary "
            f"redistribution path. Decision {p['clearance_token']} for {p['stream']} "
            f"does not approve code quality and does not repeat its requested version. "
            f"This decision {integration_effect} for {p['package']} in "
            f"{p['stream']}."
        )
    if t == "integration_merge":
        p = ev.params
        return "code", (
            f"integration gate receipt {p['merge_token']}\nRepository: {repo}\n"
            f"Date: {date_s}\nWorkstream {p['stream']} is evaluated for the release "
            f"integration branch. Gate {p['merge_token']} adopts {p['package']} from "
            f"the original request only when {p['stream']} CI is green and its "
            f"independent license decision is compatible. This {p['stream']} receipt "
            f"does not restate the requested version or decide any other workstream "
            f"in the docket."
        )
    if t == "release_decision":
        p = ev.params
        streams = ", ".join(str(item) for item in p["streams"])
        return "report", (
            f"# Release gate decision · {repo}\nDate: {date_s}\n"
            f"Gate policy: {p['policy']}. Workstreams in scope: {streams}. "
            f"The gate inspected one integration receipt per listed workstream and "
            f"accepted only receipts backed by maintainer review, green CI, and a "
            f"compatible license decision. The derived outcome is approved only when "
            f"every listed stream integrated; otherwise it is blocked. An approved "
            f"manifest takes versions in listed order without duplicating them here. "
            f"A blocked outcome instead refers "
            f"back to the release docket carried by the original requests."
        )
    if t == "cross_repo_integration":
        p = ev.params
        return "report", (
            f"# Synthetic executable cross-repository integration policy\n"
            f"Date: {date_s} · policy {p['policy_id']}\n"
            f"The simulated integration world requires the latest validated release "
            f"from {p['release_source_url']} together with the independently approved "
            f"merge from {p['merge_source_url']}. This policy declares only the "
            f"cross-project dependency. It does not claim to be a GitHub record and "
            f"does not repeat either repository's tag, commit, or license."
        )
    if t == "adopt_public":
        return "email", (
            f"Subject: public normative file adopted · {repo}\n"
            f"Date: {date_s}\n"
            f"Status: public-norm-adopted. Maintainers adopted the previously "
            f"ingested public file as the external normative reference. This "
            f"note does not reprint the public-document stem. Reconstruct the "
            f"stem from the ingested file. Alternate public files are not "
            f"adopted. Hashes and SPDX are not restated here."
        )
    if t == "seed_latent":
        return "log", (
            f"OPS NOTE {date_s} {repo}\n"
            f"Dormant incident file-code {ev.params['token']} is parked. This "
            f"note is not a git tag and not SPDX."
        )
    if t == "seed_decoy":
        return "log", (
            f"OPS NOTE {date_s} {repo}\n"
            f"Abandoned incident file-code {ev.params['token']} was parked and "
            f"never acknowledged. This note is not the later reopen authority."
        )
    if t == "seed_docket":
        return "log", (
            f"OPS NOTE {date_s} {repo}\n"
            f"Docket-code {ev.params['token']} is parked separately from the "
            f"incident file-code. This note is not a git tag and not SPDX. It "
            f"is not controlling until a later ratify instrument writes it."
        )
    if t == "ack_latent":
        return "email", (
            f"Subject: latent filing acknowledged · {repo}\nDate: {date_s}\n"
            f"Status: latent-acked. This ack does not reprint the file-code. "
            f"Reconstruct it from the parked ops note if a later reopen asks."
        )
    if t == "reopen_latent":
        return "email", (
            f"Subject: case reopened · {repo}\nDate: {date_s}\n"
            f"Status: case-reopened. The dormant file-code is now active. This "
            f"memo does not reprint the code. Reconstruct it from the seed note "
            f"via the ack. Changelog prose is not the authority."
        )
    if t == "ratify_latent":
        return "email", (
            f"Subject: case ratified · {repo}\nDate: {date_s}\n"
            f"Status: case-ratified. Reopen made a file-code active; this "
            f"instrument makes it controlling and adopts the early docket as "
            f"the controlling docket-code. This memo does not reprint the "
            f"file-code or the docket-code. Reconstruct both from their seed "
            f"notes via ack and reopen. Changelog prose is not the ratify "
            f"authority."
        )
    if t == "status_pulse":
        p = ev.params
        return "email", (
            f"Subject: {repo} weekly {p['week_index']}\nDate: {date_s}\n"
            f"Ticket {p['ticket']} blocked on {p['blocker']}. No hashes, no "
            f"SPDX, no flake tokens restated."
        )
    raise KeyError(t)


def _repo_record_body(ev: Event) -> str:
    p = ev.params
    materialized = materialize_grounded_repo_record(p)
    p.update(materialized)
    body = str(materialized["body_text"])
    return (
        f"Repository workflow record {p['record_id']}\n"
        f"Occurred: {ev.time.isoformat()}\n"
        "Source body (verbatim unless this is a counterfactual view):\n"
        f"{body}"
    )


def _visible_grounded_fact_spans(ev: Event, artifact_text: str) -> list[dict]:
    if ev.type != "repo_record":
        return []
    body = str(ev.params["body_text"])
    body_start = artifact_text.rfind(body)
    if body_start < 0:
        return []
    return [
        {
            **dict(span),
            "source_char_start": int(span["char_start"]),
            "source_char_end": int(span["char_end"]),
            "artifact_char_start": body_start + int(span["char_start"]),
            "artifact_char_end": body_start + int(span["char_end"]),
            "artifact_text_sha256": hashlib.sha256(artifact_text.encode()).hexdigest(),
        }
        for span in ev.params.get("grounded_fact_spans") or []
    ]


def render_code(sim: SimulatedWorld) -> list[Artifact]:
    project = sim.spec["project"]
    prefix = sim.spec["prefix"]
    is_focal = bool(project.get("is_focal", prefix == "focal"))
    artifacts: list[Artifact] = []
    for ev in sim.events:
        if ev.skipped or ev.type in SKIP_RENDER_TYPES:
            continue
        vis = ev.visibility[0]
        aid = f"{sim.spec['world_id']}.{vis}"
        doc_type, text = _text(project, ev)
        artifacts.append(
            _classify_repo_artifact(
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
                        "visible_grounded_fact_spans": (
                            _visible_grounded_fact_spans(ev, text)
                        ),
                    },
                    is_focal=is_focal,
                    intentional_stale=ev.type == "changelog_stale",
                    stale_event_id=ev.id if ev.type == "changelog_stale" else None,
                ),
                ev,
                sim.spec["world_id"],
            )
        )
    artifacts.sort(key=lambda a: (a.time, a.artifact_id))
    return artifacts


def _classify_repo_artifact(
    artifact: Artifact, ev: Event, coherence_workflow_id: str
) -> Artifact:
    if ev.type != "repo_record":
        return artifact
    source_facts = dict(ev.params.get("source_body_facts") or {})
    derived = any(
        key in ev.params and ev.params[key] != value
        for key, value in source_facts.items()
    )
    provenance_id = str(ev.params["provenance_id"])
    if derived:
        provenance_id = (
            "derived-sha256:" + hashlib.sha256(artifact.text.encode()).hexdigest()
        )
    artifact.slots = {
        **artifact.slots,
        "real_workflow_record": not derived,
        "source_workflow_id": str(ev.params["workflow_id"]),
        "source_record_id": str(ev.params["record_id"]),
        "source_pointer": str(ev.params.get("source_pointer") or ""),
        "source_url": str(ev.params["source_url"]),
        "base_provenance_id": str(ev.params["provenance_id"]),
        "parent_source_origin": (str(ev.params["source_origin"]) if derived else ""),
        "counterfactual_operation": "event_param_override" if derived else "",
        "source_body_sha256": str(ev.params["source_body_sha256"]),
        "visible_body_sha256": str(ev.params["body_sha256"]),
        "grounded_source_id": str(ev.params["grounded_source_id"]),
        "grounded_relations": list(ev.params.get("grounded_relations") or []),
        "grounded_relation_proof_mode": "structural_closure_only",
        "grounded_structural_relation_count": len(
            ev.params.get("grounded_relations") or []
        ),
        "grounded_semantic_relation_count": 0,
        "source_record_binding_sha256": str(ev.params["source_record_binding_sha256"]),
    }
    return classify_artifact(
        artifact,
        source_origin=(
            SourceOrigin.SYNTHETIC_WORLD
            if derived
            else SourceOrigin(str(ev.params["source_origin"]))
        ),
        workflow_kind=WorkflowKind.HYBRID_CAUSAL,
        evidence_role=EvidenceRole.CAUSAL_SUPPORTING,
        workflow_id=coherence_workflow_id,
        provenance_id=provenance_id,
    )
