# P51 OSV/upstream remediation primary-source register

Checked 2026-09-04. Only official OSV/OpenSSF, PyPA, upstream repository,
and PyPI sources were used. Raw archives, advisories, patches, licenses, and
release JSON were inspected ephemerally and are not committed.

## Schema and distribution documentation

| Authority | URL | Role | Receipt / note |
| --- | --- | --- | --- |
| OSV.dev | <https://google.github.io/osv.dev/data/> | Documents per-ecosystem `all.zip` exports, including PyPI | fetched HTML: 25,475 bytes; SHA-256 `4478274179f1bfdbbde5133791cb8436678106d3454262ef6a0bcb1dad9ac285` |
| OpenSSF OSV Schema | <https://ossf.github.io/osv-schema/> | Defines `aliases`, `related`, `upstream`, affected ranges, events, and references | fetched HTML: 221,158 bytes; SHA-256 `938319f65a14429f67e3808f162d9b45a22a78a4e7d8a0d7b72d7191bae7ac5f`; the schema says aliases denote the same vulnerability and must not be repurposed for upstream/downstream relations |
| OSV.dev API | <https://google.github.io/osv.dev/api/> | Documents ID and package/commit query surfaces | fetched HTML: 14,824 bytes; SHA-256 `bf5b83ee61a92a017a561e2f0c13f365b833d9421051cce808bcaddd6abb1a6f` |
| PyPI | <https://docs.pypi.org/api/json/> | Documents official project/release JSON | release endpoints below are version-specific |

Documentation receipts describe the consulted pages; the executable preflight
does not depend on their mutable presentation HTML.

## Frozen advisory sources

| Source | Immutable locator | Bytes | SHA-256 | Use |
| --- | --- | ---: | --- | --- |
| PyPA advisory database | commit `c4a1fde8cb41b3b5180fed3561596c9ce6de99ad`; <https://codeload.github.com/pypa/advisory-database/tar.gz/c4a1fde8cb41b3b5180fed3561596c9ce6de99ad> | 4,199,425 | `4e40288bddd838b87be3b004c721e9384629eaec646c351e8dc8324d53c8195a` | canonical licensed capacity source and source-side lifecycle records |
| OSV.dev PyPI export | GCS generation `1788521584495343`; <https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip?generation=1788521584495343> | 34,218,425 | `99b7dabe36c8bd98dc0754327994ef13753c351abc1a245befc6db0efcee5dca` | independent aggregated mirror used for cross-source projection and enriched alias checks; contributes zero capacity |

The PyPA archive contains a 18,657-byte CC BY 4.0 `LICENSE` with SHA-256
`9ba9550ad48438d0836ddab3da480b3b69ffa0aac7b7878b5a0039e7ab429411`.
The OSV.dev code repository's Apache license is not treated as a license grant
for all aggregated vulnerability content. Capacity is therefore derived only
from the commit-pinned PyPA database.

Archive checks rejected absolute/parent paths, links, special tar members,
encrypted ZIP members, oversized expansion, oversized members, and excessive
compression ratios. The PyPA archive has 8,920 members / 7,351 regular files /
7,342 advisories / 22,196,887 expanded bytes. The OSV export has 25,278 JSON
members / 71,767,248 expanded bytes. No unsafe path, link, special, or encrypted
member was observed.

## Five bounded upstream lifecycle chains

Each row is required to have all of the following in the frozen advisory
sources: an explicitly withdrawn-as-duplicate source record, a non-withdrawn
target, a shared PyPI package, a shared external alias, the same GitHub `FIX`
reference, a target `GIT.fixed` equal to the commit, and a target
`ECOSYSTEM.fixed` equal to the release. The commit patch, version-specific PyPI
release JSON, and repository license are independently fetched and hashed.

