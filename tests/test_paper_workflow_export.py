from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.documentworkflow import (
    _latex_sources_from_archive,
    _revision_added_text_fact,
    build_paper_workflow_manifest,
    load_paper_workflow_manifest,
)
from longworld.core.provenance import ProvenanceError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_source_workflow_bundle import build_source_workflow_bundle
from export_paper_workflow import export_paper_workflow
from fetch_paper_workflow import HttpResponse, fetch_paper_workflow

TEST_KEY = b"paper-export-test-attestation-key-32-bytes"


@pytest.mark.parametrize(
    "candidate",
    [
        "Funding for this study was provided by the Aurora Research Foundation.",
        "The project was funded by Aurora and another team by the National Institutes of Health (NIH), grant 12345.",
        "This revision adds a new ablation with stable results across all seeds.",
    ],
)
def test_revision_fact_export_skips_unsupported_answer_programs(candidate: str) -> None:
    assert (
        _revision_added_text_fact(
            {"main.tex": "Earlier manuscript text with no funding statement."},
            {"main.tex": candidate},
            current_record_text=candidate,
        )
        is None
    )


def test_paper_manifest_consumer_rejects_archive_expansion_bomb() -> None:
    target = io.BytesIO()
    payload = b"A" * 2_000_000
    with tarfile.open(fileobj=target, mode="w:gz") as archive:
        member = tarfile.TarInfo("paper.tex")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(ProvenanceError, match="compression ratio"):
        _latex_sources_from_archive(target.getvalue())


def _fetch_request(
    tmp_path: Path, *, include_openreview: bool = False, include_v3: bool = False
) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.paper-fetch-request.v1",
                "user_agent": "LongWorld/0.1 xnhyacinth@users.noreply.github.com",
                "authorization": {
                    "record_id": "PAPER-CONSUMER-001",
                    "scope": "bounded public scholarly source export",
                    "basis": "public arXiv records",
                    "reviewed_at": "2026-08-25T00:00:00Z",
                    "allowed_actions": [
                        "fetch_arxiv_metadata",
                        "fetch_arxiv_source",
                        *(["fetch_openreview_forum"] if include_openreview else []),
                    ],
                },
                "arxiv_version_ids": [
                    "2401.00001v1",
                    "2401.00001v2",
                    *(["2401.00001v3"] if include_v3 else []),
                ],
                "openreview_forum_ids": ["forum-1"] if include_openreview else [],
                "fetch_arxiv_source": True,
                "requests_per_second": 2,
                "max_retries": 0,
            }
        ),
        encoding="utf-8",
    )
    return path


