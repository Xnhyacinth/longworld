# P109 real-prose rule support and bounded reader pilot

P109 tested one reusable source-to-task grammar on frozen official RFC text: an explicitly named numeric field that **MUST be at least** an integer. A task combines the unmodified complete RFC with separately labelled **simulated inspection records with explicit field values** and asks which values fall below the source minimum. The source rule is parsed from the final reader; the program scans every simulated record. It judges the **declared or observed field value**, not what an endpoint subsequently does with that value. In RFC 9000, the separate note about ignoring a received `active_connection_id_limit` when issuing a zero-length connection ID is therefore outside this answer contract; the minimum for an explicitly supplied parameter remains the selected rule. No RFC sentence is rewritten in a training reader, no paragraph is copied to fill a length bucket, and no answer is manually authored per RFC.

The [P103 annual-report support ledger](../data/capability_records/p103_report_support_matrix_v2/ledger.json) remains a negative result: 32 filings from eight issuer worlds yielded 0 admissible Note/Item cross-section tasks. Its 108 Note references, 178 tabular Note mentions and six Item references lack a safe unique cue, complete row/heading binding or typed rule. The P87 RFC 9114 v6 hybrid had one real-rule source and four state-conditioned tasks; its earlier v5 ID-only shortcut is not reused.

| P109 frozen source-support stage | Result |
| --- | ---: |
| Official RFC fetch inventories pinned | 6 |
| Distinct RFC documents after exact RFC ID/SHA dedup | 44 |
| Numeric lower-bound clauses found | 4 |
| Rule clauses with typed field and bounded source paragraph support | 2 |
| Distinct supporting RFCs | 2 |
| Reader tasks from accepted rules | 4 train; 0 eval |

The two accepted rules are the `padding_length` lower bound in [RFC 6520](https://www.rfc-editor.org/info/rfc6520/) and the `active_connection_id_limit` lower bound in [RFC 9000](https://www.rfc-editor.org/info/rfc9000/). The RFC 5246 clause has a conditional/indirect subject, and the RFC 9000 Destination Connection ID clause applies only to a particular Initial-packet situation; the generic record schema does not model either scope, so both are rejected. This is **two source rule worlds**, with two paired simulated state variants for each—not four independent real documents. The machine-readable per-clause status is in `data/capability_records/p109_prose_support_v1/ledger.json` (SHA-256 `115a9e96b10ace6efe0e9641cefde9e70b9e2ef40a01a1fa6a779c6802a1b9c8`).

| Rule source / state seed | Final-chat tokens | Supervised tokens | Complete evidence extent | Last record→question gap |
| --- | ---: | ---: | ---: | ---: |
| RFC 6520 / 11 | 4,976 | 103 | 2,289 | 2 |
| RFC 6520 / 29 | 4,946 | 73 | 2,289 | 2 |
| RFC 9000 / 11 | 96,107 | 101 | 29,974 | 2 |
| RFC 9000 / 29 | 96,084 | 78 | 29,974 | 2 |

RFC 6520 has a second nearby paragraph expressing the same minimum in terms of padding. The text-deletion intervention removes **both** supporting paragraphs; deleting only the first would overstate necessity. RFC 9000's complete parameter paragraph contains its minimum, below-minimum handling and default; the intervention removes that whole paragraph. An independent blind reader cannot resolve the selected rule after either support-group deletion. In all four final readers, changing one simulated record across the threshold changes the complete answer set; changing that record to another value on the same compliant side leaves it unchanged. Each pair holds the opaque IDs and row order fixed while independently permuting field values, and its answers differ. This closes the demonstrated ID-only shortcut within this pilot, not every possible shortcut in the full RFC.

The 96K RFC 9000 input supplies a genuine approximately 30K-token rule-to-record span; the short RFC 6520 input is a useful basic rule-application case, not a long-range example. The final question is adjacent to the records, so the last-record-to-question gap is only two tokens. The source texts and exact RFC Editor URLs are verified against the frozen IETF fetch receipts; the model-visible reader labels simulated records separately from official material. All four readers are in the **train split** to avoid inventing a held-out source claim; the two source RFCs were already available to this local research pipeline. These outputs are candidate-only, `train_ready=false`, and no training/GPU task was started.

The source receipts authorize local IETF open-records research acquisition. [RFC 9000](https://www.rfc-editor.org/info/rfc9000/) and [RFC 6520](https://www.rfc-editor.org/info/rfc6520/) each carry an IETF Trust copyright notice and refer to the legal provisions in force at publication. This pilot has **local research use only**; public redistribution and downstream training-license clearance are not asserted.

The common grammar ran over all 44 documents without a per-domain question script but admitted only two source rules. This is a bounded recipe pilot, not a scalable broad-domain corpus yet. Scaling should seek native rule tables or registers with explicit field, threshold, applicability, exception and version qualifiers, then repeat the same source paragraph, duplicate-support, simulated-state and final-reader gates. Broadening regex matches while dropping scope checks would increase counts without improving supervision.

Final receipts:

The earlier native v1–v3 and unified v1/v2 artifacts are historical compiler checkpoints. Only the versioned receipts below have the final explicit-field-value contract and independent audit.

- Native: `data/candidates/p109_prose_native_v4/manifest.json`, SHA-256 `a735b45787dd2fb26a7d7fd748986170346260228d0354b62f98e4c44bc6352d`.
- Independent blind audit: `data/candidates/p109_prose_native_v4/mask_audit.json`, SHA-256 `5aab15763b945796eeecfcbc4fee83d5dfba69085f0f13878c71b5db9498ba4f`.
- Unified reader shard: `data/candidates/p109_prose_unified_v3/manifest.json`, SHA-256 `371f9e699cfc7d1e737ef1dee2ba94d7520ce2bac1c99e2340595c84f9d9f541`.
- Shared all-reader mask: `data/candidates/p109_prose_unified_all_mask_v3/manifest.json`, SHA-256 `f9ef1711321fc32a894dc9a30cbe804f3d91c495ed8d75299ff1bde9d76c4427`; 4/4 checked, 202,113 final-chat and 355 supervised tokens.

Inspect individual cases and replay the exact bytes:

```bash
cat data/capability_records/p109_prose_support_v1/ledger.json
less -R data/candidates/p109_prose_native_v4/audit.jsonl
less -R data/candidates/p109_prose_native_v4/sample_index.jsonl
.venv/bin/python scripts/p109_prose_support.py --config configs/p109_prose_support_v1.json --output data/capability_records/p109_prose_support_v1/ledger.json --verify-only
.venv/bin/python scripts/p109_prose_compile.py --config configs/p109_prose_compile_v1.json --output-dir data/candidates/p109_prose_native_v4 --verify-only
.venv/bin/python scripts/p109_prose_audit.py --config configs/p109_prose_compile_v1.json --native-dir data/candidates/p109_prose_native_v4 --verify-only
.venv/bin/python scripts/p109_prose_to_unified.py --config configs/p109_prose_compile_v1.json --native-dir data/candidates/p109_prose_native_v4 --output data/candidates/p109_prose_unified_v3 --verify-only
.venv/bin/python scripts/audit_unified_reader_mask.py data/candidates/p109_prose_unified_v3 --all --output data/candidates/p109_prose_unified_all_mask_v3 --verify-only
.venv/bin/python -m pytest -q tests/test_p109_prose_rules.py
```
