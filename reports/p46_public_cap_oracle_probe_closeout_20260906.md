# P46 public cap oracle and bounded NTSB fallback

Both bounded probes reject candidate generation. Inventory delta is zero.

The newly frozen Ofgem July 2026 summary PDF contains four payment/meter rows
on both the old 2023 and revised 2026 consumption bases. The executable Decimal
oracle produces old-basis April-to-July deltas of 221/233/215/53 GBP and revised-
basis deltas of 186/197/181/46 GBP. This difference is a real definition change,
but both bases, both quarters and the effective-date explanation fit in one
483-token contiguous source section. The complete PDF is only 2,552 Qwen tokens.
The public HTML's July/October rates and VAT comparability qualification fit
in a separate 210-token section. These are source-bound short-window rejection
witnesses, not long-context candidates. Regional interactive tables were not
extracted, so the result does not assert failure of every regional task.

The Ofgem copyright policy is frozen alongside the PDF and HTML; the report
binds its exact OGL statement and selected-document marker scan. Reuse scope is
only Ofgem-authored tables and explanatory prose, excluding logos, signatures,
contacts and third-party material. Archived raw bytes are provenance inputs,
never training text. No source signature or production rights approval is claimed.

The fallback actually fetched three CAROL records: A-20-002, H-20-003 and
M-20-006. Initial unadorned requests returned HTTP 403; requests with the public
CAROL Referer and JSON Accept header succeeded. Their frozen hashes bind 3/2/2
NTSB-authored classification spans. The replay emits previous/current status
pairs, but latest status alone still yields the final answer. Removing earlier
classifications changes the reported timeline without changing the final state.
Thus chronological accumulation is not a demonstrated causal transition oracle.
The latest literal status spans take 7/10/9 tokens respectively; these counts
exclude identity/context overhead and are not shared raw-window audit receipts.
Full event prose, recipient correspondence and personal information are not
persisted. Mutable live responses must match the pinned hashes on reproduction.
Broader NTSB author/quotation/privacy and antecedent resolution remain unproved.

The result JSON binds exact character offsets to extracted-text SHA-256, and
binds extraction inputs to original byte hashes. Five tests cover the actual
PDF arithmetic, missing operands, digest/offset tampering, exclusion of recipient
status claims and the real timeline remove-one counterexample. Ruff and format
checks pass. No GPU, adapter, shared audit, selection, promotion or B5 was run.

Reproduction (from project root):

```bash
HF_HOME=/workspace/wynckeliao/.hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run python reports/p46_public_cap_oracle_probe.py --output /tmp/p46-public-cap-replayed.json
cmp reports/p46_public_cap_oracle_probe_v1.json /tmp/p46-public-cap-replayed.json
uv run pytest -q tests/test_p46_public_cap_oracle_probe.py
uv run ruff check reports/p46_public_cap_oracle_probe.py tests/test_p46_public_cap_oracle_probe.py
```

The `--ntsb-cache` option permits an ephemeral local copy of those same three
responses; it never bypasses the frozen hash checks. The source configuration is
`configs/p46_public_cap_oracle_probe_v1.json`, the byte snapshots are under
`sources/p46_public_cap_probe_20260906/`, and the deterministic report is
`reports/p46_public_cap_oracle_probe_v1.json`.
