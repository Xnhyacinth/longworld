# P14 ResearchLab section-relation growth closeout

## Outcome

The Sparks and Llama 3 `paper_revision_section_reconciliation` worlds now use
the same source-faithful tier semantics as the accepted ResearchLab revision
trace programs: 16K reconciles compiled sections inside the signed latest
revision without asserting an edge; 32K adds the latest-to-previous official
`revision_of` edge; 64K adds the immediately preceding official edge. The
question, answer program, sufficient set, executable preconditions, rendered
control, and proof depth all grow together as `0/1/2` relations and `2/3/4`
proof depth. No threshold was relaxed.

The P13 cumulative-history checker returns `{}` for each world's nine audited
rows under `p13-authentic-six-domain-probe-12-v1`.

## Sparks of AGI

- world: `lab142303127-nacrebench-98:focal`
- official source chain: arXiv `2303.12712`, v5 -> v4 -> v3
- source bundle: `data/source_inventory/p14_paper_sparks_revision_v1/paper_source_workflow_bundle.p14.sparks-section-reconciliation.signed.json`
- bundle SHA-256: `97b35381a2dbd429b9a26b46fdd9f59e75042f5d2dfe8ccc4c373c70fee795ec`
- source key id: `probe-source-3a689228f9679b0522a18cadd07f1e97`
- candidate / preflight / strict audit / rejects: `9 / 9 / 9 / 0`
- exact tokens, all full/CF/ordered: `16K=16177`, `32K=32033`, `64K=65235`
- relation counts by band: `0 / 1 / 2`
- evidence spans full/CF/ordered: `16083/16083/16083`,
  `31928/31928/31928`, `65040/65040/64455`
- near-dup by band: `0.0085 / 0.0050 / 0.0034`
- candidate SHA-256: `6954eb57fd4c49f225d06278fdbea982f531fe071014845215cb76ca72bf6aac`
- ranking SHA-256: `d40c2c377c23b6164b9d0acc758e997722cd07cbca1708d00b0b4b55f6271918`
- audit SHA-256: `de86ede2c59c3254d647dca1c6e3db8672ee4534024b52d580193aa343d9d51f`

Fresh evidence is under
`reports/p14_researchlab_sparks_section_reconciliation_v3/`.

## Llama 3 Herd of Models

- world: `lab142407218-ashloftbench-17:focal`
- official source chain: arXiv `2407.21783`, v3 -> v2 -> v1
- source bundle: `data/source_inventory/p14_paper_llama3_revision_preflight_v1/paper_source_workflow_bundle.p14.llama3-source-foundation.signed.json`
- bundle SHA-256: `2ca7b9448646ddda01281890e6fb5f595b2f6acad6fadcc7c6f8c94aa241ed3c`
- source key id: `probe-source-3a689228f9679b0522a18cadd07f1e97`
- candidate / preflight / strict audit / rejects: `9 / 9 / 9 / 0`
- exact tokens, all full/CF/ordered: `16K=16182`, `32K=32497`, `64K=65500`
- relation counts by band: `0 / 1 / 2`
- evidence spans full/CF/ordered: `16088/16088/16088`,
  `32392/32392/32315`, `65305/65305/64429`
- near-dup by band: `0.0000 / 0.1590 / 0.0847`
- candidate SHA-256: `4c277b3f9c06ae9daf4b67dba5fa65c837c0ba1f1288bfa1bed555dcfbe656d6`
- ranking SHA-256: `d5943acc9b5b1db7768371f2bfcf022c8ea56e80e6c0dde6072390d48e1bae31`
- audit SHA-256: `c66a03526d73694c041df53a4a1093ee7fcba0fd686ca67172e0210f8f8e6373`

Fresh evidence is under
`reports/p14_researchlab_llama3_section_reconciliation_v3/`.

All candidate, ranking, and audit artifacts use the current local-probe trust
file at
`/root/.longworld-p12-active/p12-probe-12-20260829-v1/local_probe_trust.json`.
Candidate, ranker, and auditor key ids are respectively
`probe-candidate-deb4eba9a56a148b8066f10f855e1d37`,
`probe-ranker-0b6111f9459235935d33928a1803ebe8`, and
`probe-auditor-7e6285a97ae06a6a55d3992764711c3b`.

Focused verification: `7 passed`; Ruff: clean; `git diff --check`: clean.
