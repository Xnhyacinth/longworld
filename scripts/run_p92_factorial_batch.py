"""Execute or verify explicit native-compiler combinations in bounded processes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis.p92_factorial_batch import (
    _run_job,
    adopt_verified_native,
    audit_baseline,
    audit_new_wiki_mask,
    extract_new_wiki_readers,
    plan,
    run,
    verify,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--compiler-workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--single-job", help=argparse.SUPPRESS)
    parser.add_argument("--baseline-index", type=Path)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--adopt-source-config", type=Path)
    parser.add_argument("--adopt-source-output", type=Path)
    parser.add_argument("--new-only-source", type=Path)
    parser.add_argument("--question-audit", type=Path)
    parser.add_argument("--extra-prior-shard", type=Path, action="append", default=[])
    parser.add_argument("--new-only-output", type=Path)
    parser.add_argument("--audit-new-only-mask", type=Path)
    args = parser.parse_args()
    if not args.new_only_output and bool(args.baseline_index) != bool(
        args.baseline_report
    ):
        parser.error("--baseline-index and --baseline-report must be used together")
    if bool(args.adopt_source_config) != bool(args.adopt_source_output):
        parser.error(
            "both --adopt-source-config and --adopt-source-output are required"
        )
    if args.new_only_output:
        if not (args.new_only_source and args.baseline_index and args.question_audit):
            parser.error(
                "new-only extraction needs source, baseline and question audit"
            )
        result = extract_new_wiki_readers(
            args.new_only_source,
            args.baseline_index,
            args.question_audit,
            args.new_only_output,
            tuple(args.extra_prior_shard),
        )
    elif args.audit_new_only_mask:
        result = audit_new_wiki_mask(args.audit_new_only_mask, args.output)
    elif args.adopt_source_config:
        result = adopt_verified_native(
            args.config, args.output, args.adopt_source_config, args.adopt_source_output
        )
    elif args.baseline_index:
        result = audit_baseline(
            args.config, args.output, args.baseline_index, args.baseline_report
        )
    elif args.single_job:
        frozen = plan(args.config)
        if json.loads((args.output / "plan.json").read_text()) != frozen:
            raise ValueError("single job requested under a changed plan")
        jobs = [job for job in frozen["jobs"] if job["job_id"] == args.single_job]
        if len(jobs) != 1:
            raise ValueError("single job ID is not in the frozen plan")
        result = _run_job(jobs[0], args.output, args.compiler_workers)
    else:
        result = (
            verify(args.config, args.output)
            if args.verify_only
            else run(
                args.config,
                args.output,
                workers=args.workers,
                compiler_workers=args.compiler_workers,
                resume=args.resume,
            )
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
