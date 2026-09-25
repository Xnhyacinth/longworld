# P92 legal-cell batch control plane

P92 schedules existing native compilers from one frozen, explicit recipe plan.
It does **not** infer that every domain, topic, task and length forms a valid
Cartesian product. Each compiler retains its source validation, answer oracle,
reader rendering, dependency checks and admission rules. The scheduler accepts
only compiler-supported overrides, pins the base config hash, gives each recipe
a content-derived job ID, publishes its native output and receipt by atomic
directory rename, and verifies all native file hashes on resume.

The first execution config is `configs/p92_factorial_batch_v1.json`. It requests
200 new controlled state worlds at seeds 920000–920199 across four
length-record/depth cells, two new paired RFC-rule/simulated-state worlds at
three source-text lengths, and three new interval queries on one already frozen
Wiki numeric table. The state recipe still has **one** domain/topic and four
existing operations; new seeds do not create new domains or mechanisms. The RFC
recipe reuses the **same RFC 9114 and RFC 9000 source texts** as P87; new
simulated worlds do not count as new real works. The Wiki intervals reuse the
same frozen source world; they are new query parameters, not new documents.

An additional `wiki_source_pool` kind accepts a pinned multi-source config from
the existing source-pool runner. Its planning phase uses that runner's native
capability probe, records supported jobs and unsupported cells, and preserves
the domain/topic recorded on each source and emitted row. It is not present in
the first execution config. A P76 14-source pinned pool is used only by its
planning regression test until a deduplicated runnable source pool is frozen.
This kind reports zero batch-wide mask-verified rows until a final canonical
reader/mask replay is performed; source-pool native task admission alone is not
represented as an authoritative training mask check.

The implementation currently recognizes four existing native compiler kinds:
shared state, RFC hybrid, closed numeric Wiki table and Wiki source pool. It
can list many independently pinned source-pool entries without changing this
scheduler, but the source router must still find and freeze valid sources, and
new task mechanisms require a verified native compiler. This is a control-plane
scale pilot, not arbitrary vocabulary-substitution scaling or evidence that
hundreds of domains are supported.

Run and inspect:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_factorial_batch.py \
  --config configs/p92_factorial_batch_v1.json \
  --output data/candidates/p92_factorial_batch_v1 \
  --workers 2 --compiler-workers 2
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_factorial_batch.py \
  --config configs/p92_factorial_batch_v1.json \
  --output data/candidates/p92_factorial_batch_v1 --verify-only
