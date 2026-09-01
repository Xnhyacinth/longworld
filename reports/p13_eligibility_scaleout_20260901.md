# Train-ready 与 production eligibility 收敛状态 — 2026-09-01

## 当前结论

`train_ready` 和 `production_eligible` 不能通过修改 manifest 布尔值获得。前者是
source-bound candidate 经 ranking、strict replay、selection、promotion 和统一质量
门禁后产生的**行级训练资格**；后者是该训练 release 再经 production profile、完整
unseen eval、确定性训练导出、独立 KMS 审批、不可变 inventory 和 `COMMITTED` 标记
产生的**发布资格**。

当前 canonical 状态是：

| 层级 | 可用量 | 状态 |
| --- | ---: | --- |
| content-audited CPT | 3,047 rows / 288,004,845 exact Qwen tokens | 可做 local-probe CPT 对照；不是 executable SFT |
| promotion-v2 SFT rows | 12 rows / 2 ResearchLab worlds / 454,846 tokens | 行级 `train_ready=true`；没有形成当前 profile 的 12-world release |
| release-level train-ready | 0 个当前 12-world release | 缺完整 world/view/bucket selection、report、gate 和 deterministic export |
| production/KMS eligible | 0 rows / 0 worlds | profile、package finalizer、独立审批和 production unseen 尚未闭合 |

历史 P6 private probe 的 542 rows 是较早、较宽松 profile 的本地工程快照，不能覆盖
当前 P12/P13 契约，也不能作为 production 资格。

## 本轮实际推进

Macro packer 现在只使用目标经济 series 的真实 revision trajectories 作为背景历史。
构造端、verified packing cache 和独立 audit 都重新检查实际 serialized observation 的
`series_id`，而不是信任 trajectory ID 或 candidate 声明。两条攻击回归分别覆盖：

1. 修改正文中的 background observation 为另一个 series 后，audit 必须失败；
2. 把签名 packing plan 的 prefix 换成真实存在的另一个 series 后，构造必须失败。

基于同一份官方 BEA workbook，形成了四个按经济 series 隔离的真实 revision worlds：

| World | bands | candidates SHA-256 | strict audits SHA-256 |
| --- | --- | --- | --- |
| GDP current dollars, 2003Q1 | 16/32/64K | `d2cb1aa5d0b2989c10daf12d9c9484f15a3f90bf122c2d8d2bda4ce098e19238` | `52f7404d7335d0a3f924f2f1e4aa50a4a752f011bcba24ce3578acdf030109c2` |
| GDI current dollars, 2005Q1 | 16/32/64K | `142bd5225990096611b220d4d0cf01a6e1854dd23692b0bd0d4df0a92260ce06` | `d8929b37ca9f111570a408af2750fa8f67deb81062a519adb6f94ea12dd7d515` |
| real GDI percent change, 2004Q1 | 16/32/64K | `29998e29b497224dd95800b333469a143ab8498ee1a685fb6b6b688b51592fa1` | `59d73a5312ee158d1b630f05e2fc40c715e8cacbc8d0d7c49111a040b33d5bbf` |
| real GDP percent change, 2005Q4 | 16/32/64K | `d19bc8cc289d0b60567d4c1de8824529a5f1d2ec4079d39baafcdb44606b2083` | `22a1969254a92ade088aae18f52a7077c5548fef9213004e27ace9380af6158e` |

12/12 candidates 都通过 adapter replay、CF/remove-one、source/body binding、4/8/16K
raw token window、BM25/TF-IDF、dense top-3 insufficiency 和 full-pool strict replay。
四个 64K world 的 artifact ID 交集为零，单个 context 也不混入其他 series。这里的
“隔离”不应扩大解释为来源完全独立或语义零近重复：四者仍共享同一 BEA workbook、
schema 和 task template。

这些 12 条仍明确是 `data_stage=candidate`、`train_ready=false`、
`production_eligible=false`。原因不是 replay 失败，而是 Macro 还没有形成统一 exporter
要求的 `full`、真实 `cf` 和 `ordered_artifact_view` 三种训练投影，也没有加入当前
12-world profile 的 selection/gate。

生产链路同时修复了两个 fail-open 风险：

- deterministic training packer 现在对每个 source row 复算 canonical
  `sft_row_errors`，不再接受手工补齐五个顶层 trust mirror 字段；production 还必须
  使用 production promotion attestation。
