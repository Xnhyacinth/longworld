"""Source components must cross group IDs and detect alias-level split leaks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.p114_source_component_audit as source_audit
from scripts.p114_source_component_audit import (
    _check_book_source_components,
    author_key,
    components,
    page_key,
    revision_key,
)


def _book_record(group: str, split: str, *, ebook: int, work: str, author: str) -> dict:
    return {
        "source_group": group,
        "split": split,
        "ebook_id": ebook,
        "catalog_work_key": work,
        "raw_sha256": "raw-" + group,
        "body_sha256": "body-" + group,
        "catalog_author_keys": [author],
    }


def test_cross_manifest_book_source_identity_rejected_even_with_same_split() -> None:
    first = _book_record("book-a", "train", ebook=1, work="a|work", author="a|one")
    second = _book_record("book-b", "train", ebook=1, work="b|work", author="b|two")
    with pytest.raises(ValueError, match="duplicate book source identity"):
        _check_book_source_components([first, second])


def test_cross_manifest_duplicate_work_rejected_even_with_same_split() -> None:
    first = _book_record("book-a", "train", ebook=1, work="a|work", author="a|one")
    second = _book_record("book-b", "train", ebook=2, work="a|work", author="b|two")
    with pytest.raises(ValueError, match="duplicate book source identity"):
        _check_book_source_components([first, second])


@pytest.mark.parametrize("shared_field", ["catalog_work_key", "catalog_author_keys"])
def test_cross_manifest_book_work_or_author_split_rejected(shared_field: str) -> None:
    first = _book_record("book-a", "train", ebook=1, work="a|work", author="a|one")
    second = _book_record("book-b", "eval", ebook=2, work="b|work", author="b|two")
    second[shared_field] = first[shared_field]
    with pytest.raises(ValueError, match="work or author component crosses train/eval"):
        _check_book_source_components([first, second])


def test_same_author_same_split_is_allowed_for_distinct_books() -> None:
    first = _book_record("book-a", "train", ebook=1, work="a|work", author="a|one")
    second = _book_record("book-b", "train", ebook=2, work="a|other", author="a|one")
    _check_book_source_components([first, second])


def test_joint_answer_source_binding_uses_both_pinned_parent_tasks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(source_audit, "verify_merge", lambda _: None)
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    base = {
        "source_kind": "real_finance",
        "source_group": "issuer-1",
        "split": "train",
        "domain": "finance",
        "topic": "issuer",
        "context_sha256": "context-sha",
    }
    parents = [
        {
            **base,
            "sample_id": f"parent-{i}",
            "semantic_task_id": f"task-{i}",
            "operation": f"operation-{i}",
            "answer_sha256": f"answer-{i}",
            "dependency_status": f"bounded-{i}",
        }
        for i in range(2)
    ]
    source_index = materialized / "sample_index.jsonl"
    source_index.write_text(
        "".join(json.dumps({"candidate": parent}) + "\n" for parent in parents)
    )
    for name in ("train.jsonl", "eval.jsonl"):
        (materialized / name).write_text("")
    source_manifest = materialized / "manifest.json"
    source_manifest.write_text(
        json.dumps(
            {
                "schema_version": "longworld.p95-balanced-materialized-candidates.v1",
                "selected_views": 2,
                "files_sha256": {
                    name: source_audit.sha(materialized / name)
                    for name in ("sample_index.jsonl", "train.jsonl", "eval.jsonl")
                },
            }
        )
    )
    derived = {
        **base,
        "sample_id": "joint-1",
        "operation": "joint_multi_operation_answer",
        "source_name": "p125_joint_multi_operation",
        "native_row_ref": str(source_index) + ":parent-0+parent-1",
        "receipt_sha256": source_audit.sha(source_manifest),
    }
    joint = tmp_path / "joint"
    joint.mkdir()
    (joint / "sample_index.jsonl").write_text(json.dumps(derived) + "\n")
    lineage = {
        "sample_id": "joint-1",
        "context_sha256": "context-sha",
        "component_sample_ids": ["parent-0", "parent-1"],
        "component_semantic_task_ids": ["task-0", "task-1"],
        "component_operations": ["operation-0", "operation-1"],
        "answer_projection_hashes": ["answer-0", "answer-1"],
        "component_dependency_status": ["bounded-0", "bounded-1"],
    }
    (joint / "pair_lineage.jsonl").write_text(json.dumps(lineage) + "\n")
    for name in ("candidate_train.jsonl", "candidate_eval.jsonl"):
        (joint / name).write_text("")
    joint_manifest = joint / "manifest.json"
    joint_receipt = {
        "p125_schema": "longworld.p125-joint-task-compiler.v1",
        "source_materialized_manifest_sha256": source_audit.sha(source_manifest),
        "files_sha256": {
            name: source_audit.sha(joint / name)
            for name in (
                "sample_index.jsonl",
                "pair_lineage.jsonl",
                "candidate_train.jsonl",
                "candidate_eval.jsonl",
            )
        },
    }
    joint_manifest.write_text(json.dumps(joint_receipt))
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    index = {
        "shards": [
            {
                "name": "joint",
                "path": "../joint",
                "manifest_sha256": source_audit.sha(joint_manifest),
            }
        ]
    }
    selection = [{"shard": "joint", "candidate": derived}]
    rows, report = source_audit._source_support_rows(index_dir, index, selection)
    assert rows == parents
    assert report["selected_joint_views"] == 1
    assert report["source_support_rows"] == 2
    with pytest.raises(ValueError, match="source and operation tags disagree"):
        source_audit._source_support_rows(
            index_dir,
            index,
            [{"shard": "joint", "candidate": {**derived, "operation": "lookup"}}],
        )
    with pytest.raises(ValueError, match="shard, source and operation tags disagree"):
        source_audit._source_support_rows(
            index_dir,
            index,
            [
                {
                    "shard": "joint",
                    "candidate": {
                        **derived,
                        "operation": "lookup",
                        "source_name": "ordinary",
                    },
                }
            ],
        )
    unpinned = dict(joint_receipt)
    unpinned["files_sha256"] = {
        name: digest
        for name, digest in joint_receipt["files_sha256"].items()
        if name != "pair_lineage.jsonl"
    }
    joint_manifest.write_text(json.dumps(unpinned))
    index["shards"][0]["manifest_sha256"] = source_audit.sha(joint_manifest)
    with pytest.raises(ValueError, match="lacks pinned lineage"):
        source_audit._source_support_rows(index_dir, index, selection)
    joint_manifest.write_text(json.dumps(joint_receipt))
    index["shards"][0]["manifest_sha256"] = source_audit.sha(joint_manifest)
    lineage["answer_projection_hashes"][1] = "wrong"
    (joint / "pair_lineage.jsonl").write_text(json.dumps(lineage) + "\n")
    with pytest.raises(ValueError, match="parent source binding differs"):
        source_audit._source_support_rows(index_dir, index, selection)


def test_author_alias_connects_distinct_book_groups_across_split() -> None:
    assert author_key("Austen, Jane") == author_key("Jane Austen")
    graph = components(
        [
            {
                "group": "gutenberg-1",
                "split": "train",
                "identities": ["book:author:" + author_key("Austen, Jane")],
            },
            {
                "group": "gutenberg-2",
                "split": "eval",
                "identities": ["book:author:" + author_key("Jane Austen")],
            },
        ]
    )
    assert graph["components"] == 1
    assert graph["conflicts"][0]["groups"] == ["gutenberg-1", "gutenberg-2"]


def test_same_wiki_page_across_revisions_connects_source_groups() -> None:
    first = page_key("https://en.wikipedia.org/wiki/List%5Fof%5Fbridges")
    second = page_key("https://en.wikipedia.org/wiki/list_of_bridges")
    assert first == second
    assert revision_key(
        first, "https://en.wikipedia.org/w/index.php?oldid=123"
    ) != revision_key(second, "https://en.wikipedia.org/w/index.php?oldid=456")
    graph = components(
        [
            {"group": "snapshot-a", "split": "train", "identities": [first]},
            {"group": "snapshot-b", "split": "eval", "identities": [second]},
            {
                "group": "snapshot-c",
                "split": "eval",
                "identities": ["wiki:page:unrelated"],
            },
        ]
    )
    assert graph["components"] == 2
    assert graph["conflicts"][0]["groups"] == ["snapshot-a", "snapshot-b"]


def test_extra_wiki_source_pool_accepts_workspace_relative_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(source_audit, "ROOT", tmp_path)
    monkeypatch.setattr(source_audit, "WIKI_POOLS", ())
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"snapshot_id": "group", "documents": []}))
    pool = tmp_path / "pool.json"
    pool.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "name": "group",
                        "domain": "nature",
                        "topic": "parks",
                        "split": "train",
                        "snapshot": {
                            "path": "snapshot.json",
                            "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                        },
                    }
                ]
            }
        )
    )
    found = source_audit._wiki_registry(
        {"group": ("nature", "parks", "train")}, [Path("pool.json")]
    )
    assert found["group"][0]["pool_path"] == "pool.json"


def test_paper_audit_uses_archive_bytes_and_work_identity(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(source_audit, "ROOT", tmp_path)
    archive = tmp_path / "source/arxiv-1234.5678v2.source.tar"
    archive.parent.mkdir()
    archive.write_bytes(b"frozen paper source")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    native = tmp_path / "native"
    native.mkdir()
    audit = {
        "sample_id": "paper-task",
        "source_archive": {
            "path": str(archive.relative_to(tmp_path)),
            "sha256": digest,
            "version": "v2",
        },
    }
    index = {
        "sample_id": "paper-task",
        "source_group": "researchlab:arxiv:1234.5678",
        "split": "train",
    }
    (native / "audit.jsonl").write_text(json.dumps(audit) + "\n")
    (native / "sample_index.jsonl").write_text(json.dumps(index) + "\n")
    (native / "manifest.json").write_text(
        json.dumps(
            {
                "files_sha256": {
                    name: source_audit.sha(native / name)
                    for name in ("audit.jsonl", "sample_index.jsonl")
                }
            }
        )
    )
    rows = [{**index, "native_row_ref": "native/train.jsonl:0"}]
    groups, _ = source_audit._paper_groups(rows)
    assert "paper:work:arxiv:1234.5678" in groups[0]["identities"]
    archive.write_bytes(b"changed")
    with pytest.raises(ValueError, match="archive pin"):
        source_audit._paper_groups(rows)


def test_shared_rfc_filler_connects_rules_across_split() -> None:
    graph = components(
        [
            {
                "group": "rfc9114-world",
                "split": "train",
                "identities": ["rfc:text_sha256:shared-rfc9000-filler"],
            },
            {
                "group": "rfc9000-world",
                "split": "eval",
                "identities": ["rfc:text_sha256:shared-rfc9000-filler"],
            },
        ]
    )
    assert graph["conflicts"][0]["groups"] == [
        "rfc9000-world",
        "rfc9114-world",
    ]


def test_finance_native_receipt_binds_issuer_and_filing_bytes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(source_audit, "ROOT", tmp_path)
    source = tmp_path / "source/manifest.json"
    source.parent.mkdir()
    source.write_text(
        json.dumps(
            {
                "issuer": {"cik": "0000000001"},
                "records": [
                    {
                        "cik": "0000000001",
                        "source_url": "https://example.test/filing",
                        "raw_text": "frozen annual filing",
                    }
                ],
            }
        )
    )
    native = tmp_path / "native"
    native.mkdir()
    index = {"sample_id": "finance-task"}
    (native / "sample_index.jsonl").write_text(json.dumps(index) + "\n")
    (native / "manifest.json").write_text(
        json.dumps(
            {
                "file_sha256": {
                    "sample_index.jsonl": source_audit.sha(
                        native / "sample_index.jsonl"
                    )
                },
                "source_group": "0000000001",
                "split": "train",
                "source_manifest": {
                    "path": "source/manifest.json",
                    "sha256": source_audit.sha(source),
                },
            }
        )
    )
    row = {
        "sample_id": "finance-task",
        "source_group": "0000000001",
        "split": "train",
        "native_row_ref": "native/reader.jsonl:0",
    }
    groups, _ = source_audit._finance_groups([row])
    assert "finance:issuer_cik:0000000001" in groups[0]["identities"]
    source.write_text(source.read_text().replace("frozen", "changed"))
    with pytest.raises(ValueError, match="source manifest pin"):
        source_audit._finance_groups([row])


def test_invalid_article_revision_and_duplicate_group_rejected() -> None:
    with pytest.raises(ValueError, match="canonical enwiki"):
        page_key("https://example.com/wiki/List_of_bridges")
    with pytest.raises(ValueError, match="oldid"):
        revision_key("wiki:page:x", "https://en.wikipedia.org/wiki/X")
    with pytest.raises(ValueError, match="repeats"):
        components(
            [
                {"group": "same", "split": "train", "identities": []},
                {"group": "same", "split": "eval", "identities": []},
            ]
        )


def test_code_source_component_requires_raw_source_pin(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(source_audit, "ROOT", tmp_path)
    config = tmp_path / "configs/p99_code_content_v1.json"
    config.parent.mkdir()
    for name in ("p107_code_oxc_expansion_v1", "p108_code_content_v2"):
        (config.parent / f"{name}.json").write_text('{"banks": []}')
    group = "https://github.com/example/repo"
    raw = tmp_path / "source/pr.json"
    raw.parent.mkdir()
    raw.write_text("frozen pull request")
    bank = tmp_path / "bank"
    bank.mkdir()
    world = {"source_group_id": group, "split": "train", "world_instance_id": "world-a"}
    (bank / "world.json").write_text(json.dumps(world))
    receipt = {
        "source_group_id": group,
        "split": "train",
        "files": {"world.json": source_audit.sha(bank / "world.json")},
        "source_bindings": [{"path": str(raw), "sha256": source_audit.sha(raw)}],
    }
    (bank / "BUILD_RECEIPT.json").write_text(json.dumps(receipt))
    config.write_text(json.dumps({"banks": [{
        "bank_root": "bank", "source_group_id": group, "split": "train",
        "receipt_sha256": source_audit.sha(bank / "BUILD_RECEIPT.json"),
    }]}))
    native = tmp_path / "native"
    native.mkdir()
    (native / "sample_index.jsonl").write_text(json.dumps({
        "sample_id": "task-a", "source_group": group, "split": "train",
    }) + "\n")
    (native / "train.jsonl").write_text(json.dumps({"example_id": "task-a"}) + "\n")
    (native / "manifest.json").write_text(json.dumps({
        "config_sha256": source_audit.sha(config),
        "files_sha256": {name: source_audit.sha(native / name) for name in (
            "sample_index.jsonl", "train.jsonl"
        )},
    }))
    row = {
        "sample_id": "task-a", "source_group": group, "split": "train",
        "source_name": "p99_code_content",
        "native_row_ref": str(native / "train.jsonl") + ":0",
    }
    groups, _ = source_audit._code_groups([row])
    assert groups[0]["identities"] == ["code:repository:" + group]
    raw.write_text("changed pull request")
    with pytest.raises(ValueError, match="raw source binding pin"):
        source_audit._code_groups([row])


def test_simulation_content_alias_reveals_cross_split_component(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(source_audit, "ROOT", tmp_path)
    factor = tmp_path / "data/candidates/p112_world_factor_campaign_v2"
    factor.mkdir(parents=True)
    (factor / "sample_index.jsonl").write_text("")
    (factor / "manifest.json").write_text(json.dumps({
        "files_sha256": {"sample_index.jsonl": source_audit.sha(factor / "sample_index.jsonl")}
    }))
    selected = []
    for group, split in (("world-a", "train"), ("world-b", "eval")):
        shard = tmp_path / group
        shard.mkdir()
        (shard / "world.json").write_text(json.dumps({
            "world_id": group, "reader_context": "identical source world",
            "context_sha256": hashlib.sha256(b"identical source world").hexdigest(),
        }))
        (shard / "rows.jsonl").write_text(json.dumps({
            "example_id": group + ":q0", "world_id": group,
            "source_group": group, "split": split,
        }) + "\n")
        (shard / "receipt.json").write_text(json.dumps({
            "world_id": group, "world_sha256": source_audit.sha(shard / "world.json"),
            "rows_sha256": source_audit.sha(shard / "rows.jsonl"),
        }))
        selected.append({
            "sample_id": group + ":q0", "source_group": group, "split": split,
            "source_name": "state_p86",
            "native_row_ref": str(shard / "rows.jsonl") + ":0",
        })
    groups, _ = source_audit._simulation_groups(selected)
    assert components(groups)["conflicts"][0]["groups"] == ["world-a", "world-b"]
