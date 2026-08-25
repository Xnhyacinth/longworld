# LongWorld trainable-data contract

LongWorld produces two trainable products and one non-trainable diagnostic product. A long context is never accepted because it merely reaches a token cap.

## Products

| Product       | Objective                                                   | Required unit                                                                              | Forbidden                                                                                                 |
| ------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| WorldLong-SFT | Learn grounded long-context answers and evidence use        | Verified question, answer, counterfactual twin, causal/supporting evidence, hard negatives | Random concatenation, answer-only metadata replay, inherited length labels, physical duplicate upsampling |
| WorldLong-CPT | Learn the language and evolution of coherent long workflows | Chronological or provenance-linked documents from one workflow                             | QA coaching text, unrelated background fill, mixed workflow IDs, synthetic pulse/prose fill               |
| Diagnostic    | Test generator/oracle behavior only                         | Any explicitly labelled legacy/debug sample                                                | Counting the sample as train-ready SFT/CPT data                                                           |

## Artifact classification

Every production artifact must carry all of these explicit fields:

- `source_origin`: `synthetic_world`, `real_public`, `real_private_export`, or `real_derived`.
- `workflow_kind`: `synthetic_executable`, `real_source_derived`, `hybrid_causal`, or `background_only`.
- `workflow_id`: stable case/repository/paper/RFC lineage identifier.
- `provenance_id`: full content hash or a lineage record that resolves to it.
- `evidence_role`: assigned per question/context as `causal_gold`, `causal_supporting`, `structural_hard_negative`, or `natural_background`.

`evidence_role` is context-relative. The same source document may support one task and be irrelevant to another; the packed record, not only the source artifact, records the final role.

## Allowed composition

Production SFT/CPT accepts only causal timelines, provenance graphs, same-case dossiers, or counterfactual twins. `random_concat` is always rejected.

Real text can contribute in three ways:

1. A fact parsed from its body changes executable state or the answer.
2. A provenance-linked document supports a causal fact without directly containing the answer.
3. A same-entity/version/workflow document is a structural hard negative.

Generic public text may be diagnostic background, but it does not increase causal-token, workflow-history, proof-depth, or semantic-scale metrics.

## Pipeline

```text
fetch/export real records
  -> verify URL/license/timestamp/parser/full hash
  -> parse body facts and document relations
  -> build one workflow/provenance graph
  -> simulate or attach executable state transitions
  -> render natural records without solver coaching
  -> verify text semantics + strict world execution + CF replay
  -> pack only linked workflow evidence/HN
  -> recompute metrics for every emitted view
  -> deduplicate and create explicit holdout splits
  -> export SFT or CPT, never an implicit mixture
```

Fetch or manifest verification failures are fail-closed. Existing files may be retained only as diagnostic fixtures; stale files are never silently promoted to verified real data.

The engineering probe uses distinct HMAC-SHA256-v2 keys and key IDs for source,
candidate, ranker, auditor, promotion, and report roles. The signature binds its
purpose, role, key ID, environment, and complete canonical object. It refuses
legacy shared-key signatures, cross-role key reuse, and an identity different
from the configured verifier. This is process separation, not a production
trust boundary: HMAC verification still exposes signer secrets. Production
requires asymmetric/KMS-backed signing with rotation and revocation so
consumers and training launchers hold public verification material only. Plain
hashes prove self-consistency, not source authority.

## Minimum production gates

- Counterfactual context replay equals its stored label.
- Corrupting or reversing the evidence text breaks semantic sufficiency.
- No single essential or non-essential document solves a multi-document task by itself.
- Every contiguous 4k/8k/16k window and BM25/lexical-TFIDF top-k baseline remains insufficient when the sample is labelled long-dependent. Training approval also requires the pinned external dense-embedding top-k audit and strict prefix replay.
- Semantic sufficiency and strict executable sufficiency are reported separately.
- View-local token length, evidence span, evidence-to-query distance, dependency class, and verification are recomputed from that view.
- Exact duplicate conversations are rejected; weighting is metadata/sampler state, not copied rows.
- 64k to 128k to 256k growth must add event-bearing workflow content. Generic background is not allowed to supply more than 20% of an increment in a production mix.

