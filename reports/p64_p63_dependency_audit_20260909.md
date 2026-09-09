# P64 audit of the frozen P63 bank

The complete run audited **1,252 rows / 1,252 canonical tasks / 78 unique contexts** using four CPU processes. No source, P63 compiler, task row, or context was edited. Final machine evidence is `reports/p64_p63_dependency_audit_v2/summary.json` and `samples.jsonl`; v1 is the retained initial pass and is superseded by v2's tokenizer-asset binding and numeric magnitude/lookup replay.

| Measurement | Rows |
| --- | ---: |
| All bound numeric spans fit some contiguous 4,096-token window | 352 |
| All bound numeric spans fit some contiguous 8,192-token window | 556 |
| All bound numeric spans fit some contiguous 16,384-token window | 821 |
| Retrieval triage | 216 |
| Integration triage | 605 |
| Strict-candidate triage, pending alternative proofs | 431 |
| Strict long dependency verified | 0 |

Complete visible lines containing each bound number give the same 352/556/821 coverage counts. Windows are exhaustive possible starts in the exact Qwen tokenizer stream, with special tokens disabled, rather than nonoverlapping chunk boundaries. Coordinates describe the original context token stream; independently retokenizing a cropped string can change boundary tokens. The pinned revision is `a7b0d22b993d71000cf2eadfb37222a67cee521e`, with asset-manifest SHA-256 in the machine summary.

These are **support geometry measurements**. A whole visible line may omit the table's metric label, year header, scale, footnote, or reporting basis. Conversely, scattered bound leaves may have a shorter alternative proof. Neither result establishes model success or failure. Strict-candidate means multiple bound source records and a bound-span envelope exceeding 16k; it is not strict admission.

All 216 lookup cases fit 4k, as do 64 aggregate cases, 24 cash reconciliation cases, and all 48 ratio cases. All visible leaf strings, signs and integer magnitudes were replayed. The 216 lookup answers also matched their visible values plus bound period/unit metadata. This is an **oracle-located replay**, not an independent retrieval baseline: the saved evidence locations and period/unit metadata were supplied.

The latest included filing contains every exact bound numeric surface for **1,023 rows**, including **671 multi-filing rows**. Of the 431 strict candidates, **296** have this numeric-repeat opportunity. Searches exclude digit/comma/decimal substrings; per-fact alternative positions are recorded. These are gold-assisted numeric matches, not confirmed comparative-table proofs: coincident numbers can represent different metrics, years or scales. Moreover, P63 questions explicitly require amounts as reported in the specified filings, so a later comparative table does not automatically satisfy the original reporting-basis contract.

Source-deletion diagnostics currently exclude each bound numeric occurrence and count remaining identical surfaces. Full semantic execution after deleting a filing or evidence region is **unknown**. No complete source-row/year/metric parser or alternative-program search is claimed. Question-only deterministic success is **unknown** and neural question-only/full-context/window baselines are **unmeasured**.

For generation filtering, call `audit_taskbank_sample(row, context, tokenizer=...)`, or supply `token_offsets` computed once with `exact_offsets(context, tokenizer)` for a shared context. Preserve `profile` separately from `strict_long_dependency_verified`; the latter remains false. Route the 216 lookup rows to retrieval, the remaining short-support cases to integration diagnostics, and prioritize the 296 repeat-positive strict candidates for table/year/basis review. Do not reject useful retrieval data merely because it fails a strict-dependency claim. New 64/128/256k views must retain the same semantic task identity and receive their own context hash and audit.

The summary binds each tasks file, exact context hashes, tokenizer assets, audit implementation and output JSONL. Source-document/manifest identities are carried from the already-validated task rows; this audit does **not** independently re-verify their HMACs or fetch original sources. Use the batch's existing source-validation receipts for that prerequisite.

Verification: six focused tests passed, including all-window sweep versus exhaustive enumeration, tamper rejection, numeric-magnitude rejection, and non-promotion of numeric repeats or large geometry into strict proof. Ruff passed. Command:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  uv run python scripts/audit_taskbank_dependencies.py \
  --batch data/candidates/p63_finance_taskbank_v1 \
  --output reports/p64_p63_dependency_audit_v2 --workers 4
```

The output argument must be a new directory for reproduction; existing audit evidence is never overwritten.
