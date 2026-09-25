"""P97 vocabulary sharding preserves P93 bounds, splits and source identities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.synthesis.p93_wiki_structural_intake import validate as validate_p93
from scripts.plan_p97_wiki_intake_shards import plan, run

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "configs/p97_wiki_intake_catalog_v1.json"


def test_real_144_term_catalog_emits_valid_distinct_p93_shards(tmp_path: Path) -> None:
    outputs, manifest = plan(CATALOG)
    assert manifest["queries"] == manifest["topics"] == 144
    assert manifest["source_families"] == 18
    assert manifest["generated_families"] == 72
    assert manifest["potential_source_names"] == 288
    assert manifest["split_queries"] == {"eval": 32, "train": 112}
    assert len(manifest["shards"]) == 4
    names = set()
    for shard in manifest["shards"]:
        config = json.loads(outputs[shard["config_path"]])
        validate_p93(config)
        assert shard["queries"] == 36
        assert shard["queries"] <= 40
        assert shard["families"] == 18
        assert shard["families"] <= 24
        assert shard["split_queries"] == {"eval": 8, "train": 28}
        assert all(1 <= len(family["terms"]) <= 20 for family in config["families"])
        for family in config["families"]:
            assert family["name"] not in names
            names.add(family["name"])
    sources = [
        json.loads(line)["source_name"]
        for line in outputs["potential_source_names.jsonl"].splitlines()
    ]
    assert len(sources) == len(set(sources))
    output = tmp_path / "plan"
    assert run(CATALOG, output) == manifest
    assert run(CATALOG, output, verify_only=True) == manifest
    changed = output / "shards/shard_0001.json"
    changed.write_text(changed.read_text() + "\n")
    with pytest.raises(ValueError, match="replay drift"):
        run(CATALOG, output, verify_only=True)


def _family(name: str, split: str, terms: list[str]) -> dict:
    return {
        "name": name,
        "domain": "test",
        "split": split,
        "mode": "search",
        "query_template": 'intitle:"List of {term}"',
        "terms": terms,
        "max_results_per_term": 12,
        "bundle_size": 4,
        "max_bundles_per_term": 2,
    }


def test_long_single_family_is_split_without_exceeding_twenty_terms(
    tmp_path: Path,
) -> None:
    catalog = json.loads(CATALOG.read_text())
    catalog["families"] = [
        _family("train_subjects", "train", [f"train category {i}" for i in range(100)]),
        _family("eval_subjects", "eval", [f"eval category {i}" for i in range(30)]),
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    outputs, manifest = plan(path)
    assert manifest["queries"] == 130
    assert len(manifest["shards"]) == 5
    assert all(row["queries"] <= 40 for row in manifest["shards"])
    for shard in manifest["shards"]:
        validate_p93(json.loads(outputs[shard["config_path"]]))


def test_cross_split_duplicate_term_and_insufficient_eval_coverage_reject(
    tmp_path: Path,
) -> None:
    catalog = json.loads(CATALOG.read_text())
    catalog["families"] = [
        _family("train_subjects", "train", ["duplicated term"]),
        _family("eval_subjects", "eval", ["Duplicated Term"]),
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match="duplicate discovery term"):
        plan(path)
    catalog["families"] = [
        _family("train_subjects", "train", [f"train topic {i}" for i in range(81)]),
        _family("eval_subjects", "eval", ["one eval topic"]),
    ]
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match="cannot fit"):
        plan(path)


def test_fifty_distinct_families_shard_within_p93_family_bound(tmp_path: Path) -> None:
    catalog = json.loads(CATALOG.read_text())
    catalog["families"] = [
        _family(
            f"subject_{index:03d}",
            "eval" if index % 6 == 0 else "train",
            [f"distinct subject {index}"],
        )
        for index in range(50)
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    outputs, manifest = plan(path)
    assert manifest["queries"] == 50
    assert len(manifest["shards"]) == 3
    for shard in manifest["shards"]:
        assert shard["families"] <= 24
        assert shard["queries"] <= 40
        validate_p93(json.loads(outputs[shard["config_path"]]))


def test_exact_fallback_recovers_feasible_family_capacity_split(tmp_path: Path) -> None:
    catalog = json.loads(CATALOG.read_text())
    catalog.update(
        max_queries_per_shard=3,
        max_families_per_shard=2,
        max_terms_per_family=2,
    )
    catalog["families"] = [
        _family("train_a", "train", ["alpha one", "alpha two"]),
        _family("train_b", "train", ["beta one"]),
        _family("eval_c", "eval", ["gamma one", "gamma two"]),
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    outputs, manifest = plan(path)
    assert len(manifest["shards"]) == 2
    for shard in manifest["shards"]:
        config = json.loads(outputs[shard["config_path"]])
        validate_p93(config)
        assert len(config["families"]) == 2
        assert shard["split_queries"]["eval"] == 1
    train_families = [
        family
        for shard in manifest["shards"]
        for family in json.loads(outputs[shard["config_path"]])["families"]
        if family["domain"] == "test"
        and family["name"]
        not in json.loads(outputs[shard["config_path"]])["eval_families"]
    ]
    assert sorted(len(family["terms"]) for family in train_families) == [1, 2]
