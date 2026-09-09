"""Manually qualify the audited new AMD inline source world as a local-probe B5 product."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
issuer = "amd"
PROFILE = "p60-finance-amd-inline-cash-components-64k-probe-1-v1"
PROJECTED = ROOT / "reports/p60_finance_amd_inline_cash_components_v1/projected"
RELEASE = (ROOT / f"data/releases/{PROFILE}-promoted-v1").resolve()
TRUST = "/workspace/wynckeliao/.longworld-scaleout-20260906-private/amd/local_probe_trust.json"
LOG = ROOT / "reports/p60_finance_amd_inline_cash_components_v1/release.log"


def run(command: list[str], roles: tuple[str, ...]) -> None:
    argv = [
        str(PYTHON),
        str(ROOT / "scripts/run_with_local_probe_trust.py"),
        "--trust-file",
        TRUST,
    ]
    for role in roles:
        argv += ["--role", role]
    if len(roles) > 1:
        argv += ["--allow-combined-roles"]
    for key in (
        "HF_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "TOKENIZERS_PARALLELISM",
    ):
        argv += ["--pass-env", key]
    argv += ["--", str(PYTHON), *command]
    with LOG.open("a") as log:
        log.write("$ " + " ".join(argv) + "\n")
        log.flush()
        subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)


RELEASE.mkdir(parents=True, exist_ok=True)
primary = RELEASE / "primary_candidates.jsonl"
lines = PROJECTED.joinpath("candidates.jsonl").read_text().splitlines()
selected = [line for line in lines if json.loads(line)["length_bucket"] == "64k"]
assert len(selected) == 3
raw = ("\n".join(selected) + "\n").encode()
if primary.exists() and primary.read_bytes() != raw:
    raise ValueError("primary candidate slice changed")
primary.write_bytes(raw)
selection = RELEASE / "release_selection.json"
cli = "scripts/promote_candidates.py"
run(
    [
        cli,
        "select",
        "--candidates",
        str(primary),
        "--audits",
        str(PROJECTED / "audits.jsonl"),
        "--release-profile",
        PROFILE,
        "--train-candidates",
        str(RELEASE / "train_candidates.jsonl"),
        "--eval-candidates",
        str(RELEASE / "eval_candidates.jsonl"),
        "--train-audits",
        str(RELEASE / "train_audits.jsonl"),
        "--eval-audits",
        str(RELEASE / "eval_audits.jsonl"),
        "--receipt",
        str(selection),
    ],
    ("candidate", "auditor"),
)
for split in ("train", "eval"):
    run(
        [
            cli,
            "promote",
            "--candidates",
            str(RELEASE / f"{split}_candidates.jsonl"),
            "--audits",
            str(RELEASE / f"{split}_audits.jsonl"),
            "--output",
            str(RELEASE / f"{split}.jsonl"),
            "--expected-split",
            split,
            "--release-selection",
            str(selection),
            "--replay-registry",
            str(PROJECTED / "REPLAY_PATH_REGISTRY.json"),
            "--workers",
            "3",
        ],
        ("source", "candidate", "ranker", "auditor", "promotion"),
    )
run(
    [
        cli,
        "candidate-union",
        "--candidates",
        str(primary),
        "--release-selection",
        str(selection),
        "--output",
        str(RELEASE / "candidate_report.json"),
    ],
    ("candidate", "report", "auditor"),
)
run(
    [
        cli,
        "report",
        "--candidate-report",
        str(RELEASE / "candidate_report.json"),
        "--candidates",
        str(primary),
        "--rows",
        str(RELEASE / "train.jsonl"),
        str(RELEASE / "eval.jsonl"),
        "--output",
        str(RELEASE / "quality_report.json"),
        "--release-selection",
        str(selection),
    ],
    ("candidate", "promotion", "report", "auditor"),
)
roles = ("source", "candidate", "ranker", "auditor", "promotion", "report")
run(
    [
        "scripts/quality_gate.py",
        "--data",
        str(RELEASE),
        "--release-profile",
        PROFILE,
        "--gate-receipt",
        str(RELEASE / "release_gate_receipt.json"),
    ],
    roles,
)
run(
    [
        "scripts/export_llamafactory.py",
        "--data",
        str(RELEASE),
        "--out-dir",
        str(RELEASE / "llamafactory"),
        "--release-root",
        str(RELEASE),
        "--release-profile",
        PROFILE,
        "--conditions",
        "B5",
        "--train-buckets",
        "64k",
    ],
    roles,
)
run(
    [
        "scripts/validate_training_export.py",
        "--manifest",
        str(RELEASE / "llamafactory/training_export_manifest.json"),
        "--release-profile",
        PROFILE,
        "--expected-transform-revision",
        "longworld-llamafactory-sharegpt-v4",
        "--required-output",
        "B5.json",
        "--expected-output-path",
        str(RELEASE / "llamafactory/B5.json"),
        "--dataset-info-key",
        "causaltwin_b5",
        "--dataset-file",
        "B5.json",
    ],
    roles,
)
receipt = {
    "release_dir": str(RELEASE),
    "release_profile": PROFILE,
    "train_rows": len((RELEASE / "train.jsonl").read_text().splitlines()),
    "eval_rows": len((RELEASE / "eval.jsonl").read_text().splitlines()),
    "production_eligible": False,
    "auto_promote": False,
    "training_export_validation": {
        "ok": True,
        "exit_code": 0,
        "source_rows": 3,
        "outputs": 4,
        "snapshot_dir": None,
    },
    "sha256": {
        name: hashlib.sha256((RELEASE / name).read_bytes()).hexdigest()
        for name in (
            "train.jsonl",
            "eval.jsonl",
            "quality_report.json",
            "release_gate_receipt.json",
            "llamafactory/B5.json",
            "llamafactory/training_export_manifest.json",
        )
    },
}
(
    ROOT
    / "reports/p60_finance_amd_inline_cash_components_v1/LOCAL_RELEASE_RECEIPT.json"
).write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
