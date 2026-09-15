# Worlds worktree handoff — 2026-09-15

The reviewable P57--P66 pipeline, taskbank, capability curriculum, tests,
compact reports, and planning records were integrated into GitHub `main`
through the sanitized single-parent integration branch. The ordinary merge
parent was intentionally discarded so `.local-probe-env` key blobs from this
experimental branch do not become reachable from `main`.

The complete operational upload view is indexed by SHA-256 in private
`Xnhyacinth/LongWorld-Training-State` at Hub commit
tag `state-2026-09-15`. Hub accepted 1.17 GB of logs,
receipts, reports, and evaluation results. Its private LFS quota rejected
213.00 GB of full checkpoint and large evaluation payloads. Those files remain
in the resumable local hard-link view
`/workspace/wynckeliao/.longworld-hf-staging/LongWorld-Training-State` and are
listed in the uploaded `SNAPSHOT_MANIFEST.json`.

`.local-probe-env` is no longer tracked at the branch tip. Its ignored local
copy remains on this machine for migration diagnostics. Because older private
branch commits contain those non-production probe keys, provision fresh probe
keys before future signed synthesis; do not reuse the historical key bytes.
