# P73 相关工作借鉴笔记(2026-09-23,联网检索)

供共享世界多能力编译器设计引用;每条给出"可借机制"与"不可冒充为新"边界。

| 工作 | 实际机制(已核对原文摘要) | LongWorld 借什么 | 不冒充什么 |
|---|---|---|---|
| **MuSiQue** (arXiv 2108.00573) | 2–4 跳问题由"可组合对"自底向上构成;后一步**关键依赖**前一步答案;严格过滤使"断链猜测"难以成功;MuSiQue-Full 加不可答对照 | **连接器纪律**:任务链的每一步必须消费前一步输出;不可答对照进入银行 | "前一步输出进后一步"本身不是新贡献 |
| **LongCrafter** (arXiv 2607.06160) | 32 类浅/深任务分类;上下文分解为**证据图**(跨段关系);指令-答案对从标注证据 span 生成;Qwen2.5-7B/LLaMA-3.1-8B SFT 在 LongBench/v2/LooGLE 超基线 | 任务分类学 + 证据 span 标注使"忠实可溯";长度对齐任务目标 | "任务多样+证据图"不是新颖点本身 |
| **EntiGraph** (arXiv 2409.07431) | 从有限源文档抽取显著实体,**文档级**链接生成合成段落;稀疏事实扩展为多上下文;原文可用时与 RAG 增益叠加 | 来源稀缺时的实体链接扩容法;曝光控制 | 改写复用≠新增长程依赖 |
| **NExtLong** (arXiv 2501.12766) | 源文切 meta-chunk,中间插入**检索得到的困难负例**;强化长跨度依赖;HELMET/RULER 增益 | CPT 侧:原文块+困难干扰的构法(与我们的 decoy 行同构,可互证) | 不是 SFT 路线的替代主张 |
| **π²** (arXiv 2604.05114) | Wikipedia 表格→多跳分析问题;**双路径代码执行**验证答案;结构化解→回译自然语言解释;SFT 增益 4 个 benchmark | 双路径独立验证的真值构造;trace→自然语言监督的桥接 | 我们不声称"表格真值"新 |
| **SWE-smith** (arXiv 2504.21798) | 一次 repo 环境准备→**变更使既有测试失败**生成大量任务;128 repo→50k 实例 | **成本结构**:环境一次、任务多次;执行反馈作为筛选器 | 不是"world 命名方式"的创新 |
| **Wikidata dumps**(官方 wiki 已核对) | JSON dump=推荐、含 qualifier/reference;**truthy RDF 不含** qualifier/refs | 时间/限定关系任务必须走 JSON dump 或 RDF-all | 图谱事实若不在可见正文中,不能当 gold |
| **MediaWiki API Revisions**(已核对) | `revids` 按 revision 取页面;`rvstart/rvend` 时间枚举;编辑时间戳≠事件时间 | revision-pinned 来源冻结的取法 | 页面修改时间不当事件时间 |

## 借鉴计划(共享世界编译器,落到现有 spine)

1. **World 对象模型**:一个 world = 记录+事件+别名+规则示例+关系(现有 families Row 已统一),**不再按 family 专造世界**;world 是"边界明确的语义资料空间"(模拟或真实来源)。
2. **任务适用性层**:对每个 world 枚举适用的任务程序(L1 定位/L2 集合/聚合/JOIN/L3 as-of/规则),绑定对象+范围;类型/单位/时序检查(借 MuSiQue 连接纪律:多跳程序的后步参数必须来自前步输出)。
3. **契约渲染**:answer-only/+minimal-evidence/full-provenance 三契约(已实现 capability_contracts.py);指令与答案一致。
4. **Witness 层**:语义变异区分度作为质量层(已实现 capability_mutations.py),正常实例保持自然分布。
5. **来源层**:Wiki 表格 adapter(revision-pinned;2026-09-23 复核:本工作树
   无该 adapter 的任何工件/脚本/试点银行,"八行试点已验证"无盘上依据,
   应视为未验证计划)→ JSON dump 扩量;repo/research 两条后续线。
6. **Spark 映射**:source_prepare 一次→world 并行→TaskSpec 展开→CPU 验证→渲染→最终物化(借 SWE-smith 成本结构);世界内顺序执行不阻碍世界间并行。
7. **评测**:RULER/HELMET 式多任务族曲线(不只平均分);公开 GW 只作回归。

## 检索局限

- LongCrafter/EntiGraph/NExtLong/π² 机制描述基于 arXiv 摘要页;正文细节(过滤阈值、采样分布)未逐条核对,写作前需全文复读。
- SoG 未单独检索(与 EntiGraph 同段确认);RULER/HELMET 未重查(既有结论:任务间相关性有限)。
