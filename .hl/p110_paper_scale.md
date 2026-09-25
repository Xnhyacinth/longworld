# P110 automatic arXiv paper-source scaling: bounded result

P110 tested whether official category breadth can feed the existing raw-TeX
reference and two-revision compilers without manually listing fields or paper
IDs. **No task from this wave passed the final semantic gate.** The acquired
sources and rejected candidates are capacity evidence, not training data.

The category input is the frozen HTML from the [official arXiv category
taxonomy](https://arxiv.org/category_taxonomy). The planner extracts dotted
category IDs, balances arXiv's eight subject groups, excludes queries already
used in P105, and deterministically shards the remaining queries into
P105-compatible metadata configs. The [official API manual](https://info.arxiv.org/help/api/user-manual.html)
is the request source. A 3.2-second minimum interval is enforced for each
metadata and source call. arXiv says article versions can carry different
licenses, and that free download does not by itself imply redistribution
rights; see [arXiv's license guide](https://info.arxiv.org/help/license/index.html).
No selected Atom entry reported a license URI. All P110 content is restricted
to `local_research_only_no_redistribution`; every shard remains
`train_ready=false`.

| Stage | Actual result | Interpretation |
| --- | ---: | --- |
| Official dotted categories / groups | 146 / 8 | Frozen taxonomy, not task coverage |
| Planned category queries | 30 in 3 shards | No per-field hand list |
| Successful Atom pages / entries | 24 / 2,300 | Third shard stopped on HTTP 429; 6 planned queries incomplete |
| Two-revision work proposals | 19 | One repeated across metadata shards |
| Novel source work cohort | 18, including 16 non-CS | 13 train / 5 eval by work-stable hash |
| Source packets actually frozen | 7 works / 14 revisions / 63,774,955 bytes | 6 non-CS; 1 PDF source rejected, 1 HTTP 429 stopped acquisition, 9 never attempted |
| Cross-file source-shape works | 1, non-CS `stat.CO` | 6 frozen works had no eligible cross-file link |
| Raw P96 reader / independent full mask | 1 / 1 | 51,866 full-chat, 5 supervised tokens; this is **not** admitted |
| Final P105 semantic quality | **0** | Only answer has another exact visible title occurrence |
| All-file target resampling | **0** | 76 references scanned; no target passed the same shortcut/alternate-support gates |
| P86 two-revision recipe | **0/7 pairs** | 6 no quality prose hunk; 1 alternate exact support |

The initially attempted P105 transport classified all 18 source requests as
`empty or exceeds size limit`. Its downloader checked body length before HTTP
status, so an empty 429 appeared as a source-quality rejection. P110 uses a
scoped adapter: an empty/non-200 response stops acquisition as a transport
failure, with at most two 90-second resume attempts. The second, immutable
source run recovered seven valid two-revision packets and distinguished one
actual `application/pdf` source from the later empty HTTP 429. The source
ceiling was 250,000,000 archive bytes. P105's post-work soft stop was set to
121,999,998 bytes, leaving the maximum two 64,000,001-byte responses as a
hard reserve. The run stopped at 63,774,955 bytes because of 429, not because
of the byte ceiling. Failed/unfinished metadata and source requests are
retained in separate ledgers; they are not counted as source-shape rejections.

The one P96 reader was a `cross_file_section_reference` at
`researchlab:arxiv:2607.08276`. Its final-chat evidence extent was 39,706
tokens, and the last evidence ended 3,829 tokens before the question. Native
reader replay and independent all-mask passed. The exact answer, `Data
Generating Process`, also occurs elsewhere in the visible source, so deleting
the target span does not establish information necessity. P105's unchanged
quality gate correctly rejected it. No curated shard exists. These token
numbers describe a rejected case and must not be used as an admitted long
dependency statistic.

The bounded resampler enumerated **all** `ref`/`autoref` occurrences in the
ten source files of that work, not only result-file references: 76 references,
20 unique cross-file targets, ten unique prose cues and six final-reader
resolutions. Of those six, one had an alternate visible title, two printed the
answer in the cue, and three exposed a short heading through the label. It
did not change the P96/P105 exact-answer, cue, label or reader rules. The
separate P86 revision support matrix found just one prose-hunk-bearing pair;
its proposed lines had alternative exact support. Neither operation yielded
an admitted task, so this wave does not demonstrate multi-operation learning.

Pinned artifacts to inspect:

The key receipt SHA-256 values are: taxonomy plan
`31107045972ae84f9498cde1babb6626a156b34408bd6556fceb9107d81bb4c0`,
source fetch
`8c14082c42a4422d24c2992483f3cb79e4f48d12692cf4ac8a8bf96271057fda`,
four-process probe
`95f8790185dd6fc4519d4ace846e2756e1c2643a51d046c7148973d97bd0fcd0`,
raw independent mask
`2811cf15030a3aea16c2d30abfa482e0d7b582e2de8399efffb65c29a7ab3403`,
and target resampling
`1a136580f47ae621f7a82484ebada6ac21fe28be3ca133a51508f515fc37744a`.

```bash
cat data/capability_records/p110_paper_autocatalog_v1/manifest.json
cat data/capability_records/p110_paper_cohort_v1/manifest.json
cat data/capability_records/p110_paper_sources_v2/manifest.json
less -R data/capability_records/p110_paper_probe_v1/capacity_index.jsonl
less -R data/capability_records/p110_paper_target_resample_v1/native_quality_ledger.jsonl
less -R data/capability_records/p110_paper_target_resample_v1/resample_ledger.jsonl
less -R data/candidates/p110_paper_revision_native_v1/rejected.jsonl
```

Reproduce the completed local checks from the project root:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_autocatalog.py --config configs/p110_paper_autocatalog_v1.json --output-dir data/capability_records/p110_paper_autocatalog_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_cohort.py --config configs/p110_paper_cohort_v1.json --output-dir data/capability_records/p110_paper_cohort_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_acquire.py --config data/capability_records/p110_paper_cohort_v1/acquire_config.json --output-dir data/capability_records/p110_paper_sources_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_probe.py --config configs/p110_paper_probe_v1.json --output-dir data/capability_records/p110_paper_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_p96_paper_reference_qa.py --config data/capability_records/p110_paper_probe_v1/qa_config.json --output-dir data/candidates/p110_paper_reference_native_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p96_paper_to_unified.py --config data/capability_records/p110_paper_probe_v1/qa_config.json --native-dir data/candidates/p110_paper_reference_native_v1 --output data/candidates/p110_paper_reference_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p110_paper_reference_unified_v1 --all --output data/candidates/p110_paper_reference_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_revision_plan.py --config configs/p110_paper_revision_plan_v1.json --output-dir data/capability_records/p110_paper_revision_plan_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_target_resample.py --config configs/p110_paper_target_resample_v1.json --output-dir data/capability_records/p110_paper_target_resample_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p110_paper_*.py
```

`p105_paper_quality_gate.py` intentionally raises `no admitted reader` on
this cohort; the pinned `native_quality_ledger.jsonl` records that outcome.
`run_p86_frozen_paper_batch.py` has no native `--verify-only`; its seven pair
outcomes are pinned in the native manifest and rejection ledger. The next
source campaign should inspect actual source shape before choosing work IDs:
multi-file result/experiment sections, unique resolvable labels, and answer
titles that occur only once in the model-visible source are more predictive
than category breadth alone. This is a proposed source filter, not a claimed
task gain.
