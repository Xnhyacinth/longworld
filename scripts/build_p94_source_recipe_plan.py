"""Build or verify a frozen source/task/length support matrix and native jobs."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p94_source_recipe_plan import _dump, _read, _sha, compile_plan
from scripts.run_p76_source_batch import preflight

CODE_PATHS = (
    "longworld/synthesis/p94_source_recipe_plan.py",
    "longworld/synthesis/source_batch_plan.py",
    "scripts/build_p94_source_recipe_plan.py",
    "scripts/run_p76_source_batch.py",
)


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = _read(config_path)
    plan = compile_plan(config)
    native = plan.pop("native_job_config")
    new_native = plan.pop("new_native_job_config")
    with tempfile.TemporaryDirectory(prefix="p94_native_preflight_") as temp:
        for name, candidate in (("all", native), ("new", new_native)):
            if candidate["jobs"]:
                path = Path(temp) / f"{name}_job_config.json"
                path.write_text(_dump(candidate) + "\n", encoding="utf-8")
                preflight(path)
    code = {name: _sha(ROOT / name) for name in CODE_PATHS}
    receipt = {
        "schema": "longworld.p94-source-recipe-plan.v1.receipt",
        "config_sha256": _sha(config_path),
        "code_sha256": code,
        "source_matrix": config["source_matrix"],
        "native_source_pool": config["native_source_pool"],
        "prior_source_matrix": config.get("prior_source_matrix"),
        "plan_sha256": None,
        "job_config_sha256": None,
        "new_job_config_sha256": None,
        "source_groups": plan["source_groups"],
        "native_jobs": plan["native_jobs"],
        "new_native_jobs": plan["new_native_jobs"],
        "cell_status_counts": plan["cell_status_counts"],
        "train_ready": False,
    }
    outputs = {
        "plan.json": _dump(plan) + "\n",
        "job_config.json": _dump(native) + "\n",
        "new_job_config.json": _dump(new_native) + "\n",
    }
    import hashlib

    receipt["plan_sha256"] = hashlib.sha256(outputs["plan.json"].encode()).hexdigest()
    receipt["job_config_sha256"] = hashlib.sha256(
        outputs["job_config.json"].encode()
    ).hexdigest()
    receipt["new_job_config_sha256"] = hashlib.sha256(
        outputs["new_job_config.json"].encode()
    ).hexdigest()
    outputs["receipt.json"] = _dump(receipt) + "\n"
    if output_dir.exists():
        if any(
            not (output_dir / name).is_file()
            or (output_dir / name).read_text(encoding="utf-8") != value
            for name, value in outputs.items()
        ):
            raise ValueError("existing source recipe plan differs from pinned inputs")
    elif verify_only:
        raise ValueError("cannot verify missing source recipe plan")
    else:
        output_dir.mkdir(parents=True)
        for name, value in outputs.items():
            (output_dir / name).write_text(value, encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(run(args.config, args.output_dir, verify_only=args.verify_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