cat data/candidates/p92_factorial_batch_v1/manifest.json
find data/candidates/p92_factorial_batch_v1 -name receipt.json -print
UV_LINK_MODE=copy uv run --offline python -m pytest -q tests/test_p92_factorial_batch.py
```

The outer scheduler uses two threads for at most two native recipes in flight.
Native compilers own the bounded process pools; there is no nested scheduler
process pool. Outputs are research candidates with `train_ready=false`.

## Actual bounded executions

The first 3-recipe batch generated **1,615 views / 1,607 independent tasks /
202 source groups**, with 0 native rejects. The composition is 1,600
controlled-state views from 200 new worlds, 12 RFC-rule/simulated-state views
representing 4 independent semantic tasks, and 3 real Wiki numeric-table
interval tasks. Its seven operation labels consist of four state operators, two
RFC-rule operators and one numeric interval operator. This is **three**
domain/topic labels, not broad domain scaling. Actual full-chat bins are 104
under 32K, 1,007 at 32–64K and 504 at 64–128K. Every native final reader in
this batch passed its pinned assistant-only mask check (1,615/1,615). The
state compiler consumed 777.370 seconds for 200 worlds and 1,600 reader rows;
this is compiler time, excluding source acquisition and scheduler restart.

The first scheduler version forked native process pools from worker threads.
The RFC branch hung while its two children stayed idle; state and numeric
recipes had already published atomic receipts. The stuck scheduler was stopped,
and the same frozen plan was resumed through independent Python subprocesses.
The completed state/numeric receipts were hash-reused; the RFC recipe compiled
in 6.931 seconds. Future batches use subprocess isolation, never fork native
pools from a threaded scheduler. A crash after native output but before its
receipt is now recoverable when the native output is complete and verified;
partial RFC/numeric outputs fail explicitly and remain available for audit.

The consolidated Wiki source-pool batch executed **37 supported source×recipe
jobs** over 30 frozen snapshots, with 105 unsupported probed cells. It produced
593 gross views / 544 native independent tasks: 471 table-cell lookups, 70
dense-table scans and 52 table-pair tasks. The native export rejected 192 rows.
Its actual full-chat bins are 297 under 32K, 225 at 32–64K, 29 at 64–128K and
42 at 128–256K. Native production took 118.769 seconds. The batch-level final
reader mask count is **zero** because the complete 593-view pool has not been
replayed under that check.

Against the P91 candidate index, 521 of the 593 gross Wiki views reuse the
same `(source_group, operation, task_id)`; 72 source-scoped candidate tasks
remain. All 72 are L1 table-cell lookups: 56 from 6 previously unindexed
source groups and 16 from 3 already indexed groups. The 16 same-group new
task IDs have no
exact normalized question+answer, same-operation answer, or cross-operation
exact question+answer match among corresponding prior readers. This audit does
not prove the absence of paraphrase-equivalent questions. A second global
`(source_kind, semantic_task_id)` check found **12 more duplicates** across
source groups, with matching answers. The v1 72-row shard is superseded and
must not be appended to the final bank. The corrected v2 shard has **60 global
tasks**, 22 train / 38 eval, 6 source groups and all L1 lookup; 57 readers are
under 32K and 3 are 64–128K. All 60 final reader bytes passed pinned
assistant-only mask replay: 1,056,749 full-chat tokens and 481 supervised
tokens. **No L2 scan or table-pair JOIN is new against P91.** Model-visible
messages contain only the reader question/context and answer; selection and
audit metadata stay in sidecars.

The later v5 router pool freezes 33 source instances and plans 40 supported
cells. Its native run produced 618 gross views from 32 productive groups:
496 lookup, 70 scan and 52 table-pair views, with 199 rejected rows and 120
unsupported cells. Against P91, the corrected P92 60-row shard and P93's six
already-normalized rows, 587 source-scoped task keys overlap; another 12 are
global-ID duplicates across sources. The remaining **19** are L1 maritime
lookups from one eval-only world, all under 32K. All 19 final masks passed:
289,603 full-chat tokens and 157 supervised tokens. The v5 run adds no new
L2/JOIN task. It is a separate pool with SHA-256
`6afa77f6aed86e020b9544859ff198f7837bfe684a190de04ed63c78507bc1c6`.

`p92_source_router_v1`, `v2` and `v4` consolidated pool files have the same
SHA-256 `7a2e9ddcc91039a6b14e84bdd7247889b66392b22b49d2ed67d7c0147b3f1876`.
The first execution uses the v1 path; the v2 adoption plan points at v4 and
checks the same bytes. The 33-source v3 pool has different bytes and was not
silently substituted. A later router revision must get its own plan and audit.

The original v1 scheduler plans pinned source/config bytes but not native code
hashes. The v2 plans bind the scheduler, CLI, native compiler entrypoints and
relevant shared modules by SHA-256. Native files from the verified v1 runs can
be copied byte-for-byte into v2 receipts without recomputation, but those
receipts explicitly record `execution_code_unpinned_at_source=true`: a post-hoc
binding does not prove code immutability during the first execution. The v2
batch aggregation separately reports gross per-recipe tasks, globally unique
semantic tasks and source-scoped tasks; it allows one same-split source world
to carry different operations while rejecting split leakage, duplicate sample
IDs and conflicting task answers. The final code-bound adoption outputs are
`data/candidates/p92_factorial_batch_v3_final`,
`data/candidates/p92_wiki_source_pool_batch_v3_final` and
`data/candidates/p92_wiki_source_pool_batch_v5_v3_final`. The v5 adoption copies
from a code-pinned v2 execution and checks that native compiler code hashes
match; v1-origin adoptions remain explicitly post-hoc. The 30-source pool has 544 global
semantic-task IDs and 569 source-scoped task keys; the difference is shared
task IDs across sources, not 25 extra independent task mechanisms.
The earlier `*_v2_final` adoption directories were built before the
provenance verifier was strengthened. Because their frozen plans hash the
scheduler code, they are historical and must not be cited as current verified
outputs. The v3 adoptions bind the source config, plan, manifest and original
job receipts through `adoption.json`; their manifests pin its SHA-256.

Inspect the concrete artifacts:

```bash
cat data/candidates/p92_factorial_batch_v1/manifest.json
cat data/candidates/p92_factorial_batch_v3_final/adoption.json
cat data/candidates/p92_factorial_batch_v3_final/manifest.json
cat data/candidates/p92_wiki_source_pool_batch_v1/manifest.json
cat data/candidates/p92_wiki_source_pool_batch_v3_final/adoption.json
cat data/candidates/p92_wiki_source_pool_batch_v3_final/manifest.json
cat data/candidates/p92_wiki_source_pool_batch_v5_v3_final/manifest.json
cat data/candidates/p92_wiki_source_pool_batch_v1/question_fingerprint_audit.json
cat data/candidates/p92_wiki_new_only_60_v2/manifest.json
cat data/candidates/p92_wiki_new_only_60_v2/mask_audit.json
cat data/candidates/p92_wiki_v5_new_only_19_v2/manifest.json
cat data/candidates/p92_wiki_v5_new_only_19_v2/mask_audit.json
less -R data/candidates/p92_wiki_new_only_60_v2/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_factorial_batch.py \
  --config configs/p92_factorial_batch_v2.json \
  --output data/candidates/p92_factorial_batch_v3_final --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_factorial_batch.py \
  --config configs/p92_wiki_source_pool_batch_v2.json \
  --output data/candidates/p92_wiki_source_pool_batch_v3_final --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_factorial_batch.py \
  --config configs/p92_wiki_source_pool_batch_v5.json \
  --output data/candidates/p92_wiki_source_pool_batch_v5_v3_final --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_factorial_batch.py \
  --config configs/p92_wiki_source_pool_batch_v2.json \
  --output data/candidates/p92_wiki_new_only_60_v2/mask_audit.json \
  --audit-new-only-mask data/candidates/p92_wiki_new_only_60_v2
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_factorial_batch.py \
  --config configs/p92_wiki_source_pool_batch_v5.json \
  --output data/candidates/p92_wiki_v5_new_only_19_v2/mask_audit.json \
  --audit-new-only-mask data/candidates/p92_wiki_v5_new_only_19_v2
```

All outputs remain research candidates with `train_ready=false`. No GPU job,
training loader promotion or model-gain experiment was run here.
