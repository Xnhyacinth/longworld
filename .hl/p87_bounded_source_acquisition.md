# P87 bounded public-source acquisition

The P87 route is a metadata-first, work-level source-capacity probe. It queries six arXiv categories one at a time, spaces requests at least 3.2 seconds apart, excludes ten already-used paper IDs, assigns train/eval by a stable hash of the **work ID**, and caps source acquisition at four works/eight archives. If a cohort is discovered, it hands the chosen revision IDs to the existing provenance-preserving `fetch_paper_workflow.py` at the same request rate and records each source archive's hash. It then runs the P86 prose-hunk **capacity** probe; source characters and prose changes are not counted as accepted reader tasks. The route records any available license URI and otherwise marks the Atom feed's license status as unreported. Content use is local research only.

The live run did **not** acquire sources. The first `cs.CL` query returned HTTP 406 twice on spaced attempts; the second returned an empty response body (SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`). The final receipt at `data/capability_records/p87_arxiv_bounded_acquisition_v2/manifest.json` reports zero metadata pages, zero discovered or selected works, zero planned/acquired archives, and `train_ready=false`. The 406 cause is not established. No fallback sources or domain counts were invented.

The pacing and local research restriction follow [arXiv's API terms](https://info.arxiv.org/help/api/tou.html); the query/paging and version rules come from the [API manual](https://info.arxiv.org/help/api/user-manual.html). The API may change, so this frozen route requires a successful live metadata response before any source-capacity claim. Offline tests exercise Atom parsing, work deduplication, split assignment, request spacing, limits, and failure receipts. Three tests passed; Ruff check and format passed.

Inspect and rerun:

```bash
cat data/capability_records/p87_arxiv_bounded_acquisition_v2/manifest.json
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_p87_arxiv_acquisition.py
.venv/bin/ruff check scripts/acquire_p87_arxiv_sources.py tests/test_p87_arxiv_acquisition.py
PYTHONPATH=. .venv/bin/python scripts/acquire_p87_arxiv_sources.py --config configs/p87_arxiv_bounded_acquisition_v1.json --output-dir data/capability_records/p87_arxiv_bounded_acquisition_retry --fetch-sources
```

The last command is a new network attempt and should be used only after the 406 cause is understood; it is not needed to review this wave's result.
