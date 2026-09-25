"""Frozen title evidence must drive reproducible Wiki vocabulary expansion."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import p108_wiki_autotopic_catalog as catalog
from scripts import p108_wiki_autotopic_gate_bridge as bridge
from scripts import p108_wiki_autotopic_inventory as inventory
from scripts import p108_wiki_autotopic_prior as prior


def test_title_and_category_terms_keep_visible_noun_phrase() -> None:
    assert (
        catalog._term("List of botanical gardens in Tamil Nadu") == "botanical gardens"
    )
    assert (
        catalog._term("Category:Lists of industrial buildings", category=True)
        == "industrial buildings"
    )
    assert catalog._term("Category:Lists of industrial buildings") is None
    assert catalog._term("A botanical garden list") is None
    assert catalog._term("List of symbols ★ in books") is None


def test_strict_catalog_keeps_only_new_title_or_repeated_category_evidence() -> None:
    outputs, receipt = catalog.compile(
        Path("configs/p108_wiki_autotopic_catalog_v2.json")
    )
    rows = [json.loads(line) for line in outputs["ledger.jsonl"].splitlines()]
    selected = [row for row in rows if row["reject_reason"] is None]
    assert len(selected) == receipt["selected_terms"] == 81
    assert all(
        row["unseen_titles"] > 0 or row["source_categories"] >= 3 for row in selected
    )
    assert receipt["rejections"]["no_prior_novel_title_or_category"] > 0
    assert {row["split"] for row in selected} == {"train", "eval"}
    assert (
        sum(
            len(family["terms"])
            for family in json.loads(outputs["catalog.json"])["families"]
        )
        == 81
    )


def test_prior_union_rejects_same_title_across_pools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = {
        "schema": "longworld.source-batch-pool.v2",
        "sources": [
            {
                "name": "one",
                "split": "train",
                "snapshot": {"path": "one", "sha256": "a"},
            }
        ],
    }
    second = {
        "schema": "longworld.source-batch-pool.v2",
        "sources": [
            {"name": "two", "split": "eval", "snapshot": {"path": "two", "sha256": "b"}}
        ],
    }
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": prior.SCHEMA,
                "pools": [
                    {"path": "one", "sha256": "a"},
                    {"path": "two", "sha256": "b"},
                ],
            }
        )
    )
    monkeypatch.setattr(
        prior,
        "pinned_json",
        lambda _root, pin: first if pin["path"] == "one" else second,
    )
    monkeypatch.setattr(
        prior,
        "_snapshot",
        lambda _root, _pin: {
            "documents": [
                {"title": "List of bridges", "page_url": "https://example.org/bridges"}
            ]
        },
    )
    with pytest.raises(ValueError, match="title/URL repeats"):
        prior.build(config, tmp_path / "out")


def test_gate_bridge_replays_only_exact_upstream_names_order_and_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "bridge.json"
    config.write_text(
        json.dumps(
            {
                "schema": bridge.SCHEMA,
                "upstream_gate": {"path": "gate", "sha256": "gate-sha"},
                "source_pool": {"path": "pool", "sha256": "pool-sha"},
            }
        )
    )
    gate = {
        "schema": "longworld.p97-wiki-delta-gate.v1.result",
        "accepted_groups": ["first", "second"],
        "gated_source_pool_sha256": "pool-sha",
        "net_novel": {"groups": 2},
        "prior_router": {"path": "router", "sha256": "router-sha"},
    }
    pool = {
        "schema": "longworld.source-batch-pool.v2",
        "sources": [{"name": "first"}, {"name": "second"}],
    }
    monkeypatch.setattr(
        bridge,
        "pinned_json",
        lambda _root, pin: gate if pin["path"] == "gate" else pool,
    )
    output = tmp_path / "receipt.json"
    assert bridge.build(config, output)["accepted_groups"] == 2
    assert (
        bridge.build(config, output, verify_only=True)["prior_router"]
        == gate["prior_router"]
    )
    for change in (
        {"accepted_groups": ["second", "first"]},
        {"net_novel": {"groups": 3}},
        {"gated_source_pool_sha256": "tampered"},
        {"prior_router": {"path": "router", "sha256": ""}},
    ):
        bad = deepcopy(gate)
        bad.update(change)
        monkeypatch.setattr(
            bridge,
            "pinned_json",
            lambda _root, pin, bad=bad: bad if pin["path"] == "gate" else pool,
        )
        with pytest.raises(ValueError, match="upstream gate/pool identity"):
            bridge.build(config, tmp_path / "unused.json")


def test_inventory_includes_nested_campaign_intakes_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data/capability_records"
    nested = root / "campaign/intakes/shard_0000/attempt_0000"
    nested.mkdir(parents=True)
    (nested / "manifest.json").write_text(
        json.dumps({"schema": "longworld.p93-wiki-structural-intake.v1.result"})
    )
    monkeypatch.setattr(inventory, "ROOT", tmp_path)
    monkeypatch.setattr(
        inventory,
        "verify",
        lambda _path, _root: {"discovery": [{}], "source_groups": 1},
    )
    output = tmp_path / "inventory.json"
    receipt = inventory.freeze(root, output)
    assert receipt["frozen_intakes"] == 1
    assert receipt["intakes"][0]["path"].endswith(
        "campaign/intakes/shard_0000/attempt_0000/manifest.json"
    )
    assert inventory.freeze(root, output, verify_only=True) == receipt
