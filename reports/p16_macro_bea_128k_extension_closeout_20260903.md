# P16 Macro BEA vintage 128K local-probe extension — 2026-09-03

## Outcome

`p16-macro-bea-128k-extension-probe-1-v1-promoted-v1` is a signed, gate-passing
local-probe training extension. It contains 12 train rows from one complete
source-bound Macro world and no eval rows. The release is diagnostic and is
not production-trust eligible. P14 and P15 remain immutable independent
products.

| Bucket | Rows | Exact context tokens | World |
| --- | ---: | ---: | --- |
| 16K | 3 | 48,306 | BEA 2005Q4 vintage path |
| 32K | 3 | 96,306 | BEA 2005Q4 vintage path |
| 64K | 3 | 192,306 | BEA 2005Q4 vintage path |
| 128K | 3 | 384,753 | BEA 2005Q4 vintage path |
| Total | 12 | 721,671 | 1 world / 1 domain |

Using P15 v7 plus this independent extension gives 24 train rows and 1,885,593
exact context tokens across the two signed local-probe products (9×128K views).
They are not merged into a new monolithic release.

## Data program

This is the existing Macro as-of cell-revision program on the official BEA
real GDP percent-change workbook for 2005Q4, not a CISA KEV or uv relabel.
Each band reconstructs the complete revision path through an answer-changing
decision date. Length comes from additional same-series period trajectories
packed as `natural_background`. Exact 128K views are 128,251 Qwen tokens.
Dense/strict audit accepted 12/12. Near-duplicate ratio is 0.0.

## Training export

The profile exports one immutable B5 product with all four exact bands and
all three views. B1/B3/B5w stay excluded, matching the P15 small-extension
contract. The B5 export contains 12 rows, 725,610 estimated training tokens,
zero contract rejects, zero duplicate drops, and token spread 0.0. The signed
training-manifest validator returned `ok=true` with 12 source rows and four
bound output files.

Key artifacts:

- release: `data/releases/p16-macro-bea-128k-extension-probe-1-v1-promoted-v1`
- profile SHA-256: `9cd0a565c4b89468ccd74f0b9095fe4eb38b99dd3776e2a21fab39dfe51e6a2f`
- quality report SHA-256: `021f2f7d077505cd9d9570ccbbfcd5c051ed544dbb01ecc12e40a3dadef79d08`
- release gate receipt SHA-256: `002b4f0b25d5340928eadbf5ac768a3f219f10ea59f8fae3045a559f207cecf7`
- training export manifest SHA-256: `61d7117c95643df317aba580427b9dc2afe4f28b67d36d43bf1742b49090a5e9`
- B5 SHA-256: `6a6cd40b5dfcc0a709d7bfecedbbd42ca0caced567dd81ad146255279ef95107`

## Honest exclusions

- Microsoft Finance 128K is a 12/12 dense-audit candidate (history 128,217;
  views ~128,584) under `p12-probe-12-20260829-v1`. That trust cannot verify
  against this P15/P16 v2 probe, so the rows were not mixed. Same boundary as
  P15 Sparks.
- Pulumi `failure_recovery_release_trace` has authentic 64K three-view cells
  but unique 128K tokens are 114,833. Next unused unique-tag cycles overflow.
- RFC 9421 unique tokens are 72,113 after draft-19 reflow collapse.
- Production signing and HF upload remain closed.
