# P12 CPT diversity and multiband review (2026-08-30)

## Scope and classification

Reviewed candidate:
`data/releases/p12-cpt-git-history-longitudinal-1000x2-complete-v1`.
It contains 1,000 exact 64K and 1,000 exact 128K continued-pretraining
documents. These are 2,000 disjoint source windows across eight public GitHub
repositories, not 2,000 executable worlds and not SFT rows.

## Filters that passed

- pinned Qwen tokenizer length replay for every row;
- signed source-manifest and CPT-row provenance;
- public repository, exact HEAD, allowlist, and SPDX-license binding;
- chronological connected first-parent records;
- at least 8 configured distinct commits at 64K and 16 at 128K (observed
  minima 10 and 23);
- no reused source record or commit event across retained windows/bands;
- zero exact full-context duplicates and zero exact source-record-body
  duplicates;
- QA/chat contamination, secret-shaped text, disconnected components, short
  tails, and non-chronological windows rejected fail-closed.

The source-event chunk cap also prevents one large commit from dominating a
row. The largest commit contributes at most 11.90% of records at 64K and 5.81%
at 128K; no row exceeds 20%.

## Diversity and temporal findings

Repository distribution across all 2,000 rows:

| Repository                | Rows |  Share |
| ------------------------- | ---: | -----: |
| oxc-project/oxc           |  554 | 27.70% |
| astral-sh/ruff            |  438 | 21.90% |
| pulumi/pulumi             |  341 | 17.05% |
| denoland/deno             |  285 | 14.25% |
| bytecodealliance/wasmtime |  197 |  9.85% |
| huggingface/transformers  |  138 |  6.90% |
| dprint/dprint             |   27 |  1.35% |
| psf/requests              |   20 |  1.00% |

The HHI is 0.1888, corresponding to 5.30 effective repositories; the
entropy-effective repository count is 5.96 and the maximum repository share is
27.7%. This is meaningful repository diversity inside one source family, but it
is not domain diversity: all rows are public Git commit histories.

| Band | Median events | Median records | Median span | <1 day | <7 days | >=30 days |
| ---- | ------------: | -------------: | ----------: | -----: | ------: | --------: |
| 64K  |            22 |             93 |   2.17 days |    223 |     878 |        19 |
| 128K |            45 |            185 |   5.04 days |     28 |     644 |        31 |

Two 64K rows contain distinct commits sharing the same timestamp and therefore
have zero elapsed seconds. Event multiplicity is real, but the temporal result
is not yet strong enough to call the entire candidate cross-release or
long-timescale workflow data.

## Unproven or missing gates

- no release-boundary or multi-release-cycle requirement;
- no semantic document-level near-duplicate replay beyond exact source-body
  deduplication;
- no explicit answer-changing dependency or executable answer program (CPT does
  not have an answer);
- no cross-domain balance outside Git history;
- no production-asymmetric/KMS approval;
- no training/evaluation evidence that this candidate improves long-context
  capability without harming general capability.

The candidate therefore remains `train_ready=false` and
`production_eligible=false`. Short-span rows should be quality-tiered or
regenerated under stronger elapsed/release-cycle requirements before a stronger
longitudinal claim.

## Why counts were round and bands were missing

The former materializer had two independent design limitations:

1. it hard-coded only 64K and 128K in materialization, export validation,
   independent audit, and shard merge;
2. it stopped exactly at configured 1,000/1,000 quotas and the auditor required
   retained counts to equal those quotas.

Thus the round numbers were balanced curriculum quotas, not the natural number
of eligible source windows. The revised contract uses the shared exact-token
band registry for 16K, 32K, 64K, 128K, and 256K and adds a capacity-scan mode.
In capacity mode, target counts are safety caps; source exhaustion may produce a
non-round retained count, and each band reports whether the cap censored the
observed capacity. Balanced sampling is a later, separate operation.

## Product boundary

- CPT scale should come from coherent, provenance-connected source histories
  with deduplication and temporal/source-relation gates.
- SFT scale must additionally require state→answer dependence, actual CF replay,
  remove-one failure, local-window/dense negatives, semantic growth, and strict
  executable proof.
- Implemented adapters, source inventories, candidate tasks, complete promoted
  worlds, views, and trainable rows must remain separate counts.

## First multiband capacity result

The first capacity scan used the previously unused commit range 16,000–21,747
of `huggingface/transformers`. A fail-first review found and repaired a packer
bug that treated any record larger than the band's width as unpackable; that
assumption incorrectly discarded ordinary 400–768 token source records at 16K.

The corrected scan retained 91 rows before union deduplication. Cross-release
review against the used records of the 64K/128K candidate found three exact
source-body conflicts in three 256K rows. Reference subtraction removed the
whole rows and rebuilt the release.

Final audited increment:

| Band      |   Rows | Exact Qwen tokens | Minimum observed commits |
| --------- | -----: | ----------------: | -----------------------: |
| 16K       |     31 |           502,225 |                        4 |
| 32K       |     31 |         1,003,002 |                        8 |
| 256K      |     26 |         6,663,325 |                       87 |
| **Total** | **88** |     **8,168,552** |                        — |

The increment uses 4,345 real commits and 12,609 source records. Independent
audit reloaded the referenced old release and recomputed zero source-body,
commit-event, and full-context overlap across releases. The counts are non-round
because no band reached its 1,000-row capacity safety cap.

## Three-repository five-band capacity wave

The next scan consumed 32,312 first-parent commits from Flask, scikit-learn,
and DuckDB. The raw capacity pass retained 547 rows. A union subtraction against
both earlier audited releases removed ten whole rows whose signed source bodies
had already appeared; the final release retained 537 rows and 54,383,671 exact
Qwen tokens:

| Band | Rows | Exact Qwen tokens | Median commits | Median span |
| ---- | ---: | ----------------: | -------------: | ----------: |
| 16K  |  102 |         1,651,872 |              7 |   1.48 days |
| 32K  |  105 |         3,396,683 |             13 |   3.39 days |
| 64K  |  111 |         7,146,190 |             27 |   6.97 days |
| 128K |  109 |        13,990,158 |             51 |  14.08 days |
| 256K |  110 |        28,198,768 |          109.5 |  30.44 days |

The final release contains 26,113 unique commits, 82,423 unique source records,
and 537 unique contexts. The repaired independent audit verifies the full
transitive reference closure, keeps inherited manifest pins immutable, derives
body identities from signed source indexes, and reports zero cross-release body,
event, or context overlap.

This wave is length-balanced but not entity-balanced: scikit-learn contributes
276 rows, DuckDB 242, and Flask 19. Its overall median span is 7.15 days; 456 of
537 rows span less than 30 days, while only six span at least one year. Every
window is chronological and connected, but a Git-history CPT predecessor chain
is structural dependence rather than an executable answer-changing semantic
proof. The accurate product label is therefore **multiband chronological CPT
capacity probe**, not broad-domain LongWorld SFT.

Across the three mutually source-disjoint audited CPT releases, the local-probe
candidate inventory now contains 2,625 rows and 255,283,343 exact context tokens:

| Band |  Rows | Exact Qwen tokens |
| ---- | ----: | ----------------: |
| 16K  |   133 |         2,154,097 |
| 32K  |   136 |         4,399,685 |
| 64K  | 1,111 |        71,516,827 |
| 128K | 1,109 |       142,350,641 |
| 256K |   136 |        34,862,093 |

These remain `train_ready=false` and `production_eligible=false`. Exact and
cross-release duplication filters are proven; semantic near-duplicate,
release-cycle, executable long-range dependency, independent KMS trust, and
training-effect gates are not.
