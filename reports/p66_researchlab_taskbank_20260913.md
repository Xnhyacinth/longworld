# P66 ResearchLab revision taskbank closeout

## Outcome

The bounded P66 ResearchLab build produced 48 local SFT candidates from three
accepted real revision-pair worlds. The rows contain 48 distinct questions,
answers, evidence sets, and natural contexts. Sparks contributes 47 train rows;
eLife contributes one held-out eval row. The Llama 3 family remains held out and
contributes two explicit rejections because its only scientific v2-to-v3
complete-file pair is 11,526 tokens and therefore fits the 16K compact control.

This is a local candidate taskbank. The receipt keeps strict long-dependency,
training release, and production eligibility false. No neural shortcut control
has been run. The deterministic controls prove the declared complete-record
contract, but do not establish universal model difficulty.

## Sources and isolation

The build consumes only existing pinned local sources:

- official arXiv source archives for *Sparks of Artificial General
  Intelligence* (`2303.12712`, v1-v5);
- official arXiv source archives for *The Llama 3 Herd of Models*
  (`2407.21783`, v1-v3);
- the authorized, email-redacted eLife 94586 v1/v2 source inventory.

The two arXiv families bind their historical signed source-role bundle bytes,
their source-role key ID, every bundle entry manifest hash, and every tar hash.
The current environment does not hold the old probe HMAC secret, so the receipt
records fresh HMAC verification as false instead of converting the historical
signature into a current-trust claim. The user authorization covers local
derivative synthesis only. Release and production remain false.

Paper-family isolation is fixed before task generation:

| Source family | Split | Accepted tasks | Accepted worlds |
| --- | --- | ---: | ---: |
| Sparks `2303.12712` | train | 47 | 2 |
| eLife `94586` | eval | 1 | 1 |
| Llama 3 `2407.21783` | eval | 0 | 0 |

No paper, source family, or revision pair crosses train and eval.

## Task construction and controls

Each task compares complete old/new scientific source records and asks for a
path-sorted JSON reconciliation containing the exact old and new excerpts and
an added, removed, or replaced disposition. Source-only formatting changes are
discarded. Contributor lists, bibliography, main build files, and macro files
are excluded. Different rows are emitted only for different path sets, which
also changes the answer and required evidence set.

Natural complete-file pairs are grouped deterministically into capacity bins.
There is no padding, prose fill, cloning, truncation, or exact-band relabeling.
The 256K-bin rows are backed only by authentic complete changed-file pairs or,
for eLife, two complete visible article records.

Every admitted row satisfies the following non-neural controls:

- question-only deterministic output is `UNKNOWN`, not the gold answer;
- latest-revision-only input lacks the old complete records and returns
  `UNKNOWN` under the task contract;
- the compact set of all required complete children exceeds 16K exact Qwen
  tokens, so the 4K, 8K, and 16K complete-child controls are insufficient;
- the positive evidence cover exceeds 16K;
- executable remove-one replay removes each required complete record in turn
  and returns `UNKNOWN` every time.

## Measured distribution

Exact tokenizer: `Qwen/Qwen3.5-4B` at revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.

| Capacity bin | Train | Eval | Total | Actual context-token range |
| --- | ---: | ---: | ---: | ---: |
| 64K | 19 | 0 | 19 | 33,138-61,281 |
| 128K | 16 | 0 | 16 | 67,671-104,617 |
| 256K | 12 | 1 | 13 | 131,552-160,767 |

All 48 contexts fall outside the narrow exact 64K, 128K, and 256K numeric
ranges. They are reported as capacity bins only. Context length is
33,138 / 71,150 median / 160,767 tokens. Full-chat length is
33,700 / 71,646 median / 161,849 tokens. The total is 3,861,365 context tokens
and 3,890,882 full-chat tokens. Assistant targets contain 24,208 tokens.

Task evidence contains 1-10 changed paths per row. Across the taskbank, answer
programs expose 107 replacements and 114 removals. The smallest compact
complete-child set is 21,600 tokens; the smallest positive evidence cover is
33,138 tokens.

Rejected adjacent revision pairs remain in `rejects.jsonl`:

| Family / pair | Complete changed-pair tokens | Reason |
| --- | ---: | --- |
| Llama 3 v1-v2 | 0 | no substantive scientific delta after exclusions |
| Llama 3 v2-v3 | 11,526 | compact complete children fit 16K |
| Sparks v2-v3 | 19,307 | natural context remains below 32K |
| Sparks v4-v5 | 30,389 | natural context remains below 32K |

## Reproducibility

The registered command surface is:

```text
uv run python scripts/materialize_p66_researchlab_taskbank.py \
  --config configs/p66_researchlab_taskbank_v1.json \
  --output data/candidates/p66_researchlab_taskbank_v1 \
  --workers 4

uv run python scripts/materialize_p66_researchlab_taskbank.py \
  --config configs/p66_researchlab_taskbank_v1.json \
  --output data/candidates/p66_researchlab_taskbank_v1 \
  --workers 4 --validate
```

The second command rebuilt the full output in a temporary directory with four
worker processes and matched every emitted file hash.

- receipt SHA-256: `4d33dee3c81445271591e57b0b9090b9283a8bb8005c2b87a159c54e641c5b83`
- candidates SHA-256: `543161fc81a0241bc19403ac336dc3dd2b7499cf8d935ce53d62754748bbf666`
- train SHA-256: `cb90ca5469ce745f8116d6c58f12736998762759ee43e8d68b4c6570935e8a17`
- eval SHA-256: `5468594f5bbcf873c0d9f4ae1b38e72c8036410670737c5f9b6f874a91ba7211`
- tokenizer asset manifest SHA-256:
  `bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`

Focused verification:

```text
pytest: 6 passed
ruff: all checks passed
git diff --check: clean
native build/validate replay: identical
```
