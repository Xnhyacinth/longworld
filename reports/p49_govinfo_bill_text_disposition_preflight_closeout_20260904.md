# P49 GovInfo bill-text disposition preflight closeout

## Outcome

**Official topology: PASS. Section-disposition preflight: PASS. Near-deduplicated
32K/64K/128K capacity: PASS. Candidate conversion: NOT RUN. Inventory delta: 0.**

Two independent official 118th-Congress chains—H.R. 815 → Public Law 118-50
and H.R. 4366 → Public Law 118-42—each expose EAS, EAH, enrolled, and final-law
text plus dated actions connecting those stages. The ten frozen GovInfo sources
total 11,574,677 bytes with bundle SHA-256
`2b0adaf00511007bdcdb4db06ff8ea426da59ff8c7cbd9a70876b9ad210275b2`.

After removing presentation-only XML nodes, excluding sections shorter than 80
characters, and globally collapsing five-word-shingle Jaccard matches at the
unchanged 0.90 threshold, **2,635 input section instances become 871 unique
units / 204,442 exact Qwen tokens**. Whole-section deterministic packs reach all
three requested bands without padding, cloning, splitting, or truncation.

No candidate was generated or promoted. The preflight alignment is deterministic,
but formal semantic near-duplicate, answer-dependence, shortcut, counterfactual,
remove-one, and release gates remain unrun. Thus `train_ready=false`,
`do_not_generate=true`, candidate count 0, and inventory delta 0.

## Version and action topology

The main Bill Status node—not filename order—must contain each selected EAS,
EAH, and ENR type/URL. It must also contain each dated action edge:

| Chain | EAS → EAH | EAH → ENR | ENR → final |
| --- | --- | --- | --- |
| H.R. 815 | House amendment agreed 2024-04-20 | Senate concurrence 2024-04-23 | Public Law 118-50, 2024-04-24 |
| H.R. 4366 | House amendment agreed 2024-03-06 | Senate concurrence 2024-03-08 | Public Law 118-42, 2024-03-09 |

The two status files contain 71 and 104 main-bill actions respectively. Their
selected legislative XML roots, embedded dates, Public Law document numbers,
approval dates, and Public Law-to-bill backlinks are independently validated.
Every one of the eight text files carries an explicit 17 USC 105 public-domain
statement.

## Deterministic section dispositions

The key is the normalized numeric hierarchy from division through subpart plus
the section number. Unnumbered direct siblings use ordinal. Duplicate keys are
ambiguous and every unit under them is excluded; the oracle never guesses.
`page`, `sidenote`, `sourceCredit`, and `note` subtrees are removed before text
comparison. Same-key five-word-shingle Jaccard at least 0.90 is `retained`;
lower similarity is `modified`; keys present on only one side are `removed` or
`added`.

| Chain transition | Retained | Modified | Removed | Added | Ambiguous source/target units |
| --- | ---: | ---: | ---: | ---: | ---: |
| H.R. 815 EAS → EAH | 1 | 5 | 49 | 145 | 0 / 2 |
| H.R. 815 EAH → ENR | 150 | 0 | 1 | 0 | 2 / 2 |
| H.R. 815 ENR → Law | 144 | 3 | 3 | 0 | 2 / 2 |
| H.R. 4366 EAS → EAH | 124 | 108 | 129 | 361 | 0 / 0 |
| H.R. 4366 EAH → ENR | 592 | 0 | 1 | 0 | 0 / 0 |
| H.R. 4366 ENR → Law | 563 | 29 | 0 | 0 | 0 / 0 |

The results expose both substantive replacement and stable carry-forward. They
also show why repeated ENR/final content cannot be used as volume: all 740
eligible ENR sections are removed by global near-dedup, and 698 of 738 eligible
Public Law sections are removed. The final-law XML contributes only text that
does not cross the 0.90 duplicate threshold after presentation stripping.

These are source-text dispositions. A future task must not describe every
below-threshold final-law difference as a substantive legal amendment without a
stronger cross-schema normalization and legal-text equivalence oracle.

## Unique capacity and exact bands

Tokenizer: `Qwen/Qwen3.5-4B`, revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, local files only.

| Capacity boundary | Sections | Exact Qwen tokens |
| --- | ---: | ---: |
| Exact-normalized dedup | 1,664 | 412,392 |
| Global 0.90 near-dedup | 871 | 204,442 |

Deterministic SHA-256 ordering yields:

| Band | Whole sections | Tokens | Receipt SHA-256 |
| --- | ---: | ---: | --- |
| 32K `[32000, 32768]` | 155 | 32,017 | `09bb25e3bcdb27a64c9a74d3fe41bcd2e8d68a612cb07ef613b5b7e4c6e38fcd` |
| 64K `[64000, 65536]` | 290 | 64,104 | `f9d9e321d23d7037dcb51412097592fa1aaf5e514566c490c7d5b4940e4f9ca2` |
| 128K `[128000, 131072]` | 569 | 128,028 | `0bd094bc1409d31c3619bb3e5a37e7f679a8dc7ee17282a4594b0d7bfd69c837` |

Each pack uses zero padding tokens and zero split/truncated sections. These are
capacity receipts, not candidate or promotion receipts.

## Exact blockers before conversion

1. Formal candidate near-duplicate and semantic equivalence checks have not run;
   the preflight's lexical 0.90 collapse is only a conservative capacity screen.
2. Cross-schema Bill XML → USLM normalization needs gold tests so page/source
   furniture cannot create false `modified` labels.
3. A candidate oracle must bind every disposition to two source hashes and reject
   ambiguous structural keys at row construction time.
4. Counterfactual replay must change a requested disposition, and remove-one must
   make at least one requested answer unprovable.
5. Unchanged exact-band, derived-view, truncation, source-lineage, 4K/8K/16K
   shortcut, retrieval, and independent promotion gates must pass.

The rights preflight is technically green because all selected text XML includes
the explicit public-domain statement. GovInfo's third-party caveat still applies,
and no legal opinion is claimed. The retained boundary contains public legislative
section text, not private record fields.

## Reproduction

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/workspace/wynckeliao/.hf \
  uv run python reports/p49_govinfo_bill_text_disposition_preflight.py
uv run ruff format --check reports/p49_govinfo_bill_text_disposition_preflight.py
uv run ruff check reports/p49_govinfo_bill_text_disposition_preflight.py
jq empty configs/p49_govinfo_bill_text_disposition_preflight_v1.json \
  reports/p49_govinfo_bill_text_disposition_preflight_v1.json
```
