# P131 pinned Wiki HTML table campaign

P131 executed a five-chunk source-shape campaign over **all 98** frozen P119 Wikipedia pages. The acquisition plan is `data/candidates/p131_wiki_html_campaign_v1/plan.json`; the full gross-to-net manifest is `data/candidates/p131_wiki_html_campaign_v1/manifest.json` (SHA-256 `0a1c207092bd983650bcb1b34522c8352990ee2d918a5ac11f51ab8f01123bc6`). The runner is `scripts/p131_wiki_html_campaign.py` (SHA-256 `a61f064e884b26f0201ce455b1519679a9df58dd588c5c313c6098d3d8a5df51`). The plan pins the P119 source pool, source ledger and snapshots; each source HTML page pins exact oldid, bytes and receipt. One process performed HTTP requests at a global maximum of 0.5 attempts/second, with at most two 429 retries and a campaign stop on persistent rate limit. Two worker processes compiled only already frozen local HTML. No GPU was used.

The 98 pages are unique by title, oldid, revision and snapshot group. Five chunks of 20/20/20/20/18 pages preserve P119 split labels; per-chunk train/eval page counts are 3/17, 2/18, 20/0, 18/2 and 0/18. **86 pages were fetched, 12 exact P122 oldid HTML/receipt pairs reused, zero failed.** P122 found 477 gross wikitables and retained 144 strict grids. P126 compiled 38 complete-set QA from 18 pages. Fifteen of those are the exact P126 v4 sample IDs and semantic task keys already present in P130. The other 23 are from 12 source groups absent from both P126 v4 and all P130 candidate refs.

The authoritative **new-only** shard is `data/candidates/p131_wiki_html_new_only_v1/manifest.json` (SHA-256 `37d95b30356b22de1a6488e1bdc441b260bec23021692cd03e08fc011d6c8c8b`), compiled by `scripts/p131_wiki_html_new_only.py` (SHA-256 `b47f19577d51e5423dacf91373e26c6866251a1da88a0cd46cfaa54d1990601b`). Its `novelty_ledger.jsonl` records all 38 decisions; its 23 final readers, sample index, row audits and exact mask audits are independently materialized. Source group and task key overlap with P130 is zero. The new shard has **3 train / 20 eval**, spans **291–11,856 final-chat tokens**, and totals **59,437 chat / 465 supervised tokens**. Twenty-one answers contain only two names. Sports contribute 15/23; there are no culture or healthcare tasks. All 23 are short structured table scans, not verified remote dependency or natural long-document tasks. `train_ready=false`.

The campaign makes source acquisition and rejection accounting repeatable, but its low net yield and concentration show that merely fetching more category-list pages does not supply the diverse long-context supervision requested. The next expansion must target different source shapes and task operations; adding length padding or category labels to these 23 would not change their evidence dependency.

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p131_wiki_html_campaign.py --config configs/p131_wiki_html_campaign_v1.json --output data/candidates/p131_wiki_html_campaign_v1 --phase verify
UV_LINK_MODE=copy uv run --offline python scripts/p131_wiki_html_new_only.py --config configs/p131_wiki_html_new_only_v1.json --output data/candidates/p131_wiki_html_new_only_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python -m pytest -q tests/test_p131_wiki_html_campaign.py
cat data/candidates/p131_wiki_html_campaign_v1/manifest.json
cat data/candidates/p131_wiki_html_new_only_v1/manifest.json
less -R data/candidates/p131_wiki_html_new_only_v1/novelty_ledger.jsonl
```
