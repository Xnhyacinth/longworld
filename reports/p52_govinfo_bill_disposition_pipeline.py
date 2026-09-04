"""P52 source-bound GovInfo bill-disposition candidate and audit pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.pack import SEP, prompt_document_prefix, wrap_prompt
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_AUDIT_PURPOSE,
    DENSE_RANKING_PURPOSE,
    PromotionError,
    _validate_external_ranking,
    candidate_sha256,
    serialized_row_sha256,
)
from longworld.core.record_contract import (
    EXACT_TOKEN_BAND_RANGES,
    STRICT_REPLAY_REVISION,
    exact_token_band_reject_reason,
)
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)
from reports import p49_govinfo_bill_text_disposition_preflight as p49

RETAINED = "R"
MODIFIED = "M"
UNKNOWN = "U"
SOURCE_RECEIPT_PURPOSE = "source_manifest"
SOURCE_RECEIPT_SCHEMA = "longworld.p52-govinfo-source-receipt.v1"
CANDIDATE_SCHEMA = "longworld.p52-govinfo-bill-disposition-candidate.v1"
AUDIT_SCHEMA = "longworld.p52-govinfo-bill-disposition-dense-audit.v1"
ORACLE_ARTIFACT_REVISION = "p52-govinfo-whole-section-json-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class P52Blocker(ValueError):
    """A fail-closed P52 source, dependency, or data-contract blocker."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value).rstrip(b"\n"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise P52Blocker(f"{path}:{line_number}: invalid JSON") from error
            if not isinstance(row, dict):
                raise P52Blocker(f"{path}:{line_number}: expected JSON object")
            rows.append(row)
    return rows


def _write_atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    _write_atomic(path, b"".join(_canonical_bytes(row) for row in rows))


def _load_config(path: Path) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    raw = path.read_bytes()
    config = json.loads(raw)
    if not isinstance(config, dict) or config.get("schema_version") != (
        "longworld.p52-govinfo-bill-disposition-generation.v1"
    ):
        raise P52Blocker("P52 generation config schema is invalid")
    preflight_binding = config.get("preflight_config")
    if not isinstance(preflight_binding, dict):
        raise P52Blocker("P52 preflight config binding is missing")
    preflight_path = Path(str(preflight_binding.get("path") or ""))
    preflight_raw = preflight_path.read_bytes()
    if _sha256_bytes(preflight_raw) != preflight_binding.get("sha256"):
        raise P52Blocker("P49 preflight config bytes changed")
    preflight = json.loads(preflight_raw)
    expected_bands = {
        bucket: list(EXACT_TOKEN_BAND_RANGES[bucket])
        for bucket in ("32k", "64k", "128k")
    }
    if config.get("length_buckets") != expected_bands:
        raise P52Blocker("P52 exact bands differ from repository bands")
    authorization = config.get("authorization")
    allowed_actions = (
        authorization.get("allowed_actions", [])
        if isinstance(authorization, dict)
        else []
    )
    prohibited_actions = (
        authorization.get("prohibited_actions", [])
        if isinstance(authorization, dict)
        else []
    )
    if (
        not isinstance(authorization, dict)
        or "sign_candidate_rows" not in allowed_actions
        or (
            "derive_one_explicit_counterfactual_by_replacing_the_target_body_with_its_authenticated_source_body"
            not in allowed_actions
        )
        or "persist_raw_xml" not in prohibited_actions
        or "pad_or_clone_background_context_to_reach_a_length_band"
        not in prohibited_actions
    ):
        raise P52Blocker("P52 candidate authorization is incomplete")
    if (
        config.get("train_ready") is not False
        or config.get("promotion_eligible") is not False
    ):
        raise P52Blocker("P52 must remain outside shared promotion until registered")
    return config, raw, preflight


