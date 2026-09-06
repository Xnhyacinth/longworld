# World/task 与训练集扩展计划 — 2026-09-06

本轮完成状态核对、三个独立只读分支复核和后续设计。以下是执行计划，
不是新数据的通过收据；本轮 candidate、train-ready 和 production 增量均为零。

## 当前基线与证据边界

从六个明确列入库存的本地产品重新读取 train/eval JSONL，汇总
`actual_context_tokens`，并读取各自 `llamafactory/export_summary.json` 的 B5 条目：

| 产品目录（`data/releases/` 下） | Train 行 | 记录的 exact tokens | Eval 行 | B5 条数 |
| --- | ---: | ---: | ---: | ---: |
| p14-authentic-six-domain-probe-12-v1-promoted-v4 | 90 | 3,397,529 | 18 | 29 |
| p15-authentic-128k-extension-probe-2-v1-promoted-v7 | 12 | 1,163,922 | 0 | 12 |
| p16-macro-bea-128k-extension-probe-1-v1-promoted-v1 | 12 | 721,671 | 0 | 12 |
| p17-finance-128k-extension-probe-1-v1-promoted-v1 | 12 | 722,914 | 0 | 12 |
| p17-codeforge-128k-extension-probe-1-v1-promoted-v1 | 6 | 586,101 | 0 | 6 |
| p40-ietf-oauth-semantic-growth-probe-1-v1-promoted-v14 | 9 | 682,458 | 0 | 9 |

合计 train 141 行 / 7,274,595 tokens，eval 18 行 / 682,032 tokens；
train 分布为 36×16K、39×32K、48×64K、18×128K。B5 为 80 条 /
5,021,465 estimated tokens。16 个 world ID、7 个 domain 标签不等于
16 个独立 transition/oracle 程序。六个 release gate receipt 文件均存在。
本轮没有重跑 tokenizer、密码学签名验证或完整 release gate；生产资格仍按
现有收据边界保持零，不能将文件存在性当成签名复核。

当前 checkout HEAD 为 `1cf3059`，比交接多一条 P35 FAERS 容量口径提交。
工作区还有 P35 修改及历史未跟踪文件。`data` 实际指向
`/workspace/wynckeliao/longworld/data`：规划留在本 checkout；未来数据写入必须
采用全新、唯一目录，防止覆盖共享数据。当前报告没有修改它们。

旧计划的 BEA GDI “pending” 已被拒绝报告取代：12 行虽带行级
`train_ready=true`，最终 gate 因绑定和真实 proof growth 失败而未签发收据。
不能通过修复报告绑定就把该批纳入库存，也不能把它当低成本待转化余量。
见 [BEA 拒绝报告](p17_macro_bea_gdi_current_2005q1_gate_rejection_20260903.md)。

## 目标与选择

推荐采用两条并行路线：复用验证过的程序增加独立实体与真实来源覆盖；开发
新 transition/oracle 增加任务多样性。前者单独计为实体扩展，不冒充新程序。
只做新领域会长期停在 preflight；只复用模板则可能增加行数而没有新能力覆盖。
不采用凑固定行数、平衡长度桶、增加视图或改写问题来替代语义扩展。

下一轮优先验收：一个完整的新任务闭环，或每条路线可复现的准确拒绝结论。
新领域以 P54 为首选；近期可转化性以 GovInfo 已冻结 transition 筛选为并行首选。
不承诺新增行数。长档必须有额外答案依赖和 proof growth，不能只有更多正文。
先用 32K 做诊断闭环；最终仍须满足适用不可变 profile 的全部 band/view 要求，
单档成功不等于完整 world 获准入库。256K 暂不成为主任务。

## 三个并行工作包与 root 集成

工作包使用全新版本/命名空间，保留 P46/P52/P54 旧配置和失败证据。
worker 不修改共享 core、库存或签名产品，不提交其他人的文件。
共享 adapter 的变更由 root 在审查各自最小 oracle 后顺序集成，避免重叠编辑。

