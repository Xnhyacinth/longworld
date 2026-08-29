from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from longworld.core.asof import core_as_of, find_event, world_as_of
from longworld.core.provenance import ProvenanceError
from longworld.core.secxbrl import parse_ixbrl_display_number
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.company.events import apply_event, check_preconditions


def instance_topology(family: str, *parts: Any) -> str:
    """Motif family plus instance tokens. Scale unit is this string, not QA count."""
    return family + ":" + "/".join(str(p) for p in parts)


@dataclass
class QuerySpec:
    query_id: str
    query_type: str
    question: str
    answer: str
    as_of: date | None
    answer_key: str
    essential_event_ids: list[str]
    essential_artifact_ids: list[str]
    sufficient_event_ids: list[str]
    cf_event_id: str
    cf_param_updates: dict[str, Any]
    cf_answer: str
    invariance_event_id: str | None
    invariance_param_updates: dict[str, Any] = field(default_factory=dict)
    gold_expression: str = ""
    proof_depth: int = 1
    cf_op: str = "version"  # version | amount | date | score
    question_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    motif: str = ""
    truth_regime: str = "real_schema_synthetic_instance"
    topology_id: str = ""
    domain: str = "company"
    program_ops: list[dict[str, Any]] = field(default_factory=list)
    preferred_length_buckets: list[str] = field(default_factory=list)
    semantic_growth_group: str = ""
    base_task_group: str = ""


def _sim_for(world: SimulatedWorld) -> WorldSimulator:
    return WorldSimulator(
        spec=world.spec,
        init_values=world.init_values,
        check_preconditions=check_preconditions,
        apply_event=apply_event,
    )


def state_from_artifacts(
    world: SimulatedWorld,
    artifact_event_ids: list[str],
    as_of: date | None = None,
    skip_ids: set[str] | None = None,
    param_overrides: dict[str, dict[str, Any]] | None = None,
):
    allowed = set(artifact_event_ids)
    events = [e for e in world.events if e.id in allowed]
    return _sim_for(world).replay_events(
        events, up_to=as_of, skip_ids=skip_ids, param_overrides=param_overrides
    )


def eval_answer(
    world: SimulatedWorld, spec: QuerySpec, state_values: dict[str, Any]
) -> str:
    key = spec.answer_key
    val = state_values.get(key)
    if spec.query_type.startswith("sec_financial_") and spec.query_type != (
        "sec_financial_reconstruction"
    ):
        if val != "READY":
            return "unknown"
        record_id = (
            key.removeprefix("sec_financial_facet_ready:").rsplit(":", 1)[0]
            if key.startswith("sec_financial_facet_ready:")
            else ""
        )
        xbrl = dict((state_values.get("sec_xbrl_facts") or {}).get(record_id) or {})
        certs = dict(
            (state_values.get("sec_certification_facts") or {}).get(record_id) or {}
        )
        return _financial_facet_answer(spec.query_type, xbrl, certs)
    if spec.query_type == "version_diff":
        v2 = state_values.get("v2_deliverable")
        v3 = state_values.get("v3_deliverable")
        if not v2 or not v3:
            return "unknown"
        return f"{v2} -> {v3}"
    if spec.query_type == "multi_hop":
        rec = state_values.get("revenue_recognized")
        mis = state_values.get("revenue_misrecorded")
        if not rec or not mis:
            return "unknown"
        return str(int(mis) - int(rec))
    if spec.query_type == "compare_belief":
        stale = state_values.get("stale_client_cite")
        legal = state_values.get("legal_effective_version")
        if not stale or not legal:
            return "unknown"
        return f"{stale} || {legal}"
    if spec.query_type == "delayed_effect":
        blocker = state_values.get("release_blocker")
        if not blocker:
            return "unknown"
        return str(blocker)
    if spec.query_type == "cross_stream":
        rec = state_values.get("revenue_recognized")
        legal = state_values.get("legal_effective_version")
        if rec in (None, 0, False) or not legal:
            return "unknown"
        return f"{rec}@{legal}"
    if spec.query_type == "renewal_control_trace":
        first = state_values.get("audit_cycle_1")
        renewal = state_values.get("audit_cycle_2")
        legal = state_values.get("renewal_legal_effective_version")
        release = state_values.get("renewal_release_version")
        if first in (None, 0, False) or renewal in (None, 0, False):
            return "unknown"
        if not legal or not release:
            return "unknown"
        return f"{first}->{renewal}@{legal}#{release}"
    if spec.query_type == "legal_financial_release_trace":
        legal = state_values.get("legal_effective_version")
        release = state_values.get("release_version")
        revenue = state_values.get("revenue_recognized")
        if not legal or not release or revenue in (None, 0, False):
            return "unknown"
        return f"{legal}#{release}@{revenue}"
    if val is None or val == 0 or val is False:
        return "unknown"
    return str(val)


def _indexed_values(values: dict[str, Any], prefix: str) -> list[int]:
    keys = sorted(
        (key for key in values if key.startswith(prefix)),
        key=lambda key: int(key.rsplit("_", 1)[1]),
    )
    return [int(values[key]) for key in keys]


def _financial_facet_answer(
    query_type: str, xbrl: dict[str, Any], certs: dict[str, Any]
) -> str:
    if query_type == "sec_financial_sales_mix":
        required = ("product_revenue", "service_revenue", "total_revenue")
        if any(role not in xbrl for role in required):
            return "unknown"
        gap = (
            int(xbrl["product_revenue"])
            + int(xbrl["service_revenue"])
            - int(xbrl["total_revenue"])
        )
        return (
            f"MIX:{xbrl['product_revenue']}+{xbrl['service_revenue']}="
            f"{xbrl['total_revenue']}|GAP:{gap}|"
            f"STATUS:{'PASS' if gap == 0 else 'FAIL'}"
        )
    if query_type == "sec_financial_category_geography":
        categories = _indexed_values(xbrl, "category_")
        geographies = _indexed_values(xbrl, "geo_")
        if not categories or not geographies:
            return "unknown"
        category_total = sum(categories)
        geography_total = sum(geographies)
        return (
            "CAT:"
            + "+".join(str(value) for value in categories)
            + f"={category_total}||GEO:"
            + "+".join(str(value) for value in geographies)
            + f"={geography_total}|DELTA:{category_total - geography_total}"
        )
    if query_type == "sec_financial_balance_certification":
        required = ("assets", "equity", "liabilities_and_equity")
        if any(role not in xbrl for role in required):
            return "unknown"
        ceo = certs.get("ex_31_1") or []
        cfo = certs.get("ex_31_2") or []
        sox = list(certs.get("ex_32_1") or []) + list(certs.get("ex_32_2") or [])
        if len(ceo) != 1 or len(cfo) != 1 or len(sox) != 2:
            return "unknown"
        rows = [*ceo, *cfo, *sox]
        if not all(isinstance(row, dict) and row.get("name") for row in rows):
            return "unknown"
        if "liabilities" in xbrl:
            balance = (
                f"BS:{xbrl['liabilities']}+{xbrl['equity']}={xbrl['assets']}|"
                f"{xbrl['liabilities_and_equity']}"
            )
        else:
            reconstructed = int(xbrl["assets"]) - int(xbrl["equity"])
            balance = (
                f"BS:{xbrl['assets']}-{xbrl['equity']}={reconstructed}|"
                f"{xbrl['liabilities_and_equity']}"
            )
        return (
            f"{balance}||CERT:CEO:{ceo[0]['name']}||CFO:{cfo[0]['name']}||"
            f"SOX906:{sox[0]['name']}+{sox[1]['name']}"
        )
    if query_type == "sec_financial_cashflow_notes":
        common = (
            "cfo",
            "cfi",
            "cff",
            "delta_cash",
            "federal_tax",
            "state_tax",
            "foreign_tax",
            "income_tax",
            "lease_current",
            "lease_noncurrent",
            "lease_total",
        )
        if any(role not in xbrl for role in common):
            return "unknown"
        if all(
            role in xbrl for role in ("debt_current", "debt_noncurrent", "debt_total")
        ):
            cash_terms = f"{xbrl['cfo']}+{xbrl['cfi']}+{xbrl['cff']}"
            extra = (
                f"DEBT:{xbrl['debt_current']}+{xbrl['debt_noncurrent']}="
                f"{xbrl['debt_total']}"
            )
        elif all(role in xbrl for role in ("fx", "oi_0", "oi_1", "oi_2", "oi_total")):
            cash_terms = f"{xbrl['cfo']}+{xbrl['cfi']}+{xbrl['cff']}+{xbrl['fx']}"
            extra = (
                f"OI:{xbrl['oi_0']}+{xbrl['oi_1']}+{xbrl['oi_2']}={xbrl['oi_total']}"
            )
        else:
            return "unknown"
        return "||".join(
            (
                f"CF:{cash_terms}={xbrl['delta_cash']}",
                (
                    f"TAX:{xbrl['federal_tax']}+{xbrl['state_tax']}+"
                    f"{xbrl['foreign_tax']}={xbrl['income_tax']}"
                ),
                (
                    f"LEASE:{xbrl['lease_current']}+{xbrl['lease_noncurrent']}="
                    f"{xbrl['lease_total']}"
                ),
                extra,
            )
        )
    return "unknown"


_SEC_MIX_ROLES = ("product_revenue", "service_revenue", "total_revenue")
_SEC_APPLE_128K_ROLES = (
    "cfo",
    "cfi",
    "cff",
    "delta_cash",
    "federal_tax",
    "state_tax",
    "foreign_tax",
    "income_tax",
    "lease_current",
    "lease_noncurrent",
    "lease_total",
    "debt_current",
    "debt_noncurrent",
    "debt_total",
)
_SEC_AMAZON_128K_ROLES = (
    "cfo",
    "cfi",
    "cff",
    "fx",
    "delta_cash",
    "federal_tax",
    "state_tax",
    "foreign_tax",
    "income_tax",
    "lease_current",
    "lease_noncurrent",
    "lease_total",
    "oi_0",
    "oi_1",
    "oi_2",
    "oi_total",
)
_SEC_FINANCIAL_GRAMMAR_PREFIX = "Exact output grammar: `"


def _consume_indexed_roles(
    roles: tuple[str, ...], start: int, prefix: str
) -> tuple[tuple[str, ...], int]:
    end = start
    while end < len(roles) and roles[end].startswith(prefix):
        end += 1
    selected = roles[start:end]
    if not selected or selected != tuple(
        f"{prefix}{index}" for index in range(len(selected))
    ):
        return (), start
    return selected, end


