from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from longworld.domains.company.names import (
    DEPARTMENTS,
    FIRST,
    LAST,
    sample_company,
    sample_customer,
    sample_project_name,
)

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
    seed: int, n_parallel: int = 2, n_pulses: int = 14
) -> dict[str, Any]:
    """Fully deterministic world definition. Parallel projects share the seed family."""
    rng = random.Random(seed)
    used_names: set[str] = set()
    used_projects: set[str] = set()
    used_customers: set[str] = set()
    used_companies: set[str] = set()

    start = date(2026, 1, 6) + timedelta(days=rng.randrange(0, 14))

    def offsets() -> dict[str, int]:
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

        contract_id = f"C-{rng.randint(4100, 9899)}-{code}"
        off = offsets()
        pulses = pulse_offsets(off, n_pulses)
        return {
            "kind": kind,
            "is_focal": is_focal,
            "company": company,
            "project": project,
            "customer": customer,
            "people": {
                "pm": pm,
                "counsel": counsel,
                "auditor": auditor,
                "csm": csm,
                "finance": finance,
                "eng": eng,
                "customer_contact": customer_contact,
            },
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
            "supplement_version": supplement_version,
            "v2_deliverable": v2_deliv,
            "v3_deliverable": v3_deliv,
            "misrecorded_revenue": mis,
            "audited_revenue": corrected,
            "event_offsets": off,
            "pulse_offsets": pulses,
            "n_pulses": n_pulses,
            "start": start.isoformat(),
        }

    focal = one_project("focal", True)
    parallels = [one_project("parallel", False) for _ in range(n_parallel)]
    world_id = f"w{seed:06d}-{focal['project'].lower()}"
    return {
        "world_id": world_id,
        "seed": seed,
        "schema_version": SCHEMA_VERSION,
        "domain": "company",
        "truth_regime": "real_schema_synthetic_instance",
        "n_pulses": n_pulses,
        "start": start.isoformat(),
        "focal": focal,
        "parallels": parallels,
        "n_parallel": n_parallel,
    }
