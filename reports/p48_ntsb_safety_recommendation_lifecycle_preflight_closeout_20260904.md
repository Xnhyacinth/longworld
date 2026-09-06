# P48 NTSB safety-recommendation lifecycle preflight closeout

## Outcome

**Source topology: PASS. 32K/64K/128K capacity preflight: PASS. Conversion:
FAIL-CLOSED. Training inventory contribution: 0.**

The official CAROL cohort contains real recommendation, recipient-response,
NTSB evaluation/reclassification, and closure events. Of 125 frozen records,
124 contain the basic closed lifecycle and 120 also state the current status
explicitly in an NTSB-authored correspondence after a recipient response. Those
120 are the only records admitted to capacity measurement.

After exact paragraph deduplication, restricting text to NTSB-authored
correspondence, and excluding paragraphs with detected email, telephone, or
explicit personal-title patterns, the pool contains **1,389 unique paragraphs /
176,496 exact Qwen tokens**. Deterministic hash ordering constructs in-band
32K, 64K, and 128K subsets without padding or truncation.

This is not a train-ready result. The narrow privacy scan cannot prove that all
names or embedded third-party quotations are absent, the public detail endpoint
is a mutable current view rather than a versioned archive, and the row-level
oracle/shortcut/remove-one gates have not run. No candidate was generated,
promoted, or counted.

## Frozen source and lifecycle evidence

The 125 official detail responses total 4,946,787 bytes and have aggregate
bundle SHA-256:

```text
85d7b92adf2a3880b8aadc99cb8cfef5da1cc71756633b5d2be3d826752e5ec4
```

The cohort is balanced across five modes: 25 each for Aviation, Highway, Marine,
Pipeline, and Railroad. It contains 156 addressee branches and 731 dated
correspondence events:

- 265 recipient-to-NTSB events;
- 466 NTSB-to-recipient events;
- 519 official-correspondence events, 140 transmittals, 34 NPRM responses, 17
  recommendation mentions, 10 report reclassifications, 9 non-recipient
  correspondences, and 2 staff-level communications.

Current closed classifications cover acceptable action (94), acceptable
alternate action (13), unacceptable action (6), exceeds recommended action (4),
superseded (3), acceptable action/superseded (2), and one each for reconsidered,
no longer applicable, and unacceptable action/no response.

Five records are rejected from executable lifecycle capacity:

| Record | Exact blocker |
| --- | --- |
| `H-22-023` | no recipient-response event |
| `A-21-043` | later NTSB text does not explicitly state the current status |
| `M-24-010` | later NTSB text does not explicitly state the current status |
| `M-25-024` | later NTSB text does not explicitly state the current status |
| `P-21-007` | later NTSB text does not explicitly state the current status |

They remain negative evidence; no status phrase is synthesized for them.

## Capacity and exact-band packing

Tokenizer: `Qwen/Qwen3.5-4B` at revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, local files only.

| Boundary | Unique paragraphs | Exact Qwen tokens |
| --- | ---: | ---: |
| All correspondence in 120 accepted lifecycles | 2,542 | 324,713 |
| Email/phone paragraphs excluded, all authors | 2,366 | 273,407 |
| NTSB-authored, email/phone paragraphs excluded | 1,407 | 178,822 |
| NTSB-authored, email/phone/explicit-title paragraphs excluded | 1,389 | 176,496 |

The last row is the conservative preflight pool. Paragraphs are ordered by
SHA-256 and accepted whole until the unchanged repository band is reached;
paragraphs that would exceed its upper edge are skipped. No text is duplicated,
split, truncated, or padded.

| Exact band | Packed tokens | Paragraphs | Pack receipt SHA-256 |
| --- | ---: | ---: | --- |
| 32K `[32000, 32768]` | 32,026 | 255 | `57359511d18ee646795a9c88048509c294a696eb0ed871f7eeddfa3cc2f91e13` |
| 64K `[64000, 65536]` | 64,026 | 508 | `88996569c7ee0eff101531b055a745c73fe584f773238386c94bf1dfd6bce445` |
| 128K `[128000, 131072]` | 128,008 | 1,001 | `4297f929e30a91aa746e57bd9a1d8713aa590043c633be401f1cdcf5e64d0669` |

These packs prove only exact-unique source capacity. They are not candidate rows
and do not bypass semantic near-duplicate, derived-view, truncation, lineage,
shortcut, or remove-one gates.

## Three-branch lifecycle DAG and deterministic oracle

The source supports three connected, genuinely different branches:

1. NTSB recommendation/transmittal → recipient-response event;
2. recipient-response event → later NTSB evaluation and status classification;
3. subsequent response or reclassification → final NTSB closure status/date.

The future deterministic oracle must validate the recommendation ID, direction,
and timezone-aware ordering; resolve the current status code through the
record-local official lookup; canonicalize punctuation only; require the final
classification to occur explicitly in later NTSB-authored text; and emit the
ordered classification timeline plus current status/date. Ambiguity is a reject.

Eighty-one accepted records contain at least two distinct explicit status labels
and form the initial answer-changing counterfactual pool. For those records,
removing the final status-bearing NTSB event must make the recorded final
classification unrecoverable from the retained timeline. This row-level oracle
and the unchanged short-window/shortcut gates remain unimplemented and unrun.

## Rights, privacy, and exact blockers

NTSB states that staff-prepared reports, recommendations, and public-docket
content are public domain unless otherwise noted. It separately warns that
publication of copyrighted or third-party material is not permission to reuse
it. Recipient-authored response text is therefore excluded from the conservative
pool rather than assumed reusable.

Even the retained NTSB text is not yet release-cleared. Within accepted records,
156 summaries contain an email pattern, 6 a telephone pattern, and 38 an explicit
personal-title pattern. Those paragraphs were excluded for capacity measurement,
but the scanner does not detect all names or third-party quotations.

Exact blockers before candidate generation are:

1. implement and independently test a deterministic name/entity/contact scrubber;
2. audit retained NTSB paragraphs for embedded third-party quotations and rights;
3. freeze a candidate source snapshot with per-record lineage despite CAROL's
   mutable current-view endpoint;
4. implement row-level status-timeline, counterfactual, and remove-one oracles;
5. run unchanged near-duplicate, exact-band, derived-view, truncation,
   short-window/shortcut, source-lineage, and promotion gates.

Until all five pass, `train_ready=false`, `do_not_generate=true`, candidate
count is 0, and inventory delta is 0.

## Reproduction

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/workspace/wynckeliao/.hf \
  uv run python reports/p48_ntsb_safety_recommendation_lifecycle_preflight.py
uv run ruff format --check \
  reports/p48_ntsb_safety_recommendation_lifecycle_preflight.py
uv run ruff check reports/p48_ntsb_safety_recommendation_lifecycle_preflight.py
jq empty configs/p48_ntsb_safety_recommendation_lifecycle_preflight_v1.json \
  reports/p48_ntsb_safety_recommendation_lifecycle_preflight_v1.json
```

The reproducer uses only the 125 frozen public detail URLs. It does not need the
subscription key used by the current CAROL search front-end, and no such key is
stored in repository artifacts.
