# P19 ResearchLab review-response-revision preflight

## Research question and constraints

Can one public scholarly record support a new executable ResearchLab world whose
state machine is `review fan-out -> author response -> revised artifact/delta ->
claim disposition`, rather than the admitted arXiv revision-history and section-
reconciliation operators?

Admission requires official frozen source bytes, exact note and artifact-version
identities, deterministic claim/span joins, an answer-changing remove-review
counterfactual, an answer-changing remove-delta counterfactual, and a pinned-Qwen
unique source pool that can fill exact 64K and 128K bands after byte and 0.90
near-duplicate collapse. Model-written gold, search snippets, current-PDF-only
inference, unrelated paper text, and renamed revision tasks are excluded.

## Decision

`BLOCKED_ACCESS`; no generation was attempted. The bounded official OpenReview
API v2 request returned HTTP 403 `ChallengeRequiredError`. The repository fetcher
failed closed with `OpenReview request failed with HTTP 403` and published no
partial inventory. With zero admissible review or response records, relation,
oracle, counterfactual, and capacity measurements are undefined, not zero. This
candidate contributes no world, row, 64K sample, or 128K sample.

## Primary-source comparison

| Source | Expected evidence | Observed result | Admission effect |
| --- | --- | --- | --- |
| OpenReview official API v2 definition (`https://docs.openreview.net/reference/api-v2/openapi-definition`) | Public note records with immutable IDs, invitation roles, timestamps, content, and reply targets | The live notes endpoint required an interactive challenge and returned no notes | Hard access blocker |
| Official `openreview-py` examples (`https://github.com/openreview/openreview-py/blob/master/examples.md`) | Guest read-only clients can read public notes | Contradicted operationally in this environment by HTTP 403 challenge enforcement | Guest-read documentation is insufficient evidence of retrievability |
| Requested official forum (`https://openreview.net/forum?id=n2qXFXiMsAM`) | Submission, reviews, author responses, and possibly revised-artifact history | Neither forum HTML, PDF, nor API note bytes were admitted; search-index renderings were not substituted | Review fan-out and response fan-in remain unverified |
| Existing LongWorld arXiv records for `2203.01928` | Authentic manuscript revisions already used by an older revision workflow | They do not prove which review claim caused which response or revision delta in this forum | Excluded from capacity as unrelated padding until a source-proven cross-record relation exists |

The official documentation and the live endpoint therefore disagree at the
operational-access layer: guest reads are documented, but this execution context
receives a human-verification challenge. It is unknown whether an approved
authenticated session would remove the challenge; no authentication, challenge
bypass, cached surrogate, or third-party OpenReview dump was used.

## Reproducible access receipt

- Request: `configs/p19_researchlab_openreview_revision_reconciliation_preflight_v1.json`
- Request SHA-256: `1fb5f06dfb7dba45abcd74785320f5f05a23cf3707587f12dd8acdb028f6a2fa`
- Endpoint: `https://api2.openreview.net/notes?forum=n2qXFXiMsAM`
- Observed at: `2026-09-03T17:09:20Z`
- HTTP status / content type / bytes: `403` / `application/json; charset=utf-8` / `275`
- Response SHA-256: `1e81218341c7dba7634da77f2673cb811a151e10491fa81bbe32755f3ae9217c`
- Response error: `ChallengeRequiredError`, request ID
  `2026-09-03-7868300`
- Machine-readable ledger:
  `reports/p19_researchlab_openreview_revision_reconciliation_access_ledger_v1.json`

Reproduction command:

```bash
uv run python scripts/fetch_paper_workflow.py \
  --request configs/p19_researchlab_openreview_revision_reconciliation_preflight_v1.json \
  --out-dir /tmp/p19_researchlab_openreview_preflight_source_inventory
```

Observed terminal error:

```text
longworld.core.provenance.ProvenanceError:
OpenReview request failed with HTTP 403
```

The challenge request ID and response digest can change across attempts; the
stable acceptance condition is an HTTP 200 JSON response whose forum and note
identities pass the existing parser, not equality to the recorded 403 body.

## Capacity and adapter findings

Pinned tokenizer intent is `Qwen/Qwen3.5-4B` at revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, local-only, without special tokens.
No exact- or near-deduplicated capacity number is reported because the admissible
composite source set could not be constructed. Tokenizing the already available
arXiv paper while the review/response/revised-artifact records are absent would
measure an older manuscript-revision pool, not this task, and would permit
unrelated paper text to masquerade as review-dependent capacity.

There is also a distinct implementation gap after access is restored. The
current paper consumer safely recognizes an OpenReview submission root,
`Official_Review`, and `Author_Response`, deriving only `reviews` and
`responds_to` edges. It does not ingest immutable note-edit/PDF version history,
derive old-to-new artifact deltas, bind a review claim and response span to that
delta, or execute claim dispositions. Therefore an HTTP 200 response alone
would remain a source preflight, not a train-ready world.

## Next admission step

Obtain an approved non-interactive OpenReview read path or a publisher-signed
official export, then rerun the exact bounded request. Before changing the
adapter, require the returned forum to contain at least two substantive official
reviews, author responses tied to their exact note IDs, and two immutable
submission artifact versions with a source-proven delta. Only then measure
byte/near-deduplicated Qwen capacity and proceed if both 64K and 128K have
natural headroom after reserving relation and question overhead. Otherwise keep
the track closed and select another official record source.
