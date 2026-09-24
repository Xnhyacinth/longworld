# Git 管理与分支策略(2026-09-24 定稿)

## 分支模型

- **worlds** = 全量研究分支:代码 + .hl 记录 + 设计文档 + 试验脚本。所有
  P7x 工作先落 worlds,每次收口必须推送(`git log origin/worlds..worlds`
  为空是硬性检查)。
- **main** = 核心有效内容。分叉政策沿袭 docs/migration/2026-09-17-
  main-worlds-split.md(main 现有 29 个专属提交,含 P57-P65 精选落地与
  路径清理)。**不频繁更新**;worlds 上持续验证,不自动同步。

## main 合入门槛(接受才进,且必须真收益)

同时满足才考虑合入 main:
1. **训练证据**:该能力已在受控训练中显示收益(P72 的 arm 训练或未来
   P74/P75 对照),或
2. **生产依赖**:主干的 pipeline/config 依赖该模块才能运行(如当时的
   capability_curriculum.py)。
3. 稳定至少一个 wave(无未决 VER 发现),测试全绿,无 worlds-only 的
   研究性依赖(如 .hl 试验记录引用)。
4. 合入方式:cherry-pick 或精选目录移动(沿 main-only 提交 a620ec8/
   4eaa451 先例),不做整分支 merge——保持 main 历史线性、可审计。

## 当前判定(2026-09-24)

worlds 的 P73/P74 内容**不合入 main**:
- capability_mutations/shared_world/contracts 及 P74 全部模块:研究层,
  训练收益未验证(arm 训练是 P72 数据;P73/P74 数据未训)。
- p73/p74 银行与快照:data/ 不入库(既有政策),manifest 引用。
- 唯一候选:P74 的 wiki_adapter + freeze CLI 若未来成为生产抓取路径
  (P75 之后再看)。

## 工作树卫生

- `tmp/` 已入 .gitignore(会话草稿;持久规划进 .hl/)。
- `plans/` 已归档为 `.hl/plans_archive/p71_plan_20260919.md`(P71 计划
  被 .hl/design/p71_* 取代)。
- 收口前硬检查:工作树只剩干净(或刻意未跟踪项)、推送完成、
  pytest 相关文件全绿。
