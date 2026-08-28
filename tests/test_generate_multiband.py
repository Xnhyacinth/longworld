from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate import (
    _exact_metric_token_counter,
    _strict_workflow_artifacts,
    _trainable,
    _views_for_band,
    apply_source_workflow_bucket_targets,
    counterfactual_text_reject_reason,
    emit_records,
    exact_64k_reject_reason,
    exact_token_metadata_for_band,
    retune_pack_target_for_exact_64k,
)

from longworld.core.promotion import exact_token_band_reject_reason
from longworld.core.render import Artifact


def test_exact_metric_counter_does_not_depend_on_pack_admission_flag() -> None:
    counter = len

    assert (
        _exact_metric_token_counter(
            {"model_id": "pinned", "revision": "a" * 40}, "64k", counter
        )
        is counter
    )
    assert _exact_metric_token_counter({}, "64k", counter) is None
    assert (
        _exact_metric_token_counter(
            {"model_id": "pinned", "revision": "a" * 40}, "4k", counter
        )
        is None
    )


def test_source_workflow_can_use_a_real_body_specific_bucket_target() -> None:
    assert apply_source_workflow_bucket_targets(
        {"16k": 16000, "64k": 71500},
        {"source_workflow_bucket_targets": {"64k": 61500}},
    ) == {"16k": 16000, "64k": 61500}


def test_repeated_minimal_course_is_skipped_without_dropping_later_long_views() -> None:
    views = {
        "full": ("long-full", []),
        "cf": ("long-cf", []),
        "minimal": ("same-short-course", []),
        "ordered_artifact_view": ("long-ordered", []),
    }
    published_minimal_courses: set[tuple[str, str]] = set()

    first = _views_for_band(
        views,
        base_task_id="base-1",
        timing="late",
        published_minimal_courses=published_minimal_courses,
    )
    published_minimal_courses.add(("base-1", "late"))
    later = _views_for_band(
        views,
        base_task_id="base-1",
        timing="late",
        published_minimal_courses=published_minimal_courses,
    )

    assert set(first) == {"full", "cf", "minimal", "ordered_artifact_view"}
    assert set(later) == {"full", "cf", "ordered_artifact_view"}
    assert "minimal" in views


def test_strict_workflow_artifacts_drop_unverified_source_pack() -> None:
    internal = Artifact("w.internal", "email", None, "p", "focal", [], "body", [])
    legacy = Artifact(
        "w.legacy",
        "source_pack",
        None,
        "public",
        "focal",
        [],
        "legacy body",
        [],
        slots={"provenance_verified": False},
    )
    verified = Artifact(
        "w.verified",
        "source_pack",
        None,
        "public",
        "focal",
        [],
        "verified body",
        [],
        slots={"provenance_verified": True},
    )

    assert _strict_workflow_artifacts(
        [internal, legacy, verified], allow_legacy_sources=False
    ) == [internal, verified]
    assert _strict_workflow_artifacts(
        [internal, legacy, verified], allow_legacy_sources=True
    ) == [internal, legacy, verified]


def test_counterfactual_answer_change_requires_visible_context_change() -> None:
    factual_target = Artifact(
        "w.target", "record", None, "p", "focal", ["w.cf"], "before", []
    )
    changed_target = Artifact(
        "w.target", "record", None, "p", "focal", ["w.cf"], "after", []
    )
    unchanged = {"full": ("same", [factual_target]), "cf": ("same", [factual_target])}
    changed = {
        "full": ("before", [factual_target]),
        "cf": ("after", [changed_target]),
    }

    assert (
        counterfactual_text_reject_reason(
            unchanged,
            factual_answer="allow",
            counterfactual_answer="block",
            cf_event_id="w.cf",
        )
        == "counterfactual_text_unchanged"
    )
    assert (
        counterfactual_text_reject_reason(
            changed,
            factual_answer="allow",
            counterfactual_answer="block",
            cf_event_id="w.cf",
        )
        is None
    )
    assert (
        counterfactual_text_reject_reason(
            unchanged,
            factual_answer="same",
            counterfactual_answer="same",
            cf_event_id="w.cf",
        )
        is None
    )


