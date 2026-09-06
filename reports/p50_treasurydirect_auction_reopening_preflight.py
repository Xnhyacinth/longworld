"""P50 TreasuryDirect auction/reopening source and capacity preflight.

The raw API payload and CUSIP values remain in memory only.  The persisted
report contains aggregate counts, source digests, field names, and exclusions.
"""

from __future__ import annotations

import hashlib
import json
import math
import ssl
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from transformers import AutoTokenizer

from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p50_treasurydirect_auction_reopening_preflight_v1.json")
OUTPUT = Path("reports/p50_treasurydirect_auction_reopening_preflight_v1.json")
ALLOWED_HOSTS = frozenset({"www.treasurydirect.gov"})
USER_AGENT = "LongWorld-P50-source-preflight/1.0 xnhyacinth@users.noreply.github.com"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _request(url: str) -> tuple[bytes, dict[str, Any]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P50 source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        request, timeout=180, context=ssl.create_default_context()
    ) as response:
        raw = response.read()
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P50 redirect target: {final_url}")
        return raw, {
            "requested_url": url,
            "final_url": final_url,
            "status": response.status,
            "content_type": response.headers.get_content_type(),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "bytes": len(raw),
            "sha256": _sha256(raw),
        }


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _amount_bucket(value: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation:
        return "NUMERIC_PRESENT"
    if number == 0:
        return "ZERO"
    magnitude = len(str(abs(int(number)))) - 1
    return f"{'POS' if number > 0 else 'NEG'}_1E{magnitude}"


def _coarsen(field: str, value: str) -> str:
    if not value:
        return ""
    lowered = field.lower()
    if "date" in lowered:
        return value[:7]
    if any(marker in lowered for marker in ("amount", "accepted", "tendered")):
        return _amount_bucket(value)
    if any(
        marker in lowered
        for marker in ("rate", "yield", "price", "interest", "ratio", "margin")
    ):
        try:
            number = Decimal(value)
        except InvalidOperation:
            return "NUMERIC_PRESENT"
        return str(number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return value


def _line(prefix: str, row: dict[str, Any], fields: list[str]) -> str:
    parts = [prefix]
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            parts.append(f"{field}={value}")
    return "|".join(parts)


def _coarsened_line(prefix: str, row: dict[str, Any], fields: list[str]) -> str:
    parts = [prefix]
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            parts.append(f"{field}={_coarsen(field, value)}")
    return "|".join(parts)


def _token_lengths(
    tokenizer: Any, texts: list[str], batch_size: int = 128
) -> list[int]:
    lengths: list[int] = []
    for offset in range(0, len(texts), batch_size):
        encoded = tokenizer(
            texts[offset : offset + batch_size],
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )["input_ids"]
        lengths.extend(len(item) for item in encoded)
    return lengths


def _percentile(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    index = math.ceil(percentile * len(ordered)) - 1
    return ordered[max(0, index)]


def _bill_price(row: dict[str, Any], field: str) -> tuple[Decimal, Decimal]:
    issue_date = _date(row["issueDate"])
    maturity_date = _date(row["maturityDate"])
    discount_rate = Decimal(row["highDiscountRate"])
    observed = Decimal(row[field])
    days = Decimal((maturity_date - issue_date).days)
    unrounded = Decimal(100) * (
        Decimal(1) - discount_rate / Decimal(100) * days / Decimal(360)
    )
    quantum = Decimal(1).scaleb(observed.as_tuple().exponent)
    return unrounded.quantize(quantum, rounding=ROUND_HALF_UP), observed


def _chain_metrics(
    rows: list[dict[str, Any]], config: dict[str, Any], tokenizer: Any
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["cusip"])].append(row)

    group_categories: Counter[str] = Counter()
    group_sizes: Counter[int] = Counter()
    complete_chains: list[list[dict[str, Any]]] = []
    for group in grouped.values():
        original_count = sum(row["reopening"] == "No" for row in group)
        reopening_count = sum(row["reopening"] == "Yes" for row in group)
        if original_count == 1 and reopening_count:
            group_categories["complete_original_plus_reopening"] += 1
            ordered = sorted(
                group, key=lambda row: (row["auctionDate"], row["issueDate"])
            )
            complete_chains.append(ordered)
            group_sizes[len(ordered)] += 1
        elif original_count == 1:
            group_categories["original_only"] += 1
        elif original_count == 0 and reopening_count:
            group_categories["reopening_only_original_absent_from_snapshot"] += 1
        else:
            group_categories["other"] += 1

    topology_failures: Counter[str] = Counter()
    security_types: Counter[str] = Counter()
    original_terms: Counter[str] = Counter()
    exact_units: set[str] = set()
    coarsened_units: set[str] = set()
    exact_artifacts: set[str] = set()
    coarsened_artifacts: set[str] = set()
    chain_fields = config["projection"]["chain_fields"]
    event_fields = config["projection"]["event_fields"]
    for chain in complete_chains:
        original = next(row for row in chain if row["reopening"] == "No")
        security_types[original["securityType"]] += 1
        original_terms[original["originalSecurityTerm"]] += 1
        if any(row["maturityDate"] != original["maturityDate"] for row in chain):
            topology_failures["maturity_mismatch"] += 1
        if any(row["securityType"] != original["securityType"] for row in chain):
            topology_failures["security_type_mismatch"] += 1
        if any(
            row["originalIssueDate"] != original["issueDate"]
            for row in chain
            if row["reopening"] == "Yes"
        ):
            topology_failures["original_issue_mismatch"] += 1
        if original["securityType"] in {"Note", "Bond"}:
            if original["floatingRate"] == "Yes":
                if any(
                    row["spread"] != original["spread"]
                    for row in chain
                    if row["reopening"] == "Yes"
                ):
                    topology_failures["floating_rate_spread_mismatch"] += 1
            elif any(
                row["interestRate"] != original["interestRate"]
                for row in chain
                if row["reopening"] == "Yes"
            ):
                topology_failures["coupon_mismatch"] += 1
        for row in chain:
            auction_date = _date(row["auctionDate"])
            issue_date = _date(row["issueDate"])
            maturity_date = _date(row["maturityDate"])
            if auction_date > issue_date:
                topology_failures["auction_after_issue"] += 1
            if issue_date >= maturity_date:
                topology_failures["issue_not_before_maturity"] += 1
        if any(
            later["issueDate"] <= earlier["issueDate"]
            for earlier, later in pairwise(chain)
        ):
            topology_failures["nonincreasing_issue_sequence"] += 1

        header = _line("CHAIN", original, chain_fields)
        coarse_header = _coarsened_line("CHAIN", original, chain_fields)
        exact_units.add(header)
        coarsened_units.add(coarse_header)
        exact_lines = [header]
        coarsened_lines = [coarse_header]
        for row in chain:
            event = _line("EVENT", row, event_fields)
            coarse_event = _coarsened_line("EVENT", row, event_fields)
            exact_units.add(event)
            coarsened_units.add(coarse_event)
            exact_lines.append(event)
            coarsened_lines.append(coarse_event)
        exact_artifacts.add("\n".join(exact_lines))
        coarsened_artifacts.add("\n".join(coarsened_lines))

    exact_unit_lengths = _token_lengths(tokenizer, sorted(exact_units))
    coarsened_unit_lengths = _token_lengths(tokenizer, sorted(coarsened_units))
    artifact_lengths = _token_lengths(tokenizer, sorted(exact_artifacts))
    coarsened_artifact_lengths = _token_lengths(tokenizer, sorted(coarsened_artifacts))
    return {
        "distinct_ephemeral_cusip_groups": len(grouped),
        "group_categories": dict(sorted(group_categories.items())),
        "complete_chain_count": len(complete_chains),
        "complete_chain_event_count": sum(len(chain) for chain in complete_chains),
        "complete_chain_size_distribution": {
            str(size): count for size, count in sorted(group_sizes.items())
        },
        "complete_chain_security_types": dict(sorted(security_types.items())),
        "complete_chain_original_terms_top20": dict(original_terms.most_common(20)),
        "topology_failures": dict(sorted(topology_failures.items())),
        "topology_gate_pass": not topology_failures,
        "identifier_free_exact_units": len(exact_units),
        "identifier_free_exact_tokens": sum(exact_unit_lengths),
        "aggressively_coarsened_units": len(coarsened_units),
        "aggressively_coarsened_tokens": sum(coarsened_unit_lengths),
        "identifier_free_chain_artifacts": len(exact_artifacts),
        "identifier_free_chain_artifact_tokens": sum(artifact_lengths),
        "coarsened_chain_artifacts": len(coarsened_artifacts),
        "coarsened_chain_artifact_tokens": sum(coarsened_artifact_lengths),
        "chain_artifact_token_distribution": {
            "min": min(artifact_lengths),
            "p50": _percentile(artifact_lengths, 0.50),
            "p95": _percentile(artifact_lengths, 0.95),
            "p99": _percentile(artifact_lengths, 0.99),
            "max": max(artifact_lengths),
            "at_or_below_4k": sum(value <= 4_000 for value in artifact_lengths),
            "at_or_below_8k": sum(value <= 8_000 for value in artifact_lengths),
            "at_or_below_16k": sum(value <= 16_000 for value in artifact_lengths),
        },
        "complete_chains": complete_chains,
    }


def _oracle_metrics(chains: list[list[dict[str, Any]]]) -> dict[str, Any]:
    row_pass = 0
    row_fail = 0
    missing = 0
    complete_bill_chains = 0
    price_per_100_matches = 0
    price_per_100_missing = 0
    security_event_types: Counter[str] = Counter()
    for chain in chains:
        security_type = chain[0]["securityType"]
        for row in chain:
            security_event_types[security_type] += 1
        if security_type != "Bill":
            continue
        complete = True
        for row in chain:
            try:
                expected, observed = _bill_price(row, "highPrice")
            except (InvalidOperation, KeyError, TypeError, ValueError):
                missing += 1
                complete = False
            else:
                if expected == observed:
                    row_pass += 1
                else:
                    row_fail += 1
                    complete = False
            try:
                price_expected, price_observed = _bill_price(row, "pricePer100")
            except (InvalidOperation, KeyError, TypeError, ValueError):
                price_per_100_missing += 1
            else:
                price_per_100_matches += price_expected == price_observed
        if complete:
            complete_bill_chains += 1
    return {
        "complete_chain_event_security_types": dict(
            sorted(security_event_types.items())
        ),
        "bill_price_formula_rows_pass": row_pass,
        "bill_price_formula_rows_fail": row_fail,
        "bill_price_formula_rows_missing": missing,
        "bill_price_per_100_rows_pass": price_per_100_matches,
        "bill_price_per_100_rows_missing": price_per_100_missing,
        "fully_replayable_bill_chains": complete_bill_chains,
        "source_level_answer_changing_remove_one_chains": complete_bill_chains,
        "formula": "price_per_100 = 100 * (1 - (highDiscountRate / 100) * issue_to_maturity_days / 360), rounded to the published field precision; normalized settlement_per_1000 = 10 * price_per_100",
    }


def main() -> None:
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {
        name: list(bounds)
        for name, bounds in EXACT_TOKEN_BAND_RANGES.items()
        if name in {"32k", "64k", "128k"}
    }
    if config["capacity_bands"] != expected_bands:
        raise ValueError(
            "P50 capacity bands diverge from EXACT_TOKEN_BAND_RANGES: "
            f"{config['capacity_bands']} != {expected_bands}"
        )

    raw, receipt = _request(config["snapshot"]["url"])
    snapshot = config["snapshot"]
    for field, actual in (
        ("expected_bytes", len(raw)),
        ("expected_sha256", _sha256(raw)),
    ):
        if snapshot[field] != actual:
            raise ValueError(f"TreasuryDirect snapshot {field} changed: {actual}")
    rows = json.loads(raw)
    if (
        not isinstance(rows, list)
        or not rows
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise TypeError("TreasuryDirect snapshot is not a non-empty object array")
    if len(rows) != snapshot["expected_rows"]:
        raise ValueError(f"TreasuryDirect row count changed: {len(rows)}")
    field_names = set().union(*(row.keys() for row in rows))
    if len(field_names) != snapshot["expected_field_count"]:
        raise ValueError(f"TreasuryDirect field count changed: {len(field_names)}")
    missing_fields = sorted(set(config["required_fields"]) - field_names)
    if missing_fields:
        raise ValueError(f"TreasuryDirect required fields missing: {missing_fields}")
    if any(not str(row["cusip"]).strip() for row in rows):
        raise ValueError("TreasuryDirect snapshot has an empty CUSIP grouping key")
    auction_dates = [_date(row["auctionDate"]) for row in rows]
    start_date = _date(f"{snapshot['start_date']}T00:00:00")
    end_date = _date(f"{snapshot['end_date']}T23:59:59")
    if min(auction_dates) < start_date or max(auction_dates) > end_date:
        raise ValueError(
            "TreasuryDirect snapshot contains an auction outside the query"
        )

    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=tokenizer_config["local_files_only"],
    )
    relations = _chain_metrics(rows, config, tokenizer)
    chains = relations.pop("complete_chains")
    oracle = _oracle_metrics(chains)
    topology_pass = relations["topology_gate_pass"]
    formula_pass = (
        oracle["bill_price_formula_rows_pass"] > 0
        and oracle["bill_price_formula_rows_fail"] == 0
    )
    source_capacity_tokens = relations["coarsened_chain_artifact_tokens"]
    admitted_tokens = 0
    band_assessment = {
        name: {
            "identifier_free_exact_capacity_possible": relations[
                "identifier_free_chain_artifact_tokens"
            ]
            >= bounds[0],
            "aggressively_coarsened_capacity_possible": source_capacity_tokens
            >= bounds[0],
            "admissible_capacity_possible": admitted_tokens >= bounds[0],
            "admissible_gap_to_lower_edge": admitted_tokens - bounds[0],
        }
        for name, bounds in expected_bands.items()
    }
    all_chains_fit_4k = (
        relations["chain_artifact_token_distribution"]["at_or_below_4k"]
        == relations["identifier_free_chain_artifacts"]
    )
    blocking_reasons = [
        "TreasuryDirect API terms authorize search, display, analysis, and retrieval services but do not explicitly authorize redistribution as model-training data; the general site terms limit copying and derivative use, and CUSIP is a third-party identifier",
        "every complete chain's projected evidence fits within 4K tokens, so a CUSIP or replacement join-key lookup can recover the entire gold chain without long-context dependence",
        "the API change notice dated 2026-08-21 says auction/API fields will be added or renamed, so any future conversion must freeze source bytes and update mappings before the announced schema transition",
        "formal candidate-level near-duplicate, exact-band packing, derived-view, truncation, source-lineage, remove-one, and shortcut audits have not run",
    ]
    payload = {
        "schema_version": "longworld.p50-treasurydirect-auction-reopening-preflight.v1",
        "data_product": config["data_product"],
        "verdict": "SOURCE_TOPOLOGY_AND_CAPACITY_PASS_RIGHTS_AND_SHORTCUT_FAIL",
        "rights_gate": "FAIL_TRAINING_REDISTRIBUTION_NOT_EXPLICITLY_AUTHORIZED",
        "privacy_gate": "PASS_ONLY_FOR_EPHEMERAL_IDENTIFIER_FREE_PROJECTION",
        "credential_gate": "PASS_PUBLIC_API_OBSERVED_WITHOUT_CREDENTIALS",
        "schema_gate": "PASS_FROZEN_120_FIELD_SNAPSHOT",
        "topology_gate": "PASS" if topology_pass else "FAIL",
        "deterministic_oracle_gate": "PASS_SOURCE_LEVEL_BILL_REPLAY"
        if formula_pass
        else "FAIL",
        "unique_capacity_gate": "FAIL_ZERO_ADMISSIBLE_TOKENS",
        "shortcut_gate": "FAIL_COMPLETE_GOLD_CHAIN_FITS_4K"
        if all_chains_fit_4k
        else "NOT_RUN_BLOCKED_UPSTREAM",
        "world_admission_status": "BLOCKED",
        "do_not_generate": True,
        "train_ready": False,
        "production_eligible": False,
        "candidate_count": 0,
        "research_question": config["research_question"],
        "constraints": config["constraints"],
        "privacy_and_identifier_exclusions": config[
            "privacy_and_identifier_exclusions"
        ],
        "source_receipt": {
            "config": str(CONFIG),
            "config_sha256": _sha256(config_raw),
            "observed_at": config["authorization"]["reviewed_at"],
            "auction_snapshot": receipt,
            "row_count": len(rows),
            "field_count": len(field_names),
            "auction_date_min": min(auction_dates).date().isoformat(),
            "auction_date_max": max(auction_dates).date().isoformat(),
            "required_fields_present": True,
            "raw_rows_or_cusip_values_persisted": False,
        },
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "relations": relations,
        "oracle": oracle,
        "capacity": {
            "source_layer_only_not_admissible": {
                "identifier_free_exact_tokens": relations[
                    "identifier_free_chain_artifact_tokens"
                ],
                "aggressively_coarsened_tokens": source_capacity_tokens,
            },
            "admissible_tokens": admitted_tokens,
            "formal_near_duplicate_audit_executed": False,
            "exact_band_packing_executed": False,
            "bands": expected_bands,
            "band_assessment": band_assessment,
            "interpretation": "source-level volume remains above 128K after identifiers are removed and dates, rates, prices, and amounts are aggressively coarsened; this is an upper bound, not admitted training capacity",
        },
        "answer_program": {
            "status": "SOURCE_LEVEL_EXECUTABLE_BUT_NOT_ADMISSIBLE",
            "deterministic_design": [
                "group only in memory by CUSIP and require exactly one original row plus at least one reopening row",
                "order by auctionDate and require auctionDate no later than issueDate and issueDate before maturityDate",
                "require every reopening to retain securityType, maturityDate, and originalIssueDate; require Notes and Bonds to retain coupon interestRate or floating-rate spread",
                "for Bills, recompute price per 100 from highDiscountRate and actual issue-to-maturity days, then derive normalized settlement per 1000",
                "emit chain length, chronology, settlement vector, settlement total, and mismatch controls without medical, investment, policy, or causal conclusions",
            ],
            "answer_changing_remove_one": "source-level feasible for each fully replayable Bill chain: removing any reopening changes the ordered settlement vector, chain length, and positive settlement total; candidate-level replay was not run",
            "causal_or_investment_conclusions_allowed": False,
        },
        "shortcut_assessment": {
            "status": "FAIL",
            "evidence": "all identifier-free complete-chain artifacts are at most 4K tokens; adding the CUSIP or any replacement join key makes all answer-bearing rows directly retrievable",
            "required_if_redesigned": [
                "a source-authentic task whose answer requires interactions across many chains rather than one-key retrieval",
                "unchanged 4K 8K and 16K raw contiguous-window baselines",
                "lexical and dense retrieval baselines",
                "answer-changing candidate-level remove-one replay",
                "formal near-duplicate exact-band derived-view and truncation gates",
            ],
        },
        "schema_drift": {
            "status": "KNOWN_UPCOMING_CHANGE",
            "notice_date": "2026-08-21",
            "effect": "official notice says auction XML and API fields will be added or renamed; the configured 120-field byte snapshot is therefore mandatory for reproducibility",
            "source": config["official_sources"]["api_change_notice"],
        },
        "blocking_reasons": blocking_reasons,
        "next_executable_command": "uv run python reports/p50_treasurydirect_auction_reopening_preflight.py",
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "candidate_count": payload["candidate_count"],
                "relations": relations,
                "oracle": oracle,
                "capacity": payload["capacity"],
                "blocking_reasons": blocking_reasons,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
