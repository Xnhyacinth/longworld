# Final P64 dependency audit

The verified finance batch contains **1,682 canonical tasks / 104 contexts**, and the admitted CodeForge long stage contains **804 canonical tasks / 194 contexts**. These inventories are distinct from proof of strict long dependency. No task was certified strict by this audit, and no neural baseline was run.

## Finance inventory and exact capacity

All eight finance jobs have `verified_local_candidates` receipts, with matching receipt hashes. The batch uses 32 source documents and 280 consumed source facts across eight issuer groups. Train/eval contain 1,296/386 rows across six/two issuer groups, with no issuer overlap inside this isolated batch.

| Finance context capacity ceiling | Rows | Rows actually inside narrow numeric range |
| --- | ---: | ---: |
| 65,536 | 52 | 13 in [64,000, 65,536] |
| 131,072 | 1,046 | 0 in [128,000, 131,072] |
| 262,144 | 584 | 47 in [256,000, 262,144] |

Context lengths range from 59,152 to 259,729 tokens. These are context counts; complete messages also require question/chat/answer accounting. Capacity ceilings are not exact-band certificates.

All **1,252 P63 canonical IDs are retained**. The 430 additional canonical tasks comprise 376 base-family tasks from the additional sources and 54 dependent-family instances: 24 conditional cross-metric sums, 24 maximum-margin-selected cashflow tasks, and six margin-extremes cashflow differences. Context changes do not count as additional canonical tasks.

The six margin-extremes differences occur only in eval. Their operators `select_margin_extremes` and `difference_extremes` are also absent from train. This is **program-family plus primitive-operator holdout**, not pure composition generalization over known primitives. With fact/record IDs abstracted but arity, roles, relations and operator constants preserved, train/eval have 30/27 AST shapes and two eval-only shapes. The source-held-out and operator-held-out axes overlap for these six cases; no model transfer performance is established.

## Finance evidence geometry and alternatives

| All bound numeric spans fit some raw token window | Rows |
| --- | ---: |
| 4,096 | 455 |
| 8,192 | 617 |
| 16,384 | 649 |

Complete visible lines containing those spans give the same counts. Triage yields 280 retrieval, 369 integration and 1,033 strict candidates. Strict candidates are simply multi-record tasks with a bound-span envelope exceeding 16k; **strict verified remains zero**. Visible lines may omit table/year/unit context, and longer bound envelopes can have shorter alternative proofs.

The same 1,252 P63 canonical tasks illustrate why geometry cannot measure new difficulty. Their 4k coverage stays 352; 8k coverage changes 556→513; 16k coverage changes 821→545. Exactly **276 old tasks become strict candidates solely through the new context layout**. Their task identities remain unchanged. The paired accounting is saved in `p64_finance_paired_context_geometry.json`.

Every finance bound numeric leaf passed quote, magnitude and visible-sign checks. All 280 lookup answers replayed using oracle locations and bound period/unit metadata. This is a consistency check, not an independent reader.

The latest included filing contains every exact numeric surface for 1,208 rows, including 752 multi-filing rows. Among strict candidates, 592 have this repeat opportunity. These are gold-assisted search results: the alternative metric/year/scale and exact-filing reporting basis are **unverified**. After excluding a bound leaf, remaining numeric repetitions are counted, but semantic answer preservation after source deletion is unknown. Question-only deterministic success is unknown; neural question-only/window/full-context performance is unmeasured.

## CodeForge visible-source audit

The audit reconstructed each context from its source world and mapped all bound source records to complete visible `R`-label records, checking text, kind, source pointer, exact context hash and tokenizer count. The full bank has 818 tasks / 197 used contexts. Intersecting the final SFT metadata admits 804 long tasks / 194 contexts, with 657 train and 147 eval; 13 short rows and one overflow row are excluded from that long inventory.

CodeForge full-message capacity ceilings are 141/305/358 rows at 65,536/131,072/262,144. Exact **context** numeric ranges contain 12/9/16 rows respectively. These two quantities use different token scopes and must not be added or compared as if identical.

No complete conservative bound-record set plus the scope map fits a 4k/8k/16k raw window. This **does not establish necessity**: the shared evaluator over-collects evidence, including CI records for file-only queries, and full records contain unused patch content. Oracle-located complete merged-head records fit 4k/8k/16k for 3/3/46 admitted rows; this also omits the scope and graph-selection proof. These diagnostics neither prove short-window solvability nor certify long dependency. No finance numeric reader was applied to CodeForge, and no independent neural baseline or minimal alternative-proof search was performed.

## Actionable use and evidence

Keep retrieval/integration data available under those labels. Keep all strict flags false until program-specific semantic alternatives and source-deletion effects are tested. Prioritize the 592 repeat-positive finance strict candidates for comparative-table and reporting-basis review. For CodeForge, derive program-specific relevant fields and graph links before interpreting support length; neither the conservative evidence union nor the entire commit patch is a minimal proof.

Machine artifacts:

- `p64_finance_dependency_final/summary.json` and `samples.jsonl`: all 1,682 finance rows, exact support positions, hashes and opportunity diagnostics.
- `p64_codeforge_dependency_final/summary.json` and `samples.jsonl`: all 818 bank rows; full record mapping and window diagnostics.
- `p64_dependency_inventory_final.json`: finance identity/split/AST counts and the CodeForge 804-row primary intersection, bound to SFT metadata.
- `p64_finance_paired_context_geometry.json`: unchanged-task geometry comparison, bound to both audit JSONLs.

The finance audit carries source bindings from independently validated batch rows; it does not reissue source attestations. The CodeForge audit binds world/task/context/bank receipt files and reconstructs visibility; it does not replace source verification or establish publication rights.

Verification: final exporter adversarial regressions and dependency tests passed **18/18**. The earlier receipt metadata gap is fixed: false split, world identity, tokenizer revision or headroom claims now reject. All final audit processes exited successfully. No GPU or model inference was used.
