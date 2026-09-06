# P36 IETF OAuth training conversion blocker (2026-09-03)

## Result

P36 stopped fail-closed before selection. The P33 source product remains a
valid candidate-local 6/6 dense-audited world, but the current immutable release
framework cannot represent or accept it as a one-world `standards` training
product. No train/eval row, selection receipt, promotion receipt, quality-gate
receipt, B5 export, or training manifest was created.

The P36 inventory delta is therefore zero. The six P33 rows remain
`data_stage=candidate` and must not be counted as train-ready data.

## Blocking evidence

### 1. No legal one-world standards release profile

`release_profile()` resolves only the hard-coded immutable
`RELEASE_PROFILES` registry. It does not load a profile from a config file.
The registered one-world profiles cover `macro_economics`, `finance`,
`codeforge`, `company`, or `researchlab`; none has a `standards` quota.
The generic no-domain-quota probe profile requires 12 worlds and therefore
cannot select this single world.

The requested minimal profile identity fails exactly as follows:

```text
ValueError: unknown release profile: p36-ietf-oauth-128k-extension-probe-1-v1
```

Reusing a finance/codeforge/macro or research/company source-slice profile would
misstate the domain quota and trust contract, so no such substitution was made.

### 2. IETF v3 sidecars are not accepted at the promotion boundary

`taskpromotion.py` and the dense audit path support
`standards.ietf_oauth_requirement.v1`, but the shared promotion boundary's v3
`expected_domains` registry contains only Cyber, Finance, and Macro adapters.
It has no mapping from `standards.ietf_oauth_requirement.v1` to `standards`.

A read-only public structural preflight using the authentic P33 candidate key,
probe environment, and the closest 64K/128K one-world profile reached the
adapter boundary and failed exactly as follows:

```text
PromotionError: candidate structural preflight task replay sidecar is invalid
```

The incompatible Finance profile was used only to expose this earlier adapter
boundary; it was not used for selection or output construction.

## Intended profile contract

The candidate-only preflight config records the smallest contract that would be
needed without weakening any gate: one `standards` world, train 1/eval 0, B5
only, exact 64K and 128K, all three full/CF/ordered first-timing views, three real
64K rows, one source family/workflow/base task/proof/program/semantic task, and
all rows source-bound. It is deliberately marked blocked and is not a runtime
release-profile substitute.

## Pipeline status

| Stage | Status | Rows |
|---|---|---:|
| P33 dense-audited candidate input | available | 6 |
| P36 profile resolution | blocked | 0 |
| release selection | not attempted | 0 |
| strict promotion | not attempted | 0 |
| train-ready report | not attempted | 0 |
| release quality gate | not attempted | 0 |
| B5 export and manifest validation | not attempted | 0 |

No new P36 role key was used because the immutable profile failed before any
P36 signing boundary. P33 rows were not re-signed or mixed with an incompatible
trust root.

## Reproduction

Profile registry diagnostic:

```text
STANDARDS_PROFILES []
ValueError: unknown release profile: p36-ietf-oauth-128k-extension-probe-1-v1
```

Signed candidate structural preflight diagnostic:

```text
PromotionError: candidate structural preflight task replay sidecar is invalid
```

Input hashes:

- candidates:
  `9ef1d5a5a12209fcd03fe08880b61e5a61f06bf20983601f1f3606c2615ffa0a`
- dense audits:
  `6ea9394ea45374cc41a66ce595f7ef69636d119cbc6c22f456647d40c6adc082`
- audit manifest:
  `c3b1594b33c1a5fbfae790a3743355197e81d02b9526d1dcf3086d5c157520a8`
- P36 conversion preflight config:
  `c1172534210540ab4983be32ddccefd12100852e70f4f897297e2206bae0869f`

The machine-readable blocker is
`data/releases/p36-ietf-oauth-128k-extension-probe-1-v1-blocked/BLOCKER_LEDGER.json`.
No gate, threshold, domain identity, trust identity, or inventory count was
changed.
