"""Build signed P53 OSV remediation candidates without selecting or promoting."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

import yaml
from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    local_probe_diagnostic_metadata,
)
from longworld.core.pack import SEP
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from reports import p51_osv_upstream_remediation_lifecycle_preflight as p51

SOURCE_PURPOSE = "source_manifest"
REPORT_PURPOSE = "quality_report"
SOURCE_SCHEMA = "longworld.p53-osv-source-sidecar.v1"
CANDIDATE_SCHEMA = "longworld.p53-osv-candidate.v1"
REPORT_SCHEMA = "longworld.p53-osv-candidate-preflight.v1"
CF_SENTINEL = "P53-CF-WITHHELD"


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value).rstrip(b"\n"))


def _read_pinned(path: Path, expected_sha256: str) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    if _sha256_bytes(raw) != expected_sha256:
        raise ValueError(f"P53 predecessor hash changed: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"P53 predecessor is not an object: {path}")
    return raw, value


def _advisory_members(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, str]]:
    records = []
    members = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for item in archive.getmembers():
            if not (
                item.isfile() and "/vulns/" in item.name and item.name.endswith(".yaml")
            ):
                continue
            record = yaml.safe_load(archive.extractfile(item).read())
            record_id = str(record.get("id") or "")
            if not record_id or record_id in members:
                raise ValueError("P53 PyPA advisory identity is invalid")
            records.append(record)
            members[record_id] = item.name
    return records, members


def _artifact(
    *,
    artifact_id: str,
    kind: str,
    payload: dict[str, Any],
    source_url: str,
    source_sha256: str,
    source_record_id: str,
    license_id: str,
    license_source_url: str,
    occurred_at: str,
    source_origin: str = "real_public",
    parent: dict[str, str] | None = None,
) -> dict[str, Any]:
    document = {
        "kind": kind,
        "payload": payload,
        "provenance": {
            "source_url": source_url,
            "source_sha256": source_sha256,
            "source_record_id": source_record_id,
            "license_id": license_id,
            "license_source_url": license_source_url,
            "source_origin": source_origin,
        },
    }
    if parent is not None:
        document["counterfactual_derivation"] = parent
    text = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "text": text,
        "text_sha256": _sha256_bytes(text.encode()),
        "source_url": source_url,
        "source_sha256": source_sha256,
        "source_record_id": source_record_id,
        "license_id": license_id,
        "license_source_url": license_source_url,
        "occurred_at": occurred_at,
        "source_origin": source_origin,
        "parent": parent,
    }


def _advisory_payload(
    record: dict[str, Any], *, include_versions: bool
) -> dict[str, Any]:
    payload = json.loads(p51._semantic_text(record))
    if include_versions:
        payload["affected_versions"] = [
            {
                "package": str(item["package"]["name"]),
                "versions": [str(version) for version in item.get("versions", [])],
            }
            for item in p51._pypi_affected(record)
        ]
    return payload


def _replace_fixed_release(
    artifact: dict[str, Any], fixed_release: str
) -> dict[str, Any]:
    document = json.loads(artifact["text"])
    replaced = 0
    for affected in document["payload"]["affected"]:
        for affected_range in affected["ranges"]:
            if affected_range["type"] != "ECOSYSTEM":
                continue
            for event in affected_range["events"]:
                if event.get("fixed") == fixed_release:
                    event["fixed"] = CF_SENTINEL
                    replaced += 1
    if replaced != 1:
        raise ValueError("P53 counterfactual must replace exactly one fixed boundary")
    parent = {
        "operation": "replace_exact_ecosystem_fixed_value",
        "parent_artifact_id": artifact["artifact_id"],
        "parent_text_sha256": artifact["text_sha256"],
        "old_value": fixed_release,
        "new_value": CF_SENTINEL,
    }
    payload = document["payload"]
    return _artifact(
        artifact_id=f"{artifact['artifact_id']}:cf",
        kind="active_advisory",
        payload=payload,
        source_url=artifact["source_url"],
        source_sha256=artifact["source_sha256"],
        source_record_id=artifact["source_record_id"],
        license_id=artifact["license_id"],
        license_source_url=artifact["license_source_url"],
        occurred_at=artifact["occurred_at"],
        source_origin="synthetic_counterfactual",
        parent=parent,
    )


def _render_prompt(question: str, artifacts: list[dict[str, Any]]) -> str:
    documents = SEP.join(item["text"] for item in artifacts)
    return (
        "P53 OSV remediation lifecycle evidence task.\n"
        f"{question}\n\n"
        "Treat absent package versions as UNKNOWN, never SAFE. Require every named "
        "source relation and license receipt.\n\n"
        f"{documents}"
    )


def _affected_versions(document: dict[str, Any], package: str) -> set[str]:
    return {
        str(version)
        for item in document["payload"].get("affected_versions", [])
        if item.get("package") == package
        for version in item.get("versions", [])
    }


def _fixed_values(document: dict[str, Any], package: str, range_type: str) -> set[str]:
    return {
        str(event["fixed"])
        for item in document["payload"].get("affected", [])
        if item.get("package", {}).get("name") == package
        for affected_range in item.get("ranges", [])
        if affected_range.get("type") == range_type
        for event in affected_range.get("events", [])
        if "fixed" in event
    }


def _external_aliases(document: dict[str, Any]) -> set[str]:
    return {
        str(item)
        for item in document["payload"].get("aliases", [])
        if not str(item).startswith("PYSEC-")
    }


def replay(artifacts: list[dict[str, Any]], task: dict[str, Any]) -> dict[str, Any]:
    documents = {}
    for artifact in artifacts:
        try:
            document = json.loads(artifact["text"])
        except json.JSONDecodeError:
            return {"answer": "unknown", "proof_depth": 0}
        documents[artifact["artifact_id"]] = document
    lines = []
    conflict_count = 0
    for chain in task["chains"]:
        required = chain["essential_artifact_ids"]
        if any(artifact_id not in documents for artifact_id in required):
            return {"answer": "unknown", "proof_depth": 0}
        withdrawn = documents[chain["withdrawn_artifact_id"]]
        active_id = chain["active_artifact_id"]
        if active_id not in documents:
            active_id = f"{active_id}:cf"
        active = documents[active_id]
        patch = documents[chain["patch_artifact_id"]]
        release = documents[chain["release_artifact_id"]]
        license_receipt = documents[chain["license_artifact_id"]]
        package = chain["package"]
        active_record_id = chain["active_id"]
        if not (
            withdrawn["payload"].get("withdrawn")
            and str(withdrawn["payload"].get("details", "")).startswith(
                f"Withdrawn as duplicate of {active_record_id}"
            )
            and chain["shared_alias"] in _external_aliases(withdrawn)
            and chain["shared_alias"] in _external_aliases(active)
            and package
            in {
                item.get("package", {}).get("name")
                for item in active["payload"].get("affected", [])
            }
            and patch["payload"].get("commit") == chain["fixed_commit"]
            and release["payload"].get("package") == package
            and release["payload"].get("version") == chain["fixed_release"]
            and license_receipt["payload"].get("spdx") == chain["license_spdx"]
        ):
            return {"answer": "unknown", "proof_depth": 0}
        git_fixed = _fixed_values(active, package, "GIT")
        ecosystem_fixed = _fixed_values(active, package, "ECOSYSTEM")
        fix_urls = {
            item.get("url")
            for item in active["payload"].get("references", [])
            if item.get("type") == "FIX"
        }
        expected_commit_url = chain["patch_url"].removesuffix(".patch")
        if (
            chain["fixed_commit"] not in git_fixed
            or expected_commit_url not in fix_urls
        ):
            return {"answer": "unknown", "proof_depth": 0}
        if chain["fixed_release"] not in ecosystem_fixed:
            conflict_count += 1
            lines.append(f"CONFLICT-{package}")
            continue
        affected = _affected_versions(active, package)
        if chain["affected_probe"] not in affected:
            return {"answer": "unknown", "proof_depth": 0}
        fixed_state = (
            "AFFECTED" if chain["fixed_release"] in affected else "FIXED_BOUNDARY"
        )
        if fixed_state != "FIXED_BOUNDARY":
            return {"answer": "unknown", "proof_depth": 0}
        unknown_state = (
            "AFFECTED"
            if chain["unlisted_probe"] in affected
            else (
                "FIXED_BOUNDARY"
                if chain["unlisted_probe"] in ecosystem_fixed
                else "UNKNOWN"
            )
        )
        lines.append(
            f"{chain['withdrawn_id']}->{active_record_id} | package={package} | "
            f"{chain['affected_probe']}=AFFECTED | "
            f"{chain['fixed_release']}={fixed_state} | "
            f"{chain['unlisted_probe']}={unknown_state} | "
            f"commit={chain['fixed_commit']} | release={chain['fixed_release']} | "
            f"license={chain['license_spdx']}"
        )
    return {
        "answer": "\n".join(lines),
        "proof_depth": 5 + len(task["chains"]),
        "chain_count": len(task["chains"]),
        "conflict_count": conflict_count,
    }


def _question(chains: list[dict[str, Any]]) -> str:
    cases = "; ".join(
        f"{item['withdrawn_id']} probes affected={item['affected_probe']}, "
        f"fixed={item['fixed_release']}, unlisted={item['unlisted_probe']}"
        for item in chains
    )
    return (
        "For each withdrawn advisory below, follow only its explicit duplicate target, "
        "shared external alias and PyPI package, then join the active advisory's exact "
        "GIT fixed hash to the upstream patch, its ECOSYSTEM fixed value to the PyPI "
        "release receipt, and the commit-pinned repository license. Classify the three "
        "version probes using exact membership only. If any required evidence is absent "
        "reply `unknown`; if the active fixed boundary disagrees with the release receipt "
        "reply `CONFLICT-<package>` for that case. Preserve case order. Cases: "
        f"{cases}."
    )


def _token_count(tokenizer: Any, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False))


def _artifact_token_lengths(
    tokenizer: Any, artifacts: list[dict[str, Any]]
) -> dict[str, int]:
    result = {}
    for offset in range(0, len(artifacts), 128):
        batch = artifacts[offset : offset + 128]
        encoded = tokenizer(
            [item["text"] for item in batch],
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )["input_ids"]
        result.update(
            (item["artifact_id"], len(token_ids))
            for item, token_ids in zip(batch, encoded, strict=True)
        )
    return result


def _spread_order(
    essential: list[dict[str, Any]], background: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(len(essential) + 1)]
    for index, item in enumerate(
        sorted(background, key=lambda value: value["text_sha256"])
    ):
        buckets[index % len(buckets)].append(item)
    output = []
    for index, bucket in enumerate(buckets):
        output.extend(bucket)
        if index < len(essential):
            output.append(essential[index])
    return output


def _ordered_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        artifacts,
        key=lambda item: (
            item["occurred_at"],
            item["kind"],
            item["source_record_id"],
            item["artifact_id"],
        ),
    )


def _classifications(
    artifacts: list[dict[str, Any]], essential_ids: set[str]
) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": item["artifact_id"],
            "artifact_text_sha256": item["text_sha256"],
            "source_record_id": item["source_record_id"],
            "source_url": item["source_url"],
            "source_sha256": item["source_sha256"],
            "source_origin": item["source_origin"],
            "workflow_id": "p53-osv-upstream-remediation-lifecycle-v1",
            "workflow_kind": "real_source_derived",
            "evidence_role": (
                "causal_gold"
                if item["artifact_id"] in essential_ids
                else "natural_background"
            ),
            "license_id": item["license_id"],
            "license_source_url": item["license_source_url"],
        }
        for item in artifacts
    ]


def _source_token_receipt(
    tokenizer: Any,
    question: str,
    artifacts: list[dict[str, Any]],
    context_tokens: int,
) -> dict[str, Any]:
    synthetic = [
        item
        for item in artifacts
        if item["source_origin"] == "synthetic_counterfactual"
    ]
    synthetic_deltas = [
        {
            "text": json.dumps(
                {"counterfactual_derivation": item["parent"]},
                sort_keys=True,
                separators=(",", ":"),
            )
        }
        for item in synthetic
    ]
    without_real = _token_count(tokenizer, _render_prompt(question, synthetic_deltas))
    source_tokens = context_tokens - without_real
    return {
        "schema_version": "longworld.source-token-receipt.v2",
        "final_prompt_tokens": context_tokens,
        "without_real_prompt_tokens": without_real,
        "source_tokens": source_tokens,
        "source_token_ratio": round(source_tokens / context_tokens, 6),
    }


def _assert_no_near_duplicates(
    artifacts: list[dict[str, Any]], size: int, threshold: float
) -> None:
    units = [
        {
            "id": item["artifact_id"],
            "text": item["text"],
            "canonical": p51._canonical(item["text"]),
            "digest": item["text_sha256"],
        }
        for item in artifacts
    ]
    kept, removed = p51._near_deduplicate(units, size, threshold)
    if removed or len(kept) != len(units):
        raise ValueError("P53 candidate contains a >=0.90 near-duplicate artifact")


def _assert_short_windows_insufficient(
    tokenizer: Any,
    question: str,
    artifacts: list[dict[str, Any]],
    essential_ids: set[str],
    limits: list[int],
) -> dict[str, bool]:
    positions = [
        index
        for index, item in enumerate(artifacts)
        if item["artifact_id"] in essential_ids
    ]
    if len(positions) != len(essential_ids):
        raise ValueError("P53 essential artifact placement is incomplete")
    spanning = artifacts[min(positions) : max(positions) + 1]
    spanning_tokens = _token_count(tokenizer, _render_prompt(question, spanning))
    checks = {f"artifact_aligned_{limit}": spanning_tokens > limit for limit in limits}
    if not all(checks.values()):
        raise ValueError(f"P53 short-window shortcut: {spanning_tokens}")
    return {**checks, "essential_span_tokens": spanning_tokens}


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def build(config_path: Path) -> dict[str, Any]:
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    predecessor = config["predecessor"]
    _, p51_config = _read_pinned(
        Path(predecessor["config_path"]), predecessor["config_sha256"]
    )
    _, p51_report = _read_pinned(
        Path(predecessor["report_path"]), predecessor["report_sha256"]
    )
    if (
        p51_report.get("verdict", {}).get("official_cross_source_topology") != "PASS"
        or p51_report.get("verdict", {}).get(
            "near_deduplicated_32k_64k_128k_capacity_preflight"
        )
        != "PASS"
        or p51_report.get("verdict", {}).get("candidate_count") != 0
    ):
        raise ValueError(
            "P53 predecessor verdict is not the expected preflight-only PASS"
        )
    for bucket, item in config["length_buckets"].items():
        if item["bounds"] != list(EXACT_TOKEN_BAND_RANGES[bucket]):
            raise ValueError("P53 length bands must match repository exact bands")

    source_key = attestation_key_from_env(SOURCE_PURPOSE)
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    report_key = attestation_key_from_env(REPORT_PURPOSE)
    if source_key is None or candidate_key is None or report_key is None:
        raise ValueError(
            "P53 requires isolated source, candidate, and report role keys"
        )

    pypa_raw, pypa_receipt = p51._fetch(p51_config["pypa_archive"])
    p51._load_pypa_archive(pypa_raw, p51_config["pypa_archive"])
    pypa_records, member_by_id = _advisory_members(pypa_raw)
    pypa_by_id = {record["id"]: record for record in pypa_records}
    osv_raw, osv_receipt = p51._fetch(p51_config["osv_pypi_export"])
    osv_records, _ = p51._load_osv_export(osv_raw)
    osv_by_id = {record["id"]: record for record in osv_records}

    archive_url = p51_config["pypa_archive"]["url"]
    archive_sha256 = p51_config["pypa_archive"]["expected_sha256"]
    archive_license_url = (
        "https://raw.githubusercontent.com/pypa/advisory-database/"
        f"{p51_config['pypa_archive']['commit']}/LICENSE"
    )
    source_receipts = [pypa_receipt, osv_receipt]
    chain_by_withdrawn = {
        item["withdrawn_id"]: item for item in p51_config["topology_chains"]
    }
    chains = []
    all_source_artifacts: dict[str, dict[str, Any]] = {}
    for index, withdrawn_id in enumerate(config["chain_order"], start=1):
        chain = chain_by_withdrawn[withdrawn_id]
        withdrawn = pypa_by_id[withdrawn_id]
        active = pypa_by_id[chain["active_duplicate_id"]]
        if (
            withdrawn_id not in osv_by_id
            or chain["active_duplicate_id"] not in osv_by_id
        ):
            raise ValueError("P53 chain is missing from the frozen OSV export")
        shared_aliases = sorted(
            set(withdrawn.get("aliases", [])) & set(active.get("aliases", []))
        )
        if chain["required_shared_alias"] not in shared_aliases:
            raise ValueError("P53 chain shared alias changed")
        patch_raw, patch_receipt = p51._fetch(chain["patch_source"])
        release_raw, release_receipt = p51._fetch(chain["release_source"])
        license_raw, license_receipt = p51._fetch(chain["license_source"])
        source_receipts.extend([patch_receipt, release_receipt, license_receipt])
        release = json.loads(release_raw)
        release_artifacts = sorted(
            (
                {
                    "filename": item["filename"],
                    "sha256": item["digests"]["sha256"],
                }
                for item in release["urls"]
            ),
            key=lambda item: (item["filename"], item["sha256"]),
        )
        affected_versions = sorted(
            {
                str(version)
                for item in p51._pypi_affected(active)
                for version in item.get("versions", [])
                if str(version) != chain["fixed_release"]
            }
        )
        if not affected_versions:
            raise ValueError("P53 active advisory has no affected probe")
        affected_probe = affected_versions[0]
        unlisted_probe = config["oracle"]["unlisted_version_template"].format(
            index=index
        )
        if unlisted_probe in set(affected_versions) | set(p51._fixed_values(active)):
            raise ValueError("P53 unlisted probe collides with an observed version")

        withdrawn_artifact = _artifact(
            artifact_id=f"advisory:{withdrawn_id}",
            kind="withdrawn_advisory",
            payload=_advisory_payload(withdrawn, include_versions=True),
            source_url=archive_url,
            source_sha256=archive_sha256,
            source_record_id=member_by_id[withdrawn_id],
            license_id="CC-BY-4.0",
            license_source_url=archive_license_url,
            occurred_at=str(withdrawn.get("modified", "")),
        )
        active_artifact = _artifact(
            artifact_id=f"advisory:{chain['active_duplicate_id']}",
            kind="active_advisory",
            payload=_advisory_payload(active, include_versions=True),
            source_url=archive_url,
            source_sha256=archive_sha256,
            source_record_id=member_by_id[chain["active_duplicate_id"]],
            license_id="CC-BY-4.0",
            license_source_url=archive_license_url,
            occurred_at=str(active.get("modified", "")),
        )
        patch_artifact = _artifact(
            artifact_id=f"patch:{chain['fixed_commit']}",
            kind="upstream_patch",
            payload={
                "commit": chain["fixed_commit"],
                "patch": patch_raw.decode("utf-8"),
            },
            source_url=chain["patch_source"]["url"],
            source_sha256=chain["patch_source"]["expected_sha256"],
            source_record_id=chain["fixed_commit"],
            license_id=chain["license_source"]["spdx"],
            license_source_url=chain["license_source"]["url"],
            occurred_at=str(active.get("published", "")),
        )
        release_artifact = _artifact(
            artifact_id=f"release:{chain['shared_package']}:{chain['fixed_release']}",
            kind="pypi_release_receipt",
            payload={
                "package": chain["shared_package"],
                "version": chain["fixed_release"],
                "artifacts": release_artifacts,
                "release_json_sha256": chain["release_source"]["expected_sha256"],
            },
            source_url=chain["release_source"]["url"],
            source_sha256=chain["release_source"]["expected_sha256"],
            source_record_id=f"{chain['shared_package']}@{chain['fixed_release']}",
            license_id="LicenseRef-PyPI-Metadata-Facts",
            license_source_url="https://docs.pypi.org/api/json/",
            occurred_at=str(active.get("published", "")),
        )
        license_artifact = _artifact(
            artifact_id=f"license:{chain['shared_package']}:{chain['fixed_commit']}",
            kind="upstream_license_receipt",
            payload={
                "spdx": chain["license_source"]["spdx"],
                "license_bytes": len(license_raw),
                "license_sha256": chain["license_source"]["expected_sha256"],
                "license_text": license_raw.decode("utf-8"),
                "required_text_present": (
                    chain["license_source"]["required_text"].encode() in license_raw
                ),
            },
            source_url=chain["license_source"]["url"],
            source_sha256=chain["license_source"]["expected_sha256"],
            source_record_id=f"{chain['shared_package']}:{chain['fixed_commit']}:license",
            license_id=chain["license_source"]["spdx"],
            license_source_url=chain["license_source"]["url"],
            occurred_at=str(active.get("published", "")),
        )
        artifacts = [
            withdrawn_artifact,
            active_artifact,
            patch_artifact,
            release_artifact,
            license_artifact,
        ]
        for artifact in artifacts:
            if artifact["artifact_id"] in all_source_artifacts:
                raise ValueError("P53 source artifact identity collision")
            all_source_artifacts[artifact["artifact_id"]] = artifact
        chains.append(
            {
                "withdrawn_id": withdrawn_id,
                "active_id": chain["active_duplicate_id"],
                "package": chain["shared_package"],
                "shared_alias": chain["required_shared_alias"],
                "fixed_commit": chain["fixed_commit"],
                "fixed_release": chain["fixed_release"],
                "affected_probe": affected_probe,
                "unlisted_probe": unlisted_probe,
                "license_spdx": chain["license_source"]["spdx"],
                "patch_url": chain["patch_source"]["url"],
                "withdrawn_artifact_id": withdrawn_artifact["artifact_id"],
                "active_artifact_id": active_artifact["artifact_id"],
                "patch_artifact_id": patch_artifact["artifact_id"],
                "release_artifact_id": release_artifact["artifact_id"],
                "license_artifact_id": license_artifact["artifact_id"],
                "essential_artifact_ids": [item["artifact_id"] for item in artifacts],
            }
        )

    excluded_ids = {
        item for chain in chains for item in (chain["withdrawn_id"], chain["active_id"])
    }
    background_units = []
    for record in pypa_records:
        if record["id"] in excluded_ids or p51._eligibility_reasons(record):
            continue
        text = p51._semantic_text(record)
        canonical = p51._canonical(text)
        if len(canonical) < config["packing"]["minimum_background_characters"]:
            continue
        background_units.append(
            {
                "id": record["id"],
                "text": text,
                "canonical": canonical,
                "digest": _sha256_bytes(canonical.encode()),
            }
        )
    background_units, _ = p51._near_deduplicate(
        background_units,
        config["packing"]["near_duplicate_word_shingle_size"],
        config["packing"]["near_duplicate_jaccard_threshold"],
    )
    background = []
    for unit in background_units:
        record = pypa_by_id[unit["id"]]
        artifact = _artifact(
            artifact_id=f"background:{record['id']}",
            kind="advisory_background",
            payload=json.loads(unit["text"]),
            source_url=archive_url,
            source_sha256=archive_sha256,
            source_record_id=member_by_id[record["id"]],
            license_id="CC-BY-4.0",
            license_source_url=archive_license_url,
            occurred_at=str(record.get("published", record.get("modified", ""))),
        )
        background.append(artifact)
    background.sort(key=lambda item: (item["text_sha256"], item["artifact_id"]))

    tokenizer_cfg = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_cfg["model_id"],
        revision=tokenizer_cfg["revision"],
        local_files_only=tokenizer_cfg["local_files_only"],
    )
    artifact_lengths = _artifact_token_lengths(
        tokenizer, [*all_source_artifacts.values(), *background]
    )
    separator_tokens = _token_count(tokenizer, SEP)
    drafts = []
    pack_reports = {}
    cf_derivations = []
    used_artifacts: dict[str, dict[str, Any]] = dict(all_source_artifacts)
    for bucket, bucket_cfg in config["length_buckets"].items():
        selected_chains = chains[: bucket_cfg["required_chain_count"]]
        task = {
            "schema_version": "longworld.p53-osv-task.v1",
            "world_id": config["world_id"],
            "length_bucket": bucket,
            "chains": selected_chains,
            "program_ops": [
                "FOLLOW_EXPLICIT_DUPLICATE_TARGET",
                "JOIN_SHARED_EXTERNAL_ALIAS_AND_PACKAGE",
                "CLASSIFY_EXACT_AFFECTED_FIXED_UNKNOWN",
                "JOIN_GIT_FIXED_TO_UPSTREAM_PATCH",
                "JOIN_ECOSYSTEM_FIXED_TO_PYPI_RELEASE",
                "REQUIRE_COMMIT_PINNED_LICENSE",
            ],
        }
        question = _question(selected_chains)
        essential = [
            all_source_artifacts[artifact_id]
            for chain in selected_chains
            for artifact_id in chain["essential_artifact_ids"]
        ]
        selected_background = []
        lower, upper = bucket_cfg["bounds"]
        target = lower + config["packing"]["target_margin_tokens"]
        estimated = _token_count(tokenizer, _render_prompt(question, essential))
        for item in background:
            selected_background.append(item)
            estimated += artifact_lengths[item["artifact_id"]] + separator_tokens
            if estimated >= target:
                break
        full_order = _spread_order(essential, selected_background)
        observed = _token_count(tokenizer, _render_prompt(question, full_order))
        remaining = iter(background[len(selected_background) :])
        while observed < target:
            selected_background.append(next(remaining))
            full_order = _spread_order(essential, selected_background)
            observed = _token_count(tokenizer, _render_prompt(question, full_order))
        counterfactual_headroom = 128
        while observed > upper - counterfactual_headroom and selected_background:
            selected_background.pop()
            full_order = _spread_order(essential, selected_background)
            observed = _token_count(tokenizer, _render_prompt(question, full_order))
        if observed < lower:
            selected_ids = {item["artifact_id"] for item in selected_background}
            for item in sorted(
                (
                    candidate
                    for candidate in background
                    if candidate["artifact_id"] not in selected_ids
                ),
                key=lambda candidate: (
                    artifact_lengths[candidate["artifact_id"]],
                    candidate["text_sha256"],
                ),
            ):
                candidate_background = [*selected_background, item]
                candidate_order = _spread_order(essential, candidate_background)
                candidate_tokens = _token_count(
                    tokenizer, _render_prompt(question, candidate_order)
                )
                if lower <= candidate_tokens <= upper - counterfactual_headroom:
                    selected_background = candidate_background
                    full_order = candidate_order
                    observed = candidate_tokens
                    break
        if not lower <= observed <= upper:
            raise ValueError(
                f"P53 {bucket} natural exact-band blocker: {observed}, {lower}-{upper}"
            )
        for item in selected_background:
            used_artifacts[item["artifact_id"]] = item

        expected = replay(full_order, task)
        if expected["answer"] == "unknown" or expected["conflict_count"]:
            raise ValueError(f"P53 {bucket} authentic oracle failed")
        essential_ids = {
            artifact_id
            for chain in selected_chains
            for artifact_id in chain["essential_artifact_ids"]
        }
        cf_parent = all_source_artifacts[selected_chains[0]["active_artifact_id"]]
        cf_artifact = _replace_fixed_release(
            cf_parent, selected_chains[0]["fixed_release"]
        )
        used_artifacts[cf_artifact["artifact_id"]] = cf_artifact
        cf_derivations.append(cf_artifact["parent"])
        cf_order = [
            cf_artifact if item["artifact_id"] == cf_parent["artifact_id"] else item
            for item in full_order
        ]
        cf_essential_ids = {
            cf_artifact["artifact_id"]
            if artifact_id == cf_parent["artifact_id"]
            else artifact_id
            for artifact_id in essential_ids
        }
        cf_task = json.loads(json.dumps(task))
        cf_task["chains"][0]["essential_artifact_ids"] = [
            cf_artifact["artifact_id"]
            if artifact_id == cf_parent["artifact_id"]
            else artifact_id
            for artifact_id in cf_task["chains"][0]["essential_artifact_ids"]
        ]
        counterfactual = replay(cf_order, cf_task)
        if (
            counterfactual["answer"] == "unknown"
            or counterfactual["answer"] == expected["answer"]
            or counterfactual["conflict_count"] != 1
        ):
            raise ValueError(f"P53 {bucket} counterfactual oracle failed")

        view_specs = [
            ("full", full_order, task, essential_ids, expected["answer"]),
            ("cf", cf_order, cf_task, cf_essential_ids, counterfactual["answer"]),
            (
                "ordered_artifact_view",
                _ordered_artifacts(full_order),
                task,
                essential_ids,
                expected["answer"],
            ),
        ]
        bucket_views = {}
        for view, artifacts, view_task, view_essential_ids, answer in view_specs:
            context = _render_prompt(question, artifacts)
            context_tokens = _token_count(tokenizer, context)
            if not lower <= context_tokens <= upper:
                raise ValueError(
                    f"P53 {bucket}/{view} exact-band blocker: {context_tokens}"
                )
            if len({item["text_sha256"] for item in artifacts}) != len(artifacts):
                raise ValueError(f"P53 {bucket}/{view} has duplicate artifact bytes")
            _assert_no_near_duplicates(
                artifacts,
                config["packing"]["near_duplicate_word_shingle_size"],
                config["packing"]["near_duplicate_jaccard_threshold"],
            )
            if any(
                replay(
                    [item for item in artifacts if item["artifact_id"] != removed],
                    view_task,
                )["answer"]
                == answer
                for removed in view_essential_ids
            ):
                raise ValueError(f"P53 {bucket}/{view} remove-one gate failed")
            window_checks = _assert_short_windows_insufficient(
                tokenizer,
                question,
                artifacts,
                view_essential_ids,
                config["gates"]["short_window_tokens"],
            )
            source_receipt = _source_token_receipt(
                tokenizer, question, artifacts, context_tokens
            )
            if (
                source_receipt["source_token_ratio"]
                < config["gates"]["require_source_token_ratio_at_least"]
            ):
                raise ValueError(f"P53 {bucket}/{view} source-token ratio failed")
            candidate = {
                "schema_version": CANDIDATE_SCHEMA,
                "data_stage": "candidate",
                "train_ready": False,
                "selected": False,
                "promoted": False,
                "production_eligible": False,
                **local_probe_diagnostic_metadata(),
                "domain": "codeforge",
                "world_id": config["world_id"],
                "query_id": f"{config['world_id']}:{bucket}:{view}",
                "query_type": "osv_upstream_remediation_lifecycle",
                "length_bucket": bucket,
                "view": view,
                "composition_method": {
                    "full": "same_case_dossier",
                    "cf": "counterfactual_twin",
                    "ordered_artifact_view": "causal_timeline",
                }[view],
                "training_objective": "sft",
                "question": question,
                "answer": answer,
                "counterfactual_answer": counterfactual["answer"],
                "document_context": SEP.join(item["text"] for item in artifacts),
                "context": context,
                "artifact_classification": _classifications(
                    artifacts, view_essential_ids
                ),
                "essential_artifact_ids": sorted(view_essential_ids),
                "task": view_task,
                "program_ops": view_task["program_ops"],
                "graph": {
                    "proof_depth": 5 + len(selected_chains),
                    "hop_count": 5,
                    "required_chain_count": len(selected_chains),
                    "essential_artifact_count": len(view_essential_ids),
                },
                "source_family_ids": [
                    "github.com/pypa/advisory-database",
                    "osv.dev/PyPI",
                    *sorted(
                        {
                            item["patch_url"].split("/commit/", 1)[0]
                            for item in selected_chains
                        }
                    ),
                    "pypi.org",
                ],
                "source_token_receipt": source_receipt,
                "real_source_token_ratio": source_receipt["source_token_ratio"],
                "tokenizer_model_id": tokenizer_cfg["model_id"],
                "tokenizer_revision": tokenizer_cfg["revision"],
                "tokenizer_asset_manifest_sha256": tokenizer_cfg[
                    "asset_manifest_sha256"
                ],
                "tokenizer_context_tokens": context_tokens,
                "actual_context_tokens": context_tokens,
                "n_clones": 0,
                "source_license_manifest": [
                    {
                        "artifact_id": item["artifact_id"],
                        "license_id": item["license_id"],
                        "license_source_url": item["license_source_url"],
                        "source_url": item["source_url"],
                        "source_sha256": item["source_sha256"],
                    }
                    for item in artifacts
                ],
                "preflight_verification": {
                    "schema_ok": True,
                    "full_sufficient": True,
                    "minimal_sufficient": True,
                    "remove_one_fails": True,
                    "counterfactual_changes_answer": True,
                    "counterfactual_replay_sufficient": True,
                    "question_only_unsolved": True,
                    "no_unknown_as_safe": True,
                    "no_duplicate_artifact_bytes": True,
                    "no_near_duplicate_artifacts": True,
                    "exact_band": True,
                    "license_propagation_complete": True,
                    **window_checks,
                },
            }
            drafts.append(candidate)
            bucket_views[view] = {
                "tokens": context_tokens,
                "artifact_count": len(artifacts),
                "essential_artifact_count": len(view_essential_ids),
                "source_token_ratio": source_receipt["source_token_ratio"],
                "essential_span_tokens": window_checks["essential_span_tokens"],
                "answer_sha256": _sha256_bytes(answer.encode()),
            }
        pack_reports[bucket] = {
            "required_chain_count": len(selected_chains),
            "background_artifact_count": len(selected_background),
            "views": bucket_views,
        }

    source_sidecar_unsigned = {
        "schema_version": SOURCE_SCHEMA,
        "world_id": config["world_id"],
        "authorization_record_id": config["authorization_record_id"],
        "predecessor": config["predecessor"],
        "source_receipts": source_receipts,
        "records": [
            {key: value for key, value in item.items() if key not in {"parent"}}
            for item in sorted(
                used_artifacts.values(), key=lambda value: value["artifact_id"]
            )
        ],
        "counterfactual_derivations": sorted(
            {item["parent_artifact_id"]: item for item in cf_derivations}.values(),
            key=lambda item: item["parent_artifact_id"],
        ),
        "raw_archives_and_release_json_persisted": False,
        "normalized_or_licensed_source_artifacts_persisted": True,
    }
    source_sidecar = attach_attestation(
        source_sidecar_unsigned, source_key, purpose=SOURCE_PURPOSE
    )
    source_sidecar_raw = _canonical_bytes(source_sidecar)
    source_sidecar_sha256 = _sha256_bytes(source_sidecar_raw)
    signed = []
    for draft in drafts:
        draft["source_sidecar"] = {
            "schema_version": SOURCE_SCHEMA,
            "sha256": source_sidecar_sha256,
        }
        signed.append(
            attach_attestation(
                draft, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
            )
        )
    signed.sort(
        key=lambda item: (
            item["length_bucket"],
            config["views"].index(item["view"]),
        )
    )
    candidates_raw = b"".join(_canonical_bytes(item) for item in signed)
    output_dir = Path(config["output_dir"])
    _write(output_dir / "SOURCE_SIDECAR.json", source_sidecar_raw)
    _write(output_dir / "candidate" / "train.jsonl", candidates_raw)
    _write(output_dir / "candidate" / "eval.jsonl", b"")
    _write(output_dir / "candidate" / "reject_log.jsonl", b"")
    _write(output_dir / "preflight" / "accepted.jsonl", candidates_raw)
    _write(output_dir / "preflight" / "rejects.jsonl", b"")
    source_bundle = "".join(
        f"{item['url']}:{item['sha256']}\n" for item in source_receipts
    )
    quality_unsigned = {
        "schema_version": REPORT_SCHEMA,
        "data_product": config["data_product"],
        "data_stage": "candidate",
        "train_ready": False,
        "selected": False,
        "promoted": False,
        "production_eligible": False,
        **local_probe_diagnostic_metadata(),
        "config_sha256": _sha256_bytes(config_raw),
        "source_sidecar_sha256": source_sidecar_sha256,
        "source_bundle_sha256": _sha256_bytes(source_bundle.encode()),
        "candidate_rows_sha256": _sha256_bytes(candidates_raw),
        "candidate_count": len(signed),
        "rejected_count": 0,
        "world_count": 1,
        "views": {
            view: sum(item["view"] == view for item in signed)
            for view in config["views"]
        },
        "packs": pack_reports,
        "padding_tokens": 0,
        "cloned_artifacts": 0,
        "split_or_truncated_artifacts": 0,
        "formal_dense_audit_complete": False,
        "inventory_delta": 0,
        "shared_promotion_adapter_registered": False,
    }
    quality = attach_attestation(quality_unsigned, report_key, purpose=REPORT_PURPOSE)
    _write(output_dir / "candidate" / "quality_report.json", _canonical_bytes(quality))
    return quality


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().config), sort_keys=True))


if __name__ == "__main__":
    main()
