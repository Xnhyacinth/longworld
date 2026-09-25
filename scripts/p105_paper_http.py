"""Bounded arXiv transport for the existing provenance-preserving fetcher.

The host proxy inconsistently returns 406 to Python HTTP clients for uncached
arXiv API URLs. curl succeeds for the same URL. This adapter keeps curl to one
HTTPS request at a time and allows only arXiv's known e-print → src redirect.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path

from scripts.fetch_paper_workflow import (
    HttpRedirect,
    HttpResponse,
    _allowed_redirect_url,
)

MAX_BYTES = 64_000_001
MIN_INTERVAL_SECONDS = 3.2


def _once(
    url: str, headers: dict[str, str], timeout: float, max_bytes: int
) -> tuple[HttpResponse, str | None]:
    if not url.startswith("https://export.arxiv.org/") or any(
        "\n" in key + value or "\r" in key + value for key, value in headers.items()
    ):
        raise ValueError("P105 HTTP destination or header is unsafe")
    with tempfile.TemporaryDirectory(prefix="p105_arxiv_http_") as temporary:
        body = Path(temporary) / "body"
        header_file = Path(temporary) / "headers"
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--proto",
            "=https",
            "--max-redirs",
            "0",
            "--max-time",
            str(int(timeout)),
            "--max-filesize",
            str(max_bytes),
            "--output",
            str(body),
            "--dump-header",
            str(header_file),
            "--write-out",
            "%{http_code}\n%{url_effective}\n%{content_type}",
        ]
        for key, value in headers.items():
            command.extend(("--header", f"{key}: {value}"))
        command.append(url)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                f"P105 curl transport failed: {result.returncode}:{result.stderr[:160]}"
            )
        status_text, final_url, content_type = result.stdout.split("\n", 2)
        if (
            not status_text.isdecimal()
            or not body.is_file()
            or body.stat().st_size > max_bytes
        ):
            raise ValueError("P105 curl response metadata or size invalid")
        status = int(status_text)
        if final_url != url:
            raise ValueError("P105 curl changed URL without a validated redirect")
        headers_raw = header_file.read_text(errors="replace")
        matches = re.findall(
            r"^location:\s*(\S+)\s*$", headers_raw, re.IGNORECASE | re.MULTILINE
        )
        location = urllib.parse.urljoin(url, matches[-1]) if matches else None
        return HttpResponse(
            body.read_bytes(), status, final_url, content_type.strip()
        ), location


def get_metadata(url: str, user_agent: str) -> bytes:
    response, location = _once(
        url, {"User-Agent": user_agent, "Accept": "*/*"}, 40.0, 4_000_001
    )
    if response.status != 200 or location is not None or len(response.body) > 4_000_000:
        raise ValueError(
            f"arXiv metadata HTTP {response.status} or invalid redirect/size"
        )
    if response.content_type.partition(";")[0].strip().lower() not in {
        "application/atom+xml",
        "application/xml",
        "text/xml",
    }:
        raise ValueError("arXiv metadata content type changed")
    return response.body


def get_source(
    url: str,
    headers: dict[str, str],
    timeout: float,
    *,
    sleep=time.sleep,
) -> HttpResponse:
    first, location = _once(url, headers, timeout, MAX_BYTES)
    if first.status != 301:
        return first
    expected = _allowed_redirect_url(url)
    if expected is None or location != expected:
        raise ValueError(
            "P105 source redirect differs from official arXiv e-print → src"
        )
    sleep(MIN_INTERVAL_SECONDS)
    second, further = _once(expected, headers, timeout, MAX_BYTES)
    if further is not None:
        raise ValueError("P105 source redirect chain exceeds one hop")
    # The outer fetcher paces logical calls from the start of this callback.
    # Leave a gap after the physical second call as well.
    sleep(MIN_INTERVAL_SECONDS)
    return HttpResponse(
        body=second.body,
        status=second.status,
        final_url=second.final_url,
        content_type=second.content_type,
        redirect_chain=(HttpRedirect(status=301, from_url=url, to_url=expected),),
    )
