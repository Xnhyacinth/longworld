# P20 IETF OAuth cross-spec requirement preflight (2026-09-03)

## Result

The public source and oracle vertical slice passes, but candidate generation is
not yet admissible. The official ledger builds 8 primary records, 2 supporting
records, 10 relations, and 12 byte-bound requirement evidence spans. Strict
replay, every evidence remove-one replay, and every relation remove-one replay
all pass. No candidate was generated, promoted, or counted.

The remaining blocker is downstream and explicit: the task replay sidecar
registry has no standards/IETF adapter. Registering that adapter touches the
shared candidate/promotion contract and is outside this vertical slice.

## Public behavior verified

- RFC identity is selected from the first page. The authentic RFC 6750 layout
  is accepted despite four later standalone `RFC 6750` references; a body with
  no first-page identity remains rejected.
- Datatracker `refnorm` and `refinfo` objects are byte-bound as
  `normative_reference` and `informative_reference` relations, respectively.
- Requested dependency targets close the selected OAuth graph. Header targets
  outside the request are not silently converted into missing relations: RFC
  6749's obsolete RFC 2012 and RFC 5849 targets are recorded explicitly as
  `not_requested` exclusions.
- The resolver returns a fixed six-field enum answer for redirect matching,
  bearer query transport, refresh-token rotation, S256 PKCE, metadata issuer
  equality, and multi-AS response issuer matching.

## Authentic replay receipt

- Fetch config: `configs/p20_ietf_oauth_cross_spec_requirement_fetch_v1.json`
- Source inventory: `data/source_inventory/p20_ietf_oauth_cross_spec_requirement_v1/`
- Fetch request SHA-256:
  `1abfd3682f62abc5bb8ecf87bf0f40706ce05efa19238015b082beeb27db3798`
- Fetch inventory SHA-256:
  `bb428790b2d0dd8c53d64244cf8bd770ab9ddaf9081c0518bf7696fab2f1d0d8`
- Derived manifest SHA-256:
  `93c02b3eb041aaac8978c644cc1d391e31e21559c7739d1716ab06a1a23b8ed8`
- Task SHA-256:
  `dfab2bf617e28c709ec5df81b532af40b0f56cb917bdfbeef784841d4ae5a504`
- Retrieval completed at `2026-09-03T17:34:08.267364Z`.

Relation counts are 3 `updates`, 4 `normative_reference`, 2
`informative_reference`, and 1 `published_as`. The answer is:

```json
{
  "redirect_match": "FAIL_EXACT_REQUIRED",
  "bearer_transport": "FAIL_URI_QUERY_PROHIBITED",
  "refresh_protection": "PASS_ROTATION",
  "pkce": "PASS_S256",
  "metadata_issuer": "PASS_EXACT_MATCH",
  "multi_as_issuer": "PASS_MATCHED"
}
```

## TDD and verification log

1. Identity RED: `uv run pytest -q tests/test_standardsworkflow.py -k
   'first_page_identity or only_paginated_identity'` failed 1, passed 1 because
   the old whole-body regex found three identities. The same command then
   passed 2.
2. Relation RED: the official P19 ledger replay reported
   `ProvenanceError: requested RFC relation target is missing`; after the
   selected relation implementation the adapter result was null.
3. Resolver RED: `uv run pytest -q
   tests/test_ietf_cross_spec_requirement.py` failed during collection because
   the public builder/audit symbol did not exist. It then passed.
4. Exclusion RED: the public builder test failed with missing
   `excluded_rfc_relation_targets`; it then passed while retaining requested
   `Updates` relations.
5. Scenario-binding RED: `uv run pytest -q
   tests/test_ietf_cross_spec_requirement.py -k six_fields` failed because a
   tampered `pkce_method=plain` did not raise. The fixed-scenario contract was
   then bound explicitly and the same command passed.
6. Final focused run: 44 tests passed across standards workflow, cross-spec
   requirement, and source-workflow adaptation. Ruff check and `git diff
   --check` passed; the three directly edited standards/test files pass Ruff
   format check.
