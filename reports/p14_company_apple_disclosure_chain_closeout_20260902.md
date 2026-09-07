# P14 Company Apple disclosure-chain closeout

## Outcome

The Apple 2025 cross-section disclosure-chain experiment is **6/9**, not a
complete world, and contributes **0 Company quota**.  The 32k and 64k tiers
passed all three natural views.  The 16k tier remained invalid under the
unchanged distance and derived-view gates.  No dense ranking, strict audit,
promotion, or release counting was run.

The unsuccessful Apple implementation and its focused test were removed from
`longworld/domains/company/{simulate,queries}.py` and
`tests/test_company_sec_financial_programs.py` after the final run.  This keeps
the core diff free of an unpromotable task.  Only the final configuration and
this closeout (including the generated-artifact hashes) are retained.

## Source and independence

- Issuer: Apple Inc., CIK `0000320193`.
- Record: `sec:0000320193-25-000079`, fiscal-year end `2025-09-27`.
- Canonical EDGAR URL:
  `https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079.txt`.
- Source bytes SHA-256:
  `60f22bb90bb8e46894019fd57c27df69abd192f3f0f4183c4345d786426f3391`.
- The source manifest and workflow bundle were re-attested with the current
  local-probe source role without changing the URL, record ID, or bytes.
- The same Apple filing record exists in historical inventories.  Re-signing
  does not create source novelty.  Even a complete task would still require the
  canonical profile to accept its distinct answer program and source binding.
  Because this run is only 6/9, that profile question is non-operative here.

## Read-only capacity preflight

Before implementation, the proposed cumulative answer-bearing sections were
measured with pinned `Qwen/Qwen3.5-4B` tokenizer revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.

| Tier | Answer-bearing section tokens | Visible source-span tokens | Raw source char span |
|---|---:|---:|---:|
| 16k initial design | 5,830 | 9,080 | 347,202-717,280 |
| 32k initial design | 18,713 | 23,488 | 202,950-717,280 |
| 64k | 47,095 | 47,598 | 164,391-1,500,431 |

The final 16k semantic revision moved the real cybersecurity and legal sections
into the first stage, raising its answer-bearing source capacity to about 7.4k
tokens and four independent source essentials.  The 32k stage then added the
risk-factor section; the 64k stage remained a strict cumulative superset.

## Exact final candidate result

| Tier | View | Exact context tokens | Evidence distance | Near-dup ratio | Real-source token ratio | Result |
|---|---|---:|---:|---:|---:|---|
| 16k | full | not emitted | 7,548 on retained back attempt | n/a | n/a | fail: distance shortfall `<8000` |
| 16k | counterfactual | not emitted | n/a | n/a | n/a | fail with 16k slot |
| 16k | ordered artifact | not emitted | n/a | n/a | n/a | fail: an 8k contiguous window reproduced the answer |
| 32k | full | 32,749 | 31,533 | 0.0261 | 0.9768 | pass |
| 32k | counterfactual | 32,749 | 31,533 | 0.0261 | 0.9449 | pass |
| 32k | ordered artifact | 32,749 | 24,420 | 0.0261 | 0.9768 | pass |
| 64k | full | 65,196 | 63,949 | 0.0751 | 0.9838 | pass |
| 64k | counterfactual | 65,196 | 63,949 | 0.0751 | 0.9678 | pass |
| 64k | ordered artifact | 65,196 | 51,674 | 0.0751 | 0.9838 | pass |

Candidate summary: 6 rows, 2 retained length slots, 0 clones, candidate row-set
SHA-256 `f437b168c4f811a7070913c979c622f67da35435e3a4f6dca0756af966ccb0ee`.
The 16k failure was not repaired by adding another chapter because that would
have been a gate-targeted bandwidth patch after the agreed single semantic
revision.

## Acquisition blockers encountered

- The bounded four-annual Apple EDGAR request failed on the first
  `data.sec.gov/submissions` request with HTTP 403.  A read-only check of both
  `data.sec.gov` and the canonical `www.sec.gov/Archives` URL returned Akamai
  HTTP 403.  The repository fetcher intentionally revalidates online and does
  not treat an old submissions cache as fresh provenance.
- The authorized NVIDIA issuer-owned IR request stopped fail-closed because the
  first filing detail page was detected as a challenge page; no inventory was
  created.
- The existing AMD multifiling directories contain no source filings.  The
  historical AMD blocker records source bytes `0` after SEC 403 and browser
  challenge responses.

No challenge bypass, alternate-source substitution, source-ID change, padding,
copying, or gate relaxation was used.

## Commands

Focused TDD verification before cleanup:

```bash
PYTHONPATH=. /tmp/p13-st-venv/bin/python -m pytest -q \
  tests/test_company_sec_financial_programs.py::test_apple_disclosure_chain_is_incremental_and_remove_one_essential
```

Final natural candidate generation:

```bash
HF_HOME=/workspace/wynckeliao/.hf \
/tmp/p13-st-venv/bin/python scripts/run_with_local_probe_trust.py \
  --trust-file /root/.longworld-p12-active/p12-probe-12-20260829-v1/local_probe_trust.json \
  --role source --role candidate --role report --pass-env HF_HOME \
  --allow-combined-roles -- \
  /tmp/p13-st-venv/bin/python scripts/generate.py \
  --config configs/p14_company_apple_disclosure_chain_v1.yaml \
  --n-worlds 1 --target-promoted-worlds 1 --seed-start 14004 --workers 1 \
  --out-dir reports/p14_company_apple_disclosure_chain_v1/candidate
```

## Artifact hashes

- Config: `21ac08603bb5c9f26227d2d1b52c313e0b6a318598e5f798e824e9e8ebc81bce`
- Candidate quality report:
  `ba6f46d817c2f75fa61d1556ca7991d031238e0580d27737858c4e64a5223035`
- Candidate reject log:
  `226f336e5490bc0231eb6960c29d09bc1de19d118f093419e819205c2e9b2c05`
- Candidate train rows:
  `a6ce10bee33b52a5e58a7eb300f6cd669c19768c231facb35d11dff71a7da4c5`
- Current-trust source workflow bundle:
  `2fdb5294a15d1240a511da3cb6d40f7584daf97077ea4bb30b65e3f03349df68`
- Current-trust SEC filing manifest:
  `17966e95b9beb2d7b4e765b954126a66ee48faf37f2ccbcb2e7bfc53f01f8559`
