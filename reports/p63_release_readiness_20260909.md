# P63 new task-bank release readiness — 2026-09-09

**Use a NEW isolated local task bank. Public/production release is blocked; legacy gate counts do not certify reading readiness.** No CURRENT_RELEASE/HF, GPU action, old product rewrite or manufactured approval was performed.

## Current-byte reconciliation

Freshly reread 30 frozen legacy products: **246 train / 14,545,942 recorded exact context tokens; 18 eval / 682,032 tokens**. B5: **185 examples / 12,334,046 estimated tokens**. Gate-bound source and export SHA256s match. HMAC signatures were not revalidated in this audit and old tokenizer counts were not recomputed.

There are **37 world IDs, 40 base-task IDs, 39 semantic-task IDs, 44 answer-program IDs and 71 executable-proof IDs**. These are identifier counts, not certified independent semantic tasks/worlds.

The finite public-codebook / missing-patch-hash / missing-ancestry-ID checks were independently rerun against current rows: **42 train + 9 eval direct counterexamples**. Propagation across the same world+answer-program produces **57 train + 9 eval holds**; every prior hold row hash still matches. The remaining **189 train + 9 eval are unheld, not clean-certified**. Finance-domain holds under these specific detectors are zero, which is not a general finance quality or source-rights certificate.

Old eval world IDs are Wasmtime `code000130-sablewoodkit-44:focal` and Sparks `lab142303127-nacrebench-98:focal`; preserve their source groups, including held eval rows. Finance-topic entity counts also include JPMorgan and Walmart in the company domain; the older seven-issuer finance-domain count is not a whole-corpus finance-source census.

## Executable source/config catalog

Six requested configs now exist as `configs/p63_finance_taskbank_{issuer}_v1.json`. `configs/p63_finance_taskbank_source_catalog_v1.json` lists exact config/source hashes and matching per-issuer private trust-file references (no credentials). Hume confirmed split_group_id is the literal 10-digit CIK.

| Issuer | New split | Filings | Attested text bytes | Plain-text chars estimate | Numeric roles per filing |
|---|---|---:|---:|---:|---|
| amazon | train | 4 | 13,965,502 | 3,681,907 | 57,57,57,57 |
| meta | train | 4 | 13,243,111 | 3,473,805 | 11,11,11,11 |
| micron | train | 4 | 15,921,431 | 3,880,150 | 19,20,20,20 |
| alphabet | train | 4 | 15,619,801 | 3,724,173 | 18,18,18,18 |
| nvidia | train | 4 | 13,221,088 | 3,356,262 | 13,13,13,13 |
| amd | eval | 4 | 9,188,281 | 1,636,740 | 8,8,8,8 |

All selected embedded text hashes and numeric quote offsets match. The byte counts mostly describe HTML, not clean context tokens; plain-text counts are estimates, not exact capacity or proof-bearing text. Repeated source manifests/profiles must not be summed as new source mass. Exact model-context sizes belong to the new compiler/export receipts.

Five development issuers are train; **AMD is eval only in this new isolated batch**, with opposite-split CIK exclusion lists on both sides. AMD is already in legacy train: **do not union legacy train with this eval and call it held out**. The claim is compiler-unseen/cross-provider transfer, not source-new, globally blind or unseen to the base model. Freeze compiler rules and evaluation policy before using AMD outcomes for iteration.

All configs explicitly have `holds: {}`. This is an explicit empty new task-bank hold map, not a claim that the old hold receipt was loaded into new IDs. The finite legacy finance-hold observation comes from `reports/p60_reading_readiness_audit_20260908_v2.json`, independently reproduced here.

Berkshire is available as four frozen PDF/text records (2,209,432 extracted UTF-8 bytes), but its PDF inventory schema and fact structure are **not directly compatible** with the current IR/inline numeric-role compiler. Do not declare it executable blind transfer without reusable extraction support. Q4 providers are registered for five issuers and inline is AMD-specific; changing CIK/host is not a generic new-source adapter. `configs/p63_source_expansion_catalog_v1.json` records this limitation and references the genuine existing Berkshire fetch request rather than inventing one.

## Minimal readiness contract for this new batch

1. **Source authority and rights:** verify original source signatures with their own trust and retain source bytes/parser/normalization. No explicit license fields were found in the selected manifests; public access and source-fetch authorization do not establish public redistribution permission. Record permitted-use evidence before public release; no fake production approval.
2. **Source-group split:** group all years, profiles, tasks and variants by issuer CIK, not profile/world aliases. Bind opposite-group exclusions and the hold receipt before task expansion. Keep the new AMD eval isolated from legacy training.
3. **Real task/evidence:** canonicalize operator, consumed fact IDs, time/year, unit and argument roles. Do not count paraphrases, views or repeated length renders as independent tasks. Validate oracle and source perturbation/removal. Numeric sign/scale/date/unit context must be visible: e.g. AMD signed negative cash facts may quote only unsigned digits, so scalar quote extraction is insufficient.
4. **Honest class and length:** meaningful long-input retrieval and multi-filing integration are allowed. Preserve `strict_long_dependency_verified=false` unless independently certified. Natural contexts of 16,000–131,072 tokens are not exact-band certificates. No padding or retagging short-window tasks as strict.
5. **Export binding:** bind source/config/compiler/holds/splits/tokenizer asset hashes, samples, all referenced context files and evidence-offset maps; verify deterministic export. New `longworld.finance-taskbank-sample.v1` rows are not automatically legacy train-ready ShareGPT-v4 release rows. Use an explicit new local batch gate/manifest or a faithful validated adapter, not label changes.
6. **Package boundary:** legacy package code expects `release_gate_pass.json`, train/eval/quality report and a fixed output allowlist. Existing local products usually have `release_gate_receipt.json`; task-bank contexts and source/lock sidecars need a versioned package contract. Build a new isolated staging tree; do not rename/mutate frozen products.
7. **Production:** both issuable-production and package-ready profile sets are empty. Complete the technical/source-rights package before any actual external approval request. Production is prepare → independently approved exact bindings → finalize; local HMAC and user task authorization cannot substitute for those attestations.

## API guidance

- Compiler interface: `load_finance_world(path)`, `compile_finance_taskbank(world)`, `validate_finance_task(world, task)` in the compiler owner’s `longworld.core.finance_taskbank` module. The owner is landing that module; the config interface was checked against `scripts/materialize_finance_taskbank.py`.
- New exporter config keys: source_manifest, split_group_id, split, min/max_context_tokens, tokenizer `{model_id, revision}`, excluded_split_group_ids and holds. Pin Qwen/Qwen3.5-4B revision `a7b0d22b993d71000cf2eadfb37222a67cee521e`; bind resolved tokenizer assets, not merely the model name.
- Legacy deterministic validation: `longworld.core.training_manifest.validate_training_manifest` and `validate_deterministic_training_transform`, plus `scripts/validate_training_export.py`. Current strict executable transform is ShareGPT v4; a new schema needs explicit support.
- Local package API: `scripts/build_release_package.py --phase build --trust-mode local_engineering`, only for a compatible validated source release. Production uses prepare/finalize/preflight and independent release/package authority; current registries block it.

Machine-readable evidence: `reports/p63_release_readiness_audit_20260909.json`; explicit contract/remedies: `reports/p63_release_readiness_contract_20260909.json`. No new task-bank row was counted by this legacy audit.