def _sec_financial_output_contract(
    *, query_type: str, control_tier: str, required_roles: list[str]
) -> tuple[str, str, str] | None:
    roles = tuple(str(role) for role in required_roles)
    mix = (
        "MIX:<PRODUCT_REVENUE>+<SERVICE_REVENUE>=<TOTAL_REVENUE>"
        "|GAP:<MIX_GAP>|STATUS:<STATUS>"
    )
    labels = ["MIX", "GAP", "STATUS"]
    branch = "MIX_GAP is PRODUCT_REVENUE + SERVICE_REVENUE - TOTAL_REVENUE; "
    branch += "STATUS is PASS exactly when MIX_GAP is zero, otherwise FAIL"

    categories: tuple[str, ...] = ()
    geographies: tuple[str, ...] = ()
    balance_roles: tuple[str, ...] = ()
    extras: tuple[str, ...] = ()
    if query_type == "sec_financial_sales_mix":
        if roles != _SEC_MIX_ROLES or control_tier != "16k":
            return None
        return mix, ", ".join(labels), branch
    if query_type == "sec_financial_category_geography":
        categories, position = _consume_indexed_roles(roles, 0, "category_")
        geographies, position = _consume_indexed_roles(roles, position, "geo_")
        if position != len(roles) or control_tier != "32k":
            return None
        cat_terms = "+".join(f"<{role.upper()}>" for role in categories)
        geo_terms = "+".join(f"<{role.upper()}>" for role in geographies)
        grammar = (
            f"CAT:{cat_terms}=<CATEGORY_TOTAL>||GEO:{geo_terms}="
            "<GEOGRAPHY_TOTAL>|DELTA:<CATEGORY_MINUS_GEOGRAPHY>"
        )
        return (
            grammar,
            "CAT, GEO, DELTA",
            (
                "CATEGORY_TOTAL and GEOGRAPHY_TOTAL are their displayed operand "
                "sums; DELTA is CATEGORY_TOTAL - GEOGRAPHY_TOTAL"
            ),
        )
    if query_type == "sec_financial_balance_certification":
        if control_tier != "64k":
            return None
        balance_roles = roles
    elif query_type == "sec_financial_cashflow_notes":
        if control_tier != "128k":
            return None
        extras = roles
    elif query_type == "sec_financial_reconstruction":
        if roles[:3] != _SEC_MIX_ROLES:
            return None
        if control_tier == "16k":
            if roles != _SEC_MIX_ROLES:
                return None
            return mix, ", ".join(labels), branch
        categories, position = _consume_indexed_roles(roles, 3, "category_")
        geographies, position = _consume_indexed_roles(roles, position, "geo_")
        if not categories or not geographies:
            return None
        cat_terms = "+".join(f"<{role.upper()}>" for role in categories)
        geo_terms = "+".join(f"<{role.upper()}>" for role in geographies)
        grammar = (
            f"{mix}||CAT:{cat_terms}=<TOTAL_REVENUE>||GEO:{geo_terms}=<TOTAL_REVENUE>"
        )
        labels.extend(("CAT", "GEO"))
        branch += "; CAT and GEO operands must each sum to TOTAL_REVENUE"
        if control_tier == "32k":
            if position != len(roles):
                return None
            return grammar, ", ".join(labels), branch
        if roles[position : position + 4] == (
            "assets",
            "liabilities",
            "equity",
            "liabilities_and_equity",
        ):
            balance_roles = roles[position : position + 4]
        elif roles[position : position + 3] == (
            "assets",
            "equity",
            "liabilities_and_equity",
        ):
            balance_roles = roles[position : position + 3]
        else:
            return None
        position += len(balance_roles)
        if position >= len(roles) or roles[position] != "prior_total_revenue":
            return None
        prior_geographies, position = _consume_indexed_roles(
            roles, position + 1, "prior_geo_"
        )
        if len(prior_geographies) != len(geographies):
            return None
        prior_terms = "+".join(f"<{role.upper()}>" for role in prior_geographies)
        delta_terms = ",".join(
            f"<SIGNED_GEO_YOY_{index}>" for index in range(len(geographies))
        )
        grammar += (
            f"||GEO_PRIOR:{prior_terms}=<PRIOR_TOTAL_REVENUE>"
            f"||GEO_YOY:{delta_terms}|MAX_INDEX:<MAX_INDEX>:<SIGNED_MAX_DELTA>"
        )
        labels.extend(("GEO_PRIOR", "GEO_YOY", "MAX_INDEX"))
        branch += (
            "; each SIGNED_GEO_YOY_i is GEO_i - PRIOR_GEO_i; MAX_INDEX is the "
            "zero-based index of the largest absolute delta and SIGNED_MAX_DELTA "
            "is that delta"
        )
        extras = roles[position:]
        if control_tier == "64k" and extras:
            return None
        if control_tier == "128k" and extras not in {
            _SEC_APPLE_128K_ROLES,
            _SEC_AMAZON_128K_ROLES,
        }:
            return None
    else:
        return None

    if balance_roles:
        if "liabilities" in balance_roles:
            balance = "BS:<LIABILITIES>+<EQUITY>=<ASSETS>|<LIABILITIES_AND_EQUITY>"
            balance_branch = (
                "use the direct-liabilities branch because LIABILITIES is present; "
                "the first equality is LIABILITIES + EQUITY = ASSETS and the final "
                "field is LIABILITIES_AND_EQUITY"
            )
        else:
            balance = (
                "BS:<ASSETS>-<EQUITY>=<ASSETS_MINUS_EQUITY>|<LIABILITIES_AND_EQUITY>"
            )
            balance_branch = (
                "use the reconstructed-liabilities branch because LIABILITIES is "
                "absent; ASSETS_MINUS_EQUITY is ASSETS - EQUITY and the final field "
                "is LIABILITIES_AND_EQUITY"
            )
        certification = (
            "CERT:CEO:<CEO_NAME>||CFO:<CFO_NAME>||SOX906:<CEO_NAME>+<CFO_NAME>"
        )
        if query_type == "sec_financial_balance_certification":
            grammar = f"{balance}||{certification}"
            labels = ["BS", "CERT", "CEO", "CFO", "SOX906"]
            branch = balance_branch + "; certification order is CEO, CFO, then SOX906"
            return grammar, ", ".join(labels), branch
        else:
            grammar += (
                f"||{balance}||{certification}||"
                "CERT_SCOPE:Form 10-K@<COVERED_PERIOD_END>#<CERTIFICATION_DATE>"
            )
            labels.extend(("BS", "CERT", "CEO", "CFO", "SOX906", "CERT_SCOPE"))
            branch += "; " + balance_branch
            branch += (
                "; certification order is CEO, CFO, SOX906, then the covered "
                "Form 10-K period and certification date"
            )
            if control_tier == "64k":
                return grammar, ", ".join(labels), branch

    if extras:
        cash = "CF:<CFO>+<CFI>+<CFF>"
        if extras == _SEC_APPLE_128K_ROLES:
            cash += "=<DELTA_CASH>"
            final = "DEBT:<DEBT_CURRENT>+<DEBT_NONCURRENT>=<DEBT_TOTAL>"
            cash_branch = (
                "use the no-FX cash-flow branch and finish with DEBT because the "
                "debt roles are present"
            )
            final_label = "DEBT"
        elif extras == _SEC_AMAZON_128K_ROLES:
            cash += "+<FX>=<DELTA_CASH>"
            final = "OI:<OI_0>+<OI_1>+<OI_2>=<OI_TOTAL>"
            cash_branch = (
                "include FX in the cash-flow identity and finish with OI because "
                "the operating-income roles are present"
            )
            final_label = "OI"
        else:
            return None
        notes = (
            f"{cash}||TAX:<FEDERAL_TAX>+<STATE_TAX>+<FOREIGN_TAX>="
            "<INCOME_TAX>||LEASE:<LEASE_CURRENT>+<LEASE_NONCURRENT>="
            f"<LEASE_TOTAL>||{final}"
        )
        if query_type == "sec_financial_cashflow_notes":
            grammar = notes
            labels = ["CF", "TAX", "LEASE", final_label]
            branch = cash_branch
        else:
            grammar += "||" + notes
            labels.extend(("CF", "TAX", "LEASE", final_label))
            branch += "; " + cash_branch
        branch += "; every displayed equation must reconcile exactly"
        return grammar, ", ".join(labels), branch
    return None


def _sec_financial_role_name(role: str) -> str:
    fixed = {
        "product_revenue": "Product revenue",
        "service_revenue": "Service revenue",
        "total_revenue": "Total revenue",
        "assets": "Total assets",
        "liabilities": "Total liabilities",
        "equity": "Shareholders' equity",
        "liabilities_and_equity": "Total liabilities and shareholders' equity",
        "prior_total_revenue": "Prior-year total revenue",
        "cfo": "Net cash from operating activities",
        "cfi": "Net cash from investing activities",
        "cff": "Net cash from financing activities",
        "fx": "Foreign-exchange effect on cash",
        "delta_cash": "Net change in cash",
        "federal_tax": "Federal income tax",
        "state_tax": "State income tax",
        "foreign_tax": "Foreign income tax",
        "income_tax": "Total income tax",
        "lease_current": "Current lease liabilities",
        "lease_noncurrent": "Noncurrent lease liabilities",
        "lease_total": "Total lease liabilities",
        "debt_current": "Current debt",
        "debt_noncurrent": "Noncurrent debt",
        "debt_total": "Total debt",
        "oi_total": "Total operating income",
    }
    if role in fixed:
        return fixed[role]
    for prefix, label in (
        ("category_", "Revenue category"),
        ("prior_geo_", "Prior-year geography"),
        ("geo_", "Current geography"),
        ("oi_", "Operating-income segment"),
    ):
        if role.startswith(prefix) and role[len(prefix) :].isdigit():
            return f"{label} {role[len(prefix) :]}"
    return role.replace("_", " ").capitalize()


def _sec_financial_output_field(token: str) -> str:
    fixed = {
        "MIX_GAP": "Sales-mix reconciliation gap; USD exact integer",
        "STATUS": "Sales-mix status; PASS if MIX_GAP is zero, otherwise FAIL",
        "CATEGORY_TOTAL": "Sum of category operands; USD exact integer",
        "GEOGRAPHY_TOTAL": "Sum of geography operands; USD exact integer",
        "CATEGORY_MINUS_GEOGRAPHY": (
            "CATEGORY_TOTAL minus GEOGRAPHY_TOTAL; USD exact integer"
        ),
        "ASSETS_MINUS_EQUITY": "ASSETS minus EQUITY; USD exact integer",
        "MAX_INDEX": "Zero-based index of the largest absolute geography delta",
        "SIGNED_MAX_DELTA": "Delta selected by MAX_INDEX; signed USD exact integer",
        "CEO_NAME": "Chief executive officer name; source text",
        "CFO_NAME": "Chief financial officer name; source text",
        "COVERED_PERIOD_END": "Certified Form 10-K period end; YYYY-MM-DD",
        "CERTIFICATION_DATE": "Officer certification date; YYYY-MM-DD",
    }
    if token in fixed:
        return fixed[token]
    if token.startswith("SIGNED_GEO_YOY_"):
        return (
            "Current geography minus matching prior geography; signed USD exact integer"
        )
    return "Derived USD exact integer"


