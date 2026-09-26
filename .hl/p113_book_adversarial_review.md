# P113 catalog-book adversarial gold review

The first catalog-driven book shard (`p113_book_native_v6`, `p113_book_unified_v3`) is quarantined in full. It produced 43 one-view semantic tasks, and both the producer and its quote-first auditor passed 43/43. A separate scan of each frozen target chapter found **five definite wrong gold answers** under the questions' own listed speech-verb and printed-name contract. The common error is a speech tag followed by a lower-case modifier or phrase. The two code paths use different extraction algorithms but share a narrow end-of-tag termination assumption. Source replay, reader masks and their mutual parser agreement did not catch it.

| Sample ID | Frozen source / chapter | Later explicit attribution omitted by both parsers |
| --- | --- | --- |
| `book-explicit-f72db90573fc9a3bce42-v1` | gutenberg-11, Chapter X | `“What trial is it?” Alice panted as she ran` |
| `book-explicit-3e5ba3a65a16ce15c488-v1` | gutenberg-69612, Chapter XX | `“What a topping place!” exclaimed Craddock enthusiastically.` |
| `book-explicit-e72a12727d8443bc5d0d-v1` | gutenberg-69612, Chapter XX | `… declared Heavitree when` after the later quotation |
| `book-explicit-b94402ad7754f483393e-v1` | gutenberg-33798, Chapter VIII | `… said Michael bitterly.` after the later quotation |
| `book-explicit-b29750267446add4fc8a-v1` | gutenberg-1447, Chapter XXIX | `“Have you any riding clothes?” Penelope whispered to him.` |

The reviewer scanned all 43 frozen target chapters with broader quote→verb→name, quote→name→verb and name→verb→quote forms, allowing lower-case suffix phrases, and inspected potential single-quote and cross-line forms. One nested Mat quotation was a false alarm; no other definite wrong gold was found within this bounded grammar. That is **not** a proof that the other 38 are globally correct. The source/work/author train/eval split was disjoint for the 13 productive books, and no direct gold/proof leakage was found in their reader questions.

No P113 book row appears in `p113_book_quarantined_refs_v1` or its selected pack. The earlier P113 13,634-view index and 1,218-view materialized selection remain historical diagnostics. A replacement version must fix both attribution paths, run independent full-source and final-reader tests, and pass a fresh adversarial scan before index inclusion. Changing the question to a narrower meaning merely to preserve the 43 rows would not resolve the stated user-facing task.

Inspect the failure and corrected bank:

```bash
less -R data/candidates/p113_book_native_v6/proofs.jsonl
less -R data/candidates/p113_book_unified_v3/sample_index.jsonl
cat data/candidates/p113_book_unified_mask_v3/manifest.json
cat data/candidates/p113_book_quarantined_refs_v1/manifest.json
cat data/candidates/p113_book_quarantined_selection_v1/manifest.json
```