## Scale and evaluation

Primary scale counts are unique base tasks, executable proofs, source relations, answer programs, workflows, and held-out compositions. Rows created by length, timing, or view multiplication are exposure counts only.

Evaluation has separate holdouts for:

- world/entity instances;
- topology/operators;
- source/document families;
- domain compositions.

Model claims require matched unique-token budgets, length-stratified results, general-capability retention, closed-book controls, and external long-context benchmarks. Generator gates alone do not establish a model improvement.

## Current production status

`configs/p3_valid.yaml` is the completed strict local 12-world probe. Fresh
scanner-v2 export produced 80 allowlisted public episodes and 2,232 records.
The 2026-08-24 run generated 574 candidates from 20 worlds, accepted 574/574 in
the pinned dense/strict audit, and promoted 346 rows from 12 world-atomic groups
(10 train / 2 eval). Its green post-gate receipt includes 28 hybrid
real-workflow rows and six exact-tokenizer 64K rows. The signed B1/B3/B5/B5w
training export is manifest-validated, but remains a local HMAC engineering
probe rather than a production release. The candidate report binds its complete candidate row set;
the final signed report proves every promoted `candidate_sha256` belongs to
that set, then binds the exact promoted row-set digest and observed row/world
counts. The final gate requires a signed, versioned release profile that fixes
world scale and every release threshold; CLI flags cannot relax those values.
The 48/210 stages remain blocked on their larger real-source/domain quotas and
production trust upgrade; they may consume the green predecessor receipt but
cannot treat it as production authorization. Production requires
asymmetric/KMS-backed trust roots, independent policy enforcement, and
production scanner/supply-chain controls; unpublished HMAC keys are not
sufficient. Model training/evaluation has not yet established a long-context
capability gain. Topology,
source-family, and domain-composition holdouts must be exported as separate
evaluation products rather than mixed into one split.

Scale promotion consumes a separate post-gate receipt, not the earlier signed
train-ready report. That receipt binds the exact report/train/eval hashes,
profile digest, gate revision, green metrics digest, and observed world/row
counts. Its original environment and auditor key ID are explicitly pinned so a
probe receipt can be verified during the later production-stage selection
without re-signing predecessor evidence.

SFT and CPT exports are separate. `scripts/export_llamafactory.py` produces the
signed, profile-bound SFT transform. The in-repo single-GPU trainer is an
unsigned diagnostic path and cannot create a release manifest.
`scripts/export_cpt.py` accepts only records explicitly labelled `cpt`. It
validates chronological timestamps, full record hashes, predecessor edges, and
classification identity, then reconstructs `document_context` from at least two
records in one connected workflow. It rejects SFT prompts, self-reported context
mismatches, background-only artifacts, random concatenation, and exact duplicate
texts.

The historical 80-episode bundle predates the current role-bound attestation
and remains diagnostic only. It was not repaired or re-signed by inserting
metadata. The active `public_repo_episodes_v2.json` was freshly re-exported from
the allowlist under scanner-v2 and contains 80 episodes / 2,232 records, but it
still covers only two repositories and is dominated by one repository family.
Generic arXiv
full text is not promoted using a blanket arXiv
distribution label; paper revision/review/benchmark/reproduction data requires
a dedicated per-record export with an explicit reuse license. Broader source
families remain release blockers, not hidden sources of synthetic scale.

Additional release blockers are a versioned source-identity registry with
pinned revisions/digests, repository authorization plus secrets/PII review,
bounded or streaming SFT ingestion, immutable model/dependency revisions, and
production-pinned external dense-retrieval rankings. The local probe approval
record is self-consistent and explicitly non-independent; 48/210 must bind an
independently issued approval digest plus trainer configuration, model revision,
dependency/container lock and source commit/tree into the release manifest.
Remote model code is disabled, but that does not replace revision pinning or
supply-chain review.