def _sec_financial_question_schema(
    *, query_type: str, control_tier: str, required_roles: list[str]
) -> str | None:
    contract = _sec_financial_output_contract(
        query_type=query_type,
        control_tier=control_tier,
        required_roles=required_roles,
    )
    if contract is None:
        return None
    grammar, labels, branch = contract
    role_declarations = "; ".join(
        f"{_sec_financial_role_name(role)} [input role {role}; output token "
        f"{role.upper()}; USD exact integer]"
        for role in required_roles
    )
    input_tokens = {role.upper() for role in required_roles}
    derived_tokens = dict.fromkeys(re.findall(r"<([A-Z0-9_]+)>", grammar))
    output_fields = "; ".join(
        f"{token} [{_sec_financial_output_field(token)}]"
        for token in derived_tokens
        if token not in input_tokens
    )
    return (
        f"Metric roles (exact input order): {role_declarations}. "
        f"Derived/output fields: {output_fields}. "
        "All monetary metrics use USD exact integers after applying the filing's "
        "XBRL scale and sign; officer names are source text, MAX_INDEX is a "
        "zero-based integer, STATUS is PASS or FAIL, and dates are YYYY-MM-DD. "
        f"Branch semantics: {branch}. Label order: {labels}. "
        "Literal delimiters: labels use `:`, arithmetic operands use `+` or the "
        "branch's literal `-`, equations use `=`, fields inside a block use `|`, "
        "top-level blocks use `||`, and GEO_YOY deltas use `,`; emit no extra "
        "delimiters or whitespace except source officer-name spaces and literal "
        f"`Form 10-K`. Exact output grammar: `{grammar}`."
    )


def sec_financial_answer_conforms(question: str, answer: str) -> bool:
    """Return whether an answer matches the exact grammar published in its prompt."""
    if question.count(_SEC_FINANCIAL_GRAMMAR_PREFIX) != 1:
        return False
    grammar_start = question.index(_SEC_FINANCIAL_GRAMMAR_PREFIX) + len(
        _SEC_FINANCIAL_GRAMMAR_PREFIX
    )
    grammar_end = question.find("`.", grammar_start)
    if grammar_end < 0:
        return False
    grammar = question[grammar_start:grammar_end]
    pieces = re.split(r"(<[A-Z0-9_]+>)", grammar)
    patterns: list[str] = []
    captures: dict[str, str] = {}
    for piece in pieces:
        if not piece.startswith("<"):
            patterns.append(re.escape(piece))
            continue
        token = piece[1:-1]
        if token in captures:
            patterns.append(f"(?P={captures[token]})")
            continue
        group = f"value_{len(captures)}"
        captures[token] = group
        if token == "STATUS":
            value_pattern = "(?:PASS|FAIL)"
        elif token in {"COVERED_PERIOD_END", "CERTIFICATION_DATE"}:
            value_pattern = r"\d{4}-\d{2}-\d{2}"
        elif token in {"CEO_NAME", "CFO_NAME"}:
            value_pattern = r"[^|+=:\r\n]+"
        elif token == "MAX_INDEX":
            value_pattern = r"\d+"
        elif token.startswith("SIGNED_"):
            value_pattern = r"[+-]\d+"
        else:
            value_pattern = r"-?\d+"
        patterns.append(f"(?P<{group}>{value_pattern})")
    return re.fullmatch("".join(patterns), answer) is not None


