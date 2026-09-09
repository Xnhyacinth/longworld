# P59 known-question-only guard independent review

Initial review, 2026-09-08. Read-only shared-code inspection; all reproductions
used `tempfile.TemporaryDirectory` under `/tmp`. No actual pipeline filter,
audit, release signature or watch process was changed by the reviewer.

The existing `tests/test_p57_task_pipeline.py` suite passed **16 tests** before
the following missing cases were reproduced. This is not a full-suite result.

## Required corrections reproduced

1. **Missing candidates bypass the guard on cached green resume.** Create an
   `audits.jsonl` with green gates and a matching green `FILTER_RECEIPT.json`,
   but no `candidates.jsonl`. `run_pipeline(execute=True, resume=True)` returns
   `resumed`. The old receipt remains `strict_eligible=true`. The detector
   treated absent input as no detected shortcut, and resume bound only audits.

2. **Malformed question type also bypasses cached green resume.** With the same
   setup, add a primary-band candidate whose `question` is a dictionary.
   `str(row.get("question") or "")` hides the invalid input type; the result is
   again `resumed` with the old green receipt intact.

3. **No primary-band candidate can still produce a primary job's green filter.**
   Set `primary_buckets=["64k"]`, but provide only a 16K candidate and green
   audit. The candidate can even contain the known singleton-codebook shortcut.
   The guard skips the side band; `_classify_existing_audits` classifies every
   audit without primary scope. With `resume=False`, the result is `classified`,
   `strict_eligible=true`, `query_ids=["leaky-16k"]`, despite zero 64K candidates.

4. **A failed job can leave its previous positive filter untouched.** The outer
   error-isolation handler writes a failed ledger entry but does not invalidate
   an existing green filter. The malformed-input test originally covered only
   a directory without a prior filter. Failure should leave an unambiguously
   non-green current filter without rewriting historical audits/releases.

5. **The singleton detector accepts undeclared alternative-bearing shapes.**
   A field value containing both `code="VALID"` and
   `alternatives=[{"code":"VALID"},{"code":"INVALID"}]` still predicts
   `VALID`. Limit applicability to the known singleton schema so a future
   multi-option codebook is not mislabeled as this known deterministic leak.

## Correct behavior already observed

- A well-formed matching primary candidate is checked before old receipt resume.
- Newly projected matching candidates stop before ranking.
- The known codebook prediction is computed from the question only; answer
  fields are used for equality scoring afterward.
- A differing CF answer is not listed as solved by this baseline.
- Malformed JSON is isolated to its job; another well-formed job still runs.
- The ordinary blocked-shortcut path preserves existing audit bytes.

## Follow-up verification requested

Root is correcting candidate inspectability, primary-query coverage and audit
scope; binding resume to candidate bytes, primary bands and detector revision;
writing non-green failure receipts; and narrowing the singleton schema. Recheck
the five cases above with old green receipts pre-populated, plus preservation of
audit/release bytes and continuation of an independent good job.

This detector is a known singleton-codebook baseline. Absence of a match is not
evidence that a task resists a general question-only model.

## Follow-up review after the first fix

The expanded pipeline tests passed **25 tests**. Additional independent `/tmp`
checks confirmed that a mixed 16K/64K cache classifies only its requested primary
query; changing primary scope prevents resume; changing candidate bytes with an
old audit fails closed; and those paths preserve audit bytes. The original
missing/malformed candidate and singleton-shape corrections are present.

Two receipt-write boundaries remain reproducible in that reviewed revision:

- If `FILTER_RECEIPT.json` is a symlink to `audits.jsonl`, the shortcut block
  follows the symlink through `write_bytes` and overwrites the audit. Reject an
  unsafe receipt path or use a safe replacement that does not follow its target.
- If `FILTER_RECEIPT.json` is a directory, classification raises an
  `IsADirectoryError`; the outer failed-job handler tries to write the same path
  again and lets the error escape `run_pipeline`. A receipt-write failure must
  remain a failed job with recorded error, rather than aborting result collection
  for the whole batch.

Both were reproduced only under temporary directories; no real audit or receipt
was changed by this review.

## Final receipt-write recheck

The shared writer now rejects symlinks and non-regular destinations and uses an
fsynced temporary file with atomic replacement. Failed-job receipt-write errors
are recorded as `filter_receipt_error`, without escaping result collection.

Four independent temporary cases (symlink/directory × leaky/non-leaky candidate)
now pass: the bad job is failed, the independent good job is classified, both
results return, every audit byte remains unchanged, and no temporary receipt
file remains. No blocking receipt-write issue remains in this reviewed scope.
The concurrent focused pipeline/CodeForge test invocation completed with
**80 passed, 1 xfailed**; this is not a full-suite or release-gate claim.
