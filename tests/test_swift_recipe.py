"""128k method yamls must share the comparison recipe (lr, steps, wandb, cap)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "configs" / "swift" / "recipe.env"
SWIFT = ROOT / "configs" / "swift"
ALIGNED = ("ext_acc.yaml", "ext_longtrace.yaml", "ext_longmit.yaml")


def _env_defaults(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip().strip('"').strip("'")
        if "${" in raw and ":-" in raw:
            raw = raw.split(":-", 1)[1].rstrip("}")
        out[key] = raw
    return out


def _top_yaml(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped or stripped.startswith((" ", "-")):
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def test_recipe_wandb_and_128k_knobs():
    rec = _env_defaults(RECIPE)
    assert rec["WANDB_ENTITY"] == "wyncke"
    assert rec["WANDB_PROJECT"] == "longworld"
    assert rec["REPORT_TO"] == "wandb"
    assert rec["LEARNING_RATE"] == "1.0e-5"
    assert rec["MAX_STEPS_128K"] == "680"
    assert rec["MAX_LENGTH_128K"] == "133120"
    assert rec["GBS"] == "16"
    assert rec["PACKING"] == "false"
    assert rec["SEQUENCE_PARALLEL_SIZE"] == "2"
    assert rec["DEEPSPEED"] == "none"
    assert rec["ATTN_IMPL"] == "flash_attn"
    assert rec["USE_LOGITS_TO_KEEP"] == "false"
    assert rec["WANDB_RUN_GROUP_128K"] == "longworld-128k-sft"


def test_aligned_yamls_match_recipe():
    rec = _env_defaults(RECIPE)
    names = []
    for fname in ALIGNED:
        y = _top_yaml(SWIFT / fname)
        names.append(y["run_name"])
        assert y["report_to"] == rec["REPORT_TO"]
        assert y["learning_rate"] == rec["LEARNING_RATE"]
        assert y["max_steps"] == rec["MAX_STEPS_128K"]
        assert y["max_length"] == rec["MAX_LENGTH_128K"]
        assert y["warmup_ratio"] == rec["WARMUP_RATIO"]
        assert y["lr_scheduler_type"] == rec["LR_SCHEDULER_TYPE"]
        assert y["seed"] == rec["SEED"]
        assert y["packing"] == rec["PACKING"]
        assert y["padding_free"] == rec["PADDING_FREE"]
        assert y["use_logits_to_keep"] == rec["USE_LOGITS_TO_KEEP"]
        assert y["attn_impl"] == rec["ATTN_IMPL"]
        assert y["sequence_parallel_size"] == rec["SEQUENCE_PARALLEL_SIZE"]
        assert y["gradient_accumulation_steps"] == "16"
        assert y["save_steps"] == rec["SAVE_STEPS"]
        assert y["eval_steps"] == rec["EVAL_STEPS"]
        assert y["save_total_limit"] == rec["SAVE_TOTAL_LIMIT"]
        assert y["load_best_model_at_end"] == "true"
        assert y["run_name"].startswith("longworld-baseline-")
        assert y["run_name"].endswith("-128k-sp2")
    assert len(set(names)) == 3


def test_b_yamls_share_optimizer_and_wandb():
    rec = _env_defaults(RECIPE)
    for name in ("B1", "B2", "B3", "B4", "B5", "B5w"):
        y = _top_yaml(SWIFT / f"{name}.yaml")
        assert y["report_to"] == "wandb"
        assert y["learning_rate"] == rec["LEARNING_RATE"]
        assert y["warmup_ratio"] == rec["WARMUP_RATIO"]
        assert y["lr_scheduler_type"] == rec["LR_SCHEDULER_TYPE"]
        assert y["packing"] == rec["PACKING"]
        assert y["run_name"].startswith(f"longworld-{name}-")


def test_swift_release_excludes_unsupported_or_unweighted_conditions():
    exporter = (ROOT / "scripts" / "export_swift.py").read_text()
    launcher = (ROOT / "scripts" / "train_swift.sh").read_text()

    assert 'CAUSALTWIN_CONDITIONS = ("B1", "B3", "B5")' in exporter
    assert '"B2"|"B4"|"B5w")' in launcher
    assert "unsupported for the signed Swift release" in launcher
