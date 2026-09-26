"""Select a replayable multi-topic source cohort from already downloaded texts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p112_book_freeze import _body
from scripts.p113_book_catalog import _work_key
from scripts.p113_book_identity import identity_header
from scripts.p113_book_truth import audit_speeches, chapters, speeches

SCHEMA = "longworld.p113-book-source-freeze.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build(
    config: Path,
    plan: Path,
    attempt_dir: Path,
    prior_source_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    cfg = json.loads(config.read_text())
    planned = json.loads(plan.read_text())
    downloaded = json.loads((attempt_dir / "download_manifest.json").read_text())
    prior = json.loads((prior_source_dir / "manifest.json").read_text())
    if (
        cfg["schema"] != "longworld.p113-book-catalog-request.v1"
        or planned["schema"] != "longworld.p113-book-catalog-plan.v1"
        or planned["request_sha256"] != _sha(config.read_bytes())
        or prior["schema"] != "longworld.p112-book-freeze.v1"
        or downloaded["schema"] != "longworld.p113-book-mirror-attempts.v1"
        or downloaded["plan_sha256"] != _sha(plan.read_bytes())
        or downloaded["request_sha256"] != _sha(config.read_bytes())
    ):
        raise ValueError("book source inputs disagree")
    downloaded_ids = [x["ebook_id"] for x in downloaded["records"]]
    if downloaded_ids != [
        x["ebook_id"] for x in planned["candidates"][: len(downloaded_ids)]
    ]:
        raise ValueError("downloaded book prefix/order differs")
    downloaded_by_id = {x["ebook_id"]: x for x in downloaded["records"]}
    prior_keys = {
        _work_key({"Title": x["title"], "Authors": x["author"]})
        for x in prior["records"]
    }
    reasons = Counter()
    viable = []
    attempts = []
    for item in planned["candidates"]:
        source = attempt_dir / "attempts" / f"pg{item['ebook_id']}.txt"
        if not source.is_file():
            if item["ebook_id"] in downloaded_by_id:
                raise ValueError(
                    f"downloaded mirror attempt missing: {item['ebook_id']}"
                )
            continue  # Not downloaded in this bounded pilot; never a quality reject.
        raw = source.read_bytes()
        if _sha(raw) != downloaded_by_id[item["ebook_id"]]["raw_sha256"]:
            raise ValueError(f"frozen mirror attempt bytes differ: {item['ebook_id']}")
        row = {
            "ebook_id": item["ebook_id"],
            "topic": item["topic"],
            "split": item["split"],
            "raw_sha256": _sha(raw),
            "raw_bytes": len(raw),
        }
        try:
            if item["work_key"] in prior_keys:
                raise ValueError("prior_work_duplicate")
            if len(raw) > cfg["max_bytes_per_book"]:
                raise ValueError("source_exceeds_byte_cap")
            title, author = identity_header(raw, item)
            body = _body(raw, item["ebook_id"]).encode()
            cs = chapters(body.decode())
            generated = [speeches(c.text) for c in cs]
            audited = [audit_speeches(c.text) for c in cs]
            # These are proposal-capacity signals only. Task-level agreement,
            # alias, intervention and mask gates run again before admission.
            labels = [{x.label for x in group} for group in generated]
            shared_pairs = sum(
                bool(labels[a] & labels[b])
                for a in range(len(cs))
                for b in range(a + 1, len(cs))
            )
            if shared_pairs < 3:
                raise ValueError("insufficient_cross_chapter_named_speech")
            candidate = {
                **item,
                "title": title,
                "author": author,
                "raw_source": source,
                "raw": raw,
                "body": body,
                "chapter_count": len(cs),
                "generator_attributions": sum(map(len, generated)),
                "independent_attributions": sum(map(len, audited)),
                "shared_chapter_pairs": shared_pairs,
            }
            viable.append(candidate)
            row.update(
                status="viable",
                chapter_count=len(cs),
                shared_chapter_pairs=shared_pairs,
            )
        except (UnicodeError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else "non_utf8_text"
            reasons[reason] += 1
            row.update(status="rejected", reason=reason)
        attempts.append(row)
    pools = defaultdict(list)
    for candidate in viable:
        pools[candidate["topic"]].append(candidate)
    for pool in pools.values():
        pool.sort(key=lambda x: (-x["shared_chapter_pairs"], x["ebook_id"]))
    selected = []
    target_eval = min(
        math.ceil(cfg["max_frozen_books"] * cfg["eval_fraction"]),
        sum(x["split"] == "eval" for x in viable),
    )
    eval_pools = {
        topic: [x for x in pool if x["split"] == "eval"]
        for topic, pool in pools.items()
    }
    while len(selected) < target_eval and any(eval_pools.values()):
        for topic in sorted(eval_pools):
            if eval_pools[topic] and len(selected) < target_eval:
                item = eval_pools[topic].pop(0)
                pools[topic].remove(item)
                selected.append(item)
    while len(selected) < cfg["max_frozen_books"] and any(pools.values()):
        for topic in sorted(pools):
            if pools[topic] and len(selected) < cfg["max_frozen_books"]:
                selected.append(pools[topic].pop(0))
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for old in prior["records"]:
        src_raw = prior_source_dir / old["raw_file"]
        src_body = prior_source_dir / old["body_file"]
        for src in (src_raw, src_body):
            dst = output / src.name
            if verify_only:
                if dst.read_bytes() != src.read_bytes():
                    raise ValueError("prior book source copy differs")
            elif dst.exists() and dst.read_bytes() != src.read_bytes():
                raise ValueError("prior book source copy exists with different bytes")
            else:
                shutil.copyfile(src, dst)
        records.append(
            {
                **old,
                "origin_lane": "p112_frozen_source_only",
                "catalog_work_key": _work_key(
                    {"Title": old["title"], "Authors": old["author"]}
                ),
            }
        )
    for item in selected:
        ebook_id = item["ebook_id"]
        for name, content in (
            (f"pg{ebook_id}.txt", item["raw"]),
            (f"pg{ebook_id}.body.txt", item["body"]),
        ):
            path = output / name
            if verify_only:
                if path.read_bytes() != content:
                    raise ValueError(f"new book source copy differs: {ebook_id}")
            elif path.exists() and path.read_bytes() != content:
                raise ValueError(
                    f"new book source copy exists with different bytes: {ebook_id}"
                )
            else:
                path.write_bytes(content)
        records.append(
            {
                "ebook_id": ebook_id,
                "title": item["title"],
                "author": item["author"],
                "catalog_title": next(
                    x["title"]
                    for x in planned["candidates"]
                    if x["ebook_id"] == ebook_id
                ),
                "catalog_work_key": item["work_key"],
                "origin_lane": "p113_catalog_mirror",
                "domain": item["domain"],
                "topic": item["topic"],
                "split": item["split"],
                "source_group": f"gutenberg-{ebook_id}",
                "landing_url": f"https://www.gutenberg.org/ebooks/{ebook_id}",
                "text_url": f"{cfg['mirror_base']}/{ebook_id}/pg{ebook_id}.txt",
                "raw_file": f"pg{ebook_id}.txt",
                "raw_sha256": _sha(item["raw"]),
                "raw_bytes": len(item["raw"]),
                "body_file": f"pg{ebook_id}.body.txt",
                "body_sha256": _sha(item["body"]),
                "body_bytes": len(item["body"]),
                "chapter_count": item["chapter_count"],
                "shared_chapter_pairs": item["shared_chapter_pairs"],
            }
        )
    ledger = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in attempts
    ).encode()
    receipt = {
        "schema": SCHEMA,
        "source_truth_code_sha256": _sha(
            (ROOT / "scripts/p113_book_truth.py").read_bytes()
        ),
        "catalog_sha256": cfg["catalog_sha256"],
        "catalog_plan_sha256": _sha(plan.read_bytes()),
        "mirror_attempt_manifest_sha256": _sha(
            (attempt_dir / "download_manifest.json").read_bytes()
        ),
        "prior_source_manifest_sha256": _sha(
            (prior_source_dir / "manifest.json").read_bytes()
        ),
        "attempted_new_sources": len(attempts),
        "viable_new_sources": len(viable),
        "selected_new_sources": len(selected),
        "source_worlds": len(records),
        "selected_new_by_topic": dict(
            sorted(Counter(x["topic"] for x in selected).items())
        ),
        "source_rejections": dict(sorted(reasons.items())),
        "attempt_ledger_sha256": _sha(ledger),
        "records": records,
        "train_ready": False,
    }
    for name, content in (
        ("attempt_ledger.jsonl", ledger),
        (
            "manifest.json",
            (
                json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode(),
        ),
    ):
        path = output / name
        if verify_only:
            if path.read_bytes() != content:
                raise ValueError(f"source cohort receipt replay differs: {name}")
        elif path.exists() and path.read_bytes() != content:
            raise ValueError(
                f"source cohort receipt exists with different bytes: {name}"
            )
        else:
            path.write_bytes(content)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--attempt-dir", type=Path, required=True)
    parser.add_argument("--prior-source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    receipt = build(
        args.config,
        args.plan,
        args.attempt_dir,
        args.prior_source_dir,
        args.output,
        verify_only=args.verify_only,
    )
    print(json.dumps({k: v for k, v in receipt.items() if k != "records"}, indent=2))


if __name__ == "__main__":
    main()
