# P51 OSV/upstream remediation lifecycle preflight closeout

## Outcome

**Official cross-source topology: PASS. Deterministic affected/fixed oracle
preflight: PASS. Near-deduplicated 32K/64K/128K capacity: PASS. Candidate
conversion: NOT RUN. Inventory delta: 0.**

The commit-pinned PyPA archive contains 7,342 advisories. A strict filter keeps
1,851 records that have a PyPI package, external alias, explicit affected
versions, at least one fixed event, and a full GitHub fix-commit reference, and
rejects every record whose explicit affected list overlaps an ecosystem fixed
boundary. Capacity serialization omits `affected.versions`, severity, and custom
database fields. Exact dedup leaves 1,851 records / 950,081 Qwen tokens; global
five-word-shingle Jaccard 0.90 collapse leaves **1,849 records / 947,535 Qwen
tokens**.

Whole-advisory deterministic packs reach all requested exact bands without
padding, cloning, splitting, or truncation:

| Band | Whole advisories | Exact Qwen tokens | Receipt SHA-256 |
| --- | ---: | ---: | --- |
| 32K `[32000, 32768]` | 65 | 32,078 | `36622cd41f828c17e0a04ed39cef8aa2885eb0262ef6a3a221cf0d43e35246f4` |
| 64K `[64000, 65536]` | 127 | 64,309 | `7df90d01c35088e81235de6049ca99cec1e5078564de6a8e15fb71d8785e165b` |
| 128K `[128000, 131072]` | 250 | 128,191 | `536cc8bf2a616f05ecd88358306f58e0806fc379d615acb4badd7188f828aa49` |

This is capacity evidence, not training data. `train_ready=false`,
`do_not_generate=true`, candidate count 0, and inventory delta 0.

## Real lifecycle topology

The preflight validates five complete chains covering fava, jupyter-server,
rdiffweb, rply, and vantage6. Each chain follows this evidence DAG:

```text
withdrawn PyPA advisory --explicit duplicate target--> active PyPA advisory
          | shared CVE/GHSA alias + PyPI package          |
          +---------------- identity join ----------------+
                                                         |
                    +--------------------+----------------+
                    |                    |
             GIT.fixed hash       ECOSYSTEM.fixed version
                    |                    |
         upstream GitHub patch     official PyPI release JSON
                    |                    |
          commit-pinned license     artifact SHA-256 receipt
```

All 7,342 PyPA advisory IDs occur in the generation-pinned OSV.dev PyPI export.
For the ten records in the five selected chains, package names, fixed events,
GitHub fix-commit references, and withdrawn state agree across sources. OSV's
aggregated copy also connects each withdrawn record to its active duplicate ID
as an alias. Generic `related` entries are never interpreted as supersession.

The 17 fetched evidence objects total 38,644,852 bytes with bundle SHA-256
`9e2f01c9ae021b365c3b5c95d1ba73609fffe8de4dcd3c4c8a974e82a80551c4`.
The source archives pass traversal, link/special-member, encryption, expansion,
member-size, and compression-ratio checks. Raw source bodies remain uncommitted.

## Deterministic tasks and oracle boundary

Four non-isomorphic task branches are supportable:

1. **Identity retrieval:** resolve an external CVE/GHSA alias to the matching
   OSV/PYSEC record and PyPI package.
2. **Exact version state:** classify only explicit package/version pairs as
   `AFFECTED` or exact fixed-event versions as `FIXED_BOUNDARY`; all other input
   versions are `UNKNOWN`, never inferred safe.
3. **Remediation trace:** join an advisory's `GIT.fixed` to the exact upstream
   commit and its `ECOSYSTEM.fixed` to the version-specific PyPI release.
4. **Lifecycle reconciliation:** follow an explicit withdrawn-as-duplicate edge
   to the active advisory, then compare package, aliases, ranges, commit, and
   release evidence.

The eligible pool exposes 133,531 affected-version entries (34,992 distinct
package/version pairs), 3,660 ecosystem fixed boundaries (928 distinct pairs),
and 1,786 Git fixed-event entries (1,083 distinct hashes). In 1,519 eligible
records, a Git fixed hash exactly matches a GitHub `FIX` reference. Two
near-duplicate advisory bodies are removed. Individual advisory lengths span
251 to 2,383 tokens, with median 491.

## Rights, privacy, and non-capacity evidence

The capacity source is the commit-pinned CC BY 4.0 PyPA advisory database. The
five bounded upstream repositories have commit-pinned MIT, BSD-3-Clause,
GPL-3.0-only, or Apache-2.0 license receipts. Upstream patch and PyPI release
bodies contribute zero capacity. Any future derivative that includes patch text
must preserve source-specific license and attribution metadata, especially the
GPL boundary. This is a technical provenance review, not legal advice.

The sources are public vulnerability advisories, repositories, and package
metadata; no private records were found. A later release review should still
run secret/PII checks over generated rows rather than rely on this aggregate
preflight.

## Exact blockers before conversion

1. No P51 world builder, candidate schema, retrieval trace, or deterministic
   row oracle has been implemented; this task was deliberately preflight-only.
2. The version oracle cannot label an unlisted version safe. Candidate design
   must retain `UNKNOWN` and reject ambiguous/conflicting records at row
   construction time.
3. Only five explicit duplicate-target chains have complete upstream commit,
   release, and license receipts. Broad lifecycle synthesis must not promote
   arbitrary `related` links or alias overlap to supersession.
4. If patch text enters a candidate, per-repository license/attribution and
   immutable artifact receipts must travel with that row; the advisory archive's
   CC BY license is not a blanket license for upstream code.
5. Unchanged formal near-duplicate, source-lineage, derived-view, truncation,
   4K/8K/16K shortcut, answer-dependence, counterfactual, remove-one, and
   independent promotion gates remain unrun.

Therefore the preflight is green for design and capacity but fail-closed for
training conversion: candidate count stays 0.

## Reproduction

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/workspace/wynckeliao/.hf \
  uv run python reports/p51_osv_upstream_remediation_lifecycle_preflight.py
uv run ruff format --check reports/p51_osv_upstream_remediation_lifecycle_preflight.py
uv run ruff check reports/p51_osv_upstream_remediation_lifecycle_preflight.py
jq empty configs/p51_osv_upstream_remediation_lifecycle_preflight_v1.json \
  reports/p51_osv_upstream_remediation_lifecycle_preflight_v1.json
```
