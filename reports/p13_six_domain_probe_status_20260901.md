# P13 六域 12-world probe 状态 — 2026-09-01

## 结论

`p13-authentic-six-domain-probe-12-v1` 目前还不能进入统一 selection、promotion、
quality gate 或训练导出。按当前代码和 source-token receipt v2 重新验证后，内容门禁通过
的标准三视图产物为 **7 个物理 world、63 行、2,371,273 个 exact Qwen context tokens**。
其中 Macro 有 4 个真实 world，但 profile 最多计 2 个，因此按每域两个 world 的配额只能
计 **5/12**：Finance 1、Cyber 1、Macro 2、CodeForge 1、Company 0、ResearchLab 0。

语义规模也尚未达标：当前 63 行只有 **5/12 motifs、5/12 answer programs、5/12
semantic base tasks**；重算后的 executable proofs 为 16，超过 12 的下限。后续 world
不能只是换实体，必须同时带来新的真实任务语义和答案程序。

所有计入行覆盖 16/32/64K × full/CF/ordered、`query_timing=first`，并已完成当前代码下
的 dense ranking 和 strict audit。Finance、Cyber 和 4 个 Macro world 已重建为
`longworld.source-token-measurement-receipt.v2`，直接计算最终 prompt 的真实来源 marginal；
旧 receipt v1 哈希仅保留为历史记录。dprint 是 native episode workflow，不使用 task
projection receipt，但已用授权的现有 local-probe trust root 重新执行 HMAC strict audit。
所有行仍是 `data_stage=candidate`、`train_ready=false`、`production_eligible=false`。
下表的 “canonical” 仅表示当前内容门禁下的候选账本，不表示已通过 release selection。

## 可计入的 canonical worlds

| Domain | World | Rows | Exact tokens | 状态 |
| --- | --- | ---: | ---: | --- |
| Finance | Amazon issuer IR 2021–2024 | 9 | 339,841 | ranking/audit 9/9 |
| Cyber | CISA KEV catalog | 9 | 338,685 | ranking/audit 9/9 |
| Macro | BEA real GDP revisions | 9 | 336,966 | ranking/audit 9/9 |
| Macro | BEA current-dollar GDI revisions | 9 | 337,050 | ranking/audit 9/9 |
| Macro | BEA current-dollar GDP revisions | 9 | 336,939 | ranking/audit 9/9；额外多样性，不增加 Macro 配额 |
| Macro | BEA real GDI revisions | 9 | 338,304 | ranking/audit 9/9；额外多样性，不增加 Macro 配额 |
| CodeForge | dprint multi-release recovery | 9 | 343,488 | fresh HMAC preflight/ranking/audit 9/9 |

标准 task projection 必须使用 derivation-v4 和 source-token receipt v2。旧 receipt v1
采用父比例推算，不再满足最终 prompt 的真实 token marginal 契约。

## 真实缺口

| Domain | World | 当前覆盖 | 阻塞 |
| --- | --- | --- | --- |
| Company | Microsoft FY2022–FY2025 | 6/9 | reconstruction 16/32K 语义门禁通过；64K 自然长度仅 55,102，严格拒绝 |
| ResearchLab | Attention revisions | 6/9 | 16/32K 完整；64K 自然长度 48,929，来源仅含 arXiv revisions，缺 review/response/benchmark history |
| ResearchLab | AEVB v1–v11 | 6/9 | 32/64K 三视图完整且有真实 revision edges；16K=14,980，缺 1,020；near-duplicate 0.40/0.51，未 ranking/audit |
| ResearchLab | MLRC revision workflow | 9/9 | fresh HMAC audit 9/9，但 32K CF=0.3473、64K CF=0.3170，超过 near-duplicate 上限 0.25 |
| CodeForge | Pulumi | 3/9 | 只有自然 64K 三视图；16/32K 因 strict-support overflow 被拒 |
| CodeForge | Oxc | 5/9 | 16K full/CF、64K 三视图；16K ordered 距离不足，32K 的 16K 子窗仍可解，未 ranking/audit |
| Finance | 第二 world | 0/9 | 尚未生成独立真实 source-bound workflow |
| Cyber | cross-vendor remediation | 3 history rows | 17 CVEs、34 CISA/NVD records、17 relations；16/32/64K source→state→answer/CF/remove-one 已通过，尚缺三视图 sidecar、raw-window、dense/promotion |
| Company | 第二 world | 0/9 | 尚未获取并解析另一实体的完整真实多 filing history |

Microsoft v10 P1 修复后保留 8 条独立 cash-flow/tax/market-risk 64K diagnostic
candidates，共 519,335 tokens；旧 reconstruction 不再借这些叙事 section 过长度或把它们
误标为 essential。它与 16/32K reconstruction 不属于同一 semantic growth group，不能
拼成一个 9/9 world。Cyber cross-vendor 新增 3 条 executable history，共 112,860 tokens，
但尚未进入标准 view/audit 链。二者都不计入 canonical 63 行。

## 发布边界

- immutable profile digest：
  `7fc9734fdd5dd7b3420eb7235e542fe1b82b8bf5dfc146862eb889a21d6de877`
- 最低完整规模：12 worlds、108 rows、每档 36 rows、每域 6 条 64K rows。
- 已用当前 7 个 world 和授权 local-probe trust root 实际运行统一 selection；它按设计
  fail-closed 为 `insufficient fully audited worlds: 7<12`，未写 selection receipt。
  因此仍没有 promotion manifest、quality-gate receipt 或 B1/B3/B5/B5w export。
- production trust 的 prepare/独立 ECDSA approval/finalize/COMMITTED 代码路径已实现，但
  production profile allowlist 和 package-ready allowlist 都为空。
- production-48、四套 unseen eval、private HF 上传继续阻塞；不能手工修改 eligibility 字段。

机器可读账本见 `reports/p13_six_domain_probe_status_v1.json`。
