"""Official MRCR / GraphWalks graders used by scripts/eval_mrcr_graphwalks_client.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_client():
    path = ROOT / "scripts" / "eval_mrcr_graphwalks_client.py"
    spec = importlib.util.spec_from_file_location("eval_mrcr_graphwalks_client", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_official_graders_self_check():
    _load_client()._self_check()
