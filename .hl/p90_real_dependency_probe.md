# P90 frozen real-source dependency yield probe

This is an offline, hash-pinned probe of the already frozen P89 Wikipedia pool and P86 paper-revision batch. It reruns the existing conservative typed-row JOIN index against all 133 frozen Wiki titles, then isolates pairs involving the 12 P89 pages. It also checks whether each new page has the existing closed `Established` table shape required by `wiki_table_scan`. Paper counts are verified against the existing P86 batch files; no new paper task is claimed.

Result: **zero new real L2/L3 tasks** in this bounded probe. Five of the 12 new pages have parsable typed rows (151 total); none has a closed `Established` table. One exact-name overlap pair involving a P89 page was found, but it joins architecture and biology sources and fails domain/topic compatibility. The full frozen index has 10 overlap pairs: seven prior-task duplicates and three incompatible-domain/topic pairs. The P86 paper batch has 20 revision pairs, 11 with a qualifying prose change and seven previously admitted tasks. Its 13 rejected pairs comprise nine without qualifying prose, three with alternate exact support and one under the minimum length.

The result is deliberately narrower than “no real dependency is possible.” It shows that the currently implemented closed-year scan and exact two-table JOIN cannot extract **new** tasks from this frozen addition while preserving their source and split requirements. The seven P86 paper tasks are real revision-alignment tasks already in the candidate bank; they are not newly generated here. No mask check or train export is run because the new-task count is zero.

Concrete source criteria for the next intake:

- For exact two-table JOIN, freeze at least two same-split, same-domain/topic pages that share several clean entity names; one must have a unique selector and the other a different target attribute with alternatives. Preserve a second-page value that is not already visible in the first page.
- For the existing complete-set scan, freeze a bounded table with a clean name column and plain four-digit `Established` values for at least eight eligible rows, including misses and exclusions. A generalized numeric scan for height or elevation would need a new, separately validated compiler; these P89 pages alone do not establish a complete or correctly normalized numeric universe.
- For paper revision QA, prioritize version pairs with substantive unique prose changes and enough full source text to meet the length target. Repeated answer text and cosmetic diffs should continue to be rejected.

Inspect the exact page and pair records:

```bash
cat data/candidates/p90_real_dependency_probe_v1/manifest.json
cat data/candidates/p90_real_dependency_probe_v1/wiki_pages.json
cat data/candidates/p90_real_dependency_probe_v1/new_page_pairs.json
cat data/candidates/p90_real_dependency_probe_v1/paper_support.json
cat data/candidates/p90_real_dependency_probe_v1/wiki_join/pair_audit.jsonl
```

Reproduce in a new output directory:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/probe_p90_real_dependency_sources.py --config configs/p90_real_dependency_probe_v1.json --output-dir data/candidates/p90_real_dependency_probe_replay
UV_LINK_MODE=copy uv run --offline python -m pytest -q tests/test_probe_p90_real_dependency_sources.py
```

The receipt is a research probe (`train_ready=false`), not a data release or evidence of model improvement.
