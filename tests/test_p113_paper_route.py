import json

from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p113_paper_route import ROOT, run


def test_new_frozen_works_with_no_supported_cross_file_targets_do_not_reach_qa():
    manifest = run(
        ROOT / "configs/p113_paper_route_v1.json",
        ROOT / "data/capability_records/p113_paper_route_v1",
        verify_only=True,
    )
    assert manifest["frozen_works"] == 6
    assert manifest["raw_source_shape_works"] == 0
    assert manifest["screen_positive_works"] == 0
    assert manifest["train_ready"] is False


def test_new_revision_task_has_separate_reader_and_complete_mask_receipts():
    native = json.loads(
        (
            ROOT / "data/candidates/p113_paper_revision_native_v1/manifest.json"
        ).read_text()
    )
    unified_dir = ROOT / "data/candidates/p113_paper_revision_unified_v1"
    unified = verify_merge(unified_dir)
    mask = json.loads(
        (ROOT / "data/candidates/p113_paper_revision_mask_v1/manifest.json").read_text()
    )
    audit = json.loads(
        (ROOT / "data/candidates/p113_paper_revision_native_v1/audit.jsonl").read_text()
    )
    assert native["source_works"] == 6
    assert native["quality_admitted_tasks"] == 1
    assert unified["independent_semantic_tasks"] == mask["audited_views"] == 1
    assert mask["full_chat_tokens"] == 80455
    assert mask["supervised_tokens"] == 313
    assert audit["bounded_evidence_extent_tokens"] >= 32768
    assert audit["reader_replay"] and audit["mask_checked"]