| 轨道 | 独占输出 | 最小交付 | 进入下一阶段的条件 |
| --- | --- | --- | --- |
| A：P54 | 新 `p54_*` 来源收据、配置、报告和专用测试 | exact relation span 表、逐文档权利覆盖表、最小条款演变 oracle 与 reduced-evidence 结果 | 答案由真实正文与关系推导，短子集不能直接给完整答案，权利范围闭合 |
| B：GovInfo | 新版本 `p52_*` 几何筛选配置和报告 | 冻结 transition/key 搜索清单、逐尝试三视图长度和 shared ordered 位置、失败原因 | factual/CF/ordered 同档且共享窗口/依赖证明可通过 |
| C：Ofgem | 新 `p46_*` public-cap 来源请求和报告 | 真实表头/行键、修订关系、Decimal oracle、单表与窗口反证 | 跨文档关系改变最终结果，且不是独立短问答拼接 |
| root | 本计划、共享 replay 集成、统一审计与后续库存报告 | source-parent→projection→audit→selection→promotion→quality→B5 的完整证据链 | 每个新产品完成适用 gate 与独立复核后才更新库存 |

每个工作包先提交 source/oracle 反证，失败即换预声明的实体或程序。
批量生成与 dense 计算在廉价拒绝检查之后；共享审计要求 candidate 输入时使用
隔离的 candidate-stage 输出，不能把预检查冒充正式审计。

## A：P54 的任务扩展

当前 `_replay_oracle` 只检查角色模式，metadata 只解析 corrected-by；
五角色齐全和配置中的 14 个纠错点都不证明立法链已经重建。
224,955 来源 tokens 与 32,277/64,285/128,392 聚合容量不等于候选长度。
见 [P54 closeout](p54_eurlex_legislative_chain_preflight_closeout_20260905.md)。

1. 先检验 metadata+corrigendum、单文档、relation-only 子集能否回答目标。
   能回答则拒绝该问题；不通过强制检查五种角色制造 remove-one 失败。
2. 绑定 proposal→position→act、corrected-by 等确实存在的关系端点，记录
   source SHA、原始 byte span、解析值、所用表示及正文定位映射。
   不能从配置补全关系；跨版本条款映射不能只靠相同编号猜测。
3. 新来源请求明确下一阶段正文持久化范围；逐文档绑定官方 reuse notice、
   attribution 与 exclusions。原 Commission 决定不自动覆盖所有机构文件。
   本轮未在线核验新权利条款，不声称该关已通过。
4. 首选任务为同一真实条款的 proposal→position→act 条件/例外演变，或纠错
   目标定位与应用。后者若 corrigendum 自足则直接拒绝。输出结构化差异与
   证据 span，不做开放式法律判断。
5. 最小 oracle 通过后，用 `taskreplaysidecar.py` 现有契约接入专用 adapter，
   参考 `govinfodisposition.py` 的来源绑定和 raw-slice replay。
   CF 可删除必要 span/关系并返回 UNKNOWN；不虚构替代法律史。
6. 经共享 materializer 同时测 full/CF/ordered，再验证 32K 和后续档位。
   更高档加入新的必要条款与状态转换；全部依赖检查保持原门槛。

## B：GovInfo 从 transition 筛选起步

优先顺序是 H.R.4366 EAS→EAH、H.R.815 EAS→EAH，再有限考察
H.R.4366 ENR→Law。P49 报告相应 modified pair 数为 108、5、29；
这些是历史 source-text 比较统计，本轮没有重算，也不表示实质法律修改数量。
两条 EAH→ENR 的 modified 均为零，不适合当前要求 modified 的 builder。
见 [P49](p49_govinfo_bill_text_disposition_preflight_closeout_20260904.md)。

- 第一批计划限定前两条 transition，每条最多 12 个 key 组合，使用稳定排序，
  在运行前记录完整清单和配置 digest；所有失败都写出，不只保存赢家。
  这是资源上限，不是质量阈值；后续扩大搜索需另存一批清单。
- 用 shared serialization 计算三视图长度差。32K band 是 32000–32768，
  三者最大最小差超过 768 就不可能同档；差小于等于 768 也不保证可打包。
  pure body token 差仅用于廉价排序，不能作为正式通过依据。
- 必须重新测 shared ordered：`_section_artifact_id` 使用 key 哈希，
  `govinfo_chronology` 按日期/kind/artifact ID 排序。division A/F 跨度与
  日历跨度均不能代替实际 token 位置。不得搜索 ID 或改排序来制造距离。
- 沿用 whole-section packer，新建 authentic parent；不修补旧签名行。
  shared CF、remove-one、artifact windows 和穷举 raw windows 均需运行。
- added/removed 需要来源完整性收据支持“缺失证明”，当前不作为配置扩展。
  三阶段首次变化/变化后保留链属于新 oracle 开发，排在两端点任务后。