| Withdrawn -> active duplicate | Package | Fixed commit | Fixed release | Patch receipt | Release JSON receipt | Commit-pinned license |
| --- | --- | --- | --- | --- | --- | --- |
| `PYSEC-2022-43182` -> `PYSEC-2022-239` | fava | `ca9e3882c7b5fbf5273ba52340b9fea6a99f3711` | 1.22 | 6,470 bytes; `1ce430ec84fb8c222830ebb4dceae1715accd15a94dac75668d05eadbcb269b3` | 6,793 bytes; `cb01f2be23ae3a82c0a725ebf5e671d2a114e4880eb4261edddfe3c7e2164eee` | MIT; 1,108 bytes; `143ab9400e5ecf3fe1ec07725e1781ec60c24fa2765a6bd22c6a0eda6770d567` |
| `PYSEC-2020-346` -> `PYSEC-2020-50` | jupyter-server | `85e4abccf6ea9321d29153f73b0bd72ccb3a6bca` | 1.1.1 | 5,410 bytes; `e5ad5145982fca965188ceb7ba7130f40c9eb86a988bc322ceb5ff2a366577c0` | 30,595 bytes; `4a3ae8faddcffe998d6f3c4b1b4f1d14d5ba7747966599b5f3034328064a12dd` | BSD-3-Clause; 2,888 bytes; `aa93a54b783ec3c2eafe5969d1b7947f427a7813846551ae65a52e1fa5489f42` |
| `PYSEC-2022-43184` -> `PYSEC-2022-292` | rdiffweb | `667657c6fe2b336c90be37f37fb92f65df4feee3` | 2.4.8 | 8,722 bytes; `40a6a3d89e7bd557cc0bf84a8822cfddb96e5a447045091204f2f14be662953b` | 40,838 bytes; `f400fdf0b2e6d558e50fc42027d57dd6f209b28933af087b0ad34b68d9afff68` | GPL-3.0-only; 35,146 bytes; `c53a65c2fd561c87eaabf1072ef5dcab8653042bc15308465f52413585eb6271` |
| `PYSEC-2014-117` -> `PYSEC-2014-17` | rply | `fc9bbcd25b0b4f09bbd6339f710ad24c129d5d7c` | 0.7.1 | 2,038 bytes; `d9c99436d93e656817d698f7203635333b72a37afc1257d496a6c70189dc22e8` | 6,968 bytes; `eec6061918313aac8201942c4e143379ba24e60b3c3f3eb69ac7cf3034b43420` | BSD-3-Clause; 1,535 bytes; `aa19e7ffb50ba42c2b9df7e9e8b317d3fc24ed3c40f11eb958856b68cf381d18` |
| `PYSEC-2023-313` -> `PYSEC-2023-52` | vantage6 | `ab4381c35d24add06f75d5a8a284321f7a340bd2` | 3.8.0 | 32,090 bytes; `ac2682e2f5f339e4cab5ac51525ee77662e8e5d77857319448802b5e07ffcf38` | 35,068 bytes; `63e4109c6a327ee15011e62d8777251b4ddf26a91ea0955b46d763f772c2a7e6` | Apache-2.0; 11,333 bytes; `64f513f80a474c27b21f87dee40548fe1234588e7e36157de6847cdefe810977` |

The exact URLs and byte limits are in the P51 config. Patch and release bodies
are validation evidence only. If a future task includes patch text, it must
carry repository-specific license and attribution metadata; the current
technical review is not a legal opinion.

## Fail-closed interpretation

- `aliases` means same vulnerability. `related` is never promoted to
  supersession. A duplicate edge is accepted only from explicit
  `Withdrawn as duplicate of ...` source text plus the shared identity and
  package checks above.
- An exact package/version listed in `affected.versions` is `AFFECTED`; an exact
  `ECOSYSTEM.fixed` value is `FIXED_BOUNDARY`; every other version is `UNKNOWN`.
  No inferred safe label is allowed.
- A commit is `FIXED_COMMIT` only when the target `GIT.fixed` hash and GitHub
  `FIX` URL agree and the upstream patch receipt validates.
- `affected.versions` is available to the oracle but excluded from capacity, so
  repeated version enumerations cannot be used as long-context filler.
