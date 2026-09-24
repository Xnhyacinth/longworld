# P74 真实共享世界 — 第一波执行记录(2026-09-24)

指令:用户 2026-09-23 的方向裁定(基于 worlds `ee98b60` 的代码核查),冻结为
`.hl/design/p74_real_shared_worlds.md`(commit b90128d)。本波 = 章程 §15 的执行
切片:W0 / W1 / T1+T2 核心 / T4 首片。GPU 不涉及;全部 CPU/代码。

## W0:witness 三指标拆分 + 死变异处置(任务 #7,agent w0-metrics)

capability_mutations.py 的 witness_report 现在按三指标返回每个变异:
`applicable`(错误程序在该实例上有定义:relax 需 ≥3 条件;last_declaration
因 spine 单声明约束标记 not-applicable)/ `valid_output`(返回了任务答案
类型的合法结果)/ `distinguished`(合法输出且 ≠ 正确答案)。**报错的变异
不再计入 semantic_distinguished**(语法报错、类型不支持和语义答错不再合
成一个分数——章程 §0.2)。审计与 C/D 导出改用 `semantic_distinguished_fraction`。

关键修复:`latest_text` 从"与正确解同一路径"的 no-op 改为真正的 reveal-order
折叠。重跑审计(588 行)前后对比:

| family | 旧 rich / mean-frac | 新 sem rich / mean-sem-frac | 说明 |
|---|---|---|---|
| asof_state | 100% / 0.500 | 100% / **0.964** | latest_text 78/84 现在真区分 |
| rule_holdout | 100% / 0.952 | 90.5% / 0.476 | wrong_rule_family 84→**4/84**:旧值大多是报错计数,语义化后只有 4 行真输出不同答案;新数字更诚实也更刺眼 |
| alias_locate | 96.4% / 0.482 | 75.0% / 0.375 | nearest_lexical 81→63(语义化) |
| set_complete | 81.0% / 0.405 | 50.0% / **0.250** | relax 的报错行不再计区分 |
| 三 records 族 | 100% / 0.58-0.97 | 不变 | 其变异本就少报错 |

新 C/D 臂(arms.json,语义阈值 0.5):D=360 行、C=360 行;总重叠 76.1%;
**set_complete 重叠 0%**(D 全 witness-rich / C 全 witness-poor——第一个真
对比族);asof/filter 仍 100%(该两族 witness-poor 训练池为 0,如实记录)。
结论:重叠问题部分缓解;干净 D>C 对照还需要 witness-poor 池加大的银行
(下波规模决策)。

## W1:85.9% 的字段级分解(任务 #8,主线)

`scripts/decompose_supervision.py`(offset-mapping 精确、region 归类——hex id
被切成单字符 token,per-token 正则会数错;两处真 bug 修复:hex 长度多档
{8,}、asof 置换行单列)。分解 full-provenance→answer-only 的 2,406,816
supervised-token 差:

| 移除 token 类别 | tokens | 占差值 |
|---|---:|---:|
| id 枚举(r/k-hex、unit-*) | 2,148,442 | **89.3%** |
| 键名/结构包裹 | 233,720 | 9.7% |
| 标量值 | 21,754 | 0.9% |
| 切分边界残差 | 5,636 | 0.2% |

行级核验一致(26 ids→578 id tokens vs 47 structural)。**正确引用口径:
"answer-only 契约移除 85.9% 监督 token;其中 89.3% 是 id 枚举"**;旧的
"~73%"不引用;85.9% 也不等于"全是 id"。结果落
`data/capability_records/p73_contract_arms/supervision_decomposition.json`。

## T1+T2:统一语义世界 + 依赖算子(任务 #9,agent t1t2-compiler)

两个新模块(不动任何既有文件):
- `longworld/synthesis/shared_semantic_world.py`:SourceSnapshot 加载(章程
  §14 契约,dataclass 严格往返)、SemanticWorld(类型化对象/关系/带版本与
  撤销的状态时间线/显式 scope 块)、render() 使任务范围**可从渲染文本+
  问题恢复**(§0.1 的补齐方向)。
- `longworld/synthesis/dependency_ops.py`:Bind/FollowRelation/
  ResolveVersion/ApplyRule/JoinOnBoundResult/Group/Aggregate;**不可折叠门**
  = 常量依赖分析(每个非首步必须消费至少一个前驱绑定的变量,拒绝等价于
  常量 AND 的平铺程序);执行产出带 span lineage 的 proof;干预检查 API。

`scripts/demo_p74_world.py` 端到端演示(模拟快照,14 对象/39 facts/3 文档):
链 program 深度 7(version→rule→objects→measurements→比较),answer
aurora=2 boreal=25 LT;17 个 facts 的 proof 带 D2[207:225] 式正文 span;
干预检查双向正确(上游 adoption 事实翻转 LT→GT;无关观测事实不影响);
scope recovery True。28 测试覆盖章程 §15 的 (a)-(f)。

## T4:Wiki 目录 SourceSnapshot adapter 首片(任务 #10,agent t4-wiki)

`longworld/synthesis/wiki_adapter.py`(62KB)+ `scripts/freeze_wiki_snapshot.py`
(CLI;live API / 本地缓存 / BLOCKER 退出码 3 三态)。**真实冻结成功**:
Category:Astronomical observatories → 15 页(revid 钉死,32 次 HTTP,
live-api)→ 2,781 实体 / 196 facts / 34 relations / 15 条 ungrounded 隔离;
**196/196 span 抽验逐字命中**;许可如实记录(CC-BY-SA,per-page URL 留
attribution)。快照落 `data/capability_records/p74_wiki_snapshot_v1/`(data/
按先例不入库,manifest 引用其路径)。17 嵌入式 fixture 测试(无网络)。

## 未做(下一波,按章程)

- 快照→世界→任务的接线(真实 Wiki 世界上的多任务族实例化):T1/T2 核心
  与 T4 产出各在一侧,中间的"无专属任务脚本生成多族任务"是下波核心。
- T3 正文级 proof 搜索(替代证明、AND-OR 图、四证书);T5 长度控制器;
  T6 renderer 双路;T7 分层生产矩阵。
- p73_shared_v1 的多档重生成(8K/32K)与 witness-poor 池扩大(若要干净
  D>C 对照,需低区分世界更多)。
- 训练臂 R/A'/B'/C/D:仍用户门控。

## 验证

- 新+受影响测试 75/75 绿(test_wiki_adapter 17、test_shared_semantic_world 11、
  test_dependency_ops 17、mutations 8+新增、p73_arms 3、shared_world/contracts
  回归)。
- 密钥扫描:clean。
- 三 agent 各自独立验证 + 主线复核(witness 审计重跑、demo 实跑、快照
  span 抽验)。
