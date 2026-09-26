"""Compile bounded Wiki list-link-to-article readers with text interventions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.wiki_row_binding import _rows
from scripts.audit_wiki_join_positions import token_span
from scripts.p111_wiki_linked_body_support import _target_body
from scripts.p111_wiki_linked_freeze import _sha
from scripts.p111_wiki_linked_support import _snapshot
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p111-wiki-linked-reader-batch.v2"
SEP = "\n\nQUESTION\n"
LINK = re.compile(r"\[([^\]\n]+)\]\(<(https://en\.wikipedia\.org/wiki/[^>]+)>\)")
FIELD_PRIORITY = (
    "motto",
    "students",
    "built",
    "platforms",
    "opened",
    "rector",
    "operator",
    "structure",
    "address",
    "borough",
    "location",
)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _choose(body: dict, target_manifest: Path) -> list[dict]:
    by_target = {(r["source_group"], r["target_title"]): r for r in body["records"]}
    target_receipt = json.loads(target_manifest.read_text())
    source_records = {
        (r["source_group"], r["target_title"]): r
        for r in target_receipt["records"]
        if r["status"] == "frozen"
    }
    accepted = []
    used_targets = set()
    for pair in sorted(
        body["pair_support"],
        key=lambda p: (p["source_group"], p["first_target"], p["second_target"]),
    ):
        a_key = (pair["source_group"], pair["first_target"])
        b_key = (pair["source_group"], pair["second_target"])
        if a_key in used_targets or b_key in used_targets:
            continue
        first, second = by_target[a_key], by_target[b_key]
        first_selectors = {s["key"]: s["value"] for s in first["selectors"]}
        second_selectors = {s["key"]: s["value"] for s in second["selectors"]}
        common_selectors = [
            key
            for key in first_selectors
            if key in second_selectors and first_selectors[key] != second_selectors[key]
        ]
        if not common_selectors:
            continue
        body_a, _ = _target_body(source_records[a_key], target_manifest.parent)
        body_b, _ = _target_body(source_records[b_key], target_manifest.parent)
        if any(
            first_selectors[key].casefold() in (body_a + body_b).casefold()
            or second_selectors[key].casefold() in (body_a + body_b).casefold()
            for key in common_selectors
        ):
            continue
        for field in sorted(
            pair["common_distinct_properties"],
            key=lambda k: (FIELD_PRIORITY.index(k) if k in FIELD_PRIORITY else 100, k),
        ):
            value_a, value_b = pair["common_distinct_properties"][field]
            if (
                value_a == value_b
                or body_a.count(value_a) != 1
                or body_b.count(value_b) != 1
                or value_a in body_b
                or value_b in body_a
            ):
                continue
            accepted.append(
                {
                    **pair,
                    "selector_key": common_selectors[0],
                    "selector_value": first_selectors[common_selectors[0]],
                    "alternate_selector_value": second_selectors[common_selectors[0]],
                    "field": field,
                    "answer": value_a,
                    "alternate_answer": value_b,
                }
            )
            used_targets.update((a_key, b_key))
            break
    return accepted


def _source_with_links(text: str, labels: dict[str, str]) -> str:
    rows = _rows(text)
    replacements = []
    for name, url in labels.items():
        matches = [row for row in rows if row.name.value == name]
        if (
            len(matches) != 1
            or text[matches[0].name.start : matches[0].name.end] != name
        ):
            raise ValueError("P111 list row name not uniquely reader-visible")
        replacements.append(
            (matches[0].name.start, matches[0].name.end, f"[{name}](<{url}>)")
        )
    for start, end, value in sorted(replacements, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def _swap_selector(text: str, first: str, second: str, key: str) -> str:
    rows = _rows(text)
    selected = {}
    for name in (first, second):
        matches = [row for row in rows if row.name.value == name]
        if len(matches) != 1 or matches[0].cell(key) is None:
            raise ValueError("P111 selector row not unique")
        selected[name] = matches[0].cell(key)
    a, b = selected[first], selected[second]
    for start, end, value in sorted(
        ((a.start, a.end, b.value), (b.start, b.end, a.value)), reverse=True
    ):
        text = text[:start] + value + text[end:]
    return text


def _context(
    source_title: str,
    source_revid: int,
    source: str,
    first_title: str,
    first_revid: int,
    first: str,
    second_title: str,
    second_revid: int,
    second: str,
) -> str:
    return (
        f"=== DOCUMENT: {source_title} (revision {source_revid}) ===\n{source}"
        f"\n\n=== DOCUMENT: {first_title} (revision {first_revid}) ===\n{first}"
        f"\n\n=== DOCUMENT: {second_title} (revision {second_revid}) ===\n{second}"
    )


def _solve(context: str, key: str, value: str, field: str) -> str | None:
    docs = re.split(r"(?m)^=== DOCUMENT: (.+?) \(revision ([0-9]+)\) ===\n", context)
    if len(docs) < 10 or (len(docs) - 1) % 3 != 0 or docs[0] != "":
        raise ValueError("P111 reader document boundaries differ")
    source = docs[3]
    links = LINK.findall(source)
    plain = LINK.sub(lambda m: m.group(1), source)
    matches = [
        row
        for row in _rows(plain)
        if row.cell(key) is not None and row.cell(key).value == value
    ]
    if len(matches) != 1:
        return None
    labels = [url for label, url in links if label == matches[0].name.value]
    if len(labels) != 1:
        return None
    title = urllib.parse.unquote(
        urllib.parse.urlsplit(labels[0]).path.removeprefix("/wiki/")
    ).replace("_", " ")
    targets = {docs[4]: docs[6], docs[7]: docs[9]}
    if title not in targets:
        return None
    lines = [
        line.removeprefix(f"{field}: ")
        for line in targets[title].splitlines()
        if line.startswith(f"{field}: ")
    ]
    return lines[0] if len(lines) == 1 else None


def _compile(
    item: dict, target_manifest: Path, body_ledger: Path
) -> tuple[dict, dict, dict]:
    targets = json.loads(target_manifest.read_text())
    target_by_title = {
        (r["source_group"], r["target_title"]): r
        for r in targets["records"]
        if r["status"] == "frozen"
    }
    first = target_by_title[(item["source_group"], item["first_target"])]
    second = target_by_title[(item["source_group"], item["second_target"])]
    if (
        first["source_doc_id"] != second["source_doc_id"]
        or first["split"] != second["split"]
    ):
        raise ValueError("P111 pair crosses source or split")
    source = _snapshot(
        {**first, "doc_id": first["source_doc_id"], "title": first["source_title"]}
    )["text"]
    body_a, fields_a = _target_body(first, target_manifest.parent)
    body_b, fields_b = _target_body(second, target_manifest.parent)
    key, value, field = item["selector_key"], item["selector_value"], item["field"]
    if (
        fields_a.get(field) != item["answer"]
        or fields_b.get(field) != item["alternate_answer"]
    ):
        raise ValueError("P111 target field changed")
    if any(
        v.casefold() in (body_a + body_b).casefold()
        for v in (value, item["alternate_selector_value"])
    ):
        raise ValueError("P111 selector shortcut in target")
    if any(
        x.casefold() in source.casefold()
        for x in (item["answer"], item["alternate_answer"])
    ):
        raise ValueError("P111 answer shortcut in source")
    if body_a.count(item["answer"]) != 1 or body_b.count(item["alternate_answer"]) != 1:
        raise ValueError("P111 repeated target answer")
    if item["answer"] in body_b or item["alternate_answer"] in body_a:
        raise ValueError("P111 crossed target answer support")
    labels = {
        first["row_name"]: first["target_url"],
        second["row_name"]: second["target_url"],
    }
    decorated = _source_with_links(source, labels)
    context = _context(
        first["source_title"],
        first["source_revid"],
        decorated,
        first["target_title"],
        first["target_revid"],
        body_a,
        second["target_title"],
        second["target_revid"],
        body_b,
    )
    if _solve(context, key, value, field) != item["answer"]:
        raise ValueError("P111 final reader answer replay differs")
    changed_source = _swap_selector(source, first["row_name"], second["row_name"], key)
    changed = _context(
        first["source_title"],
        first["source_revid"],
        _source_with_links(changed_source, labels),
        first["target_title"],
        first["target_revid"],
        body_a,
        second["target_title"],
        second["target_revid"],
        body_b,
    )
    if _solve(changed, key, value, field) != item["alternate_answer"]:
        raise ValueError("P111 selector-value swap did not change answer")
    if (
        body_a.count(f"{field}: {item['answer']}") != 1
        or body_b.count(f"{field}: {item['alternate_answer']}") != 1
    ):
        raise ValueError("P111 target field line ambiguous")
    sentinel = "withheld-for-intervention"
    changed_a = body_a.replace(f"{field}: {item['answer']}", f"{field}: {sentinel}")
    hit = _context(
        first["source_title"],
        first["source_revid"],
        decorated,
        first["target_title"],
        first["target_revid"],
        changed_a,
        second["target_title"],
        second["target_revid"],
        body_b,
    )
    if _solve(hit, key, value, field) != sentinel:
        raise ValueError("P111 target hit intervention failed")
    changed_b = body_b.replace(
        f"{field}: {item['alternate_answer']}", f"{field}: {sentinel}"
    )
    control = _context(
        first["source_title"],
        first["source_revid"],
        decorated,
        first["target_title"],
        first["target_revid"],
        body_a,
        second["target_title"],
        second["target_revid"],
        changed_b,
    )
    if _solve(control, key, value, field) != item["answer"]:
        raise ValueError("P111 target control intervention failed")
    removed_link = context.replace(
        f"[{first['row_name']}](<{first['target_url']}>)", first["row_name"]
    )
    if _solve(removed_link, key, value, field) is not None:
        raise ValueError("P111 link removal did not break answer")
    removed_field = context.replace(f"{field}: {item['answer']}", "")
    if _solve(removed_field, key, value, field) is not None:
        raise ValueError("P111 target field removal did not break answer")
    question = f"What {field.replace('_', ' ')} is given for the entry in {first['source_title']} whose {key} is {value}?"
    if (
        item["answer"].casefold() in question.casefold()
        or first["target_title"].casefold() in question.casefold()
    ):
        raise ValueError("P111 question leaks answer or bound entity")
    messages = [
        {"role": "user", "content": context + SEP + question},
        {"role": "assistant", "content": item["answer"]},
    ]
    tokenizer = get_tokenizer()
    encoded = tokenize_assistant_only(tokenizer, messages, 131072)
    full = len(encoded["input_ids"])
    supervised = sum(label != -100 for label in encoded["labels"])
    if (
        not supervised
        or encoded["labels"]
        != [-100] * (full - supervised) + encoded["input_ids"][full - supervised :]
    ):
        raise ValueError("P111 assistant-only mask failed")
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    user_text = messages[0]["content"]
    if prompt.count(user_text) != 1:
        raise ValueError("P111 final prompt reader occurrence differs")
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    user_start = prompt.index(user_text)
    source_doc = context.split("\n\n=== DOCUMENT:", 1)[0]
    selector_line = [
        line
        for line in source_doc.splitlines()
        if f"[{first['row_name']}](<{first['target_url']}>)" in line and value in line
    ]
    if len(selector_line) != 1:
        raise ValueError("P111 source selector row evidence ambiguous")
    selector_pos = context.index(selector_line[0]) + selector_line[0].index(value)
    target_marker = f"=== DOCUMENT: {first['target_title']} (revision {first['target_revid']}) ===\n"
    target_start = context.index(target_marker) + len(target_marker)
    field_line = f"{field}: {item['answer']}"
    field_pos = target_start + body_a.index(field_line) + len(field) + 2
    source_span = token_span(
        offsets, user_start + selector_pos, user_start + selector_pos + len(value)
    )
    target_span = token_span(
        offsets, user_start + field_pos, user_start + field_pos + len(item["answer"])
    )
    if source_span[1] >= full - supervised or target_span[1] >= full - supervised:
        raise ValueError("P111 evidence outside final prompt")
    digest = hashlib.sha256(
        f"{first['source_doc_id']}|{first['target_title']}|{second['target_title']}|{key}|{field}".encode()
    ).hexdigest()[:20]
    sample_id = "p111-wiki-linked-" + digest
    index = {
        "sample_id": sample_id,
        "example_id": sample_id,
        "task_id": digest,
        "world_id": first["source_group"],
        "source_group": first["source_group"],
        "split": first["split"],
        "source_kind": "real_wiki",
        "domain": first["domain"],
        "topic": first["topic"],
        "operation": "list_selector_linked_article_attribute",
        "family": "join",
        "task_type": "list_selector_linked_article_attribute",
        "dependency_status": "reader_visible_source_selector_link_and_target_field_interventions",
        "question_style": "natural_attribute_query",
        "tokenizer_profile": "pinned-chat-template",
        "full_chat_tokens": full,
        "input_tokens": full - supervised,
        "supervised_tokens": supervised,
        "evidence_token_extent": max(source_span[1], target_span[1])
        - min(source_span[0], target_span[0]),
        "selector_to_target_token_gap": abs(source_span[0] - target_span[0]),
        "answer_sha256": hashlib.sha256(item["answer"].encode()).hexdigest(),
    }
    proof = {
        **item,
        "sample_id": sample_id,
        "source_snapshot_sha256": first["source_snapshot_sha256"],
        "target_response_sha256": [first["response_sha256"], second["response_sha256"]],
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "reader_sha256": hashlib.sha256(user_text.encode()).hexdigest(),
        "selector_char_span": [selector_pos, selector_pos + len(value)],
        "target_char_span": [field_pos, field_pos + len(item["answer"])],
        "selector_token_span": list(source_span),
        "target_token_span": list(target_span),
        "interventions": {
            "selector_swap_answer": item["alternate_answer"],
            "target_hit_answer": sentinel,
            "target_control_answer": item["answer"],
            "link_removed_answer": None,
            "target_field_removed_answer": None,
        },
    }
    return {"sample_id": sample_id, "messages": messages}, index, proof


def run(
    target_manifest: Path,
    body_ledger: Path,
    output_dir: Path,
    *,
    verify_only: bool = False,
) -> dict:
    target_manifest, body_ledger = (
        (ROOT / target_manifest).absolute(),
        (ROOT / body_ledger).absolute(),
    )
    body = json.loads(body_ledger.read_text())
    if body["target_manifest_sha256"] != _sha(target_manifest):
        raise ValueError("P111 body/target receipt mismatch")
    choices = _choose(body, target_manifest)
    with ProcessPoolExecutor(max_workers=4) as pool:
        rows = list(
            pool.map(
                _compile,
                choices,
                [target_manifest] * len(choices),
                [body_ledger] * len(choices),
            )
        )
    files = {
        "train.jsonl": [],
        "eval.jsonl": [],
        "sample_index.jsonl": [],
        "audit.jsonl": [],
    }
    for reader, index, proof in rows:
        files[index["split"] + ".jsonl"].append(_dump(reader))
        files["sample_index.jsonl"].append(_dump(index))
        files["audit.jsonl"].append(_dump(proof))
    manifest = {
        "schema": SCHEMA,
        "target_manifest_sha256": _sha(target_manifest),
        "body_ledger_sha256": _sha(body_ledger),
        "structural_pairs": body["two_target_common_field_pairs"],
        "selected_disjoint_pairs": len(choices),
        "reader_tasks": len(rows),
        "worlds": len({i["source_group"] for _, i, _ in rows}),
        "splits": dict(sorted(Counter(i["split"] for _, i, _ in rows).items())),
        "domains": dict(sorted(Counter(i["domain"] for _, i, _ in rows).items())),
        "full_chat_tokens": sum(i["full_chat_tokens"] for _, i, _ in rows),
        "supervised_tokens": sum(i["supervised_tokens"] for _, i, _ in rows),
        "files_sha256": {},
        "train_ready": False,
    }
    output_dir = (ROOT / output_dir).absolute()
    for filename, lines in files.items():
        content = ("\n".join(lines) + ("\n" if lines else "")).encode()
        manifest["files_sha256"][filename] = hashlib.sha256(content).hexdigest()
        path = output_dir / filename
        if verify_only:
            if not path.exists() or path.read_bytes() != content:
                raise ValueError(f"P111 native replay differs: {filename}")
        else:
            if path.exists() and path.read_bytes() != content:
                raise ValueError(
                    f"P111 native file exists with different bytes: {filename}"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    encoded = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    if verify_only:
        if (output_dir / "manifest.json").read_bytes() != encoded:
            raise ValueError("P111 native manifest replay differs")
    else:
        path = output_dir / "manifest.json"
        if path.exists() and path.read_bytes() != encoded:
            raise ValueError("P111 native manifest exists with different bytes")
        path.write_bytes(encoded)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--body-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(
        args.target_manifest,
        args.body_ledger,
        args.output_dir,
        verify_only=args.verify_only,
    )
    print(
        _dump(
            {
                k: result[k]
                for k in (
                    "structural_pairs",
                    "selected_disjoint_pairs",
                    "reader_tasks",
                    "worlds",
                    "splits",
                    "domains",
                    "full_chat_tokens",
                    "supervised_tokens",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
