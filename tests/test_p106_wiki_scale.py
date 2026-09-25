"""P106 source novelty, completed replay and delegated task identity."""

from copy import deepcopy
from pathlib import Path

import pytest

from scripts import p106_wiki_grid_batch as batch
from scripts.p106_freeze_width_revisions import _verify_completed, plan


def test_p95_width_plan_is_novel_against_p97() -> None:
    config, jobs, support = plan(Path("configs/p106_wiki_raw_p95_v1.json"))
    assert config["schema"] == "longworld.p106-wiki-raw-revision-plan.v1"
    assert support["gross_width_rejections"] == 217
    assert support["prior_p97_title_url_overlap"] == 0
    assert len(jobs) == 43
    assert len({row["page_url"] for row in jobs}) == 43
    assert support["split_pages"] == {"eval": 19, "train": 24}


def test_completed_freeze_is_verified_without_rewriting() -> None:
    config, jobs, _support = plan(Path("configs/p106_wiki_raw_p95_v1.json"))
    destination = Path("data/candidates/p106_wiki_width_raw_p95_v1").resolve()
    import json

    stored = json.loads((destination / "manifest.json").read_text())
    assert _verify_completed(stored, config, jobs, destination)
    tampered = deepcopy(stored)
    tampered["records"][0]["response_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="response hash"):
        _verify_completed(tampered, config, jobs, destination)


def test_delegated_compiler_uses_p106_task_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_compile(_job):
        old = "p105-wiki-grid-abcdef"
        return [
            (
                {"sample_id": old, "example_id": old},
                {"sample_id": old, "example_id": old, "task_id": "abcdef"},
                {"sample_id": old},
            )
        ], []

    monkeypatch.setattr(batch.base, "_compile_one", fake_compile)
    accepted, rejected = batch._compile_job((None,))
    assert not rejected
    reader, index, proof = accepted[0]
    assert (
        reader["sample_id"]
        == index["sample_id"]
        == proof["sample_id"]
        == "p106-wiki-grid-abcdef"
    )
    assert index["task_id"] == "abcdef"
