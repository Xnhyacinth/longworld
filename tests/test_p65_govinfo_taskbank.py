from decimal import Decimal

import pytest

from longworld.core.p65_govinfo_taskbank import (
    amount,
    execute,
    maps_from_blocks,
    render,
)


def world():
    old = {
        "division:a/section:1": "1. A limit of $100, provided that approval is obtained.",
        "division:a/section:2": "2. The limit is $500.",
        "division:a/section:4": "4. Use $200, except for excluded grants.",
    }
    new = {
        "division:a/section:1": "1. A limit of $150, provided that approval is obtained.",
        "division:a/section:2": "2. The limit is $490, provided that conditions apply.",
        "division:a/section:3": "3. Use $40, except for excluded grants.",
    }
    return {
        "bill_id": "fixture",
        "stages": {
            s: {
                "sections": [{"base_key": k, "text": v} for k, v in values.items()],
                "ambiguous_keys": [],
                "date": "2024-01-01",
                "url": "https://www.govinfo.gov/fixture",
            }
            for s, values in [("eas", old), ("eah", new)]
        },
    }


def test_exact_amount_grammar_excludes_scaling_and_multiple_figures():
    assert amount("Use $16,000,000, as provided.") == Decimal(16000000)
    assert amount("Use $16.25.") == Decimal("16.25")
    assert amount("Use $16 million.") is None
    assert amount("Use $1,00.") is None
    assert amount("Use $5 or $10.") is None


def test_version_condition_then_numeric_rank_and_exact_quote():
    _, blocks, maps = render(world(), "all")
    assert maps_from_blocks(blocks) == maps
    result = execute(maps, "largest_exception_amount_change")["locations"]
    assert len(result) == 1
    assert result[0]["section"] == "division:a/section:1"
    assert result[0]["change"] == "50"
    assert result[0]["eah_exception_excerpt"] == "provided that approval is obtained."
    introduced = execute(maps, "introduced_exception_amounts")["locations"]
    assert introduced[0]["section"] == "division:a/section:2"
    assert introduced[0]["change"] == "-10"


def test_added_and_removed_locations_do_not_invent_counterpart_amounts():
    _, _, maps = render(world(), "all")
    added = execute(maps, "added_exception_amounts")["locations"][0]
    removed = execute(maps, "removed_exception_sections")["locations"][0]
    assert (
        added["section"] == "division:a/section:3"
        and added["eas_amount"] is None
        and added["change"] is None
    )
    assert (
        removed["section"] == "division:a/section:4" and removed["eah_amount"] is None
    )


def test_ambiguous_source_location_is_not_misclassified_as_absent():
    w = world()
    w["stages"]["eas"]["ambiguous_keys"] = ["division:a/section:3"]
    _, _, maps = render(w, "all")
    assert execute(maps, "added_exception_amounts")["locations"] == []


def test_identical_whole_body_is_rendered_once_with_both_memberships():
    w = world()
    w["stages"]["eah"]["sections"][0]["text"] = w["stages"]["eas"]["sections"][0][
        "text"
    ]
    context, blocks, maps = render(w, "all")
    text = w["stages"]["eas"]["sections"][0]["text"]
    assert context.count(text) == 1
    assert maps_from_blocks(blocks) == maps


def test_unknown_program_is_not_silently_accepted():
    with pytest.raises(ValueError, match="unknown"):
        execute({"eas": {}, "eah": {}}, "invent_legal_effect")


def test_nonmonetary_counterpart_prevents_false_added_location():
    w = world()
    w["stages"]["eas"]["sections"].append(
        {
            "base_key": "division:a/section:3",
            "text": "3. Existing authority without a dollar figure.",
        }
    )
    _, _, maps = render(w, "all")
    assert "division:a/section:3" in maps["eas"]
    assert execute(maps, "added_exception_amounts")["locations"] == []


