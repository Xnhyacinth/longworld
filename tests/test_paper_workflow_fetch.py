from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import tarfile
import urllib.error
from pathlib import Path
from typing import Self

import pytest

from longworld.core.provenance import ProvenanceError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch_paper_workflow import (
    HttpRedirect,
    HttpResponse,
    _arxiv_record,
    _latex_sources,
    _openreview_records,
    _PinnedRedirectHandler,
    _validate_request,
    fetch_paper_workflow,
)


def _request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.paper-fetch-request.v1",
                "user_agent": "LongWorld/0.1 xnhyacinth@users.noreply.github.com",
                "authorization": {
                    "record_id": "PAPER-PUBLIC-001",
                    "scope": "bounded public scholarly metadata export",
                    "basis": "public arXiv and OpenReview records",
                    "reviewed_at": "2026-08-25T00:00:00Z",
                    "allowed_actions": [
                        "fetch_arxiv_metadata",
                        "fetch_openreview_forum",
                    ],
                },
                "arxiv_version_ids": ["2203.01928v1", "2203.01928v2"],
                "openreview_forum_ids": ["forum-1"],
                "fetch_arxiv_source": False,
                "requests_per_second": 2,
                "max_retries": 0,
            }
        ),
        encoding="utf-8",
    )
    return path


def _atom(version: str, *, updated: str) -> bytes:
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <id>https://arxiv.org/api/test</id>
  <entry>
    <id>http://arxiv.org/abs/2203.01928{version}</id>
    <updated>{updated}</updated>
    <published>2022-03-03T18:59:03Z</published>
    <title>Label-Free Explainability</title>
    <summary>Revision {version} changes the benchmark protocol.</summary>
    <author><name>Ada Researcher</name></author>
  </entry>
