"""P97 title and URL overlap checks reject whole repeated source bundles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import audit_p97_wiki_intake_overlap as audit
from scripts import gate_p97_wiki_delta as gate


def _source(root: Path, name: str, split: str, title: str, url: str) -> dict:
    path = root / f"{name}.json"
    path.write_text(
        json.dumps({"documents": [{"title": title, "page_url": url}], "facts": [{}]})
    )
    return {
        "name": name,
        "split": split,
        "snapshot": {"path": path.name, "sha256": audit.sha(path)},
    }


def test_cross_split_title_and_url_only_overlap_are_not_novel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.setattr(
        audit,
        "_snapshot",
        lambda root, pin: json.loads((root / pin["path"]).read_text()),
    )
    monkeypatch.setattr(
        audit,
        "verify_intake",
        lambda _dir, _root: {
            "source_groups": 3,
            "frozen_pages": 3,
            "frozen_facts": 3,
            "http_requests": 7,
            "failures": [],
        },
    )
    prior = {
        "schema": "longworld.source-batch-pool.v2",
        "sources": [_source(tmp_path, "old", "train", "Old title", "https://wiki/old")],
    }
    prior_path = tmp_path / "prior.json"
    prior_path.write_text(json.dumps(prior))
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "manifest.json").write_text("{}")
    sources = [
        _source(tmp_path, "same_title", "eval", "Old title", "https://wiki/other"),
        _source(tmp_path, "same_url", "eval", "Different title", "https://wiki/old"),
        _source(tmp_path, "novel", "eval", "Novel title", "https://wiki/novel"),
    ]
    (intake / "source_pool.json").write_text(
        json.dumps({"schema": prior["schema"], "sources": sources})
    )
    result = audit.report(Path("prior.json"), [Path("intake")])
    assert result["gross"] == {"facts": 3, "groups": 3, "pages": 3}
    assert result["net_novel"] == {"facts": 1, "groups": 1, "pages": 1}
    assert result["accepted_groups"] == ["novel"]
    assert result["cross_split_overlap_groups"] == 2
    assert result["url_only_overlap_groups"] == 1
    router_source = _source(
        tmp_path, "router_page", "train", "Router title", "https://wiki/router"
    )
    router = {
        "schema": "longworld.p92-source-router.v1.result",
        "sources": [
            {
                "source_kind": "real_wiki",
                "source_identity": f"router_{index}",
                "split": "train",
                "source_pins": [router_source["snapshot"]],
            }
            for index in range(2)
        ],
    }
    router_path = tmp_path / "router.json"
    router_path.write_text(json.dumps(router))
    with_router = audit.report(
        Path("prior.json"), [Path("intake")], prior_router=Path("router.json")
    )
    assert with_router["prior_router"]["unique_titles"] == 1
    assert with_router["prior_router"]["unique_urls"] == 1
    router["sources"][1]["split"] = "eval"
    router_path.write_text(json.dumps(router))
    with pytest.raises(ValueError, match="conflicting identity or split"):
        audit.report(
            Path("prior.json"), [Path("intake")], prior_router=Path("router.json")
        )
    router["sources"][1]["split"] = "train"
    router_path.write_text(json.dumps(router))
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(
        gate,
        "_snapshot",
        lambda root, pin: json.loads((root / pin["path"]).read_text()),
    )
    delta_path = tmp_path / "delta.json"
    delta_path.write_text(
        json.dumps({"schema": prior["schema"], "sources": [sources[2]]})
    )
    _, gate_result = gate.build(
        Path("prior.json"),
        [Path("intake")],
        Path("delta.json"),
        prior_router=Path("router.json"),
    )
    assert gate_result["accepted_groups"] == ["novel"]
    delta_path.write_text(
        json.dumps({"schema": prior["schema"], "sources": [sources[1], sources[2]]})
    )
    with pytest.raises(ValueError, match="delta source records differ"):
        gate.build(Path("prior.json"), [Path("intake")], Path("delta.json"))
    changed_record = {**sources[2], "split": "train"}
    delta_path.write_text(
        json.dumps({"schema": prior["schema"], "sources": [changed_record]})
    )
    with pytest.raises(ValueError, match="delta source records differ"):
        gate.build(Path("prior.json"), [Path("intake")], Path("delta.json"))
    duplicate_prior = _source(
        tmp_path, "duplicate_prior", "eval", "Another title", "https://wiki/old"
    )
    prior["sources"].append(duplicate_prior)
    prior_path.write_text(json.dumps(prior))
    with pytest.raises(ValueError, match="prior pool repeats"):
        audit.report(Path("prior.json"), [Path("intake")])
    prior["sources"].pop()
    prior_path.write_text(json.dumps(prior))
    novel_path = tmp_path / "novel.json"
    novel = json.loads(novel_path.read_text())
    novel["documents"].append(
        {"title": "Another novel", "page_url": "https://wiki/novel"}
    )
    novel_path.write_text(json.dumps(novel))
    with pytest.raises(ValueError, match="new snapshot repeats"):
        audit.report(Path("prior.json"), [Path("intake")])


def test_p92_router_pages_are_excluded_from_new_delta(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.setattr(
        audit,
        "_snapshot",
        lambda root, pin: json.loads((root / pin["path"]).read_text()),
    )
    monkeypatch.setattr(
        audit,
        "verify_intake",
        lambda _dir, _root: {
            "source_groups": 1,
            "frozen_pages": 1,
            "frozen_facts": 1,
            "http_requests": 1,
            "failures": [],
        },
    )
    prior = tmp_path / "prior.json"
    prior.write_text(
        json.dumps({"schema": "longworld.source-batch-pool.v2", "sources": []})
    )
    router_source = _source(
        tmp_path, "router_snapshot", "train", "Historic page", "https://wiki/historic"
    )
    router = tmp_path / "router.json"
    router.write_text(
        json.dumps(
            {
                "schema": "longworld.p92-source-router.v1.result",
                "sources": [
                    {
                        "source_kind": "real_wiki",
                        "source_identity": "historic",
                        "split": "train",
                        "source_pins": [router_source["snapshot"]],
                    }
                ],
            }
        )
    )
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "manifest.json").write_text("{}")
    repeated = _source(
        tmp_path, "new", "eval", "Historic page", "https://wiki/historic"
    )
    (intake / "source_pool.json").write_text(
        json.dumps({"schema": "longworld.source-batch-pool.v2", "sources": [repeated]})
    )
    without_router = audit.report(prior, [intake])
    assert without_router["accepted_groups"] == ["new"]
    with_router = audit.report(prior, [intake], prior_router=router)
    assert with_router["accepted_groups"] == []
    assert with_router["cross_split_overlap_groups"] == 1
