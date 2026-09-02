# P14 真实长文数据转化状态 — 2026-09-02

## 当前结论

P13/P14 当前有可审计的候选库存，但还没有可发布的正式训练包。Cyber
cross-vendor remediation、Microsoft multi-filing asset/operating-cash
trajectory、Wasmtime patch/review/test/release ancestry、JPMorgan
cross-year risk taxonomy 和 Walmart cross-year reconciliation 均已完成
16/32/64K × full/CF/ordered 的 9-cell dense audit。因此当前账本从 7 物理 /
5 配额更新为 **12 物理 / 10 配额 / 108 行 / 4,070,381 exact Qwen context
tokens**。所有行仍为 candidate，统一 selection 尚未在这个 12-physical /
10-quota 账本上
重跑；在 12/12 前重跑只会重复 fail-closed。

新增 Cyber world 的候选 SHA 为 `068a10daf9e4fbfbc0f59c91b4f2aff6fe043cbbcdef45b514f8bdf4adcfc18d`，
audit SHA 为 `d64aabeb43804ca02b88f65e84314a3b536b5535b16d88db6daeec32ea25fcce`。
九个 Cyber cell 均在 exact band 内，source-token measurement、strict replay、
CF、remove-one、4K/8K window 与 dense retrieval gate 保持原阈值通过。该
world 使 Cyber 达到 2/2。

新增 Finance world 使用 Microsoft FY2022--FY2025 四份 issuer-owned SEC iXBRL
10-K，不复用 Amazon Finance world。16/32/64K history 精确为
16,119/32,028/64,083 tokens，依次包含 2/3/4 filings、8/12/16 个必要 statement
rows 和 1/2/3 条相邻 filing 关系。第一次只使用 revenue/assets/balance rows 的
版本被 ordered-view 16K 窗口解出，未通过；最终程序加入逐年 operating-cash
证据和 trajectory 后重新物化，9/9 audit 通过，near-dup 最大值为 0.0。该
world 贡献 337,159 final-view tokens，使 Finance 达到 2/2。

新增 CodeForge world 使用 Wasmtime v45--v48 四个真实 release episodes。
16/32/64K 程序依次联合 1/2/4 个 patch diff、唯一 approved review、一个
selected final pre-merge test、merge 与 exporter-verified tag ancestry；exact
tokens 为 16,363/32,694/64,842，每档三视图一致。第一次 strict audit 正确发现
generator 把同 repo、但没有 signed child-to-parent link 的关系误报为 authentic；
shared relation builder 改为复用 auditor 的 fail-closed predicate 后，最终 9/9
accepted、0 rejected。该 world 贡献 341,697 tokens，使 CodeForge 达到 2/2。

新增 Company world 使用 JPMorgan Chase 2022--2024 三份 issuer-owned 官方
年报 PDF。16/32/64K 程序依次读取 10/21/43 个有字节范围与哈希收据的风险管理
section，并验证 0/1/2 条 signed 相邻年报关系；三档 exact tokens 为
16,377/32,731/65,032。最终 9/9 strict audit 通过、0 rejected，贡献 342,420
tokens，使 Company 达到 1/2。relation artifact 的 `source_family` 与 promotion
replay 对称接线均由失败优先测试定位并修复，没有修改任何内容阈值。

第二个 Company world 使用 Walmart FY2022--FY2025 四份 issuer-owned 官方
年报。16/32/64K 程序分别联合 5/10/20 个 segment、strategy-execution risk、
capex、ICFR 与 distant segment-note sections，并验证 0/1/3 条 signed 相邻
年报关系；三档 exact tokens 为 16,371/32,250/64,141。最终 9/9 strict audit
通过、0 rejected，贡献 338,286 tokens，使 Company 达到 2/2。原 source
fixture 签名已在内容 hashes 不变的条件下由当前 source role 重签并 replay。

## 剩余缺口与 P14 路线

| Domain | 当前/目标 | P14 路线 |
| --- | ---: | --- |
| Company | 2/2 | JPMorgan 与 Walmart 均通过；停止扩充配额 |
| ResearchLab | 0/2 | 换自然长度足够的论文、revision/review/benchmark 程序，不复用 MLRC/AEVB/Megatron 死路 |
| CodeForge | 2/2 | Wasmtime patch/review/test/release ancestry 已补齐；不再扩充配额 |
| Finance | 2/2 | 已由 Microsoft asset/operating-cash trajectory 补齐；不再扩充配额 |

硬 gate 不变：near-duplicate、exact band、`derived_view_gate`、真实 source
lineage、truncation ppm 和禁止 padding/复制。为减少冗余，只在新逻辑存在时
写失败优先的专属测试；每个 candidate 跑一次局部 9-cell audit；达到 12/12 后
再统一执行 selection、promotion、quality gate 和训练导出。

机器可读增量账本见 `p14_conversion_status_v1.json`；2026-09-01 的 P13 报告
保留为不可变历史快照。
