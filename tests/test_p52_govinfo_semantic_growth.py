from reports import p52_govinfo_semantic_growth_20260906 as growth


def test_substantial_growth_uses_ceiling_and_rejects_small_proof_increase():
    def row(bucket, context, proof, support):
        return {
            "bucket": bucket,
            "views": {
                "full": {
                    "context_tokens": context,
                    "proof_tokens": proof,
                    "strict_support_event_count": support,
                }
            },
        }

    checks = growth.assess_growth(
        [
            row("32k", 32000, 1000, 2),
            row("64k", 64001, 2600, 6),
            row("128k", 128002, 5801, 12),
        ]
    )
    assert checks[0]["minimum_proof_growth"] == 1601
    assert checks[0]["status"] == "REJECT_SUBSTANTIAL_PROOF_GROWTH"
    assert checks[1]["minimum_proof_growth"] == 3201
    assert checks[1]["status"] == "PRECHECK_PASS_SHARED_GATES_PENDING"


def test_more_context_without_new_dependencies_is_rejected():
    measures = [
        {
            "bucket": bucket,
            "views": {
                "full": {
                    "context_tokens": context,
                    "proof_tokens": 4000,
                    "strict_support_event_count": 4,
                }
            },
        }
        for bucket, context in [("64k", 64000), ("128k", 128000)]
    ]
    check = growth.assess_growth(measures)[0]
    assert check["strict_support_growth"] == 0
    assert check["proof_growth"] == 0
    assert check["status"] == "REJECT_SUBSTANTIAL_PROOF_GROWTH"


def test_proof_counter_joins_declared_essential_order_with_shared_separator(
    monkeypatch,
):
    class RecordingTokenizer:
        def __init__(self):
            self.inputs = []

        def encode(self, text, *, add_special_tokens):
            self.inputs.append(text)
            return list(text)

    tokenizer = RecordingTokenizer()
    monkeypatch.setattr(
        growth,
        "materialize_govinfo_counterfactual",
        lambda _parent, artifacts: artifacts,
    )
    monkeypatch.setattr(
        growth,
        "govinfo_chronology",
        lambda artifacts: [
            (str(index), cls, doc) for index, (cls, doc) in enumerate(artifacts)
        ],
    )
    parent = {
        "artifact_classification": [
            {"artifact_id": "left"},
            {"artifact_id": "background"},
            {"artifact_id": "right"},
        ],
        "document_context": growth.SEP.join(["first", "irrelevant", "last"]),
        "essential_artifact_ids": ["right", "left"],
        "question": "Compare",
        "query_timing": "first",
        "requested_dispositions": [{}, {}],
        "length_bucket": "32k",
    }
    measured = growth.measure_growth_row(parent, tokenizer)
    exact_proof = growth.SEP.join(["last", "first"])
    assert exact_proof in tokenizer.inputs
    assert all(
        view["proof_tokens"] == len(exact_proof) for view in measured["views"].values()
    )
    assert "irrelevant" not in exact_proof