def _fetch_atom(version: str, updated: str) -> bytes:
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom'><entry>
<id>http://arxiv.org/abs/2401.00001{version}</id>
<updated>{updated}</updated><published>2024-01-01T00:00:00Z</published>
<title>Authentic Long Context Study</title>
<summary>Revision {version} evaluates a long-context workflow.</summary>
<author><name>Ada Researcher</name></author>
</entry></feed>""".encode()


def _fetch_archive(text: str) -> bytes:
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w:gz") as archive:
        payload = text.encode()
        member = tarfile.TarInfo("main.tex")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return target.getvalue()


def _fetch_openreview(*, unknown_review_role: bool = False) -> bytes:
    invitation = "Official_Comment" if unknown_review_role else "Official_Review"
    return json.dumps(
        {
            "notes": [
                {
                    "id": "forum-1",
                    "forum": "forum-1",
                    "replyto": None,
                    "invitation": "Venue/2026/-/Submission",
                    "pdate": 1738454400000,
                    "content": {"title": {"value": "A public submission"}},
                },
                {
                    "id": "review-1",
                    "forum": "forum-1",
                    "replyto": "forum-1",
                    "invitation": f"Venue/2026/Paper1/-/{invitation}",
                    "cdate": 1738540800000,
                    "content": {"review": {"value": "The analysis needs variance."}},
                },
                {
                    "id": "response-1",
                    "forum": "forum-1",
                    "replyto": "review-1",
                    "invitation": "Venue/2026/Paper1/-/Author_Response",
                    "cdate": 1738627200000,
                    "content": {"response": {"value": "We added variance analysis."}},
                },
            ]
        }
    ).encode()


def _fetched_inventory(
    tmp_path: Path,
    *,
    include_funding: bool = True,
    include_v3: bool = False,
    include_openreview: bool = False,
    unknown_review_role: bool = False,
) -> Path:
    v1 = "\\section{Methods}\nWe evaluate the model on three long-context workflows.\n"
    funding = (
        "The authors thank the reviewers for their careful comments. "
        "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
        "Office of Naval Research (ONR), NSF 172251.\n"
        if include_funding
        else ""
    )
    v2 = v1
    if include_funding:
        v2 += (
            "We also report calibration results for every evaluated model.\n" + funding
        )
    v3 = v2 + "% Formatting-only camera-ready update.\n"

    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        if "api/query" in url and "v1" in url:
            body = _fetch_atom("v1", "2024-01-01T00:00:00Z")
            content_type = "application/atom+xml"
        elif "api/query" in url and "v2" in url:
            body = _fetch_atom("v2", "2024-02-01T00:00:00Z")
            content_type = "application/atom+xml"
        elif "api/query" in url and "v3" in url:
            body = _fetch_atom("v3", "2024-03-01T00:00:00Z")
            content_type = "application/atom+xml"
        elif "/e-print/2401.00001v1" in url:
            body = _fetch_archive(v1)
            content_type = "application/gzip"
        elif "/e-print/2401.00001v2" in url:
            body = _fetch_archive(v2)
            content_type = "application/gzip"
        elif "/e-print/2401.00001v3" in url:
            body = _fetch_archive(v3)
            content_type = "application/gzip"
        elif "api2.openreview.net/notes" in url:
            body = _fetch_openreview(unknown_review_role=unknown_review_role)
            content_type = "application/json"
        else:
            raise AssertionError(url)
        return HttpResponse(
            body=body,
            status=200,
            final_url=url,
            content_type=content_type,
        )

    return fetch_paper_workflow(
        _fetch_request(
            tmp_path,
            include_openreview=include_openreview,
            include_v3=include_v3,
        ),
        tmp_path / "fetched",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-25T01:00:00Z",
    )


def test_fetch_inventory_export_derives_revision_and_added_body_fact(
    tmp_path: Path,
) -> None:
    inventory_path = _fetched_inventory(tmp_path)
    output_path = tmp_path / "manifest.json"

    export_paper_workflow(
        None,
        output_path,
        fetch_inventory_path=inventory_path,
        attestation_key=TEST_KEY,
        generated_at="2026-08-25T02:00:00Z",
    )
    loaded = load_paper_workflow_manifest(output_path, attestation_key=TEST_KEY)

    assert loaded["schema_version"] == "longworld.paper-workflow-manifest.v2"
    assert loaded["generation_integration"] == "disabled"
    assert (
        loaded["fetch_inventory_sha256"]
        == hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    )
    assert {record["role"] for record in loaded["records"]} == {"manuscript_revision"}
    assert {relation["kind"] for relation in loaded["relations"]} == {"revision_of"}
    revision = next(
        record for record in loaded["records"] if record["revision_id"] == "v2"
    )
    fact = next(
        fact
        for fact in revision["derived_facts"]
        if fact["field"] == "revision_added_text"
    )
    assert fact["value"] == (
        "The authors thank the reviewers for their careful comments. "
        "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
        "Office of Naval Research (ONR), NSF 172251."
    )
    start = fact["evidence_char_start"]
    assert (
        revision["text"][start : start + len(fact["evidence_quote"])]
        == fact["evidence_quote"]
    )
    assert fact["text_sha256"] == revision["text_sha256"]
    assert revision["retrieval"]["parser"] == "arxiv_atom_v1"
    assert (
        revision["source_archive"]["sha256"]
        == hashlib.sha256(
            inventory_path.parent.joinpath("arxiv-2401.00001v2.source.tar").read_bytes()
        ).hexdigest()
    )


def test_fetched_paper_manifest_builds_a_reloadable_source_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], TEST_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "paper-source-test-v1")
    inventory_path = _fetched_inventory(tmp_path)
    manifest_path = tmp_path / "paper-manifest.json"
    bundle_path = tmp_path / "source-bundle.json"
    export_paper_workflow(
        None,
        manifest_path,
        fetch_inventory_path=inventory_path,
        attestation_key=TEST_KEY,
        generated_at="2026-08-25T02:00:00Z",
    )

    loaded = build_source_workflow_bundle(
        [("paper_workflow", manifest_path)],
        bundle_path,
        attestation_key=TEST_KEY,
    )

    assert bundle_path.is_file()
    assert len(loaded.workflows) == 1
    assert loaded.workflows[0].source_kind == "paper_workflow"
    assert any(
        fact.field == "revision_added_text"
        for record in loaded.workflows[0].records
        for fact in record.facts
    )


def test_fetch_inventory_export_fails_without_new_semantic_latex_body(
    tmp_path: Path,
) -> None:
    inventory_path = _fetched_inventory(tmp_path, include_funding=False)

    with pytest.raises(ProvenanceError, match="new semantic LaTeX body"):
        export_paper_workflow(
            None,
            tmp_path / "manifest.json",
            fetch_inventory_path=inventory_path,
            attestation_key=TEST_KEY,
        )


def test_fetch_inventory_allows_later_formatting_only_revision(
    tmp_path: Path,
) -> None:
    inventory_path = _fetched_inventory(tmp_path, include_v3=True)
    output_path = tmp_path / "manifest.json"

    export_paper_workflow(
        None,
        output_path,
        fetch_inventory_path=inventory_path,
        attestation_key=TEST_KEY,
    )
    loaded = load_paper_workflow_manifest(output_path, attestation_key=TEST_KEY)

    revisions = {record["revision_id"]: record for record in loaded["records"]}
    assert any(
        fact["field"] == "revision_added_text"
        and "Aviva" in fact["value"]
        and "ONR" in fact["value"]
        and "NSF 172251" in fact["value"]
        for fact in revisions["v2"]["derived_facts"]
    )
    assert not any(
        fact["field"] == "revision_added_text"
        for fact in revisions["v3"]["derived_facts"]
    )
    assert any(
        relation["kind"] == "revision_of"
        and relation["source_record_id"] == revisions["v3"]["record_id"]
        and relation["target_record_id"] == revisions["v2"]["record_id"]
        for relation in loaded["relations"]
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda inventory: inventory.__setitem__("production_eligible", True),
            "disabled",
        ),
        (
            lambda inventory: inventory["records"][1].__setitem__(
                "previous_revision_id", "v0"
            ),
            "previous revision",
        ),
    ],
)
def test_fetch_inventory_export_rejects_disabled_or_revision_rebinding(
    tmp_path: Path, mutation, message: str
) -> None:
    inventory_path = _fetched_inventory(tmp_path)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    mutation(inventory)
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(ProvenanceError, match=message):
        export_paper_workflow(
            None,
            tmp_path / "manifest.json",
            fetch_inventory_path=inventory_path,
            attestation_key=TEST_KEY,
        )


def test_fetch_inventory_export_rejects_retrieval_byte_tampering(
    tmp_path: Path,
) -> None:
    inventory_path = _fetched_inventory(tmp_path)
    retrieval_path = inventory_path.parent / "arxiv-2401.00001v2.atom.xml"
    retrieval_path.write_bytes(retrieval_path.read_bytes() + b"tampered")

    with pytest.raises(ProvenanceError, match="retrieval hash"):
        export_paper_workflow(
            None,
            tmp_path / "manifest.json",
            fetch_inventory_path=inventory_path,
            attestation_key=TEST_KEY,
        )


def test_fetch_inventory_derives_only_metadata_proven_openreview_edges(
    tmp_path: Path,
) -> None:
    inventory_path = _fetched_inventory(tmp_path, include_openreview=True)
    output_path = tmp_path / "manifest.json"

    export_paper_workflow(
        None,
        output_path,
        fetch_inventory_path=inventory_path,
        attestation_key=TEST_KEY,
    )
    loaded = load_paper_workflow_manifest(output_path, attestation_key=TEST_KEY)

    assert {record["role"] for record in loaded["records"]} == {
        "manuscript_revision",
        "peer_review",
        "author_response",
    }
    assert {relation["kind"] for relation in loaded["relations"]} == {
        "revision_of",
        "reviews",
        "responds_to",
    }
    assert not {
        "evaluates_benchmark",
        "reproduces_result",
    }.intersection(relation["kind"] for relation in loaded["relations"])


def test_fetch_inventory_fails_closed_on_unknown_openreview_invitation(
    tmp_path: Path,
) -> None:
    inventory_path = _fetched_inventory(
        tmp_path, include_openreview=True, unknown_review_role=True
    )

    with pytest.raises(ProvenanceError, match="OpenReview invitation"):
        export_paper_workflow(
            None,
            tmp_path / "manifest.json",
            fetch_inventory_path=inventory_path,
            attestation_key=TEST_KEY,
        )


def _record(
    tmp_path: Path,
    *,
    record_id: str,
    role: str,
    source_family: str,
    source_url: str,
    revision_id: str,
    occurred_at: str,
    text: str,
) -> dict:
    source = tmp_path / f"{record_id}.txt"
    source.write_text(text, encoding="utf-8")
    return {
        "record_id": record_id,
        "work_id": "arxiv:2401.00001",
        "role": role,
        "source_family": source_family,
        "source_url": source_url,
        "source_file": source.name,
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "revision_id": revision_id,
        "occurred_at": occurred_at,
        "retrieved_at": "2026-08-24T09:00:00Z",
        "access_policy": "public scholarly record used for source audit",
        "parser": {"name": "fixture_text", "version": "1"},
    }


def _relation(
    relation_id: str,
    kind: str,
    source: dict,
    target: dict,
    quote: str,
) -> dict:
    text = Path(source["_base_directory"], source["source_file"]).read_text(
        encoding="utf-8"
    )
    return {
        "relation_id": relation_id,
        "kind": kind,
        "source_record_id": source["record_id"],
        "target_record_id": target["record_id"],
        "evidence_record_id": source["record_id"],
        "evidence_quote": quote,
        "evidence_char_start": text.index(quote),
    }


def _input(tmp_path: Path) -> dict:
    specs = (
        (
            "manuscript-v1",
            "manuscript_revision",
            "arxiv_record",
            "https://arxiv.org/abs/2401.00001v1",
            "v1",
            "2025-01-01T00:00:00Z",
            "Revision v1 reports Aurora accuracy of 81.0%.",
        ),
        (
            "manuscript-v2",
            "manuscript_revision",
            "arxiv_record",
            "https://arxiv.org/abs/2401.00001v2",
            "v2",
            "2025-02-01T00:00:00Z",
            (
                "Revision v2 supersedes revision v1 and reports Aurora accuracy of "
                "84.2% against benchmark-1."
            ),
        ),
        (
            "review-1",
            "peer_review",
            "openreview_note",
            "https://api2.openreview.net/notes?id=review-1",
            "review-1",
            "2025-02-03T00:00:00Z",
            "The review evaluates revision v2 and requests a variance analysis.",
        ),
        (
            "response-1",
            "author_response",
            "openreview_note",
            "https://api2.openreview.net/notes?id=response-1",
            "response-1",
            "2025-02-05T00:00:00Z",
            "This response addresses review-1 with a five-seed variance analysis.",
        ),
        (
            "benchmark-1",
            "benchmark_report",
            "openreview_note",
            "https://api2.openreview.net/notes?id=benchmark-1",
            "benchmark-1",
            "2025-02-10T00:00:00Z",
            "The reproduction of revision v2 measured Aurora accuracy of 83.9%.",
        ),
    )
    records = [
        _record(
            tmp_path,
            record_id=record_id,
            role=role,
            source_family=source_family,
            source_url=source_url,
            revision_id=revision_id,
            occurred_at=occurred_at,
            text=text,
        )
        for (
            record_id,
            role,
            source_family,
            source_url,
            revision_id,
            occurred_at,
            text,
        ) in specs
    ]
    for record in records:
        record["_base_directory"] = str(tmp_path)
    by_id = {record["record_id"]: record for record in records}
    relations = [
        _relation(
            "revision-v2-v1",
            "revision_of",
            by_id["manuscript-v2"],
            by_id["manuscript-v1"],
            "Revision v2 supersedes revision v1",
        ),
        _relation(
            "review-v2",
            "reviews",
            by_id["review-1"],
            by_id["manuscript-v2"],
            "The review evaluates revision v2",
        ),
        _relation(
            "response-review",
            "responds_to",
            by_id["response-1"],
            by_id["review-1"],
            "This response addresses review-1",
        ),
        _relation(
            "v2-benchmark",
            "evaluates_benchmark",
            by_id["manuscript-v2"],
            by_id["benchmark-1"],
            "reports Aurora accuracy of 84.2% against benchmark-1",
        ),
        _relation(
            "reproduction-v2",
            "reproduces_result",
            by_id["benchmark-1"],
            by_id["manuscript-v2"],
            "reproduction of revision v2 measured Aurora accuracy of 83.9%",
        ),
    ]
    for record in records:
        record.pop("_base_directory")
    return {
        "schema_version": "longworld.paper-workflow-input.v1",
        "source_status": "test_fixture",
        "authorization": {
            "record_id": "TEST-PAPER-FIXTURE-001",
            "scope": "read-only fixture export",
            "basis": "test fixture only; not a live API export",
            "reviewed_at": "2026-08-24T10:00:00Z",
        },
        "records": records,
        "relations": relations,
    }


def test_paper_export_preserves_body_and_executable_source_relations(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "manifest.json"
    input_path.write_text(json.dumps(_input(tmp_path)), encoding="utf-8")

    export_paper_workflow(
        input_path,
        output_path,
        attestation_key=TEST_KEY,
        generated_at="2026-08-24T11:00:00Z",
    )
    loaded = load_paper_workflow_manifest(output_path, attestation_key=TEST_KEY)

    assert loaded["data_stage"] == "source_inventory"
    assert loaded["hybrid_train_ready"] is False
    assert loaded["generation_integration"] == "disabled"
    assert loaded["records"][1]["text"].endswith("against benchmark-1.")
    assert {relation["kind"] for relation in loaded["relations"]} == {
        "revision_of",
        "reviews",
        "responds_to",
        "evaluates_benchmark",
        "reproduces_result",
    }
    relation = loaded["relations"][0]
    evidence = next(
        record
        for record in loaded["records"]
        if record["record_id"] == relation["evidence_record_id"]
    )
    start = relation["evidence_char_start"]
    assert (
        evidence["text"][start : start + len(relation["evidence_quote"])]
        == (relation["evidence_quote"])
    )


def test_paper_export_rejects_relation_not_grounded_at_declared_span(
    tmp_path: Path,
) -> None:
    payload = _input(tmp_path)
    payload["relations"][1]["evidence_char_start"] += 1

    with pytest.raises(ProvenanceError, match="evidence span"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_paper_export_rejects_semantically_invalid_relation(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    payload["relations"][1]["target_record_id"] = "benchmark-1"

    with pytest.raises(ProvenanceError, match="reviews relation"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_paper_export_rejects_caller_invented_arxiv_work_identity(
    tmp_path: Path,
) -> None:
    payload = _input(tmp_path)
    payload["records"][0]["work_id"] = "paper:invented"

    with pytest.raises(ProvenanceError, match="derived from its URL"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_paper_export_rejects_review_before_manuscript(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    payload["records"][2]["occurred_at"] = "2025-01-15T00:00:00Z"

    with pytest.raises(ProvenanceError, match="reviews relation is not chronological"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_paper_manifest_attestation_detects_tampering(tmp_path: Path) -> None:
    payload = build_paper_workflow_manifest(
        _input(tmp_path), tmp_path, generated_at="2026-08-24T11:00:00Z"
    )
    signed = attach_attestation(payload, TEST_KEY, purpose="source_manifest")
    signed["relations"][0]["target_record_id"] = "review-1"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(signed), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="attestation"):
        load_paper_workflow_manifest(path, attestation_key=TEST_KEY)


def test_paper_loader_rejects_attested_relation_hash_rebinding(tmp_path: Path) -> None:
    payload = build_paper_workflow_manifest(
        _input(tmp_path), tmp_path, generated_at="2026-08-24T11:00:00Z"
    )
    payload["relations"][0]["source_sha256"] = "0" * 64
    path = tmp_path / "rebound.json"
    path.write_text(
        json.dumps(attach_attestation(payload, TEST_KEY, purpose="source_manifest")),
        encoding="utf-8",
    )

    with pytest.raises(ProvenanceError, match="source hash"):
        load_paper_workflow_manifest(path, attestation_key=TEST_KEY)


def test_paper_export_rejects_unrelated_inventory_record(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    payload["records"].append(
        _record(
            tmp_path,
            record_id="unrelated-review",
            role="peer_review",
            source_family="openreview_note",
            source_url="https://api2.openreview.net/notes?id=unrelated-review",
            revision_id="unrelated-review",
            occurred_at="2025-02-12T00:00:00Z",
            text="This review belongs to an unrelated workflow.",
        )
    )

    with pytest.raises(ProvenanceError, match="not connected"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_paper_relation_requires_whole_target_identity(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    source = tmp_path / payload["records"][1]["source_file"]
    text = source.read_text(encoding="utf-8").replace("revision v1", "revision v10")
    source.write_text(text, encoding="utf-8")
    payload["records"][1]["source_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    payload["relations"][0]["evidence_quote"] = "Revision v2 supersedes revision v10"
    payload["relations"][3]["evidence_char_start"] = text.index(
        payload["relations"][3]["evidence_quote"]
    )

    with pytest.raises(ProvenanceError, match="omits target identity"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_paper_export_rejects_duplicate_relation_edge(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    duplicate = dict(payload["relations"][0])
    duplicate["relation_id"] = "same-edge-new-id"
    payload["relations"].append(duplicate)

    with pytest.raises(ProvenanceError, match="edge is duplicated"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def _add_funding_fact(payload: dict, tmp_path: Path) -> None:
    record = payload["records"][1]
    source = tmp_path / record["source_file"]
    funding = "Funding for this study was provided by the Aurora Research Foundation."
    raw_text = f"Contact funding-team@example.org. {source.read_text()} {funding}"
    source.write_text(raw_text, encoding="utf-8")
    record["source_sha256"] = hashlib.sha256(raw_text.encode()).hexdigest()
    record["derived_facts"] = [
        {
            "fact_id": "revision-added-funding",
            "field": "revision_added_text",
            "value": funding,
            "evidence_quote": funding,
            "evidence_char_start": raw_text.index(funding),
        }
    ]
    for relation in payload["relations"]:
        if relation["evidence_record_id"] == record["record_id"]:
            relation["evidence_char_start"] = raw_text.index(relation["evidence_quote"])


def test_paper_export_rebinds_derived_fact_to_cleaned_text_span_and_hash(
    tmp_path: Path,
) -> None:
    payload = _input(tmp_path)
    _add_funding_fact(payload, tmp_path)

    manifest = build_paper_workflow_manifest(
        payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
    )
    record = next(
        record
        for record in manifest["records"]
        if record["record_id"] == "manuscript-v2"
    )
    fact = record["derived_facts"][0]

    assert "funding-team@example.org" not in record["text"]
    assert (
        record["text"][
            fact["evidence_char_start"] : fact["evidence_char_start"]
            + len(fact["evidence_quote"])
        ]
        == fact["evidence_quote"]
    )
    assert fact["text_sha256"] == record["text_sha256"]


def test_paper_export_rejects_derived_fact_with_false_span(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    _add_funding_fact(payload, tmp_path)
    payload["records"][1]["derived_facts"][0]["evidence_char_start"] += 1

    with pytest.raises(ProvenanceError, match="derived fact evidence span"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_paper_export_rejects_filename_as_revision_fact(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    _add_funding_fact(payload, tmp_path)
    fact = payload["records"][1]["derived_facts"][0]
    fact["field"] = "source_file"
    fact["value"] = payload["records"][1]["source_file"]

    with pytest.raises(ProvenanceError, match="derived fact field"):
        build_paper_workflow_manifest(
            payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
        )


def test_paper_loader_rejects_attested_derived_fact_hash_rebinding(
    tmp_path: Path,
) -> None:
    payload = _input(tmp_path)
    _add_funding_fact(payload, tmp_path)
    manifest = build_paper_workflow_manifest(
        payload, tmp_path, generated_at="2026-08-24T11:00:00Z"
    )
    manifest["records"][1]["derived_facts"][0]["text_sha256"] = "0" * 64
    path = tmp_path / "rebound-fact.json"
    path.write_text(
        json.dumps(attach_attestation(manifest, TEST_KEY, purpose="source_manifest")),
        encoding="utf-8",
    )

    with pytest.raises(ProvenanceError, match="derived fact binding"):
        load_paper_workflow_manifest(path, attestation_key=TEST_KEY)