P52 原 8K 窗口失败与 CF=31,000 拒绝保持为负例，见
[P52 closeout](p52_govinfo_bill_disposition_registered_parent_closeout_20260905.md)。

## C：Ofgem 与后备 world

Ofgem 使用公开 cap-level 表及季度决策链，先冻结两期与一条真实修订关系。
行键候选为 region/fuel/payment method/meter type/unit，必须以实际表头为准。
oracle 用 Decimal、显式单位和舍入口径解析生效期间、修订优先级与金额差。
new/withdrawn 必须有完整表范围证明，否则返回 UNKNOWN。

只有真实定义变化、适用期或 supersession 使后续状态消费前序状态，才计为
依赖链。全部季度各算一次 delta 仍可能是独立短任务集合；最终表或一个短窗
能给全答案就拒绝。旧 469,328 formula tokens 不提供单元格闭包或输出收据，
不作为新父样本。见 [P46](p46_ofgem_formula_dependency_gate_closeout_20260905.md)。

后备队列按已有失败证据收紧，不重新泛搜同一失败实例：

| 方向 | 值得新增的程序 | 再进入的前提 |
| --- | --- | --- |
| SEC/issuer 财报 | restatement＋footnote qualifier＋subtotal reconciliation | 换真正有充分必要证据的链；P18 Microsoft recast 仅 3,889 tokens，旧链不重跑 |
| CodeForge | 可执行 culprit localization→fix→first release | 新链先实测 fail/pass；P18 pandas 仅 21,200 去重 tokens 且无 midpoint 观测，不补背景 |
| NTSB | response→评价→重分类的分支状态链 | 冻结历史响应、隐私/权利和 oracle；当前状态摘要不能替代历史 |
| 已验证 Finance/CodeForge/IETF | 同程序新实体或独立来源组件 | 先测真实 proof growth、近重复与短窗；计实体覆盖，不计新 transition/oracle |

FAERS 旧同季度、P45 零跨季度 overlap、P47 缺失桥接表、EPA 短纠正问题继续
保留拒绝。不得用更多独立记录弥补不存在的版本/关系边。

## 统一验收与训练集边界

按 source receipt/rights→小 oracle 反证→共享序列化 exact band→正式 replay
与 retrieval/raw-window audit→selection→promotion→release quality→B5 验证推进。
廉价预检查允许提前拒绝，不代替共享正式审计。原 near-dup、derived-view、
truncation、proof-growth、profile coverage 与 trust gate 都不变。

新增统计分别记录：source connected component、实体、transition/oracle、
依赖 motif、语义任务、独立 parent、各 band/view、最终产品和 B5 数量。
沿用 P17 diversity signature：新 transition/oracle pair 及至少两个其他维度
变化才可申报新的语义 world 类型；world ID 数仍单列，不混用定义。

训练/评估按完整 bill/procedure/来源 connected component 隔离，同一父样本的
CF/ordered/不同长度不得跨 split。保留旧 eval 产品，新来源另建冻结评估设计；
不能为了新增 eval 擅改已有 immutable profile。来源重叠先检查后分组。
任务筛选用的失败样本只作诊断，不再标成 unseen evaluation。

B5 是当前实际训练条件入口；141 产品行不等于 141 条 B5，也不保证所有任务
都被实际训练。后续先核验各签名 manifest 绑定的输出，再按已有训练消费路径
检查覆盖、重复与来源隔离，保留产品各自 trust root。模型质量仍未测量；
本计划不启动训练、GPU 作业、HF 上传或生产发布。

## 本轮验证范围

三个 subagent 分别只读审查 P54、GovInfo、Ofgem/后备路线，root 核对关键
代码位置、历史拒绝报告和六产品汇总。没有新增/修改生成器或来源正文。
数据行数汇总可由上述六目录的 train/eval JSONL 与 B5 export summary 重现；
此项仅核对当前库存口径，不宣称完整签名/模型/tokenizer 重新验证。

实际执行 `uv run --no-sync pytest -q`，选择
`tests/test_p46_ofgem_formula_dependency_gate.py`、
`tests/test_p52_govinfo_bill_disposition.py` 和
`tests/test_p54_eurlex_legislative_chain_preflight.py`：19 passed（38.53s）。
本轮没有重跑历史 27 项全组合。5 个本地 Markdown 证据链接存在性、占位符
检查和 `git diff --check` 通过。另一个 agent 只读复核本计划 P54 内容为 PASS；
其范围不包括重算其他轨道统计。计划未提交 Git，未变更当前库存状态文件。