def test_counterfactual_answer_change_requires_target_event_artifact_change() -> None:
    factual_target = Artifact(
        "w.target", "record", None, "p", "focal", ["w.cf"], "target", []
    )
    unchanged_target = Artifact(
        "w.target", "record", None, "p", "focal", ["w.cf"], "target", []
    )
    factual_other = Artifact(
        "w.other", "record", None, "p", "focal", ["w.other"], "before", []
    )
    changed_other = Artifact(
        "w.other", "record", None, "p", "focal", ["w.other"], "after", []
    )
    wrong_change = {
        "full": ("target\nbefore", [factual_target, factual_other]),
        "cf": ("target\nafter", [unchanged_target, changed_other]),
    }
    target_not_visible = {
        "full": ("before", [factual_other]),
        "cf": ("after", [changed_other]),
    }

    assert (
        counterfactual_text_reject_reason(
            target_not_visible,
            factual_answer="allow",
            counterfactual_answer="block",
            cf_event_id="w.cf",
        )
        == "counterfactual_event_not_visible"
    )
    assert (
        counterfactual_text_reject_reason(
            wrong_change,
            factual_answer="allow",
            counterfactual_answer="block",
            cf_event_id="w.cf",
        )
        == "counterfactual_event_text_unchanged"
    )

    changed_target = Artifact(
        "w.target", "record", None, "p", "focal", ["w.cf"], "changed target", []
    )
    target_and_downstream_change = {
        "full": ("target\nbefore", [factual_target, factual_other]),
        "cf": ("changed target\nafter", [changed_target, changed_other]),
    }
    assert (
        counterfactual_text_reject_reason(
            target_and_downstream_change,
            factual_answer="allow",
            counterfactual_answer="block",
            cf_event_id="w.cf",
        )
        is None
    )


def test_exact_64k_generation_range_is_closed_on_both_sides() -> None:
    assert exact_64k_reject_reason(63_999) == "exact_64k_out_of_range:63999"
    assert exact_64k_reject_reason(64_000) is None
    assert exact_64k_reject_reason(65_536) is None
    assert exact_64k_reject_reason(65_537) == "exact_64k_out_of_range:65537"


def test_exact_token_band_ranges_are_closed_and_do_not_relax() -> None:
    assert exact_token_band_reject_reason("16k", 15_999) == (
        "exact_16k_out_of_range:15999"
    )
    assert exact_token_band_reject_reason("16k", 16_000) is None
    assert exact_token_band_reject_reason("16k", 16_384) is None
    assert exact_token_band_reject_reason("16k", 16_385) == (
        "exact_16k_out_of_range:16385"
    )
    assert exact_token_band_reject_reason("32k", 32_000) is None
    assert exact_token_band_reject_reason("32k", 32_768) is None
    assert exact_token_band_reject_reason("64k", 64_000) is None
    assert exact_token_band_reject_reason("64k", 65_536) is None
    assert exact_token_band_reject_reason("8k", 8_192) is None


def test_exact_token_metadata_is_written_for_each_strict_long_band() -> None:
    class FakeTokenizer:
        def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
            assert add_special_tokens is False
            return [1] * int(text)

    cache: dict[str, int] = {}
    for band, tokens in (("16k", 16_100), ("32k", 32_200), ("64k", 64_300)):
        metadata, reason = exact_token_metadata_for_band(
            str(tokens),
            band,
            model_id="pinned/model",
            revision="a" * 40,
            asset_manifest_sha256="b" * 64,
            tokenizer=FakeTokenizer(),
            cache=cache,
        )
        assert reason is None
        assert metadata == {
            "tokenizer_context_tokens": tokens,
            "tokenizer_model_id": "pinned/model",
            "tokenizer_revision": "a" * 40,
            "tokenizer_asset_manifest_sha256": "b" * 64,
        }

    metadata, reason = exact_token_metadata_for_band(
        "9000",
        "8k",
        model_id="pinned/model",
        revision="a" * 40,
        asset_manifest_sha256="b" * 64,
        tokenizer=FakeTokenizer(),
        cache=cache,
    )
    assert metadata == {}
    assert reason is None


def test_emit_records_routes_every_strict_long_band_through_exact_counting() -> None:
    source = inspect.getsource(emit_records)

    assert "bname in EXACT_TOKEN_BAND_RANGES" in source
    assert "length_bucket=bname" in source


def test_exact_64k_pack_retune_scales_shared_window_from_observed_wraps() -> None:
    assert retune_pack_target_for_exact_64k(44_500, (56_165, 57_065)) == 50_906
    assert retune_pack_target_for_exact_64k(50_200, (65_901,)) == 49_336
    assert retune_pack_target_for_exact_64k(49_300, (64_976, 64_965)) == 49_300


def test_exact_64k_pack_retune_fails_when_first_and_late_cannot_share_a_cap() -> None:
    try:
        retune_pack_target_for_exact_64k(44_500, (50_000, 70_000))
    except ValueError as error:
        assert "cannot share one pack target" in str(error)
    else:
        raise AssertionError("expected first/late wrap conflict")


def test_strict_long_product_excludes_shallow_company_version_diff() -> None:
    spec = SimpleNamespace(
        query_id="company:version_diff",
        query_type="version_diff",
        domain="company",
    )

    assert _trainable(spec, production=False)
    assert not _trainable(spec, production=True)
