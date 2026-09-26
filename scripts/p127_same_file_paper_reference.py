"""Compile bounded same-file TeX reference tasks from pinned paper sources."""

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
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.audit_wiki_join_positions import token_span
from scripts.p96_paper_caption_qa import (
    REF,
    _active,
    _records,
    _targets,
    cue_for_reference,
    render_files,
    shortcut_reason,
)
from scripts.p104_paper_quality_gate import quality_status
from scripts.run_p86_frozen_paper_batch import _dedupe
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p127-same-file-paper-reference.v1"
OPERATION = "same_file_section_or_caption_reference"
SEPARATOR = "\n\nQUESTION\n"
WORDS = re.compile(r"[A-Za-z]{4,}")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _dump(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _pin(pin: dict) -> Path:
    if (
        not isinstance(pin, dict)
        or not {"path", "sha256"} <= set(pin)
        or not set(pin) <= {"path", "sha256", "version"}
    ):
        raise ValueError("P127 source pin needs path and sha256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P127 source pin path must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path.read_bytes()) != pin["sha256"]:
        raise ValueError(f"P127 pinned source drift: {relative}")
    return path


def _config(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    if (
        cfg.get("schema") != "longworld.p127-same-file-paper-reference-request.v1"
        or not isinstance(cfg.get("work_matrices"), list)
        or len(cfg["work_matrices"]) != 2
        or not 1 <= cfg.get("workers", 0) <= 8
        or not 1 <= cfg.get("max_tasks_per_work", 0) <= 4
        or not 1 <= cfg.get("min_unexposed_answer_words", 0) <= 6
        or not 8192 <= cfg.get("min_support_gap_tokens", 0) <= 32768
        or not 16384
        <= cfg.get("min_full_chat_tokens", 0)
        < cfg.get("max_full_chat_tokens", 0)
        <= 262144
    ):
        raise ValueError("invalid bounded P127 request")
    return cfg


def _word_boundary(cue: str, label: str, answer: str, minimum: int) -> bool:
    """Keep headings with substantial words absent from cue and TeX label."""
    answer_words = {word.casefold() for word in WORDS.findall(answer)}
    label_words = {word.casefold() for word in WORDS.findall(label)}
    cue_words = {word.casefold() for word in WORDS.findall(cue)}

    def singular(word: str) -> str:
        return word[:-1] if len(word) > 5 and word.endswith("s") else word

    if any(
        singular(answer_word).startswith(singular(label_word))
        or singular(label_word).startswith(singular(answer_word))
        for answer_word in answer_words
        for label_word in label_words
    ):
        return False
    label_tail = "".join(
        char.casefold() for char in label.rsplit(":", 1)[-1] if char.isalnum()
    )
    compact_answer = "".join(char.casefold() for char in answer if char.isalnum())
    if len(label_tail) >= 6 and label_tail in compact_answer:
        return False
    return (
        not answer_words & label_words
        and len(answer_words - label_words - cue_words) >= minimum
    )


def resolve_same_file(context: str, cue: str) -> dict | None:
    """Resolve one active TeX reference to one target in its own source file."""
    if not cue or "%" in cue or context.count(cue) != 1:
        return None
    records = _records(context)
    targets = _targets(records)
    hits = []
    for path, (text, offset) in records.items():
        if cue not in text:
            continue
        for ref in REF.finditer(text):
            if not _active(text, ref.start()) or cue_for_reference(text, ref) != cue:
                continue
            options = targets.get(ref.group(1), [])
            if len(options) != 1 or options[0]["path"] != path:
                return None
            target = options[0]
            hits.append(
                {
                    "source_path": path,
                    "reference_label": ref.group(1),
                    "reference_span": [offset + ref.start(), offset + ref.end()],
                    "target_span": target["caption_span"],
                    "kind": target["kind"],
                    "answer": target["caption"],
                }
            )
    return hits[0] if len(hits) == 1 else None


def _question(cue: str, kind: str) -> str:
    if kind == "section":
        return (
            "In the paper source sentence containing ‘"
            + cue
            + "’, what is the exact heading of the section named by its nearby "
            "reference? Return only the heading."
        )
    return (
        "In the paper source sentence containing ‘"
        + cue
        + "’, what is the exact caption of the nearby referenced "
        + kind
        + "? Return only the caption."
    )


def _compile_link(
    context: str, work: dict, link: dict, tokenizer, cfg: dict, receipt: Path
) -> tuple[dict, dict, dict, dict]:
    resolved = resolve_same_file(context, link["cue"])
    if (
        resolved is None
        or resolved["reference_label"] != link["reference_label"]
        or resolved["reference_span"] != link["reference_span"]
        or resolved["target_span"] != link["target_span"]
    ):
        raise ValueError("final_reader_same_file_resolution_disagrees")
    answer = resolved["answer"]
    if (
        shortcut_reason(link["cue"], link["reference_label"], answer, resolved["kind"])
        or quality_status(context, answer) != "accepted_raw_tex_reference"
        or not _word_boundary(
            link["cue"],
            link["reference_label"],
            answer,
            cfg["min_unexposed_answer_words"],
        )
    ):
        raise ValueError("final_reader_answer_shortcut")
    deletions = {}
    for name in ("reference_span", "target_span"):
        start, end = resolved[name]
        minus = context[:start] + "?" * (end - start) + context[end:]
        if resolve_same_file(minus, link["cue"]) is not None:
            raise ValueError(name + "_deletion_did_not_break_resolution")
        deletions[name] = _sha(minus.encode())
    question = _question(link["cue"], resolved["kind"])
    reader = {
        "sample_id": "p127-paper-"
        + _sha(
            json.dumps(
                [
                    work["work_id"],
                    work["source_archive"]["sha256"],
                    link["reference_label"],
                    link["cue"],
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )[:20],
        "messages": [
            {"role": "user", "content": context + SEPARATOR + question},
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
            ("reference", resolved["reference_span"]),
            ("target", resolved["target_span"]),
        )
    }
    extent = max(span[1] for span in spans.values()) - min(
        span[0] for span in spans.values()
    )
    support_gap = max(span[0] for span in spans.values()) - min(
        span[1] for span in spans.values()
    )
    query_start = token_span(
        offsets, start + len(context) + len(SEPARATOR), start + len(user)
    )[0]
    if (
        support_gap < cfg["min_support_gap_tokens"]
        or max(span[1] for span in spans.values()) >= query_start
        or query_start > full - supervised
    ):
        raise ValueError("final_token_evidence_extent_or_order_invalid")
    raw_index = {
        "sample_id": reader["sample_id"],
        "semantic_task_id": reader["sample_id"],
        "source_kind": "real_paper_source",
        "source_group": work["source_group"],
        "split": work["split"],
        "domain": "researchlab",
        "topic": "paper:" + work["work_id"],
        "operation": OPERATION,
        "context_sha256": _sha(context.encode()),
        "answer_sha256": _sha(answer.encode()),
        "full_chat_tokens": full,
        "input_tokens": full - supervised,
        "supervised_tokens": supervised,
        "length_bin": "native",
        "observed_lineage_token_envelope": {
            "start": min(span[0] for span in spans.values()),
            "end": max(span[1] for span in spans.values()),
        },
        "evidence_status": "unique_active_same_file_tex_reference_and_exact_answer",
        "dependency_status": "bounded_reference_and_target_text_deletions_passed",
    }
    binding = AdapterBinding(
        source_kind="real_paper_source",
        source_group=work["source_group"],
        domain="researchlab",
        topic="paper:" + work["work_id"],
        operation=OPERATION,
        evidence_profile="raw_tex_same_file_reference_dual_text_deletion",
        tokenizer_profile="pinned-chat-template",
        receipt_path=receipt,
        receipt_sha256=_sha(receipt.read_bytes()),
    )
    index = normalize_native_candidate(
        raw_index, reader, binding, context_text=context
    ).to_dict()
    index.update(
        evidence_extent_tokens=extent,
        bounded_support_gap_tokens=support_gap,
        last_evidence_to_query_tokens=query_start
        - max(span[1] for span in spans.values()),
    )
    mask = audit_reader(reader, index, tokenizer, cfg["max_full_chat_tokens"])
    proof = {
        "sample_id": reader["sample_id"],
        "source_archive": work["source_archive"],
        "source_path": resolved["source_path"],
        "source_cue": link["cue"],
        "reference_label": resolved["reference_label"],
        "answer": answer,
        "kind": resolved["kind"],
        "reader_char_spans": {
            "reference": resolved["reference_span"],
            "target": resolved["target_span"],
        },
        "prompt_token_spans": spans,
        "query_token_start": query_start,
        "evidence_extent_tokens": extent,
        "bounded_support_gap_tokens": support_gap,
        "bounded_alternative_support": {
            "exact_answer_occurrences_in_reader": context.casefold().count(
                answer.casefold()
            ),
            "active_reference_target_count_for_label": 1,
            "unique_source_cue_occurrences": context.count(link["cue"]),
            "scope": "exact answer string and parser-recognized TeX label targets only",
        },
        "reader_context_minus_sha256": deletions,
        "claim_limit": "unique visible same-file TeX reference and target; no compiled-PDF or unrestricted semantic-proof claim",
    }
    return reader, index, proof, mask


def _work_job(args: tuple[dict, dict, str, set]) -> tuple[dict, list[dict], list]:
    work, cfg, receipt_text, prior_answers = args
    source = work["source_archive"]
    path = _pin(source)
    result = {
        "work_id": work["work_id"],
        "source_group": work["source_group"],
        "split": work["split"],
        "source_archive": source,
        "license_status": work["license_status"],
    }
    ledger = []
    try:
        files, duplicate_files = _dedupe(load_text_tar(path))
        context = render_files(files)
        records = _records(context)
        targets = _targets(records)
    except (OSError, ValueError) as error:
        result.update(
            status="source_parser_rejected",
            source_files=0,
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
            item = {
                "work_id": work["work_id"],
                "source_path": source_path,
                "reference_label": ref.group(1),
                "reference_span": [offset + ref.start(), offset + ref.end()],
            }
            if not _active(text, ref.start()):
                item["status"] = "commented_reference"
            else:
                options = targets.get(ref.group(1), [])
                if len(options) != 1 or options[0]["path"] != source_path:
                    item["status"] = "no_unique_same_file_target"
                else:
                    item["target_span"] = options[0]["caption_span"]
                    cue = cue_for_reference(text, ref)
                    if cue is None or "%" in cue or context.count(cue) != 1:
                        item["status"] = "no_unique_natural_cue"
                    else:
                        target = options[0]
                        item["cue"] = cue
                        item["target_kind"] = target["kind"]
                        shortcut = shortcut_reason(
                            cue, ref.group(1), target["caption"], target["kind"]
                        )
                        quality = quality_status(context, target["caption"])
                        if shortcut:
                            item["status"] = shortcut
                        elif quality != "accepted_raw_tex_reference":
                            item["status"] = quality
                        elif not _word_boundary(
                            cue,
                            ref.group(1),
                            target["caption"],
                            cfg["min_unexposed_answer_words"],
                        ):
                            item["status"] = "label_or_cue_exposes_answer_words"
                        else:
                            item["status"] = "source_shape_candidate"
                            item["answer_sha256"] = _sha(target["caption"].encode())
                            proposals.append(item)
            ledger.append(item)
    proposals.sort(
        key=lambda item: (
            -abs(item["reference_span"][0] - item["target_span"][0]),
            item["reference_label"],
            item["cue"],
        )
    )
    accepted = []
    used_labels = set()
    tokenizer = get_tokenizer() if proposals else None
    receipt = ROOT / receipt_text
    for item in proposals:
        if (work["source_group"], item["answer_sha256"]) in prior_answers:
            item["status"] = "prior_exact_answer_overlap"
            continue
        if item["reference_label"] in used_labels:
            item["status"] = "duplicate_target_in_work"
            continue
        if len(accepted) >= cfg["max_tasks_per_work"]:
            item["status"] = "not_materialized_after_cap"
            continue
        try:
            candidate = _compile_link(context, work, item, tokenizer, cfg, receipt)
        except (ValueError, OverflowError) as error:
            item["status"] = str(error)
            continue
        item["status"] = "accepted"
        accepted.append(candidate)
        used_labels.add(item["reference_label"])
    result.update(
        status="source_parsed",
        active_references=sum(row["status"] != "commented_reference" for row in ledger),
        same_file_targets=sum("target_span" in row for row in ledger),
        candidate_tasks=len(accepted),
        reason_counts=dict(sorted(Counter(row["status"] for row in ledger).items())),
    )
    return result, ledger, accepted


def build(config_path: Path, output: Path) -> dict[str, bytes]:
    cfg = _config(config_path)
    relative_output = output.relative_to(ROOT) if output.is_absolute() else output
    if ".." in relative_output.parts or not relative_output.parts:
        raise ValueError("P127 output path must stay under the workspace")
    frozen_v4 = None
    if "frozen_v4_manifest" in cfg:
        frozen_v4 = json.loads(_pin(cfg["frozen_v4_manifest"]).read_text())
        if frozen_v4.get("candidate_views") != 2:
            raise ValueError("P127 frozen v4 reader baseline differs")
    matrix_paths = [_pin(pin) for pin in cfg["work_matrices"]]
    works = [
        json.loads(line)
        for path in matrix_paths
        for line in path.read_text().splitlines()
    ]
    if len({row["work_id"] for row in works}) != len(works):
        raise ValueError("P127 source work repeated across pinned matrices")
    prior_path = _pin(cfg["prior_candidate_refs"])
    prior = [
        json.loads(line)["candidate"] for line in prior_path.read_text().splitlines()
    ]
    prior_tasks = {row["semantic_task_id"] for row in prior}
    prior_answers = {
        (row["source_group"], row["answer_sha256"])
        for row in prior
        if row["source_kind"].startswith("real_paper")
    }
    matrix_by_id = {
        row["work_id"]: cfg["work_matrices"][i]
        for i, path in enumerate(matrix_paths)
        for row in (json.loads(line) for line in path.read_text().splitlines())
    }
    args = [
        (work, cfg, matrix_by_id[work["work_id"]]["path"], prior_answers)
        for work in works
    ]
    with ProcessPoolExecutor(max_workers=cfg["workers"]) as pool:
        processed = list(pool.map(_work_job, args))
    support, rejections, proofs, masks, indices = [], [], [], [], []
    readers = {"train": [], "eval": []}
    ledger = CandidateLedger()
    for work_row, decisions, accepted in processed:
        support.append(work_row)
        rejections.extend(decisions)
        for reader, index, proof, mask in accepted:
            if (
                index["semantic_task_id"] in prior_tasks
                or (index["source_group"], index["answer_sha256"]) in prior_answers
            ):
                raise ValueError("P127 paper task or exact answer overlaps prior bank")
            from longworld.synthesis.unified_candidate_contract import NativeCandidate

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
                source_name="p127_real_paper_same_file_reference",
            )
            readers[split].append(reader)
            indices.append(index)
            proofs.append(proof)
            masks.append(mask)
    files = {
        "candidate_train.jsonl": b"".join(_dump(row) for row in readers["train"]),
        "candidate_eval.jsonl": b"".join(_dump(row) for row in readers["eval"]),
        "sample_index.jsonl": b"".join(_dump(row) for row in indices),
        "proofs.jsonl": b"".join(_dump(row) for row in proofs),
        "audit.jsonl": b"".join(_dump(row) for row in proofs),
        "mask_rows.jsonl": b"".join(_dump(row) for row in masks),
        "support_matrix.jsonl": b"".join(_dump(row) for row in support),
        "decision_ledger.jsonl": b"".join(_dump(row) for row in rejections),
    }
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "p127_schema": SCHEMA + ".result",
        "compiler_sha256": _sha(Path(__file__).read_bytes()),
        "config_sha256": _sha(config_path.read_bytes()),
        "frozen_v4_manifest_sha256": (
            cfg["frozen_v4_manifest"]["sha256"] if frozen_v4 is not None else None
        ),
        "source_matrix_sha256": {
            pin["path"]: pin["sha256"] for pin in cfg["work_matrices"]
        },
        "prior_candidate_refs_sha256": cfg["prior_candidate_refs"]["sha256"],
        "source_works": len(works),
        "source_parsed_works": sum(row["status"] == "source_parsed" for row in support),
        "source_license_statuses": dict(
            sorted(Counter(row["license_status"] for row in support).items())
        ),
        "redistribution_status": "local_research_only_no_redistribution",
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "splits": {split: len(rows) for split, rows in readers.items()},
        "views_by_lane": {"p127_real_paper_same_file_reference": ledger.rows},
        "accepted_worlds": len({row["source_group"] for row in indices}),
        "decision_counts": dict(
            sorted(Counter(row["status"] for row in rejections).items())
        ),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in indices),
        "supervised_tokens": sum(row["supervised_tokens"] for row in indices),
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in indices).items())
        ),
        "files_sha256": {name: _sha(raw) for name, raw in files.items()},
        "claim_limit": "same-file raw-TeX reference and exact target under two visible text deletions; no compiled-PDF, unrestricted semantic-proof, license clearance or model-gain claim",
        "train_ready": False,
    }
    if frozen_v4 is not None:
        for name in (
            "candidate_train.jsonl",
            "candidate_eval.jsonl",
            "mask_rows.jsonl",
        ):
            old = cfg["frozen_v4_manifest"]["path"]
            old_path = ROOT / Path(old).parent / name
            if (
                _sha(old_path.read_bytes()) != frozen_v4["files_sha256"][name]
                or _sha(files[name]) != frozen_v4["files_sha256"][name]
            ):
                raise ValueError(f"P127 v4 final reader or mask bytes changed: {name}")
        if len(indices) != frozen_v4["candidate_views"]:
            raise ValueError("P127 v4 reader inventory changed")
    files["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    return files


def run(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    if verify_only:
        frozen = json.loads((output / "manifest.json").read_text())
        if frozen.get("compiler_sha256") != _sha(Path(__file__).read_bytes()):
            raise ValueError("P127 compiler code changed since source freeze")
    files = build(config_path, output)
    if verify_only:
        if not output.is_dir() or {path.name for path in output.iterdir()} != set(
            files
        ):
            raise ValueError("P127 output inventory changed")
        for name, raw in files.items():
            if (output / name).read_bytes() != raw:
                raise ValueError(f"P127 replay changed: {name}")
    else:
        if output.exists():
            raise ValueError("P127 output must be new")
        output.mkdir(parents=True)
        for name, raw in files.items():
            (output / name).write_bytes(raw)
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.config, args.output, verify_only=args.verify_only), indent=2
        )
    )


if __name__ == "__main__":
    main()
