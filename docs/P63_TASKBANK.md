# P63 finance task bank

This pipeline compiles multiple numerical tasks from each frozen issuer source, then verifies their persisted contexts and answers. Its output is **local candidate data**, not a legacy B5/v4 training release. Final counts must come from the completed batch receipts; this document does not prescribe or infer them.

## Build and verify

Run from the owning checkout with its real `.venv` and `uv.lock`. The source manifests, original source trust files referenced by the catalog, and pinned tokenizer assets must already be available. The runner supplies each issuer's source credentials through the existing local-probe wrapper; it does not create or replace those credentials.

```bash
cd /workspace/wynckeliao/longworld-worlds
P63_OUTPUT=/workspace/wynckeliao/longworld-worlds/data/taskbanks/p63_user_run_v1
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  uv run python scripts/run_finance_taskbank_batch.py \
  --catalog configs/p63_finance_taskbank_source_catalog_v1.json \
  --output "$P63_OUTPUT" \
  --workers 6
```

Choose an output directory that does not exist. The runner accepts 1–16 workers, checks catalog/config/source hashes and duplicate issuer groups, then runs **build followed by `--validate` for every issuer**. It records per-issuer logs and `BATCH_RECEIPT.json`; any blocked job makes the command exit nonzero. A completed subprocess or nonempty directory alone is not a passing batch.

The batch already performs per-issuer validation. For an independent recheck, keep `P63_OUTPUT` set to the completed batch and use that issuer's catalog config and trust file. For example:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  uv run python scripts/run_with_local_probe_trust.py \
  --trust-file /workspace/wynckeliao/.longworld-scaleout-20260906-private/amazon/local_probe_trust.json \
  --role source \
  --pass-env HF_HUB_OFFLINE --pass-env TRANSFORMERS_OFFLINE \
  --pass-env TOKENIZERS_PARALLELISM \
  -- .venv/bin/python scripts/materialize_finance_taskbank.py \
  --config configs/p63_finance_taskbank_amazon_v1.json \
  --output "$P63_OUTPUT/amazon" \
  --validate
