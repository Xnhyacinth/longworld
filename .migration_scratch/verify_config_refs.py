#!/usr/bin/env python3
"""Decide, per pNN config, whether it is reachable from any entry point.

Static grep is insufficient: reports/*_pipeline_*.py build config paths with
f-strings (`configs/p60_code_{job_id}_v1.yaml`) whose job ids come from a
preflight JSON. This resolves those cases rather than guessing, then reports a
verdict per config with the evidence behind it.

Verdicts:
  PINNED      - referenced by name in code/catalog/reports
  DYNAMIC     - reachable via an f-string template that resolves to this file
  ARTIFACT    - unreferenced, but a reports/ artifact of the same stem exists
  ORPHAN      - unreferenced and no artifact trace
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_ls(pat: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", pat], cwd=ROOT, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line]


def dynamic_templates() -> dict[str, set[str]]:
    """Find f-string config templates and resolve {job_id} from preflights.

    Returns {glob-ish template -> set of source files that use it}.
    """
    templates: dict[str, set[str]] = {}
    for f in list(ROOT.glob("reports/*.py")) + list(ROOT.glob("scripts/*.py")):
        try:
            src = f.read_text()
        except Exception:
            continue
        for m in re.finditer(r"configs/([A-Za-z0-9_.\-]*)\{(\w+)\}([A-Za-z0-9_.\-]*)", src):
            tmpl = f"configs/{m.group(1)}*{m.group(3)}"
            templates.setdefault(tmpl, set()).add(str(f.relative_to(ROOT)))
            # try to resolve the braced variable from a nearby preflight default
            var = m.group(2)
            pf = re.search(r"--preflight[^\n]*?default=ROOT/f?'([^']+)'", src)
            if not pf:
                continue
            cand = ROOT / pf.group(1)
            if not cand.exists():
                continue
            try:
                data = json.loads(cand.read_text())
            except Exception:
                continue
            items = data if isinstance(data, list) else data.get("jobs", [])
            for item in items:
                job = item.get("job", item) if isinstance(item, dict) else {}
                if item.get("preflight_ok") is False and isinstance(item, dict):
                    continue
                jid = job.get("job_id") if isinstance(job, dict) else None
                if not jid:
                    continue
                resolved = tmpl.replace("*", jid)
                templates.setdefault(resolved, set()).add(f"{f.relative_to(ROOT)} (via {cand.name}:{var})")
    return templates


def main() -> int:
    configs = sorted(git_ls("configs/"))
    pnn = [c for c in configs if re.match(r"configs/p\d+_", c)]
    tmpl = dynamic_templates()
    dynamic_exact = {k for k in tmpl if "*" not in k}

    pinned: dict[str, list[str]] = {}
    for c in pnn:
        r = subprocess.run(
            ["grep", "-rlF", c, "scripts", "tests", "longworld", "configs", "reports", "README.md"],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout
        hits = [
            x for x in r.splitlines()
            if x and not x.endswith(c)
        ]
        if hits:
            pinned[c] = hits

    report_artifacts = {
        str(p.relative_to(ROOT)) for p in ROOT.glob("reports/*")
    }
    artifacts: dict[str, str] = {}
    orphans: list[str] = []
    dyn: dict[str, list[str]] = {}

    for c in pnn:
        if c in pinned:
            continue
        if c in dynamic_exact:
            dyn[c] = sorted(tmpl[c])
            continue
        stem = re.sub(r"\.(json|yaml)$", "", Path(c).name)
        bases = {
            stem,
            re.sub(r"_v\d+$", "", stem),
            re.sub(r"_(fetch_request|generation|preflight|signed|slice|run)$", "", stem),
        }
        hit = None
        for b in bases:
            g = list(ROOT.glob(f"reports/*{b}*"))
            if g:
                hit = str(g[0].relative_to(ROOT))
                break
        if hit:
            artifacts[c] = hit
        else:
            orphans.append(c)

    print(f"pNN configs            : {len(pnn)}")
    print(f"  PINNED    (by name)  : {len(pinned)}")
    print(f"  DYNAMIC   (f-string) : {len(dyn)}")
    print(f"  ARTIFACT  (ran)      : {len(artifacts)}")
    print(f"  ORPHAN    (no trace) : {len(orphans)}")
    print()
    if dyn:
        print("=== DYNAMIC (static grep missed these) ===")
        for c, src in sorted(dyn.items()):
            print(f"  {c}")
            for s in src:
                print(f"      <- {s}")
    out = ROOT / "tmp_scratch" / "config_verdicts.json"
    out.write_text(json.dumps(
        {
            "pinned": {k: v for k, v in pinned.items()},
            "dynamic": dyn,
            "artifact": artifacts,
            "orphan": orphans,
        }, indent=2) + "\n")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
