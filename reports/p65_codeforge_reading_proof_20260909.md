# P65 CodeForge: scoped filename-copy proof

**130 existing tasks qualify for the content-backed subset: 96 train and 34 eval.** They are a subset of 179 raw-stream scoped certificates; the remaining 49 pass the raw-stream rule but not the extra content-span control. Build and full replay validation both passed in the P65 wave-2 ledger.

This qualifies existing P64 tasks under a new, explicitly bounded profile. It adds **zero source worlds, zero semantic tasks, and zero counterfactual views**. P64 source banks, messages, IDs, split membership, and renderings remain unchanged. The new export preserves one primary sample per selected canonical task.

## What is certified

The raw profile is `p65-codeforge-filename-copy-aggregation-v1`. Its reader receives the whole question for free and one arbitrary contiguous 4,096-, 8,192-, or 16,384-token window from the exact full-context tokenizer stream. It may select any gold output filename when any allowed alias is present. It is deliberately granted oracle resolution of ambiguous basenames and arbitrary subset selection.

The complete finite alias set includes full path, basename, slash/backslash variants, JSON escaping, URL encoding/decoding, and NFC/NFD forms. Matching is ASCII-case-insensitive and permits substrings and overlaps. Question aliases, including the fixed `Source records:` prefix, are free and never forced into a source window. The grammar does not model filename synthesis, pretrained memorization, semantic inference, or several disjoint retrieval windows.

The certificate establishes that this generous literal-copy reader cannot reproduce all required filename strings from any single tested window. It does **not** establish universal model failure, deep causal reasoning, or canonical strict-production eligibility. Short-window checks operate on answer-string occurrences and exact token coordinates; they do not require a cropped window to parse as complete JSON.

The full visible reader separately resolves the supplied PR, merge, head and review links and aggregates `diff --` filename headers. It agrees with each certified task's frozen oracle using only reader-visible fields. Every active merged-head input changes the aggregation result when symbolically omitted. Approval-qualified tasks must also differ from the unfiltered union; no-effect approval cases stay outside the certificate subset.

## Exact coverage calculation

All alias occurrences are enumerated with an overlapping Aho–Corasick search. For each filename, its alternative occurrence intervals are OR choices. The minimum covering interval is computed by sweeping all occurrence right endpoints: at each endpoint, the greatest available start for each label dominates its earlier alternatives. The minimum of those starts is the latest feasible window start. This yields the exact minimum interval over all choices.

A second sweep independently evaluates every integer start of each fixed-width window. Each filename contributes the union of starts that fully contain at least one of its occurrences, so repeated aliases cannot count as several labels. The minimum-cover and fixed-window results must agree. Small exhaustive tests compare both sweeps with brute-force enumeration.

## Content-span control

The main export uses `p65-codeforge-filename-copy-content-backed-v1`. Besides passing the raw certificate, a task must still need more than 16,384 tokens to cover its answer aliases in a control stream consisting of complete decoded record texts in their original order, with record metadata removed and standalone 40/64-character hexadecimal identifiers normalized to spaces.

Normalization preserves every answer alias and every visible diff/summary filename alias, including hexadecimal filenames and aliases occurring inside hashes. The occurrence receipt records both normalized and protected SHA intervals. This prevents deleting a required string and then claiming that its absence proves difficulty.

This transformed stream is a **content-span control**, not a standalone solver: dropping metadata removes graph-selection information. It is not substituted into the training input. Raw and controlled results remain separate. A raw-only case remains a valid raw-profile result but is excluded from the stronger main export.

## Measured results

| Dimension | Result |
| --- | ---: |
| P64 primary rows examined | 804 |
| Rows in filename grammar | 313 |
| Raw scoped certificates | 179 |
| Content-backed subset | 130 |
| Raw-only certificates | 49 |
| No-effect approval holds | 49 |
| Other redundant-source holds | 18 |
| Qualified merged-file union tasks | 109 |
| Qualified effective approval-union tasks | 21 |

No shared-file intersection task qualified. Outside-grammar tasks remain available in P64 and are indexed as such, rather than assigned a proof they do not support.

Across the 313 filename tasks, some raw window can contain every gold alias for 43/53/79 cases at 4k/8k/16k respectively. These are gold-assisted copying opportunities, not measured neural accuracy. The content-backed subset has zero single-head alias-availability opportunities and zero exact single-head file-set successes. One whole-latest-episode alias opportunity remains; this is neither a 16k-window success nor a verified semantic shortcut, and remains recorded in the proof.

The content-backed tasks have exact minimum raw covers of **17,653–244,741 tokens**. Their metadata-free, SHA-normalized control covers are **16,750–177,071 tokens**. The upper context-capacity bins contain 12 cap64k, 47 cap128k, and 71 cap256k samples. Separately, actual context ranges [64,000,65,536], [128,000,131,072], and [256,000,262,144] contain **1/2/2 samples**. Capacity bins and narrow numerical ranges are distinct.

| Repository | Qualified tasks |
| --- | ---: |
| Ruff | 8 |
| uv | 24 |
| dprint | 11 |
| Transformers | 22 |
| OpenSearch PHP | 10 |
| Oxc | 50 |
| Pulumi | 5 |

Examples demonstrate the difference between original input length and the certified alias-cover bound:

| Repository | Context tokens | Minimum raw cover | Minimum content-control cover |
| --- | ---: | ---: | ---: |
| Transformers | 64,728 | 41,029 | 21,858 |
| Oxc | 129,143 | 72,510 | 39,591 |
| uv | 130,621 | 81,292 | 45,300 |
| OpenSearch PHP | 257,979 | 244,741 | 177,071 |
| dprint | 258,393 | 138,647 | 99,863 |

These bounds do not imply that every token in those contexts is necessary.

## Artifacts and replay

- Config: `configs/p65_codeforge_reading_proof_v1.json`
- Output: `data/candidates/p65_codeforge_reading_proof_v1/`
- Receipt: `BUILD_RECEIPT.json`, schema `longworld.codeforge-reading-proof-build.v2`
- `proofs.jsonl`: all 804 primary rows, including failures and outside-grammar cases.
- `occurrences.jsonl`: complete alias tables, raw/control occurrence positions and normalization records, stored once per context.
- `raw_qualified_index.jsonl`: all 179 raw-profile references.
- `train.jsonl`, `eval.jsonl`, `metadata.jsonl`: the 130 content-backed primary messages and aligned original five IDs.
- `unqualified_index.jsonl`: references for rows outside the stronger export; original data are retained in P64.
- Ledger: `reports/p65_pipeline_wave2/PIPELINE_RECEIPT.json`
- Closeout: `reports/p65_codeforge_reading_proof_20260909.json`

The job runs under the existing P17 CodeForge source trust and P64 catalog policy/client pins. It freshly validates source attestations before workers remove producer credentials and load the pinned local Qwen tokenizer. Raw and control tokenization/occurrence indexes are reused across tasks sharing a context within each CPU process.

```bash
.venv/bin/python scripts/audit_codeforge_reading_proof.py \
  --config configs/p65_codeforge_reading_proof_v1.json \
  --output data/candidates/p65_codeforge_reading_proof_v1 \
  --workers 3 --validate
```

Run the command through the same source-role wrapper and pins used by the wave-2 ledger. Eight focused tests passed, including exhaustive interval checks, overlapping aliases, ambiguous basenames, question-free aliases and preservation of hexadecimal filenames. Ruff passed. No model inference, GPU job, publication, canonical strict promotion, or CF claim was made.
