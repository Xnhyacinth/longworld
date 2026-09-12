# P65 GovInfo printed-version task bank

The final source-replayed bank contains **28 canonical program/scope tasks, seven long inputs and 21 short inputs**, from **one existing bill chain**. Two existing chains were probed; no new source entity is counted. There are eight source scopes/contexts, seven comparison programs and 21 distinct structured answers. These are related task instances, not 28 independent worlds or proofs.

The usable source is H.R. 4366's [EAS print, 2023-11-01](https://www.govinfo.gov/content/pkg/BILLS-118hr4366eas/xml/BILLS-118hr4366eas.xml) and [EAH print, 2024-03-06](https://www.govinfo.gov/content/pkg/BILLS-118hr4366eah/xml/BILLS-118hr4366eah.xml). Each build and validation re-fetches the two frozen XML texts plus the official bill-status XML, verifies exact sizes/hashes, source identity, printed dates, the existing action/version relations and the embedded public-domain statement, then regenerates the stored normalized whole-section source. Original XML remains in memory. This uses the existing P49 validators and immutable source pins.

GovInfo notes that government works are generally public domain while third-party material in government publications can retain copyright; the selected XML files carry their own explicit public-domain statement. No public release was performed. [GovInfo policy](https://www.govinfo.gov/about/policies#copyright)

## What the tasks ask

The programs compare printed dollar figures and literal exception language at exact structural locations. They report changed figures, rank absolute changes, detect introduced/removed exception markers, or report/rank added and removed monetary locations. Outputs contain actual decimal amounts, differences and exact normalized source excerpts—not R/M/U codes or hashes. Matching a structural location does not assert that renumbered provisions are identical or that a print became law.

Ambiguous locations are excluded at both endpoints. A monetary location retains its other-print counterpart even when that counterpart has no dollar sign, preventing an existing nonmonetary section from being falsely labelled added. Multiple-dollar and scaled-dollar sections cannot enter the single-figure answer grammar. Identical whole section bodies are serialized once with all their authentic printed memberships.

The context population includes only locations with a dollar sign in at least one print and their counterpart sections. The full two-print section pool was about 187k tokens, but the relevant whole-body population was only 57,739 tokens (55,492 after exact body deduplication). The unrelated remainder was not used to claim larger contexts.

## Actual capacity and split

All seven long tasks use the same **59,765-token** normalized context. They fit a **65,536-token context ceiling**, but none reaches the narrow exact 64k range [64,000, 65,536]. There are no 128k or 256k source-context products.

Full chat messages are counted with the existing `scripts.train_sft.tokenize_assistant_only` helper under a credential-free environment; the helper verifies a complete assistant boundary and does not truncate. Six long examples fit 65,536 full-message tokens; the added-section-list example uses **71,006**, including 10,978 supervised answer tokens, and fits the 131,072 full-message ceiling. Do not confuse that full-message ceiling with a 128k source context. All outputs stay below the 262,144 model cap.

All 28 tasks are train-side local candidates for `govinfo:118-HR-4366`. There is no GovInfo held-out-source evaluation in this bank. The seven long examples are in `sft_candidates.jsonl`; the 21 short examples are in `short_sft_candidates.jsonl` and do not count toward long-input inventory.

H.R. 815 remains a bounded negative result: only six shared unambiguous locations, zero paired single-dollar cases and a 12,722-token monetary population. Its source/grammar diagnostic was preserved; no rows or larger bands were manufactured.

## Dependency diagnostics

| Complete-record window answer EM | All 28 tasks | Seven long tasks |
| --- | ---: | ---: |
| 4,096 raw tokens | 11 | 2 |
| 8,192 raw tokens | 19 | 2 |
| 16,384 raw tokens | 25 | 4 |

The source-record availability sweep covers every distinct set of complete serialized records admitted by a contiguous raw-token window. It runs the finite comparison program on those records and compares structured outputs. It is not a neural reader, an exhaustive search over all semantic alternatives, or proof of complete query-population scope. In particular, missing records can make an absent-counterpart inference unjustified even when the answer happens to match.

Nine tasks also match with the latest print alone; zero match with the earliest print alone or an empty-context constant answer. These are deterministic probe outcomes, not question-only model scores. The final labels are **25 retrieval candidates and three integration candidates**. Among the seven long tasks, four are retrieval candidates and three are integration candidates. **Strict verified: zero.** The absence of a complete-record 16k witness does not prove genuine long dependency.

## Reproduction and delivery

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  uv run python scripts/materialize_p65_govinfo_taskbank.py \
  --config configs/p65_govinfo_taskbank_hr4366_v1.json \
  --output data/candidates/p65_govinfo_taskbank_v2 --validate
```

Omit `--validate` only with a new output directory. Validation live-replays the official source and exactly regenerates every task, question, answer, context, SFT row, window diagnostic and receipt; missing/extra files, self-rehashed task changes and false receipt claims reject.

Final artifacts:

- `data/candidates/p65_govinfo_taskbank_v2/`: final bank and `BUILD_RECEIPT.json`.
- `configs/p65_govinfo_taskbank_hr4366_v1.json`: source/config/tokenizer pins and declared scopes.
- `data/source_inventory/p65_govinfo_derivatives_v1/`: normalized source derivatives for the two bounded probes.
- `reports/p65_govinfo_closeout.json`: actual identity/capacity/profile/baseline accounting.
- `reports/p65_govinfo_taskbank_v2_validation.json`: successful final live-source and artifact replay.

Source verification is explicit official hash-pinned replay, **not** a newly issued HMAC or production signature. No private trust file is required by this CLI. Shared downstream training/release integration remains separate; production and strict flags stay false.

An initial exporter bug counted two keys of a tokenizer `BatchEncoding` as two tokens. A failing regression reproduced it before replacement with the existing training helper. The invalid v1 receipt, compact row evidence and logs are preserved in `reports/p65_govinfo_v1_token_count_bug/`; its unused unsigned task/SFT/context payloads were removed after v2 validated. Final verification: **12 focused tests passed**, Ruff passed, and full live-source/artifact replay passed. P63/P64 code, source snapshots and products were not edited.
