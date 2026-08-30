# P12 longitudinal Git-history CPT 1,000×2

Date: 2026-08-30

## Outcome

The final local-probe candidate retained 1,000 exact-tokenizer 64K rows and
1,000 exact-tokenizer 128K rows. It contains 192,731,120 Qwen context tokens:
64,370,637 at 64K and 128,360,483 at 128K. The independent audit recomputed
every context length, reconstructed all 2,000 training rows, verified 99 signed
source manifests, and passed.

Every retained 64K row contains at least 8 distinct real commits and every 128K
row at least 16. The observed minima were 10 and 23; the final corpus uses
71,294 non-reused commit events and 280,183 non-reused source records. No
source record or commit event crosses a window or length band.

This is a real-source, multi-event continued-pretraining candidate. It is not
2,000 independent worlds and it is not executable SFT: it has no question,
counterfactual, or answer program. It remains `train_ready=false` and
`production_eligible=false` under combined local-probe trust.

## Artifacts and receipts

- Release: `data/releases/p12-cpt-git-history-longitudinal-1000x2-complete-v1/`
- Release manifest SHA-256:
  `ebbb0c3b1ca3aca9f8bd1c241385f7181eeef22689b3d88dda708e40f47aead2`
- CPT rows SHA-256:
  `86892f1190c96563be5f1580f5e52c9e28a39f7187563354a8e064330544da02`
- Training export SHA-256:
  `56d8c7d220d1b563abeedcdcf35f25eb932369e6deb33ee53cbdcdb8a8be5f84`
- Audit file SHA-256:
  `99fcd7dab48fc9548e4668b2f4f014c699a2260c20c0a02d697c41cb6c89b6df`
- Tokenizer: `Qwen/Qwen3.5-4B` at
  `a7b0d22b993d71000cf2eadfb37222a67cee521e`
- Tokenizer asset manifest SHA-256:
  `bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`
- Total release bytes: 2,376,762,680

## Distribution

| Band |  Rows |      Tokens | Events min / median / mean / max | Records min / median / mean / max | Calendar span median / mean / max |
| ---- | ----: | ----------: | -------------------------------- | --------------------------------- | --------------------------------- |
| 64K  | 1,000 |  64,370,637 | 10 / 22 / 23.82 / 123            | 84 / 93 / 93.47 / 157             | 2.17 / 5.96 / 592.34 days         |
| 128K | 1,000 | 128,360,483 | 23 / 45 / 47.48 / 181            | 172 / 185 / 186.71 / 279          | 5.04 / 10.72 / 833.26 days        |

The rows come from eight public, exact-HEAD and license-checked repositories:
`dprint/dprint`, `astral-sh/ruff`, `bytecodealliance/wasmtime`,
`oxc-project/oxc`, `denoland/deno`, `pulumi/pulumi`, `psf/requests`, and
`huggingface/transformers`. The 64K/128K row counts by repository are
14/13, 227/211, 113/84, 273/281, 140/145, 170/171, 10/10, and 53/85.

## Filtering and throughput

- zero exact duplicate contexts
- zero duplicate source bodies among used training records
- zero source-record reuse across windows or bands
- zero commit-event reuse across windows or bands
- 383 QA/chat-contaminated candidate windows rejected
- 24 non-chronological candidate windows rejected
- 2 cross-shard source-body-conflicting windows skipped and refilled
- 7,833 same-event tail records discarded to prevent a commit from crossing
  window boundaries
- 27,054 records rejected at disconnected component boundaries

The initial six-repository pass processed roughly 78K first-parent commits in
about one hour and retained 937×64K plus 905×128K. A deterministic shard merger
then preserved that work, added Requests and Transformers supplements, removed
cross-shard source-body collisions, and filled the exact 1,000/1,000 target.
Repeated checkout and remote validation was reduced to once per repository;
observed extraction throughput improved from roughly 1.6 to 0.9–1.0 minutes per
1,000-commit slice after source objects were local.

## Remaining limitations

The event gate fixes the earlier single-large-commit failure mode, but distinct
commits alone do not prove a release-cycle dependency or an answer-changing
long-range relation. One 64K row has zero measured calendar span because its
ten distinct commits share a timestamp; the median spans are only 2.17 and 5.04
days. The next longitudinal revision should require elapsed-time or release-tag
growth and explicit dependency/recovery relations, not only event count.

This candidate can support controlled local CPT experiments. Long-range
reasoning SFT remains a separate product and still requires state→answer,
counterfactual replay, remove-one, semantic corruption, window/dense retrieval,
and semantic-growth gates in SEC, paper/review, KB, standards, regulation,
clinical, cyber, and other source families.