def _merge_overrides(
    spec: QuerySpec, extra: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {
        k: dict(v) for k, v in spec.question_overrides.items()
    }
    for eid, upd in (extra or {}).items():
        merged[eid] = {**merged.get(eid, {}), **upd}
    return merged


def gold_from_full(world: SimulatedWorld, spec: QuerySpec) -> str:
    from longworld.core.domain import eval_answer as deval

    st = _sim_for(world).replay_events(
        world.events, up_to=spec.as_of, param_overrides=_merge_overrides(spec)
    )
    return deval(world, spec, st.values)


def cf_from_full(world: SimulatedWorld, spec: QuerySpec) -> str:
    from longworld.core.domain import eval_answer as deval

    extra = {spec.cf_event_id: spec.cf_param_updates}
    st = _sim_for(world).replay_events(
        world.events,
        up_to=spec.as_of,
        param_overrides=_merge_overrides(spec, extra),
    )
    return deval(world, spec, st.values)


def _event(world: SimulatedWorld, suffix: str) -> Event:
    prefix = world.spec["prefix"]
    eid = f"{prefix}.{suffix}"
    for e in world.events:
        if e.id == eid:
            return e
    raise KeyError(eid)


def _aid(world: SimulatedWorld, suffix: str) -> str:
    return f"{world.spec['world_id']}.{world.spec['prefix']}.{suffix}"


def _financial_program_ops(
    control_tier: str, extra_128k: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = [
        {"op": "READ_XBRL_FACT", "role": "product_revenue"},
        {"op": "READ_XBRL_FACT", "role": "service_revenue"},
        {"op": "READ_XBRL_FACT", "role": "total_revenue"},
        {"op": "COMPUTE_MIX"},
    ]
    if control_tier in {"32k", "64k", "128k"}:
        ops.extend(
            [
                {"op": "READ_XBRL_FACT", "role": "category_mix"},
                {"op": "READ_XBRL_FACT", "role": "geographic_mix"},
                {"op": "COMPUTE_CATEGORY_GEO"},
            ]
        )
    if control_tier in {"64k", "128k"}:
        ops.extend(
            [
                {"op": "READ_XBRL_FACT", "role": "prior_geographic_mix"},
                {"op": "COMPUTE_GEOGRAPHIC_YOY"},
                {"op": "READ_XBRL_FACT", "role": "balance_sheet"},
                {"op": "READ_CERTIFICATION"},
                {"op": "COMPUTE_BS_CERT"},
            ]
        )
    if control_tier == "128k":
        last_role = "operating_income" if "note10_segment_oi" in extra_128k else "debt"
        last_compute = (
            "COMPUTE_CF_TAX_LEASE_OI"
            if last_role == "operating_income"
            else "COMPUTE_CF_TAX_LEASE_DEBT"
        )
        ops.extend(
            [
                {"op": "READ_XBRL_FACT", "role": "cash_flow"},
                {"op": "READ_XBRL_FACT", "role": "income_tax"},
                {"op": "READ_XBRL_FACT", "role": "leases"},
                {"op": "READ_XBRL_FACT", "role": last_role},
                {"op": last_compute},
            ]
        )
    return ops


def _financial_cf_updates(
    ops: Event, target_role: str = "product_revenue"
) -> dict[str, Any]:
    original = str(ops.params.get("text") or "")
    product = next(
        (
            span
            for span in ops.params.get("fact_spans") or []
            if isinstance(span, dict) and span.get("role") == target_role
        ),
        None,
    )
    if not isinstance(product, dict):
        return {"text": original}
    start = product.get("char_start")
    end = product.get("char_end")
    quote = str(product.get("evidence_quote") or "")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or original[start:end] != quote
    ):
        return {"text": original}
    digit_index = next(
        (
            index
            for index, value in enumerate(quote)
            if value.isdigit() and value != "0"
        ),
        -1,
    )
    mutated_quote = (
        quote[:digit_index]
        + str(int(quote[digit_index]) - 1)
        + quote[digit_index + 1 :]
        if digit_index >= 0
        else quote
    )
    if mutated_quote == quote:
        return {"text": original}
    mutated = original[:start] + mutated_quote + original[end:]
    spans = []
    for span in ops.params.get("fact_spans") or []:
        if not isinstance(span, dict):
            continue
        updated = dict(span)
        if span is product or (
            span.get("role") == target_role and span.get("evidence_quote") == quote
        ):
            updated["evidence_quote"] = mutated_quote
            raw_scale = span.get("scale")
            try:
                scale = int(raw_scale) if isinstance(raw_scale, (int, str)) else 6
            except (TypeError, ValueError):
                scale = 6
            try:
                updated["numeric_value"] = parse_ixbrl_display_number(
                    mutated_quote,
                    scale=scale,
                    sign=str(span.get("sign") or ""),
                )
            except ProvenanceError:
                return {"text": original}
        spans.append(updated)
    parent_provenance_id = str(ops.params.get("provenance_id") or "")
    provenance_payload = {
        "operation": "sec-financial-counterfactual-splice-v1",
        "parent_provenance_id": parent_provenance_id,
        "event_id": ops.id,
        "text_sha256": hashlib.sha256(mutated.encode()).hexdigest(),
    }
    return {
        "text": mutated,
        "text_sha256": hashlib.sha256(mutated.encode()).hexdigest(),
        "section_sha256": hashlib.sha256(
            mutated.removeprefix("SEC source section\n").encode()
        ).hexdigest(),
        "fact_spans": spans,
        "ground_values": [mutated_quote],
        "source_origin": "synthetic_world",
        "parent_provenance_id": parent_provenance_id,
        "provenance_operation": provenance_payload["operation"],
        "provenance_id": "synthetic-sha256:"
        + hashlib.sha256(
            json.dumps(
                provenance_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    }


def _sec_form_cf_updates(source: Event, replacement: str) -> dict[str, Any] | None:
    form_fact = next(
        (
            fact
            for fact in source.params.get("fact_spans") or []
            if isinstance(fact, dict) and fact.get("field") == "form"
        ),
        None,
    )
    if not isinstance(form_fact, dict):
        return None
    text = str(source.params.get("text") or "")
    try:
        quote_start = int(form_fact["char_start"])
        value_start = quote_start + int(form_fact["value_offset"])
        value_end = value_start + int(form_fact["value_length"])
    except (KeyError, TypeError, ValueError):
        return None
    original = text[value_start:value_end]
    if not original or len(original) != len(replacement):
        return None
    cf_text = text[:value_start] + replacement + text[value_end:]
    cf_fact_spans = []
    for fact in source.params.get("fact_spans") or []:
        if not isinstance(fact, dict):
            return None
        copied = dict(fact)
        if copied.get("field") == "form":
            quote = str(copied.get("evidence_quote") or "")
            offset = int(copied.get("value_offset") or 0)
            copied["evidence_quote"] = (
                quote[:offset]
                + replacement
                + quote[offset + int(copied["value_length"]) :]
            )
        cf_fact_spans.append(copied)
    cf_text_sha256 = hashlib.sha256(cf_text.encode()).hexdigest()
    cf_provenance = hashlib.sha256(
        json.dumps(
            {
                "operation": "counterfactual_sec_form",
                "parent_provenance_id": source.params["provenance_id"],
                "text_sha256": cf_text_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "text": cf_text,
        "text_sha256": cf_text_sha256,
        "source_origin": "synthetic_world",
        "parent_provenance_id": source.params["provenance_id"],
        "provenance_id": f"synthetic-sha256:{cf_provenance}",
        "provenance_operation": "counterfactual_sec_form",
        "fact_spans": cf_fact_spans,
        "ground_values": [
            replacement if value == original else value
            for value in source.params.get("ground_values") or []
        ],
    }


def _issuer_ir_revenue_cf_updates(source: Event) -> dict[str, Any] | None:
    text = str(source.params.get("text") or "")
    prefix = "Issuer IR rendered XBRL statement\n"
    spans = source.params.get("fact_spans")
    if not text.startswith(prefix) or not isinstance(spans, list):
        return None
    revenue = next(
        (
            span
            for span in spans
            if isinstance(span, dict) and span.get("role") == "revenue"
        ),
        None,
    )
    if not isinstance(revenue, dict):
        return None
    start = revenue.get("char_start")
    end = revenue.get("char_end")
    numeric_value = revenue.get("numeric_value")
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or isinstance(numeric_value, bool)
        or not isinstance(numeric_value, int)
    ):
        return None
    quote = text[start:end]
    replacement = quote.replace(f"{numeric_value:,}", f"{numeric_value + 1_000:,}")
    if replacement == quote or len(replacement) != len(quote):
        return None
    cf_text = text[:start] + replacement + text[end:]
    cf_spans = [dict(span) for span in spans]
    cf_revenue = next(span for span in cf_spans if span.get("role") == "revenue")
    cf_revenue.update(
        evidence_quote=replacement,
        numeric_value=numeric_value + 1_000,
        source_evidence_quote=replacement,
    )
    return {
        "text": cf_text,
        "text_sha256": hashlib.sha256(cf_text.encode()).hexdigest(),
        "section_sha256": hashlib.sha256(cf_text[len(prefix) :].encode()).hexdigest(),
        "fact_spans": cf_spans,
        "ground_values": [
            replacement if value == quote else value
            for value in source.params.get("ground_values") or []
        ],
        "source_origin": "synthetic_world",
        "provenance_id": "derived-sha256:"
        + hashlib.sha256(
            f"issuer_ir_cf_revenue|{source.params.get('provenance_id')}|{cf_text}".encode()
        ).hexdigest(),
        "parent_provenance_id": str(source.params.get("provenance_id") or ""),
        "provenance_operation": "counterfactual_issuer_ir_revenue",
    }


def _issuer_ir_question_schema(
    *,
    years: list[int],
    roles: list[str],
    role_kinds: dict[str, str],
    record_role_extensions: dict[int, list[str]] | None = None,
) -> str | None:
    extensions = record_role_extensions or {}
    all_roles = [*roles, *(role for values in extensions.values() for role in values)]
    if (
        not years
        or not roles
        or any(year not in years for year in extensions)
        or any(role not in role_kinds for role in all_roles)
    ):
        return None

    def declarations_for(selected_roles: list[str]) -> str:
        declarations = []
        for role in selected_roles:
            kind = role_kinds[role]
            if kind == "numeric":
                unit = "USD millions"
            elif kind in {"policy_presence", "disclosure_presence"}:
                unit = "presence flag, 1=phrase present, 0=absent"
            else:
                return ""
            declarations.append(
                f"{role.replace('_', ' ').capitalize()} [label {role.upper()}; {unit}]"
            )
        return "; ".join(declarations)

    declarations = declarations_for(roles)
    if not declarations:
        return None
    extension_declarations = []
    for year in years:
        extension_roles = extensions.get(year, [])
        if not extension_roles:
            continue
        rendered = declarations_for(extension_roles)
        if not rendered:
            return None
        extension_declarations.append(f"{year}: {rendered}")
    extension_clause = ""
    if extension_declarations:
        extension_clause = (
            " Per-year extensions appended to that year's YEAR block: "
            + "; ".join(extension_declarations)
            + ". Consecutive-pair deltas use the common roles only."
        )
    return (
        f"{years[0]}–{years[-1]}. Required roles in exact order: "
        + declarations
        + extension_clause
        + ". Exact output format: for each year in ascending order, "
        "YEAR:ROLE=integer,ROLE=integer; then for each consecutive pair, "
        "PRIOR_YEAR→CURRENT_YEAR:ΔROLE=integer,ΔROLE=integer. "
        "Separate blocks with ` | ` and do not reorder labels."
    )


def _issuer_ir_question_declares_schema(
    question: str,
    *,
    years: list[int],
    roles: list[str],
    role_kinds: dict[str, str],
    record_role_extensions: dict[int, list[str]] | None = None,
) -> bool:
    schema = _issuer_ir_question_schema(
        years=years,
        roles=roles,
        role_kinds=role_kinds,
        record_role_extensions=record_role_extensions,
    )
    return schema is not None and schema in question


def _issuer_ir_required_closure(world: SimulatedWorld, target: Event) -> list[Event]:
    by_id = {event.id: event for event in world.events}
    selected: set[str] = set()

    def visit(event: Event) -> None:
        if event.id in selected:
            return
        for parent_id in event.required_inputs:
            parent = by_id.get(parent_id)
            if parent is not None:
                visit(parent)
        selected.add(event.id)

    visit(target)
    return [event for event in world.events if event.id in selected]


def _issuer_ir_proof_depth(events: list[Event], target: Event) -> int:
    selected = {event.id: event for event in events}
    memo: dict[str, int] = {}

    def depth(event_id: str) -> int:
        if event_id in memo:
            return memo[event_id]
        event = selected[event_id]
        parents = [parent for parent in event.required_inputs if parent in selected]
        memo[event_id] = 1 + max((depth(parent) for parent in parents), default=0)
        return memo[event_id]

    return depth(target.id)


def build_queries(world: SimulatedWorld) -> list[QuerySpec]:
    """Six programmatic question types. Answers come from replay, never from an LLM."""
    project = world.spec["project"]
    prefix = world.spec["prefix"]
    if prefix != "focal":
        return []

    sign = _event(world, "sign_contract")
    roadmap = _event(world, "change_roadmap")
    supp = _event(world, "legal_supplement")
    mis = _event(world, "misrecord_revenue")
    aud = _event(world, "audit_correction")

    qid = world.spec["world_id"].split(":")[0]
    queries: list[QuerySpec] = []

    issuer_answers = sorted(
        (
            event
            for event in world.events
            if event.type == "issuer_ir_cross_year_answer"
        ),
        key=lambda event: event.time,
    )
    issuer_type = {
        "16k": "issuer_ir_two_year_growth",
        "32k": "issuer_ir_three_year_growth",
        "64k": "issuer_ir_four_year_consistency",
    }
    for answer_event in issuer_answers:
        tier = str(answer_event.params.get("control_tier") or "")
        query_type = issuer_type.get(tier)
        years = answer_event.params.get("report_years")
        if query_type is None or not isinstance(years, list) or not years:
            continue
        latest_record_id = str(answer_event.params["record_ids"][-1])
        cf_source = next(
            (
                event
                for event in world.events
                if event.type == "issuer_ir_source_section"
                and event.params.get("record_id") == latest_record_id
                and any(
                    span.get("role") == "revenue"
                    for span in event.params.get("fact_spans") or []
                    if isinstance(span, dict)
                )
            ),
            None,
        )
        if cf_source is None:
            continue
        cf_updates = _issuer_ir_revenue_cf_updates(cf_source)
        if cf_updates is None:
            continue
        essential_events = _issuer_ir_required_closure(world, answer_event)
        essential = [event.id for event in essential_events]
        roles = [str(role) for role in answer_event.params["required_roles"]]
        record_ids = [str(item) for item in answer_event.params["record_ids"]]
        extensions_by_record = {
            str(record_id): [str(role) for role in extension_roles]
            for record_id, extension_roles in dict(
                answer_event.params.get("record_role_extensions") or {}
            ).items()
        }
        extensions_by_year = {
            int(record_id.rsplit(":", 1)[-1][:4]): extension_roles
            for record_id, extension_roles in extensions_by_record.items()
        }
        all_roles = {
            *roles,
            *(role for values in extensions_by_record.values() for role in values),
        }
        role_kinds: dict[str, str] = {}
        role_kind_conflict = False
        for event_id in essential:
            source_event = next(
                (event for event in world.events if event.id == event_id), None
            )
            if source_event is None:
                continue
            for span in source_event.params.get("fact_spans") or []:
                if not isinstance(span, dict) or str(span.get("role")) not in all_roles:
                    continue
                role = str(span["role"])
                kind = str(span.get("kind") or "")
                if role in role_kinds and role_kinds[role] != kind:
                    role_kinds = {}
                    role_kind_conflict = True
                    break
                role_kinds[role] = kind
            if role_kind_conflict:
                break
        schema = _issuer_ir_question_schema(
            years=[int(year) for year in years],
            roles=roles,
            role_kinds=role_kinds,
            record_role_extensions=extensions_by_year,
        )
        if schema is None:
            continue
        question = (
            "Using the issuer-owned rendered XBRL statements and the validated "
            "annual-filing chain, reconstruct the cross-year financial comparison. "
            + schema
        )
        queries.append(
            QuerySpec(
                query_id=f"{qid}:{query_type}:{answer_event.params['workflow_id']}",
                query_type=query_type,
                question=question,
                answer="",
                as_of=answer_event.time,
                answer_key=str(answer_event.params["answer_key"]),
                essential_event_ids=essential,
                essential_artifact_ids=[
                    f"{world.spec['world_id']}.{event_id}" for event_id in essential
                ],
                sufficient_event_ids=essential,
                cf_event_id=cf_source.id,
                cf_param_updates=cf_updates,
                cf_answer="",
                invariance_event_id=None,
                gold_expression=(
                    "read issuer XBRL metrics; validate same-issuer prior-annual "
                    "relations; format per-record extensions; compute consecutive-"
                    "year common-role deltas"
                ),
                proof_depth=_issuer_ir_proof_depth(essential_events, answer_event),
                cf_op="amount",
                motif="company.issuer_ir_cross_year_financial_history",
                truth_regime="real_source_derived",
                topology_id=instance_topology(
                    "company.issuer_ir_cross_year", tier, len(years), len(all_roles)
                ),
                program_ops=[
                    *(
                        {"op": "READ_ISSUER_XBRL_METRIC", "role": role}
                        for role in roles
                    ),
                    *(
                        {
                            "op": "READ_ISSUER_XBRL_RECORD_EXTENSION",
                            "record_index": record_ids.index(record_id),
                            "roles": extension_roles,
                        }
                        for record_id, extension_roles in sorted(
                            extensions_by_record.items(),
                            key=lambda item: record_ids.index(item[0]),
                        )
                    ),
                    {"op": "VALIDATE_PRIOR_ANNUAL_RELATION"},
                    *(
                        [{"op": "FORMAT_PER_RECORD_EXTENSION"}]
                        if extensions_by_record
                        else []
                    ),
                    {"op": "COMPUTE_CONSECUTIVE_YEAR_DELTAS"},
                ],
                preferred_length_buckets=[tier],
                semantic_growth_group="company_real_issuer_ir_cross_year",
                base_task_group=f"issuer_ir_cross_year:{answer_event.params['workflow_id']}",
            )
        )

    for source in [event for event in world.events if event.type == "sec_filing"]:
        policy = next(
            event
            for event in world.events
            if event.type == "sec_filing_eligibility_policy"
            and event.params.get("source_record_event_id") == source.id
        )
        approval = next(
            event
            for event in world.events
            if event.type == "sec_filing_approval"
            and event.params.get("policy_event_id") == policy.id
        )
        ratifications = sorted(
            (
                event
                for event in world.events
                if event.type == "sec_filing_publication_ratification"
                and event.params.get("source_approval_event_id") == approval.id
            ),
            key=lambda event: event.time,
        )
        if len(ratifications) != 3:
            continue
        ratification = ratifications[0]
        initial_control_stage = str(ratification.params["control_stage"])
        form_fact = next(
            (
                fact
                for fact in source.params.get("fact_spans") or []
                if fact.get("field") == "form"
            ),
            None,
        )
        if not isinstance(form_fact, dict):
            continue
        text = str(source.params["text"])
        quote_start = int(form_fact["char_start"])
        value_start = quote_start + int(form_fact["value_offset"])
        value_end = value_start + int(form_fact["value_length"])
        form = text[value_start:value_end]
        if form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            continue
        cf_form = (
            form.replace("10-K", "10-Q")
            if "10-K" in form
            else form.replace("10-Q", "10-K")
        )
        cf_text = text[:value_start] + cf_form + text[value_end:]
        cf_fact_spans = []
        for fact in source.params.get("fact_spans") or []:
            copied = dict(fact)
            if copied.get("field") == "form":
                quote = str(copied["evidence_quote"])
                offset = int(copied["value_offset"])
                copied["evidence_quote"] = (
                    quote[:offset]
                    + cf_form
                    + quote[offset + int(copied["value_length"]) :]
                )
            cf_fact_spans.append(copied)
        cf_text_sha256 = hashlib.sha256(cf_text.encode()).hexdigest()
        cf_provenance = hashlib.sha256(
            json.dumps(
                {
                    "operation": "counterfactual_sec_form",
                    "parent_provenance_id": source.params["provenance_id"],
                    "text_sha256": cf_text_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        source_key = hashlib.sha256(
            f"{source.params['workflow_id']}:{source.params['record_id']}".encode()
        ).hexdigest()[:12]
        queries.append(
            QuerySpec(
                query_id=f"{qid}:sec_filing_eligibility:{source_key}:16k",
                query_type="sec_filing_eligibility",
                question=(
                    "Follow the verified SEC filing body through its linked release-"
                    "eligibility policy and subsequent committee approval. Report the "
                    "computed publication status, the filing's reporting period, and "
                    "its accession. Reply exactly as <APPROVED|BLOCKED> | YYYY-MM-DD | "
                    "<accession>. Do not infer filing facts from the policy or approval "
                    "memo. Decision checkpoint: "
                    f"{initial_control_stage}."
                ),
                answer="",
                as_of=ratification.time,
                answer_key=str(ratification.params["answer_key"]),
                essential_event_ids=[
                    source.id,
                    policy.id,
                    approval.id,
                    ratification.id,
                ],
                essential_artifact_ids=[
                    f"{world.spec['world_id']}.{source.visibility[0]}",
                    f"{world.spec['world_id']}.{policy.visibility[0]}",
                    f"{world.spec['world_id']}.{approval.visibility[0]}",
                    f"{world.spec['world_id']}.{ratification.visibility[0]}",
                ],
                sufficient_event_ids=[
                    source.id,
                    policy.id,
                    approval.id,
                    ratification.id,
                ],
                cf_event_id=source.id,
                cf_param_updates={
                    "text": cf_text,
                    "text_sha256": cf_text_sha256,
                    "source_origin": "synthetic_world",
                    "parent_provenance_id": source.params["provenance_id"],
                    "provenance_id": f"synthetic-sha256:{cf_provenance}",
                    "provenance_operation": "counterfactual_sec_form",
                    "fact_spans": cf_fact_spans,
                    "ground_values": [
                        cf_form if value == form else value
                        for value in source.params.get("ground_values") or []
                    ],
                },
                cf_answer="",
                invariance_event_id=source.id,
                invariance_param_updates={
                    "retrieval_url": "https://www.sec.gov/Archives/"
                },
                gold_expression=(
                    "READ_SOURCE_SPANS(accession, form, filing_date, report_date) "
                    "THEN APPLY_ELIGIBILITY_POLICY(form, filing_date, report_date) "
                    "THEN APPLY_COMMITTEE_APPROVAL THEN RATIFY_PUBLICATION"
                ),
                proof_depth=4,
                cf_op="filing_body",
                motif="real_filing_policy_approval",
                topology_id=instance_topology(
                    "company.real_sec_policy_approval",
                    source.params["workflow_id"],
                    form,
                ),
                domain="company",
                truth_regime="real_source_derived",
                program_ops=[
                    {"op": "READ_SOURCE_SPAN", "field": "accession"},
                    {"op": "READ_SOURCE_SPAN", "field": "form"},
                    {"op": "READ_SOURCE_SPAN", "field": "filing_date"},
                    {"op": "READ_SOURCE_SPAN", "field": "report_date"},
                    {"op": "APPLY_FILING_ELIGIBILITY_POLICY"},
                    {"op": "APPLY_COMMITTEE_APPROVAL"},
                    {"op": "RATIFY_PUBLICATION", "tier": "16k"},
                ],
                preferred_length_buckets=["16k"],
                semantic_growth_group="company_real_sec_policy_approval",
                base_task_group=f"sec_filing_eligibility:{source_key}",
            )
        )
        primary = queries[-1]
        for index, later_ratification in enumerate(ratifications[1:], start=1):
            control_tier = str(later_ratification.params["control_tier"])
            control_stage = str(later_ratification.params["control_stage"])
            required_ratifications = ratifications[: index + 1]
            required_events = [
                source.id,
                policy.id,
                approval.id,
                *(event.id for event in required_ratifications),
            ]
            queries.append(
                replace(
                    primary,
                    query_id=(
                        f"{qid}:sec_filing_eligibility:{source_key}:{control_tier}"
                    ),
                    question=primary.question.replace(
                        f"Decision checkpoint: {initial_control_stage}.",
                        f"Decision checkpoint: {control_stage}.",
                    ),
                    as_of=later_ratification.time,
                    answer_key=str(later_ratification.params["answer_key"]),
                    essential_event_ids=required_events,
                    essential_artifact_ids=[
                        f"{world.spec['world_id']}.{source.visibility[0]}",
                        f"{world.spec['world_id']}.{policy.visibility[0]}",
                        f"{world.spec['world_id']}.{approval.visibility[0]}",
                        *(
                            f"{world.spec['world_id']}.{event.visibility[0]}"
                            for event in required_ratifications
                        ),
                    ],
                    sufficient_event_ids=required_events,
                    gold_expression=(
                        "READ_SOURCE_SPANS(accession, form, filing_date, report_date) "
                        "THEN APPLY_ELIGIBILITY_POLICY(form, filing_date, report_date) "
                        "THEN APPLY_COMMITTEE_APPROVAL THEN "
                        + " THEN ".join(
                            f"RATIFY_PUBLICATION({event.params['control_tier']})"
                            for event in required_ratifications
                        )
                    ),
                    proof_depth=4 + index,
                    topology_id=instance_topology(
                        "company.real_sec_policy_approval",
                        source.params["workflow_id"],
                        form,
                        control_tier,
                    ),
                    program_ops=[
                        *primary.program_ops[:-1],
                        *(
                            {
                                "op": "RATIFY_PUBLICATION",
                                "tier": str(event.params["control_tier"]),
                            }
                            for event in required_ratifications
                        ),
                    ],
                    preferred_length_buckets=[control_tier],
                )
            )

    sources_by_record = {
        str(event.params.get("record_id") or ""): event
        for event in world.events
        if event.type == "sec_filing"
    }
    for resolution in (
        event for event in world.events if event.type == "sec_amendment_resolution"
    ):
        amendment = sources_by_record.get(str(resolution.params.get("record_id") or ""))
        original = sources_by_record.get(
            str(resolution.params.get("target_record_id") or "")
        )
        if amendment is None or original is None:
            continue
        form_fact = next(
            (
                fact
                for fact in amendment.params.get("fact_spans") or []
                if isinstance(fact, dict) and fact.get("field") == "form"
            ),
            None,
        )
        if not isinstance(form_fact, dict):
            continue
        value_start = int(form_fact["char_start"]) + int(form_fact["value_offset"])
        value_end = value_start + int(form_fact["value_length"])
        amendment_form = str(amendment.params["text"])[value_start:value_end]
        replacement = "10-Q/A" if amendment_form == "10-K/A" else "10-K/A"
        cf_updates = _sec_form_cf_updates(amendment, replacement)
        if cf_updates is None:
            continue
        relation_key = hashlib.sha256(
            str(resolution.params["source_relation_id"]).encode()
        ).hexdigest()[:12]
        essential = [original, amendment, resolution]
        queries.append(
            QuerySpec(
                query_id=f"{qid}:sec_amendment_resolution:{relation_key}:32k",
                query_type="sec_amendment_resolution",
                question=(
                    "Resolve whether the later SEC filing amends the earlier filing "
                    "by reading both filing bodies. Report the relationship, later "
                    "accession, earlier accession, and shared reporting period exactly "
                    "as <AMENDS|INVALID_RELATION> | <later accession> | <earlier "
                    "accession> | YYYY-MM-DD. Do not infer the relationship from "
                    "document order or filenames."
                ),
                answer="",
                as_of=resolution.time,
                answer_key=str(resolution.params["answer_key"]),
                essential_event_ids=[event.id for event in essential],
                essential_artifact_ids=[
                    f"{world.spec['world_id']}.{event.visibility[0]}"
                    for event in essential
                ],
                sufficient_event_ids=[event.id for event in essential],
                cf_event_id=amendment.id,
                cf_param_updates=cf_updates,
                cf_answer="",
                invariance_event_id=amendment.id,
                invariance_param_updates={
                    "retrieval_url": "https://www.sec.gov/Archives/"
                },
                gold_expression=(
                    "READ_SOURCE_SPANS(original accession, form, filing_date, "
                    "report_date) THEN READ_SOURCE_SPANS(amendment accession, form, "
                    "filing_date, report_date) THEN VALIDATE_AMENDS_REPORT"
                ),
                proof_depth=3,
                cf_op="filing_body",
                motif="real_filing_amendment_resolution",
                topology_id=instance_topology(
                    "company.real_sec_amendment_resolution", amendment_form
                ),
                domain="company",
                truth_regime="real_source_derived",
                program_ops=[
                    {"op": "READ_SOURCE_SPAN", "record": "original", "field": "form"},
                    {
                        "op": "READ_SOURCE_SPAN",
                        "record": "original",
                        "field": "report_date",
                    },
                    {
                        "op": "READ_SOURCE_SPAN",
                        "record": "amendment",
                        "field": "form",
                    },
                    {
                        "op": "READ_SOURCE_SPAN",
                        "record": "amendment",
                        "field": "report_date",
                    },
                    {"op": "VALIDATE_AMENDS_REPORT"},
                ],
                preferred_length_buckets=["32k"],
                semantic_growth_group="company_real_sec_amendment_resolution",
                base_task_group=f"sec_amendment_resolution:{relation_key}",
            )
        )

    computes: dict[str, dict[str, Event]] = {}
    facet_answers: dict[str, dict[str, Event]] = {}
    sections: dict[str, dict[str, Event]] = {}
    for event in world.events:
        record_id = str(event.params.get("record_id") or "")
        if not record_id:
            continue
        if event.type == "sec_financial_answer":
            tier = str(event.params.get("control_tier") or "")
            answer_family = str(event.params.get("answer_family") or "")
            if answer_family:
                facet_answers.setdefault(record_id, {})[answer_family] = event
            elif str(event.params.get("compose") or "compute") != "copy" and tier:
                computes.setdefault(record_id, {})[tier] = event
        elif event.type == "sec_source_section":
            section_id = str(event.params.get("section_id") or "")
            if section_id:
                sections.setdefault(record_id, {})[section_id] = event
            section_alias = str(event.params.get("section_alias") or "")
            if section_alias:
                sections.setdefault(record_id, {})[section_alias] = event
            financial_role = str(event.params.get("financial_role") or "")
            if financial_role:
                sections.setdefault(record_id, {})[
                    f"item8_operations_{financial_role}"
                ] = event
    for record_id, by_tier in computes.items():
        if "16k" not in by_tier:
            continue
        by_section = sections.get(record_id) or {}
        operation_sections = (
            "item8_operations_product_revenue",
            "item8_operations_service_revenue",
            "item8_operations_total_revenue",
        )
        ops = by_section.get(operation_sections[0])
        if ops is None:
            continue
        source_key = record_id.replace(":", "_")
        extra_128k: tuple[str, ...] = ()
        apple_128k = (
            "item8_cash_flow",
            "note7_income_taxes",
            "note8_leases",
            "note9_debt",
        )
        amazon_128k = (
            "item8_cash_flow",
            "note4_leases",
            "note9_income_taxes",
            "note10_segment_oi",
        )
        if all(name in by_section for name in apple_128k):
            extra_128k = apple_128k
        elif all(name in by_section for name in amazon_128k):
            extra_128k = amazon_128k
        facet_specs = (
            (
                "sales_mix",
                "16k",
                operation_sections,
                operation_sections[0],
                "product_revenue",
                (
                    {"op": "READ_XBRL_FACT", "role": "product_revenue"},
                    {"op": "READ_XBRL_FACT", "role": "service_revenue"},
                    {"op": "READ_XBRL_FACT", "role": "total_revenue"},
                    {"op": "COMPUTE_SALES_MIX_IDENTITY"},
                ),
            ),
            (
                "category_geography",
                "32k",
                ("note2_revenue", "note13_segments"),
                "note2_revenue",
                "category_0",
                (
                    {"op": "READ_XBRL_FACT", "role": "category_mix"},
                    {"op": "READ_XBRL_FACT", "role": "geographic_mix"},
                    {"op": "RECONCILE_CATEGORY_GEOGRAPHY"},
                ),
            ),
            (
                "balance_certification",
                "64k",
                (
                    "item8_balance_sheet",
                    "ex_31_1",
                    "ex_31_2",
                    "ex_32_1",
                    *(("ex_32_2",) if "ex_32_2" in by_section else ()),
                ),
                "item8_balance_sheet",
                "assets",
                (
                    {"op": "READ_XBRL_FACT", "role": "balance_sheet"},
                    {"op": "READ_CERTIFICATION", "role": "ceo_section_302"},
                    {"op": "READ_CERTIFICATION", "role": "cfo_section_302"},
                    {"op": "READ_CERTIFICATION", "role": "section_906"},
                    {"op": "RECONCILE_BALANCE_AND_CERTIFICATIONS"},
                ),
            ),
            *(
                (
                    (
                        "cashflow_notes",
                        "128k",
                        extra_128k,
                        "item8_cash_flow",
                        "cfo",
                        (
                            {"op": "READ_XBRL_FACT", "role": "cash_flow"},
                            {"op": "READ_XBRL_FACT", "role": "income_tax"},
                            {"op": "READ_XBRL_FACT", "role": "leases"},
                            {
                                "op": "READ_XBRL_FACT",
                                "role": (
                                    "operating_income"
                                    if "note10_segment_oi" in extra_128k
                                    else "debt"
                                ),
                            },
                            {"op": "RECONCILE_CASHFLOW_NOTES"},
                        ),
                    ),
                )
                if extra_128k
                else ()
            ),
        )
        by_facet = facet_answers.get(record_id) or {}
        for (
            answer_family,
            control_tier,
            needed_names,
            cf_section_name,
            cf_role,
            program_ops,
        ) in facet_specs:
            answer_event = by_facet.get(answer_family)
            needed_sections = [by_section.get(name) for name in needed_names]
            cf_section = by_section.get(cf_section_name)
            if (
                answer_event is None
                or cf_section is None
                or any(event is None for event in needed_sections)
            ):
                continue
            essential_events = [
                *(event for event in needed_sections if event is not None),
                answer_event,
            ]
            query_type = f"sec_financial_{answer_family}"
            required_roles = [
                str(role) for role in answer_event.params.get("required_roles") or []
            ]
            question_schema = _sec_financial_question_schema(
                query_type=query_type,
                control_tier=control_tier,
                required_roles=required_roles,
            )
            if question_schema is None:
                continue
            queries.append(
                QuerySpec(
                    query_id=(
                        f"{qid}:sec_financial_{answer_family}:{source_key}:"
                        f"{control_tier}"
                    ),
                    query_type=query_type,
                    question=(
                        "Execute the independent SEC XBRL "
                        f"{answer_family.replace('_', ' ')} program using only the "
                        "statement, note, and certification evidence in context. "
                        "Return the tagged reconciliation exactly; do not substitute "
                        "filing identity metadata for financial facts. "
                        + question_schema
                    ),
                    answer="",
                    as_of=answer_event.time,
                    answer_key=str(answer_event.params["answer_key"]),
                    essential_event_ids=[event.id for event in essential_events],
                    essential_artifact_ids=[
                        f"{world.spec['world_id']}.{event.visibility[0]}"
                        for event in essential_events
                    ],
                    sufficient_event_ids=[event.id for event in essential_events],
                    cf_event_id=cf_section.id,
                    cf_param_updates=_financial_cf_updates(cf_section, cf_role),
                    cf_answer="",
                    invariance_event_id=cf_section.id,
                    invariance_param_updates={
                        "retrieval_url": (
                            "https://www.sec.gov/Archives/edgar/data/0/noise.htm"
                        )
                    },
                    gold_expression=" THEN ".join(
                        str(operation["op"]) for operation in program_ops
                    ),
                    proof_depth=2,
                    cf_op="numeric",
                    motif=f"source-financial-{answer_family}",
                    topology_id=instance_topology(
                        f"company.sec_financial_{answer_family}", record_id
                    ),
                    domain="company",
                    truth_regime="real_source_derived",
                    program_ops=list(program_ops),
                    preferred_length_buckets=[control_tier],
                    semantic_growth_group=(
                        f"company_real_sec_financial_{answer_family}"
                    ),
                    base_task_group=f"sec_financial_{answer_family}:{source_key}",
                )
            )
        staged = [
            ("16k", 2, operation_sections, ("16k",)),
            (
                "32k",
                3,
                (*operation_sections, "note2_revenue", "note13_segments"),
                ("16k", "32k"),
            ),
            (
                "64k",
                4,
                (
                    *operation_sections,
                    "note2_revenue",
                    "note13_segments",
                    "note13_prior_segments",
                    "item8_balance_sheet",
                    "ex_31_1",
                    "ex_31_2",
                    "ex_32_1",
                    *(("ex_32_2",) if "ex_32_2" in by_section else ()),
                ),
                ("16k", "32k", "64k"),
            ),
        ]
        staged = [item for item in staged if item[0] in by_tier]
        if (
            "128k" in by_tier
            and extra_128k
            and all(name in by_section for name in extra_128k)
        ):
            staged.append(
                (
                    "128k",
                    5,
                    (
                        *operation_sections,
                        "note2_revenue",
                        "note13_segments",
                        "note13_prior_segments",
                        "item8_balance_sheet",
                        "ex_31_1",
                        "ex_31_2",
                        "ex_32_1",
                        *(("ex_32_2",) if "ex_32_2" in by_section else ()),
                        *extra_128k,
                    ),
                    ("16k", "32k", "64k", "128k"),
                )
            )
        for (
            control_tier,
            proof_depth,
            needed_names,
            extra_answers,
        ) in staged:
            answer_event = by_tier[control_tier]
            needed_sections = [by_section.get(name) for name in needed_names]
            needed_answers = [by_tier.get(name) for name in extra_answers]
            if any(item is None for item in (*needed_sections, *needed_answers)):
                continue
            essential_events = [
                item for item in (*needed_sections, *needed_answers) if item is not None
            ]
            essential_ids = [event.id for event in essential_events]
            required_roles = [
                str(role) for role in answer_event.params.get("required_roles") or []
            ]
            question_schema = _sec_financial_question_schema(
                query_type="sec_financial_reconstruction",
                control_tier=control_tier,
                required_roles=required_roles,
            )
            if question_schema is None:
                continue
            queries.append(
                QuerySpec(
                    query_id=(
                        f"{qid}:sec_financial_reconstruction:{source_key}:"
                        f"{control_tier}"
                    ),
                    query_type="sec_financial_reconstruction",
                    question=(
                        "Reconstruct the tagged financial program for this issuer "
                        f"through the {control_tier} control stage using only the "
                        "itemized statements, notes, and certifications in context. "
                        "Do not use identity-header fields as substitutes for "
                        "statement amounts. " + question_schema
                    ),
                    answer="",
                    as_of=answer_event.time,
                    answer_key=str(answer_event.params["answer_key"]),
                    essential_event_ids=essential_ids,
                    essential_artifact_ids=[
                        f"{world.spec['world_id']}.{event.visibility[0]}"
                        for event in essential_events
                    ],
                    sufficient_event_ids=essential_ids,
                    cf_event_id=ops.id,
                    cf_param_updates=_financial_cf_updates(ops),
                    cf_answer="",
                    invariance_event_id=ops.id,
                    invariance_param_updates={
                        "retrieval_url": (
                            "https://www.sec.gov/Archives/edgar/data/0/noise.htm"
                        )
                    },
                    gold_expression=(
                        (
                            "tagged MIX/CAT/GEO/BS/CERT/CF/TAX/LEASE/OI reconstruction"
                            if "note10_segment_oi" in extra_128k
                            else "tagged MIX/CAT/GEO/BS/CERT/CF/TAX/LEASE/DEBT reconstruction"
                        )
                        if control_tier == "128k"
                        else "tagged MIX/CAT/GEO/BS/CERT reconstruction"
                    ),
                    proof_depth=proof_depth,
                    cf_op="numeric",
                    motif="source-financial-program",
                    topology_id=instance_topology(
                        "company.sec_financial_reconstruction",
                        record_id,
                        control_tier,
                    ),
                    domain="company",
                    truth_regime="real_source_derived",
                    program_ops=_financial_program_ops(control_tier, extra_128k),
                    preferred_length_buckets=[control_tier],
                    semantic_growth_group="company_real_sec_financial_reconstruction",
                    base_task_group=f"sec_financial_reconstruction:{source_key}",
                )
            )

    # Core questions stop before process extensions (rollback).
    as_of_now = core_as_of(world)
    as_of_end = world_as_of(world)
    q_cur = QuerySpec(
        query_id=f"{qid}:current_state",
        query_type="current_state",
        question=(
            f"As of {as_of_now.isoformat()}, what is the legally effective delivery "
            f"version for project {project['project']} under contract {project['contract_id']}? "
            f"Customer emails are not legal instruments. Reply with the version token only."
        ),
        answer="",  # filled after gold
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[roadmap.id, supp.id],
        essential_artifact_ids=[
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
        ],
        sufficient_event_ids=[roadmap.id, supp.id],
        cf_event_id=roadmap.id,
        cf_param_updates={"version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9"},
        gold_expression="legal_effective_version @ end",
        proof_depth=3,
        cf_op="version",
        motif="supersession",
        topology_id=instance_topology(
            "company.legal_adopt_roadmap",
            project["contract_id"],
            project["roadmap_version"],
        ),
        domain="company",
    )
    queries.append(q_cur)

    # 2. historical_state — version in force before the amendment.
    hist_day = supp.time - timedelta(days=1)
    q_hist = QuerySpec(
        query_id=f"{qid}:historical_state",
        query_type="historical_state",
        question=(
            f"As of {hist_day.isoformat()} (the day before the legal amendment), what "
            f"delivery version was legally in force for {project['project']} "
            f"({project['contract_id']})? Reply with the version token only."
        ),
        answer="",
        as_of=hist_day,
        answer_key="legal_effective_version",
        essential_event_ids=[sign.id],
        essential_artifact_ids=[_aid(world, "contract_email")],
        sufficient_event_ids=[sign.id],
        cf_event_id=sign.id,
        cf_param_updates={"version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=roadmap.id,
        invariance_param_updates={
            "version": "v9",
            "v3_deliverable": "noise-deliverable",
        },
        gold_expression="legal_effective_version @ pre-amendment",
        proof_depth=1,
        cf_op="version",
        motif="historical_cut",
        topology_id=instance_topology(
            "company.legal_pre_amendment",
            project["contract_id"],
            project["signed_version"],
        ),
        domain="company",
    )
    queries.append(q_hist)

    # 3. multi_hop — audit delta requires June misrecord AND July restatement.
    q_mh = QuerySpec(
        query_id=f"{qid}:multi_hop",
        query_type="multi_hop",
        question=(
            f"After Internal Audit restated {project['project']}, by how many currency "
            f"units did recognized revenue fall relative to the June close? "
            f"Reply with a single integer (June minus restated). Ignore preview drafts."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="audit_adjustment",
        essential_event_ids=[mis.id, aud.id],
        essential_artifact_ids=[
            _aid(world, "finance_june"),
            _aid(world, "finance_july"),
        ],
        sufficient_event_ids=[mis.id, aud.id],
        cf_event_id=aud.id,
        cf_param_updates={"amount": int(project["audited_revenue"]) + 111},
        cf_answer="",
        invariance_event_id=_event(world, "finance_preview").id,
        invariance_param_updates={"amount": 1},
        gold_expression="revenue_misrecorded - revenue_recognized",
        proof_depth=2,
        cf_op="amount",
        motif="fork_join",
        topology_id=instance_topology(
            "company.audit_delta",
            project["misrecorded_revenue"],
            project["audited_revenue"],
        ),
        domain="company",
    )
    queries.append(q_mh)

    # 4. version_diff
    q_vd = QuerySpec(
        query_id=f"{qid}:version_diff",
        query_type="version_diff",
        question=(
            f"For {project['project']}, what deliverable token did the signed packet "
            f"name, and what deliverable token did the Q-cycle roadmap name? "
            f"Reply exactly as: <v2_token> -> <v3_token>."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="v3_deliverable",
        essential_event_ids=[sign.id, roadmap.id],
        essential_artifact_ids=[
            _aid(world, "contract_email"),
            _aid(world, "roadmap_notes"),
        ],
        sufficient_event_ids=[sign.id, roadmap.id],
        cf_event_id=roadmap.id,
        cf_param_updates={
            "v3_deliverable": f"{project['project'].lower()}-stream-api-99"
        },
        cf_answer="",
        invariance_event_id=_event(world, "standup_notes").id,
        invariance_param_updates={"version": "noise-beta"},
        gold_expression="v2_deliverable -> v3_deliverable",
        proof_depth=2,
        cf_op="version",
        motif="chain",
        topology_id=instance_topology(
            "company.deliverable_pair",
            project["v2_deliverable"],
            project["v3_deliverable"],
        ),
        domain="company",
    )
    queries.append(q_vd)

    q_belief = QuerySpec(
        query_id=f"{qid}:compare_belief",
        query_type="compare_belief",
        question=(
            f"For {project['project']} ({project['contract_id']}), what delivery "
            f"version does the customer still believe is in force, and what "
            f"version is legally effective after the numbered amendment? Reply "
            f"exactly as: <customer_belief> || <legal_version>. Customer email "
            f"is not a legal instrument."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[
            _event(world, "client_cite_old").id,
            roadmap.id,
            supp.id,
        ],
        essential_artifact_ids=[
            _aid(world, "client_email"),
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
        ],
        sufficient_event_ids=[
            _event(world, "client_cite_old").id,
            roadmap.id,
            supp.id,
        ],
        cf_event_id=_event(world, "client_cite_old").id,
        cf_param_updates={"cited_version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=_event(world, "standup_notes").id,
        invariance_param_updates={"version": "noise-beta"},
        gold_expression="stale_client_cite || legal_effective_version",
        proof_depth=3,
        cf_op="version",
        motif="source_authority",
        topology_id=instance_topology(
            "company.belief_vs_legal",
            project["signed_version"],
            project["roadmap_version"],
        ),
        domain="company",
    )
    queries.append(q_belief)

    # 5. counterfactual question (hypothetical on the original documents)
    q_cf = QuerySpec(
        query_id=f"{qid}:counterfactual",
        query_type="counterfactual",
        question=(
            f"Suppose the legal amendment for {project['contract_id']} had NOT adopted "
            f"the roadmap and had instead kept the originally signed delivery version. "
            f"What would the legally effective delivery version be as of "
            f"{as_of_now.isoformat()}? Reply with the version token only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[sign.id],
        essential_artifact_ids=[_aid(world, "contract_email")],
        sufficient_event_ids=[sign.id, supp.id],
        # Document-level twin still has to change a different parameter so y_cf != y.
        cf_event_id=sign.id,
        cf_param_updates={"version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9"},
        gold_expression="cf: supplement keeps signed version",
        proof_depth=3,
        cf_op="version",
        motif="counterfactual_supersession",
        topology_id=instance_topology(
            "company.legal_keep_signed",
            project["contract_id"],
            project["signed_version"],
        ),
        domain="company",
        question_overrides={
            supp.id: {
                "adopt_roadmap": False,
                "keep_signed": True,
                "version": project["signed_version"],
            }
        },
    )
    queries.append(q_cf)

    carve = find_event(world, "carveout")
    if carve is not None:
        j = str(project.get("carveout_jurisdiction") or "J-NA")
        q_ex = QuerySpec(
            query_id=f"{qid}:exception_scope",
            query_type="exception_scope",
            question=(
                f"For jurisdiction {j} on contract {project['contract_id']}, which "
                f"delivery version still governs after the numbered amendment? "
                f"The carve-out memo does not reprint the token. Reply with the "
                f"version token only."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="carveout_version",
            essential_event_ids=[sign.id, carve.id],
            essential_artifact_ids=[
                _aid(world, "contract_email"),
                _aid(world, "carveout_memo"),
            ],
            sufficient_event_ids=[sign.id, carve.id],
            cf_event_id=sign.id,
            cf_param_updates={"version": project["cf_version"]},
            cf_answer="",
            invariance_event_id=_event(world, "client_cite_old").id,
            invariance_param_updates={"cited_version": "v9"},
            gold_expression="carveout_version copies signed packet",
            proof_depth=2,
            cf_op="version",
            motif="exception",
            topology_id=instance_topology(
                "company.carveout_keeps_signed", project["contract_id"], j
            ),
            domain="company",
        )
        queries.append(q_ex)

    hold = find_event(world, "announce_hold")
    if carve is not None and hold is not None:
        q_del = QuerySpec(
            query_id=f"{qid}:delayed_effect",
            query_type="delayed_effect",
            question=(
                f"Which jurisdiction now blocks the public announcement for "
                f"{project['project']} ({project['contract_id']})? The hold notice "
                f"does not reprint the token; reconstruct it from the earlier "
                f"carve-out instrument. Reply with the jurisdiction token only."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="release_blocker",
            essential_event_ids=[carve.id, hold.id],
            essential_artifact_ids=[
                _aid(world, "carveout_memo"),
                _aid(world, "announce_hold"),
            ],
            sufficient_event_ids=[carve.id, hold.id],
            cf_event_id=carve.id,
            cf_param_updates={"jurisdiction": f"J-HOLD{project['contract_id'][-4:]}"},
            cf_answer="",
            invariance_event_id=_event(world, "client_cite_old").id,
            invariance_param_updates={"cited_version": "v9"},
            gold_expression="release_blocker copies carveout_jurisdiction at announce",
            proof_depth=2,
            cf_op="version",
            motif="delayed_effect",
            topology_id=instance_topology(
                "company.carveout_then_announce",
                project["contract_id"],
                j,
            ),
            domain="company",
        )
        queries.append(q_del)

    rb = find_event(world, "rollback_amendment")
    if rb is not None:
        q_rb = QuerySpec(
            query_id=f"{qid}:rollback_state",
            query_type="rollback_state",
            question=(
                f"After the amendment for {project['contract_id']} was withdrawn, "
                f"what is the legally effective delivery version for "
                f"{project['project']}? Reply with the version token only."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="legal_effective_version",
            essential_event_ids=[sign.id, rb.id],
            essential_artifact_ids=[
                _aid(world, "contract_email"),
                _aid(world, "rollback_memo"),
            ],
            sufficient_event_ids=[sign.id, rb.id],
            cf_event_id=sign.id,
            cf_param_updates={"version": project["cf_version"]},
            cf_answer="",
            invariance_event_id=_event(world, "client_cite_old").id,
            invariance_param_updates={"cited_version": "v9"},
            gold_expression="rollback restores signed legal version",
            proof_depth=2,
            cf_op="version",
            motif="rollback",
            topology_id=instance_topology(
                "company.amendment_withdrawn",
                project["contract_id"],
                project["signed_version"],
            ),
            domain="company",
        )
        queries.append(q_rb)

        q_xs = QuerySpec(
            query_id=f"{qid}:cross_stream",
            query_type="cross_stream",
            question=(
                f"After the amendment for {project['contract_id']} was withdrawn, "
                f"what recognized revenue is booked against the restored legal "
                f"delivery version for {project['project']}? Reply exactly as "
                f"<integer>@<version> using the executed packet, the July "
                f"restatement, and the withdrawal instrument. Do not use June "
                f"or customer email."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="revenue_recognized",
            essential_event_ids=[sign.id, aud.id, rb.id],
            essential_artifact_ids=[
                _aid(world, "contract_email"),
                _aid(world, "finance_july"),
                _aid(world, "rollback_memo"),
            ],
            sufficient_event_ids=[sign.id, aud.id, rb.id],
            cf_event_id=aud.id,
            cf_param_updates={"amount": int(project["audited_revenue"]) + 173},
            cf_answer="",
            invariance_event_id=_event(world, "client_cite_old").id,
            invariance_param_updates={"cited_version": "v9"},
            gold_expression="revenue_recognized @ post-rollback legal version",
            proof_depth=3,
            cf_op="amount",
            motif="cross_workstream",
            topology_id=instance_topology(
                "company.revenue_against_rolled_legal",
                project["audited_revenue"],
                project["signed_version"],
            ),
            domain="company",
        )
        queries.append(q_xs)

    # 6. aggregation — final recognized revenue
    q_ag = QuerySpec(
        query_id=f"{qid}:aggregation",
        query_type="aggregation",
        question=(
            f"What is the final recognized revenue for {project['project']} after the "
            f"July restatement? Reply with a single integer. Do not use preview drafts "
            f"or the uncorrected June close."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="revenue_recognized",
        essential_event_ids=[aud.id],
        essential_artifact_ids=[_aid(world, "finance_july")],
        sufficient_event_ids=[mis.id, aud.id],
        cf_event_id=aud.id,
        cf_param_updates={"amount": int(project["audited_revenue"]) - 221},
        cf_answer="",
        invariance_event_id=mis.id,
        # Changing June without changing July should NOT change final recognized
        # if we only read the July file... wait, replay still applies both.
        # For invariance we need an event that does not affect this answer.
        invariance_param_updates={"amount": int(project["misrecorded_revenue"]) + 50},
        gold_expression="revenue_recognized @ end",
        proof_depth=1,
        cf_op="amount",
        motif="aggregation",
        topology_id=instance_topology(
            "company.final_recognized", project["audited_revenue"]
        ),
        domain="company",
    )
    # Fix aggregation invariance: June amount change DOES change the multi-hop
    # delta but NOT final recognized if audit sets absolute amount.
    # Replay: misrecord then audit_correction(amount=audited) → recognized = audited.
    # Changing misrecord amount does NOT change final recognized. Good.
    queries.append(q_ag)

    renewal_roadmap = _event(world, "renewal_roadmap")
    renewal_amendment = _event(world, "renewal_amendment")
    renewal_release = _event(world, "renewal_release")
    renewal_audit = _event(world, "renewal_audit")
    q_renewal = QuerySpec(
        query_id=f"{qid}:renewal_control_trace",
        query_type="renewal_control_trace",
        question=(
            f"For the second release cycle of {project['project']} under "
            f"{project['contract_id']}, trace the first final audit amount to the "
            "renewal final audit amount, then identify the delivery version adopted "
            "by the renewal amendment and the renewal release tag. Reply exactly as "
            "<first_audit>-><renewal_audit>@<legal_version>#<release_tag>. The "
            "renewal amendment does not reprint its adopted version."
        ),
        answer="",
        as_of=as_of_end,
        answer_key="audit_cycle_2",
        essential_event_ids=[
            aud.id,
            renewal_roadmap.id,
            renewal_amendment.id,
            renewal_release.id,
            renewal_audit.id,
        ],
        essential_artifact_ids=[
            _aid(world, "finance_july"),
            _aid(world, "renewal_roadmap"),
            _aid(world, "renewal_amendment"),
            _aid(world, "renewal_release"),
            _aid(world, "renewal_audit"),
        ],
        sufficient_event_ids=[renewal_audit.id],
        cf_event_id=renewal_roadmap.id,
        cf_param_updates={
            "version": project["cf_version"],
            "v3_deliverable": f"{project['project']}-renewal-stream-cf",
        },
        cf_answer="",
        invariance_event_id=_event(world, "finance_preview").id,
        invariance_param_updates={"amount": 1},
        gold_expression=(
            "audit_cycle_1 -> audit_cycle_2 @ renewal_legal_effective_version "
            "# renewal_release_version"
        ),
        proof_depth=7,
        cf_op="version",
        motif="longitudinal_cross_workstream",
        topology_id=instance_topology(
            "company.renewal_control_trace",
            project["contract_id"],
            project["renewal_roadmap_version"],
            project["renewal_beta_tag"],
        ),
        domain="company",
        program_ops=[
            {"op": "LOOKUP", "field": "audit_cycle_1"},
            {"op": "LOOKUP", "field": "audit_cycle_2"},
            {"op": "JOIN", "field": "renewal_legal_effective_version"},
            {"op": "JOIN", "field": "renewal_release_version"},
        ],
        preferred_length_buckets=["64k", "128k", "256k"],
        semantic_growth_group="company.renewal_cycle",
    )
    queries.append(q_renewal)

    release = _event(world, "release_beta")
    q_legal_financial = QuerySpec(
        query_id=f"{qid}:legal_financial_release_trace",
        query_type="legal_financial_release_trace",
        question=(
            f"For the first release cycle of {project['project']} under "
            f"{project['contract_id']}, combine the roadmap version adopted by the "
            "legal supplement, the release tag, and the final audited revenue. Reply "
            "exactly as <legal_version>#<release_tag>@<audited_revenue>. No single "
            "document repeats all three values."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[roadmap.id, supp.id, release.id, aud.id],
        essential_artifact_ids=[
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
            _aid(world, "beta_notes"),
            _aid(world, "finance_july"),
        ],
        sufficient_event_ids=[
            sign.id,
            roadmap.id,
            supp.id,
            release.id,
            mis.id,
            aud.id,
        ],
        cf_event_id=roadmap.id,
        cf_param_updates={
            "version": project["cf_version"],
            "v3_deliverable": f"{project['project']}-financial-trace-cf",
        },
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9"},
        gold_expression=(
            "JOIN(legal_supplement.roadmap_version, release.tag, audit.final_revenue)"
        ),
        proof_depth=6,
        cf_op="version",
        motif="legal_financial_release_join",
        topology_id=instance_topology(
            "company.legal_financial_release_trace",
            project["contract_id"],
            project["beta_tag"],
        ),
        domain="company",
        program_ops=[
            {"op": "RESOLVE_LEGAL_ADOPTION"},
            {"op": "FOLLOW_RELEASE_TAG"},
            {"op": "REQUIRE_FINAL_AUDIT"},
            {"op": "FORMAT_VERSION_RELEASE_REVENUE"},
        ],
        preferred_length_buckets=["32k", "64k"],
        semantic_growth_group="company.legal_financial_release",
    )
    queries.append(q_legal_financial)

    workstreams = list(project.get("workstreams") or [])
    if len(workstreams) >= 3:
        first_index = 0
        pivot_index = len(workstreams) // 2
        final_index = len(workstreams) - 1
        pivot_recovery = _event(world, f"cycle_{pivot_index:03d}_recovery")
        final_audit = _event(world, f"cycle_{final_index:03d}_audit")
        event_index = {event.id: event for event in world.events}
        closure: set[str] = set()

        def visit(event_id: str) -> None:
            if event_id in closure or event_id not in event_index:
                return
            event = event_index[event_id]
            for parent_id in (*event.required_inputs, *event.causal_inputs):
                visit(parent_id)
            closure.add(event_id)

        visit(final_audit.id)
        sufficient = [
            event.id
            for event in world.events
            if event.id in closure and not event.skipped
        ]
        q_portfolio = QuerySpec(
            query_id=f"{qid}:portfolio_recovery_trace",
            query_type="portfolio_recovery_trace",
            question=(
                f"Across the {len(workstreams)} sequential contract workstreams for "
                f"{project['project']}, reconstruct every cycle in chronological "
                "order. For each cycle report its incident, corrective action, "
                "recovered release, and controlling audit amount as "
                "<incident>=><resolution>#<release>@<audit>; join all cycle records "
                "with ` | `. Recovery and release records do not repeat the incident, "
                "and no single audit summarizes an earlier cycle."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="portfolio_control_trace",
            essential_event_ids=[
                _event(world, f"cycle_{index:03d}_{stage}").id
                for index in range(len(workstreams))
                for stage in ("failure", "recovery", "release", "audit")
            ],
            essential_artifact_ids=[
                _aid(world, f"cycle_{index:03d}_{stage}")
                for index in range(len(workstreams))
                for stage in ("failure", "recovery", "release", "audit")
            ],
            sufficient_event_ids=sufficient,
            cf_event_id=pivot_recovery.id,
            cf_param_updates={
                "resolution_token": (
                    f"FIX-CF-{project['contract_id'][-3:]}-{pivot_index + 1:03d}"
                )
            },
            cf_answer="",
            invariance_event_id=_event(world, "finance_preview").id,
            invariance_param_updates={"amount": 1},
            gold_expression=(
                "FOR_EACH_CYCLE(incident => resolution # release @ audit) "
                "IN_CHRONOLOGICAL_ORDER"
            ),
            proof_depth=5 * len(workstreams) - 1,
            cf_op="version",
            motif="sequential_failure_recovery_portfolio",
            topology_id=instance_topology(
                "company.portfolio_recovery_trace",
                workstreams[first_index]["id"],
                workstreams[pivot_index]["id"],
                workstreams[final_index]["id"],
            ),
            domain="company",
            program_ops=[
                {"op": "FOR_EACH_CYCLE", "count": len(workstreams)},
                {
                    "op": "REQUIRE_FIELDS",
                    "fields": ["incident", "resolution", "release", "audit"],
                },
                {"op": "FORMAT_CHRONOLOGICAL_TRACE"},
            ],
            preferred_length_buckets=["64k"],
            semantic_growth_group="company.portfolio_recovery",
        )
        queries.append(q_portfolio)

    from longworld.core.cascade import (
        build_docket_control_query,
        build_ratification_query,
        build_revisitation_query,
    )
    from longworld.core.grounded import (
        build_content_grounded_query,
        build_source_choice_query,
        build_source_grounded_query,
    )

    q_g = build_source_grounded_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_g is not None:
        queries.append(q_g)
    q_content = build_content_grounded_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_content is not None:
        queries.append(q_content)
    q_choice = build_source_choice_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_choice is not None:
        queries.append(q_choice)

    q_rev = build_revisitation_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_rev is not None:
        queries.append(q_rev)
    q_rat = build_ratification_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_rat is not None:
        queries.append(q_rat)
    q_dock = build_docket_control_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_dock is not None:
        queries.append(q_dock)

    # Decoy A: intervene on a known-invariance event. Gold must not change;
    # the verifier is supposed to drop this candidate.
    q_decoy_inv = QuerySpec(
        query_id=f"{qid}:decoy_invariance",
        query_type="current_state",
        question=q_cur.question,
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[roadmap.id, supp.id],
        essential_artifact_ids=[
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
        ],
        sufficient_event_ids=[roadmap.id, supp.id],
        cf_event_id=_event(world, "client_cite_old").id,
        cf_param_updates={"cited_version": f"NOISE-{project['cf_version']}"},
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9-noise"},
        gold_expression="decoy: invariance intervention",
        proof_depth=3,
        cf_op="version",
    )
    queries.append(q_decoy_inv)

    # Decoy B: overspecified essentials. Dropping the contract file still
    # reconstructs the legal version, so remove-one must fail.
    q_decoy_over = QuerySpec(
        query_id=f"{qid}:decoy_overspec",
        query_type="current_state",
        question=q_cur.question,
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[sign.id, roadmap.id, supp.id],
        essential_artifact_ids=[
            _aid(world, "contract_email"),
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
        ],
        sufficient_event_ids=[roadmap.id, supp.id],
        cf_event_id=roadmap.id,
        cf_param_updates={"version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9"},
        gold_expression="decoy: overspecified essentials",
        proof_depth=3,
        cf_op="version",
    )
    queries.append(q_decoy_over)

    # Fill gold / cf answers from the engine.
    out: list[QuerySpec] = []
    for q in queries:
        q.answer = gold_from_full(world, q)
        q.cf_answer = cf_from_full(world, q)
        if q.query_type.startswith("sec_financial_") and (
            not sec_financial_answer_conforms(q.question, q.answer)
            or not sec_financial_answer_conforms(q.question, q.cf_answer)
        ):
            continue
        out.append(q)
    return out
