"""Official MRCR / GraphWalks graders used by scripts/eval_mrcr_graphwalks_client.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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


def test_graphwalks_f1_boundary_behavior():
    grade = _load_client().grade_graphwalks
    gold = ["a", "b"]
    # Official F1 stays 1.0 whenever precision+recall==0 (compat with the
    # OpenAI card and past summary.json files); f1_standard fixes the boundary.
    parse_fail = grade("no list here", gold)
    assert parse_fail["format_fail"] is True
    assert parse_fail["f1"] == 1.0
    assert parse_fail["f1_standard"] == 0.0
    disjoint = grade("Final Answer: [x]", gold)
    assert disjoint["f1"] == 1.0
    assert disjoint["f1_standard"] == 0.0
    empty = grade("Final Answer: []", [])
    assert empty["f1"] == 1.0
    assert empty["f1_standard"] == 1.0
    partial = grade("Final Answer: [a]", gold)
    assert partial["f1"] == pytest.approx(2 / 3)
    assert partial["f1_standard"] == pytest.approx(2 / 3)
