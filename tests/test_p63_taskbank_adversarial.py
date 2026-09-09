"""Independent boundary tests; fixtures and mutations stay inside tmp_path."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_finance_taskbank_batch as batch
from longworld.core import finance_taskbank as bank


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_batch_rejects_source_changed_after_catalog_check(tmp_path, monkeypatch):
    """Successful child commands cannot make a stale catalog source pin valid."""
    monkeypatch.setattr(batch, "ROOT", tmp_path)
    source = tmp_path / "source.json"
    source.write_text('{"version":"original"}')
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "source_manifest": "source.json",
                "split_group_id": "issuer-a",
                "split": "train",
            }
        )
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": "longworld.p63-finance-taskbank-source-catalog.v1",
                "jobs": [
                    {
                        "issuer": "issuer_a",
                        "config": "config.json",
                        "config_sha256": _sha(config),
                        "source_manifest": "source.json",
                        "source_manifest_sha256": _sha(source),
                        "trust_file": str(tmp_path / "unused-trust.json"),
                    }
                ],
            }
        )
    )

    def successful_child(argv, **kwargs):
        destination = Path(argv[argv.index("--output") + 1])
        destination.mkdir(exist_ok=True)
        # Simulate a newer signed source being installed while the child runs.
        source.write_text('{"version":"changed-during-build"}')
        (destination / "BUILD_RECEIPT.json").write_text(
            json.dumps(
                {
                    "accepted_semantic_tasks": 1,
                    "training_views": 1,
                    "split": "train",
                    "split_group_id": "issuer-a",
                    "task_families": {"delta": 1},
                    "natural_length_caps": {"16384": 1},
                    "compiler_metrics": {},
                    "unique_contexts": 1,
                }
            )
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(batch.subprocess, "run", successful_child)
    result = batch.run_batch(catalog, tmp_path / "batch", workers=1)
    assert result["jobs"][0]["status"] == "blocked"
    assert result["accepted_semantic_tasks"] == 0


@pytest.fixture
def compiled_export(tmp_path, monkeypatch):
    import scripts.materialize_finance_taskbank as exporter
    from tests.test_finance_taskbank import world as world_fixture

    # Reuse the four-year signed source fixture and its real query executor.
    world = inspect.unwrap(world_fixture)(tmp_path, monkeypatch)
    monkeypatch.setattr(bank, "load_finance_world", lambda _: world)

    class CharacterTokenizer:
        def encode(self, text, *, add_special_tokens):
            assert add_special_tokens is False
            return list(text)

    monkeypatch.setattr(exporter, "_load_tokenizer", lambda *_: CharacterTokenizer())
    monkeypatch.setattr(
        exporter, "resolved_tokenizer_asset_manifest_sha256", lambda *_: "a" * 64
    )
    config = tmp_path / "build-config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "longworld.finance-taskbank-build.v1",
                "source_manifest": "fixture-source-is-loaded-by-test.json",
                "split_group_id": world["split_group_id"],
                "split": "train",
                "min_context_tokens": 1,
                "max_context_tokens": 131072,
                "tokenizer": {"model_id": "fixture-tokenizer", "revision": "b" * 40},
                "holds": {},
            }
        )
    )
    output = tmp_path / "built"
    receipt = exporter.materialize(config, output)
    assert receipt["accepted_semantic_tasks"] > 0
    assert exporter.validate_export(config, output)["status"] == "PASS"
    return exporter, config, output


def _rewrite_tasks_and_hashes(output, rows):
    path = output / "tasks.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    receipt_path = output / "BUILD_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files"]["tasks.jsonl"] = _sha(path)
    receipt_path.write_text(json.dumps(receipt))


def test_persisted_small_world_roundtrip_is_read_only(compiled_export):
    exporter, config, output = compiled_export
    before = {
        p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()
    }
    assert exporter.validate_export(config, output)["status"] == "PASS"
    assert {
        p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()
    } == before


def test_validator_rejects_other_occurrence_of_the_same_value(compiled_export):
    exporter, config, output = compiled_export
    rows = [
        json.loads(line) for line in (output / "tasks.jsonl").read_text().splitlines()
    ]
    chosen = next(
        row
        for row in rows
        if any(bound["value"] == 500 for bound in row["visible_evidence"])
    )
    bound = next(bound for bound in chosen["visible_evidence"] if bound["value"] == 500)
    context = (output / chosen["context_path"]).read_text()
    # Assets and liabilities-and-equity share 500 in different cells.
    alternatives = [
        index + 1 for index in range(len(context)) if context.startswith("\t500", index)
    ]
    alternate = next(index for index in alternatives if index != bound["context_start"])
    assert context[alternate : alternate + 3] == bound["quote"] == "500"
    bound["context_start"], bound["context_end"] = alternate, alternate + 3
    _rewrite_tasks_and_hashes(output, rows)
    with pytest.raises(ValueError, match="offset|evidence|binding"):
        exporter.validate_export(config, output)


@pytest.mark.parametrize(
    ("field", "forged"),
    [
        ("strict_long_dependency_verified", True),
        ("alternative_proof_search_complete", True),
        ("exact_band_certificate", True),
        ("source_document_count", 999),
        ("task_profile", "strict_long_dependency"),
    ],
)
def test_validator_rejects_forged_readiness_or_source_count(
    compiled_export, field, forged
):
    exporter, config, output = compiled_export
    rows = [
        json.loads(line) for line in (output / "tasks.jsonl").read_text().splitlines()
    ]
    rows[0][field] = forged
    _rewrite_tasks_and_hashes(output, rows)
    with pytest.raises(ValueError):
        exporter.validate_export(config, output)


def test_validator_rejects_inflated_training_view_receipt(compiled_export):
    exporter, config, output = compiled_export
    path = output / "BUILD_RECEIPT.json"
    receipt = json.loads(path.read_text())
    receipt["training_views"] += 1000
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="receipt|count|metric|summary"):
        exporter.validate_export(config, output)


@pytest.mark.parametrize("outside_path", [False, True])
def test_validator_requires_exact_compiler_code_inventory(
    compiled_export, outside_path
):
    exporter, config, output = compiled_export
    path = output / "BUILD_RECEIPT.json"
    receipt = json.loads(path.read_text())
    if outside_path:
        unrelated = output.parent / "not-the-compiler.py"
        unrelated.write_text(
            "# An unrelated file must not replace compiler provenance.\n"
        )
        receipt["code_sha256"] = {str(unrelated): _sha(unrelated)}
    else:
        receipt["code_sha256"] = {}
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="code|compiler|path"):
        exporter.validate_export(config, output)


def test_validator_rejects_external_receipt_symlink(compiled_export):
    exporter, config, output = compiled_export
    path = output / "BUILD_RECEIPT.json"
    external = output.parent / "external-receipt.json"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(ValueError, match="receipt|regular|symlink|unsafe"):
        exporter.validate_export(config, output)
