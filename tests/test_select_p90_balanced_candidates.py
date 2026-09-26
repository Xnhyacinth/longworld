"""Behavioral checks for source-aware candidate reference balancing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import select_p90_balanced_candidates as balanced


def _entry(
    sample: str,
    *,
    group: str,
    task: str,
    split: str = "train",
    operation: str = "join",
    length: str = "lt32k",
) -> dict:
    full_tokens = {"lt32k": 110, "32k": 32770, "64k": 65540, "128k": 131080}[length]
    return {
        "shard": "toy",
        "candidate": {
            "sample_id": sample,
            "source_kind": "real_wiki",
            "domain": "nature",
            "topic": "parks",
            "source_group": group,
            "semantic_task_id": task,
            "split": split,
            "operation": operation,
            "length_bin": length,
            "input_tokens": full_tokens - 10,
            "supervised_tokens": 10,
            "full_chat_tokens": full_tokens,
        },
    }


def _index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict]
) -> Path:
    path = tmp_path / "index"
    path.mkdir()
    raw = "".join(json.dumps(entry) + "\n" for entry in entries)
    (path / "candidate_refs.jsonl").write_text(raw)
    (path / "manifest.json").write_text('{"train_ready":false}\n')
    digest = hashlib.sha256(raw.encode()).hexdigest()
    monkeypatch.setattr(
        balanced,
        "verify_index",
        lambda _: {"candidate_views": len(entries), "refs_sha256": digest},
    )
    return path


def test_balances_cells_and_groups_without_repeating_semantic_task() -> None:
    entries = [
        _entry("a", group="big", task="one"),
        _entry("a-64", group="big", task="one", length="64k"),
        _entry("b", group="big", task="two"),
        _entry("c", group="small", task="three"),
        _entry("d", group="other", task="four", operation="aggregate"),
        _entry("e", group="eval", task="five", split="eval"),
    ]
    selected = balanced._choose(
        entries,
        seed=90,
        max_per_group=1,
        max_per_cell=2,
        max_per_kind_by_split={"train": 3, "eval": 1},
    )
    assert len(selected) == 4
    assert len({balanced._group(entry) for entry in selected}) == 4
    assert len({balanced._task(entry) for entry in selected}) == 4
    assert {balanced._cell(entry)[0] for entry in selected} == {"train", "eval"}
    assert {balanced._cell(entry)[2] for entry in selected} == {"join", "aggregate"}


def test_rejects_source_group_crossing_train_eval() -> None:
    entries = [
        _entry("train", group="shared", task="one"),
        _entry("eval", group="shared", task="two", split="eval"),
    ]
    with pytest.raises(ValueError, match="source group crosses train/eval split"):
        balanced._choose(
            entries,
            seed=90,
            max_per_group=2,
            max_per_cell=2,
            max_per_kind_by_split={"train": 2, "eval": 2},
        )


def test_rejects_declared_length_that_differs_from_final_chat_tokens() -> None:
    entry = _entry("wrong", group="one", task="one")
    entry["candidate"]["length_bin"] = "64k"
    with pytest.raises(ValueError, match="physical length bin changed"):
        balanced._choose(
            [entry],
            seed=90,
            max_per_group=2,
            max_per_cell=2,
            max_per_kind_by_split={"train": 2, "eval": 2},
        )


def test_kind_split_cap_preserves_rare_operation_cell() -> None:
    entries = [
        _entry("common-1", group="one", task="one"),
        _entry("common-2", group="two", task="two"),
        _entry("rare", group="three", task="three", operation="aggregate"),
    ]
    selected = balanced._choose(
        entries,
        seed=90,
        max_per_group=2,
        max_per_cell=4,
        max_per_kind_by_split={"train": 2, "eval": 2},
    )
    assert len(selected) == 2
    assert {balanced._cell(entry)[2] for entry in selected} == {"join", "aggregate"}


def test_supervised_token_cap_limits_a_dominant_kind_without_dropping_other_kinds() -> (
    None
):
    entries = [
        _entry("wiki-1", group="one", task="one"),
        _entry("wiki-2", group="two", task="two"),
        _entry("wiki-3", group="three", task="three"),
        _entry("finance", group="four", task="four"),
    ]
    entries[-1]["candidate"]["source_kind"] = "real_finance"
    selected = balanced._choose(
        entries,
        seed=90,
        max_per_group=2,
        max_per_cell=4,
        max_per_kind_by_split={"train": 4, "eval": 4},
        max_supervised_tokens_by_kind={"real_wiki": 15},
    )
    assert len(selected) == 2
    assert balanced._coverage(selected)["by_source_kind"] == {
        "real_finance": 1,
        "real_wiki": 1,
    }
    assert (
        balanced._coverage(selected)["by_source_kind_tokens"]["real_wiki"][
            "supervised_tokens"
        ]
        == 10
    )


def test_select_replays_exact_bytes_and_detects_selected_ref_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [
        _entry("a", group="one", task="a"),
        _entry("b", group="two", task="b", length="64k"),
        _entry("c", group="three", task="c", split="eval"),
    ]
    index = _index(tmp_path, monkeypatch, entries)
    kwargs = {
        "seed": 90,
        "max_per_group": 1,
        "max_per_cell": 2,
        "max_per_kind_by_split": {"train": 2, "eval": 1},
    }
    first = tmp_path / "first"
    second = tmp_path / "second"
    report = balanced.select(index, first, **kwargs)
    assert report == balanced.select(index, second, **kwargs)
    assert (first / "selected_refs.jsonl").read_bytes() == (
        second / "selected_refs.jsonl"
    ).read_bytes()
    assert balanced.verify_selection(index, first, **kwargs) == report
    assert report["train_ready"] is False
    assert report["after"]["input_tokens"] == 65730
    assert report["after"]["supervised_tokens"] == 30
    assert report["after"]["by_domain"] == {"nature": 3}
    assert report["after"]["by_source_kind_groups"] == {"real_wiki": 3}
    (first / "selected_refs.jsonl").write_text("changed\n")
    with pytest.raises(ValueError, match="selected references changed"):
        balanced.verify_selection(index, first, **kwargs)


def test_optional_shared_world_rebalance_completes_second_operation_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _entry("first-join", group="first", task="join")
    second = _entry(
        "first-aggregate", group="first", task="aggregate", operation="aggregate"
    )
    donor_id = min(
        (f"donor-{number}" for number in range(100)),
        key=lambda sample: balanced._tie(
            _entry(sample, group="donor", task="donor", operation="aggregate"), 94
        ),
    )
    donor = _entry(donor_id, group="donor", task="donor", operation="aggregate")
    # The donor wins the aggregate cell's original tie, leaving the first
    # group with one selected operation and one proof-eligible completion.
    assert balanced._tie(donor, 94) < balanced._tie(second, 94)
    index = _index(tmp_path, monkeypatch, [first, second, donor])
    kwargs = {
        "seed": 94,
        "max_per_group": 2,
        "max_per_cell": 1,
        "max_per_kind_by_split": {"train": 2, "eval": 1},
    }
    baseline = balanced.select(index, tmp_path / "baseline", **kwargs)
    policy = {"max_source_group_loss": 1}
    report = balanced.select(
        index,
        tmp_path / "shared",
        **kwargs,
        shared_world_rebalance=policy,
    )
    arm = report["shared_world_rebalance"]
    assert baseline["after"]["source_groups"] == 2
    assert report["after"]["source_groups"] == 1
    assert arm["before_multiop_by_kind"] == {}
    assert arm["after_multiop_by_kind"] == {"real_wiki": 1}
    assert (arm["before_multiop_groups"], arm["after_multiop_groups"]) == (0, 1)
    assert arm["before_rebalance"] == baseline["after"]
    assert arm["swaps"] == [
        {
            "from_sample_id": donor_id,
            "to_sample_id": "first-aggregate",
            "cell": "train|real_wiki|aggregate|lt32k",
            "source_group": "first",
        }
    ]
    assert all(arm["histogram_invariants"].values())
    assert report["after"]["by_cell"] == baseline["after"]["by_cell"]
    assert (
        balanced.verify_selection(
            index,
            tmp_path / "shared",
            **kwargs,
            shared_world_rebalance=policy,
        )
        == report
    )
    with pytest.raises(ValueError, match="selection differs"):
        balanced.verify_selection(index, tmp_path / "shared", **kwargs)


def test_shared_world_rebalance_respects_group_and_token_caps() -> None:
    first = _entry("first-join", group="first", task="join")
    second = _entry(
        "first-aggregate", group="first", task="aggregate", operation="aggregate"
    )
    second["candidate"]["supervised_tokens"] = 11
    second["candidate"]["input_tokens"] = 99
    donor = _entry("donor", group="donor", task="donor", operation="aggregate")
    eligible = [first, second, donor]
    baseline = [first, donor]
    for group_cap, token_cap, group_loss in ((1, 100, 1), (2, 20, 1), (2, 100, 0)):
        selected, receipt = balanced._rebalance_shared_world(
            eligible,
            baseline,
            seed=94,
            max_per_group=group_cap,
            max_per_cell=1,
            max_per_kind_by_split={"train": 2, "eval": 1},
            max_supervised_tokens_by_kind={"real_wiki": token_cap},
            max_source_group_loss=group_loss,
        )
        assert selected == baseline
        assert receipt["swaps"] == []


def test_pinned_codeforge_proof_gate_keeps_only_content_backed_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = [
        _entry(name, group=name, task=name)
        for name in ("certified", "failed", "uncovered")
    ]
    for entry in code:
        entry["candidate"]["source_kind"] = "real_code_workflow"
    wiki = _entry("wiki", group="wiki", task="wiki")
    index = _index(tmp_path, monkeypatch, [*code, wiki])
    proofs = []
    for name, positive in (("certified", True), ("failed", False)):
        proofs.append(
            {
                "source_group_id": name,
                "semantic_task_id": name,
                "sample_id": name,
                "split": "train",
                "full_message_tokens": 110,
                "classification": (
                    "scoped_long_input_file_aggregation_certificate"
                    if positive
                    else "outside_filename_copy_grammar"
                ),
                "scoped_long_input_certificate": positive,
                "content_backed_scoped_certificate": positive,
            }
        )
    proof_path = tmp_path / "proofs.jsonl"
    proof_path.write_text("".join(json.dumps(row) + "\n" for row in proofs))
    proof_hash = hashlib.sha256(proof_path.read_bytes()).hexdigest()
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.codeforge-reading-proof-build.v2",
                "profile_id": "p65-codeforge-filename-copy-content-backed-v1",
                "files": {"proofs.jsonl": proof_hash},
                "primary_rows": 2,
                "qualified_existing_semantic_tasks": 1,
            }
        )
    )
    monkeypatch.setattr(balanced, "ROOT", tmp_path)
    pins = [
        {
            "receipt": {
                "path": "receipt.json",
                "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            },
            "proofs": {"path": "proofs.jsonl", "sha256": proof_hash},
        }
    ]
    kwargs = {
        "seed": 95,
        "max_per_group": 2,
        "max_per_cell": 4,
        "max_per_kind_by_split": {"train": 4, "eval": 4},
        "codeforge_proofs": pins,
    }
    report = balanced.select(index, tmp_path / "selection", **kwargs)
    assert report["quality_gate"]["counts"] == {
        "code_views": 3,
        "content_backed_views": 1,
        "excluded_failed_proof": 1,
        "excluded_without_proof": 1,
        "proof_identity_match_views": 2,
        "proof_record_views": 2,
    }
    assert report["eligible"]["views"] == 2
    assert report["after"]["by_source_kind"] == {
        "real_code_workflow": 1,
        "real_wiki": 1,
    }
    assert balanced.verify_selection(index, tmp_path / "selection", **kwargs) == report
    proof_path.write_text(proof_path.read_text() + "{}\n")
    with pytest.raises(ValueError, match="CodeForge proof pin mismatch"):
        balanced.verify_selection(index, tmp_path / "selection", **kwargs)


def test_codeforge_proof_scope_admits_new_tasks_without_duplicate_old_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(balanced, "ROOT", tmp_path)

    def proof(name: str) -> dict:
        return {
            "source_group_id": "repo",
            "semantic_task_id": name,
            "sample_id": name,
            "split": "train",
            "full_message_tokens": 110,
            "classification": "scoped_long_input_file_aggregation_certificate",
            "scoped_long_input_certificate": True,
            "content_backed_scoped_certificate": True,
        }

    def package(name: str, rows: list[dict]) -> dict:
        proof_path = tmp_path / f"{name}_proofs.jsonl"
        proof_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        proof_hash = hashlib.sha256(proof_path.read_bytes()).hexdigest()
        receipt_path = tmp_path / f"{name}_receipt.json"
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": "longworld.codeforge-reading-proof-build.v2",
                    "profile_id": "p65-codeforge-filename-copy-content-backed-v1",
                    "files": {"proofs.jsonl": proof_hash},
                    "primary_rows": len(rows),
                    "qualified_existing_semantic_tasks": len(rows),
                }
            )
        )
        return {
            "receipt": {
                "path": receipt_path.name,
                "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            },
            "proofs": {"path": proof_path.name, "sha256": proof_hash},
        }

    old = package("old", [proof("old")])
    new = package("new", [proof("old"), proof("new")])
    scope_dir = tmp_path / "scope"
    scope_dir.mkdir()
    scope_index = scope_dir / "sample_index.jsonl"
    scope_index.write_text(
        json.dumps(
            {"source_group": "repo", "semantic_task_id": "new", "sample_id": "new"}
        )
        + "\n"
    )
    scope_manifest = scope_dir / "manifest.json"
    scope_manifest.write_text(
        json.dumps(
            {
                "schema_version": "longworld.unified-candidates.v1",
                "candidate_views": 1,
                "files_sha256": {
                    "sample_index.jsonl": hashlib.sha256(
                        scope_index.read_bytes()
                    ).hexdigest()
                },
            }
        )
    )
    new["scope_unified_manifest"] = {
        "path": "scope/manifest.json",
        "sha256": hashlib.sha256(scope_manifest.read_bytes()).hexdigest(),
    }
    entries = [_entry(name, group="repo", task=name) for name in ("old", "new")]
    for entry in entries:
        entry["candidate"]["source_kind"] = "real_code_workflow"
    eligible, report = balanced._codeforge_eligible(entries, [old, new])
    assert len(eligible) == 2
    assert report["counts"]["content_backed_views"] == 2

    scope_index.write_text(scope_index.read_text().replace('"new"', '"old"'))
    with pytest.raises(ValueError, match="CodeForge proof scope index differs"):
        balanced._codeforge_eligible(entries, [old, new])


def test_p99_added_code_proof_gate_requires_pinned_positive_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(balanced, "ROOT", tmp_path)
    proof = {
        "source_group": "repo",
        "semantic_task_id": "task",
        "sample_id": "sample",
        "reader_visible_replay": True,
        "filename_preserving_added_code_removal_changes_answer": True,
        "all_selected_identifiers_absent_after_added_code_removal": True,
        "single_raw_16k_window_insufficient_for_both_witnesses": True,
        "evidence_token_span": 20000,
    }
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(json.dumps(proof) + "\n")
    audit_sha = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    native_index_path = tmp_path / "sample_index.jsonl"
    native_index_path.write_text(
        json.dumps(
            {
                "source_group": "repo",
                "semantic_task_id": "task",
                "sample_id": "sample",
                "source_kind": "real_code_workflow",
                "split": "train",
                "full_chat_tokens": 110,
            }
        )
        + "\n"
    )
    native_index_sha = hashlib.sha256(native_index_path.read_bytes()).hexdigest()
    native_path = tmp_path / "native.json"
    native_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.p99-code-content.v1",
                "files_sha256": {
                    "audit.jsonl": audit_sha,
                    "sample_index.jsonl": native_index_sha,
                },
                "semantic_tasks": 1,
                "views": 1,
                "train_ready": False,
            }
        )
    )
    native_sha = hashlib.sha256(native_path.read_bytes()).hexdigest()
    unified_path = tmp_path / "unified.json"
    unified_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.unified-candidates.v1",
                "native_manifest_sha256": native_sha,
                "native_audit_sha256": audit_sha,
                "candidate_views": 1,
                "independent_semantic_tasks": 1,
                "files_sha256": {"sample_index.jsonl": "indexed-sample-hash"},
                "train_ready": False,
            }
        )
    )
    unified_sha = hashlib.sha256(unified_path.read_bytes()).hexdigest()
    mask_path = tmp_path / "mask.json"
    mask_path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.unified-reader-mask-all.v1",
                "source_manifest_sha256": unified_sha,
                "source_index_sha256": "indexed-sample-hash",
                "audited_views": 1,
                "train_ready": False,
            }
        )
    )
    pin = {
        name: {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in (
            ("native_manifest", native_path),
            ("native_audit", audit_path),
            ("unified_manifest", unified_path),
            ("mask_manifest", mask_path),
        )
    }
    entry = _entry("sample", group="repo", task="task")
    row = entry["candidate"]
    row.update(
        source_kind="real_code_workflow",
        source_name="p99_code_content",
        receipt_sha256=native_sha,
        dependency_status="content_backed_two_source_scoped_certificate",
        native_audit_ref="audit.jsonl:0",
        observed_witness_span_tokens=20000,
    )
    eligible, receipt = balanced._code_content_eligible([entry], pin)
    assert eligible == [entry]
    assert receipt["counts"]["content_backed_views"] == 1
    row["observed_witness_span_tokens"] = 19999
    assert balanced._code_content_eligible([entry], pin)[0] == []
    audit_path.write_text(audit_path.read_text() + "{}\n")
    with pytest.raises(ValueError, match="CodeForge proof pin mismatch"):
        balanced._code_content_eligible([entry], pin)


def test_multiple_code_proofs_reject_unpinned_and_uncurated_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _entry("old", group="repo", task="old-task")
    new = _entry("new", group="repo", task="new-task")
    unknown = _entry("unknown", group="repo", task="unknown-task")
    for entry, receipt in ((old, "prior"), (new, "expanded"), (unknown, "unknown")):
        entry["candidate"].update(
            receipt_sha256=receipt, source_kind="real_code_workflow"
        )
    prior = {"native_manifest": {"sha256": "prior"}}
    expanded = {
        "native_manifest": {"sha256": "expanded"},
        "prior_native_manifest": {"sha256": "prior"},
        "curated_manifest": {"sha256": "curated"},
        "curated_mask_manifest": {"sha256": "mask"},
    }
    monkeypatch.setattr(
        balanced,
        "_code_content_eligible",
        lambda rows, pin: (
            rows,
            {"proof_pin": pin, "counts": {"content_backed_views": len(rows)}},
        ),
    )
    monkeypatch.setattr(
        balanced,
        "_curated_code_membership",
        lambda _pin: {
            "new": {
                key: new["candidate"].get(key)
                for key in (
                    "semantic_task_id",
                    "source_group",
                    "split",
                    "native_audit_ref",
                    "receipt_sha256",
                )
            }
        },
    )
    eligible, receipt = balanced._code_content_eligible_many(
        [old, new, unknown], [prior, expanded]
    )
    assert eligible == [old, new]
    assert receipt["excluded_unpinned_receipt"] == 1
    assert receipt["content_backed_views"] == 2

    with pytest.raises(ValueError, match="curator membership"):
        balanced._code_content_eligible_many(
            [old, new], [prior, {"native_manifest": {"sha256": "expanded"}}]
        )
    with pytest.raises(ValueError, match="repeats native receipt"):
        balanced._code_content_eligible_many([old], [prior, expanded, expanded])


def test_multiple_code_proofs_filter_raw_task_outside_curator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = {"native_manifest": {"sha256": "prior"}}
    expanded = {
        "native_manifest": {"sha256": "expanded"},
        "prior_native_manifest": {"sha256": "prior"},
        "curated_manifest": {"sha256": "curated"},
        "curated_mask_manifest": {"sha256": "mask"},
    }
    raw_only = _entry("raw-only", group="repo", task="raw-only")
    raw_only["candidate"]["receipt_sha256"] = "expanded"
    monkeypatch.setattr(
        balanced, "_code_content_eligible", lambda rows, pin: (rows, {"proof_pin": pin})
    )
    monkeypatch.setattr(balanced, "_curated_code_membership", lambda _pin: {})
    eligible, receipt = balanced._code_content_eligible_many(
        [raw_only], [prior, expanded]
    )
    assert eligible == []
    assert receipt["packages"][1]["excluded_outside_curated_subset"] == 1


def test_curated_code_membership_rejects_quality_ledger_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(balanced, "ROOT", tmp_path)
    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    prior = tmp_path / "prior.json"
    prior.write_text("{}\n")
    index = curated_dir / "sample_index.jsonl"
    row = {
        "sample_id": "new",
        "semantic_task_id": "task",
        "source_group": "repo",
        "split": "train",
        "receipt_sha256": "raw-native",
    }
    index.write_text(json.dumps(row) + "\n")
    ledger = curated_dir / "quality_ledger.jsonl"
    ledger.write_text(json.dumps({**row, "status": "accepted_new_pr_pair"}) + "\n")
    curated = {
        "curation_schema": "longworld.p107-code-curation.v1",
        "train_ready": False,
        "raw_native_manifest_sha256": "raw-native",
        "raw_unified_manifest_sha256": "raw-unified",
        "raw_all_mask_manifest_sha256": "raw-mask",
        "prior_native_manifest_sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
        "quality_ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
        "files_sha256": {
            "sample_index.jsonl": hashlib.sha256(index.read_bytes()).hexdigest()
        },
        "candidate_views": 1,
        "gross_reader_views": 1,
        "rejected_prior_pr_pairs": 0,
    }
    manifest = curated_dir / "manifest.json"
    manifest.write_text(json.dumps(curated) + "\n")
    mask = curated_dir / "mask.json"
    mask.write_text(
        json.dumps(
            {
                "schema_version": "longworld.unified-reader-mask-all.v1",
                "train_ready": False,
                "source_manifest_sha256": hashlib.sha256(
                    manifest.read_bytes()
                ).hexdigest(),
                "source_index_sha256": curated["files_sha256"]["sample_index.jsonl"],
                "audited_views": 1,
            }
        )
        + "\n"
    )
    monkeypatch.setattr(balanced, "verify_merge", lambda _path: curated)
    pin = {
        "native_manifest": {"sha256": "raw-native"},
        "unified_manifest": {"sha256": "raw-unified"},
        "mask_manifest": {"sha256": "raw-mask"},
        "curated_manifest": {
            "path": "curated/manifest.json",
            "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        },
        "curated_mask_manifest": {
            "path": "curated/mask.json",
            "sha256": hashlib.sha256(mask.read_bytes()).hexdigest(),
        },
        "prior_native_manifest": {
            "path": "prior.json",
            "sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
        },
    }
    assert balanced._curated_code_membership(pin) == {"new": row}
    ledger.write_text("{}\n")
    with pytest.raises(ValueError, match="curator membership"):
        balanced._curated_code_membership(pin)
