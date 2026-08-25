from __future__ import annotations

import hashlib
from typing import Any

from longworld.core.cascade import apply_cascade, check_cascade
from longworld.core.grounded import apply_grounded, check_grounded
from longworld.core.scholarly import format_revision_funding_delta
from longworld.core.state import WorldState
from longworld.core.world import Event


def _format_revision_delta(value: str) -> str:
    return format_revision_funding_delta(value)


def init_values(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "reported_score": None,
        "eval_stale": False,
        "testset_ok": True,
        "rerun_score": None,
        "table_score": None,
        "body_score": None,
        "authoritative_score": None,
        "tokenizer_commit": None,
        "cause_cache": None,
        "cause_split": None,
        "inflation_cause": None,
        "issue_filed": False,
        "fixed": False,
        "release_published": False,
        "pending_license": None,
        "ship_license": None,
        "paper": project["paper"],
        "model": project["model"],
        "benchmark": project["benchmark"],
        "withdrawn_score": None,
        "score_license_at_withdraw": None,
        "dataset_version": None,
        "pending_public_primary": None,
        "pending_public_alt": None,
        "public_norm": None,
        "pending_latent": None,
        "pending_decoy": None,
        "acked_latent": None,
        "active_latent": None,
        "controlling_latent": None,
        "pending_docket": None,
        "controlling_docket": None,
        "revision_candidate": None,
        "benchmark_candidate": None,
        "active_review_requirement": None,
        "active_review_response": None,
        "failed_reproduction": None,
        "benchmark_frozen": None,
        "successful_reproduction": None,
        "resolved_review_requirement": None,
        "resolved_review_response": None,
        "resolved_revision": None,
        "accepted_revision": None,
        "accepted_benchmark": None,
        "accepted_reproduction": None,
        "accepted_review_response": None,
        "experiment_matrix": None,
        "source_record_texts": {},
        "source_record_metadata": {},
        "real_revision_delta_candidate": None,
        "real_revision_added_text": None,
    }


def check_preconditions(state: WorldState, ev: Event) -> tuple[bool, str | None]:
    grounded = check_grounded(state, ev)
    if grounded is not None:
        return grounded
    casc = check_cascade(state, ev)
    if casc is not None:
        return casc
    t = ev.type
    if t in {"report_v1", "license_clause"}:
        return True, None
    if t == "arxiv_revision":
        return True, None
    if t == "arxiv_revision_relation":
        texts = state.values.get("source_record_texts") or {}
        if not all(
            texts.get(str(ev.params.get(field) or ""))
            for field in ("source_record_id", "target_record_id")
        ):
            return False, "source_revision_endpoint_missing"
        return True, None
    if t == "arxiv_revision_decision":
        if not state.values.get("real_revision_delta_candidate"):
            return False, "source_revision_relation_missing"
        return True, None
    if t in {"commit_tokenizer", "log_stale_cache", "issue_testset", "status_pulse"}:
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "fix_rerun":
        if not state.values.get("issue_filed"):
            return False, "issue_not_filed"
        return True, None
    if t in {"camera_ready", "release_note"}:
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "invalidate_run":
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "submit_revision_2":
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "review_round_1":
        if not state.values.get("revision_candidate"):
            return False, "no_revision_2"
        return True, None
    if t == "respond_round_1":
        if not state.values.get("active_review_requirement"):
            return False, "no_review_round_1"
        return True, None
    if t == "reproduction_failure":
        if not state.values.get("active_review_response"):
            return False, "no_response_round_1"
        return True, None
    if t == "benchmark_patch":
        if not state.values.get("failed_reproduction"):
            return False, "no_failed_reproduction"
        return True, None
    if t == "review_round_2":
        if not state.values.get("benchmark_frozen"):
            return False, "no_benchmark_patch"
        return True, None
    if t == "respond_round_2":
        if not state.values.get("active_review_requirement"):
            return False, "no_review_round_2"
        return True, None
    if t == "reproduction_recovery":
        if not state.values.get("active_review_response"):
            return False, "no_response_round_2"
        if not state.values.get("benchmark_frozen"):
            return False, "no_frozen_benchmark"
        return True, None
    if t == "submit_revision_3":
        if not state.values.get("successful_reproduction"):
            return False, "no_successful_reproduction"
        return True, None
    if t == "resolve_review":
        if not state.values.get("revision_candidate"):
            return False, "no_revision_3"
        if not state.values.get("active_review_response"):
            return False, "no_response_round_2"
        return True, None
    if t == "meta_decision":
        if not state.values.get("resolved_revision"):
            return False, "review_not_resolved"
        if not state.values.get("successful_reproduction"):
            return False, "reproduction_not_recovered"
        return True, None
    workstream = str(ev.params.get("workstream") or "")
    if t == "experiment_revision":
        return True, None
    if t == "experiment_review":
        if not state.values.get(f"experiment:{workstream}:revision"):
            return False, "experiment_revision_missing"
        return True, None
    if t == "experiment_benchmark":
        if not state.values.get(f"experiment:{workstream}:review"):
            return False, "experiment_review_missing"
        return True, None
    if t == "experiment_failure":
        if not state.values.get(f"experiment:{workstream}:benchmark"):
            return False, "experiment_benchmark_missing"
        return True, None
    if t == "experiment_recovery":
        if not state.values.get(f"experiment:{workstream}:failure"):
            return False, "experiment_failure_missing"
        return True, None
    if t == "experiment_matrix_decision":
        workstream_ids = [str(value) for value in ev.params.get("workstream_ids") or []]
        if not workstream_ids or any(
            not state.values.get(f"experiment:{workstream_id}:recovery")
            for workstream_id in workstream_ids
        ):
            return False, "experiment_recovery_set_incomplete"
        return True, None
    return True, None


