# P108 new CodeForge repositories and bounded content expansion

P108 discovers merged PRs from the existing public allowlist without choosing
individual tasks by hand. The catalog uses the frozen 100-closed-PR pages for
each newly eligible repository. The detail probe reuses pinned responses across
versions and globally rate-limits new GitHub requests. The exporter runs four
bounded workers under the unchanged P17 source-role trust wrapper; each PR
episode and each repository bundle is signed and replayed before taskbank use.
The source snapshots, signed bundles, native banks and reader shards remain
local-probe research candidates (`train_ready=false`).

The new allowlisted MIT/Apache repositories are Bitcoin, Deno, Godot and
Requests. The three BSD allowlist repositories are unsupported by the current
exporter's license detector and were not silently admitted. Repository split
is fixed: Bitcoin, Deno and Requests train; Godot eval. The Deno split
preserves the earlier Deno reservation. This is a cohort-specific boundary,
not a general source-discovery rule.

| Stage | Actual result |
| --- | ---: |
| Initial catalog / detail probe | 4 repositories × 100 closed-PR entries; 96 selected merged PRs; 73 details probed; 22 structure-eligible |
| Initial source export | 22 attempts; 21 signed usable patches, 1 signed no-patch; 4 signed repository bundles |
| Initial native CodeForge bank | 4/4 verified; 143 semantic tasks |
| Initial P99 added-code proof | 46 candidate PR pairs, 0 qualified; 41 missing distinct anchors, 5 spans ≤16K |
| Independent filename-content profile | 82 long primary readers; 39 filename-grammar rows; 19 raw scoped, 4 content-backed qualified |
| Filename final readers | 4 train tasks, Bitcoin 2 and Deno 2; 64K bin 2, 128K bin 2; all-mask 4/4 |
| Bounded expanded detail probe | 93/96 PRs probed; 31 structure-eligible (Bitcoin 9, Deno 12, Godot 6, Requests 4) |
| Expanded source export | 22 old frozen episodes reused by SHA-checked hardlinks; 9 new attempts, 6 usable and 3 no-patch; 27 usable total |
| Expanded native bank | 4/4 verified; 206 semantic tasks (includes tasks over unchanged scopes) |
| Expanded P99 added-code proof | 85 candidate PR pairs; 2 qualified new Deno pairs, 83 rejected |
| Expanded P99 final readers | 2 train tasks, 128K bin 2; all-mask 2/2; full chat 375,454 and supervised 98 tokens |
| Expanded filename-content proof | 140 long primary readers; 72 filename-grammar rows; 45 raw scoped, 26 content-backed qualified |
| Expanded filename final readers | 26 qualified minus 2 old PR/program scopes = 24 net new train tasks; 32K/64K/128K bins 8/14/2; all-mask 24/24 |

The first two source-export attempts are retained as failures: seven validation
receipts from comparing intentionally redacted public hashes, then three from
an import-path error in the new verifier entrypoint. No source was admitted by
those attempts. The corrected verifier compares policy/client pins inside the
trusted child and returns booleans; the existing wrapper and attestation code
were not changed. The first bank attempt also remains at 0/4 because the
trusted subprocess omitted the pinned local `HF_HOME` path. A one-repository
Requests smoke passed after that environment correction, followed by 4/4
parallel bank builds. The intermediate detail-probe v3 failed before any new
packet was fetched because v2 had reused v1 packet paths; v4 resolves both
pinned prior roots and replays v2 unchanged.

The expanded cohort is deliberately bounded to the same 24 merged PR
candidates per repository. It added three Bitcoin and six Deno structural PRs;
Godot and Requests had no additional eligible PR in that frozen page. Only two
of the nine newly signed PRs had a unique syntactic added-code identifier in
their own patch, both from Deno. This preflight is only a necessary condition.
The full P99 compiler found two accepted pairs: `#36482/#36785` and
`#36785/#36696`, with 45,132 and 76,101-token separation between its selected
added-code witnesses. Both pass filename-preserving removal of added lines,
which changes the answer and removes both selected identifiers. The 83
rejections comprise 77 missing distinct content-only anchors, five spans
≤16,384 tokens and one final-chat overflow. The accepted chats contain
180,568/194,886 tokens and supervise 53/45 assistant tokens. The question is
at the start of the user message before `Source records`; witness separation
does not measure last-evidence-to-query distance, nor prove a global shortest
proof or a model-learning improvement.

