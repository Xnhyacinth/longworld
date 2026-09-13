import json

import pytest

from scripts.eval_capability_curriculum import evaluate_rows, grade_response


def response(content='{"q000": 1}', finish="stop"):
    return {"model": "test", "choices": [{"finish_reason": finish, "message": {"content": content}}]}


def row(view="factual", capacity=65536):
    return {"example_id": f"w:{view}:{capacity}", "world_id": "w", "view": view,
            "capacity_tokens": capacity,
            "messages": [{"role": "user", "content": "question"},
                         {"role": "assistant", "content": '{"q000":1}'}]}


def test_fake_transport_never_receives_gold():
    requests = []

    def transport(request):
        requests.append(request)
        return response()

    records, summary = evaluate_rows([row(), row("counterfactual")], transport, "test")
    assert requests[0]["messages"] == [{"role": "user", "content": "question"}]
    assert summary["complete_pairs"] == 1
    assert summary["paired_row_exact_accuracy"] == 1
    assert records[0]["raw_response"] == response()
    assert len(records[0]["request_sha256"]) == 64


def test_host_failure_is_not_model_zero_or_complete_pair():
    calls = iter([response(), OSError("host unavailable")])

    def transport(request):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    records, summary = evaluate_rows([row(), row("counterfactual")], transport, "test")
    assert records[1]["status"] == "transport_error"
    assert "qa_correct" not in records[1]
    assert summary["qa_exact_accuracy_completed"] == 1
    assert summary["complete_pairs"] == 0
    assert summary["paired_row_exact_accuracy"] is None


@pytest.mark.parametrize("finish,status", [("length", "truncated"), ("tool_calls", "unfinished")])
def test_incomplete_response_excluded(finish, status):
    records, summary = evaluate_rows([row()], lambda request: response(finish=finish), "test")
    assert records[0]["status"] == status
    assert summary["completed_rows"] == 0
    assert summary["qa_exact_accuracy_completed"] is None


@pytest.mark.parametrize("content", ['not JSON', '[]', '{"q000":1,"q000":2}', '{"q000":NaN}'])
def test_invalid_format_is_separate_from_wrong_answer(content):
    result = grade_response(response(content), {"q000": 1})
    assert result["status"] == "invalid_format"
    assert result["qa_correct"] == 0


def test_native_types_and_extra_keys_require_exact_match():
    assert not grade_response(response('{"q000":true}'), {"q000": 1})["row_correct"]
    result = grade_response(response('{"q000":1,"extra":2}'), {"q000": 1})
    assert result["qa_correct"] == 1
    assert not result["row_correct"]


def test_cross_capacity_rows_do_not_form_pair():
    _, summary = evaluate_rows([row(), row("counterfactual", 131072)], lambda request: response(), "test")
    assert summary["complete_pairs"] == 0


def test_protocol_failure_separate():
    records, summary = evaluate_rows([row()], lambda request: {"error": "bad envelope"}, "test")
    assert records[0]["status"] == "protocol_error"
    assert summary["completed_rows"] == 0


def test_offline_cli_marks_no_endpoint(tmp_path, monkeypatch):
    from scripts.eval_capability_curriculum import main

    input_path = tmp_path / "input.jsonl"
    saved = tmp_path / "saved.jsonl"
    output = tmp_path / "result"
    input_path.write_text(json.dumps(row()) + "\n")
    saved.write_text(json.dumps({"example_id": row()["example_id"], "raw_response": response()}) + "\n")
    monkeypatch.setattr("sys.argv", ["eval", "--input", str(input_path), "--output", str(output),
                                    "--model", "test", "--responses", str(saved)])
    main()
    summary = json.loads((output / "summary.json").read_text())
    assert summary["mode"] == "offline_scoring"
    assert summary["endpoint_called"] is False