- production unseen 现在只接受当前可发行 profile；任一 requested axis 为 blocked、
  train/eval 为空或输出文件为空时，整个 production unseen release 失败并清理输出。
  传入 rows 的 exact row-set 与原始 train/eval split row-set 也必须等于 auditor-signed
  release gate；相同行数的另一个合法签名 release 不能混入。多 axis 中途错误、缺失或
  错误 report key 等任意 production 异常都删除 partial JSONL 和 manifest。

## 让两个状态真实变为 true 的顺序

### A. 先形成 release-level train-ready

1. 为 Macro、Finance、Cyber 输出标准 `full/first`、真实 `cf/first` 和
   `ordered_artifact_view`；CF 必须 replay 得到 `cf_answer`，不是换标签。
2. 将 Company、ResearchLab、CodeForge 的缺失 16/32/64K history 补齐，并保证每个
   selected world 的所有训练行都 source-bound。
3. 采用新的 immutable 六域 probe profile，而不是放宽旧 P12：Company、
   ResearchLab、CodeForge、Finance、Cyber、Macro 各 2 worlds，共 12 worlds；
   domain-stratified 10 train / 2 eval。
4. selection 前移检查每个 world × bucket 的三视图 coverage；统一 selection、
   promotion、train-ready report、quality gate 和 B1/B3/B5/B5w deterministic export。
5. 只有上述 receipt 全部通过，才把最终 promoted rows 写成 `train_ready=true`。

建议新 profile 的结构下限为 144 SFT rows，其中至少 72 条是真实 64K；新增 64K row
必须改变 answer program、query timing 或真实 source relation，不能复制三视图凑数。

### B. 再形成 production eligible

1. 新建且冻结 production-48 profile；只有成功的 12-world gate receipt 可以作为
   predecessor。
2. 在受保护 CI 中运行六个分离角色，使用 KMS/ECDSA 非对称信任根；source bytes、
   selection、quality report、unseen split、training transform 和 inventory digest
   必须逐层绑定。
3. 四个 production unseen axes 均需非空且 ready；随后跑固定 RULER、LongBench v2、
   MRCR/GraphWalks、HELMET 及一般能力/closed-book 对照。
4. 实现 package `prepare -> independent approval -> finalize`：独立审批签署 inventory、
   training manifest 和待提交 marker digest；finalizer 用公钥复核后原子写入
   `COMMITTED`。
5. 只有 immutable inventory 的 `trust_mode=production`、独立审批和 committed marker
   全部可重放验证时，才生成 `production_eligible=true`；之后才允许上传 private HF。

当前代码仍把 `ISSUABLE_PRODUCTION_PROFILE_IDS`、
`PRODUCTION_PACKAGE_READY_PROFILE_IDS` 保持为空，并让 production package builder 在
sidecar finalizer 前失败。这是诚实的 fail-closed 状态，不应通过添加 profile ID 绕过。

## 并行扩展优先级

数据仍需继续合成，但优先级应按“离正式训练还有几道真实门禁”排序：

1. **收敛现有 executable domains**：先完成 Macro/Finance/Cyber 三视图，再补齐
   Company/ResearchLab/CodeForge 的缺 band；这是最快增加合格 SFT 的路径。
2. **扩大现有实体和时间跨度**：同 adapter 下更换真实公司、仓库、series、filing、
   revision cycle 可以并行，但必须重新导出 source、改变 state/answer/proof，并做
   cross-release semantic near-duplicate 与 source overlap 审计。
3. **新增 domain**：Regulation、Clinical、IETF、KB/Wikipedia 可以继续进入统一
   state→answer→CF/remove-one/replay；只有存在真实纵向 workflow 和 executable answer
   program 才计作 world。视觉/游戏等 domain 也适用同一标准，不能仅拼接说明文档。
4. **CPT 单独扩容**：现有 0.288B tokens 足够先做早期 equal-token probe；继续扩容
   应增加 source family、release/vintage/revision 时间跨度和事件密度，而不是复制窗口。

因此近期的主路径不是同时铺开大量未闭合 adapter，而是以最多并行的
source/export、projection/replay、selection/audit 四个阶段流水化，把已接入的真实
domain 转成可统一 promotion 的训练单元；新 domain 作为下一批 source-bound world
并行准备。

本轮完整代码回归结果为 **1,563 passed / 1 expected xfail**；六条随后补充的 Macro
cross-series、unseen row-set 混用和 fail-atomic cleanup 回归在当前 18-test Macro 与
189-test production-focused suites 中单独通过。
