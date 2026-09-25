"""P108 source export keeps attestation pins inside the trusted verifier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import p108_code_anchor_preflight as anchors
from scripts import p108_code_export as export
from scripts import p108_code_verify_export_v2 as verifier


def test_trusted_verifier_entrypoint_imports_from_external_cwd(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(verifier.__file__).resolve()), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_public_pin_comparisons_do_not_echo_wrapper_redacted_hashes(monkeypatch):
    monkeypatch.setattr(
        verifier,
        "verify",
        lambda *_args: {
            "source_sha256": "source",
            "public_policy_sha256": "policy-pin",
            "source_client_sha256": "client-pin",
        },
    )
    monkeypatch.setenv("LONGWORLD_PUBLIC_POLICY_SHA256", "policy-pin")
    monkeypatch.setenv("LONGWORLD_GH_BINARY_SHA256", "client-pin")
    result = verifier.verify_pins(None, "owner/repo", 12)
    assert result == {
        "source_sha256": "source",
        "public_policy_pin_matches": True,
        "source_client_pin_matches": True,
    }
    monkeypatch.setenv("LONGWORLD_GH_BINARY_SHA256", "different")
    assert (
        verifier.verify_pins(None, "owner/repo", 12)["source_client_pin_matches"]
        is False
    )


def test_episode_rejects_false_trusted_pin_even_when_source_sha_matches(
    monkeypatch, tmp_path
):
    source = tmp_path / "episode.json"
    source.write_text("{}")
    monkeypatch.setattr(
        export,
        "_trusted",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            '{"source_sha256":"' + export._sha(source) + '",'
            '"public_policy_pin_matches":true,'
            '"source_client_pin_matches":false}',
            "",
        ),
    )
    with pytest.raises(ValueError, match="pins differ"):
        export._verify_episode(
            None,
            {"trust_wrapper": None, "source_client": None, "verify_export": None},
            "policy",
            source,
            "owner/repo",
            12,
        )


def test_expansion_reuses_exact_prior_bytes_and_rejects_split_drift(tmp_path):
    prior = tmp_path / "prior"
    source = prior / "exports" / "owner__repo" / "pr_12.json"
    receipt_path = prior / "attempts" / "owner__repo" / "pr_12.json"
    source.parent.mkdir(parents=True)
    receipt_path.parent.mkdir(parents=True)
    source.write_text('{"signed":"source"}')
    receipt = {
        "repository": "owner/repo",
        "pull_number": 12,
        "status": "frozen_usable",
        "source": {"source_sha256": export._sha(source)},
    }
    receipt_path.write_text(json.dumps(receipt))
    manifest = prior / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": export.SCHEMA + ".result",
                "by_repository": [
                    {
                        "repository": "owner/repo",
                        "split": "train",
                        "attempts": [receipt],
                    }
                ],
            }
        )
    )
    probe = {
        "by_repository": [
            {
                "repository": "owner/repo",
                "split": "train",
                "eligible_prs_in_priority_order": [12],
            }
        ]
    }
    destination = tmp_path / "expanded"
    assert export._prefill_prior(manifest, destination, probe, verify_only=False) == 1
    linked = destination / "exports" / "owner__repo" / "pr_12.json"
    assert linked.stat().st_ino == source.stat().st_ino
    assert export._prefill_prior(manifest, destination, probe, verify_only=True) == 1
    with pytest.raises(ValueError, match="split differs"):
        export._prefill_prior(
            manifest,
            destination,
            {
                "by_repository": [
                    {
                        "repository": "owner/repo",
                        "split": "eval",
                        "eligible_prs_in_priority_order": [12],
                    }
                ]
            },
            verify_only=True,
        )


def test_added_code_preflight_requires_unique_nonfilename_identifier():
    payload = {
        "records": [
            {"id": "merge:12", "kind": "merge", "links": ["commit:a"]},
            {
                "id": "commit:a",
                "kind": "commit",
                "text": "diff -- src/filename_marker.py\n+def useful_identifier():\n",
            },
        ]
    }
    assert anchors._anchors(payload, 12) == (1, 1)
    payload["records"][1]["text"] += "+useful_identifier()\n"
    assert anchors._anchors(payload, 12) == (1, 0)
