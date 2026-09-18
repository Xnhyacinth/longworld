#!/usr/bin/env python3
"""Build and replay a bounded, multiprocessing BEA macro-vintage taskbank."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.p66_macro_vintage_taskbank import (
    PROGRAMS,
    REVISION,
    canonical,
    execute,
    question,
    render_block,
    sha,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.train_sft import tokenize_assistant_only

CODE = (
    "longworld/core/p66_macro_vintage_taskbank.py",
    "scripts/materialize_p66_macro_vintage_taskbank.py",
    "scripts/train_sft.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rejection_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    """Count rejections from both schemas this build emits.

    Whole-world rejections write "rejection" (:81), band/program rejections
    write "reason" (:251/:291). Reading only "reason" raised KeyError on the
    first rejected world and lost the whole report; reading both keys keeps
    every rejection class visible.
    """
    return dict(
        Counter(row.get("reason") or row.get("rejection") or "unknown" for row in rows)
    )


def _load(config: dict[str, object]) -> tuple[dict[str, object], str]:
    source = config["source"]
    assert isinstance(source, dict)
    manifest_path = ROOT / str(source["manifest_path"])
    raw_path = ROOT / str(source["raw_path"])
    if manifest_path.is_symlink() or raw_path.is_symlink():
        raise ValueError("macro source paths may not be symlinks")
    if (
        digest(manifest_path) != source["manifest_sha256"]
        or digest(raw_path) != source["raw_sha256"]
    ):
        raise ValueError("macro source binding mismatch")
    manifest = json.loads(manifest_path.read_text())
    authorization = manifest.get("authorization", {})
    policies = manifest.get("source_policies", {})
    if (
        manifest.get("raw_source_sha256") != source["raw_sha256"]
        or not authorization.get("record_id")
        or "bea" not in policies
        or "public-domain" not in policies["bea"].get("license", "")
        or manifest.get("production_eligible") is not False
    ):
        raise ValueError("macro source provenance contract mismatch")
    return manifest, digest(manifest_path)


def _world(
    payload: tuple[str, str, list[dict[str, object]], list[dict[str, object]]],
) -> dict[str, object]:
    series, period, observations, relations = payload
    target = [
        row
        for row in observations
        if row["series_id"] == series and row["period"] == period
    ]
    if len(target) < 4:
        return {
            "series_id": series,
            "period": period,
            "rejection": "fewer_than_four_vintages",
        }
    target_ids = {str(row["observation_id"]) for row in target}
    target_relations = [
        row
        for row in relations
        if row["series_id"] == series
        and row["period"] == period
        and row["source_observation_id"] in target_ids
        and row["target_observation_id"] in target_ids
    ]
    blocks = []
    for row in observations:
        if row["series_id"] == series:
            blocks.append(
                {
                    "id": row["observation_id"],
                    "kind": "observation",
                    "row": row,
                    "target": row["period"] == period,
                }
            )
    for row in relations:
        if row["series_id"] == series:
            blocks.append(
                {
                    "id": row["relation_id"],
                    "kind": "relation",
                    "row": row,
                    "target": row["period"] == period,
                }
            )
    for block in blocks:
        block["text"] = render_block(str(block["kind"]), block["row"])
    blocks.sort(
        key=lambda item: (
            str(
                item["row"].get(
                    "source_vintage_date", item["row"].get("vintage_date", "")
                )
            ),
            str(item["id"]),
        )
    )
    return {
        "series_id": series,
        "period": period,
        "target": target,
        "target_relations": target_relations,
        "blocks": blocks,
        "answers": {program: execute(target, program) for program in PROGRAMS},
    }


def _render_context(series: str, period: str, blocks: list[dict[str, object]]) -> str:
    header = (
        "Authenticated BEA GDP/GDI vintage history derivative. Records remain in observed vintage order.\n"
        f"Target identity: series_id={series}; period={period}.\n"
    )
    return header + "".join(str(block["text"]) for block in blocks)


def _pack(
    tokenizer, world: dict[str, object], lower: int, upper: int
) -> tuple[str, list[dict[str, object]], int] | None:
    blocks = world["blocks"]
    assert isinstance(blocks, list)
    essentials = [item for item in blocks if item["target"]]
    background = [item for item in blocks if not item["target"]]
    background.sort(key=lambda item: sha(str(item["id"]) + str(world["period"])))
    lo, hi = 0, len(background)
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        chosen_ids = {str(item["id"]) for item in essentials + background[:mid]}
        chosen = [item for item in blocks if str(item["id"]) in chosen_ids]
        context = _render_context(str(world["series_id"]), str(world["period"]), chosen)
        tokens = len(tokenizer.encode(context, add_special_tokens=False))
        if tokens <= upper:
            best = (context, chosen, tokens)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None or best[2] < lower:
        return None
    return best


def _visible_target(blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        item["row"]
        for item in blocks
        if item["target"] and item["kind"] == "observation"
    ]


def _window_em(
    blocks: list[dict[str, object]],
    lengths: list[int],
    program: str,
    gold: dict[str, object],
) -> dict[str, bool]:
    result = {}
    for width in (4096, 8192, 16384):
        hit = False
        for start in range(len(blocks)):
            total = 0
            selected = []
            for index in range(start, len(blocks)):
                total += lengths[index]
                if total > width:
                    break
                selected.append(blocks[index])
            if execute(_visible_target(selected), program) == gold:
                hit = True
                break
        result[str(width)] = hit
    return result


def build(
    config_path: Path, output: Path, workers: int | None = None
) -> dict[str, object]:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "longworld.p66-macro-vintage-taskbank-build.v1":
        raise ValueError("unsupported macro taskbank config")
    manifest, manifest_sha = _load(config)
    periods = list(config["periods"])
    split_by_series = dict(config["split_by_series"])
    series = sorted(split_by_series)
    payloads = [
        (item, period, manifest["observations"], manifest["relations"])
        for item in series
        for period in periods
    ]
    worker_count = workers or int(config["workers"])
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        worlds = list(pool.map(_world, payloads))
    tokenizer_config = config["tokenizer"]
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_config["model_id"],
            revision=tokenizer_config["revision"],
            local_files_only=True,
            trust_remote_code=False,
        )
        assets = resolved_tokenizer_asset_manifest_sha256(
            tokenizer_config["model_id"], tokenizer_config["revision"]
        )
        output.mkdir(parents=True, exist_ok=False)
        (output / "contexts").mkdir()
        rows, sft, rejects = [], [], []
        for world in worlds:
            if "rejection" in world:
                rejects.append(world)
                continue
            series_id, period = str(world["series_id"]), str(world["period"])
            source_group = "bea-vintage-series:" + series_id
            split = split_by_series[series_id]
            world_id = sha(canonical([manifest_sha, series_id, period]))
            for band in config["bands"]:
                packed = _pack(
                    tokenizer,
                    world,
                    int(band["lower_tokens"]),
                    int(band["upper_tokens"]),
                )
                if packed is None:
                    rejects.append(
                        {
                            "series_id": series_id,
                            "period": period,
                            "band": band["name"],
                            "reason": "authentic_unique_records_cannot_fill_band",
                        }
                    )
                    continue
                context, blocks, context_tokens = packed
                context_sha = sha(context)
                context_path = f"contexts/{context_sha}.txt"
                (output / context_path).write_text(context)
                target_only = [item for item in blocks if item["target"]]
                target_only_tokens = len(
                    tokenizer.encode(
                        _render_context(series_id, period, target_only),
                        add_special_tokens=False,
                    )
                )
                block_lengths = [
                    len(tokenizer.encode(str(item["text"]), add_special_tokens=False))
                    for item in blocks
                ]
                latest = sorted(
                    world["target"], key=lambda row: str(row["vintage_date"])
                )[-1:]
                for program in PROGRAMS:
                    answer = world["answers"][program]
                    prompt = question(series_id, period, program)
                    messages = [
                        {
                            "role": "user",
                            "content": context + "\n\nQuestion:\n" + prompt,
                        },
                        {"role": "assistant", "content": canonical(answer)},
                    ]
                    chat = tokenize_assistant_only(tokenizer, messages, 1 << 30)
                    full_tokens = len(chat["input_ids"])
                    if full_tokens > 262144:
                        rejects.append(
                            {
                                "series_id": series_id,
                                "period": period,
                                "band": band["name"],
                                "program": program,
                                "reason": "complete_hf_chat_overflow",
                                "tokens": full_tokens,
                            }
                        )
                        continue
                    probes = _window_em(blocks, block_lengths, program, answer)
                    latest_em = execute(latest, program) == answer
                    remove_one = [
                        execute(
                            [row for j, row in enumerate(world["target"]) if j != i],
                            program,
                        )
                        == answer
                        for i in range(len(world["target"]))
                    ]
                    semantic = sha(
                        canonical([REVISION, manifest_sha, series_id, period, program])
                    )
                    sample_id = sha(
                        canonical([semantic, band["name"], context_sha, answer])
                    )
                    row = {
                        "schema_version": "longworld.p66-macro-vintage-taskbank-sample.v1",
                        "sample_id": sample_id,
                        "semantic_task_id": semantic,
                        "variant_family_id": "variants:" + semantic,
                        "variant": str(band["name"]),
                        "source_collection_id": manifest_sha,
                        "source_group_id": source_group,
                        "world_instance_id": world_id,
                        "split": split,
                        "series_id": series_id,
                        "period": period,
                        "program_id": program,
                        "question": prompt,
                        "answer": answer,
                        "context_path": context_path,
                        "context_sha256": context_sha,
                        "context_tokens": context_tokens,
                        "full_hf_chat_tokens": full_tokens,
                        "assistant_tokens": sum(
                            token != -100 for token in chat["labels"]
                        ),
                        "capacity_bin": next(
                            value
                            for value in (65536, 131072, 262144)
                            if context_tokens <= value
                        ),
                        "exact_numeric_range": str(
                            next(
                                value
                                for value in (65536, 131072, 262144)
                                if context_tokens <= value
                            )
                        ),
                        "target_observations": len(world["target"]),
                        "target_relations": len(world["target_relations"]),
                        "unique_visible_records": len(blocks),
                        "latest_only_answer_em": latest_em,
                        "short_window_answer_em": probes,
                        "compact_target_only_tokens": target_only_tokens,
                        "compact_target_only_answer_em": True,
                        "remove_one_all_change_answer": not any(remove_one),
                        "remove_one_no_effect_count": sum(remove_one),
                        "profile": "integration_candidate"
                        if not latest_em and not any(probes.values())
                        else "retrieval_candidate",
                        "strict_long_dependency_verified": False,
                        "training_view_eligible_local": True,
                        "training_release_eligible": False,
                        "production_eligible": False,
                    }
                    rows.append(row)
                    sft.append(
                        {"sample_id": sample_id, "split": split, "messages": messages}
                    )
        for name, values in (("tasks.jsonl", rows), ("sft_candidates.jsonl", sft)):
            (output / name).write_text(
                "".join(canonical(value) + "\n" for value in values)
            )
        (output / "rejections.json").write_text(canonical(rejects) + "\n")
    receipt = {
        "schema_version": "longworld.p66-macro-vintage-taskbank-receipt.v1",
        "status": "verified_hash_pinned_local_candidates",
        "config_sha256": digest(config_path),
        "source_manifest_sha256": manifest_sha,
        "raw_source_sha256": config["source"]["raw_sha256"],
        "source_authorization_record_id": manifest["authorization"]["record_id"],
        "source_license": manifest["source_policies"]["bea"]["license"],
        "source_groups": dict(Counter(row["source_group_id"] for row in rows)),
        "semantic_tasks": len({row["semantic_task_id"] for row in rows}),
        "training_views": len(rows),
        "local_sft_rows": len(sft),
        "worlds": len({row["world_instance_id"] for row in rows}),
        "splits": dict(Counter(row["split"] for row in rows)),
        "families": dict(Counter(row["program_id"] for row in rows)),
        "capacity_bins": dict(Counter(str(row["capacity_bin"]) for row in rows)),
        "exact_numeric_ranges": dict(
            Counter(row["exact_numeric_range"] for row in rows)
        ),
        "profiles": dict(Counter(row["profile"] for row in rows)),
        "latest_only_failures": sum(not row["latest_only_answer_em"] for row in rows),
        "short_window_failures_all_widths": sum(
            not any(row["short_window_answer_em"].values()) for row in rows
        ),
        "compact_target_only_successes": sum(
            row["compact_target_only_answer_em"] for row in rows
        ),
        "remove_one_all_change": sum(
            row["remove_one_all_change_answer"] for row in rows
        ),
        "rejections": rejection_counts(rejects),
        "workers": worker_count,
        "tokenizer": dict(tokenizer_config, asset_manifest_sha256=assets),
        "strict_verified": 0,
        "production_eligible": False,
        "training_release_eligible": False,
        "code_sha256": {name: digest(ROOT / name) for name in CODE},
        "files": {
            str(path.relative_to(output)): digest(path)
            for path in sorted(output.rglob("*"))
            if path.is_file()
        },
    }
    (output / "BUILD_RECEIPT.json").write_text(canonical(receipt) + "\n")
    return receipt


def validate(
    config_path: Path, output: Path, workers: int | None = None
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="p66-macro-replay-") as temporary:
        replay = Path(temporary) / "bank"
        receipt = build(config_path, replay, workers)
        expected = {
            str(path.relative_to(replay))
            for path in replay.rglob("*")
            if path.is_file()
        }
        observed = {
            str(path.relative_to(output))
            for path in output.rglob("*")
            if path.is_file()
        }
        if expected != observed or any(path.is_symlink() for path in output.rglob("*")):
            raise ValueError("output member inventory mismatch")
        for name in expected:
            if (output / name).read_bytes() != (replay / name).read_bytes():
                raise ValueError("macro taskbank replay mismatch: " + name)
    return {
        "status": "PASS",
        "training_views": receipt["training_views"],
        "worlds": receipt["worlds"],
        "strict_verified": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    result = (validate if args.validate else build)(
        args.config, args.output, args.workers
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"files", "code_sha256"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
