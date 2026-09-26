"""Compile source-backed long TeX reference QA with exact reader-side gates.

This broadens P127's single-line/same-file candidate search to bounded prose
fragments around same- or cross-file references. It retains unique-target,
answer leakage, alternative support, dual visible-deletion, final token gap,
assistant-only mask, and source split checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.p66_researchlab_taskbank import load_text_tar
from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    NativeCandidate,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.p96_paper_caption_qa import (
    REF,
    SECTION,
    _active,
    _records,
    _targets,
    cue_for_reference,
    render_files,
    shortcut_reason,
)
from scripts.p104_paper_quality_gate import quality_status
from scripts.p127_same_file_paper_reference import _word_boundary
from scripts.run_p86_frozen_paper_batch import _dedupe
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p146-real-paper-reference.v1"
SEPARATOR = "\n\nQUESTION\n"
WORD = re.compile(r"[A-Za-z]{3,}")
OPENING = re.compile(r"\A([A-Z][^.!?]{45,220}[.!?])", re.DOTALL)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dump(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def pin(value: dict) -> Path:
    if (
        not isinstance(value, dict)
        or not {"path", "sha256"} <= set(value)
        or not set(value) <= {"path", "sha256", "version"}
    ):
        raise ValueError("source pin needs path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or sha(path.read_bytes()) != value["sha256"]:
        raise ValueError(f"source pin drift: {relative}")
    return path


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def candidate_cues(text: str, ref: re.Match[str]) -> list[str]:
    """Quote a unique nearby prose fragment without TeX control text."""
    native = cue_for_reference(text, ref)
    options = [native] if native else []
    left = text[max(0, ref.start() - 350) : ref.start()]
    left = left.rsplit("\\", 1)[-1].rsplit("{", 1)[-1].rsplit("}", 1)[-1]
    left = left.rsplit("%", 1)[-1].rsplit("\n\n", 1)[-1][-150:]
    right = text[ref.end() : ref.end() + 350]
    right = re.split(r"\\|[{}%]|\n\n", right, 1)[0][:150]
    options.extend((left, right))
    fragments = []
    for raw in options:
        if raw is None:
            continue
        fragments.append(raw)
        # A citation parenthetical or inline TeX display often follows an
        # otherwise usable prose clause. Quote the clause, not the markup.
        fragments.append(raw.split("(", 1)[0])
        fragments.append(re.split(r"[$~^_]", raw, 1)[0])
    clean = []
    for raw in fragments:
        cue = raw.strip(" \n\t.,;:()[]")
        if (
            35 <= len(cue) <= 150
            and len(WORD.findall(cue)) >= 6
            and not any(
                mark in cue for mark in ("\\", "{", "}", "%", "$", "~", "^", "_")
            )
            and cue not in clean
        ):
            clean.append(cue)
    return clean


def section_opening(text: str, offset: int, label: str) -> tuple[str, list[int]] | None:
    """Locate the first full plain-prose sentence after an exact section label."""
    matches = [
        item
        for item in SECTION.finditer(text)
        if item.group(2) == label and _active(text, item.start())
    ]
    if len(matches) != 1:
        return None
    heading = matches[0]
    tail = text[heading.end() : heading.end() + 350]
    whitespace = len(tail) - len(tail.lstrip())
    match = OPENING.match(tail[whitespace:])
    if match is None:
        return None
    answer = match.group(1)
    if len(WORD.findall(answer)) < 9 or any(
        mark in answer for mark in ("\\", "{", "}", "%", "$", "~")
    ):
        return None
    start = offset + heading.end() + whitespace
    return answer, [start, start + len(answer)]


def resolve(
    context: str, cue: str, answer_mode: str = "heading_or_caption"
) -> dict | None:
    """Reconstruct the sole adjacent active reference and unique target."""
    if not cue or context.count(cue) != 1:
        return None
    records = _records(context)
    targets = _targets(records)
    hits = []
    for path, (text, offset) in records.items():
        at = text.find(cue)
        if at < 0:
            continue
        nearby_start = max(0, at - 200)
        nearby_end = min(len(text), at + len(cue) + 200)
        refs = [
            ref
            for ref in REF.finditer(text, nearby_start, nearby_end)
            if _active(text, ref.start())
        ]
        if len(refs) != 1:
            return None
        ref = refs[0]
        # The prose cue must border the reference; another command or sentence
        # between them would make the intended binding ambiguous.
        between = (
            text[at + len(cue) : ref.start()]
            if at + len(cue) <= ref.start()
            else text[ref.end() : at]
        )
        if len(between) > 70 or re.search(r"\\[A-Za-z]+|[{}%]", between):
            return None
        options = targets.get(ref.group(1), [])
        if len(options) != 1:
            return None
        target = options[0]
        if answer_mode == "section_opening":
            if target["kind"] != "section":
                return None
            opening = section_opening(*records[target["path"]], ref.group(1))
            if opening is None:
                return None
            answer, target_span = opening
            kind = "section_opening_sentence"
        elif answer_mode == "heading_or_caption":
            answer, target_span, kind = (
                target["caption"],
                target["caption_span"],
                target["kind"],
            )
        else:
            raise ValueError("unknown answer mode")
        hits.append(
            {
                "source_path": path,
                "target_path": target["path"],
                "reference_label": ref.group(1),
                "reference_span": [offset + ref.start(), offset + ref.end()],
                "target_span": target_span,
                "kind": kind,
                "answer": answer,
            }
        )
    return hits[0] if len(hits) == 1 else None


def question(cue: str, kind: str) -> str:
    quoted = " ".join(cue.split())
    if kind == "section_opening_sentence":
        return (
            "In the paper source passage containing ‘" + quoted + "’, "
            "what is the first complete plain-prose sentence immediately after "
            "the heading of the referenced section? Return only that sentence."
        )
    target = "section heading" if kind == "section" else kind + " caption"
    return (
        "In the paper source passage containing ‘" + quoted + "’, "
        "what is the exact " + target + " identified by its adjacent reference? "
        "Return only that heading or caption."
    )


def compile_link(
    context: str, work: dict, link: dict, tokenizer, cfg: dict, receipt: Path
) -> tuple[dict, dict, dict, dict]:
    result = resolve(context, link["cue"], link["answer_mode"])
    if result is None or any(
        result[key] != link[key]
        for key in (
            "reference_label",
            "reference_span",
            "target_span",
            "source_path",
            "target_path",
        )
    ):
        raise ValueError("final_reader_reference_resolution_disagrees")
    answer = result["answer"]
    if (
        shortcut_reason(link["cue"], result["reference_label"], answer, result["kind"])
        or quality_status(context, answer) != "accepted_raw_tex_reference"
        or not _word_boundary(
            link["cue"],
            result["reference_label"],
            answer,
            cfg["min_unexposed_answer_words"],
        )
    ):
        raise ValueError("final_reader_answer_shortcut")
    deletions = {}
    for name in ("reference_span", "target_span"):
        start, end = result[name]
        minus = context[:start] + "?" * (end - start) + context[end:]
        if resolve(minus, link["cue"], link["answer_mode"]) is not None:
            raise ValueError(name + "_deletion_did_not_break_resolution")
        deletions[name] = sha(minus.encode())
    sample_id = (
        "p146-paper-"
        + sha(
            dump(
                [
                    work["work_id"],
                    link["cue"],
                    result["reference_label"],
                    link["answer_mode"],
                ]
            )
        )[:20]
    )
    reader = {
        "sample_id": sample_id,
        "messages": [
            {
                "role": "user",
                "content": context + SEPARATOR + question(link["cue"], result["kind"]),
            },
            {"role": "assistant", "content": answer},
        ],
    }
    encoded = tokenize_assistant_only(
        tokenizer, reader["messages"], cfg["max_full_chat_tokens"]
    )
    full = len(encoded["input_ids"])
    supervised = sum(label != -100 for label in encoded["labels"])
    if full < cfg["min_full_chat_tokens"] or supervised < 1:
        raise ValueError("final_chat_length_or_supervision_invalid")
    prompt = _render_chat(tokenizer, reader["messages"][:1], generation_prompt=True)
    user = reader["messages"][0]["content"]
    if prompt.count(user) != 1:
        raise ValueError("final_reader_user_serialization_ambiguous")
    start = prompt.index(user)
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    spans = {
        name: list(token_span(offsets, start + a, start + b))
        for name, (a, b) in (
            ("reference", result["reference_span"]),
            ("target", result["target_span"]),
        )
    }
    gap = max(span[0] for span in spans.values()) - min(
        span[1] for span in spans.values()
    )
    extent = max(span[1] for span in spans.values()) - min(
        span[0] for span in spans.values()
    )
    query_start = token_span(
        offsets, start + len(context) + len(SEPARATOR), start + len(user)
    )[0]
    if (
        gap < cfg["min_support_gap_tokens"]
        or max(span[1] for span in spans.values()) >= query_start
        or query_start > full - supervised
    ):
        raise ValueError("final_token_evidence_extent_or_order_invalid")
    raw_index = {
        "sample_id": sample_id,
        "semantic_task_id": sample_id,
        "source_kind": "real_paper_source",
        "source_group": work["source_group"],
        "split": work["split"],
        "domain": "researchlab",
        "topic": "paper:" + work["work_id"],
        "operation": "source_reference_target_text",
        "context_sha256": sha(context.encode()),
        "answer_sha256": sha(answer.encode()),
        "full_chat_tokens": full,
        "input_tokens": full - supervised,
        "supervised_tokens": supervised,
        "length_bin": "native",
        "observed_lineage_token_envelope": {
            "start": min(span[0] for span in spans.values()),
            "end": max(span[1] for span in spans.values()),
        },
        "evidence_status": "unique_active_reference_and_exact_visible_target",
        "dependency_status": "bounded_reference_and_target_text_deletions_passed",
    }
    binding = AdapterBinding(
        source_kind="real_paper_source",
        source_group=work["source_group"],
        domain="researchlab",
        topic="paper:" + work["work_id"],
        operation="source_reference_target_text",
        evidence_profile="raw_tex_reference_target_dual_text_deletion",
        tokenizer_profile="pinned-chat-template",
        receipt_path=receipt,
        receipt_sha256=sha(receipt.read_bytes()),
    )
    index = normalize_native_candidate(
        raw_index, reader, binding, context_text=context
    ).to_dict()
    index.update(
        evidence_extent_tokens=extent,
        bounded_support_gap_tokens=gap,
        last_evidence_to_query_tokens=query_start
        - max(span[1] for span in spans.values()),
    )
    mask = audit_reader(reader, index, tokenizer, cfg["max_full_chat_tokens"])
    proof = {
        "sample_id": sample_id,
        "source_archive": work["source_archive"],
        "source_path": result["source_path"],
        "target_path": result["target_path"],
        "source_cue": link["cue"],
        "reference_label": result["reference_label"],
        "answer": answer,
        "kind": result["kind"],
        "answer_mode": link["answer_mode"],
        "reader_char_spans": {
            "reference": result["reference_span"],
            "target": result["target_span"],
        },
        "prompt_token_spans": spans,
        "query_token_start": query_start,
        "evidence_extent_tokens": extent,
        "bounded_support_gap_tokens": gap,
        "reader_context_minus_sha256": deletions,
        "bounded_alternative_support": {
            "exact_answer_occurrences_in_reader": context.casefold().count(
                answer.casefold()
            ),
            "active_reference_target_count_for_label": 1,
            "unique_source_cue_occurrences": context.count(link["cue"]),
            "scope": "exact answer string and parser-recognized TeX label targets only",
        },
        "claim_limit": "visible unique source reference/target under dual deletion; no unrestricted natural semantic proof or license clearance",
    }
    return reader, index, proof, mask


def work_job(args: tuple[dict, dict, str, set]) -> tuple[dict, list[dict], list]:
    work, cfg, receipt_text, prior_answers = args
    path = pin(work["source_archive"])
    result = {
        "work_id": work["work_id"],
        "source_group": work["source_group"],
        "split": work["split"],
        "license_status": work["license_status"],
        "source_archive": work["source_archive"],
    }
    ledger: list[dict] = []
    try:
        files, duplicate_files = _dedupe(load_text_tar(path))
        context = render_files(files)
        records = _records(context)
        targets = _targets(records)
    except (OSError, ValueError) as error:
        result.update(
            status="source_parser_rejected",
            parser_error=type(error).__name__,
            candidate_tasks=0,
        )
        return result, ledger, []
    result.update(
        source_files=len(files),
        duplicate_files=duplicate_files,
        source_chars=len(context),
    )
    proposals = []
    for source_path, (text, offset) in records.items():
        for ref in REF.finditer(text):
            base = {
                "work_id": work["work_id"],
                "source_path": source_path,
                "reference_label": ref.group(1),
                "reference_span": [offset + ref.start(), offset + ref.end()],
            }
            if not _active(text, ref.start()):
                ledger.append({**base, "status": "commented_reference"})
                continue
            options = targets.get(ref.group(1), [])
            if len(options) != 1:
                ledger.append({**base, "status": "no_unique_target"})
                continue
            target = options[0]
            modes = ["heading_or_caption"]
            if target["kind"] == "section":
                modes.append("section_opening")
            cues = candidate_cues(text, ref)
            for mode in modes:
                item = {
                    **base,
                    "target_path": target["path"],
                    "target_kind": target["kind"],
                    "answer_mode": mode,
                }
                if mode == "section_opening":
                    opening = section_opening(*records[target["path"]], ref.group(1))
                    if opening is None:
                        item["status"] = "no_plain_prose_section_opening"
                        ledger.append(item)
                        continue
                    answer, item["target_span"] = opening
                else:
                    answer, item["target_span"] = (
                        target["caption"],
                        target["caption_span"],
                    )
                quality = quality_status(context, answer)
                if quality != "accepted_raw_tex_reference":
                    item["status"] = quality
                else:
                    chosen = None
                    unique_cue = nonleaking_cue = False
                    for cue in cues:
                        if context.count(cue) != 1:
                            continue
                        unique_cue = True
                        if shortcut_reason(
                            cue, ref.group(1), answer, target["kind"]
                        ) or not _word_boundary(
                            cue, ref.group(1), answer, cfg["min_unexposed_answer_words"]
                        ):
                            continue
                        nonleaking_cue = True
                        resolved = resolve(context, cue, mode)
                        if (
                            resolved is not None
                            and resolved["reference_span"] == item["reference_span"]
                            and resolved["target_span"] == item["target_span"]
                        ):
                            chosen = cue
                            break
                    if chosen is None:
                        item["status"] = (
                            "no_unique_visible_prose_cue"
                            if not unique_cue
                            else "cue_or_label_answer_leak"
                            if not nonleaking_cue
                            else "cue_reference_ambiguity"
                        )
                    else:
                        item["cue"] = chosen
                        item["answer_sha256"] = sha(answer.encode())
                        item["status"] = "source_shape_candidate"
                        proposals.append(item)
                ledger.append(item)
    proposals.sort(
        key=lambda x: (
            -abs(x["reference_span"][0] - x["target_span"][0]),
            x["reference_label"],
            x["cue"],
        )
    )
    accepted = []
    used_labels = set()
    used_answers = set()
    tokenizer = get_tokenizer() if proposals else None
    receipt = ROOT / receipt_text
    for item in proposals:
        if (work["source_group"], item["answer_sha256"]) in prior_answers:
            item["status"] = "prior_exact_answer_overlap"
            continue
        if (
            item["reference_label"] in used_labels
            or item["answer_sha256"] in used_answers
        ):
            item["status"] = "duplicate_target_in_work"
            continue
        if len(accepted) >= cfg["max_tasks_per_work"]:
            item["status"] = "not_materialized_after_cap"
            continue
        try:
            candidate = compile_link(context, work, item, tokenizer, cfg, receipt)
        except (ValueError, OverflowError) as error:
            item["status"] = str(error)
            continue
        item["status"] = "accepted"
        accepted.append(candidate)
        used_labels.add(item["reference_label"])
        used_answers.add(item["answer_sha256"])
    result.update(
        status="source_parsed",
        active_references=len(
            {
                tuple(row["reference_span"])
                for row in ledger
                if row["status"] != "commented_reference"
            }
        ),
        unique_targets=sum("target_span" in row for row in ledger),
        candidate_tasks=len(accepted),
        reason_counts=dict(sorted(Counter(row["status"] for row in ledger).items())),
    )
    return result, ledger, accepted


def build(config_path: Path, output: Path) -> dict[str, bytes]:
    cfg = json.loads(config_path.read_text())
    if (
        cfg.get("schema") != SCHEMA + ".config"
        or type(cfg.get("workers")) is not int
        or not 1 <= cfg["workers"] <= 8
        or type(cfg.get("max_tasks_per_work")) is not int
        or not 1 <= cfg["max_tasks_per_work"] <= 8
        or type(cfg.get("min_unexposed_answer_words")) is not int
        or not 1 <= cfg["min_unexposed_answer_words"] <= 6
        or not 8192 <= cfg.get("min_support_gap_tokens", 0) <= 32768
        or not 16384
        <= cfg.get("min_full_chat_tokens", 0)
        < cfg.get("max_full_chat_tokens", 0)
        <= 262144
    ):
        raise ValueError("invalid P146 request")
    p141_path = pin(cfg["p141_manifest"])
    p141 = json.loads(p141_path.read_text())
    if p141["schema"] != "longworld.p141-source-opportunity.v1.manifest":
        raise ValueError("wrong P141 source index")
    opportunity_path = p141_path.parent / "opportunities.jsonl"
    if (
        sha(opportunity_path.read_bytes())
        != p141["files_sha256"]["opportunities.jsonl"]
    ):
        raise ValueError("P141 opportunity index drift")
    eligible_groups = {
        row["source_group"]
        for row in rows(opportunity_path)
        if row["source_kind"] == "real_paper_tex"
    }
    p127_path = pin(cfg["p127_manifest"])
    p127 = json.loads(p127_path.read_text())
    if p127["source_works"] != 34:
        raise ValueError("P127 frozen work inventory changed")
    support_path = p127_path.parent / "support_matrix.jsonl"
    if sha(support_path.read_bytes()) != p127["files_sha256"]["support_matrix.jsonl"]:
        raise ValueError("P127 source matrix drift")
    works = [row for row in rows(support_path) if row["status"] == "source_parsed"]
    if len(works) != p127["source_parsed_works"] or not eligible_groups <= {
        row["source_group"] for row in works
    }:
        raise ValueError("P141/P127 eligible source mismatch")
    prior_path = pin(cfg["prior_refs"])
    prior = [row["candidate"] for row in rows(prior_path)]
    prior_tasks = {row["semantic_task_id"] for row in prior}
    prior_answers = {
        (row["source_group"], row["answer_sha256"])
        for row in prior
        if row["source_kind"].startswith("real_paper")
    }
    tasks = [
        (work, cfg, str(support_path.relative_to(ROOT)), prior_answers)
        for work in works
    ]
    with ProcessPoolExecutor(max_workers=cfg["workers"]) as pool:
        processed = list(pool.map(work_job, tasks))
    relative_output = output.relative_to(ROOT) if output.is_absolute() else output
    if ".." in relative_output.parts:
        raise ValueError("output path escapes workspace")
    support, decisions, proofs, masks, indices = [], [], [], [], []
    readers = {"train": [], "eval": []}
    ledger = CandidateLedger()
    for work_row, local_decisions, accepted in processed:
        support.append(work_row)
        decisions.extend(local_decisions)
        for reader, index, proof, mask in accepted:
            if (
                index["semantic_task_id"] in prior_tasks
                or (index["source_group"], index["answer_sha256"]) in prior_answers
            ):
                raise ValueError("P146 task overlaps P139 bank")
            ledger.add(
                NativeCandidate(
                    **{
                        name: index[name]
                        for name in NativeCandidate.__dataclass_fields__
                    }
                )
            )
            split = index["split"]
            index["native_row_ref"] = (
                str(relative_output / "audit.jsonl") + ":" + str(len(proofs))
            )
            index.update(
                output_file=f"candidate_{split}.jsonl",
                row_index=len(readers[split]),
                source_name="p146_real_paper_source_reference",
            )
            readers[split].append(reader)
            indices.append(index)
            proofs.append(proof)
            masks.append(mask)
    files = {
        "candidate_train.jsonl": b"".join(dump(row) for row in readers["train"]),
        "candidate_eval.jsonl": b"".join(dump(row) for row in readers["eval"]),
        "sample_index.jsonl": b"".join(dump(row) for row in indices),
        "audit.jsonl": b"".join(dump(row) for row in proofs),
        "mask_rows.jsonl": b"".join(dump(row) for row in masks),
        "support_matrix.jsonl": b"".join(dump(row) for row in support),
        "decision_ledger.jsonl": b"".join(dump(row) for row in decisions),
    }
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "p146_schema": SCHEMA + ".result",
        "compiler_sha256": sha(Path(__file__).read_bytes()),
        "config_sha256": sha(config_path.read_bytes()),
        "p141_manifest_sha256": cfg["p141_manifest"]["sha256"],
        "p127_manifest_sha256": cfg["p127_manifest"]["sha256"],
        "prior_candidate_refs_sha256": cfg["prior_refs"]["sha256"],
        "source_works": p127["source_works"],
        "source_parsed_works": len(works),
        "source_license_statuses": dict(
            sorted(Counter(row["license_status"] for row in support).items())
        ),
        "redistribution_status": "local_research_only_no_redistribution",
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "splits": {split: len(rows) for split, rows in readers.items()},
        "views_by_lane": {"p146_real_paper_source_reference": ledger.rows},
        "accepted_worlds": len({row["source_group"] for row in indices}),
        "decision_counts": dict(
            sorted(Counter(row["status"] for row in decisions).items())
        ),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in indices),
        "supervised_tokens": sum(row["supervised_tokens"] for row in indices),
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in indices).items())
        ),
        "files_sha256": {name: sha(raw) for name, raw in files.items()},
        "claim_limit": "unique active raw-TeX reference and exact target under two visible text deletions; no unrestricted semantic proof, license clearance or model-gain claim",
        "train_ready": False,
    }
    files["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    return files


def run(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config_path = config_path if config_path.is_absolute() else ROOT / config_path
    output = output if output.is_absolute() else ROOT / output
    if verify_only:
        frozen = json.loads((output / "manifest.json").read_text())
        if frozen.get("compiler_sha256") != sha(Path(__file__).read_bytes()):
            raise ValueError("P146 compiler code changed since freeze")
    files = build(config_path, output)
    if verify_only:
        if not output.is_dir() or {path.name for path in output.iterdir()} != set(
            files
        ):
            raise ValueError("P146 output inventory drift")
        for name, raw in files.items():
            if (output / name).read_bytes() != raw:
                raise ValueError(f"P146 replay drift: {name}")
    else:
        if output.exists():
            raise ValueError("P146 output must be new")
        output.mkdir(parents=True)
        for name, raw in files.items():
            (output / name).write_bytes(raw)
    return (
        verify_merge(output)
        if json.loads(files["manifest.json"])["candidate_views"]
        else json.loads(files["manifest.json"])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.config, args.output, verify_only=args.verify_only), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