The filename profile is separate: it covers finite filename alias matching
for `merged_files_union`, `shared_changed_files` and `approved_merge_files`,
then normalizes record text and SHA values while preserving filename surfaces.
Its four initial qualified rows have minimum controlled alias covers of
17,368, 21,483, 31,578 and 20,366 tokens. Those four readers have 647,760
full-chat and 252 supervised tokens. In the expanded bank, 26 filename rows
passed the same content control. Comparing source group, split, operation and
sorted PR numbers against the initial four rows removes two repeated scopes;
all 24 remaining rows contain at least one newly signed PR. They have
2,164,540 full-chat and 1,771 supervised tokens, with 22 Bitcoin and two
Deno tasks. Their certificate does not imply the P99 two-added-code-witness
property. Across the initial and expanded shards, P108 has 30 distinct
qualified reader tasks: 4 initial filename, 24 new filename and 2 new
added-code. This is not 30 new source groups; the cohort has four repository
worlds, of which two yielded final qualified readers. All final readers remain
candidates; no GPU training was launched.

Pinned receipts to inspect:

```bash
cat data/capability_records/p108_code_catalog_v2/manifest.json
cat data/capability_records/p108_code_probe_v4/manifest.json
cat data/capability_records/p108_code_sources_v4/manifest.json
cat data/capability_records/p108_code_anchor_preflight_v1/manifest.json
cat data/candidates/p108_code_banks_v3/manifest.json
cat data/candidates/p108_code_content_native_v2/manifest.json
cat data/candidates/p108_code_content_unified_v2/manifest.json
cat data/candidates/p108_code_content_mask_v2/manifest.json
cat data/candidates/p108_code_filename_unified_v2/manifest.json
cat data/candidates/p108_code_filename_unified_expanded_v1/manifest.json
cat data/candidates/p108_code_filename_mask_expanded_v1/manifest.json
less -R data/candidates/p108_code_content_native_v2/rejects.jsonl
less -R data/candidates/p108_code_content_native_v2/audit.jsonl
```

Reproduce the final source and reader checks with the same private P17 trust
file used for the batch:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p108_code_export.py \
  --config configs/p108_code_export_v5.json \
  --output-dir data/capability_records/p108_code_sources_v4 \
  --trust-file "$P17_TRUST_FILE" --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p108_code_bank.py \
  --config configs/p108_code_bank_v3.json \
  --output data/candidates/p108_code_banks_v3 \
  --trust-file "$P17_TRUST_FILE" --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p99_code_to_unified.py \
  --config configs/p108_code_content_v2.json \
  --native-dir data/candidates/p108_code_content_native_v2 \
  --output data/candidates/p108_code_content_unified_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p108_code_content_unified_v2 --all \
  --output data/candidates/p108_code_content_mask_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p108_code_filename_to_unified.py \
  --proof-dir data/candidates/p108_code_filename_proof_v2 \
  --bank-manifest data/candidates/p108_code_banks_v3/manifest.json \
  --prior-proof-dir data/candidates/p108_code_filename_proof_v1 \
  --prior-bank-manifest data/candidates/p108_code_banks_v2/manifest.json \
  --output data/candidates/p108_code_filename_unified_expanded_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p108_code_filename_unified_expanded_v1 --all \
  --output data/candidates/p108_code_filename_mask_expanded_v1 --verify-only
```

The native bank and P99 compilers use the pinned Qwen3.5-4B tokenizer. The
bank config pins the local cache root and revision. `P17_TRUST_FILE` points to
the private local-probe trust JSON outside the repository; neither that file
nor any key is copied into these artifacts.
