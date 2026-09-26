# P116 book speaker-set intersection: v3 candidate, v1/v2 diagnostics

**Local research candidate; `train_ready=false`.** The first P116 v1 unified
shard, `data/candidates/p116_book_intersection_unified_v1`, is **quarantined
in full**. Independent source review found one definite wrong gold:
`book-intersection-9901a998dd21060e2d9c-v1` omitted the printed speaker
`the Prince` in both Chapter XXVI and Chapter XXXII. The P113 precise parser
does not recognize an article plus title; a broad unknown-name scan marked
35/42 v1 readers as *potentially* affected. That count is conservative, not
35 confirmed errors. No v1 rows should enter an index or training export.

The intermediate v2 shard produced 12 readers, but its compiler was edited
after artifact creation; independent `--verify-only` found a byte mismatch in
`candidate_train.jsonl`. V2 is **diagnostic and not eligible for integration**.
The current frozen **v3** candidate is
`data/candidates/p116_book_intersection_unified_v3` (manifest SHA-256
`623309fc50b7976a40506c521e2a50b95bfa5ef5347d20a5cc7f466da4a7ac83`).
It is a second operation on the 58 frozen P114 Gutenberg source worlds, not
new books or new domain labels. The task asks for the complete intersection of
exact printed speaker names in two chapters. A name counts only where a
single-line quotation is directly tagged with a declared speech verb. Aliases,
implicit dialogue and character coreference are outside this syntactic answer
contract. The full original chapter text remains visible in the reader.

| v3 stage | Observed result |
| --- | ---: |
| Frozen books / all chapter pairs | 58 / 25,664 |
| Structural candidate chapter pairs | 3,784 |
| Final independent tasks and views / productive books | 34 / 34 / 18 |
| Train / eval | 27 / 7 |
| Final-chat 32–64K / 64–128K / 128–256K | 10 / 19 / 5 |
| Total final-chat / assistant-supervised tokens | 2,748,489 / 217 |
| Answer cardinality one / two / three | 28 / 5 / 1 |

The complete 25,664-row `compile_ledger.jsonl` includes every initial
rejection and every candidate's compilation outcome. Among the 3,784
structural candidates, v3 vetoed 2,623 pairs for a plausible unknown or
article-titled speaker, 694 for possible omitted support of a known label,
11 for complete attribution parser disagreement and two for insufficient
final-token distance. Four hundred eighteen pairs were not
materialized after the deterministic three-task-per-book cap; these are
**unexamined capacity**, not semantic failures. `unknown_tag_evidence.jsonl`
gives quote and adjacent tag excerpts by chapter: 981 article-title and
833 other possible-name evidence *occurrences*, including uncertain cases.
The v3 generic edge scanner requires adjacent verb/name syntax and rejects
article-titled labels such as `the Prince`; it also avoids v2's known false
positives from distant capitalized words after `he/she said`. The gate still
favors answer correctness over throughput.

For admitted tasks, two precise parsers agree on all tagged attributions in
both selected chapters. The reader is re-parsed without gold; all equivalent
recognized supports for **each shared printed label** are deleted from each
chapter in turn, and one non-member from each side is inserted into the other
chapter. Answers must change as specified. The final tokenizer records all
recognized shared-label support spans, query span, length and assistant-only
loss mask. The closest observed span across **recognized shared positive-label
supports** is 18,654–134,357 tokens (median 59,292). This is not a complete
proof over every natural-language alternative, every negative candidate or a
model-gain claim.

The v3 standard unified shard contains `candidate_train.jsonl`,
`candidate_eval.jsonl`, `sample_index.jsonl`, `manifest.json`, `proofs.jsonl`,
all-reader `mask_rows.jsonl`, `compile_ledger.jsonl` and
`unknown_tag_evidence.jsonl`. The compiler SHA-256
`aca51cff1b8d7dadc08c5fa1b4ff2fc32d731edd9d12c7735c7e1683c64b7fb5`
is pinned in both screen and unified manifests. The 34/34 final-chat mask
audit, two complete frozen byte replays, focused pytest and Ruff checks passed.
Independent adversarial review of v3 has found no definite wrong gold in its
first pass; final source/token review is pending. Research screening and
model-readout remain necessary. The data directory is
an immutable local candidate artifact and is not tracked in Git.

```bash
cat data/candidates/p116_book_intersection_screen_v3/manifest.json
less -R data/candidates/p116_book_intersection_unified_v3/compile_ledger.jsonl
less -R data/candidates/p116_book_intersection_unified_v3/unknown_tag_evidence.jsonl
less -R data/candidates/p116_book_intersection_unified_v3/proofs.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p116_book_speaker_intersection.py --config configs/p116_book_speaker_intersection_v3.json --phase screen --output data/candidates/p116_book_intersection_screen_v3 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p116_book_speaker_intersection.py --config configs/p116_book_speaker_intersection_v3.json --phase compile --screen-dir data/candidates/p116_book_intersection_screen_v3 --output data/candidates/p116_book_intersection_unified_v3 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p116_book_speaker_intersection.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p116_book_speaker_intersection.py tests/test_p116_book_speaker_intersection.py
```
