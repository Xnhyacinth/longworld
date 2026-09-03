# P19 IETF OAuth cross-specification preflight (2026-09-03)

## Decision

**Source capacity passes, generation remains blocked.** The official OAuth
requirement graph supplies 140,405 unique Qwen tokens after collapsing the
draft-29/RFC 9700 `published_as` identity. This is enough source mass for both
64K and 128K. No candidate was generated because the current IETF adapter and
task compiler cannot faithfully represent or replay this cross-specification
operator.

## Research question and constraints

Can an official IETF update/dependency graph support a deterministic long-world
task that resolves the effective OAuth requirements at a fixed cutoff, rather
than another near-identical draft revision comparison?

The preflight used only pinned HTTPS responses from IETF Datatracker, the IETF
draft archive, and RFC Editor. It preserved exact response hashes and observed
times, counted each published specification once, did not add unrelated RFCs,
and did not relax exact-band, near-duplicate, provenance, or promotion gates.

## Selected graph and source evidence

- RFC 9700, published January 2025, explicitly **updates** RFC 6749, RFC 6750,
  and RFC 6819. It does not claim to obsolete them; treating it as a replacement
  would be incorrect.
- Datatracker binds `draft-ietf-oauth-security-topics-29` to RFC 9700 with a
  unique `became_rfc` relation. The draft and RFC therefore count as one source
  identity even though their normalized word quick-ratio is 0.895262.
- Datatracker records RFC 6749, RFC 6750, RFC 6819, and RFC 8414 as normative
  references of the draft. RFC 7636 (PKCE) and RFC 9207 (`iss`) are informative
  references, but they are executable dependencies of normative requirements
  in RFC 9700 and are used by the proposed oracle rather than as padding.
- Primary records: [RFC 9700](https://www.rfc-editor.org/info/rfc9700),
  [draft history](https://datatracker.ietf.org/doc/draft-ietf-oauth-security-topics/history/),
  [RFC 6749](https://www.rfc-editor.org/info/rfc6749),
  [RFC 6750](https://www.rfc-editor.org/info/rfc6750),
  [RFC 6819](https://www.rfc-editor.org/info/rfc6819),
  [RFC 7636](https://www.rfc-editor.org/info/rfc7636),
  [RFC 8414](https://www.rfc-editor.org/info/rfc8414), and
  [RFC 9207](https://www.rfc-editor.org/info/rfc9207).

Every retrieval URL, SHA-256, observation time, relation-object hash, and oracle
evidence span is recorded in the machine-readable ledger.

## Exact capacity

Tokenizer: `Qwen/Qwen3.5-4B` at
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, local-only, no special tokens.

| Record | Exact tokens | Capacity treatment |
| --- | ---: | --- |
| draft-ietf-oauth-security-topics-29 | 35,558 | excluded: same `published_as` identity as RFC 9700 |
| RFC 6749 | 36,504 | counted |
| RFC 6750 | 9,321 | counted |
| RFC 6819 | 36,239 | counted |
| RFC 7636 | 10,044 | counted |
| RFC 8414 | 12,979 | counted |
| RFC 9207 | 5,103 | counted |
| RFC 9700 | 30,215 | counted |

- Naive total including the published draft: 175,963 tokens.
- Published-identity-deduplicated total: **140,405 tokens**.
- Near-deduplicated total at 0.90: **140,405 tokens**; no pair of distinct
  published RFCs crossed the threshold.
- Margin over the 64K lower bound: 74,869 tokens.
- Margin over the 128K lower bound: **12,405 tokens**.

This is a capacity result, not an exact-band packed-row result. Exact packing,
truncation quality, task-view audit, selection, and promotion remain unrun.

## Proposed deterministic oracle

At cutoff `2025-01-31T00:00:00Z`, resolve the effective requirement vector for
a fixed public authorization-code client deployment. The executable outputs are:

1. redirect URI component/prefix match -> fail; RFC 9700 requires exact match;
2. bearer token in URI query -> fail; RFC 9700 upgrades the RFC 6750 baseline;
3. public refresh-token rotation -> pass;
4. PKCE with S256 -> pass under RFC 9700 plus RFC 7636;
5. RFC 8414 metadata issuer equality -> pass;
6. multi-AS RFC 9207 issuer comparison -> pass.

The answer is a fixed six-field enum vector, not free-form prose. Removing RFC
9700 changes the first four effective modalities to the older conditional,
discouraged, described-but-not-universal, or unknown states. Removing any one
of RFC 6749/6750/6819/7636/8414/9207 makes its corresponding branch unknown;
removing `published_as` makes the cutoff lineage ungrounded. The ledger binds
12 exact evidence spans for these transitions.

## Fail-closed blocker

The current adapter rejects the untouched official source before a manifest can
be built:

`ProvenanceError: RFC body does not uniquely bind its URL identity`

Root cause: RFC 6750 has five matches under the current identity regex (the
title header plus four standalone page-header lines), while the parser requires
exactly one. All other selected RFCs have one match.

Even after that parser defect is fixed, three independent gaps remain:

- target closure recognizes only `published_as` and RFC header
  `Updates`/`Obsoletes`; dependency-only RFC 7636/8414/9207 cannot close;
- the signed-manifest relation allowlist excludes Datatracker `refnorm` and
  `refinfo` relations;
- the only IETF task compiler resolves one draft-revision normative hunk, not a
  cross-specification update/dependency DAG.

Accordingly `do_not_generate=true`, `hybrid_train_ready=false`, and
`production_eligible=false`. The next implementation slice is narrowly defined:
fix unique first-page RFC identity binding, add byte-bound dependency relations,
then implement and test the six-field resolver plus remove-one replay before any
64K/128K candidate generation.

## Reproduction

```bash
uv run python scripts/fetch_ietf_workflow.py \
  --request configs/p19_ietf_oauth_security_requirement_preflight_v1.json \
  --out /tmp/p19_ietf_oauth_security_requirement_preflight

HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/workspace/wynckeliao/.hf \
  uv run python reports/p19_ietf_oauth_requirement_capacity_preflight.py \
  --source-dir /tmp/p19_ietf_oauth_security_requirement_preflight
```

Machine ledger:
`reports/p19_ietf_oauth_security_requirement_capacity_preflight_v1.json`.
