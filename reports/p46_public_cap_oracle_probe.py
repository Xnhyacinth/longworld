"""Pinned Ofgem numeric oracle and bounded NTSB fallback rejection probe.

Source snapshots are provenance inputs, never training rows. CAROL bodies remain
in process memory; the persisted projection contains only dated status spans.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import urllib.request
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader
from transformers import AutoTokenizer

CONFIG = Path("configs/p46_public_cap_oracle_probe_v1.json")
OUTPUT = Path("reports/p46_public_cap_oracle_probe_v1.json")
STATUS = re.compile(r"(?:OPEN|CLOSED)[\s–—-]+[A-Z][A-Z /–—-]+")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def html_text(raw: bytes) -> str:
    parser = VisibleText()
    parser.feed(raw.decode("utf-8"))
    return "\n".join(parser.parts)


def span(text: str, excerpt: str) -> dict:
    if text.count(excerpt) != 1:
        raise ValueError("source span is missing or ambiguous")
    start = text.index(excerpt)
    return {
        "start": start,
        "end": start + len(excerpt),
        "text": excerpt,
        "text_sha256": digest(text.encode()),
    }


def verify_span(text: str, bound: dict) -> str:
    if digest(text.encode()) != bound["text_sha256"]:
        raise ValueError("source text changed")
    if text[bound["start"] : bound["end"]] != bound["text"]:
        raise ValueError("source span changed")
    return bound["text"]


def cap_oracle(text: str, revised: bool = True) -> dict:
    """Execute only the four source-stated cap rows, on one consumption basis."""
    heading = (
        "under revised TDCV values (coming into effect 1 July 2026)."
        if revised
        else "Summary of changes to the energy price cap by payment method and meter type"
    )
    start = text.index(heading)
    tail = text[start:]
    end_marker = "As of 1 July 2026" if revised else "All bill values presented"
    table = tail[: tail.index(end_marker)]
    # PDF line wraps occur inside the Economy 7 label, so preserve the source
    # substring for receipts while normalizing only the matching expression.
    pattern = r"(Direct Debit|Standard Credit|PPM|Economy 7\s*\(Direct\s*Debit\))\s+£([\d,]+)\s+£([\d,]+)\s+(\d+)%"
    matches = list(re.finditer(pattern, table))
    if len(matches) != 4:
        raise ValueError("expected four public cap rows")
    rows = []
    for match in matches:
        key = re.sub(r"\s+", " ", match[1])
        old, new = (Decimal(match[i].replace(",", "")) for i in (2, 3))
        rows.append(
            {
                "key": key,
                "previous_gbp": str(old),
                "current_gbp": str(new),
                "delta_gbp": str(new - old),
                "status": "retained" if new == old else "changed",
                "source_span": span(text, match[0]),
            }
        )
    return {
        "basis": "revised_2026" if revised else "2023",
        "rows": rows,
        "period_from": "2026-04-01/2026-06-30",
        "period_to": "2026-07-01/2026-09-30",
    }


def ntsb_projection(record: dict) -> list[dict]:
    result = []
    for branch, addressee in enumerate(record["Addressees"]):
        for index, event in enumerate(addressee.get("Correspondence") or []):
            if event["IsFromNtsb"] is not True:
                continue
            text = event["ResponseSummary"]
            for match in STATUS.finditer(text):
                # These are literal classification spans, not authority or
                # antecedent resolution. That distinction remains a blocker.
                result.append(
                    {
                        "branch": branch,
                        "event_index": index,
                        "date": event["CorrespondenceDate"],
                        "status": re.sub(r"[\s–—-]+", " ", match[0]).strip(),
                        "source_span": span(text, match[0]),
                    }
                )
    return sorted(
        result, key=lambda item: (item["branch"], item["date"], item["event_index"])
    )


def replay_timeline(events: list[dict]) -> list[dict]:
    states: dict[int, str] = {}
    result = []
    for event in events:
        branch = event["branch"]
        result.append(
            {
                "branch": branch,
                "date": event["date"],
                "previous": states.get(branch),
                "current": event["status"],
            }
        )
        states[branch] = event["status"]
    return result


def run(ntsb_cache: Path | None = None) -> dict:
    config = json.loads(CONFIG.read_text())
    texts, receipts = {}, []
    for source in config["sources"]:
        raw = gzip.decompress(Path(source["path"]).read_bytes())
        if len(raw) != source["bytes"] or digest(raw) != source["sha256"]:
            raise ValueError("pinned source bytes changed")
        text = (
            "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)
            if source["id"] == "july_summary"
            else html_text(raw)
        )
        texts[source["id"]] = text
        receipts.append(source | {"extracted_text_sha256": digest(text.encode())})
    tc = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tc["model_id"], revision=tc["revision"], local_files_only=True
    )

    def tokens(text):
        return len(tokenizer.encode(text, add_special_tokens=False))

    summary = texts["july_summary"]
    old, new = cap_oracle(summary, False), cap_oracle(summary)
    # One contiguous section carries old/new tables AND the consumption revision.
    start = summary.index("Summary of changes to the energy price cap")
    end = summary.index("You can get a further breakdown")
    witness = summary[start:end]
    assert cap_oracle(witness)["rows"][0]["delta_gbp"] == new["rows"][0]["delta_gbp"]
    rates = texts["rates"]
    rate_start = rates.index("Average electricity and gas unit prices")
    rate_end = rates.index("Costs cannot be compared directly") + len(
        "Costs cannot be compared directly to previous periods because of this change."
    )
    rates_witness = rates[rate_start:rate_end]
    rights = texts["rights"]
    rights_excerpt = rights[
        rights.index("Material featured") : rights.index("Hyperlinking & sharing")
    ]
    ofgem = {
        "old_basis": old,
        "revised_basis": new,
        "revision_span": span(
            summary, summary[summary.index("As of 1 July 2026") : end].strip()
        ),
        "single_document_shortcut": True,
        "shortcut_tokens": tokens(witness),
        "shortcut_span": span(summary, witness),
        "rates_shortcut_tokens": tokens(rates_witness),
        "rates_shortcut_span": span(rates, rates_witness),
        "summary_total_tokens": tokens(summary),
        "rights": {
            "policy_span": span(rights, rights_excerpt),
            "scope": "Ofgem-authored selected table and explanatory prose only; excludes logos, contacts, signatures and third-party material",
            "selected_documents": ["july_summary", "rates", "july_decision"],
            "marker_scan": {
                key: {
                    term: bool(re.search(term, texts[key], re.IGNORECASE))
                    for term in ["Internal Only", "third.party copyright"]
                }
                for key in ["july_summary", "rates", "july_decision"]
            },
            "status": "OGL_POLICY_BOUND_SELECTED_TEXT; NO_TRAINING_RELEASE",
        },
        "verdict": "REJECT_SINGLE_DOCUMENT_SHORTCUT",
        "shared_candidate_eligible": False,
    }
    fallback = []
    for rid in config["ntsb_ids"]:
        url = "https://data.ntsb.gov/carol-main-public/api/Query/GetSrRecord/" + rid
        if ntsb_cache is not None:
            raw = (ntsb_cache / (rid + ".json")).read_bytes()
        else:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "LongWorld-P48-source-preflight/1.0 xnhyacinth@users.noreply.github.com",
                    "Accept": "application/json",
                    "Referer": "https://data.ntsb.gov/carol-main-public/landing-page",
                },
            )
            with urllib.request.urlopen(request, timeout=40) as response:
                if response.geturl() != url:
                    raise ValueError("CAROL redirect changed")
                raw = response.read(8_000_001)
        if digest(raw) != config["ntsb_expected_sha256"][rid]:
            raise ValueError("CAROL mutable snapshot changed; fresh review required")
        record = json.loads(raw)
        events = ntsb_projection(record)
        timeline = replay_timeline(events)
        final = {row["branch"]: row["current"] for row in timeline}
        # Single latest-classification span witnesses a final-state shortcut.
        last_events = [
            next(e for e in reversed(events) if e["branch"] == branch)
            for branch in final
        ]
        short_final = {
            row["branch"]: row["current"] for row in replay_timeline(last_events)
        }
        fallback.append(
            {
                "id": rid,
                "url": url,
                "source_sha256": digest(raw),
                "bytes": len(raw),
                "classification_spans": events,
                "timeline": timeline,
                "status_projection_tokens": tokens(json.dumps(events, sort_keys=True)),
                "final_state_shortcut": final == short_final,
                "latest_status_span_tokens": sum(
                    tokens(e["source_span"]["text"]) for e in last_events
                ),
                "prior_state_required_by_final_classification": False,
                "verdict": "REJECT_FINAL_STATE_SHORTCUT_AND_UNPROVEN_CAUSAL_CHAIN",
                "rights": "NTSB-authored literal status spans only; recipient prose excluded; broader source rights/privacy review outstanding",
            }
        )
    return {
        "schema_version": config["schema_version"],
        "source_receipts": receipts,
        "ofgem": ofgem,
        "ntsb_fallback": fallback,
        "candidate_count": 0,
        "inventory_delta": 0,
        "train_ready": False,
        "production_eligible": False,
        "limits": [
            "No shared adapter, exact-band candidate, dense or exhaustive raw-window audit claimed.",
            "NTSB timelines consume prior status for reporting only; source-backed causal transition oracle is unproved.",
            "Public regional tables embedded as interactive content were not extracted; UK-average HTML and source PDF tables were inspected.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ntsb-cache",
        type=Path,
        help="optional ephemeral cache; hashes always verified",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(run(args.ntsb_cache), indent=2, ensure_ascii=False) + "\n"
    )
