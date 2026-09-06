from reports import p52_govinfo_raw_growth_20260906 as runner


def test_raw_precheck_uses_shared_artifact_record_mode_and_retains_rejection(
    monkeypatch,
):
    class Tokenizer:
        def encode(self, text, *, add_special_tokens):
            return list(text)

    def reject(**kwargs):
        assert kwargs["records_are_artifacts"] is True
        assert kwargs["artifact_ids"] == ["source"]
        assert kwargs["documents"] == ["whole document"]
        assert kwargs["expected_total_tokens"] == len("whole document")
        assert kwargs["expected_answer"] == '{"D01":"M"}'
        raise runner.TaskProofError(
            "raw token window 16k intersecting-artifact upper bound retrieves the gold answer: 1:16385"
        )

    monkeypatch.setattr(runner, "_raw_token_window_proof", reject)
    candidate = {
        "view": "ordered_artifact_view",
        "query_id": "example",
        "artifact_classification": [{"artifact_id": "source"}],
        "document_context": "whole document",
        "answer": '{"D01":"M"}',
    }
    report = runner.raw_precheck(candidate, Tokenizer())
    assert report["status"] == "REJECT"
    assert report["error"].endswith("1:16385")
    assert "not complete task proof" in report["scope"]


def test_request_expansion_is_nested_and_keeps_old_schedule():
    import pytest

    assert runner.request_schedules(
        {"base_configs": ["a", "b"], "request_counts": [2, 6, 12]}
    ) == [[2, 6, 12], [2, 6, 12]]
    assert runner.request_schedules(
        {"base_configs": ["a", "a"], "request_schedules": [[4, 8, 16], [6, 12, 24]]}
    ) == [[4, 8, 16], [6, 12, 24]]
    for schedules in (
        [[4, 8, 8], [6, 12, 24]],
        [[4, 8, 16]],
        [[True, 8, 16], [6, 12, 24]],
    ):
        with pytest.raises(runner.p52.P52Blocker, match="predeclared increasing"):
            runner.request_schedules(
                {"base_configs": ["a", "b"], "request_schedules": schedules}
            )
