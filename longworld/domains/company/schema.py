from __future__ import annotations

import random
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from longworld.core.grounded import pick_anchors
from longworld.core.process import assign_register, attach_roles, sample_process
from longworld.domains.company.names import (
    DEPARTMENTS,
    FIRST,
    LAST,
    sample_company,
    sample_customer,
    sample_project_name,
)

if TYPE_CHECKING:
    from longworld.core.sourceworkflow import SourceWorkflow

SCHEMA_VERSION = "p1.1"

EVENT_TYPES = [
    "sign_contract",
    "change_roadmap",
    "client_cite_old",
    "legal_supplement",
    "grant_access",
    "release_beta",
    "misrecord_revenue",
    "audit_correction",
    "client_followup",
    "finance_preview",
    "legal_reminder",
    "standup_notes",
    "status_pulse",
    "renewal_roadmap",
    "renewal_amendment",
    "renewal_release",
    "renewal_close",
    "renewal_audit",
    "cycle_plan",
    "cycle_failure",
    "cycle_recovery",
    "cycle_release",
    "cycle_audit",
    "sec_filing",
    "sec_filing_eligibility_policy",
    "sec_filing_approval",
    "sec_amendment_resolution",
    "sec_filing_publication_ratification",
    "sec_source_section",
    "sec_financial_answer",
    "issuer_ir_source_section",
    "issuer_ir_prior_filing_relation",
    "issuer_ir_cross_year_answer",
]

ARTIFACT_KEYS_CORE = [
    "contract_email",
    "client_email",
    "legal_email",
    "roadmap_notes",
    "beta_notes",
    "standup_notes",
    "finance_june",
    "finance_july",
    "access_email",
    "legal_reminder",
    "client_followup",
    "finance_preview",
    "announce_hold",
    "renewal_roadmap",
    "renewal_amendment",
    "renewal_release",
    "renewal_close",
    "renewal_audit",
]


def _pick(rng: random.Random, items: list[str], used: set[str] | None = None) -> str:
    pool = [x for x in items if used is None or x not in used]
    if not pool:
        pool = list(items)
    choice = rng.choice(pool)
    if used is not None:
        used.add(choice)
    return choice