</feed>
""".encode()


def _openreview() -> bytes:
    return json.dumps(
        {
            "notes": [
                {
                    "id": "forum-1",
                    "forum": "forum-1",
                    "replyto": None,
                    "invitation": "MLRC/2022/-/Submission",
                    "pdate": 1690934400000,
                    "mdate": 1690934400000,
                    "content": {
                        "title": {"value": "A reproducibility study"},
                        "paper_url": {"value": "https://arxiv.org/abs/2203.01928v2"},
                        "abstract": {"value": "The central claim was reproduced."},
                    },
                },
                {
                    "id": "review-1",
                    "forum": "forum-1",
                    "replyto": "forum-1",
                    "invitation": "MLRC/2022/Submission1/-/Official_Review",
                    "cdate": 1691020800000,
                    "content": {
                        "summary": {"value": "The variance analysis is missing."}
                    },
                },
                {
                    "id": "response-1",
                    "forum": "forum-1",
                    "replyto": "review-1",
                    "invitation": "MLRC/2022/Submission1/-/Author_Response",
                    "cdate": 1691107200000,
                    "content": {
                        "response": {"value": "We added the requested analysis."}
                    },
                },
            ]
        },
        sort_keys=True,
    ).encode()


def _source_archive(*, unsafe_name: str | None = None) -> bytes:
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w:gz") as archive:
        for name, text in (
            (
                unsafe_name or "main.tex",
                "\\section{Method}\nAuthentic revision body.\n",
            ),
            ("parts/results.tex", "\\section{Results}\nAccuracy is 84.2.\n"),
        ):
            payload = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return target.getvalue()


def _response(url: str, body: bytes) -> HttpResponse:
    if "api/query" in url:
        content_type = "application/atom+xml"
    elif "/e-print/" in url:
        content_type = "application/x-eprint-tar"
    else:
        content_type = "application/json"
    return HttpResponse(
        body=body,
        status=200,
        final_url=url,
        content_type=content_type,
    )


def _http_get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
    if "api/query" in url and "2203.01928v1" in url:
        return _response(url, _atom("v1", updated="2022-03-03T18:59:03Z"))
    if "api/query" in url and "2203.01928v2" in url:
        return _response(url, _atom("v2", updated="2022-06-07T11:25:15Z"))
    if "api2.openreview.net/notes" in url:
        return _response(url, _openreview())
    raise AssertionError(url)


def test_fetch_paper_workflow_derives_api_identities_and_timestamps(
    tmp_path: Path,
) -> None:
    output = tmp_path / "inventory"
    request_path = _request(tmp_path)

    inventory_path = fetch_paper_workflow(
        request_path,
        output,
        http_get=_http_get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-25T01:00:00Z",
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    assert inventory["schema_version"] == "longworld.paper-fetch-inventory.v1"
    assert inventory["data_stage"] == "source_inventory"
    assert inventory["generation_integration"] == "disabled"
    assert inventory["hybrid_train_ready"] is False
    request_sha256 = hashlib.sha256(request_path.read_bytes()).hexdigest()
    assert inventory["request_file"] == "paper_fetch_request.json"
    assert inventory["request_sha256"] == request_sha256
    assert (
        hashlib.sha256((output / inventory["request_file"]).read_bytes()).hexdigest()
        == request_sha256
    )
    assert (
        inventory["fetch_receipt"]["user_agent_sha256"]
        == hashlib.sha256(
            b"LongWorld/0.1 xnhyacinth@users.noreply.github.com"
        ).hexdigest()
    )
    arxiv = [record for record in inventory["records"] if record["kind"] == "arxiv"]
    assert [(record["work_id"], record["revision_id"]) for record in arxiv] == [
        ("arxiv:2203.01928", "v1"),
        ("arxiv:2203.01928", "v2"),
    ]
    assert arxiv[1]["occurred_at"] == "2022-06-07T11:25:15Z"
    assert arxiv[1]["previous_revision_id"] == "v1"
    assert arxiv[1]["facts"][1]["value"].startswith("Revision v2")
    notes = [
        record for record in inventory["records"] if record["kind"] == "openreview"
    ]
    assert [record["record_id"] for record in notes] == [
        "openreview:forum-1",
        "openreview:review-1",
        "openreview:response-1",
    ]
    assert notes[0]["occurred_at"] == "2023-08-02T00:00:00Z"
    assert notes[1]["replyto"] == "forum-1"
    assert notes[2]["replyto"] == "review-1"
    assert notes[0]["linked_arxiv_ids"] == ["2203.01928v2"]
    assert all(
        retrieval["status"] == 200
        and retrieval["requested_url"] == retrieval["final_url"]
        and retrieval["content_type"]
        for retrieval in inventory["fetch_receipt"]["retrievals"]
    )
    assert all(
        hashlib.sha256((output / record["source_file"]).read_bytes()).hexdigest()
        == record["source_sha256"]
        for record in inventory["records"]
    )
    for record in inventory["records"]:
        source = json.loads(
            (output / record["source_file"]).read_text(encoding="utf-8")
        )
        assert source["request_sha256"] == request_sha256
        assert source["retrieval"]["final_url"] == record["retrieval_url"]
        assert source["retrieval"]["sha256"] == record["retrieval_sha256"]


def test_fetch_paper_workflow_rejects_arxiv_response_identity_mismatch(
    tmp_path: Path,
) -> None:
    def mismatch(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        if "2203.01928v1" in url:
            return _response(url, _atom("v9", updated="2022-03-03T18:59:03Z"))
        return _http_get(url, headers, timeout)

    output = tmp_path / "inventory"
    with pytest.raises(ProvenanceError, match="arXiv response identity"):
        fetch_paper_workflow(
            _request(tmp_path), output, http_get=mismatch, sleep=lambda _: None
        )
    assert not output.exists()


def test_fetch_paper_workflow_rejects_openreview_forum_rebinding(
    tmp_path: Path,
) -> None:
    payload = json.loads(_openreview())
    payload["notes"][1]["forum"] = "other-forum"

    def rebound(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        if "api2.openreview.net/notes" in url:
            return _response(url, json.dumps(payload).encode())
        return _http_get(url, headers, timeout)

    with pytest.raises(ProvenanceError, match="forum identity"):
        fetch_paper_workflow(
            _request(tmp_path),
            tmp_path / "inventory",
            http_get=rebound,
            sleep=lambda _: None,
        )


def test_fetch_paper_workflow_reports_challenge_without_partial_inventory(
    tmp_path: Path,
) -> None:
    def challenge(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        if "api2.openreview.net/notes" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return _http_get(url, headers, timeout)

    output = tmp_path / "inventory"
    with pytest.raises(ProvenanceError, match="OpenReview.*HTTP 403"):
        fetch_paper_workflow(
            _request(tmp_path), output, http_get=challenge, sleep=lambda _: None
        )
    assert not output.exists()


def test_fetch_paper_workflow_allows_arxiv_only_inventory(tmp_path: Path) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["openreview_forum_ids"] = []
    request_path.write_text(json.dumps(request), encoding="utf-8")

    inventory_path = fetch_paper_workflow(
        request_path,
        tmp_path / "inventory",
        http_get=_http_get,
        sleep=lambda _: None,
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    assert inventory["n_records"] == 2
    assert {record["kind"] for record in inventory["records"]} == {"arxiv"}


def test_fetch_paper_workflow_preserves_bounded_arxiv_source_text(
    tmp_path: Path,
) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["openreview_forum_ids"] = []
    request["fetch_arxiv_source"] = True
    request["authorization"]["allowed_actions"].append("fetch_arxiv_source")
    request_path.write_text(json.dumps(request), encoding="utf-8")
    source_archive = _source_archive()

    def with_source(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        if "/e-print/" in url:
            return _response(url, source_archive)
        return _http_get(url, headers, timeout)

    inventory_path = fetch_paper_workflow(
        request_path,
        tmp_path / "inventory",
        http_get=with_source,
        sleep=lambda _: None,
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    record = inventory["records"][0]
    source = json.loads(
        (inventory_path.parent / record["source_file"]).read_text(encoding="utf-8")
    )

    assert record["source_archive_sha256"] == hashlib.sha256(source_archive).hexdigest()
    assert record["latex_char_count"] > 50
    assert "Authentic revision body" in json.dumps(source)
    assert "Accuracy is 84.2" in json.dumps(source)
    assert (
        source["request_sha256"]
        == hashlib.sha256(request_path.read_bytes()).hexdigest()
    )
    assert source["retrieval"] == {
        "requested_url": "https://export.arxiv.org/api/query?id_list=2203.01928v1",
        "final_url": "https://export.arxiv.org/api/query?id_list=2203.01928v1",
        "status": 200,
        "content_type": "application/atom+xml",
        "redirect_chain": [],
        "sha256": hashlib.sha256(
            _atom("v1", updated="2022-03-03T18:59:03Z")
        ).hexdigest(),
    }
    assert source["source_archive"] == {
        "requested_url": "https://export.arxiv.org/e-print/2203.01928v1",
        "final_url": "https://export.arxiv.org/e-print/2203.01928v1",
        "status": 200,
        "content_type": "application/x-eprint-tar",
        "redirect_chain": [],
        "sha256": hashlib.sha256(source_archive).hexdigest(),
        "parser": "arxiv_source_tar_v2",
    }


def test_fetch_paper_workflow_rejects_unsafe_arxiv_archive_member(
    tmp_path: Path,
) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["openreview_forum_ids"] = []
    request["fetch_arxiv_source"] = True
    request["authorization"]["allowed_actions"].append("fetch_arxiv_source")
    request_path.write_text(json.dumps(request), encoding="utf-8")

    def unsafe(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        if "/e-print/" in url:
            return _response(url, _source_archive(unsafe_name="../main.tex"))
        return _http_get(url, headers, timeout)

    with pytest.raises(ProvenanceError, match="unsafe member"):
        fetch_paper_workflow(
            request_path,
            tmp_path / "inventory",
            http_get=unsafe,
            sleep=lambda _: None,
        )


def test_latex_archive_is_iterated_without_materializing_all_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
        raise AssertionError("getmembers materializes the complete remote archive")

    monkeypatch.setattr(tarfile.TarFile, "getmembers", forbidden)

    sources, _ = _latex_sources(_source_archive())

    assert [source["path"] for source in sources] == [
        "main.tex",
        "parts/results.tex",
    ]


@pytest.mark.parametrize(
    ("declared_size", "message"),
    (
        (64_000_001, "declared size"),
        (1_000_000, "compression ratio"),
    ),
)
def test_latex_archive_rejects_declared_size_and_compression_bombs(
    declared_size: int,
    message: str,
) -> None:
    target = io.BytesIO()
    member = tarfile.TarInfo("oversized.bin")
    member.size = declared_size
    target.write(member.tobuf())

    with pytest.raises(ProvenanceError, match=message):
        _latex_sources(target.getvalue())


def test_arxiv_parser_rejects_xml_declaration_after_prefix_limit() -> None:
    raw = (
        b'<?xml version="1.0"?>'
        + b" " * 5_000
        + b'<!DOCTYPE feed [<!ENTITY injected "Injected title">]>'
        + b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        + b"<id>http://arxiv.org/abs/2203.01928v1</id>"
        + b"<updated>2022-03-03T18:59:03Z</updated>"
        + b"<published>2022-03-03T18:59:03Z</published>"
        + b"<title>&injected;</title><summary>Summary.</summary>"
        + b"<author><name>Ada Researcher</name></author></entry></feed>"
    )

    with pytest.raises(ProvenanceError, match="unsupported XML declarations"):
        _arxiv_record(raw, "2203.01928v1")


def test_arxiv_parser_rejects_entry_identity_from_wrong_authority() -> None:
    raw = _atom("v1", updated="2022-03-03T18:59:03Z").replace(
        b"http://arxiv.org/abs/", b"https://attacker.invalid/abs/"
    )

    with pytest.raises(ProvenanceError, match="identity"):
        _arxiv_record(raw, "2203.01928v1")


def test_openreview_parser_rejects_excessive_derived_records() -> None:
    notes = [json.loads(_openreview())["notes"][0]]
    notes.extend(
        {
            "id": f"review-{index}",
            "forum": "forum-1",
            "replyto": "forum-1",
            "invitation": "MLRC/2022/Submission1/-/Official_Review",
            "cdate": 1691020800000 + index,
            "content": {"summary": {"value": f"Review {index}."}},
        }
        for index in range(512)
    )

    with pytest.raises(ProvenanceError, match="too many notes"):
        _openreview_records(json.dumps({"notes": notes}).encode(), "forum-1")


def test_openreview_parser_rejects_excessive_content_fields() -> None:
    payload = json.loads(_openreview())
    payload["notes"][0]["content"] = {
        f"field-{index}": {"value": f"value-{index}"} for index in range(65)
    }

    with pytest.raises(ProvenanceError, match="content is invalid"):
        _openreview_records(json.dumps(payload).encode(), "forum-1")


def test_fetch_rejects_action_not_covered_by_authorization(tmp_path: Path) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["openreview_forum_ids"] = []
    request["fetch_arxiv_source"] = True
    request_path.write_text(json.dumps(request), encoding="utf-8")
    source_archive = _source_archive()

    def with_source(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        if "/e-print/" in url:
            return _response(url, source_archive)
        return _http_get(url, headers, timeout)

    with pytest.raises(ProvenanceError, match="allowed_actions"):
        fetch_paper_workflow(
            request_path,
            tmp_path / "inventory",
            http_get=with_source,
            sleep=lambda _: None,
        )


def test_fetch_rejects_arxiv_source_action_without_arxiv_ids(tmp_path: Path) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["arxiv_version_ids"] = []
    request["fetch_arxiv_source"] = True
    request["authorization"]["allowed_actions"].append("fetch_arxiv_source")
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="requires arxiv_version_ids"):
        fetch_paper_workflow(
            request_path,
            tmp_path / "inventory",
            http_get=_http_get,
            sleep=lambda _: None,
        )


def test_fetch_publishes_private_directory_and_files(tmp_path: Path) -> None:
    output = tmp_path / "inventory"

    inventory_path = fetch_paper_workflow(
        _request(tmp_path), output, http_get=_http_get, sleep=lambda _: None
    )

    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE(inventory_path.stat().st_mode) == 0o600
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in output.rglob("*")
        if path.is_file()
    )


def test_fetch_rejects_dangling_output_symlink_before_network(
    tmp_path: Path,
) -> None:
    output = tmp_path / "inventory"
    output.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    requested = False

    def unexpected(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        nonlocal requested
        requested = True
        return _response(url, b"unexpected")

    with pytest.raises(ProvenanceError, match="already exists"):
        fetch_paper_workflow(
            _request(tmp_path),
            output,
            http_get=unexpected,
            sleep=lambda _: None,
        )

    assert requested is False
    assert output.is_symlink()


def test_fetch_rejects_source_specific_response_size_before_parse(
    tmp_path: Path,
) -> None:
    def oversized(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        return _response(url, b"x" * 4_000_001)

    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["arxiv_version_ids"] = ["2203.01928v1"]
    request["openreview_forum_ids"] = []
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="response.*size limit"):
        fetch_paper_workflow(
            request_path,
            tmp_path / "inventory",
            http_get=oversized,
            sleep=lambda _: None,
        )


class _Headers:
    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class _UrlopenResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        final_url: str,
        content_type: str = "application/atom+xml",
    ) -> None:
        self._body = body
        self.status = status
        self._final_url = final_url
        self.headers = _Headers(content_type)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._body[:limit]

    def geturl(self) -> str:
        return self._final_url


class _Opener:
    def __init__(self, response: _UrlopenResponse) -> None:
        self.response = response

    def open(self, request: object, *, timeout: float) -> _UrlopenResponse:
        assert timeout == 90.0
        return self.response


@pytest.mark.parametrize(
    ("status", "final_url", "content_type", "message"),
    (
        (
            200,
            "https://redirect.invalid/api/query?id_list=2203.01928v1",
            "application/atom+xml",
            "final URL",
        ),
        (
            200,
            "https://export.arxiv.org/api/query?id_list=2203.01928v1",
            "text/html",
            "content type",
        ),
        (
            204,
            "https://export.arxiv.org/api/query?id_list=2203.01928v1",
            "application/atom+xml",
            "HTTP 204",
        ),
    ),
)
def test_default_fetch_binds_status_final_url_and_content_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    final_url: str,
    content_type: str,
    message: str,
) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["arxiv_version_ids"] = ["2203.01928v1"]
    request["openreview_forum_ids"] = []
    request_path.write_text(json.dumps(request), encoding="utf-8")

    response = _UrlopenResponse(
        _atom("v1", updated="2022-03-03T18:59:03Z"),
        status=status,
        final_url=final_url,
        content_type=content_type,
    )
    redirect_handlers: list[object] = []

    def build_opener(*handlers: object) -> _Opener:
        redirect_handlers.extend(handlers)
        return _Opener(response)

    monkeypatch.setattr("urllib.request.build_opener", build_opener)

    with pytest.raises(ProvenanceError, match=message):
        fetch_paper_workflow(request_path, tmp_path / "inventory", sleep=lambda _: None)

    assert len(redirect_handlers) == 1
    handler = redirect_handlers[0]
    assert isinstance(handler, urllib.request.HTTPRedirectHandler)
    assert (
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1/internal",
        )
        is None
    )


def test_fetch_rejects_malformed_client_response_metadata(tmp_path: Path) -> None:
    def malformed(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        return HttpResponse(
            body=_atom("v1", updated="2022-03-03T18:59:03Z"),
            status=200,
            final_url=url,
            content_type=None,  # type: ignore[arg-type]
        )

    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["arxiv_version_ids"] = ["2203.01928v1"]
    request["openreview_forum_ids"] = []
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="response metadata"):
        fetch_paper_workflow(
            request_path,
            tmp_path / "inventory",
            http_get=malformed,
            sleep=lambda _: None,
        )


def test_default_fetch_allows_one_exact_arxiv_source_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["arxiv_version_ids"] = ["2203.01928v1"]
    request["openreview_forum_ids"] = []
    request["fetch_arxiv_source"] = True
    request["authorization"]["allowed_actions"].append("fetch_arxiv_source")
    request_path.write_text(json.dumps(request), encoding="utf-8")
    archive = _source_archive()

    class RedirectingOpener:
        def __init__(self, handler: urllib.request.HTTPRedirectHandler) -> None:
            self.handler = handler

        def open(
            self, request: urllib.request.Request, *, timeout: float
        ) -> _UrlopenResponse:
            assert timeout == 90.0
            url = request.full_url
            if "/api/query" in url:
                return _UrlopenResponse(
                    _atom("v1", updated="2022-03-03T18:59:03Z"),
                    final_url=url,
                )
            target = "https://export.arxiv.org/src/2203.01928v1"
            redirected = self.handler.redirect_request(
                request,
                None,
                301,
                "Moved Permanently",
                {},
                target,
            )
            assert redirected is not None
            return _UrlopenResponse(
                archive,
                final_url=target,
                content_type="application/x-eprint-tar",
            )

    def build_opener(
        handler: urllib.request.HTTPRedirectHandler,
    ) -> RedirectingOpener:
        return RedirectingOpener(handler)

    monkeypatch.setattr("urllib.request.build_opener", build_opener)

    inventory_path = fetch_paper_workflow(
        request_path,
        tmp_path / "inventory",
        sleep=lambda _: None,
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    archive_retrieval = next(
        retrieval
        for retrieval in inventory["fetch_receipt"]["retrievals"]
        if retrieval["source_family"] == "arxiv_source_archive"
    )
    assert archive_retrieval["final_url"] == (
        "https://export.arxiv.org/src/2203.01928v1"
    )
    assert archive_retrieval["redirect_chain"] == [
        {
            "status": 301,
            "from_url": "https://export.arxiv.org/e-print/2203.01928v1",
            "to_url": "https://export.arxiv.org/src/2203.01928v1",
        }
    ]
    record = inventory["records"][0]
    source = json.loads(
        (inventory_path.parent / record["source_file"]).read_text(encoding="utf-8")
    )
    assert (
        source["source_archive"]["redirect_chain"]
        == archive_retrieval["redirect_chain"]
    )


@pytest.mark.parametrize(
    ("final_url", "redirect_chain"),
    (
        (
            "https://attacker.invalid/src/2203.01928v1",
            (
                HttpRedirect(
                    status=301,
                    from_url="https://export.arxiv.org/e-print/2203.01928v1",
                    to_url="https://attacker.invalid/src/2203.01928v1",
                ),
            ),
        ),
        (
            "https://export.arxiv.org/pdf/2203.01928v1",
            (
                HttpRedirect(
                    status=301,
                    from_url="https://export.arxiv.org/e-print/2203.01928v1",
                    to_url="https://export.arxiv.org/pdf/2203.01928v1",
                ),
            ),
        ),
        (
            "https://export.arxiv.org/src/2203.01928v1",
            (
                HttpRedirect(
                    status=301,
                    from_url="https://export.arxiv.org/e-print/2203.01928v1",
                    to_url="https://export.arxiv.org/src/2203.01928v1",
                ),
                HttpRedirect(
                    status=301,
                    from_url="https://export.arxiv.org/src/2203.01928v1",
                    to_url="https://export.arxiv.org/src/2203.01928v1",
                ),
            ),
        ),
    ),
)
def test_fetch_rejects_cross_authority_wrong_path_and_multiple_redirects(
    tmp_path: Path,
    final_url: str,
    redirect_chain: tuple[HttpRedirect, ...],
) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["arxiv_version_ids"] = ["2203.01928v1"]
    request["openreview_forum_ids"] = []
    request["fetch_arxiv_source"] = True
    request["authorization"]["allowed_actions"].append("fetch_arxiv_source")
    request_path.write_text(json.dumps(request), encoding="utf-8")

    def redirected(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        if "/api/query" in url:
            return _http_get(url, headers, timeout)
        return HttpResponse(
            body=_source_archive(),
            status=200,
            final_url=final_url,
            content_type="application/x-eprint-tar",
            redirect_chain=redirect_chain,
        )

    with pytest.raises(ProvenanceError, match="redirect chain"):
        fetch_paper_workflow(
            request_path,
            tmp_path / "inventory",
            http_get=redirected,
            sleep=lambda _: None,
        )


def test_redirect_handler_rejects_wrong_target_and_second_hop_before_follow() -> None:
    source_url = "https://export.arxiv.org/e-print/2203.01928v1"
    source_target = "https://export.arxiv.org/src/2203.01928v1"
    handler = _PinnedRedirectHandler(source_url)
    request = urllib.request.Request(source_url)

    assert (
        handler.redirect_request(
            request,
            None,
            301,
            "Moved Permanently",
            {},
            "https://attacker.invalid/src/2203.01928v1",
        )
        is None
    )
    assert (
        handler.redirect_request(
            request,
            None,
            301,
            "Moved Permanently",
            {},
            "https://export.arxiv.org/pdf/2203.01928v1",
        )
        is None
    )
    redirected = handler.redirect_request(
        request,
        None,
        301,
        "Moved Permanently",
        {},
        source_target,
    )
    assert redirected is not None
    assert (
        handler.redirect_request(
            redirected,
            None,
            301,
            "Moved Permanently",
            {},
            source_target,
        )
        is None
    )


def test_checked_in_fetch_requests_have_matching_structured_actions() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = {
        "arxiv_public_fetch_request_v1.json": {
            "fetch_arxiv_metadata",
            "fetch_arxiv_source",
        },
        "openreview_public_fetch_request_v1.json": {"fetch_openreview_forum"},
        "paper_public_fetch_request_v1.json": {
            "fetch_arxiv_metadata",
            "fetch_arxiv_source",
            "fetch_openreview_forum",
        },
    }

    for name, actions in expected.items():
        payload = json.loads((root / "configs" / name).read_text(encoding="utf-8"))
        request = _validate_request(payload)
        assert set(request["authorization"]["allowed_actions"]) == actions
