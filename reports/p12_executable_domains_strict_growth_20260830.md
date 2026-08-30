# P12 executable domains and strict-growth checkpoint

Date: 2026-08-30

## Result

This checkpoint converts the Clinical, Regulation, and Cyber source inventories
into three real-source executable task candidates. Each candidate executes its
answer and counterfactual answer, fails remove-one and single-evidence tests,
checks answer disclosure on each evidence surface, and re-verifies the producer
source attestation with an explicit source-role key. They remain deliberately
classified as ignored, non-world candidates: they are not long-context views,
train-ready rows, complete worlds, or an HF release.

The local inventory is recorded at `data/task_candidates/MANIFEST.json`. The
three candidates contain 9 official source records, 8 source relations, and
55,640 Qwen source-body tokens when each domain's record bodies are newline
joined and counted without special tokens. Cyber supplies 49,725 of those
tokens; Clinical and Regulation are genuine but naturally short at 5,167 and
748 tokens. Candidate
JSON byte size is not used as a context-length claim because the task envelope
also carries provenance and attestation metadata.

| Candidate                  | Records / relations | Source-body tokens | Candidate SHA256                                                   | Result                                 |
| -------------------------- | ------------------: | -----------------: | ------------------------------------------------------------------ | -------------------------------------- |
| Clinical Veklury           |               3 / 2 |              5,167 | `755fbb5d8e137911301a0e12879d355de21c032465dd174a7bf32c8c1646fc76` | all 9 gates; source attestation valid  |
| FTC non-compete regulation |               4 / 5 |                748 | `cffb672626688d5edc184b08c7ed8cf0aa3cf8db575fc9fab4949ebf2689d325` | all 10 gates; source attestation valid |
| Log4Shell cyber            |               2 / 1 |             49,725 | `b7515643b7c6c0b6855b80a88d27318d8b00c027f2367bcf447ea9897af436fb` | all 8 gates; source attestation valid  |

The exact tokenizer is the installed immutable
`Qwen/Qwen3.5-4B@a7b0d22b993d71000cf2eadfb37222a67cee521e` snapshot.

## Correctness changes

- Clinical, Regulation, and Cyber now separate executable semantic proof from
  source authenticity. A coordinated rewrite can remain internally consistent,
  but cannot pass `source_attestation_verified` under the original producer key.
- Candidate bookkeeping is uniform: `candidate_task`, disabled generation,
  and every training, production, promotion, and complete-world flag is false.
- Clinical and Cyber receipts bind the exact newline-bearing candidate file;
  all three receipts record the exact candidate digest.
- IETF accepts multi-line substantive changes and same-modal wording changes
  only when local anchors and byte-bound revision evidence identify one change.
  A direct task without an authorized workflow binding no longer reports a
  valid binding. Bundle materialization now carries a producer-signed component
  authorization that binds the source-bundle digest, binding digest, adapter,
  workflow, and component; a recomputed public component hash is insufficient.
- CodeForge validates cumulative one/two/three release-cycle programs for
  16K/32K/64K, including nested evidence, increasing proof depth, increasing
  execution work, and positive source-record/link validation for authentic
  relations. A missing synthetic marker is not sufficient to turn a synthetic
  supersession edge into an authentic source relation.
- Dense audit signs replay-derived semantic tokens, support count, essential
  events, proof depth, and authentic relations. Selection and the final
  train-ready report both recompute cumulative growth from these replayed
  metrics. `internal` is the inclusive workflow-owned token count and
  `event_bearing` is its event-revealing subset, so both must grow independently
  without generic background masking. The density and cross-band gates count
  `internal` once, and signed audit/preflight validation rejects
  `event_bearing > internal`. Real hard negatives cannot satisfy a real-proof
  quota.
- Promotion uses `train-ready-promotion-v2` and release selection uses
  `longworld-release-world-selection-v2`; historical v1 rows and receipts must
  be regenerated rather than silently accepted. Final reports use
  `longworld-quality-binding-v4` so the new replay-growth check cannot be
  confused with older report semantics.

## Honest retention status

- Complete source-bound worlds under the current v2 contracts: **0 new**.
- New train-ready or promoted rows: **0**.
- Executable task candidates: **3**.
- The historical stricter-gate union contains 35 rows across 5 worlds and
  1,347,609 receipt-reported tokens, but it predates the mandatory promotion-v2
  replay-growth contract and is not counted as currently qualified v2 data.
- RFC 9421 v4 contains 4 primary records, 2 supporting records, 3 relations,
  and 282,883 newline-joined source-body tokens. Its real selected revisions do
  not uniquely select one strict normative task, so actual retention remains
  zero. The broader parser is covered by executable fixtures, not mislabeled as
  a retained real RFC task.
- The Deno lower-band diagnostic retains three 64K candidate views (196,488
  context tokens total, 0.601 mean real-source ratio) but no 16K/32K views. It is
  one incomplete diagnostic world, not a complete multiband world.

Because the count is below 12 complete source-bound worlds, unified promotion
was not run. No new data is eligible for HF upload.

## Next production work

1. Turn the three executable domain tasks into cumulative 16K/32K/64K histories:
   multiple regulatory actions and enforcement updates; vulnerability,
   remediation, release, and regression cycles; and trial, protocol, label,
   safety, and approval revisions. Each band must add source-bound events,
   essential/support evidence, proof depth, and authentic relations.
2. Acquire a narrower CodeForge release/CI episode whose one-cycle closure fits
   16K, then extend the same history to two and three cycles instead of
   truncating evidence.
3. Export an IETF revision family with one uniquely anchored real substantive
   change; retain zero if the real revisions remain ambiguous.
4. Build at least 12 complete worlds and run world-parallel candidate ranking,
   strict replay, signed audit, final replay-growth report, and filtering.
5. Keep 48/210 and HF publication blocked until that 12-world receipt is green.