def _person(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name not in used:
            used.add(name)
            return name


def sample_world_spec(
    seed: int,
    n_parallel: int = 2,
    n_pulses: int = 0,
    n_workstreams: int = 0,
    source_workflows: list[SourceWorkflow] | None = None,
) -> dict[str, Any]:
    """Fully deterministic world definition. Parallel projects share the seed family."""
    if not 0 <= n_workstreams <= 64:
        raise ValueError("n_workstreams must be between 0 and 64")
    workflows = list(source_workflows or [])
    if any(
        workflow.target_domain != "company"
        or workflow.source_kind not in {"sec_filing", "issuer_ir_filing"}
        for workflow in workflows
    ):
        raise ValueError("company requires filing source workflows")
    rng = random.Random(seed)
    used_names: set[str] = set()
    used_projects: set[str] = set()
    used_customers: set[str] = set()
    used_companies: set[str] = set()

    start = date(2026, 1, 6) + timedelta(days=rng.randrange(0, 14))

    def offsets(renewal_rng: random.Random) -> dict[str, int]:
        # Strict order with gaps so historical_state has a clean cut.
        return {
            "sign_contract": 0,
            "change_roadmap": rng.randint(18, 28),
            "client_cite_old": rng.randint(32, 40),
            "legal_supplement": rng.randint(48, 58),
            "grant_access": rng.randint(60, 66),
            "release_beta": rng.randint(70, 80),
            "misrecord_revenue": rng.randint(88, 96),
            "audit_correction": rng.randint(110, 122),
            "client_followup": rng.randint(42, 46),
            "finance_preview": rng.randint(84, 86),
            "legal_reminder": rng.randint(100, 108),
            "standup_notes": rng.randint(72, 78),
            "carveout": 59,
            "announce_hold": 82,
            "rollback_amendment": rng.randint(124, 132),
            "latent_seed": 8,
            "latent_ack": 99,
            "latent_reopen": 136,
            "latent_ratify": 150,
            "renewal_roadmap": renewal_rng.randint(178, 190),
            "renewal_amendment": renewal_rng.randint(208, 220),
            "renewal_release": renewal_rng.randint(236, 248),
            "renewal_close": renewal_rng.randint(270, 282),
            "renewal_audit": renewal_rng.randint(306, 320),
        }

    def pulse_offsets(core: dict[str, int], n: int) -> list[int]:
        occupied = set(core.values())
        out: list[int] = []
        lo, hi = 3, 118
        guard = 0
        while len(out) < n and guard < 400:
            guard += 1
            day = rng.randint(lo, hi)
            if day in occupied:
                continue
            occupied.add(day)
            out.append(day)
        out.sort()
        return out

    def one_project(kind: str, is_focal: bool) -> dict[str, Any]:
        renewal_rng = random.Random(
            f"company-renewal:{seed}:{kind}:{len(used_projects)}"
        )
        pm = _person(rng, used_names)
        counsel = _person(rng, used_names)
        auditor = _person(rng, used_names)
        csm = _person(rng, used_names)
        finance = _person(rng, used_names)
        eng = _person(rng, used_names)
        customer_contact = _person(rng, used_names)
        project = sample_project_name(rng, used_projects)
        customer = sample_customer(rng, used_customers)
        company = sample_company(rng, used_companies)
        dept_legal = DEPARTMENTS[2]
        dept_rev = DEPARTMENTS[0]
        dept_prod = DEPARTMENTS[1]

        code = project[:3].upper()
        signed_version = f"RV-{code}{rng.randint(100, 999)}-S"
        roadmap_version = f"RV-{code}{rng.randint(100, 999)}-R"
        while roadmap_version == signed_version:
            roadmap_version = f"RV-{code}{rng.randint(100, 999)}-R"
        cf_version = f"RV-{code}{rng.randint(100, 999)}-X"
        while cf_version in {signed_version, roadmap_version}:
            cf_version = f"RV-{code}{rng.randint(100, 999)}-X"
        beta_tag = f"BT-{code}{rng.randint(1000, 9999)}"
        renewal_roadmap_version = f"RV-{code}{renewal_rng.randint(100, 999)}-N"
        while renewal_roadmap_version in {
            signed_version,
            roadmap_version,
            cf_version,
        }:
            renewal_roadmap_version = f"RV-{code}{renewal_rng.randint(100, 999)}-N"
        renewal_beta_tag = f"BT-{code}{renewal_rng.randint(1000, 9999)}-N"
        supplement_version = (
            roadmap_version
            if is_focal
            else rng.choice([signed_version, roadmap_version])
        )

        v2_deliv = f"{project}-batch-api-{rng.randint(11, 19)}"
        v3_deliv = f"{project}-stream-api-{rng.randint(21, 29)}"
        if not is_focal:
            v2_deliv = f"{project}-batch-api-{rng.randint(31, 39)}"
            v3_deliv = f"{project}-stream-api-{rng.randint(41, 49)}"

        # World-private amounts (not round thousands).
        mis = 100000 + rng.randint(17000, 49000) + rng.choice([13, 37, 41, 73])
        corrected = mis - rng.randint(28000, 52000) - rng.choice([9, 17, 23])
        if not is_focal:
            mis = 200000 + rng.randint(11000, 33000) + rng.choice([11, 19])
            corrected = mis - rng.randint(15000, 40000) - rng.choice([3, 7])
        renewal_mis = (
            corrected + renewal_rng.randint(42000, 78000) + renewal_rng.choice([13, 29])
        )
        renewal_corrected = (
            renewal_mis - renewal_rng.randint(9000, 26000) - renewal_rng.choice([7, 17])
        )

        contract_id = f"C-{rng.randint(4100, 9899)}-{code}"
        off = offsets(renewal_rng)
        pulses = pulse_offsets(off, n_pulses)
        people = {
            "pm": pm,
            "counsel": counsel,
            "auditor": auditor,
            "csm": csm,
            "finance": finance,
            "eng": eng,
            "customer_contact": customer_contact,
        }
        workstream_kinds = (
            "contract-migration",
            "privacy-remediation",
            "billing-recovery",
            "supplier-transition",
            "security-hardening",
            "data-residency",
            "platform-upgrade",
            "audit-remediation",
        )
        failure_modes = (
            "acceptance-suite regression",
            "regional policy conflict",
            "billing reconciliation mismatch",
            "supplier certification lapse",
            "security control regression",
            "residency routing violation",
            "upgrade compatibility break",
            "audit evidence gap",
        )
        workstreams = []
        if is_focal:
            for index in range(n_workstreams):
                stream_rng = random.Random(
                    f"company-cycle:{seed}:{project}:{contract_id}:{index}"
                )
                stream_kind = workstream_kinds[index % len(workstream_kinds)]
                close_amount = (
                    corrected + 23000 + index * 977 + stream_rng.randint(101, 899)
                )
                audit_amount = close_amount - stream_rng.randint(1300, 8700)
                workstreams.append(
                    {
                        "index": index,
                        "id": f"{stream_kind}-{index + 1:03d}",
                        "kind": stream_kind,
                        "agreement_id": (
                            f"AGR-{code}-{index + 1:03d}-{stream_rng.randint(100, 999)}"
                        ),
                        "plan_version": (
                            f"PLN-{code}-{index + 1:03d}-{stream_rng.randint(1000, 9999)}"
                        ),
                        "candidate_token": (
                            f"RC-{code}-{index + 1:03d}-{stream_rng.randint(1000, 9999)}"
                        ),
                        "incident_token": (
                            f"INC-{code}-{index + 1:03d}-{stream_rng.randint(1000, 9999)}"
                        ),
                        "failure_mode": failure_modes[index % len(failure_modes)],
                        "resolution_token": (
                            f"FIX-{code}-{index + 1:03d}-{stream_rng.randint(1000, 9999)}"
                        ),
                        "release_token": (
                            f"REL-{code}-{index + 1:03d}-{stream_rng.randint(1000, 9999)}"
                        ),
                        "audit_amount": audit_amount,
                        "close_amount": close_amount,
                    }
                )
        return {
            "kind": kind,
            "is_focal": is_focal,
            "company": company,
            "project": project,
            "customer": customer,
            "people": people,
            "org": attach_roles(
                people,
                {
                    "customer_contact": "csm",
                },
            ),
            "process": sample_process(rng, "company"),
            "carveout_jurisdiction": f"J-{code}{rng.randint(10, 99)}",
            "departments": {
                "legal": dept_legal,
                "revenue": dept_rev,
                "product": dept_prod,
            },
            "contract_id": contract_id,
            "signed_version": signed_version,
            "roadmap_version": roadmap_version,
            "cf_version": cf_version,
            "beta_tag": beta_tag,
            "renewal_roadmap_version": renewal_roadmap_version,
            "renewal_beta_tag": renewal_beta_tag,
            "supplement_version": supplement_version,
            "v2_deliverable": v2_deliv,
            "v3_deliverable": v3_deliv,
            "misrecorded_revenue": mis,
            "audited_revenue": corrected,
            "renewal_misrecorded_revenue": renewal_mis,
            "renewal_audited_revenue": renewal_corrected,
            "event_offsets": off,
            "pulse_offsets": pulses,
            "n_pulses": n_pulses,
            "start": start.isoformat(),
            "latent_token": f"LT-{code}{rng.randint(1000, 9999)}",
            "decoy_latent_token": f"LD-{code}{rng.randint(1000, 9999)}",
            **pick_anchors(rng),
            "docket_token": f"DK-{code}{rng.randint(1000, 9999)}",
            "workstreams": workstreams,
        }

    focal = one_project("focal", True)
    focal["source_workflows"] = workflows
    focal["source_workflow_ids"] = [workflow.workflow_id for workflow in workflows]
    parallels = [one_project("parallel", False) for _ in range(n_parallel)]
    assign_register(seed, focal, parallels)
    world_id = f"w{seed:06d}-{focal['project'].lower()}"
    return {
        "world_id": world_id,
        "seed": seed,
        "schema_version": SCHEMA_VERSION,
        "domain": "company",
        "truth_regime": (
            "verified_real_content_hybrid"
            if workflows
            else "real_schema_synthetic_instance"
        ),
        "n_pulses": n_pulses,
        "start": start.isoformat(),
        "focal": focal,
        "parallels": parallels,
        "n_parallel": n_parallel,
        "n_workstreams": n_workstreams,
    }
