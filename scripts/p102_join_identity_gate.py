"""Filter exact-name Wiki JOINs by independent reader-visible identity evidence.

The native JOIN parser proves selector and target cell dependence, but equal
row names can denote different entities. This gate checks auxiliary row fields
or an explicit country/list scope before a candidate reaches the final bank.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_row_binding, wiki_world_bridge
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.probe_wiki_row_binding import verify_output
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p102-wiki-join-identity.v1"
FIELDS = {
    "team": ("Team", "Club", "Home team"),
    "place": ("City", "Location"),
    "state": ("State", "Province"),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("identity gate pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"identity gate pin changed: {relative}")
    return path


def _cell(
    row: wiki_row_binding.Row, aliases: tuple[str, ...]
) -> tuple[str, str] | None:
    found = [(header, cell.value) for header, cell in row.columns if header in aliases]
    return found[0] if len(found) == 1 else None


def _normalized(value: str, kind: str) -> str:
    return wiki_row_binding._fold(value)


def _place_compatible(left: str, right: str) -> bool:
    """Allow a detailed place only when the other cell names its full suffix."""
    full_left, full_right = _normalized(left, "place"), _normalized(right, "place")
    if full_left == full_right:
        return True
    left_suffix = _normalized(left.rsplit(",", 1)[-1], "place") if "," in left else ""
    right_suffix = (
        _normalized(right.rsplit(",", 1)[-1], "place") if "," in right else ""
    )
    return bool(left_suffix and left_suffix == full_right) or bool(
        right_suffix and right_suffix == full_left
    )


def _country_scope(
    first_title: str, first_row: wiki_row_binding.Row, second_title: str
) -> dict | None:
    country = _cell(first_row, ("Country",))
    if country is None or " in " not in second_title:
        return None
    raw = country[1]
    if raw.startswith("flag|"):
        raw = raw.removeprefix("flag|")
    country_value = wiki_row_binding._fold(raw).removeprefix("the ")
    scope = wiki_row_binding._fold(second_title.rsplit(" in ", 1)[1]).removeprefix(
        "the "
    )
    if not country_value or country_value != scope:
        return None
    return {
        "kind": "row_country_to_other_page_scope",
        "source_title": first_title,
        "target_title": second_title,
        "source_column": "Country",
        "source_value": country[1],
        "target_scope": second_title.rsplit(" in ", 1)[1],
    }


def identity(task: wiki_row_binding.JoinTask) -> dict:
    """Accept only unique names with a nonconflicting auxiliary identity witness."""
    first_rows, second_rows = wiki_row_binding._visible_rows(task, task.context)
    name = task.context[task.bound_name_span[0] : task.bound_name_span[1]]
    first = [row for row in first_rows if row.name.value == name]
    second = [row for row in second_rows if row.name.value == name]
    result = {
        "bound_name": name,
        "first_title": task.first_title,
        "second_title": task.second_title,
        "first_name_occurrences": len(first),
        "second_name_occurrences": len(second),
        "identity_evidence": [],
        "status": "rejected",
        "reason": "",
    }
    if len(first) != 1 or len(second) != 1:
        result["reason"] = "nonunique_row_name_in_visible_table"
        return result
    witnessed = []
    conflicts = []
    for kind, aliases in FIELDS.items():
        left = _cell(first[0], aliases)
        right = _cell(second[0], aliases)
        if left is None or right is None:
            continue
        lval, rval = _normalized(left[1], kind), _normalized(right[1], kind)
        if not lval or not rval:
            continue
        record = {
            "kind": "matching_" + kind,
            "first_column": left[0],
            "second_column": right[0],
            "first_value": left[1],
            "second_value": right[1],
        }
        if lval == rval or (kind == "place" and _place_compatible(left[1], right[1])):
            witnessed.append(record)
        else:
            conflicts.append(record)
    if conflicts:
        result["reason"] = "conflicting_auxiliary_identity"
        result["identity_evidence"] = conflicts
        return result
    if not witnessed:
        for args in (
            (task.first_title, first[0], task.second_title),
            (task.second_title, second[0], task.first_title),
        ):
            if country := _country_scope(*args):
                witnessed.append(country)
    if not witnessed:
        result["reason"] = "no_independent_identity_witness"
        return result
    result["status"] = "accepted"
    result["reason"] = "reader_visible_auxiliary_identity"
    result["identity_evidence"] = witnessed
    return result


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or output_dir.exists() != verify_only:
        raise ValueError("invalid identity gate config or output state")
    source_pool_path = _pin(config["source_pool"])
    native_manifest_path = _pin(config["native_manifest"])
    native_dir = native_manifest_path.parent
    pool = json.loads(source_pool_path.read_text())
    if pool.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("wrong identity source pool schema")
    verified = verify_output(source_pool_path, native_dir)
    native = json.loads(native_manifest_path.read_text())
    if verified["verified_rows"] != native["views"]:
        raise ValueError("native JOIN verification count differs")
    tasks_by_group = {}
    for source in pool["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        tasks_by_group[source["name"]] = {
            task.task_id: task
            for task in wiki_row_binding.build_join_tasks(
                wiki_world_bridge.snapshot_to_world(snapshot), max_tasks=1000
            )
        }
    audits = []
    allowed = set()
    for row in _rows(native_dir / "sample_index.jsonl"):
        task = tasks_by_group[row["source_group"]][row["semantic_task_id"]]
        result = identity(task)
        result.update(
            {
                "sample_id": row["example_id"],
                "semantic_task_id": task.task_id,
                "source_group": row["source_group"],
                "domain": row["domain"],
                "topic": row["topic"],
                "split": row["split"],
                "answer": task.answer,
            }
        )
        audits.append(result)
        if result["status"] == "accepted":
            allowed.add(row["example_id"])
    if not verify_only:
        output_dir.mkdir(parents=True)
    identity_content = "".join(_dump(row) + "\n" for row in audits)
    identity_path = output_dir / "identity_audit.jsonl"
    if verify_only:
        if identity_path.read_text() != identity_content:
            raise ValueError("identity audit byte replay differs")
    else:
        identity_path.write_text(identity_content)
    unified_pin = config.get("unified_manifest")
    files = {}
    source_unified_sha = None
    curated_manifest_sha = None
    if unified_pin is not None:
        source_manifest = _pin(unified_pin)
        unified_dir = source_manifest.parent
        source_unified_sha = _sha(source_manifest)
        source = verify_merge(unified_dir)
        index = _rows(unified_dir / "sample_index.jsonl")
        by_split = {
            "train": _rows(unified_dir / "candidate_train.jsonl"),
            "eval": _rows(unified_dir / "candidate_eval.jsonl"),
        }
        selected = {"train": [], "eval": []}
        selected_index = []
        for row in index:
            if row["sample_id"] not in allowed:
                continue
            split = row["split"]
            reader = by_split[split][row["row_index"]]
            if reader["sample_id"] != row["sample_id"]:
                raise ValueError("unified reader/index identity differs")
            selected_index.append({**row, "row_index": len(selected[split])})
            selected[split].append(reader)
        if len(selected_index) != len(allowed) or source["candidate_views"] != len(
            audits
        ):
            raise ValueError("identity review and unified candidate counts differ")
        if len({row["semantic_task_id"] for row in selected_index}) != len(
            selected_index
        ):
            raise ValueError("accepted semantic JOIN tasks repeat")
        files = {
            "candidate_train.jsonl": "".join(
                _dump(row) + "\n" for row in selected["train"]
            ),
            "candidate_eval.jsonl": "".join(
                _dump(row) + "\n" for row in selected["eval"]
            ),
            "sample_index.jsonl": "".join(_dump(row) + "\n" for row in selected_index),
        }
        curated_dir = output_dir / "merged"
        if not verify_only:
            curated_dir.mkdir()
        for name, content in files.items():
            path = curated_dir / name
            if verify_only:
                if path.read_text() != content:
                    raise ValueError(f"curated identity shard changed: {name}")
            else:
                path.write_text(content)
        curated_manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": len(selected_index),
            "source_scoped_semantic_tasks": len(selected_index),
            "independent_semantic_tasks": len(selected_index),
            "views_by_lane": dict(
                sorted(Counter(row["source_name"] for row in selected_index).items())
            ),
            "splits": dict(
                sorted(Counter(row["split"] for row in selected_index).items())
            ),
            "length_bins": dict(
                sorted(Counter(row["length_bin"] for row in selected_index).items())
            ),
            "source_unified_manifest_sha256": source_unified_sha,
            "identity_audit_sha256": _sha(identity_path),
            "files_sha256": {name: _sha(curated_dir / name) for name in files},
            "train_ready": False,
        }
        curated_path = curated_dir / "manifest.json"
        curated_content = (
            json.dumps(curated_manifest, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        )
        if verify_only:
            if curated_path.read_text() != curated_content:
                raise ValueError("curated identity manifest changed")
        else:
            curated_path.write_text(curated_content)
        verify_merge(curated_dir)
        curated_manifest_sha = _sha(curated_path)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "source_pool_sha256": _sha(source_pool_path),
        "native_manifest_sha256": _sha(native_manifest_path),
        "source_unified_manifest_sha256": source_unified_sha,
        "curated_manifest_sha256": curated_manifest_sha,
        "reviewed_tasks": len(audits),
        "accepted_tasks": len(allowed),
        "rejected_tasks": len(audits) - len(allowed),
        "rejection_reasons": dict(
            sorted(
                Counter(
                    row["reason"] for row in audits if row["status"] == "rejected"
                ).items()
            )
        ),
        "accepted_domains": dict(
            sorted(
                Counter(
                    row["domain"] for row in audits if row["status"] == "accepted"
                ).items()
            )
        ),
        "identity_audit_sha256": _sha(identity_path),
        "train_ready": False,
    }
    path = output_dir / "manifest.json"
    if verify_only:
        if json.loads(path.read_text()) != manifest:
            raise ValueError("identity gate manifest replay differs")
    else:
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(run(args.config, args.output_dir, verify_only=args.verify_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
