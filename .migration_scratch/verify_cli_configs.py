#!/usr/bin/env python3
"""Second-pass reachability: configs consumed via a `--config` CLI argument.

The first pass (verify_config_refs.py) greps for literal config filenames. That
misses configs handed to a script at runtime, e.g.

    python scripts/materialize_p66_ietf_taskbank.py --config configs/X.json

where nothing names X in code. This pass closes that gap by matching each
unreferenced config's `schema_version` against the schema constants that
`--config`-accepting scripts validate against.

Verdicts per unreferenced config:
  CLI-REACHABLE - its schema_version is accepted by some --config script
  ORPHAN        - no schema match and no artifact
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_HINT = re.compile(r"^longworld[.-][a-z0-9._-]+", re.I)


def load_verdicts() -> dict:
    v = json.loads((ROOT / "tmp_scratch" / "config_verdicts.json").read_text())
    # older key name compatibility
    if "pinned" not in v and "artifact" in v:
        v.setdefault("orphan", [])
    return v


def scripts_taking_config() -> list[Path]:
    out = []
    for d in ("scripts", "reports"):
        for f in (ROOT / d).glob("*.py"):
            try:
                src = f.read_text()
            except Exception:
                continue
            if 'add_argument("--config"' in src or "add_argument('--config'" in src:
                out.append(f)
    return out


def schema_constants(f: Path) -> set[str]:
    """Pull string constants that look like longworld schema ids."""
    try:
        tree = ast.parse(f.read_text())
    except Exception:
        return set()
    found: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if SCHEMA_HINT.match(n.value):
                found.add(n.value)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    if isinstance(n.value, ast.Constant) and isinstance(n.value, str):
                        found.add(n.value)
    return found


def config_schema(f: Path) -> str | None:
    try:
        if f.suffix in (".yaml", ".yml"):
            for line in f.read_text().splitlines():
                if line.startswith("schema_version:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
            return None
        d = json.loads(f.read_text())
        return d.get("schema_version") if isinstance(d, dict) else None
    except Exception:
        return None


def main() -> int:
    v = load_verdicts()
    orphans = list(v.get("orphan", []))
    scripts = scripts_taking_config()
    print(f"--config scripts: {len(scripts)}")

    # schema id -> scripts accepting it
    accepted: dict[str, set[str]] = {}
    for s in scripts:
        for c in schema_constants(s):
            accepted.setdefault(c, set()).add(str(s.relative_to(ROOT)))

    cli: dict[str, list[str]] = {}
    still: list[str] = []
    for o in orphans:
        p = ROOT / o
        if not p.exists():
            still.append(o)
            continue
        sc = config_schema(p)
        if sc and sc in accepted:
            cli[o] = sorted(accepted[sc])
        else:
            still.append(o)

    print(f"unreferenced configs      : {len(orphans)}")
    print(f"  CLI-REACHABLE (schema)  : {len(cli)}")
    print(f"  still unreachable       : {len(still)}")
    print()
    if cli:
        print("=== CLI-REACHABLE (first pass wrongly called these dead) ===")
        for c, s in sorted(cli.items())[:40]:
            print(f"  {c}\n      schema accepted by: {', '.join(s[:3])}")
    (ROOT / "tmp_scratch" / "cli_reachable.json").write_text(
        json.dumps({"cli_reachable": cli, "unreachable": still}, indent=2) + "\n"
    )
    (ROOT / "tmp_scratch" / "definitive_dead.txt").write_text("\n".join(still) + "\n")
    print(f"\nwrote definitive_dead.txt ({len(still)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
