# P112 real-book cross-chapter reader pilot

The pilot freezes three complete Project Gutenberg texts with raw/body SHA,
landing URL and source-group split: *Alice's Adventures in Wonderland*
(train), *Pride and Prejudice* (eval) and *The Adventures of Sherlock Holmes*
(train). The source manifest records Project Gutenberg's US public-domain
policy; it does not assert worldwide redistribution or training rights.
These three works are manually chosen seeds, not automated book-topic scale.
The compiler itself accepts a source-manifest list and extracts chapters and
explicitly attributed quotations without hard-coded character names.

The task first binds the speaker of an exact quotation in one chapter, then
asks for that speaker's last explicitly attributed quotation in another.
The reader contains original chapter text. Candidate admission requires two
different chapters, a unique source quotation, a unique target answer across
the full frozen book, a competing explicitly attributed speaker in the target
chapter, source-attribution swap changing the answer, deletion of source
attribution making it unsolved, deletion of all equivalent target quotations
making it unsolved, and an assistant-only final-chat mask. The question cannot
print the speaker or target answer. These are literal attribution tasks;
implicit speech, motives, themes and broader narrative inference are outside
this pilot's truth contract.

Three-process compilation yields **20 independent semantic tasks**: five
Alice, seven Pride and eight Holmes; 13 train and seven eval, with work-level
split. All 20 have a directly measured source-to-target evidence envelope of
at least 16,384 tokens. Observed lineage extent is 17,240–126,221 tokens
(median 58,089.5); last-evidence-to-question gap is 425–72,366 (median
5,977). Physical final-chat length bins are seven 32–64K, nine 64–128K and
four 128–256K. Source/native audit, unified conversion and separate all-row
mask replay each passed 20/20. These numbers measure the executed evidence
path, not an exhaustive shortest natural-language proof.

Concrete cases for inspection:

* `book-cross-3054d0275d8446744e38-v1`: Alice Chapter I → XII;
  source quotation tokens 2,322–2,330, target 37,150–37,160.
* `book-cross-40a1a72d0654c9114e73-v1`: Alice Chapter I → X;
  source quotation 2,518–2,529, target 29,991–30,003.
* `book-cross-8e76cb972bd5373ee10d-v1`: Alice Chapter II → IX;
  source quotation 3,336–3,344, target 28,830–28,841.

The current intake is small and literary English only. A scalable source
planner should consume Gutenberg's machine-readable catalog, sample by
language/subject and chapter/speaker capacity, then rate-limit/freeze text
and retain license and rejection ledgers. Catalog rows would be source
proposals, not admitted worlds or tasks. No model training or gain is claimed;
all artifacts remain `train_ready=false`.

```bash
cat data/sources/p112_books_v1/manifest.json
cat data/candidates/p112_book_native_v5/manifest.json
cat data/candidates/p112_book_unified_v3/manifest.json
cat data/candidates/p112_book_unified_mask_v3/manifest.json
less -R data/candidates/p112_book_native_v5/audit.jsonl
less -R data/candidates/p112_book_unified_v3/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p112_book_audit.py --source-dir data/sources/p112_books_v1 --native-dir data/candidates/p112_book_native_v5 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p112_book_to_unified.py --source-dir data/sources/p112_books_v1 --native-dir data/candidates/p112_book_native_v5 --output data/candidates/p112_book_unified_v3 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p112_book_unified_mask.py --unified-dir data/candidates/p112_book_unified_v3 --output data/candidates/p112_book_unified_mask_v3 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p112_book_tasks.py
```
