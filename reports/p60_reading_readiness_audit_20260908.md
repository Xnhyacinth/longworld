# P60 reading-only readiness audit — 2026-09-08

Read-only scan of **27 gate-bearing local products / 255 rows** (237 train, 18 eval). Gate-bound train/eval/quality-report file hashes matched. This is a known-defect diagnostic, not a new release gate or model evaluation.

| Classification | Train | Eval | Total |
| --- | ---: | ---: | ---: |
| `known_question_only` | 18 | 0 | 18 |
| `needs_byte_hash_tool` | 15 | 9 | 24 |
| `not_assessed` | 0 | 0 | 0 |
| `checked_no_known_issue` | 204 | 9 | 213 |

## New historical findings

The patch-hash requirement extends beyond the previously inspected train rows: **P14 Wasmtime contributes nine affected eval rows** (16K/32K/64K × full/CF/ordered). The P40 OAuth v3 128K full and ordered rows also match the known codebook baseline despite that answer program being absent from the current P57 catalog.

| Patch source family | Train rows | Eval rows | Requested digest occurrences | Missing from all inspected surfaces |
| --- | ---: | ---: | ---: | ---: |
| github.com/astral-sh/uv | 6 | 0 | 36 | 36 |
| github.com/bytecodealliance/wasmtime | 0 | 9 | 21 | 21 |
| github.com/huggingface/transformers | 6 | 0 | 36 | 36 |
| github.com/pulumi/pulumi | 3 | 0 | 12 | 12 |

Across the patch family, **105 requested digest occurrences / 24 distinct digests** were inspected. Literal visibility was checked separately in `question`, `document_context`, and `context`. Exact patch bytes and a SHA256 tool would be needed for missing digests; this scan did not establish whether rendered context preserves those exact bytes. It does not claim mathematical unanswerability.

## Known question-only matches

The shared detector applies to 33 rows and exactly matches 18: {'full': 9, 'ordered_artifact_view': 9}. It exactly matches **0 CF rows** out of 11 applicable CF rows; the report does not describe CF as universally solved.

| Product | Exact-match rows | Views | Outside P57 answer-program coverage |
| --- | ---: | --- | --- |
| p40-ietf-oauth-semantic-growth-probe-1-v1-promoted-v14 | 2 | full, ordered_artifact_view | True |
| p57-ietf-acme-issuance-32k-probe-1-v1-promoted-v1 | 2 | full, ordered_artifact_view | False |
| p57-ietf-dnssec-64k-probe-1-v1-promoted-v1 | 2 | full, ordered_artifact_view | False |
| p57-ietf-http-semantics-succession-probe-1-v1-promoted-v1 | 2 | full, ordered_artifact_view | False |
| p57-ietf-http2-succession-probe-1-v1-promoted-v1 | 2 | full, ordered_artifact_view | False |
| p57-ietf-pkix-path-succession-probe-1-v1-promoted-v1 | 2 | full, ordered_artifact_view | False |
| p57-ietf-ssh-architecture-32k-probe-1-v1-promoted-v1 | 2 | full, ordered_artifact_view | False |
| p57-ietf-tls13-handshake-succession-probe-1-v1-promoted-v1 | 4 | full, ordered_artifact_view | False |

## Scope and interpretation

The product list uses the twenty canonical P58 baseline products, its two P58 additions, the four P59 qualified closeout products, and the newly gated P60 Pulumi product. Older duplicate promoted versions and unqualified candidate directories are not included. Canonical physical paths are deduplicated. Existing receipt `ok` and its file hashes were checked; source/auditor HMAC signatures were not independently reverified.

`checked_no_known_issue` means only that these two checks did not flag a row. It is **not** evidence of general reading-only readiness or resistance to no-context models. No broad model, dense-retrieval, near-duplicate, split-leakage or B5-alignment evaluation was performed.

The JSON companion records every product, input hash, source family, split, view, query ID, predictor applicability and per-digest visibility. It also records the exact shared detector source and module hash for reproducibility. Nothing in the frozen products or their eligibility receipts was modified.

## New train versus existing eval source identities

The additional check compares the **15 newly qualified P59/P60 train rows** against **18 existing eval rows** using repository names, structured issuer CIKs and `real_source_workflow_ids`, not world IDs. All required applicable identifiers were present; no intersection was found in these recorded identifier sets. This is not a general content-leakage audit.

Existing eval includes nine Wasmtime rows. The new qualified train set includes **zero Wasmtime rows**; unsuccessful source/generation attempts were excluded. The new finance CIKs are Alphabet `0001652044`, Micron `0000723125`, and NVIDIA `0001045810`; the existing eval set has no finance CIK records.

P59 Pulumi recovery and P60 Pulumi patch-review **do share the same repository and four source-workflow IDs with each other**. That is train-to-train source reuse for distinct tasks, not two independent newly acquired repositories. No such identifiers matched existing eval.

The JSON now includes `flagged_rows_by_split`: **33 train and nine eval flags**, each with query ID, view, product split-file SHA256 and gate-receipt SHA256. The remaining **213 rows merely did not trigger these two known detectors**. All exact-match classifications were rechecked after the shared singleton guard refinement and were unchanged.
