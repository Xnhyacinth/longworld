# P15 authentic 128K extension closeout — 2026-09-03

## Outcome

`p15-authentic-128k-extension-probe-2-v1-promoted-v7` is a signed,
gate-passing local-probe training extension. It contains 12 train rows from two
complete source-bound worlds and no eval rows. The release is diagnostic and is
not production-trust eligible.

| Bucket | Rows | Exact context tokens | Worlds / domains |
| --- | ---: | ---: | --- |
| 64K | 6 | 389,283 | uv CodeForge + CISA KEV Cyber |
| 128K | 6 | 774,639 | uv CodeForge + CISA KEV Cyber |
| Total | 12 | 1,163,922 | 2 worlds / 2 domains |

The unchanged P14 release remains 108 rows and 4,079,561 exact context tokens.
Using P14 plus this independent extension gives 120 train/eval rows and
5,243,483 exact context tokens across the two signed local-probe products. They
are not merged into a new monolithic release.

## Data programs

- CodeForge uses eight real `astral-sh/uv` release cycles and replays
  patch → approved review → passing CI → merge → release ancestry. Its six
  retained rows contain three 65,532-token and three 129,955-token views.
- Cyber uses the official CISA KEV catalog as a cumulative source-history
  program. Its retained rows contain three 64,229-token and three 128,258-token
  views, with 645 strict support records and 1,287 source relations at 128K.
- All three view types are retained at each bucket: `full`, `cf`, and
  `ordered_artifact_view`. The release gate reports no duplicates or conflicting
  prompt answers, retention 1.0, and mean sentence near-duplicate ratio 0.019975.

The Cyber projection initially omitted the exact evidence-span metric required
to classify a complete intrinsically long source. The shared view projection now
computes the span from the first and last causal-gold artifact under the pinned
tokenizer. The independent auditor now derives the span endpoints from the
remove-one/replay-verified essential-artifact receipt, requires the declared
causal-gold set to match it exactly, and recomputes the complete difficulty
record. Rebuilt 128K Cyber views report an exact evidence span of 128,170 tokens
and passed all six dense/strict audits. The release profile now requires both
exact 64K and exact 128K cells. No lower-band, near-duplicate, derived-view,
truncation, replay, or shortcut gate was weakened.

## Training export

P15 exports one immutable B5 product, containing all factual,
counterfactual, and ordered views. B1/B3/B5w were deliberately excluded from
this small extension profile: equal-token capping cannot retain every 64K/128K
view cell for those conditions without duplicating or dropping required rows.
P14 remains the comparison product for those conditions.

The B5 export contains 12 rows, 1,167,816 estimated training tokens, zero
contract rejects, zero duplicate drops, and token spread 0.0. The signed
training-manifest validator returned `ok=true` with 12 source rows and four
bound output files.

Key artifacts:

- release: `data/releases/p15-authentic-128k-extension-probe-2-v1-promoted-v7`
- quality report SHA-256:
  `96f1c5b1110c00190b91940d89374196c2db5fd9c07ac82281e2843a0dc44283`
- release gate receipt SHA-256:
  `95c7ae0381728268e2962acab23acb4e08ca556dbe3704b5df139e68b92a91bf`
- training export manifest SHA-256:
  `86d66ee6b292ba039bb5970b622565952b5976b244c70cada4ee46c99b2c4e75`
- B5 SHA-256:
  `30dcac47c46895bf38de621c18cbec2c06851ef050c045940be953e6b8e91049`

## Honest exclusions

- JPMorgan remained at zero new rows: the complete risk task reached only
  88,934 exact tokens and near-duplicate ratio 0.2919269.
- ResearchLab Sparks produced three independently strict-audited 128K rows
  (385,026 exact tokens total), but its source/candidate artifacts use a
  different local-probe trust. They remain candidates and were not mixed into
  P15.
- CodeForge 16K/32K cells remained rejected for strict-support overflow; no
  truncation or support relaxation was used to manufacture a full four-band
  world.
- The release has no independent production signing and was not uploaded or
  published.
- Generated JSONL, the Cyber sidecar, and CodeForge source exports remain in
  the external local data workspace rather than Git. The committed registry
  records their paths and hashes, but a clean checkout requires the matching
  external artifact archive before full replay.

## Verification

- P15 release gate: `ok=true`, 12/12 promotion-ready, zero errors.
- LLaMAFactory B5 manifest validation: `ok=true`, 12 source rows, four bound
  outputs.
- Core focused suite: 82 passed, including targeted regressions for forged
  numeric difficulty metadata, background-to-causal-gold relabeling, and the
  exact 64K/128K profile contract.
- ResearchLab/Sparks suite under its source trust: 7 passed.
- Ruff checks, formatting checks for touched/new files, and `git diff --check`
  passed.

## Next world queue

The next worlds are selected by executable topology, not domain labels:

1. BEA spreadsheet vintage: multiple cell-revision trajectories joined by an
   as-of calculation.
2. GitHub failure recovery: failed CI → repair → parallel review/pass →
   merge/release.
3. Microsoft five-year finance: per-year cross-statement branches followed by
   temporal reduction.
4. IETF controlling requirement: revision/published-as/update/obsolete graph
   resolution, after a unique-delta preflight.
5. OpenReview/arXiv evidence reconciliation and proposal-to-final document
   disposition, after source-capacity and cross-source/full-text adapters exist.

The design references and reuse boundaries are recorded in
`sources/research_world_synthesis_and_agent_scenarios_20260903.md`.
