"""Build the P21 eLife 94586 v1/v2 source inventory and task audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

from longworld.core.documentworkflow import (
    audit_elife_review_revision_task,
    build_elife_review_revision_inventory,
)

CONFIG = Path("configs/p21_researchlab_elife_94586_review_revision_64k_v1.json")
OUTPUT = Path(
    "data/source_inventories/"
    "p21-researchlab-elife-94586-review-revision-64k-v1/"
    "source_inventory.json"
)
AUDIT = Path("reports/p21_researchlab_elife_94586_review_revision_64k_audit_v1.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--audit", type=Path, default=AUDIT)
    args = parser.parse_args()

    request = json.loads(args.config.read_text(encoding="utf-8"))
    repository = request["source"]["repository"]
    commit = request["source"]["repository_commit"]
    user_agent = "LongWorld/0.1 xnhyacinth@users.noreply.github.com"
    versions: dict[int, bytes] = {}
    for expected in request["versions"]:
        url = (
            f"https://raw.githubusercontent.com/{repository}/{commit}/"
            f"{expected['path']}"
        )
        source_request = urllib.request.Request(
            url,
            headers={"User-Agent": user_agent, "Accept": "application/xml"},
        )
        with urllib.request.urlopen(source_request, timeout=90) as response:
            raw = response.read(expected["bytes"] + 1)
            if response.status != 200 or response.geturl() != url:
                raise ValueError(
                    f"untrusted eLife retrieval for v{expected['version']}"
                )
        versions[expected["version"]] = raw

    inventory = build_elife_review_revision_inventory(
        request,
        versions,
        generated_at="2026-09-03T00:00:00Z",
    )
    audit = {
        "schema_version": "longworld.elife-review-revision-task-audit.v1",
        "data_product": request["data_product"],
        "source_inventory": str(args.out),
        "source_inventory_sha256": "computed-after-write",
        "length_policy": request["length_policy"],
        "privacy_records": [
            {
                "record_id": record["record_id"],
                "email_redaction_count": record["privacy_review"][
                    "email_redaction_count"
                ],
                "source_sha256": record["source_sha256"],
                "text_sha256": record["text_sha256"],
            }
            for record in inventory["records"]
        ],
        "task_audit": audit_elife_review_revision_task(inventory),
        "candidate_generated": False,
        "promoted": False,
        "production_eligible": False,
        "next_blocker": "source-attested replay-sidecar integration",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    inventory_raw = (
        json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    args.out.write_bytes(inventory_raw)
    audit["source_inventory_sha256"] = hashlib.sha256(inventory_raw).hexdigest()
    args.audit.write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
