"""Scale pinned Wiki HTML table QA with one globally rate-limited fetch stream.

Plan first, acquire the 98 P119 oldid pages, then compile frozen P122 grids and
P126 tasks in local worker processes. No request is issued by a worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_merge import verify_merge
from longworld.synthesis.wiki_adapter import HttpError, WikiHttpFetcher
from scripts import p122_wiki_html_grid as html_grid
from scripts import p126_wiki_html_table_tasks as table_tasks

SCHEMA = "longworld.p131-wiki-html-campaign.v1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def line(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def pinned(value: dict[str, str]) -> Path:
    return html_grid.pinned(value)


def config_data(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or not 1 <= config.get("max_pages_per_chunk", 0) <= 24
        or not 0 < config.get("requests_per_second", 0) <= 0.5
        or not 0 <= config.get("max_429_retries", -1) <= 2
        or not 1 <= config.get("local_workers", 0) <= 2
        or not 1 <= config.get("max_html_bytes_per_page", 0) <= 5_000_000
    ):
        raise ValueError("invalid P131 campaign config")
    for key in (
        "source_manifest",
        "source_pool",
        "source_ledger",
        "existing_html_source",
    ):
        pinned(config[key])
    return config


def source_config(config: dict, titles: list[str]) -> dict:
    return {
        "schema": html_grid.SCHEMA + ".config",
        "api": config["api"],
        "requests_per_second": config["requests_per_second"],
        "max_html_bytes_per_page": config["max_html_bytes_per_page"],
        "source_manifest": config["source_manifest"],
        "source_pool": config["source_pool"],
        "source_ledger": config["source_ledger"],
        "titles": titles,
    }


def plan(config_path: Path, output: Path) -> dict:
    config = config_data(config_path)
    if output.exists():
        raise ValueError("campaign plan output already exists")
    pool = json.loads(pinned(config["source_pool"]).read_text())
    if (
        pool.get("schema") != "longworld.source-batch-pool.v2"
        or len(pool["sources"]) != 98
    ):
        raise ValueError("expected exact 98-source P119 pool")
    rows = []
    for source in pool["sources"]:
        snapshot = json.loads(pinned(source["snapshot"]).read_text())
        if len(snapshot["documents"]) != 1:
            raise ValueError("P119 source is not single-page")
        doc = snapshot["documents"][0]
        rows.append(
            {
                "title": doc["title"],
                "oldid": snapshot["source"]["revisions"][doc["title"]],
                "snapshot": source["snapshot"],
                "source_group": snapshot["snapshot_id"],
                "domain": source["domain"],
                "topic": source["topic"],
                "split": source["split"],
                "revision_url": doc["revision_url"],
            }
        )
    for key in ("title", "oldid", "source_group", "revision_url"):
        if len({row[key] for row in rows}) != len(rows):
            raise ValueError(f"duplicate P119 {key}")
    rows.sort(key=lambda row: (row["domain"], row["topic"], row["title"]))
    size = config["max_pages_per_chunk"]
    chunks = [rows[start : start + size] for start in range(0, len(rows), size)]
    output.mkdir(parents=True)
    for index, chunk in enumerate(chunks):
        chunk_dir = output / "chunks" / f"chunk{index:02d}"
        chunk_dir.mkdir(parents=True)
        (chunk_dir / "planned_source_config.json").write_bytes(
            encoded(source_config(config, [row["title"] for row in chunk]))
        )
        html_grid.sources(source_config(config, [row["title"] for row in chunk]))
    result = {
        "schema": SCHEMA + ".plan",
        "code_sha256": digest(Path(__file__).read_bytes()),
        "config_sha256": digest(config_path.read_bytes()),
        "source_pool_sha256": config["source_pool"]["sha256"],
        "planned_pages": len(rows),
        "chunks": len(chunks),
        "max_pages_per_chunk": max(map(len, chunks)),
        "planned_domain_counts": dict(
            sorted(Counter(row["domain"] for row in rows).items())
        ),
        "planned_topic_counts": dict(
            sorted(Counter(row["topic"] for row in rows).items())
        ),
        "pages": rows,
    }
    (output / "plan.json").write_bytes(encoded(result))
    return result


def old_cache(config: dict) -> dict[tuple[str, int], dict]:
    previous = json.loads(pinned(config["existing_html_source"]).read_text())
    if previous.get("schema") != html_grid.SCHEMA + ".source":
        raise ValueError("old HTML source schema differs")
    return {(row["title"], row["oldid"]): row for row in previous["records"]}


class RateLimited(RuntimeError):
    pass


class RateClient:
    def __init__(self, config: dict):
        self.client = WikiHttpFetcher(config["api"])
        self.interval = 1 / config["requests_per_second"]
        self.max_retries = config["max_429_retries"]
        self.last_attempt: float | None = None
        self.attempts = 0

    def get_json(self, params: dict) -> dict:
        for retry in range(self.max_retries + 1):
            if self.last_attempt is not None:
                time.sleep(
                    max(0, self.interval - (time.monotonic() - self.last_attempt))
                )
            self.last_attempt = time.monotonic()
            self.attempts += 1
            try:
                return self.client.get_json(params)
            except HttpError as error:
                cause = error.__cause__
                limited = getattr(cause, "code", None) == 429 or any(
                    marker in str(error).lower()
                    for marker in ("429", "ratelimit", "maxlag")
                )
                if not limited:
                    raise
                if retry == self.max_retries:
                    raise RateLimited("persistent MediaWiki rate limit") from error
                time.sleep(self.interval * (retry + 1))
        raise AssertionError("retry loop exhausted")


def cache_paths(source: dict, source_dir: Path) -> tuple[Path, Path]:
    key = html_grid.sha(f"{source['title']}\0{source['oldid']}".encode())[:20]
    return (
        source_dir / "html" / f"{key}.html",
        source_dir / "html" / f"{key}.receipt.json",
    )


def check_cache(source: dict, html_path: Path, receipt_path: Path, cap: int) -> None:
    if html_path.exists() != receipt_path.exists() or not html_path.is_file():
        raise ValueError("incomplete HTML/receipt pair")
    html = html_path.read_bytes()
    receipt = json.loads(receipt_path.read_text())
    if (
        not html
        or len(html) > cap
        or any(
            (
                receipt.get("requested_title") != source["title"],
                receipt.get("requested_oldid") != source["oldid"],
                receipt.get("parsed_title") != source["title"],
                receipt.get("parsed_revid") != source["oldid"],
                receipt.get("snapshot_sha256") != source["snapshot"]["sha256"],
                receipt.get("html_sha256") != digest(html),
                receipt.get("html_bytes") != len(html),
            )
        )
    ):
        raise ValueError("HTML/receipt source pin differs")


def acquire(
    source: dict, source_dir: Path, old: dict, client: RateClient, cap: int
) -> str:
    html_path, receipt_path = cache_paths(source, source_dir)
    if html_path.exists() or receipt_path.exists():
        check_cache(source, html_path, receipt_path, cap)
        return "campaign_cache"
    old_row = old.get((source["title"], source["oldid"]))
    if old_row is not None:
        if old_row["snapshot"] != source["snapshot"]:
            raise ValueError("old cached snapshot differs")
        previous_html = pinned(
            {"path": old_row["html_path"], "sha256": old_row["html_sha256"]}
        )
        previous_receipt = pinned(old_row["fetch_receipt"])
        html_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(previous_html, html_path)
        shutil.copyfile(previous_receipt, receipt_path)
        check_cache(source, html_path, receipt_path, cap)
        return "reused_p122"
    payload = client.get_json(
        {
            "action": "parse",
            "format": "json",
            "formatversion": 2,
            "oldid": source["oldid"],
            "prop": "text",
        }
    )
    parsed = payload.get("parse", {})
    if (
        parsed.get("revid") != source["oldid"]
        or parsed.get("title") != source["title"]
        or not isinstance(parsed.get("text"), str)
    ):
        raise ValueError("MediaWiki oldid/title/content changed")
    html = parsed["text"].encode()
    if not html or len(html) > cap:
        raise ValueError("HTML empty or above byte cap")
    receipt = {
        "requested_title": source["title"],
        "requested_oldid": source["oldid"],
        "parsed_title": parsed["title"],
        "parsed_revid": parsed["revid"],
        "html_sha256": digest(html),
        "html_bytes": len(html),
        "snapshot_sha256": source["snapshot"]["sha256"],
    }
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_bytes(html)
    receipt_path.write_bytes(html_grid.encoded(receipt))
    check_cache(source, html_path, receipt_path, cap)
    return "fetched"


def compile_chunk(args: tuple[str, list[str], dict]) -> dict:
    chunk_text, titles, config = args
    chunk = Path(chunk_text)
    source_config_path = chunk / "source_config.json"
    value = source_config(config, titles)
    data = encoded(value)
    if source_config_path.exists() and source_config_path.read_bytes() != data:
        raise ValueError("frozen effective chunk source config differs")
    if not source_config_path.exists():
        source_config_path.write_bytes(data)
    source_dir, grid_dir, task_dir = chunk / "source", chunk / "grid", chunk / "tasks"
    html_grid.freeze(
        source_config_path,
        source_dir,
        verify_only=(source_dir / "source_manifest.json").exists(),
    )
    grid = html_grid.compile_grids(source_dir, grid_dir, verify_only=grid_dir.exists())
    task_config = {
        "schema": table_tasks.SCHEMA + ".config",
        "source_manifest": {
            "path": str((source_dir / "source_manifest.json").relative_to(ROOT)),
            "sha256": html_grid.sha((source_dir / "source_manifest.json").read_bytes()),
        },
        "source_pool": config["source_pool"],
        "grid_manifest": {
            "path": str((grid_dir / "manifest.json").relative_to(ROOT)),
            "sha256": html_grid.sha((grid_dir / "manifest.json").read_bytes()),
        },
        "max_tasks_per_table": config["max_tasks_per_table"],
        "max_tasks_per_page": config["max_tasks_per_page"],
        "max_seq_len": config["max_seq_len"],
    }
    task_config_path = chunk / "task_config.json"
    task_bytes = encoded(task_config)
    if task_config_path.exists() and task_config_path.read_bytes() != task_bytes:
        raise ValueError("frozen effective chunk task config differs")
    if not task_config_path.exists():
        task_config_path.write_bytes(task_bytes)
    tasks = table_tasks.compile(
        task_config_path, task_dir, verify_only=task_dir.exists()
    )
    return {
        "chunk": chunk.name,
        "pages": len(titles),
        "source_manifest_sha256": html_grid.sha(
            (source_dir / "source_manifest.json").read_bytes()
        ),
        "grid_manifest_sha256": html_grid.sha(
            (grid_dir / "manifest.json").read_bytes()
        ),
        "task_manifest_sha256": html_grid.sha(
            (task_dir / "manifest.json").read_bytes()
        ),
        "gross_wikitables": grid["gross_wikitables"],
        "valid_grids": grid["grid_valid_tables"],
        "candidate_views": tasks["candidate_views"],
        "grid_rejections": grid["reject_reasons"],
        "task_rejections": tasks["rejection_reasons"],
    }


def merge_chunks(output: Path, chunks: list[dict], *, verify_only: bool) -> dict:
    destination = output / "combined"
    readers = {"train": [], "eval": []}
    indexes = []
    seen_ids, splits = set(), {}
    for chunk in chunks:
        task_dir = output / "chunks" / chunk["chunk"] / "tasks"
        local_readers = {
            split: [
                json.loads(raw)
                for raw in (task_dir / f"candidate_{split}.jsonl")
                .read_text()
                .splitlines()
            ]
            for split in readers
        }
        for raw in (task_dir / "sample_index.jsonl").read_text().splitlines():
            row = json.loads(raw)
            split = row["split"]
            reader = local_readers[split][row["row_index"]]
            if row["sample_id"] != reader["sample_id"] or row["sample_id"] in seen_ids:
                raise ValueError("duplicate or mismatched campaign task")
            seen_ids.add(row["sample_id"])
            group = row["source_group"]
            if group in splits and splits[group] != split:
                raise ValueError("campaign source crosses split")
            splits[group] = split
            row.update(
                output_file=f"candidate_{split}.jsonl", row_index=len(readers[split])
            )
            readers[split].append(reader)
            indexes.append(row)
    files = {
        "candidate_train.jsonl": b"".join(line(row) for row in readers["train"]),
        "candidate_eval.jsonl": b"".join(line(row) for row in readers["eval"]),
        "sample_index.jsonl": b"".join(line(row) for row in indexes),
    }
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "campaign_schema": SCHEMA,
        "candidate_views": len(indexes),
        "source_scoped_semantic_tasks": len(indexes),
        "independent_semantic_tasks": len(indexes),
        "splits": {split: len(readers[split]) for split in readers},
        "source_groups": len(splits),
        "views_by_lane": {"p131_wiki_html_table_scan": len(indexes)},
        "domains": dict(sorted(Counter(row["domain"] for row in indexes).items())),
        "topics": dict(sorted(Counter(row["topic"] for row in indexes).items())),
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in indexes).items())
        ),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in indexes),
        "supervised_tokens": sum(row["supervised_tokens"] for row in indexes),
        "files_sha256": {name: digest(data) for name, data in files.items()},
        "train_ready": False,
        "claim_limit": "single frozen HTML table complete-set scan; no remote dependency or model-gain claim",
    }
    files["manifest.json"] = encoded(manifest)
    if verify_only:
        if {path.name for path in destination.iterdir()} != set(files):
            raise ValueError("combined campaign inventory differs")
        for name, data in files.items():
            if (destination / name).read_bytes() != data:
                raise ValueError(f"combined campaign byte replay differs: {name}")
    else:
        destination.mkdir()
        for name, data in files.items():
            (destination / name).write_bytes(data)
    verify_merge(destination)
    return manifest


def run(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = config_data(config_path)
    campaign = json.loads((output / "plan.json").read_text())
    if campaign.get("code_sha256") != digest(
        Path(__file__).read_bytes()
    ) or campaign.get("config_sha256") != digest(config_path.read_bytes()):
        raise ValueError("campaign plan code/config changed")
    old = old_cache(config)
    prior_ledger = {}
    ledger_path = output / "fetch_ledger.jsonl"
    if ledger_path.exists():
        for raw in ledger_path.read_text().splitlines():
            row = json.loads(raw)
            prior_ledger[row["title"]] = row
    ledger = {}
    if not verify_only:
        client = RateClient(config)
        stopped = False
        for index, source in enumerate(campaign["pages"]):
            title = source["title"]
            chunk = (
                output / "chunks" / f"chunk{index // config['max_pages_per_chunk']:02d}"
            )
            previous = prior_ledger.get(title)
            if stopped:
                ledger[title] = {"title": title, "status": "pending_rate_limit"}
                continue
            try:
                status = acquire(
                    source,
                    chunk / "source",
                    old,
                    client,
                    config["max_html_bytes_per_page"],
                )
                if (
                    previous
                    and previous["status"] in {"fetched", "reused_p122"}
                    and status == "campaign_cache"
                ):
                    status = previous["status"]
                ledger[title] = {
                    "title": title,
                    "status": status,
                    "oldid": source["oldid"],
                    "source_group": source["source_group"],
                }
            except RateLimited:
                ledger[title] = {
                    "title": title,
                    "status": "failed_rate_limit",
                    "oldid": source["oldid"],
                }
                stopped = True
            except (HttpError, ValueError) as error:
                ledger[title] = {
                    "title": title,
                    "status": "failed",
                    "oldid": source["oldid"],
                    "reason": str(error),
                }
            ledger_path.write_bytes(
                b"".join(
                    line(ledger[row["title"]])
                    for row in campaign["pages"]
                    if row["title"] in ledger
                )
            )
        if stopped:
            return {
                "status": "stopped_rate_limit",
                "page_status": dict(Counter(row["status"] for row in ledger.values())),
                "network_attempts": client.attempts,
            }
    else:
        if set(prior_ledger) != {row["title"] for row in campaign["pages"]}:
            raise ValueError("fetch ledger incomplete")
        ledger = prior_ledger
    accepted = {
        title
        for title, row in ledger.items()
        if row["status"] in {"fetched", "reused_p122", "campaign_cache"}
    }
    jobs = []
    for index in range(campaign["chunks"]):
        pages = campaign["pages"][
            index * config["max_pages_per_chunk"] : (index + 1)
            * config["max_pages_per_chunk"]
        ]
        titles = [row["title"] for row in pages if row["title"] in accepted]
        if titles:
            jobs.append((str(output / "chunks" / f"chunk{index:02d}"), titles, config))
    if not jobs:
        raise ValueError("no frozen pages to compile")
    with ProcessPoolExecutor(max_workers=config["local_workers"]) as pool:
        chunks = list(pool.map(compile_chunk, jobs))
    chunk_path = output / "chunk_ledger.jsonl"
    chunk_bytes = b"".join(line(row) for row in chunks)
    if verify_only:
        if chunk_path.read_bytes() != chunk_bytes:
            raise ValueError("chunk ledger byte replay differs")
    else:
        chunk_path.write_bytes(chunk_bytes)
    combined = merge_chunks(output, chunks, verify_only=verify_only)
    old_titles = {title for title, _ in old}
    index = [
        json.loads(raw)
        for raw in (output / "combined" / "sample_index.jsonl").read_text().splitlines()
    ]
    result = {
        "schema": SCHEMA + ".manifest",
        "code_sha256": digest(Path(__file__).read_bytes()),
        "config_sha256": digest(config_path.read_bytes()),
        "plan_sha256": digest((output / "plan.json").read_bytes()),
        "fetch_ledger_sha256": digest(ledger_path.read_bytes()),
        "chunk_ledger_sha256": digest(chunk_bytes),
        "combined_manifest_sha256": digest(
            (output / "combined" / "manifest.json").read_bytes()
        ),
        "planned_pages": len(campaign["pages"]),
        "page_status": dict(
            sorted(Counter(row["status"] for row in ledger.values()).items())
        ),
        "successful_pages": len(accepted),
        "failed_pages": len(campaign["pages"]) - len(accepted),
        "existing_p122_cached_pages": len(old_titles & accepted),
        "new_source_pages_with_tasks": len(
            {
                row["source_group"]
                for row in index
                if row["source_group"]
                not in {
                    item["source_group"]
                    for item in campaign["pages"]
                    if item["title"] in old_titles
                }
            }
        ),
        "candidate_views": combined["candidate_views"],
        "genuinely_new_source_tasks": sum(
            row["source_group"]
            not in {
                item["source_group"]
                for item in campaign["pages"]
                if item["title"] in old_titles
            }
            for row in index
        ),
        "gross_wikitables": sum(row["gross_wikitables"] for row in chunks),
        "valid_grids": sum(row["valid_grids"] for row in chunks),
        "grid_rejections": dict(
            sorted(
                sum(
                    (Counter(row["grid_rejections"]) for row in chunks), Counter()
                ).items()
            )
        ),
        "task_rejections": dict(
            sorted(
                sum(
                    (Counter(row["task_rejections"]) for row in chunks), Counter()
                ).items()
            )
        ),
        "splits": combined["splits"],
        "domains": combined["domains"],
        "topics": combined["topics"],
        "length_bins": combined["length_bins"],
        "full_chat_tokens": combined["full_chat_tokens"],
        "supervised_tokens": combined["supervised_tokens"],
        "train_ready": False,
        "claim_limit": "P119 oldid Wikipedia HTML, strict table scan; no remote dependency or model-gain claim",
    }
    manifest_path = output / "manifest.json"
    data = encoded(result)
    if verify_only:
        if manifest_path.read_bytes() != data:
            raise ValueError("campaign manifest byte replay differs")
    else:
        manifest_path.write_bytes(data)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("plan", "run", "verify"), required=True)
    args = parser.parse_args()
    config_path = (ROOT / args.config).absolute()
    output = (ROOT / args.output).absolute()
    result = (
        plan(config_path, output)
        if args.phase == "plan"
        else run(config_path, output, verify_only=args.phase == "verify")
    )
    print(
        json.dumps(
            result
            if args.phase == "plan"
            else {
                key: result[key]
                for key in (
                    "planned_pages",
                    "successful_pages",
                    "failed_pages",
                    "candidate_views",
                    "genuinely_new_source_tasks",
                )
                if key in result
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