def section_units_from_xml(
    raw: bytes,
    *,
    structural_ancestors: set[str],
    excluded_tags: set[str],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Normalize Bill XML or USLM XML into unambiguous whole sections."""
    root = p49._xml(raw, "P52 cross-schema fixture")
    units, ambiguous = p49._section_units(root, structural_ancestors, excluded_tags)
    sections = [item for item in root.iter() if p49._local(item) == "section"]
    if len(sections) != len(units):
        raise P52Blocker("P52 section normalization lost source alignment")
    for unit, section in zip(units, sections, strict=True):
        oracle_text = p49._content_text(section, excluded_tags | {"enum", "num"})
        unit["oracle_text"] = oracle_text
        unit["oracle_canonical"] = p49._canonical(oracle_text)
    counts = Counter(str(unit["base_key"]) for unit in units)
    return (
        {
            str(unit["base_key"]): dict(unit)
            for unit in units
            if counts[str(unit["base_key"])] == 1
        },
        set(ambiguous),
    )


def cross_schema_dispositions(
    source: Mapping[str, Mapping[str, Any]],
    target: Mapping[str, Mapping[str, Any]],
    *,
    shingle_size: int,
    threshold: float,
) -> dict[str, str]:
    """Classify common unambiguous keys without making a legal-effect claim."""
    if shingle_size <= 0 or not 0 < threshold <= 1:
        raise P52Blocker("P52 oracle parameters are invalid")
    result: dict[str, str] = {}
    for key in sorted(source.keys() & target.keys()):
        similarity = p49._jaccard(
            p49._shingles(str(source[key]["oracle_canonical"]), shingle_size),
            p49._shingles(str(target[key]["oracle_canonical"]), shingle_size),
        )
        result[key] = RETAINED if similarity >= threshold else MODIFIED
    return result


def _artifact_documents(candidate: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        raise P52Blocker("candidate artifact serialization is missing")
    documents = document_context.split(SEP)
    if len(documents) != len(classifications):
        raise P52Blocker("candidate artifact/document counts differ")
    parsed: dict[str, dict[str, Any]] = {}
    for classification, document in zip(classifications, documents, strict=True):
        if not isinstance(classification, dict):
            raise P52Blocker("candidate classification is malformed")
        artifact_id = str(classification.get("artifact_id") or "")
        if not artifact_id or artifact_id in parsed:
            raise P52Blocker("candidate artifact identity is missing or duplicated")
        try:
            value = json.loads(document)
        except json.JSONDecodeError as error:
            raise P52Blocker("candidate artifact is not JSON") from error
        if not isinstance(value, dict):
            raise P52Blocker("candidate artifact JSON is malformed")
        parsed[artifact_id] = value
    return parsed


def replay_candidate(
    candidate: Mapping[str, Any], evidence_artifact_ids: Sequence[str]
) -> dict[str, str]:
    """Replay requested source-text dispositions from the selected artifacts."""
    if candidate.get("context") != wrap_prompt(
        str(candidate.get("question") or ""),
        str(candidate.get("document_context") or ""),
        str(candidate.get("query_timing") or ""),
    ):
        raise P52Blocker("candidate serialized prompt is not canonical")
    documents = _artifact_documents(candidate)
    selected = list(evidence_artifact_ids)
    if len(selected) != len(set(selected)) or any(
        item not in documents for item in selected
    ):
        raise P52Blocker("candidate replay selection is invalid")
    chosen = [documents[item] for item in selected]
    relations = {
        (
            str(item.get("bill_id") or ""),
            str(item.get("from_stage") or ""),
            str(item.get("to_stage") or ""),
        )
        for item in chosen
        if item.get("artifact_type") == "govinfo_transition"
        and item.get("action_date")
        and item.get("action_texts")
    }
    sections: dict[tuple[str, str, str], str] = {}
    for item in chosen:
        if item.get("artifact_type") != "govinfo_section":
            continue
        key = (
            str(item.get("bill_id") or ""),
            str(item.get("stage") or ""),
            str(item.get("base_key") or ""),
        )
        text = item.get("oracle_text")
        if key in sections or not isinstance(text, str) or not text:
            raise P52Blocker("candidate replay section is missing or duplicated")
        sections[key] = text
    requests = candidate.get("requested_dispositions")
    if not isinstance(requests, list) or not requests:
        raise P52Blocker("candidate disposition request is missing")
    shingle_size = int(candidate.get("oracle_shingle_size") or 0)
    threshold = float(candidate.get("oracle_threshold") or 0)
    if shingle_size <= 0 or not 0 < threshold <= 1:
        raise P52Blocker("candidate oracle parameters are invalid")
    result: dict[str, str] = {}
    for request in requests:
        if not isinstance(request, dict):
            raise P52Blocker("candidate disposition request is malformed")
        code = str(request.get("code") or "")
        bill_id = str(request.get("bill_id") or "")
        base_key = str(request.get("base_key") or "")
        from_stage = str(request.get("from_stage") or "")
        to_stage = str(request.get("to_stage") or "")
        if not code or code in result:
            raise P52Blocker("candidate disposition code is missing or duplicated")
        source = sections.get((bill_id, from_stage, base_key))
        target = sections.get((bill_id, to_stage, base_key))
        if (
            (bill_id, from_stage, to_stage) not in relations
            or source is None
            or target is None
        ):
            result[code] = UNKNOWN
            continue
        similarity = p49._jaccard(
            p49._shingles(p49._canonical(source), shingle_size),
            p49._shingles(p49._canonical(target), shingle_size),
        )
        result[code] = RETAINED if similarity >= threshold else MODIFIED
    return result


def _stage_source(chain: Mapping[str, Any], stage_name: str) -> Mapping[str, Any]:
    matches = [stage for stage in chain["stages"] if stage["stage"] == stage_name]
    if len(matches) != 1:
        raise P52Blocker(f"P52 source stage is not unique: {stage_name}")
    return matches[0]


def _status_action_texts(
    root: Any, chain: Mapping[str, Any], from_stage: str, to_stage: str
) -> tuple[str, list[str]]:
    edges = [
        edge
        for edge in chain["action_edges"]
        if edge["from"] == from_stage and edge["to"] == to_stage
    ]
    if len(edges) != 1:
        raise P52Blocker("P52 transition action edge is not unique")
    edge = edges[0]
    bills = p49._children(root, "bill")
    if len(bills) != 1:
        raise P52Blocker("P52 Bill Status node is not unique")
    actions: list[str] = []
    for container in p49._children(bills[0], "actions"):
        for item in p49._children(container, "item"):
            date = p49._direct_text(item, "actionDate")
            text = p49._direct_text(item, "text")
            if date == edge["date"] and edge["required_text"] in text:
                actions.append(text)
    unique = sorted(set(actions))
    if not unique:
        raise P52Blocker("P52 authenticated transition text is missing")
    return str(edge["date"]), unique


def _section_artifact_id(record: Mapping[str, Any]) -> str:
    suffix = _sha256_text(str(record["base_key"]))[:20]
    return f"govinfo:{record['bill_id']}:{record['stage']}:{suffix}"


def _section_document(record: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "artifact_type": "govinfo_section",
            "bill_id": record["bill_id"],
            "stage": record["stage"],
            "stage_date": record["stage_date"],
            "base_key": record["base_key"],
            "source_url": record["source_url"],
            "source_sha256": record["source_sha256"],
            "derivation_revision": ORACLE_ARTIFACT_REVISION,
            "source_text": record["text"],
            "oracle_text": record["oracle_text"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _section_entry(
    record: Mapping[str, Any], *, workflow_id: str, essential: bool
) -> dict[str, Any]:
    document = _section_document(record)
    artifact_id = _section_artifact_id(record)
    provenance_id = (
        f"govinfo-section-sha256:{record['source_sha256']}:"
        f"{_sha256_text(str(record['base_key']))}:{_sha256_text(record['text'])}"
    )
    return {
        "artifact_id": artifact_id,
        "document": document,
        "record": dict(record),
        "sort_key": (
            str(record["stage_date"]),
            str(record["stage"]),
            str(record["base_key"]),
        ),
        "classification": {
            "artifact_id": artifact_id,
            "workflow_id": workflow_id,
            "source_origin": "real_public",
            "workflow_kind": "real_source_derived",
            "evidence_role": "causal_gold" if essential else "natural_background",
            "provenance_id": provenance_id,
            "source_url": record["source_url"],
            "source_record_id": record["record_id"],
            "source_sha256": record["source_sha256"],
            "derived_text_sha256": _sha256_text(document),
            "base_key": record["base_key"],
            "stage": record["stage"],
            "derivation_revision": ORACLE_ARTIFACT_REVISION,
            "whole_section": True,
            "truncated": False,
        },
    }


def _relation_entry(state: Mapping[str, Any], *, workflow_id: str) -> dict[str, Any]:
    document = json.dumps(
        {
            "artifact_type": "govinfo_transition",
            "bill_id": state["bill_id"],
            "from_stage": state["from_stage"],
            "to_stage": state["to_stage"],
            "action_date": state["action_date"],
            "action_texts": state["action_texts"],
            "status_source_sha256": state["status_source_sha256"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    artifact_id = (
        f"govinfo:{state['bill_id']}:relation:{state['from_stage']}-{state['to_stage']}"
    )
    return {
        "artifact_id": artifact_id,
        "document": document,
        "record": None,
        "sort_key": (state["action_date"], "transition", artifact_id),
        "classification": {
            "artifact_id": artifact_id,
            "workflow_id": workflow_id,
            "source_origin": "real_public",
            "workflow_kind": "real_source_derived",
            "evidence_role": "causal_gold",
            "provenance_id": (
                f"govinfo-status-sha256:{state['status_source_sha256']}:"
                f"{_sha256_text(document)}"
            ),
            "source_url": state["status_source_url"],
            "source_record_id": f"{state['bill_id']}:bill-status",
            "source_sha256": state["status_source_sha256"],
            "derived_text_sha256": _sha256_text(document),
            "derivation_revision": ORACLE_ARTIFACT_REVISION,
            "whole_section": False,
            "truncated": False,
        },
    }


def _fetch_source_state(
    config: Mapping[str, Any], preflight: Mapping[str, Any]
) -> dict[str, Any]:
    chains = [
        chain for chain in preflight["chains"] if chain["bill_id"] == config["bill_id"]
    ]
    if len(chains) != 1:
        raise P52Blocker("P52 selected bill chain is not unique")
    chain = chains[0]
    receipts: list[dict[str, Any]] = []
    status_raw, status_receipt = p49._fetch(chain["status_source"])
    status_root = p49._xml(status_raw, f"{chain['bill_id']} Bill Status")
    p49._validate_status(status_root, chain)
    receipts.append(status_receipt)
    action_date, action_texts = _status_action_texts(
        status_root, chain, str(config["from_stage"]), str(config["to_stage"])
    )

    oracle = preflight["section_oracle"]
    structural = set(oracle["structural_ancestors"])
    excluded = set(oracle["presentation_only_tags"])
    minimum = int(config["oracle"]["minimum_section_characters"])
    records: dict[tuple[str, str], dict[str, Any]] = {}
    all_units: list[dict[str, Any]] = []
    stage_ambiguous: dict[str, set[str]] = {}
    for stage in chain["stages"]:
        raw, receipt = p49._fetch(stage)
        receipts.append(receipt)
        root = p49._xml(raw, f"{chain['bill_id']} {stage['stage']}")
        p49._validate_stage(root, chain, stage, preflight["public_domain_text"])
        units, ambiguous = section_units_from_xml(
            raw,
            structural_ancestors=structural,
            excluded_tags=excluded,
        )
        stage_name = str(stage["stage"])
        stage_ambiguous[stage_name] = ambiguous
        for base_key, unit in units.items():
            if len(str(unit["text"])) < minimum:
                continue
            record = {
                **unit,
                "bill_id": chain["bill_id"],
                "stage": stage_name,
                "stage_date": stage["date"],
                "source_url": stage["url"],
                "source_sha256": receipt["sha256"],
                "record_id": f"{chain['bill_id']}:{stage_name}:{base_key}",
                "stage_id": f"{chain['bill_id']}:{stage_name}",
            }
            records[(stage_name, base_key)] = record
            all_units.append(record)

    shingle_size = int(config["oracle"]["near_duplicate_word_shingle_size"])
    threshold = float(config["oracle"]["near_duplicate_jaccard_threshold"])
    from_stage = str(config["from_stage"])
    to_stage = str(config["to_stage"])
    requested_keys = list(config["requested_keys"])
    for base_key in requested_keys:
        if (
            base_key in stage_ambiguous[from_stage]
            or base_key in stage_ambiguous[to_stage]
        ):
            raise P52Blocker(f"requested structural key is ambiguous: {base_key}")
        source = records.get((from_stage, base_key))
        target = records.get((to_stage, base_key))
        if source is None or target is None:
            raise P52Blocker(f"requested cross-schema pair is missing: {base_key}")
        disposition = cross_schema_dispositions(
            {base_key: source},
            {base_key: target},
            shingle_size=shingle_size,
            threshold=threshold,
        ).get(base_key)
        if disposition != MODIFIED:
            raise P52Blocker(
                f"requested pair is not a modified source-text pair: {base_key}"
            )

    near_units, _removed = p49._near_deduplicate(all_units, shingle_size, threshold)
    essential_records = {
        (stage, key) for key in requested_keys for stage in (from_stage, to_stage)
    }
    essential_shingles = [
        p49._shingles(records[key]["canonical"], shingle_size)
        for key in sorted(essential_records)
    ]
    fillers: list[dict[str, Any]] = []
    for unit in near_units:
        identity = (str(unit["stage"]), str(unit["base_key"]))
        if identity in essential_records:
            continue
        shingles = unit["shingles"]
        if any(
            p49._jaccard(shingles, essential) >= threshold
            for essential in essential_shingles
        ):
            continue
        clean = dict(unit)
        clean.pop("shingles", None)
        fillers.append(clean)

    bundle_lines = [f"{item['url']}:{item['sha256']}" for item in receipts]
    return {
        "bill_id": chain["bill_id"],
        "from_stage": from_stage,
        "to_stage": to_stage,
        "action_date": action_date,
        "action_texts": action_texts,
        "status_source_url": chain["status_source"]["url"],
        "status_source_sha256": status_receipt["sha256"],
        "records": records,
        "fillers": sorted(
            fillers,
            key=lambda item: (_sha256_text(str(item["text"])), str(item["record_id"])),
        ),
        "source_receipts": receipts,
        "source_bundle_sha256": _sha256_text("\n".join(bundle_lines) + "\n"),
    }


def _verified_source_state(
    config: Mapping[str, Any], preflight: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        return _fetch_source_state(config, preflight)
    except P52Blocker:
        raise
    except (OSError, ValueError) as error:
        raise P52Blocker(f"P52 frozen-source verification failed: {error}") from error


def _source_receipt(
    config: Mapping[str, Any], config_raw: bytes, state: Mapping[str, Any], key: bytes
) -> tuple[dict[str, Any], bytes]:
    payload = attach_attestation(
        {
            "schema_version": SOURCE_RECEIPT_SCHEMA,
            "data_stage": "source_inventory",
            "authorization": deepcopy(config["authorization"]),
            "config_sha256": _sha256_bytes(config_raw),
            "preflight_config_sha256": config["preflight_config"]["sha256"],
            "oracle_revision": config["oracle"]["revision"],
            "source_bundle_sha256": state["source_bundle_sha256"],
            "source_count": len(state["source_receipts"]),
            "sources": deepcopy(state["source_receipts"]),
            "raw_xml_persisted": False,
            "train_ready": False,
            "production_eligible": False,
        },
        key,
        purpose=SOURCE_RECEIPT_PURPOSE,
    )
    return payload, _canonical_bytes(payload)


def _question(
    config: Mapping[str, Any], count: int
) -> tuple[str, list[dict[str, str]]]:
    requests = [
        {
            "code": f"D{index:02d}",
            "bill_id": str(config["bill_id"]),
            "base_key": str(base_key),
            "from_stage": str(config["from_stage"]),
            "to_stage": str(config["to_stage"]),
        }
        for index, base_key in enumerate(config["requested_keys"][:count], start=1)
    ]
    lines = [
        "Resolve each requested GovInfo ENR-to-Public-Law source-text disposition.",
        "Use the authenticated transition record and compare the two whole sections after excluding page, sidenote, sourceCredit, and note presentation subtrees plus the num or enum identifier already encoded in the structural key.",
        "Codebook: R=retained (canonical five-word-shingle Jaccard at least 0.90); M=modified (same unambiguous structural key below 0.90); U=unknown (transition or either endpoint absent).",
        "Return only one compact JSON object whose keys are the requested codes.",
    ]
    lines.extend(
        f"{item['code']}={item['bill_id']} {item['from_stage']}->{item['to_stage']} {item['base_key']}"
        for item in requests
    )
    return "\n".join(lines), requests


def _relations(
    config: Mapping[str, Any], requests: Sequence[Mapping[str, str]]
) -> list[dict[str, str]]:
    return [
        {
            "relation_id": (
                f"govinfo:{config['bill_id']}:enr-law:"
                f"{_sha256_text(request['base_key'])[:20]}"
            ),
            "parent_record_id": (
                f"{config['bill_id']}:{config['from_stage']}:{request['base_key']}"
            ),
            "child_record_id": (
                f"{config['bill_id']}:{config['to_stage']}:{request['base_key']}"
            ),
            "relation_provenance": "authenticated_bill_status_transition",
        }
        for request in requests
    ]


def _cf_entry(target: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    target_document = json.loads(str(target["document"]))
    source_document = json.loads(str(source["document"]))
    parent_text = str(target_document["source_text"])
    replacement = str(source_document["source_text"])
    parent_oracle_text = str(target_document["oracle_text"])
    replacement_oracle_text = str(source_document["oracle_text"])
    target_document["source_text"] = replacement
    target_document["oracle_text"] = replacement_oracle_text
    target_document["counterfactual"] = {
        "operation": "replace_target_with_source_text",
        "parent_text_sha256": _sha256_text(parent_text),
        "replacement_text_sha256": _sha256_text(replacement),
        "parent_oracle_text_sha256": _sha256_text(parent_oracle_text),
        "replacement_oracle_text_sha256": _sha256_text(replacement_oracle_text),
    }
    document = json.dumps(
        target_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    result = deepcopy(dict(target))
    result["document"] = document
    classification = deepcopy(dict(target["classification"]))
    classification.update(
        {
            "source_origin": "synthetic_counterfactual",
            "evidence_role": "causal_gold",
            "counterfactual_operation": "replace_target_with_source_text",
            "counterfactual_parent_provenance_id": classification["provenance_id"],
            "counterfactual_parent_text_sha256": _sha256_text(str(target["document"])),
            "derived_text_sha256": _sha256_text(document),
            "provenance_id": f"counterfactual-sha256:{_sha256_text(document)}",
        }
    )
    result["classification"] = classification
    return result


def _ordered(entries: Iterable[Mapping[str, Any]], view: str) -> list[dict[str, Any]]:
    values = [deepcopy(dict(entry)) for entry in entries]
    if view == "ordered_artifact_view":
        return sorted(values, key=lambda item: tuple(item["sort_key"]))
    return sorted(values, key=lambda item: _sha256_text(str(item["artifact_id"])))


def _view_entries(
    entries: Iterable[Mapping[str, Any]],
    view: str,
    *,
    cf_target_id: str,
    cf_source_id: str,
) -> list[dict[str, Any]]:
    by_id = {str(entry["artifact_id"]): dict(entry) for entry in entries}
    if view == "cf":
        by_id[cf_target_id] = _cf_entry(by_id[cf_target_id], by_id[cf_source_id])
    return _ordered(by_id.values(), view)


def _context(question: str, entries: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    document_context = SEP.join(str(entry["document"]) for entry in entries)
    return document_context, wrap_prompt(question, document_context, "first")


def _token_count(tokenizer: Any, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False))


def _candidate_near_duplicate_ratio(entries: Sequence[Mapping[str, Any]]) -> float:
    seen: set[str] = set()
    duplicate_characters = 0
    total_characters = 0
    for entry in entries:
        payload = json.loads(str(entry["document"]))
        text = str(payload.get("source_text") or "")
        for raw in _SENTENCE.split(text):
            sentence = " ".join(raw.lower().split())
            if len(sentence) < 40:
                continue
            total_characters += len(sentence)
            if sentence in seen:
                duplicate_characters += len(sentence)
            seen.add(sentence)
    return duplicate_characters / total_characters if total_characters else 0.0


def _prefix_artifact_ids(
    candidate: Mapping[str, Any], tokenizer: Any, limit: int
) -> list[str]:
    classifications = candidate["artifact_classification"]
    documents = str(candidate["document_context"]).split(SEP)
    prefix = prompt_document_prefix(str(candidate["question"]), "first")
    full = str(candidate["context"])
    encoded = tokenizer(
        full,
        add_special_tokens=False,
        return_offsets_mapping=True,
    )
    offsets = encoded.get("offset_mapping")
    if not isinstance(offsets, list):
        raise P52Blocker("P52 tokenizer has no offset mapping")
    token_ends = [int(pair[1]) for pair in offsets[:limit]]
    boundary = token_ends[-1] if token_ends else 0
    cursor = len(prefix)
    selected: list[str] = []
    for index, (classification, document) in enumerate(
        zip(classifications, documents, strict=True)
    ):
        if index:
            cursor += len(SEP)
        cursor += len(document)
        if cursor <= boundary:
            selected.append(str(classification["artifact_id"]))
    return selected


def _entries_for_requests(
    config: Mapping[str, Any],
    state: Mapping[str, Any],
    requests: Sequence[Mapping[str, str]],
) -> tuple[dict[str, dict[str, Any]], list[str], str, str]:
    workflow_id = str(config["workflow_id"])
    entries: dict[str, dict[str, Any]] = {}
    relation = _relation_entry(state, workflow_id=workflow_id)
    entries[relation["artifact_id"]] = relation
    essential_ids = [str(relation["artifact_id"])]
    cf_source_id = ""
    cf_target_id = ""
    for request in requests:
        source_record = state["records"][(request["from_stage"], request["base_key"])]
        target_record = state["records"][(request["to_stage"], request["base_key"])]
        for record in (source_record, target_record):
            entry = _section_entry(record, workflow_id=workflow_id, essential=True)
            entries[entry["artifact_id"]] = entry
            essential_ids.append(str(entry["artifact_id"]))
        if request["code"] == config["counterfactual_code"]:
            cf_source_id = _section_artifact_id(source_record)
            cf_target_id = _section_artifact_id(target_record)
    if not cf_source_id or not cf_target_id:
        raise P52Blocker("P52 counterfactual request is not present")
    return entries, essential_ids, cf_source_id, cf_target_id


def _pack_bucket(
    config: Mapping[str, Any],
    state: Mapping[str, Any],
    tokenizer: Any,
    *,
    bucket: str,
    prior_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], str, list[dict[str, str]], list[str], str, str]:
    count = int(config["requested_key_count_by_bucket"][bucket])
    question, requests = _question(config, count)
    required, essential_ids, cf_source_id, cf_target_id = _entries_for_requests(
        config, state, requests
    )
    filler_entries = {
        entry["artifact_id"]: entry
        for entry in (
            _section_entry(
                record, workflow_id=str(config["workflow_id"]), essential=False
            )
            for record in state["fillers"]
        )
    }
    selected: dict[str, dict[str, Any]] = {}
    for artifact_id in sorted(prior_ids | set(required)):
        entry = required.get(artifact_id) or filler_entries.get(artifact_id)
        if entry is None:
            raise P52Blocker("P52 nested source pack cannot be reconstructed")
        selected[artifact_id] = entry
    lower, upper = config["length_buckets"][bucket]

    def counts(values: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
        result = {}
        for view in config["views"]:
            entries = _view_entries(
                values.values(),
                view,
                cf_target_id=cf_target_id,
                cf_source_id=cf_source_id,
            )
            _document_context, prompt = _context(question, entries)
            result[str(view)] = _token_count(tokenizer, prompt)
        return result

    observed = counts(selected)
    remaining = [
        (artifact_id, entry)
        for artifact_id, entry in filler_entries.items()
        if artifact_id not in selected
    ]
    if lower - min(observed.values()) > 4096:
        estimated = max(observed.values())
        for artifact_id, entry in remaining:
            incremental = _token_count(tokenizer, SEP + str(entry["document"]))
            if estimated + incremental > lower - 2048:
                continue
            selected[artifact_id] = entry
            estimated += incremental
        observed = counts(selected)
    for artifact_id, entry in remaining:
        if artifact_id in selected:
            continue
        if min(observed.values()) >= lower:
            break
        trial = {**selected, artifact_id: entry}
        trial_counts = counts(trial)
        if max(trial_counts.values()) <= upper:
            selected = trial
            observed = trial_counts
    if min(observed.values()) < lower or max(observed.values()) > upper:
        raise P52Blocker(
            f"{bucket} exact natural pack unavailable: {observed}, band={lower}-{upper}"
        )
    return selected, question, requests, essential_ids, cf_source_id, cf_target_id


def _build_candidate(
    config: Mapping[str, Any],
    tokenizer: Any,
    source_receipt_sha256: str,
    *,
    bucket: str,
    entries: Mapping[str, Mapping[str, Any]],
    question: str,
    requests: list[dict[str, str]],
    essential_ids: list[str],
    cf_source_id: str,
    cf_target_id: str,
    view: str,
) -> dict[str, Any]:
    ordered = _view_entries(
        entries.values(), view, cf_target_id=cf_target_id, cf_source_id=cf_source_id
    )
    document_context, context = _context(question, ordered)
    answer_mapping = replay_candidate(
        {
            "question": question,
            "query_timing": "first",
            "requested_dispositions": requests,
            "oracle_shingle_size": config["oracle"]["near_duplicate_word_shingle_size"],
            "oracle_threshold": config["oracle"]["near_duplicate_jaccard_threshold"],
            "artifact_classification": [entry["classification"] for entry in ordered],
            "document_context": document_context,
            "context": context,
        },
        [str(entry["artifact_id"]) for entry in ordered],
    )
    answer = json.dumps(answer_mapping, sort_keys=True, separators=(",", ":"))
    authentic_entries = [
        entry
        for entry in ordered
        if entry["classification"]["source_origin"] == "real_public"
    ]
    without_real_context = SEP.join(
        str(entry["document"]) for entry in ordered if entry not in authentic_entries
    )
    source_tokens = _token_count(tokenizer, context) - _token_count(
        tokenizer, wrap_prompt(question, without_real_context, "first")
    )
    requested_keys = [request["base_key"] for request in requests]
    relations = _relations(config, requests)
    token_count = _token_count(tokenizer, context)
    candidate: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA,
        "data_product": config["data_product"],
        "world_id": config["world_id"],
        "query_id": f"{config['world_id']}:{bucket}:{view}:first",
        "domain": "government_legislation",
        "data_stage": "candidate",
        "training_objective": "sft",
        "length_bucket": bucket,
        "view": view,
        "composition_method": {
            "full": "same_bill_lifecycle_dossier",
            "cf": "counterfactual_transition_dossier",
            "ordered_artifact_view": "authenticated_legislative_timeline",
        }[view],
        "query_timing": "first",
        "question": question,
        "answer": answer,
        "cf_answer": "",
        "requested_dispositions": requests,
        "oracle_revision": config["oracle"]["revision"],
        "oracle_shingle_size": config["oracle"]["near_duplicate_word_shingle_size"],
        "oracle_threshold": config["oracle"]["near_duplicate_jaccard_threshold"],
        "document_context": document_context,
        "context": context,
        "artifact_classification": [entry["classification"] for entry in ordered],
        "source_record_ids_by_artifact": {
            str(entry["artifact_id"]): [
                str(entry["classification"]["source_record_id"])
            ]
            for entry in ordered
        },
        "essential_artifact_ids": essential_ids,
        "source_record_ids": sorted(
            {
                str(entry["classification"]["source_record_id"])
                for entry in ordered
                if entry["classification"]["source_origin"] == "real_public"
            }
        ),
        "source_relation_ids": [relation["relation_id"] for relation in relations],
        "authentic_source_relation_edges": relations,
        "source_binding": {
            "source_receipt_sha256": source_receipt_sha256,
            "authorization_record_id": config["authorization"]["record_id"],
            "preflight_config_sha256": config["preflight_config"]["sha256"],
        },
        "source_family_ids": [
            "govinfo.gov/billstatus",
            "govinfo.gov/congressional-bills",
        ],
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "base_task_id": _sha256_text(f"{config['world_id']}|bill-disposition")[:20],
        "semantic_base_task_id": _sha256_text(
            "govinfo|enr-law|source-text-disposition"
        )[:20],
        "semantic_growth_group_id": _sha256_text(f"{config['world_id']}|nested-bands")[
            :20
        ],
        "executable_proof_id": _canonical_sha256(requested_keys)[:20],
        "answer_program_id": _sha256_text(str(config["oracle"]["revision"]))[:20],
        "dossier_id": _sha256_text(f"{config['world_id']}|{bucket}|first")[:20],
        "graph": {
            "proof_depth": 2,
            "hop_count": 2,
            "n_essential_events": len(essential_ids),
        },
        "strict_support_event_count": len(essential_ids),
        "dependency_class": "long_range",
        "tokenizer_model_id": config["tokenizer"]["model_id"],
        "tokenizer_revision": config["tokenizer"]["revision"],
        "tokenizer_asset_manifest_sha256": config["tokenizer"]["asset_manifest_sha256"],
        "tokenizer_context_tokens": token_count,
        "actual_context_tokens": token_count,
        "real_source_marginal_tokens": source_tokens,
        "real_source_token_ratio": source_tokens / token_count,
        "near_dup_sentence_ratio": round(_candidate_near_duplicate_ratio(ordered), 4),
        "padding_tokens": 0,
        "cloned_artifacts": 0,
        "split_or_truncated_sections": 0,
        "truncation_ppm": 0,
        "promotion_eligible": False,
        "train_ready": False,
        "production_eligible": False,
        "promoted": False,
    }
    return candidate


def _load_tokenizer(config: Mapping[str, Any]) -> Any:
    tokenizer = config["tokenizer"]
    try:
        observed_assets = resolved_tokenizer_asset_manifest_sha256(
            tokenizer["model_id"], tokenizer["revision"]
        )
    except (OSError, ValueError) as error:
        raise P52Blocker(f"P52 tokenizer resolution failed: {error}") from error
    if observed_assets != tokenizer["asset_manifest_sha256"]:
        raise P52Blocker("P52 tokenizer asset manifest changed")
    try:
        return AutoTokenizer.from_pretrained(
            tokenizer["model_id"],
            revision=tokenizer["revision"],
            trust_remote_code=False,
            local_files_only=True,
        )
    except (OSError, ValueError) as error:
        raise P52Blocker(f"P52 tokenizer load failed: {error}") from error


def build_candidates(
    config_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], bytes, dict[str, Any]]:
    config, config_raw, preflight = _load_config(config_path)
    source_key = attestation_key_from_env(SOURCE_RECEIPT_PURPOSE)
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if source_key is None or candidate_key is None:
        raise P52Blocker("P52 generation requires source and candidate role keys")
    state = _verified_source_state(config, preflight)
    source_receipt, source_receipt_raw = _source_receipt(
        config, config_raw, state, source_key
    )
    source_receipt_sha256 = _sha256_bytes(source_receipt_raw)
    tokenizer = _load_tokenizer(config)
    signed: list[dict[str, Any]] = []
    prior_ids: set[str] = set()
    pack_receipts: dict[str, Any] = {}
    for bucket in ("32k", "64k", "128k"):
        entries, question, requests, essential_ids, cf_source_id, cf_target_id = (
            _pack_bucket(config, state, tokenizer, bucket=bucket, prior_ids=prior_ids)
        )
        prior_ids = set(entries)
        rows = [
            _build_candidate(
                config,
                tokenizer,
                source_receipt_sha256,
                bucket=bucket,
                entries=entries,
                question=question,
                requests=requests,
                essential_ids=essential_ids,
                cf_source_id=cf_source_id,
                cf_target_id=cf_target_id,
                view=view,
            )
            for view in config["views"]
        ]
        factual_answer = next(row["answer"] for row in rows if row["view"] == "full")
        counterfactual_answer = next(
            row["answer"] for row in rows if row["view"] == "cf"
        )
        if factual_answer == counterfactual_answer:
            raise P52Blocker(f"{bucket} counterfactual does not change the answer")
        for row in rows:
            row["cf_answer"] = counterfactual_answer
            signed.append(
                attach_attestation(
                    row, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
                )
            )
        pack_receipts[bucket] = {
            "artifact_count": len(entries),
            "essential_artifact_count": len(essential_ids),
            "source_record_count": len(
                {record_id for row in rows for record_id in row["source_record_ids"]}
            ),
            "context_tokens_by_view": {
                row["view"]: row["tokenizer_context_tokens"] for row in rows
            },
        }
    receipt = {
        "schema_version": "longworld.p52-govinfo-generation-receipt.v1",
        "data_stage": "candidate",
        "candidate_count": len(signed),
        "source_receipt_sha256": source_receipt_sha256,
        "candidate_row_set_sha256": _sha256_text(
            "\n".join(sorted(serialized_row_sha256(row) for row in signed))
        ),
        "packs": pack_receipts,
        "raw_xml_persisted": False,
        "padding_tokens": 0,
        "cloned_artifacts": 0,
        "split_or_truncated_sections": 0,
        "promotion_eligible": False,
        "train_ready": False,
        "production_eligible": False,
    }
    return signed, source_receipt, source_receipt_raw, receipt


def _expected_entries(
    config: Mapping[str, Any], state: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    entries = {
        entry["artifact_id"]: entry
        for entry in (
            _section_entry(
                record, workflow_id=str(config["workflow_id"]), essential=False
            )
            for record in state["records"].values()
        )
    }
    relation = _relation_entry(state, workflow_id=str(config["workflow_id"]))
    entries[relation["artifact_id"]] = relation
    return entries


def _validate_candidate(
    candidate: Mapping[str, Any],
    config: Mapping[str, Any],
    state: Mapping[str, Any],
    tokenizer: Any,
    source_receipt_sha256: str,
) -> dict[str, Any]:
    errors: list[str] = []
    bucket = str(candidate.get("length_bucket") or "")
    view = str(candidate.get("view") or "")
    if candidate.get("schema_version") != CANDIDATE_SCHEMA:
        errors.append("schema")
    if candidate.get("data_stage") != "candidate":
        errors.append("stage")
    if any(
        candidate.get(field) is not False
        for field in (
            "train_ready",
            "promotion_eligible",
            "production_eligible",
            "promoted",
        )
    ):
        errors.append("promotion_boundary")
    if bucket not in config["requested_key_count_by_bucket"]:
        errors.append("bucket")
        expected_question = ""
        expected_requests: list[dict[str, str]] = []
    else:
        expected_question, expected_requests = _question(
            config, int(config["requested_key_count_by_bucket"][bucket])
        )
    static_contract = {
        "data_product": config["data_product"],
        "world_id": config["world_id"],
        "query_id": f"{config['world_id']}:{bucket}:{view}:first",
        "domain": "government_legislation",
        "training_objective": "sft",
        "query_timing": "first",
        "question": expected_question,
        "requested_dispositions": expected_requests,
        "oracle_revision": config["oracle"]["revision"],
        "oracle_shingle_size": config["oracle"]["near_duplicate_word_shingle_size"],
        "oracle_threshold": config["oracle"]["near_duplicate_jaccard_threshold"],
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "tokenizer_model_id": config["tokenizer"]["model_id"],
        "tokenizer_revision": config["tokenizer"]["revision"],
        "tokenizer_asset_manifest_sha256": config["tokenizer"]["asset_manifest_sha256"],
    }
    if any(candidate.get(field) != value for field, value in static_contract.items()):
        errors.append("candidate_contract")
    expected_composition = {
        "full": "same_bill_lifecycle_dossier",
        "cf": "counterfactual_transition_dossier",
        "ordered_artifact_view": "authenticated_legislative_timeline",
    }.get(view)
    if (
        expected_composition is None
        or candidate.get("composition_method") != expected_composition
    ):
        errors.append("view_contract")
    expected_relations = _relations(config, expected_requests)
    if candidate.get(
        "authentic_source_relation_edges"
    ) != expected_relations or candidate.get("source_relation_ids") != [
        relation["relation_id"] for relation in expected_relations
    ]:
        errors.append("relation_contract")
    requested_keys = [request["base_key"] for request in expected_requests]
    expected_identity = {
        "base_task_id": _sha256_text(f"{config['world_id']}|bill-disposition")[:20],
        "semantic_base_task_id": _sha256_text(
            "govinfo|enr-law|source-text-disposition"
        )[:20],
        "semantic_growth_group_id": _sha256_text(f"{config['world_id']}|nested-bands")[
            :20
        ],
        "executable_proof_id": _canonical_sha256(requested_keys)[:20],
        "answer_program_id": _sha256_text(str(config["oracle"]["revision"]))[:20],
        "dossier_id": _sha256_text(f"{config['world_id']}|{bucket}|first")[:20],
    }
    if any(candidate.get(field) != value for field, value in expected_identity.items()):
        errors.append("identity_contract")
    if candidate.get("source_binding") != {
        "source_receipt_sha256": source_receipt_sha256,
        "authorization_record_id": config["authorization"]["record_id"],
        "preflight_config_sha256": config["preflight_config"]["sha256"],
    }:
        errors.append("source_binding")
    if candidate.get("context") != wrap_prompt(
        str(candidate.get("question") or ""),
        str(candidate.get("document_context") or ""),
        str(candidate.get("query_timing") or ""),
    ):
        errors.append("prompt_binding")
    observed_tokens = _token_count(tokenizer, str(candidate.get("context") or ""))
    if (
        candidate.get("tokenizer_context_tokens") != observed_tokens
        or candidate.get("actual_context_tokens") != observed_tokens
        or exact_token_band_reject_reason(bucket, observed_tokens)
    ):
        errors.append("exact_band")
    if (
        candidate.get("truncation_ppm") != 0
        or candidate.get("padding_tokens") != 0
        or candidate.get("cloned_artifacts") != 0
        or candidate.get("split_or_truncated_sections") != 0
    ):
        errors.append("padding_clone_truncation")
    try:
        documents = _artifact_documents(candidate)
    except P52Blocker:
        documents = {}
        errors.append("artifact_serialization")
    expected = _expected_entries(config, state)
    classifications = candidate.get("artifact_classification")
    classification_by_id = {
        str(item.get("artifact_id") or ""): item
        for item in classifications or []
        if isinstance(item, dict)
    }
    if len(classification_by_id) != len(documents):
        errors.append("classification_identity")
    expected_essential: list[str] = []
    if expected_requests:
        try:
            (
                _required,
                expected_essential,
                _cf_source_id,
                _cf_target_id,
            ) = _entries_for_requests(config, state, expected_requests)
        except P52Blocker:
            errors.append("essential_contract")
    if candidate.get("essential_artifact_ids") != expected_essential:
        errors.append("essential_contract")
    cf_changes = 0
    for artifact_id, document in documents.items():
        entry = expected.get(artifact_id)
        classification = classification_by_id.get(artifact_id)
        if entry is None or classification is None:
            errors.append("unknown_artifact")
            continue
        observed_document = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if classification.get("source_origin") == "synthetic_counterfactual":
            requests = candidate.get("requested_dispositions") or []
            request = next(
                (
                    item
                    for item in requests
                    if item.get("code") == config["counterfactual_code"]
                ),
                None,
            )
            if not isinstance(request, dict):
                errors.append("counterfactual_request")
                continue
            source_record = state["records"][
                (request["from_stage"], request["base_key"])
            ]
            source_entry = _section_entry(
                source_record, workflow_id=str(config["workflow_id"]), essential=True
            )
            expected_cf = _cf_entry(entry, source_entry)
            if (
                observed_document != expected_cf["document"]
                or classification != expected_cf["classification"]
            ):
                errors.append("counterfactual_binding")
            cf_changes += 1
        elif observed_document != entry["document"]:
            errors.append("source_reconstruction")
        elif any(
            classification.get(field) != entry["classification"].get(field)
            for field in (
                "workflow_id",
                "source_origin",
                "workflow_kind",
                "provenance_id",
                "source_record_id",
                "source_sha256",
                "derived_text_sha256",
                "derivation_revision",
                "whole_section",
                "truncated",
            )
        ):
            errors.append("source_classification")
        expected_role = (
            "causal_gold" if artifact_id in expected_essential else "natural_background"
        )
        if classification.get("evidence_role") != expected_role:
            errors.append("evidence_role")
    if (candidate.get("view") == "cf") != (cf_changes == 1):
        errors.append("counterfactual_cardinality")
    all_ids = list(documents)
    try:
        replay = replay_candidate(candidate, all_ids)
        expected_answer = json.dumps(replay, sort_keys=True, separators=(",", ":"))
        if candidate.get("answer") != expected_answer:
            errors.append("oracle_answer")
        essential = candidate.get("essential_artifact_ids")
        if (
            not isinstance(essential, list)
            or replay_candidate(candidate, essential) != replay
        ):
            errors.append("essential_sufficiency")
        elif any(
            replay_candidate(candidate, [item for item in essential if item != removed])
            == replay
            for removed in essential
        ):
            errors.append("essential_leave_one")
        shortcut_results = {}
        for limit in config["shortcut_windows"]:
            selected = _prefix_artifact_ids(candidate, tokenizer, int(limit))
            shortcut = replay_candidate(candidate, selected)
            shortcut_results[str(limit)] = shortcut
            if shortcut == replay:
                errors.append(f"shortcut_{limit}")
    except (P52Blocker, TypeError, ValueError) as error:
        errors.append(f"replay:{error}")
        shortcut_results = {}
    entries = [
        {
            "document": json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        }
        for value in documents.values()
    ]
    near_dup = _candidate_near_duplicate_ratio(entries)
    if near_dup > float(
        config["oracle"]["max_candidate_near_duplicate_sentence_ratio"]
    ):
        errors.append("near_duplicate")
    if candidate.get("near_dup_sentence_ratio") != round(near_dup, 4):
        errors.append("near_duplicate_receipt")
    expected_source_map = {
        artifact_id: [str(classification.get("source_record_id") or "")]
        for artifact_id, classification in classification_by_id.items()
    }
    expected_source_ids = sorted(
        {
            str(classification.get("source_record_id") or "")
            for classification in classification_by_id.values()
            if classification.get("source_origin") == "real_public"
        }
    )
    if (
        candidate.get("source_record_ids_by_artifact") != expected_source_map
        or candidate.get("source_record_ids") != expected_source_ids
    ):
        errors.append("source_record_contract")
    expected_graph = {
        "proof_depth": 2,
        "hop_count": 2,
        "n_essential_events": len(expected_essential),
    }
    if (
        candidate.get("graph") != expected_graph
        or candidate.get("strict_support_event_count") != len(expected_essential)
        or candidate.get("dependency_class") != "long_range"
    ):
        errors.append("proof_contract")
    without_real_documents = [
        documents[artifact_id]
        for artifact_id, classification in classification_by_id.items()
        if classification.get("source_origin") != "real_public"
        and artifact_id in documents
    ]
    without_real_context = SEP.join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in without_real_documents
    )
    source_tokens = observed_tokens - _token_count(
        tokenizer,
        wrap_prompt(
            str(candidate.get("question") or ""), without_real_context, "first"
        ),
    )
    ratio = source_tokens / observed_tokens if observed_tokens else 0.0
    if (
        candidate.get("real_source_marginal_tokens") != source_tokens
        or abs(float(candidate.get("real_source_token_ratio") or 0) - ratio) > 1e-12
    ):
        errors.append("source_token_receipt")
    return {
        "errors": sorted(set(errors)),
        "observed_tokens": observed_tokens,
        "near_dup_sentence_ratio": round(near_dup, 4),
        "shortcut_answers": shortcut_results,
        "full_replay_answer": expected_answer if "expected_answer" in locals() else "",
    }


def _cross_row_errors(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    by_bucket: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in candidates:
        bucket = str(row.get("length_bucket") or "")
        view = str(row.get("view") or "")
        if view in by_bucket.setdefault(bucket, {}):
            errors.append(f"duplicate_cell:{bucket}:{view}")
        by_bucket[bucket][view] = row
    expected_views = {"full", "cf", "ordered_artifact_view"}
    if set(by_bucket) != {"32k", "64k", "128k"}:
        errors.append("bucket_coverage")
    for bucket, views in by_bucket.items():
        if set(views) != expected_views:
            errors.append(f"view_coverage:{bucket}")
            continue
        full, cf, ordered = views["full"], views["cf"], views["ordered_artifact_view"]
        if full.get("question") != cf.get("question") or full.get(
            "question"
        ) != ordered.get("question"):
            errors.append(f"question_parity:{bucket}")
        ids = [
            {
                str(item.get("artifact_id") or "")
                for item in row.get("artifact_classification") or []
            }
            for row in (full, cf, ordered)
        ]
        if ids[0] != ids[1] or ids[0] != ids[2]:
            errors.append(f"view_source_set:{bucket}")
        if full.get("answer") != ordered.get("answer") or full.get("answer") == cf.get(
            "answer"
        ):
            errors.append(f"view_answer_contract:{bucket}")
        if any(row.get("cf_answer") != cf.get("answer") for row in (full, cf, ordered)):
            errors.append(f"counterfactual_label_contract:{bucket}")
        factual = json.loads(str(full.get("answer") or "{}"))
        counterfactual = json.loads(str(cf.get("answer") or "{}"))
        if (
            sum(
                factual.get(key) != counterfactual.get(key)
                for key in set(factual) | set(counterfactual)
            )
            != 1
        ):
            errors.append(f"counterfactual_answer_delta:{bucket}")
        if full.get("document_context") == ordered.get("document_context"):
            errors.append(f"ordered_view_not_distinct:{bucket}")
    for before, after in (("32k", "64k"), ("64k", "128k")):
        if before not in by_bucket or after not in by_bucket:
            continue
        for view in expected_views:
            if view not in by_bucket[before] or view not in by_bucket[after]:
                continue
            prior = by_bucket[before][view]
            later = by_bucket[after][view]
            prior_sources = set(prior.get("source_record_ids") or [])
            later_sources = set(later.get("source_record_ids") or [])
            prior_essential = set(prior.get("essential_artifact_ids") or [])
            later_essential = set(later.get("essential_artifact_ids") or [])
            prior_relations = set(prior.get("source_relation_ids") or [])
            later_relations = set(later.get("source_relation_ids") or [])
            if not prior_sources < later_sources:
                errors.append(f"source_history_not_nested:{view}:{before}->{after}")
            if not prior_essential < later_essential:
                errors.append(f"essential_history_not_nested:{view}:{before}->{after}")
            if not prior_relations < later_relations:
                errors.append(f"relation_history_not_nested:{view}:{before}->{after}")
    contexts = [str(row.get("context") or "") for row in candidates]
    if len(contexts) != len(set(contexts)):
        errors.append("duplicate_context")
    return sorted(set(errors))


def run_preflight(
    config_path: Path,
    candidates_path: Path,
    source_receipt_path: Path,
    accepted_path: Path,
    rejects_path: Path,
    report_path: Path,
) -> int:
    config, config_raw, preflight = _load_config(config_path)
    source_key = attestation_key_from_env(SOURCE_RECEIPT_PURPOSE)
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if source_key is None or candidate_key is None:
        raise P52Blocker("P52 preflight requires source and candidate role keys")
    source_raw = source_receipt_path.read_bytes()
    source_receipt = json.loads(source_raw)
    if not isinstance(source_receipt, dict) or not verify_attestation(
        source_receipt, source_key, purpose=SOURCE_RECEIPT_PURPOSE
    ):
        raise P52Blocker("P52 source receipt attestation is invalid")
    state = _verified_source_state(config, preflight)
    _expected_source, expected_source_raw = _source_receipt(
        config, config_raw, state, source_key
    )
    if source_raw != expected_source_raw:
        raise P52Blocker(
            "P52 source receipt no longer matches the config and frozen sources"
        )
    candidates = _read_jsonl(candidates_path)
    if not candidates:
        raise P52Blocker("P52 candidate input is empty")
    tokenizer = _load_tokenizer(config)
    source_sha = _sha256_bytes(source_raw)
    row_results: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        digest = candidate_sha256(dict(candidate))
        errors = []
        if not verify_attestation(
            dict(candidate), candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
        ):
            errors.append("candidate_attestation")
        result = _validate_candidate(candidate, config, state, tokenizer, source_sha)
        result["errors"] = sorted({*errors, *result["errors"]})
        row_results[digest] = result
    cross_errors = _cross_row_errors(candidates)
    all_errors = [
        error for result in row_results.values() for error in result["errors"]
    ]
    all_errors.extend(cross_errors)
    accepted = candidates if not all_errors else []
    rejects = (
        []
        if not all_errors
        else [
            {
                "schema_version": "longworld.p52-govinfo-preflight-reject.v1",
                "candidate_sha256": candidate_sha256(dict(candidate)),
                "world_id": candidate.get("world_id"),
                "query_id": candidate.get("query_id"),
                "reason": ";".join(sorted(set(all_errors))),
            }
            for candidate in candidates
        ]
    )
    report = {
        "schema_version": "longworld.p52-govinfo-preflight-report.v1",
        "candidate_count": len(candidates),
        "accepted_count": len(accepted),
        "rejected_count": len(rejects),
        "world_atomic": True,
        "row_results": row_results,
        "cross_row_errors": cross_errors,
        "errors": sorted(set(all_errors)),
        "promotion_eligible": False,
        "train_ready": False,
    }
    _write_jsonl(accepted_path, accepted)
    _write_jsonl(rejects_path, rejects)
    _write_atomic(report_path, _canonical_bytes(report))
    return len(accepted)


def run_audit(
    config_path: Path,
    candidates_path: Path,
    source_receipt_path: Path,
    rankings_path: Path,
    audits_path: Path,
    report_path: Path,
) -> int:
    config, config_raw, preflight = _load_config(config_path)
    source_key = attestation_key_from_env(SOURCE_RECEIPT_PURPOSE)
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    ranking_key = attestation_key_from_env(DENSE_RANKING_PURPOSE)
    audit_key = attestation_key_from_env(DENSE_AUDIT_PURPOSE)
    if None in (source_key, candidate_key, ranking_key, audit_key):
        raise P52Blocker(
            "P52 audit requires source, candidate, ranker, and auditor keys"
        )
    assert source_key is not None and candidate_key is not None
    assert ranking_key is not None and audit_key is not None
    source_raw = source_receipt_path.read_bytes()
    source_receipt = json.loads(source_raw)
    if not isinstance(source_receipt, dict) or not verify_attestation(
        source_receipt, source_key, purpose=SOURCE_RECEIPT_PURPOSE
    ):
        raise P52Blocker("P52 audit source receipt is invalid")
    state = _verified_source_state(config, preflight)
    _expected_source, expected_source_raw = _source_receipt(
        config, config_raw, state, source_key
    )
    if source_raw != expected_source_raw:
        raise P52Blocker("P52 audit source receipt or source bytes changed")
    candidates = _read_jsonl(candidates_path)
    rankings = _read_jsonl(rankings_path)
    ranking_by_candidate = {
        str(row.get("candidate_sha256") or ""): row for row in rankings
    }
    if len(ranking_by_candidate) != len(rankings) or len(rankings) != len(candidates):
        raise P52Blocker("P52 ranking coverage is incomplete or duplicated")
    tokenizer = _load_tokenizer(config)
    source_sha = _sha256_bytes(source_raw)
    audits: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        digest = candidate_sha256(row)
        if not verify_attestation(
            row, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
        ):
            raise P52Blocker("P52 audit candidate attestation is invalid")
        result = _validate_candidate(row, config, state, tokenizer, source_sha)
        if result["errors"]:
            raise P52Blocker(
                f"P52 full-pool replay failed for {row.get('query_id')}: {result['errors']}"
            )
        ranking = ranking_by_candidate.get(digest)
        if ranking is None:
            raise P52Blocker("P52 audit ranking candidate binding is missing")
        try:
            ranked, model = _validate_external_ranking(row, ranking, ranking_key)
        except PromotionError as error:
            raise P52Blocker(f"P52 dense ranking is invalid: {error}") from error
        k = int(config["dense_model"]["top_k"])
        top_k = ranked[:k]
        strict = replay_candidate(row, [item["artifact_id"] for item in top_k])
        strict_answer = json.dumps(strict, sort_keys=True, separators=(",", ":"))
        if strict_answer == row["answer"]:
            raise P52Blocker(f"dense top-{k} shortcut answered {row.get('query_id')}")
        audit = attach_attestation(
            {
                "schema_version": AUDIT_SCHEMA,
                "data_stage": "candidate_audited",
                "candidate_sha256": digest,
                "query_id": row["query_id"],
                "ranking_sha256": serialized_row_sha256(ranking),
                "ranker_type": "dense_embedding",
                "model": model,
                "k": k,
                "top_k": top_k,
                "embedding_topk_insufficient": True,
                "strict_replay_revision": STRICT_REPLAY_REVISION,
                "strict_replay_answer": strict_answer,
                "expected_answer": row["answer"],
                "full_pool_strict_replay_sufficient": True,
                "essential_leave_one_passed": True,
                "counterfactual_replay_passed": True,
                "shortcut_windows_insufficient": True,
                "near_dup_sentence_ratio": result["near_dup_sentence_ratio"],
                "tokenizer_asset_manifest_sha256": row[
                    "tokenizer_asset_manifest_sha256"
                ],
                "source_receipt_sha256": source_sha,
                "source_relation_ids": row["source_relation_ids"],
                "verification_replay_sha256": _canonical_sha256(result),
                "promotion_eligible": False,
                "train_ready": False,
                "production_eligible": False,
            },
            audit_key,
            purpose=DENSE_AUDIT_PURPOSE,
        )
        audits.append(audit)
    report = {
        "schema_version": "longworld.p52-govinfo-dense-audit-report.v1",
        "candidate_count": len(candidates),
        "audit_count": len(audits),
        "all_dense_top3_insufficient": len(audits) == len(candidates),
        "candidate_row_set_sha256": _sha256_text(
            "\n".join(sorted(serialized_row_sha256(row) for row in candidates))
        ),
        "audit_row_set_sha256": _sha256_text(
            "\n".join(sorted(serialized_row_sha256(row) for row in audits))
        ),
        "promotion_eligible": False,
        "train_ready": False,
        "production_eligible": False,
        "errors": [],
    }
    _write_jsonl(audits_path, audits)
    _write_atomic(report_path, _canonical_bytes(report))
    return len(audits)


def _write_generation_output(
    output_dir: Path,
    candidates: Sequence[Mapping[str, Any]],
    source_raw: bytes,
    receipt: Mapping[str, Any],
) -> None:
    if output_dir.exists():
        raise P52Blocker(f"P52 output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        _write_jsonl(staging / "candidates.jsonl", candidates)
        _write_atomic(staging / "SOURCE_RECEIPT.json", source_raw)
        _write_atomic(staging / "GENERATION_RECEIPT.json", _canonical_bytes(receipt))
        os.replace(staging, output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _write_blocker(output_dir: Path, error: Exception) -> None:
    if output_dir.exists():
        raise P52Blocker(f"refusing to replace existing P52 output: {output_dir}")
    output_dir.mkdir(parents=True)
    _write_atomic(output_dir / "candidates.jsonl", b"")
    _write_atomic(
        output_dir / "BLOCKER.json",
        _canonical_bytes(
            {
                "schema_version": "longworld.p52-govinfo-candidate-blocker.v1",
                "candidate_count": 0,
                "inventory_delta": 0,
                "train_ready": False,
                "promotion_eligible": False,
                "reason": str(error),
            }
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--config", type=Path, required=True)
    generate.add_argument("--output-dir", type=Path, required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--config", type=Path, required=True)
    preflight.add_argument("--candidates", type=Path, required=True)
    preflight.add_argument("--source-receipt", type=Path, required=True)
    preflight.add_argument("--accepted", type=Path, required=True)
    preflight.add_argument("--rejects", type=Path, required=True)
    preflight.add_argument("--report", type=Path, required=True)
    audit = subparsers.add_parser("audit")
    audit.add_argument("--config", type=Path, required=True)
    audit.add_argument("--candidates", type=Path, required=True)
    audit.add_argument("--source-receipt", type=Path, required=True)
    audit.add_argument("--rankings", type=Path, required=True)
    audit.add_argument("--audits", type=Path, required=True)
    audit.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "generate":
            candidates, _source, source_raw, receipt = build_candidates(args.config)
            _write_generation_output(args.output_dir, candidates, source_raw, receipt)
            print(
                json.dumps(
                    {
                        "command": "generate",
                        "rows": len(candidates),
                        "output": str(args.output_dir),
                    }
                )
            )
        elif args.command == "preflight":
            count = run_preflight(
                args.config,
                args.candidates,
                args.source_receipt,
                args.accepted,
                args.rejects,
                args.report,
            )
            print(
                json.dumps(
                    {
                        "command": "preflight",
                        "rows": count,
                        "output": str(args.accepted),
                    }
                )
            )
            if count == 0:
                raise SystemExit(2)
        else:
            count = run_audit(
                args.config,
                args.candidates,
                args.source_receipt,
                args.rankings,
                args.audits,
                args.report,
            )
            print(
                json.dumps(
                    {"command": "audit", "rows": count, "output": str(args.audits)}
                )
            )
    except P52Blocker as error:
        if args.command == "generate":
            _write_blocker(args.output_dir, error)
        raise SystemExit(f"P52 blocked: {error}") from error


if __name__ == "__main__":
    main()
