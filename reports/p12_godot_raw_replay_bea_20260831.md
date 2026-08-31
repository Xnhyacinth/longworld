# P12 Godot raw replay and BEA source inventory — 2026-08-31

## Outcome

The Godot first-parent capacity scan is complete and independently replayed.
It retained 41 natural-capacity CPT rows and 1,370,439 exact Qwen context
tokens. The configured 1,000 rows per band were ceilings, not quotas; no row
was copied, padded, or resampled.

| Band | Rows | Exact context tokens |
| ---- | ---: | -------------------: |
| 16K  |   23 |              371,643 |
| 32K  |   11 |              355,565 |
| 64K  |    4 |              258,049 |
| 128K |    3 |              385,182 |
| 256K |    0 |                    0 |
| **Total** | **41** | **1,370,439** |

The report-role audit verified all 40 source manifests and the complete
39,499-commit allowlisted suffix of the 42,495-commit first-parent history.
The preceding 2,996 commits were excluded at the exact boundary where the
approved `LICENSE.txt` path is absent. It replayed portable commit/tree/blob
objects, license-path bindings, chronological source records, exact tokenizer
lengths, source-event and elapsed-time gates, truncation accounting, and the
training export. It found zero exact duplicate contexts, exact duplicate used
source bodies, cross-band source-record reuse, or cross-band commit-event
reuse. The retained rows contain 965 unique commits, 2,232 source records, 41
windows, and 24 accepted base workflows.

The first audit exposed a producer bug: the release manifest counted
repositories instead of accepted `base_workflow_id` values and reported one
workflow. The auditor rejected it. The producer now counts workflows only when
a row passes the CPT contract; the release was deterministically rebuilt from
40 authenticated extraction checkpoints and reports the replayed value 24.

Immutable receipts:

- release: `data/releases/p12-cpt-git-history-multiband-godot-capacity-v7/`
- release manifest SHA-256:
  `90a714ab0b9163f092a5c257635ebad812676d45304ec4ee0e7b9579c82c614e`
- CPT rows SHA-256:
  `698732434c0e2d72956f5abd144f24c8e24b47465afb125b4644d41ca1df29c0`
- training export SHA-256:
  `8f6475fcc9230f0d87bf62ab88f0a74dbe1258b507c2da4d6d442d66fb3363c8`
- audit file SHA-256:
  `cb4a6c22983aed08b3b9baa4c802a144756ddc15f45821a6de5767c37b05ee39`
- report attestation digest:
  `ef55b666bd9fd19db69805cdc7f2e6086775e85d6b96bfae81186fb73071a713`

This release is a standalone audited local-probe candidate. It is not yet a
member of the signed cross-release union. The current trust root cannot verify
the older pandas reference's source-role signatures, and the historical
source key is not present. Re-signing those manifests would fabricate source
approval. Therefore the canonical mutually disjoint inventory remains 3,006
rows / 286,634,406 tokens; the arithmetic 3,047 / 288,004,845 must not be
reported as a signed union until the old releases are independently re-imported
or a legitimate historical verification root is restored.

## Raw task replay

Cyber and Finance now replay arbitrary raw tokenizer-offset windows from the
exact serialized task context. Fresh local-probe audits accepted 3/3 Cyber and
3/3 Finance raw executable receipts. Semantic artifact-boundary enumeration is
reported separately from exact raw execution; generic local-window,
contiguous-window, and no-shortcut claims remain false. Consequently these six
rows are diagnostics, not promoted SFT. The Cyber and Finance audit digests are
`edd1c9cb2c566dce45707da23342baa47c7b83f4ea96a9f09e493302d94fefbd`
and `899f846e374f7092c4b68484aecff7fc4be5ff00857cb0bdb96e99c692247b2a`.

Git-history source receipts now include portable raw commit/tree/blob path
proofs and authenticated extraction checkpoints. Public Git email metadata in
those proof sidecars is audit-only and is not approved for HF publication.

## Official BEA inventory

The first live macro-vintage source inventory fetched the fixed official BEA
GDP/GDI workbook URL. The 74,180 response bytes have SHA-256
`c6b10cc799e213974cb73fb7083221e71b8298e98cb5c3b36153a42cd09af3fe`.
Bounded XLSX parsing yielded 3,959 cell-provenanced observations, 3,567
temporal relations, and 384 trajectories of depth 3–16. Of the relations,
2,935 change the observed value and 632 supersede a vintage without an observed
value change.

This is source inventory, not training data. It has no canonical 16/32/64K
rendering, task sidecar, raw/dense replay, promotion receipt, or production
attestation, so it contributes zero worlds and zero CPT/SFT rows. The next
macro step is to render non-overlapping vintage histories and implement an
answer program that distinguishes value-changing revisions from unchanged
supersession before attaching it to the shared replay chain.

The macro, visual-model compatibility, and game-compatibility evaluators are
also deterministic nonproduction prototypes only. They implement state,
answer, counterfactual, and remove-one mechanics but are not yet bound to
signed official source bundles or registered for generation.

## Release boundary and next work

All artifacts in this report remain `train_ready=false` and
`production_eligible=false`. Production/KMS-qualified rows remain zero, so no
new HF upload is authorized. The 48/210 expansion remains blocked behind at
least 12 complete source-bound executable worlds and independent production
trust.

The next throughput work is a signed remote-identity receipt and a
content-addressed packing/reference cache. The current checkpoint stores raw
Git extraction only; a transient GitHub API failure after packing caused an
otherwise valid full resume to repeat several minutes of deterministic work.
Caching must preserve exact source/policy/client/tokenizer identities and may
not turn a network failure into a skipped proof.
