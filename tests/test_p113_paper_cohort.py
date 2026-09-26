from scripts.p113_paper_cohort import ROOT, run


def test_new_source_cohort_excludes_prior_attempts():
    manifest = run(
        ROOT / "configs/p113_paper_cohort_v1.json",
        ROOT / "data/capability_records/p113_paper_cohort_v1",
        verify_only=True,
    )
    assert manifest["prior_selected_works"] == 18
    assert manifest["prior_attempted_works"] == 9
    assert manifest["pending_works"] == 9
    assert manifest["planned_new_works"] == 6
    assert len(manifest["planned_categories"]) == 6
    assert manifest["train_ready"] is False
