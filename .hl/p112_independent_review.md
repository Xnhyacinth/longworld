# P112 independent quality review and quarantine

A source/native replay and final assistant mask check passed all 20 P112 book rows, but both the producer and its audit used the same narrow `speeches()` parser. An independent target-chapter scan that included additional explicit attribution verbs and short quotations found **at least 12 of the 20 gold answers wrong**. The remaining eight are not certified correct. A second problem is speaker identity: `Holmes` and `Sherlock Holmes`, or `Bradstreet` and `Inspector Bradstreet`, can denote the same person while the current counterfactual treats them as different.

Affected sample IDs:

```text
book-cross-40a1a72d0654c9114e73-v1
book-cross-8e76cb972bd5373ee10d-v1
book-cross-09b6cd6d23ad9c2afcdb-v1
book-cross-63de2d12f47fe2f2b76e-v1
book-cross-dc3ce1ef653ce76dea18-v1
book-cross-d95a1e3fd087dc189638-v1
book-cross-298b2a40d5dc662e923d-v1
book-cross-5011e596e27c20c8035c-v1
book-cross-8368e186a50e6774cec2-v1
book-cross-c13356efa80489f24a26-v1
book-cross-8b480eafd8d7d22521b4-v1
book-cross-c8ce0d368a02641b9d7f-v1
```

For example, `book-cross-40a1a72d0654c9114e73-v1` selected an Alice quotation near character 3,603 in target Chapter X while a later explicit `“What trial is it?” Alice panted` appears near character 11,145. The narrow parser accepted only 25–130-character curly quotations followed by literal `said NAME`; the natural question did not disclose that restriction. All 20 P112 book rows are excluded from the corrected index and selected packs. P113 must use a broader, separately implemented attribution audit and reject unresolved aliases before any replacement enters the bank.

The P112 finance report route also has a narrower dependency guarantee than the initial wording implied. Its target-support deletion removes a `proof_cells` key while leaving model-visible `context` unchanged. Visible selector/target value edits do change the answer, but repeated equivalent values and other textual supports have not all been removed. In sample `p112:71eb4672aafaac986c6142f8f1f7491c594dc994e414d63b38427578edef0630`, target value 24,879 appears in several report chunks and 68,593 twice in another chunk. The observed evidence extent is an executed lineage span, not a proven shortest reader-text proof.

The corrected index and selection are documented in `.hl/p112_integrated_status.md`. The old P112 materialized packs remain immutable diagnostics, not an approved training release. This review is bounded: it does not certify all other candidate families or model learning value.

```bash
less -R data/candidates/p112_book_unified_v3/sample_index.jsonl
less -R data/candidates/p112_book_native_v5/audit.jsonl
cat data/candidates/p112_quarantined_book_refs_v1/manifest.json
cat data/candidates/p112_report_route_native_v5/final_audit.json
```
