"""World-process grammar: optional real events, not a second noun set.

A Domain is state semantics + event algebra + artifact ecology.
This module only samples which *process extensions* fire on a world.
Core scripts stay; extensions add Exception / Rollback / Exclusion.
"""

from __future__ import annotations

import random
from typing import Any

# Events that must not be included in core-query as_of, so current_state
# gold stays the pre-extension legal/HEAD/score.
EXTENSION_TYPES = frozenset(
    {
        "carveout",
        "rollback_amendment",
        "rollback_hotfix",
        "invalidate_run",
    }
)

ROLE_CATALOG: dict[str, dict[str, Any]] = {
    "counsel": {
        "title": "legal_counsel",
        "team": "legal",
        "responsibilities": ["file_amendment", "file_carveout", "withdraw_amendment"],
        "knowledge_scope": ["contract", "amendment", "carveout"],
        "visibility_channels": ["legal_email", "carveout_memo", "rollback_memo"],
        "authority": ["set_legal_version"],
    },
    "pm": {
        "title": "program_manager",
        "team": "product",
        "responsibilities": ["roadmap", "customer_comm"],
        "knowledge_scope": ["roadmap", "beta"],
        "visibility_channels": ["roadmap_notes", "standup_notes", "announce_hold"],
        "authority": ["propose_version", "hold_announcement"],
    },
    "auditor": {
        "title": "internal_auditor",
        "team": "audit",
        "responsibilities": ["restate_revenue"],
        "knowledge_scope": ["ledger"],
        "visibility_channels": ["finance_july"],
        "authority": ["set_recognized_revenue"],
    },
    "finance": {
        "title": "revenue_accountant",
        "team": "finance",
        "responsibilities": ["period_close"],
        "knowledge_scope": ["ledger"],
        "visibility_channels": ["finance_june", "finance_preview"],
        "authority": [],
    },
    "eng": {
        "title": "staff_engineer",
        "team": "engineering",
        "responsibilities": ["cut_beta"],
        "knowledge_scope": ["release"],
        "visibility_channels": ["beta_notes"],
        "authority": [],
    },
    "csm": {
        "title": "customer_success",
        "team": "gtm",
        "responsibilities": ["customer_belief"],
        "knowledge_scope": ["signed_packet"],
        "visibility_channels": ["client_email", "client_followup"],
        "authority": [],
    },
    "pi": {
        "title": "research_lead",
        "team": "lab",
        "responsibilities": ["release_note", "invalidate_run"],
        "knowledge_scope": ["scores", "dataset"],
        "visibility_channels": ["release_note", "dataset_card"],
        "authority": ["adopt_score", "withdraw_score"],
    },
    "student": {
        "title": "evaluation_engineer",
        "team": "benchmark_team",
        "responsibilities": ["rerun", "logs"],
        "knowledge_scope": ["experiment_runs"],
        "visibility_channels": ["eval_log", "rerun_json", "github_issue"],
        "authority": ["file_issue"],
    },
    "reviewer": {
        "title": "external_reviewer",
        "team": "venue",
        "responsibilities": ["camera_ready_quote"],
        "knowledge_scope": ["draft_pdf"],
        "visibility_channels": ["camera_ready"],
        "authority": [],
    },
    "owner": {
        "title": "maintainer",
        "team": "engineering",
        "responsibilities": ["hotfix", "revert"],
        "knowledge_scope": ["head", "ci"],
        "visibility_channels": ["hotfix_commit", "rollback_note"],
        "authority": ["move_head"],
    },
}


# Workplace document register. Sampled once per world, independent of the
# schema RNG so gold tokens stay seed-stable. Not a paraphrase sentence bank.
REGISTERS = ("instrument", "ops_log", "standup", "audit_trail")
REGISTER_MARKERS = {
    "instrument": "Filed as a numbered workspace instrument",
    "ops_log": "Ops log entry, terse and time-stamped",
    "standup": "Standup brief: we parked the follow-up",
    "audit_trail": "Audit trail for later reconstruction",
}


def sample_register(rng: random.Random) -> str:
    return rng.choice(REGISTERS)


def assign_register(
    seed: int,
    focal: dict[str, Any],
    parallels: list[dict[str, Any]] | None = None,
) -> str:
    """World-level register. Derived rng: do not consume the schema stream."""
    reg = sample_register(random.Random(int(seed) ^ 0x5EED57A1))
    focal["register"] = reg
    for p in parallels or []:
        p["register"] = reg
    return reg


def sample_process(rng: random.Random, domain: str) -> dict[str, bool]:
    """Independent Bernoulli flags. Core script always runs."""
    if domain == "company":
        return {
            "carveout": rng.random() < 0.62,
            "rollback": rng.random() < 0.58,
            "invalidate": False,
            "grounded": rng.random() < 0.88,
            "cascade": rng.random() < 0.80,
        }
    if domain == "researchlab":
        return {
            "carveout": False,
            "rollback": False,
            "invalidate": rng.random() < 0.72,
            "grounded": rng.random() < 0.88,
            "cascade": rng.random() < 0.80,
        }
    return {
        "carveout": False,
        "rollback": rng.random() < 0.72,
        "invalidate": False,
        "grounded": rng.random() < 0.88,
        "cascade": rng.random() < 0.80,
    }


def attach_roles(people: dict[str, str], mapping: dict[str, str]) -> dict[str, Any]:
    """Names last. Role graph is the identity."""
    out: dict[str, Any] = {}
    for slot, name in people.items():
        role_key = mapping.get(slot, slot)
        catalog = ROLE_CATALOG.get(role_key, {"title": role_key, "team": "unknown"})
        out[slot] = {
            "person_id": f"P-{slot}",
            "name": name,
            "role": catalog.get("title", role_key),
            "team": catalog.get("team", "unknown"),
            "responsibilities": list(catalog.get("responsibilities") or []),
            "knowledge_scope": list(catalog.get("knowledge_scope") or []),
            "visibility_channels": list(catalog.get("visibility_channels") or []),
            "authority": list(catalog.get("authority") or []),
        }
    return out


def process_on(project: dict[str, Any], flag: str) -> bool:
    proc = project.get("process") or {}
    return bool(proc.get(flag))
