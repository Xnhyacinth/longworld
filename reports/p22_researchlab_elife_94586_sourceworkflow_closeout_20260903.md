# P22 ResearchLab eLife 94586 privacy/sourceworkflow closeout

## Result

The P21 eLife v1/v2 inventory now redacts all six public email strings (four in
v1 and two in v2) with the existing `[redacted-email]` policy before any text
reaches `SourceRecord`.
Each version binds its exact redaction count, redacted-text SHA-256, original
source SHA-256, DOI, Git blob, repository commit, and parser. Original source
hashes remain lineage fields; they are not treated as hashes of redacted text.

The source adapter emits one two-record ResearchLab workflow with four distinct
source-grounded relations: `requests_revision`, `responds_to_review`,
`revision_of`, and `implements_revision_delta`. Relation evidence retains exact
post-redaction spans for the review, direct response, both version DOI elements,
and the `fig5`, `APP9`, and `tbl3` XML objects. Adapter entry requires explicit
`signed_bundle_authorized=true` and reruns the strict/remove-one task audit
before normalization.

## Vertical TDD

The new public test was written first. Initial RED was an import failure for the
missing adapter. The next behavioral RED found that a bare version DOI was not
a unique source span because subarticle DOIs share its prefix. Binding the full
article-meta version-DOI element fixed that ambiguity. A real-source run then
found the same substring issue for `id="fig5"` and `id="APP9"`; the evidence is
now the unique typed XML start tag instead of the loose attribute substring.

The final public path proves six-for-six email redaction, redaction hashes and
counts, raw-hash lineage separation, exact relation evidence spans, source
normalization, strict replay, and fail-closed rejection of a tampered task.

## Boundary

No candidate, dense audit, promotion, or training row was created. The exact
next blocker is a source-attested task-replay sidecar for this operator. Sidecar
and promotion code remain untouched in P22.B.
