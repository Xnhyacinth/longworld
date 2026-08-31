# P12 Bitcoin/pandas multiband CPT — 2026-08-31

## Outcome

Two public Git first-parent histories were materialized as natural-capacity CPT
candidates at exact 16K, 32K, and 256K Qwen token bands. The configured 1,000
rows per band were safety caps, not quotas: no rows were copied, resampled, or
duplicated to fill them.

| Repository                |     16K |     32K |   256K |    Rows |   Exact tokens |
| ------------------------- | ------: | ------: | -----: | ------: | -------------: |
| `bitcoin/bitcoin`         |      76 |      69 |     58 |     203 |     18,327,180 |
| `pandas-dev/pandas`       |      91 |      48 |     39 |     178 |     13,023,883 |
| **New audited increment** | **167** | **117** | **97** | **381** | **31,351,063** |

The pandas raw scan produced one additional 256K row. Full transitive
cross-release subtraction found an exact source-body overlap and removed that
whole row; the retained 381-row increment has zero source-body, source-event,
or full-context overlap with the prior inventory or between the two new
repositories.

## Replayed gates

Every retained row was independently replayed after filtering. The audit binds
the serialized row to the signed source manifest's native record ordinal,
source pointer, predecessor edges, committer timestamp, source-event identity,
and exact text digest. It also reconstructs the CPT training export and exact
Qwen token count.

The configured minimum source spans are one day at 16K, three days at 32K, and
30 days at 256K. The observed minima were:

| Repository          |      16K |       32K |        256K |
| ------------------- | -------: | --------: | ----------: |
| `bitcoin/bitcoin`   | 86,689 s | 260,953 s | 2,594,466 s |
| `pandas-dev/pandas` | 86,667 s | 260,415 s | 2,624,124 s |

Minimum distinct commit events were 4/8/68 for Bitcoin and 4/8/64 for pandas.
All source slices satisfy the configured maximum truncated-commit ratio of 20%,
and the signed omission index accounts for every retained and omitted chunk.

The Bitcoin release binds 27,749 source records and 8,808 commit events across
18 signed source manifests. The pandas release binds 20,678 records and 7,798
events across 34 manifests.

Immutable release manifest digests:

- Bitcoin: `08fd21d2a1f8a4ed90ff80c11fea8d28a73c3053924c3dbf318b09e31a6c11e5`
- pandas: `ba65ec62d92e5b3ca0824df0dbb1044cce7f374eaca0a6ae9482f06867cd7bab`

## Inventory and trust boundary

Adding this disjoint increment to the previous audited CPT inventory yields
3,006 rows and 286,634,406 exact tokens:

| Band |  Rows |
| ---- | ----: |
| 16K  |   300 |
| 32K  |   253 |
| 64K  | 1,111 |
| 128K | 1,109 |
| 256K |   233 |

These are content-gated local-probe CPT candidates, not production-trust
releases. Their manifests explicitly retain `train_ready=false` and
`production_eligible=false`. They may be used for local diagnostic training
only after the operator accepts that trust boundary; they are not uploaded to
the Hugging Face training dataset until the production signing/approval chain
authorizes publication.

The new span and predecessor gates prove authentic, connected, long-time source
history. They do not turn CPT rows into executable SFT tasks and do not prove an
answer-changing dependency program. Cyber/Finance and other state→answer
worlds remain a separate promotion track.

Local artifacts:

- `data/releases/p12-cpt-git-history-multiband-bitcoin-capacity-v1-dedup/`
- `data/releases/p12-cpt-git-history-multiband-pandas-capacity-v1-dedup/`
