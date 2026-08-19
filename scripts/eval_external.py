#!/usr/bin/env python3
"""Optional external long-context sanity (HELMET / cached datasets).

Does not replace the CausalTwin diagnostic. Skips cleanly if files are absent.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB = Path.home() / ".cache" / "huggingface" / "hub"


def main() -> None:
    found = {
        "Qwen2.5-7B-Instruct": (HUB / "models--Qwen--Qwen2.5-7B-Instruct").exists(),
        "HELMET": (HUB / "datasets--princeton-nlp--HELMET").exists(),
        "ProLong-64K": (HUB / "datasets--princeton-nlp--prolong-data-64K").exists(),
    }
    report = {
        "role": "external sanity only — not the paper claim",
        "cached": found,
        "note": (
            "Run RULER / LongBench v2 / NoLiMa with the same backbone after SFT. "
            "7B is not expected to match ACC-paper MRCR. Claim lives on CausalTwin CFR."
        ),
    }
    dest = ROOT / "data" / "p0" / "eval_external.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
