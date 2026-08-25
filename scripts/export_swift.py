#!/usr/bin/env python3
"""Convert LLaMA-Factory ShareGPT dumps to ms-swift messages jsonl.

Does not re-read the raw HF downloads. Reads data/external/llamafactory/
(and optional CausalTwin data/sft/llamafactory/) and writes data/external/swift/.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from export_external_llamafactory import sharegpt_to_messages
from quality_gate import load_release_product

from longworld.core.attestation import attestation_key_from_env
from longworld.core.release_profile import release_profile
from longworld.core.training_manifest import (
    create_training_manifest,
    validate_training_manifest,
)

ACC_PARTS = ("acc_search", "acc_swe", "acc_sql")
EXTERNAL_KEYS = (
    "acc_search",
    "acc_swe",
    "acc_sql",
    "longtracerl",
    "longmit",
    "loongrl",
    "docqa_rl",
)
CAUSALTWIN_CONDITIONS = ("B1", "B3", "B5")
TRANSFORM_REVISION = "longworld-swift-messages-v2"


def iter_records(path: Path):
    if path.suffix == ".jsonl":
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    data = json.loads(path.read_text())
    if isinstance(data, list):
        yield from data
    elif isinstance(data, dict):
        yield data


def convert_stream(rows, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    skipped = 0
    with dest.open("w") as f:
        for rec in rows:
            out = sharegpt_to_messages(rec)
            if out is None:
                skipped += 1
                continue
            src = rec.get("source")
            if src:
                out["source"] = src
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
            n += 1
    return {"n": n, "skipped": skipped, "path": str(dest)}


def split_val(
    src: Path, train_dest: Path, val_dest: Path, n_val: int, seed: int
) -> dict:
    n = 0
    with src.open() as f:
        for line in f:
            if line.strip():
                n += 1
    k = min(n_val, n)
    val_idx = set(random.Random(seed).sample(range(n), k)) if k else set()
    train_dest.parent.mkdir(parents=True, exist_ok=True)
    n_train = 0
    n_hold = 0
    with src.open() as f, train_dest.open("w") as tr, val_dest.open("w") as va:
        i = 0
        for line in f:
            if not line.strip():
                continue
            if i in val_idx:
                va.write(line if line.endswith("\n") else line + "\n")
                n_hold += 1
            else:
                tr.write(line if line.endswith("\n") else line + "\n")
                n_train += 1
            i += 1
    if src.resolve() == train_dest.resolve():
        raise ValueError(
            "split_val needs a distinct train_dest; src would be truncated"
        )
    return {
        "n": n,
        "n_train": n_train,
        "n_val": n_hold,
        "train": str(train_dest),
        "val": str(val_dest),
    }


def concat_jsonl(parts: list[Path], dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with dest.open("w") as out:
        for p in parts:
            with p.open() as f:
                for line in f:
                    if line.strip():
                        out.write(line if line.endswith("\n") else line + "\n")
                        n += 1
    return n


def convert_named(src: Path, dest: Path) -> dict:
    return convert_stream(iter_records(src), dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src-dir", type=Path, default=ROOT / "data" / "external" / "llamafactory"
    )
    ap.add_argument(
        "--out-dir", type=Path, default=ROOT / "data" / "external" / "swift"
    )
    ap.add_argument(
        "--causaltwin-src",
        type=Path,
        default=ROOT / "data" / "sft" / "llamafactory",
    )
    ap.add_argument(
        "--causaltwin-out",
        type=Path,
        default=ROOT / "data" / "sft" / "swift",
    )
    ap.add_argument("--val-size", type=int, default=32)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--release-profile")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    metas = []
    only = set(args.only) if args.only else None

    converted = {}
    for name in EXTERNAL_KEYS:
        if (
            only
            and name not in only
            and not (name.startswith("acc_") and only & {"acc", *ACC_PARTS})
        ):
            continue
        src = args.src_dir / f"{name}.jsonl"
        if not src.exists():
            continue
        dest = args.out_dir / f"{name}.jsonl"
        meta = convert_named(src, dest)
        meta["source"] = name
        converted[name] = dest
        metas.append(meta)
        print(json.dumps(meta, indent=2))

    want_acc = only is None or "acc" in only or bool(only & set(ACC_PARTS))
    acc_parts = [converted[k] for k in ACC_PARTS if k in converted]
    if want_acc and len(acc_parts) == 3:
        merged = args.out_dir / "acc_all.jsonl"
        concat_jsonl(acc_parts, merged)
        split_meta = split_val(
            merged,
            args.out_dir / "acc.jsonl",
            args.out_dir / "acc_val.jsonl",
            args.val_size,
            args.seed,
        )
        split_meta["source"] = "acc"
        metas.append(split_meta)
        print(json.dumps(split_meta, indent=2))
        merged.unlink(missing_ok=True)

    for name in ("longtracerl", "longmit", "loongrl", "docqa_rl"):
        if name not in converted:
            continue
        if only and name not in only:
            continue
        n_val = args.val_size if name in {"longtracerl", "longmit"} else 0
        src = converted[name]
        if n_val <= 0:
            continue
        tmp = src.with_name(f"{name}_all.jsonl")
        src.replace(tmp)
        split_meta = split_val(
            tmp,
            src,
            args.out_dir / f"{name}_val.jsonl",
            n_val,
            args.seed,
        )
        split_meta["source"] = name
        metas.append(split_meta)
        print(json.dumps(split_meta, indent=2))
        tmp.unlink(missing_ok=True)

    if args.causaltwin_src.exists() and (only is None or "causaltwin" in only):
        if not args.release_profile:
            raise SystemExit("--release-profile is required for CausalTwin conversion")
        if (
            TRANSFORM_REVISION
            != release_profile(args.release_profile).swift_transform_revision
        ):
            raise SystemExit("Swift transform differs from immutable release profile")
        upstream_manifest_path = args.causaltwin_src / "training_export_manifest.json"
        upstream_manifest = validate_training_manifest(
            upstream_manifest_path,
            expected_release_profile_id=args.release_profile,
            expected_transform_revision="longworld-llamafactory-sharegpt-v4",
            attestation_key=attestation_key_from_env("training_export_manifest"),
        )
        product = load_release_product(
            Path(str(upstream_manifest["source_data_dir"])), args.release_profile
        )
        bound_inputs = {str(entry["path"]) for entry in upstream_manifest["outputs"]}
        args.causaltwin_out.mkdir(parents=True, exist_ok=True)
        causaltwin_outputs: list[Path] = []
        causal_metas: list[dict] = []
        for name in CAUSALTWIN_CONDITIONS:
            src = args.causaltwin_src / f"{name}.json"
            if src.name not in bound_inputs:
                raise SystemExit(f"upstream manifest does not bind {src.name}")
            dest = args.causaltwin_out / f"{src.stem}.jsonl"
            meta = convert_named(src, dest)
            meta["source"] = src.stem
            metas.append(meta)
            causal_metas.append(meta)
            causaltwin_outputs.append(dest)
            print(json.dumps(meta, indent=2))
        causal_summary = args.causaltwin_out / "export_summary.json"
        causal_summary.write_text(json.dumps({"conditions": causal_metas}, indent=2))
        causaltwin_outputs.append(causal_summary)
        create_training_manifest(
            args.causaltwin_out / "training_export_manifest.json",
            source_data_dir=Path(str(upstream_manifest["source_data_dir"])),
            release_profile_id=args.release_profile,
            transform_revision=TRANSFORM_REVISION,
            output_paths=causaltwin_outputs,
            source_file_sha256=product.source_file_sha256,
            attestation_key=attestation_key_from_env("training_export_manifest"),
            upstream_manifest_path=upstream_manifest_path,
        )

    summary = {"conditions": metas}
    (args.out_dir / "export_summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
