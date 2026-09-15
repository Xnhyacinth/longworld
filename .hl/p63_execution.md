# P63 world-to-task compiler and batch production

User objective (2026-09-09): apply the supplied review and related work to
continue authentic long-context synthesis/filtering toward training and release
readiness; parallel work authorized. Count source entities, semantic tasks,
proof families and serialized views separately. Keep existing frozen products.

## Plan

1. Complete: generic finance manifest adapter and typed compositional query
   compiler, explicit scope, source provenance, nondegeneracy and semantic IDs.
2. Complete: source-bound long-context batch export with one primary context per
   task, exact pinned tokenizer counts, source-group splits and propagated holds.
3. Complete: verify relevant primary literature and current release blockers.
4. Complete: actual-source arithmetic review and final readiness closeout.
5. Complete: reuse the existing LLaMA-Factory launcher validation/snapshot seam
   for explicit local taskbank input preparation, without GPU training or changes
   to frozen data, legacy strict profiles or production authorization.

## Ownership

- Hume: new finance_taskbank module and its tests.
- Herschel: source catalog and independent old-inventory/release-readiness audit.
- Einstein: primary-paper verification, then independent compiler review.
- Root: batch exporter, integration, final tests and evidence reconciliation.

## Acceptance and limits

One suitable frozen source world should automatically yield dozens of distinct
tasks over several supported query families. Transfer requires manifest/config
only and the same compiler. Correct local retrieval and integration are separate
profiles; no padding, artificial summary deletion, invented causality, or strict
claims based on long input alone. Legacy 57 train / 9 eval holds stay excluded;
their complement is not a certified pool. Production readiness requires actual
source rights/trust and release evidence; local signing is not production trust.

## Next Step

Local delivery is complete. Public release remains blocked on documented source
redistribution rights and production authorization/packaging. Matched training,
unseen-structure evaluation and new compatible source entities remain subsequent
work, not measured outcomes of this batch.

## Implementation checkpoint

- Source catalog: configs/p63_finance_taskbank_source_catalog_v1.json. Five train
  issuers and AMD eval belong to a new isolated batch. AMD is already in legacy
  training, so this is cross-provider compiler reuse, not unseen-source evidence
  against the legacy corpus. Berkshire is source-new to the admitted pool but
  its existing PDF schema is incompatible with this numerical adapter.
- Root implemented taskbank_context.py, materialize_finance_taskbank.py and
  run_finance_taskbank_batch.py. Complete filings are preferred; over-cap inputs
  use the complete set of adapter-supported statement sections, with no partial
  rows, padding or claimed strict-long necessity. Persisted export validation
  replays task semantics and reassembles source contexts independently of hashes.
- Independent review reproduced entity-offset errors, lost word spacing,
  cross-document binding, hidden/cross-cell negative signs, trailing encoded
  parentheses lost by section selection, and NBSP layout inflation. Regression
  tests preceded fixes; root context/export focused checks: 15 passed.
- Primary literature verification: six originals accessible; mechanism/number
  claims and bounded deductions in reports/p63_related_work_compiler_checks_20260909.md.
- No new GPU jobs, external publication, changes to CURRENT_RELEASE, or frozen
  product rewrites. Generic taskbank candidates do not inherit old strict-profile
  or production eligibility. Production packaging still requires its actual
  supported contract and independent source/trust evidence.

## Actual batch result

- Code froze before the first AMD taskbank execution; every pinned runtime file
  still matches reports/p63_pretransfer_freeze_20260909.json. Focused new and
  existing finance tests: 117 passed; all changed/new code checks passed Ruff.
- Six CPU source jobs completed build and independent persisted-export replay:
  data/candidates/p63_finance_taskbank_v1/BATCH_RECEIPT.json.
- 24 existing filings / 216 supported facts / 2,466 draft candidates; 8 missing
  applicability bindings, 2,458 executable proposals, 1,206 program/nondegeneracy
  rejections, 1,252 accepted task instances. No new-source entities were added.
- Train 1,067 / 91,733,476 context tokens; isolated AMD eval 185 / 16,713,551.
  Exact contexts range 59,152–129,233 tokens; 78 unique contexts overlap in source
  content and are not 78 independent source worlds. Eight task families, all full
  variants. Median 205 tasks per source world. No strict dependency claim.
- Fixed capacity policy additionally retains newest complete filing + earlier
  complete financial statements before falling back to all audited statements.
  Actual selections: 424 complete-filings, 800 analyst packets, 28 statement
  packets. No source-summary removal is used as evidence of necessity.
- Source/public-rights bounded check recorded official AMD terms and other
  availability evidence in reports/p63_public_source_rights_check_20260909.md.
  Public redistribution permission and production authority remain unverified;
  do not promote local candidates by relabeling them.

## Local training handoff complete

- Dedicated new taskbank_training module/prepare CLI, TASKBANK LF recipe and
  explicit launcher branch reuse source revalidation and immutable snapshots.
  Legacy schemas, strict profiles and candidate receipts were not relabeled.
- First private preparation failed before signing because simultaneous first
  AutoTokenizer construction raced in lazy imports. A regression reproduced it;
  initialization is serialized, counting remains bounded at eight workers.
  Failed v1 partial JSONL files removed after diagnostic hashes; logs retained.
- Valid preparation:
  /workspace/wynckeliao/.longworld-taskbank-training-private/p63_v2/TASKBANK_TRAINING_MANIFEST.json.
  1,067 train /185 eval, max full-chat tokens129,654; local_training_eligible=true,
  production_eligible=false, framework_preprocessing_verified=false.
- Actual launcher --prepare-only returned0 with nonexistent LF_ROOT and reused
  its readonly snapshot. Root independently checked signature/bindings and all
  snapshot bytes/modes. No model/GPU started. Snapshot directory0500/files0400.
- Final new/legacy training/export regression group149passed; new runtime/tests
  Ruff clean, launcher bash syntax/diff checks clean. Original finance group117
  passed. Actual source numeric sample48/48 passed; no sampled new holds.
- Final reports: reports/p63_taskbank_closeout_20260909.md and
  reports/p63_local_training_preparation_20260909.md. Public source rights check
  supplies concrete official policy evidence but no fabricated permission.