```

Omit `--validate` only to build a **new** per-issuer output directory. Changing a source, compiler, tokenizer, config, split or hold policy requires a new run and matching catalog hashes. Do not patch an old receipt to make validation pass.

Each issuer directory contains:

- `tasks.jsonl`: typed task specifications, five identities, source/evidence bindings, context references and readiness flags.
- `sft_candidates.jsonl`: `{sample_id, messages}` records with user context/question and the canonical JSON assistant answer.
- `contexts/<sha256>.txt`: shared immutable context text, avoiding duplicate on-disk copies across tasks.
- `rejections.json`: compiler, applicability, hold, surface and natural-length failures.
- `BUILD_RECEIPT.json`: source/config/code/tokenizer and file hashes, accepted-task/view counts, context counts, families, exposures and unresolved work.

`--validate` reloads the signed sources, checks the exact member inventory and hashes, rejects changed compiler/config/tokenizer bindings, replays tasks, rebuilds contexts, checks visible evidence and signs, recomputes token counts, and compares the messages export. It is more than a row-count check. Inspect `BATCH_RECEIPT.json` and each issuer's validation log before using that output.

## Identity and counting

| Identity | Current meaning | Counting/splitting rule |
|---|---|---|
| `source_collection_id` | Hash of sorted source record IDs and document hashes | A new question or profile is not a new source collection. |
| `world_instance_id` | Issuer CIK bound to that source collection; `world_id` is its alias | A source snapshot, not an independent task. |
| `semantic_task_id` | Compiler revision, canonical program and declared scope | Count canonical tasks; do not infer novelty from wording alone. |
| `variant_family_id` | `variants:` plus the semantic task ID | Related views/perturbations must stay together. |
| `sample_id` | Semantic ID, exact context hash, question and answer | Identifies a serialized sample, not a new world. |

The separate `split_group_id` is the original **10-digit issuer CIK**. Changing manifests, source windows or world aliases does not move an issuer into another split. Receipts additionally bind tokenizer and split metadata; a sample ID alone is not that complete contract.

Current exports contain only `variant="full"`. Do not describe compiler sensitivity checks as exported CF views. Count source entities, snapshots, semantic tasks, training views and unique contexts separately. Reusing a context for different tasks increases training exposure, not unique source text.

## Natural contexts and difficulty

The configs admit naturally assembled contexts between **16,000 and 131,072 exact tokenizer tokens**. Assembly first tries complete required filings and, when too large, uses complete audited statement packets. It rejects contexts outside the configured interval; it does not fill short inputs with unrelated text.

`natural_length_cap` is the next capacity bin among 16,384/32,768/65,536/131,072. It is **not** an exact-band certificate: a context assigned a 65,536 cap need not reach the strict 64,000 lower bound. The exporter explicitly writes:

```text
exact_band_certificate = false
strict_long_dependency_verified = false
alternative_proof_search_complete = false
```

One-document tasks are labelled `long_input_retrieval`; multiple-document tasks are `multi_filing_integration_candidate`. Document count is not proof that every document is necessary. Short-window or retrieval solvability must be measured separately before claiming strict long dependency. Keep the strict subset and its unchanged tests separate from useful retrieval/integration data.

The recorded `context_tokens` count covers the context, not the added question, answer and model chat template. Check the complete tokenized messages against the training model's sequence limit. If necessary, rebuild with appropriate context headroom; do not silently truncate bound evidence.

## Split and source exposure

The supplied catalog assigns Amazon, Meta, Micron, Alphabet and NVIDIA to train, and AMD to eval. Each train config excludes AMD's CIK; AMD excludes all five train CIKs. `holds: {}` is explicit for this new bank. It does not mean a general cleanliness certificate or that legacy hold IDs were loaded into these new task IDs.

**This is an isolated batch. AMD already occurs in legacy training products. Do not combine legacy train with this AMD eval and call it source-held-out.** AMD is a cross-provider/compiler-transfer issuer for this experiment, not a newly acquired source or proof of absence from model pretraining. The frozen financial sources are reused; their source rights and original provenance still apply.

Preserve older eval source groups as well. Wasmtime and Sparks belong to the historical eval inventory even where a legacy task has a reading hold. A hold is not permission to silently recycle that source into training. Use the current hold/split audit rather than assuming unflagged rows are clean.

## Matched training/evaluation plan — not executed

The narrow causal question is whether **multiple semantic tasks from the same source/context** improve transfer beyond repeatedly training on one task, at equal exposure and compute. Do not compare a small single-task dataset against a larger multi-task run with more optimizer work and attribute the difference solely to task diversity.

Use these planned arms:

| Arm | Training data | Purpose |
|---|---|---|
| Base | Same frozen checkpoint, no updates | Establish pretraining/closed-book starting performance. |
| Single | One canonical task per eligible source/context stratum, repeated | Control source and context exposure. |
| Multi | Multiple distinct semantic tasks from those same strata | Test the task-diversity intervention. |

Before any training, create a paired schedule from **train data only**. For each world choose a fixed validated context packet and a stratum of tasks with the same full chat-template question-prefix token count and assistant-target token count. Require at least two distinct semantic tasks in that stratum. Single repeats one preselected task; Multi rotates the alternatives. Use the same issuer/context schedule, occurrence counts, batch shapes, accumulation, steps and attention backend. This makes real token counts, supervised token counts and executed sequence shapes match without editing questions or source content. Do not count ordinary masked batching padding as source tokens.

Select one stratum per eligible world deterministically with paired seeds `0,1,2`, balanced over available families; preselect the Single task without eval outcomes. Use up to eight alternatives where the common support permits, and freeze the actual K and token/step budget in a run manifest. If matching support is insufficient, report the design as infeasible for that cohort instead of padding text or claiming approximate budgets are exact. Results from this matched cohort do not automatically establish performance for the entire bank.

Freeze the base checkpoint/revision, model-compatible chat template, optimizer, precision, trainable parameters/adapters, learning-rate schedule, batch geometry and stopping rule before evaluation. Model/trainer availability and the concrete budget remain launch prerequisites, not fabricated settings in this document. Record nonpadding input/target tokens, allocated tokens, optimizer steps, accelerator time and measured throughput for each arm. Any mismatch must be reported; equal row counts or equal epochs are insufficient.

Keep evaluation axes separate:

| Evaluation | What is unseen | Availability/interpretation |
|---|---|---|
| Issuer transfer | AMD CIK relative to the new five-issuer train split | Available only within the isolated bank. One eval issuer supports a case study, not a population-wide source-generalization claim. |
| Snapshot/instance transfer | A genuinely different, nonoverlapping source snapshot | Requires a separate future split with source/vintage/near-duplicate audit. Do not manufacture it by changing IDs over the same documents. Current catalog does not establish this axis. |
| Program-composition transfer | A canonical operator composition held out of training | For example, hold out `aggregate(sum, filter(collect(...)))` while retaining valid filter and aggregate primitives. Verify eligible tasks exist; retain all related task variants in the held-out cell. |
| Joint transfer | Held-out issuer plus held-out composition | Report separately from either single-axis result. |

For the composition test, use program structure/argument-role signatures, not literal question hashes. A new metric label or a new fact ID alone does not establish a new program. Known-source composition evaluation may intentionally reuse source facts; label that weaker setting explicitly. AMD outcomes must not tune compiler rules, task selection or model hyperparameters.

Use the same deterministic decoding policy and a frozen, value-free output-schema instruction for all arms; do not give gold values or answer-dependent array lengths. This separates source reasoning from accidental differences in format familiarity. Primary scoring is exact structured-answer match after JSON parsing, with the compiler's units, periods, tie handling and rounding contract. Also report family-macro accuracy, invalid-output rate and component errors (sign, unit, period, operands and ties). Compare paired arms on exactly the same tasks. Report seed variation separately from source-level uncertainty; repeated tasks/views from one issuer are not independent source samples. Include question-only and source-evidence-only diagnostics to measure memorization and retrieval difficulty, without labelling their results before running them.

No GPU run, model score, improvement, significance or final task count is supplied here. The machine-readable plan is `reports/p63_taskbank_training_plan_20260909.json`.

## Remaining release integration

`BATCH_RECEIPT.json` and `BUILD_RECEIPT.json` retain `production_eligible=false` and `training_release_eligible=false`. `sft_candidates.jsonl` is a messages-format local candidate input; it is **not** the old ShareGPT B5 file, a signed training export manifest, or a production-ready release.

Before a training release, implement/register a compatible export contract that binds sample/context/source/split/hold/tokenizer/compiler identities and validates the deterministic messages transform. An explicit adapter to the existing B5/v4 contract is an alternative only if all its source-row and release checks actually pass. Do not change schema labels or set train-ready flags to bypass missing checks.

The legacy package path expects `release_gate_pass.json`, train/eval/quality files and an allowlisted training export. Current task-bank contexts/source/evidence sidecars need an explicit versioned package contract; existing local products often use `release_gate_receipt.json`. A new staging tree may resolve naming/layout compatibility while preserving old bytes, but renaming alone does not supply a complete replay package. Bind relocatable source/context/evidence sidecars, the transitive compiler/normalizer version and runtime dependency lock; the three direct code hashes in a build receipt are not a complete portable runtime lock.

Selected source manifests do not record explicit redistribution licenses. Public fetching authorization and local HMAC verification do not supply missing source rights or independent production/KMS approval. At this checkpoint, production-issuable and production-package-ready profile sets are empty. Prepare the concrete technical package and rights evidence first; production approval must bind the real staged artifacts and come from the required independent authority. Do not manufacture it from a user task authorization.

See `reports/p63_release_readiness_20260909.md` and its audit/contract JSON for the measured legacy holds, source inventory and API boundaries. Final batch counts should be inserted only from a fully validated run's receipts.