@pytest.fixture
def exported_bank(tmp_path, monkeypatch):
    import json

    from transformers import AutoTokenizer

    from scripts import materialize_p65_govinfo_taskbank as exporter

    class Tokenizer:
        chat_template = "fixture"

        def __call__(self, text, **kwargs):
            return {
                "input_ids": range(len(text)),
                "offset_mapping": [(i, i + 1) for i in range(len(text))],
            }

        def apply_chat_template(self, messages, **kwargs):
            text = "\n".join(m["role"] + ": " + m["content"] for m in messages)
            if kwargs.get("add_generation_prompt"):
                text += "\nassistant: "
            if kwargs.get("tokenize") is False:
                return text
            ids = list(range(len(text)))
            return (
                {"input_ids": ids, "attention_mask": [1] * len(ids)}
                if kwargs.get("return_dict", True)
                else ids
            )

    w = world()
    w["sources"] = []
    monkeypatch.setattr(exporter, "load_source", lambda config: w)
    monkeypatch.setattr(
        AutoTokenizer, "from_pretrained", lambda *args, **kwargs: Tokenizer()
    )
    monkeypatch.setattr(
        exporter, "resolved_tokenizer_asset_manifest_sha256", lambda *args: "a" * 64
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "longworld.p65-govinfo-taskbank-build.v1",
                "split": "train",
                "scopes": ["all"],
                "source_world": {"path": "fixture", "sha256": "b" * 64},
                "tokenizer": {"model_id": "fixture", "revision": "c" * 40},
            }
        )
    )
    output = tmp_path / "bank"
    receipt = exporter.build(config, output)
    return exporter, config, output, receipt


def test_export_regenerates_and_preserves_short_classification(exported_bank):
    exporter, config, output, receipt = exported_bank
    assert receipt["semantic_tasks"] > 0
    assert receipt["long_sft_rows"] == 0
    assert receipt["short_sft_rows"] == receipt["semantic_tasks"]
    assert exporter.validate(config, output)["status"] == "PASS"


@pytest.mark.parametrize(
    "target", ["tasks.jsonl", "short_sft_candidates.jsonl", "BUILD_RECEIPT.json"]
)
def test_rehashed_or_receipt_tampering_fails_reexecution(exported_bank, target):
    import hashlib
    import json

    exporter, config, output, _ = exported_bank
    path = output / target
    if target == "BUILD_RECEIPT.json":
        receipt = json.loads(path.read_text())
        receipt["strict_verified"] = 1
        path.write_text(json.dumps(receipt) + "\n")
    else:
        path.write_text(path.read_text() + "{}\n")
        receipt_path = output / "BUILD_RECEIPT.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["files"][target] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt) + "\n")
    with pytest.raises(ValueError, match="replay mismatch"):
        exporter.validate(config, output)


def test_full_chat_count_counts_ids_not_batch_encoding_keys(exported_bank):
    import json

    _, _, output, _ = exported_bank
    rows = [
        json.loads(line) for line in (output / "tasks.jsonl").read_text().splitlines()
    ]
    assert all(row["full_hf_chat_tokens"] > row["context_tokens"] for row in rows)


def test_source_preflight_must_authorize_candidate_generation(tmp_path, monkeypatch):
    import hashlib
    import json

    from scripts import materialize_p65_govinfo_taskbank as exporter

    source = tmp_path / "source.json"
    source.write_text(json.dumps({"bill_id": "fixture"}))
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "authorization": {
                    "allowed_actions": ["fetch_frozen_official_xml_to_process_memory"],
                    "prohibited_actions": ["generate_candidates"],
                },
                "chains": [{"bill_id": "fixture"}],
            }
        )
    )
    monkeypatch.setattr(exporter, "ROOT", tmp_path)
    config = {
        "source_world": {
            "path": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "original_source_config": {
            "path": preflight.name,
            "sha256": hashlib.sha256(preflight.read_bytes()).hexdigest(),
        },
    }

    with pytest.raises(ValueError, match="does not allow candidate generation"):
        exporter.load_source(config)
