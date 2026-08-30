# P12 CodeForge dprint Real-History Diagnostic

Date: 2026-08-30 UTC

## Status

This diagnostic is a reproducible, source-signed CodeForge candidate attempt,
not a promotion-v2 world and not train-ready data. The final P12 preflight
retained zero complete worlds because the real 32K band was not feasible.

## Real source history

The local-probe bundle contains nine public dprint repository episodes: three
release cycles (0.50.2, 0.51.0, and 0.51.1) plus six intervening PR/CI histories.
Together they contain 502 records and 546,115 source-body characters. No episode
is duplicated in the bundle.

## Candidate and filter result

Generation made 12 attempts and retained five candidate rows before the
complete-world preflight:

| Band | Retained views | Exact Qwen tokens | Result         |
| ---- | -------------: | ----------------: | -------------- |
| 16K  |              2 |            16,298 | candidate-only |
| 32K  |              0 |            30,361 | rejected       |
| 64K  |              3 |            65,528 | candidate-only |

The five temporary candidates total 229,180 exact context tokens and have zero
duplicate content hashes. Their real-source ratio is 0.525–0.725 and generic
background is zero. Across the retained lower/upper candidates, strict support
grows from 14 to 40, authentic relations from 22–24 to 66–68, and event-bearing
tokens from 8,620 to 47,472.

The 32K attempt was rejected because it reached only 30,361 exact tokens and an
evidence distance of 15,144 tokens, below the required 16,000. Other views also
exposed local-window shortcuts. A 20-seed sweep retained no complete 32K band,
so the failure is structural rather than a lucky-seed issue.

The P12 preflight therefore rejected all five temporary candidates with
`missing 32k`. Dense audit, promotion, release gating, and HF publication were
not run.

## Review disposition

An experimental release-window selector was removed after independent review:
it did not execute for this exactly-three-release bundle and optimized character
mass without first proving pinned-tokenizer band feasibility. The next repair
must first reject windows that cannot satisfy exact length, evidence distance,
strict support, proof growth, and authentic-source-relation growth; only then may
it rank feasible windows. It also needs additional causally linked real PR/CI or
release-cycle records so 32K grows by workflow history rather than padding.

Reproduction inputs are:

- `configs/p12_codeforge_dprint_patch_early_v1.yaml`
- `configs/p12_codeforge_dprint_patch_early_v1_bundle.json`

Local ignored diagnostics are under
`data/releases/p12-codeforge-dprint-patch-early-v1-candidate/` and
`data/releases/p12-codeforge-dprint-patch-early-v1-p12-preflight/`.
