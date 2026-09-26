"""Freeze bounded linked entity revisions older than their source list revision."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_row_binding
from scripts.p104_freeze_width_revisions import RateLimiter
from scripts.p111_wiki_linked_freeze import _sha
from scripts.p111_wiki_linked_support import _snapshot

SCHEMA = "longworld.p111-wiki-linked-target-freeze.v1"
API = "https://en.wikipedia.org/w/api.php"
PLAIN = re.compile(r"[A-Za-z0-9][^\[\]{}|<>]{1,70}\Z")
MAX_BYTES = 8 * 1024 * 1024


def _safe_text(value: str) -> bool:
    return bool(PLAIN.fullmatch(value.strip())) and not value.strip().startswith("http")


def _url_key(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.netloc != "en.wikipedia.org" or not parsed.path.startswith("/wiki/"):
        raise ValueError("P111 source URL outside expected Wiki article namespace")
    return urllib.parse.unquote(parsed.path).replace("_", " ").casefold()


def plan(
    raw_manifest: Path, support_ledger: Path, cap_per_group: int = 8
) -> tuple[list[dict], dict]:
    raw = json.loads(raw_manifest.read_text())
    support = json.loads(support_ledger.read_text())
    if support["raw_manifest_sha256"] != _sha(raw_manifest) or len(
        raw["records"]
    ) != len(support["pages"]):
        raise ValueError("P111 source/support pin mismatch")
    records = {r["doc_id"]: r for r in raw["records"]}
    prior_titles = {r["title"].casefold() for r in raw["records"]}
    prior_urls = {_url_key(r["page_url"]) for r in raw["records"]}
    # The prior P108 source pool includes P95/P97 ancestry; exclude any target
    # already present there to avoid cross-split or duplicate reader support.
    prior_pool_path = (
        ROOT / "data/capability_records/p108_wiki_autotopic_prior_v1/source_pool.json"
    )
    prior_pool = json.loads(prior_pool_path.read_text())
    for source in prior_pool["sources"]:
        snapshot_path = ROOT / source["snapshot"]["path"]
        if _sha(snapshot_path) != source["snapshot"]["sha256"]:
            raise ValueError("P111 prior source snapshot pin drift")
        snapshot = json.loads(snapshot_path.read_text())
        prior_titles.update(doc["title"].casefold() for doc in snapshot["documents"])
        prior_urls.update(_url_key(doc["page_url"]) for doc in snapshot["documents"])
    jobs = []
    rejection = Counter()
    used_targets = set()
    group_count = Counter()
    for page in sorted(support["pages"], key=lambda p: (p["source_group"], p["title"])):
        if not page["candidates"]:
            continue
        record = records[page["doc_id"]]
        visible_rows = wiki_row_binding._rows(_snapshot(record)["text"])
        values = Counter(
            (key, cell.value.casefold())
            for row in visible_rows
            for key, cell in row.columns
        )
        candidates = sorted(
            page["candidates"],
            key=lambda c: (c["name"].casefold(), c["target_title"].casefold()),
        )
        for candidate in candidates:
            group = page["source_group"]
            if group_count[group] >= cap_per_group:
                rejection["group_cap"] += 1
                continue
            target = candidate["target_title"]
            target_url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(
                target.replace(" ", "_"), safe="()"
            )
            if (
                target.casefold() in prior_titles
                or _url_key(target_url) in prior_urls
                or target.casefold() in used_targets
            ):
                rejection["prior_or_batch_title_overlap"] += 1
                continue
            choices = [
                (key, value)
                for key, value in candidate["visible_columns"].items()
                if key.casefold()
                not in {"name", "station", "site", "castle", "fortress", "institution"}
                and _safe_text(key)
                and _safe_text(value)
                and value.casefold() not in target.casefold()
                and values[(key, value.casefold())] == 1
            ]
            if not choices:
                rejection["no_unique_visible_nonname_selector"] += 1
                continue
            choices.sort(key=lambda x: ("code" not in x[0].casefold(), len(x[1]), x[0]))
            selector_key, selector_value = choices[0]
            job = {
                "source_group": group,
                "source_doc_id": page["doc_id"],
                "source_title": page["title"],
                "source_revid": record["revid"],
                "source_revision_timestamp": record["revision_timestamp"],
                "source_snapshot_path": record["source_snapshot_path"],
                "source_snapshot_sha256": record["source_snapshot_sha256"],
                "domain": page["domain"],
                "topic": page["topic"],
                "split": page["split"],
                "row_name": candidate["name"],
                "row_name_span": candidate["visible_name_span"],
                "raw_table_line": candidate["raw_table_line"],
                "selector_key": selector_key,
                "selector_value": selector_value,
                "target_title": target,
                "target_url": target_url,
                "user_agent": record["user_agent"],
            }
            jobs.append(job)
            group_count[group] += 1
            used_targets.add(target.casefold())
    return jobs, {
        "raw_manifest_sha256": _sha(raw_manifest),
        "support_ledger_sha256": _sha(support_ledger),
        "prior_pool_sha256": _sha(prior_pool_path),
        "cap_per_group": cap_per_group,
        "candidate_rows": sum(len(p["candidates"]) for p in support["pages"]),
        "rejected": dict(sorted(rejection.items())),
        "selected_groups": dict(sorted(group_count.items())),
    }


def _request_url(job: dict) -> str:
    return (
        API
        + "?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "prop": "revisions",
                "titles": job["target_title"],
                "rvstart": job["source_revision_timestamp"],
                "rvdir": "older",
                "rvlimit": 1,
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "format": "json",
                "formatversion": 2,
            }
        )
    )


def _validate(raw: bytes, job: dict) -> dict:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("target_response_empty_or_large")
    payload = json.loads(raw)
    pages = payload.get("query", {}).get("pages", [])
    if payload.get("error") or len(pages) != 1:
        raise ValueError("target_api_error_or_ambiguous")
    page = pages[0]
    if page.get("title") != job["target_title"]:
        raise ValueError("target_title_redirect_or_normalization")
    revisions = page.get("revisions", [])
    if len(revisions) != 1:
        raise ValueError("target_revision_missing")
    revision = revisions[0]
    content = revision.get("slots", {}).get("main", {}).get("content")
    if (
        not isinstance(content, str)
        or not content
        or revision.get("timestamp", "") > job["source_revision_timestamp"]
    ):
        raise ValueError("target_revision_or_content_invalid")
    return {
        "target_revid": revision["revid"],
        "target_revision_timestamp": revision["timestamp"],
        "wikitext_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def _fetch(job: dict, output_dir: Path, limiter: RateLimiter) -> dict:
    key = hashlib.sha256(
        (job["source_doc_id"] + "\x00" + job["target_title"]).encode()
    ).hexdigest()[:20]
    path = output_dir / "responses" / f"target-{key}.json"
    if path.exists():
        raw = path.read_bytes()
    else:
        url = _request_url(job)
        limiter.wait()
        request = urllib.request.Request(
            url, headers={"User-Agent": job["user_agent"], "Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(MAX_BYTES + 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(raw)
        temp.replace(path)
    try:
        identity = _validate(raw, job)
    except (ValueError, json.JSONDecodeError) as error:
        return {
            **job,
            "status": "rejected",
            "reason": str(error),
            "response_path": str(path.relative_to(output_dir)),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        }
    return {
        **job,
        **identity,
        "status": "frozen",
        "response_path": str(path.relative_to(output_dir)),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
    }


def run(
    raw_manifest: Path,
    support_ledger: Path,
    output_dir: Path,
    *,
    verify_only: bool = False,
) -> dict:
    raw_manifest, support_ledger = (
        (ROOT / raw_manifest).absolute(),
        (ROOT / support_ledger).absolute(),
    )
    output_dir = (ROOT / output_dir).absolute()
    jobs, plan_info = plan(raw_manifest, support_ledger)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "single_writer.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest_path = output_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest["plan"] != plan_info or len(manifest["records"]) != len(jobs):
                raise ValueError("P111 target completed plan drift")
            for job, record in zip(jobs, manifest["records"], strict=True):
                if any(record[key] != job[key] for key in job):
                    raise ValueError("P111 target job identity drift")
                path = output_dir / record["response_path"]
                if _sha(path) != record["response_sha256"]:
                    raise ValueError("P111 target response SHA drift")
                if (
                    record["status"] == "frozen"
                    and _validate(path.read_bytes(), job)["target_revid"]
                    != record["target_revid"]
                ):
                    raise ValueError("P111 target revision drift")
            return manifest
        if verify_only:
            raise ValueError("P111 target manifest missing")
        limiter = RateLimiter(2.0)
        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(
                executor.map(lambda job: _fetch(job, output_dir, limiter), jobs)
            )
        result = {
            "schema": SCHEMA,
            "plan": plan_info,
            "planned_targets": len(jobs),
            "frozen_targets": sum(r["status"] == "frozen" for r in records),
            "rejected_targets": sum(r["status"] == "rejected" for r in records),
            "records": records,
            "reader_tasks": 0,
            "train_ready": False,
        }
        manifest_path.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--support-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(
        args.raw_manifest,
        args.support_ledger,
        args.output_dir,
        verify_only=args.verify_only,
    )
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("planned_targets", "frozen_targets", "rejected_targets")
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