def apply_event(state: WorldState, ev: Event) -> None:
    if apply_grounded(state, ev):
        return
    if apply_cascade(state, ev):
        return
    t = ev.type
    p = ev.params
    eid = ev.id
    day = ev.time
    if t == "license_clause":
        state.set("pending_license", p["spdx"], eid, day)
    elif t == "arxiv_revision":
        text = str(p.get("text") or "")
        if hashlib.sha256(text.encode()).hexdigest() != p.get("text_sha256"):
            return
        texts = dict(state.values.get("source_record_texts") or {})
        texts[str(p["record_id"])] = text
        state.set("source_record_texts", texts, eid, day)
        metadata = dict(state.values.get("source_record_metadata") or {})
        metadata[str(p["record_id"])] = {
            "occurred_at": str(p.get("occurred_at") or ""),
            "revision_id": str(p.get("revision_id") or ""),
        }
        state.set("source_record_metadata", metadata, eid, day)
    elif t == "arxiv_revision_relation":
        texts = state.values.get("source_record_texts") or {}
        source_text = str(texts.get(str(p["source_record_id"])) or "")
        target_text = str(texts.get(str(p["target_record_id"])) or "")
        if not source_text or not target_text:
            return
        evidence_quote = str(p["evidence_quote"])
        if not evidence_quote:
            return
        start = int(p["fact_char_start"])
        end = int(p["fact_char_end"])
        offset = int(p["fact_value_offset"])
        value_length = int(p["fact_value_length"])
        quote = source_text[start:end]
        value = quote[offset : offset + value_length]
        if not value or value in target_text:
            return
        metadata = state.values.get("source_record_metadata") or {}
        prior = metadata.get(str(p["target_record_id"])) or {}
        prior_date = str(prior.get("occurred_at") or "")[:10]
        if not prior_date:
            return
        state.set(
            "real_revision_delta_candidate",
            {"prior_date": prior_date, "value": value},
            eid,
            day,
        )
    elif t == "arxiv_revision_decision":
        candidate = state.values.get("real_revision_delta_candidate")
        if not isinstance(candidate, dict):
            return
        answer = _format_revision_delta(str(candidate.get("value") or ""))
        prior_date = str(candidate.get("prior_date") or "")
        if answer and prior_date:
            state.set("real_revision_added_text", f"{prior_date} | {answer}", eid, day)
    elif t == "report_v1":
        state.set("reported_score", p["score"], eid, day)
        state.set("authoritative_score", p["score"], eid, day)
        state.set("body_score", p["score"], eid, day)
        state.set("eval_stale", True, eid, day)
        state.set("testset_ok", False, eid, day)
    elif t == "commit_tokenizer":
        state.set("tokenizer_commit", p["commit"], eid, day)
    elif t == "log_stale_cache":
        state.set("cause_cache", p["cause_cache"], eid, day)
    elif t == "issue_testset":
        if p.get("filed", True):
            state.set("issue_filed", True, eid, day)
            state.set("cause_split", p["cause_split"], eid, day)
    elif t == "fix_rerun":
        if p.get("aborted"):
            return
        state.set("eval_stale", False, eid, day)
        state.set("testset_ok", True, eid, day)
        state.set("fixed", True, eid, day)
        state.set("rerun_score", p["score"], eid, day)
        state.set("table_score", p["score"], eid, day)
    elif t == "camera_ready":
        # Table follows rerun if present; body may linger at v1.
        table = state.values.get("rerun_score") or state.values.get("reported_score")
        body = p.get("body_score", state.values.get("reported_score"))
        state.set("table_score", table, eid, day)
        state.set("body_score", body, eid, day)
    elif t == "release_note":
        state.set("release_published", True, eid, day)
        if p.get("adopt_rerun"):
            rs = state.values.get("rerun_score")
            if rs is not None:
                state.set("authoritative_score", rs, eid, day)
            else:
                state.set(
                    "authoritative_score", state.values.get("reported_score"), eid, day
                )
        else:
            state.set(
                "authoritative_score",
                p.get("score", state.values.get("reported_score")),
                eid,
                day,
            )
        pending = state.values.get("pending_license")
        if pending:
            state.set("ship_license", pending, eid, day)
    elif t == "invalidate_run":
        state.set("dataset_version", p["dataset"], eid, day)
        rs = state.values.get("reported_score")
        pending = state.values.get("pending_license")
        if rs is not None:
            state.set("withdrawn_score", rs, eid, day)
        elif pending is not None:
            state.set("withdrawn_score", 0, eid, day)
        else:
            return
        if pending is None:
            return
        state.set("score_license_at_withdraw", pending, eid, day)
    elif t == "status_pulse":
        return
    elif t == "submit_revision_2":
        state.set("revision_candidate", p["revision"], eid, day)
        state.set("benchmark_candidate", p["benchmark"], eid, day)
    elif t in {"review_round_1", "review_round_2"}:
        state.set("active_review_requirement", p["requirement"], eid, day)
    elif t in {"respond_round_1", "respond_round_2"}:
        state.set("active_review_response", p["response"], eid, day)
    elif t == "reproduction_failure":
        state.set("failed_reproduction", p["run"], eid, day)
    elif t == "benchmark_patch":
        state.set("benchmark_frozen", p["benchmark"], eid, day)
    elif t == "reproduction_recovery":
        state.set("successful_reproduction", p["run"], eid, day)
    elif t == "submit_revision_3":
        state.set("revision_candidate", p["revision"], eid, day)
    elif t == "resolve_review":
        state.set(
            "resolved_review_requirement",
            state.values.get("active_review_requirement"),
            eid,
            day,
        )
        state.set(
            "resolved_review_response",
            state.values.get("active_review_response"),
            eid,
            day,
        )
        state.set("resolved_revision", state.values.get("revision_candidate"), eid, day)
    elif t == "meta_decision":
        if not p.get("accepted", True):
            return
        state.set("accepted_revision", state.values.get("resolved_revision"), eid, day)
        state.set("accepted_benchmark", state.values.get("benchmark_frozen"), eid, day)
        state.set(
            "accepted_reproduction",
            state.values.get("successful_reproduction"),
            eid,
            day,
        )
        state.set(
            "accepted_review_response",
            state.values.get("resolved_review_response"),
            eid,
            day,
        )
    elif t == "experiment_revision":
        state.set(f"experiment:{p['workstream']}:revision", p["revision"], eid, day)
    elif t == "experiment_review":
        state.set(f"experiment:{p['workstream']}:review", p["review"], eid, day)
    elif t == "experiment_benchmark":
        state.set(f"experiment:{p['workstream']}:benchmark", p["benchmark"], eid, day)
    elif t == "experiment_failure":
        state.set(f"experiment:{p['workstream']}:failure", p["failure"], eid, day)
    elif t == "experiment_recovery":
        state.set(f"experiment:{p['workstream']}:recovery", p["run"], eid, day)
    elif t == "experiment_matrix_decision":
        workstream_ids = [str(value) for value in p.get("workstream_ids") or []]
        runs = [
            str(state.values.get(f"experiment:{workstream_id}:recovery") or "")
            for workstream_id in workstream_ids
        ]
        if not workstream_ids or any(not run for run in runs):
            return
        state.set("experiment_matrix", " | ".join(runs), eid, day)
