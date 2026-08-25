#!/usr/bin/env python3
"""Export replayable LongWorld views to LLaMA-Factory ShareGPT shards.

B1 is full-only, B3 adds counterfactual twins, B5 adds the causal timeline,
and B5w applies distance weights through LLaMA-Factory v1's dataset sampler.
Short minimal contexts are a separate curriculum, not a long-view ablation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quality_gate import load_release_product

from longworld.core.attestation import attestation_key_from_env
from longworld.core.record_contract import sft_row_errors
from longworld.core.release_profile import release_profile
from longworld.core.training_manifest import create_training_manifest

TRANSFORM_REVISION = "longworld-llamafactory-sharegpt-v4"

COND_VIEWS = {
    "B1": {"full"},
    "B3": {"full", "cf"},
    "B5": {"full", "cf", "ordered_artifact_view"},
    "B5w": {"full", "cf", "ordered_artifact_view"},
}

SYSTEM = (
    "You are a careful analyst of long internal records: contracts, lab notes, "
    "git objects, CI logs, and search snapshots. Use only the provided context. "
    "If the context is insufficient, reply exactly: unanswerable"
)


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def filter_rows(
    rows: list[dict], condition: str, train_buckets: set[str]
) -> list[dict]:
    if condition not in COND_VIEWS:
        raise ValueError(f"unsupported release condition: {condition}")
    views = COND_VIEWS[condition]
    long_buckets = {"32k", "64k", "128k", "256k"}
    out = []
    for r in rows:
        if sft_row_errors(r):
            continue
        if r["view"] not in views:
            continue
        if r.get("length_bucket", "8k") not in train_buckets:
            continue
        if "decoy" in r.get("query_id", ""):
            continue
        bucket = r.get("length_bucket", "8k")
        klass = r.get("dependency_class")
        if bucket in long_buckets and klass == "local_or_mixed":
            continue
        out.append(r)
    if condition in {"B1", "B3"}:
        out = [r for r in out if r["query_timing"] == "first"]
    return out


def validate_condition_views(rows: list[dict], condition: str) -> None:
    if condition not in COND_VIEWS:
        raise ValueError(f"unsupported release condition: {condition}")
    observed = {str(row.get("view") or "") for row in rows}
    missing = sorted(COND_VIEWS[condition] - observed)
    if missing:
        raise ValueError(f"{condition} missing required views: {missing}")


def coverage_first_rows(
    rows: list[dict], condition: str, train_buckets: set[str]
) -> list[dict]:
    """Put one row from every required view/bucket cell before cap truncation."""
    required = {
        (view, bucket) for view in COND_VIEWS[condition] for bucket in train_buckets
    }
    seen: set[tuple[str, str]] = set()
    heads: list[dict] = []
    tails: list[dict] = []
    for row in rows:
        key = (str(row.get("view") or ""), str(row.get("length_bucket") or ""))
        if key in required and key not in seen:
            heads.append(row)
            seen.add(key)
        else:
            tails.append(row)
    missing = sorted(required - seen)
    if missing:
        raise ValueError(f"{condition} missing required view/length cells: {missing}")
    return [*heads, *tails]


def validate_export_meta(meta: dict, condition: str, train_buckets: set[str]) -> None:
    observed = set((meta.get("by_view_length") or {}).keys())
    required = {
        f"{view}|{bucket}" for view in COND_VIEWS[condition] for bucket in train_buckets
    }
    missing = sorted(required - observed)
    if missing:
        raise ValueError(f"{condition} lost required coverage after cap: {missing}")


def validate_export_twins(rows: list[dict], condition: str) -> None:
    """Require every factual/CF dossier in a CF condition to remain paired."""
    if "cf" not in COND_VIEWS[condition]:
        return
    dossiers: dict[str, set[str]] = {}
    for row in rows:
        view = str(row.get("view") or "")
        if view not in {"full", "cf"}:
            continue
        dossier = str(row.get("dossier_id") or "")
        if not dossier:
            raise ValueError(f"{condition} exported a twin without dossier identity")
        views = dossiers.setdefault(dossier, set())
        if view in views:
            raise ValueError(f"{condition} duplicated a {view} dossier twin: {dossier}")
        views.add(view)
    asymmetric = sorted(
        dossier for dossier, views in dossiers.items() if views != {"full", "cf"}
    )
    if asymmetric:
        raise ValueError(f"{condition} exported asymmetric dossier twins: {asymmetric}")


def validate_token_spread(budgets: list[int], maximum: float) -> float:
    spread = 0.0
    ordered = sorted(budgets)
    if ordered and ordered[-1]:
        spread = (ordered[-1] - ordered[0]) / ordered[-1]
    if spread > maximum:
        raise ValueError(
            f"equal-token spread {spread:.2%} exceeds release maximum {maximum:.2%}"
        )
    return spread


def validate_release_transform(
    release_profile_id: str,
    *,
    conditions: list[str] | tuple[str, ...],
    train_buckets: set[str],
    seed: int,
    token_budget: int | None,
) -> None:
    profile = release_profile(release_profile_id)
    if (
        tuple(conditions) != profile.training_conditions
        or train_buckets != set(profile.training_length_buckets)
        or seed != profile.training_export_seed
        or token_budget is not None
        or TRANSFORM_REVISION != profile.llamafactory_transform_revision
    ):
        raise ValueError("release training transform differs from immutable profile")


def to_sharegpt(row: dict, *, sample_weight: int = 1) -> dict:
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM},
            {"from": "human", "value": row["context"]},
            {"from": "gpt", "value": str(row["answer"])},
        ],
        "world_id": row["world_id"],
        "query_id": row["query_id"],
        "dossier_id": row.get("dossier_id"),
        "view": row["view"],
        "query_type": row["query_type"],
        "query_timing": row["query_timing"],
        "length_bucket": row.get("length_bucket"),
        "evidence_distance": (
            (row.get("difficulty") or {}).get("max_evidence_distance")
            if isinstance(row.get("difficulty"), dict)
            else None
        )
        or row.get("evidence_distance", 0),
        "dependency_class": row.get("dependency_class"),
        "sample_weight": sample_weight,
        "base_task_id": row.get("base_task_id"),
        "executable_proof_id": row.get("executable_proof_id"),
        "source_relation_id": row.get("source_relation_id"),
        "answer_program_id": row.get("answer_program_id"),
        "source_origins": row.get("source_origins", []),
        "workflow_kinds": row.get("workflow_kinds", []),
        "workflow_ids": row.get("workflow_ids", []),
        "evidence_roles": row.get("evidence_roles", []),
        "composition_method": row.get("composition_method"),
        "training_objective": row.get("training_objective"),
    }


def token_est(row: dict) -> int:
    return int(row["difficulty"]["context_tokens"]) + max(
        1, len(str(row["answer"])) // 4
    )


def distance_repeats(row: dict) -> int:
    """EXACT-lite: upsample long-span unique evidence, cap at 3 copies."""
    if row.get("view") not in {"full", "ordered_artifact_view", "trajectory", "cf"}:
        return 1
    d = int(row.get("difficulty", {}).get("max_evidence_distance") or 0)
    t = max(1, int(row.get("difficulty", {}).get("context_tokens") or 1))
    return max(1, min(3, 1 + int(2 * d / t)))


def write_condition(
    rows: list[dict],
    dest: Path,
    token_budget: int | None,
    upsample: bool,
    *,
    require_cf_twins: bool = False,
) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    used = 0
    n = 0
    weighted_n = 0
    by_view: Counter = Counter()
    by_len: Counter = Counter()
    by_timing: Counter = Counter()
    by_view_length: Counter = Counter()
    out_rows: list[dict] = []
    seen: set[str] = set()
    duplicates_dropped = 0
    contract_rejects = 0
    eligible: list[dict] = []
    for r in rows:
        if sft_row_errors(r):
            contract_rejects += 1
            continue
        digest = str(r.get("content_hash") or "")
        if not digest:
            digest = hashlib.sha256(
                json.dumps(
                    {"context": r.get("context"), "answer": str(r.get("answer"))},
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        if digest in seen:
            duplicates_dropped += 1
            continue
        seen.add(digest)
        eligible.append(r)

    units: list[list[dict]] = []
    if require_cf_twins:
        twins: dict[str, dict[str, dict]] = {}
        for row in eligible:
            view = str(row.get("view") or "")
            if view not in {"full", "cf"}:
                continue
            dossier = str(row.get("dossier_id") or "")
            if not dossier:
                raise ValueError(
                    "counterfactual export row is missing dossier identity"
                )
            twin_views = twins.setdefault(dossier, {})
            if view in twin_views:
                raise ValueError(f"duplicate {view} row for dossier: {dossier}")
            twin_views[view] = row
        asymmetric = sorted(
            dossier
            for dossier, twin_views in twins.items()
            if set(twin_views) != {"full", "cf"}
        )
        if asymmetric:
            raise ValueError(
                f"counterfactual export has asymmetric dossiers: {asymmetric}"
            )
        emitted_dossiers: set[str] = set()
        for row in eligible:
            view = str(row.get("view") or "")
            if view in {"full", "cf"}:
                dossier = str(row["dossier_id"])
                if dossier in emitted_dossiers:
                    continue
                emitted_dossiers.add(dossier)
                units.append([twins[dossier]["full"], twins[dossier]["cf"]])
            else:
                units.append([row])
    else:
        units = [[row] for row in eligible]

    for unit in units:
        weighted_tokens = sum(
            token_est(row) * (distance_repeats(row) if upsample else 1) for row in unit
        )
        if token_budget is not None and used + weighted_tokens > token_budget:
            continue
        used += weighted_tokens
        for r in unit:
            weight = distance_repeats(r) if upsample else 1
            out_rows.append(to_sharegpt(r, sample_weight=weight))
            n += 1
            weighted_n += weight
            by_view[r["view"]] += 1
            by_len[r.get("length_bucket", "?")] += 1
            by_timing[r.get("query_timing", "?")] += 1
            by_view_length[f"{r['view']}|{r.get('length_bucket', '?')}"] += 1
    if not out_rows:
        raise ValueError("token budget cannot fit any atomic export unit")
    if require_cf_twins:
        validate_export_twins(out_rows, "B5w" if upsample else "B5")
    dest.write_text(json.dumps(out_rows, ensure_ascii=False) + "\n")
    return {
        "n": n,
        "weighted_n": weighted_n,
        "tokens_est": used,
        "path": dest.name,
        "by_view": dict(by_view),
        "by_length": dict(by_len),
        "by_timing": dict(by_timing),
        "by_view_length": dict(by_view_length),
        "upsample": upsample,
        "duplicates_dropped": duplicates_dropped,
        "contract_rejects": contract_rejects,
    }


def write_b5w_v1_sampler(combined_path: Path, out_dir: Path) -> dict:
    """Shard unique rows by weight for LLaMA-Factory v1's real data index sampler."""
    rows = json.loads(combined_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("B5w sampler input is empty or malformed")
    groups: dict[int, list[dict]] = {}
    seen: set[str] = set()
    logical_seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("B5w sampler row must be an object")
        weight = row.get("sample_weight")
        if not isinstance(weight, int) or weight not in {1, 2, 3}:
            raise ValueError("B5w sample weight must be an integer in [1, 3]")
        digest = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest in seen:
            raise ValueError("B5w sampler input duplicates a serialized row")
        seen.add(digest)
        logical_row = dict(row)
        logical_row.pop("sample_weight", None)
        logical_digest = hashlib.sha256(
            json.dumps(logical_row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if logical_digest in logical_seen:
            raise ValueError("B5w sampler input duplicates a logical row")
        logical_seen.add(logical_digest)
        groups.setdefault(weight, []).append(row)

    for stale_shard in out_dir.glob("B5w.weight*.json"):
        stale_shard.unlink()
    dataset_info: dict[str, dict[str, object]] = {}
    shard_paths: list[Path] = []
    single_weight = len(groups) == 1
    for weight, group in sorted(groups.items()):
        shard = combined_path if single_weight else out_dir / f"B5w.weight{weight}.json"
        if not single_weight:
            shard.write_text(json.dumps(group, ensure_ascii=False) + "\n")
            shard_paths.append(shard)
        dataset_info[f"causaltwin_b5w_w{weight}"] = {
            "path": shard.name,
            "source": "local",
            "converter": "sharegpt",
            "weight": float(weight),
        }
    if not single_weight:
        combined_path.unlink()
    index_path = out_dir / "B5w.datasets.yaml"
    index_path.write_text(
        yaml.safe_dump(dataset_info, sort_keys=True), encoding="utf-8"
    )
    return {
        "engine": "llamafactory_v1_data_index_weight",
        "n": len(rows),
        "weighted_n": sum(weight * len(group) for weight, group in groups.items()),
        "by_weight": {
            str(weight): len(group) for weight, group in sorted(groups.items())
        },
        "combined_retained": single_weight,
        "output_paths": [index_path, *shard_paths],
    }


def write_dataset_info(out_dir: Path, conditions: list[str]) -> None:
    info = {}
    for c in conditions:
        if c == "B5w":
            continue
        info[f"causaltwin_{c.lower()}"] = {
            "file_name": f"{c}.json",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
                "system_tag": "system",
            },
        }
    (out_dir / "dataset_info.json").write_text(json.dumps(info, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "p0")
    ap.add_argument(
        "--out-dir", type=Path, default=ROOT / "data" / "sft" / "llamafactory"
    )
    ap.add_argument(
        "--release-root",
        type=Path,
        help="root used for relocatable source, manifest, and output paths",
    )
    ap.add_argument("--token-budget", type=int, default=None)
    ap.add_argument(
        "--train-buckets",
        default="16k,32k,64k",
        help="immutable release buckets for the selected profile",
    )
    ap.add_argument("--conditions", default="B1,B3,B5,B5w")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--release-profile", required=True)
    args = ap.parse_args()
    train_buckets = {b.strip() for b in args.train_buckets.split(",") if b.strip()}
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    try:
        validate_release_transform(
            args.release_profile,
            conditions=conditions,
            train_buckets=train_buckets,
            seed=args.seed,
            token_budget=args.token_budget,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    try:
        product = load_release_product(args.data, args.release_profile)
    except (TypeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    rows = list(product.train_rows)
    rng = random.Random(args.seed)
    rng.shuffle(rows)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    metas = []
    prepared: dict[str, list[dict]] = {}
    for c in conditions:
        prepared[c] = filter_rows(rows, c, train_buckets)
        if c == "B5w":
            prepared[c] = filter_rows(rows, "B5", train_buckets)
        validate_condition_views(prepared[c], c)
        prepared[c] = coverage_first_rows(prepared[c], c, train_buckets)

    # Equal-token: cap to the smallest condition after a first pass without cap.
    first_pass = {}
    for c in conditions:
        upsample = c == "B5w"
        first_pass[c] = write_condition(
            prepared[c],
            args.out_dir / f"{c}.json",
            args.token_budget,
            upsample,
            require_cf_twins="cf" in COND_VIEWS[c],
        )
    if args.token_budget is None and len(first_pass) > 1:
        cap = min(m["tokens_est"] for m in first_pass.values() if m["n"])
        for c in conditions:
            upsample = c == "B5w"
            meta = write_condition(
                prepared[c],
                args.out_dir / f"{c}.json",
                cap,
                upsample,
                require_cf_twins="cf" in COND_VIEWS[c],
            )
            meta["condition"] = c
            metas.append(meta)
            (args.out_dir / f"{c}.meta.json").write_text(json.dumps(meta, indent=2))
    else:
        for c, meta in first_pass.items():
            meta["condition"] = c
            metas.append(meta)
            (args.out_dir / f"{c}.meta.json").write_text(json.dumps(meta, indent=2))

    for meta in metas:
        validate_export_meta(meta, str(meta["condition"]), train_buckets)
        condition = str(meta["condition"])
        exported = json.loads((args.out_dir / f"{condition}.json").read_text())
        validate_export_twins(exported, condition)

    write_dataset_info(args.out_dir, conditions)
    budgets = [m["tokens_est"] for m in metas]
    try:
        spread = validate_token_spread(
            budgets,
            release_profile(args.release_profile).max_training_token_spread,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    b5w_sampler = None
    if "B5w" in conditions:
        b5w_sampler = write_b5w_v1_sampler(args.out_dir / "B5w.json", args.out_dir)
    summary = {
        "conditions": metas,
        "token_spread": round(spread, 4),
        "b5w_sampler": (
            {key: value for key, value in b5w_sampler.items() if key != "output_paths"}
            if b5w_sampler is not None
            else None
        ),
    }
    summary_path = args.out_dir / "export_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    output_paths = [summary_path, args.out_dir / "dataset_info.json"]
    for condition in conditions:
        if condition != "B5w" or (
            b5w_sampler is not None and b5w_sampler["combined_retained"]
        ):
            output_paths.append(args.out_dir / f"{condition}.json")
        output_paths.append(args.out_dir / f"{condition}.meta.json")
    if b5w_sampler is not None:
        output_paths.extend(b5w_sampler["output_paths"])
    create_training_manifest(
        args.out_dir / "training_export_manifest.json",
        source_data_dir=args.data,
        release_profile_id=args.release_profile,
        transform_revision=TRANSFORM_REVISION,
        output_paths=output_paths,
        source_file_sha256=product.source_file_sha256,
        attestation_key=attestation_key_from_env("training_export_manifest"),
        release_root=args.release_root,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
